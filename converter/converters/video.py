"""Converter video basato su ffmpeg (incluso video -> GIF).

Verdetto: la ri-codifica video è sempre LOSSY; usiamo impostazioni a qualità
elevata di default (CRF basso). Anche GIF è LOSSY (palette a 256 colori).
"""

from __future__ import annotations

from ..core.base import Converter
from ..core.deps import ffmpeg_status
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.options import ChoiceOption, IntOption, Option, TextOption
from ..core.quality import QualityVerdict, lossy, unsupported
from ..core.registry import register
from ._ffmpeg import parse_time, probe_duration, probe_streams, run_ffmpeg

_INPUT = frozenset({"mp4", "mkv", "mov", "webm", "avi"})
_OUTPUT = frozenset({"mp4", "mkv", "mov", "webm", "avi", "gif"})

MAX_FPS = 240  # oltre questo un valore di frame rate è palesemente irrealistico


def _field(opts: dict, key: str) -> str:
    """Valore stringa ripulito di un'opzione testo (stringa vuota se assente)."""
    value = opts.get(key)
    return str(value).strip() if value is not None else ""


def validate_video_options(opts: dict, duration: float | None) -> str | None:
    """Validatore PURO (niente ffmpeg) delle opzioni video avanzate.

    `duration` è la durata reale del sorgente in secondi, o None se ignota: in
    tal caso i controlli sui limiti del trim si degradano (parsing e start<end
    restano attivi). Ritorna un messaggio chiaro o None se tutto valido.
    """
    start_raw, end_raw = _field(opts, "trim_start"), _field(opts, "trim_end")
    start = end = None

    if start_raw:
        start = parse_time(start_raw)
        if start is None:
            return f"Trim start '{start_raw}' isn't a valid time (use seconds or HH:MM:SS)."
        if start < 0:
            return "Trim start must be 0 or greater."
        if duration is not None and start >= duration:
            return f"Trim start {start_raw} is past the video length ({duration:.1f}s)."

    if end_raw:
        end = parse_time(end_raw)
        if end is None:
            return f"Trim end '{end_raw}' isn't a valid time (use seconds or HH:MM:SS)."
        if end <= 0:
            return "Trim end must be greater than 0."
        if duration is not None and end > duration + 0.05:
            return f"Trim end {end_raw} exceeds video length ({duration:.1f}s)."

    if start is not None and end is not None and end <= start:
        return "Trim end must be after start."

    for key, label in (("scale_w", "Width"), ("scale_h", "Height")):
        raw = _field(opts, key)
        if raw:
            try:
                value = int(raw)
            except ValueError:
                return f"{label} must be a whole number of pixels."
            if value <= 0:
                return f"{label} must be a positive number."
            if value % 2 != 0:
                return f"{label} {value} must be even (the codec requires even dimensions)."

    fps_raw = _field(opts, "fps")
    if fps_raw and fps_raw != "original":
        try:
            fps = float(fps_raw)
        except ValueError:
            return f"Frame rate '{fps_raw}' isn't a number."
        if fps <= 0:
            return "Frame rate must be positive."
        if fps > MAX_FPS:
            return f"Frame rate {fps_raw} is unrealistic (max {MAX_FPS})."

    return None

# Sottotitoli testuali (convertibili tra loro); PGS/DVB/DVD sono immagini.
_TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "mov_text", "webvtt", "text"}
# Codec sottotitoli per contenitore; None = il contenitore non li regge.
_SUBTITLE_CODEC = {"mkv": "copy", "mp4": "mov_text", "mov": "mov_text", "webm": "webvtt",
                   "avi": None}


def _trimmed_duration(opts: dict, duration: float | None) -> float | None:
    """Durata dell'USCITA dopo il trim: con la durata intera il progresso si
    fermava a metà barra su un taglio corto."""
    if duration is None:
        return None
    start = parse_time(_field(opts, "trim_start")) or 0.0
    end = parse_time(_field(opts, "trim_end")) or duration
    return max(end - start, 0.1)


# Codec video consentiti per ciascun contenitore (il primo è il default).
_CODECS_BY_CONTAINER = {
    "mp4": ["h264", "h265"],
    "mov": ["h264", "h265"],
    "mkv": ["h264", "h265", "vp9"],
    "webm": ["vp9"],
    "avi": ["h264"],
}
_CODEC_LABEL = {"h264": "H.264 (x264)", "h265": "H.265 (x265)", "vp9": "VP9"}


