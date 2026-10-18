"""Bootstrap dell'applicazione GUI."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QRunnable, QThreadPool
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


def _load_fonts() -> None:
    """Carica i font impacchettati in assets/fonts (best-effort).

    Se la cartella o i file mancano (es. rimossi per distribuire senza il font),
    la GUI ricade sul fallback di sistema definito nel QSS. Non deve mai bloccare
    l'avvio.
    """
    fonts_dir = _asset_path("fonts")
    if not fonts_dir.is_dir():
        return
    for otf in fonts_dir.glob("*.otf"):
        QFontDatabase.addApplicationFont(str(otf))


def _app_icon() -> QIcon:
    """Icona dell'app. Su Windows preferisce il .ico multi-risoluzione (title bar,
    taskbar e Alt-Tab usano dimensioni diverse: il .ico le contiene tutte e non le
    scala male). Fallback al PNG 256 se il .ico manca o su altre piattaforme."""
    ico = _asset_path("converter.ico")
    if sys.platform == "win32" and ico.exists():
        return QIcon(str(ico))
    return QIcon(str(_asset_path("converter_icon_256.png")))


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

    icon = _app_icon()
    app.setWindowIcon(icon)

    # Import dei converter: popola il registry via auto-registrazione.
    from .. import converters  # noqa: F401

    _load_fonts()  # PRIMA di apply_theme: il QSS imposta la famiglia Inter
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
