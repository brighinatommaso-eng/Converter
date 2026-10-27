"""Converter dei formati markup: md / html / epub / odt / txt → md, html, epub, txt,
rtf, docx, odt. In-process (converters/markupdoc), disponibile anche in PORTABLE.

Prima qui si lanciava pandoc (GPL-2, ~200 MB nel bundle). Adesso un lettore per
formato costruisce un albero HTML ripulito e uno scrittore per formato lo
percorre: nessun processo esterno, nessuna rete (le immagini remote restano link
o vengono dichiarate come non incorporate).

Coppie: le sorgenti markup (md, html, epub, txt) verso tutti i target; l'odt solo
verso md/txt, come prima: odt→docx/html/rtf/pdf restano a LibreOffice (FULL), che
conserva il layout. txt→md resta a plaintext.py (escape riga per riga).
"""

from __future__ import annotations

from ..core import deps
from ..core.base import Converter
from ..core.deps import DependencyStatus
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent, cancelled_result
from ..core.quality import QualityVerdict, lossless, lossy, unsupported
from ..core.registry import register

_MARKUP_SRC = frozenset({"md", "html", "epub", "txt"})
_ALL_OUT = frozenset({"md", "html", "epub", "txt", "rtf", "docx", "odt"})
_ODT_OUT = frozenset({"md", "txt"})

# Moduli che servono a ciascun formato (tutti MIT/BSD/Apache, in-process).
_MODULES = {"md": ("markdown_it",), "docx": ("docx",), "odt": ("odf", "defusedxml"),
            "epub": ("defusedxml",)}


def _modules_ok(fmt: str) -> tuple[bool, str]:
    missing = [name for name in _MODULES.get(fmt, ()) if not deps.has_module(name)]
    if missing:
        return False, f"Missing Python module(s): {', '.join(missing)}."
    return True, ""


_SOURCE_LOSS = {
    "html": "CSS, scripts, forms and page layout are dropped; text, headings, lists, tables, "
            "links and images are kept.",
    "epub": "The book's styling and layout are dropped; text, headings, lists, tables, links "
            "and images are kept (all chapters in one document).",
    "odt": "Page layout, fonts, colours and fields are dropped; text, headings, lists, tables, "
           "links and emphasis are kept, footnotes move to the end.",
}
_TARGET_NOTE = {
    "docx": "Headings, emphasis, lists, tables, links, code and local images become native Word "
            "styles; exact spacing and code-block languages are not kept.",
    "odt": "Headings, emphasis, lists, tables, links, code and local images become native "
           "OpenDocument styles; exact spacing and code-block languages are not kept.",
    "rtf": "Headings and emphasis are kept; lists become text markers and some table "
           "layout is approximate.",
    "epub": "The document is split into chapters at each top-level heading.",
    "md": "Converted to GitHub-flavoured Markdown; merged table cells and anything "
          "Markdown cannot express are simplified.",
    "html": "",
}


@register
class MarkupConverter(Converter):
    category = "document"
    name = "Markup documents"
    input_formats = frozenset(_MARKUP_SRC | {"odt"})
    output_formats = _ALL_OUT
    priority = 12  # batte LibreOffice sulle coppie markup (e il vecchio pandoc a 10)

    @classmethod
    def available(cls) -> DependencyStatus:
        return DependencyStatus(True)

    def _supported(self, src: str, dst: str) -> bool:
        if src == dst or dst not in _ALL_OUT:
            return False
        if src == "txt" and dst == "md":
            return False                       # plaintext.py: escape riga per riga
        if src in _MARKUP_SRC:
            return True
        return src == "odt" and dst in _ODT_OUT

    def format_available(self, format_id: str) -> tuple[bool, str]:
        return _modules_ok(format_id)

    def pair_available(self, src: str, dst: str) -> tuple[bool, str]:
        return (True, "") if self._supported(src, dst) else (False, "")

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if not self._supported(src, dst):
            return unsupported()
        if dst == "txt":
            return lossy("Plain text keeps only the words; formatting, links, images and "
                         "table structure are dropped.")
        if src == "txt":
            return lossless("Every character, line break and space of the text is kept.")
        if src == "md" and dst == "html":
            return lossless("Rendered with the CommonMark/GitHub reference rules: text, "
                            "structure, links and local images (embedded) are kept.")
        if src == "md" and dst == "epub":
            return lossy("Rendered with the CommonMark/GitHub rules; " + _TARGET_NOTE["epub"].lower()
                         + " Remote images are not downloaded.")
        reason = _SOURCE_LOSS.get(src, "")
        target = _TARGET_NOTE.get(dst, "")
        return lossy(" ".join(part for part in (reason, target) if part) or
                     "Converted best-effort; some formatting may change.")

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        from .markupdoc import READERS, WRITERS, ArchiveError

        reader = READERS.get(job.src_format)
        writer = WRITERS.get(job.dst_format)
        if reader is None or writer is None or not self._supported(job.src_format, job.dst_format):
            return ConversionResult(ok=False, error=f"Cannot convert {job.src_format}→{job.dst_format}.")
        progress(ProgressEvent(0.15, "Reading…"))
        try:
            document = reader(job.src_path)
        except ArchiveError as exc:
            return ConversionResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001  file malformato: errore leggibile, mai crash
            return ConversionResult(ok=False, error=f"Could not read the {job.src_format.upper()} "
                                                    f"file: {exc}")
        if job.cancel.cancelled:
            return cancelled_result()
        if not any(node.tag != "body" for node in document.body.iter()) and \
                not document.body.text().strip():
            return ConversionResult(ok=False, error="The document has no readable content.")
        progress(ProgressEvent(0.6, "Writing…"))
        try:
            report = writer(document, job.output_path)
        except Exception as exc:  # noqa: BLE001
            job.output_path.unlink(missing_ok=True)    # niente file a metà accanto all'errore
            return ConversionResult(ok=False, error=f"Could not write the {job.dst_format.upper()} "
                                                    f"file: {exc}")
        if job.cancel.cancelled:
            job.output_path.unlink(missing_ok=True)
            return cancelled_result()
        verdict = self.verdict(job.src_format, job.dst_format)
        notes = report.all_notes(document)
        if notes:
            # Onestà a runtime: ciò che questo file ha perso davvero.
            verdict = lossy(" ".join([verdict.reason, *notes]).strip())
        progress(ProgressEvent(1.0, "Done"))
        return ConversionResult(ok=True, output_path=job.output_path,
                                output_size=job.output_path.stat().st_size, verdict=verdict)
