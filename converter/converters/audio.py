"""Converter audio basato su ffmpeg.

Verdetto: destinazioni con codec lossless (WAV PCM, FLAC) -> LOSSLESS solo se la
profondità della sorgente si conserva (verificata con ffprobe) e nessuna opzione
altera il segnale; gli altri
codec (MP3/AAC/OGG/M4A/Opus) -> LOSSY, con bitrate massimo sensato di default.
"""

from __future__ import annotations

from ..core.base import Converter
from ..core.deps import ffmpeg_status
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.options import BoolOption, ChoiceOption, Option
from ..core.quality import QualityVerdict, lossless, lossy, unsupported
from ..core.registry import register
from ._ffmpeg import probe_audio, probe_duration, probe_streams, run_ffmpeg

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
    "wav": ["-c:a", "pcm_s16le"],  # solo fallback: il piano reale è in _lossless_plan
}

# Contenitori che reggono una copertina (ID3 APIC, FLAC PICTURE, MP4 covr).
_COVER_DST = ("mp3", "flac", "m4a")

# Codec sorgente già lossless: i loro campioni sono i dati originali.
_LOSSLESS_SRC_CODECS = ("pcm_", "flac", "alac", "wavpack", "tta", "ape")
_FLOAT_FMTS = {"flt", "fltp", "dbl", "dblp"}
_DEPTH_LABEL = {"16": "16-bit", "24": "24-bit", "32f": "32-bit float"}


def _source_depth(info: dict | None) -> tuple[bool, int]:
    """(virgola_mobile, bit) dei campioni sorgente.

    Un codec lossy (mp3/aac/vorbis/opus) viene decodificato in virgola mobile:
    quei float SONO il segnale da conservare, quindi contano come float a 32 bit."""
    if info is None:
        return True, 32
    fmt, bits = info["sample_fmt"], info["bits"]
    if not info["codec"].startswith(_LOSSLESS_SRC_CODECS) or fmt in _FLOAT_FMTS:
        return True, 64 if fmt.startswith("dbl") else 32
    if not bits:
        bits = {"u8": 8, "u8p": 8, "s16": 16, "s16p": 16}.get(fmt, 32)
    return False, bits


