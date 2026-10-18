"""Converter di markup/documenti basato su pandoc (binario, bundlato in Fase 4).

Copre i formati "markup" — md, html, epub, txt — e le conversioni verso/da i
formati office quando ha senso, SENZA pretendere fedeltà dove non è realistica:

  A) sorgente markup leggibile da pandoc (md/html/epub/txt) → qualunque target
     pandoc (md/html/epub/txt/rtf/docx/odt). Es. md→docx, html→epub, md→rtf.
  B) sorgente office (docx/odt) → SOLO i target che sacrificano il layout
     (md/txt). Es. docx→md. Qui pandoc batte LibreOffice (contenuto più
     pulito) e dà un percorso PORTABLE; docx/odt→html invece resta a
     LibreOffice anche in FULL (preserva colori/sfondi via CSS inline, che
     pandoc scarta) — la fedeltà office↔office resta comunque a LibreOffice.

Escluso di proposito:
  - qualunque →pdf  (serve un motore LaTeX che non bundliamo: il PDF resta FULL);
  - office↔office docx↔odt  (riservato a LibreOffice, fedele, solo FULL);
  - rtf come SORGENTE  (pandoc non legge l'RTF).

Disponibile in ENTRAMBE le modalità: pandoc non è LibreOffice. Ogni chiamata passa
da clean_subprocess_env() come gli altri tool esterni (anti-contaminazione PyInstaller).
"""

from __future__ import annotations

import subprocess

from ..core import deps
from ..core.base import Converter
from ..core.deps import DependencyStatus, pandoc_install_hint
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.quality import QualityVerdict, lossless, lossy, unsupported
from ..core.registry import register

# Nessuna finestra di console quando si lancia pandoc su Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_TIMEOUT = 120

# Nomi formato lato pandoc. Il txt si legge come markdown e si scrive come "plain".
_IN_FMT = {"md": "markdown", "html": "html", "epub": "epub", "txt": "markdown",
           "docx": "docx", "odt": "odt"}
_OUT_FMT = {"md": "markdown", "html": "html", "epub": "epub", "txt": "plain",
            "rtf": "rtf", "docx": "docx", "odt": "odt"}

_MARKUP_SRC = frozenset({"md", "html", "epub", "txt"})   # sorgenti leggibili (no rtf)
_ANY_OUT = frozenset(_OUT_FMT)                            # tutti i target pandoc
_OFFICE_SRC = frozenset({"docx", "odt"})                 # office come sorgente
_OFFICE_OVERRIDE_OUT = frozenset({"md", "txt"})          # solo target che sacrificano layout


@register
class MarkupConverter(Converter):
    category = "document"
    name = "Markup (pandoc)"
    input_formats = frozenset(_IN_FMT)
    output_formats = frozenset(_OUT_FMT)
    priority = 10  # vince su LibreOffice per le coppie markup/override

    @classmethod
    def available(cls) -> DependencyStatus:
        # pandoc è bundlato (Fase 4) o su PATH: disponibile in FULL e PORTABLE.
        if deps.find_pandoc():
            return DependencyStatus(True)
        return DependencyStatus(False, "Pandoc was not found.", pandoc_install_hint())

    def _supported(self, src: str, dst: str) -> bool:
        if src == dst:
            return False
        if src in _MARKUP_SRC and dst in _ANY_OUT:          # A
            return True
        if src in _OFFICE_SRC and dst in _OFFICE_OVERRIDE_OUT:  # B
            return True
        return False

    def pair_available(self, src: str, dst: str) -> tuple[bool, str]:
        # Le coppie non gestite non vanno offerte come "pandoc": restano ad altri
        # converter o non supportate. (verdict() le marca già unsupported.)
        return (True, "") if self._supported(src, dst) else (False, "")

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if not self._supported(src, dst):
            return unsupported()
        if dst == "txt":
            return lossy("Plain text keeps only the words; formatting, images and "
                         "structure are dropped.")
        if src == "txt":
            return lossless("Plain text is valid Markdown; every character is preserved.")
        if src == "md" and dst == "html":
            return lossless("Markdown maps cleanly to HTML; text and structure are preserved.")
        return lossy("pandoc converts between markup and document formats best-effort; "
                     "some formatting or layout may shift.")

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        progress(ProgressEvent(0.1, "Starting pandoc…"))
        pandoc = deps.find_pandoc()
        if not pandoc:
            return ConversionResult(ok=False, error="Pandoc was not found.")
        fmt_in = _IN_FMT.get(job.src_format)
        fmt_out = _OUT_FMT.get(job.dst_format)
        if not fmt_in or not fmt_out:
            return ConversionResult(
                ok=False, error=f"pandoc cannot convert {job.src_format}→{job.dst_format}.")

        cmd = [pandoc, "-f", fmt_in, "-t", fmt_out, "-s",
               "-o", str(job.output_path), str(job.src_path)]
        progress(ProgressEvent(0.4, "Converting with pandoc…"))
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT,
                                  creationflags=_NO_WINDOW, env=deps.clean_subprocess_env())
        except subprocess.TimeoutExpired:
            job.output_path.unlink(missing_ok=True)  # -o killato a metà: via il parziale
            return ConversionResult(ok=False, error="Pandoc timed out.")
        except OSError as exc:
            return ConversionResult(ok=False, error=f"Could not run pandoc: {exc}")

        if proc.returncode != 0:
            job.output_path.unlink(missing_ok=True)
            return ConversionResult(ok=False, error=(proc.stderr or "pandoc failed").strip())
        if not job.output_path.exists():
            return ConversionResult(ok=False, error="Pandoc produced no output file.")
        progress(ProgressEvent(1.0, "Done"))
        return ConversionResult(ok=True, output_path=job.output_path,
                                output_size=job.output_path.stat().st_size,
                                verdict=self.verdict(job.src_format, job.dst_format))
