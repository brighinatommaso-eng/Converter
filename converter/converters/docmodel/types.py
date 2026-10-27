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


# Proiettili "ASCII" ambigui: sono anche segno meno, trattino, asterisco di nota.
_DASH_BULLETS = "-–*"


def is_bullet(text: str) -> bool:
    """True se `text` inizia con un proiettile seguito da testo.

    I glifi dedicati (•, ▪, ◦…) bastano da soli. Trattino, lineetta e asterisco
    invece sono anche testo normale, quindi valgono come proiettile solo se
    seguiti da uno spazio: "-5 °C" (segno meno), "---" (riga) e "*Nota" restano
    testo. "- 5 kg" con lo spazio resta un elenco. La lineetta lunga "—" non è
    mai un proiettile: a inizio riga apre un inciso o un dialogo, e trattarla da
    voce di elenco spezzava i paragrafi a metà frase.
    """
    stripped = text.lstrip()
    if not stripped:
        return False
    first = stripped[0]
    if first in _DASH_BULLETS:
        if len(stripped) < 2 or not stripped[1].isspace():
            return False
        rest = stripped[1:].strip()
        return bool(rest) and not all(char in "-–—*_ " for char in rest)
    if first not in BULLETS or first == "—":
        return False
    return bool(stripped[1:].strip())


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
    link: str = ""      # URL di un hyperlink Word, "" = testo normale


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
    # Ruolo deciso dall'analisi AI del layout: "title"/"heading" (regione titolo),
    # "body" (paragrafo di una regione di testo: mai un titolo), "" = lo decide
    # l'euristica dei corpi.
    role: str = ""

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
    # None = eredita ListBlock.ordered. Serve agli elenchi misti (voci numerate
    # con sotto-punti puntati): spezzarli in blocchi separati faceva ripartire la
    # numerazione ("1. primo · - sotto · 1. secondo" invece di "2. secondo").
    ordered: bool | None = None

    def is_ordered(self, block: ListBlock) -> bool:
        return block.ordered if self.ordered is None else self.ordered


@dataclass(slots=True)
class ListBlock:
    items: list[ListItem] = field(default_factory=list)
    ordered: bool = False


@dataclass(slots=True)
class TableCell:
    paragraphs: list[Paragraph] = field(default_factory=list)
    col: int = -1                # colonna logica (0-based), -1 = posizione sequenziale
    col_span: int = 1            # merge orizzontale Word (w:gridSpan)
    row_span: int = 1            # merge verticale Word (w:vMerge restart/continue)


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
    def col_count(self) -> int:
        """Numero di colonne della griglia, contando anche le celle unite."""
        count = len(self.widths_pt)
        for row in self.rows:
            fallback = 0
            for cell in row:
                col = cell.col if cell.col >= 0 else fallback
                count = max(count, col + max(1, cell.col_span))
                fallback = col + max(1, cell.col_span)
        return count

    def text_grid(self) -> list[list[str]]:
        """Griglia logica compatta: i merge ripetono il testo nelle righe coperte.

        Le colonne sono quelle in cui una cella *inizia* davvero: una colonna
        coperta solo da un merge orizzontale non diventa una colonna vuota in
        Markdown/TXT. Markdown e TXT non hanno rowspan/colspan, quindi questa è
        la vista tabellare comune ai writer e allo snapshot dei test.
        """
        entries: list[list[tuple[int, int, str]]] = []
        origins: set[int] = set()
        for row in self.rows:
            fallback = 0
            row_entries: list[tuple[int, int, str]] = []
            for cell in row:
                col = cell.col if cell.col >= 0 else fallback
                span = max(1, cell.col_span)
                text = " ".join(p.text for p in cell.paragraphs if p.text.strip()).strip()
                row_entries.append((col, max(1, cell.row_span), text))
                origins.add(col)
                fallback = col + span
            entries.append(row_entries)

        columns = sorted(origins)
        if not columns:
            # Righe presenti ma senza celle (es. celle dentro w:sdt): la struttura
            # delle righe va conservata, non azzerata.
            return [[] for _ in self.rows]
        index = {col: position for position, col in enumerate(columns)}
        grid = [["" for _ in columns] for _ in self.rows]
        for row_index, row_entries in enumerate(entries):
            for col, row_span, text in row_entries:
                target = index[col]
                for covered in range(row_index, min(len(grid), row_index + row_span)):
                    if not grid[covered][target]:
                        grid[covered][target] = text
        return grid

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
    # Testatina/piè di pagina Word: non sono blocchi del corpo (non si ripetono
    # nell'ordine di lettura), ma vanno conservati per i writer che li supportano.
    headers: list[Paragraph] = field(default_factory=list)
    footers: list[Paragraph] = field(default_factory=list)
    # Pagine che il lettore non è riuscito a interpretare (PDF danneggiati): il
    # resto del documento si legge, ma il verdetto deve dire cosa manca.
    skipped_pages: int = 0
    # Caratteri che il PDF non permette di decodificare (font senza mappa
    # Unicode): resi con U+FFFD "�" e dichiarati nel verdetto.
    undecoded_chars: int = 0
    # Analisi del layout: pagine ricostruite dal modello AI e pagine rimaste al
    # motore euristico (modello assente, pagina ruotata, errore). Il verdetto lo
    # dichiara: le due strade non danno la stessa qualità.
    ai_pages: int = 0
    heuristic_pages: int = 0
    ai_unavailable: str = ""

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
