"""Modello documentale intermedio (IR): l'unica rappresentazione su cui lavorano
lettori (PDF/DOCX → IR) e scrittori (IR → docx/md/txt/html).

Perché esiste: un PDF non ha struttura — ha caratteri a coordinate. Ricostruire
"titoli, paragrafi, liste, tabelle" una volta sola, in un modello esplicito, fa sì
che ogni formato di uscita racconti la STESSA storia. Prima ogni coppia aveva il
suo percorso (dump di testo pdfminer, clone visivo pdf2docx) e i risultati non
c'erano: la tabella si smontava in txt, l'heading spariva in docx.

Il modello resta serializzabile e senza dipendenze: niente pdfminer né python-docx
qui dentro. È anche il punto d'innesto naturale per un passo AI opzionale di
rifinitura (Document in → Document out), spento per default.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Caratteri usati come proiettile dai PDF (Symbol/Wingdings finiscono nelle aree
# private-use: \uf0b7 è il "•" di Symbol che Word e LibreOffice stampano).
BULLETS = "•●▪▫◦‣∙·\uf0b7\uf0a7\uf0d8\uf0fc-–—*"


def is_bullet(text: str) -> bool:
    """True se `text` inizia con un proiettile (e non è una riga di soli trattini)."""
    stripped = text.lstrip()
    if not stripped:
        return False
    if stripped[0] not in BULLETS:
        return False
    rest = stripped[1:].lstrip()
    # Un trattino è un proiettile solo se seguito da spazio e da testo: così
    # "---" o "-5 °C" non diventano voci di elenco.
    return bool(rest)


@dataclass(slots=True)
class Run:
    """Testo con la sua formattazione, il più vicino possibile all'originale."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    font: str = ""      # famiglia leggibile ("Calibri"), "" = quella del formato
    size: float = 0.0   # punti tipografici, 0 = sconosciuta
    color: str = ""     # "#RRGGBB", "" = nero/default


@dataclass(slots=True)
class Line:
    """Una riga *fisica*: serve al layout visivo e alla ricostruzione dei paragrafi.

    y è la coordinata PDF (origine in basso a sinistra): gli scrittori che usano
    l'origine in alto (docx/html) la ribaltano loro, con l'altezza pagina nota.
    """

    runs: list[Run]
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    # Parole con la loro geometria: servono a riconoscere le tabelle allineate
    # per spazi (un PDF non ha celle, ha solo parole a coordinate).
    words: list[tuple[float, float, str]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)

    @property
    def size(self) -> float:
        sizes = [r.size for r in self.runs if r.size]
        return max(sizes) if sizes else 0.0

    @property
    def bold(self) -> bool:
        return any(r.bold for r in self.runs)

    @property
    def font(self) -> str:
        for r in self.runs:
            if r.font:
                return r.font
        return ""

    @property
    def color(self) -> str:
        for r in self.runs:
            if r.color:
                return r.color
        return ""

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    @property
    def bottom(self) -> float:
        return self.y


@dataclass(slots=True)
class Paragraph:
    """Blocco di testo continuo: una o più righe fisiche che formano un paragrafo."""

    lines: list[Line]
    style: str = "body"          # "body" | "heading" | "title"
    level: int = 0               # livello heading 1..6
    align: str = "left"          # "left" | "center" | "right" | "justify"
    indent_pt: float = 0.0       # rientro della prima riga rispetto al margine
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0

    @property
    def text(self) -> str:
        """Testo continuo: righe unite, sillabazione a fine riga ricucita."""
        out = ""
        for line in self.lines:
            chunk = line.text.strip()
            if not chunk:
                continue
            if not out:
                out = chunk
            elif out.endswith("-") and len(out) > 1 and out[-2].isalpha() and chunk[:1].islower():
                out = out[:-1] + chunk          # "inter-" + "national" -> "international"
            elif out.endswith("‑"):             # trattino unificatore
                out = out[:-1] + chunk
            else:
                out += " " + chunk
        return out

    @property
    def size(self) -> float:
        sizes = [line.size for line in self.lines if line.size]
        return max(sizes) if sizes else 0.0

    @property
    def font(self) -> str:
        for line in self.lines:
            if line.font:
                return line.font
        return ""

    @property
    def bold(self) -> bool:
        return bool(self.lines) and all(line.bold for line in self.lines if line.text.strip())

    @property
    def x(self) -> float:
        return min((line.x for line in self.lines), default=0.0)

    @property
    def y(self) -> float:
        return max((line.y for line in self.lines), default=0.0)

    @property
    def width(self) -> float:
        return max((line.width for line in self.lines), default=0.0)

    @property
    def height(self) -> float:
        return sum(line.height for line in self.lines)

    @property
    def right(self) -> float:
        return max((line.right for line in self.lines), default=self.x)

    @property
    def top(self) -> float:
        return max((line.top for line in self.lines), default=self.y)

    @property
    def bottom(self) -> float:
        return min((line.bottom for line in self.lines), default=self.y)


@dataclass(slots=True)
class ListItem:
    marker: str
    level: int
    paragraph: Paragraph


@dataclass(slots=True)
class ListBlock:
    items: list[ListItem] = field(default_factory=list)
    ordered: bool = False


@dataclass(slots=True)
class TableCell:
    paragraphs: list[Paragraph] = field(default_factory=list)


@dataclass(slots=True)
class Table:
    rows: list[list[TableCell]] = field(default_factory=list)
    widths_pt: list[float] = field(default_factory=list)
    borders: bool = False
    header_rows: int = 0
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def top(self) -> float:
        return self.y + self.height

    @property
    def bottom(self) -> float:
        return self.y


@dataclass(slots=True)
class ImageBlock:
    data: bytes
    ext: str = "png"            # estensione reale del flusso ("png", "jpg", "tif"…)
    width_pt: float = 0.0
    height_pt: float = 0.0
    x: float = 0.0
    y: float = 0.0

    @property
    def right(self) -> float:
        return self.x + self.width_pt

    @property
    def top(self) -> float:
        return self.y + self.height_pt

    @property
    def bottom(self) -> float:
        return self.y


@dataclass(slots=True)
class PageBreak:
    """Confine di pagina dell'originale (i formati testo lo ignorano)."""


Block = Paragraph | ListBlock | Table | ImageBlock | PageBreak


@dataclass(slots=True)
class Document:
    blocks: list[Block] = field(default_factory=list)
    page_width_pt: float = 595.0
    page_height_pt: float = 842.0
    margin_left_pt: float = 0.0
    margin_right_pt: float = 0.0
    title: str = ""
    source_format: str = ""

    def body_size(self) -> float:
        """Dimensione più diffusa, pesata sui caratteri: è il "testo normale"."""
        counts: dict[float, int] = {}
        for block in self.blocks:
            paragraphs = []
            if isinstance(block, Paragraph):
                paragraphs = [block]
            elif isinstance(block, ListBlock):
                paragraphs = [item.paragraph for item in block.items]
            elif isinstance(block, Table):
                paragraphs = [p for row in block.rows for cell in row for p in cell.paragraphs]
            for para in paragraphs:
                for line in para.lines:
                    for run in line.runs:
                        if run.size:
                            counts[round(run.size, 1)] = counts.get(round(run.size, 1), 0) + len(run.text)
        if not counts:
            return 11.0
        return max(counts.items(), key=lambda kv: kv[1])[0]
