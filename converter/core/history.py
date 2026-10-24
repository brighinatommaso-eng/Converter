"""Cronologia delle conversioni completate, persistita su disco.

Stessa disciplina dei preset: file JSON nella user config dir, scrittura atomica,
campo `version` per la migrazione. Il log è limitato (cap) e si pota dal più
vecchio. Registra sia i successi sia i fallimenti passati dalla pipeline, così
una conversione fallita può comunque essere ri-eseguita (re-run).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import paths

HISTORY_VERSION = 1
MAX_ENTRIES = 200  # cap del log: oltre questo numero si potano le voci più vecchie


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class HistoryEntry:
    source_path: str
    source_format: str
    dst_format: str
    options: dict = field(default_factory=dict)
    strip_metadata: bool = False
    output_path: str | None = None
    result: str = "ok"            # "ok" | "failed"
    output_size: int | None = None
    error: str | None = None
    verdict: str | None = None
    timestamp: str = field(default_factory=_now_iso)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


class HistoryStore:
    def __init__(self, path=None, max_entries: int = MAX_ENTRIES) -> None:
        # path iniettabile per i test; di default la user config dir.
        self.path = path or paths.history_file()
        self.max_entries = max_entries

    def load(self) -> list[HistoryEntry]:
        """Voci in ordine cronologico (più vecchia -> più recente)."""
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []  # file corrotto: niente crash, si riparte vuoti
        # JSON valido ma forma inattesa (lista, "entries" non lista): la cronologia
        # si apre da un dialog, quindi un crash qui sarebbe a un clic di distanza.
        if not isinstance(data, dict):
            return []
        entries = data.get("entries")
        if not isinstance(entries, list):
            return []
        result: list[HistoryEntry] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                result.append(HistoryEntry(**{k: entry.get(k) for k in _FIELDS if k in entry}))
            except (TypeError, KeyError):
                continue
        return result

    def save(self, entries: list[HistoryEntry]) -> None:
        data = {
            "version": HISTORY_VERSION,
            "entries": [vars(e) for e in entries],
        }
        paths.atomic_write_json(self.path, data)

    def append(self, entry: HistoryEntry) -> list[HistoryEntry]:
        entries = self.load()
        entries.append(entry)
        # Potatura: tieni solo le ultime `max_entries` (le più recenti).
        if len(entries) > self.max_entries:
            entries = entries[-self.max_entries:]
        self.save(entries)
        return entries

    def clear(self) -> None:
        self.save([])


# Campi accettati dal costruttore di HistoryEntry (per un load robusto).
_FIELDS = set(HistoryEntry.__dataclass_fields__.keys())
