"""Regressioni per i rischi 9-12 di BUGS.md (robustezza e igiene).

9  — pdf_import: il gate scansione legge solo le prime pagine (no estrazione
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

    import converter.converters.pdf_import as pi

    captured: dict = {}

    def fake_extract(path, **kw):
        captured.update(kw)
        return "questo pdf ha del testo reale " * 3

    monkeypatch.setattr(hl, "extract_text", fake_extract)
    assert pi._has_extractable_text(Path("x.pdf")) is True
    assert captured.get("maxpages") == pi._SCAN_CHECK_MAX_PAGES  # cap applicato


# --- Rischio 12: _dumpenv/_selftest sopravvivono a sys.stdout None --------------

def test_dumpenv_survives_no_stdout(monkeypatch, tmp_path):
    import converter_app

    monkeypatch.setattr(converter_app.sys, "stdout", None)
    out = tmp_path / "env.txt"
    rc = converter_app._dumpenv(str(out))
    assert rc == 0 and out.exists()  # niente AttributeError su stdout None
