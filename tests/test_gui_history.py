"""Test del re-run dalla cronologia, incluso il caso sorgente mancante."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PIL import Image
from PySide6.QtWidgets import QApplication

import converter.converters  # noqa: F401
from converter.core.history import HistoryEntry
from converter.gui.main_window import MainWindow

_app = QApplication.instance() or QApplication([])


def test_rerun_missing_source_fails_cleanly(tmp_path):
    win = MainWindow()
    entry = HistoryEntry(str(tmp_path / "gone.png"), "png", "jpg", {"quality": 85})
    win._rerun_entry(entry)
    # Fallimento pulito: messaggio chiaro, NIENTE in coda, nessun output.
    assert "source no longer exists" in win.status.text().lower()
    assert len(win.model.items) == 0


def test_rerun_existing_source_enqueues_with_settings(tmp_path):
    png = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(png)
    win = MainWindow()
    entry = HistoryEntry(str(png), "png", "jpg", {"quality": 80}, strip_metadata=True)
    win._rerun_entry(entry)
    assert len(win.model.items) == 1
    item = win.model.items[0]
    assert item.dst_format == "jpg"
    assert item.options.get("quality") == 80
    assert item.strip_metadata is True
    assert item.verdict.is_lossy
