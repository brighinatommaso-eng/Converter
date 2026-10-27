"""Lettori dei formati markup → MarkupDocument (albero HTML ripulito).

md    markdown-it-py (MIT), regole CommonMark + tabelle e barrato GFM: lo stesso
      HTML che produce il renderer di riferimento.
html  parser tollerante della stdlib (tree.parse_html).
epub  spine dell'OPF, capitolo per capitolo, immagini prese dall'archivio.
odt   content.xml letto con defusedxml: titoli, paragrafi, elenchi, tabelle,
      immagini, stili di carattere (grassetto, corsivo, sottolineato, barrato,
      apice/pedice, monospazio), note a piè di pagina spostate in fondo.
txt   paragrafi e a capo.

Nessun lettore va in rete: le immagini remote restano link (gli scrittori che
devono incorporarle le scartano e lo dichiarano).
"""

from __future__ import annotations

import base64
import posixpath
import re
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .tree import MarkupDocument, Node, normalize, parse_html, text_document

# Limiti contro archivi malevoli (zip bomb) e immagini enormi.
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_IMAGE_BYTES = 50 * 1024 * 1024
_IMAGE_EXTS = {"PNG": "png", "JPEG": "jpg", "GIF": "gif", "BMP": "bmp", "WEBP": "webp",
               "TIFF": "tif"}


def decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def image_ext(data: bytes) -> str | None:
    """Estensione reale di un'immagine che Pillow sa aprire, o None se non lo è."""
    if not data or len(data) > _MAX_IMAGE_BYTES:
        return None
    if data.lstrip()[:5].lower() in (b"<?xml", b"<svg ") or b"<svg" in data[:512]:
        return "svg"
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            return _IMAGE_EXTS.get(image.format or "")
    except Exception:
        return None


def _data_uri(src: str) -> tuple[bytes, str] | None:
    match = re.match(r"^data:([\w/+.-]+)?(;base64)?,(.*)$", src, re.S)
    if not match or not match.group(2):
        return None
    try:
        data = base64.b64decode(match.group(3), validate=False)
    except ValueError:
        return None
    ext = image_ext(data)
    return (data, ext) if ext else None


def _is_remote(src: str) -> bool:
    return urlsplit(src).scheme.lower() in ("http", "https", "ftp")


def resolve_local_images(document: MarkupDocument, base_dir: Path) -> None:
    """Carica le immagini referenziate da file locali o data-URI (mai dalla rete)."""
    missing = 0
    for node in document.body.iter():
        if node.tag != "img":
            continue
        src = node.attrs.get("src", "")
        if not src or src in document.images or _is_remote(src):
            continue
        if src.startswith("data:"):
            found = _data_uri(src)
        else:
            path_text = unquote(urlsplit(src).path) if src.startswith("file:") else unquote(src.split("#")[0].split("?")[0])
            path = Path(path_text)
            if not path.is_absolute():
                path = base_dir / path
            found = None
            try:
                if path.is_file() and path.stat().st_size <= _MAX_IMAGE_BYTES:
                    data = path.read_bytes()
                    ext = image_ext(data)
                    found = (data, ext) if ext else None
            except OSError:
                found = None
        if found:
            document.images[src] = found
        else:
            missing += 1
    if missing:
        document.notes.append(f"{missing} image(s) could not be found or read next to the source.")


# --- Markdown -------------------------------------------------------------------

def markdown_to_html(text: str) -> str:
    """Rendering di riferimento: CommonMark + tabelle e barrato GFM, HTML grezzo ammesso
    (viene poi ripulito da parse_html: niente script né eventi)."""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    return md.render(text)


def has_raw_html(text: str) -> bool:
    """True se il Markdown contiene HTML grezzo (blocchi o inline)."""
    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    for token in md.parse(text):
        if token.type == "html_block":
            return True
        if token.type == "inline" and any(child.type == "html_inline" for child in token.children or []):
            return True
    return False


def read_markdown(path: Path) -> MarkupDocument:
    text = decode_text(path.read_bytes())
    body, _title, _lang = parse_html(markdown_to_html(text))
    document = MarkupDocument(body=body, title=_first_heading(body) or path.stem)
    if has_raw_html(text):
        document.notes.append("Raw HTML inside the Markdown was cleaned: scripts, styles, "
                              "attributes and unknown tags were removed.")
    resolve_local_images(document, path.parent)
    return document


