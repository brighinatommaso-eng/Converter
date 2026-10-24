"""Regressioni per i rischi 13-17 di BUGS.md.

13 — document.py: src==dst dichiarato lossless ma faceva un round-trip reale.
14 — image.py: sorgenti multi-frame perdevano tutto tranne il primo, in silenzio.
15 — image.py: EXIF Orientation né applicata né preservata verso png/bmp.
16 — deps.find_ghostscript: sort lessicografico delle versioni.
17 — pdf→docx: il gate della dipendenza non deve richiedere LibreOffice.
"""

from pathlib import Path

from PIL import Image

import converter.converters  # noqa: F401
from converter.core import deps, engine
from converter.core.job import ConversionJob
from converter.core.registry import resolve

# --- Rischio 13: docx→docx (stesso formato) è una copia onesta lossless ---------

def test_same_format_document_is_byte_copy(tmp_path):
    from converter.converters.document import DocumentConverter

    src = tmp_path / "a.docx"
    src.write_bytes(b"PK\x03\x04 finto ma sufficiente per una copia")
    out = tmp_path / "b.docx"

    res = DocumentConverter().convert(ConversionJob(src, "docx", "docx", out), lambda e: None)
    assert res.ok
    assert res.verdict.is_lossless
    assert out.read_bytes() == src.read_bytes()  # copia byte-a-byte, niente round-trip


# --- Rischio 16: ordinamento numerico delle versioni di Ghostscript -------------

def test_ghostscript_version_sort_is_numeric():
    from converter.core.deps import _gs_version_key

    dirs = [Path("gs9.56"), Path("gs10.02"), Path("gs9.06")]
    ordered = sorted(dirs, key=_gs_version_key, reverse=True)
    assert ordered[0].name == "gs10.02"  # 10.x batte 9.x (non lessicografico)


# --- Rischio 17: pdf→docx disponibile senza LibreOffice; odt/rtf no -------------

def test_pdf_to_docx_resolves_without_soffice(monkeypatch):
    engine.set_mode("FULL")
    monkeypatch.setattr(deps, "has_module", lambda name: True)
    monkeypatch.setattr(deps, "find_soffice", lambda: None)
    from converter.converters.textdoc import TextDocConverter

    assert isinstance(resolve("pdf", "docx"), TextDocConverter)  # in-process
    conv = TextDocConverter()
    assert conv.pair_available("pdf", "docx")[0] is True
    # odt/rtf richiedono soffice → coppia non disponibile (run_job la rifiuterebbe).
    assert conv.pair_available("pdf", "odt")[0] is False


# --- Rischio 14: sorgenti multi-frame ------------------------------------------

def _animated_gif(path: Path, n: int = 3) -> None:
    frames = [Image.new("RGB", (8, 8), (c, 0, 0)) for c in (10, 120, 240)][:n]
    frames[0].save(path, save_all=True, append_images=frames[1:], loop=0, duration=100)


def test_animated_gif_to_gif_preserves_frames(tmp_path):
    from converter.converters.image import ImageConverter

    src = tmp_path / "a.gif"
    _animated_gif(src, 3)
    out = tmp_path / "b.gif"
    res = ImageConverter().convert(ConversionJob(src, "gif", "gif", out), lambda e: None)
    assert res.ok
    with Image.open(out) as im:
        assert getattr(im, "n_frames", 1) == 3  # tutti i frame preservati


def test_animated_gif_to_png_is_lossy_first_frame(tmp_path):
    from converter.converters.image import ImageConverter

    src = tmp_path / "a.gif"
    _animated_gif(src, 3)
    out = tmp_path / "b.png"
    res = ImageConverter().convert(ConversionJob(src, "gif", "png", out), lambda e: None)
    assert res.ok
    # png tiene un solo frame → il verdetto NON può essere lossless.
    assert res.verdict.is_lossy
    assert "first" in res.verdict.reason.lower()


# --- Rischio 15: EXIF Orientation applicata ai pixel verso png ------------------

def test_exif_orientation_applied_to_png(tmp_path):
    from converter.converters.image import ImageConverter

    src = tmp_path / "rot.jpg"
    base = Image.new("RGB", (20, 10), (0, 0, 0))  # largo 20×10
    exif = base.getexif()
    exif[0x0112] = 6  # Orientation = ruota 90° → la foto "corretta" è 10×20
    base.save(src, exif=exif)

    out = tmp_path / "rot.png"
    res = ImageConverter().convert(ConversionJob(src, "jpg", "png", out), lambda e: None)
    assert res.ok
    with Image.open(out) as im:
        assert im.size == (10, 20)  # pixel trasposti: l'orientamento è stato applicato
