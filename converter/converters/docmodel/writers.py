"""Scrittori IR → formato. Un solo modello, tanti bersagli.

docx  — struttura Word vera (Heading N, liste native, tabelle, immagini) con la
        formattazione dell'originale (font, corpo, colori, rientri); con
        layout="visual" ogni riga diventa un frame posizionato come nel PDF.
md    — GFM: ATX headings, liste, tabelle pipe; immagini inline in data-URI.
txt   — ordine di lettura, tabelle tab-separate, liste con "- ".
html  — HTML semantico con stili inline e immagini incorporate (nessun file
        collaterale: l'output resta UN file).
"""

from __future__ import annotations

import base64
import html as html_lib
from io import BytesIO
from pathlib import Path

from .types import Document, ImageBlock, ListBlock, PageBreak, Paragraph, Run, Table

# --- html / md comuni -------------------------------------------------------

def _escape(text: str) -> str:
    return html_lib.escape(text, quote=False)


def _run_style(run: Run) -> str:
    parts = []
    if run.font:
        parts.append(f"font-family:'{run.font}'")
    if run.size:
        parts.append(f"font-size:{run.size:g}pt")
    if run.color:
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
            text = f'<span style="{style}">{text}</span>'
        if run.italic:
            text = f"<em>{text}</em>"
        if run.bold:
            text = f"<strong>{text}</strong>"
        out.append(text)
    return "".join(out)


def _para_align_style(paragraph: Paragraph) -> str:
    parts = []
    if paragraph.align != "left":
        parts.append(f"text-align:{paragraph.align}")
    if paragraph.indent_pt > 2:
        parts.append(f"margin-left:{paragraph.indent_pt:g}pt")
    return ";".join(parts)


def _md_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _md_runs(runs: list[Run], *, heading: bool = False) -> str:
    out = []
    for run in runs:
        text = _md_escape(run.text)
        if not text.strip():
            out.append(text)
            continue
        if run.italic:
            text = f"*{text}*"
        if run.bold and not heading:   # un heading è già enfatizzato: ** è rumore
            text = f"**{text}**"
        out.append(text)
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
                        font=r.font, size=r.size, color=r.color) for r in chunk)
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
            if (last.bold, last.italic, last.underline, last.font, last.size, last.color) == \
                    (run.bold, run.italic, run.underline, run.font, run.size, run.color):
                last.text += run.text
                continue
        merged.append(run)
    return merged


def _table_text(table: Table) -> list[list[str]]:
    rows = []
    for row in table.rows:
        rows.append([" ".join(p.text for p in cell.paragraphs if p.text.strip()).strip()
                     for cell in row])
    return rows


# --- Markdown ---------------------------------------------------------------

def _md_blocks(document: Document) -> list[str]:
    blocks: list[str] = []
    for block in document.blocks:
        if isinstance(block, Paragraph):
            runs = _paragraph_runs(block)
            if not runs:
                continue
            if block.style == "title":
                blocks.append(f"# {_md_runs(runs, heading=True).strip()}")
            elif block.style == "heading":
                blocks.append(f"{'#' * max(1, min(block.level, 6))} {_md_runs(runs, heading=True).strip()}")
            else:
                text = _md_runs(runs).strip()
                if text:
                    blocks.append(text)
        elif isinstance(block, ListBlock):
            lines = []
            for number, item in enumerate(block.items, start=1):
                indent = "  " * max(0, item.level - 1)
                body = _md_runs(_paragraph_runs(item.paragraph)).strip()
                if not body:
                    continue
                marker = f"{number}. " if block.ordered else "- "
                lines.append(f"{indent}{marker}{body}")
            if lines:
                blocks.append("\n".join(lines))
        elif isinstance(block, Table):
            rows = _table_text(block)
            if not rows:
                continue
            width = max(len(row) for row in rows)
            rows = [row + [""] * (width - len(row)) for row in rows]
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
            payload = base64.b64encode(block.data).decode("ascii")
            blocks.append(f'<img alt="image" src="data:image/{block.ext};base64,{payload}">')
        elif isinstance(block, PageBreak):
            continue
    return blocks


