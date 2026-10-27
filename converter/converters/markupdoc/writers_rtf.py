"""Scrittore RTF dall'albero markup (RTF 1.x scritto a mano, solo ASCII).

Titoli come stili "heading N" con \\outlinelevel (Word li vede come titoli),
elenchi con marcatore testuale e rientro (come fa pandoc: niente \\listtable),
codice in monospazio ombreggiato, tabelle con bordi e celle unite, link cliccabili
(campo HYPERLINK), immagini PNG/JPEG incorporate.
"""

from __future__ import annotations

from io import BytesIO

from .common import WriteReport, as_blocks, heading_level, segments, table_grid
from .tree import HEADINGS, MarkupDocument, Node

_TEXT_WIDTH = 9360          # twip: 6.5" di riga utile su A4/Letter
_HEADING_HALF_POINTS = {1: 40, 2: 32, 3: 28, 4: 24, 5: 22, 6: 20}


def rtf_escape(text: str) -> str:
    out: list[str] = []
    for char in text:
        code = ord(char)
        if char in "\\{}":
            out.append("\\" + char)
        elif char == "\t":
            out.append("\\tab ")
        elif char == "\n":
            out.append("\\line ")
        elif 32 <= code < 127:
            out.append(char)
        elif code < 32:
            continue
        elif code <= 0xFFFF:
            out.append(f"\\u{code if code < 32768 else code - 65536}?")
        else:
            # Fuori dal piano base: coppia surrogata, come vuole RTF.
            code -= 0x10000
            for unit in (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF)):
                out.append(f"\\u{unit - 65536}?")
    return "".join(out)


