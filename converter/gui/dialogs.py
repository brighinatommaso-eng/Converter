"""Dialog: Informazioni, report dipendenze e avviso di conversione lossy."""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_TAGLINE, __version__
from ..core import deps, engine
from .theme import badge_color


class AboutDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)

        title = QLabel(APP_NAME)
        title.setObjectName("Title")
        layout.addWidget(title)
        layout.addWidget(QLabel(f"Version {__version__}"))
        layout.addWidget(QLabel(APP_TAGLINE))

        note = QLabel(
            "Converter runs 100% locally. No file ever leaves your computer and "
            "the app makes no network calls.\n\n"
            "It never lies about quality: every conversion shows whether it is "
            "lossless or lossy, and why."
        )
        note.setObjectName("Subtle")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class DependencyDialog(QDialog):
    """Mostra lo stato delle dipendenze esterne e come installarle."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Dependencies")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Converter degrades gracefully: missing tools only disable the "
            "conversions that need them — everything else keeps working."
        )
        intro.setWordWrap(True)
        intro.setObjectName("Subtle")
        layout.addWidget(intro)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)

        for name, status in deps.report().items():
            mark = "✓" if status.ok else "✗"
            row = QLabel(f"<b>{mark} {name}</b>")
            row.setTextFormat(Qt.RichText)
            inner_layout.addWidget(row)
            if not status.ok:
                detail = QLabel(f"   {status.reason}\n   Install: {status.install_hint}")
                detail.setObjectName("Subtle")
                detail.setWordWrap(True)
                inner_layout.addWidget(detail)
        inner_layout.addStretch(1)
        scroll.setWidget(inner)
        layout.addWidget(scroll)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class EngineModeDialog(QDialog):
    """Scelta della modalità motore: FULL o PORTABLE.

    All'avvio è bloccante e senza Cancel (l'app non procede finché non si sceglie);
    dal menu Tools è ri-apribile e annullabile, così una scelta ricordata non è una
    trappola. selected_mode()/remember vanno letti dopo exec()."""

    def __init__(self, parent=None, current: str | None = None,
                 cancellable: bool = False) -> None:
        super().__init__(parent)
        self._cancellable = cancellable
        self.setWindowTitle(f"{APP_NAME} — Engine mode")
        self.setMinimumWidth(460)
        self.setModal(True)
        if not cancellable:
            # All'avvio l'unica via d'uscita è Continue: niente X nella title bar
            # (Esc e closeEvent sono comunque bloccati sotto).
            self.setWindowFlag(Qt.WindowCloseButtonHint, False)
            self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        layout = QVBoxLayout(self)

        intro = QLabel("Choose how Converter should run. You can change this later "
                       "from the Tools menu.")
        intro.setWordWrap(True)
        intro.setObjectName("Subtle")
        layout.addWidget(intro)

        self._group = QButtonGroup(self)
        self._full = QRadioButton("Full")
        self._portable = QRadioButton("Portable")
        for radio, blurb in (
            (self._full,
             "Best quality, including faithful Office documents. Requires LibreOffice."),
            (self._portable,
             "Works anywhere, no installs. Office-to-Office documents limited."),
        ):
            self._group.addButton(radio)
            layout.addWidget(radio)
            sub = QLabel("      " + blurb)
            sub.setObjectName("Subtle")
            sub.setWordWrap(True)
            layout.addWidget(sub)

        # Selezione iniziale: la modalità corrente; default FULL.
        (self._portable if current == "PORTABLE" else self._full).setChecked(True)

        self._remember_chk = QCheckBox("Remember my choice")
        layout.addWidget(self._remember_chk)

        buttons = QDialogButtonBox()
        cont = buttons.addButton("Continue", QDialogButtonBox.AcceptRole)
        cont.setObjectName("Primary")
        if cancellable:
            buttons.addButton(QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_mode(self) -> str:
        return "PORTABLE" if self._portable.isChecked() else "FULL"

    @property
    def remember(self) -> bool:
        return self._remember_chk.isChecked()

    def reject(self) -> None:
        # All'avvio (non annullabile) Esc/chiusura NON devono scavalcare la scelta:
        # l'unico modo di proseguire è premere Continue (accept).
        if self._cancellable:
            super().reject()

    def closeEvent(self, event) -> None:
        if self._cancellable:
            super().closeEvent(event)
        else:
            event.ignore()


# Tool installabili dalla modale: chiave -> (etichetta, id winget, url download, rilevatore).
# Il rilevatore è ri-eseguito dopo l'installazione (cache già invalidata) per confermare.
_INSTALLERS = {
    "libreoffice": (
        "LibreOffice",
        "TheDocumentFoundation.LibreOffice",
        "https://www.libreoffice.org/download/download/",
        deps.find_soffice,
    ),
}


class MissingDepsDialog(QDialog):
    """Modale bloccante: in FULL mancano tool richiesti.

    Per ogni tool offre un pulsante Install (winget) oppure, se winget è assente,
    un Download che apre l'URL ufficiale. In alternativa l'utente può passare a
    PORTABLE e proseguire subito. "Continue in Full" si abilita solo quando non
    manca più nulla."""

    def __init__(self, parent=None, missing: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Missing tools")
        self.setMinimumWidth(560)
        self.setModal(True)
        # Non scavalcabile: si esce solo installando, passando a PORTABLE o (a tutto
        # risolto) con Continue. Niente X nella title bar; Esc/closeEvent bloccati sotto.
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self._procs: dict[str, QProcess] = {}
        self._rows: dict[str, tuple[QLabel, QPushButton]] = {}

        missing = missing if missing is not None else engine.full_mode_missing()
        # winget può mancare (Windows datati): in tal caso si ripiega sul download.
        self._has_winget = shutil.which("winget") is not None

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Full mode needs these tools, which weren't found. Install them, or "
            "switch to Portable mode to continue without them."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        for key in missing:
            label, _winget_id, url, _find = _INSTALLERS[key]
            row = QHBoxLayout()
            status = QLabel("✗")
            status.setObjectName("Subtle")
            row.addWidget(status)
            name = QLabel(f"<b>{label}</b>")
            name.setTextFormat(Qt.RichText)
            row.addWidget(name)
            row.addStretch(1)
            if self._has_winget:
                btn = QPushButton("Install")
                btn.clicked.connect(lambda _checked=False, k=key: self._install(k))
            else:
                btn = QPushButton("Download…")
                btn.clicked.connect(
                    lambda _checked=False, u=url: QDesktopServices.openUrl(QUrl(u)))
            row.addWidget(btn)
            layout.addLayout(row)
            self._rows[key] = (status, btn)

        self._log = QLabel("")
        self._log.setObjectName("Subtle")
        self._log.setWordWrap(True)
        layout.addWidget(self._log)

        buttons = QDialogButtonBox()
        portable_btn = buttons.addButton("Switch to Portable",
                                         QDialogButtonBox.DestructiveRole)
        portable_btn.clicked.connect(self._switch_portable)
        self._continue = buttons.addButton("Continue in Full",
                                           QDialogButtonBox.AcceptRole)
        self._continue.setObjectName("Primary")
        self._continue.setEnabled(not missing)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    def _install(self, key: str) -> None:
        if self._procs.get(key) is not None:  # installazione già in corso
            return
        label, winget_id, _url, _find = _INSTALLERS[key]
        _status, btn = self._rows[key]
        btn.setEnabled(False)
        self._log.setText(f"Installing {label} via winget…")

        proc = QProcess(self)
        # Ambiente ripulito dalle contaminazioni PyInstaller (come ogni tool esterno).
        qenv = QProcessEnvironment()
        for k, v in deps.clean_subprocess_env().items():
            qenv.insert(k, v)
        proc.setProcessEnvironment(qenv)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda k=key: self._on_output(k))
        proc.finished.connect(lambda code, _s, k=key: self._on_finished(k, code))
        proc.errorOccurred.connect(lambda _e, k=key: self._on_error(k))
        self._procs[key] = proc
        # --scope user evita (dove possibile) il prompt UAC dell'installazione di sistema.
        proc.start("winget", ["install", "--id", winget_id, "-e", "--scope", "user",
                              "--accept-package-agreements", "--accept-source-agreements"])

    def _on_output(self, key: str) -> None:
        proc = self._procs.get(key)
        if proc is None:
            return
        text = bytes(proc.readAllStandardOutput()).decode("utf-8", "replace").strip()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        if lines:
            self._log.setText(lines[-1])

    def _on_finished(self, key: str, code: int) -> None:
        label, _winget_id, url, find = _INSTALLERS[key]
        status, btn = self._rows[key]
        self._procs.pop(key, None)
        engine.refresh_deps()  # cache invalidate: il tool appena installato va ri-rilevato
        if find():
            status.setText("✓")
            btn.setEnabled(False)
            self._log.setText(f"{label} installed.")
        else:
            btn.setEnabled(True)
            self._log.setText(
                f"{label} install did not complete (exit {code}). Retry, "
                f"download it from {url}, or switch to Portable."
            )
        if not engine.full_mode_missing():
            self._continue.setEnabled(True)
            self.accept()  # tutto risolto: la modale si chiude da sola

    def _on_error(self, key: str) -> None:
        label, _winget_id, url, _find = _INSTALLERS[key]
        _status, btn = self._rows[key]
        self._procs.pop(key, None)
        btn.setEnabled(True)
        self._log.setText(
            f"Couldn't launch winget for {label}. Download it from {url}, "
            f"or switch to Portable."
        )

    def _switch_portable(self) -> None:
        engine.set_mode("PORTABLE")
        self.accept()

    def reject(self) -> None:
        # Non scavalcabile via Esc: l'uscita è una scelta esplicita (install /
        # Switch to Portable / Continue a tutto risolto).
        pass

    def closeEvent(self, event) -> None:
        event.ignore()


class HistoryDialog(QDialog):
    """Cronologia delle conversioni con possibilità di re-run.

    on_rerun(entry) è fornita dalla finestra principale: ri-accoda il job.
    """

    _HEADERS = ["When", "Source", "Conversion", "Quality", "Result"]

    def __init__(self, parent, store, on_rerun) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — History")
        self.resize(720, 420)
        self._store = store
        self._on_rerun = on_rerun
        self._entries = []

        layout = QVBoxLayout(self)
        self._table = QTableWidget(0, len(self._HEADERS))
        self._table.setHorizontalHeaderLabels(self._HEADERS)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        for col in range(len(self._HEADERS)):
            # Colonna "Source" elastica; le altre adattate al contenuto (niente troncamenti).
            header.setSectionResizeMode(col, QHeaderView.Stretch if col == 1 else QHeaderView.ResizeToContents)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        rerun_btn = QPushButton("Re-run selected")
        rerun_btn.setObjectName("Primary")
        rerun_btn.clicked.connect(self._rerun_selected)
        clear_btn = QPushButton("Clear history")
        clear_btn.clicked.connect(self._clear)
        buttons.addWidget(rerun_btn)
        buttons.addWidget(clear_btn)
        buttons.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        self._reload()

    def _reload(self) -> None:
        # Mostra le voci dalla più recente alla più vecchia.
        self._entries = list(reversed(self._store.load()))
        self._table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            when = entry.timestamp.replace("T", " ").replace("+00:00", " UTC")
            missing = not Path(entry.source_path).exists()
            name = Path(entry.source_path).name + ("  (missing)" if missing else "")
            cells = [
                when,
                name,
                f"{(entry.source_format or '?').upper()} → {entry.dst_format.upper()}",
                (entry.verdict or "").upper(),
                entry.result.upper(),
            ]
            for col, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                if col == 1:
                    cell.setToolTip(entry.source_path)
                if col == 3 and entry.verdict:
                    cell.setForeground(QColor(badge_color(entry.verdict)))
                if col == 4:
                    # Il colore del risultato segue la QUALITÀ reale: una
                    # conversione riuscita ma LOSSY resta ambra, non verde (che
                    # leggerebbe come "lossless"). Fallito → rosso.
                    level = "failed" if entry.result == "failed" else (entry.verdict or "lossless")
                    cell.setForeground(QColor(badge_color(level)))
                    if entry.error:
                        cell.setToolTip(entry.error)
                self._table.setItem(row, col, cell)

    def _rerun_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        entry = self._entries[row]
        self.accept()  # chiudi prima, così l'utente vede la coda aggiornata
        self._on_rerun(entry)

    def _clear(self) -> None:
        if QMessageBox.question(self, "Clear history", "Delete the entire conversion history?") \
                == QMessageBox.Yes:
            self._store.clear()
            self._reload()


def confirm_lossy(parent, lossy_items: list[tuple[str, str, str]]) -> bool:
    """Mostra l'avviso prima di una conversione lossy. lossy_items: lista di
    (nome_file, target, motivo). Ritorna True se l'utente conferma."""
    if not lossy_items:
        return True
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Lossy conversion")
    count = len(lossy_items)
    box.setText(f"{count} file{'s' if count > 1 else ''} will be converted with a lossy format.")
    details = "\n\n".join(f"• {name} → {tgt}\n   {reason}" for name, tgt, reason in lossy_items[:12])
    if count > 12:
        details += f"\n\n… and {count - 12} more."
    box.setInformativeText("Maximum-quality settings are used, but some data will be lost.")
    box.setDetailedText(details)
    box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
    box.button(QMessageBox.Ok).setText("Convert anyway")
    return box.exec() == QMessageBox.Ok
