"""I font impacchettati (Inter) devono caricarsi e tutta la GUI deve usare il font
dell'applicazione: i widget ereditano Inter da QApplication.setFont, non un font
costruito dallo stylesheet. Nessun riferimento residuo al font precedente
(Anthropic)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PySide6")

from PySide6.QtGui import QFontDatabase, QFontInfo  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QWidget,
)

import converter.converters  # noqa: F401,E402
from converter.core import engine  # noqa: E402
from converter.gui.app import _load_fonts  # noqa: E402
from converter.gui.main_window import MainWindow  # noqa: E402
from converter.gui.theme import apply_theme  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

_PACKAGE_DIR = Path(__file__).resolve().parent.parent
_CODE_PATHS = [_PACKAGE_DIR / "converter", _PACKAGE_DIR / "converter_app.py"]


def _setup_app() -> str | None:
    """Replica l'ordine di run(): carica Inter, la imposta come font dell'app,
    poi applica il tema (che riapplica il font DOPO lo stylesheet)."""
    family = _load_fonts()
    if family:
        font = _app.font()
        font.setFamily(family)
        _app.setFont(font)
    apply_theme(_app, "light")
    return family


def test_load_fonts_registers_inter():
    """Dopo _load_fonts, Qt deve conoscere la famiglia 'Inter' (offscreen)."""
    family = _load_fonts()
    assert family is not None, "Inter non caricato da converter/assets/fonts"
    assert family.lower() == "inter", f"famiglia inattesa: {family!r}"
    available = [f for f in QFontDatabase.families() if f.lower().startswith("inter")]
    assert "Inter" in available, f"font Inter non disponibile in Qt: {available}"


def test_no_font_family_in_stylesheet():
    """Lo stylesheet non deve fissare la famiglia: il font globale viene
    dall'applicazione, così ogni widget lo eredita (niente font "paralleli")."""
    _setup_app()
    assert "font-family" not in _app.styleSheet()


def test_main_window_widgets_use_app_font():
    """I widget REALI della MainWindow (menu, pulsanti, tabella, header, label)
    devono usare Inter ereditata dal font dell'applicazione.

    Regressione: prima della correzione lo stylesheet imponeva
    `* { font-family: "Inter", "Segoe UI Variable Text", "Segoe UI", ... }`, così
    ogni widget portava nel proprio QFont quella lista di fallback (families()
    conteneva 'Segoe UI') invece del font dell'applicazione: qui sotto il secondo
    assert falliva. Ora families() è la sola [ 'Inter' ] ereditata dall'app.
    """
    _setup_app()
    engine.set_mode("FULL")
    win = MainWindow()
    win.show()
    _app.processEvents()

    widgets = [win, *win.findChildren(QWidget)]
    # Sanity: ci aspettiamo davvero i widget di testo principali tra i figli.
    must_have = (QPushButton, QLabel, QTableView, QHeaderView)
    missing = [
        cls.__name__ for cls in must_have
        if not any(isinstance(w, cls) for w in widgets)
    ]
    assert not missing, f"widget di testo mancanti nella MainWindow: {missing}"

    for w in widgets:
        font = w.font()
        resolved = QFontInfo(font).family()
        assert resolved.startswith("Inter"), (
            f"{type(w).__name__} {w.objectName()!r}: font risolto {resolved!r}, "
            "atteso Inter (font dell'applicazione)"
        )
        fallbacks = [f for f in font.families() if not f.startswith("Inter")]
        assert not fallbacks, (
            f"{type(w).__name__} {w.objectName()!r}: font costruito dallo "
            f"stylesheet (fallback {fallbacks}) invece che ereditato dall'app"
        )


def test_no_anthropic_reference_in_source():
    """Nessun riferimento residuo al font precedente nel codice distribuito."""
    hits = []
    for code in _CODE_PATHS:
        paths = code.rglob("*.py") if code.is_dir() else [code]
        for path in paths:
            if "anthropic" in path.read_text(encoding="utf-8", errors="ignore").lower():
                hits.append(str(path.relative_to(_PACKAGE_DIR)))
    assert not hits, f"riferimenti ad Anthropic ancora presenti: {hits}"
