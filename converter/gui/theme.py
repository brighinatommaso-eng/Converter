"""Temi chiaro/scuro via QSS, con persistenza della preferenza.

I colori dei badge (lossless/lossy/unsupported/failed) e l'intero stylesheet
sono derivati da una palette unica per tema, così che cambiare un colore qui si
propaghi a tutta la GUI senza toccare i singoli widget.

Palette LIGHT: "Fluent chiaro" — grigi di sistema Windows 11, accento blu #0067C0.
Palette DARK:  coerente con la chiara, accento verde-acqua per separarsi dal
               blu di selezione del sistema.

I selettori QSS usano gli objectName REALI del codice:
    Primary  -> bottoni accentati (Convert checked, Mode attivo, Continue dialog)
    Subtle   -> testo secondario (header, reason, output, status, helper)
    Card     -> pannello opzioni a destra
    Title    -> titolo nei dialog

API pubblica invariata:
    apply_theme(app), toggle_theme(app=None), current_theme(), badge_color(level)
"""
from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QWidget

from ..core.paths import config_dir

# --------------------------------------------------------------------------- #
# Palette
# --------------------------------------------------------------------------- #
_LIGHT = {
    "window":        "#F3F3F3",
    "surface":       "#FBFBFB",
    "surface_alt":   "#FFFFFF",
    "header":        "#F0F0F0",
    "border":        "#D9D9D9",
    "border_strong": "#C4C4C4",
    "row_line":      "#EDEDED",
    "text":          "#1A1A1A",
    "text_muted":    "#6B6B6B",
    "text_disabled": "#A6A6A6",
    "accent":        "#0067C0",
    "accent_hover":  "#1A75C7",
    "accent_press":  "#005BA8",
    "accent_text":   "#FFFFFF",
    "selection":     "#CCE4F7",
    "track":         "#E0E0E0",
    "ok_fg":   "#107C41", "ok_bg":   "#E3F3E9",
    "loss_fg": "#9A5700", "loss_bg": "#FBF0DF",
    "uns_fg":  "#A02C2C", "uns_bg":  "#FBEAEA",
    "fail_fg": "#C42B1C", "fail_bg": "#FDECEA",
    "accent_bg": "#E5F0FB",
}

_DARK = {
    "window":        "#1E1E1E",
    "surface":       "#252526",
    "surface_alt":   "#2D2D2E",
    "header":        "#2D2D2E",
    "border":        "#3C3C3C",
    "border_strong": "#4A4A4A",
    "row_line":      "#333333",
    "text":          "#E4E4E4",
    "text_muted":    "#A0A0A0",
    "text_disabled": "#6A6A6A",
    "accent":        "#2A9D8F",
    "accent_hover":  "#34B0A1",
    "accent_press":  "#23867A",
    "accent_text":   "#062B27",
    "selection":     "#163A35",
    "track":         "#3C3C3C",
    "ok_fg":   "#4EC9B0", "ok_bg":   "#16332E",
    "loss_fg": "#DCB067", "loss_bg": "#332914",
    "uns_fg":  "#E07A7A", "uns_bg":  "#3A1E1E",
    "fail_fg": "#F08A7A", "fail_bg": "#3A1E1E",
    "accent_bg": "#16283B",
}

_PALETTES = {"light": _LIGHT, "dark": _DARK}


# --------------------------------------------------------------------------- #
# Persistenza
# --------------------------------------------------------------------------- #
def _settings() -> QSettings:
    return QSettings("Converter", "Converter")


def current_theme() -> str:
    """Tema corrente persistito ('light' di default)."""
    val = _settings().value("theme", "light")
    return val if val in _PALETTES else "light"


# --------------------------------------------------------------------------- #
# Badge
# --------------------------------------------------------------------------- #
def badge_color(level: str) -> str:
    """Colore (foreground) del badge per il livello dato, adattato al tema.

    level: 'lossless' | 'lossy' | 'unsupported' | 'failed' (case-insensitive).
    Usato da queue_model.py e options_panel.py.
    """
    p = _PALETTES[current_theme()]
    key = (level or "").strip().lower()
    return {
        "lossless":    p["ok_fg"],
        "lossy":       p["loss_fg"],
        "unsupported": p["uns_fg"],
        "failed":      p["fail_fg"],
    }.get(key, p["text_muted"])


def badge_bg(level: str) -> str:
    """Sfondo (pill) del badge per il livello dato. Companion di badge_color,
    usato dal delegate della tabella per disegnare le pillole colorate."""
    p = _PALETTES[current_theme()]
    key = (level or "").strip().lower()
    return {
        "lossless":    p["ok_bg"],
        "lossy":       p["loss_bg"],
        "unsupported": p["uns_bg"],
        "failed":      p["fail_bg"],
        "accent":      p["accent_bg"],
    }.get(key, p["header"])


