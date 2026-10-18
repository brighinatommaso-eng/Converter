"""Test della matrice dei verdetti di qualità (lossless/lossy/unsupported)."""

import converter.converters  # noqa: F401
from converter.converters.audio import AudioConverter
from converter.converters.data import DataConverter
from converter.converters.image import ImageConverter


def test_image_verdicts():
    img = ImageConverter()
    assert img.verdict("png", "bmp").is_lossless
    assert img.verdict("png", "tiff").is_lossless
    assert img.verdict("png", "jpg").is_lossy
    assert img.verdict("png", "gif").is_lossy


def test_audio_verdicts():
    audio = AudioConverter()
    assert audio.verdict("wav", "flac").is_lossless
    assert audio.verdict("flac", "wav").is_lossless
    assert audio.verdict("wav", "mp3").is_lossy


def test_data_verdicts():
    data = DataConverter()
    assert data.verdict("csv", "tsv").is_lossless
    assert data.verdict("json", "yaml").is_lossless
    assert data.verdict("csv", "json").is_lossless
    assert data.verdict("json", "csv").is_lossy   # appiattimento di strutture annidate
    assert data.verdict("xlsx", "csv").is_lossy   # solo valori, niente formule/formattazione


def test_unsupported_pair_has_reason():
    img = ImageConverter()
    verdict = img.verdict("png", "mp3")
    assert verdict.level == "unsupported"
