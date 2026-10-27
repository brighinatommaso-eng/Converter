"""Scrittore EPUB 3 dall'albero markup (con toc.ncx per i lettori EPUB 2).

Un capitolo per ogni titolo di primo livello (o un capitolo solo), indice con i
titoli di livello 1-2, immagini come file del libro. Le immagini remote non si
scaricano: vengono tolte e dichiarate. Lo XHTML è sempre ben formato (scritto da
html_fragment in modalità xhtml, caratteri di controllo rimossi).
"""

from __future__ import annotations

import datetime as _dt
import html as html_lib
import re
import uuid
import zipfile

from .common import WriteReport, heading_level
from .tree import HEADINGS, MarkupDocument, Node
from .writers_text import html_fragment

_MIME = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "svg": "image/svg+xml",
         "webp": "image/webp"}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_CSS = ("body{font-family:serif;line-height:1.5}pre{font-family:monospace;white-space:pre-wrap;"
        "background:#f5f5f5;padding:.5em}code{font-family:monospace}blockquote{margin-left:1.5em;"
        "font-style:italic}table{border-collapse:collapse}td,th{border:1px solid #999;padding:.2em .5em}"
        "img{max-width:100%}")


def _esc(text: str) -> str:
    return html_lib.escape(_CONTROL.sub("", text), quote=True)


def _chapters(body: Node) -> list[list[Node]]:
    nodes = [c for c in body.children if isinstance(c, Node)]
    split_tag = "h1" if any(n.tag == "h1" for n in nodes) else None
    chapters: list[list[Node]] = [[]]
    for node in nodes:
        if split_tag and node.tag == split_tag and chapters[-1]:
            chapters.append([])
        chapters[-1].append(node)
    return [chapter for chapter in chapters if chapter]


def write_epub(document: MarkupDocument, path) -> WriteReport:
    report = WriteReport()
    language = document.language or "und"
    title = document.title or "Untitled"

    # Immagini → file del libro (stessa src = stesso file).
    image_files: dict[str, str] = {}
    manifest_images: list[tuple[str, str, str]] = []
    for node in document.body.iter():
        if node.tag != "img":
            continue
        src = node.attrs.get("src", "")
        if src in image_files or src not in document.images:
            continue
        data, ext = document.images[src]
        if ext not in _MIME:
            from ..docmodel.writers import _png_from

            data = _png_from(data)
            if data is None:
                continue
            ext = "png"
            document.images[src] = (data, ext)
        name = f"images/img-{len(image_files) + 1:03d}.{ext}"
        image_files[src] = name
        manifest_images.append((f"img{len(image_files)}", name, _MIME[ext]))

    def image_src(src: str) -> str:
        return image_files.get(src, "")

    # Id ai titoli per l'indice.
    toc: list[tuple[int, str, str, str]] = []      # (livello, file, id, testo)
    chapters = _chapters(document.body)
    files = []
    for number, chapter in enumerate(chapters, start=1):
        name = f"chapter-{number:03d}.xhtml"
        files.append(name)
        for node in chapter:
            if node.tag in HEADINGS and heading_level(node.tag) <= 2:
                anchor = f"h{len(toc) + 1}"
                node.attrs["id"] = anchor
                toc.append((heading_level(node.tag), name, anchor, " ".join(node.text().split())))

    with zipfile.ZipFile(path, "w") as book:
        # "mimetype" per primo e NON compresso: è così che un lettore riconosce l'EPUB.
        book.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        deflate = zipfile.ZIP_DEFLATED
        book.writestr("META-INF/container.xml",
                      '<?xml version="1.0" encoding="UTF-8"?>\n'
                      '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                      '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                      'media-type="application/oebps-package+xml"/></rootfiles></container>\n',
                      compress_type=deflate)
        css = _CSS + ("p{white-space:pre-wrap}" if document.preserve_spaces else "")
        book.writestr("OEBPS/style.css", css, compress_type=deflate)
        for chapter, name in zip(chapters, files, strict=True):
            body = html_fragment(Node("body", children=list(chapter)), document, report,
                                 xhtml=True, image_src=image_src)
            chapter_title = next((text for _l, file, _a, text in toc if file == name), title)
            xhtml = ('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
                     f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="{_esc(language)}" '
                     f'lang="{_esc(language)}"><head><meta charset="utf-8"/>'
                     f'<title>{_esc(chapter_title)}</title>'
                     '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
                     f"<body>{_CONTROL.sub('', body)}</body></html>\n")
            book.writestr(f"OEBPS/{name}", xhtml, compress_type=deflate)
        for src, name in image_files.items():
            book.writestr(f"OEBPS/{name}", document.images[src][0], compress_type=deflate)

        entries = toc or [(1, files[0] if files else "chapter-001.xhtml", "", title)]
        nav_items = "".join(
            f'<li><a href="{file}{"#" + anchor if anchor else ""}">{_esc(text) or "…"}</a></li>'
            for _level, file, anchor, text in entries)
        book.writestr("OEBPS/nav.xhtml",
                      '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
                      '<html xmlns="http://www.w3.org/1999/xhtml" '
                      'xmlns:epub="http://www.idpf.org/2007/ops"><head><meta charset="utf-8"/>'
                      f"<title>{_esc(title)}</title></head><body>"
                      f'<nav epub:type="toc" id="toc"><h1>{_esc(title)}</h1><ol>{nav_items}</ol></nav>'
                      "</body></html>\n", compress_type=deflate)
        identifier = f"urn:uuid:{uuid.uuid4()}"
        nav_points = "".join(
            f'<navPoint id="np{index}" playOrder="{index}"><navLabel><text>{_esc(text) or "…"}</text>'
            f'</navLabel><content src="{file}{"#" + anchor if anchor else ""}"/></navPoint>'
            for index, (_level, file, anchor, text) in enumerate(entries, start=1))
        book.writestr("OEBPS/toc.ncx",
                      '<?xml version="1.0" encoding="UTF-8"?>\n'
                      '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
                      f'<head><meta name="dtb:uid" content="{identifier}"/></head>'
                      f"<docTitle><text>{_esc(title)}</text></docTitle><navMap>{nav_points}</navMap></ncx>\n",
                      compress_type=deflate)
        modified = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                    '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
                    '<item id="css" href="style.css" media-type="text/css"/>']
        manifest += [f'<item id="c{index}" href="{name}" media-type="application/xhtml+xml"/>'
                     for index, name in enumerate(files, start=1)]
        manifest += [f'<item id="{item_id}" href="{name}" media-type="{mime}"/>'
                     for item_id, name, mime in manifest_images]
        spine = "".join(f'<itemref idref="c{index}"/>' for index in range(1, len(files) + 1))
        book.writestr("OEBPS/content.opf",
                      '<?xml version="1.0" encoding="UTF-8"?>\n'
                      '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">'
                      '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                      f'<dc:identifier id="uid">{identifier}</dc:identifier>'
                      f"<dc:title>{_esc(title)}</dc:title><dc:language>{_esc(language)}</dc:language>"
                      f'<meta property="dcterms:modified">{modified}</meta></metadata>'
                      f'<manifest>{"".join(manifest)}</manifest>'
                      f'<spine toc="ncx">{spine}</spine></package>\n', compress_type=deflate)
    return report
