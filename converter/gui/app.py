"""Bootstrap dell'applicazione GUI."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRunnable, QSize, QThreadPool
from PySide6.QtGui import QFontDatabase, QIcon
from PySide6.QtWidgets import QApplication

from .. import APP_NAME, __version__
from ..core import deps, engine
from .dialogs import DependencyDialog, EngineModeDialog, MissingDepsDialog
from .main_window import MainWindow
from .theme import apply_theme


def _asset_path(name: str) -> Path:
    """Percorso di un asset, valido sia da sorgente sia dentro un bundle
    PyInstaller. Da frozen gli asset stanno in <_MEIPASS>/assets; da sorgente
    in converter/assets accanto al package."""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    else:
        base = Path(__file__).resolve().parent.parent  # cartella del package converter/
    return base / "assets" / name


def _load_fonts() -> str | None:
    """Carica i font impacchettati in assets/fonts e ritorna la famiglia realmente
    registrata da Qt ("Inter"), oppure None se non disponibile.

    Best-effort: se la cartella o i file mancano (es. distribuzione senza il font)
    ritorna None e la GUI usa il fallback di sistema. Non deve mai bloccare l'avvio.
    """
    fonts_dir = _asset_path("fonts")
    if not fonts_dir.is_dir():
        return None
    families: list[str] = []
    for otf in sorted(fonts_dir.glob("*.otf")):
        fid = QFontDatabase.addApplicationFont(str(otf))
        if fid == -1:  # -1: Qt non è riuscito a caricare il file
            continue
        families.extend(QFontDatabase.applicationFontFamilies(fid))
    # La base è "Inter" (esistono anche "Inter Medium"/"Inter SemiBold").
    exact = [f for f in families if f.lower() == "inter"]
    prefixed = [f for f in families if f.lower().startswith("inter")]
    return next(iter(exact or prefixed), None)


_ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def _app_icon() -> QIcon:
    """Icona dell'app costruita da PNG in più dimensioni (assets/icons).

    Title bar, taskbar e Alt-Tab chiedono dimensioni diverse (16…256 px a seconda
    del ridimensionamento dello schermo). Prima si caricava il .ico: le sue
    immagini sono PNG compressi e Qt li legge solo tramite il plugin "qico";
    nella build Windows l'icona risultava VUOTA senza errori e la taskbar
    mostrava quella generica (sul file .exe si vedeva, perché lì l'icona è
    incorporata da PyInstaller, non caricata da Qt). Il PNG è supportato da
    QtGui senza plugin. Il .ico resta come ripiego."""
    icon = QIcon()
    for size in _ICON_SIZES:
        png = _asset_path(f"icons/converter_{size}.png")
        if png.exists():
            icon.addFile(str(png), QSize(size, size))
    if icon.isNull() or not icon.availableSizes():
        for fallback in ("converter.ico", "converter_icon_256.png"):
            candidate = QIcon(str(_asset_path(fallback)))
            if not candidate.isNull() and candidate.availableSizes():
                return candidate
    return icon


def _set_windows_app_id() -> None:
    """Windows: senza un AppUserModelID esplicito la taskbar mostra l'icona
    generica di Python invece della nostra. Va impostato PRIMA di mostrare la
    finestra."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Converter.App")
        except Exception:  # pragma: no cover - non deve mai bloccare l'avvio
            pass


class _WarmUpLibreOffice(QRunnable):
    """Scalda LibreOffice in background: il primo lancio è lento e NON deve
    bloccare il thread della GUI all'avvio."""

    def run(self) -> None:  # eseguito in un thread del pool
        try:
            from ..converters.document import warm_up_libreoffice
            warm_up_libreoffice()
        except Exception:
            pass  # il warm-up è best-effort: non deve mai disturbare l'avvio


def run() -> int:
    _set_windows_app_id()  # PRIMA della finestra: icona corretta in taskbar

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setApplicationVersion(__version__)
    # Linux/Wayland: GNOME e KDE abbinano finestra e icona tramite il file .desktop
    # (converter.desktop dei pacchetti .deb/AppImage). Su Windows non ha effetto.
    app.setDesktopFileName("converter")

    icon = _app_icon()
    app.setWindowIcon(icon)

    # Import dei converter: popola il registry via auto-registrazione.
    from .. import converters  # noqa: F401

    # Carica Inter e impostala come font dell'applicazione: è la base da cui ogni
    # widget eredita (apply_theme la riapplica dopo lo stylesheet, che altrimenti
    # la scavalca). Se Inter non c'è, resta il fallback di sistema.
    family = _load_fonts()
    if family:
        font = app.font()
        font.setFamily(family)
        app.setFont(font)
    apply_theme(app)
    window = MainWindow()
    window.setWindowIcon(icon)  # icona anche sulla finestra (title bar)

    # Modalità motore: ricordata o scelta all'avvio. Il chooser è bloccante e
    # senza Cancel — l'app non procede finché non si è scelto. Anche una chiusura
    # forzata ricade su una modalità valida (selected_mode() default FULL).
    mode = engine.load_persisted()
    if mode is None:
        chooser = EngineModeDialog(window)
        chooser.exec()
        mode = chooser.selected_mode()
        if chooser.remember:
            engine.persist(mode)
    engine.set_mode(mode)
    window.sync_engine_mode_ui()  # la barra riflette la modalità scelta all'avvio

    window.show()

    # In FULL con tool richiesti mancanti: modale bloccante con installazione o
    # passaggio a PORTABLE. Altrimenti, il consueto riepilogo informativo se manca
    # qualcosa di opzionale (ffmpeg, plugin immagine...).
    if engine.get_mode() == "FULL" and engine.full_mode_missing():
        MissingDepsDialog(window).exec()
    elif any(not status.ok for status in deps.report().values()):
        DependencyDialog(window).exec()

    # Warm-up di LibreOffice fuori dal thread UI: solo in FULL e se presente (la
    # modale può aver appena installato LibreOffice o attivato PORTABLE).
    if engine.get_mode() == "FULL" and deps.find_soffice():
        QThreadPool.globalInstance().start(_WarmUpLibreOffice())

    return app.exec()
