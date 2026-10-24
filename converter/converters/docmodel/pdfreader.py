"""Lettore PDF → IR, basato su pdfminer.six (MIT, già bundlato).

Il PDF non contiene struttura: contiene caratteri a coordinate. Questo modulo la
RICOSTRUISCE, invece di limitarsi a estrarre testo:

  1. righe fisiche con stile per-run (font, corpo, grassetto/corsivo, colore);
  2. tabelle dalle linee di griglia disegnate (LTLine/LTRect, unite in componenti);
  3. ordine di lettura con xy-cut ricorsivo (colonne, titoli a tutta larghezza);
  4. paragrafi per prossimità verticale + rientro;
  5. titoli per gerarchia di corpi (e numerazione "1.2"), liste dai proiettili;
  6. intestazioni/piè di pagina ripetuti, rimossi solo se davvero ripetuti.

Scelta di licenza: pdfminer (MIT) e non PyMuPDF (AGPL) — l'app distribuisce un
eseguibile MIT e PyMuPDF è escluso dal bundle. Tutto ciò che serve (geometria,
font, immagini, linee) pdfminer ce l'ha.
"""

from __future__ import annotations

import re
import tempfile
from bisect import bisect_right
from pathlib import Path

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
    is_bullet,
)

# --- Tarature geometriche (punti tipografici) -------------------------------
_RULE_THICKNESS = 2.5      # una linea più sottile di così è una riga di griglia
_MIN_RULE_LEN = 15.0
_RULE_TOUCH = 2.0          # tolleranza perché due segmenti si considerino connessi
_GRID_TOL = 2.5            # tolleranza per fondere due coordinate di griglia
_COLUMN_GAP = 10.0         # spazio orizzontale che separa due colonne di testo
_BLOCK_GAP = 8.0           # spazio verticale minimo per un taglio orizzontale
_MARGIN_BAND = 0.06        # fascia superiore/inferiore per intestazioni e piè
_CELL_GAP = 8.0            # vuoto tra parole che può separare due celle
_COLUMN_TOL = 18.0         # tolleranza per riconoscere la stessa colonna
_ROW_TOL = 2.5             # tolleranza verticale per la stessa riga di tabella
_FRAGMENT_GAP = 40.0       # distacco massimo di un proiettile dal suo testo

_ORDERED_RE = re.compile(r"^(\(?\d{1,3}[.)]|[a-zA-Z][.)]|[IVXLC]{1,5}[.)])\s+\S")
_NUMBERED_RE = re.compile(r"^(\d{1,3}(?:\.\d{1,3})*)\.?\s+\S")
_CHAPTER_RE = re.compile(r"^(?:chapter|capitolo|part|parte|captulo)\s+[\dIVXLC]+", re.IGNORECASE)
_NUMERIC_CELL_RE = re.compile(r"^[\s\d.,%+\-–—/()]+$")
_CAPTION_RE = re.compile(r"^(table|tabella|tableau|tabela)\b", re.IGNORECASE)

_SUBSET_RE = re.compile(r"^[A-Z0-9]{1,6}\+")
_STYLE_SUFFIXES = (
    "bolditalic", "boldoblique", "bold", "italic", "oblique", "regular", "roman",
    "light", "medium", "black", "heavy", "semibold", "extrabold", "thin",
)


# --- Pulizia di font, testo e colori ---------------------------------------

def _split_font(fontname: str) -> tuple[str, bool, bool]:
    """('BAAAAA+Calibri-Bold', ...) -> ('Calibri', bold=True, italic=False)."""
    name = _SUBSET_RE.sub("", fontname or "")
    low = name.lower()
    bold = any(k in low for k in ("bold", "black", "heavy", "semibold"))
    italic = "italic" in low or "oblique" in low
    family = name.split(",")[0]
    if "-" in family:
        parts = family.split("-")
        family = parts[0]
        for part in parts[1:]:
            if part.lower() in _STYLE_SUFFIXES:
                continue
            family += " " + part
    for suffix in ("PSMT", "MT", "Std", "Pro"):
        if family.endswith(suffix) and len(family) > len(suffix) + 2:
            family = family[: -len(suffix)]
    # Nome "a parole" da nomi concatenati (TimesNewRoman): PDF spesso non ha
    # spazi nel nome interno del font, ma Word li vuole.
    family = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", family)
    family = family.strip()
    if family.lower().startswith(("symbol", "wingdings", "zapfdingbats")):
        family = ""   # font simbolici: non portano una famiglia utile
    return family, bold, italic


def _color_of(char) -> str:
    """Colore del carattere in #RRGGBB; nero/sconosciuto -> "" (default)."""
    try:
        value = char.graphicstate.ncolor
    except Exception:
        return ""
    try:
        if isinstance(value, (int, float)):
            grey = int(round(float(value) * 255))
            return "" if grey == 0 else f"#{grey:02x}{grey:02x}{grey:02x}"
        vals = [float(v) for v in value]
    except Exception:
        return ""
    if len(vals) == 3:
        r, g, b = (max(0, min(255, int(round(v * 255)))) for v in vals)
    elif len(vals) == 4:
        c, m, y, k = vals
        r = int(round(255 * (1 - c) * (1 - k)))
        g = int(round(255 * (1 - m) * (1 - k)))
        b = int(round(255 * (1 - y) * (1 - k)))
    else:
        return ""
    if (r, g, b) == (0, 0, 0):
        return ""
    return f"#{r:02x}{g:02x}{b:02x}"


