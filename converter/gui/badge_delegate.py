"""Delegate per disegnare i valori di Target/Quality/Status come badge a pillola.

Un QAbstractTableModel può colorare il testo (ForegroundRole) ma non disegnare
uno sfondo arrotondato dietro una cella: per quello serve un delegate che dipinge
a mano. Qui ridisegniamo SOLO le colonne badge; le altre restano al rendering
di default chiamando super().paint().

Mappatura colore:
  - Quality (col 3): livello preso da QueueItem.verdict.level (lossless/lossy/...)
  - Status  (col 5): mappato dallo stato testuale (Done->lossless tint, Failed->failed, ...)
  - Target  (col 2): tinta accento neutra (non è un giudizio di qualità)
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from .theme import badge_bg, badge_color

# Colonne che ricevono il trattamento a pillola.
COL_TARGET = 2
COL_QUALITY = 3
COL_STATUS = 5
_BADGE_COLS = (COL_TARGET, COL_QUALITY, COL_STATUS)


def _status_level(status: str) -> str:
    """Traduce lo stato testuale della riga nel 'level' usato per il colore."""
    s = (status or "").lower()
    if s.startswith("failed"):
        return "failed"
    if s.startswith("done"):
        return "lossless"
    if s.startswith("unsupported"):
        return "unsupported"
    if s.startswith("converting"):
        return "accent"
    return "neutral"  # Ready / queued / altro -> grigio


class BadgeDelegate(QStyledItemDelegate):
    def __init__(self, model, parent=None) -> None:
        super().__init__(parent)
        self._model = model  # QueueModel, per leggere l'item e il verdict

    def paint(self, painter: QPainter, option, index) -> None:
        col = index.column()
        if col not in _BADGE_COLS:
            super().paint(painter, option, index)
            return

        text = index.data(Qt.DisplayRole)
        if not text or text == "—":
            super().paint(painter, option, index)
            return

        # Disegna prima lo sfondo di selezione/riga, poi il pill sopra.
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        items = getattr(self._model, "items", [])
        row = index.row()
        item = items[row] if 0 <= row < len(items) else None

        if col == COL_QUALITY:
            level = item.verdict.level if (item and item.verdict) else "neutral"
        elif col == COL_STATUS:
            level = _status_level(item.status if item else "")
        else:  # COL_TARGET
            level = "accent"

        fg = QColor(badge_color(level) if level != "accent" else _accent_fg())
        bg = QColor(badge_bg(level))

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        fm = option.fontMetrics
        label = str(text)
        tw = fm.horizontalAdvance(label)
        th = fm.height()
        pad_x, pad_y = 9, 3
        bw = tw + pad_x * 2
        bh = th + pad_y * 2

        # Pillola allineata a sinistra nella cella, centrata verticalmente.
        x = option.rect.x() + 8
        y = option.rect.y() + (option.rect.height() - bh) / 2
        rect = QRectF(x, y, bw, bh)

        path = QPainterPath()
        path.addRoundedRect(rect, bh / 2, bh / 2)
        painter.fillPath(path, bg)

        f: QFont = painter.font()
        f.setFamily("Inter")
        f.setWeight(QFont.Weight.DemiBold)
        f.setHintingPreference(QFont.PreferNoHinting)
        painter.setFont(f)
        painter.setPen(fg)
        painter.drawText(rect, Qt.AlignCenter, label)
        painter.restore()


def _accent_fg() -> str:
    """Foreground del pill 'accent' (Target). Riusa il testo accentato del tema."""
    from .theme import current_theme
    # In light: blu scuro su tint chiara; in dark: ciano chiaro su tint scura.
    return "#185FA5" if current_theme() == "light" else "#85B7EB"
