"""Converter immagini basato su Pillow (+ plugin opzionali HEIC/AVIF).

Verdetto onesto: il badge riflette il passo di conversione. Salvare in PNG/BMP/
TIFF/WebP-lossless non aggiunge perdita (LOSSLESS) anche se la sorgente era già
lossy; salvare in JPEG/HEIC/AVIF/GIF/ICO introduce perdita (LOSSY) e va spiegato.
"""

from __future__ import annotations

from PIL import Image, ImageOps, ImageSequence

from ..core.base import Converter
from ..core.deps import has_module
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.options import BoolOption, ChoiceOption, IntOption, Option
from ..core.quality import QualityVerdict, lossless, lossy, unsupported
from ..core.registry import register

# --- Registrazione plugin opzionali (HEIC/AVIF) ---------------------------------
# Questi pacchetti portano i codec nativi (libheif, ecc.): su Windows non sono
# banali, perciò sono opzionali. Se assenti, i relativi formati restano in grigio.
_HEIF_OK = False
if has_module("pillow_heif"):
    try:
        import pillow_heif  # type: ignore

        pillow_heif.register_heif_opener()
        _HEIF_OK = True
    except Exception:  # pragma: no cover - dipende dall'ambiente
        _HEIF_OK = False

_AVIF_OK = False
if has_module("pillow_avif"):
    try:
        import pillow_avif  # type: ignore  # noqa: F401  (auto-registra il plugin)

        _AVIF_OK = True
    except Exception:  # pragma: no cover
        _AVIF_OK = False
# AVIF potrebbe essere supportato nativamente da Pillow >= 11.3.
if not _AVIF_OK:
    _AVIF_OK = "AVIF" in Image.SAVE and "AVIF" in Image.OPEN

# Nome del formato Pillow per ogni id canonico.
_PIL_FORMAT = {
    "png": "PNG", "jpg": "JPEG", "webp": "WEBP", "bmp": "BMP", "tiff": "TIFF",
    "gif": "GIF", "ico": "ICO", "heic": "HEIF", "avif": "AVIF",
}

# Target che reggono più frame/pagine (animazioni, TIFF multipagina).
_MULTI_FRAME_DST = ("gif", "webp", "tiff")

# Modi colore che ogni formato sa scrivere SENZA conversioni.
_NATIVE_MODES = {
    "png": {"1", "L", "LA", "P", "RGB", "RGBA", "I;16", "I;16B", "I"},
    "jpg": {"L", "RGB", "CMYK"},
    # BMP: niente RGBA. Pillow scrive 32 bit ma rilegge senza alpha, e molti
    # programmi fanno lo stesso: la trasparenza va dichiarata persa.
    "bmp": {"1", "L", "RGB"},
    "tiff": {"1", "L", "LA", "P", "RGB", "RGBA", "CMYK", "I;16", "I", "F", "YCbCr"},
    "webp": {"RGB", "RGBA"},
    "gif": {"P", "L", "RGB", "RGBA"},      # Pillow quantizza tenendo la trasparenza
    "ico": {"RGBA", "RGB", "P", "L", "1"},
    "heic": {"RGB", "RGBA"},
    "avif": {"RGB", "RGBA"},
}
_HIGH_DEPTH = {"I;16", "I;16B", "I;16L", "I", "F"}


def _to_8bit(image: Image.Image) -> Image.Image:
    """16 bit/float → 8 bit SCALANDO: convert("L") da I;16 satura tutto oltre 255."""
    if image.mode == "F":
        return image.point(lambda value: value * 255).convert("L")
    as_int = image.convert("I") if image.mode != "I" else image
    peak = as_int.getextrema()[1] or 1
    scale = 1 / 256 if peak > 255 else 1
    return as_int.point(lambda value: value * scale).convert("L")


def _prepare_mode(image: Image.Image, dst: str) -> tuple[Image.Image, str | None]:
    """Porta l'immagine a un modo che `dst` sa scrivere; ritorna (immagine, perdita).

    Prima un JPEG CMYK verso PNG o un PNG a 16 bit verso BMP/JPG fallivano con un
    OSError grezzo ("cannot write mode CMYK as PNG"); ora si converte e il
    verdetto dice cosa si è perso."""
    native = _NATIVE_MODES.get(dst, {"RGB", "RGBA"})
    mode = image.mode
    has_alpha = mode in ("RGBA", "LA", "PA", "La", "RGBa") or (
        mode == "P" and "transparency" in image.info)
    if mode == "P" and has_alpha and dst == "ico":
        # ICO in palette perde la trasparenza: in RGBA la conserva.
        return image.convert("RGBA"), None
    if dst == "bmp" and has_alpha:
        return (image.convert("RGBA").convert("RGB"),
                "Transparency is dropped (BMP has no reliable alpha channel).")
    if dst == "bmp" and mode == "P":
        return image.convert("RGB"), None      # stessi pixel, niente palette da perdere
    if mode in native:
        return image, None
    if dst == "jpg":
        if has_alpha:
            return ImageConverter._flatten_alpha(image), "Transparency is flattened onto white (JPEG has no alpha)."
        if mode in _HIGH_DEPTH:
            return _to_8bit(image), "16-bit depth is reduced to 8-bit (JPEG is 8-bit)."
        return image.convert("RGB"), None
    if mode in _HIGH_DEPTH:
        return _to_8bit(image), f"16-bit depth is reduced to 8-bit ({dst.upper()} is 8-bit)."
    if mode == "CMYK":
        return image.convert("RGB"), f"CMYK colours are converted to RGB ({dst.upper()} has no CMYK)."
    return image.convert("RGBA" if has_alpha else "RGB"), None


