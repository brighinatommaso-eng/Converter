"""Scrittori testuali dall'albero: Markdown (GFM), testo semplice, HTML."""

from __future__ import annotations

import base64
import html as html_lib
import re

from .common import Segment, WriteReport, as_blocks, heading_level, segments, table_grid
from .tree import HEADINGS, MarkupDocument, Node, safe_url

# --- Markdown -------------------------------------------------------------------

_MD_INLINE = re.compile(r"([\\`*_\[\]<>|~&])")
_MD_LINE_START = re.compile(r"^(\s*)([#+\-=>])")
_MD_ORDERED_START = re.compile(r"^(\s*\d{1,9})([.)])")
_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "bmp": "image/bmp",
         "webp": "image/webp", "svg": "image/svg+xml", "tif": "image/tiff"}


def _md_escape(text: str) -> str:
    return _MD_INLINE.sub(r"\\\1", text)


def _md_url(url: str) -> str:
    return url.replace("<", "%3C").replace(">", "%3E").replace(" ", "%20").replace("\n", "%0A")


def _code_span(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") or (text[:1] == " " and text[-1:] == " ") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _image_src(document: MarkupDocument, src: str) -> str:
    """src utilizzabile fuori dalla sorgente: le immagini prese da un archivio
    (epub:/odt:) diventano data-URI, i percorsi e gli URL restano come sono."""
    if src.startswith(("epub:", "odt:")):
        found = document.images.get(src)
        if not found:
            return ""
        data, ext = found
        return f"data:{_MIME.get(ext, 'image/png')};base64,{base64.b64encode(data).decode('ascii')}"
    return src


def _md_piece(seg: Segment, document: MarkupDocument, in_table: bool) -> str:
    """Un segmento in Markdown, senza il link (lo aggiunge _md_inline al gruppo)."""
    if seg.kind == "br":
        return "<br>" if in_table else "\\\n"
    if seg.kind == "img":
        src = _image_src(document, seg.src)
        return f"![{_md_escape(seg.text)}](<{_md_url(src)}>)" if src else ""
    if "code" in seg.marks:
        return _code_span(seg.text.replace("\n", " "))
    text = _md_escape(seg.text)
    if not text.strip():
        return text
    # Gli spazi ai bordi vanno FUORI dai delimitatori ("**a **" non è grassetto).
    core = text.strip()
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    if "em" in seg.marks:
        core = f"*{core}*"
    if "strong" in seg.marks:
        core = f"**{core}**"
    if "s" in seg.marks:
        core = f"~~{core}~~"
    if "u" in seg.marks:
        core = f"<u>{core}</u>"
    if "sup" in seg.marks:
        core = f"<sup>{core}</sup>"
    if "sub" in seg.marks:
        core = f"<sub>{core}</sub>"
    return lead + core + trail


def _md_inline(segs: list[Segment], document: MarkupDocument, *, in_table: bool = False) -> str:
    out: list[str] = []
    index = 0
    while index < len(segs):
        href = segs[index].href
        group = []
        while index < len(segs) and segs[index].href == href:
            group.append(_md_piece(segs[index], document, in_table))
            index += 1
        text = "".join(group)
        if href and text.strip():
            lead = text[: len(text) - len(text.lstrip())]
            trail = text[len(text.rstrip()):]
            text = f"{lead}[{text.strip()}](<{_md_url(href)}>){trail}"
        out.append(text)
    return "".join(out)


def _md_block_escape(text: str) -> str:
    text = _MD_LINE_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text, count=1)
    return _MD_ORDERED_START.sub(lambda m: m.group(1) + "\\" + m.group(2), text, count=1)


def _md_blocks(nodes: list[Node], document: MarkupDocument, report: WriteReport) -> list[str]:
    out: list[str] = []
    for node in nodes:
        rendered = _md_block(node, document, report)
        if rendered is not None and rendered.strip():
            out.append(rendered)
    return out


def _md_block(node: Node, document: MarkupDocument, report: WriteReport) -> str | None:
    tag = node.tag
    if tag in HEADINGS:
        text = _md_inline(segments(node.children), document).replace("\\\n", " ").strip()
        if text.endswith("#"):
            text = text[:-1] + "\\#"
        return f"{'#' * heading_level(tag)} {text}" if text else None
    if tag in ("p", "figcaption", "dt", "dd"):
        text = _md_inline(segments(node.children), document).strip()
        if tag == "dt":
            text = f"**{text}**" if text else text
        return _md_block_escape(text)
    if tag == "pre":
        code = node.text()
        language = ""
        for child in node.children:
            if isinstance(child, Node) and child.tag == "code":
                cls = child.attrs.get("class", "")
                match = re.search(r"language-([\w+#.-]+)", cls)
                language = match.group(1) if match else ""
        longest = max((len(m) for m in re.findall(r"`{3,}", code)), default=2)
        fence = "`" * max(3, longest + 1)
        return f"{fence}{language}\n{code.rstrip(chr(10))}\n{fence}"
    if tag == "blockquote":
        inner = "\n\n".join(_md_blocks(as_blocks(node), document, report))
        return "\n".join(("> " + line) if line else ">" for line in inner.split("\n"))
    if tag in ("ul", "ol"):
        return _md_list(node, document, report)
    if tag == "hr":
        return "---"
    if tag == "table":
        return _md_table(node, document, report)
    if tag in ("figure", "dl", "li", "td", "th"):
        return "\n\n".join(_md_blocks(as_blocks(node), document, report))
    if tag == "img":
        return _md_inline(segments([node]), document)
    return _md_inline(segments(node.children), document)


