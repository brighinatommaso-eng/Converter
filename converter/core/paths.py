"""Percorsi di configurazione nella user config dir del sistema operativo.

I dati persistenti (preset, cronologia) NON vanno mai nella cartella di
installazione: così sopravvivono a un eventuale bundle/aggiornamento. Si usa la
config dir standard per OS, risolta a mano dalla stdlib (niente dipendenze).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .. import APP_NAME


def config_dir() -> Path:
    """Cartella di configurazione dell'utente, creata se assente.

    Windows: %APPDATA%\\Converter · macOS: ~/Library/Application Support/Converter
    Linux:   $XDG_CONFIG_HOME/Converter  (fallback ~/.config/Converter)
    """
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    directory = Path(base) / APP_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def presets_file() -> Path:
    return config_dir() / "presets.json"


def history_file() -> Path:
    return config_dir() / "history.json"


def libreoffice_profile_dir() -> Path:
    """Profilo LibreOffice DEDICATO all'app, isolato da quello reale dell'utente.

    Riusato tra le conversioni (resta "caldo"), vive nella config dir così non
    sporca il profilo personale dell'utente e non viene conteso da un'istanza di
    LibreOffice aperta a parte."""
    directory = config_dir() / "lo_profile"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def atomic_write_json(path: Path, data) -> None:
    """Scrittura atomica: scrive su file temporaneo e poi rinomina, così un
    crash a metà non lascia mai un JSON corrotto."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # rename atomico sullo stesso filesystem


def settings_file() -> Path:
    """Piccolo store chiave-valore dell'app (es. ultima cartella di input)."""
    return config_dir() / "settings.json"


def read_setting(key: str, default=None):
    """Legge una chiave dalle impostazioni; default se assente o file corrotto."""
    path = settings_file()
    if not path.exists():
        return default
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default
    return data.get(key, default) if isinstance(data, dict) else default


def write_setting(key: str, value) -> None:
    """Aggiorna una chiave nelle impostazioni (merge non distruttivo)."""
    path = settings_file()
    data = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (json.JSONDecodeError, OSError):
            data = {}
    data[key] = value
    atomic_write_json(path, data)