def _fit_square(image: Image.Image, size: int) -> Image.Image:
    """Immagine dentro un quadrato size×size, proporzioni intatte, bordi trasparenti."""
    rgba = image.convert("RGBA")
    ratio = size / max(rgba.size)
    if ratio != 1:
        rgba = rgba.resize((max(1, round(rgba.width * ratio)), max(1, round(rgba.height * ratio))),
                           Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(rgba, ((size - rgba.width) // 2, (size - rgba.height) // 2))
    return canvas


_LOSSLESS_DST = {"png", "bmp", "tiff", "webp"}  # webp salvato in modalità lossless di default
_READABLE = frozenset({"png", "jpg", "webp", "heic", "bmp", "tiff", "gif", "avif", "ico"})
_WRITABLE = frozenset({"png", "jpg", "webp", "heic", "bmp", "tiff", "gif", "avif", "ico", "svg"})


@register
class ImageConverter(Converter):
    category = "image"
    name = "Images (Pillow)"
    input_formats = _READABLE
    output_formats = _WRITABLE

    # --- Disponibilità ---

    def format_available(self, format_id: str) -> tuple[bool, str]:
        if format_id == "svg":
            return False, "Raster → SVG tracing is planned for a later release."
        if format_id == "heic" and not _HEIF_OK:
            return False, "HEIC needs the optional 'pillow-heif' package (pip install pillow-heif)."
        if format_id == "avif" and not _AVIF_OK:
            return False, "AVIF needs the optional 'pillow-avif-plugin' package (pip install pillow-avif-plugin)."
        return True, ""

    # --- Qualità ---

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src not in self.input_formats or dst not in self.output_formats:
            return unsupported()
        if dst in ("png", "bmp"):
            return lossless("Stores exact pixels in a lossless raster format.")
        if dst == "tiff":
            return lossless("TIFF with LZW/Deflate stores exact pixels (lossless).")
        if dst == "webp":
            # WebP può essere lossless (default) o lossy: il verdetto dipende dall'opzione.
            if options is not None and not options.get("lossless", True):
                return lossy("WebP lossy mode selected; some detail is discarded.")
            return lossless("Saved as lossless WebP by default (toggle for smaller lossy files).")
        if dst == "jpg":
            return lossy("JPEG is a lossy codec; fine detail is discarded on encode.")
        if dst in ("heic", "avif"):
            return lossy(f"{dst.upper()} uses lossy compression by default.")
        if dst == "gif":
            return lossy("GIF is limited to a 256-colour palette; colours are quantised.")
        if dst == "ico":
            return lossy("ICO resizes the image to icon dimensions.")
        if dst == "svg":
            return lossy("Raster → SVG tracing approximates pixels as vector paths.")
        return unsupported()

    # --- Opzioni dichiarative ---

    def options_schema(self, src: str, dst: str) -> list[Option]:
        if dst == "jpg":
            return [
                IntOption("quality", "JPEG quality", 95, 1, 100, slider=True, suffix=" %",
                          help="95 is visually near-lossless; lower means smaller files."),
            ]
        if dst == "webp":
            return [
                BoolOption("lossless", "Lossless mode", True,
                           help="On = pixel-perfect. Off = smaller lossy file using the quality below."),
                IntOption("quality", "Quality (lossy mode)", 90, 1, 100, slider=True, suffix=" %"),
            ]
        if dst == "png":
            return [IntOption("compress_level", "PNG compression", 6, 0, 9,
                              help="Higher = smaller file, slower. Always lossless.")]
        if dst == "tiff":
            return [ChoiceOption("compression", "TIFF compression", "tiff_lzw",
                                 choices=[("tiff_lzw", "LZW (lossless)"),
                                          ("tiff_deflate", "Deflate (lossless)"),
                                          ("none", "None (uncompressed)")])]
        if dst in ("heic", "avif"):
            return [IntOption("quality", f"{dst.upper()} quality", 90, 1, 100, slider=True, suffix=" %")]
        if dst == "ico":
            return [ChoiceOption("size", "Icon size", 256,
                                 choices=[(s, f"{s}×{s}") for s in (16, 32, 48, 64, 128, 256)])]
        return []

    # --- Esecuzione ---

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        progress(ProgressEvent(0.1, "Reading image…"))
        dst = job.dst_format
        notes: list[str] = []          # perdite scoperte a runtime (modo colore, frame)
        with Image.open(job.src_path) as img:
            img.load()
            n_frames = getattr(img, "n_frames", 1)
            is_animated = getattr(img, "is_animated", False) or n_frames > 1
            keeps_frames = is_animated and dst in _MULTI_FRAME_DST

            save_kwargs: dict = {}
            # Metadati: per default si preservano EXIF e profilo ICC.
            if not job.strip_metadata:
                exif = img.info.get("exif")
                if exif and dst in ("jpg", "webp", "tiff", "heic", "avif", "png"):
                    save_kwargs["exif"] = exif
                icc = img.info.get("icc_profile")
                if icc:
                    save_kwargs["icc_profile"] = icc

            if keeps_frames:
                # Ogni frame va preparato per il formato di destinazione: prima la
                # conversione colore toccava solo il primo frame e save_all salvava
                # quindi UN frame (WebP/TIFF animati → GIF statica, in silenzio).
                frames, durations = [], []
                for frame in ImageSequence.Iterator(img):
                    prepared, note = _prepare_mode(frame.copy(), dst)
                    if note and note not in notes:
                        notes.append(note)
                    frames.append(prepared)
                    durations.append(frame.info.get("duration", img.info.get("duration", 100)))
                image = frames[0]
                save_kwargs.update(save_all=True, append_images=frames[1:])
                if dst in ("gif", "webp"):
                    save_kwargs.update(duration=durations, loop=img.info.get("loop", 0))
                    if dst == "gif":
                        save_kwargs["disposal"] = 2
            else:
                # Orientamento EXIF applicato ai PIXEL (png/bmp non portano l'EXIF);
                # exif_transpose rimuove anche il tag, così non si riapplica due volte.
                image = img if is_animated else ImageOps.exif_transpose(img)
                if "exif" in save_kwargs:
                    # L'EXIF dell'immagine RADDRIZZATA (senza Orientation).
                    save_kwargs["exif"] = image.info.get("exif", save_kwargs["exif"])
                image, note = _prepare_mode(image, dst)
                if note:
                    notes.append(note)

            opts = job.options
            progress(ProgressEvent(0.5, "Encoding…"))
            if dst == "jpg":
                save_kwargs.update(quality=int(opts.get("quality", 95)), optimize=True,
                                   progressive=True, subsampling=0)
            elif dst == "webp":
                if opts.get("lossless", True):
                    save_kwargs.update(lossless=True, quality=100, method=6)
                else:
                    save_kwargs.update(lossless=False, quality=int(opts.get("quality", 90)), method=6)
            elif dst == "png":
                save_kwargs.update(compress_level=int(opts.get("compress_level", 6)), optimize=True)
            elif dst == "tiff":
                comp = opts.get("compression", "tiff_lzw")
                if comp != "none":
                    save_kwargs["compression"] = comp
            elif dst in ("heic", "avif"):
                save_kwargs["quality"] = int(opts.get("quality", 90))
            elif dst == "ico":
                size = int(opts.get("size", 256))
                # Pillow rifiuta le dimensioni più grandi dell'immagine ("All sizes
                # too large"): un'icona da 256 di un PNG 32×32 falliva sempre. Si
                # porta l'immagine al quadrato richiesto (proporzioni intatte,
                # bordi trasparenti), come fa un editor di icone.
                image = _fit_square(image, size)
                save_kwargs["sizes"] = [(size, size)]

            image.save(job.output_path, format=_PIL_FORMAT[dst], **save_kwargs)

        size = job.output_path.stat().st_size
        verdict = self.verdict(job.src_format, dst, job.options)
        # Onestà: se la sorgente è multi-frame ma il target tiene un solo frame,
        # è andato perso tutto tranne il primo — NON è lossless, qualunque cosa
        # dica il verdetto per-formato.
        if is_animated and not keeps_frames:
            notes.insert(0, f"Source has {n_frames} frames/pages; only the first is kept in "
                            f"{dst.upper()} (single-frame format).")
        if notes:
            reason = " ".join(notes)
            verdict = lossy(reason if verdict.is_lossless else f"{verdict.reason} {reason}")
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=verdict)

    @staticmethod
    def _flatten_alpha(image: Image.Image) -> Image.Image:
        """Compone l'immagine con trasparenza su uno sfondo bianco opaco."""
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
