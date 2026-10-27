"""Conversioni REALI di Fase 2 (pdfminer / motore markup / striprtf).

Eseguite in PORTABLE per provare che NON serve LibreOffice. Ogni gruppo fa skip se
il suo motore manca (come i test ffmpeg/document) — controllare il conteggio skip."""

from pathlib import Path

import pytest

import converter.converters  # noqa: F401  popola il registry (auto-registrazione)
from converter.core import deps, engine
from converter.core.job import ConversionJob
from converter.core.pipeline import run_job

markup = pytest.mark.skipif(not deps.has_module("markdown_it"), reason="markdown-it-py not installed")
pdfminer = pytest.mark.skipif(not deps.has_module("pdfminer"), reason="pdfminer.six not installed")
striprtf = pytest.mark.skipif(not deps.has_module("striprtf"), reason="striprtf not installed")

_ALL_PURE = (deps.has_module("markdown_it") and deps.has_module("pdfminer")
             and deps.has_module("striprtf"))
needs_all = pytest.mark.skipif(not _ALL_PURE, reason="needs markdown-it-py+pdfminer+striprtf")


@pytest.fixture(autouse=True)
def _portable():
    engine.set_mode("PORTABLE")  # tutto deve girare SENZA LibreOffice


def _run(src_path, src_fmt, dst_fmt, out):
    return run_job(ConversionJob(src_path=src_path, src_format=src_fmt,
                                 dst_format=dst_fmt, output_path=out))


def _make_min_pdf(path: Path, text: str = "Hello PDF") -> None:
    """Scrive un PDF minimo ma valido (xref con offset corretti) con una riga di
    testo, così pdfminer ha qualcosa da estrarre senza dipendenze esterne."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    ]
    stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET"
    objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF")
    path.write_bytes(bytes(out))


# --- motore markup (in-process, ha sostituito pandoc) ---------------------------

@markup
def test_md_to_html_lossless(tmp_path):
    src = tmp_path / "a.md"
    src.write_text("# Title\n\nHello **world**\n", encoding="utf-8")
    out = tmp_path / "a.html"
    r = _run(src, "md", "html", out)
    assert r.ok, r.error
    assert r.verdict.is_lossless
    body = out.read_text(encoding="utf-8")
    assert "<h1" in body and "Hello" in body


@markup
def test_html_to_md_lossy(tmp_path):
    src = tmp_path / "a.html"
    src.write_text("<h1>Title</h1><p>Hello <b>world</b></p>", encoding="utf-8")
    out = tmp_path / "a.md"
    r = _run(src, "html", "md", out)
    assert r.ok, r.error
    assert r.verdict.is_lossy
    assert "Title" in out.read_text(encoding="utf-8")


@markup
def test_md_to_docx_produces_real_docx(tmp_path):
    import zipfile
    src = tmp_path / "a.md"
    src.write_text("# Heading\n\nBody text.\n", encoding="utf-8")
    out = tmp_path / "a.docx"
    r = _run(src, "md", "docx", out)
    assert r.ok, r.error
    assert r.verdict.is_lossy
    assert zipfile.is_zipfile(out)  # un docx è un pacchetto ZIP valido


@markup
def test_md_to_epub_then_epub_to_html(tmp_path):
    md = tmp_path / "a.md"
    md.write_text("# Chapter\n\nContent here.\n", encoding="utf-8")
    epub = tmp_path / "a.epub"
    assert _run(md, "md", "epub", epub).ok
    assert epub.exists() and epub.stat().st_size > 0
    html = tmp_path / "from_epub.html"
    r = _run(epub, "epub", "html", html)
    assert r.ok, r.error
    assert "Content" in html.read_text(encoding="utf-8")


# --- striprtf ---------------------------------------------------------------

@striprtf
def test_rtf_to_txt_keeps_text_drops_formatting(tmp_path):
    src = tmp_path / "a.rtf"
    src.write_text(r"{\rtf1\ansi Hello \b bold\b0 world}", encoding="utf-8")
    out = tmp_path / "a.txt"
    r = _run(src, "rtf", "txt", out)
    assert r.ok, r.error
    assert r.verdict.is_lossy
    text = out.read_text(encoding="utf-8")
    assert "Hello" in text and "world" in text and "\\b" not in text


# --- pdfminer ---------------------------------------------------------------

@pdfminer
def test_pdf_to_txt_extracts_text(tmp_path):
    src = tmp_path / "a.pdf"
    _make_min_pdf(src, "Hello PDF")
    out = tmp_path / "a.txt"
    r = _run(src, "pdf", "txt", out)
    assert r.ok, r.error
    assert r.verdict.is_lossy
    assert "Hello" in out.read_text(encoding="utf-8")


@pdfminer
def test_pdf_to_html_produces_markup(tmp_path):
    src = tmp_path / "a.pdf"
    _make_min_pdf(src, "Hello PDF")
    out = tmp_path / "a.html"
    r = _run(src, "pdf", "html", out)
    assert r.ok, r.error
    assert r.verdict.is_lossy
    assert "<" in out.read_text(encoding="utf-8")  # è HTML


# --- "Funziona senza nulla installato": LibreOffice ASSENTE per davvero -------

@needs_all
def test_portable_conversions_run_with_libreoffice_absent(tmp_path, monkeypatch):
    """Il senso di PORTABLE è "funziona senza installazioni". Qui LibreOffice è
    forzato ASSENTE (resolver → None) E il suo percorso d'esecuzione è una TRAPPOLA:
    se un converter ci ricadesse, il test fallirebbe. md→docx / pdf→txt / rtf→txt
    devono comunque andare a buon fine via i motori puri."""
    monkeypatch.setattr(deps, "find_soffice", lambda: None)

    import converter.converters.document as docmod

    def _boom(*args, **kwargs):
        raise AssertionError("LibreOffice (run_convert) was invoked in Portable mode!")

    monkeypatch.setattr(docmod, "run_convert", _boom)

    engine.set_mode("PORTABLE")
    assert deps.find_soffice() is None  # l'app NON vede LibreOffice

    from converter.core.registry import resolve
    # Ogni coppia è instradata a un motore PURO, mai al DocumentConverter.
    assert resolve("md", "docx").__class__.__name__ == "MarkupConverter"
    assert resolve("pdf", "txt").__class__.__name__ == "TextDocConverter"
    assert resolve("rtf", "txt").__class__.__name__ == "RtfTextConverter"

    # E le conversioni REALI riescono senza LibreOffice presente.
    md = tmp_path / "a.md"
    md.write_text("# Heading\n\nBody.\n", encoding="utf-8")
    assert _run(md, "md", "docx", tmp_path / "a.docx").ok

    pdf = tmp_path / "a.pdf"
    _make_min_pdf(pdf, "Hello PDF")
    out_txt = tmp_path / "a.txt"
    assert _run(pdf, "pdf", "txt", out_txt).ok
    assert "Hello" in out_txt.read_text(encoding="utf-8")

    rtf = tmp_path / "a.rtf"
    rtf.write_text(r"{\rtf1\ansi Hi there}", encoding="utf-8")
    out_rtf = tmp_path / "r.txt"
    assert _run(rtf, "rtf", "txt", out_rtf).ok
    assert "Hi" in out_rtf.read_text(encoding="utf-8")
