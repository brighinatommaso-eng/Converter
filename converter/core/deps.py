"""Rilevamento delle dipendenze esterne e degrado controllato.

L'app non deve crashare se manca ffmpeg/LibreOffice: deve disabilitare
solo le conversioni interessate e dire all'utente come installare ciò che serve.
I percorsi NON sono assunti solo da PATH (così l'app resta impacchettabile):
si cerca anche in posizioni comuni e in una cartella "bin" accanto all'eseguibile.
"""

from __future__ import annotations

import importlib.util
import os
import platform
import re
import shutil
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path


@dataclass(frozen=True)
class DependencyStatus:
    ok: bool
    reason: str = ""
    install_hint: str = ""


def clean_subprocess_env() -> dict[str, str]:
    """Ambiente per i tool esterni (LibreOffice, ffmpeg, OCRmyPDF...).

    Un eseguibile congelato con PyInstaller inietta nell'ambiente variabili che
    riferiscono al SUO runtime Python e alla cartella del bundle. Se le ereditasse,
    un tool che porta il PROPRIO Python (LibreOffice) caricherebbe le librerie/DLL
    sbagliate e fallirebbe (es. 'no export filter for docx', 'Could not find
    platform independent libraries'). Qui si parte dall'ambiente corrente e si
    rimuovono SOLO le variabili pericolose, lasciando intatto il resto.
    """
    env = dict(os.environ)

    # Variabili che farebbero usare al figlio il nostro interprete/percorsi Python.
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONEXECUTABLE",
                "PYTHONNOUSERSITE", "PYTHONDONTWRITEBYTECODE"):
        env.pop(key, None)

    # Variabili iniettate dal bootloader PyInstaller.
    for key in list(env):
        if key.startswith("_PYI") or key in ("_MEIPASS", "_MEIPASS2"):
            env.pop(key, None)

    # Path delle librerie: PyInstaller antepone la propria cartella e salva
    # l'originale come *_ORIG. Per il figlio si ripristina l'originale (o si
    # rimuove la variabile iniettata se non c'era un originale).
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "LD_PRELOAD"):
            orig = env.pop(var + "_ORIG", None)
            if orig is not None:
                env[var] = orig
            else:
                env.pop(var, None)
        # Togli dal PATH eventuali voci che puntano dentro il bundle: eviterebbero
        # che il figlio carichi le NOSTRE DLL (es. python3xx.dll) invece delle sue.
        if meipass and env.get("PATH"):
            sep = os.pathsep
            kept = [p for p in env["PATH"].split(sep)
                    if p and os.path.normcase(os.path.abspath(p)).startswith(
                        os.path.normcase(os.path.abspath(meipass))) is False]
            env["PATH"] = sep.join(kept)

    return env


def _bundled_dir() -> Path:
    """Cartella accanto all'eseguibile dove un bundle potrebbe mettere i binari."""
    base = Path(getattr(sys, "_MEIPASS", Path(sys.argv[0]).resolve().parent))
    return base / "bin"


def _locate(name: str, extra: list[Path] | None = None) -> str | None:
    exe = name + (".exe" if platform.system() == "Windows" else "")
    candidate = _bundled_dir() / exe
    if candidate.exists():
        return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    for d in extra or []:
        if (d / exe).exists():
            return str(d / exe)
    return None


def _windows_common() -> list[Path]:
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]
    return [Path(r) for r in roots if r]


@cache
def find_ffmpeg() -> str | None:
    return _locate("ffmpeg")


@cache
def find_ffprobe() -> str | None:
    return _locate("ffprobe")


@cache
def find_soffice() -> str | None:
    extra: list[Path] = []
    system = platform.system()
    if system == "Windows":
        extra += [r / "LibreOffice" / "program" for r in _windows_common()]
    elif system == "Darwin":
        # Il binario dentro il .app non è su PATH (README: l'app gira da sorgente
        # anche su macOS). _windows_common() NON va usata qui: senza la guardia
        # restituirebbe i default Windows anche su macOS.
        extra += [Path("/Applications/LibreOffice.app/Contents/MacOS"),
                  Path.home() / "Applications/LibreOffice.app/Contents/MacOS"]
    return _locate("soffice", extra)


@cache
def find_tesseract() -> str | None:
    extra = [r / "Tesseract-OCR" for r in _windows_common()]
    return _locate("tesseract", extra)


def _gs_version_key(d: Path) -> tuple[int, ...]:
    """Chiave d'ordinamento numerica per una cartella 'gs<versione>' (es. gs10.02
    → (10, 2)), così 10.x batte 9.x invece di perdere col confronto stringa."""
    return tuple(int(x) for x in re.findall(r"\d+", d.name)) or (0,)


