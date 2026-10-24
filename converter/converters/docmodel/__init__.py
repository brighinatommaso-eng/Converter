"""Motore documentale strutturale: un modello (IR), lettori e scrittori.

    read_pdf / read_docx  ->  Document  ->  write_docx / write_markdown / write_text / write_html

`converters/textdoc.py` è l'unico punto che parla col registry. Questo package
non conosce né il registry né il job: è una libreria, come _ffmpeg/_libreoffice.

Punto d'innesto AI (opzionale, spento): un raffinatore riceve il Document dopo la
lettura e ne restituisce uno con la stessa forma (titoli, ordine di lettura,
tabelle). Nessun codice AI è attivo qui: il motore deterministico deve restare
la base riproducibile, e l'app promette di non usare la rete.
"""

from __future__ import annotations

from .docxreader import read_docx
from .pdfreader import read_pdf
from .types import (
    Document,
    ImageBlock,
    Line,
    ListBlock,
    ListItem,
    PageBreak,
    Paragraph,
    Run,
    Table,
    TableCell,
)
from .writers import write_docx, write_html, write_markdown, write_text

__all__ = [
    "Document", "ImageBlock", "Line", "ListBlock", "ListItem", "PageBreak",
    "Paragraph", "Run", "Table", "TableCell",
    "read_docx", "read_pdf",
    "write_docx", "write_html", "write_markdown", "write_text",
]
