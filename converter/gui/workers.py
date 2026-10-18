"""Ponte tra la GUI e core.pipeline tramite QThreadPool.

La conversione gira fuori dal thread della UI. Ogni file è un QRunnable che
chiama core.pipeline.run_job (l'unico punto di esecuzione, condiviso con la CLI)
ed emette segnali Qt per progresso e completamento, consegnati al thread UI.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal

from ..core.job import ConversionJob, ConversionResult, ProgressEvent
from ..core.pipeline import run_job


class WorkerSignals(QObject):
    progress = Signal(int, float)        # (row, fraction)
    finished = Signal(int, object)       # (row, ConversionResult)


class ConvertRunnable(QRunnable):
    """Esegue la conversione di un singolo file."""

    def __init__(self, row: int, job: ConversionJob, signals: WorkerSignals) -> None:
        super().__init__()
        self._row = row
        self._job = job
        self._signals = signals

    def run(self) -> None:  # eseguito in un thread del pool
        def on_progress(evt: ProgressEvent) -> None:
            if evt.fraction >= 0:
                self._signals.progress.emit(self._row, evt.fraction)

        result: ConversionResult = run_job(self._job, on_progress)
        self._signals.finished.emit(self._row, result)
