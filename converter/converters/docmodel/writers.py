"""Scrittori IR → formato. Un solo modello, tanti bersagli.

docx  — struttura Word vera (Heading N, liste native, tabelle, immagini) E
        impaginazione dell'originale (sezioni per pagina, colonne, spaziature):
        lo scrittore è in docx_faithful.py.
md    — GFM: ATX headings, liste, tabelle pipe; immagini inline in data-URI.
txt   — ordine di lettura, tabelle tab-separate, liste con "- ".
html  — HTML semantico con stili inline e immagini incorporate (nessun file
        collaterale: l'output resta UN file).
"""

from __future__ import annotations

import base64
import html as html_lib
import re
from io import BytesIO
from pathlib import Path

from .types import Document, ImageBlock, ListBlock, PageBreak, Paragraph, Run, Table

# --- html / md comuni -------------------------------------------------------

_SAFE_ALIGN = {"left", "center", "right", "justify"}
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SAFE_SCHEMES = {"http", "https", "mailto", "tel"}


def _escape(text: str) -> str:
    return html_lib.escape(text, quote=False)


def _css_string(value: str) -> str:
    """Stringa CSS fra doppi apici, con escape anche di apici e control char.

    Il nome di un font viene dal documento, quindi è input non fidato: senza
    questo escape un font chiamato `x" onmouseover="...` chiudeva l'attributo
    `style` e aggiungeva un evento JavaScript all'HTML generato.
    """
    out = []
    for char in value:
        if char in ('\\', '"'):
            out.append("\\" + char)
        elif char == "\r":
            out.append("\\d ")
        elif char == "\n":
            out.append("\\a ")
        elif ord(char) < 32 or ord(char) == 127:
            out.append(f"\\{ord(char):x} ")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _safe_href(url: str) -> str:
    """Solo link apribili senza eseguire codice nel browser."""
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith(("#", "/", "./", "../")):
        return url
    from urllib.parse import urlsplit

    try:
        scheme = urlsplit(url).scheme.lower()
    except ValueError:
        return ""
    return url if scheme in _SAFE_SCHEMES else ""


def _readable_color(color: str) -> str:
    """Il colore del testo, o "" se è quasi bianco.

    Nel PDF il testo chiaro sta sopra una foto o un riquadro scuro; nel file
    scritto lo sfondo non sta dietro al testo (è un'immagine a parte, o non c'è),
    e il testo bianco diventava invisibile sulla pagina bianca.
    """
    if not color or not _COLOR_RE.match(color):
        return ""
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    luminance = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    return "" if luminance > 0.85 else color


def _furniture(paragraphs: list[Paragraph]) -> list[Paragraph]:
    """Testatina/piè di pagina per i formati senza pagine: il numero di pagina
    (ruolo "page_number") lì non significa nulla e si omette."""
    return [p for p in paragraphs if p.role != "page_number"]


def _run_style(run: Run) -> str:
    parts = []
    if run.font:
        parts.append(f"font-family:{_css_string(run.font)}")
    if run.size:
        parts.append(f"font-size:{run.size:g}pt")
    if _readable_color(run.color):
        parts.append(f"color:{run.color}")
    return ";".join(parts)


def _html_runs(runs: list[Run]) -> str:
    out = []
    for run in runs:
        text = _escape(run.text)
        if not text:
            continue
        style = _run_style(run)
        if style:
            # Anche il valore CSS va escapato come attributo: `_css_string`
            # impedisce di uscire dalla stringa CSS, questo dalla stringa HTML.
            text = f'<span style="{html_lib.escape(style, quote=True)}">{text}</span>'
        if run.italic:
            text = f"<em>{text}</em>"
        if run.bold:
            text = f"<strong>{text}</strong>"
        href = _safe_href(run.link)
        if href:
            text = f'<a href="{html_lib.escape(href, quote=True)}">{text}</a>'
        out.append(text)
    return "".join(out)


def _para_align_style(paragraph: Paragraph) -> str:
    parts = []
    if paragraph.align != "left":
        parts.append(f"text-align:{paragraph.align}")
    if paragraph.indent_pt > 2:
        parts.append(f"margin-left:{paragraph.indent_pt:g}pt")
    return ";".join(parts)


# Caratteri che Markdown interpreta ovunque nella riga (CommonMark/GFM).
_MD_INLINE = re.compile(r"([\\`*_\[\]<>|~&])")
# Costrutti che contano solo a inizio riga: titolo, elenco, citazione, setext.
_MD_LINE_START = re.compile(r"^(\s*)([#+\-=>])")
_MD_ORDERED_START = re.compile(r"^(\s*\d{1,9})([.)])")


