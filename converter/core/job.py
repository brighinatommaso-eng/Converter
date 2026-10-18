"""Tipi di dato che attraversano il confine GUI <-> core.

Regola: un converter non solleva mai un'eccezione verso il chiamante; in caso
di errore restituisce un ConversionResult con ok=False ed `error` valorizzato.
Così un file fallito in un batch non interrompe gli altri.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .quality import QualityVerdict


class CancelToken:
    """Token di annullamento cooperativo condiviso col worker."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class ConversionJob:
    """Singola unità di lavoro: un file in ingresso -> un file in uscita."""

    src_path: Path
    src_format: str
    dst_format: str
    output_path: Path
    options: dict[str, Any] = field(default_factory=dict)
    # strip_metadata=False -> per default i metadati (EXIF/ID3/...) si preservano.
    strip_metadata: bool = False
    cancel: CancelToken = field(default_factory=CancelToken)


@dataclass
class ConversionResult:
    ok: bool
    output_path: Path | None = None
    output_size: int | None = None
    verdict: QualityVerdict | None = None
    error: str | None = None


@dataclass
class ProgressEvent:
    """fraction in [0,1]; -1 significa avanzamento indeterminato."""

    fraction: float = -1.0
    message: str = ""


ProgressCallback = Callable[[ProgressEvent], None]


def _noop(_: ProgressEvent) -> None:
    """Callback di default quando il chiamante non vuole il progresso."""
