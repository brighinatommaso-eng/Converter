"""La barra ha un toggle modalità: cambiarlo deve ri-grigiare/ri-abilitare i
target Office della coda DAL VIVO, ma SOLO per la sessione — NON deve persistere
nulla su disco (usare il toggle non è "ricorda la scelta"). Regressione del
comportamento verificato a mano sul platform nativo."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import converter.converters  # noqa: E402,F401  popola il registry
from converter.core import deps, engine, paths  # noqa: E402
from converter.core.detection import DetectedFormat  # noqa: E402
from converter.core.registry import targets_for  # noqa: E402
from converter.gui.main_window import MainWindow  # noqa: E402
from converter.gui.queue_model import QueueItem  # noqa: E402


def _app():
    return QApplication.instance() or QApplication([])


def _docx_item():
    return QueueItem(
        src_path=Path("sample.docx"),
        detected=DetectedFormat("docx", "document", 1.0, "ext"),
        targets=targets_for("docx"),
        src_size=0,
        dst_format="odt",
    )


def test_toggle_regreys_queue_session_only_no_persist(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)  # niente scrittura reale
    monkeypatch.setattr(deps, "find_soffice", lambda: r"C:\soffice.exe")
    _app()
    engine.set_mode("FULL")

    w = MainWindow()
    w.sync_engine_mode_ui()
    item = _docx_item()
    w.model.add_item(item)

    def odt():
        return next(t for t in item.targets if t.dst_format == "odt")

    # FULL: target Office disponibile, pulsante Full evidenziato.
    assert odt().available is True
    assert w.mode_full_btn.objectName() == "Primary"

    # Switch a PORTABLE: la riga si ri-grigia subito, con motivo onesto.
    w._set_engine_mode("PORTABLE")
    assert engine.get_mode() == "PORTABLE"
    assert odt().available is False
    assert "Full mode" in odt().reason_unavailable
    assert w.mode_portable_btn.objectName() == "Primary"
    assert w.mode_full_btn.objectName() == ""
    # Il toggle NON persiste: settings.json non deve aver acquisito engine_mode.
    assert paths.read_setting("engine_mode") is None

    # Ritorno a FULL: la riga torna disponibile dal vivo, ancora senza scrivere.
    w._set_engine_mode("FULL")
    assert odt().available is True
    assert w.mode_full_btn.objectName() == "Primary"
    assert paths.read_setting("engine_mode") is None
