"""Test end-to-end del converter audio (richiede ffmpeg).

Genera un breve campione con il generatore lavfi di ffmpeg, converte e verifica
che l'output esista, non sia vuoto e abbia la durata attesa.
"""

import json
import subprocess

import pytest

import converter.converters  # noqa: F401
from converter.core.deps import find_ffmpeg, find_ffprobe
from converter.core.job import ConversionJob
from converter.core.registry import resolve

# Senza ffmpeg questi test non hanno senso: si saltano (degrado controllato).
pytestmark = pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg not installed")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _make_wav(path, duration=1):
    subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={duration}", str(path)],
        check=True, creationflags=_NO_WINDOW,
    )


def _make_tagged_mp3(path, duration=1):
    """MP3 con tag ID3 noti (title/artist) da verificare dopo lo strip."""
    subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=440:duration={duration}",
         "-metadata", "title=SECRET-TITLE", "-metadata", "artist=SECRET-ARTIST",
         str(path)],
        check=True, creationflags=_NO_WINDOW,
    )


def _format_tags(path):
    out = subprocess.run(
        [find_ffprobe(), "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    return json.loads(out.stdout)["format"].get("tags", {})


def _duration(path):
    out = subprocess.run(
        [find_ffprobe(), "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


def test_wav_to_flac_lossless(tmp_path):
    src = tmp_path / "tone.wav"
    out = tmp_path / "tone.flac"
    _make_wav(src, duration=1)
    result = resolve("wav", "flac").convert(ConversionJob(src, "wav", "flac", out), lambda e: None)
    assert result.ok and out.exists()
    assert out.stat().st_size > 0
    assert result.verdict.is_lossless
    assert abs(_duration(out) - 1.0) < 0.2


@pytest.mark.skipif(find_ffprobe() is None, reason="ffprobe not installed")
def test_wav_to_mp3_lossy(tmp_path):
    src = tmp_path / "tone.wav"
    out = tmp_path / "tone.mp3"
    _make_wav(src, duration=1)
    conv = resolve("wav", "mp3")
    job = ConversionJob(src, "wav", "mp3", out, options={"bitrate": "192k"})
    result = conv.convert(job, lambda e: None)
    assert result.ok and out.exists()
    assert out.stat().st_size > 0
    assert result.verdict.is_lossy
    assert abs(_duration(out) - 1.0) < 0.3


@pytest.mark.skipif(find_ffprobe() is None, reason="ffprobe not installed")
def test_strip_metadata_removes_id3_tags(tmp_path):
    """Strip ON: i tag ID3 noti devono essere ASSENTI nell'output riletto.

    Nota: il muxer di ffmpeg ristampa sempre un tag 'encoder' (Lavf...); perciò
    si verifica l'assenza dei tag UTENTE (title/artist), non che i tag siano vuoti.
    """
    src = tmp_path / "tagged.mp3"
    out = tmp_path / "stripped.mp3"
    _make_tagged_mp3(src, duration=1)
    assert _format_tags(src).get("title") == "SECRET-TITLE"  # precondizione

    conv = resolve("mp3", "mp3")
    job = ConversionJob(src, "mp3", "mp3", out, options={"bitrate": "192k"},
                        strip_metadata=True)
    result = conv.convert(job, lambda e: None)
    assert result.ok and out.exists()

    tags = _format_tags(out)
    assert "title" not in tags
    assert "artist" not in tags


@pytest.mark.skipif(find_ffprobe() is None, reason="ffprobe not installed")
def test_preserve_metadata_keeps_id3_tags(tmp_path):
    """Strip OFF (default): i tag ID3 noti devono SOPRAVVIVERE (controparte)."""
    src = tmp_path / "tagged.mp3"
    out = tmp_path / "kept.mp3"
    _make_tagged_mp3(src, duration=1)

    conv = resolve("mp3", "mp3")
    job = ConversionJob(src, "mp3", "mp3", out, options={"bitrate": "192k"},
                        strip_metadata=False)
    result = conv.convert(job, lambda e: None)
    assert result.ok and out.exists()

    tags = _format_tags(out)
    assert tags.get("title") == "SECRET-TITLE"
    assert tags.get("artist") == "SECRET-ARTIST"