def _clean_text(text: str) -> str:
    """Normalizza i glifi delle aree private (Symbol/Wingdings) in Unicode."""
    table = {
        "\uf0b7": "•", "\uf0a7": "▪", "\uf0d8": "➢", "\uf0fc": "✓",
        "\uf0a8": "◆", "\uf0e8": "➔", "\uf0b0": "°", "\uf0d7": "•",
        "\u00a0": " ", "\u200b": "",
    }
    return "".join(table.get(ch, ch) for ch in text)


# --- Oggetti pdfminer -> IR -------------------------------------------------

def _line_from_container(container) -> Line | None:
    """LTTextLine -> Line con run per stile, oppure None se è solo spazio."""
    from pdfminer.layout import LTAnno, LTChar

    runs: list[Run] = []
    words: list[tuple[float, float, str]] = []
    word_chars: list = []
    word_text = ""

    def flush_word() -> None:
        nonlocal word_text
        if word_text:
            words.append((word_chars[0].x0, word_chars[-1].x1, word_text))
        word_chars.clear()
        word_text = ""

    for char in container:
        # pdfminer rappresenta gli spazi sintetici (quelli che non esistono nel
        # content stream, ma separano due parole) come LTAnno, non come LTChar:
        # scartarli incolla tutte le parole della riga ("LayoutParser:AUnified").
        if isinstance(char, LTAnno):
            text = _clean_text(char.get_text())
            if not text or not text.isspace():
                continue
            flush_word()
            if runs:
                runs[-1].text += " "
            continue
        if not isinstance(char, LTChar):
            continue
        text = _clean_text(char.get_text())
        if not text:
            continue
        if text.isspace():
            # Lo spazio è un carattere come gli altri: saltarlo incolla le
            # parole ("Quarterlyreport2026") nei PDF che lo scrivono davvero.
            flush_word()
            if runs and not runs[-1].text.endswith(" "):
                runs[-1].text += " "
            continue
        size = float(char.size) or 1.0
        if word_chars and char.x0 - word_chars[-1].x1 > max(1.0, 0.18 * size):
            flush_word()
        word_chars.append(char)
        word_text += text

        family, bold, italic = _split_font(char.fontname)
        run_size = round(float(char.size), 1)
        color = _color_of(char)
        last = runs[-1] if runs else None
        if (last is not None and last.bold == bold and last.italic == italic
                and last.font == family and last.size == run_size and last.color == color):
            last.text += text
        else:
            runs.append(Run(text=text, bold=bold, italic=italic, font=family,
                            size=run_size, color=color))

    flush_word()

    # pdfminer spezza la riga anche su cambi di stile: ricompatta gli spazi ai
    # bordi dei run (uno solo), senza toccare quelli interni.
    cleaned: list[Run] = []
    for run in runs:
        text = re.sub(r"[ \t]{2,}", " ", run.text)
        if cleaned and cleaned[-1].text.endswith(" "):
            text = text.lstrip()
        if not text:
            continue
        run.text = text
        cleaned.append(run)
    if cleaned:
        cleaned[-1].text = cleaned[-1].text.rstrip()
    if not cleaned or not "".join(r.text for r in cleaned).strip():
        return None
    x0, y0, x1, y1 = container.bbox
    return Line(runs=cleaned, x=x0, y=y0, width=x1 - x0, height=max(y1 - y0, 0.0),
                words=words)


def _rule_segment(obj) -> tuple[str, tuple[float, float, float, float]] | None:
    """Riga di griglia orizzontale/verticale da LTLine/LTRect/LTCurve."""
    x0, y0, x1, y1 = obj.bbox
    w, h = x1 - x0, y1 - y0
    if h <= _RULE_THICKNESS and w >= _MIN_RULE_LEN:
        y = (y0 + y1) / 2
        return "h", (x0, y, x1, y)
    if w <= _RULE_THICKNESS and h >= _MIN_RULE_LEN:
        x = (x0 + x1) / 2
        return "v", (x, y0, x, y1)
    return None


def _is_marker(text: str) -> bool:
    """True se il frammento è un proiettile o un numero di elenco isolato."""
    stripped = text.strip()
    if not stripped or len(stripped) > 3:
        return False
    return (is_bullet(stripped) or stripped.rstrip(".)").isdigit()
            or stripped in "•●▪▫◦‣∙·-–—*")


