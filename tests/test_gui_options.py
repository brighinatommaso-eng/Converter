"""Test del pannello opzioni: i valori scelti non devono andare persi.

Regressione: selezionare un'altra riga e tornare indietro (o applicare un
preset e poi rinfrescare il pannello) deve PRESERVARE le opzioni impostate.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # nessun display nei test

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import converter.converters  # noqa: F401
from converter.core.detection import DetectedFormat
from converter.core.registry import targets_for
from converter.gui.options_panel import OptionsPanel
from converter.gui.queue_model import QueueItem

_app = QApplication.instance() or QApplication([])


def _png_item() -> QueueItem:
    detected = DetectedFormat("png", "image", 0.99, "magic")
    return QueueItem(src_path=Path("x.png"), detected=detected,
                     targets=targets_for("png"), src_size=100)


def _select_target(panel: OptionsPanel, fmt: str) -> None:
    combo = panel._target_combo
    for i in range(combo.count()):
        if combo.itemData(i) == fmt:
            combo.setCurrentIndex(i)
            return
    raise AssertionError(f"target {fmt} not found")


def test_options_survive_reselect():
    panel = OptionsPanel()
    item = _png_item()
    panel.set_item(item)
    _select_target(panel, "jpg")
    panel._set_opt("quality", 70)            # l'utente abbassa la qualità
    assert item.options["quality"] == 70

    panel.set_item(None)                      # cambia selezione
    panel.set_item(item)                      # torna sulla stessa riga
    assert item.options["quality"] == 70      # deve essere preservata, non 95
