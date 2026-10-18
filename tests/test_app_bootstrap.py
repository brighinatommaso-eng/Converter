"""Smoke test del bootstrap GUI (run()).

I 130 test non importavano mai converter.gui.app: un import rimosso da ruff
(es. `engine`) sfuggiva fino al lancio reale. Qui si esegue run() fino in fondo
(con dialog e loop neutralizzati) così un NameError da import mancante fa fallire
pytest, non l'utente. In più un controllo ruff F821 copre l'intero package."""

import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class _StubDialog:
    """Dialog finto: costruzione no-op, exec() ritorna subito."""

    def __init__(self, *args, **kwargs):
        pass

    def exec(self):
        return 0


def test_run_resolves_imports_and_bootstraps(monkeypatch, tmp_path):
    import converter.gui.app as appmod
    from converter.core import deps, engine, paths

    # Config isolata + modalità ricordata: nessun chooser interattivo all'avvio.
    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    engine.persist("FULL")
    # Niente LibreOffice: evita di far partire il warm-up reale di soffice.
    monkeypatch.setattr(deps, "find_soffice", lambda: None)

    # Dialog neutralizzati: la modale FULL-mancante e gli altri non bloccano.
    monkeypatch.setattr(appmod, "EngineModeDialog", _StubDialog)
    monkeypatch.setattr(appmod, "MissingDepsDialog", _StubDialog)
    monkeypatch.setattr(appmod, "DependencyDialog", _StubDialog)

    # Riusa l'eventuale QApplication già viva e NON avviare il vero event loop.
    existing = QApplication.instance() or QApplication([])
    monkeypatch.setattr(appmod, "QApplication", lambda *a, **k: existing)
    monkeypatch.setattr(type(existing), "exec", lambda self: 0)

    rc = appmod.run()  # se un import (engine, dialog) manca → NameError qui

    assert rc == 0
    # I riferimenti a engine.* dentro run() sono stati eseguiti davvero.
    assert engine.get_mode() == "FULL"


class _ChooserStub:
    """Chooser pilotabile: registra di essere stato mostrato e restituisce una
    scelta + uno stato 'remember' fissati dalla classe."""

    shown = 0
    mode = "PORTABLE"
    remember = False

    def __init__(self, *args, **kwargs):
        type(self).shown += 1

    def exec(self):
        return 1

    def selected_mode(self):
        return type(self).mode


def _bootstrap_with_chooser(monkeypatch, appmod, tmp_path, *, mode, remember):
    from converter.core import deps

    monkeypatch.setattr(_ChooserStub, "shown", 0)
    monkeypatch.setattr(_ChooserStub, "mode", mode)
    monkeypatch.setattr(_ChooserStub, "remember", remember)
    monkeypatch.setattr(appmod, "EngineModeDialog", _ChooserStub)
    monkeypatch.setattr(appmod, "MissingDepsDialog", _StubDialog)
    monkeypatch.setattr(appmod, "DependencyDialog", _StubDialog)
    monkeypatch.setattr(deps, "find_soffice", lambda: None)

    existing = QApplication.instance() or QApplication([])
    monkeypatch.setattr(appmod, "QApplication", lambda *a, **k: existing)
    monkeypatch.setattr(type(existing), "exec", lambda self: 0)
    return appmod.run()


def test_first_run_shows_chooser_and_unchecked_remember_persists_nothing(monkeypatch, tmp_path):
    """Nessun engine_mode salvato → il chooser appare. Scegliere SENZA 'ricorda'
    fissa la sessione ma NON deve scrivere engine_mode su disco: il prossimo avvio
    deve essere di nuovo un first-run."""
    import converter.gui.app as appmod
    from converter.core import engine, paths

    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    assert engine.load_persisted() is None  # chiave assente → "nessuna scelta"

    rc = _bootstrap_with_chooser(monkeypatch, appmod, tmp_path,
                                 mode="PORTABLE", remember=False)
    assert rc == 0
    assert _ChooserStub.shown == 1                  # il chooser è stato mostrato
    assert engine.get_mode() == "PORTABLE"          # sessione impostata
    assert paths.read_setting("engine_mode") is None  # NIENTE persistito
    assert engine.load_persisted() is None            # il prossimo avvio = first-run


def test_first_run_checked_remember_persists_choice(monkeypatch, tmp_path):
    """Scegliere CON 'ricorda' scrive engine_mode: il prossimo avvio salta il chooser."""
    import converter.gui.app as appmod
    from converter.core import engine, paths

    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    assert engine.load_persisted() is None

    rc = _bootstrap_with_chooser(monkeypatch, appmod, tmp_path,
                                 mode="PORTABLE", remember=True)
    assert rc == 0
    assert _ChooserStub.shown == 1
    assert paths.read_setting("engine_mode") == "PORTABLE"  # persistito
    assert engine.load_persisted() == "PORTABLE"            # niente chooser al riavvio


def test_persisted_mode_skips_chooser(monkeypatch, tmp_path):
    """Con engine_mode presente, il chooser NON deve apparire."""
    import converter.gui.app as appmod
    from converter.core import engine, paths

    monkeypatch.setattr(paths, "config_dir", lambda: tmp_path)
    engine.persist("PORTABLE")

    rc = _bootstrap_with_chooser(monkeypatch, appmod, tmp_path,
                                 mode="FULL", remember=False)
    assert rc == 0
    assert _ChooserStub.shown == 0              # saltato: scelta già ricordata
    assert engine.get_mode() == "PORTABLE"      # usata la modalità persistita


def test_package_has_no_undefined_names():
    """Rete di sicurezza ampia: ruff F821 segnala nomi usati ma non definiti
    (tipico di un import rimosso da ruff --fix in un'altra parte del package)."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--select", "F821", "converter"],
        capture_output=True, text=True, cwd=_PROJECT_ROOT,
    )
    assert result.returncode == 0, (
        "Undefined names (stripped imports?):\n" + result.stdout + result.stderr
    )
