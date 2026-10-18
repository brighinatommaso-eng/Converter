"""Modello della coda di conversione (QAbstractTableModel).

Ogni riga è un QueueItem: file sorgente, formato rilevato, target scelto,
verdetto, stato e dimensioni. Il modello è puramente di presentazione: la scelta
del target e delle opzioni avviene nel pannello a destra, non in tabella.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from ..core.detection import DetectedFormat
from ..core.job import CancelToken
from ..core.quality import QualityVerdict
from ..core.registry import TargetOption
from .theme import badge_color
from .util import human_size

COLUMNS = ["File", "Detected", "→ Target", "Quality", "Size", "Status"]

# Default di Qt per gli override che ricevono il parent: un singleton a livello di
# modulo (una chiamata QModelIndex() nella firma sarebbe rivalutata a ogni chiamata).
_NO_PARENT = QModelIndex()


@dataclass
class QueueItem:
    src_path: Path
    detected: DetectedFormat
    targets: list[TargetOption]
    src_size: int
    dst_format: str | None = None
    verdict: QualityVerdict | None = None
    options: dict[str, Any] = field(default_factory=dict)
    strip_metadata: bool = False
    status: str = "Ready"          # Ready / Converting / Done / Failed / Unsupported
    checked: bool = True           # spunta nella colonna File: include il file nel batch
    progress: float = 0.0          # 0..1
    est_size: int | None = None
    out_size: int | None = None
    output_path: Path | None = None
    error: str | None = None
    cancel: CancelToken = field(default_factory=CancelToken)

    @property
    def convertible(self) -> bool:
        return self.dst_format is not None and self.status not in ("Done",)


class QueueModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[QueueItem] = []

    # --- API di base del modello ---

    def rowCount(self, parent=_NO_PARENT) -> int:
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=_NO_PARENT) -> int:
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.NoItemFlags
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == 0:  # solo la colonna File è spuntabile
            base |= Qt.ItemIsUserCheckable
        return base

    def setData(self, index: QModelIndex, value, role=Qt.EditRole) -> bool:
        if role == Qt.CheckStateRole and index.isValid() and index.column() == 0:
            self.items[index.row()].checked = Qt.CheckState(value) == Qt.Checked
            self.dataChanged.emit(index, index, [Qt.CheckStateRole])
            return True
        return False

    def items_to_convert(self) -> list[QueueItem]:
        """File che faranno parte del batch: spuntati, convertibili e non già in
        corso. Unica fonte di verità per 'Convert checked'."""
        return [it for it in self.items
                if it.checked and it.convertible and it.status != "Converting"]

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0:
                return item.src_path.name
            if col == 1:
                return (item.detected.format_id or "unknown").upper()
            if col == 2:
                return item.dst_format.upper() if item.dst_format else "—"
            if col == 3:
                return item.verdict.badge if item.verdict else "—"
            if col == 4:
                if item.out_size is not None:
                    return human_size(item.out_size)
                est = f" → ~{human_size(item.est_size)}" if item.est_size else ""
                return f"{human_size(item.src_size)}{est}"
            if col == 5:
                if item.status == "Converting":
                    return f"Converting… {int(item.progress * 100)}%"
                if item.status == "Failed" and item.error:
                    return f"Failed: {item.error}"
                return item.status

        if role == Qt.CheckStateRole and col == 0:
            return Qt.Checked if item.checked else Qt.Unchecked

        if role == Qt.ForegroundRole and col == 3 and item.verdict:
            return QColor(badge_color(item.verdict.level))
        if role == Qt.ForegroundRole and col == 5:
            if item.status == "Failed":
                return QColor(badge_color("failed"))
            if item.status == "Done":
                return QColor(badge_color("lossless"))

        if role == Qt.ToolTipRole:
            if col == 1:
                return f"Detected by: {item.detected.method} (confidence {item.detected.confidence:.0%})"
            if col == 3 and item.verdict:
                return item.verdict.reason
            if col == 5 and item.error:
                return item.error
        return None

    # --- Mutazioni ---

    def add_item(self, item: QueueItem) -> None:
        row = len(self.items)
        self.beginInsertRows(QModelIndex(), row, row)
        self.items.append(item)
        self.endInsertRows()

    def clear(self) -> None:
        self.beginResetModel()
        self.items = []
        self.endResetModel()

    def row_changed(self, row: int) -> None:
        if 0 <= row < len(self.items):
            top = self.index(row, 0)
            bottom = self.index(row, len(COLUMNS) - 1)
            self.dataChanged.emit(top, bottom)
