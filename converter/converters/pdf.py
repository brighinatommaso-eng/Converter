"""Converter PDF basato su pdfminer.six (pure-Python, MIT): ripiego per pdf→txt/html.

Le coppie pdf→txt/html sono rivendicate a priorità più alta da `textdoc.py`
(motore strutturale: titoli, elenchi, tabelle). Questo converter vince solo se
il motore strutturale non è disponibile (python-docx assente): estrae il testo
e una resa HTML semplice, senza struttura. Funziona in ENTRAMBE le modalità.
"""

from __future__ import annotations

from ..core import deps
from ..core.base import Converter
from ..core.deps import DependencyStatus
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.quality import QualityVerdict, lossy, unsupported
from ..core.registry import register

_OUTPUTS = frozenset({"txt", "html"})


@register
class PdfConverter(Converter):
    category = "document"
    name = "PDF text/HTML (pdfminer)"
    input_formats = frozenset({"pdf"})
    output_formats = _OUTPUTS
    priority = 10  # sotto textdoc (20), sopra LibreOffice: ripiego senza python-docx

    @classmethod
    def available(cls) -> DependencyStatus:
        # pdfminer è pure-Python e bundlato: disponibile in FULL e in PORTABLE.
        if deps.has_module("pdfminer"):
            return DependencyStatus(True)
        return DependencyStatus(False, "PDF text extraction needs the 'pdfminer.six' package.",
                                "pip install pdfminer.six")

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src != "pdf" or dst not in _OUTPUTS:
            return unsupported()
        if dst == "txt":
            return lossy("A PDF has no structural text model; extraction keeps the words "
                         "but drops layout, images and exact positioning.")
        return lossy("HTML is reconstructed from the PDF's text runs — structural, not a "
                     "pixel-faithful render; images and exact layout are not preserved.")

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        # Import locale: pdfminer è opzionale e potrebbe non essere installato.
        try:
            from pdfminer.high_level import extract_text, extract_text_to_fp
            from pdfminer.layout import LAParams
        except ImportError as exc:
            return ConversionResult(ok=False, error=f"pdfminer.six is not available: {exc}")

        progress(ProgressEvent(0.2, "Reading PDF…"))
        try:
            if job.dst_format == "txt":
                text = extract_text(str(job.src_path))
                job.output_path.write_text(text, encoding="utf-8")
            else:  # html
                with open(job.src_path, "rb") as inp, open(job.output_path, "wb") as out:
                    extract_text_to_fp(inp, out, output_type="html",
                                       laparams=LAParams(), codec="utf-8")
        except Exception as exc:  # pdfminer può sollevare vari errori su PDF malformati
            job.output_path.unlink(missing_ok=True)  # via l'output scritto a metà
            return ConversionResult(ok=False, error=f"PDF extraction failed: {exc}")

        if not job.output_path.exists():
            return ConversionResult(ok=False, error="No output was produced from the PDF.")
        progress(ProgressEvent(1.0, "Done"))
        return ConversionResult(ok=True, output_path=job.output_path,
                                output_size=job.output_path.stat().st_size,
                                verdict=self.verdict(job.src_format, job.dst_format))
