"""Schema dichiarativo delle opzioni di conversione.

I converter NON costruiscono widget: descrivono le proprie opzioni con questi
dataclass. La GUI (options_panel.py) genera i widget a partire da questa
descrizione. Così aggiungere un formato non richiede di toccare la UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Option:
    """Opzione base. `key` è la chiave usata in ConversionJob.options.

    `help` è kw_only: così i campi posizionali delle sottoclassi (minimum,
    maximum, ...) non collidono con esso nell'ordine degli argomenti.
    """

    key: str
    label: str
    default: Any
    help: str = field(default="", kw_only=True)


@dataclass
class IntOption(Option):
    """Valore intero, reso come spinbox o (se slider=True) come slider."""

    minimum: int = 0
    maximum: int = 100
    step: int = 1
    suffix: str = ""
    slider: bool = False


@dataclass
class ChoiceOption(Option):
    """Scelta tra valori discreti. choices: lista di (valore, etichetta)."""

    choices: list[tuple[Any, str]] = field(default_factory=list)


@dataclass
class BoolOption(Option):
    """Interruttore on/off reso come checkbox."""


@dataclass
class TextOption(Option):
    """Campo di testo libero (es. timecode di trim, dimensioni custom).

    A differenza di ChoiceOption il valore NON è vincolato: per questo i campi
    TextOption sono gli unici che richiedono validazione esplicita prima di
    raggiungere ffmpeg. `placeholder` è il suggerimento mostrato a campo vuoto.
    """

    placeholder: str = ""


def defaults(schema: list[Option]) -> dict[str, Any]:
    """Estrae i valori di default da uno schema (usato da CLI e stime)."""
    return {opt.key: opt.default for opt in schema}
