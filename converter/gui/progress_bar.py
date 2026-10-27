"""Barra di avanzamento con la percentuale sempre leggibile.

La barra standard scrive la percentuale in un solo colore: grigio scuro, quindi
quando la parte piena (blu) arriva sotto il testo il numero sparisce. Qui il testo
è disegnato due volte, ritagliato: sopra la parte piena nel colore del testo
d'accento (bianco nel tema chiaro), sopra la parte vuota nel colore del testo
normale. Ogni cifra ha così contrasto pieno, a qualsiasi percentuale.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QProgressBar

from . import theme


class TwoToneProgressBar(QProgressBar):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # Il testo lo disegna paintEvent: quello dello stile resta spento.
        super().setTextVisible(False)

    def percent_text(self) -> str:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return ""
        return f"{round(100 * (self.value() - self.minimum()) / span)}%"

    def filled_width(self) -> int:
        span = self.maximum() - self.minimum()
        if span <= 0:
            return 0
        return round(self.width() * (self.value() - self.minimum()) / span)

    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().paintEvent(event)
        text = self.percent_text()
        if not text:
            return
        palette = theme.palette()
        painter = QPainter(self)
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        rect = self.rect()
        split = self.filled_width()
        for clip, color in ((QRect(0, 0, split, rect.height()), palette["accent_text"]),
                            (QRect(split, 0, rect.width() - split, rect.height()), palette["text"])):
            if clip.width() <= 0:
                continue
            painter.save()
            painter.setClipRect(clip)
            painter.setPen(QColor(color))
            painter.drawText(rect, Qt.AlignCenter, text)
            painter.restore()
        painter.end()
