"""Preset di conversione: configurazioni nominate, persistite su disco.

Un preset cattura formato di destinazione + opzioni + flag strip-metadati
(es. "Web JPG 85%", "Podcast MP3 192k"). I preset NON memorizzano la cartella
di output: quella è una scelta di sessione, non di conversione.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import paths, registry
from .formats import category_of

PRESETS_VERSION = 1


@dataclass
class Preset:
    name: str
    dst_format: str
    options: dict = field(default_factory=dict)
    strip_metadata: bool = False

    @property
    def category(self) -> str | None:
        return category_of(self.dst_format)

    @property
    def valid(self) -> bool:
        """Un preset è valido se il formato esiste ancora ed è producibile da
        qualche converter registrato (i preset 'orfani' vengono mostrati ma
        non applicati)."""
        if category_of(self.dst_format) is None:
            return False
        return any(self.dst_format in conv.output_formats for conv in registry._all())


class PresetStore:
    """Carica/salva i preset da presets.json nella user config dir."""

    def __init__(self, path=None) -> None:
        # path iniettabile per i test; di default la user config dir.
        self.path = path or paths.presets_file()

    def load(self) -> list[Preset]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []  # file corrotto/illeggibile: si riparte da zero, niente crash
        # Un JSON VALIDO ma della forma sbagliata (lista, numero, "presets" non
        # lista) non deve far crashare l'avvio: la GUI carica i preset in fase di
        # costruzione. Stessa disciplina di paths.read_setting.
        if not isinstance(data, dict):
            return []
        entries = data.get("presets")
        if not isinstance(entries, list):
            return []
        result: list[Preset] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                result.append(Preset(
                    name=entry["name"],
                    dst_format=entry["dst_format"],
                    options=dict(entry.get("options", {})),
                    strip_metadata=bool(entry.get("strip_metadata", False)),
                ))
            except (KeyError, TypeError):
                continue  # voce malformata: si salta
        return result

    def save(self, presets: list[Preset]) -> None:
        data = {
            "version": PRESETS_VERSION,
            "presets": [
                {"name": p.name, "dst_format": p.dst_format,
                 "options": p.options, "strip_metadata": p.strip_metadata}
                for p in presets
            ],
        }
        paths.atomic_write_json(self.path, data)

    def add(self, preset: Preset) -> list[Preset]:
        # Nome univoco: salvare con un nome esistente sovrascrive (la UI conferma).
        presets = [p for p in self.load() if p.name != preset.name]
        presets.append(preset)
        self.save(presets)
        return presets

    def delete(self, name: str) -> list[Preset]:
        presets = [p for p in self.load() if p.name != name]
        self.save(presets)
        return presets


def reconcile_options(src_format: str, dst_format: str, saved_options: dict) -> dict:
    """Adatta le opzioni salvate allo schema ATTUALE del converter.

    Tiene solo le chiavi ancora valide, scarta quelle ignote e riempie con i
    default quelle mancanti. È la rete di sicurezza per quando uno schema cambia.
    """
    conv = registry.resolve(src_format, dst_format)
    if conv is None:
        return {}
    schema = conv.options_schema(src_format, dst_format)
    result = {opt.key: opt.default for opt in schema}
    for opt in schema:
        if opt.key in saved_options:
            result[opt.key] = saved_options[opt.key]
    return result
