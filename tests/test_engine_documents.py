"""La mode-awareness alla prova: in PORTABLE le conversioni office (LibreOffice)
compaiono in grigio con un motivo chiaro; in FULL con LibreOffice presente sono
disponibili. Il verdetto onesto resta invariato in entrambe."""

from converter.core import deps, engine
from converter.core.registry import targets_for


def _docx_targets():
    return {t.dst_format: t for t in targets_for("docx")}


def test_office_pair_available_in_full(monkeypatch):
    monkeypatch.setattr(deps, "find_soffice", lambda: r"C:\soffice.exe")
    engine.set_mode("FULL")
    odt = _docx_targets()["odt"]
    assert odt.available is True
    assert odt.verdict.is_lossy  # docx→odt è best-effort: lossy, ma onesto


def test_office_pair_greyed_in_portable(monkeypatch):
    monkeypatch.setattr(deps, "find_soffice", lambda: r"C:\soffice.exe")
    engine.set_mode("PORTABLE")
    odt = _docx_targets()["odt"]
    assert odt.available is False
    assert "Full mode" in odt.reason_unavailable
    # Il verdetto (qualità) non cambia: cambia solo la disponibilità.
    assert odt.verdict.is_lossy


def test_portable_reason_is_not_install_hint(monkeypatch):
    """In PORTABLE il greying NON deve suggerire di installare LibreOffice: non è
    un problema d'installazione, è una promessa della modalità FULL."""
    from converter.converters.document import DocumentConverter

    monkeypatch.setattr(deps, "find_soffice", lambda: r"C:\soffice.exe")
    engine.set_mode("PORTABLE")
    ds = DocumentConverter.available()
    assert ds.ok is False
    assert ds.install_hint == ""
