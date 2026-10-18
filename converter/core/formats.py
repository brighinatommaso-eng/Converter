"""Tabella canonica dei formati supportati.

Ogni formato ha un id canonico (es. "jpg" copre jpg/jpeg). Le categorie
guidano sia il converter da usare sia la lista dei target mostrata in UI.
"""

from __future__ import annotations

from dataclasses import dataclass

# Categorie note. "document" è elencato per il rilevamento e la Fase 2, ma in
# Fase 1 non esiste ancora un converter per i documenti.
CATEGORIES = ("image", "audio", "video", "data", "document", "model3d")


@dataclass(frozen=True)
class Format:
    id: str
    label: str
    category: str
    extensions: tuple[str, ...]  # la prima è quella usata per i file di output
    mime: str = ""


def _f(*args, **kwargs) -> tuple[str, Format]:
    fmt = Format(*args, **kwargs)
    return fmt.id, fmt


FORMATS: dict[str, Format] = dict(
    [
        # --- Immagini ---
        _f("png", "PNG", "image", ("png",), "image/png"),
        _f("jpg", "JPEG", "image", ("jpg", "jpeg"), "image/jpeg"),
        _f("webp", "WebP", "image", ("webp",), "image/webp"),
        _f("heic", "HEIC", "image", ("heic", "heif"), "image/heic"),
        _f("bmp", "BMP", "image", ("bmp",), "image/bmp"),
        _f("tiff", "TIFF", "image", ("tiff", "tif"), "image/tiff"),
        _f("gif", "GIF", "image", ("gif",), "image/gif"),
        _f("avif", "AVIF", "image", ("avif",), "image/avif"),
        _f("ico", "ICO", "image", ("ico",), "image/x-icon"),
        _f("svg", "SVG", "image", ("svg",), "image/svg+xml"),
        # --- Audio ---
        _f("mp3", "MP3", "audio", ("mp3",), "audio/mpeg"),
        _f("wav", "WAV", "audio", ("wav",), "audio/wav"),
        _f("flac", "FLAC", "audio", ("flac",), "audio/flac"),
        _f("aac", "AAC", "audio", ("aac",), "audio/aac"),
        _f("ogg", "OGG", "audio", ("ogg",), "audio/ogg"),
        _f("m4a", "M4A", "audio", ("m4a",), "audio/mp4"),
        _f("opus", "Opus", "audio", ("opus",), "audio/opus"),
        # --- Video ---
        _f("mp4", "MP4", "video", ("mp4",), "video/mp4"),
        _f("mkv", "MKV", "video", ("mkv",), "video/x-matroska"),
        _f("mov", "MOV", "video", ("mov",), "video/quicktime"),
        _f("webm", "WebM", "video", ("webm",), "video/webm"),
        _f("avi", "AVI", "video", ("avi",), "video/x-msvideo"),
        # --- Dati ---
        _f("csv", "CSV", "data", ("csv",), "text/csv"),
        _f("tsv", "TSV", "data", ("tsv",), "text/tab-separated-values"),
        _f("json", "JSON", "data", ("json",), "application/json"),
        _f("xlsx", "XLSX", "data", ("xlsx",), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        _f("ods", "ODS", "data", ("ods",), "application/vnd.oasis.opendocument.spreadsheet"),
        _f("xml", "XML", "data", ("xml",), "application/xml"),
        _f("yaml", "YAML", "data", ("yaml", "yml"), "application/yaml"),
        # --- Documenti (Fase 2; qui solo per il rilevamento) ---
        _f("pdf", "PDF", "document", ("pdf",), "application/pdf"),
        _f("docx", "DOCX", "document", ("docx",), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        _f("odt", "ODT", "document", ("odt",), "application/vnd.oasis.opendocument.text"),
        _f("rtf", "RTF", "document", ("rtf",), "application/rtf"),
        _f("md", "Markdown", "document", ("md", "markdown"), "text/markdown"),
        _f("txt", "Text", "document", ("txt",), "text/plain"),
        _f("html", "HTML", "document", ("html", "htm"), "text/html"),
        _f("epub", "EPUB", "document", ("epub",), "application/epub+zip"),
        # --- Modelli 3D ---
        _f("obj", "OBJ", "model3d", ("obj",), "model/obj"),
        _f("stl", "STL", "model3d", ("stl",), "model/stl"),
        _f("ply", "PLY", "model3d", ("ply",), "model/ply"),
        _f("glb", "GLB", "model3d", ("glb",), "model/gltf-binary"),
        _f("gltf", "glTF", "model3d", ("gltf",), "model/gltf+json"),
        _f("3mf", "3MF", "model3d", ("3mf",), "model/3mf"),
    ]
)


def primary_ext(format_id: str) -> str:
    return FORMATS[format_id].extensions[0]


def category_of(format_id: str) -> str | None:
    fmt = FORMATS.get(format_id)
    return fmt.category if fmt else None


def label_of(format_id: str) -> str:
    fmt = FORMATS.get(format_id)
    return fmt.label if fmt else format_id.upper()


def by_extension(ext: str) -> str | None:
    """Restituisce l'id canonico data un'estensione (senza punto)."""
    ext = ext.lower().lstrip(".")
    for fmt in FORMATS.values():
        if ext in fmt.extensions:
            return fmt.id
    return None
