"""Lettore DOCX → IR (python-docx, MIT).

Serve alle coppie docx→md/txt: pandoc le copre già, ma produce "grid table" e
appiattisce i titoli nel front-matter. Qui la struttura Word (Heading N, liste,
tabelle con header) arriva all'IR e quindi a un Markdown GFM pulito.
"""

from __future__ import annotations

from pathlib import Path

from .types import Document, Line, ListBlock, ListItem, Paragraph, Run, Table, TableCell


def _run_style(run, style) -> tuple[bool, bool, str, float, str]:
    """(bold, italic, family, size_pt, #RRGGBB) risolvendo l'eredità dallo stile."""
    bold = run.bold if run.bold is not None else bool(style and style.font.bold)
    italic = run.italic if run.italic is not None else bool(style and style.font.italic)
    family = run.font.name or (style.font.name if style and style.font.name else "")
    size = 0.0
    if run.font.size is not None:
        size = float(run.font.size.pt)
    elif style is not None and style.font.size is not None:
        size = float(style.font.size.pt)
    color = ""
    try:
        rgb = run.font.color.rgb
        if rgb is not None:
            color = f"#{rgb}"
    except (AttributeError, ValueError):
        color = ""
    return bold, italic, family, size, color


def _paragraph(docx_paragraph) -> tuple[Paragraph, str, int]:
    """(paragrafo IR, nome stile, livello) da un paragrafo Word."""
    style = docx_paragraph.style
    style_name = style.name if style is not None else ""
    runs: list[Run] = []
    for run in docx_paragraph.runs:
        text = run.text
        if not text:
            continue
        bold, italic, family, size, color = _run_style(run, style)
        runs.append(Run(text=text, bold=bold, italic=italic, font=family, size=size, color=color))

    level = 0
    low = style_name.lower()
    if low == "title":
        paragraph = Paragraph(lines=[Line(runs=runs)], style="title", level=1)
    elif low.startswith("heading "):
        try:
            level = int(low.split()[-1])
        except ValueError:
            level = 1
        paragraph = Paragraph(lines=[Line(runs=runs)], style="heading", level=level)
    else:
        paragraph = Paragraph(lines=[Line(runs=runs)])
    return paragraph, style_name, level


def _cell_paragraphs(docx_cell) -> list[Paragraph]:
    paragraphs = []
    for docx_paragraph in docx_cell.paragraphs:
        paragraph, _, _ = _paragraph(docx_paragraph)
        if paragraph.text.strip():
            paragraphs.append(paragraph)
    return paragraphs


def _row_cells(docx_row) -> list[TableCell]:
    """Celle della riga, senza le copie delle celle unite orizzontalmente.

    python-docx restituisce lo stesso `w:tc` una volta per colonna coperta: una
    intestazione unita su 63 colonne diventerebbe 63 celle con lo stesso testo.
    """
    cells: list[TableCell] = []
    previous_tc = None
    for docx_cell in docx_row.cells:
        if docx_cell._tc is previous_tc:
            continue
        cells.append(TableCell(paragraphs=_cell_paragraphs(docx_cell)))
        previous_tc = docx_cell._tc
    return cells


