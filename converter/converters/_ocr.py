"""OCR per PDF scansionati (Tesseract + Ghostscript via OCRmyPDF).

Il PDF che esce da OCRmyPDF ha un layer di testo *invisibile*: pdfminer lo legge
normalmente, quindi il lettore strutturale ricostruisce il documento esattamente
come farebbe con un PDF nativo. Per questo l'OCR è solo un PREPROCESSING: il
docx/md/txt non hanno più un ramo "solo testo" separato.

Degradante per scelta: senza la catena installata i PDF testuali si convertono
comunque; solo le scansioni vengono rifiutate, con l'istruzione d'installazione —
mai un documento vuoto che sembra riuscito.
"""

from __future__ import annotations

from pathlib import Path

from ..core import deps
from ._libreoffice import run_with_timeout

# Lingue del layer OCR (hardcoded: coprono i documenti dell'utente).
LANGS = "eng+ita+fra"

# L'OCR è LENTO (secondi per pagina): timeout largo ma DURO — un PDF ostile non
# deve gelare la coda (stessa disciplina di LibreOffice).
TIMEOUT = 600.0

# Sotto questa soglia di caratteri non-whitespace nelle prime pagine, SE le pagine
# contengono immagini, il PDF è considerato una scansione (pagine-immagine).
MIN_TEXT_CHARS = 10
SCAN_CHECK_MAX_PAGES = 5

SCANNED_MESSAGE = (
    "This PDF appears to be scanned (no extractable text). Converting it requires "
    "OCR — install Tesseract and Ghostscript, plus the 'ocrmypdf' package "
    "(pip install ocrmypdf)."
)


def available() -> bool:
    """Catena OCR completa presente? (gate del ramo, non del converter)."""
    return (deps.find_ocrmypdf() is not None
            and deps.find_ghostscript() is not None
            and deps.find_tesseract() is not None)


def _has_page_images(src: Path) -> bool:
    """True se le prime pagine contengono immagini (il segno di una scansione)."""
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTImage

    def walk(node) -> bool:
        if isinstance(node, LTImage):
            return True
        try:
            children = list(node)
        except TypeError:
            return False
        return any(walk(child) for child in children)

    return any(walk(page) for page in extract_pages(str(src), maxpages=SCAN_CHECK_MAX_PAGES))


def has_extractable_text(src: Path) -> bool:
    """True se il PDF va letto come testo (non è una pura scansione).

    Basta guardare le prime pagine: un PDF testuale ha testo già lì, una
    scansione non ne ha in nessuna, ed estrarre centinaia di pagine due volte
    (validate + convert) sarebbe inutile lentezza.

    Poco testo NON basta a dire "scansione": una pagina con la sola parola
    "Invoice" è un PDF testuale vero (prima veniva rifiutato chiedendo l'OCR).
    È una scansione solo se, oltre al testo quasi assente, le pagine contengono
    immagini; senza immagini il testo che c'è è tutto il contenuto.
    """
    from pdfminer.high_level import extract_text

    text = extract_text(str(src), maxpages=SCAN_CHECK_MAX_PAGES)
    if len("".join(text.split())) >= MIN_TEXT_CHARS:
        return True
    return not _has_page_images(src)


def apply(src: Path, dst: Path) -> tuple[bool, str]:
    """Aggiunge un layer di testo al PDF scansito. Ritorna (ok, errore_pulito).

    Subprocess CLI e non API Python, per lo stesso rigore di LibreOffice:
    run_with_timeout dà env pulito, niente finestra console e timeout duro con
    kill dell'albero — l'OCR in-process non sarebbe interrompibile.
    """
    exe = deps.find_ocrmypdf()
    if not exe:
        return False, "OCRmyPDF was not found."
    # --skip-text: le pagine che hanno GIÀ testo vengono saltate invece di far
    # fallire tutto (PriorOcrFoundError): copre i PDF misti scansione+testo.
    args = [exe, "-l", LANGS, "--skip-text", str(src), str(dst)]
    timed_out, code, out, err = run_with_timeout(args, TIMEOUT)
    if timed_out:
        return False, f"OCR timed out after {int(TIMEOUT)}s and was stopped."
    if code != 0 or not dst.exists():
        tail = (err or out or "").strip().splitlines()[-4:]
        detail = (" " + " ".join(s.strip() for s in tail)) if tail else ""
        return False, f"OCR failed (exit {code}).{detail}"
    return True, ""