def _lossless_plan(dst: str, info: dict | None, depth: str) -> tuple[list[str], str | None]:
    """Argomenti codec per wav/flac + motivo di perdita (None se non si perde nulla).

    "source" = profondità della sorgente (mai una riduzione silenziosa); una
    profondità esplicita può ridurla e allora il verdetto lo dice."""
    is_float, bits = _source_depth(info)
    src_label = f"{bits}-bit float" if is_float else f"{bits}-bit"
    if dst == "wav":
        if depth == "source":
            if is_float:
                return ["-c:a", "pcm_f64le" if bits == 64 else "pcm_f32le"], None
            codec = ("pcm_u8" if bits <= 8 else "pcm_s16le" if bits <= 16
                     else "pcm_s24le" if bits <= 24 else "pcm_s32le")
            return ["-c:a", codec], None
        if depth == "32f":
            lost = bits == 64 or (not is_float and bits > 24)
            reason = (f"The {src_label} source is stored as 32-bit float, which keeps only "
                      f"24 bits of precision.") if lost else None
            return ["-c:a", "pcm_f32le"], reason
        target = int(depth)
        codec = "pcm_s16le" if target == 16 else "pcm_s24le"
        lost = is_float or bits > target
        reason = f"The {src_label} source is reduced to {target}-bit." if lost else None
        return ["-c:a", codec], reason
    # FLAC: solo campioni interi, fino a 24 bit di serie (32 bit sperimentale).
    if depth in ("16", "24"):
        target = int(depth)
        args = (["-c:a", "flac", "-sample_fmt", "s16"] if target == 16 else
                ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"])
        lost = is_float or bits > target
        reason = f"The {src_label} source is reduced to {target}-bit." if lost else None
        return args, reason
    if is_float:
        return (["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"],
                "FLAC stores integer samples: the floating-point audio is rounded to 24-bit.")
    if bits <= 16:
        return ["-c:a", "flac", "-sample_fmt", "s16"], None
    if bits <= 24:
        return ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"], None
    return ["-c:a", "flac", "-sample_fmt", "s32", "-strict", "experimental"], None


def _altering_options(opts: dict) -> list[str]:
    """Opzioni che cambiano il segnale: con queste nessun target è lossless."""
    changes = []
    if str(opts.get("sample_rate", "original")) != "original":
        changes.append("resampling")
    if str(opts.get("channels", "original")) != "original":
        changes.append("channel remix")
    if opts.get("normalize"):
        changes.append("loudness normalisation")
    return changes


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
            opts = options or {}
            changes = _altering_options(opts)
            if changes:
                return lossy(f"{dst.upper()} is lossless, but the selected options alter "
                             f"the audio: " + ", ".join(changes) + ".")
            depth = str(opts.get("bit_depth", "source"))
            if depth != "source":
                return lossy(f"A fixed {_DEPTH_LABEL.get(depth, depth)} depth can reduce the "
                             f"source's precision (checked when converting).")
            return lossless(f"{dst.upper()} keeps the source's sample format; the convert step "
                            f"adds no loss (checked when converting).")
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
        if dst in _LOSSLESS:
            depths = [("16", "16-bit"), ("24", "24-bit")]
            if dst == "wav":
                depths.append(("32f", "32-bit float"))
            opts.append(ChoiceOption("bit_depth", "Bit depth", "source",
                                     choices=[("source", "Match source")] + depths,
                                     help="Match source never reduces precision."))
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
        info = probe_audio(job.src_path)
        # Prima traccia audio esplicita: con la mappatura automatica una
        # copertina (stream video) poteva finire nel file o farlo fallire.
        args = ["-i", str(job.src_path), "-map", "0:a:0"]

        opts = job.options
        if opts.get("sample_rate", "original") != "original":
            args += ["-ar", str(opts["sample_rate"])]
        elif opts.get("normalize") and info and info.get("sample_rate"):
            # loudnorm lavora (ed esce) a 192 kHz: senza -ar un WAV/FLAC
            # normalizzato usciva a 192 kHz, 4 volte più grande e ricampionato.
            allowed = _CODEC_SAMPLE_RATES.get(job.dst_format)
            if allowed is None or info["sample_rate"] in allowed:
                args += ["-ar", str(info["sample_rate"])]
        if opts.get("channels", "original") != "original":
            args += ["-ac", str(opts["channels"])]

        # Normalizzazione loudness (EBU R128): opzionale, OFF di default.
        if opts.get("normalize"):
            args += ["-af", "loudnorm"]

        loss_reason = None
        if job.dst_format in _LOSSLESS:
            codec_args, loss_reason = _lossless_plan(
                job.dst_format, info, str(opts.get("bit_depth", "source")))
            args += codec_args
        else:
            args += _CODEC[job.dst_format]
            args += ["-b:a", str(opts.get("bitrate", "192k"))]

        # Metadati (ID3/tag): -map_metadata -1 li rimuove se richiesto lo strip.
        if job.strip_metadata:
            args += ["-map_metadata", "-1"]
        elif job.dst_format in _COVER_DST and any(
                stream["attached_pic"] for stream in probe_streams(job.src_path)):
            # La copertina è un metadato come i tag: si conserva (copiata, non
            # ricodificata) dove il contenitore la regge. Prima -vn la toglieva sempre.
            args += ["-map", "0:v:0", "-c:v", "copy", "-disposition:v:0", "attached_pic"]

        args += [str(job.output_path)]

        progress(ProgressEvent(0.05, "Converting audio…"))
        code, err = run_ffmpeg(args, job.output_path, duration, progress, job.cancel)
        if code != 0:
            return ConversionResult(ok=False, error=err or f"FFmpeg exited with code {code}.")

        size = job.output_path.stat().st_size
        # Verdetto a runtime: ora la profondità della sorgente è nota (come il
        # multi-frame di image.py). Una profondità esplicita che non riduce nulla
        # torna lossless; una che riduce resta lossy col motivo esatto.
        verdict = self.verdict(job.src_format, job.dst_format, opts)
        if job.dst_format in _LOSSLESS and not _altering_options(opts):
            verdict = (lossy(loss_reason) if loss_reason else
                       lossless(f"{job.dst_format.upper()} keeps the source's sample format; "
                                f"the convert step adds no loss."))
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=verdict)
