"""txt→md: il testo resta testo (prima pandoc lo rifondeva come markdown)."""

from __future__ import annotations

import converter.converters  # noqa: F401
from converter.converters.plaintext import PlainTextMarkdownConverter, text_to_markdown
from converter.core import deps, registry
from converter.core.job import ConversionJob
from converter.core.pipeline import run_job


def test_txt_to_md_is_routed_in_process_even_without_pandoc(monkeypatch):
    monkeypatch.setattr(deps, "find_pandoc", lambda: None)
    assert isinstance(registry.resolve("txt", "md"), PlainTextMarkdownConverter)


def test_markdown_syntax_in_text_is_escaped():
    md = text_to_markdown("# not heading\n*not* emphasis\n1. not a list\n- no bullet\na | b\n")
    lines = md.splitlines()
    assert lines[0].startswith("\\#")
    assert "\\*not\\*" in lines[1]
    assert lines[2].startswith("1\\.")
    assert lines[3].startswith("\\-")
    assert "\\|" in lines[4]


def test_line_breaks_and_paragraphs_are_kept():
    md = text_to_markdown("line one\nline two\n\nsecond paragraph\n")
    assert md == "line one\\\nline two\n\nsecond paragraph\n"


def test_indentation_does_not_become_a_code_block():
    md = text_to_markdown("    indented\n")
    assert md.startswith("&nbsp;&nbsp;&nbsp;&nbsp;indented")


def test_txt_to_md_end_to_end_is_lossless(tmp_path):
    src = tmp_path / "note.txt"
    src.write_bytes("città *bold*\n".encode("cp1252"))
    out = tmp_path / "note.md"
    result = run_job(ConversionJob(src, "txt", "md", out))
    assert result.ok and result.verdict.is_lossless
    assert out.read_text(encoding="utf-8") == "città \\*bold\\*\n"
