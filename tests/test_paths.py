"""Percorsi di configurazione: dove vivono i dati che devono sopravvivere.

Nessuna di queste proprietà si vede nell'interfaccia, ed è proprio per questo che
vale la pena bloccarle: un preset che finisce nella cartella del bundle sparisce
al primo aggiornamento, e una scrittura non atomica può lasciare un JSON troncato
che al riavvio sembra un file di configurazione valido.
"""

from __future__ import annotations

import json

import pytest

from converter import APP_NAME
from converter.core import paths


def test_config_dir_windows_uses_appdata(monkeypatch, tmp_path):
    """Su Windows la configurazione sta in %APPDATA%, non accanto all'exe."""
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))

    assert paths.config_dir() == tmp_path / "Roaming" / APP_NAME
    assert paths.config_dir().is_dir()


def test_config_dir_windows_survives_missing_appdata(monkeypatch, tmp_path):
    """Senza APPDATA si ricade sulla home: meglio un percorso strano che un crash."""
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))

    expected = tmp_path / "AppData" / "Roaming" / APP_NAME
    assert paths.config_dir() == expected


def test_config_dir_macos_uses_application_support(monkeypatch, tmp_path):
    """Su macOS il posto giusto è ~/Library/Application Support, non ~/.config."""
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))

    expected = tmp_path / "Library" / "Application Support" / APP_NAME
    assert paths.config_dir() == expected


def test_config_dir_linux_honours_xdg(monkeypatch, tmp_path):
    """Su Linux XDG_CONFIG_HOME vince sulla home, quando è impostata."""
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    assert paths.config_dir() == tmp_path / "xdg" / APP_NAME


def test_config_dir_linux_falls_back_to_dot_config(monkeypatch, tmp_path):
    """Senza XDG_CONFIG_HOME il fallback è ~/.config, non la cwd."""
    monkeypatch.setattr(paths.sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))

    expected = tmp_path / ".config" / APP_NAME
    assert paths.config_dir() == expected


def test_persistent_files_stay_inside_config_dir(monkeypatch, tmp_path):
    """Preset, cronologia e impostazioni devono stare tutti nella stessa cartella."""
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    assert paths.presets_file() == tmp_path / "presets.json"
    assert paths.history_file() == tmp_path / "history.json"
    assert paths.settings_file() == tmp_path / "settings.json"


def test_libreoffice_profile_is_private_and_created(monkeypatch, tmp_path):
    """Il profilo LibreOffice è dedicato: non deve toccare quello personale."""
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)

    profile = paths.libreoffice_profile_dir()

    assert profile == tmp_path / "lo_profile"
    assert profile.is_dir()


def test_atomic_write_json_is_readable_and_leaves_no_temp(tmp_path):
    """Dopo una scrittura riuscita resta un solo file, con JSON valido."""
    target = tmp_path / "history.json"

    paths.atomic_write_json(target, {"entries": [{"src": "a.png"}]})

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "entries": [{"src": "a.png"}]
    }
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_json_keeps_old_content_when_rename_fails(tmp_path, monkeypatch):
    """Se il rename fallisce il file precedente resta intatto, non troncato."""
    target = tmp_path / "presets.json"
    paths.atomic_write_json(target, {"presets": ["old"]})

    def refuse_replace(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(paths.os, "replace", refuse_replace)

    with pytest.raises(OSError):
        paths.atomic_write_json(target, {"presets": ["new"]})

    assert json.loads(target.read_text(encoding="utf-8")) == {"presets": ["old"]}


def test_read_setting_rejects_json_that_is_not_an_object(monkeypatch, tmp_path):
    """Un file valido ma con la forma sbagliata non deve far esplodere l'avvio."""
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text("[1, 2, 3]", encoding="utf-8")

    assert paths.read_setting("engine_mode", "FULL") == "FULL"


def test_write_setting_recovers_from_a_corrupt_file(monkeypatch, tmp_path):
    """Scrivere una chiave deve funzionare anche se il file era illeggibile."""
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text("{ not json", encoding="utf-8")

    paths.write_setting("theme", "dark")

    stored = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert stored == {"theme": "dark"}
