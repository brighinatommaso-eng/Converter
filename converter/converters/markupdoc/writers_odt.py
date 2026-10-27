"""Scrittore ODT dall'albero markup (odfpy, già dipendenza per gli ODS).

Stili con nome che LibreOffice riconosce (Heading N, Quotations, Preformatted
Text, Caption), elenchi puntati/numerati annidati con numerazione che riparte,
tabelle con celle unite, immagini incorporate nel pacchetto.
"""

from __future__ import annotations

from io import BytesIO

from .common import WriteReport, as_blocks, heading_level, segments, table_grid
from .tree import HEADINGS, MarkupDocument, Node

_MAX_WIDTH_CM = 16.0


def add_text(element, text: str) -> None:
    """Testo ODF con gli spazi che contano: ODF fonde gli spazi multipli e ignora
    quelli a inizio paragrafo (come l'HTML), quindi vanno scritti come text:s.
    teletype.addTextToElement lasciava il primo spazio letterale: un rientro di
    codice di 4 spazi diventava 3."""
    import re

    from odf.text import LineBreak, S, Tab

    pos = 0
    for match in re.finditer(r"\n|\t| +", text):
        if match.start() > pos:
            element.addText(text[pos:match.start()])
        token = match.group()
        if token == "\n":
            element.addElement(LineBreak())
        elif token == "\t":
            element.addElement(Tab())
        elif len(token) > 1 or match.start() == 0 or text[match.start() - 1] == "\n":
            element.addElement(S(c=str(len(token))))
        else:
            element.addText(" ")
        pos = match.end()
    if pos < len(text):
        element.addText(text[pos:])
_HEADING_SIZES = {1: "20pt", 2: "16pt", 3: "14pt", 4: "12pt", 5: "11pt", 6: "10pt"}


