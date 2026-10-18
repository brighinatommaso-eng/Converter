"""Test della modalità motore (engine.py): default, set/get, persistenza e
invalidazione delle cache di rilevamento."""

import pytest

import converter.core.paths as paths
from converter.core import deps, engine


def test_default_mode_is_full():
    assert engine.get_mode() == "FULL"


def test_set_and_get_mode():
    engine.set_mode("PORTABLE")
    assert engine.get_mode() == "PORTABLE"


def test_invalid_mode_rejected():
    with pytest.raises(ValueError):
        engine.set_mode("TURBO")


def test_persist_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    assert engine.load_persisted() is None
    engine.persist("PORTABLE")
    assert engine.load_persisted() == "PORTABLE"


def test_load_persisted_ignores_garbage(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    paths.write_setting("engine_mode", "NONSENSE")
    assert engine.load_persisted() is None


def test_set_mode_clears_dep_caches(monkeypatch):
    """find_soffice è memoizzata: dopo set_mode deve ri-eseguire il rilevamento.
    Questo è ciò che permette al riconoscimento post-installazione di funzionare."""
    calls = {"soffice": 0}
    real_locate = deps._locate

    def counting_locate(name, extra=None):
        if name == "soffice":
            calls["soffice"] += 1
        return real_locate(name, extra)

    monkeypatch.setattr(deps, "_locate", counting_locate)
    deps.find_soffice.cache_clear()

    deps.find_soffice()
    after_first = calls["soffice"]
    deps.find_soffice()  # cache calda: nessun nuovo rilevamento
    assert calls["soffice"] == after_first

    engine.set_mode("PORTABLE")  # deve invalidare la cache
    deps.find_soffice()
    assert calls["soffice"] == after_first + 1


def test_full_mode_missing_reports_libreoffice(monkeypatch):
    monkeypatch.setattr(deps, "find_soffice", lambda: None)
    assert "libreoffice" in engine.full_mode_missing()


def test_full_mode_missing_empty_when_present(monkeypatch):
    monkeypatch.setattr(deps, "find_soffice", lambda: r"C:\soffice.exe")
    assert engine.full_mode_missing() == []
