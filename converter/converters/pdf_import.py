"""Import PDF verso formati Writer (docx/odt/rtf) via pdf2docx.

LibreOffice importa un PDF come documento Draw e non ha filtri di export verso
i formati Writer: pdf→docx/odt/rtf non è mai stato possibile (DocumentConverter
li blocca con pair_available). pdf2docx ricostruisce il layout via PyMuPDF e
copre il salto pdf→docx; per odt/rtf si concatena INTERNAMENTE docx→odt/rtf con
LibreOffice, perché registry.resolve() non concatena da solo: la catena vive
tutta dentro questo converter.

Full-only per TUTTI i target (anche docx, che tecnicamente non userebbe
LibreOffice): coerenza di modalità prima della disponibilità tecnica — PORTABLE
promette solo i motori interni già garantiti.

I PDF scansionati (senza testo estraibile) passano da OCRmyPDF (Tesseract +
Ghostscript), che aggiunge un layer di testo; il docx viene poi ricostruito dal
TESTO OCR (python-docx), NON con pdf2docx: il layer OCR è invisibile (alpha 0)
e pdf2docx lo scarta — il docx uscirebbe vuoto (verificato empiricamente).
Il ramo OCR è DEGRADANTE: senza la catena installata i
PDF testuali continuano a convertirsi e solo le scansioni vengono rifiutate in
validate(), con l'istruzione d'installazione — mai un docx vuoto "riuscito".
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from ..core import deps, engine
from ..core.base import Converter
from ..core.deps import DependencyStatus, libreoffice_install_hint
from ..core.formats import primary_ext
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.quality import QualityVerdict, lossy, unsupported
from ..core.registry import register
from ._libreoffice import DEFAULT_TIMEOUT, run_convert, run_with_timeout

_OUTPUTS = frozenset({"docx", "odt", "rtf"})

# Sotto questa soglia di caratteri non-whitespace il PDF è considerato una
# scansione: pagine-immagine, nessun testo da ricostruire.
_MIN_TEXT_CHARS = 10

# Per decidere "scansione o no" bastano le prime pagine: estrarre TUTTO il testo di
# un PDF di centinaia di pagine (e per giunta due volte, in validate() e convert())
# è inutile e lento. Un PDF testuale ha testo già nelle prime pagine; una scansione
# non ne ha in nessuna.
_SCAN_CHECK_MAX_PAGES = 5

# Lingue del layer OCR (hardcoded per ora: coprono i documenti dell'utente).
_OCR_LANGS = "eng+ita+fra"

# L'OCR è LENTO (secondi per pagina): timeout largo ma DURO — un PDF ostile non
# deve gelare la coda (stessa disciplina di LibreOffice).
_OCR_TIMEOUT = 600.0

# Anche pdf2docx gira come processo FIGLIO con timeout duro: in-process non è
# interrompibile e un PDF ostile gelerebbe il worker per sempre.
_PDF2DOCX_TIMEOUT = 300.0

_SCANNED_MESSAGE = (
    "This PDF appears to be scanned (no extractable text). Converting it requires "
    "OCR — install Tesseract and Ghostscript, plus the 'ocrmypdf' package "
    "(pip install ocrmypdf)."
)


def _has_extractable_text(src: Path) -> bool:
    """True se il PDF contiene testo estraibile (non è una pura scansione).

    Usa pdfminer, già bundlato per pdf→txt/html: nessuna dipendenza nuova.
    Unico punto che decide "scansione o no": lo usano validate() (gate) e
    convert() (scelta del ramo OCR)."""
    from pdfminer.high_level import extract_text

    text = extract_text(str(src), maxpages=_SCAN_CHECK_MAX_PAGES)
    return len("".join(text.split())) >= _MIN_TEXT_CHARS


def _ocr_available() -> bool:
    """Gate del ramo OCR — DEGRADANTE: se manca un pezzo della catena, l'import
    PDF resta attivo per i PDF testuali; solo le scansioni vengono rifiutate
    (con l'istruzione d'installazione in _SCANNED_MESSAGE)."""
    return (deps.find_ocrmypdf() is not None
            and deps.find_ghostscript() is not None
            and deps.find_tesseract() is not None)


def _ocr_pdf(src: Path, dst: Path) -> tuple[bool, str]:
    """Applica un layer di testo OCR a un PDF scansionato via OCRmyPDF
    (Tesseract + Ghostscript). Ritorna (ok, errore_pulito), mai uno stacktrace.

    Subprocess CLI e non API Python, per lo stesso rigore di LibreOffice:
    run_with_timeout dà env pulito, niente finestra console e timeout duro con
    kill dell'albero — l'OCR in-process non sarebbe interrompibile."""
    exe = deps.find_ocrmypdf()
    if not exe:
        return False, "OCRmyPDF was not found."
    # --skip-text: le pagine che hanno GIÀ testo vengono saltate invece di far
    # fallire tutto (PriorOcrFoundError): copre i PDF misti scansione+testo.
    args = [exe, "-l", _OCR_LANGS, "--skip-text", str(src), str(dst)]
    timed_out, code, out, err = run_with_timeout(args, _OCR_TIMEOUT)
    if timed_out:
        return False, f"OCR timed out after {int(_OCR_TIMEOUT)}s and was stopped."
    if code != 0 or not dst.exists():
        tail = (err or out or "").strip().splitlines()[-4:]
        detail = (" " + " ".join(s.strip() for s in tail)) if tail else ""
        return False, f"OCR failed (exit {code}).{detail}"
    return True, ""


def _pdf_to_docx_inprocess(src: Path, dst: Path) -> tuple[bool, str]:
    """Salto pdf→docx con pdf2docx, IN-PROCESS. Ritorna (ok, errore_pulito).

    È il corpo vero della conversione: lo chiama il processo figlio (converter_app
    `--pdf2docx`) e serve da fallback quando non si riesce a lanciare un figlio."""
    try:
        from pdf2docx import Converter as Pdf2DocxConverter
    except ImportError as exc:
        return False, f"pdf2docx is not available: {exc}"
    try:
        cv = Pdf2DocxConverter(str(src))
        try:
            cv.convert(str(dst))
        finally:
            cv.close()
    except Exception as exc:  # pdf2docx solleva errori vari su PDF ostici
        return False, f"PDF import failed: {exc}"
    if not dst.exists():
        return False, "PDF import produced no output."
    return True, ""


def _pdf2docx_command(src: Path, dst: Path) -> list[str] | None:
    """Comando per eseguire pdf2docx in un processo figlio, o None se non si sa
    come lanciarlo (allora si ripiega in-process). Frozen: l'exe stesso con il
    flag; da sorgente: l'interprete su converter_app.py."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--pdf2docx", str(src), str(dst)]
    try:
        import converter_app
    except Exception:
        return None
    script = getattr(converter_app, "__file__", None)
    if not script:
        return None
    return [sys.executable, str(script), "--pdf2docx", str(src), str(dst)]


