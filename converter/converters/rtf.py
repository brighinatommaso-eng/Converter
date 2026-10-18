"""Converter rtf→txt basato su striprtf (pure-Python, MIT).

Senza questo, in PORTABLE un file .rtf sarebbe un vicolo cieco: rtf→html/txt sono
solo FULL (LibreOffice) e pandoc non legge l'RTF. striprtf dà almeno rtf→txt in
ENTRAMBE le modalità — onesto (il formato si perde, resta il testo) e coerente con
la regola "per i target che sacrificano il layout vince il motore puro".
"""

from __future__ import annotations

from ..core import deps
from ..core.base import Converter
from ..core.deps import DependencyStatus
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.quality import QualityVerdict, lossy, unsupported
from ..core.registry import register


@register
class RtfTextConverter(Converter):
    category = "document"
    name = "RTF text (striprtf)"
    input_formats = frozenset({"rtf"})
    output_formats = frozenset({"txt"})
    priority = 10  # vince su LibreOffice per rtf→txt (disponibile anche in PORTABLE)

    @classmethod
    def available(cls) -> DependencyStatus:
        if deps.has_module("striprtf"):
            return DependencyStatus(True)
        return DependencyStatus(False, "RTF text extraction needs the 'striprtf' package.",
                                "pip install striprtf")

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src != "rtf" or dst != "txt":
            return unsupported()
        return lossy("RTF formatting is dropped; only the plain text is kept.")

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        try:
            from striprtf.striprtf import rtf_to_text
        except ImportError as exc:
            return ConversionResult(ok=False, error=f"striprtf is not available: {exc}")

        progress(ProgressEvent(0.2, "Reading RTF…"))
        try:
            raw = job.src_path.read_text(encoding="utf-8", errors="replace")
            job.output_path.write_text(rtf_to_text(raw), encoding="utf-8")
        except Exception as exc:
            return ConversionResult(ok=False, error=f"RTF extraction failed: {exc}")

        progress(ProgressEvent(1.0, "Done"))
        return ConversionResult(ok=True, output_path=job.output_path,
                                output_size=job.output_path.stat().st_size,
                                verdict=self.verdict(job.src_format, job.dst_format))
