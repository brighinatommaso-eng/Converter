"""Verdetto di qualità di una conversione.

Principio non negoziabile dell'app: non mentire mai sulla qualità. Ogni coppia
(formato sorgente -> formato destinazione) produce un QualityVerdict che la GUI
traduce in un badge colorato e, se necessario, in un avviso esplicito.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Level = Literal["lossless", "lossy", "unsupported"]


@dataclass(frozen=True)
class QualityVerdict:
    """Esito qualitativo di una conversione.

    level:  "lossless" (verde), "lossy" (ambra) o "unsupported" (grigio).
    reason: spiegazione leggibile, mostrata nell'avviso prima di convertire.
    """

    level: Level
    reason: str = ""

    @property
    def is_lossless(self) -> bool:
        return self.level == "lossless"

    @property
    def is_lossy(self) -> bool:
        return self.level == "lossy"

    @property
    def is_supported(self) -> bool:
        return self.level != "unsupported"

    @property
    def badge(self) -> str:
        return self.level.upper()


def lossless(reason: str = "This conversion preserves all data.") -> QualityVerdict:
    return QualityVerdict("lossless", reason)


def lossy(reason: str) -> QualityVerdict:
    # reason è obbligatorio: una conversione lossy deve sempre spiegare il perché.
    return QualityVerdict("lossy", reason)


def unsupported(reason: str = "This conversion is not possible.") -> QualityVerdict:
    return QualityVerdict("unsupported", reason)
