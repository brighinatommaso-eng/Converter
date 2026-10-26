"""Corpus reale: misura la struttura ricostruita, non il pixel.

I file veri stanno in `tests/corpus/local/` (gitignored) o nella cartella indicata
da `CONVERTER_CORPUS_DIR`. Le attese stanno in `tests/corpus/expected/`, con lo
stesso percorso relativo e suffisso `.json` (es. `report.pdf.json`).

Prima esecuzione (crea le baseline da rivedere):
    CORPUS_UPDATE=1 python -m pytest -q tests/test_corpus.py

Poi:
    python -m pytest -q tests/test_corpus.py
"""

from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

import pytest

import converter.converters  # noqa: F401  (registra i converter)
from converter.converters.docmodel import read_docx, read_pdf
from converter.converters.docmodel.types import (
    Document,
    ImageBlock,
    Line,
    ListBlock,
    ListItem,
    PageBreak,
    Paragraph,
    Run,
    Table,
    TableCell,
)

_ROOT = Path(__file__).resolve().parent
_EXPECTED = _ROOT / "corpus" / "expected"
_CORPUS = Path(os.environ.get("CONVERTER_CORPUS_DIR", _ROOT / "corpus" / "local"))
_CORPUS = _CORPUS.expanduser().resolve()
_UPDATE = os.environ.get("CORPUS_UPDATE", "").strip().lower() not in ("", "0", "false", "no")
_SUFFIXES = {".pdf": read_pdf, ".docx": read_docx}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _digest(document: Document) -> dict:
    """Struttura confrontabile: ordine, titoli, liste, tabelle, immagini, pagine."""
    blocks: list[dict] = []
    for block in document.blocks:
        if isinstance(block, Paragraph):
            if block.style in ("title", "heading"):
                entry = {"type": block.style, "text": _normalise(block.text)}
                if block.style == "heading":
                    entry["level"] = block.level
            else:
                entry = {"type": "body", "text": _normalise(block.text)}
            blocks.append(entry)
        elif isinstance(block, ListBlock):
            blocks.append({
                "type": "list",
                "ordered": block.ordered,
                "items": [{"level": item.level, "text": _normalise(item.paragraph.text)}
                          for item in block.items],
            })
        elif isinstance(block, Table):
            blocks.append({
                "type": "table",
                "rows": [[_normalise(text) for text in row] for row in block.text_grid()],
            })
        elif isinstance(block, ImageBlock):
            blocks.append({"type": "image", "width_pt": round(block.width_pt, 1),
                           "height_pt": round(block.height_pt, 1)})
        elif isinstance(block, PageBreak):
            blocks.append({"type": "page_break"})
    return {"title": _normalise(document.title), "blocks": blocks}


def _sources() -> list[Path]:
    if not _CORPUS.is_dir():
        return []
    return sorted(p for p in _CORPUS.rglob("*") if p.suffix.lower() in _SUFFIXES)


def _expected_path(source: Path) -> Path:
    relative = source.relative_to(_CORPUS)
    return _EXPECTED / relative.with_name(relative.name + ".json")


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


_SOURCES = _sources()


@pytest.mark.skipif(not _SOURCES,
                    reason=f"no corpus documents in {_CORPUS} (see tests/corpus/README.md)")
@pytest.mark.parametrize("source", _SOURCES,
                         ids=[str(p.relative_to(_CORPUS)) for p in _SOURCES])
def test_corpus_structure(source: Path):
    expected_path = _expected_path(source)
    try:
        document = _SUFFIXES[source.suffix.lower()](source)
    except ValueError as exc:
        if expected_path.exists():
            pytest.fail(f"Reading failed but a baseline exists: {exc}")
        if _UPDATE:
            pytest.skip(f"Unreadable document, no baseline to update: {exc}")
        pytest.xfail(f"Unreadable document, no baseline yet: {exc}")
    actual = _digest(document)

    if _UPDATE:
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        expected_path.write_text(_json(actual), encoding="utf-8")
        pytest.skip(f"baseline written to {expected_path.relative_to(_ROOT)}; review it")

    if not expected_path.exists():
        pytest.fail(
            f"Missing expected structure for {source.relative_to(_CORPUS)}.\n"
            f"Create the baseline with: CORPUS_UPDATE=1 python -m pytest -q tests/test_corpus.py\n"
            f"(then review {expected_path.relative_to(_ROOT)})")

    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if actual != expected:
        diff = difflib.unified_diff(
            _json(expected).splitlines(), _json(actual).splitlines(),
            fromfile=str(expected_path.relative_to(_ROOT)), tofile="actual", lineterm="")
        pytest.fail("Reconstructed structure changed:\n" + "\n".join(diff))


def test_digest_keeps_the_structure_that_matters():
    """Autotest dell'harness: se il digest perde pezzi, il corpus non misura nulla."""
    paragraph = Paragraph(lines=[Line(runs=[Run(text="Body")])])
    heading = Paragraph(lines=[Line(runs=[Run(text="1. Intro")])], style="heading", level=1)
    table = Table(rows=[[TableCell([paragraph]), TableCell([paragraph])]])
    document = Document(
        title="Report",
        blocks=[
            heading,
            paragraph,
            ListBlock(ordered=False, items=[ListItem("•", 1, paragraph)]),
            table,
            ImageBlock(data=b"x", width_pt=10.0, height_pt=20.0),
            PageBreak(),
        ],
    )
    assert _digest(document) == {
        "title": "Report",
        "blocks": [
            {"type": "heading", "text": "1. Intro", "level": 1},
            {"type": "body", "text": "Body"},
            {"type": "list", "ordered": False,
             "items": [{"level": 1, "text": "Body"}]},
            {"type": "table", "rows": [["Body", "Body"]]},
            {"type": "image", "width_pt": 10.0, "height_pt": 20.0},
            {"type": "page_break"},
        ],
    }
