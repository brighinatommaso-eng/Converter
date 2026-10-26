"""Converter txt→md in-process (nessun tool esterno, disponibile anche PORTABLE).

Prima txt→md passava da pandoc leggendo il testo COME markdown e riscrivendolo:
le righe venivano rifuse in un solo paragrafo, `*parola*` diventava enfasi e
`# riga` un titolo — sotto un badge LOSSLESS "every character is preserved".

Qui il testo resta testo: ogni carattere che Markdown interpreterebbe viene
"escapato", gli a capo diventano a capo forzati e il rientro iniziale resta
visibile. Il Markdown renderizzato mostra esattamente il testo d'origine.
"""

from __future__ import annotations

import re

from ..core.base import Converter
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.quality import QualityVerdict, lossless, unsupported
from ..core.registry import register

# Caratteri che Markdown (CommonMark/GFM) interpreta OVUNQUE nella riga.
_INLINE_SPECIAL = re.compile(r"([\\`*_\[\]<>|~&])")
# Costrutti di blocco che contano solo a inizio riga (dopo il rientro).
_BLOCK_START = re.compile(r"^([#+\-=>])")
_ORDERED_START = re.compile(r"^(\d{1,9})([.)])")


def _escape_line(line: str) -> str:
    """Una riga di testo → la stessa riga in Markdown, senza interpretazioni."""
    stripped = line.lstrip(" \t")
    indent = line[: len(line) - len(stripped)]
    body = _INLINE_SPECIAL.sub(r"\\\1", stripped)
    body = _BLOCK_START.sub(r"\\\1", body)
    body = _ORDERED_START.sub(r"\1\\\2", body)
    # Spazi multipli interni: l'HTML li collasserebbe in uno. Il primo resta
    # uno spazio normale, gli altri diventano &nbsp; (allineamenti a mano).
    body = re.sub(r" {2,}", lambda m: " " + "&nbsp;" * (len(m.group(0)) - 1), body)
    body = body.replace("\t", "&nbsp;" * 4)
    # Il rientro iniziale farebbe un blocco di codice (4 spazi) o sparirebbe:
    # come &nbsp; resta visibile e non viene letto come rientro di blocco.
    lead = indent.replace("\t", "    ").replace(" ", "&nbsp;")
    return lead + body


def text_to_markdown(text: str) -> str:
    """Testo semplice → Markdown che si renderizza come lo stesso testo."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    for index, line in enumerate(lines):
        if not line.strip():
            out.append("")
            continue
        escaped = _escape_line(line)
        nxt = lines[index + 1] if index + 1 < len(lines) else ""
        # A capo dentro un paragrafo: backslash finale = interruzione forzata.
        if nxt.strip():
            escaped += "\\"
        out.append(escaped)
    return "\n".join(out).rstrip("\n") + "\n"


@register
class PlainTextMarkdownConverter(Converter):
    category = "document"
    name = "Plain text → Markdown"
    input_formats = frozenset({"txt"})
    output_formats = frozenset({"md"})
    priority = 15  # batte pandoc (10), che rifonderebbe il testo come markdown

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src != "txt" or dst != "md":
            return unsupported()
        return lossless("Every character and line break is kept; Markdown symbols are "
                        "escaped and indentation is kept with non-breaking spaces, so the "
                        "rendered page shows the original text.")

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        progress(ProgressEvent(0.2, "Reading text…"))
        raw = job.src_path.read_bytes()
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        job.output_path.write_text(text_to_markdown(text), encoding="utf-8")
        progress(ProgressEvent(1.0, "Done"))
        return ConversionResult(ok=True, output_path=job.output_path,
                                output_size=job.output_path.stat().st_size,
                                verdict=self.verdict(job.src_format, job.dst_format))
