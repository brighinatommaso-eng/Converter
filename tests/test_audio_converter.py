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


# --- Profondità in bit: niente troncamenti sotto LOSSLESS (review 2026-10-26) ---

def _make_pcm(path, codec):
    subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=0.3", "-c:a", codec, str(path)],
        check=True, creationflags=_NO_WINDOW,
    )


def _stream(path):
    out = subprocess.run(
        [find_ffprobe(), "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    return json.loads(out.stdout)["streams"][0]


def _convert(src, s, d, out, **opts):
    from converter.core.pipeline import run_job
    return run_job(ConversionJob(src, s, d, out, options=opts))


def test_24bit_wav_stays_24bit(tmp_path):
    src = tmp_path / "s24.wav"
    _make_pcm(src, "pcm_s24le")
    for dst in ("wav", "flac"):
        out = tmp_path / f"o.{dst}"
        result = _convert(src, "wav", dst, out)
        assert result.ok and result.verdict.is_lossless, result.error
        assert str(_stream(out).get("bits_per_raw_sample")) == "24"


def test_float_wav_to_wav_stays_float_and_to_flac_is_lossy(tmp_path):
    src = tmp_path / "f32.wav"
    _make_pcm(src, "pcm_f32le")
    wav = tmp_path / "o.wav"
    assert _convert(src, "wav", "wav", wav).verdict.is_lossless
    assert _stream(wav)["codec_name"] == "pcm_f32le"
    flac = _convert(src, "wav", "flac", tmp_path / "o.flac")
    assert flac.ok and flac.verdict.is_lossy and "integer" in flac.verdict.reason


def test_explicit_bit_depth_reduction_is_declared(tmp_path):
    src = tmp_path / "s24.wav"
    _make_pcm(src, "pcm_s24le")
    result = _convert(src, "wav", "wav", tmp_path / "o.wav", bit_depth="16")
    assert result.ok and result.verdict.is_lossy and "16-bit" in result.verdict.reason
    # 16-bit verso 24-bit non riduce nulla: il verdetto a runtime torna lossless.
    src16 = tmp_path / "s16.wav"
    _make_pcm(src16, "pcm_s16le")
    assert _convert(src16, "wav", "flac", tmp_path / "o.flac", bit_depth="24").verdict.is_lossless


def test_signal_altering_options_are_never_lossless():
    conv = resolve("wav", "flac")
    assert conv.verdict("wav", "flac", {"channels": "1"}).is_lossy
    assert conv.verdict("wav", "wav", {"sample_rate": "44100"}).is_lossy
    assert conv.verdict("wav", "wav", {"normalize": True}).is_lossy
    assert conv.verdict("wav", "flac", {}).is_lossless


def test_cover_art_survives_and_normalize_keeps_the_sample_rate(tmp_path):
    from PIL import Image

    cover = tmp_path / "cover.png"
    Image.new("RGB", (16, 16), "red").save(cover)
    src = tmp_path / "song.mp3"
    subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "sine=d=0.5", "-i", str(cover), "-map", "0:a", "-map", "1:v", "-c:v", "copy",
         "-disposition:v", "attached_pic", str(src)],
        check=True, creationflags=_NO_WINDOW,
    )
    for dst in ("mp3", "flac", "m4a"):
        out = tmp_path / f"o.{dst}"
        assert _convert(src, "mp3", dst, out).ok
        assert any(s["codec_type"] == "video" and s["disposition"]["attached_pic"]
                   for s in _all_streams(out)), dst
    stripped = tmp_path / "s.flac"
    from converter.core.pipeline import run_job
    assert run_job(ConversionJob(src, "mp3", "flac", stripped, strip_metadata=True)).ok
    assert not any(s["codec_type"] == "video" for s in _all_streams(stripped))
    normalized = tmp_path / "n.wav"
    assert _convert(src, "mp3", "wav", normalized, normalize=True).ok
    assert _stream(normalized)["sample_rate"] == "44100"   # loudnorm uscirebbe a 192000


def _all_streams(path):
    out = subprocess.run(
        [find_ffprobe(), "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    return json.loads(out.stdout)["streams"]
