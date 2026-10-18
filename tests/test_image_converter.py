"""Test end-to-end del converter immagini (un converter per categoria)."""

from PIL import ExifTags, Image

import converter.converters  # noqa: F401
from converter.core.job import ConversionJob
from converter.core.registry import resolve


def _make_png(path):
    img = Image.new("RGB", (16, 16))
    img.putpixel((0, 0), (10, 20, 30))
    img.putpixel((15, 15), (200, 100, 50))
    img.save(path, format="PNG")


def _make_gps_jpeg(path):
    """JPEG con un tag GPS EXIF noto (lat 51°30'N, lon 7'W) + Model in IFD0."""
    exif = Image.Exif()
    exif[0x0110] = "TEST-CAM"  # Model (IFD0)
    exif[ExifTags.IFD.GPSInfo] = {1: "N", 2: (51.0, 30.0, 0.0), 3: "W", 4: (0.0, 7.0, 0.0)}
    Image.new("RGB", (16, 16), (120, 60, 30)).save(path, format="JPEG", exif=exif)


def _gps_ifd(path):
    """Rilegge il file dal disco e restituisce il GPS IFD (vuoto se assente)."""
    return dict(Image.open(path).getexif().get_ifd(ExifTags.IFD.GPSInfo))


def test_png_to_bmp_is_pixel_perfect(tmp_path):
    src = tmp_path / "a.png"
    out = tmp_path / "a.bmp"
    _make_png(src)
    conv = resolve("png", "bmp")
    result = conv.convert(ConversionJob(src, "png", "bmp", out), lambda e: None)
    assert result.ok and out.exists()
    assert result.verdict.is_lossless
    # Lossless: i byte dei pixel devono coincidere esattamente.
    before = Image.open(src).convert("RGB").tobytes()
    after = Image.open(out).convert("RGB").tobytes()
    assert before == after


def test_png_to_jpg_produces_output(tmp_path):
    src = tmp_path / "a.png"
    out = tmp_path / "a.jpg"
    _make_png(src)
    conv = resolve("png", "jpg")
    result = conv.convert(
        ConversionJob(src, "png", "jpg", out, options={"quality": 95}), lambda e: None
    )
    assert result.ok and out.exists()
    assert result.output_size and result.output_size > 0
    assert result.verdict.is_lossy
    assert Image.open(out).format == "JPEG"


def test_strip_metadata_removes_gps_exif(tmp_path):
    """Strip ON: il tag GPS noto deve essere ASSENTE nel file di output riletto."""
    src = tmp_path / "geo.jpg"
    out = tmp_path / "stripped.jpg"
    _make_gps_jpeg(src)
    assert _gps_ifd(src), "precondizione: la sorgente deve contenere il GPS"

    conv = resolve("jpg", "jpg")
    result = conv.convert(
        ConversionJob(src, "jpg", "jpg", out, options={"quality": 95},
                      strip_metadata=True),
        lambda e: None,
    )
    assert result.ok and out.exists()
    # Verifica reale: rileggi l'output e accerta che il GPS sia sparito.
    assert _gps_ifd(out) == {}


def test_preserve_metadata_keeps_gps_exif(tmp_path):
    """Strip OFF (default): il tag GPS noto deve SOPRAVVIVERE alla conversione.

    Senza questo controparte, uno strip 'vacuo' (output sempre senza EXIF)
    passerebbe il test precedente senza fare nulla.
    """
    src = tmp_path / "geo.jpg"
    out = tmp_path / "kept.jpg"
    _make_gps_jpeg(src)

    conv = resolve("jpg", "jpg")
    result = conv.convert(
        ConversionJob(src, "jpg", "jpg", out, options={"quality": 95},
                      strip_metadata=False),
        lambda e: None,
    )
    assert result.ok and out.exists()
    gps = _gps_ifd(out)
    assert gps.get(1) == "N" and gps.get(2) == (51.0, 30.0, 0.0)