def _first_heading(body: Node) -> str:
    for node in body.iter():
        if node.tag in ("h1", "h2"):
            return " ".join(node.text().split())
    return ""


# --- HTML -----------------------------------------------------------------------

_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", re.I)


def decode_html(raw: bytes) -> str:
    match = _META_CHARSET.search(raw[:4096])
    if match:
        try:
            return raw.decode(match.group(1).decode("ascii"), errors="replace").lstrip("﻿")
        except LookupError:
            pass
    return decode_text(raw)


def read_html(path: Path) -> MarkupDocument:
    body, title, language = parse_html(decode_html(path.read_bytes()))
    document = MarkupDocument(body=body, title=title or _first_heading(body) or path.stem,
                              language=language)
    resolve_local_images(document, path.parent)
    return document


def read_text(path: Path) -> MarkupDocument:
    return MarkupDocument(body=text_document(decode_text(path.read_bytes())), title=path.stem,
                          preserve_spaces=True)


# --- EPUB -----------------------------------------------------------------------

class ArchiveError(ValueError):
    """Archivio illeggibile, troppo grande o protetto: messaggio per l'utente."""


def _open_archive(path: Path) -> zipfile.ZipFile:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("The file is not a valid ZIP-based document.") from exc
    if sum(info.file_size for info in archive.infolist()) > _MAX_ARCHIVE_BYTES:
        archive.close()
        raise ArchiveError("The document expands to more than 512 MB: refused as unsafe.")
    return archive


def _xml(data: bytes):
    from defusedxml import ElementTree

    return ElementTree.fromstring(data)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def read_epub(path: Path) -> MarkupDocument:
    with _open_archive(path) as archive:
        names = set(archive.namelist())
        if "META-INF/encryption.xml" in names:
            encryption = archive.read("META-INF/encryption.xml")
            # I font offuscati (IDPF/Adobe) sono innocui; contenuti cifrati = DRM.
            if re.search(rb"<(\w+:)?CipherReference[^>]+URI=\"[^\"]+\.x?html?\"", encryption):
                raise ArchiveError("This EPUB is DRM-protected and cannot be read.")
        try:
            container = _xml(archive.read("META-INF/container.xml"))
        except KeyError as exc:
            raise ArchiveError("Not an EPUB: META-INF/container.xml is missing.") from exc
        rootfile = next((el.get("full-path") for el in container.iter()
                         if _local(el.tag) == "rootfile" and el.get("full-path")), None)
        if not rootfile or rootfile not in names:
            raise ArchiveError("Not an EPUB: the package document is missing.")
        opf = _xml(archive.read(rootfile))
        opf_dir = posixpath.dirname(rootfile)
        manifest = {el.get("id"): (el.get("href", ""), el.get("media-type", ""))
                    for el in opf.iter() if _local(el.tag) == "item"}
        spine = [el.get("idref") for el in opf.iter() if _local(el.tag) == "itemref"]
        title = next((" ".join((el.text or "").split()) for el in opf.iter()
                      if _local(el.tag) == "title" and (el.text or "").strip()), "")
        language = next(((el.text or "").strip() for el in opf.iter()
                         if _local(el.tag) == "language" and (el.text or "").strip()), "")

        body = Node("body")
        document = MarkupDocument(body=body, title=title or path.stem, language=language)
        for idref in spine:
            href, media = manifest.get(idref, ("", ""))
            if not href or "html" not in media:
                continue
            member = posixpath.normpath(posixpath.join(opf_dir, unquote(href)))
            if member not in names:
                continue
            chapter, _t, _l = parse_html(decode_html(archive.read(member)))
            base = posixpath.dirname(member)
            for node in chapter.iter():
                if node.tag == "img":
                    src = node.attrs.get("src", "")
                    if not src or _is_remote(src) or src.startswith("data:"):
                        continue
                    target = posixpath.normpath(posixpath.join(base, unquote(src.split("#")[0])))
                    key = f"epub:{target}"
                    if key not in document.images and target in names:
                        data = archive.read(target)
                        ext = image_ext(data)
                        if ext:
                            document.images[key] = (data, ext)
                    node.attrs["src"] = key
                elif node.tag == "a" and node.attrs.get("href", "").split("#")[0].endswith(
                        (".xhtml", ".html", ".htm")):
                    # Link fra capitoli: nel documento unico restano solo le ancore.
                    anchor = node.attrs["href"].partition("#")[2]
                    if anchor:
                        node.attrs["href"] = "#" + anchor
                    else:
                        node.attrs.pop("href", None)
            body.children.extend(chapter.children)
        if not body.children:
            raise ArchiveError("The EPUB has no readable chapters.")
        missing = sum(1 for node in body.iter()
                      if node.tag == "img" and node.attrs.get("src", "").startswith("epub:")
                      and node.attrs["src"] not in document.images)
        if missing:
            document.notes.append(f"{missing} image(s) of the EPUB could not be read.")
        return document


