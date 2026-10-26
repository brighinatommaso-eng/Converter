"""Test end-to-end del converter video (richiede ffmpeg).

Usa una sorgente sintetica piccola (testsrc 64x64) per restare veloce.
"""

import json
import subprocess

import pytest

import converter.converters  # noqa: F401
from converter.core.deps import find_ffmpeg, find_ffprobe
from converter.core.job import ConversionJob
from converter.core.registry import resolve

pytestmark = pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg not installed")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _make_video(path, duration=1):
    subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"testsrc=duration={duration}:size=64x64:rate=10", "-pix_fmt", "yuv420p", str(path)],
        check=True, creationflags=_NO_WINDOW,
    )


def _duration(path):
    out = subprocess.run(
        [find_ffprobe(), "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


@pytest.mark.skipif(find_ffprobe() is None, reason="ffprobe not installed")
def test_mp4_to_mkv(tmp_path):
    src = tmp_path / "clip.mp4"
    out = tmp_path / "clip.mkv"
    _make_video(src, duration=1)
    conv = resolve("mp4", "mkv")
    result = conv.convert(
        ConversionJob(src, "mp4", "mkv", out,
                      options={"codec": "h264", "crf": 23, "resolution": "original", "fps": "original"}),
        lambda e: None,
    )
    assert result.ok and out.exists()
    assert out.stat().st_size > 0
    assert result.verdict.is_lossy
    assert abs(_duration(out) - 1.0) < 0.3


def test_mp4_to_gif(tmp_path):
    src = tmp_path / "clip.mp4"
    out = tmp_path / "clip.gif"
    _make_video(src, duration=1)
    conv = resolve("mp4", "gif")
    result = conv.convert(
        ConversionJob(src, "mp4", "gif", out, options={"fps": "10", "width": "320"}),
        lambda e: None,
    )
    assert result.ok and out.exists()
    assert out.stat().st_size > 0
    assert result.verdict.is_lossy


# --- Review 2026-10-26: tracce, sottotitoli, larghezza custom, codec ------------

def _streams(path):
    out = subprocess.run(
        [find_ffprobe(), "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    return json.loads(out.stdout)["streams"]


def _rich_mkv(tmp_path):
    """mkv con 2 tracce audio e un sottotitolo SRT."""
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    src = tmp_path / "rich.mkv"
    subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=d=1:s=160x120:r=10", "-f", "lavfi", "-i", "sine=d=1",
         "-f", "lavfi", "-i", "sine=f=880:d=1", "-i", str(srt),
         "-map", "0", "-map", "1", "-map", "2", "-map", "3",
         "-c:v", "libx264", "-c:a", "aac", "-c:s", "srt", str(src)],
        check=True, creationflags=_NO_WINDOW,
    )
    return src


def _convert(src, dst, out, **opts):
    from converter.core.pipeline import run_job
    return run_job(ConversionJob(src, src.suffix[1:], dst, out, options=opts))


def test_all_audio_tracks_and_text_subtitles_are_kept(tmp_path):
    src = _rich_mkv(tmp_path)
    for dst, sub in (("mp4", "mov_text"), ("mkv", "subrip"), ("webm", "webvtt")):
        out = tmp_path / f"out.{dst}"
        result = _convert(src, dst, out)
        assert result.ok, result.error
        streams = _streams(out)
        assert sum(1 for s in streams if s["codec_type"] == "audio") == 2, dst
        assert [s["codec_name"] for s in streams if s["codec_type"] == "subtitle"] == [sub], dst


def test_subtitles_a_container_cannot_hold_are_declared(tmp_path):
    result = _convert(_rich_mkv(tmp_path), "avi", tmp_path / "out.avi")
    assert result.ok, result.error
    assert "subtitle" in result.verdict.reason


def test_custom_width_alone_scales_keeping_the_aspect_ratio(tmp_path):
    src = _rich_mkv(tmp_path)
    out = tmp_path / "small.mp4"
    assert _convert(src, "mp4", out, scale_w="80").ok
    video = next(s for s in _streams(out) if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (80, 60)


def test_hevc_in_mp4_is_tagged_hvc1_for_apple_players(tmp_path):
    out = tmp_path / "hevc.mp4"
    assert _convert(_rich_mkv(tmp_path), "mp4", out, codec="h265").ok
    video = next(s for s in _streams(out) if s["codec_type"] == "video")
    assert video["codec_tag_string"] == "hvc1"


def test_webm_without_options_uses_a_webm_codec(tmp_path):
    out = tmp_path / "plain.webm"
    result = _convert(_rich_mkv(tmp_path), "webm", out)
    assert result.ok, result.error
    assert next(s for s in _streams(out) if s["codec_type"] == "video")["codec_name"] == "vp9"
