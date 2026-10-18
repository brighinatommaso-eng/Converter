"""Test della validazione degli input avanzati (Tier D).

Il rischio non è la conversione (ffmpeg la fa): è che un input cattivo produca
un errore criptico di ffmpeg invece di un messaggio chiaro. Questi test
verificano i validatori PURI (senza ffmpeg) e il gate in run_job (un input
invalido non raggiunge mai ffmpeg).
"""

import subprocess

import pytest

import converter.converters  # noqa: F401
from converter.converters._ffmpeg import parse_time
from converter.converters.audio import validate_audio_options
from converter.converters.video import validate_video_options
from converter.core.deps import find_ffmpeg, find_ffprobe
from converter.core.job import ConversionJob
from converter.core.pipeline import run_job

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --- parse_time ---------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("12", 12.0), ("12.5", 12.5), ("0:30", 30.0), ("1:02", 62.0),
    ("01:02:03", 3723.0), ("00:00:05.5", 5.5),
])
def test_parse_time_valid(text, expected):
    assert parse_time(text) == expected


@pytest.mark.parametrize("text", ["abc", "1:2:3:4", "", "  ", "x:y"])
def test_parse_time_invalid(text):
    assert parse_time(text) is None


# --- Video: trim -------------------------------------------------------------

def test_video_no_options_is_valid():
    assert validate_video_options({}, duration=10.0) is None


def test_trim_start_unparseable():
    msg = validate_video_options({"trim_start": "9:99:99:99"}, duration=10.0)
    assert msg and "valid time" in msg


def test_trim_start_negative():
    msg = validate_video_options({"trim_start": "-3"}, duration=10.0)
    assert msg and "0 or greater" in msg


def test_trim_start_past_duration():
    msg = validate_video_options({"trim_start": "20"}, duration=10.0)
    assert msg and "past the video length" in msg


def test_trim_end_exceeds_duration():
    msg = validate_video_options({"trim_end": "20"}, duration=10.0)
    assert msg and "exceeds video length" in msg


def test_trim_end_not_after_start():
    msg = validate_video_options({"trim_start": "5", "trim_end": "5"}, duration=10.0)
    assert msg and "after start" in msg


def test_trim_valid_within_duration():
    assert validate_video_options({"trim_start": "1", "trim_end": "8"}, duration=10.0) is None


def test_trim_degrades_without_duration():
    # Durata ignota: niente controllo sul limite superiore, ma start<end resta.
    assert validate_video_options({"trim_start": "1", "trim_end": "999"}, duration=None) is None
    bad = validate_video_options({"trim_end": "1", "trim_start": "5"}, duration=None)
    assert bad and "after start" in bad


# --- Video: scale (reject odd always) ----------------------------------------

def test_scale_odd_width_rejected():
    msg = validate_video_options({"scale_w": "481", "scale_h": "270"}, duration=None)
    assert msg and "even" in msg


def test_scale_odd_height_rejected():
    msg = validate_video_options({"scale_w": "480", "scale_h": "271"}, duration=None)
    assert msg and "even" in msg


def test_scale_non_integer_rejected():
    msg = validate_video_options({"scale_w": "480.5", "scale_h": "270"}, duration=None)
    assert msg and "whole number" in msg


def test_scale_non_positive_rejected():
    msg = validate_video_options({"scale_w": "0", "scale_h": "270"}, duration=None)
    assert msg and "positive" in msg


def test_scale_even_dimensions_valid():
    assert validate_video_options({"scale_w": "480", "scale_h": "270"}, duration=None) is None


# --- Video: fps --------------------------------------------------------------

@pytest.mark.parametrize("fps", ["0", "-5"])
def test_fps_non_positive_rejected(fps):
    msg = validate_video_options({"fps": fps}, duration=None)
    assert msg and "positive" in msg


def test_fps_absurd_rejected():
    msg = validate_video_options({"fps": "9000"}, duration=None)
    assert msg and "unrealistic" in msg


def test_fps_non_number_rejected():
    msg = validate_video_options({"fps": "fast"}, duration=None)
    assert msg and "isn't a number" in msg


def test_fps_valid():
    assert validate_video_options({"fps": "30"}, duration=None) is None


# --- Audio: codec-aware sample rate & channels -------------------------------

def test_opus_rejects_44100():
    msg = validate_audio_options({"sample_rate": "44100"}, "opus")
    assert msg and "support" in msg


def test_opus_accepts_48000():
    assert validate_audio_options({"sample_rate": "48000"}, "opus") is None


def test_mp3_accepts_44100():
    assert validate_audio_options({"sample_rate": "44100"}, "mp3") is None


def test_lossless_accepts_any_sample_rate():
    # WAV/FLAC: nessun vincolo di sample rate.
    assert validate_audio_options({"sample_rate": "12345"}, "wav") is None


def test_sample_rate_non_number_rejected():
    msg = validate_audio_options({"sample_rate": "fast"}, "mp3")
    assert msg and "isn't a number" in msg


def test_channels_out_of_range_rejected():
    msg = validate_audio_options({"channels": "6"}, "mp3")
    assert msg and "mono or stereo" in msg


def test_channels_valid():
    assert validate_audio_options({"channels": "2"}, "mp3") is None


# --- Gate: un input invalido non raggiunge ffmpeg ----------------------------

@pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg not installed")
def test_run_job_gate_blocks_invalid_before_ffmpeg(tmp_path):
    """run_job deve restituire un messaggio CHIARO, non eseguire, non creare
    output, quando l'input è invalido."""
    src = tmp_path / "in.mp3"
    src.write_bytes(b"not really audio")  # non serve: il gate scatta prima
    out = tmp_path / "out.opus"
    job = ConversionJob(src, "mp3", "opus", out, options={"sample_rate": "44100"})
    result = run_job(job)
    assert not result.ok
    assert "support" in result.error.lower()
    assert not out.exists()  # ffmpeg mai eseguito


# --- Real video trim end-to-end ----------------------------------------------

@pytest.mark.skipif(find_ffmpeg() is None or find_ffprobe() is None,
                    reason="ffmpeg/ffprobe not installed")
def test_video_trim_real_roundtrip(tmp_path):
    src = tmp_path / "clip.mp4"
    subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=duration=3:size=160x120:rate=15", str(src)],
        check=True, creationflags=_NO_WINDOW,
    )

    # Trim oltre la durata reale -> messaggio chiaro, niente dump ffmpeg.
    bad = run_job(ConversionJob(src, "mp4", "mp4", tmp_path / "bad.mp4",
                                options={"trim_end": "10"}))
    assert not bad.ok and "exceeds video length" in bad.error

    # Trim valido -> output reale più corto.
    out = tmp_path / "trim.mp4"
    good = run_job(ConversionJob(src, "mp4", "mp4", out,
                                 options={"trim_start": "0", "trim_end": "1"}))
    assert good.ok and out.exists()
    probe = subprocess.run(
        [find_ffprobe(), "-v", "quiet", "-print_format", "json", "-show_format", str(out)],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    import json
    dur = float(json.loads(probe.stdout)["format"]["duration"])
    assert dur < 1.6  # ~1s, ben sotto i 3s originali