def _merge_fragments(fragments: list[Line]) -> list[Line]:
    """Unisce frammenti sulla stessa base (es. proiettile Symbol + testo) in una riga.

    Restituisce righe NUOVE: mutare le righe di partenza contaminava la pagina
    (la riga "Dataset" assorbiva le parole della cella accanto e la tabella
    allineata le vedeva due volte). Anche le parole vanno concatenate, altrimenti
    la riga unita perde la geometria delle celle.
    """
    if not fragments:
        return []
    ordered = sorted(fragments, key=lambda ln: (round(ln.y, 1), ln.x))
    merged: list[Line] = []
    for line in ordered:
        if merged:
            prev = merged[-1]
            same_baseline = abs(prev.y - line.y) <= 1.5
            gap = line.x - prev.right
            # Un frammento si riattacca solo se è contiguo (cambio di font) o se
            # quello prima è un proiettile/numero: un vuoto di 12pt tra due
            # colonne non è uno spazio, e unirle distrugge l'ordine di lettura.
            # Un proiettile isolato appartiene alla colonna in cui sta: non si
            # aggancia al testo della colonna accanto solo perché è vicino.
            # Lo spazio inline è relativo al corpo (~0,6 em): a 12pt di vuoto
            # si è già passati alla colonna successiva.
            size = max(prev.size, line.size) or 10.0
            inline = gap <= min(8.0, 0.6 * size)
            close = (0 <= gap <= _FRAGMENT_GAP and not _is_marker(line.text)
                     and (inline or _is_marker(prev.text)))
            if same_baseline and close:
                if gap > 1.2 and prev.runs and not prev.runs[-1].text.endswith(" "):
                    prev.runs[-1].text = prev.runs[-1].text.rstrip() + " "
                prev.runs.extend(line.runs)
                prev.words.extend(line.words)
                prev.width = max(prev.width, line.right - prev.x)
                prev.height = max(prev.height, line.height)
                continue
        merged.append(Line(runs=list(line.runs), words=list(line.words), x=line.x,
                           y=line.y, width=line.width, height=line.height))
    return merged


def _xy_cut(items: list) -> list:
    """Ordine di lettura: colonne prima, poi blocchi; fallback alto→basso.

    Funziona sia sulle righe sia sui blocchi (paragrafi, tabelle, immagini):
    basta che abbiano `x`, `right`, `top` e `bottom`.

    Regola: un taglio verticale è valido solo se NESSUN elemento lo attraversa
    (un titolo a tutta larghezza lo blocca — ed è giusto: il taglio scende sotto
    al titolo). Se non c'è un taglio verticale, si taglia in orizzontale nel
    vuoto più ampio, così le due colonne restano blocchi separati.
    """
    if len(items) <= 1:
        return list(items)

    # Taglio verticale: la banda libera più larga nella proiezione su x.
    spans = sorted((item.x, item.right) for item in items)
    best: tuple[float, float] | None = None
    reach = spans[0][1]
    for start, end in spans[1:]:
        if start - reach >= _COLUMN_GAP:
            if best is None or (start - reach) > (best[1] - best[0]):
                best = (reach, start)
        reach = max(reach, end)
    if best is not None:
        left, right = best
        left_items = [item for item in items if item.right <= right + 0.5]
        right_items = [item for item in items if item.x >= left - 0.5]
        if left_items and right_items and len(left_items) + len(right_items) == len(items):
            return _xy_cut(left_items) + _xy_cut(right_items)

    # Taglio orizzontale: il vuoto verticale più ampio tra due fasce.
    ordered = sorted(items, key=lambda item: (item.bottom, item.x))
    bands: list[tuple[float, float, list]] = []      # (top, bottom, elementi)
    for item in ordered:
        top, bottom = item.top, item.bottom
        if bands and bottom <= bands[-1][1] + 2.0:      # stessa fascia (stessa base)
            prev_top, prev_bottom, members = bands[-1]
            bands[-1] = (max(prev_top, top), min(prev_bottom, bottom), members + [item])
        else:
            bands.append((top, bottom, [item]))
    cut_at = None
    widest = _BLOCK_GAP
    for index in range(1, len(bands)):
        gap = bands[index][1] - bands[index - 1][0]     # fondo della fascia alta - cima della bassa
        if gap > widest:
            widest, cut_at = gap, index
    if cut_at is None:
        return sorted(items, key=lambda item: (-round(item.bottom, 1), item.x))
    upper = [item for band in bands[cut_at:] for item in band[2]]
    lower = [item for band in bands[:cut_at] for item in band[2]]
    return _xy_cut(upper) + _xy_cut(lower)


# --- Tabelle ---------------------------------------------------------------