def _md_list(node: Node, document: MarkupDocument, report: WriteReport) -> str:
    ordered = node.tag == "ol"
    number = int(node.attrs.get("start", "1") or 1) if ordered else 0
    items: list[str] = []
    loose = False
    for item in node.children:
        if not isinstance(item, Node):
            continue
        marker = f"{number}. " if ordered else "- "
        number += 1
        blocks = _md_blocks(as_blocks(item), document, report)
        if len([b for b in as_blocks(item) if b.tag not in ("ul", "ol")]) > 1:
            loose = True
        body = "\n\n".join(blocks) if loose else "\n".join(blocks)
        pad = " " * len(marker)
        lines = body.split("\n") if body else [""]
        rendered = marker + lines[0] + "".join("\n" + (pad + line if line else "") for line in lines[1:])
        items.append(rendered)
    return ("\n\n" if loose else "\n").join(items)


def _md_table(node: Node, document: MarkupDocument, report: WriteReport) -> str | None:
    grid, header_rows, merged = table_grid(node)
    if not grid or not grid[0]:
        return None
    if merged:
        report.note("Merged table cells were split (Markdown tables have no merged cells).")

    def cell_text(cell: Node | None) -> str:
        if cell is None:
            return ""
        parts = []
        for block in as_blocks(cell):
            if block.tag in ("ul", "ol", "table", "pre", "blockquote"):
                report.note("Lists, code and nested tables inside table cells became plain text.")
                parts.append(_md_escape(" ".join(block.text().split())))
            else:
                parts.append(_md_inline(segments(block.children), document, in_table=True).strip())
        return "<br>".join(p for p in parts if p)

    rows = [[cell_text(cell) for cell in line] for line in grid]
    head = rows[:max(1, header_rows)] if header_rows else rows[:1]
    body = rows[len(head):]
    if len(head) > 1:
        report.note("Only the first header row of a table stays a Markdown header.")
        body = head[1:] + body
        head = head[:1]
    width = len(rows[0])
    aligns = []
    for col in range(width):
        cell = next((line[col] for line in grid if line[col] is not None), None)
        align = cell.attrs.get("align", "") if cell is not None else ""
        aligns.append({"left": ":---", "center": ":---:", "right": "---:"}.get(align, "---"))
    lines = ["| " + " | ".join(head[0]) + " |", "| " + " | ".join(aligns) + " |"]
    lines += ["| " + " | ".join(row) + " |" for row in body]
    return "\n".join(lines)


def write_markdown(document: MarkupDocument, path) -> WriteReport:
    report = WriteReport()
    blocks = _md_blocks([c for c in document.body.children if isinstance(c, Node)], document, report)
    path.write_text("\n\n".join(blocks).rstrip() + "\n", encoding="utf-8")
    return report


# --- Testo semplice ---------------------------------------------------------------

def _plain_inline(node: Node) -> str:
    out = []
    for seg in segments(node.children):
        if seg.kind == "br":
            out.append("\n")
        elif seg.kind == "text":
            out.append(seg.text)
    return "".join(out).strip()


def _txt_blocks(nodes: list[Node]) -> list[str]:
    out: list[str] = []
    for node in nodes:
        text = _txt_block(node)
        if text and text.strip():
            out.append(text)
    return out