# --- ODT ------------------------------------------------------------------------

_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
}


def _q(prefix: str, name: str) -> str:
    return f"{{{_NS[prefix]}}}{name}"


class _OdtStyles:
    """Stili ODT risolti per nome (con ereditarietà parent-style-name)."""

    _MONO = re.compile(r"mono|courier|consol|menlo|liberation mono|dejavu sans mono", re.I)

    def __init__(self, roots) -> None:
        self.raw: dict[str, tuple[dict[str, str], str, str]] = {}
        self.list_ordered: dict[str, bool] = {}
        for root in roots:
            for el in root.iter():
                tag = el.tag
                if tag == _q("style", "style"):
                    props: dict[str, str] = {}
                    for child in el:
                        if child.tag in (_q("style", "text-properties"), _q("style", "paragraph-properties")):
                            for key, value in child.attrib.items():
                                props[_local(key)] = value
                    self.raw[el.get(_q("style", "name"), "")] = (
                        props, el.get(_q("style", "parent-style-name"), ""),
                        el.get(_q("style", "display-name"), "") or el.get(_q("style", "name"), ""))
                elif tag == _q("text", "list-style"):
                    first = next(iter(el), None)
                    self.list_ordered[el.get(_q("style", "name"), "")] = \
                        first is not None and first.tag == _q("text", "list-level-style-number")

    def props(self, name: str) -> dict[str, str]:
        merged: dict[str, str] = {}
        seen = set()
        while name and name in self.raw and name not in seen:
            seen.add(name)
            props, parent, _display = self.raw[name]
            merged = {**props, **merged}
            name = parent
        return merged

    def names(self, name: str) -> list[str]:
        out, seen = [], set()
        while name and name in self.raw and name not in seen:
            seen.add(name)
            out.append(self.raw[name][2].lower())
            name = self.raw[name][1]
        return out

    def inline_tags(self, name: str) -> list[str]:
        props = self.props(name)
        tags = []
        if props.get("font-weight") in ("bold", "600", "700", "800", "900"):
            tags.append("strong")
        if props.get("font-style") in ("italic", "oblique"):
            tags.append("em")
        if props.get("text-underline-style", "none") != "none":
            tags.append("u")
        if props.get("text-line-through-style", "none") != "none":
            tags.append("s")
        position = props.get("text-position", "")
        if position.startswith("super") or (position[:1] not in ("", "0", "-") and position[0].isdigit()):
            tags.append("sup")
        elif position.startswith("sub") or position.startswith("-"):
            tags.append("sub")
        if self._MONO.search(props.get("font-name", "") + " " + props.get("font-family", "")):
            tags.append("code")
        return tags

    def paragraph_kind(self, name: str) -> str:
        names = self.names(name)
        joined = " ".join(names)
        if "preformatted" in joined or "source text" in joined:
            return "pre"
        if "horizontal line" in joined:
            return "hr"
        if "quotation" in joined or "quote" in joined:
            return "blockquote"
        if "subtitle" in names:
            return "h2"
        if "title" in names:
            return "h1"
        return "p"