class _OdtWriter:
    def __init__(self, document: MarkupDocument, report: WriteReport) -> None:
        from odf.opendocument import OpenDocumentText

        self.document = document
        self.report = report
        self.odt = OpenDocumentText()
        self.text_styles: dict[frozenset[str], object] = {}
        self.table_count = 0
        self.image_count = 0
        self._styles()
        if document.title:
            from odf.dc import Title

            self.odt.meta.addElement(Title(text=document.title))

    # --- stili ---------------------------------------------------------------------
    def _styles(self) -> None:
        from odf.style import (
            FontFace,
            GraphicProperties,
            ListLevelProperties,
            ParagraphProperties,
            Style,
            TableCellProperties,
            TextProperties,
        )
        from odf.text import ListLevelStyleBullet, ListLevelStyleNumber, ListStyle

        odt = self.odt
        odt.fontfacedecls.addElement(FontFace(name="Monospace", fontfamily="'Liberation Mono'",
                                              fontfamilygeneric="modern", fontpitch="fixed"))
        styles = odt.styles
        self.standard = Style(name="Standard", family="paragraph")
        styles.addElement(self.standard)
        self.headings = {}
        for level, size in _HEADING_SIZES.items():
            style = Style(name=f"Heading_20_{level}", displayname=f"Heading {level}",
                          family="paragraph", parentstylename="Standard", nextstylename="Standard",
                          defaultoutlinelevel=str(level))
            style.addElement(ParagraphProperties(margintop="0.42cm", marginbottom="0.21cm",
                                                 keepwithnext="always"))
            style.addElement(TextProperties(fontsize=size, fontweight="bold"))
            styles.addElement(style)
            self.headings[level] = style
        self.quote = Style(name="Quotations", family="paragraph", parentstylename="Standard")
        self.quote.addElement(ParagraphProperties(marginleft="1cm", marginright="1cm",
                                                  marginbottom="0.25cm"))
        self.quote.addElement(TextProperties(fontstyle="italic"))
        styles.addElement(self.quote)
        self.pre = Style(name="Preformatted_20_Text", displayname="Preformatted Text",
                         family="paragraph", parentstylename="Standard")
        self.pre.addElement(ParagraphProperties(backgroundcolor="#f2f2f2", padding="0.15cm"))
        self.pre.addElement(TextProperties(fontname="Monospace", fontsize="9.5pt"))
        styles.addElement(self.pre)
        self.caption = Style(name="Caption", family="paragraph", parentstylename="Standard")
        self.caption.addElement(TextProperties(fontstyle="italic", fontsize="10pt"))
        styles.addElement(self.caption)
        self.rule = Style(name="Horizontal_20_Line", displayname="Horizontal Line",
                          family="paragraph", parentstylename="Standard")
        self.rule.addElement(ParagraphProperties(borderbottom="0.06pt solid #999999",
                                                 paddingbottom="0cm", marginbottom="0.3cm"))
        styles.addElement(self.rule)
        self.body = Style(name="Text_20_body", displayname="Text body", family="paragraph",
                          parentstylename="Standard")
        self.body.addElement(ParagraphProperties(marginbottom="0.21cm"))
        styles.addElement(self.body)
        self.indented: dict[int, object] = {}

        self.cell_style = Style(name="CellBorder", family="table-cell")
        self.cell_style.addElement(TableCellProperties(border="0.5pt solid #999999", padding="0.1cm"))
        odt.automaticstyles.addElement(self.cell_style)
        self.frame_style = Style(name="ImageFrame", family="graphic")
        self.frame_style.addElement(GraphicProperties(wrap="none", verticalpos="top",
                                                      verticalrel="baseline"))
        odt.automaticstyles.addElement(self.frame_style)

        self.list_styles = {}
        for ordered in (False, True):
            list_style = ListStyle(name="L_number" if ordered else "L_bullet")
            for level in range(1, 11):
                if ordered:
                    element = ListLevelStyleNumber(level=str(level), numformat="1", numsuffix=".")
                else:
                    element = ListLevelStyleBullet(level=str(level), bulletchar="•◦▪"[(level - 1) % 3])
                element.addElement(ListLevelProperties(spacebefore=f"{0.64 * (level - 1):.2f}cm",
                                                       minlabelwidth="0.64cm"))
                list_style.addElement(element)
            styles.addElement(list_style)
            self.list_styles[ordered] = list_style

    def indent_style(self, cm: float):
        from odf.style import ParagraphProperties, Style

        key = round(cm * 10)
        if key not in self.indented:
            style = Style(name=f"Indent{key}", family="paragraph", parentstylename="Text_20_body")
            style.addElement(ParagraphProperties(marginleft=f"{cm:.2f}cm"))
            self.odt.automaticstyles.addElement(style)
            self.indented[key] = style
        return self.indented[key]

    def text_style(self, marks: frozenset[str]):
        from odf.style import Style, TextProperties

        if marks not in self.text_styles:
            props = {}
            if "strong" in marks:
                props["fontweight"] = "bold"
            if "em" in marks:
                props["fontstyle"] = "italic"
            if "u" in marks:
                props.update(textunderlinestyle="solid", textunderlinewidth="auto",
                             textunderlinecolor="font-color")
            if "s" in marks:
                props.update(textlinethroughstyle="solid", textlinethroughtype="single")
            if "sup" in marks:
                props["textposition"] = "super 58%"
            elif "sub" in marks:
                props["textposition"] = "sub 58%"
            if "code" in marks:
                props["fontname"] = "Monospace"
            style = Style(name=f"T{len(self.text_styles) + 1}", family="text")
            style.addElement(TextProperties(**props))
            self.odt.automaticstyles.addElement(style)
            self.text_styles[marks] = style
        return self.text_styles[marks]

    # --- contenuto -------------------------------------------------------------------
    def inline(self, element, children) -> None:
        from odf.text import A, LineBreak, Span

        for seg in segments(children):
            if seg.kind == "br":
                element.addElement(LineBreak())
                continue
            if seg.kind == "img":
                frame = self.image(seg.src, seg.text)
                if frame is not None:
                    element.addElement(frame)
                continue
            target = element
            if seg.href:
                link = A(href=seg.href, type="simple")
                element.addElement(link)
                target = link
            if seg.marks:
                span = Span(stylename=self.text_style(seg.marks))
                add_text(span, seg.text)
                target.addElement(span)
            else:
                add_text(target, seg.text)

    def image(self, src: str, alt: str):
        from odf.draw import Frame, Image
        from odf.svg import Desc

        found = self.document.images.get(src)
        if not found:
            self.report.dropped_images += 1
            return None
        data, ext = found
        mime = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
                "tif": "image/tiff", "svg": "image/svg+xml"}.get(ext)
        if mime is None:
            from ..docmodel.writers import _png_from

            data, mime, ext = _png_from(data), "image/png", "png"
            if data is None:
                self.report.dropped_images += 1
                return None
        width_cm, height_cm = _size_cm(data)
        self.image_count += 1
        href = self.odt.addPictureFromString(data, mime)
        frame = Frame(stylename=self.frame_style, anchortype="as-char", name=f"Image{self.image_count}",
                      width=f"{width_cm:.2f}cm", height=f"{height_cm:.2f}cm")
        frame.addElement(Image(href=href, type="simple", show="embed", actuate="onLoad"))
        if alt:
            frame.addElement(Desc(text=alt))
        return frame

    def blocks(self, parent, nodes: list[Node], *, quote: bool = False, indent_cm: float = 0.0) -> None:
        for node in nodes:
            self.block(parent, node, quote=quote, indent_cm=indent_cm)

    def paragraph_style(self, quote: bool, indent_cm: float):
        if quote:
            return self.quote
        if indent_cm:
            return self.indent_style(indent_cm)
        return self.body

    def block(self, parent, node: Node, *, quote: bool = False, indent_cm: float = 0.0) -> None:
        from odf.text import H, P

        tag = node.tag
        if tag in HEADINGS:
            level = heading_level(tag)
            heading = H(outlinelevel=str(level), stylename=self.headings[level])
            self.inline(heading, node.children)
            parent.addElement(heading)
        elif tag in ("p", "dt", "dd", "figcaption"):
            style = self.caption if tag == "figcaption" else self.paragraph_style(
                quote, indent_cm + (1.0 if tag == "dd" else 0.0))
            paragraph = P(stylename=style)
            children = node.children if tag != "dt" else [Node("strong", children=list(node.children))]
            self.inline(paragraph, children)
            parent.addElement(paragraph)
        elif tag == "pre":
            for line in node.text().rstrip("\n").split("\n"):
                paragraph = P(stylename=self.pre)
                add_text(paragraph, line)
                parent.addElement(paragraph)
        elif tag == "blockquote":
            self.blocks(parent, as_blocks(node), quote=True, indent_cm=indent_cm)
        elif tag in ("ul", "ol"):
            parent.addElement(self.list(node))
        elif tag == "hr":
            parent.addElement(P(stylename=self.rule))
        elif tag == "table":
            parent.addElement(self.table(node))
            parent.addElement(P(stylename=self.body))
        elif tag in ("figure", "dl", "li", "td", "th"):
            self.blocks(parent, as_blocks(node), quote=quote, indent_cm=indent_cm)
        elif tag == "img":
            paragraph = P(stylename=self.body)
            self.inline(paragraph, [node])
            parent.addElement(paragraph)

    def list(self, node: Node, *, nested: bool = False):
        from odf.text import List, ListItem, P

        ordered = node.tag == "ol"
        element = List(stylename=self.list_styles[ordered]) if not nested else List()
        start = node.attrs.get("start", "")
        first = True
        for item in node.children:
            if not isinstance(item, Node):
                continue
            attrs = {}
            if ordered and first and start and start != "1":
                attrs["startvalue"] = start
            first = False
            list_item = ListItem(**attrs)
            blocks = as_blocks(item) or [Node("p")]
            for block in blocks:
                if block.tag in ("ul", "ol"):
                    nested_list = self.list(block, nested=True)
                    # Un elenco annidato di tipo diverso porta il suo stile.
                    if (block.tag == "ol") != ordered:
                        nested_list.setAttribute("stylename", self.list_styles[block.tag == "ol"])
                    list_item.addElement(nested_list)
                elif block.tag in ("p",) + HEADINGS:
                    paragraph = P(stylename=self.body)
                    self.inline(paragraph, block.children)
                    list_item.addElement(paragraph)
                else:
                    self.block(list_item, block)
            element.addElement(list_item)
        return element

    def table(self, node: Node):
        from odf.table import CoveredTableCell, Table, TableColumn, TableHeaderRows, TableRow
        from odf.table import TableCell as OdfCell
        from odf.text import P

        grid, header_rows, _merged = table_grid(node)
        self.table_count += 1
        table = Table(name=f"Table{self.table_count}")
        cols = len(grid[0]) if grid else 0
        if cols:
            table.addElement(TableColumn(numbercolumnsrepeated=str(cols)))
        header = TableHeaderRows() if header_rows else None
        if header is not None:
            table.addElement(header)
        for r, line in enumerate(grid):
            row = TableRow()
            for cell in line:
                if cell is None:
                    row.addElement(CoveredTableCell())
                    continue
                attrs = {"stylename": self.cell_style, "valuetype": "string"}
                colspan = int(cell.attrs.get("colspan", "1") or 1)
                rowspan = int(cell.attrs.get("rowspan", "1") or 1)
                if colspan > 1:
                    attrs["numbercolumnsspanned"] = str(colspan)
                if rowspan > 1:
                    attrs["numberrowsspanned"] = str(rowspan)
                odf_cell = OdfCell(**attrs)
                is_header = r < header_rows or cell.tag == "th"
                blocks = as_blocks(cell) or [Node("p")]
                for block in blocks:
                    if block.tag in ("ul", "ol", "table", "pre", "blockquote"):
                        self.report.note("Lists, code and nested tables inside table cells became plain text.")
                        block = Node("p", children=[" ".join(block.text().split())])
                    paragraph = P(stylename=self.body)
                    children = block.children
                    if is_header:
                        children = [Node("strong", children=list(children))]
                    self.inline(paragraph, children)
                    odf_cell.addElement(paragraph)
                row.addElement(odf_cell)
            (header if header is not None and r < header_rows else table).addElement(row)
        return table


def _size_cm(data: bytes) -> tuple[float, float]:
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            dpi = image.info.get("dpi", (96, 96))[0] or 96
            width = image.width / float(dpi) * 2.54
            height = image.height / float(dpi) * 2.54
    except Exception:
        return 10.0, 7.5
    if width > _MAX_WIDTH_CM:
        height *= _MAX_WIDTH_CM / width
        width = _MAX_WIDTH_CM
    return max(width, 0.1), max(height, 0.1)


def write_odt(document: MarkupDocument, path) -> WriteReport:
    report = WriteReport()
    writer = _OdtWriter(document, report)
    writer.blocks(writer.odt.text, [c for c in document.body.children if isinstance(c, Node)])
    writer.odt.save(str(path))
    return report
