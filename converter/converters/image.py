"""Converter immagini basato su Pillow (+ plugin opzionali HEIC/AVIF).

Verdetto onesto: il badge riflette il passo di conversione. Salvare in PNG/BMP/
TIFF/WebP-lossless non aggiunge perdita (LOSSLESS) anche se la sorgente era già
lossy; salvare in JPEG/HEIC/AVIF/GIF/ICO introduce perdita (LOSSY) e va spiegato.
"""

from __future__ import annotations

from PIL import Image, ImageOps

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
        with Image.open(job.src_path) as img:
            img.load()
            n_frames = getattr(img, "n_frames", 1)
            is_animated = getattr(img, "is_animated", False) or n_frames > 1
            # Orientamento EXIF: applicalo ai PIXEL così una foto verticale non
            # esce coricata verso png/bmp (che non portano l'EXIF). Solo per le
            # sorgenti a frame singolo: exif_transpose collasserebbe un'animazione.
            # exif_transpose rimuove anche il tag Orientation, quindi la copia
            # dell'EXIF più sotto non lo riapplica una seconda volta.
            if not is_animated:
                img = ImageOps.exif_transpose(img)
            save_kwargs: dict = {}
            out_format = _PIL_FORMAT[job.dst_format]
            image = img

            # Metadati: per default si preservano EXIF e profilo ICC.
            if not job.strip_metadata:
                exif = img.info.get("exif")
                if exif and job.dst_format in ("jpg", "webp", "tiff", "heic", "avif"):
                    save_kwargs["exif"] = exif
                icc = img.info.get("icc_profile")
                if icc:
                    save_kwargs["icc_profile"] = icc

            opts = job.options
            progress(ProgressEvent(0.5, "Encoding…"))

            if job.dst_format == "jpg":
                # JPEG non ha canale alpha: appiattisci su bianco. (commento IT)
                if image.mode in ("RGBA", "LA", "P"):
                    image = self._flatten_alpha(image)
                save_kwargs.update(quality=int(opts.get("quality", 95)), optimize=True,
                                   progressive=True, subsampling=0)
            elif job.dst_format == "webp":
                if opts.get("lossless", True):
                    save_kwargs.update(lossless=True, quality=100, method=6)
                else:
                    save_kwargs.update(lossless=False, quality=int(opts.get("quality", 90)), method=6)
            elif job.dst_format == "png":
                save_kwargs.update(compress_level=int(opts.get("compress_level", 6)), optimize=True)
            elif job.dst_format == "tiff":
                comp = opts.get("compression", "tiff_lzw")
                if comp != "none":
                    save_kwargs["compression"] = comp
            elif job.dst_format in ("heic", "avif"):
                save_kwargs["quality"] = int(opts.get("quality", 90))
            elif job.dst_format == "gif":
                if image.mode not in ("P", "L"):
                    image = image.convert("RGBA").convert("P", palette=Image.ADAPTIVE)
            elif job.dst_format == "ico":
                size = int(opts.get("size", 256))
                save_kwargs["sizes"] = [(size, size)]

            # Sorgente multi-frame verso un container che regge l'animazione/le
            # pagine (gif/webp/tiff): preservali tutti invece di tenere solo il
            # primo frame in silenzio.
            if is_animated and job.dst_format in ("gif", "webp", "tiff"):
                save_kwargs["save_all"] = True

            image.save(job.output_path, format=out_format, **save_kwargs)

        size = job.output_path.stat().st_size
        verdict = self.verdict(job.src_format, job.dst_format, job.options)
        # Onestà: se la sorgente è multi-frame ma il target tiene un solo frame,
        # è andato perso tutto tranne il primo — NON è lossless, qualunque cosa
        # dica il verdetto per-formato.
        if is_animated and job.dst_format not in ("gif", "webp", "tiff"):
            verdict = lossy(f"Source has multiple frames/pages; only the first is kept "
                            f"in {job.dst_format.upper()} (single-frame format).")
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=verdict)

    @staticmethod
    def _flatten_alpha(image: Image.Image) -> Image.Image:
        """Compone l'immagine con trasparenza su uno sfondo bianco opaco."""
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