def read_odt(path: Path) -> MarkupDocument:
    with _open_archive(path) as archive:
        names = set(archive.namelist())
        if "content.xml" not in names:
            raise ArchiveError("Not an OpenDocument text file: content.xml is missing.")
        content = _xml(archive.read("content.xml"))
        roots = [content]
        if "styles.xml" in names:
            roots.insert(0, _xml(archive.read("styles.xml")))
        reader = _OdtReader(_OdtStyles(roots), archive, names)
        office_text = next((el for el in content.iter() if el.tag == _q("office", "text")), None)
        if office_text is None:
            raise ArchiveError("The OpenDocument file has no text body.")
        body = Node("body", children=reader.blocks(office_text))
        if reader.footnotes:
            body.children.append(Node("hr"))
            body.children.append(Node("ol", children=[Node("li", children=list(note))
                                                      for note in reader.footnotes]))
            reader.document_notes.append("Footnotes were moved to the end of the document.")
        title = ""
        if "meta.xml" in names:
            meta = _xml(archive.read("meta.xml"))
            title = next((" ".join((el.text or "").split()) for el in meta.iter()
                          if _local(el.tag) == "title" and (el.text or "").strip()), "")
        document = MarkupDocument(body=normalize(body), title=title or path.stem,
                                  images=reader.images, notes=reader.document_notes)
        return document


