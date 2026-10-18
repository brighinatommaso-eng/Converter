"""Regressione: run_ffmpeg non deve mai andare in deadlock per via dello stderr.

Bug originale: stderr finiva su una PIPE letta solo DOPO aver drenato stdout. Con
uno stderr voluminoso (> ~64KB, il buffer della pipe) ffmpeg si bloccava in
scrittura, non chiudeva stdout e run_ffmpeg restava appeso per sempre — gelando
la conversione di file audio/video grandi. La fix manda stderr su un file
temporaneo. Questo test forza uno stderr enorme (`-v trace`) e pretende che la
chiamata termini entro un timeout, dimostrando l'assenza di deadlock.
"""

import subprocess
import tempfile
import threading
import time

import pytest

from converter.converters._ffmpeg import run_ffmpeg
from converter.core.deps import find_ffmpeg
from converter.core.job import CancelToken

pytestmark = pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg not installed")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PIPE_BUF = 64 * 1024  # soglia oltre cui una pipe stderr si bloccherebbe


def _make_clip(path):
    subprocess.run(
        [find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=duration=4:size=320x240:rate=25", str(path)],
        check=True, creationflags=_NO_WINDOW,
    )


def test_run_ffmpeg_large_stderr_does_not_deadlock(tmp_path):
    src = tmp_path / "clip.mp4"
    out = tmp_path / "out.mp4"
    _make_clip(src)

    # `-v trace` come PRIMO argomento: stderr enorme. args senza binario/-progress
    # (li aggiunge run_ffmpeg). -preset ultrafast per non allungare il test.
    args = ["-v", "trace", "-i", str(src), "-c:v", "libx264", "-crf", "30",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)]

    # Precondizione: il carico genera DAVVERO > 64KB di stderr, altrimenti il
    # test non eserciterebbe la condizione di deadlock che deve sorvegliare.
    errfile = tempfile.TemporaryFile()
    subprocess.run([find_ffmpeg(), "-hide_banner", *args],
                   stdout=subprocess.DEVNULL, stderr=errfile, creationflags=_NO_WINDOW)
    assert errfile.tell() > _PIPE_BUF, "il carico di prova deve superare il buffer della pipe"
    errfile.close()

    # Esecuzione sotto timeout: se run_ffmpeg fosse ancora vulnerabile, il thread
    # resterebbe vivo e il test fallirebbe (invece di appendere l'intera suite).
    result: dict = {}

    def worker():
        result["rc"], result["err"] = run_ffmpeg(
            args, out, 4.0, lambda e: None, CancelToken())

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout=90)

    assert not thread.is_alive(), "run_ffmpeg deadlocked on large stderr"
    assert result["rc"] == 0
    assert out.exists() and out.stat().st_size > 0


def test_run_ffmpeg_cancel_stops_and_removes_partial(tmp_path):
    """Rischi 10/11: cancel ferma ffmpeg (rc 130) e l'output parziale viene rimosso.

    Sorgente INFINITA (testsrc senza durata): ffmpeg non finisce da solo, quindi
    quando cancelliamo è certamente ancora in esecuzione."""
    out = tmp_path / "out.mkv"
    args = ["-f", "lavfi", "-i", "testsrc=size=640x480:rate=30",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)]
    token = CancelToken()
    result: dict = {}

    def worker():
        result["rc"], result["err"] = run_ffmpeg(args, out, None, lambda e: None, token)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    time.sleep(1.0)          # lascia partire davvero l'encode
    token.cancel()
    thread.join(timeout=30)

    assert not thread.is_alive(), "run_ffmpeg non ha rispettato il cancel"
    assert result["rc"] == 130
    assert not out.exists()  # output parziale ripulito