def _pdf_to_docx(src: Path, dst: Path) -> tuple[bool, str]:
    """Primo salto pdf→docx: pdf2docx in un PROCESSO FIGLIO killabile
    (run_with_timeout: timeout duro + kill dell'albero + env pulito), così un PDF
    ostile non gela il worker. Se non si riesce a lanciare il figlio, si ripiega
    in-process (comportamento precedente) per non perdere la funzione."""
    cmd = _pdf2docx_command(src, dst)
    if cmd is None:
        return _pdf_to_docx_inprocess(src, dst)

    timed_out, code, out, err = run_with_timeout(cmd, _PDF2DOCX_TIMEOUT)
    if timed_out:
        dst.unlink(missing_ok=True)  # via l'eventuale docx parziale
        return False, f"PDF import timed out after {int(_PDF2DOCX_TIMEOUT)}s and was stopped."
    if code != 0 or not dst.exists():
        dst.unlink(missing_ok=True)
        tail = (err or out or "").strip().splitlines()[-4:]
        detail = (" " + " ".join(s.strip() for s in tail)) if tail else ""
        return False, f"PDF import failed (exit {code}).{detail}"
    return True, ""


def _ocr_pdf_to_docx(src: Path, dst: Path) -> tuple[bool, str]:
    """Docx di SOLO TESTO dalle pagine OCR-izzate. Il layer di OCRmyPDF è
    invisibile (alpha 0) e pdf2docx lo scarta producendo un docx vuoto, quindi
    per le scansioni si ricostruisce dal testo estratto: onesto — il layout di
    una scansione non è comunque recuperabile come struttura."""
    try:
        from docx import Document  # python-docx: già presente (dipendenza pdf2docx)
        from pdfminer.high_level import extract_text
    except ImportError as exc:
        return False, f"python-docx is not available: {exc}"
    try:
        text = extract_text(str(src))
        doc = Document()
        first_page = True
        for page in text.split("\x0c"):  # pdfminer separa le pagine con form feed
            # Blocchi separati da riga vuota = paragrafi; i newline interni si
            # ricompattano (l'OCR spezza le righe come sulla carta).
            paras = [" ".join(block.split()) for block in page.split("\n\n")]
            paras = [p for p in paras if p]
            if not paras:
                continue
            if not first_page:
                doc.add_page_break()
            first_page = False
            for para in paras:
                doc.add_paragraph(para)
        doc.save(str(dst))
    except Exception as exc:
        return False, f"Could not build a document from the OCR text: {exc}"
    if not dst.exists():
        return False, "OCR produced no output document."
    return True, ""