class _OdtReader:
    def __init__(self, styles: _OdtStyles, archive: zipfile.ZipFile, names: set[str]) -> None:
        self.styles = styles
        self.archive = archive
        self.names = names
        self.images: dict[str, tuple[bytes, str]] = {}
        self.footnotes: list[list[Node | str]] = []
        self.document_notes: list[str] = []

    def blocks(self, parent, list_style: str = "") -> list[Node | str]:
        out: list[Node | str] = []
        for el in parent:
            tag = el.tag
            if tag == _q("text", "h"):
                level = el.get(_q("text", "outline-level"), "1")
                level = max(1, min(int(level) if level.isdigit() else 1, 6))
                out.append(Node(f"h{level}", children=self.inline(el)))
            elif tag == _q("text", "p"):
                kind = self.styles.paragraph_kind(el.get(_q("text", "style-name"), ""))
                children = self.inline(el, keep_spaces=(kind == "pre"))
                if kind == "pre":
                    # In ODT ogni riga di codice è un paragrafo: righe consecutive
                    # tornano un unico blocco preformattato.
                    last = out[-1] if out else None
                    if isinstance(last, Node) and last.tag == "pre":
                        last.children[0].children.extend(["\n", *children])
                    else:
                        out.append(Node("pre", children=[Node("code", children=children)]))
                elif kind == "hr":
                    out.append(Node("hr"))
                elif kind == "blockquote":
                    out.append(Node("blockquote", children=[Node("p", children=children)]))
                else:
                    out.append(Node(kind, children=children))
                out.extend(self.frames(el))
            elif tag == _q("text", "list"):
                out.append(self.list_node(el, list_style))
            elif tag == _q("table", "table"):
                out.append(self.table(el))
            elif tag in (_q("text", "section"), _q("text", "index-body")):
                out.extend(self.blocks(el, list_style))
            elif tag in (_q("text", "table-of-content"), _q("text", "alphabetical-index"),
                         _q("text", "illustration-index"), _q("text", "bibliography")):
                for child in el:
                    if child.tag == _q("text", "index-body"):
                        out.extend(self.blocks(child, list_style))
        return out

    def list_node(self, el, inherited: str) -> Node:
        style = el.get(_q("text", "style-name"), "") or inherited
        ordered = self.styles.list_ordered.get(style, False)
        node = Node("ol" if ordered else "ul")
        for item in el:
            if item.tag not in (_q("text", "list-item"), _q("text", "list-header")):
                continue
            node.children.append(Node("li", children=self.blocks(item, style)))
        return node

    def table(self, el) -> Node:
        table = Node("table")
        header = Node("thead")
        body = Node("tbody")

        def rows(parent, target: Node, cell_tag: str) -> None:
            for row in parent:
                if row.tag == _q("table", "table-header-rows"):
                    rows(row, header, "th")
                elif row.tag in (_q("table", "table-rows"), _q("table", "table-row-group")):
                    rows(row, target, cell_tag)
                elif row.tag == _q("table", "table-row"):
                    tr = Node("tr")
                    for cell in row:
                        if cell.tag != _q("table", "table-cell"):
                            continue          # covered-table-cell: coperta da un'unione
                        attrs = {}
                        span = cell.get(_q("table", "number-columns-spanned"), "1")
                        rspan = cell.get(_q("table", "number-rows-spanned"), "1")
                        if span.isdigit() and int(span) > 1:
                            attrs["colspan"] = span
                        if rspan.isdigit() and int(rspan) > 1:
                            attrs["rowspan"] = rspan
                        # Le celle vuote ripetute (number-columns-repeated) in coda
                        # sono solo il bordo della griglia di LibreOffice: non contano.
                        repeat = cell.get(_q("table", "number-columns-repeated"), "1")
                        content = self.blocks(cell)
                        count = int(repeat) if repeat.isdigit() and content else 1
                        for _ in range(min(count, 64)):
                            tr.children.append(Node(cell_tag, dict(attrs), list(content)))
                    while tr.children and not tr.children[-1].children:
                        tr.children.pop()
                    if tr.children:
                        target.children.append(tr)

        rows(el, body, "td")
        if header.children:
            table.children.append(header)
        table.children.append(body)
        return table

    def frames(self, el) -> list[Node]:
        """Immagini in draw:frame ancorate al paragrafo (non inline)."""
        out = []
        for frame in el:
            if frame.tag == _q("draw", "frame"):
                image = self.image(frame)
                if image is not None:
                    out.append(Node("p", children=[image]))
        return out

    def image(self, frame) -> Node | None:
        for child in frame.iter():
            if child.tag == _q("draw", "image"):
                href = child.get(_q("xlink", "href"), "")
                if href in self.names:
                    key = f"odt:{href}"
                    if key not in self.images:
                        data = self.archive.read(href)
                        ext = image_ext(data)
                        if ext:
                            self.images[key] = (data, ext)
                    alt = next(("".join(t.itertext()).strip() for t in frame.iter()
                                if t.tag in (_q("svg", "title"), _q("svg", "desc"))), "")
                    return Node("img", {"src": key, "alt": alt or "image"})
        return None

    def inline(self, el, *, keep_spaces: bool = False) -> list[Node | str]:
        out: list[Node | str] = []
        if el.text:
            out.append(el.text)
        for child in el:
            tag = child.tag
            if tag == _q("text", "span"):
                inner = self.inline(child, keep_spaces=keep_spaces)
                for wrapper in reversed(self.styles.inline_tags(child.get(_q("text", "style-name"), ""))):
                    inner = [Node(wrapper, children=inner)]
                out.extend(inner)
            elif tag == _q("text", "a"):
                attrs = {}
                href = child.get(_q("xlink", "href"), "")
                if href:
                    attrs["href"] = href
                from .tree import safe_url

                if "href" in attrs and not safe_url(attrs["href"]):
                    attrs.pop("href")
                out.append(Node("a", attrs, self.inline(child, keep_spaces=keep_spaces)))
            elif tag == _q("text", "s"):
                count = child.get(_q("text", "c"), "1")
                out.append(" " * (int(count) if count.isdigit() else 1))
            elif tag == _q("text", "tab"):
                out.append("\t")
            elif tag == _q("text", "line-break"):
                out.append(Node("br"))
            elif tag == _q("text", "note"):
                body = next((c for c in child if c.tag == _q("text", "note-body")), None)
                if body is not None:
                    self.footnotes.append(self.blocks(body))
                    out.append(Node("sup", children=[str(len(self.footnotes))]))
            elif tag == _q("draw", "frame"):
                image = self.image(child)
                if image is not None:
                    out.append(image)
            elif tag in (_q("text", "bookmark"), _q("text", "bookmark-start"),
                         _q("text", "bookmark-end"), _q("text", "soft-page-break"),
                         _q("office", "annotation"), _q("office", "annotation-end")):
                pass
            else:
                out.extend(self.inline(child, keep_spaces=keep_spaces))
            if child.tail:
                out.append(child.tail)
        return out