def _connected_components(segments: list[tuple[str, tuple[float, float, float, float]]]) -> list[list[int]]:
    """Raggruppa i segmenti che si toccano: una componente = una tabella."""
    parent = list(range(len(segments)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    def bbox(seg):
        _, (x0, y0, x1, y1) = seg
        return x0, y0, x1, y1

    for i in range(len(segments)):
        ax0, ay0, ax1, ay1 = bbox(segments[i])
        for j in range(i + 1, len(segments)):
            bx0, by0, bx1, by1 = bbox(segments[j])
            if (ax0 - _RULE_TOUCH <= bx1 and bx0 - _RULE_TOUCH <= ax1
                    and ay0 - _RULE_TOUCH <= by1 and by0 - _RULE_TOUCH <= ay1):
                union(i, j)

    groups: dict[int, list[int]] = {}
    for index in range(len(segments)):
        groups.setdefault(find(index), []).append(index)
    return list(groups.values())


def _cluster(values: list[float], tol: float = _GRID_TOL) -> list[float]:
    values = sorted(values)
    if not values:
        return []
    clusters = [values[0]]
    for value in values[1:]:
        if value - clusters[-1] > tol:
            clusters.append(value)
    return clusters


def _build_table(segments: list[tuple[str, tuple[float, float, float, float]]],
                 indexes: list[int], lines: list[Line],
                 page_width: float) -> tuple[Table, list[int]] | None:
    """Griglia da una componente di segmenti + le righe di testo che cadono dentro."""
    horizontals = [seg[1][1] for idx in indexes if (seg := segments[idx])[0] == "h"]
    verticals = [seg[1][0] for idx in indexes if (seg := segments[idx])[0] == "v"]
    xs = _cluster(verticals)
    ys = _cluster(horizontals)
    if len(xs) < 2 or len(ys) < 2:
        return None

    x0, x1 = xs[0] - 1.0, xs[-1] + 1.0
    y0, y1 = ys[0] - 1.0, ys[-1] + 1.0
    width, height = x1 - x0, y1 - y0
    if width < 20 or height < 8:
        return None
    if len(xs) == 2 and len(ys) == 2 and width > page_width * 0.97:
        return None  # è il bordo pagina, non una tabella

    row_count, col_count = len(ys) - 1, len(xs) - 1
    cells: list[list[list[Line]]] = [[[] for _ in range(col_count)] for _ in range(row_count)]
    used: list[int] = []
    for index, line in enumerate(lines):
        cx, cy = line.x + line.width / 2, line.y + line.height / 2
        if not (x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1):
            continue
        col = min(max(bisect_right(xs, cx) - 1, 0), col_count - 1)
        from_bottom = min(bisect_right(ys, cy) - 1, row_count - 1)
        row = row_count - 1 - from_bottom
        cells[row][col].append(line)
        used.append(index)

    if not used:
        return None   # griglia disegnata ma senza testo: non è una tabella

    texts = [paragraph.text for cell_lines in (line for row in cells for line in row)
             for paragraph in _paragraphs_from_lines(_merge_fragments(cell_lines))]
    if row_count <= 2 and col_count <= 2 and texts:
        # Un riquadro 2x2 è spesso una cornice di layout: paragrafi lunghi o
        # elenchi puntati dentro la cornice non sono celle di una tabella.
        longest = max(len(text) for text in texts)
        bullets = sum(text.count(mark) for text in texts for mark in "•●▪")
        numeric = any(_NUMERIC_CELL_RE.match(text) and any(ch.isdigit() for ch in text)
                      for text in texts)
        if longest > 300 or (bullets >= 2 and not numeric):
            return None

    rows: list[list[TableCell]] = []
    for row in cells:
        rows.append([TableCell(paragraphs=_paragraphs_from_lines(_merge_fragments(cell_lines)))
                     for cell_lines in row])

    widths = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    first_row_runs = [run for cell in rows[0] for para in cell.paragraphs
                      for line in para.lines for run in line.runs if run.text.strip()]
    header = 1 if first_row_runs and all(run.bold for run in first_row_runs) else 0
    table = Table(rows=rows, widths_pt=widths, borders=True, header_rows=header,
                  x=x0, y=y0, width=width, height=height)
    return table, used


# --- Tabelle senza griglia (allineate per spazi) ---------------------------

# (x0, x1, y, testo, grassetto, corpo, altezza)
_Cell = tuple[float, float, float, str, bool, float, float]


def _word_cell(words: list[tuple[float, float, str]], line: Line) -> _Cell:
    return (min(word[0] for word in words), max(word[1] for word in words), line.y,
            " ".join(word[2] for word in words), line.bold, line.size, line.height)


def _line_cells(line: Line) -> list[_Cell]:
    """Divide una riga fisica in celle: i vuoti grandi tra parole sono colonne."""
    if not line.words:
        text = line.text.strip()
        return [(line.x, line.right, line.y, text, line.bold, line.size, line.height)] if text else []
    gap = max(_CELL_GAP, 0.8 * (line.size or 10.0))
    cells: list[_Cell] = []
    start = 0
    for index in range(1, len(line.words)):
        if line.words[index][0] - line.words[index - 1][1] > gap:
            cells.append(_word_cell(line.words[start:index], line))
            start = index
    cells.append(_word_cell(line.words[start:], line))
    return cells


def _row_bands(lines: list[Line]) -> list[list]:
    """Righe visive: frammenti con la stessa base (una riga di tabella)."""
    bands: list[list] = []
    for line in sorted(lines, key=lambda item: (-item.y, item.x)):
        if bands and abs(bands[-1][0] - line.y) <= _ROW_TOL:
            bands[-1][1].append(line)
        else:
            bands.append([line.y, [line]])
    return bands


def _inside_tables(line: Line, tables: list[Table]) -> bool:
    cx, cy = line.x + line.width / 2, line.y + line.height / 2
    return any(table.x - 1 <= cx <= table.x + table.width + 1
               and table.y - 1 <= cy <= table.y + table.height + 1
               for table in tables)


def _assign_row(fragments: list[Line], columns: list[float]) -> list[_Cell | None]:
    """Assegna le celle alle colonne; una cella che ne copre più di una si divide."""
    groups: list[list[tuple[float, float, str, Line]]] = [[] for _ in columns]
    for fragment in sorted(fragments, key=lambda item: item.x):
        for cell in _line_cells(fragment):
            x0, x1, _, text, _, _, _ = cell
            start = min(range(len(columns)), key=lambda index: abs(x0 - columns[index]))
            spanned = [index for index, column in enumerate(columns)
                       if x0 - 1 <= column <= x1 + 1]
            if len(spanned) <= 1:
                groups[start].append((x0, x1, text, fragment))
                continue
            words = [word for word in fragment.words
                     if word[0] >= x0 - 0.5 and word[1] <= x1 + 0.5]
            if not words:
                groups[start].append((x0, x1, text, fragment))
                continue
            for word_x0, word_x1, word_text in words:
                column = min(spanned, key=lambda index: abs(word_x0 - columns[index]))
                groups[column].append((word_x0, word_x1, word_text, fragment))
    cells: list[_Cell | None] = []
    for group in groups:
        if not group:
            cells.append(None)
            continue
        fragment = group[0][3]
        cells.append((min(word[0] for word in group), max(word[1] for word in group),
                      fragment.y, " ".join(word[2] for word in group), fragment.bold,
                      fragment.size, fragment.height))
    return cells


def _merge_sparse_columns(grids: list[list[_Cell | None]],
                          columns: list[float]) -> tuple[list[list[_Cell | None]], list[float]]:
    """Fonde le colonne che compaiono in una sola riga nella colonna vicina.

    Un'intestazione unita ("Base Model1") finisce a cavallo di due colonne dei
    dati: la colonna che esiste solo nell'intestazione non è una colonna vera e
    il suo testo va nella vicina, non in una colonna vuota per tutta la tabella.
    """
    counts = [sum(1 for grid in grids if grid[index]) for index in range(len(columns))]
    kept = [index for index, count in enumerate(counts) if count >= 2]
    if not kept:
        return grids, columns
    order = list(kept)
    target = {index: position for position, index in enumerate(kept)}
    for index in range(len(columns)):
        if index in target:
            continue
        nearest = min(kept, key=lambda item: abs(columns[item] - columns[index]))
        if abs(columns[nearest] - columns[index]) <= _COLUMN_TOL * 2:
            target[index] = target[nearest]
        else:
            target[index] = len(order)
            order.append(index)

    result: list[list[_Cell | None]] = [[None] * len(order) for _ in grids]
    for index, slot in target.items():
        for row_index, grid in enumerate(grids):
            cell = grid[index]
            if cell is None:
                continue
            previous = result[row_index][slot]
            if previous is None:
                result[row_index][slot] = cell
                continue
            left, right = sorted((previous, cell), key=lambda item: item[0])
            result[row_index][slot] = (
                left[0], max(previous[1], cell[1]), left[2],
                " ".join(part for part in (left[3], right[3]) if part.strip()),
                previous[4] or cell[4], max(previous[5], cell[5]), max(previous[6], cell[6]))
    return result, [columns[index] for index in order]


def _caption_above(lines: list[Line], top: float) -> bool:
    """True se sopra la tabella c'è una didascalia "Table N"."""
    return any(0 <= line.y - top <= 40 and _CAPTION_RE.match(line.text.strip())
               for line in lines)


def _build_aligned_tables(lines: list[Line], page_height: float,
                          ruled: list[Table]) -> list[Table]:
    """Tabelle ricostruite dagli allineamenti, non dalle linee disegnate.

    Molti PDF (LaTeX booktabs, Elsevier, report Word stampati) non disegnano le
    colonne: le celle sono solo parole a coordinate. Si riconoscono per righe
    consecutive con almeno due celle e colonne che si ripetono. Le due colonne
    di testo sono protette da una guardia: senza numeri o celle corte non è una
    tabella, è un layout a colonne. Con tre o più colonne serve una didascalia,
    un'intestazione in grassetto o celle numeriche.
    """
    bands = _row_bands(lines)
    candidates: list[tuple[int, float, list[_Cell], list[Line]]] = []
    for index, (y, fragments) in enumerate(bands):
        cells: list[_Cell] = []
        for fragment in sorted(fragments, key=lambda item: item.x):
            cells.extend(_line_cells(fragment))
        cells.sort(key=lambda cell: cell[0])
        if len(cells) >= 2:
            candidates.append((index, y, cells, fragments))
    if len(candidates) < 2:
        return []

    groups: list[list[tuple[int, float, list[_Cell], list[Line]]]] = []
    for candidate in candidates:
        if groups and candidate[0] == groups[-1][-1][0] + 1:
            groups[-1].append(candidate)
        else:
            groups.append([candidate])

    tables: list[Table] = []
    for group in groups:
        if len(group) < 2:
            continue
        rows = [candidate[2] for candidate in group]
        all_cells = [cell for row in rows for cell in row]
        columns = _cluster([cell[0] for cell in all_cells], tol=_COLUMN_TOL)
        # Una colonna esiste solo se compare in almeno due righe.
        columns = [column for column in columns
                   if sum(1 for row in rows
                          if any(abs(cell[0] - column) <= _COLUMN_TOL for cell in row)) >= 2]
        if len(columns) < 2:
            continue

        # Le parole sono assegnate alle colonne: una cella unita nell'originale
        # ("Base Model1 Large Model Notes") torna nelle colonne giuste.
        cell_rows = [_assign_row(candidate[3], columns) for candidate in group]
        cell_rows, columns = _merge_sparse_columns(cell_rows, columns)
        if len(columns) < 2:
            continue
        if sum(1 for grid in cell_rows if sum(1 for cell in grid if cell) >= 2) < 2:
            continue

        texts = [cell[3] for grid in cell_rows for cell in grid if cell and cell[3].strip()]
        if not texts:
            continue
        lengths = sorted(len(text) for text in texts)
        median = lengths[len(lengths) // 2]
        numeric = sum(1 for text in texts
                      if _NUMERIC_CELL_RE.match(text) and any(ch.isdigit() for ch in text)) / len(texts)
        x0 = min(cell[0] for cell in all_cells)
        x1 = max(cell[1] for cell in all_cells)
        y0 = min(candidate[1] for candidate in group)
        y1 = max(candidate[1] + max(cell[6] for cell in candidate[2]) for candidate in group)
        width, height = x1 - x0, y1 - y0
        if width < 40 or height < 8:
            continue
        if len(columns) == 2:
            # Due righe di due colonne sono quasi sempre un'intestazione di
            # pagina (numero + testatina) o due colonne di testo: una tabella
            # vera a due colonne ha almeno tre righe e celle numeriche.
            if len(group) < 3 or numeric < 0.6:
                continue
            if height > page_height * 0.5:
                continue
        else:
            first_row = [cell for cell in cell_rows[0] if cell and cell[3].strip()]
            bold_header = bool(first_row) and all(cell[4] for cell in first_row)
            dense = (sum(1 for grid in cell_rows for cell in grid if cell)
                     / (len(cell_rows) * len(columns)))
            if _caption_above(lines, y1) or numeric >= 0.5:
                pass
            elif not (bold_header and dense >= 0.6 and median <= 15):
                # Senza didascalia né numeri serve un'intestazione in grassetto
                # con celle corte e righe piene; altrimenti sono colonne di
                # testo (o un'infografica), non una tabella.
                continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if any(table.x - 2 <= cx <= table.x + table.width + 2
               and table.y - 2 <= cy <= table.y + table.height + 2 for table in ruled):
            continue

        bounds = [x0] + [(columns[index] + columns[index + 1]) / 2
                         for index in range(len(columns) - 1)] + [x1]
        widths = [max(1.0, bounds[index + 1] - bounds[index])
                  for index in range(len(bounds) - 1)]

        output: list[list[TableCell]] = []
        for grid in cell_rows:
            row: list[TableCell] = []
            for cell in grid:
                if cell is None or not cell[3].strip():
                    row.append(TableCell(paragraphs=[]))
                    continue
                line = Line(runs=[Run(text=cell[3], bold=cell[4], size=cell[5])],
                            x=cell[0], y=cell[2], width=cell[1] - cell[0], height=cell[6])
                row.append(TableCell(paragraphs=[Paragraph(lines=[line])]))
            output.append(row)

        first_row = [cell for cell in cell_rows[0] if cell and cell[3].strip()]
        header = 1 if first_row and all(cell[4] for cell in first_row) else 0
        tables.append(Table(rows=output, widths_pt=widths, borders=False, header_rows=header,
                            x=x0, y=y0, width=width, height=height))
    return tables


# --- Paragrafi, lista, allineamento ---------------------------------------

def _merge_ok(previous: Line, current: Line) -> bool:
    size = max(previous.size, current.size, 1.0)
    gap = previous.y - (current.y + current.height)
    if gap < -2.0 or gap > 0.55 * size + 3.0:
        return False
    if current.size and previous.size and abs(current.size - previous.size) > 1.2:
        return False
    # Una nuova voce di elenco apre SEMPRE un blocco: il proiettile/numero è il
    # confine che la prossimità verticale non vede (gli item sono a 18pt).
    text = current.text.lstrip()
    if is_bullet(text) or _ORDERED_RE.match(text):
        return False
    dx = current.x - previous.x
    if dx > 6.0:
        # Rientro di continuazione: ammesso per il testo che segue un proiettile o
        # per la riga rientrata di un elenco, non per un nuovo paragrafo indentato.
        if not (is_bullet(previous.text.lstrip()) and dx <= 40.0):
            return False
    if dx < -40.0:
        return False
    return True


def _paragraphs_from_lines(lines: list[Line]) -> list[Paragraph]:
    """Raggruppa righe fisiche in paragrafi (ordine di lettura già applicato)."""
    paragraphs: list[Paragraph] = []
    current: list[Line] = []
    for line in lines:
        if current and not _merge_ok(current[-1], line):
            paragraphs.append(Paragraph(lines=current))
            current = []
        current.append(line)
    if current:
        paragraphs.append(Paragraph(lines=current))
    return paragraphs


def _detect_align(paragraph: Paragraph, text_left: float, text_right: float) -> str:
    lines = paragraph.lines
    if not lines:
        return "left"
    width = max(text_right - text_left, 1.0)
    center = (text_left + text_right) / 2
    if all(abs((ln.x + ln.width / 2) - center) <= 6.0 and ln.width < width * 0.92 for ln in lines):
        return "center"
    if len(lines) >= 2:
        body = lines[:-1]
        if all(abs(ln.right - text_right) <= 3.0 for ln in body) and lines[-1].right < text_right - 2:
            return "justify"
    if all(abs(ln.right - text_right) <= 4.0 for ln in lines) and abs(lines[0].x - text_right) > 20:
        return "right"
    return "left"


def _heading_level(paragraph: Paragraph, body_size: float, size_rank: dict[float, int]) -> int | None:
    """Livello heading (1..6) o None se è testo normale."""
    if not paragraph.lines:
        return None
    text = paragraph.text.strip()
    if not text or len(text) > 220 or len(paragraph.lines) > 3:
        return None
    size = paragraph.size or body_size
    bold = paragraph.bold
    numbered = _NUMBERED_RE.match(text)
    chapter = _CHAPTER_RE.match(text)
    if size >= body_size * 1.15:
        pass
    elif bold and size >= body_size * 1.03 and len(text) <= 120:
        pass
    elif chapter and len(text) <= 120:
        return 1
    elif numbered and bold and size >= body_size * 0.98 and len(text) <= 120:
        pass
    else:
        return None
    rank = size_rank.get(round(size, 1))
    if rank is None:
        rank = 1
    level = rank
    if numbered:
        level = max(level, min(numbered.group(1).count(".") + 1, 6))
    return max(1, min(level, 6))


def _list_marker(text: str) -> tuple[str, bool] | None:
    """(marcatore, ordinato) se il testo inizia una voce di elenco, altrimenti None."""
    stripped = text.lstrip()
    if is_bullet(stripped):
        marker = stripped[0]
        return marker, False
    match = _ORDERED_RE.match(stripped)
    if match:
        return match.group(1), True
    return None


def _strip_marker(text: str, marker: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith(marker):
        return stripped[len(marker):].lstrip()
    return stripped


# --- Intestazioni e piè di pagina -----------------------------------------

def _normalise_margin_text(text: str) -> str:
    text = re.sub(r"\d+", "#", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _drop_repeated_margins(pages: list[dict], page_height: float) -> None:
    """Toglie solo le righe di margine RIPETUTE (>=2 pagine), non i titoli di testa."""
    if len(pages) < 2:
        return
    seen: dict[str, int] = {}
    for page in pages:
        keys = set()
        for line in page["lines"]:
            top = page_height - (line.y + line.height)
            in_band = top <= page_height * _MARGIN_BAND or line.y <= page_height * _MARGIN_BAND
            if in_band:
                key = _normalise_margin_text(line.text)
                if key:
                    keys.add(key)
        for key in keys:
            seen[key] = seen.get(key, 0) + 1
    threshold = max(2, int(len(pages) * 0.8))

    for page in pages:
        kept = []
        for line in page["lines"]:
            top = page_height - (line.y + line.height)
            in_band = top <= page_height * _MARGIN_BAND or line.y <= page_height * _MARGIN_BAND
            key = _normalise_margin_text(line.text)
            if in_band and key and seen.get(key, 0) >= threshold:
                continue
            kept.append(line)
        page["lines"] = kept


# --- Lettura ---------------------------------------------------------------

def _collect_page(layout, image_writer, tmp_dir: str) -> dict:
    from pdfminer.layout import LTFigure, LTImage, LTLine, LTRect, LTTextLine

    lines: list[Line] = []
    segments: list[tuple[str, tuple[float, float, float, float]]] = []
    images: list[ImageBlock] = []

    def walk(node) -> None:
        if isinstance(node, LTTextLine):
            line = _line_from_container(node)
            if line is not None:
                lines.append(line)
            return
        if isinstance(node, LTImage):
            try:
                name = image_writer.export_image(node)
                if name:
                    path = Path(tmp_dir) / name
                    if path.exists() and path.stat().st_size > 0:
                        x0, y0, x1, y1 = node.bbox
                        images.append(ImageBlock(
                            data=path.read_bytes(), ext=path.suffix.lstrip(".").lower() or "png",
                            width_pt=x1 - x0, height_pt=y1 - y0, x=x0, y=y0))
            except Exception:
                pass   # immagine non estraibile (filtri esotici): si prosegue
            return
        if isinstance(node, (LTRect, LTLine)):
            segment = _rule_segment(node)
            if segment is not None:
                segments.append(segment)
            if isinstance(node, LTRect):
                return
        if isinstance(node, LTTextLine) or isinstance(node, LTFigure) or hasattr(node, "__iter__"):
            for child in node:
                walk(child)

    walk(layout)
    return {"lines": lines, "segments": segments, "images": images,
            "width": layout.width, "height": layout.height}


def read_pdf(path: Path) -> Document:
    """PDF → Document. Solleva ValueError con messaggio pulito se il PDF è illeggibile."""
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.image import ImageWriter
    from pdfminer.layout import LAParams
    from pdfminer.pdfdocument import PDFPasswordIncorrect
    from pdfminer.pdfinterp import PDFPageInterpreter, PDFResourceManager
    from pdfminer.pdfpage import PDFPage

    manager = PDFResourceManager(caching=True)
    pages: list[dict] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        writer = ImageWriter(tmp_dir)
        device = PDFPageAggregator(manager, laparams=LAParams())
        interpreter = PDFPageInterpreter(manager, device)
        try:
            with open(path, "rb") as handle:
                for page in PDFPage.get_pages(handle, check_extractable=False):
                    interpreter.process_page(page)
                    pages.append(_collect_page(device.get_result(), writer, tmp_dir))
        except PDFPasswordIncorrect as exc:
            raise ValueError(
                "Could not read this PDF: it is password-protected.") from exc
        except Exception as exc:
            raise ValueError(f"Could not read this PDF: {exc}") from exc

        if not pages:
            raise ValueError("Could not read this PDF: it has no pages.")

        page_width = max(p["width"] for p in pages) or 595.0
        page_height = max(p["height"] for p in pages) or 842.0
        _drop_repeated_margins(pages, page_height)
        return _assemble(pages, page_width, page_height)


def _assemble(pages: list[dict], page_width: float, page_height: float) -> Document:
    document = Document(page_width_pt=page_width, page_height_pt=page_height,
                        source_format="pdf")

    all_lines: list[Line] = [ln for page in pages for ln in page["lines"]]
    text_left = min((ln.x for ln in all_lines), default=0.0)
    text_right = max((ln.right for ln in all_lines), default=page_width)

    for page_index, page in enumerate(pages):
        lines = _merge_fragments(page["lines"])
        table_lines: set[int] = set()
        tables: list[Table] = []
        for component in _connected_components(page["segments"]):
            built = _build_table(page["segments"], component, lines, page_width)
            if built is not None:
                tables.append(built[0])
                table_lines.update(built[1])

        aligned = _build_aligned_tables(page["lines"], page_height, tables)
        tables.extend(aligned)

        free = [line for index, line in enumerate(lines)
                if index not in table_lines and not _inside_tables(line, aligned)]
        paragraphs = _paragraphs_from_lines(_xy_cut(free))

        for paragraph in paragraphs:
            paragraph.align = _detect_align(paragraph, text_left, text_right)
            paragraph.indent_pt = max(0.0, paragraph.x - text_left)
        # Anche i blocchi (non solo le righe) vanno ordinati per colonne: un
        # sort per sola y mescolerebbe le due colonne di un PDF.
        page_blocks = _xy_cut([*paragraphs, *tables, *page["images"]])
        if page_index > 0 and page_blocks:
            document.blocks.append(PageBreak())
        document.blocks.extend(page_blocks)

    _classify_headings(document)
    document.blocks = _group_lists(document.blocks, text_left)
    document.margin_left_pt = text_left
    document.margin_right_pt = max(0.0, page_width - text_right)
    return document


def _classify_headings(document: Document) -> None:
    """Titoli: gerarchia per corpo (i corpi più grandi = livelli più alti).

    Il titolo del documento è escluso dai ranghi: altrimenti "Title 26pt /
    Heading1 14pt / Heading2 13pt" diventerebbe 1/2/3 invece di title/1/2.
    """
    body_size = document.body_size()
    heading_sizes = sorted({round(block.size, 1) for block in document.blocks
                            if isinstance(block, Paragraph)
                            and body_size * 1.15 <= block.size < body_size * 1.45},
                           reverse=True)
    size_rank = {size: index + 1 for index, size in enumerate(heading_sizes)}

    for block in document.blocks:
        if not isinstance(block, Paragraph):
            continue
        level = _heading_level(block, body_size, size_rank)
        if level is None:
            continue
        block.style = "heading"
        block.level = level

    # Titolo del documento: il primo blocco grande della prima pagina.
    for block in document.blocks:
        if isinstance(block, PageBreak):
            break
        if isinstance(block, Paragraph) and block.style == "heading" and block.size >= body_size * 1.45:
            block.style = "title"
            block.level = 1
            document.title = block.text
            break


def _remove_prefix(paragraph: Paragraph, marker: str) -> None:
    """Toglie il proiettile/numero dall'inizio della prima riga (run per run)."""
    remaining = len(marker)
    for run in paragraph.lines[0].runs:
        if remaining <= 0:
            break
        stripped = run.text.lstrip()
        skipped = len(run.text) - len(stripped)
        remaining -= skipped
        if remaining <= 0:
            run.text = stripped
            break
        if len(stripped) >= remaining:
            run.text = stripped[remaining:]
            remaining = 0
            break
        remaining -= len(stripped)
        run.text = ""
    first = paragraph.lines[0]
    first.runs = [run for run in first.runs if run.text.strip()]
    if first.runs:
        first.runs[0].text = first.runs[0].text.lstrip()


def _group_lists(blocks: list, text_left: float) -> list:
    """Trasforma i paragrafi che iniziano con proiettile/numero in ListBlock."""
    result: list = []
    current: ListBlock | None = None
    for block in blocks:
        marker_info = None
        if isinstance(block, Paragraph) and block.style == "body":
            marker_info = _list_marker(block.text)
        if marker_info is None:
            current = None
            result.append(block)
            continue
        marker, ordered = marker_info
        if current is None or current.ordered != ordered:
            current = ListBlock(ordered=ordered)
            result.append(current)
        level = 1 + max(0, int(round((block.x - text_left - 8) / 18.0)))
        _remove_prefix(block, marker)
        current.items.append(ListItem(marker=marker, level=min(level, 5), paragraph=block))
    return result
