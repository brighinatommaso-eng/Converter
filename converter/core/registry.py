"""Registry dei converter con auto-registrazione tramite decoratore @register.

La GUI interroga solo `targets_for(src)` e `resolve(src, dst)`: non importa né
istanzia mai direttamente una sottoclasse. Aggiungere un formato = creare/
estendere un converter in converters/, senza toccare la UI.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import Converter
from .quality import QualityVerdict

_CONVERTER_CLASSES: list[type[Converter]] = []
_instances: list[Converter] | None = None


def register(cls: type[Converter]) -> type[Converter]:
    """Decoratore usato dalle sottoclassi per auto-registrarsi all'import."""
    _CONVERTER_CLASSES.append(cls)
    global _instances
    _instances = None  # invalida la cache se si registra dopo il primo uso
    return cls


def _all() -> list[Converter]:
    global _instances
    if _instances is None:
        instances = [cls() for cls in _CONVERTER_CLASSES]
        # Ordina per priorità DECRESCENTE (stabile): i motori specifici vincono le
        # coppie sovrapposte. resolve() ritorna il primo che sa convertire e
        # targets_for() preferisce il primo disponibile, quindi l'ordine basta a
        # instradare il routing senza logica extra.
        instances.sort(key=lambda c: -c.priority)
        _instances = instances
    return _instances


@dataclass(frozen=True)
class TargetOption:
    """Una destinazione raggiungibile da un dato formato sorgente."""

    dst_format: str
    verdict: QualityVerdict
    available: bool
    reason_unavailable: str = ""


def targets_for(src_format: str) -> list[TargetOption]:
    """Tutte le destinazioni valide a partire da src_format.

    Le coppie senza senso tra categorie diverse (es. mp4 -> xlsx) semplicemente
    non compaiono: nessun converter le offre. Le destinazioni che richiedono una
    dipendenza assente compaiono comunque, ma marcate non disponibili (la UI le
    mostra in grigio col motivo), così l'utente capisce il perché.
    """
    seen: dict[str, TargetOption] = {}
    for conv in _all():
        if src_format not in conv.input_formats:
            continue
        conv_status = conv.available()
        src_ok, src_reason = conv.format_available(src_format)
        for dst in conv.output_formats:
            verdict = conv.verdict(src_format, dst)
            if not verdict.is_supported:
                continue
            dst_ok, dst_reason = conv.format_available(dst)
            pair_ok, pair_reason = conv.pair_available(src_format, dst)
            available = conv_status.ok and src_ok and dst_ok and pair_ok
            reason = ""
            if not conv_status.ok:
                reason = conv_status.reason
            elif not src_ok:
                reason = src_reason
            elif not dst_ok:
                reason = dst_reason
            elif not pair_ok:
                reason = pair_reason
            option = TargetOption(dst, verdict, available, reason)
            # Se due converter offrono lo stesso target, preferisci il disponibile.
            existing = seen.get(dst)
            if existing is None or (available and not existing.available):
                seen[dst] = option
    return [seen[k] for k in sorted(seen)]


def _pair_fully_available(conv: Converter, src: str, dst: str) -> bool:
    """Stessa nozione di disponibilità usata da targets_for: converter attivo,
    entrambi i formati abilitati e la direzione (src→dst) permessa."""
    return (conv.available().ok
            and conv.format_available(src)[0]
            and conv.format_available(dst)[0]
            and conv.pair_available(src, dst)[0])


def resolve(src_format: str, dst_format: str) -> Converter | None:
    """Restituisce il converter che gestisce la coppia, o None.

    Tra i converter che sanno gestire la coppia (già in ordine di priorità
    decrescente) si preferisce il primo DISPONIBILE: altrimenti targets_for
    offrirebbe una coppia via un motore secondario disponibile mentre resolve la
    instraderebbe a quello ad alta priorità ma indisponibile, e run_job la
    rifiuterebbe (es. striprtf assente ma LibreOffice presente per rtf→txt).
    Se nessuno è disponibile si ripiega sul più prioritario, così il messaggio
    d'errore di run_job resta sensato."""
    candidates = [c for c in _all() if c.can_convert(src_format, dst_format)]
    if not candidates:
        return None
    for conv in candidates:
        if _pair_fully_available(conv, src_format, dst_format):
            return conv
    return candidates[0]
