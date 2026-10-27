"""Converter documenti testuali strutturale: IR unico, lettori e scrittori.

Copre le coppie in cui l'app PERDEVA struttura:
  pdf → docx / odt / rtf / md / txt / html     (pdf→md non esisteva nemmeno)
  docx → md / txt                              (prima pandoc: grid table, titoli
                                                nel front-matter, nessun livello)

Le altre direzioni restano ai motori che le fanno già bene (LibreOffice per
office↔office e docx→html con i colori, il motore markup per md/html/txt→office). Il
registry sceglie per priorità, quindi qui si rivendicano SOLO queste coppie.

AI locale, non generativa: il testo viene SEMPRE dal PDF (pdfminer, MIT); un
modello di rilevamento del layout (PP-DocLayoutV2, Apache-2.0, offline via
onnxruntime) guarda la pagina renderizzata e dice dove sono titoli, paragrafi,
tabelle, figure e in che ordine si leggono (`docmodel.layout_ai`). Non inventa né
riscrive parole, e a parità di file dà sempre lo stesso risultato. Senza modello
(o su una pagina che non sa analizzare) resta il motore geometrico, e il verdetto
lo dice.

Licenza: niente PyMuPDF/pdf2docx (AGPL, esclusi dal bundle). python-docx è MIT e
diventa una dipendenza diretta: è lo scrittore/lettore docx del motore.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from ..core import deps, engine
from ..core.base import Converter
from ..core.deps import DependencyStatus, libreoffice_install_hint
from ..core.formats import primary_ext
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent, cancelled_result
from ..core.options import BoolOption, Option
from ..core.quality import QualityVerdict, lossy, unsupported
from ..core.registry import register
from . import _ocr
from ._libreoffice import DEFAULT_TIMEOUT, run_convert
from .docmodel import PageBreak, read_docx, read_pdf, write_docx, write_html, write_markdown, write_text
from .docmodel.pdfreader import ReadCancelled
from .docmodel.writers import WriteReport

# Target di questo converter. `pdf` come target resta a LibreOffice (export fedele
# dal documento strutturato), non si rigenera dall'IR.
_OUTPUTS = frozenset({"docx", "odt", "rtf", "md", "txt", "html"})

# Sorgenti: il PDF è il caso in cui si perdeva tutto; docx→md/txt lo gestiamo noi
# perché la resa pandoc (grid table, titoli nel front-matter) è peggiore.
_INPUTS = frozenset({"pdf", "docx"})

_PDF_OUTPUTS = _OUTPUTS
_DOCX_OVERRIDE_OUT = frozenset({"md", "txt"})

# odt/rtf non hanno uno scrittore proprio: si scrive docx (l'IR ci sa arrivare) e
# si concatena INTERNAMENTE docx→odt/rtf con LibreOffice, come faceva l'import
# PDF precedente (registry.resolve non concatena da solo).
_SECOND_HOP = frozenset({"odt", "rtf"})

def _pdf_error(exc: Exception) -> str:
    """Messaggio leggibile per un PDF che pdfminer rifiuta.

    L'eccezione della password di pdfminer non ha testo: il messaggio era
    "Could not read this PDF: " seguito dal nulla."""
    from pdfminer.pdfdocument import PDFEncryptionError, PDFPasswordIncorrect

    if isinstance(exc, (PDFPasswordIncorrect, PDFEncryptionError)):
        return "Could not read this PDF: it is password-protected."
    detail = str(exc).strip() or type(exc).__name__
    return f"Could not read this PDF: {detail}"


@register
class TextDocConverter(Converter):
    category = "document"
    name = "Text documents (structure)"
    input_formats = _INPUTS
    output_formats = _OUTPUTS
    # Batte pdfminer (10) su pdf→txt/html e il motore markup (12) sulle coppie che questo
    # motore ricostruisce meglio.
    priority = 20

    @classmethod
    def available(cls) -> DependencyStatus:
        # Tutto in-process e MIT: niente modalità FULL, funziona anche PORTABLE
        # (come image.py). LibreOffice serve solo per il secondo salto odt/rtf e
        # il suo gate sta in pair_available().
        if not deps.has_module("pdfminer"):
            return DependencyStatus(False, "Structured document import needs 'pdfminer.six'.",
                                    "pip install pdfminer.six")
        if not deps.has_module("docx"):
            return DependencyStatus(False, "Structured document export needs 'python-docx'.",
                                    "pip install python-docx")
        return DependencyStatus(True)

    def _supported(self, src: str, dst: str) -> bool:
        if src == "pdf" and dst in _PDF_OUTPUTS:
            return True
        return src == "docx" and dst in _DOCX_OVERRIDE_OUT

    def pair_available(self, src: str, dst: str) -> tuple[bool, str]:
        if not self._supported(src, dst):
            return False, ""
        if dst in _SECOND_HOP:
            # Stessa disciplina dell'import PDF precedente: il secondo salto è di
            # LibreOffice, quindi FULL + soffice presente.
            if engine.get_mode() == "PORTABLE":
                return False, (f"Converting PDF to {dst.upper()} needs Full mode: the "
                               f"docx→{dst} step runs in LibreOffice.")
            if not deps.find_soffice():
                return False, (f"Converting to {dst.upper()} needs LibreOffice (the "
                               f"docx→{dst} step). " + libreoffice_install_hint())
        return True, ""

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if not self._supported(src, dst):
            return unsupported()
        if src == "pdf":
            if dst == "docx":
                return lossy(
                    "PDF has no structural text model: headings, paragraphs, lists, tables and "
                    "figures are detected by a local AI layout model and the page geometry (the "
                    "words themselves come from the PDF). The Word file is both editable and laid "
                    "out like the original: one section per page with its size and margins, "
                    "real columns, measured spacing, fonts, colours, shaded boxes, headers and "
                    "footers. Lines may wrap differently where the original font is not installed, "
                    "and overlapping graphics or text drawn over images are approximated.")
            if dst == "md":
                return lossy(
                    "Markdown keeps the text, the heading hierarchy, the lists and the tables "
                    "as GFM; images are embedded, but page layout, fonts and colours do not "
                    "exist in Markdown.")
            if dst == "txt":
                return lossy(
                    "Plain text keeps the words in reading order, list bullets and "
                    "tab-separated table rows; all formatting and images are dropped by "
                    "definition.")
            if dst == "html":
                return lossy(
                    "HTML is rebuilt semantically (h1–h6, lists, tables, embedded images) with "
                    "inline fonts/colours; page positioning is not reproduced.")
            return lossy(
                f"PDF has no structural text model: the document is reconstructed and exported "
                f"to {dst.upper()} through LibreOffice. Layout is approximated.")
        # docx come sorgente
        if dst == "md":
            return lossy(
                "Markdown keeps the text, heading levels, lists and tables; Word page layout, "
                "fonts, footnotes and images-as-fields do not exist in Markdown.")
        return lossy(
            "Plain text keeps the words, list bullets and tab-separated table rows; all "
            "formatting and images are dropped by definition.")

    def options_schema(self, src: str, dst: str) -> list[Option]:
        if src == "pdf" and dst == "docx":
            return [
                BoolOption("page_breaks", "Keep page breaks", True,
                           help="Insert a page break where the original PDF had one."),
            ]
        return []

    def validate(self, job: ConversionJob) -> str | None:
        if job.src_format != "pdf":
            return None
        try:
            if _ocr.has_extractable_text(job.src_path):
                return None
        except Exception as exc:
            # PDF malformato/cifrato: pdfminer solleva. Messaggio pulito, mai
            # uno stacktrace (run_job mostra questo testo all'utente).
            return _pdf_error(exc)
        # Scansione: con la catena OCR presente passa (l'OCR avviene in convert()),
        # senza il rifiuto resta — degradante, mai un documento vuoto.
        return None if _ocr.available() else _ocr.SCANNED_MESSAGE

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        options = job.options or {}
        page_breaks = bool(options.get("page_breaks", True))

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            source = job.src_path
            if job.src_format == "pdf":
                progress(ProgressEvent(0.1, "Reading PDF layout…"))
                try:
                    needs_ocr = not _ocr.has_extractable_text(job.src_path)
                except Exception as exc:
                    return ConversionResult(ok=False, error=_pdf_error(exc))
                if job.cancel.cancelled:
                    return cancelled_result()
                if needs_ocr:
                    if not _ocr.available():
                        return ConversionResult(ok=False, error=_ocr.SCANNED_MESSAGE)
                    progress(ProgressEvent(0.2, "Running OCR… (this can take a while)"))
                    ocr_pdf = tmp / (job.src_path.stem + ".ocr.pdf")
                    ok, error = _ocr.apply(job.src_path, ocr_pdf)
                    if not ok:
                        return ConversionResult(ok=False, error=error)
                    source = ocr_pdf
                    if job.cancel.cancelled:
                        return cancelled_result()
                progress(ProgressEvent(0.4, "Reconstructing the document…"))

                def page_done(done: int, total: int) -> None:
                    progress(ProgressEvent(0.4 + 0.35 * done / max(total, 1),
                                           f"Analysing page {done} of {total}…"))

                try:
                    document = read_pdf(source, progress=page_done,
                                        cancel=lambda: job.cancel.cancelled)
                except ReadCancelled:
                    return cancelled_result()
                except ValueError as exc:
                    return ConversionResult(ok=False, error=str(exc))
                if not any(not isinstance(block, PageBreak) for block in document.blocks):
                    # Mai un documento vuoto che sembra riuscito.
                    return ConversionResult(
                        ok=False, error="This PDF contains no text or images to convert.")
            else:
                progress(ProgressEvent(0.3, "Reading document…"))
                try:
                    document = read_docx(job.src_path)
                except ValueError as exc:
                    return ConversionResult(ok=False, error=str(exc))

            # Annullamento cooperativo tra le fasi: la lettura pdfminer non si può
            # interrompere a metà, ma il lavoro successivo non parte.
            if job.cancel.cancelled:
                return cancelled_result()
            progress(ProgressEvent(0.75, "Writing " + job.dst_format.upper() + "…"))
            try:
                report = self._write(document, job, tmp, page_breaks, progress)
            except Exception as exc:  # scrittore: errore pulito, mai stacktrace
                job.output_path.unlink(missing_ok=True)
                return ConversionResult(ok=False,
                                        error=f"Could not write the document: {exc}")

        if not job.output_path.exists():
            return ConversionResult(ok=False, error="No output was produced.")
        size = job.output_path.stat().st_size
        progress(ProgressEvent(1.0, "Done"))
        verdict = self.verdict(job.src_format, job.dst_format, options)
        if job.src_format == "pdf":
            verdict = _layout_note(verdict, document)
        if document.skipped_pages:
            # PDF danneggiato: il resto è stato letto, ma quelle pagine mancano.
            verdict = lossy(f"{verdict.reason} {document.skipped_pages} damaged page(s) could "
                            f"not be read and are missing from the output.")
        if document.undecoded_chars:
            verdict = lossy(f"{verdict.reason} {document.undecoded_chars} character(s) use a "
                            f"font without a Unicode mapping and are shown as �.")
        if report.dropped_images:
            # Onestà a runtime: un'immagine in un formato che il target non sa
            # mostrare (EMF/WMF, JBIG2…) e che Pillow non riesce a ricodificare.
            verdict = lossy(f"{verdict.reason} {report.dropped_images} image(s) in a format "
                            f"{job.dst_format.upper()} cannot show were left out.")
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=verdict)

    def _write(self, document, job: ConversionJob, tmp: Path,
               page_breaks: bool, progress: ProgressCallback) -> WriteReport:
        if job.dst_format == "docx":
            return write_docx(document, job.output_path, page_breaks=page_breaks)
        if job.dst_format == "md":
            return write_markdown(document, job.output_path)
        if job.dst_format == "txt":
            return write_text(document, job.output_path)
        if job.dst_format == "html":
            return write_html(document, job.output_path)
        # odt/rtf: docx intermedio + secondo salto LibreOffice (catena interna).
        intermediate = tmp / (job.src_path.stem + ".docx")
        report = write_docx(document, intermediate, page_breaks=page_breaks)
        progress(ProgressEvent(0.85, "Converting with LibreOffice…"))
        ok, payload = run_convert(intermediate, primary_ext(job.dst_format), tmp,
                                  timeout=DEFAULT_TIMEOUT)
        if not ok:
            raise RuntimeError(payload)
        shutil.move(str(payload), str(job.output_path))
        return report


def _layout_note(verdict: QualityVerdict, document) -> QualityVerdict:
    """Dice nel verdetto se e dove il layout NON è stato analizzato dal modello AI."""
    if document.ai_unavailable:
        return lossy(f"{verdict.reason} AI layout analysis was not used "
                     f"({document.ai_unavailable}): the layout was rebuilt from the page "
                     f"geometry only, which is less accurate on columns, tables and figures.")
    if document.heuristic_pages:
        return lossy(f"{verdict.reason} {document.heuristic_pages} page(s) could not be "
                     f"analysed by the AI layout model (e.g. rotated pages) and were rebuilt "
                     f"from the page geometry only.")
    return verdict