def _txt_block(node: Node) -> str:
    tag = node.tag
    if tag in HEADINGS or tag in ("p", "figcaption", "dt", "dd"):
        return _plain_inline(node)
    if tag == "pre":
        return node.text().rstrip("\n")
    if tag == "blockquote":
        inner = "\n\n".join(_txt_blocks(as_blocks(node)))
        return "\n".join("    " + line if line else "" for line in inner.split("\n"))
    if tag in ("ul", "ol"):
        ordered = tag == "ol"
        number = int(node.attrs.get("start", "1") or 1)
        items = []
        for item in node.children:
            if not isinstance(item, Node):
                continue
            marker = f"{number}. " if ordered else "- "
            number += 1
            body = "\n".join(_txt_blocks(as_blocks(item)))
            pad = " " * len(marker)
            lines = body.split("\n") if body else [""]
            items.append(marker + lines[0] + "".join("\n" + pad + line for line in lines[1:]))
        return "\n".join(items)
    if tag == "hr":
        return "-" * 40
    if tag == "table":
        grid, _h, _m = table_grid(node)
        return "\n".join("\t".join(" ".join(cell.text().split()) if cell is not None else ""
                                   for cell in line) for line in grid)
    if tag in ("figure", "dl", "li", "td", "th"):
        return "\n\n".join(_txt_blocks(as_blocks(node)))
    return _plain_inline(node)


def write_text(document: MarkupDocument, path) -> WriteReport:
    blocks = _txt_blocks([c for c in document.body.children if isinstance(c, Node)])
    path.write_text("\n\n".join(blocks).rstrip() + "\n", encoding="utf-8")
    return WriteReport()


# --- HTML -------------------------------------------------------------------------

def _esc(text: str) -> str:
    return html_lib.escape(text, quote=False)


def _attr(value: str) -> str:
    return html_lib.escape(value, quote=True)


def html_fragment(node: Node, document: MarkupDocument, report: WriteReport, *,
                  xhtml: bool = False, image_src=None) -> str:
    """Serializza un sottoalbero. `image_src(src)` decide dove puntano le immagini
    (data-URI per l'HTML a file unico, file dell'archivio per l'EPUB)."""
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(_esc(child))
            continue
        tag = child.tag
        if tag == "br":
            parts.append("<br/>" if xhtml else "<br>")
            continue
        if tag == "hr":
            parts.append("<hr/>" if xhtml else "<hr>")
            continue
        if tag == "img":
            src = image_src(child.attrs.get("src", "")) if image_src else child.attrs.get("src", "")
            if not src:
                report.dropped_images += 1
                continue
            alt = _attr(child.attrs.get("alt", ""))
            parts.append(f'<img src="{_attr(src)}" alt="{alt}"{"/" if xhtml else ""}>')
            continue
        attrs = ""
        if child.attrs.get("align") in ("left", "center", "right"):
            attrs += f' style="text-align:{child.attrs["align"]}"'
        if tag in HEADINGS and re.fullmatch(r"h\d+", child.attrs.get("id", "")):
            attrs += f' id="{child.attrs["id"]}"'      # solo gli id generati per l'indice EPUB
        for name in ("href", "start", "colspan", "rowspan", "class"):
            value = child.attrs.get(name)
            if value is None:
                continue
            if name == "href":
                value = safe_url(value)
                if not value:
                    continue
            if name == "class" and not re.fullmatch(r"language-[\w+#.-]+", value):
                continue
            attrs += f' {name}="{_attr(value)}"'
        inner = html_fragment(child, document, report, xhtml=xhtml, image_src=image_src)
        parts.append(f"<{tag}{attrs}>{inner}</{tag}>")
    return "".join(parts)


def embedded_src(document: MarkupDocument):
    """Immagini locali/archivio → data-URI; URL remoti restano link."""
    def image_src(src: str) -> str:
        found = document.images.get(src)
        if found:
            data, ext = found
            return f"data:{_MIME.get(ext, 'image/png')};base64,{base64.b64encode(data).decode('ascii')}"
        if src.startswith(("http://", "https://")):
            return src
        return ""
    return image_src


_CSS = ("body{font-family:system-ui,sans-serif;line-height:1.5;max-width:46em;margin:2em auto;"
        "padding:0 1em}pre{background:#f5f5f5;padding:.8em;overflow:auto}"
        "code{font-family:ui-monospace,monospace}blockquote{border-left:3px solid #ccc;"
        "margin-left:0;padding-left:1em;color:#444}table{border-collapse:collapse}"
        "td,th{border:1px solid #bbb;padding:.3em .6em}img{max-width:100%}")
# Testo semplice: spazi e rientri sono contenuto (vedi MarkupDocument.preserve_spaces).
_PRE_WRAP = "p{white-space:pre-wrap}"


def write_html(document: MarkupDocument, path) -> WriteReport:
    report = WriteReport()
    body = html_fragment(document.body, document, report, image_src=embedded_src(document))
    lang = f' lang="{_attr(document.language)}"' if document.language else ""
    title = f"<title>{_esc(document.title)}</title>" if document.title else ""
    path.write_text(
        f'<!DOCTYPE html>\n<html{lang}>\n<head>\n<meta charset="utf-8">\n{title}\n'
        f"<style>{_CSS}{_PRE_WRAP if document.preserve_spaces else ''}</style>\n</head>\n<body>\n{body}\n</body>\n</html>\n", encoding="utf-8")
    return report
