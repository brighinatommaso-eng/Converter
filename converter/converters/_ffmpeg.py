"""Utility condivise per i converter basati su ffmpeg (audio e video).

Gestisce: localizzazione del binario, lettura della durata via ffprobe (per
stima dimensioni e progresso), ed esecuzione con parsing del progresso. Su
Windows nasconde la finestra di console del sottoprocesso.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

from ..core.deps import clean_subprocess_env, find_ffmpeg, find_ffprobe
from ..core.job import CancelToken, ProgressCallback, ProgressEvent

# Flag per non far comparire una finestra console quando si lancia ffmpeg su Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ffprobe è veloce: se resta appeso (file di rete, device) non deve gelare la coda.
_PROBE_TIMEOUT = 15.0
# Secondi di SILENZIO totale di ffmpeg (nessuna riga di progress) oltre i quali lo
# consideriamo bloccato e lo uccidiamo: con `-progress pipe:1` ffmpeg emette righe
# di continuo durante l'encode, quindi un silenzio così lungo = processo impiccato.
_STALL_TIMEOUT = 120.0


def parse_time(text: str) -> float | None:
    """Converte un timecode in secondi. Accetta secondi (`12`, `12.5`) o forme
    con i due punti (`MM:SS`, `HH:MM:SS`, con decimali). Ritorna None se non
    interpretabile (così il validatore può dare un messaggio chiaro)."""
    text = text.strip()
    if not text:
        return None
    try:
        if ":" in text:
            parts = text.split(":")
            if len(parts) > 3:
                return None
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60 + float(part)
            return seconds
        return float(text)
    except ValueError:
        return None


def probe_duration(path: Path) -> float | None:
    """Durata del media in secondi, o None se non determinabile."""
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", str(path)],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
            env=clean_subprocess_env(), timeout=_PROBE_TIMEOUT,
        )
        data = json.loads(out.stdout or "{}")
        return float(data.get("format", {}).get("duration", 0)) or None
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        # subprocess.TimeoutExpired è una SubprocessError: un ffprobe appeso viene
        # killato da subprocess.run e qui si degrada a None (durata non nota).
        return None


def probe_audio(path: Path) -> dict | None:
    """Formato del primo stream audio: {'codec', 'sample_fmt', 'bits'} o None.

    Serve al verdetto onesto dei target lossless: un WAV a 24 bit o in virgola
    mobile non va scritto a 16 bit sotto un badge LOSSLESS. `bits` è la
    profondità reale (bits_per_raw_sample, poi bits_per_sample), 0 se ignota.
    """
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams",
             "-select_streams", "a:0", str(path)],
            capture_output=True, text=True, creationflags=_NO_WINDOW,
            env=clean_subprocess_env(), timeout=_PROBE_TIMEOUT,
        )
        streams = json.loads(out.stdout or "{}").get("streams") or []
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        return None
    if not streams:
        return None
    stream = streams[0]
    bits = 0
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        try:
            bits = int(stream.get(key) or 0)
        except (TypeError, ValueError):
            bits = 0
        if bits:
            break
    return {"codec": str(stream.get("codec_name") or ""),
            "sample_fmt": str(stream.get("sample_fmt") or ""), "bits": bits}


def run_ffmpeg(
    args: list[str],
    output_path: Path,
    duration: float | None,
    progress: ProgressCallback,
    cancel: CancelToken,
) -> tuple[int, str]:
    """Esegue `ffmpeg <args>` con parsing del progresso da `-progress pipe:1`.

    Ritorna (returncode, ultime_righe_stderr). Il chiamante costruisce `args`
    SENZA il binario e senza -progress: li aggiungiamo qui.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return 1, "FFmpeg not found."

    cmd = [ffmpeg, "-hide_banner", "-y", *args, "-progress", "pipe:1", "-nostats"]
    # stderr va su un file temporaneo, NON su una pipe: se mettessimo stderr in
    # PIPE e leggessimo solo dopo aver drenato stdout, un stderr voluminoso
    # riempirebbe il buffer della pipe (~64KB), ffmpeg si bloccherebbe in
    # scrittura e non chiuderebbe stdout -> deadlock. Col file il problema sparisce.
    errfile = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    stalled = threading.Event()
    stop_watchdog = threading.Event()
    last_active = [time.monotonic()]
    proc: subprocess.Popen | None = None

    def _watchdog() -> None:
        # Uccide ffmpeg se: (a) è stato richiesto il cancel ma la lettura di stdout è
        # bloccata (nessuna riga in arrivo) — così il cancel funziona anche senza
        # progress; (b) resta muto oltre _STALL_TIMEOUT (impiccato). Uccidere chiude
        # stdout, il ciclo di lettura riceve EOF e run_ffmpeg può concludere.
        while not stop_watchdog.wait(1.0):
            if proc is None:
                continue
            if cancel.cancelled:
                proc.kill()
                return
            if time.monotonic() - last_active[0] > _STALL_TIMEOUT:
                stalled.set()
                proc.kill()
                return

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=errfile, text=True,
            bufsize=1, creationflags=_NO_WINDOW, env=clean_subprocess_env(),
        )
        watchdog = threading.Thread(target=_watchdog, daemon=True)
        watchdog.start()

        assert proc.stdout is not None
        for line in proc.stdout:
            last_active[0] = time.monotonic()
            if cancel.cancelled:
                proc.kill()
                proc.wait()
                output_path.unlink(missing_ok=True)  # via il file parziale
                return 130, "Cancelled."
            line = line.strip()
            # ffmpeg riporta il tempo elaborato come out_time_us / out_time_ms.
            if line.startswith("out_time_us=") and duration:
                try:
                    secs = int(line.split("=", 1)[1]) / 1_000_000
                    progress(ProgressEvent(min(secs / duration, 0.99), "Converting…"))
                except ValueError:
                    pass
            elif line.startswith("progress=") and line.endswith("end"):
                progress(ProgressEvent(0.99, "Finalising…"))

        proc.wait()
        errfile.seek(0)
        stderr_tail: deque[str] = deque(errfile.read().splitlines(), maxlen=20)
        rc = proc.returncode
        if stalled.is_set():
            output_path.unlink(missing_ok=True)
            return rc or 1, f"FFmpeg stalled (no progress for {int(_STALL_TIMEOUT)}s) and was stopped."
        if cancel.cancelled:
            output_path.unlink(missing_ok=True)
            return 130, "Cancelled."
        if rc != 0:
            output_path.unlink(missing_ok=True)  # non lasciare output rotto accanto all'errore
        return rc, "\n".join(t.strip() for t in stderr_tail if t.strip())
    finally:
        stop_watchdog.set()
        errfile.close()
