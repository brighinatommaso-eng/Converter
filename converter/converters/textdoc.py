"""Converter documenti testuali strutturale: IR unico, lettori e scrittori.

Copre le coppie in cui l'app PERDEVA struttura:
  pdf → docx / odt / rtf / md / txt / html     (pdf→md non esisteva nemmeno)
  docx → md / txt                              (prima pandoc: grid table, titoli
                                                nel front-matter, nessun livello)

Le altre direzioni restano ai motori che le fanno già bene (LibreOffice per
office↔office e docx→html con i colori, pandoc per md/html/txt→office). Il
registry sceglie per priorità, quindi qui si rivendicano SOLO queste coppie.

Perché è un converter e non "un modello AI": un LLM non aggiunge struttura a un
PDF, la INFERISCE in modo non riproducibile. La struttura (titoli, liste,
tabelle, ordine di lettura) si ricostruisce deterministicamente dalla geometria
(pdfminer, MIT) e si riversa su ogni formato dallo stesso IR. Il punto d'innesto
per un'eventuale rifinitura AI resta `docmodel` (Document in → Document out).

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
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.options import BoolOption, ChoiceOption, Option
from ..core.quality import QualityVerdict, lossy, unsupported
from ..core.registry import register
from . import _ocr
from ._libreoffice import DEFAULT_TIMEOUT, run_convert
from .docmodel import read_docx, read_pdf, write_docx, write_html, write_markdown, write_text

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

_LAYOUT_HELP = (
    "Editable structure keeps real Word headings, lists and tables. Visual layout "
    "positions every line exactly where it was printed (best for print-like copies; "
    "text no longer reflows)."
)


@register
class TextDocConverter(Converter):
    category = "document"
    name = "Text documents (structure)"
    input_formats = _INPUTS
    output_formats = _OUTPUTS
    # Batte pdfminer (10) su pdf→txt/html e pandoc (10) sulle coppie che questo
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
        opts = options or {}
        if src == "pdf":
            if dst == "docx" and opts.get("docx_layout") == "visual":
                return lossy(
                    "PDF has no structural text model: each printed line is placed where it "
                    "was printed, so the page looks like the original. Lines are separate "
                    "frames, not real paragraphs/tables — editing or rewrapping will not "
                    "reflow the text.")
            if dst == "docx":
                return lossy(
                    "PDF has no structural text model: headings, paragraphs, lists and tables "
                    "are inferred from the page geometry. Fonts, sizes and colours are carried "
                    "over, so the result is editable and visually close — but absolutely "
                    "positioned elements (text boxes, overlapping graphics) are not reproduced.")
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
                ChoiceOption("docx_layout", "Layout", "structure",
                             [("structure", "Editable structure"),
                              ("visual", "Visual layout (as printed)")],
                             help=_LAYOUT_HELP),
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
            return f"Could not read this PDF: {exc}"
        # Scansione: con la catena OCR presente passa (l'OCR avviene in convert()),
        # senza il rifiuto resta — degradante, mai un documento vuoto.
        return None if _ocr.available() else _ocr.SCANNED_MESSAGE

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        options = job.options or {}
        layout = str(options.get("docx_layout", "structure"))
        page_breaks = bool(options.get("page_breaks", True))

        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            source = job.src_path
            if job.src_format == "pdf":
                progress(ProgressEvent(0.1, "Reading PDF layout…"))
                try:
                    needs_ocr = not _ocr.has_extractable_text(job.src_path)
                except Exception as exc:
                    return ConversionResult(ok=False, error=f"Could not read this PDF: {exc}")
                if needs_ocr:
                    if not _ocr.available():
                        return ConversionResult(ok=False, error=_ocr.SCANNED_MESSAGE)
                    progress(ProgressEvent(0.2, "Running OCR… (this can take a while)"))
                    ocr_pdf = tmp / (job.src_path.stem + ".ocr.pdf")
                    ok, error = _ocr.apply(job.src_path, ocr_pdf)
                    if not ok:
                        return ConversionResult(ok=False, error=error)
                    source = ocr_pdf
                progress(ProgressEvent(0.4, "Reconstructing the document…"))
                try:
                    document = read_pdf(source)
                except ValueError as exc:
                    return ConversionResult(ok=False, error=str(exc))
            else:
                progress(ProgressEvent(0.3, "Reading document…"))
                try:
                    document = read_docx(job.src_path)
                except ValueError as exc:
                    return ConversionResult(ok=False, error=str(exc))

            progress(ProgressEvent(0.75, "Writing " + job.dst_format.upper() + "…"))
            try:
                self._write(document, job, tmp, layout, page_breaks, progress)
            except Exception as exc:  # scrittore: errore pulito, mai stacktrace
                job.output_path.unlink(missing_ok=True)
                return ConversionResult(ok=False,
                                        error=f"Could not write the document: {exc}")

        if not job.output_path.exists():
            return ConversionResult(ok=False, error="No output was produced.")
        size = job.output_path.stat().st_size
        progress(ProgressEvent(1.0, "Done"))
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=self.verdict(job.src_format, job.dst_format, options))

    def _write(self, document, job: ConversionJob, tmp: Path, layout: str,
               page_breaks: bool, progress: ProgressCallback) -> None:
        if job.dst_format == "docx":
            write_docx(document, job.output_path, layout=layout, page_breaks=page_breaks)
            return
        if job.dst_format == "md":
            write_markdown(document, job.output_path)
            return
        if job.dst_format == "txt":
            write_text(document, job.output_path)
            return
        if job.dst_format == "html":
            write_html(document, job.output_path)
            return
        # odt/rtf: docx intermedio + secondo salto LibreOffice (catena interna).
        intermediate = tmp / (job.src_path.stem + ".docx")
        write_docx(document, intermediate, layout=layout, page_breaks=page_breaks)
        progress(ProgressEvent(0.85, "Converting with LibreOffice…"))
        ok, payload = run_convert(intermediate, primary_ext(job.dst_format), tmp,
                                  timeout=DEFAULT_TIMEOUT)
        if not ok:
            raise RuntimeError(payload)
        shutil.move(str(payload), str(job.output_path))