def write_markdown(document: Document, path: Path) -> None:
    path.write_text("\n\n".join(_md_blocks(document)) + "\n", encoding="utf-8")


# --- Testo semplice ---------------------------------------------------------

def write_text(document: Document, path: Path) -> None:
    chunks: list[str] = []
    for block in document.blocks:
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                chunks.append(text)
        elif isinstance(block, ListBlock):
            lines = []
            for number, item in enumerate(block.items, start=1):
                indent = "  " * max(0, item.level - 1)
                marker = f"{number}. " if block.ordered else "- "
                lines.append(f"{indent}{marker}{item.paragraph.text.strip()}")
            if lines:
                chunks.append("\n".join(lines))
        elif isinstance(block, Table):
            rows = _table_text(block)
            if rows:
                chunks.append("\n".join("\t".join(cell for cell in row) for row in rows))
        elif isinstance(block, PageBreak):
            continue
    path.write_text("\n\n".join(chunks) + "\n", encoding="utf-8")


# --- HTML -------------------------------------------------------------------

def _html_table(table: Table) -> str:
    border = "1px solid #999" if table.borders else "none"
    parts = ['<table style="border-collapse:collapse;width:100%">']
    if table.widths_pt:
        parts.append("<colgroup>")
        for width in table.widths_pt:
            parts.append(f'<col style="width:{width:g}pt">')
        parts.append("</colgroup>")
    for index, row in enumerate(table.rows):
        parts.append("<tr>")
        is_header = index < table.header_rows
        tag = "th" if is_header else "td"
        for cell in row:
            content = "<br>".join(_html_runs(_paragraph_runs(p)) for p in cell.paragraphs if p.text.strip())
            align = cell.paragraphs[0].align if cell.paragraphs else "left"
            style = f"border:{border};padding:3pt 6pt;text-align:{align}"
            parts.append(f'<{tag} style="{style}">{content}</{tag}>')
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)