def _md_escape(text: str) -> str:
    """Testo letterale in Markdown: niente enfasi, link, HTML o celle spezzate.

    Prima si escapavano solo `\\ | [ ]`: "# riga" diventava un titolo, `*x*`
    corsivo, `<b>` HTML vivo e il `|` di una cella aggiungeva una colonna."""
    return _MD_INLINE.sub(r"\\\1", text).replace("\r", " ").replace("\n", " ")


def _md_block_escape(text: str) -> str:
    """Escape dei costrutti di blocco a inizio paragrafo (dopo l'escape inline)."""
    text = _MD_LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text, count=1)
    return _MD_ORDERED_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text, count=1)


def _md_heading_text(text: str) -> str:
    """Un titolo ATX che finisce con '#' perderebbe il cancelletto (sequenza di
    chiusura): "C #" diventava "C"."""
    return text[:-1] + "\\#" if text.endswith("#") and not text.endswith("\\#") else text


def _md_url(url: str) -> str:
    return (url.replace("<", "%3C").replace(">", "%3E")
            .replace(" ", "%20").replace("\n", "%0A"))


def _md_runs(runs: list[Run], *, heading: bool = False) -> str:
    out = []
    for run in runs:
        text = _md_escape(run.text)
        if not text.strip():
            out.append(text)
            continue
        # Gli spazi ai bordi vanno FUORI dai delimitatori: "**parola **" non è
        # grassetto in CommonMark e il lettore vedeva gli asterischi.
        core = text.strip()
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        if run.italic:
            core = f"*{core}*"
        if run.bold and not heading:   # un heading è già enfatizzato: ** è rumore
            core = f"**{core}**"
        href = _safe_href(run.link)
        if href:
            core = f"[{core}](<{_md_url(href)}>)"
        out.append(lead + core + trail)
    return "".join(out)


def _paragraph_runs(paragraph: Paragraph) -> list[Run]:
    """Run del paragrafo con le righe riunite (sillabazione ricucita)."""
    runs: list[Run] = []
    for index, line in enumerate(paragraph.lines):
        chunk = line.runs
        if index > 0 and runs and runs[-1].text.endswith("-") and chunk and chunk[0].text[:1].islower():
            runs[-1].text = runs[-1].text[:-1]
        elif index > 0 and runs and chunk:
            runs[-1].text = runs[-1].text.rstrip() + " "
        runs.extend(Run(text=r.text, bold=r.bold, italic=r.italic, underline=r.underline,
                        font=r.font, size=r.size, color=r.color, link=r.link) for r in chunk)
    if runs:
        runs[0].text = runs[0].text.lstrip()
        runs[-1].text = runs[-1].text.rstrip()
    # Coalesci run adiacenti con lo stesso stile: Word non deve mostrare una
    # "spezzatura" a ogni riga del PDF (stessa formattazione = stesso run).
    merged: list[Run] = []
    for run in runs:
        if not run.text:
            continue
        if merged:
            last = merged[-1]
            if (last.bold, last.italic, last.underline, last.font, last.size, last.color,
                    last.link) == (run.bold, run.italic, run.underline, run.font, run.size,
                                   run.color, run.link):
                last.text += run.text
                continue
        merged.append(run)
    return merged


def _table_text(table: Table) -> list[list[str]]:
    return table.text_grid()


def _table_has_text(table: Table) -> bool:
    """Una tabella senza testo (es. righe con celle dentro w:sdt) non va scritta.

    Markdown non può rappresentare zero colonne: emetterebbe un separatore
    senza `---`, cioè una tabella invalida. Lo snapshot del corpus la conserva
    comunque, perché la struttura delle righe è un dato.
    """
    return any(paragraph.text.strip()
               for row in table.rows for cell in row for paragraph in cell.paragraphs)


# --- Immagini e liste comuni -------------------------------------------------

# Formati che un browser (e quindi md/html) mostra davvero, col MIME corretto.
_WEB_IMAGE_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
    "bmp": "image/bmp", "webp": "image/webp", "svg": "image/svg+xml",
}