def _video_codec_args(codec: str, crf: int) -> list[str]:
    if codec == "h264":
        return ["-c:v", "libx264", "-crf", str(crf), "-preset", "slow", "-pix_fmt", "yuv420p"]
    if codec == "h265":
        return ["-c:v", "libx265", "-crf", str(crf), "-preset", "slow", "-pix_fmt", "yuv420p"]
    if codec == "vp9":
        return ["-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0"]
    return ["-c:v", "libx264", "-crf", str(crf)]


@register
class VideoConverter(Converter):
    category = "video"
    name = "Video (FFmpeg)"
    input_formats = _INPUT
    output_formats = _OUTPUT

    @classmethod
    def available(cls):
        return ffmpeg_status()

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src not in _INPUT or dst not in _OUTPUT:
            return unsupported()
        if dst == "gif":
            return lossy("GIF is limited to 256 colours and a low frame rate.")
        return lossy("Video is re-encoded; this is lossy. High-quality settings (low CRF) are used by default.")

    def options_schema(self, src: str, dst: str) -> list[Option]:
        if dst == "gif":
            return [
                ChoiceOption("fps", "Frame rate", "15",
                             choices=[(f, f"{f} fps") for f in ("10", "15", "20", "25")]),
                ChoiceOption("width", "Width", "480",
                             choices=[(w, f"{w} px") for w in ("320", "480", "640", "800")]),
            ]
        codecs = _CODECS_BY_CONTAINER.get(dst, ["h264"])
        return [
            ChoiceOption("codec", "Video codec", codecs[0],
                         choices=[(c, _CODEC_LABEL[c]) for c in codecs]),
            IntOption("crf", "Quality (CRF)", 18, 0, 51, slider=True,
                      help="Lower = higher quality / bigger file. 18 is visually near-lossless."),
            ChoiceOption("resolution", "Resolution", "original",
                         choices=[("original", "Keep original"), ("2160", "2160p"), ("1440", "1440p"),
                                  ("1080", "1080p"), ("720", "720p"), ("480", "480p")]),
            TextOption("fps", "Frame rate", "", placeholder="original",
                       help="Leave blank to keep the source frame rate. Numbers only (max 240)."),
            TextOption("trim_start", "Trim start", "", placeholder="e.g. 0:05",
                       help="Cut from this point. Seconds or HH:MM:SS. Blank = from the start."),
            TextOption("trim_end", "Trim end", "", placeholder="e.g. 0:30",
                       help="Cut up to this point. Must be after start and within the video length."),
            TextOption("scale_w", "Custom width", "", placeholder="auto",
                       help="Optional width in px (even number). Overrides Resolution; alone, "
                                 "the height follows the aspect ratio."),
            TextOption("scale_h", "Custom height", "", placeholder="auto",
                       help="Optional height in px (even number). Overrides Resolution; alone, "
                                 "the width follows the aspect ratio."),
        ]

    # --- Validazione ---

    def validate(self, job: ConversionJob) -> str | None:
        if job.dst_format == "gif":
            return None  # le opzioni GIF sono tutte vincolate (dropdown): nulla da validare
        duration = probe_duration(job.src_path)
        return validate_video_options(job.options, duration)

    def validate_quick(self, src: str, dst: str, options: dict) -> str | None:
        if dst == "gif":
            return None
        # Senza durata: parsing, even/positive, fps; il limite sul trim resta a validate().
        return validate_video_options(options, None)

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        duration = probe_duration(job.src_path)
        progress(ProgressEvent(0.05, "Converting video…"))

        notes: list[str] = []
        if job.dst_format == "gif":
            args = self._gif_args(job)
        else:
            args, notes = self._video_args(job, probe_streams(job.src_path))
            duration = _trimmed_duration(job.options, duration)

        if job.strip_metadata:
            args += ["-map_metadata", "-1"]
        args += [str(job.output_path)]

        code, err = run_ffmpeg(args, job.output_path, duration, progress, job.cancel)
        if code != 0:
            return ConversionResult(ok=False, error=err or f"FFmpeg exited with code {code}.")

        size = job.output_path.stat().st_size
        verdict = self.verdict(job.src_format, job.dst_format)
        if notes:
            verdict = lossy(verdict.reason + " " + " ".join(notes))
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=verdict)

    def _video_args(self, job: ConversionJob, streams: list[dict]) -> tuple[list[str], list[str]]:
        """Argomenti ffmpeg + perdite da dichiarare nel verdetto.

        Mappatura esplicita: la selezione automatica di ffmpeg teneva UNA traccia
        audio (le altre lingue sparivano) e un solo sottotitolo, e un sottotitolo
        a immagine (PGS/DVD) verso mp4 faceva fallire l'intera conversione."""
        opts = job.options
        dst = job.dst_format
        args = ["-i", str(job.src_path), "-map", "0:V:0", "-map", "0:a?"]
        notes: list[str] = []

        audio_tracks = sum(1 for stream in streams if stream["type"] == "audio")
        subtitles = [stream for stream in streams if stream["type"] == "subtitle"]
        text_subs = [stream for stream in subtitles if stream["codec"] in _TEXT_SUBTITLE_CODECS]
        sub_codec = _SUBTITLE_CODEC.get(dst)
        kept_subs = subtitles if dst == "mkv" else (text_subs if sub_codec else [])
        for stream in kept_subs:
            args += ["-map", f"0:{stream['index']}"]
        if kept_subs:
            args += ["-c:s", sub_codec]
        dropped = len(subtitles) - len(kept_subs)
        if dropped:
            why = (f"{dst.upper()} cannot hold subtitles" if not sub_codec else
                   f"{dst.upper()} can only hold text subtitles")
            notes.append(f"{dropped} subtitle track(s) were left out ({why}).")
        if dst == "mkv":
            args += ["-map", "0:t?", "-c:t", "copy"]   # font allegati ai sottotitoli ASS

        # Trim: -ss/-to come opzioni di output (timeline del sorgente). I valori
        # sono già stati validati da validate(), quindi qui ci si fida del parsing.
        start_raw, end_raw = _field(opts, "trim_start"), _field(opts, "trim_end")
        if start_raw:
            args += ["-ss", str(parse_time(start_raw))]
        if end_raw:
            args += ["-to", str(parse_time(end_raw))]

        # Il codec deve essere ammesso dal contenitore: senza opzioni (CLI, preset
        # vecchi) il default fisso "h264" rendeva webm impossibile da scrivere.
        allowed = _CODECS_BY_CONTAINER.get(dst, ["h264"])
        codec = opts.get("codec") if opts.get("codec") in allowed else allowed[0]
        args += _video_codec_args(codec, int(opts.get("crf", 18)))
        if codec == "h265" and dst in ("mp4", "mov"):
            args += ["-tag:v", "hvc1"]   # senza, QuickTime/iOS/macOS non riproducono l'HEVC

        # Scala: dimensioni custom (anche una sola: l'altra segue le proporzioni,
        # lato pari) hanno precedenza; altrimenti la risoluzione del menu.
        scale_w, scale_h = _field(opts, "scale_w"), _field(opts, "scale_h")
        if scale_w or scale_h:
            args += ["-vf", f"scale={scale_w or -2}:{scale_h or -2}"]
        elif opts.get("resolution", "original") != "original":
            args += ["-vf", f"scale=-2:{opts['resolution']}"]

        fps_raw = _field(opts, "fps")
        if fps_raw and fps_raw != "original":
            args += ["-r", fps_raw]

        # Audio: Opus per webm, AAC altrimenti — TUTTE le tracce, non solo la prima.
        args += ["-c:a", "libopus"] if dst == "webm" else ["-c:a", "aac", "-b:a", "192k"]
        if audio_tracks > 1 and dst == "avi":
            notes.append("AVI players usually play only the first of the audio tracks.")
        return args, notes

    def _gif_args(self, job: ConversionJob) -> list[str]:
        opts = job.options
        fps = opts.get("fps", "15")
        width = opts.get("width", "480")
        # palettegen/paletteuse: genera una palette ottimale per una GIF di qualità.
        vf = (f"fps={fps},scale={width}:-1:flags=lanczos,"
              f"split[s0][s1];[s0]palettegen[p];[s1][p]paletteuse")
        return ["-i", str(job.src_path), "-filter_complex", vf, "-an"]
