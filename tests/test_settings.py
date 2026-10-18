"""Test del piccolo store impostazioni (es. last_input_dir)."""

import converter.core.paths as paths


def test_setting_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    assert paths.read_setting("last_input_dir") is None
    assert paths.read_setting("last_input_dir", "fallback") == "fallback"

    paths.write_setting("last_input_dir", r"C:\Users\me\Pictures")
    assert paths.read_setting("last_input_dir") == r"C:\Users\me\Pictures"


def test_setting_merge_is_non_destructive(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    paths.write_setting("a", "1")
    paths.write_setting("b", "2")
    assert paths.read_setting("a") == "1"
    assert paths.read_setting("b") == "2"


def test_corrupt_settings_file_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    (tmp_path / "settings.json").write_text("not json {{{", encoding="utf-8")
    assert paths.read_setting("last_input_dir", "default") == "default"
    # Scrivere dopo un file corrotto non deve crashare e ricrea un JSON valido.
    paths.write_setting("last_input_dir", "X")
    assert paths.read_setting("last_input_dir") == "X"
