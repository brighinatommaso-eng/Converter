"""Finestra principale di Converter.

Orchestrazione: drag&drop -> rilevamento -> coda -> (pannello opzioni) ->
conversione su thread pool. La finestra non contiene logica di conversione:
chiama solo detection, registry e pipeline tramite i worker.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME
from ..core import engine, paths
from ..core.detection import detect
from ..core.history import HistoryEntry, HistoryStore
from ..core.job import ConversionJob
from ..core.naming import build_output_path
from ..core.presets import Preset, PresetStore, reconcile_options
from ..core.registry import resolve, targets_for
from .badge_delegate import BadgeDelegate
from .dialogs import AboutDialog, DependencyDialog, EngineModeDialog, HistoryDialog, confirm_lossy
from .options_panel import OptionsPanel
from .progress_bar import TwoToneProgressBar
from .queue_model import QueueItem, QueueModel
from .theme import apply_titlebar, toggle_theme
from .workers import ConvertRunnable, WorkerSignals

# Destinazione di default per categoria, nell'ordine di ciò che si chiede più
# spesso. Prima vinceva il primo target lossless in ordine ALFABETICO: ogni
# JPG/PNG partiva verso BMP e ogni video verso AVI.
_PREFERRED_TARGETS = {
    "image": ("jpg", "png", "webp"),
    "audio": ("mp3", "wav", "flac"),
    "video": ("mp4", "mkv", "webm"),
    "data": ("xlsx", "csv", "json"),
    "document": ("docx", "pdf", "md", "html", "txt"),
    "model3d": ("glb", "stl", "obj"),
}


def _preferred_target(category: str | None, usable: list):
    by_format = {t.dst_format: t for t in usable}
    for fmt in _PREFERRED_TARGETS.get(category or "", ()):
        if fmt in by_format:
            return by_format[fmt]
    return None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(980, 600)
        self.setAcceptDrops(True)

        self.model = QueueModel()
        self.preset_store = PresetStore()
        self.history_store = HistoryStore()
        self.pool = QThreadPool.globalInstance()
        self.signals = WorkerSignals()
        self.signals.progress.connect(self._on_progress)
        self.signals.finished.connect(self._on_finished)

        self.same_as_source = True
        self.output_dir: Path | None = None
        self._total = 0
        self._completed = 0
        self._fractions: dict[int, float] = {}

        self._build_ui()
        self._build_menu()

    # --- Costruzione UI ---

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)

        # Barra azioni in alto.
        top = QHBoxLayout()
        st = self.style()
        add_btn = QPushButton("Add files…")
        add_btn.setIcon(st.standardIcon(QStyle.SP_FileDialogNewFolder))
        add_btn.clicked.connect(self._add_files)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setIcon(st.standardIcon(QStyle.SP_TrashIcon))
        self.clear_btn.clicked.connect(self._clear)
        top.addWidget(add_btn)
        top.addWidget(self.clear_btn)
        top.addStretch(1)

        # Controllo segmentato della modalità motore: sempre visibile, mostra a
        # colpo d'occhio quale modalità è attiva (il bottone attivo è "Primary")
        # e cambia modalità al clic — niente menu, niente riavvio.
        top.addWidget(QLabel("Mode:"))
        self.mode_full_btn = QPushButton("Full")
        self.mode_full_btn.setCheckable(True)
        self.mode_full_btn.setToolTip("Best quality, incl. faithful Office documents (needs LibreOffice).")
        self.mode_portable_btn = QPushButton("Portable")
        self.mode_portable_btn.setCheckable(True)
        self.mode_portable_btn.setToolTip("Works anywhere, no installs. Office-to-Office documents limited.")
        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)
        mode_group.addButton(self.mode_full_btn)
        mode_group.addButton(self.mode_portable_btn)
        self.mode_full_btn.clicked.connect(lambda: self._set_engine_mode("FULL"))
        self.mode_portable_btn.clicked.connect(lambda: self._set_engine_mode("PORTABLE"))
        top.addWidget(self.mode_full_btn)
        top.addWidget(self.mode_portable_btn)

        theme_btn = QPushButton("Toggle theme")
        theme_btn.setIcon(st.standardIcon(QStyle.SP_DesktopIcon))
        theme_btn.clicked.connect(self._toggle_theme)
        top.addWidget(theme_btn)
        root.addLayout(top)
        self._sync_mode_buttons()

        # Riga cartella di output.
        out_row = QHBoxLayout()
        self.same_chk = QCheckBox("Save next to source")
        self.same_chk.setChecked(True)
        self.same_chk.toggled.connect(self._on_same_toggled)
        out_row.addWidget(self.same_chk)
        self.out_edit = QLineEdit()
        self.out_edit.setPlaceholderText("Output folder… (type a path or Browse)")
        # Un percorso digitato vale quanto uno scelto con Browse: prima era
        # ignorato e i file finivano accanto alla sorgente senza avviso.
        self.out_edit.editingFinished.connect(self._on_output_typed)
        self.out_edit.setEnabled(False)
        out_row.addWidget(self.out_edit, 1)
        self.browse_btn = QPushButton("Browse…")
        self.browse_btn.setIcon(st.standardIcon(QStyle.SP_DirOpenIcon))
        self.browse_btn.setEnabled(False)
        self.browse_btn.clicked.connect(self._browse_output)
        out_row.addWidget(self.browse_btn)
        root.addLayout(out_row)

        # Riga preset: applica/salva/elimina configurazioni nominate.
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset:"))
        self.preset_combo = QComboBox()
        self.preset_combo.setMinimumWidth(200)
        preset_row.addWidget(self.preset_combo, 1)
        apply_sel_btn = QPushButton("Apply to selected")
        apply_sel_btn.setIcon(st.standardIcon(QStyle.SP_DialogApplyButton))
        apply_sel_btn.clicked.connect(lambda: self._apply_preset(all_files=False))
        apply_all_btn = QPushButton("Apply to all")
        apply_all_btn.setIcon(st.standardIcon(QStyle.SP_DialogApplyButton))
        apply_all_btn.clicked.connect(lambda: self._apply_preset(all_files=True))
        save_btn = QPushButton("Save current as…")
        save_btn.setIcon(st.standardIcon(QStyle.SP_DialogSaveButton))
        save_btn.clicked.connect(self._save_preset)
        del_btn = QPushButton("Delete")
        del_btn.setIcon(st.standardIcon(QStyle.SP_TrashIcon))
        del_btn.clicked.connect(self._delete_preset)
        for b in (apply_sel_btn, apply_all_btn, save_btn, del_btn):
            preset_row.addWidget(b)
        root.addLayout(preset_row)
        self._reload_presets()

        # Splitter: tabella + pannello opzioni.
        splitter = QSplitter(Qt.Horizontal)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setItemDelegate(BadgeDelegate(self.model, self.table))
        self.table.verticalHeader().setDefaultSectionSize(34)  # righe più alte per i pill
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 6):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # Le colonne con badge a pillola hanno bisogno di un minimo costante.
        header.setMinimumSectionSize(78)
        self.table.selectionModel().currentRowChanged.connect(self._on_row_selected)
        splitter.addWidget(self.table)

        self.options = OptionsPanel()
        self.options.changed.connect(self._on_options_changed)
        self.options.estimate_ready.connect(self._on_estimate_ready)
        splitter.addWidget(self.options)
        splitter.setSizes([560, 380])
        root.addWidget(splitter, 1)

        # Barra inferiore: progresso + azioni di conversione.
        bottom = QHBoxLayout()
        self.overall = TwoToneProgressBar()
        self.overall.setRange(0, 100)
        self.overall.setValue(0)
        bottom.addWidget(self.overall, 1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setIcon(st.standardIcon(QStyle.SP_DialogCancelButton))
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._cancel_all)
        bottom.addWidget(self.cancel_btn)
        self.convert_btn = QPushButton("Convert checked")
        self.convert_btn.setIcon(st.standardIcon(QStyle.SP_MediaPlay))
        self.convert_btn.setObjectName("Primary")
        self.convert_btn.clicked.connect(self._convert_all)
        bottom.addWidget(self.convert_btn)
        root.addLayout(bottom)

        self.status = QLabel("Drop files anywhere to begin.")
        self.status.setObjectName("Subtle")
        root.addWidget(self.status)

        self.setCentralWidget(central)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        history_action = QAction("History…", self)
        history_action.triggered.connect(self._open_history)
        file_menu.addAction(history_action)

        tools_menu = self.menuBar().addMenu("&Tools")
        mode_action = QAction("Engine mode…", self)
        mode_action.triggered.connect(self._change_engine_mode)
        tools_menu.addAction(mode_action)

        help_menu = self.menuBar().addMenu("&Help")
        deps_action = QAction("Dependencies…", self)
        deps_action.triggered.connect(lambda: DependencyDialog(self).exec())
        about_action = QAction(f"About {APP_NAME}", self)
        about_action.triggered.connect(lambda: AboutDialog(self).exec())
        help_menu.addAction(deps_action)
        help_menu.addAction(about_action)

    def _open_history(self) -> None:
        HistoryDialog(self, self.history_store, self._rerun_entry).exec()

    # --- Tema ---

    def _toggle_theme(self) -> None:
        """Alterna il tema e ri-tinge la barra del titolo: il QSS copre i widget,
        la caption è fuori dal QSS e va aggiornata a parte (vedi apply_titlebar)."""
        toggle_theme(QApplication.instance())
        apply_titlebar(self)

    def showEvent(self, event) -> None:
        # La caption si può tingere solo quando l'handle nativo esiste: al primo
        # show è garantito. apply_titlebar è idempotente e best-effort.
        super().showEvent(event)
        apply_titlebar(self)

    # --- Modalità motore ---

    def sync_engine_mode_ui(self) -> None:
        """Allinea il controllo segmentato alla modalità corrente. Chiamato dal
        bootstrap dopo che app.py ha fissato la modalità (chooser/persistenza)."""
        self._sync_mode_buttons()

    def _set_engine_mode(self, mode: str) -> None:
        """Cambio diretto dalla barra: applica alla SESSIONE e ricalcola la coda live.
        Un file con target Office in grigio (Portable) torna disponibile passando a
        Full e viceversa — senza riavvio. NON persiste: usare il toggle non è
        "ricorda la scelta". Solo la checkbox del chooser scrive su disco."""
        if mode == engine.get_mode():
            self._sync_mode_buttons()  # coerenza visiva anche se invariato
            return
        engine.set_mode(mode)
        self._sync_mode_buttons()
        self._refresh_targets()
        self.status.setText(f"Engine mode: {mode.title()}.")

    def _sync_mode_buttons(self) -> None:
        """Riflette engine.get_mode() sul controllo: il bottone attivo è evidenziato
        (objectName 'Primary'); serve un re-polish per applicare lo stile a runtime."""
        mode = engine.get_mode()
        for btn, on in ((self.mode_full_btn, mode == "FULL"),
                        (self.mode_portable_btn, mode == "PORTABLE")):
            btn.setChecked(on)
            btn.setObjectName("Primary" if on else "")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _change_engine_mode(self) -> None:
        """Cambia la modalità a runtime dal menu Tools (con blurb + 'ricorda').
        Una scelta ricordata non è una trappola. Dopo il cambio si ricalcolano le
        destinazioni: ciò che una modalità offre, l'altra può negarlo, e i verdetti
        devono restare onesti."""
        dlg = EngineModeDialog(self, current=engine.get_mode(), cancellable=True)
        if not dlg.exec():  # annullato
            return
        new_mode = dlg.selected_mode()
        engine.set_mode(new_mode)
        if dlg.remember:
            engine.persist(new_mode)
        self._sync_mode_buttons()
        self._refresh_targets()
        self.status.setText(f"Engine mode: {new_mode.title()}.")

    def _refresh_targets(self) -> None:
        """Ricalcola destinazioni e verdetti di ogni file in coda per la modalità
        corrente. Se il target scelto non è più disponibile, resta selezionato ma la
        coppia comparirà in grigio (run_job la rifiuterebbe con un motivo chiaro)."""
        for row, item in enumerate(self.model.items):
            src = item.detected.format_id
            item.targets = targets_for(src) if src else []
            if src and item.dst_format:
                conv = resolve(src, item.dst_format)
                if conv:
                    item.verdict = conv.verdict(src, item.dst_format, item.options)
            self.model.row_changed(row)
        current = self.table.currentIndex().row()
        self.options.set_item(self.model.items[current] if current >= 0 else None)

    # --- Drag & drop ---

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        self._add_paths([p for p in paths if p.is_file()])

    # --- Gestione coda ---

    def _add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add files", self._initial_input_dir())
        if not files:
            return
        self._add_paths([Path(f) for f in files])
        # Ricorda la cartella per la prossima volta (store in %APPDATA%\Converter).
        paths.write_setting("last_input_dir", str(Path(files[0]).parent))

    @staticmethod
    def _initial_input_dir() -> str:
        """Cartella d'apertura del dialog 'Add files': l'ultima usata se valida,
        altrimenti Downloads/home — MAI la cartella d'installazione."""
        saved = paths.read_setting("last_input_dir")
        if saved and Path(saved).is_dir():
            return saved
        downloads = Path.home() / "Downloads"
        return str(downloads if downloads.is_dir() else Path.home())

    def _add_paths(self, paths: list[Path]) -> None:
        added = skipped = 0
        for path in paths:
            # Il file può sparire/diventare illeggibile tra la selezione (o il
            # drop) e ora: saltalo senza far crashare l'aggiunta dell'intero lotto.
            try:
                src_size = path.stat().st_size
                detected = detect(path)
            except OSError:
                skipped += 1
                continue
            targets = targets_for(detected.format_id) if detected.format_id else []
            item = QueueItem(
                src_path=path, detected=detected, targets=targets,
                src_size=src_size,
            )
            if not targets:
                item.status = "Unsupported"
                item.checked = False  # non convertibile: spunta tolta di default
            else:
                # Assegna subito un target di default sensato così "Convert all"
                # funziona anche sui file mai selezionati. Si evita il formato
                # uguale alla sorgente (conversione a vuoto) e si preferisce lossless.
                src = detected.format_id
                usable = [t for t in targets if t.available and t.dst_format != src]
                default = (_preferred_target(detected.category, usable)
                           or next((t for t in usable if t.verdict.is_lossless), None)
                           or (usable[0] if usable else None)
                           or next((t for t in targets if t.available), None))
                if default:
                    item.dst_format = default.dst_format
                    item.verdict = default.verdict
            self.model.add_item(item)
            added += 1
        if added:
            skip_note = f" — {skipped} skipped (no longer available)" if skipped else ""
            self.status.setText(f"{len(self.model.items)} file(s) in queue{skip_note}.")
            # Seleziona automaticamente il primo file aggiunto se nulla è selezionato.
            if not self.table.selectionModel().hasSelection():
                self.table.selectRow(len(self.model.items) - added)
        elif skipped:
            self.status.setText(f"{skipped} file(s) skipped — no longer available.")

    def _clear(self) -> None:
        self.model.clear()
        self.options.set_item(None)
        self.status.setText("Queue cleared.")

    def _on_row_selected(self, current, _previous) -> None:
        if current.isValid():
            self.options.set_item(self.model.items[current.row()])
        else:
            self.options.set_item(None)
        self._reset_progress_if_idle()

    def _on_options_changed(self) -> None:
        row = self.table.currentIndex().row()
        if row >= 0:
            item = self.model.items[row]
            # Nuove impostazioni su una riga già convertita = nuovo lavoro. Senza
            # questo reset `items_to_convert` la escluderebbe (convertible esclude
            # "Done") e "Convert checked" ignorerebbe in silenzio la modifica.
            if item.status == "Done":
                item.status = "Ready"
                item.out_size = None
                item.output_path = None
            self.model.row_changed(row)
        self._reset_progress_if_idle()

    def _on_estimate_ready(self) -> None:
        row = self.table.currentIndex().row()
        if row >= 0:
            self.model.row_changed(row)

    def _reset_progress_if_idle(self) -> None:
        """La barra di avanzamento globale torna a 0% quando si riconfigura un
        job (cambio target o selezione di un altro file): il 100% residuo della
        conversione precedente sarebbe fuorviante. Non si tocca durante un batch
        attivo, dove la barra riflette il progresso reale."""
        if not self.cancel_btn.isEnabled():  # cancel abilitato == conversione in corso
            self.overall.setValue(0)

    # --- Preset ---

    def _reload_presets(self) -> None:
        self.preset_combo.clear()
        presets = self.preset_store.load()
        if not presets:
            self.preset_combo.addItem("No presets saved", None)
            self.preset_combo.setEnabled(False)
            return
        self.preset_combo.setEnabled(True)
        for i, preset in enumerate(presets):
            label = preset.name if preset.valid else f"{preset.name}  (invalid)"
            self.preset_combo.addItem(label, preset)
            if not preset.valid:
                # Preset orfano (formato non più producibile): mostrato ma non applicabile.
                self.preset_combo.model().item(i).setEnabled(False)

    def _current_preset(self) -> Preset | None:
        data = self.preset_combo.currentData()
        return data if isinstance(data, Preset) else None

    def _save_preset(self) -> None:
        row = self.table.currentIndex().row()
        if row < 0 or not self.model.items[row].dst_format:
            QMessageBox.information(self, "Save preset",
                                    "Select a file with a chosen target format first.")
            return
        item = self.model.items[row]
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        name = name.strip()
        if not ok or not name:
            return
        existing = {p.name for p in self.preset_store.load()}
        if name in existing:
            confirm = QMessageBox.question(self, "Overwrite preset",
                                           f"A preset named “{name}” already exists. Overwrite it?")
            if confirm != QMessageBox.Yes:
                return
        preset = Preset(name=name, dst_format=item.dst_format,
                        options=dict(item.options), strip_metadata=item.strip_metadata)
        self.preset_store.add(preset)
        self._reload_presets()
        self.preset_combo.setCurrentText(name)
        self.status.setText(f"Saved preset “{name}”.")

    def _delete_preset(self) -> None:
        preset = self._current_preset()
        if preset is None:
            return
        confirm = QMessageBox.question(self, "Delete preset", f"Delete preset “{preset.name}”?")
        if confirm == QMessageBox.Yes:
            self.preset_store.delete(preset.name)
            self._reload_presets()
            self.status.setText(f"Deleted preset “{preset.name}”.")

    def _apply_preset(self, all_files: bool) -> None:
        preset = self._current_preset()
        if preset is None:
            return
        if not preset.valid:
            self.status.setText(f"Preset “{preset.name}” targets a format that's no longer available.")
            return

        if all_files:
            rows = range(len(self.model.items))
        else:
            current = self.table.currentIndex().row()
            if current < 0:
                self.status.setText("Select a file first, or use “Apply to all”.")
                return
            rows = [current]

        applied = skipped = 0
        for row in rows:
            if self._apply_preset_to_item(preset, self.model.items[row]):
                applied += 1
                self.model.row_changed(row)
            else:
                skipped += 1

        # Aggiorna il pannello se la riga selezionata è cambiata.
        current = self.table.currentIndex().row()
        if current >= 0:
            self.options.set_item(self.model.items[current])

        msg = f"Applied “{preset.name}” to {applied} file(s)"
        if skipped:
            msg += f", skipped {skipped} (incompatible with {preset.dst_format.upper()})"
        self.status.setText(msg + ".")

    def _apply_preset_to_item(self, preset: Preset, item) -> bool:
        """Applica un preset a un file SE il formato target è raggiungibile da
        quel file. Riconcilia le opzioni con lo schema attuale e ricalcola il
        verdetto onesto. Ritorna False se incompatibile (verrà saltato)."""
        available = {t.dst_format for t in item.targets if t.available}
        if preset.dst_format not in available:
            return False
        src = item.detected.format_id
        item.dst_format = preset.dst_format
        item.options = reconcile_options(src, preset.dst_format, preset.options)
        item.strip_metadata = preset.strip_metadata
        conv = resolve(src, preset.dst_format)
        item.verdict = conv.verdict(src, preset.dst_format, item.options) if conv else None
        return True

    # --- Cronologia / re-run ---

    def _record_history(self, item, result) -> None:
        """Registra ogni conversione conclusa (successo o fallimento)."""
        entry = HistoryEntry(
            source_path=str(item.src_path),
            source_format=item.detected.format_id or "",
            dst_format=item.dst_format,
            options=dict(item.options),
            strip_metadata=item.strip_metadata,
            output_path=str(item.output_path) if item.output_path else None,
            result="ok" if result.ok else "failed",
            output_size=result.output_size if result.ok else None,
            error=None if result.ok else result.error,
            verdict=(result.verdict or item.verdict).level if (result.verdict or item.verdict) else None,
        )
        self.history_store.append(entry)

    def _rerun_entry(self, entry) -> None:
        """Ri-accoda una voce di cronologia con le stesse impostazioni.

        La sorgente mancante è un fallimento PULITO: nessun crash, nessun file
        vuoto. Le opzioni vengono riconciliate (sopravvive agli aggiornamenti
        del converter) e si usano le impostazioni di output correnti.
        """
        src = Path(entry.source_path)
        if not src.exists():
            self.status.setText(f"Re-run failed: source no longer exists — {src}")
            return

        detected = detect(src)
        targets = targets_for(detected.format_id) if detected.format_id else []
        available = {t.dst_format for t in targets if t.available}
        if entry.dst_format not in available:
            self.status.setText(
                f"Re-run failed: {entry.dst_format.upper()} is no longer a valid target for this file.")
            return

        options = reconcile_options(detected.format_id, entry.dst_format, entry.options)
        item = QueueItem(
            src_path=src, detected=detected, targets=targets,
            src_size=src.stat().st_size, dst_format=entry.dst_format,
            options=options, strip_metadata=entry.strip_metadata,
        )
        conv = resolve(detected.format_id, entry.dst_format)
        item.verdict = conv.verdict(detected.format_id, entry.dst_format, options) if conv else None
        self.model.add_item(item)
        self.table.selectRow(len(self.model.items) - 1)
        self.status.setText(f"Re-added “{src.name}” to the queue — press Convert all to run.")

    # --- Output ---

    def _on_same_toggled(self, checked: bool) -> None:
        self.same_as_source = checked
        self.out_edit.setEnabled(not checked)
        self.browse_btn.setEnabled(not checked)

    def _on_output_typed(self) -> None:
        text = self.out_edit.text().strip()
        self.output_dir = Path(text).expanduser() if text else None

    def _browse_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if folder:
            self.output_dir = Path(folder)
            self.out_edit.setText(folder)

    # --- Conversione ---

    def _convert_all(self) -> None:
        # Premere Convert "arma" la stilatura degli errori del pannello: se la
        # riga selezionata ha un input invalido, l'utente lo vede subito in rosso.
        self.options.arm_validation()
        items = self.model.items_to_convert()  # solo i file spuntati
        if not items:
            self.status.setText("Nothing to convert — tick at least one file.")
            return
        if not self.same_as_source:
            # Un percorso nel campo vale anche se Invio non è stato premuto.
            typed = self.out_edit.text().strip()
            if typed:
                self.output_dir = Path(typed).expanduser()
            if self.output_dir is None:
                self.status.setText("Choose an output folder, or tick “Save next to source”.")
                return
        # File convertibili lasciati senza spunta: esclusi in silenzio, ma contati.
        skipped = sum(1 for it in self.model.items
                      if it.convertible and not it.checked and it.status != "Converting")

        lossy_info = [
            (it.src_path.name, it.dst_format.upper(), it.verdict.reason)
            for it in items if it.verdict and it.verdict.is_lossy
        ]
        if not confirm_lossy(self, lossy_info):
            return

        self._total = len(items)
        self._completed = 0
        self._fractions = {}
        self.convert_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        # Svuotare la coda durante un batch corromperebbe gli indici di riga su
        # cui viaggiano i segnali progress/finished: Clear resta spento fino a fine batch.
        self.clear_btn.setEnabled(False)
        self.overall.setValue(0)
        skip_note = f" — {skipped} unchecked, skipped" if skipped else ""
        self.status.setText(f"Converting {self._total} file(s){skip_note}…")

        reserved: set[Path] = set()  # path già assegnati in QUESTO batch (non ancora su disco)
        started = 0
        for it in items:
            # Identità, non uguaglianza: due righe con gli stessi valori non devono
            # far finire i segnali della seconda sulla prima (items.index usa __eq__).
            row = next(i for i, candidate in enumerate(self.model.items) if candidate is it)
            try:
                output_path = build_output_path(
                    it.src_path, it.dst_format, self.output_dir, self.same_as_source, reserved)
            except OSError as exc:
                # Cartella di output non scrivibile (permessi, disco/USB rimosso):
                # una riga fallita non deve sollevare dal click e lasciare il batch
                # a metà, con Convert disabilitato per sempre.
                it.status = "Failed"
                it.error = f"Could not prepare the output file: {exc}"
                self.model.row_changed(row)
                self._fractions[row] = 1.0
                self._completed += 1
                continue
            reserved.add(output_path)
            started += 1
            it.output_path = output_path
            it.status = "Converting"
            it.progress = 0.0
            it.error = None
            it.cancel = it.cancel.__class__()  # token fresco per ogni run
            self._fractions[row] = 0.0
            self.model.row_changed(row)

            job = ConversionJob(
                src_path=it.src_path, src_format=it.detected.format_id,
                dst_format=it.dst_format, output_path=output_path,
                options=dict(it.options), strip_metadata=it.strip_metadata,
                cancel=it.cancel,
            )
            self.pool.start(ConvertRunnable(row, job, self.signals))

        # Nessun job avviato (tutte le righe sono fallite in preparazione): il
        # batch è già concluso, la UI va sbloccata subito.
        if not started and self._completed >= self._total:
            self._finish_batch()

    def _on_progress(self, row: int, fraction: float) -> None:
        if 0 <= row < len(self.model.items):
            self.model.items[row].progress = fraction
            self._fractions[row] = fraction
            self.model.row_changed(row)
            self._refresh_overall()

    def _on_finished(self, row: int, result) -> None:
        # La riga può non esistere più (es. Clear premuto a batch in corso):
        # in tal caso NON tocchiamo il modello, ma contiamo comunque il job così
        # il batch si conclude e l'UI non resta bloccata (vedi anche _on_progress).
        if 0 <= row < len(self.model.items):
            item = self.model.items[row]
            if result.ok:
                item.status = "Done"
                item.progress = 1.0
                item.out_size = result.output_size
                item.verdict = result.verdict or item.verdict
            elif result.cancelled:
                # Annullato dall'utente: non è un fallimento e non va in cronologia.
                item.status = "Cancelled"
                item.progress = 0.0
                item.error = None
            else:
                item.status = "Failed"
                item.error = result.error
            self.model.row_changed(row)
            if not result.cancelled:
                self._record_history(item, result)
        self._fractions[row] = 1.0

        self._completed += 1
        self._refresh_overall()
        if self._completed >= self._total:
            self._finish_batch()

    def _refresh_overall(self) -> None:
        if self._total == 0:
            return
        progress = sum(self._fractions.values()) / self._total
        self.overall.setValue(int(progress * 100))

    def _finish_batch(self) -> None:
        self.convert_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.clear_btn.setEnabled(True)
        self.overall.setValue(100)
        done = sum(1 for it in self.model.items if it.status == "Done")
        failed = sum(1 for it in self.model.items if it.status == "Failed")
        cancelled = sum(1 for it in self.model.items if it.status == "Cancelled")
        msg = f"Finished: {done} done"
        if failed:
            msg += f", {failed} failed (see the Status column)"
        if cancelled:
            msg += f", {cancelled} cancelled"
        self.status.setText(msg + ".")

    def _cancel_all(self) -> None:
        # I job ancora in coda nel pool non partiranno (run_job controlla il token);
        # quelli già in corso: ffmpeg viene ucciso, i motori in-process finiscono
        # il passo corrente (PDF: si ferma tra le fasi).
        for it in self.model.items:
            if it.status == "Converting":
                it.cancel.cancel()
        self.status.setText("Cancelling… (files already being converted may still finish)")
