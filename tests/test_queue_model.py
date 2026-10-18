"""Test del modello della coda: la spunta per-file governa il batch.

Regressione mirata (#3): i file NON spuntati devono essere esclusi da
items_to_convert(), così "Convert checked" non li tocca.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # nessun display nei test

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from converter.core.detection import DetectedFormat
from converter.gui.queue_model import QueueItem, QueueModel

_app = QApplication.instance() or QApplication([])


def _item(name: str, dst: str | None = "png", status: str = "Ready",
          checked: bool = True) -> QueueItem:
    det = DetectedFormat("jpg", "image", 0.99, "magic")
    return QueueItem(src_path=Path(name), detected=det, targets=[], src_size=10,
                     dst_format=dst, status=status, checked=checked)


def test_unchecked_files_excluded_from_batch():
    model = QueueModel()
    model.add_item(_item("a.jpg", checked=True))
    model.add_item(_item("b.jpg", checked=False))   # non spuntato -> escluso
    model.add_item(_item("c.jpg", checked=True))

    names = [it.src_path.name for it in model.items_to_convert()]
    assert names == ["a.jpg", "c.jpg"]


def test_unconvertible_and_in_progress_excluded_even_if_checked():
    model = QueueModel()
    model.add_item(_item("no-target.jpg", dst=None, checked=True))      # non convertibile
    model.add_item(_item("running.jpg", status="Converting", checked=True))
    model.add_item(_item("ok.jpg", checked=True))

    assert [it.src_path.name for it in model.items_to_convert()] == ["ok.jpg"]


def test_checkbox_roundtrip_via_setdata():
    model = QueueModel()
    model.add_item(_item("a.jpg", checked=True))
    idx = model.index(0, 0)

    # Stato iniziale esposto come CheckStateRole.
    assert model.data(idx, Qt.CheckStateRole) == Qt.Checked
    # La colonna File è spuntabile.
    assert model.flags(idx) & Qt.ItemIsUserCheckable

    # Togliere la spunta via setData aggiorna il modello e lo esclude dal batch.
    assert model.setData(idx, Qt.Unchecked, Qt.CheckStateRole) is True
    assert model.items[0].checked is False
    assert model.data(idx, Qt.CheckStateRole) == Qt.Unchecked
    assert model.items_to_convert() == []
