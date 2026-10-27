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

import contextvars
import io
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
_GRID_CELL = 48.0          # lato della cella della griglia spaziale dei segmenti
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


# pdfminer scrive "(cid:N)" per un glifo che il font non mappa su Unicode (niente
# ToUnicode): il carattere vero è ignoto. Lasciarlo dava testo spazzatura,
# toglierlo poteva cambiare il senso (un segno meno perso: −0,94 → 0,94).
_CID_RE = re.compile(r"\(cid:\d+\)")
UNDECODED = "\ufffd"     # carattere sostitutivo Unicode, dichiarato nel verdetto


def _clean_text(text: str) -> str:
    """Normalizza i glifi delle aree private (Symbol/Wingdings) in Unicode."""
    if "(cid:" in text:
        text = _CID_RE.sub(UNDECODED, text)
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


def _is_rotated(container) -> bool:
    """True se la riga è scritta ruotata (timbro, filigrana): nessun glifo dritto."""
    from pdfminer.layout import LTChar

    chars = [char for char in container if isinstance(char, LTChar)]
    return bool(chars) and all(not char.upright for char in chars)


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
    # L'ordinamento iniziale serve solo a unire i frammenti sulla stessa base:
    # per la lettura, però, una pagina (e soprattutto una cella multilinea) va
    # dall'alto verso il basso. Senza questo sort le righe di una cella lunga
    # arrivavano invertite, con le parole di ogni riga mescolate alle altre.
    merged.sort(key=lambda ln: (-round(ln.y, 1), ln.x))
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

    # Griglia spaziale: si confrontano solo i segmenti che condividono una
    # cella. Il confronto di TUTTE le coppie era O(n²) in Python puro: una
    # tavola CAD o una mappa con migliaia di linee costava minuti per pagina.
    buckets: dict[tuple[int, int], list[int]] = {}
    for index, segment in enumerate(segments):
        x0, y0, x1, y1 = bbox(segment)
        for cx in range(int((x0 - _RULE_TOUCH) // _GRID_CELL), int((x1 + _RULE_TOUCH) // _GRID_CELL) + 1):
            for cy in range(int((y0 - _RULE_TOUCH) // _GRID_CELL),
                            int((y1 + _RULE_TOUCH) // _GRID_CELL) + 1):
                buckets.setdefault((cx, cy), []).append(index)

    checked: set[tuple[int, int]] = set()
    for members in buckets.values():
        for position, i in enumerate(members):
            ax0, ay0, ax1, ay1 = bbox(segments[i])
            for j in members[position + 1:]:
                pair = (i, j) if i < j else (j, i)
                if pair in checked:
                    continue
                checked.add(pair)
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
        # Ordine di lettura dentro la cella: riga per riga dall'alto, poi da
        # sinistra (una cella multilinea non deve mescolare le sue righe).
        group.sort(key=lambda word: (-round(word[3].y, 1), word[0]))
        fragment = group[0][3]
        bottom = min(word[3].y for word in group)
        cells.append((min(word[0] for word in group), max(word[1] for word in group),
                      fragment.y, " ".join(word[2] for word in group), fragment.bold,
                      fragment.size, fragment.height + (fragment.y - bottom)))
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


def _page_gutters(lines: list[Line]) -> list[tuple[float, float]]:
    """Bande verticali libere su tutta la pagina: separano colonne di testo.

    A differenza dei vuoti interni a una tabella, una gutter di pagina non è
    attraversata da nessuna riga: è lo stesso segnale che usa `_xy_cut` per
    leggere prima la colonna sinistra e poi la destra.
    """
    spans = sorted((line.x, line.right) for line in lines)
    if not spans:
        return []
    gutters: list[tuple[float, float]] = []
    reach = spans[0][1]
    for start, end in spans[1:]:
        if start - reach >= _COLUMN_GAP:
            gutters.append((reach, start))
        reach = max(reach, end)
    return gutters


_MIN_COLUMN_LINES = 3


def _column_gutters(lines: list[Line]) -> list[tuple[float, float]]:
    """Gutter di pagina che separano colonne di testo VERE.

    Una gutter conta solo se ha almeno qualche riga da entrambi i lati: una
    data allineata a destra o un numero isolato non fanno una colonna, e il
    loro rientro deve restare misurato dal margine."""
    kept = []
    for left, right in _page_gutters(lines):
        before = sum(1 for line in lines if line.right <= left + 0.5)
        after = sum(1 for line in lines if line.x >= right - 0.5)
        if before >= _MIN_COLUMN_LINES and after >= _MIN_COLUMN_LINES:
            kept.append((left, right))
    return kept


def _column_bounds(block, gutters: list[tuple[float, float]], text_left: float,
                   text_right: float) -> tuple[float, float]:
    """(sinistra, destra) della colonna che contiene il blocco."""
    left = max([text_left] + [end for _, end in gutters if end <= block.x + 0.5])
    right = min([text_right] + [start for start, _ in gutters if start >= block.right - 0.5])
    return left, right


def _has_text_beside(lines: list[Line], left: float, right: float) -> bool:
    """True se la pagina ha testo a sinistra o a destra della tabella.

    Serve a distinguere una tabella affiancata a una colonna di prosa da una
    tabella sola sulla pagina: nel secondo caso un vuoto largo tra le colonne
    non è una gutter di pagina e non va usato per tagliare.
    """
    return any(line.x < left - 2.0 or line.right > right + 2.0 for line in lines)


def _is_prose(cells: list[_Cell]) -> bool:
    """True se le celle sembrano righe di prosa invece che celle di tabella.

    Una colonna di testo accanto a una tabella ha celle lunghe (molte parole);
    una colonna vera di tabella può essere stretta e avere poche parole anche
    quando la tabella è larga. Serve a non staccare una colonna legittima solo
    perché è più stretta delle altre.
    """
    if not cells:
        return False
    words = sorted(len(cell[3].split()) for cell in cells)
    return words[len(words) // 2] >= 4


def _split_at_gutters(columns: list[float], grids: list[list[_Cell | None]],
                      gutters: list[tuple[float, float]]
                      ) -> tuple[list[float], list[list[_Cell | None]]]:
    """Scarta la colonna di prosa finita nella stessa fascia della tabella.

    Una tabella nella colonna destra condivide le righe con la prosa della
    colonna sinistra: senza questo taglio la prosa diventa una colonna della
    tabella (e sparisce dal corpo del documento). Si tagliano solo i gruppi di
    colonne che sono prosa e stanno a sinistra della tabella: un vuoto largo
    dentro una tabella (colonna di lettere, colonna stretta, colonna di
    descrizioni) non deve dividerla a metà.
    """
    splits = [index + 1 for index in range(len(columns) - 1)
              if any(columns[index] <= left and columns[index + 1] >= right
                     for left, right in gutters)]
    if not splits:
        return columns, grids
    groups: list[tuple[int, int]] = []
    start = 0
    for end in splits:
        groups.append((start, end))
        start = end
    groups.append((start, len(columns)))
    cells = [[cell for grid in grids for cell in grid[low:high] if cell]
             for low, high in groups]
    prose = [_is_prose(group) for group in cells]
    if all(prose) or not any(prose):
        return columns, grids
    # La tabella è la sequenza di colonne non-prosa che contiene il gruppo più
    # pieno; la prosa può stare solo alla sua sinistra (ordine di lettura).
    best = max((index for index in range(len(groups)) if not prose[index]),
               key=lambda index: len(cells[index]))
    low, high = best, best
    while low > 0 and not prose[low - 1]:
        low -= 1
    while high + 1 < len(groups) and not prose[high + 1]:
        high += 1
    if any(prose[index] for index in range(high + 1, len(groups))):
        return columns, grids
    return (columns[groups[low][0]:groups[high][1]],
            [grid[groups[low][0]:groups[high][1]] for grid in grids])


def _caption_above(lines: list[Line], top: float) -> bool:
    """True se sopra la tabella c'è una didascalia "Table N"."""
    return any(0 <= line.y - top <= 40 and _CAPTION_RE.match(line.text.strip())
               for line in lines)


def _median_size(cells: list[_Cell]) -> float:
    """Corpo mediano delle celle: un marcatore di nota non cambia la riga."""
    sizes = sorted(cell[5] for cell in cells)
    return sizes[len(sizes) // 2]


# Gutter della pagina in esame, impostate da _build_aligned_tables. ContextVar e
# non globale: la GUI converte più file in parallelo su thread diversi.
_PAGE_GUTTERS: contextvars.ContextVar[tuple[tuple[float, float], ...]] = contextvars.ContextVar(
    "_PAGE_GUTTERS", default=())


def _region(x: float, gutters) -> int:
    """Indice della colonna di pagina (tra le gutter) che contiene la coordinata x."""
    return sum(1 for _, gutter_right in gutters if gutter_right <= x + 0.5)


def _prose_regions(items, x_of, words_of) -> set[int]:
    """Colonne di pagina che sono prosa A SINISTRA della tabella nella banda.

    Una colonna è prosa se ogni suo elemento ha 4+ parole; conta solo se a destra
    c'è una colonna "da tabella" (celle corte). Una descrizione lunga in fondo a
    una tabella (a destra) non è prosa da scartare."""
    gutters = _PAGE_GUTTERS.get()
    if not gutters:
        return set()
    by_region: dict[int, list[int]] = {}
    for item in items:
        by_region.setdefault(_region(x_of(item), gutters), []).append(words_of(item))
    tabular = [region for region, words in by_region.items() if any(count < 4 for count in words)]
    if not tabular:
        return set()
    return {region for region, words in by_region.items()
            if all(count >= 4 for count in words) and region < max(tabular)}


def _table_cells(cells: list[_Cell]) -> list[_Cell]:
    """Celle "da tabella" di una banda: senza la prosa della colonna di pagina accanto.

    Quando una tabella sta in una colonna e la prosa nell'altra, ogni banda
    orizzontale contiene anche una riga di prosa, oltre una gutter: per gli
    allineamenti tra righe di tabella quella cella non va contata."""
    prose = _prose_regions(cells, lambda cell: cell[0], lambda cell: len(cell[3].split()))
    if not prose:
        return cells
    gutters = _PAGE_GUTTERS.get()
    return [cell for cell in cells if _region(cell[0], gutters) not in prose]


def _table_fragments(fragments: list[Line]) -> list[Line]:
    """Frammenti della banda che non sono prosa della colonna accanto."""
    prose = _prose_regions(fragments, lambda line: line.x,
                           lambda line: len(line.text.split()))
    if not prose:
        return list(fragments)
    gutters = _PAGE_GUTTERS.get()
    return [line for line in fragments if _region(line.x, gutters) not in prose]


def _aligned_with(candidate_cells: list[_Cell], previous_cells: list[_Cell]) -> bool:
    """True se almeno metà delle celle candidate parte sotto una colonna di sopra.

    Una riga di prosa con il titolo di sezione nel margine ("2.3.59.7 | (1) Le
    abbreviazioni…") ha due celle anche lei, ma non sulle colonne della tabella."""
    # Si misura la riga con MENO celle contro l'altra: un'intestazione su due
    # righe ("concentration (g) … resistance (Ω)") ha meno celle dei dati sotto.
    fewer, more = sorted((_table_cells(candidate_cells), _table_cells(previous_cells)), key=len)
    starts = [cell[0] for cell in more]
    aligned = sum(1 for cell in fewer
                  if any(abs(cell[0] - start) <= _COLUMN_TOL for start in starts))
    return aligned * 2 >= len(fewer)


def _continues_table(previous: tuple[int, float, list[_Cell], list[Line]],
                     candidate: tuple[int, float, list[_Cell], list[Line]],
                     bands: list[list], pitch: float | None = None) -> bool:
    """True se la riga candidata continua la tabella della riga precedente.

    Tra due righe di tabella può cadere una riga di prosa dell'altra colonna:
    non è una candidata (una sola cella) e non deve spezzare la tabella. Una
    banda che resta fuori dalla larghezza della riga precedente non appartiene
    alla tabella e viene saltata; una didascalia o un'altra tabella, che invece
    ne occupa la larghezza, interrompe il gruppo. Un titolo sopra la tabella ha
    un corpo più grande delle righe e non è una riga della tabella.
    """
    if abs(_median_size(previous[2]) - _median_size(candidate[2])) > 2.0:
        return False
    if not _aligned_with(candidate[2], previous[2]):
        return False
    if candidate[0] == previous[0] + 1:
        return _close_enough(previous, previous[1], candidate, pitch)
    # Larghezza della TABELLA (senza la riga di prosa della colonna accanto che
    # condivide la banda): la prosa tra due righe sta fuori e non le separa.
    table_cells = _table_cells(previous[2])
    left = min(cell[0] for cell in table_cells)
    right = max(cell[1] for cell in table_cells)
    last_y = previous[1]
    for index in range(previous[0] + 1, candidate[0]):
        band_y, fragments = bands[index]
        if (_is_cell_continuation(fragments, previous, last_y)
                or _is_row_label(fragments, previous, last_y, candidate[1])):
            # Seconda riga di una cella multilinea (descrizione che va a capo) o
            # etichetta unita su più righe ("DP" centrato tra NLL e Triplet):
            # fanno parte della tabella, non la interrompono.
            last_y = band_y
            continue
        if not all(fragment.right <= left or fragment.x >= right for fragment in fragments):
            return False
    return _close_enough(previous, last_y, candidate, pitch)


def _close_enough(previous, last_y: float, candidate, pitch: float | None) -> bool:
    """Un vuoto verticale molto più ampio del passo delle righe chiude la tabella:
    quello che segue è un altro blocco anche se ha colonne simili."""
    line_height = max((cell[6] for cell in previous[2]), default=10.0) or 10.0
    limit = max(3.0 * line_height, 1.8 * pitch) if pitch else 3.0 * line_height
    return last_y - candidate[1] <= limit + line_height


def _is_cell_continuation(fragments: list[Line],
                          previous: tuple[int, float, list[_Cell], list[Line]],
                          last_y: float) -> bool:
    """True se la banda è il seguito di una cella della riga di tabella sopra.

    Nei manuali e nei moduli la descrizione dell'ultima colonna va a capo: la
    riga successiva ha UNA sola cella, allineata sotto una colonna che non è la
    prima. Prima quella riga spezzava la tabella in frammenti (e il suo testo
    finiva fuori, come paragrafo). Una riga che parte dalla prima colonna è una
    riga nuova, non un seguito; una lontana in verticale è altro testo."""
    cells = [cell for fragment in _table_fragments(fragments) for cell in _line_cells(fragment)]
    if len(cells) != 1 or len(previous[2]) < 2:
        return False
    x0, x1, y, _, _, size, height = cells[0]
    row = sorted(_table_cells(previous[2]), key=lambda cell: cell[0])
    if len(row) < 2:
        return False
    second_column = row[1][0]
    right = max(cell[1] for cell in row)
    if x0 < second_column - _COLUMN_TOL or x1 > right + _COLUMN_TOL * 4:
        return False
    if abs(size - _median_size(previous[2])) > 2.0:
        return False
    line_height = max(height, size, 1.0)
    return 0 < last_y - y <= line_height * 2.2


def _is_row_label(fragments: list[Line],
                  previous: tuple[int, float, list[_Cell], list[Line]],
                  last_y: float, next_y: float | None) -> bool:
    """True se la banda è un'etichetta unita in verticale su più righe.

    Nelle tabelle dei paper ("DP" / "L2" per due righe ciascuna) l'etichetta è
    centrata TRA le righe che raggruppa, in una colonna a sinistra della prima
    cella della riga sopra. Senza questo riconoscimento spezzava la tabella."""
    cells = [cell for fragment in _table_fragments(fragments) for cell in _line_cells(fragment)]
    if len(cells) != 1 or next_y is None:
        return False
    x0, x1, y, _, _, size, height = cells[0]
    first_column = min(cell[0] for cell in _table_cells(previous[2]))
    if x1 > first_column - 2.0:
        return False
    if abs(size - _median_size(previous[2])) > 2.0:
        return False
    line_height = max(height, size, 1.0)
    # Stretta tra la riga sopra e quella sotto, senza vuoti propri.
    return 0 < last_y - y <= line_height * 1.2 and 0 < y - next_y <= line_height * 1.2


def _attach_continuations(group: list[tuple[int, float, list[_Cell], list[Line]]],
                          bands: list[list]) -> list[tuple[int, float, list[_Cell], list[Line]]]:
    """Aggiunge a ogni riga del gruppo le righe di seguito delle sue celle."""
    attached = []
    for position, candidate in enumerate(group):
        stop = group[position + 1][0] if position + 1 < len(group) else len(bands)
        extra: list[Line] = []
        last_y = candidate[1]
        next_y = group[position + 1][1] if position + 1 < len(group) else None
        for index in range(candidate[0] + 1, stop):
            band_y, fragments = bands[index]
            if not (_is_cell_continuation(fragments, candidate, last_y)
                    or _is_row_label(fragments, candidate, last_y, next_y)):
                break
            extra.extend(_table_fragments(fragments))   # mai la prosa accanto
            last_y = band_y
        # Le celle aggiunte contano per le colonne: un'etichetta unita ("DP")
        # ha la sua colonna, non si incolla alla cella accanto.
        cells = list(candidate[2])
        for fragment in extra:
            cells.extend(_line_cells(fragment))
        cells.sort(key=lambda cell: cell[0])
        attached.append((candidate[0], candidate[1], cells, list(candidate[3]) + extra))
    return attached


def _build_aligned_tables(lines: list[Line], page_height: float,
                          ruled: list[Table],
                          gutters: list[tuple[float, float]]) -> list[Table]:
    """Tabelle ricostruite dagli allineamenti, non dalle linee disegnate.

    Molti PDF (LaTeX booktabs, Elsevier, report Word stampati) non disegnano le
    colonne: le celle sono solo parole a coordinate. Si riconoscono per righe
    consecutive con almeno due celle e colonne che si ripetono. Le due colonne
    di testo sono protette da una guardia: senza numeri o celle corte non è una
    tabella, è un layout a colonne. Con tre o più colonne serve una didascalia,
    un'intestazione in grassetto o celle numeriche.
    """
    token = _PAGE_GUTTERS.set(tuple(gutters))
    try:
        return _build_aligned_tables_inner(lines, page_height, ruled, gutters)
    finally:
        _PAGE_GUTTERS.reset(token)


def _build_aligned_tables_inner(lines: list[Line], page_height: float, ruled: list[Table],
                                gutters: list[tuple[float, float]]) -> list[Table]:
    bands = _row_bands(lines)
    candidates: list[tuple[int, float, list[_Cell], list[Line]]] = []
    for index, (y, fragments) in enumerate(bands):
        cells: list[_Cell] = []
        for fragment in sorted(fragments, key=lambda item: item.x):
            cells.extend(_line_cells(fragment))
        cells.sort(key=lambda cell: cell[0])
        # Una banda con UNA sola cella corta più una riga di prosa accanto non è
        # una riga di tabella: è un'etichetta o un seguito (vedi sotto).
        if len(cells) >= 2 and len(_table_cells(cells)) >= 2:
            candidates.append((index, y, cells, fragments))
    if len(candidates) < 2:
        return []

    groups: list[list[tuple[int, float, list[_Cell], list[Line]]]] = []
    for candidate in candidates:
        pitch = None
        if groups and len(groups[-1]) >= 2:
            steps = sorted(a[1] - b[1] for a, b in zip(groups[-1], groups[-1][1:], strict=False))
            pitch = steps[len(steps) // 2]
        if groups and _continues_table(groups[-1][-1], candidate, bands, pitch):
            groups[-1].append(candidate)
        else:
            groups.append([candidate])

    tables: list[Table] = []
    for group in groups:
        if len(group) < 2:
            continue
        group = _attach_continuations(group, bands)
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

        # Il taglio della prosa vale solo se la parte che resta è ancora una
        # tabella e se accanto c'è davvero una colonna di testo: una tabella
        # sola sulla pagina non ha prosa da scartare.
        options: list[tuple[list[float], list[list[_Cell | None]]]] = []
        split = _split_at_gutters(columns, cell_rows, gutters)
        if split[0] != columns:
            kept_cells = [cell for grid in split[1] for cell in grid if cell]
            if kept_cells and _has_text_beside(lines,
                                               min(cell[0] for cell in kept_cells),
                                               max(cell[1] for cell in kept_cells)):
                options.append(split)
        options.append((columns, cell_rows))
        for option_columns, option_rows in options:
            built = _table_from_group(group, option_columns, option_rows, lines,
                                      page_height, ruled)
            if built is not None:
                tables.append(built)
                break
    return tables


def _table_from_group(group: list[tuple[int, float, list[_Cell], list[Line]]],
                      columns: list[float], cell_rows: list[list[_Cell | None]],
                      lines: list[Line], page_height: float,
                      ruled: list[Table]) -> Table | None:
    """Costruisce la tabella del gruppo, se colonne e righe superano le guardie."""
    # Una riga fatta solo di prosa dell'altra colonna resta vuota: non è una
    # riga della tabella e non deve comparire come riga bianca.
    kept = [(candidate, grid) for candidate, grid in zip(group, cell_rows, strict=True)
            if any(grid)]
    if len(kept) < 2:
        return None
    group = [candidate for candidate, _ in kept]
    cell_rows = [grid for _, grid in kept]
    if len(columns) < 2:
        return None
    if sum(1 for grid in cell_rows if sum(1 for cell in grid if cell) >= 2) < 2:
        return None

    texts = [cell[3] for grid in cell_rows for cell in grid if cell and cell[3].strip()]
    if not texts:
        return None
    lengths = sorted(len(text) for text in texts)
    median = lengths[len(lengths) // 2]
    numeric = sum(1 for text in texts
                  if _NUMERIC_CELL_RE.match(text) and any(ch.isdigit() for ch in text)) / len(texts)
    # Il fondo della tabella include le righe di seguito dell'ultima riga.
    y0 = min(min(fragment.y for fragment in candidate[3]) for candidate in group)
    y1 = max(candidate[1] + max(cell[6] for cell in candidate[2]) for candidate in group)
    x0 = min(cell[0] for grid in cell_rows for cell in grid if cell)
    x1 = max(cell[1] for grid in cell_rows for cell in grid if cell)
    width, height = x1 - x0, y1 - y0
    if width < 40 or height < 8:
        return None
    if len(columns) == 2:
        # Due righe di due colonne sono quasi sempre un'intestazione di
        # pagina (numero + testatina) o due colonne di testo: una tabella
        # vera a due colonne ha almeno tre righe e celle numeriche.
        if len(group) < 3 or numeric < 0.6:
            return None
        if height > page_height * 0.5:
            return None
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
            return None
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if any(table.x - 2 <= cx <= table.x + table.width + 2
           and table.y - 2 <= cy <= table.y + table.height + 2 for table in ruled):
        return None

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
    return Table(rows=output, widths_pt=widths, borders=False, header_rows=header,
                 x=x0, y=y0, width=width, height=height)


# --- Paragrafi, lista, allineamento ---------------------------------------

def _merge_ok(previous: Line, current: Line) -> bool:
    size = max(previous.size, current.size, 1.0)
    gap = previous.y - (current.y + current.height)
    if gap < -2.0 or gap > 0.55 * size + 3.0:
        return False
    if current.size and previous.size and abs(current.size - previous.size) > 1.2:
        return False
    # Un titolo di sezione numerato non assorbe il paragrafo che segue, anche
    # quando il corpo è solo ~1pt più piccolo ("1 Introduction" 12pt su 10.9pt):
    # la differenza di corpo è il confine che la soglia dei 1.2pt non vede.
    previous_text = previous.text.strip()
    if (current.size and previous.size and previous.size - current.size > 0.8
            and len(previous_text) <= 80 and _NUMBERED_RE.match(previous_text)):
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
    if (all(abs(ln.right - text_right) <= 4.0 for ln in lines)
            and all(ln.x > text_left + 12 for ln in lines)):
        # Deve iniziare lontano dal margine sinistro: su una pagina con una
        # sola riga `text_right` coincide con la riga, e il vecchio test
        # (`abs(x - text_right) > 20`) la classificava "right" per finta.
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
    elif numbered and len(text) <= 120 and (
            (bold and size >= body_size * 0.98) or size >= body_size * 1.05):
        # "1 Introduction" in un corpo appena più grande (non bold) è un titolo:
        # senza questa fascia la riga si fondeva col paragrafo che segue.
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

_COMPRESSED_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _compact_image(data: bytes, ext: str) -> tuple[bytes, str]:
    """BMP/TIFF grezzi -> PNG lossless, così l'IR (e ogni writer) resta leggero.

    pdfminer esporta la maggior parte delle immagini come BMP non compresso: un
    paper di 16 pagine passava da 40 MB di immagini a ~4 MB, e il Markdown da
    53 MB a ~5 MB. I formati già compressi non vengono ricodificati; se la
    conversione fallisce o non conviene, si tiene il flusso originale.
    """
    if ext in _COMPRESSED_IMAGE_EXTS:
        return data, ext
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image.load()
            if image.mode in ("CMYK", "F"):
                image = image.convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="PNG", optimize=True)
        png = buffer.getvalue()
    except Exception:
        return data, ext
    return (png, "png") if len(png) < len(data) else (data, ext)


def _ccitt_image(node) -> bytes | None:
    """Immagine CCITT (fax G3/G4) decodificata da libtiff via Pillow, in PNG.

    pdfminer la decodifica in Python puro: ~6 s per UNA pagina A4 a 300 dpi,
    minuti per una scansione di qualche decina di pagine. Avvolgendo il flusso
    grezzo in un'intestazione TIFF, Pillow (libtiff, in C) la decodifica in
    millisecondi. Qualunque caso insolito torna None e ripiega su pdfminer."""
    import struct

    try:
        filters = node.stream.get_filters()
        if len(filters) != 1 or getattr(filters[0][0], "name", "") != "CCITTFaxDecode":
            return None
        params = filters[0][1] or {}
        if isinstance(params, list):
            params = params[0] if params else {}
        k = int(params.get("K", 0))
        width = int(params.get("Columns", node.srcsize[0]) or node.srcsize[0])
        height = int(params.get("Rows", node.srcsize[1]) or node.srcsize[1])
        black_is_1 = bool(params.get("BlackIs1", False))
        data = node.stream.get_rawdata()
        if not data or width <= 0 or height <= 0:
            return None
        compression = 4 if k < 0 else 3
        t4_options = 1 if k > 0 else 0          # G3 2D quando K > 0
        # BlackIs1=false (default PDF): i bit 0 sono neri → BlackIsZero (1).
        photometric = 0 if black_is_1 else 1
        tags = [(256, 4, width), (257, 4, height), (258, 3, 1), (259, 3, compression),
                (262, 3, photometric), (273, 4, 0), (277, 3, 1), (278, 4, height),
                (279, 4, len(data))]
        if compression == 3:
            tags.append((292, 4, t4_options))
        header_size = 8 + 2 + 12 * len(tags) + 4
        entries = b"".join(
            struct.pack("<HHII", tag, kind, 1, header_size if tag == 273 else value)
            if kind == 4 else struct.pack("<HHIHH", tag, kind, 1, value, 0)
            for tag, kind, value in tags)
        tiff = (b"II" + struct.pack("<HI", 42, 8) + struct.pack("<H", len(tags)) + entries
                + struct.pack("<I", 0) + data)
        from PIL import Image

        with Image.open(io.BytesIO(tiff)) as image:
            image.load()
            if image.size != (width, height):
                return None
            buffer = io.BytesIO()
            image.convert("1").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
    except Exception:
        return None


def _collect_page(layout, image_writer, tmp_dir: str) -> dict:
    from pdfminer.layout import LTFigure, LTImage, LTLine, LTRect, LTTextLine

    lines: list[Line] = []
    rotated: list[Line] = []
    segments: list[tuple[str, tuple[float, float, float, float]]] = []
    images: list[ImageBlock] = []

    def walk(node) -> None:
        if isinstance(node, LTTextLine):
            line = _line_from_container(node)
            if line is not None:
                (rotated if _is_rotated(node) else lines).append(line)
            return
        if isinstance(node, LTImage):
            fast = _ccitt_image(node)
            if fast is not None:
                x0, y0, x1, y1 = node.bbox
                images.append(ImageBlock(data=fast, ext="png", width_pt=x1 - x0,
                                         height_pt=y1 - y0, x=x0, y=y0))
                return
            try:
                name = image_writer.export_image(node)
                if name:
                    path = Path(tmp_dir) / name
                    if path.exists() and path.stat().st_size > 0:
                        x0, y0, x1, y1 = node.bbox
                        data, ext = _compact_image(path.read_bytes(),
                                                   path.suffix.lstrip(".").lower() or "png")
                        images.append(ImageBlock(
                            data=data, ext=ext,
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
    # Il testo ruotato (timbri "arXiv:...", filigrane, artefatti fuori pagina) non
    # fa parte del flusso di lettura: pdfminer lo spezza in un glifo per riga e
    # quei glifi finivano per fondersi con le righe di corpo (una cifra diventava
    # un proiettile) o per superare la soglia heading. Si scarta, a meno che la
    # pagina non sia ruotata per intero: lì è l'unico testo disponibile.
    if not lines:
        lines = rotated
    return {"lines": lines, "segments": segments, "images": images,
            "width": layout.width, "height": layout.height}


def _tolerant_classes():
    """Documento e interprete pdfminer che non abbandonano un PDF per un difetto locale.

    * Dizionario /Info (titolo, autore) malformato: è FACOLTATIVO per lo standard,
      ma pdfminer lo risolve aprendo il documento e un errore di sintassi lì
      rifiutava tutto il file (es. un titolo LaTeX con spazi non escapati).
    * Spazio colore ICCBased senza /N o simili: pdfminer fallisce l'intera pagina
      inizializzando le risorse; senza quegli spazi colore il testo si legge
      ugualmente (i colori ricadono sul default).
    """
    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfinterp import PDFPageInterpreter
    from pdfminer.pdftypes import PDFObjRef
    from pdfminer.psexceptions import PSException

    class TolerantDocument(PDFDocument):
        def __init__(self, parser, *args, **kwargs):
            self._broken_info: set[int] = set()
            try:
                super().__init__(parser, *args, **kwargs)
            except PSException:
                broken = set()
                for xref in getattr(self, "xrefs", []):
                    trailer = xref.get_trailer() or {}
                    info = trailer.get("Info")
                    if isinstance(info, PDFObjRef):
                        broken.add(info.objid)
                if not broken:
                    raise
                # Secondo tentativo: /Info illeggibile = metadati vuoti (prima di
                # ripartire, perché il costruttore lo risolve subito).
                self._broken_info = broken
                parser.fp.seek(0)
                type(parser).__init__(parser, parser.fp)
                super().__init__(parser, *args, **kwargs)

        def getobj(self, objid):
            if objid in getattr(self, "_broken_info", ()):
                return {}
            return super().getobj(objid)

    class TolerantInterpreter(PDFPageInterpreter):
        def init_resources(self, resources):
            try:
                super().init_resources(resources)
            except Exception:
                if not isinstance(resources, dict) or "ColorSpace" not in resources:
                    raise
                safe = {key: value for key, value in resources.items() if key != "ColorSpace"}
                super().init_resources(safe)

    return TolerantDocument, TolerantInterpreter


def iter_pdf_pages(handle, maxpages: int = 0):
    """Pagine pdfminer di un PDF aperto, con il documento tollerante."""
    from pdfminer.pdfpage import PDFPage
    from pdfminer.pdfparser import PDFParser

    document_class, _ = _tolerant_classes()
    document = document_class(PDFParser(handle))
    for index, page in enumerate(PDFPage.create_pages(document)):
        if maxpages and index >= maxpages:
            break
        yield page


class ReadCancelled(Exception):
    """L'utente ha annullato durante la lettura (controllato a ogni pagina)."""


def _open_layout_pages(path: Path, use_ai: bool):
    """(pagine PDFium per l'analisi AI | None, motivo se non disponibile)."""
    from . import layout_ai

    if not use_ai:
        return None, ""
    analyzer = layout_ai.get_analyzer()
    if analyzer is None:
        return None, layout_ai.unavailable_reason() or "AI layout analysis is not available"
    try:
        return analyzer.open(path), ""
    except Exception as exc:     # PDFium non apre il file che pdfminer legge
        return None, f"the page renderer could not open this PDF ({exc})"


def _page_regions(layout_pages, index: int):
    if layout_pages is None:
        return None
    try:
        regions = layout_pages.regions(index)
    except Exception:
        return None
    return regions or None


def read_pdf(path: Path, *, use_ai: bool = True, progress=None, cancel=None) -> Document:
    """PDF → Document. Solleva ValueError con messaggio pulito se il PDF è illeggibile.

    Una pagina che non si riesce a interpretare viene saltata e contata in
    `Document.skipped_pages`: un difetto locale non deve costare l'intero file,
    ma nemmeno sparire in silenzio (il verdetto lo dichiara).

    Con `use_ai` ogni pagina passa anche dal modello di layout (`layout_ai`), se
    installato; una pagina che il modello non sa analizzare usa l'euristica.
    `progress(fatte, totali)` è chiamata a ogni pagina, `cancel()` vero la ferma
    con `ReadCancelled`."""
    from pdfminer.converter import PDFPageAggregator
    from pdfminer.image import ImageWriter
    from pdfminer.layout import LAParams
    from pdfminer.pdfdocument import PDFPasswordIncorrect
    from pdfminer.pdfinterp import PDFResourceManager

    _, interpreter_class = _tolerant_classes()
    manager = PDFResourceManager(caching=True)
    pages: list[dict] = []
    skipped = 0
    layout_pages, ai_unavailable = _open_layout_pages(path, use_ai)
    total = layout_pages.count if layout_pages is not None else 0
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            writer = ImageWriter(tmp_dir)
            # all_texts: senza, pdfminer NON raggruppa in righe il testo dentro i
            # Form XObject (impaginati InDesign, report, moduli compilati): restava
            # LTChar sciolto e il lettore lo saltava. Su 4 PDF del corpus si
            # perdeva dal 20 al 97% del testo, in silenzio.
            device = PDFPageAggregator(manager, laparams=LAParams(all_texts=True))
            interpreter = interpreter_class(manager, device)
            try:
                with open(path, "rb") as handle:
                    for index, page in enumerate(iter_pdf_pages(handle)):
                        if cancel is not None and cancel():
                            raise ReadCancelled()
                        try:
                            interpreter.process_page(page)
                        except PDFPasswordIncorrect:
                            raise
                        except Exception:
                            skipped += 1
                            continue
                        collected = _collect_page(device.get_result(), writer, tmp_dir)
                        collected["index"] = index
                        collected["regions"] = _page_regions(layout_pages, index)
                        pages.append(collected)
                        if progress is not None:
                            progress(index + 1, max(total, index + 1))
            except ReadCancelled:
                raise
            except PDFPasswordIncorrect as exc:
                raise ValueError(
                    "Could not read this PDF: it is password-protected.") from exc
            except Exception as exc:
                raise ValueError(f"Could not read this PDF: {exc}") from exc

            if not pages:
                if skipped:
                    raise ValueError(f"Could not read this PDF: none of its {skipped} page(s) "
                                     f"could be interpreted.")
                raise ValueError("Could not read this PDF: it has no pages.")

            page_width = max(p["width"] for p in pages) or 595.0
            page_height = max(p["height"] for p in pages) or 842.0
            _drop_repeated_margins(pages, page_height)
            document = _assemble(pages, page_width, page_height, layout_pages)
            document.skipped_pages = skipped
            document.undecoded_chars = _count_undecoded(document)
            document.ai_unavailable = ai_unavailable
            return document
    finally:
        if layout_pages is not None:
            layout_pages.close()


def _count_undecoded(document: Document) -> int:
    """Quanti caratteri il PDF non permette di decodificare (vedi `_CID_RE`)."""
    def paragraphs():
        for block in document.blocks:
            if isinstance(block, Paragraph):
                yield block
            elif isinstance(block, ListBlock):
                yield from (item.paragraph for item in block.items)
            elif isinstance(block, Table):
                yield from (p for row in block.rows for cell in row for p in cell.paragraphs)
    return sum(run.text.count(UNDECODED) for paragraph in paragraphs()
               for line in paragraph.lines for run in line.runs)


def _assemble(pages: list[dict], page_width: float, page_height: float,
              layout_pages=None) -> Document:
    document = Document(page_width_pt=page_width, page_height_pt=page_height,
                        source_format="pdf")

    all_lines: list[Line] = [ln for page in pages for ln in page["lines"]]
    text_left = min((ln.x for ln in all_lines), default=0.0)
    text_right = max((ln.right for ln in all_lines), default=page_width)

    for page_index, page in enumerate(pages):
        lines = _merge_fragments(page["lines"])
        table_lines: set[int] = set()
        tables: list[Table] = []
        ruled_lines: list[set[int]] = []
        for component in _connected_components(page["segments"]):
            built = _build_table(page["segments"], component, lines, page_width)
            if built is not None:
                tables.append(built[0])
                ruled_lines.append(set(built[1]))
                table_lines.update(built[1])

        if page.get("regions"):
            page_blocks = _ai_page_blocks(page, lines, tables, ruled_lines, layout_pages,
                                          text_left, text_right)
            document.ai_pages += 1
            if page_index > 0 and page_blocks:
                document.blocks.append(PageBreak())
            document.blocks.extend(page_blocks)
            continue
        document.heuristic_pages += 1

        aligned = _build_aligned_tables(page["lines"], page_height, tables, _page_gutters(lines))
        tables.extend(aligned)

        free = [line for index, line in enumerate(lines)
                if index not in table_lines and not _inside_tables(line, aligned)]
        paragraphs = _paragraphs_from_lines(_xy_cut(free))

        gutters = _column_gutters(free)
        for paragraph in paragraphs:
            # Rientro e allineamento si misurano dentro la COLONNA del paragrafo:
            # misurati dal margine della pagina, ogni paragrafo della colonna
            # destra riceveva ~250pt di rientro e in Word finiva schiacciato in
            # una striscia stretta.
            left, right = _column_bounds(paragraph, gutters, text_left, text_right)
            paragraph.align = _detect_align(paragraph, left, right)
            paragraph.indent_pt = max(0.0, paragraph.x - left)
        # Anche i blocchi (non solo le righe) vanno ordinati per colonne: un
        # sort per sola y mescolerebbe le due colonne di un PDF.
        page_blocks = _xy_cut([*paragraphs, *tables, *page["images"]])
        if page_index > 0 and page_blocks:
            document.blocks.append(PageBreak())
        document.blocks.extend(page_blocks)

    _classify_headings(document)
    document.blocks = _group_lists(document.blocks)
    document.margin_left_pt = text_left
    document.margin_right_pt = max(0.0, page_width - text_right)
    return document


# --- Ricostruzione guidata dal modello AI di layout -------------------------

# Etichette del modello (PP-DocLayoutV2) raggruppate per ciò che diventano.
_AI_FIGURES = {"image", "chart", "display_formula", "seal", "header_image", "footer_image"}
_AI_TITLES = {"doc_title", "paragraph_title"}
_AI_IGNORED = {"inline_formula"}     # formule dentro una riga: il testo resta al paragrafo
_AI_PROSE = {"text", "abstract", "content", "reference_content", "footnote",
             "vision_footnote", "algorithm", "aside_text", "vertical_text", "reference"}
_PAGE_NUMBER_RE = re.compile(
    r"^\W*(\d{1,4}|[ivxlcdm]{1,6}|(page|pagina|pag\.|p\.)\s*\d{1,4}"
    r"(\s*(of|di|/)\s*\d{1,4})?)\W*$", re.IGNORECASE)
_FIGURE_MAX_TEXT = 600     # oltre questi caratteri una "figura" è testo riconosciuto male
_ASSIGN_MIN = 0.5          # quota della riga che deve cadere nella regione


def _line_area(line: Line) -> float:
    return max(line.width, 1.0) * max(line.height, 1.0)


def _best_region(x0: float, y0: float, x1: float, y1: float, regions) -> int | None:
    """Indice della regione che contiene di più il rettangolo (la più piccola a parità)."""
    area = max(x1 - x0, 1.0) * max(y1 - y0, 1.0)
    best: tuple[float, float, int] | None = None
    for index, region in enumerate(regions):
        share = region.overlap(x0, y0, x1, y1) / area
        if share < _ASSIGN_MIN:
            continue
        key = (round(share, 2), -region.area, index)
        if best is None or key[:2] > best[:2]:
            best = key
    return None if best is None else best[2]


def _center_in(block, region, pad: float = 2.0) -> bool:
    if isinstance(block, ImageBlock):
        cx, cy = block.x + block.width_pt / 2, block.y + block.height_pt / 2
    else:
        cx, cy = block.x + block.width / 2, block.y + block.height / 2
    return (region.x0 - pad <= cx <= region.x1 + pad
            and region.y0 - pad <= cy <= region.y1 + pad)


def _merge_formula_numbers(regions):
    """Il numero "(3)" accanto a una formula va nel ritaglio della formula."""
    formulas = [r for r in regions if r.kind == "display_formula"]
    kept = []
    for region in regions:
        if region.kind == "formula_number":
            mid = (region.y0 + region.y1) / 2
            target = next((f for f in formulas if f.y0 - 4 <= mid <= f.y1 + 4), None)
            if target is not None:
                target.x0, target.x1 = min(target.x0, region.x0), max(target.x1, region.x1)
                target.y0, target.y1 = min(target.y0, region.y0), max(target.y1, region.y1)
                continue
            region.kind = "text"
        kept.append(region)
    return kept


def _table_from_region_lines(lines: list[Line], bordered: bool) -> Table | None:
    """Tabella dalle righe di una regione che il modello ha riconosciuto come tabella.

    Qui non servono le guardie del motore euristico (che deve indovinare se un
    allineamento È una tabella): lo dice il modello. Resta da ricostruire la
    griglia: colonne dagli inizi delle celle, righe dalle basi comuni, e le righe
    di seguito (cella su più righe: prima colonna vuota, subito sotto) riattaccate
    alla riga sopra.
    """
    bands = _row_bands(lines)
    if len(bands) < 2:
        return None
    band_cells = []
    for _y, fragments in bands:
        cells: list[_Cell] = []
        for fragment in sorted(fragments, key=lambda item: item.x):
            cells.extend(_line_cells(fragment))
        band_cells.append(sorted(cells, key=lambda cell: cell[0]))
    columns = _cluster([cell[0] for cells in band_cells for cell in cells], tol=_COLUMN_TOL)
    columns = [column for column in columns
               if sum(1 for cells in band_cells
                      if any(abs(cell[0] - column) <= _COLUMN_TOL for cell in cells)) >= 2]
    if len(columns) < 2:
        return None
    grids = [_assign_row(fragments, columns) for _y, fragments in bands]
    grids, columns = _merge_sparse_columns(grids, columns)
    if len(columns) < 2:
        return None

    rows: list[list[list[_Cell]]] = []
    bottoms: list[float] = []
    for (_y, fragments), grid in zip(bands, grids, strict=True):
        if not any(grid):
            continue
        top = max(fragment.top for fragment in fragments)
        height = max(fragment.height for fragment in fragments) or 10.0
        filled = sum(1 for cell in grid if cell)
        close = bool(rows) and bottoms[-1] - top <= 1.6 * height
        continuation = (close and grid[0] is None and filled >= 1
                        and filled < len(columns))
        # Etichetta di riga unita ("DP" a metà fra due righe): solo la prima
        # colonna, e la riga sopra ce l'ha vuota. Va alla riga sopra.
        row_label = (close and grid[0] is not None and filled == 1
                     and not rows[-1][0])
        if continuation or row_label:
            for index, cell in enumerate(grid):
                if cell is not None:
                    rows[-1][index].append(cell)
            continuation = True
        else:
            rows.append([[cell] if cell is not None else [] for cell in grid])
        bottoms.append(min(fragment.y for fragment in fragments))
        if continuation:
            bottoms[-2:] = [bottoms[-1]]
    if len(rows) < 2 or sum(1 for row in rows if sum(1 for cell in row if cell) >= 2) < 1:
        return None

    all_cells = [cell for row in rows for cells in row for cell in cells]
    x0 = min(cell[0] for cell in all_cells)
    x1 = max(cell[1] for cell in all_cells)
    y0 = min(line.y for line in lines)
    y1 = max(line.top for line in lines)
    bounds = [x0] + [(columns[i] + columns[i + 1]) / 2 for i in range(len(columns) - 1)] + [x1]
    widths = [max(1.0, bounds[i + 1] - bounds[i]) for i in range(len(bounds) - 1)]

    output: list[list[TableCell]] = []
    for row in rows:
        out_row = []
        for cells in row:
            paragraphs = []
            for cell in cells:
                if cell[3].strip():
                    line = Line(runs=[Run(text=cell[3], bold=cell[4], size=cell[5])],
                                x=cell[0], y=cell[2], width=cell[1] - cell[0], height=cell[6])
                    paragraphs.append(line)
            out_row.append(TableCell(paragraphs=[Paragraph(lines=paragraphs)] if paragraphs else []))
        output.append(out_row)
    first = [cell for cells in rows[0] for cell in cells if cell[3].strip()]
    header = 1 if first and all(cell[4] for cell in first) else 0
    return Table(rows=output, widths_pt=widths, borders=bordered, header_rows=header,
                 x=x0, y=y0, width=x1 - x0, height=y1 - y0)


def _segments_inside(segments, region) -> bool:
    for _kind, (sx0, sy0, sx1, sy1) in segments:
        if region.overlap(sx0 - 0.5, sy0 - 0.5, sx1 + 0.5, sy1 + 0.5) > 0:
            return True
    return False


def _figure_block(region, members: list[Line], images: list[ImageBlock],
                  layout_pages, page_index: int) -> ImageBlock | None:
    """Immagine di una regione figura: l'originale se è una sola foto, altrimenti un ritaglio.

    Il ritaglio della pagina renderizzata conserva anche i grafici VETTORIALI
    (assi, curve, etichette) che pdfminer non estrae come immagine: prima quei
    grafici sparivano e restavano solo i numeri degli assi come testo sparso.
    """
    if len(images) == 1 and region.kind != "display_formula":
        image = images[0]
        covered = region.overlap(image.x, image.y, image.right, image.top)
        if covered >= 0.8 * region.area:
            return image
    if layout_pages is None:
        return None
    data = layout_pages.crop_png(page_index, region.x0, region.y0, region.x1, region.y1)
    if data is None:
        return None
    return ImageBlock(data=data, ext="png", width_pt=region.x1 - region.x0,
                      height_pt=region.y1 - region.y0, x=region.x0, y=region.y0)


def _join_baselines(lines: list[Line]) -> list[Line]:
    """Dentro una regione (una sola colonna) i pezzi sulla stessa base sono UNA riga.

    pdfminer spezza le righe giustificate con spazi larghi ("facto method.   In
    this work…"): fuori da una regione unirle rischia di fondere due colonne,
    dentro una regione no, e lasciarle separate spezzava il paragrafo a metà.
    """
    ordered = sorted(lines, key=lambda ln: (-round(ln.y, 1), ln.x))
    rows: list[Line] = []
    for line in ordered:
        if rows:
            prev = rows[-1]
            size = max(prev.size, line.size) or 10.0
            if abs(prev.y - line.y) <= 0.35 * size and line.x >= prev.right - 1.0:
                if prev.runs and not prev.runs[-1].text.endswith(" "):
                    prev.runs[-1].text = prev.runs[-1].text.rstrip() + " "
                prev.runs.extend(line.runs)
                prev.words.extend(line.words)
                prev.width = max(prev.width, line.right - prev.x)
                prev.height = max(prev.height, line.height)
                continue
        rows.append(Line(runs=list(line.runs), words=list(line.words), x=line.x,
                         y=line.y, width=line.width, height=line.height))
    return rows


_WEAK_BULLETS = "·∙"


def _starts_item(text: str, paragraph_start: str) -> bool:
    """La riga apre una nuova voce di elenco.

    Il punto mediano "·" separa anche le parole chiave ("Deep Learning · Layout
    Analysis") e può capitare a inizio riga per un a capo: conta come proiettile
    solo se anche il paragrafo in corso era una voce con lo stesso segno.
    """
    if _ORDERED_RE.match(text):
        return True
    if not is_bullet(text):
        return False
    if text[0] in _WEAK_BULLETS:
        return paragraph_start[:1] == text[0]
    return True


def _would_fit(prev: Line, line: Line, right: float) -> bool:
    """L'a capo è voluto: la prima parola della riga dopo stava nello spazio rimasto.

    Un testo che va a capo per mancanza di spazio non lascia mai in fondo alla
    riga un vuoto più largo della parola seguente; se lo lascia, la riga è finita
    lì apposta (elenco di righe, indirizzo, verso, voce di menu).
    """
    if not line.words:
        return False
    first = line.words[0]
    word = first[1] - first[0]
    room = right - prev.right
    space = 0.3 * max(prev.size, line.size, 6.0)
    return room > word + space + 2.0


def _region_paragraphs(lines: list[Line], left: float, right: float) -> list[Paragraph]:
    """Paragrafi di una regione di testo del modello.

    Una regione è già un blocco di testo: le righe si uniscono anche con
    un'interlinea larga (titoli coreani, testo a 1,5 righe) che l'euristica di
    pagina deve trattare come confine. Si va a capo per un nuovo punto elenco, un
    rientro di prima riga dopo una riga corta, un vuoto grande o un cambio di corpo.
    """
    rows = _join_baselines(lines)
    if rows:
        left = min(left, min(line.x for line in rows))
        right = max(line.right for line in rows)     # margine vero del testo
    width = max(right - left, 1.0)
    paragraphs: list[Paragraph] = []
    current: list[Line] = []
    for line in rows:
        if current:
            prev = current[-1]
            size = max(prev.size, line.size, 1.0)
            gap = prev.y - line.top
            text = line.text.lstrip()
            short = prev.right < right - 0.18 * width
            new = (_starts_item(text, current[0].text.lstrip())
                   or gap > 1.3 * size + 4.0
                   or (line.size and prev.size and abs(line.size - prev.size) > 1.2)
                   or (line.x - left > 6.0 and short)
                   or (short and line.x - prev.x > 6.0)
                   or _would_fit(prev, line, right))
            if new:
                paragraphs.append(Paragraph(lines=current))
                current = []
        current.append(line)
    if current:
        paragraphs.append(Paragraph(lines=current))
    return paragraphs


def _continues_paragraph(previous, paragraph: Paragraph, right: float) -> bool:
    """La regione successiva continua il paragrafo precedente (stessa colonna, a capo).

    Serve quando il modello separa le righe di uno stesso paragrafo: stesso corpo,
    stesso margine sinistro (niente rientro di prima riga), interlinea normale e
    riga precedente piena fino in fondo.
    """
    if not isinstance(previous, Paragraph):
        return False
    if not previous.lines or not paragraph.lines:
        return False
    last, first = previous.lines[-1], paragraph.lines[0]
    size = max(last.size, first.size, 1.0)
    if abs(last.size - first.size) > 0.6 or abs(first.x - previous.x) > 3.0:
        return False
    if is_bullet(first.text.lstrip()) or _ORDERED_RE.match(first.text.lstrip()):
        return False
    gap = last.y - first.top
    if gap < -1.0 or gap > 0.8 * size:
        return False
    widest = max(previous.right, paragraph.right, right) - previous.x
    if last.right - previous.x < 0.8 * widest:
        return False
    return not _would_fit(last, first, max(previous.right, paragraph.right))


def _region_columns(regions, text_left: float, text_right: float) -> list[tuple[float, float]]:
    """Colonna (sinistra, destra) di ogni regione, per rientri e allineamento.

    La colonna è la regione di testo più larga che contiene in orizzontale la
    regione (un titolo "1 Introduction" sta nella colonna sinistra); una regione
    larga quasi quanto la pagina, o che non sta in nessuna colonna, usa i margini
    della pagina (così i titoli centrati restano centrati).
    """
    page_width = max(text_right - text_left, 1.0)
    columns = []
    for region in regions:
        best = None
        for other in regions:
            if other.kind not in _AI_PROSE or other.x1 - other.x0 > 0.65 * page_width:
                continue
            if other.x0 - 6 <= region.x0 and region.x1 <= other.x1 + 6:
                if best is None or other.x1 - other.x0 > best[1] - best[0]:
                    best = (other.x0, other.x1)
        columns.append(best or (text_left, text_right))
    return columns


def _ai_page_blocks(page: dict, lines: list[Line], ruled: list[Table],
                    ruled_lines: list[set[int]], layout_pages,
                    text_left: float, text_right: float) -> list:
    """Blocchi di una pagina, nell'ordine di lettura del modello AI.

    Ogni riga di testo va alla regione che la contiene; le righe che non cadono
    in nessuna regione NON si perdono: diventano paragrafi euristici inseriti
    vicino alla regione che le precede. Così il modello può solo migliorare
    l'ordine e la struttura, mai togliere testo.
    """
    regions = _merge_formula_numbers(
        [region for region in page["regions"] if region.kind not in _AI_IGNORED])
    regions.sort(key=lambda region: region.order)

    used_by_ruled: set[int] = set()
    region_ruled: dict[int, Table] = {}
    leftovers: list = []
    for table, members in zip(ruled, ruled_lines, strict=True):
        target = next((i for i, region in enumerate(regions)
                       if region.kind == "table" and _center_in(table, region)), None)
        if target is not None and target not in region_ruled:
            region_ruled[target] = table
            used_by_ruled |= members
        elif not any(_center_in(table, region) for region in regions):
            leftovers.append(table)          # griglia disegnata fuori da ogni regione
            used_by_ruled |= members
        # una griglia dentro una regione di testo è una cornice: vale il testo

    members: list[list[Line]] = [[] for _ in regions]
    free: list[Line] = []
    for index, line in enumerate(lines):
        if index in used_by_ruled or not line.text.strip():
            continue
        target = _best_region(line.x, line.y, line.right, line.top, regions)
        if target is None:
            free.append(line)
        else:
            members[target].append(line)

    # I riquadri del modello sono larghi di qualche punto: per rientri, colonne e
    # allineamento valgono i bordi VERI del testo che contengono.
    for region, region_lines in zip(regions, members, strict=True):
        if region_lines and region.kind not in _AI_FIGURES and region.kind != "table":
            region.x0 = min(line.x for line in region_lines)
            region.x1 = max(line.right for line in region_lines)

    region_images: list[list[ImageBlock]] = [[] for _ in regions]
    for image in page["images"]:
        target = next((i for i, region in enumerate(regions)
                       if region.kind in _AI_FIGURES and _center_in(image, region)), None)
        if target is None:
            leftovers.append(image)
        else:
            region_images[target].append(image)

    gutters = _column_gutters(lines)
    columns = _region_columns(regions, text_left, text_right)
    placed: list[tuple[float, object]] = []   # (posizione, blocco)
    last_prose: list = [None]                 # ultimo paragrafo uscito da una regione di testo

    def place(position: float, block, column: tuple[float, float] | None = None) -> None:
        if isinstance(block, Paragraph):
            left, right = column or _column_bounds(block, gutters, text_left, text_right)
            block.align = _detect_align(block, left, right)
            block.indent_pt = max(0.0, block.x - left)
        placed.append((position, block))

    def prose(position: int | float, region_lines: list[Line], role: str) -> None:
        column = columns[int(position)]
        region = regions[int(position)]
        paragraphs = _region_paragraphs(region_lines, region.x0, region.x1)
        previous = placed[-1][1] if placed else None
        if (paragraphs and previous is not None and previous is last_prose[0]
                and _continues_paragraph(previous, paragraphs[0], column[1])):
            # Il modello a volte vede ogni riga di un paragrafo come una regione.
            previous.lines.extend(paragraphs.pop(0).lines)
            if role == "body":
                previous.role = "body"
        for paragraph in paragraphs:
            if role == "body" and (len(paragraphs) > 1 or len(paragraph.lines) > 1):
                paragraph.role = "body"
            place(position, paragraph, column)
            if len(paragraph.lines) > 1:
                # Allineamento misurato sulla regione: un abstract giustificato più
                # stretto della pagina, misurato sui margini, risultava "centrato".
                paragraph.align = _detect_align(paragraph, region.x0, max(
                    line.right for line in region_lines))
        last_prose[0] = placed[-1][1] if placed else None

    for index, region in enumerate(regions):
        region_lines = members[index]
        text_chars = sum(len(line.text.strip()) for line in region_lines)
        if region.kind in _AI_FIGURES and (region.kind == "display_formula"
                                           or region_images[index]
                                           or text_chars <= _FIGURE_MAX_TEXT):
            figure = _figure_block(region, region_lines, region_images[index],
                                   layout_pages, page["index"])
            if figure is not None:
                place(index, figure)
                for image in region_images[index]:
                    if image is not figure and not (region.overlap(
                            image.x, image.y, image.right, image.top)
                            >= 0.9 * max(1.0, image.width_pt * image.height_pt)):
                        place(index + 0.1, image)   # foto solo in parte nella regione
                continue
            for image in region_images[index]:
                place(index, image)
            prose(index, region_lines, "")
            continue
        for image in region_images[index]:
            place(index, image)
        if not region_lines and index not in region_ruled:
            continue
        if region.kind == "table":
            table = region_ruled.get(index) or _table_from_region_lines(
                region_lines, _segments_inside(page["segments"], region))
            if table is not None:
                place(index, table)
                if index in region_ruled and region_lines:
                    prose(index + 0.1, region_lines, "")   # testo fuori dalla griglia
                continue
            prose(index, region_lines, "")
            continue
        if region.kind in _AI_TITLES:
            ordered = _merge_fragments(sorted(region_lines, key=lambda ln: (-ln.y, ln.x)))
            text = " ".join(line.text.strip() for line in ordered)
            if len(ordered) <= 4 and len(text) <= 250:
                heading = Paragraph(lines=_join_baselines(ordered),
                                    role="title" if region.kind == "doc_title" else "heading")
                place(index, heading, columns[index])
                continue
            prose(index, region_lines, "")
            continue
        if region.kind == "number" and all(_PAGE_NUMBER_RE.match(line.text.strip())
                                           for line in region_lines):
            continue      # numero di pagina: Word numera da sé, nel testo è rumore
        prose(index, region_lines, "body" if region.kind in _AI_PROSE else "")

    # Testo e oggetti fuori da ogni regione: accanto alla regione più vicina sopra.
    if free:
        leftovers.extend(_paragraphs_from_lines(_xy_cut(free)))
    page_area = max(1.0, page["width"] * page["height"])
    for block in leftovers:
        top = block.top
        x0 = block.x
        x1 = block.right
        if (isinstance(block, ImageBlock)
                and block.width_pt * block.height_pt >= 0.6 * page_area):
            # Sfondo a tutta pagina: in un flusso di testo va dopo il contenuto,
            # non prima (spingeva tutto il testo alla pagina successiva).
            place(len(regions) + 1.0, block)
            continue
        anchor = -0.5
        for index, region in enumerate(regions):
            above = region.y1 >= top - 1.0
            beside = region.x0 <= x1 and x0 <= region.x1
            if above and beside:
                anchor = max(anchor, index + 0.5)
        place(anchor, block)

    placed.sort(key=lambda item: item[0])
    return [block for _position, block in placed]


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
        if not isinstance(block, Paragraph) or block.role == "body":
            continue
        level = _heading_level(block, body_size, size_rank)
        if level is None and block.role in ("heading", "title"):
            # Il modello AI l'ha visto come titolo anche se il corpo non lo dice
            # ("Abstract" in corpo normale): livello più basso fra quelli presenti.
            numbered = _NUMBERED_RE.match(block.text.strip())
            level = (min(numbered.group(1).count(".") + 1, 6) if numbered
                     else size_rank.get(round(block.size, 1), min(len(size_rank) + 1, 6)))
        if level is None:
            continue
        block.style = "heading"
        block.level = level

    # Titolo del documento: il primo blocco grande della prima pagina.
    for block in document.blocks:
        if isinstance(block, PageBreak):
            break
        if isinstance(block, Paragraph) and block.style == "heading" and (
                block.size >= body_size * 1.45 or block.role == "title"):
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
        # Lo spazio PRIMA del marcatore non fa parte del marcatore: sottrarlo
        # lasciava il proiettile nel testo ("- • voce" in Markdown).
        stripped = run.text.lstrip()
        if not stripped:
            run.text = ""
            continue
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


def _group_lists(blocks: list) -> list:
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
        # Il livello viene dal rientro DENTRO la colonna (indent_pt), non dal
        # margine della pagina: nella colonna destra ogni voce finiva al livello 5.
        level = min(1 + max(0, int(round((block.indent_pt - 8) / 18.0))), 5)
        # Una sotto-voce di tipo diverso (punti sotto un elenco numerato) resta
        # nello stesso elenco: spezzarlo faceva ripartire la numerazione da 1.
        top = min((item.level for item in current.items), default=level) if current else level
        if current is None or (current.ordered != ordered and level <= top):
            current = ListBlock(ordered=ordered)
            result.append(current)
        _remove_prefix(block, marker)
        current.items.append(ListItem(marker=marker, level=level, paragraph=block,
                                      ordered=ordered))
    return result