def _numbering_info(docx_paragraph) -> tuple[bool, int] | None:
    """(ordinato, livello) dal `w:numPr`, risolvendo il formato in numbering.xml.

    Word non mette "List Bullet/Number" nello stile: la maggior parte delle liste
    reali è `List Paragraph` + `w:numPr`. Senza questo, ogni voce diventa un
    paragrafo e la struttura sparisce.
    """
    from docx.oxml.ns import qn

    p_pr = docx_paragraph._p.pPr
    num_pr = p_pr.numPr if p_pr is not None else None
    if num_pr is None or num_pr.numId is None:
        return None
    num_id = num_pr.numId.val
    if not num_id:                      # numId 0 = numerazione rimossa
        return None
    level = int(num_pr.ilvl.val) + 1 if num_pr.ilvl is not None else 1

    ordered = True
    try:
        numbering = docx_paragraph.part.numbering_part.element
    except (AttributeError, NotImplementedError):
        return ordered, level

    abstract_id = None
    for num in numbering.findall(qn("w:num")):
        if num.get(qn("w:numId")) == str(num_id):
            ref = num.find(qn("w:abstractNumId"))
            abstract_id = ref.get(qn("w:val")) if ref is not None else None
            break
    if abstract_id is None:
        return ordered, level

    for abstract in numbering.findall(qn("w:abstractNum")):
        if abstract.get(qn("w:abstractNumId")) != abstract_id:
            continue
        formats: dict[str, str] = {}
        for lvl in abstract.findall(qn("w:lvl")):
            fmt = lvl.find(qn("w:numFmt"))
            formats[lvl.get(qn("w:ilvl"))] = fmt.get(qn("w:val")) if fmt is not None else ""
        # Un livello non dichiarato eredita il formato del primo: è quello che
        # fa Word quando il documento definisce solo il livello 0.
        fmt = formats.get(str(level - 1), formats.get("0", ""))
        if fmt == "bullet":
            ordered = False
        break
    return ordered, level


def read_docx(path: Path) -> Document:
    """DOCX → Document. Solleva ValueError con messaggio pulito se illeggibile."""
    from docx import Document as DocxDocument
    from docx.table import Table as DocxTable
    from docx.text.paragraph import Paragraph as DocxParagraph

    try:
        docx = DocxDocument(str(path))
    except Exception as exc:
        raise ValueError(f"Could not read this DOCX: {exc}") from exc

    document = Document(source_format="docx")
    section = docx.sections[0] if docx.sections else None
    if section is not None:
        # Un DOCX può non dichiarare la sezione (Bug60341): None non è 0.
        if section.page_width is not None:
            document.page_width_pt = float(section.page_width.pt)
        if section.page_height is not None:
            document.page_height_pt = float(section.page_height.pt)
        if section.left_margin is not None:
            document.margin_left_pt = float(section.left_margin.pt)
        if section.right_margin is not None:
            document.margin_right_pt = float(section.right_margin.pt)

    pending_list: ListBlock | None = None

    def flush_list() -> None:
        nonlocal pending_list
        pending_list = None

    for child in docx.element.body.iterchildren():
        if not isinstance(child.tag, str):
            continue                     # commenti/PI: lxml usa una funzione come tag
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "tbl":
            flush_list()
            table = DocxTable(child, docx)
            rows = [_row_cells(row) for row in table.rows]
            widths: list[float] = []
            try:
                for column in table.columns:
                    try:
                        widths.append(float(column.width.pt) if column.width else 0.0)
                    except (AttributeError, ValueError):
                        widths.append(0.0)
            except Exception:
                # Tabelle malformate senza w:tblGrid (Bug66263): la griglia
                # testuale resta valida, si perdono solo le larghezze.
                widths = []
            document.blocks.append(Table(rows=rows, borders=True, header_rows=1,
                                         widths_pt=[w for w in widths if w] or []))
            continue
        if tag != "p":
            continue

        paragraph, style_name, _ = _paragraph(DocxParagraph(child, docx))
        if not paragraph.text.strip():
            continue
        low = style_name.lower()
        if low == "title" or low.startswith("heading "):
            flush_list()
            document.blocks.append(paragraph)
            continue

        info = _numbering_info(DocxParagraph(child, docx))
        if info is None:
            is_bullet_style = low.startswith("list bullet")
            is_number_style = low.startswith("list number")
            if is_bullet_style or is_number_style:
                suffix = low.split()[-1]
                level = int(suffix) if suffix.isdigit() else 1
                info = (is_number_style, level)

        if info is not None:
            ordered, level = info
            if pending_list is None or pending_list.ordered != ordered:
                pending_list = ListBlock(ordered=ordered)
                document.blocks.append(pending_list)
            marker = "•" if not ordered else "1"
            pending_list.items.append(ListItem(marker=marker, level=level, paragraph=paragraph))
            continue

        flush_list()
        document.blocks.append(paragraph)

    if document.title == "":
        for block in document.blocks:
            if isinstance(block, Paragraph) and block.style == "title":
                document.title = block.text
                break
    return document