def _png_from(data: bytes) -> bytes | None:
    """Ricodifica in PNG un flusso che il browser/Word non sa mostrare (TIFF,
    JPEG 2000, EMF/WMF dove Pillow li legge). None se Pillow non ci riesce."""
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            image.load()
            if image.mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
                image = image.convert("RGBA" if "A" in image.mode else "RGB")
            buffer = BytesIO()
            image.save(buffer, format="PNG")
        return buffer.getvalue()
    except Exception:
        return None


def _web_image(image: ImageBlock) -> tuple[str, bytes] | None:
    """(MIME, dati) sicuri per un data-URI, o None se l'immagine va scartata.

    L'estensione arriva dal file (per il DOCX anche dal nome della parte ZIP):
    usarla così com'era nel data-URI permetteva a un nome con `"` di uscire
    dall'attributo `src` e iniettare HTML, e dava MIME sbagliati (image/svg,
    image/jpg, image/tif) che il browser non mostra."""
    ext = (image.ext or "").lower()
    mime = _WEB_IMAGE_MIME.get(ext)
    if mime:
        return mime, image.data
    png = _png_from(image.data)
    return ("image/png", png) if png else None


class WriteReport:
    """Ciò che un writer non ha potuto rappresentare (per il verdetto a runtime)."""

    def __init__(self) -> None:
        self.dropped_images = 0


def _marker_start(marker: str) -> int:
    """Numero di partenza da un marcatore ("3." → 3, "(2)" → 2); 1 se non numerico."""
    digits = "".join(char for char in marker if char.isdigit())
    return int(digits) if digits and len(digits) <= 6 else 1


def _list_entries(block: ListBlock, body_of) -> list[tuple[int, str, str]]:
    """(rientro, marcatore, corpo) per ogni voce, con numerazione per livello.

    La numerazione riparte a ogni livello più profondo e prosegue al rientro:
    prima un contatore unico per tutto il blocco dava "1. a · 2. a.i · 3. b".
    Il rientro di una sotto-voce è la colonna del contenuto del genitore, come
    vuole CommonMark (2 spazi sotto "1. " non annidano)."""
    entries: list[tuple[int, str, str]] = []
    counters: dict[int, int] = {}
    stack: list[tuple[int, int]] = []        # (livello, colonna del contenuto)
    for item in block.items:
        body = body_of(item)
        if not body:
            continue
        level = max(1, item.level)
        for deeper in [key for key in counters if key > level]:
            del counters[deeper]
        while stack and stack[-1][0] >= level:
            stack.pop()
        indent = stack[-1][1] if stack else 0
        if item.is_ordered(block):
            # La prima voce di un livello parte dal numero dell'originale ("3."
            # in un PDF che continua un elenco), le successive incrementano.
            counters[level] = counters[level] + 1 if level in counters else _marker_start(item.marker)
            marker = f"{counters[level]}. "
        else:
            marker = "- "
        stack.append((level, indent + len(marker)))
        entries.append((indent, marker, body))
    return entries


# --- Markdown ---------------------------------------------------------------

def _md_blocks(document: Document, report: WriteReport) -> list[str]:
    blocks: list[str] = []
    for paragraph in _furniture(document.headers):
        text = _md_block_escape(_md_runs(_paragraph_runs(paragraph)).strip())
        if text:
            blocks.append(text)
    for block in document.blocks:
        if isinstance(block, Paragraph):
            runs = _paragraph_runs(block)
            if not runs:
                continue
            if block.style in ("title", "heading"):
                level = 1 if block.style == "title" else max(1, min(block.level, 6))
                text = _md_heading_text(_md_runs(runs, heading=True).strip())
                if text:
                    blocks.append(f"{'#' * level} {text}")
            else:
                text = _md_block_escape(_md_runs(runs).strip())
                if text:
                    blocks.append(text)
        elif isinstance(block, ListBlock):
            entries = _list_entries(
                block, lambda item: _md_block_escape(_md_runs(_paragraph_runs(item.paragraph)).strip()))
            if entries:
                blocks.append("\n".join(f"{' ' * indent}{marker}{body}"
                                         for indent, marker, body in entries))
        elif isinstance(block, Table):
            rows = _table_text(block)
            if not rows or not _table_has_text(block):
                continue
            width = max(len(row) for row in rows)
            rows = [[_md_escape(cell) for cell in row] + [""] * (width - len(row)) for row in rows]
            if block.header_rows:
                head, body = rows[: block.header_rows], rows[block.header_rows:]
            else:
                # GFM richiede sempre una riga d'intestazione: senza header
                # riconosciuto la prima riga della tabella fa da intestazione
                # (una riga vuota sarebbe una riga finta in più).
                head, body = rows[:1], rows[1:]
            lines = ["| " + " | ".join(cell for cell in row) + " |" for row in head]
            lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
            lines += ["| " + " | ".join(cell for cell in row) + " |" for row in body]
            blocks.append("\n".join(lines))
        elif isinstance(block, ImageBlock):
            web = _web_image(block)
            if web is None:
                report.dropped_images += 1
                continue
            mime, data = web
            payload = base64.b64encode(data).decode("ascii")
            blocks.append(f'<img alt="image" src="data:{mime};base64,{payload}">')
        elif isinstance(block, PageBreak):
            continue
    for paragraph in _furniture(document.footers):
        text = _md_block_escape(_md_runs(_paragraph_runs(paragraph)).strip())
        if text:
            blocks.append(text)
    return blocks


