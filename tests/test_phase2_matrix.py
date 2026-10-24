"""La matrice di capacità di Fase 2, resa eseguibile.

Verifica disponibilità/engine/verdetto per ogni coppia chiave, in FULL e PORTABLE,
con TUTTI i motori "presenti" (monkeypatch): così la matrice è deterministica a
prescindere da cosa è installato sulla macchina. Le conversioni REALI stanno in
test_phase2_converters.py."""

import pytest

import converter.converters  # noqa: F401  popola il registry (auto-registrazione)
from converter.core import deps, engine
from converter.core.registry import resolve, targets_for


@pytest.fixture
def all_engines(monkeypatch):
    """Finge presenti LibreOffice, pandoc, pdfminer, striprtf e python-docx."""
    monkeypatch.setattr(deps, "find_soffice", lambda: r"C:\LibreOffice\program\soffice.exe")
    monkeypatch.setattr(deps, "find_pandoc", lambda: r"C:\pandoc.exe")
    real_has = deps.has_module
    monkeypatch.setattr(deps, "has_module",
                        lambda n: True if n in ("pdfminer", "striprtf", "docx") else real_has(n))


def _targets(src):
    return {t.dst_format: t for t in targets_for(src)}


def _engine(src, dst):
    conv = resolve(src, dst)
    return conv.__class__.__name__ if conv else None


# --- PDF (motore strutturale, entrambe le modalità) ---------------------------

@pytest.mark.parametrize("mode", ["FULL", "PORTABLE"])
def test_pdf_to_text_documents_via_structured_engine(all_engines, mode):
    engine.set_mode(mode)
    t = _targets("pdf")
    # pdf→md è una coppia NUOVA: prima non esisteva alcun percorso.
    for dst in ("docx", "md", "txt", "html"):
        assert t[dst].available is True, dst
        assert t[dst].verdict.is_lossy
        assert _engine("pdf", dst) == "TextDocConverter"
    # odt/rtf: docx intermedio + secondo salto LibreOffice → solo FULL.
    if mode == "FULL":
        for dst in ("odt", "rtf"):
            assert t[dst].available is True, dst
            assert _engine("pdf", dst) == "TextDocConverter"
    else:
        for dst in ("odt", "rtf"):
            assert t[dst].available is False, dst
            assert "Full mode" in t[dst].reason_unavailable


# --- Markup md/epub (pandoc, entrambe le modalità) --------------------------

@pytest.mark.parametrize("mode", ["FULL", "PORTABLE"])
def test_md_conversions_available_both_modes(all_engines, mode):
    engine.set_mode(mode)
    t = _targets("md")
    assert t["html"].available is True and t["html"].verdict.is_lossless
    assert t["docx"].available is True and t["docx"].verdict.is_lossy  # md→docx (richiesto)
    assert _engine("md", "html") == "MarkupConverter"
    assert _engine("md", "docx") == "MarkupConverter"
    # md→pdf è IMPOSSIBILE (LibreOffice non legge md, pandoc non scrive pdf).
    assert "pdf" not in t


# --- Office come sorgente: override solo sui target che sacrificano il layout -

def test_docx_markup_targets_use_structured_engine_office_targets_use_libreoffice(all_engines):
    engine.set_mode("FULL")
    assert _engine("docx", "html") == "DocumentConverter"  # fedeltà colori → LibreOffice
    assert _engine("docx", "txt") == "TextDocConverter"
    assert _engine("docx", "md") == "TextDocConverter"
    assert _engine("docx", "odt") == "DocumentConverter"  # fedeltà → LibreOffice
    assert _engine("docx", "pdf") == "DocumentConverter"  # export PDF → LibreOffice


def test_docx_office_targets_greyed_in_portable(all_engines):
    engine.set_mode("PORTABLE")
    t = _targets("docx")
    # md/txt restano disponibili (motore strutturale in-process); html ora la
    # rivendica solo LibreOffice, quindi in PORTABLE compare in grigio, come odt/pdf.
    assert t["html"].available is False and "Full mode" in t["html"].reason_unavailable
    assert t["md"].available is True
    assert t["txt"].available is True
    assert t["odt"].available is False and "Full mode" in t["odt"].reason_unavailable
    assert t["pdf"].available is False and "Full mode" in t["pdf"].reason_unavailable


def test_docx_html_routes_to_libreoffice_in_full(all_engines):
    engine.set_mode("FULL")
    conv = resolve("docx", "html")
    assert conv.__class__.__name__ == "DocumentConverter"


# --- RTF: rtf→txt ovunque; rtf→html solo FULL; rtf→md/epub assenti ----------

@pytest.mark.parametrize("mode", ["FULL", "PORTABLE"])
def test_rtf_to_txt_via_striprtf_both_modes(all_engines, mode):
    engine.set_mode(mode)
    t = _targets("rtf")
    assert t["txt"].available is True and t["txt"].verdict.is_lossy
    assert _engine("rtf", "txt") == "RtfTextConverter"
    # rtf→md ed rtf→epub: nessun lettore+scrittore → coppia assente.
    assert "md" not in t and "epub" not in t


def test_rtf_to_html_is_full_only(all_engines):
    engine.set_mode("FULL")
    assert _targets("rtf")["html"].available is True
    engine.set_mode("PORTABLE")
    rtf_html = _targets("rtf")["html"]
    assert rtf_html.available is False and "Full mode" in rtf_html.reason_unavailable


# --- Export PDF dai formati leggibili da LibreOffice: solo FULL --------------

@pytest.mark.parametrize("src", ["html", "txt", "docx", "odt", "rtf"])
def test_to_pdf_is_full_only(all_engines, src):
    engine.set_mode("FULL")
    assert _targets(src)["pdf"].available is True
    engine.set_mode("PORTABLE")
    pdf = _targets(src)["pdf"]
    assert pdf.available is False and "Full mode" in pdf.reason_unavailable
