"""Rilevamento del formato dal CONTENUTO, non dall'estensione.

Le estensioni mentono, quindi il rilevamento è a strati:
  1. firme binarie (magic bytes) per i formati binari;
  2. ispezione del contenuto degli archivi ZIP (xlsx/ods/docx/...);
  3. sniffing strutturale per i formati testuali (json/yaml/xml/svg/html/csv/tsv)
     che non hanno magic bytes affidabili;
  4. l'estensione come ultimo indizio (tiebreaker), mai come prova.
"""

from __future__ import annotations

import csv
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .formats import by_extension, category_of


@dataclass(frozen=True)
class DetectedFormat:
    format_id: str | None
    category: str | None
    confidence: float  # 0..1
    method: str        # come è stato rilevato (per debug/UI)

    @property
    def ok(self) -> bool:
        return self.format_id is not None


def _read_head(path: Path, n: int = 4096) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(n)


def _ftyp_brand(head: bytes) -> str | None:
    """Formati basati su box ISO-BMFF (mp4/mov/m4a/heic/avif): 'ftyp' a offset 4."""
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None
    brand = head[8:12].decode("ascii", "ignore").strip().lower()
    if brand in ("heic", "heix", "heif", "mif1", "hevc", "msf1"):
        return "heic"
    if brand in ("avif", "avis"):
        return "avif"
    if brand in ("qt",):
        return "mov"
    if brand.startswith("m4a"):
        return "m4a"
    # isom/mp41/mp42/iso2/dash/... -> mp4 generico
    return "mp4"


