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
