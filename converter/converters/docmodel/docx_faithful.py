"""Scrittore DOCX unico: modificabile E impaginato come l'originale.

Prima c'erano due modalità che si escludevano: "struttura" (titoli, paragrafi,
tabelle Word veri, ma l'impaginazione persa: una colonna sola, spaziature del
template, pagine che non coincidono) e "visiva" (ogni riga un riquadro alla sua
posizione: identica, ma non si poteva scrivere un paragrafo senza rompere tutto).

Qui c'è un documento solo che fa entrambe le cose, con gli strumenti NATIVI di
Word, quelli che restano modificabili:

* una SEZIONE per ogni pagina del PDF, con le sue misure e i suoi margini;
* le colonne del PDF diventano colonne di sezione Word (con le larghezze e lo
  spazio fra colonne misurati), con l'interruzione di colonna dove l'originale
  passa alla colonna successiva;
* spazio prima di ogni blocco e interlinea misurati sul PDF, così le righe cadono
  alla stessa altezza; rientri (sinistro, destro, prima riga) misurati nella colonna;
* titoli con gli stili Titolo/Titolo N (navigazione, sommario) ma con l'aspetto
  dell'originale (font, corpo, colore), non quello del modello Word;
* liste native con il rientro dell'originale; tabelle Word con larghezze, altezze
  di riga e colori delle celle;
* riquadri colorati del PDF → ombreggiatura dei paragrafi;
* immagini nel flusso, oppure ancorate alla loro posizione quando hanno testo
  accanto (scorrimento "quadrato"), o dietro al testo se sono lo sfondo della pagina;
* testatina e piè di pagina ripetuti → header/footer Word, numero di pagina col
  campo PAGE.

Il limite onesto: se sul computer manca il font dell'originale, Word ne usa un
altro e le righe possono andare a capo in punti diversi. Per questo i font PDF
più comuni sono mappati sui loro equivalenti metrici (Helvetica → Arial, Times →
Times New Roman, …).
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from .types import Document, ImageBlock, ListBlock, PageBreak, Paragraph, Table

_EMU_PER_PT = 12700
_MIN_MARGIN = 14.0        # pt: spazio minimo ai bordi, contro lo "sbordo" di pagina
_NARROW = 0.62            # un blocco più stretto di così (rispetto al testo) sta in una colonna
_BACKGROUND_AREA = 0.6    # un'immagine che copre più di così è lo sfondo della pagina

# Font PDF senza equivalente installato su Windows/macOS → font con le stesse
# metriche (stessa larghezza dei caratteri), così le righe vanno a capo uguali.
_FONT_EQUIVALENTS = (
    (re.compile(r"^(helvetica|arial ?mt|nimbus ?sans|liberation ?sans|arimo)", re.I), "Arial"),
    (re.compile(r"^(times|nimbus ?rom|liberation ?serif|tinos|tex ?gyre ?termes)", re.I),
     "Times New Roman"),
    (re.compile(r"^(courier|nimbus ?mono|liberation ?mono|cousine)", re.I), "Courier New"),
    (re.compile(r"^(carlito)", re.I), "Calibri"),
    (re.compile(r"^(caladea)", re.I), "Cambria"),
    (re.compile(r"^(cm ?r|cmu ?serif|lm ?roman|latin ?modern ?roman|sf ?rm)", re.I), "Times New Roman"),
    (re.compile(r"^(cm ?ss|lm ?sans|latin ?modern ?sans)", re.I), "Arial"),
    (re.compile(r"^(cm ?tt|lm ?mono|latin ?modern ?mono)", re.I), "Courier New"),
)


# Font che Word (Windows/macOS) ha di serie e LibreOffice sostituisce con un
# equivalente metrico: con questi le righe vanno a capo come nell'originale.
_STANDARD_FONTS = {
    "arial", "arial narrow", "arial black", "times new roman", "courier new", "calibri",
    "calibri light", "cambria", "cambria math", "candara", "consolas", "constantia",
    "corbel", "georgia", "verdana", "tahoma", "trebuchet ms", "segoe ui", "segoe ui light",
    "segoe ui semibold", "garamond", "book antiqua", "palatino linotype", "century gothic",
    "franklin gothic medium", "lucida console", "lucida sans unicode", "impact",
    "comic sans ms", "symbol", "wingdings", "aptos", "malgun gothic", "ms gothic",
    "ms mincho", "simsun", "microsoft yahei", "meiryo", "yu gothic", "batang", "gulim",
}
_SERIF_HINTS = ("minion", "garamond", "times", "roman", "serif", "georgia", "palatino",
                "caslon", "baskerville", "bodoni", "didot", "slab", "merriweather", "utopia",
                "charter", "century", "book", "antiqua", "sabon", "janson", "plantin",
                "bembo", "cochin", "lora", "crimson", "playfair", "tiempos", "cm r", "cmr",
                "dutch", "mincho", "batang", "myungjo", "song")
_MONO_HINTS = ("mono", "courier", "code", "consol", "typewriter", "menlo", "inconsolata")


def word_font(name: str) -> str:
    """Il font da chiedere a Word per un font del PDF.

    Un font installato quasi ovunque resta tale; per gli altri (MinionPro,
    HelveticaNeue, Poppins…) Word sceglierebbe un sostituto a caso, diverso fra
    computer: si sceglie qui uno standard dello stesso tipo (con grazie, senza,
    a spaziatura fissa), così il risultato è lo stesso ovunque e la sua larghezza
    si può misurare (vedi `text_scale`)."""
    for pattern, replacement in _FONT_EQUIVALENTS:
        if pattern.match(name or ""):
            return replacement
    if not name or name.lower() in _STANDARD_FONTS:
        return name
    low = name.lower()
    if any(hint in low for hint in _MONO_HINTS):
        return "Courier New"
    if any(hint in low for hint in _SERIF_HINTS):
        return "Times New Roman"
    if any(ord(char) > 0x2E80 for char in name) or any(
            hint in low for hint in ("gothic", "hei", "malgun", "nanum", "noto sans cjk")):
        return name            # font CJK: lo sostituisce Word col suo font di sistema
    return "Arial"


# Metriche dei sostituti: Arial/Times New Roman/Courier New hanno le stesse
# larghezze di Helvetica/Times/Courier, di cui pdfminer porta le tabelle AFM.
_METRIC_NAMES = {
    "Arial": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
    "Times New Roman": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "Courier New": ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"),
}


def _metric_width(text: str, font: str, size: float, bold: bool, italic: bool) -> float | None:
    from pdfminer.fontmetrics import FONT_METRICS

    names = _METRIC_NAMES.get(font)
    if names is None:
        return None
    widths = FONT_METRICS[names[(1 if bold else 0) + (2 if italic else 0)]][1]
    total, missing = 0.0, 0
    for char in text:
        width = widths.get(char)
        if width is None:
            missing += 1
            width = 500
        total += width
    if text and missing > 0.2 * len(text):
        return None
    return total * size / 1000.0


def text_scale(source: Paragraph) -> int | None:
    """Scala orizzontale (%) che fa occupare al testo, nel font sostitutivo, la
    stessa larghezza che ha nel PDF; None se il font è quello vero o non misurabile.

    Così un paragrafo in MinionPro (stretto) scritto in Times New Roman va a capo
    negli stessi punti invece di allungarsi di qualche riga e spingere la pagina."""
    pdf_width = substitute = 0.0
    dominant = _dominant_font(source)
    lines = source.lines
    if source.align == "justify" and len(lines) > 1:
        # Le righe giustificate sono allargate dagli spazi: la larghezza "vera"
        # del testo si legge solo sull'ultima riga, che non è giustificata.
        lines = lines[-1:]
    for line in lines:
        runs = [run for run in line.runs if run.text.strip()]
        if not runs or line.width <= 0:
            continue
        line_width = 0.0
        for run in runs:
            font = run.font or dominant
            target = word_font(font)
            if not font or target == font:
                return None        # font vero (o ignoto): nessuna correzione
            width = _metric_width(run.text, target, run.size or 10.0, run.bold, run.italic)
            if width is None:
                return None
            line_width += width
        pdf_width += line.width
        substitute += line_width
    if pdf_width <= 0 or substitute <= 0:
        return None
    # 2% di margine: una riga che nell'originale sta "al pelo" non deve andare a
    # capo nel sostituto. Mai allargare (allungherebbe il testo e la pagina).
    scale = min(98, round(98 * pdf_width / substitute))
    return max(66, scale)


def _dominant_font(source: Paragraph) -> str:
    counts: dict[str, int] = {}
    for line in source.lines:
        for run in line.runs:
            if run.font:
                counts[run.font] = counts.get(run.font, 0) + len(run.text)
    return max(counts, key=counts.get) if counts else ""


# --- Geometria dei blocchi ----------------------------------------------------

def _paragraphs_of(block) -> list[Paragraph]:
    if isinstance(block, Paragraph):
        return [block]
    if isinstance(block, ListBlock):
        return [item.paragraph for item in block.items]
    if isinstance(block, Table):
        return [p for row in block.rows for cell in row for p in cell.paragraphs]
    return []


def _box(block) -> tuple[float, float, float, float] | None:
    """(x0, y0, x1, y1) in coordinate PDF, o None se il blocco non ha geometria."""
    if isinstance(block, ImageBlock):
        if block.width_pt <= 0:
            return None
        return block.x, block.y, block.x + block.width_pt, block.y + block.height_pt
    if isinstance(block, Table):
        if block.width <= 0:
            return None
        return block.x, block.y, block.x + block.width, block.y + block.height
    lines = [line for p in _paragraphs_of(block) for line in p.lines]
    if not lines or all(line.width <= 0 for line in lines):
        return None
    return (min(line.x for line in lines), min(line.y for line in lines),
            max(line.right for line in lines), max(line.top for line in lines))


def _split_pages(document: Document) -> list[list]:
    pages: list[list] = [[]]
    for block in document.blocks:
        if isinstance(block, PageBreak):
            pages.append([])
        else:
            pages[-1].append(block)
    return pages


def _is_background(image: ImageBlock, width: float, height: float) -> bool:
    return image.width_pt * image.height_pt >= _BACKGROUND_AREA * width * height


class _Band:
    """Tratto di pagina a più colonne affiancate: diventa una tabella senza bordi
    (una cella per colonna). Le colonne di sezione Word si "bilanciano" in modo
    diverso da Word a LibreOffice e spostavano il testo da una colonna all'altra;
    una tabella invisibile tiene ogni colonna al suo posto ovunque, e resta testo
    modificabile."""

    def __init__(self, columns: list[tuple[float, float]]) -> None:
        self.columns = columns
        self.cells: list[list] = [[] for _ in columns]
        self.top = float("-inf")
        self.bottom = float("inf")


def _columns_of_page(boxes: list[tuple[float, float, float, float]], left: float,
                     right: float) -> list[tuple[float, float]]:
    """Colonne della pagina: intervalli orizzontali dei blocchi stretti, disgiunti,
    con almeno due blocchi affiancati (stessa fascia verticale)."""
    width = max(right - left, 1.0)
    narrow = [b for b in boxes if (b[2] - b[0]) <= _NARROW * width]
    # Solo i blocchi con un vicino AFFIANCATO (stessa fascia, x disgiunte) fanno
    # colonna: un titolo stretto e centrato sopra tre colonne non le deve fondere.
    narrow = [a for a in narrow
              if any(b is not a and a[1] < b[3] and b[1] < a[3]
                     and (b[2] <= a[0] + 2 or b[0] >= a[2] - 2) for b in narrow)]
    spans = sorted((b[0], b[2]) for b in narrow)
    merged: list[list[float]] = []
    for x0, x1 in spans:
        if merged and x0 <= merged[-1][1] - 4:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    if len(merged) < 2:
        return []
    columns = [(x0, x1) for x0, x1 in merged]
    members = [[b for b in narrow if x0 - 1 <= b[0] and b[2] <= x1 + 1] for x0, x1 in columns]
    side_by_side = any(a[1] < b[3] and b[1] < a[3]
                       for i, column in enumerate(members) for a in column
                       for j, other in enumerate(members) if j != i for b in other)
    return columns if side_by_side else []


def _page_items(page_blocks: list, left: float, right: float) -> list:
    """Elementi della pagina dall'alto in basso: blocchi a tutta larghezza e tratti
    a colonne (`_Band`).

    Per il Word conta la GEOMETRIA: i blocchi in colonna che si sovrappongono in
    verticale formano un tratto, e dentro ogni colonna si va dall'alto in basso.
    L'ordine di lettura del modello resta per md/txt/html, ma qui un'infografica
    letta "a zig-zag" non deve scambiare i suoi riquadri di posto."""
    boxes = {id(block): _box(block) for block in page_blocks}
    columns = _columns_of_page([b for b in boxes.values() if b], left, right)
    width = max(right - left, 1.0)

    def column_of(block) -> int | None:
        box = boxes[id(block)]
        if not columns or box is None or (box[2] - box[0]) > _NARROW * width:
            return None
        for index, (x0, x1) in enumerate(columns):
            if x0 - 1 <= box[0] and box[2] <= x1 + 1:
                return index
        return None

    flow: list = []          # blocchi a tutta larghezza (o senza geometria), in ordine
    in_columns: list = []    # (colonna, blocco, box)
    for block in page_blocks:
        column = column_of(block)
        if column is None:
            flow.append(block)
        else:
            in_columns.append((column, block, boxes[id(block)]))

    # Tratti: catene di blocchi in colonna che si sovrappongono in verticale.
    in_columns.sort(key=lambda item: -item[2][3])
    flow_tops = [boxes[id(b)][3] for b in flow if boxes.get(id(b))]
    bands: list[_Band] = []
    for column, block, box in in_columns:
        # Lo stesso tratto continua anche dopo lo spazio fra due paragrafi (fino a
        # 30 pt), purché in mezzo non ci sia un blocco a tutta larghezza.
        band = None
        if bands:
            last = bands[-1]
            between = any(box[3] < top < last.bottom for top in flow_tops)
            if box[3] > last.bottom - 30.0 and not between:
                band = last
        if band is None:
            band = _Band(columns)
            bands.append(band)
        band.cells[column].append(block)
        band.top = max(band.top, box[3])
        band.bottom = min(band.bottom, box[1])
    # Un blocco a tutta larghezza che taglia un tratto lo spezza: si riprova
    # tenendolo come confine (caso raro, es. una figura larga fra due colonne).
    result: list = []
    for band in bands:
        used = [i for i, cell in enumerate(band.cells) if cell]
        if len(used) < 2:
            flow.extend(block for cell in band.cells for block in cell)
            continue
        for cell in band.cells:
            cell.sort(key=lambda block: -boxes[id(block)][3])
        result.append(band)

    def top_of(item) -> float:
        if isinstance(item, _Band):
            return item.top
        box = boxes.get(id(item))
        return box[3] if box else float("inf")

    # I blocchi senza geometria tengono la posizione relativa del flusso.
    ordered = sorted(flow + result, key=lambda item: -top_of(item))
    if any(boxes.get(id(item)) is None for item in flow):
        return flow + result
    return ordered


# --- XML Word di supporto -------------------------------------------------------

def _qn(tag: str):
    from docx.oxml.ns import qn

    return qn(tag)


def _shade(element_pr, color: str) -> None:
    """Ombreggiatura (w:shd) di un paragrafo o di una cella."""
    from docx.oxml import OxmlElement

    shd = OxmlElement("w:shd")
    shd.set(_qn("w:val"), "clear")
    shd.set(_qn("w:color"), "auto")
    shd.set(_qn("w:fill"), color.lstrip("#").upper())
    element_pr.append(shd)


def _neutral_styles(docx) -> None:
    """Titoli Word con l'aspetto dell'ORIGINALE: niente blu, bordi, spazi del modello.

    Gli stili restano (navigazione, sommario, struttura), ma il loro aspetto lo
    decide il PDF: i run portano font/corpo/colore espliciti."""
    from docx.shared import Pt

    for name in ("Normal", "Title", *(f"Heading {n}" for n in range(1, 10)),
                 "List Bullet", "List Number", "List Bullet 2", "List Number 2",
                 "List Bullet 3", "List Number 3"):
        try:
            style = docx.styles[name]
        except KeyError:
            continue
        fmt = style.paragraph_format
        fmt.space_before = Pt(0)
        fmt.space_after = Pt(0)
        if name != "Normal":
            style.font.color.rgb = None
            style.font.italic = None
            p_pr = style.element.pPr
            if p_pr is not None:
                for border in p_pr.findall(_qn("w:pBdr")):
                    p_pr.remove(border)
    try:
        title = docx.styles["Title"]
        r_pr = title.element.rPr
        if r_pr is not None:
            for tag in ("w:spacing", "w:kern"):
                for item in r_pr.findall(_qn(tag)):
                    r_pr.remove(item)
    except KeyError:
        pass


def _tiny(paragraph) -> None:
    """Paragrafo di servizio (interruzione di sezione, ancoraggio): alto 1 pt."""
    from docx.enum.text import WD_LINE_SPACING
    from docx.shared import Pt

    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    fmt.line_spacing = Pt(1)


def _anchor_picture(run, image: ImageBlock, page_height: float, *, behind: bool) -> None:
    """Trasforma l'immagine inline del run in un'immagine ancorata alla pagina.

    `behind`: dietro al testo (sfondo); altrimenti il testo le scorre attorno."""
    from docx.oxml import parse_xml

    inline = run._r.find(".//" + _qn("wp:inline"))
    if inline is None:
        return
    extent = inline.find(_qn("wp:extent"))
    doc_pr = inline.find(_qn("wp:docPr"))
    graphic = inline.find(_qn("a:graphic"))
    x = int(max(0.0, image.x) * _EMU_PER_PT)
    y = int(max(0.0, page_height - image.top) * _EMU_PER_PT)
    wrap = "<wp:wrapNone/>" if behind else '<wp:wrapSquare wrapText="bothSides"/>'
    anchor = parse_xml(
        '<wp:anchor xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        f'distT="0" distB="0" distL="57150" distR="57150" simplePos="0" relativeHeight="{1 if behind else 3}" '
        f'behindDoc="{1 if behind else 0}" locked="0" layoutInCell="1" allowOverlap="1">'
        '<wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="page"><wp:posOffset>{x}</wp:posOffset></wp:positionH>'
        f'<wp:positionV relativeFrom="page"><wp:posOffset>{y}</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{extent.get("cx")}" cy="{extent.get("cy")}"/>'
        '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f"{wrap}</wp:anchor>")
    anchor.append(doc_pr)
    anchor.append(parse_xml(
        '<wp:cNvGraphicFramePr xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/'
        'wordprocessingDrawing"/>'))
    anchor.append(graphic)
    inline.addprevious(anchor)
    inline.getparent().remove(inline)


def _add_page_field(paragraph) -> None:
    """Campo PAGE: Word scrive il numero della pagina corrente."""
    from docx.oxml import OxmlElement

    for kind, text in (("begin", None), (None, " PAGE "), ("separate", None), (None, "1"),
                       ("end", None)):
        run = paragraph.add_run()
        if kind:
            char = OxmlElement("w:fldChar")
            char.set(_qn("w:fldCharType"), kind)
            run._r.append(char)
        elif text == " PAGE ":
            instr = OxmlElement("w:instrText")
            instr.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            instr.text = text
            run._r.append(instr)
        else:
            run.text = text


# --- Scrittore --------------------------------------------------------------------

class _FaithfulWriter:
    def __init__(self, document: Document, page_breaks: bool, report) -> None:
        from docx import Document as DocxDocument

        self.document = document
        self.page_breaks = page_breaks
        self.report = report
        self.docx = DocxDocument()
        _neutral_styles(self.docx)
        self.geometry = document.source_format == "pdf"
        # Dove si scrive: il corpo del documento o la cella di una colonna.
        self.target = self.docx
        self._custom_footers = False

    # Paragrafi --------------------------------------------------------------
    def _runs(self, paragraph, source: Paragraph, keep_light: bool) -> None:
        from docx.oxml import OxmlElement

        from .writers import _apply_hyperlink, _paragraph_runs, _set_run

        scale = text_scale(source) if self.geometry else None
        dominant = _dominant_font(source)
        for run in _paragraph_runs(source):
            run.font = word_font(run.font or dominant)
            docx_run = paragraph.add_run()
            _set_run(docx_run, run, keep_light=keep_light)
            if scale:
                width = OxmlElement("w:w")
                width.set(_qn("w:val"), str(scale))
                docx_run._r.get_or_add_rPr().append(width)
            if run.link:
                _apply_hyperlink(paragraph, docx_run, run.link)

    def _format(self, paragraph, source: Paragraph, column: tuple[float, float],
                space_before: float, *, list_item: bool = False) -> float | None:
        """Formato del paragrafo; restituisce l'interlinea esatta usata (o None)."""
        from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
        from docx.shared import Pt

        fmt = paragraph.paragraph_format
        fmt.space_after = Pt(0)
        fmt.space_before = Pt(max(0.0, round(space_before, 1)))
        paragraph.alignment = {"center": WD_ALIGN_PARAGRAPH.CENTER,
                               "right": WD_ALIGN_PARAGRAPH.RIGHT,
                               "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}.get(
                                   source.align, WD_ALIGN_PARAGRAPH.LEFT)
        if source.shading:
            _shade(paragraph._p.get_or_add_pPr(), source.shading)
        lines = source.lines
        if not lines or not self.geometry:
            return None
        left, right = column
        x = min(line.x for line in lines)
        if list_item:
            first = lines[0]
            text_x = first.words[1][0] if len(first.words) > 1 else first.x + 18
            fmt.left_indent = Pt(max(0.0, round(text_x - left, 1)))
            fmt.first_line_indent = Pt(round(min(0.0, first.x - text_x), 1))
        elif source.align != "center":
            if x - left > 1.5:
                fmt.left_indent = Pt(round(x - left, 1))
            if len(lines) >= 2:
                rest = min(line.x for line in lines[1:])
                if abs(lines[0].x - rest) > 2.0:
                    fmt.left_indent = Pt(max(0.0, round(rest - left, 1)))
                    fmt.first_line_indent = Pt(round(lines[0].x - rest, 1))
                widest = max(line.right for line in lines)
                if right - widest > 8.0:
                    fmt.right_indent = Pt(round(right - widest, 1))
        # Interlinea dell'originale: passo fra le basi delle righe.
        sizes = [line.size for line in lines if line.size]
        size = max(sizes) if sizes else 11.0
        if len(lines) >= 2:
            steps = sorted(a.y - b.y for a, b in zip(lines, lines[1:], strict=False) if a.y > b.y)
            pitch = steps[len(steps) // 2] if steps else 0.0
        else:
            # Riga singola: alta quanto nel PDF, così il blocco occupa lo stesso spazio.
            pitch = lines[0].height if lines[0].height >= 0.95 * size else size * 1.15
        if 0.9 * size <= pitch <= 3.0 * size:
            fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
            fmt.line_spacing = Pt(round(pitch, 2))
            return pitch
        return None

    def _paragraph(self, block: Paragraph, column, space_before: float,
                   keep_light: bool) -> float | None:
        """Scrive il paragrafo; restituisce l'altezza stimata in Word (o None)."""
        target = self.target
        style = None
        if block.style == "title":
            style = "Title"
        elif block.style == "heading":
            style = f"Heading {max(1, min(block.level, 6))}"
        try:
            paragraph = target.add_paragraph(style=style) if style else target.add_paragraph()
        except KeyError:
            paragraph = target.add_paragraph()
        self._runs(paragraph, block, keep_light or bool(block.shading))
        pitch = self._format(paragraph, block, column, space_before)
        return pitch * len(block.lines) if pitch else None

    def _framed(self, block: Paragraph, page_height: float, keep_light: bool) -> None:
        """Paragrafo isolato in fondo alla pagina (piè, fonte, nota): resta testo
        modificabile ma in un riquadro alla sua posizione, fuori dal flusso, così
        non può far sbordare la pagina sulla successiva."""
        from docx.oxml import OxmlElement

        self._paragraph(block, (block.x, block.right), 0.0, keep_light)
        paragraph = self.target.paragraphs[-1]
        paragraph.paragraph_format.left_indent = None
        paragraph.paragraph_format.right_indent = None
        frame = OxmlElement("w:framePr")
        frame.set(_qn("w:w"), str(int(max(block.right - block.x + 8, 24) * 20)))
        frame.set(_qn("w:x"), str(int(max(block.x, 0) * 20)))
        frame.set(_qn("w:y"), str(int(max(page_height - block.top, 0) * 20)))
        frame.set(_qn("w:hAnchor"), "page")
        frame.set(_qn("w:vAnchor"), "page")
        frame.set(_qn("w:wrap"), "around")
        p_pr = paragraph._p.get_or_add_pPr()
        # framePr va subito dopo pStyle/keepNext/keepLines/pageBreakBefore nello schema.
        anchor = None
        for tag in ("w:pStyle", "w:keepNext", "w:keepLines", "w:pageBreakBefore"):
            found = p_pr.find(_qn(tag))
            if found is not None:
                anchor = found
        if anchor is not None:
            anchor.addnext(frame)
        else:
            p_pr.insert(0, frame)

    def _list(self, block: ListBlock, column, space_before: float, keep_light: bool) -> float | None:
        """Scrive l'elenco; restituisce il fondo stimato in Word (y PDF) o None."""
        from .writers import _marker_start, _restarted_num_id

        restarted: dict[str, int] = {}      # ogni elenco numerato riparte da 1
        previous_bottom = None
        for index, item in enumerate(block.items):
            level = max(1, min(item.level, 3))
            ordered = item.is_ordered(block)
            style = None
            if ordered and item.marker.rstrip(".)").isdigit():
                style = "List Number" if level == 1 else f"List Number {level}"
            elif not ordered:
                style = "List Bullet" if level == 1 else f"List Bullet {level}"
            try:
                paragraph = (self.target.add_paragraph(style=style) if style
                             else self.target.add_paragraph())
            except KeyError:
                paragraph = self.target.add_paragraph()
                style = None
            if style is None:
                paragraph.add_run(f"{item.marker}\t")
            elif ordered:
                num_id = _restarted_num_id(self.docx, style, restarted, _marker_start(item.marker))
                if num_id is not None:
                    paragraph._p.get_or_add_pPr().get_or_add_numPr().get_or_add_numId().val = num_id
            self._runs(paragraph, item.paragraph, keep_light or bool(item.paragraph.shading))
            gap = space_before if index == 0 else (
                previous_bottom - item.paragraph.top
                if previous_bottom is not None and item.paragraph.lines else 0.0)
            pitch = self._format(paragraph, item.paragraph, column, gap, list_item=True)
            if item.paragraph.lines:
                # Come per i paragrafi: il fondo è quello occupato IN WORD, così gli
                # scarti di interlinea non si sommano voce dopo voce.
                top = item.paragraph.top if previous_bottom is None else min(
                    item.paragraph.top, previous_bottom)
                previous_bottom = (top - pitch * len(item.paragraph.lines) if pitch
                                   else item.paragraph.bottom)
        return previous_bottom

    def _spacer(self, height: float) -> None:
        """Spazio verticale prima di una tabella (le tabelle non hanno "spazio prima")."""
        if height < 2.0:
            return
        from docx.enum.text import WD_LINE_SPACING
        from docx.shared import Pt

        paragraph = self.target.add_paragraph()
        fmt = paragraph.paragraph_format
        fmt.space_before = Pt(0)
        fmt.space_after = Pt(0)
        fmt.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        fmt.line_spacing = Pt(round(height, 1))

    def _table(self, table: Table, column, space_before: float) -> None:
        from docx.enum.table import WD_ROW_HEIGHT_RULE
        from docx.oxml import OxmlElement
        from docx.shared import Pt

        from .writers import _write_table

        self._spacer(space_before)
        before = len(self.target.tables)
        _write_table(self.target, table)
        if len(self.target.tables) == before:
            return
        docx_table = self.target.tables[-1]
        if self.geometry and table.x - column[0] > 1.5:
            ind = OxmlElement("w:tblInd")
            ind.set(_qn("w:w"), str(int(round((table.x - column[0]) * 20))))
            ind.set(_qn("w:type"), "dxa")
            docx_table._tbl.tblPr.append(ind)
        for row_index, row in enumerate(table.rows):
            if row_index >= len(docx_table.rows):
                break
            if self.geometry and table.rows:
                height = table.height / len(table.rows)
                docx_table.rows[row_index].height = Pt(max(8.0, height * 0.9))
                docx_table.rows[row_index].height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST
            fallback = 0
            for cell in row:
                col = cell.col if cell.col >= 0 else fallback
                fallback = col + max(1, cell.col_span)
                if cell.shading and col < len(docx_table.columns):
                    _shade(docx_table.cell(row_index, col)._tc.get_or_add_tcPr(), cell.shading)

    def _picture(self, image: ImageBlock):
        """Paragrafo con l'immagine; (paragrafo, run) o (None, None) se il formato non va."""
        from docx.shared import Pt

        from .writers import _png_from

        paragraph = self.target.add_paragraph()
        width = Pt(image.width_pt) if image.width_pt > 1 else None
        for data in (image.data, _png_from(image.data)):
            if data is None:
                continue
            run = paragraph.add_run()
            try:
                run.add_picture(BytesIO(data), width=width)
                return paragraph, run
            except Exception:
                paragraph._p.remove(run._r)
        paragraph._p.getparent().remove(paragraph._p)
        self.report.dropped_images += 1
        return None, None

    def _image(self, image: ImageBlock, column, space_before: float, page_height: float,
               floating: bool) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Pt

        paragraph, run = self._picture(image)
        if paragraph is None:
            return
        if self.geometry and floating:
            _anchor_picture(run, image, page_height, behind=False)
            _tiny(paragraph)
            return
        fmt = paragraph.paragraph_format
        fmt.space_before = Pt(max(0.0, round(space_before, 1)))
        fmt.space_after = Pt(0)
        if self.geometry:
            left, right = column
            center = image.x + image.width_pt / 2
            if abs(center - (left + right) / 2) <= 8.0 and image.width_pt < right - left - 4:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif image.x - left > 1.5:
                fmt.left_indent = Pt(round(image.x - left, 1))

    def _block(self, block, column, previous_bottom: float, page_blocks, size,
               keep_light: bool, allow_float: bool) -> float:
        """Scrive un blocco; restituisce il nuovo "fondo" del flusso (y PDF)."""
        box = _box(block)
        gap = (previous_bottom - box[3]) if (box and self.geometry) else 0.0
        if isinstance(block, Paragraph):
            height = self._paragraph(block, column, gap, keep_light)
            if height and box is not None:
                # Il fondo vale quanto occupa il paragrafo IN WORD, non nel PDF: così
                # le piccole differenze di interlinea non si accumulano pagina dopo pagina.
                return min(box[3], previous_bottom) - height
        elif isinstance(block, ListBlock):
            bottom = self._list(block, column, gap, keep_light)
            if bottom is not None:
                return bottom
        elif isinstance(block, Table):
            self._table(block, column, gap)
        elif isinstance(block, ImageBlock):
            floating = (allow_float and box is not None
                        and self._has_text_beside(block, page_blocks))
            self._image(block, column, gap, size[1], floating)
            if floating:
                return previous_bottom      # ancorata: non occupa spazio nel flusso
        return box[1] if box is not None else previous_bottom

    def _band(self, band: _Band, left: float, right: float, previous_bottom: float,
              page_blocks, size, keep_light: bool) -> float:
        """Tratto a colonne: tabella invisibile, una cella per colonna."""
        from docx.oxml import OxmlElement
        from docx.shared import Pt

        self._spacer(previous_bottom - band.top)
        columns = list(band.columns)
        cells = [list(cell) for cell in band.cells]
        # Una "colonna" più stretta di 36 pt (un timbro, un numero nel margine) non
        # regge del testo: le parole andrebbero a capo lettera per lettera, per
        # decine di pagine. Il suo contenuto passa alla colonna accanto.
        index = 0
        while len(columns) > 2 and index < len(columns):
            x0, x1 = max(columns[index][0], left), min(columns[index][1], right)
            if x1 - x0 < 36.0:
                neighbour = index - 1 if index > 0 else index + 1
                cells[neighbour].extend(cells[index])
                lo = min(columns[neighbour][0], columns[index][0])
                hi = max(columns[neighbour][1], columns[index][1])
                columns[neighbour] = (lo, hi)
                del columns[index], cells[index]
                continue
            index += 1
        # Celle: colonna + metà dello spazio fra colonne a destra (la cella dopo inizia
        # dove inizia la colonna). Margini interni a zero: il testo sta dov'era.
        # L'ultima cella arriva al margine destro del testo: un font sostitutivo un
        # po' più largo ha spazio prima di andare a capo.
        edges = [c[0] for c in columns] + [right]
        # Dentro i margini e in ordine: niente tabella più larga della pagina.
        edges = [min(max(edge, left), right) for edge in edges]
        for i in range(1, len(edges)):
            edges[i] = max(edges[i], edges[i - 1] + 12.0)
        overflow = edges[-1] - right
        if overflow > 0:
            edges = [left + (edge - left) * (right - left) / (edges[-1] - left) for edge in edges]
        widths = [max(6.0, edges[i + 1] - edges[i]) for i in range(len(columns))]
        # Fra una colonna e l'altra resta un margine interno destro (parte dello
        # spazio fra colonne): il testo non tocca la colonna accanto.
        gutters = [max(3.0, min(10.0, 0.5 * (columns[i + 1][0] - columns[i][1])))
                   for i in range(len(columns) - 1)] + [0.0]
        table = self.docx.add_table(rows=1, cols=len(columns)) if self.target is self.docx \
            else self.target.add_table(rows=1, cols=len(columns))
        table.autofit = False
        tbl_pr = table._tbl.tblPr
        margins = OxmlElement("w:tblCellMar")
        for side in ("top", "left", "bottom", "right"):
            node = OxmlElement(f"w:{side}")
            node.set(_qn("w:w"), "0")
            node.set(_qn("w:type"), "dxa")
            margins.append(node)
        tbl_pr.append(margins)
        if edges[0] - left > 1.5:
            ind = OxmlElement("w:tblInd")
            ind.set(_qn("w:w"), str(int(round((edges[0] - left) * 20))))
            ind.set(_qn("w:type"), "dxa")
            tbl_pr.append(ind)
        for index, width in enumerate(widths):
            table.columns[index].width = Pt(width)
            cell = table.rows[0].cells[index]
            cell.width = Pt(width)
            if gutters[index]:
                tc_mar = OxmlElement("w:tcMar")
                node = OxmlElement("w:right")
                node.set(_qn("w:w"), str(int(round(gutters[index] * 20))))
                node.set(_qn("w:type"), "dxa")
                tc_mar.append(node)
                cell._tc.get_or_add_tcPr().append(tc_mar)
        outer = self.target
        lowest = band.bottom
        for index, blocks in enumerate(cells):
            blocks.sort(key=lambda block: -((_box(block) or (0, 0, 0, 0))[3]))
            cell = table.rows[0].cells[index]
            self.target = cell
            column = (edges[index], edges[index + 1] - gutters[index])
            bottom = band.top
            for block in blocks:
                bottom = self._block(block, column, bottom, page_blocks, size, keep_light, False)
                lowest = min(lowest, bottom)
            # La cella nasce con un paragrafo vuoto: via, se dopo c'è contenuto.
            first = cell.paragraphs[0]
            if len(cell._tc) > 2 and not first.text and not first._p.xpath(".//w:drawing"):
                first._p.getparent().remove(first._p)
            # Una cella che finisce con una tabella vuole un paragrafo in coda.
            if cell._tc[-1].tag == _qn("w:tbl"):
                _tiny(cell.add_paragraph())
        self.target = outer
        return lowest

    # Pagine ------------------------------------------------------------------
    def _page_frame(self, section, page_blocks, size: tuple[float, float]):
        """Misure e margini della sezione-pagina; restituisce (sinistra, destra, alto)."""
        from docx.enum.section import WD_ORIENT
        from docx.shared import Pt

        width, height = size
        section.page_width = Pt(width)
        section.page_height = Pt(height)
        section.orientation = WD_ORIENT.LANDSCAPE if width > height else WD_ORIENT.PORTRAIT
        boxes = [b for block in page_blocks
                 if not (isinstance(block, ImageBlock) and _is_background(block, width, height))
                 and (b := _box(block))]
        if self.geometry and boxes:
            left = max(_MIN_MARGIN, min(b[0] for b in boxes))
            right = min(width - _MIN_MARGIN, max(b[2] for b in boxes))
            top = min(height - _MIN_MARGIN, max(b[3] for b in boxes))
            bottom = max(_MIN_MARGIN, min(b[1] for b in boxes))
        else:
            left, right = 72.0, width - 72.0
            top, bottom = height - 72.0, 72.0
        section.left_margin = Pt(left)
        section.right_margin = Pt(max(_MIN_MARGIN, width - right))
        section.top_margin = Pt(max(_MIN_MARGIN, height - top))
        # In fondo resta solo un margine minimo: se un font sostitutivo allunga di
        # una riga il testo, la pagina non deve sbordare sulla successiva.
        section.bottom_margin = Pt(max(8.0, min(bottom - 8.0, 30.0)))
        section.header_distance = Pt(min(18.0, max(6.0, (height - top) / 2)))
        section.footer_distance = Pt(min(18.0, max(6.0, bottom / 2)))
        return left, right, top

    def write(self, path: Path) -> None:
        from docx.enum.section import WD_SECTION

        docx = self.docx
        pages = _split_pages(self.document)
        sizes = self.document.page_sizes or []
        default = (self.document.page_width_pt, self.document.page_height_pt)
        for page_index, page_blocks in enumerate(pages):
            size = sizes[page_index] if page_index < len(sizes) else default
            if page_index == 0:
                section = docx.sections[0]
            else:
                start = WD_SECTION.NEW_PAGE if self.page_breaks else WD_SECTION.CONTINUOUS
                section = self._new_section(start)
            left, right, top = self._page_frame(section, page_blocks, size)
            backgrounds = [b for b in page_blocks if self.geometry
                           and isinstance(b, ImageBlock) and _is_background(b, *size)]
            if page_index in self.document.ocr_pages:
                # Scansione con testo OCR: vale il testo; la foto della pagina sotto il
                # testo ricostruito lo renderebbe illeggibile (due copie sovrapposte).
                page_blocks = [b for b in page_blocks if not any(b is bg for bg in backgrounds)]
                backgrounds = []
            # Gli sfondi per primi: ancorati alla pagina, dietro al testo, e sicuri di
            # stare sulla pagina giusta qualunque cosa succeda al flusso.
            for image in backgrounds:
                paragraph, run = self._picture(image)
                if paragraph is not None:
                    _anchor_picture(run, image, size[1], behind=True)
                    _tiny(paragraph)
            content = [b for b in page_blocks if not any(b is bg for bg in backgrounds)]
            keep_light = bool(backgrounds)
            previous_bottom = top
            items = _page_items(content, left, right) if self.geometry else content
            framed = self._isolated_at_bottom(items, size[1]) if self.geometry else []
            # I riquadri a posizione fissa si ancorano IN CIMA alla pagina: ancorati
            # in fondo, su una pagina piena l'ancora scivolava sulla pagina dopo
            # portandosi dietro il riquadro (una pagina in più, quasi vuota).
            if framed:
                self._page_footer(section, framed, size)
            elif self._custom_footers:
                # Dopo una pagina con piè proprio, Word erediterebbe quel piè: le
                # pagine senza testo isolato riprendono quello comune.
                self._page_footer(section, [], size)
            for item in items:
                if any(item is f for f in framed):
                    continue
                if isinstance(item, _Band):
                    previous_bottom = self._band(item, left, right, previous_bottom, content,
                                                 size, keep_light)
                else:
                    previous_bottom = self._block(item, (left, right), previous_bottom, content,
                                                  size, keep_light, True)
            if not page_blocks:
                _tiny(docx.add_paragraph())       # pagina vuota: resta una pagina
        body = docx.element.body
        if len(body) < 2 or body[-2].tag == _qn("w:tbl"):
            # Il corpo deve chiudersi con un paragrafo (Word rifiuta una tabella in coda).
            _tiny(docx.add_paragraph())
        self._furniture()
        docx.save(str(path))

    def _page_footer(self, section, paragraphs: list[Paragraph], size) -> None:
        """Testo isolato in fondo a QUESTA pagina (numero + titolo corrente, fonte,
        nota) → piè di pagina della sua sezione: resta modificabile, sta sotto il
        testo come nell'originale e non può spingere il contenuto su un'altra pagina.

        Ogni pagina è già una sezione: il suo piè è suo, preceduto da quello comune
        (testo ripetuto su tutte le pagine), che altrimenti non si vedrebbe più."""
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        footer = section.footer
        footer.is_linked_to_previous = False
        existing = list(footer.paragraphs)
        sources = [*self.document.footers, *paragraphs]
        width = size[0]
        for index, source in enumerate(sources):
            target = existing[0] if index == 0 and existing else footer.add_paragraph()
            if source.role == "page_number":
                _add_page_field(target)
            else:
                self._runs(target, source, True)
            if source.lines:
                line = source.lines[0]
                center = line.x + line.width / 2
                if abs(center - width / 2) <= 0.08 * width:
                    target.alignment = WD_ALIGN_PARAGRAPH.CENTER
                elif center > width / 2:
                    target.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        self._custom_footers = True

    def _new_section(self, start):
        """Nuova sezione-pagina SENZA paragrafo vuoto di chiusura.

        python-docx chiude la sezione con un paragrafo vuoto in più: su una pagina
        piena fino in fondo quel paragrafo non ci stava, scivolava sulla pagina dopo
        e la sezione seguente ne apriva un'altra: una pagina bianca ogni volta. Qui
        le proprietà della sezione finita vanno nell'ultimo paragrafo vero."""
        from copy import deepcopy

        from docx.oxml import OxmlElement
        from docx.section import Section

        body = self.docx.element.body
        sentinel = body.get_or_add_sectPr()
        ended = deepcopy(sentinel)
        last = sentinel.getprevious()
        if (last is None or last.tag != _qn("w:p") or last.find(_qn("w:pPr")) is not None
                and last.find(_qn("w:pPr")).find(_qn("w:sectPr")) is not None
                or last.xpath(".//w:framePr")):
            paragraph = self.docx.add_paragraph()
            _tiny(paragraph)
            last = paragraph._p
        p_pr = last.get_or_add_pPr()
        change = p_pr.find(_qn("w:pPrChange"))
        if change is not None:
            change.addprevious(ended)
        else:
            p_pr.append(ended)
        for ref in sentinel.findall(_qn("w:headerReference")) + sentinel.findall(_qn("w:footerReference")):
            sentinel.remove(ref)
        section = Section(sentinel, self.docx.part)
        section.start_type = start
        del OxmlElement
        return section

    def _isolated_at_bottom(self, items, page_height: float) -> list:
        """Paragrafi in coda alla pagina da mettere nel piè di pagina della sezione.

        Due casi: righe brevi nella fascia più bassa (numero e titolo corrente,
        editore: un piè "di fatto"), oppure testo nel quarto inferiore staccato di
        molto dal resto (una fonte, una nota a fondo pagina)."""
        def bottom_of(item) -> float:
            return item.bottom if isinstance(item, _Band) else (_box(item) or (0, 1e9, 0, 0))[1]

        def tail_where(condition) -> list:
            tail = []
            for item in reversed(items):
                box = _box(item) if isinstance(item, Paragraph) else None
                if box is None or not condition(item, box):
                    break
                tail.append(item)
            return tail if len(tail) < len(items) else []

        def detached(tail, gap: float) -> bool:
            rest = [item for item in items if not any(item is t for t in tail)]
            rest_bottom = min(bottom_of(r) for r in rest)
            return rest_bottom - max(_box(t)[3] for t in tail) > gap

        band = tail_where(lambda item, box: box[3] <= 0.09 * page_height
                          and len(item.lines) <= 2 and len(item.text) <= 120)
        if band and detached(band, 4.0):
            return band
        low = tail_where(lambda item, box: box[3] <= 0.25 * page_height)
        if low and detached(low, 24.0):
            return low
        return []

    def _has_text_beside(self, image: ImageBlock, page_blocks) -> bool:
        """Testo affiancato all'immagine (stessa fascia verticale, altra x): va ancorata."""
        for block in page_blocks:
            if block is image or isinstance(block, ImageBlock):
                continue
            box = _box(block)
            if box is None:
                continue
            vertical = box[1] < image.top - 2 and image.y + 2 < box[3]
            apart = box[2] <= image.x + 2 or box[0] >= image.right - 2
            if vertical and apart:
                return True
        return False

    def _furniture(self) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        section = self.docx.sections[0]
        width = self.document.page_width_pt
        for container, paragraphs in ((section.header, self.document.headers),
                                      (section.footer, self.document.footers)):
            if not paragraphs:
                continue
            if not container.is_linked_to_previous and container.paragraphs and any(
                    p.text for p in container.paragraphs):
                continue        # la prima pagina ha già un piè suo (che include il comune)
            existing = container.paragraphs
            for index, source in enumerate(paragraphs):
                target = existing[0] if index == 0 and existing else container.add_paragraph()
                if source.role == "page_number":
                    _add_page_field(target)
                else:
                    self._runs(target, source, False)
                if source.lines and self.geometry:
                    line = source.lines[0]
                    center = line.x + line.width / 2
                    if abs(center - width / 2) <= 0.08 * width:
                        target.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    elif center > width / 2:
                        target.alignment = WD_ALIGN_PARAGRAPH.RIGHT


def write_faithful_docx(document: Document, path: Path, report, *, page_breaks: bool = True) -> None:
    _FaithfulWriter(document, page_breaks, report).write(path)


__all__ = ["write_faithful_docx", "word_font"]
