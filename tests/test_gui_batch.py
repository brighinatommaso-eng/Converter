"""Test del batch nella GUI: Clear durante la conversione non deve incastrare l'UI.

Regressione BUG 4: con un batch in corso, premere Clear svuotava `model.items`;
il primo segnale `finished` sollevava IndexError in `_on_finished` (accesso a
`model.items[row]` senza guardia), `_completed` non veniva mai incrementato,
`_finish_batch` non partiva e `convert_btn` restava disabilitato per sempre.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PIL import Image
from PySide6.QtWidgets import QApplication

import converter.converters  # noqa: F401
from converter.core.job import ConversionResult
from converter.gui.main_window import MainWindow

_app = QApplication.instance() or QApplication([])


def test_clear_during_batch_does_not_wedge(tmp_path):
    win = MainWindow()
    # Simula un batch di 1 job in corso.
    win._total = 1
    win._completed = 0
    win._fractions = {0: 0.0}
    win.convert_btn.setEnabled(False)
    win.cancel_btn.setEnabled(True)

    win._clear()  # l'utente svuota la coda mentre converte

    # Arriva il finished del job: NON deve sollevare IndexError...
    win._on_finished(0, ConversionResult(ok=True, output_path=tmp_path / "x.jpg", output_size=1))

    # ...e il batch deve concludersi (UI sbloccata).
    assert win.convert_btn.isEnabled()
    assert not win.cancel_btn.isEnabled()


def test_clear_disabled_while_converting(tmp_path):
    png = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(png)
    win = MainWindow()
    win.output_dir = tmp_path
    win.same_as_source = False
    win._add_paths([png])
    it = win.model.items[0]
    it.dst_format = "bmp"  # lossless → niente dialog confirm_lossy che bloccherebbe
    it.verdict = next(t.verdict for t in it.targets if t.dst_format == "bmp")
    it.checked = True

    win._convert_all()  # avvia il batch (non pompiamo l'event loop: resta "in corso")

    assert not win.clear_btn.isEnabled()  # Clear disabilitato durante il batch

    # Drena il segnale finished mentre la finestra è ancora viva (evita il rumore
    # "Signal source has been deleted" quando il worker emette dopo il GC).
    win._cancel_all()
    _app.processEvents()


def test_add_paths_skips_vanished_files_without_crashing(tmp_path):
    """Un file che sparisce tra la selezione e l'elaborazione non deve far
    crashare l'aggiunta dell'intero lotto: viene saltato, gli altri entrano."""
    real = tmp_path / "ok.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(real)
    ghost = tmp_path / "gone.png"  # non esiste: path.stat() solleverebbe OSError

    win = MainWindow()
    win._add_paths([ghost, real])  # con il ghost per primo

    names = [it.src_path.name for it in win.model.items]
    assert names == ["ok.png"]  # il ghost è saltato, il reale è entrato
    assert "skipped" in win.status.text().lower()


def test_batch_same_stem_gets_distinct_paths_via_convert_all(tmp_path):
    """BUG 3 a livello GUI: due sorgenti con lo STESSO stem in cartelle diverse,
    verso la stessa cartella di output, devono ricevere path DISTINTI da
    _convert_all (che deve costruire e passare il set 'reserved'). Il test sul
    solo naming non prova questo cablaggio."""
    a = tmp_path / "dirA"; a.mkdir()
    b = tmp_path / "dirB"; b.mkdir()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(a / "photo.png")
    Image.new("RGB", (8, 8), (4, 5, 6)).save(b / "photo.png")
    out = tmp_path / "out"; out.mkdir()

    win = MainWindow()
    win.output_dir = out
    win.same_as_source = False
    win._add_paths([a / "photo.png", b / "photo.png"])
    for it in win.model.items:
        it.dst_format = "bmp"  # lossless → nessun dialog confirm_lossy
        it.verdict = next(t.verdict for t in it.targets if t.dst_format == "bmp")
        it.checked = True

    win._convert_all()  # assegna gli output_path in modo sincrono nel loop

    paths = [it.output_path for it in win.model.items]
    assert paths[0] != paths[1], "stesso path per due sorgenti → sovrascrittura silenziosa"
    assert {p.name for p in paths} == {"photo.bmp", "photo (1).bmp"}

    win._cancel_all()
    _app.processEvents()


def test_unwritable_output_folder_fails_the_row_without_wedging(tmp_path):
    """Una cartella di output non scrivibile non deve sollevare dal click né
    lasciare il batch appeso (Convert disabilitato per sempre): la riga fallisce
    con un messaggio chiaro e la UI torna utilizzabile."""
    png = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(png)
    blocked = tmp_path / "blocked"      # un FILE al posto della cartella
    blocked.write_text("not a directory", encoding="utf-8")

    win = MainWindow()
    win.output_dir = blocked
    win.same_as_source = False
    win._add_paths([png])               # default: png → bmp (lossless, niente dialog)

    win._convert_all()                  # non deve sollevare

    item = win.model.items[0]
    assert item.status == "Failed"
    assert "Could not prepare" in item.error
    assert win.convert_btn.isEnabled()
    assert not win.cancel_btn.isEnabled()
    assert win.clear_btn.isEnabled()


def test_changing_target_rearms_a_completed_row(tmp_path):
    """Dopo una conversione la riga è "Done": cambiare target/opzioni è un nuovo
    lavoro e deve tornare convertibile, altrimenti "Convert checked" la ignora
    in silenzio (convertible esclude Done)."""
    png = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(png)

    win = MainWindow()
    win._add_paths([png])
    win.table.selectRow(0)
    item = win.model.items[0]
    item.status = "Done"
    item.out_size = 123
    item.output_path = tmp_path / "old.bmp"
    assert win.model.items_to_convert() == []

    idx = win.options._target_combo.findData("jpg")
    assert idx >= 0
    win.options._target_combo.setCurrentIndex(idx)  # emette changed

    assert item.dst_format == "jpg"
    assert item.status == "Ready"
    assert item.out_size is None and item.output_path is None
    assert win.model.items_to_convert() == [item]
