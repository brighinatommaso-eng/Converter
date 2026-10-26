"""Regressioni per i rischi 9-12 di BUGS.md (robustezza e igiene).

9  — _ocr: il gate scansione legge solo le prime pagine (no estrazione
     completa, per giunta doppia).
10 — _ffmpeg: probe_duration ha un timeout; run_ffmpeg pulisce l'output su
     cancel/errore (vedi anche 11).
11 — output parziali rimossi su fallimento (pandoc, pdfminer, ffmpeg).
12 — converter_app: stdout può essere None nell'exe windowed → guardia.
"""

from pathlib import Path

import pytest

import converter.converters  # noqa: F401
from converter.core.job import ConversionJob


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# --- Rischio 10: probe_duration usa un timeout ---------------------------------

def test_probe_duration_passes_timeout(monkeypatch):
    import converter.converters._ffmpeg as ff

    monkeypatch.setattr(ff, "find_ffprobe", lambda: "C:/fake/ffprobe.exe")
    captured: dict = {}

    def fake_run(args, **kw):
        captured.update(kw)
        return _FakeProc(stdout='{"format": {"duration": "3.5"}}')

    monkeypatch.setattr(ff.subprocess, "run", fake_run)
    assert ff.probe_duration(Path("x.mp4")) == 3.5
    assert captured.get("timeout")  # timeout duro passato a subprocess.run


# --- Rischio 11: pandoc rimuove l'output parziale su fallimento -----------------

def test_pandoc_removes_partial_output_on_failure(monkeypatch, tmp_path):
    import converter.converters.markup as mk

    monkeypatch.setattr(mk.deps, "find_pandoc", lambda: "C:/fake/pandoc.exe")
    monkeypatch.setattr(mk.subprocess, "run",
                        lambda *a, **k: _FakeProc(returncode=1, stderr="boom"))

    src = tmp_path / "a.md"
    src.write_text("# hi", encoding="utf-8")
    out = tmp_path / "o.html"
    out.write_text("output scritto a metà", encoding="utf-8")  # partial di -o

    res = mk.MarkupConverter().convert(ConversionJob(src, "md", "html", out), lambda e: None)
    assert not res.ok
    assert not out.exists()  # parziale rimosso, non lasciato accanto all'errore


def test_pandoc_timeout_removes_partial_output(monkeypatch, tmp_path):
    import subprocess

    import converter.converters.markup as mk

    monkeypatch.setattr(mk.deps, "find_pandoc", lambda: "C:/fake/pandoc.exe")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pandoc", timeout=1)

    monkeypatch.setattr(mk.subprocess, "run", boom)

    src = tmp_path / "a.md"
    src.write_text("# hi", encoding="utf-8")
    out = tmp_path / "o.html"
    out.write_text("partial", encoding="utf-8")

    res = mk.MarkupConverter().convert(ConversionJob(src, "md", "html", out), lambda e: None)
    assert not res.ok and "timed out" in res.error.lower()
    assert not out.exists()


# --- Rischio 11: pdfminer rimuove l'output parziale su fallimento ---------------

def test_pdf_removes_partial_output_on_failure(monkeypatch, tmp_path):
    pytest.importorskip("pdfminer")
    import pdfminer.high_level as hl

    from converter.converters.pdf import PdfConverter

    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4 non valido")
    out = tmp_path / "o.txt"

    def boom(*a, **k):
        out.write_text("meta pagina", encoding="utf-8")  # scrittura parziale…
        raise ValueError("bad pdf")                       # …poi errore

    monkeypatch.setattr(hl, "extract_text", boom)
    res = PdfConverter().convert(ConversionJob(src, "pdf", "txt", out), lambda e: None)
    assert not res.ok
    assert not out.exists()


# --- Rischio 9: il gate scansione legge solo le prime pagine --------------------

def test_scan_gate_reads_limited_pages(monkeypatch):
    pytest.importorskip("pdfminer")
    import pdfminer.high_level as hl

    import converter.converters._ocr as ocr

    captured: dict = {}

    def fake_extract(path, **kw):
        captured.update(kw)
        return "questo pdf ha del testo reale " * 3

    monkeypatch.setattr(hl, "extract_text", fake_extract)
    assert ocr.has_extractable_text(Path("x.pdf")) is True
    assert captured.get("maxpages") == ocr.SCAN_CHECK_MAX_PAGES  # cap applicato


# --- Rischio 12: _dumpenv/_selftest sopravvivono a sys.stdout None --------------

def test_dumpenv_survives_no_stdout(monkeypatch, tmp_path):
    import converter_app

    monkeypatch.setattr(converter_app.sys, "stdout", None)
    out = tmp_path / "env.txt"
    rc = converter_app._dumpenv(str(out))
    assert rc == 0 and out.exists()  # niente AttributeError su stdout None


# --- Annulla: i job in coda non partono (review 2026-10-26) -------------------

def test_cancelled_job_never_starts(tmp_path):
    from PIL import Image

    from converter.core.job import ConversionJob
    from converter.core.pipeline import run_job

    src = tmp_path / "a.png"
    Image.new("RGB", (8, 8)).save(src)
    out = tmp_path / "a.jpg"
    job = ConversionJob(src, "png", "jpg", out)
    job.cancel.cancel()          # Annulla premuto mentre il job era in coda
    result = run_job(job)
    assert result.ok is False and result.cancelled and result.error == "Cancelled."
    assert not out.exists()


# --- Licenze GPL nel bundle (review 2026-10-26) --------------------------------

def test_gpl_license_texts_ship_with_the_build():
    root = Path(__file__).resolve().parent.parent
    for name, first_line in (("ffmpeg-GPLv3.txt", "GNU GENERAL PUBLIC LICENSE"),
                             ("ffmpeg-LGPLv2.1.txt", "GNU LESSER GENERAL PUBLIC LICENSE"),
                             ("pandoc-GPLv2.txt", "GNU GENERAL PUBLIC LICENSE"),
                             ("Qt-PySide6-LGPLv3.txt", "GNU LESSER GENERAL PUBLIC LICENSE")):
        text = (root / "licenses" / name).read_text(encoding="utf-8")
        assert first_line in text[:200], name
    spec = (root / "converter.spec").read_text(encoding="utf-8")
    assert 'shutil.copytree("licenses"' in spec and "THIRD_PARTY_NOTICES.md" in spec


def test_spec_and_selftest_agree_on_the_excluded_modules():
    """Lo spec esclude dal bundle e il selftest verifica: i due elenchi devono coincidere."""
    import ast

    import converter_app

    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "converter.spec").read_text(encoding="utf-8"))
    spec_list = next(ast.literal_eval(node.value) for node in ast.walk(tree)
                     if isinstance(node, ast.Assign)
                     and getattr(node.targets[0], "id", "") == "UNUSED_OPTIONAL_MODULES")
    assert tuple(spec_list) == converter_app._UNUSED_OPTIONAL_MODULES
    # Nessun modulo usato davvero da Converter tra gli esclusi.
    assert not {"pdfminer", "docx", "lxml", "PIL", "pandas", "numpy", "trimesh",
                "cryptography", "yaml", "openpyxl", "odf"} & set(spec_list)