def write_markdown(document: Document, path: Path) -> WriteReport:
    report = WriteReport()
    path.write_text("\n\n".join(_md_blocks(document, report)) + "\n", encoding="utf-8")
    return report


# --- Testo semplice ---------------------------------------------------------

def write_text(document: Document, path: Path) -> WriteReport:
    chunks: list[str] = []
    for paragraph in _furniture(document.headers):
        text = paragraph.text.strip()
        if text:
            chunks.append(text)
    for block in document.blocks:
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                chunks.append(text)
        elif isinstance(block, ListBlock):
            entries = _list_entries(block, lambda item: item.paragraph.text.strip())
            if entries:
                chunks.append("\n".join(f"{' ' * indent}{marker}{body}"
                                         for indent, marker, body in entries))
        elif isinstance(block, Table):
            rows = _table_text(block)
            if rows and _table_has_text(block):
                chunks.append("\n".join("\t".join(cell for cell in row) for row in rows))
        elif isinstance(block, PageBreak):
            continue
    for paragraph in _furniture(document.footers):
        text = paragraph.text.strip()
        if text:
            chunks.append(text)
    path.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")
    report = WriteReport()
    # Il testo semplice non ha immagini per definizione (lo dice il verdetto):
    # non sono "scartate per formato", quindi non si contano.
    return report


# --- HTML -------------------------------------------------------------------

def _html_table(table: Table) -> str:
    border = "1px solid #999" if table.borders else "none"
    cols = table.col_count
    parts = ['<table style="border-collapse:collapse;width:100%">']
    if table.widths_pt:
        parts.append("<colgroup>")
        for width in table.widths_pt:
            parts.append(f'<col style="width:{width:g}pt">')
        parts.append("</colgroup>")
    covered: set[tuple[int, int]] = set()
    for row_index, row in enumerate(table.rows):
        parts.append("<tr>")
        is_header = row_index < table.header_rows
        tag = "th" if is_header else "td"
        by_col: dict[int, object] = {}
        fallback = 0
        for cell in row:
            col = cell.col if cell.col >= 0 else fallback
            by_col[col] = cell
            fallback = col + max(1, cell.col_span)
        for col in range(cols):
            if (row_index, col) in covered:
                continue
            cell = by_col.get(col)
            if cell is None:
                style = f"border:{border};padding:3pt 6pt"
                parts.append(f'<{tag} style="{style}"></{tag}>')
                continue
            row_span = max(1, cell.row_span)
            col_span = max(1, cell.col_span)
            for rr in range(row_index, row_index + row_span):
                for cc in range(col, col + col_span):
                    covered.add((rr, cc))
            content = "<br>".join(_html_runs(_paragraph_runs(p))
                                  for p in cell.paragraphs if p.text.strip())
            align = cell.paragraphs[0].align if cell.paragraphs else "left"
            if align not in _SAFE_ALIGN:
                align = "left"
            style = f"border:{border};padding:3pt 6pt;text-align:{align}"
            span_attr = ""
            if row_span > 1:
                span_attr += f' rowspan="{row_span}"'
            if col_span > 1:
                span_attr += f' colspan="{col_span}"'
            parts.append(f'<{tag}{span_attr} style="{style}">{content}</{tag}>')
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def _html_list(block: ListBlock) -> str:
    """Elenchi annidati veri (<ol>/<ul> dentro <li>), anche misti.

    Prima ogni voce era un <li> dello stesso elenco con un margine finto: il
    livello si vedeva ma non esisteva, e una sotto-voce puntata in un elenco
    numerato riceveva un numero."""
    out: list[str] = []
    stack: list[tuple[int, str]] = []      # (livello, tag) degli elenchi aperti
    for item in block.items:
        content = _html_runs(_paragraph_runs(item.paragraph))
        level = max(1, item.level)
        tag = "ol" if item.is_ordered(block) else "ul"
        while stack and stack[-1][0] > level:
            out.append(f"</li></{stack.pop()[1]}>")
        if stack and stack[-1][0] == level:
            if stack[-1][1] == tag:
                out.append("</li>")
            else:
                out.append(f"</li></{stack.pop()[1]}>")
        if not stack or stack[-1][0] < level:
            out.append(f"<{tag}>")
            stack.append((level, tag))
        out.append(f"<li>{content}")
    while stack:
        out.append(f"</li></{stack.pop()[1]}>")
    return "".join(out)