# --------------------------------------------------------------------------- #
# Glifo di spunta (per le checkbox)
# --------------------------------------------------------------------------- #
def _check_icon(color: str) -> str:
    """Scrive (se assente) un piccolo SVG con la spunta nel colore dato e ne
    ritorna il path in forma POSIX per la url() del QSS.

    Disegnare la spunta come immagine — invece di lasciare l'indicatore come
    blocco pieno d'accento — dà il look Fluent: segno di spunta su sfondo chiaro,
    non un quadrato blu saturo. Il file vive nella config dir (scrivibile anche
    da frozen); un file per colore così light e dark non si pestano i piedi.
    """
    name = "check_" + color.lstrip("#").lower() + ".svg"
    path = config_dir() / name
    if not path.exists():
        # viewBox 16x16, spunta inset (~3..13) così resta margine dentro i 16px
        # dell'indicatore; stroke spesso e arrotondato per leggerlo a 16px.
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" '
            'viewBox="0 0 16 16">'
            f'<path d="M3.5 8.5 L6.5 11.5 L12.5 4.5" fill="none" stroke="{color}" '
            'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
            "</svg>"
        )
        path.write_text(svg, encoding="utf-8")
    return path.as_posix()


def _arrow_icon(color: str) -> str:
    """Scrive (se assente) un piccolo SVG con una freccetta verso il basso nel
    colore dato e ne ritorna il path per la url() del QSS.

    Stesso schema di _check_icon: senza questa immagine QComboBox::drop-down
    non disegna alcun glifo (border:none + nessuna image = box vuoto), e il
    controllo non si legge più come cliccabile.
    """
    name = "arrow_" + color.lstrip("#").lower() + ".svg"
    path = config_dir() / name
    if not path.exists():
        # viewBox 10x10, chevron semplice, stroke arrotondato coerente col check.
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
            'viewBox="0 0 10 10">'
            f'<path d="M2 3.5 L5 6.5 L8 3.5" fill="none" stroke="{color}" '
            'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>'
            "</svg>"
        )
        path.write_text(svg, encoding="utf-8")
    return path.as_posix()


# --------------------------------------------------------------------------- #
# Stylesheet
# --------------------------------------------------------------------------- #
def _stylesheet(p: dict) -> str:
    check = _check_icon(p["accent"])
    arrow = _arrow_icon(p["text_muted"])
    return f"""
* {{
    font-family: "Inter", "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif;
    font-weight: 400;
    font-size: 13px;
    color: {p['text']};
}}
QWidget {{ background: {p['window']}; }}
QMainWindow, QDialog {{ background: {p['window']}; }}

QLabel#Subtle {{ color: {p['text_muted']}; background: transparent; }}
QLabel#Title  {{ font-family: "Inter"; font-weight: 600; font-size: 17px; background: transparent; }}
QLabel {{ background: transparent; }}

QMenuBar {{ background: {p['surface']}; border-bottom: 1px solid {p['border']}; }}
QMenuBar::item {{ padding: 6px 12px; background: transparent; }}
QMenuBar::item:selected {{ background: {p['header']}; border-radius: 4px; }}
QMenu {{ background: {p['surface']}; border: 1px solid {p['border']}; }}
QMenu::item {{ padding: 6px 24px; }}
QMenu::item:selected {{ background: {p['selection']}; }}

QPushButton {{
    background: {p['surface_alt']};
    border: 1px solid {p['border_strong']};
    border-radius: 6px;
    padding: 6px 13px;
    min-height: 18px;
}}
QPushButton:hover {{ background: {p['header']}; }}
QPushButton:pressed {{ background: {p['track']}; }}
QPushButton:disabled {{ color: {p['text_disabled']}; background: {p['surface']}; border-color: {p['border']}; }}

QPushButton#Primary {{
    background: {p['accent']};
    color: {p['accent_text']};
    border: 1px solid {p['accent']};
    /* Inter è registrata come famiglia unica con facce statiche (Regular 400,
       Medium 500, SemiBold 600): chiediamo il peso 600 a "Inter" -> match esatto
       con la faccia SemiBold (glifi veri), invece di riferire un nome-famiglia
       separato, che su QPushButton (testo disegnato dallo stile) rendeva male. */
    font-family: "Inter";
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {p['accent_hover']}; border-color: {p['accent_hover']}; }}
QPushButton#Primary:pressed {{ background: {p['accent_press']}; border-color: {p['accent_press']}; }}
QPushButton#Primary:disabled {{ background: {p['border_strong']}; color: {p['surface']}; border-color: {p['border_strong']}; }}

QLineEdit, QComboBox, QSpinBox {{
    background: {p['surface_alt']};
    border: 1px solid {p['border_strong']};
    border-radius: 6px;
    padding: 5px 9px;
    selection-background-color: {p['selection']};
    selection-color: {p['text']};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border: 1px solid {p['accent']}; }}
QLineEdit:disabled, QComboBox:disabled {{ color: {p['text_disabled']}; background: {p['surface']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({arrow}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {p['surface']};
    border: 1px solid {p['border']};
    selection-background-color: {p['selection']};
    selection-color: {p['text']};
    outline: none;
}}
QSpinBox::up-button, QSpinBox::down-button {{ width: 16px; }}

QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {p['border_strong']};
    background: {p['surface_alt']};
}}
QCheckBox::indicator:hover {{ border-color: {p['accent']}; }}
QCheckBox::indicator:checked {{
    background: {p['accent_bg']};
    border: 1px solid {p['accent']};
    image: url("{check}");
}}
QCheckBox::indicator:checked:hover {{ background: {p['selection']}; }}

QTableView {{
    background: {p['surface_alt']};
    alternate-background-color: {p['surface']};
    border: 1px solid {p['border']};
    border-radius: 8px;
    gridline-color: {p['row_line']};
    selection-background-color: {p['selection']};
    selection-color: {p['text']};
    outline: none;
}}
QTableView::item {{ padding: 4px 8px; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {p['header']};
    color: {p['text_muted']};
    padding: 6px 10px;
    border: none;
    border-bottom: 1px solid {p['border']};
    font-family: "Inter";
    font-weight: 500;
}}
QTableCornerButton::section {{ background: {p['header']}; border: none; }}

QFrame#Card {{
    background: {p['surface_alt']};
    border: 1px solid {p['border']};
    border-radius: 8px;
}}
QFrame#Card QWidget {{ background: transparent; }}

QSlider {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 4px; background: {p['track']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {p['accent']}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: {p['track']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {p['accent']}; width: 14px; height: 14px;
    margin: -6px 0; border-radius: 7px;
}}
QSlider::handle:horizontal:hover {{ background: {p['accent_hover']}; }}

QProgressBar {{
    background: {p['track']};
    border: none; border-radius: 4px;
    height: 8px; text-align: center;
    color: {p['text_muted']}; font-size: 11px;
}}
QProgressBar::chunk {{ background: {p['accent']}; border-radius: 4px; }}

QStatusBar {{ background: {p['surface']}; border-top: 1px solid {p['border']}; }}
QStatusBar QLabel {{ color: {p['text_muted']}; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {p['border_strong']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['text_muted']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {p['border_strong']}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
"""