@cache
def find_ghostscript() -> str | None:
    # Windows: l'eseguibile console è gswin64c e l'installer NON lo mette su
    # PATH: si cerca in C:\Program Files\gs\gs<versione>\bin (più recente prima).
    # NOTA: OCRmyPDF trova Ghostscript da sé via registro di Windows (verificato
    # empiricamente con env pulito); questo finder serve al GATE degradante,
    # per dire onestamente all'utente cosa manca.
    if platform.system() == "Windows":
        extra: list[Path] = []
        for root in _windows_common():
            gs_root = root / "gs"
            if gs_root.is_dir():
                # Ordine NUMERICO per versione (non lessicografico): altrimenti
                # "gs9.56" > "gs10.02" come stringhe e vincerebbe la più vecchia.
                versions = sorted((d for d in gs_root.iterdir() if d.is_dir()),
                                  key=_gs_version_key, reverse=True)
                extra += [d / "bin" for d in versions]
        return _locate("gswin64c", extra)
    return _locate("gs")


@cache
def find_ocrmypdf() -> str | None:
    # Console script pip (Scripts/ su PATH): si lancia come subprocess, con lo
    # stesso rigore degli altri tool esterni (timeout, kill, env pulito).
    return _locate("ocrmypdf")


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _hint(win: str, mac: str, linux: str) -> str:
    system = platform.system()
    if system == "Windows":
        return win
    if system == "Darwin":
        return mac
    return linux


def ffmpeg_install_hint() -> str:
    return _hint(
        win="winget install Gyan.FFmpeg  —  or download from ffmpeg.org and add it to PATH",
        mac="brew install ffmpeg",
        linux="sudo apt install ffmpeg   (Debian/Ubuntu)  ·  sudo dnf install ffmpeg (Fedora)",
    )


def libreoffice_install_hint() -> str:
    return _hint(
        win="winget install TheDocumentFoundation.LibreOffice",
        mac="brew install --cask libreoffice",
        linux="sudo apt install libreoffice",
    )


def tesseract_install_hint() -> str:
    return _hint(
        win="winget install UB-Mannheim.TesseractOCR",
        mac="brew install tesseract",
        linux="sudo apt install tesseract-ocr",
    )


def ghostscript_install_hint() -> str:
    return _hint(
        win="winget install ArtifexSoftware.GhostScript",
        mac="brew install ghostscript",
        linux="sudo apt install ghostscript",
    )


def ffmpeg_status() -> DependencyStatus:
    if find_ffmpeg():
        return DependencyStatus(True)
    return DependencyStatus(False, "FFmpeg was not found on this system.", ffmpeg_install_hint())


def report() -> dict[str, DependencyStatus]:
    """Riepilogo usato dal dialog di avvio. Le voci opzionali immagine sono
    valutate dentro converters/image.py per evitare import circolari."""
    items: dict[str, DependencyStatus] = {}
    items["FFmpeg (audio/video)"] = ffmpeg_status()

    if find_soffice():
        items["LibreOffice (documents)"] = DependencyStatus(True)
    else:
        items["LibreOffice (documents)"] = DependencyStatus(
            False, "LibreOffice was not found.", libreoffice_install_hint()
        )

    if find_tesseract():
        items["Tesseract (OCR)"] = DependencyStatus(True)
    else:
        items["Tesseract (OCR)"] = DependencyStatus(
            False, "Tesseract was not found.", tesseract_install_hint()
        )

    if find_ghostscript():
        items["Ghostscript (OCR)"] = DependencyStatus(True)
    else:
        items["Ghostscript (OCR)"] = DependencyStatus(
            False, "Ghostscript was not found.", ghostscript_install_hint()
        )

    if find_ocrmypdf():
        items["OCRmyPDF (OCR)"] = DependencyStatus(True)
    else:
        items["OCRmyPDF (OCR)"] = DependencyStatus(
            False, "OCRmyPDF was not found.", "pip install ocrmypdf"
        )

    for label, mod, pip_name in [
        ("HEIC support (pillow-heif)", "pillow_heif", "pillow-heif"),
        ("AVIF support (pillow-avif)", "pillow_avif", "pillow-avif-plugin"),
    ]:
        if has_module(mod):
            items[label] = DependencyStatus(True)
        else:
            items[label] = DependencyStatus(
                False, f"Optional package '{pip_name}' is not installed.",
                f"pip install {pip_name}",
            )
    return items