def _magic(head: bytes) -> str | None:
    """Firme binarie hand-rolled: più affidabili delle librerie per i nostri formati."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if head.startswith(b"BM"):
        return "bmp"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return "tiff"
    if head.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"OggS"):
        # Contenitore Ogg: distingui Opus da Vorbis guardando l'header del codec.
        return "opus" if b"OpusHead" in head else "ogg"
    # AAC ADTS PRIMA di mp3: il sync ADTS (0xFFF1/0xFFF9, 12 bit) ricade sotto la
    # maschera lasca del check mp3 (0xFFE0, 3 bit) e verrebbe scambiato per mp3.
    if head.startswith(b"\xff\xf1") or head.startswith(b"\xff\xf9"):  # ADTS
        return "aac"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head.startswith(b"\x1a\x45\xdf\xa3"):  # EBML -> Matroska/WebM
        return "webm" if b"webm" in head[:64] else "mkv"
    # Modelli 3D con magic affidabile: glb (binary glTF) e ply. Lo STL binario
    # non ha magic (header di 80 byte a testo libero) → cade sull'estensione.
    if head.startswith(b"glTF"):  # header binario glTF (.glb)
        return "glb"
    if head.startswith(b"ply\n") or head.startswith(b"ply\r"):
        return "ply"
    if head.startswith(b"RIFF") and len(head) >= 12:
        fourcc = head[8:12]
        if fourcc == b"WAVE":
            return "wav"
        if fourcc == b"AVI ":
            return "avi"
        if fourcc == b"WEBP":
            return "webp"
    return _ftyp_brand(head)


def _zip_member(path: Path) -> str | None:
    """xlsx/ods/docx/odt/epub sono tutti contenitori ZIP: si distinguono SOLO
    guardando dentro l'archivio, mai dall'estensione.

    - ODF (ods/odt) ed EPUB dichiarano il tipo in un entry 'mimetype' (il primo
      del pacchetto, non compresso).
    - OOXML (docx/xlsx) dichiara i tipi MIME in '[Content_Types].xml'; lì si
      legge se il pacchetto è spreadsheetml (xlsx) o wordprocessingml (docx).
    """
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())

            # --- ODF ed EPUB: entry 'mimetype' ---
            if "mimetype" in names:
                mime = zf.read("mimetype").decode("ascii", "ignore").strip()
                if "opendocument.spreadsheet" in mime:
                    return "ods"
                if "opendocument.text" in mime:
                    return "odt"
                if "epub" in mime:
                    return "epub"

            # --- 3MF: modello 3D in un contenitore ZIP. Il pacchetto contiene
            # '3D/3dmodel.model' e il Content_Types dichiara '3dmanufacturing'. ---
            if any("3dmodel.model" in n for n in names):
                return "3mf"

            # --- OOXML: [Content_Types].xml è obbligatorio in ogni pacchetto ---
            if "[Content_Types].xml" in names:
                content_types = zf.read("[Content_Types].xml").decode("utf-8", "ignore")
                if "3dmanufacturing" in content_types:
                    return "3mf"
                if "spreadsheetml" in content_types:
                    return "xlsx"
                if "wordprocessingml" in content_types:
                    return "docx"
                if "presentationml" in content_types:
                    return None  # pptx non supportato

            # --- Fallback sulla struttura di cartelle (pacchetti non standard) ---
            if any(n.startswith("xl/") for n in names):
                return "xlsx"
            if any(n.startswith("word/") for n in names):
                return "docx"
    except (zipfile.BadZipFile, OSError, KeyError):
        return None
    return None


def _sniff_text(head: bytes) -> str | None:
    """Sniffing strutturale per i formati testuali privi di magic bytes."""
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = head.decode("utf-8-sig")
        except UnicodeDecodeError:
            return None

    stripped = text.lstrip()
    if not stripped:
        return None

    # STL ASCII: 'solid' seguito da 'facet normal'. Richiedere 'facet normal'
    # (non il solo 'solid ', che compare anche in testo libero e negli header
    # STL binari) evita i falsi positivi. Lo STL binario cade sull'estensione.
    lowered_head = stripped[:64].lower()
    if lowered_head.startswith("solid") and "facet normal" in text.lower():
        return "stl"

    lowered = stripped[:512].lower()
    if lowered.startswith("<?xml") or stripped.startswith("<"):
        if "<svg" in lowered:
            return "svg"
        if lowered.startswith("<!doctype html") or "<html" in lowered:
            return "html"
        return "xml"

    # JSON: deve fare il parse completo (head troncato può fallire: lo accettiamo
    # solo se il primo carattere è coerente e il parse riesce sull'intero head).
    if stripped[0] in "{[":
        try:
            json.loads(text)
            return "json"
        except json.JSONDecodeError:
            pass  # potrebbe essere troncato; ritenta a livello file più sotto

    # CSV/TSV: usa lo Sniffer della stdlib sulle prime righe.
    sample = "\n".join(text.splitlines()[:20])
    if sample:
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
            if dialect.delimiter == "\t":
                return "tsv"
            if dialect.delimiter == ",":
                return "csv"
        except csv.Error:
            pass
        if "\t" in sample and "," not in sample:
            return "tsv"

    # YAML: euristica leggera (chiave: valore o lista con trattino) per evitare
    # falsi positivi su testo libero. Lasciato per ultimo perché molto permissivo.
    if any(line and (": " in line or line.lstrip().startswith("- ")) for line in text.splitlines()[:20]):
        return "yaml"
    return None


def _full_json(path: Path):
    """Parse dell'intero file: ritorna l'oggetto JSON o None se non è JSON valido."""
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _is_gltf(obj) -> bool:
    """Un glTF (.gltf) è JSON, ma con 'asset' + almeno una tra meshes/scenes/
    nodes. Richiederle evita di rubare i generici file .json alla categoria dati."""
    return (isinstance(obj, dict) and "asset" in obj
            and any(k in obj for k in ("meshes", "scenes", "nodes")))


def detect(path: str | Path) -> DetectedFormat:
    """Rileva il formato di `path` combinando gli strati sopra."""
    path = Path(path)
    if not path.is_file():
        return DetectedFormat(None, None, 0.0, "missing-file")

    head = _read_head(path)

    fmt = _magic(head)
    if fmt:
        # I container ZIP appaiono come 'PK': se è uno ZIP Office, raffina.
        return DetectedFormat(fmt, category_of(fmt), 0.99, "magic")

    if head.startswith(b"PK\x03\x04"):
        member = _zip_member(path)
        if member:
            return DetectedFormat(member, category_of(member), 0.95, "zip")

    fmt = _sniff_text(head)
    # Un JSON "pretty" più grande dello head (4KB) fa fallire il parse parziale in
    # _sniff_text, che ricade su yaml/csv (virgole e ": " abbondano in un JSON). Se
    # il testo inizia con { o [ tentiamo SEMPRE il parse dell'intero file prima di
    # fidarci dello sniff — a prescindere da cosa lo sniff ha proposto.
    # (`head.lstrip()[:1]`, non `head[:1].lstrip()`: quest'ultimo guarda un solo
    # byte e un file che inizia con whitespace non rientrava mai.)
    if head.lstrip()[:1] in (b"{", b"["):
        obj = _full_json(path)
        if obj is not None:
            # Un .gltf è JSON: distinguilo PRIMA di etichettarlo 'json', o il
            # modello 3D finirebbe nella categoria dati.
            if _is_gltf(obj):
                return DetectedFormat("gltf", "model3d", 0.9, "gltf-json")
            return DetectedFormat("json", "data", 0.9, "json-parse")
    if fmt:
        return DetectedFormat(fmt, category_of(fmt), 0.7, "text-sniff")

    # Ultimo indizio: l'estensione, con bassa confidenza.
    ext_fmt = by_extension(path.suffix)
    if ext_fmt:
        return DetectedFormat(ext_fmt, category_of(ext_fmt), 0.3, "extension")

    return DetectedFormat(None, None, 0.0, "unknown")