def apply_theme(app: QApplication, theme: str | None = None) -> None:
    """Applica il tema all'intera applicazione e lo persiste.

    Se `theme` è None usa quello corrente persistito.
    """
    name = theme or current_theme()
    if name not in _PALETTES:
        name = "light"
    _settings().setValue("theme", name)
    app.setStyle("Fusion")  # base coerente cross-version; il QSS fa il resto

    # Hinting: a 13px il font custom, con l'hinting pieno di default, snappa gli
    # stem alla griglia in modo irregolare -> testo "ballerino" (baseline e spessori
    # disuguali), evidentissimo sul bianco dei bottoni accentati. PreferNoHinting
    # rende i glifi lisci e allineati. Si imposta sul font dell'app perché la
    # preferenza si propaga ai widget anche quando il QSS ne fissa famiglia/peso.
    font = app.font()
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)

    app.setStyleSheet(_stylesheet(_PALETTES[name]))


def toggle_theme(app: QApplication | None = None) -> str:
    """Alterna light<->dark, applica e ritorna il nuovo tema.

    `app` è accettato per compatibilità con la chiamata in main_window.py:
    toggle_theme(QApplication.instance()). Se None, usa l'istanza corrente.
    """
    new = "dark" if current_theme() == "light" else "light"
    if app is None:
        app = QApplication.instance()
    if app is not None:
        apply_theme(app, new)
    return new


def _colorref(hex_color: str) -> int:
    """#RRGGBB -> COLORREF (0x00BBGGRR), il formato che si aspetta DWM."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_titlebar(widget: QWidget, theme: str | None = None) -> None:
    """Windows 11: tinge la barra del titolo della finestra per intonarla al tema.

    Senza questo la caption resta nera e stona con il grigio chiaro Fluent. Usa
    l'API DWM (non c'è modo di stilizzare la barra via QSS): CAPTION_COLOR per lo
    sfondo, USE_IMMERSIVE_DARK_MODE perché testo e pulsanti (min/max/chiudi) siano
    chiari su barra scura e scuri su barra chiara.

    No-op fuori da Windows o su build troppo vecchie (la DwmSetWindowAttribute
    fallisce silenziosamente: la barra resta quella di default, nessun crash).
    """
    if sys.platform != "win32":
        return
    name = theme or current_theme()
    p = _PALETTES.get(name, _LIGHT)
    try:
        hwnd = int(widget.winId())  # forza la creazione dell'handle nativo se serve
        dwm = ctypes.windll.dwmapi
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_CAPTION_COLOR = 35
        dark = ctypes.c_int(1 if name == "dark" else 0)
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                                  ctypes.byref(dark), ctypes.sizeof(dark))
        caption = ctypes.c_int(_colorref(p["window"]))
        dwm.DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR,
                                  ctypes.byref(caption), ctypes.sizeof(caption))
    except Exception:  # pragma: no cover - best-effort, mai bloccante
        pass
