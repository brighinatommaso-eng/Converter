"""Converter documenti basato su LibreOffice headless (Tier E, fase 1).

Copre i formati "office": docx/odt/rtf/pdf/html/txt. Markdown/EPUB li gestisce il
motore markup in-process (markup.py). La conversione di documenti è onestamente
*best-effort*: passare tra formati ricchi può spostare formattazione e layout, e
importare da PDF è un'approssimazione (il PDF non ha un modello strutturale del
testo). I verdetti lo dicono chiaramente.

Robustezza: l'esecuzione di LibreOffice è blindata in _libreoffice.py (timeout
duro, kill dell'albero, serializzazione, profilo isolato) così la coda non gela.
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
from ..core.quality import QualityVerdict, lossless, lossy, unsupported
from ..core.registry import register
from ._libreoffice import DEFAULT_TIMEOUT, run_convert
from ._libreoffice import warm_up as _warm_up

_FORMATS = frozenset({"docx", "odt", "rtf", "pdf", "html", "txt"})

# LibreOffice importa un PDF come documento Draw: per i formati Writer non esiste
# un filtro di export, quindi pdf→{docx,odt,rtf,txt} fallisce sempre. Solo
# pdf→html (Draw esporta HTML) e pdf→pdf hanno senso. Offrire gli altri sarebbe
# disonesto: li marchiamo non disponibili con un motivo chiaro.
_PDF_WRITER_TARGETS = frozenset({"docx", "odt", "rtf", "txt"})
_PDF_SOURCE_REASON = (
    "LibreOffice imports PDF as a Draw document; no export filter exists to "
    "convert it to a Writer format. Only pdf→html is supported."
)


@register
class DocumentConverter(Converter):
    category = "document"
    name = "Documents (LibreOffice)"
    input_formats = _FORMATS
    output_formats = _FORMATS

    @classmethod
    def available(cls) -> DependencyStatus:
        # In PORTABLE non si invoca MAI LibreOffice: le conversioni office fedeli
        # restano una promessa della modalità FULL. Non è un problema d'installazione,
        # quindi nessun install_hint.
        if engine.get_mode() == "PORTABLE":
            return DependencyStatus(
                False, "Faithful Office conversion requires Full mode (LibreOffice)."
            )
        # find_soffice cerca anche i percorsi d'installazione standard, non solo PATH.
        if deps.find_soffice():
            return DependencyStatus(True)
        return DependencyStatus(False, "LibreOffice was not found.",
                                libreoffice_install_hint())

    def pair_available(self, src: str, dst: str) -> tuple[bool, str]:
        if src == "pdf" and dst in _PDF_WRITER_TARGETS:
            return False, _PDF_SOURCE_REASON
        return True, ""

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src not in _FORMATS or dst not in _FORMATS:
            return unsupported()
        if src == dst:
            return lossless("Same format.")
        if src == "pdf":
            return lossy("PDF stores no structural text model; LibreOffice reconstructs "
                         "the layout approximately — expect imperfect formatting.")
        if dst == "pdf":
            return lossy("Exporting to PDF fixes the layout; the result is no longer "
                         "editable as structured text.")
        if dst == "txt":
            return lossy("Plain text keeps only the words; formatting, images and "
                         "structure are dropped.")
        if src == "txt":
            return lossless("All text is preserved; only default styling is added.")
        return lossy("Converting between rich document formats is best-effort; some "
                     "formatting, fonts or layout may shift.")

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        # Stesso formato: copia byte-a-byte. Il verdict src==dst è lossless
        # "Same format." e la copia lo rende ONESTO — un round-trip LibreOffice
        # invece riscriverebbe/approssimerebbe il file (pdf→pdf ri-importato come
        # Draw, docx→docx round-trip import/export), tradendo il "lossless".
        if job.src_format == job.dst_format:
            progress(ProgressEvent(0.5, "Copying…"))
            shutil.copy(job.src_path, job.output_path)
            size = job.output_path.stat().st_size
            return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                    verdict=self.verdict(job.src_format, job.dst_format))

        progress(ProgressEvent(0.1, "Starting LibreOffice…"))
        dst_ext = primary_ext(job.dst_format)
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            progress(ProgressEvent(0.4, "Converting document…"))
            ok, payload = run_convert(job.src_path, dst_ext, out_dir, timeout=DEFAULT_TIMEOUT)
            if not ok:
                return ConversionResult(ok=False, error=payload)
            progress(ProgressEvent(0.9, "Saving…"))
            # payload è il percorso prodotto nella cartella temporanea: spostalo
            # alla destinazione richiesta prima che la TemporaryDirectory sparisca.
            shutil.move(payload, str(job.output_path))

        size = job.output_path.stat().st_size
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=self.verdict(job.src_format, job.dst_format))


def warm_up_libreoffice() -> None:
    """Esposto per il bootstrap della GUI: scalda LibreOffice fuori dal thread UI."""
    _warm_up()