class _RtfWriter:
    def __init__(self, document: MarkupDocument, report: WriteReport) -> None:
        self.document = document
        self.report = report
        self.out: list[str] = []

    def inline(self, children, *, bold: bool = False) -> str:
        parts: list[str] = []
        for seg in segments(children):
            if seg.kind == "br":
                parts.append("\\line ")
                continue
            if seg.kind == "img":
                parts.append(self.picture(seg.src))
                continue
            controls = []
            if "strong" in seg.marks or bold:
                controls.append("\\b")
            if "em" in seg.marks:
                controls.append("\\i")
            if "u" in seg.marks:
                controls.append("\\ul")
            if "s" in seg.marks:
                controls.append("\\strike")
            if "sup" in seg.marks:
                controls.append("\\super")
            elif "sub" in seg.marks:
                controls.append("\\sub")
            if "code" in seg.marks:
                controls.append("\\f1")
            text = rtf_escape(seg.text)
            piece = "{" + "".join(controls) + " " + text + "}" if controls else text
            if seg.href:
                url = seg.href.replace("\\", "\\\\").replace('"', "%22")
                piece = ('{\\field{\\*\\fldinst{HYPERLINK "' + rtf_escape(url) + '"}}'
                         "{\\fldrslt{\\ul\\cf1 " + piece + "}}}")
            parts.append(piece)
        return "".join(parts)

    def picture(self, src: str) -> str:
        found = self.document.images.get(src)
        if not found:
            self.report.dropped_images += 1
            return ""
        data, ext = found
        if ext == "jpg":
            blip = "\\jpegblip"
        elif ext == "png":
            blip = "\\pngblip"
        else:
            from ..docmodel.writers import _png_from

            data = _png_from(data)
            if data is None:
                self.report.dropped_images += 1
                return ""
            blip = "\\pngblip"
        try:
            from PIL import Image

            with Image.open(BytesIO(data)) as image:
                width, height = image.size
        except Exception:
            self.report.dropped_images += 1
            return ""
        goal_w = width * 15                      # 96 dpi → 15 twip per pixel
        goal_h = height * 15
        if goal_w > _TEXT_WIDTH:
            goal_h = goal_h * _TEXT_WIDTH // goal_w
            goal_w = _TEXT_WIDTH
        return (f"{{\\pict{blip}\\picw{width}\\pich{height}\\picwgoal{goal_w}\\pichgoal{goal_h}\n"
                + data.hex() + "}")

    def paragraph(self, content: str, *, style: str = "\\s0", extra: str = "") -> None:
        if content.strip():
            self.out.append(f"{{\\pard\\plain{style}\\sa120{extra} {content}\\par}}\n")

    def blocks(self, nodes: list[Node], *, quote: bool = False, indent: int = 0) -> None:
        for node in nodes:
            self.block(node, quote=quote, indent=indent)

    def block(self, node: Node, *, quote: bool = False, indent: int = 0) -> None:
        tag = node.tag
        left = f"\\li{indent}" if indent else ""
        if tag in HEADINGS:
            level = heading_level(tag)
            size = _HEADING_HALF_POINTS[level]
            self.paragraph(self.inline(node.children),
                           style=f"\\s{level}\\outlinelevel{level - 1}\\keepn\\sb240\\b\\fs{size}")
        elif tag in ("p", "dt", "dd", "figcaption"):
            extra = left
            if quote:
                extra = f"\\li{indent + 720}\\ri720\\i"
            elif tag == "dd":
                extra = f"\\li{indent + 720}"
            elif tag == "figcaption":
                extra += "\\i\\fs20"
            self.paragraph(self.inline(node.children, bold=(tag == "dt")), extra=extra)
        elif tag == "pre":
            code = rtf_escape(node.text().rstrip("\n"))
            self.out.append(f"{{\\pard\\plain\\s0\\sa120{left}\\cbpat3\\f1\\fs19 {code}\\par}}\n")
        elif tag == "blockquote":
            self.blocks(as_blocks(node), quote=True, indent=indent)
        elif tag in ("ul", "ol"):
            self.list(node, indent)
        elif tag == "hr":
            self.out.append("{\\pard\\plain\\brdrb\\brdrs\\brdrw10\\brsp20\\sa120\\par}\n")
        elif tag == "table":
            self.table(node)
        elif tag in ("figure", "dl", "li", "td", "th"):
            self.blocks(as_blocks(node), quote=quote, indent=indent)
        elif tag == "img":
            self.paragraph(self.inline([node]), extra="\\qc")

    def list(self, node: Node, indent: int) -> None:
        ordered = node.tag == "ol"
        number = int(node.attrs.get("start", "1") or 1)
        level_indent = indent + 360
        for item in node.children:
            if not isinstance(item, Node):
                continue
            marker = f"{number}." if ordered else "\\bullet"
            number += 1
            first = True
            for block in as_blocks(item):
                if block.tag in ("ul", "ol"):
                    self.list(block, level_indent + 360)
                elif first and block.tag in ("p",) + HEADINGS:
                    self.paragraph(f"{marker}\\tab {self.inline(block.children)}",
                                   extra=f"\\fi-360\\li{level_indent + 360}\\tx{level_indent + 360}")
                else:
                    self.block(block, indent=level_indent + 360)
                first = False

    def table(self, node: Node) -> None:
        grid, header_rows, _merged = table_grid(node)
        if not grid or not grid[0]:
            return
        cols = len(grid[0])
        width = _TEXT_WIDTH // cols
        anchors: dict[tuple[int, int], tuple[int, int]] = {}
        for r, line in enumerate(grid):
            for c, cell in enumerate(line):
                if cell is not None:
                    colspan = int(cell.attrs.get("colspan", "1") or 1)
                    rowspan = int(cell.attrs.get("rowspan", "1") or 1)
                    for dr in range(rowspan):
                        for dc in range(colspan):
                            anchors[(r + dr, c + dc)] = (r, c)
        border = "\\clbrdrt\\brdrs\\brdrw10\\clbrdrl\\brdrs\\brdrw10\\clbrdrb\\brdrs\\brdrw10\\clbrdrr\\brdrs\\brdrw10"
        for r in range(len(grid)):
            row = ["{\\trowd\\trgaph108\\trleft0"]
            if r < header_rows:
                row.append("\\trhdr")
            cells: list[str] = []
            for c in range(cols):
                anchor = anchors.get((r, c), (r, c))
                merge = ""
                if anchor != (r, c):
                    merge = "\\clvmrg" if anchor[0] != r else "\\clmrg"
                else:
                    cell = grid[r][c]
                    if cell is not None and int(cell.attrs.get("rowspan", "1") or 1) > 1:
                        merge = "\\clvmgf"
                    if cell is not None and int(cell.attrs.get("colspan", "1") or 1) > 1:
                        merge += "\\clmgf"
                row.append(f"{merge}{border}\\cellx{width * (c + 1)}")
                cell = grid[r][c]
                if cell is None or anchor != (r, c):
                    cells.append("\\pard\\intbl \\cell")
                    continue
                header = r < header_rows or cell.tag == "th"
                parts = []
                for block in as_blocks(cell):
                    if block.tag in ("ul", "ol", "table", "pre", "blockquote"):
                        self.report.note("Lists, code and nested tables inside table cells became plain text.")
                        parts.append(rtf_escape(" ".join(block.text().split())))
                    else:
                        parts.append(self.inline(block.children, bold=header))
                align = {"center": "\\qc", "right": "\\qr"}.get(cell.attrs.get("align", ""), "")
                cells.append(f"\\pard\\intbl{align} " + "\\line ".join(p for p in parts if p) + "\\cell")
            row.append("\n" + "\n".join(cells) + "\n\\row}\n")
            self.out.append("".join(row))
        self.out.append("{\\pard\\plain\\sa120\\par}\n")


_HEADER = ("{\\rtf1\\ansi\\ansicpg1252\\deff0\\uc1\n"
           "{\\fonttbl{\\f0\\fswiss\\fcharset0 Calibri;}{\\f1\\fmodern\\fcharset0 Consolas;}}\n"
           "{\\colortbl;\\red5\\green99\\blue193;\\red153\\green153\\blue153;\\red242\\green242\\blue242;}\n"
           "{\\stylesheet{\\s0 Normal;}"
           + "".join(f"{{\\s{n}\\outlinelevel{n - 1}\\keepn\\sb240\\sa120\\b\\fs{size} heading {n};}}"
                     for n, size in _HEADING_HALF_POINTS.items())
           + "}\n")


def write_rtf(document: MarkupDocument, path) -> WriteReport:
    report = WriteReport()
    writer = _RtfWriter(document, report)
    writer.blocks([c for c in document.body.children if isinstance(c, Node)])
    info = f"{{\\info{{\\title {rtf_escape(document.title)}}}}}\n" if document.title else ""
    body = "".join(writer.out)
    path.write_text(_HEADER + info + "\\viewkind4\\fs22\n" + body + "}\n", encoding="ascii")
    return report
