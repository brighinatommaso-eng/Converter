"""Test della costruzione dei path di output e delle collisioni.

Regressione BUG 3: in un batch, due sorgenti con lo STESSO stem verso la stessa
cartella di output risolvevano entrambe allo stesso file (es. photo.jpg) perché
`resolve_collision` guardava solo `path.exists()` — e nessun output è ancora su
disco quando i path vengono assegnati. Due worker scrivevano lo stesso file.
"""

from pathlib import Path

from converter.core.naming import build_output_path, resolve_collision


def test_resolve_collision_honors_reserved(tmp_path):
    t = tmp_path / "x.jpg"
    assert resolve_collision(t) == t                       # libero su disco e non riservato
    # riservato nel batch anche se non esiste su disco → deve saltare
    assert resolve_collision(t, {t}) == tmp_path / "x (1).jpg"


def test_batch_same_stem_gets_distinct_outputs(tmp_path):
    a = tmp_path / "dirA"; a.mkdir()
    b = tmp_path / "dirB"; b.mkdir()
    (a / "photo.png").write_bytes(b"x")
    (b / "photo.png").write_bytes(b"y")
    out = tmp_path / "out"; out.mkdir()

    reserved: set[Path] = set()
    p1 = build_output_path(a / "photo.png", "jpg", out, False, reserved)
    reserved.add(p1)
    p2 = build_output_path(b / "photo.png", "jpg", out, False, reserved)
    reserved.add(p2)

    assert p1 != p2
    assert p1.name == "photo.jpg"
    assert p2.name == "photo (1).jpg"
