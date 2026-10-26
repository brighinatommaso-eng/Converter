# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — build ONEDIR riproducibile di Converter.

Scelte:
  * onedir (NON onefile): onefile estrae in una temp a ogni avvio, rompe/rallenta
    la risoluzione dei percorsi di ffmpeg/LibreOffice. onedir è stabile e veloce.
  * ffmpeg/ffprobe inclusi in <bundle>/_internal/bin (dove deps._bundled_dir li
    cerca). Risolti via PATH al momento del build -> nessun percorso macchina
    hardcoded. Se assenti, il build prosegue ma senza bundling (warning).
  * LibreOffice NON incluso: rilevato a runtime; i documenti si disabilitano se
    manca (resolver esistente). Config/preset/cronologia restano in %APPDATA%.
  * assets (icona) in <bundle>/_internal/assets (dove _asset_path li cerca).

Build:  pyinstaller converter.spec --noconfirm --clean
"""
import os
import shutil

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


def _real_tool(name):
    """Percorso REALE del binario (risolve gli shim/symlink, es. WinGet Links)."""
    found = shutil.which(name)
    if not found:
        print(f"[converter.spec] WARNING: '{name}' non trovato su PATH: non incluso.")
        return None
    real = os.path.realpath(found)
    return real if os.path.exists(real) else found


binaries = []
# pandoc = motore markup PORTABLE (md/html/epub/docx/odt). Bundlato come ffmpeg in
# _internal/bin, dove deps._locate("pandoc") lo cerca per primo. GPL ma è un binario
# subprocess separato (mera aggregazione), stesso pattern di ffmpeg.
for _tool in ("ffmpeg", "ffprobe", "pandoc"):
    _p = _real_tool(_tool)
    if _p:
        binaries.append((_p, "bin"))  # -> _internal/bin

datas = [("converter/assets", "assets")]  # -> _internal/assets
# pdfminer.six porta risorse dati (cmap per la decodifica CID/CJK): senza queste,
# l'estrazione testo fallisce su certi PDF. collect_data_files le raccoglie.
datas += collect_data_files("pdfminer")
# trimesh legge risorse dati (trimesh/resources: schema glTF, template): i loader
# le aprono a runtime, quindi vanno raccolte come per pdfminer.
datas += collect_data_files("trimesh")
# python-docx apre i suoi template (default.docx e i default-*.xml) a runtime:
# senza questi dati il motore strutturale fallirebbe nell'exe.
datas += collect_data_files("docx")

hiddenimports = [
    "pillow_heif",      # HEIC (opzionale): incluso solo se installato nel build env
    "pillow_avif",      # AVIF (opzionale)
    "odf",              # odfpy (ODS), import name 'odf'
    "docx",             # python-docx: lettore/scrittore del motore strutturale
    "striprtf",         # rtf->txt PORTABLE (puro Python, MIT)
    "striprtf.striprtf",
]
# OCRmyPDF NON viene importato in-process: _ocr.py lo avvia come eseguibile
# esterno, se installato sul sistema. Importarlo qui non renderebbe disponibile
# l'eseguibile nel bundle e trascinerebbe anche img2pdf -> fitz/PyMuPDF (AGPL).
# pdfminer importa sottomoduli dinamicamente: raccoglierli tutti evita esclusioni
# silenziose di un submodule che PyInstaller non vede dall'analisi statica.
hiddenimports += collect_submodules("pdfminer")
# trimesh carica i suoi exchange loader (stl/gltf/ply/obj/threemf) in modo LAZY:
# l'analisi statica non li vede, così l'exe importerebbe trimesh ma fallirebbe ogni
# conversione 3D con ModuleNotFoundError. collect_submodules li include tutti.
# lxml (per il 3mf) arriva già da python-docx: non serve duplicarlo.
hiddenimports += collect_submodules("trimesh")

a = Analysis(
    # Entry con import ASSOLUTO: __main__.py usa import relativi che PyInstaller
    # non può risolvere (manca il contesto di package) -> Qt non verrebbe raccolto.
    ["converter_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "pytest",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore",
        # Pacchetti ML/scientifici pesanti presenti nel site-packages ma NON usati
        # da Converter: senza escluderli PyInstaller li raccoglie (torch da solo
        # ~4 GB). Converter usa solo PySide6/Pillow/pandas/numpy/openpyxl/odf/yaml.
        "torch", "torchvision", "torchaudio", "transformers", "bitsandbytes",
        "accelerate", "safetensors", "datasets", "sklearn", "scipy", "pyarrow",
        "sympy", "numba", "tensorflow", "matplotlib", "IPython", "notebook",
        # AGPL/estranei al motore: PDF solo con pdfminer.six. PyMuPDF (fitz),
        # pdf2docx e img2pdf non devono finire nel bundle nemmeno come dipendenze
        # opzionali di un altro pacchetto; OCRmyPDF/pikepdf restano esterni.
        "fitz", "pymupdf", "pdf2docx", "img2pdf", "ocrmypdf", "pikepdf",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Converter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                 # app GUI: niente console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="converter/assets/converter.ico",
    version="version_info.txt",
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Converter",
)
