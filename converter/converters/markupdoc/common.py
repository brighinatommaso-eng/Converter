"""Pezzi comuni agli scrittori: inline "appiattiti" e blocchi di un contenitore."""

from __future__ import annotations

from dataclasses import dataclass, field

from .tree import BLOCK_TAGS, HEADINGS, MarkupDocument, Node, safe_url

MARKS = ("strong", "em", "u", "s", "code", "sup", "sub")


@dataclass(slots=True)
class Segment:
    """Un pezzo di testo con uno stile uniforme, un a capo o un'immagine."""

    kind: str                                   # "text" | "br" | "img"
    text: str = ""
    marks: frozenset[str] = frozenset()
    href: str = ""
    src: str = ""


def segments(children, marks: frozenset[str] = frozenset(), href: str = "") -> list[Segment]:
    out: list[Segment] = []
    for child in children:
        if isinstance(child, str):
            if child:
                if out and out[-1].kind == "text" and out[-1].marks == marks and out[-1].href == href:
                    out[-1].text += child
                else:
                    out.append(Segment("text", child, marks, href))
            continue
        tag = child.tag
        if tag == "br":
            out.append(Segment("br", marks=marks, href=href))
        elif tag == "img":
            out.append(Segment("img", child.attrs.get("alt", ""), marks, href, child.attrs.get("src", "")))
        elif tag == "a":
            out.extend(segments(child.children, marks, safe_url(child.attrs.get("href", "")) or href))
        elif tag in MARKS:
            out.extend(segments(child.children, marks | {tag}, href))
        elif tag in BLOCK_TAGS:
            # Un blocco dentro un inline (non dovrebbe succedere dopo normalize):
            # se ne tiene il testo, separato da un a capo.
            if out:
                out.append(Segment("br", marks=marks, href=href))
            out.extend(segments(child.children, marks, href))
        else:
            out.extend(segments(child.children, marks, href))
    return out


def as_blocks(node: Node) -> list[Node]:
    """I blocchi di un contenitore (li, td, blockquote…): se ha solo inline, un <p>."""
    if any(isinstance(c, Node) and c.tag in BLOCK_TAGS for c in node.children):
        return [c for c in node.children if isinstance(c, Node)]
    if not node.children:
        return []
    return [Node("p", children=list(node.children))]


def heading_level(tag: str) -> int:
    return HEADINGS.index(tag) + 1 if tag in HEADINGS else 0


def table_grid(table: Node) -> tuple[list[list[Node | None]], int, bool]:
    """Righe della tabella come griglia logica (celle unite = None sulle posizioni
    coperte), numero di righe d'intestazione e se c'erano unioni."""
    rows: list[Node] = []
    header_rows = 0
    for section in table.children:
        if not isinstance(section, Node):
            continue
        if section.tag == "tr":
            rows.append(section)
        elif section.tag in ("thead", "tbody", "tfoot"):
            section_rows = [r for r in section.children if isinstance(r, Node) and r.tag == "tr"]
            if section.tag == "thead":
                header_rows += len(section_rows)
            rows.extend(section_rows)
    if not header_rows and rows and all(isinstance(c, Node) and c.tag == "th"
                                        for c in rows[0].children):
        header_rows = 1
    grid: list[list[Node | None]] = []
    covered: dict[tuple[int, int], bool] = {}
    merged = False
    for r, row in enumerate(rows):
        line: list[Node | None] = []
        col = 0
        for cell in row.children:
            if not isinstance(cell, Node):
                continue
            while covered.get((r, col)):
                line.append(None)
                col += 1
            colspan = int(cell.attrs.get("colspan", "1") or 1)
            rowspan = int(cell.attrs.get("rowspan", "1") or 1)
            if colspan > 1 or rowspan > 1:
                merged = True
            line.append(cell)
            for dr in range(rowspan):
                for dc in range(colspan):
                    if dr or dc:
                        covered[(r + dr, col + dc)] = True
            col += 1
            for _ in range(colspan - 1):
                line.append(None)
                col += 1
        while covered.get((r, col)):
            line.append(None)
            col += 1
        grid.append(line)
    width = max((len(line) for line in grid), default=0)
    for line in grid:
        line.extend([None] * (width - len(line)))
    return grid, header_rows, merged


@dataclass
class WriteReport:
    """Ciò che uno scrittore non ha potuto rappresentare: diventa verdetto a runtime."""

    dropped_images: int = 0
    notes: list[str] = field(default_factory=list)

    def note(self, text: str) -> None:
        if text not in self.notes:
            self.notes.append(text)

    def all_notes(self, document: MarkupDocument) -> list[str]:
        notes = list(document.notes) + list(self.notes)
        if self.dropped_images:
            notes.append(f"{self.dropped_images} image(s) could not be embedded "
                         "(remote images are never downloaded: the app works offline).")
        return notes


def image_bytes(document: MarkupDocument, src: str) -> tuple[bytes, str] | None:
    return document.images.get(src)
