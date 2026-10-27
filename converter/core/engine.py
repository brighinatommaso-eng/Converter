"""Modalità del motore di conversione: FULL vs PORTABLE.

L'app è *globalmente modale*: una sola modalità per processo, scelta all'avvio e
cambiata di rado. Tenerla come stato a livello di modulo evita di filtrare un
parametro `mode` dentro available()/resolve()/targets_for()/run_job() e in ogni
chiamata della GUI. I converter la leggono qui, dentro le loro available()/
pair_available(); set_mode() è l'UNICO punto di scrittura.

- FULL     — usa LibreOffice quando presente (più i motori interni/bundled).
- PORTABLE — solo motori pure-Python/bundled: non invoca MAI LibreOffice.
"""

from __future__ import annotations

from typing import Literal

from . import deps, paths

Mode = Literal["FULL", "PORTABLE"]

_VALID: tuple[Mode, ...] = ("FULL", "PORTABLE")
_DEFAULT: Mode = "FULL"
_SETTING_KEY = "engine_mode"

# Stato corrente del processo. Default FULL: comportamento storico dell'app.
_current: Mode = _DEFAULT


def get_mode() -> Mode:
    """Modalità attiva nel processo."""
    return _current


def set_mode(mode: Mode) -> None:
    """Imposta la modalità corrente — unico punto di scrittura.

    Invalida le cache di rilevamento dipendenze: dopo un cambio di modalità (o
    un'installazione appena conclusa) i tool vanno RI-rilevati, non riletti da una
    cache che diceva "assente".
    """
    if mode not in _VALID:
        raise ValueError(f"Modalità sconosciuta: {mode!r}")
    global _current
    _current = mode
    refresh_deps()


def refresh_deps() -> None:
    """Forza il ri-rilevamento dei tool esterni (es. dopo un'installazione).

    `find_*` sono memoizzate con lru_cache: senza svuotarle, un tool installato
    a runtime continuerebbe a risultare assente."""
    for fn in (deps.find_ffmpeg, deps.find_ffprobe, deps.find_soffice,
               deps.find_tesseract, deps.find_ghostscript, deps.find_ocrmypdf):
        clear = getattr(fn, "cache_clear", None)
        if clear is not None:  # robusto se la funzione è stata sostituita nei test
            clear()


def load_persisted() -> Mode | None:
    """Modalità ricordata in settings.json, o None se mai scelta/valore invalido."""
    value = paths.read_setting(_SETTING_KEY)
    return value if value in _VALID else None


def persist(mode: Mode) -> None:
    """Ricorda la scelta per i prossimi avvii."""
    if mode not in _VALID:
        raise ValueError(f"Modalità sconosciuta: {mode!r}")
    paths.write_setting(_SETTING_KEY, mode)


def full_mode_missing() -> list[str]:
    """Tool RICHIESTI dalla modalità FULL che risultano assenti.

    In FULL il motore documenti è LibreOffice: se manca, le conversioni office non
    funzionano e va offerta l'installazione (o il passaggio a PORTABLE). Lista di
    chiavi note a MissingDepsDialog."""
    missing: list[str] = []
    if not deps.find_soffice():
        missing.append("libreoffice")
    return missing
