"""Test di routing di resolve() con disponibilità mista.

Regressione BUG 5: targets_for preferisce il converter DISPONIBILE, ma resolve
prendeva il primo per priorità ignorando la disponibilità. Con un motore ad alta
priorità assente e uno a bassa priorità presente sulla stessa coppia, la UI
offriva la coppia ma resolve la instradava al converter indisponibile e run_job
la rifiutava. Verificato con striprtf assente + LibreOffice presente su rtf→txt.

Nota: test_phase3_invariants mocka TUTTI i motori presenti, quindi non poteva
catturare questo mismatch — è il buco esatto in cui viveva il bug.
"""

import converter.converters  # noqa: F401
from converter.converters.document import DocumentConverter
from converter.converters.rtf import RtfTextConverter
from converter.core.deps import DependencyStatus
from converter.core.registry import resolve


def _avail(ok: bool):
    if ok:
        return classmethod(lambda cls: DependencyStatus(True))
    return classmethod(lambda cls: DependencyStatus(False, "unavailable", "install it"))


def test_resolve_prefers_available_over_higher_priority(monkeypatch):
    # striprtf (priority 10) assente, LibreOffice (priority 0) presente.
    monkeypatch.setattr(RtfTextConverter, "available", _avail(False))
    monkeypatch.setattr(DocumentConverter, "available", _avail(True))
    conv = resolve("rtf", "txt")
    assert isinstance(conv, DocumentConverter)  # NON RtfTextConverter indisponibile


def test_resolve_uses_high_priority_when_it_is_available(monkeypatch):
    # Entrambi presenti: vince il più prioritario (comportamento invariato).
    monkeypatch.setattr(RtfTextConverter, "available", _avail(True))
    monkeypatch.setattr(DocumentConverter, "available", _avail(True))
    assert isinstance(resolve("rtf", "txt"), RtfTextConverter)


def test_resolve_falls_back_to_priority_when_none_available(monkeypatch):
    # Nessuno disponibile: si ripiega sul più prioritario così l'errore di
    # run_job resta sensato (cita striprtf, non LibreOffice).
    monkeypatch.setattr(RtfTextConverter, "available", _avail(False))
    monkeypatch.setattr(DocumentConverter, "available", _avail(False))
    assert isinstance(resolve("rtf", "txt"), RtfTextConverter)