def write_html(document: Document, path: Path) -> None:
    parts = ['<!DOCTYPE html>', '<html><head><meta charset="utf-8">']
    if document.title:
        parts.append(f"<title>{_escape(document.title)}</title>")
    parts.append("</head><body>")
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
            tag = "ol" if block.ordered else "ul"
            parts.append(f"<{tag}>")
            for item in block.items:
                content = _html_runs(_paragraph_runs(item.paragraph))
                indent = f' style="margin-left:{(item.level - 1) * 1.5:g}em"' if item.level > 1 else ""
                parts.append(f"<li{indent}>{content}</li>")
            parts.append(f"</{tag}>")
        elif isinstance(block, Table):
            parts.append(_html_table(block))
        elif isinstance(block, ImageBlock):
            payload = base64.b64encode(block.data).decode("ascii")
            parts.append(f'<p><img alt="image" src="data:image/{block.ext};base64,{payload}"></p>')
    parts.append("</body></html>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


# --- DOCX -------------------------------------------------------------------

def _set_run(docx_run, run: Run) -> None:
    from docx.shared import Pt, RGBColor

    docx_run.text = run.text
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
    if run.color:
        try:
            docx_run.font.color.rgb = RGBColor.from_string(run.color.lstrip("#").upper())
        except ValueError:
            pass


def _add_runs(docx_paragraph, runs: list[Run], fallback: Paragraph | None = None) -> None:
    if not runs and fallback is not None:
        runs = [Run(text=fallback.text, bold=fallback.bold, font=fallback.font, size=fallback.size)]
    for run in runs:
        _set_run(docx_paragraph.add_run(), run)


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


def _write_list(document, block: ListBlock) -> None:
    from docx.shared import Pt

    for item in block.items:
        level = max(1, min(item.level, 3))
        style: str | None = None
        if block.ordered and item.marker.isdigit():
            style = "List Number" if level == 1 else f"List Number {level}"
        elif not block.ordered:
            style = "List Bullet" if level == 1 else f"List Bullet {level}"
        paragraph = None
        if style:
            try:
                paragraph = document.add_paragraph(style=style)
            except KeyError:
                paragraph = None
        if paragraph is None:
            paragraph = document.add_paragraph()
            paragraph.add_run(f"{item.marker} ")
            paragraph.paragraph_format.left_indent = Pt((level - 1) * 18)
        _add_runs(paragraph, _paragraph_runs(item.paragraph), item.paragraph)
        # Gli stili lista hanno un proprio rientro: la geometria del PDF lo
        # sovrascriverebbe in modo sbagliato, quindi qui vale il livello.
        if item.paragraph.align != "left":
            _set_paragraph_format(paragraph, item.paragraph)


def _write_table(document, table: Table) -> None:
    from docx.shared import Pt

    cols = max((len(row) for row in table.rows), default=0)
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
    for row_index, row in enumerate(table.rows):
        for col_index, cell in enumerate(row[:cols]):
            docx_cell = docx_table.cell(row_index, col_index)
            first = True
            for source in cell.paragraphs:
                runs = _paragraph_runs(source)
                if not runs:
                    continue
                target = docx_cell.paragraphs[0] if first else docx_cell.add_paragraph()
                first = False
                _add_runs(target, runs, source)
                _set_paragraph_format(target, source)


def _write_image(document, image: ImageBlock) -> None:
    from docx.shared import Pt

    try:
        if image.width_pt > 1:
            document.add_picture(BytesIO(image.data), width=Pt(image.width_pt))
        else:
            document.add_picture(BytesIO(image.data))
    except Exception:
        pass   # flusso non supportato da Word (es. TIFF CCITT): non blocca il resto


def write_docx(document: Document, path: Path, *, layout: str = "structure",
               page_breaks: bool = True) -> None:
    from docx import Document as DocxDocument
    from docx.enum.section import WD_ORIENT
    from docx.shared import Pt

    docx = DocxDocument()
    section = docx.sections[0]
    section.page_width = Pt(document.page_width_pt)
    section.page_height = Pt(document.page_height_pt)
    if document.page_width_pt > document.page_height_pt:
        section.orientation = WD_ORIENT.LANDSCAPE
    if layout == "visual":
        # I frame sono ancorati alla pagina: i margini devono essere zero,
        # altrimenti Word somma il margine all'offset.
        section.left_margin = section.right_margin = Pt(0)
        section.top_margin = section.bottom_margin = Pt(0)
        _write_visual_docx(docx, document)
        docx.save(str(path))
        return

    section.left_margin = Pt(max(document.margin_left_pt, 28))
    section.right_margin = Pt(max(document.margin_right_pt, 28))

    for block in document.blocks:
        if isinstance(block, Paragraph):
            if block.style == "title":
                paragraph = docx.add_paragraph(style="Title")
            elif block.style == "heading":
                try:
                    paragraph = docx.add_paragraph(style=f"Heading {max(1, min(block.level, 6))}")
                except KeyError:
                    paragraph = docx.add_paragraph()
            else:
                paragraph = docx.add_paragraph()
            _add_runs(paragraph, _paragraph_runs(block), block)
            _set_paragraph_format(paragraph, block)
        elif isinstance(block, ListBlock):
            _write_list(docx, block)
        elif isinstance(block, Table):
            _write_table(docx, block)
        elif isinstance(block, ImageBlock):
            _write_image(docx, block)
        elif isinstance(block, PageBreak) and page_breaks:
            docx.add_page_break()
    # Un corpo docx deve chiudersi con un paragrafo (Word rifiuta una tabella in
    # coda); non ne servono altri tra un blocco e l'altro.
    if document.blocks and isinstance(document.blocks[-1], Table):
        docx.add_paragraph()
    docx.save(str(path))


def _write_visual_docx(docx, document: Document) -> None:
    """Layout visivo: una riga = un frame Word nella posizione esatta del PDF.

    Si perde la rifiusione (Word non ricalcola le righe) in cambio della
    corrispondenza visiva: è il modo onesto di dare "come l'originale" senza
    rasterizzare la pagina. Le tabelle diventano testo posizionato (le celle
    restano ai loro posti, ma non sono una tabella Word).
    """
    for block in document.blocks:
        if isinstance(block, Paragraph):
            for line in block.lines:
                _add_visual_line(docx, line, document.page_height_pt)
        elif isinstance(block, ListBlock):
            for item in block.items:
                for index, line in enumerate(item.paragraph.lines):
                    # Il proiettile è stato tolto dall'IR (serve a md/txt/docx
                    # strutturale): nel layout visivo torna com'era.
                    if index == 0:
                        line = _with_marker(line, item.marker)
                    _add_visual_line(docx, line, document.page_height_pt)
        elif isinstance(block, Table):
            for row in block.rows:
                for cell in row:
                    for paragraph in cell.paragraphs:
                        for line in paragraph.lines:
                            _add_visual_line(docx, line, document.page_height_pt)
        elif isinstance(block, ImageBlock):
            _add_visual_image(docx, block, document.page_height_pt)
        elif isinstance(block, PageBreak):
            # I frame sono ancorati alla pagina: senza il salto, la pagina 2
            # finirebbe sopra la 1.
            docx.add_page_break()


def _with_marker(line, marker: str):
    """Copia della riga con il proiettile/numero davanti al primo run."""
    from .types import Line

    runs = [Run(text=r.text, bold=r.bold, italic=r.italic, underline=r.underline,
                font=r.font, size=r.size, color=r.color) for r in line.runs]
    prefix = f"{marker} "
    if runs:
        runs[0].text = prefix + runs[0].text
    else:
        runs = [Run(text=prefix)]
    return Line(runs=runs, x=line.x, y=line.y, width=line.width, height=line.height)


def _frame(paragraph, x: float, y: float, width: float, height: float, page_height: float) -> None:
    """Ancora il paragrafo a (x, y) misurati dall'alto-sinistra della pagina."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Pt

    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(0)
    fmt.space_after = Pt(0)
    fmt.line_spacing = 1.0
    fmt.left_indent = Pt(0)
    fmt.first_line_indent = Pt(0)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
    p_pr = paragraph._p.get_or_add_pPr()
    element = OxmlElement("w:framePr")
    element.set(qn("w:w"), str(int(max(width + 6, 24) * 20)))
    element.set(qn("w:h"), str(int(max(height, 8) * 20)))
    element.set(qn("w:hRule"), "atLeast")
    element.set(qn("w:x"), str(int(max(x, 0) * 20)))
    element.set(qn("w:y"), str(int(max(page_height - (y + height), 0) * 20)))
    element.set(qn("w:hAnchor"), "page")
    element.set(qn("w:vAnchor"), "page")
    element.set(qn("w:wrap"), "none")
    # L'ordine dei figli di w:pPr è uno schema (non libero): framePr va prima
    # di spacing/ind/jc, altrimenti Word può ignorare il frame o "riparare" il file.
    p_pr.insert_element_before(
        element,
        "w:widowControl", "w:numPr", "w:suppressLineNumbers", "w:pBdr", "w:shd",
        "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap", "w:overflowPunct",
        "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi", "w:adjustRightInd",
        "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing", "w:mirrorIndents",
        "w:suppressOverlap", "w:jc", "w:textDirection", "w:textAlignment",
        "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr",
        "w:sectPr", "w:pPrChange",
    )


def _add_visual_line(docx, line, page_height: float) -> None:
    paragraph = docx.add_paragraph()
    _add_runs(paragraph, line.runs)
    _frame(paragraph, line.x, line.y, line.width, max(line.height, line.size * 1.25), page_height)


def _add_visual_image(docx, image: ImageBlock, page_height: float) -> None:
    from docx.shared import Pt

    paragraph = docx.add_paragraph()
    try:
        paragraph.add_run().add_picture(BytesIO(image.data), width=Pt(image.width_pt or 100))
    except Exception:
        return
    _frame(paragraph, image.x, image.y, image.width_pt, image.height_pt, page_height)
