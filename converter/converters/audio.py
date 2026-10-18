"""Converter audio basato su ffmpeg.

Verdetto: destinazioni con codec lossless (WAV PCM, FLAC) -> LOSSLESS; gli altri
codec (MP3/AAC/OGG/M4A/Opus) -> LOSSY, con bitrate massimo sensato di default.
"""

from __future__ import annotations

from ..core.base import Converter
from ..core.deps import ffmpeg_status
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.options import BoolOption, ChoiceOption, Option
from ..core.quality import QualityVerdict, lossless, lossy, unsupported
from ..core.registry import register
from ._ffmpeg import probe_duration, run_ffmpeg

_FORMATS = frozenset({"mp3", "wav", "flac", "aac", "ogg", "m4a", "opus"})
_LOSSLESS = {"wav", "flac"}

# Sample rate (Hz) supportati dal codec di destinazione. None = qualunque (PCM/FLAC).
# Serve a rifiutare valori che ffmpeg non accetterebbe (es. 44100 su Opus) con un
# messaggio chiaro, invece di un errore criptico del codec.
_CODEC_SAMPLE_RATES: dict[str, set[int] | None] = {
    "mp3": {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000},
    "aac": {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000},
    "m4a": {8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000},
    "ogg": {8000, 11025, 16000, 22050, 32000, 44100, 48000},
    "opus": {8000, 12000, 16000, 24000, 48000},
    "wav": None,
    "flac": None,
}
# Sottoinsieme "comune" mostrato nei dropdown (intersecato col codec).
_COMMON_RATES = (16000, 22050, 24000, 44100, 48000)


def _field(opts: dict, key: str) -> str:
    value = opts.get(key)
    return str(value).strip() if value is not None else ""


def _fmt_khz(hz: int) -> str:
    khz = hz / 1000
    return str(int(khz)) if khz == int(khz) else f"{khz:g}"


def validate_audio_options(opts: dict, dst: str) -> str | None:
    """Validatore PURO delle opzioni audio: sample rate e canali devono essere
    supportati dal codec di destinazione. Ritorna un messaggio chiaro o None."""
    sr = _field(opts, "sample_rate")
    if sr and sr != "original":
        try:
            value = int(sr)
        except ValueError:
            return f"Sample rate '{sr}' isn't a number."
        allowed = _CODEC_SAMPLE_RATES.get(dst)
        if allowed is not None and value not in allowed:
            nice = ", ".join(_fmt_khz(r) for r in sorted(allowed))
            return f"{dst.upper()} doesn't support a {value} Hz sample rate (allowed: {nice} kHz)."

    ch = _field(opts, "channels")
    if ch and ch != "original":
        try:
            count = int(ch)
        except ValueError:
            return f"Channels '{ch}' isn't a number."
        if count < 1 or count > 2:
            return f"{dst.upper()} supports mono or stereo only (1–2 channels), got {count}."

    return None

# Codec ffmpeg per ciascuna destinazione.
_CODEC = {
    "mp3": ["-c:a", "libmp3lame"],
    "aac": ["-c:a", "aac"],
    "m4a": ["-c:a", "aac"],
    "ogg": ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
}


@register
class AudioConverter(Converter):
    category = "audio"
    name = "Audio (FFmpeg)"
    input_formats = _FORMATS
    output_formats = _FORMATS

    @classmethod
    def available(cls):
        return ffmpeg_status()

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src not in _FORMATS or dst not in _FORMATS:
            return unsupported()
        if dst in _LOSSLESS:
            return lossless(f"{dst.upper()} uses lossless PCM/FLAC encoding (the convert step adds no loss).")
        return lossy(f"{dst.upper()} is a lossy codec; audio is re-encoded with loss.")

    def options_schema(self, src: str, dst: str) -> list[Option]:
        opts: list[Option] = []
        if dst not in _LOSSLESS:
            default = "128k" if dst == "opus" else "192k"
            opts.append(ChoiceOption("bitrate", "Bitrate", default,
                                     choices=[(b, b.replace("k", " kbps"))
                                              for b in ("96k", "128k", "192k", "256k", "320k")]))
        # Sample rate vincolato a ciò che il codec di destinazione supporta:
        # così la GUI non offre nemmeno valori invalidi (es. 44.1 kHz su Opus).
        allowed = _CODEC_SAMPLE_RATES.get(dst)
        rates = _COMMON_RATES if allowed is None else tuple(r for r in _COMMON_RATES if r in allowed)
        opts.append(ChoiceOption("sample_rate", "Sample rate", "original",
                                 choices=[("original", "Keep original")]
                                 + [(str(r), f"{_fmt_khz(r)} kHz") for r in rates]))
        opts.append(ChoiceOption("channels", "Channels", "original",
                                 choices=[("original", "Keep original"), ("1", "Mono"), ("2", "Stereo")]))
        opts.append(BoolOption("normalize", "Normalize loudness", False,
                               help="Off by default. On = EBU R128 loudness normalisation (re-encodes audio)."))
        return opts

    # --- Validazione ---

    def validate(self, job: ConversionJob) -> str | None:
        return validate_audio_options(job.options, job.dst_format)

    def validate_quick(self, src: str, dst: str, options: dict) -> str | None:
        return validate_audio_options(options, dst)

    def estimate_size(self, job: ConversionJob) -> int | None:
        if job.dst_format in _LOSSLESS:
            return None  # FLAC/WAV dipendono dal contenuto: niente stima affidabile
        duration = probe_duration(job.src_path)
        if not duration:
            return None
        bitrate = job.options.get("bitrate", "192k")
        try:
            kbps = int(str(bitrate).rstrip("k"))
        except ValueError:
            return None
        return int(kbps * 1000 / 8 * duration)  # byte ≈ (bit/s) / 8 * secondi

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        duration = probe_duration(job.src_path)
        args = ["-i", str(job.src_path)]

        opts = job.options
        if opts.get("sample_rate", "original") != "original":
            args += ["-ar", str(opts["sample_rate"])]
        if opts.get("channels", "original") != "original":
            args += ["-ac", str(opts["channels"])]

        # Normalizzazione loudness (EBU R128): opzionale, OFF di default.
        if opts.get("normalize"):
            args += ["-af", "loudnorm"]

        args += _CODEC[job.dst_format]
        if job.dst_format not in _LOSSLESS:
            args += ["-b:a", str(opts.get("bitrate", "192k"))]

        # Metadati (ID3/tag): -map_metadata -1 li rimuove se richiesto lo strip.
        if job.strip_metadata:
            args += ["-map_metadata", "-1"]
        if job.dst_format != "wav":
            args += ["-vn"]  # niente eventuali cover/stream video

        args += [str(job.output_path)]

        progress(ProgressEvent(0.05, "Converting audio…"))
        code, err = run_ffmpeg(args, job.output_path, duration, progress, job.cancel)
        if code != 0:
            return ConversionResult(ok=False, error=err or f"FFmpeg exited with code {code}.")

        size = job.output_path.stat().st_size
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=self.verdict(job.src_format, job.dst_format))
