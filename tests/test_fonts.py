"""I font impacchettati (Inter) devono caricarsi e risultare disponibili in Qt, e
il codice non deve più contenere riferimenti al font precedente (Anthropic)."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from converter.gui.app import _load_fonts  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv[:1])

_PACKAGE_DIR = Path(__file__).resolve().parent.parent
_CODE_PATHS = [_PACKAGE_DIR / "converter", _PACKAGE_DIR / "converter_app.py"]


def test_load_fonts_registers_inter():
    """Dopo _load_fonts, Qt deve conoscere la famiglia 'Inter' (offscreen)."""
    family = _load_fonts()
    assert family is not None, "Inter non caricato da converter/assets/fonts"
    assert family.lower() == "inter", f"famiglia inattesa: {family!r}"
    available = [f for f in QFontDatabase.families() if f.lower().startswith("inter")]
    assert "Inter" in available, f"font Inter non disponibile in Qt: {available}"


def test_no_anthropic_reference_in_source():
    """Nessun riferimento residuo al font precedente nel codice distribuito."""
    hits = []
    for code in _CODE_PATHS:
        paths = code.rglob("*.py") if code.is_dir() else [code]
        for path in paths:
            if "anthropic" in path.read_text(encoding="utf-8", errors="ignore").lower():
                hits.append(str(path.relative_to(_PACKAGE_DIR)))
    assert not hits, f"riferimenti ad Anthropic ancora presenti: {hits}"
