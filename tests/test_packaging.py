"""Invarianti del packaging: icone, identita' Windows e installer.

Ogni test qui fissa una regola che, se rotta, si vede solo DOPO l'installazione
(icona generica nella taskbar, scorciatoia assente, voce di menu che non
parte): in CI sono troppo tardi per accorgersene.
"""

from __future__ import annotations

import importlib.util
import re
import struct
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ICO = _ROOT / "converter" / "assets" / "converter.ico"
_ISS = _ROOT / "installer" / "converter.iss"


def _ico_frames() -> list[tuple[int, int, bytes]]:
    """(larghezza, altezza, dati) di ogni frame del file .ico."""
    data = _ICO.read_bytes()
    _reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert kind == 1, "non e' un file .ico"
    frames = []
    for index in range(count):
        offset = 6 + 16 * index
        width, height, _colors, _reserved2, _planes, _bits, size, file_offset = \
            struct.unpack_from("<BBBBHHII", data, offset)
        frames.append((width or 256, height or 256, data[file_offset:file_offset + size]))
    return frames


def test_icon_file_has_the_sizes_windows_asks_for():
    sizes = {width for width, _height, _payload in _ico_frames()}
    assert {16, 32, 48, 64, 128, 256} <= sizes, sizes


def test_small_icon_frames_are_readable_by_every_windows_tool():
    """I frame PNG-compressi si leggono solo da Explorer (Vista+): GDI+ (e la
    shell che lo usa) ricava rumore, e l'icona risultava "generica di Windows"
    fino al riavvio. Le dimensioni piccole devono quindi essere DIB classici;
    il 256 resta PNG (e' lo standard per quella dimensione)."""
    png_signature = b"\x89PNG\r\n\x1a\n"
    for width, _height, payload in _ico_frames():
        is_png = payload.startswith(png_signature)
        if width <= 128:
            assert not is_png, f"frame {width} in formato PNG: GDI+ non lo legge"
        else:
            assert is_png, f"frame {width} deve restare PNG"


def test_icon_file_matches_the_png_sources():
    """Il .ico deve essere quello che genera installer/tools/make-ico.py dalle
    PNG dell'app: se le PNG cambiano e il .ico no, icona di finestra e icona
    del file divergono."""
    spec = importlib.util.spec_from_file_location(
        "make_ico", _ROOT / "installer" / "tools" / "make-ico.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.build_ico() == _ICO.read_bytes(), (
        "converter/assets/converter.ico non e' aggiornato: "
        "rigeneralo con python installer/tools/make-ico.py")


def test_windows_app_id_matches_the_installer():
    """L'AppUserModelID dichiarato dall'app e quello registrato dall'installer
    devono combaciare: se divergono Windows non sa a che icona associare
    l'app e la taskbar mostra quella generica."""
    from converter.gui.app import _APP_USER_MODEL_ID

    script = _ISS.read_text(encoding="utf-8")
    match = re.search(r'#define\s+AppUserModelID\s+"([^"]+)"', script)
    assert match, "AppUserModelID non definito in installer/converter.iss"
    assert match.group(1) == _APP_USER_MODEL_ID


def test_installer_creates_start_menu_entry_and_offers_desktop_shortcut():
    """Il collegamento nel menu Start e' sempre; quello sul desktop e' una
    scelta del wizard (attiva di default)."""
    script = _ISS.read_text(encoding="utf-8")
    assert 'Name: "{autoprograms}\\{#AppName}"' in script
    assert 'Name: "{autodesktop}\\{#AppName}"' in script
    assert "Tasks: desktopicon" in script
    # In Inno 6 un task senza "Flags: unchecked" e' gia' selezionato: il flag
    # "checked" non esiste e il setup non compilerebbe.
    task_line = next(line for line in script.splitlines() if line.startswith('Name: "desktopicon"'))
    assert "unchecked" not in task_line


def test_installer_refreshes_the_windows_icon_cache():
    """Senza il refresh della cache, Explorer continua a mostrare l'icona del
    vecchio eseguibile fino al riavvio di Windows."""
    script = _ISS.read_text(encoding="utf-8")
    assert "ie4uinit.exe" in script and "-show" in script


def test_appimage_menu_entry_points_to_its_own_launcher():
    """La voce di menu dell'AppImage deve puntare ad AppRun: il comando
    /usr/bin/converter esiste solo nel pacchetto .deb."""
    script = (_ROOT / "installer" / "linux" / "build-appimage.sh").read_text(encoding="utf-8")
    assert "Exec=AppRun" in script
    desktop = (_ROOT / "installer" / "linux" / "converter.desktop").read_text(encoding="utf-8")
    assert "Exec=converter" in desktop


def test_linux_packages_ship_appstream_metadata():
    for name in ("metainfo.xml.in",):
        assert (_ROOT / "installer" / "linux" / name).is_file()
    deb = (_ROOT / "installer" / "linux" / "build-deb.sh").read_text(encoding="utf-8")
    appimage = (_ROOT / "installer" / "linux" / "build-appimage.sh").read_text(encoding="utf-8")
    assert "converter.metainfo.xml" in deb and "converter.metainfo.xml" in appimage