@register
class PdfImportConverter(Converter):
    category = "document"
    name = "PDF import (pdf2docx)"
    input_formats = frozenset({"pdf"})
    output_formats = _OUTPUTS
    # resolve() ignora pair_available: DocumentConverter (priority 0) rivendica
    # comunque pdf→docx/odt/rtf pur bloccandole. Priorità alta per vincerle.
    priority = 10

    @classmethod
    def available(cls) -> DependencyStatus:
        # Full-only per coerenza di modalità: non è un problema d'installazione,
        # quindi nessun install_hint (stessa disciplina di DocumentConverter).
        # NOTA: la catena OCR NON è richiesta qui — è un ramo degradante, il suo
        # gate sta in validate()/convert().
        if engine.get_mode() == "PORTABLE":
            return DependencyStatus(False, "PDF import requires Full mode.")
        if not deps.has_module("pdf2docx"):
            return DependencyStatus(False, "PDF import needs the 'pdf2docx' package.",
                                    "pip install pdf2docx")
        # NOTA: LibreOffice serve SOLO per il secondo salto docx→odt/rtf, quindi il
        # suo gate sta in pair_available() per i soli odt/rtf: pdf→docx (solo
        # pdf2docx) resta disponibile anche senza LibreOffice — degrado granulare.
        return DependencyStatus(True)

    def pair_available(self, src: str, dst: str) -> tuple[bool, str]:
        # pdf→docx usa solo pdf2docx; pdf→odt/rtf concatena docx→odt/rtf via
        # LibreOffice, quindi solo quelle due direzioni richiedono soffice.
        if dst in ("odt", "rtf") and not deps.find_soffice():
            return False, ("Converting PDF to " + dst.upper() + " also needs LibreOffice "
                           "(the docx→" + dst + " step). " + libreoffice_install_hint())
        return True, ""

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src != "pdf" or dst not in _OUTPUTS:
            return unsupported()
        return lossy("PDF has no structural text model; the layout is reconstructed "
                     "and will be imperfect. Best for simple, text-based PDFs — tables, "
                     "columns and spacing may shift. Scanned PDFs are converted via "
                     "OCR into text-only documents — layout is not preserved and "
                     "text accuracy depends on scan quality.")

    def validate(self, job: ConversionJob) -> str | None:
        try:
            if _has_extractable_text(job.src_path):
                return None
        except Exception as exc:
            # PDF malformato/cifrato: pdfminer solleva. Messaggio pulito, mai
            # uno stacktrace (run_job mostra questo testo all'utente).
            return f"Could not read this PDF: {exc}"
        # Scansione. Con la catena OCR presente passa (l'OCR avviene in
        # convert()); senza, il rifiuto resta — degradante, mai un docx vuoto.
        if _ocr_available():
            return None
        return _SCANNED_MESSAGE

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        progress(ProgressEvent(0.05, "Reading PDF…"))
        try:
            needs_ocr = not _has_extractable_text(job.src_path)
        except Exception as exc:
            return ConversionResult(ok=False, error=f"Could not read this PDF: {exc}")

        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            src_pdf = job.src_path
            if needs_ocr:
                # Cintura: validate() lascia passare le scansioni solo con la
                # catena OCR presente; se arriviamo qui senza, errore pulito.
                if not _ocr_available():
                    return ConversionResult(ok=False, error=_SCANNED_MESSAGE)
                progress(ProgressEvent(0.15, "Running OCR… (this can take a while)"))
                ocr_tmp = out_dir / (job.src_path.stem + ".ocr.pdf")
                ok, error = _ocr_pdf(job.src_path, ocr_tmp)
                if not ok:
                    return ConversionResult(ok=False, error=error)
                src_pdf = ocr_tmp

            progress(ProgressEvent(0.45, "Rebuilding document from PDF…"))
            docx_tmp = out_dir / (job.src_path.stem + ".docx")
            # Scansione: docx dal testo OCR (pdf2docx scarterebbe il layer
            # invisibile e produrrebbe un docx vuoto). PDF testuale: pdf2docx.
            if needs_ocr:
                ok, error = _ocr_pdf_to_docx(src_pdf, docx_tmp)
            else:
                ok, error = _pdf_to_docx(src_pdf, docx_tmp)
            if not ok:
                return ConversionResult(ok=False, error=error)

            if job.dst_format == "docx":
                progress(ProgressEvent(0.9, "Saving…"))
                shutil.move(str(docx_tmp), str(job.output_path))
            else:
                # Secondo salto docx→odt/rtf via LibreOffice (runner blindato:
                # timeout duro, kill dell'albero, profilo isolato).
                progress(ProgressEvent(0.7, "Converting with LibreOffice…"))
                ok, payload = run_convert(docx_tmp, primary_ext(job.dst_format),
                                          out_dir, timeout=DEFAULT_TIMEOUT)
                if not ok:
                    return ConversionResult(ok=False, error=payload)
                progress(ProgressEvent(0.9, "Saving…"))
                shutil.move(payload, str(job.output_path))

        size = job.output_path.stat().st_size
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=self.verdict(job.src_format, job.dst_format))