def write_html(document: Document, path: Path) -> WriteReport:
    report = WriteReport()
    parts = ['<!DOCTYPE html>', '<html><head><meta charset="utf-8">']
    if document.title:
        parts.append(f"<title>{_escape(document.title)}</title>")
    parts.append("</head><body>")
    if document.headers:
        parts.append("<header>")
        for paragraph in _furniture(document.headers):
            content = _html_runs(_paragraph_runs(paragraph))
            if content.strip():
                parts.append(f"<p>{content}</p>")
        parts.append("</header>")
    for block in document.blocks:
        if isinstance(block, Paragraph):
            content = _html_runs(_paragraph_runs(block))
            if not content.strip():
                continue
            if block.style == "title":
                parts.append(f"<h1>{content}</h1>")
            elif block.style == "heading":
                level = max(1, min(block.level, 6))
                parts.append(f"<h{level}>{content}</h{level}>")
            else:
                style = _para_align_style(block)
                attr = f' style="{style}"' if style else ""
                parts.append(f"<p{attr}>{content}</p>")
        elif isinstance(block, ListBlock):
            parts.append(_html_list(block))
        elif isinstance(block, Table):
            if _table_has_text(block):
                parts.append(_html_table(block))
        elif isinstance(block, ImageBlock):
            web = _web_image(block)
            if web is None:
                report.dropped_images += 1
                continue
            mime, data = web
            payload = base64.b64encode(data).decode("ascii")
            parts.append(f'<p><img alt="image" src="data:{mime};base64,{payload}"></p>')
    if document.footers:
        parts.append("<footer>")
        for paragraph in _furniture(document.footers):
            content = _html_runs(_paragraph_runs(paragraph))
            if content.strip():
                parts.append(f"<p>{content}</p>")
        parts.append("</footer>")
    parts.append("</body></html>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return report


# --- DOCX -------------------------------------------------------------------

_XML_INVALID_RE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _set_run(docx_run, run: Run, *, keep_light: bool = False) -> None:
    from docx.shared import Pt, RGBColor

    # Caratteri di controllo (NUL, form feed…) arrivano da PDF malformati: in XML
    # non sono ammessi e python-docx rifiutava l'intero documento.
    docx_run.text = _XML_INVALID_RE.sub("", run.text)
    if run.font:
        from docx.oxml.ns import qn

        docx_run.font.name = run.font
        fonts = docx_run._element.get_or_add_rPr().get_or_add_rFonts()
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            fonts.set(qn(attr), run.font)
    if run.size:
        docx_run.font.size = Pt(run.size)
    if run.bold:
        docx_run.font.bold = True
    if run.italic:
        docx_run.font.italic = True
    if run.underline:
        docx_run.font.underline = True
    if (run.color and _COLOR_RE.match(run.color)) if keep_light else _readable_color(run.color):
        # keep_light: dietro al testo c'è lo sfondo (riquadro colorato, foto della
        # pagina), quindi il bianco resta leggibile e va conservato.
        try:
            docx_run.font.color.rgb = RGBColor.from_string(run.color.lstrip("#").upper())
        except ValueError:
            pass


def _apply_hyperlink(docx_paragraph, docx_run, url: str) -> None:
    """Trasforma un run appena aggiunto in `w:hyperlink` con relazione esterna."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    href = _safe_href(url)
    if not href or href.startswith("#"):
        return          # un'ancora non ha una destinazione nel nuovo documento
    try:
        r_id = docx_paragraph.part.relate_to(href, RT.HYPERLINK, is_external=True)
    except Exception:
        return
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    element = docx_run._r
    element.addprevious(link)
    link.append(element)


def _add_runs(docx_paragraph, runs: list[Run], fallback: Paragraph | None = None) -> None:
    if not runs and fallback is not None:
        runs = [Run(text=fallback.text, bold=fallback.bold, font=fallback.font, size=fallback.size)]
    for run in runs:
        docx_run = docx_paragraph.add_run()
        _set_run(docx_run, run)
        if run.link:
            _apply_hyperlink(docx_paragraph, docx_run, run.link)


def _set_paragraph_format(paragraph, source: Paragraph) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    alignment = {"left": WD_ALIGN_PARAGRAPH.LEFT, "center": WD_ALIGN_PARAGRAPH.CENTER,
                 "right": WD_ALIGN_PARAGRAPH.RIGHT, "justify": WD_ALIGN_PARAGRAPH.JUSTIFY}
    paragraph.alignment = alignment.get(source.align, WD_ALIGN_PARAGRAPH.LEFT)
    if source.indent_pt > 2:
        paragraph.paragraph_format.left_indent = Pt(source.indent_pt)
    if source.space_before_pt:
        paragraph.paragraph_format.space_before = Pt(source.space_before_pt)
    if source.space_after_pt:
        paragraph.paragraph_format.space_after = Pt(source.space_after_pt)


def _restarted_num_id(document, style_name: str, cache: dict[str, int],
                      start: int = 1) -> int | None:
    """numId nuovo che riparte da 1 per lo stile lista dato (uno per ListBlock).

    Tutti i paragrafi "List Number" del template condividono UN numId: senza
    un'istanza nuova il secondo elenco numerato proseguiva (3, 4…) invece di
    ripartire da 1."""
    if style_name in cache:
        return cache[style_name]
    try:
        style = document.styles[style_name]
        num_pr = style.element.pPr.numPr
        numbering = document.part.numbering_part.element
        abstract_id = numbering.num_having_numId(num_pr.numId.val).abstractNumId.val
        ilvl = num_pr.ilvl.val if num_pr.ilvl is not None else 0
        num = numbering.add_num(abstract_id)
        num.add_lvlOverride(ilvl=ilvl).add_startOverride(max(1, start))
        cache[style_name] = num.numId
    except Exception:
        return None    # template senza numerazione: resta quella dello stile
    return cache[style_name]


def _clear_cell(docx_cell) -> None:
    """Svuota una cella lasciando un solo paragrafo vuoto (requisito Word)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc = docx_cell._tc
    for child in list(tc):
        if child.tag != qn("w:tcPr"):
            tc.remove(child)
    tc.append(OxmlElement("w:p"))


def _fill_cell(docx_cell, cell) -> None:
    _clear_cell(docx_cell)
    first = True
    for source in cell.paragraphs:
        runs = _paragraph_runs(source)
        if not runs:
            continue
        target = docx_cell.paragraphs[0] if first else docx_cell.add_paragraph()
        first = False
        _add_runs(target, runs, source)
        _set_paragraph_format(target, source)


def _write_table(document, table: Table) -> None:
    from docx.shared import Pt

    cols = table.col_count
    if not cols or not table.rows:
        return
    docx_table = document.add_table(rows=len(table.rows), cols=cols)
    docx_table.autofit = False
    if table.borders:
        try:
            docx_table.style = "Table Grid"
        except KeyError:
            pass
    if table.widths_pt and len(table.widths_pt) >= cols:
        for index in range(cols):
            width = Pt(table.widths_pt[index])
            docx_table.columns[index].width = width
            for row in docx_table.rows:
                row.cells[index].width = width
    last_row = len(table.rows) - 1
    for row_index, row in enumerate(table.rows):
        fallback = 0
        for cell in row:
            col = cell.col if cell.col >= 0 else fallback
            col_span = max(1, cell.col_span)
            row_span = max(1, cell.row_span)
            fallback = col + col_span
            if col >= cols:
                continue
            end_col = min(cols - 1, col + col_span - 1)
            end_row = min(last_row, row_index + row_span - 1)
            target = docx_table.cell(row_index, col)
            if end_row > row_index or end_col > col:
                target = target.merge(docx_table.cell(end_row, end_col))
            _fill_cell(target, cell)


def write_docx(document: Document, path: Path, *, page_breaks: bool = True) -> WriteReport:
    """DOCX modificabile E impaginato come l'originale (vedi docx_faithful)."""
    from .docx_faithful import write_faithful_docx

    report = WriteReport()
    write_faithful_docx(document, path, report, page_breaks=page_breaks)
    return report
