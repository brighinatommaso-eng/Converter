"""Motore dei formati markup, in-process (sostituisce pandoc).

    read_markdown / read_html / read_epub / read_odt / read_text  →  MarkupDocument
    MarkupDocument  →  write_markdown / write_text / write_html / write_docx /
                       write_odt / write_rtf / write_epub

L'albero intermedio è un HTML ridotto e ripulito (tree.py). Come docmodel, il
package non conosce né il registry né i job: converters/markup.py è l'unico
punto che parla col registry.
"""

from __future__ import annotations

from .readers import ArchiveError, read_epub, read_html, read_markdown, read_odt, read_text
from .tree import MarkupDocument, Node
from .writers_docx import write_docx
from .writers_epub import write_epub
from .writers_odt import write_odt
from .writers_rtf import write_rtf
from .writers_text import write_html, write_markdown, write_text

READERS = {"md": read_markdown, "html": read_html, "epub": read_epub, "odt": read_odt,
           "txt": read_text}
WRITERS = {"md": write_markdown, "txt": write_text, "html": write_html, "docx": write_docx,
           "odt": write_odt, "rtf": write_rtf, "epub": write_epub}

__all__ = ["READERS", "WRITERS", "ArchiveError", "MarkupDocument", "Node"]
