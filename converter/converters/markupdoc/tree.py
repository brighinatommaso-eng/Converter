"""Albero documentale dei formati markup: un HTML ridotto e ripulito.

È l'equivalente (molto più piccolo) dell'AST di pandoc: ogni lettore (md, html,
epub, odt, txt) produce questo albero e ogni scrittore (md, txt, html, docx, odt,
rtf, epub) lo percorre. Si sceglie l'HTML perché markdown-it rende il Markdown in
HTML secondo le regole di riferimento CommonMark/GFM ed EPUB è già XHTML: così due
lettori su cinque non devono inventare nulla.

L'albero tiene solo i tag che uno scrittore sa rappresentare (elenco qui sotto):
tutto il resto viene "srotolato" (si tengono i figli) oppure, per script/style e
simili, scartato con il contenuto. Gli attributi sopravvissuti sono pochi e
controllati: un documento non fidato non può portare JavaScript nell'output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Blocchi e inline che gli scrittori conoscono. Qualunque altro tag diventa i suoi figli.
BLOCK_TAGS = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "blockquote", "pre",
    "table", "thead", "tbody", "tfoot", "tr", "th", "td", "hr", "figure", "figcaption",
    "dl", "dt", "dd",
})
INLINE_TAGS = frozenset({"strong", "em", "u", "s", "code", "a", "sup", "sub", "br", "img"})
KEPT_TAGS = BLOCK_TAGS | INLINE_TAGS
VOID_TAGS = frozenset({"br", "hr", "img"})
# Tutti gli elementi HTML senza tag di chiusura: contano per sapere quando finisce
# un blocco scartato. Senza, un <meta> dentro <head> lasciava il contatore aperto
# e l'intero <body> veniva buttato (l'HTML scritto da noi si rileggeva vuoto).
_HTML_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
                        "meta", "param", "source", "track", "wbr", "keygen", "basefont", "frame"})
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Sinonimi HTML → tag canonico.
_ALIASES = {"b": "strong", "i": "em", "cite": "em", "var": "em", "dfn": "em",
            "del": "s", "strike": "s", "ins": "u", "kbd": "code", "samp": "code",
            "tt": "code", "mark": "strong", "caption": "figcaption"}
# Contenuto da buttare insieme al tag: non è testo del documento.
_DROP_WITH_CONTENT = frozenset({"script", "style", "head", "title", "noscript", "template",
                                "iframe", "object", "embed", "svg", "math", "canvas",
                                "select", "textarea", "button", "nav"})
# Blocchi "contenitore" che non hanno un equivalente: srotolati come paragrafi.
_PARAGRAPH_LIKE = frozenset({"div", "section", "article", "main", "header", "footer",
                             "aside", "address", "center", "details", "summary"})
_ALLOWED_ATTRS = {"a": ("href",), "img": ("src", "alt"), "ol": ("start",),
                  "td": ("colspan", "rowspan", "align"), "th": ("colspan", "rowspan", "align"),
                  "code": ("class",), "pre": ("class",)}
_SAFE_SCHEMES = ("http", "https", "mailto", "tel")


@dataclass(slots=True)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)

    def text(self) -> str:
        """Testo piatto del sottoalbero (gli a capo <br> diventano \\n)."""
        out: list[str] = []
        for child in self.children:
            if isinstance(child, str):
                out.append(child)
            elif child.tag == "br":
                out.append("\n")
            elif child.tag == "img":
                out.append(child.attrs.get("alt", ""))
            else:
                out.append(child.text())
        return "".join(out)

    def iter(self):
        """Visita in profondità di tutti i nodi (sé compreso)."""
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()


@dataclass(slots=True)
class MarkupDocument:
    body: Node
    title: str = ""
    language: str = ""
    # Immagini incorporate: src (come compare nell'albero) → (byte, estensione).
    # I lettori le risolvono (file locale accanto alla sorgente, risorsa dell'EPUB,
    # data-URI); gli scrittori non toccano mai il disco o la rete per trovarle.
    images: dict[str, tuple[bytes, str]] = field(default_factory=dict)
    # Ciò che la lettura ha perso: finisce nel verdetto a runtime.
    notes: list[str] = field(default_factory=list)
    # Testo semplice: gli spazi multipli e i rientri sono contenuto, non layout
    # (gli scrittori HTML/EPUB usano white-space:pre-wrap invece di fonderli).
    preserve_spaces: bool = False


def safe_url(url: str) -> str:
    """Solo link che non eseguono codice: http(s)/mailto/tel o percorsi relativi."""
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith(("#", "/", "./", "../")):
        return url
    match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*):", url)
    if not match:
        return url                       # relativo senza schema: "foto.png", "a/b.html"
    return url if match.group(1).lower() in _SAFE_SCHEMES else ""


def _clean_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> dict[str, str]:
    allowed = _ALLOWED_ATTRS.get(tag, ())
    out: dict[str, str] = {}
    if tag in ("td", "th"):
        # L'allineamento delle colonne (GFM ":-:") arriva come style="text-align:…":
        # dello stile si tiene solo questo, normalizzato in un attributo "align".
        for name, value in attrs:
            if name.lower() == "style" and value:
                match = re.search(r"text-align\s*:\s*(left|center|right)", value, re.I)
                if match:
                    out["align"] = match.group(1).lower()
    for name, value in attrs:
        name = name.lower()
        if name not in allowed or value is None:
            continue
        if name == "href":
            value = safe_url(value)
            if not value:
                continue
        elif name == "src":
            value = value.strip()
            if value.lower().startswith("javascript:"):
                continue
        elif name == "align":
            value = value.strip().lower()
            if value not in ("left", "center", "right"):
                continue
        elif name in ("colspan", "rowspan", "start"):
            if not value.strip().isdigit():
                continue
            value = str(max(1, min(int(value.strip()), 1000)))
        out[name] = value
    return out


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("body")
        self.stack: list[Node] = [self.root]
        self.drop_depth = 0
        self.title_parts: list[str] = []
        self.in_title = False
        self.language = ""

    # Un tag di chiusura chiude anche quelli rimasti aperti sopra di lui (HTML
    # reale non chiude sempre <p> o <li>); uno sconosciuto viene ignorato.
    def _close(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "html":
            for name, value in attrs:
                if name.lower() == "lang" and value:
                    self.language = value.strip()
        if tag == "title":
            self.in_title = True
        if self.drop_depth or tag in _DROP_WITH_CONTENT:
            if tag not in _HTML_VOID:
                self.drop_depth += 1
            return
        if tag in _HTML_VOID and tag not in VOID_TAGS:
            return                      # meta, link, input…: nessun contenuto da tenere
        tag = _ALIASES.get(tag, tag)
        if tag in _PARAGRAPH_LIKE:
            tag = "p" if not self._inside_paragraph() else ""
        if tag not in KEPT_TAGS:
            return
        # Un nuovo blocco chiude il <p> aperto (come fa il browser).
        if tag in BLOCK_TAGS and tag not in ("li", "td", "th", "tr", "dt", "dd"):
            self._close_open_paragraph()
        if tag == "li":
            self._close_sibling("li", ("ul", "ol"))
        elif tag in ("td", "th"):
            self._close_sibling(("td", "th"), ("tr",))
        elif tag == "tr":
            self._close_sibling("tr", ("table", "thead", "tbody", "tfoot"))
        node = Node(tag, _clean_attrs(tag, attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _HTML_VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag in _HTML_VOID:
            return                      # </br> e simili: niente da chiudere
        if self.drop_depth:
            self.drop_depth -= 1
            return
        tag = _ALIASES.get(tag, tag)
        if tag in _PARAGRAPH_LIKE:
            tag = "p"
        if tag in KEPT_TAGS and tag not in VOID_TAGS:
            self._close(tag)

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)
        if self.drop_depth or not data:
            return
        self.stack[-1].children.append(data)

    def _inside_paragraph(self) -> bool:
        return any(node.tag in ("p", "li", "td", "th", "dd", "dt") + HEADINGS for node in self.stack)

    def _close_open_paragraph(self) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            node = self.stack[index]
            if node.tag == "p":
                del self.stack[index:]
                return
            if node.tag in BLOCK_TAGS:
                return

    def _close_sibling(self, tags, parents) -> None:
        tags = (tags,) if isinstance(tags, str) else tags
        for index in range(len(self.stack) - 1, 0, -1):
            node = self.stack[index]
            if node.tag in parents:
                return
            if node.tag in tags:
                del self.stack[index:]
                return


def parse_html(markup: str) -> tuple[Node, str, str]:
    """HTML (anche sporco) → (body ripulito, titolo, lingua)."""
    builder = _TreeBuilder()
    builder.feed(markup)
    builder.close()
    body = normalize(builder.root)
    return body, " ".join("".join(builder.title_parts).split()), builder.language


# --- Normalizzazione ------------------------------------------------------------

_WS = re.compile(r"[ \t\r\n\f]+")


def _is_blank(child: Node | str) -> bool:
    return isinstance(child, str) and not child.strip()


def normalize(node: Node, *, preformatted: bool = False) -> Node:
    """Spazi come li mostra un browser, testo sciolto dentro paragrafi, nodi vuoti via.

    Dopo questo passo ogni contenitore di blocchi (body, li, blockquote, td…) ha
    figli che sono o blocchi o — solo in li/td/th/dd — inline: gli scrittori non
    devono indovinare.
    """
    pre = preformatted or node.tag == "pre"
    children: list[Node | str] = []
    for child in node.children:
        if isinstance(child, str):
            children.append(child if pre else _WS.sub(" ", child))
        else:
            normalized = normalize(child, preformatted=pre)
            if _keep(normalized):
                children.append(normalized)
    node.children = _hoist_blocks(_merge_text(children, pre=pre), node.tag)
    if node.tag in ("body", "blockquote", "figure"):
        node.children = _wrap_inline(node.children)
    elif node.tag in ("ul", "ol"):
        node.children = [c for c in node.children if isinstance(c, Node) and c.tag == "li"] or \
                        [Node("li", children=[c]) for c in node.children if not _is_blank(c)]
    elif node.tag in ("table", "thead", "tbody", "tfoot"):
        node.children = [c for c in node.children if isinstance(c, Node)]
    elif node.tag == "tr":
        node.children = [c for c in node.children if isinstance(c, Node) and c.tag in ("td", "th")]
    elif node.tag in ("li", "td", "th", "dd") and _has_block(node.children):
        node.children = _wrap_inline(node.children)
    if not pre and node.tag not in INLINE_TAGS:
        _trim(node)
    return node


def _hoist_blocks(children: list[Node | str], parent: str) -> list[Node | str]:
    """Un blocco finito dentro un paragrafo o un titolo (HTML sporco) ne esce.

    `<p>testo<ul>…</ul>altro</p>` diventa p · ul · p, come lo mostra il browser;
    un `<li>` fuori da un elenco è un paragrafo. Così ogni p/h contiene solo inline."""
    out: list[Node | str] = []
    for child in children:
        if isinstance(child, Node) and child.tag == "li" and parent not in ("ul", "ol"):
            child.tag = "p"
        if isinstance(child, Node) and child.tag in ("p",) + HEADINGS and _has_block(child.children):
            pending: list[Node | str] = []
            for grandchild in child.children:
                if isinstance(grandchild, Node) and grandchild.tag in BLOCK_TAGS:
                    if any(isinstance(c, Node) or c.strip() for c in pending):
                        piece = Node(child.tag, dict(child.attrs), list(pending))
                        _trim(piece)
                        out.append(piece)
                    pending = []
                    out.append(grandchild)
                else:
                    pending.append(grandchild)
            if any(isinstance(c, Node) or c.strip() for c in pending):
                piece = Node(child.tag, dict(child.attrs), pending)
                _trim(piece)
                out.append(piece)
        else:
            out.append(child)
    return out


def _keep(node: Node) -> bool:
    if node.tag in VOID_TAGS:
        return node.tag != "img" or bool(node.attrs.get("src"))
    if node.tag in ("td", "th", "tr", "li"):
        return True                       # le celle vuote tengono il posto in tabella
    return any(isinstance(c, Node) or c.strip() for c in node.children) or \
        (node.tag == "pre" and bool(node.children))


def _merge_text(children: list[Node | str], *, pre: bool = False) -> list[Node | str]:
    out: list[Node | str] = []
    for child in children:
        if isinstance(child, str) and out and isinstance(out[-1], str):
            out[-1] += child
        else:
            out.append(child)
    if not pre:
        # Lo spazio attorno a un <br> non si vede nel browser: via anche qui
        # ("a capo<br>\nforzato" non deve diventare "a capo⏎ forzato").
        for index, child in enumerate(out):
            if isinstance(child, Node) and child.tag == "br":
                if index and isinstance(out[index - 1], str):
                    out[index - 1] = out[index - 1].rstrip(" ")
                if index + 1 < len(out) and isinstance(out[index + 1], str):
                    out[index + 1] = out[index + 1].lstrip(" ")
    return out


def _has_block(children) -> bool:
    return any(isinstance(c, Node) and c.tag in BLOCK_TAGS for c in children)


def _wrap_inline(children: list[Node | str]) -> list[Node | str]:
    """Inline sciolti in un contenitore di blocchi → raccolti in <p>."""
    out: list[Node | str] = []
    pending: list[Node | str] = []

    def flush() -> None:
        if any(isinstance(c, Node) or c.strip() for c in pending):
            paragraph = Node("p", children=list(pending))
            _trim(paragraph)
            out.append(paragraph)
        pending.clear()

    for child in children:
        if isinstance(child, Node) and child.tag in BLOCK_TAGS:
            flush()
            out.append(child)
        else:
            pending.append(child)
    flush()
    return out


def _trim(node: Node) -> None:
    """Toglie lo spazio iniziale/finale di un blocco (non dentro gli inline)."""
    while node.children and _is_blank(node.children[0]):
        node.children.pop(0)
    while node.children and _is_blank(node.children[-1]):
        node.children.pop()
    if node.children and isinstance(node.children[0], str):
        node.children[0] = node.children[0].lstrip()
    if node.children and isinstance(node.children[-1], str):
        node.children[-1] = node.children[-1].rstrip()


def text_document(text: str) -> Node:
    """Testo semplice → paragrafi (righe vuote) con gli a capo come <br>."""
    body = Node("body")
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    blocks = re.split(r"\n[ \t]*\n", text)
    for block in blocks:
        lines = block.strip("\n").split("\n")
        if not any(line.strip() for line in lines):
            continue
        paragraph = Node("p")
        for index, line in enumerate(lines):
            if index:
                paragraph.children.append(Node("br"))
            paragraph.children.append(line)
        body.children.append(paragraph)
    return body
