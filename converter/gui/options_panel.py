"""Pannello opzioni: costruisce i widget a partire dallo schema dichiarativo.

Questo è il punto chiave dell'architettura: nessun riferimento a formati o
converter specifici. Dato un QueueItem, interroga il registry per i target e il
converter per options_schema(), e genera i controlli di conseguenza. Aggiungere
un formato non richiede di modificare questo file.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.job import ConversionJob
from ..core.options import BoolOption, ChoiceOption, IntOption, TextOption
from ..core.registry import resolve
from .queue_model import QueueItem
from .theme import badge_color
from .util import human_size


class OptionsPanel(QWidget):
    """Editor delle impostazioni per la riga selezionata."""

    changed = Signal()  # emesso quando target/opzioni cambiano (per aggiornare la tabella)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._item: QueueItem | None = None
        self._building = False
        # L'errore di validazione si STILA in rosso solo dopo che un campo ha
        # perso il focus (o alla pressione di Convert): così digitare "0:3" verso
        # "0:30" non lampeggia in rosso. La validazione live (badge/stima) resta
        # attiva; il gate in pipeline.run_job resta comunque il backstop.
        self._errors_armed = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._card = QFrame()
        self._card.setObjectName("Card")
        card_layout = QVBoxLayout(self._card)

        self._header = QLabel("Select a file to configure its conversion.")
        self._header.setObjectName("Subtle")
        self._header.setWordWrap(True)
        card_layout.addWidget(self._header)

        # Riga target + badge.
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Convert to:"))
        self._target_combo = QComboBox()
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        target_row.addWidget(self._target_combo, 1)
        self._badge = QLabel()
        self._badge.setStyleSheet('font-family: "Inter"; font-weight: 600;')
        target_row.addWidget(self._badge)
        card_layout.addLayout(target_row)

        self._reason = QLabel()
        self._reason.setObjectName("Subtle")
        self._reason.setWordWrap(True)
        card_layout.addWidget(self._reason)

        # Form dinamico delle opzioni.
        self._form_host = QWidget()
        self._form = QFormLayout(self._form_host)
        self._form.setContentsMargins(0, 8, 0, 8)
        card_layout.addWidget(self._form_host)

        # Messaggio di validazione (feedback immediato, prima di convertire).
        self._validation = QLabel()
        self._validation.setWordWrap(True)
        self._validation.setStyleSheet('color: #C42B1C; font-family: "Inter"; font-weight: 500;')
        self._validation.hide()
        card_layout.addWidget(self._validation)

        self._strip = QCheckBox("Strip metadata (EXIF / ID3 / tags) for privacy")
        self._strip.toggled.connect(self._on_strip_toggled)
        card_layout.addWidget(self._strip)

        self._output = QLabel()
        self._output.setObjectName("Subtle")
        self._output.setWordWrap(True)
        card_layout.addWidget(self._output)

        card_layout.addStretch(1)
        root.addWidget(self._card)
        self.set_item(None)

    # --- API pubblica ---

    def set_item(self, item: QueueItem | None) -> None:
        self._item = item
        enabled = item is not None
        self._target_combo.setEnabled(enabled)
        self._strip.setEnabled(enabled)
        if item is None:
            self._header.setText("Select a file to configure its conversion.")
            self._target_combo.clear()
            self._badge.clear()
            self._reason.clear()
            self._output.clear()
            self._validation.hide()
            self._clear_form()
            return

        self._building = True
        self._header.setText(
            f"<b>{item.src_path.name}</b> — detected as "
            f"{(item.detected.format_id or 'unknown').upper()}"
        )
        self._populate_targets(item)
        self._strip.setChecked(item.strip_metadata)
        self._building = False
        self._rebuild_for_target()

    # --- Costruzione target ---

    def _populate_targets(self, item: QueueItem) -> None:
        self._target_combo.clear()
        if not item.targets:
            self._target_combo.addItem("No conversions available", None)
            self._target_combo.setEnabled(False)
            return
        self._target_combo.setEnabled(True)
        select_index = 0
        for i, target in enumerate(item.targets):
            label = target.dst_format.upper()
            if not target.available:
                label += "  (unavailable)"
            self._target_combo.addItem(label, target.dst_format)
            model_item = self._target_combo.model().item(i)
            if not target.available:
                model_item.setEnabled(False)
                model_item.setToolTip(target.reason_unavailable)
            if item.dst_format == target.dst_format:
                select_index = i
        # Se il target salvato non è valido, scegli il primo disponibile.
        if item.dst_format is None:
            select_index = self._first_available(item)
        self._target_combo.setCurrentIndex(select_index)

    @staticmethod
    def _first_available(item: QueueItem) -> int:
        for i, t in enumerate(item.targets):
            if t.available:
                return i
        return 0

    # --- Slot ---

    def _on_target_changed(self) -> None:
        if self._building or self._item is None:
            return
        self._rebuild_for_target()
        self.changed.emit()

    def _on_strip_toggled(self, checked: bool) -> None:
        if self._item is not None:
            self._item.strip_metadata = checked

    def _on_option_changed(self) -> None:
        if not self._building:
            self._update_badge_and_estimate()
            self.changed.emit()

    # --- Rebuild dinamico ---

    def _rebuild_for_target(self) -> None:
        item = self._item
        if item is None:
            return
        # Nuovo target = campi nuovi: niente errori in rosso finché l'utente non
        # esce da un campo (o preme Convert).
        self._errors_armed = False
        dst = self._target_combo.currentData()
        item.dst_format = dst
        self._clear_form()
        if dst is None:
            self._update_badge_and_estimate()
            return

        conv = resolve(item.detected.format_id, dst)
        schema = conv.options_schema(item.detected.format_id, dst) if conv else []
        # Conserva i valori già impostati (da un preset o da una visita precedente)
        # e riempie SOLO le chiavi mancanti col default dello schema. Così tornare
        # su una riga non azzera le scelte fatte.
        prev = item.options or {}
        item.options = {opt.key: prev.get(opt.key, opt.default) for opt in schema}
        self._building = True
        for opt in schema:
            widget = self._make_widget(opt, item)
            self._form.addRow(opt.label, widget)
        self._building = False
        self._update_badge_and_estimate()

    def _make_widget(self, opt, item: QueueItem) -> QWidget:
        # Inizializza il widget dal valore corrente (preset/visita precedente),
        # non dal solo default: così i valori applicati restano visibili.
        current = item.options.get(opt.key, opt.default)
        item.options[opt.key] = current
        if isinstance(opt, BoolOption):
            w = QCheckBox()
            w.setChecked(bool(current))
            w.toggled.connect(lambda v, k=opt.key: self._set_opt(k, v))
            self._tip(w, opt)
            return w
        if isinstance(opt, TextOption):
            field = QLineEdit()
            field.setText(str(current))
            if opt.placeholder:
                field.setPlaceholderText(opt.placeholder)
            field.textChanged.connect(lambda v, k=opt.key: self._set_opt(k, v))
            # Il blur (o Invio) "arma" la stilatura dell'errore per questo item.
            field.editingFinished.connect(self._arm_validation)
            self._tip(field, opt)
            if not opt.help:
                return field
            # #5: testo d'aiuto PERSISTENTE sotto il campo (non solo tooltip): la
            # guida sul formato resta visibile senza dover passare col mouse.
            host = QWidget()
            lay = QVBoxLayout(host)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(2)
            lay.addWidget(field)
            helper = QLabel(opt.help)
            helper.setObjectName("Subtle")
            helper.setWordWrap(True)
            lay.addWidget(helper)
            return host
        if isinstance(opt, ChoiceOption):
            w = QComboBox()
            for value, label in opt.choices:
                w.addItem(label, value)
            values = [v for v, _ in opt.choices]
            w.setCurrentIndex(values.index(current) if current in values else 0)
            w.currentIndexChanged.connect(lambda _i, k=opt.key, c=w: self._set_opt(k, c.currentData()))
            self._tip(w, opt)
            return w
        if isinstance(opt, IntOption):
            if opt.slider:
                host = QWidget()
                lay = QHBoxLayout(host)
                lay.setContentsMargins(0, 0, 0, 0)
                slider = QSlider(Qt.Horizontal)
                slider.setRange(opt.minimum, opt.maximum)
                slider.setSingleStep(opt.step)
                slider.setValue(int(current))
                value_label = QLabel(f"{int(current)}{opt.suffix}")
                slider.valueChanged.connect(
                    lambda v, k=opt.key, lbl=value_label, s=opt.suffix: (
                        lbl.setText(f"{v}{s}"), self._set_opt(k, v))
                )
                lay.addWidget(slider, 1)
                lay.addWidget(value_label)
                self._tip(slider, opt)
                return host
            spin = QSpinBox()
            spin.setRange(opt.minimum, opt.maximum)
            spin.setSingleStep(opt.step)
            spin.setValue(int(current))
            spin.setSuffix(opt.suffix)
            spin.valueChanged.connect(lambda v, k=opt.key: self._set_opt(k, v))
            self._tip(spin, opt)
            return spin
        return QLabel(str(current))

    @staticmethod
    def _tip(widget: QWidget, opt) -> None:
        if opt.help:
            widget.setToolTip(opt.help)

    def _set_opt(self, key: str, value) -> None:
        if self._item is not None:
            self._item.options[key] = value
        self._on_option_changed()

    # --- Badge, motivo, stima ---

    def _update_badge_and_estimate(self) -> None:
        item = self._item
        if item is None or item.dst_format is None:
            self._badge.clear()
            self._reason.clear()
            self._output.clear()
            self._validation.hide()
            return

        conv = resolve(item.detected.format_id, item.dst_format)
        # Il verdetto (incluso il caso WebP lossless/lossy) è calcolato dal
        # converter a partire dalle opzioni correnti: la GUI non lo duplica.
        verdict = conv.verdict(item.detected.format_id, item.dst_format, item.options) if conv else None
        item.verdict = verdict

        if verdict:
            self._badge.setText(verdict.badge)
            self._badge.setStyleSheet(
                f'font-family: "Inter"; font-weight: 600; color: {badge_color(verdict.level)};')
            self._reason.setText(verdict.reason)

        # Stima dimensione di output (best-effort).
        est = None
        if conv:
            dummy = ConversionJob(
                src_path=item.src_path, src_format=item.detected.format_id,
                dst_format=item.dst_format, output_path=Path("dummy"),
                options=dict(item.options), strip_metadata=item.strip_metadata,
            )
            try:
                est = conv.estimate_size(dummy)
            except Exception:
                est = None
        item.est_size = est
        est_text = f"  ·  estimated output ~{human_size(est)}" if est else ""
        self._output.setText(f"Source size {human_size(item.src_size)}{est_text}")

        self._update_validation(conv, item)

    def _update_validation(self, conv, item) -> None:
        """Validazione strutturale (senza durata). Il messaggio rosso compare
        SOLO quando gli errori sono "armati" (un campo ha perso il focus, o si è
        premuto Convert): così non lampeggia mentre si digita. Il gate completo
        resta comunque in run_job."""
        msg = None
        if conv is not None:
            msg = conv.validate_quick(item.detected.format_id, item.dst_format, item.options)
        if msg and self._errors_armed:
            self._validation.setText(f"⚠ {msg}")
            self._validation.show()
        else:
            self._validation.clear()
            self._validation.hide()

    def _arm_validation(self) -> None:
        """Abilita la stilatura dell'errore (su blur del campo o pressione di
        Convert) e ri-renderizza il messaggio se serve."""
        self._errors_armed = True
        item = self._item
        if item is None or item.dst_format is None:
            return
        self._update_validation(resolve(item.detected.format_id, item.dst_format), item)

    def arm_validation(self) -> None:
        """Esposto a MainWindow: al click di Convert, mostra eventuali errori."""
        self._arm_validation()

    def _clear_form(self) -> None:
        while self._form.rowCount():
            self._form.removeRow(0)
