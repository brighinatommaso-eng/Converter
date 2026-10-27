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


# Vedi l'elenco in Analysis(excludes=...). Condiviso col selftest dell'exe
# (converter_app._UNUSED_OPTIONAL_MODULES), che verifica che non siano nel bundle.
UNUSED_OPTIONAL_MODULES = [
    "pygame", "sqlalchemy", "botocore", "boto3", "s3transfer", "psycopg2", "pydantic",
    "pydantic_core", "rich", "jinja2", "markupsafe", "fsspec", "anyio", "pygments", "bcrypt",
    "requests", "urllib3", "certifi", "pyglet", "networkx", "shapely", "rtree", "psutil",
    "pkg_resources", "setuptools",
]

binaries = []
# pandoc = motore markup PORTABLE (md/html/epub/docx/odt). Bundlato come ffmpeg in
# _internal/bin, dove deps._locate("pandoc") lo cerca per primo. GPL ma è un binario
# subprocess separato (mera aggregazione), stesso pattern di ffmpeg.
#
# I tool NON passano dall'Analysis: PyInstaller analizza le dipendenze di ogni
# eseguibile (anche se dichiarato in datas, lo riclassifica) e ne copia le librerie
# nella radice di _internal, dove l'exe in bin/ non le cerca (e con ffmpeg "shared"
# sarebbero copie doppie delle sue DLL). Vengono aggiunti dopo l'Analysis, come
# file da copiare tali e quali: bin/ resta una cartella autosufficiente.
# CONVERTER_BUNDLE_TOOLS=0: bundle SENZA ffmpeg/pandoc. Serve al pacchetto .deb,
# che li dichiara come dipendenze e usa quelli del sistema (aggiornati da apt).
_bundled_tools = []
for _tool in ("ffmpeg", "ffprobe", "pandoc"):
    if os.environ.get("CONVERTER_BUNDLE_TOOLS", "1") == "0":
        break
    _p = _real_tool(_tool)
    if _p:
        _bundled_tools.append(_p)
# ffmpeg "shared" (build ufficiale gyan.dev full-shared): ffmpeg.exe e ffprobe.exe
# sono piccoli e condividono le DLL avcodec/avformat/... accanto a loro. Una copia
# sola delle librerie invece di due exe statici da ~150 MB l'uno.
_ffmpeg_dir = os.path.dirname(_bundled_tools[0]) if _bundled_tools and \
    os.path.basename(_bundled_tools[0]).lower().startswith("ffmpeg") else None
_SHARED_FFMPEG_LIBS = ("avcodec", "avformat", "avutil", "avfilter", "avdevice",
                       "swscale", "swresample", "postproc")
_ffmpeg_libs = []
if _ffmpeg_dir and os.name == "nt":
    for _name in sorted(os.listdir(_ffmpeg_dir)):
        if _name.lower().endswith(".dll") and _name.lower().startswith(_SHARED_FFMPEG_LIBS):
            _ffmpeg_libs.append(os.path.join(_ffmpeg_dir, _name))

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
        # Dipendenze OPZIONALI di pandas/trimesh/fsspec presenti nel Python di build
        # ma mai importate da Converter (verificato: suite completa + --selftest con
        # questi moduli bloccati all'import). Senza escluderle PyInstaller le
        # raccoglie dalle importazioni opzionali e il bundle ingrassa.
        *UNUSED_OPTIONAL_MODULES,
    ],
    noarchive=False,
    optimize=0,
)

# --- Qt ridotto all'osso -----------------------------------------------------
# L'app usa solo QtCore/QtGui/QtWidgets (QtNetwork resta: arriva coi moduli base).
# I plugin "imageformats/qpdf" e "platforminputcontexts/virtualkeyboard" trascinano
# QtPdf, QtQuick, QtQml e la tastiera virtuale (~25-35 MB); le traduzioni .qm non
# servono (nessun QTranslator) e opengl32sw.dll è l'OpenGL software di Qt Quick,
# inutile a un'app a widget (che disegna in raster). Stesso elenco di
# converter_app._UNUSED_QT_MARKERS: il selftest dell'exe verifica che manchino.
UNUSED_QT_MARKERS = [
    "Qt6Quick", "Qt6Qml", "Qt6Pdf", "Qt6VirtualKeyboard", "qtvirtualkeyboard", "qpdf",
    "opengl32sw",
]


def _unused_qt(entry):
    name = os.path.basename(entry[0])
    return name.endswith(".qm") or any(marker in name for marker in UNUSED_QT_MARKERS)


_dropped = [e[0] for e in list(a.binaries) + list(a.datas) if _unused_qt(e)]
a.binaries = [e for e in a.binaries if not _unused_qt(e)]
a.datas = [e for e in a.datas if not _unused_qt(e)]
print(f"[converter.spec] Qt non usato tolto dal bundle: {len(_dropped)} file")

# Tool esterni -> _internal/bin, copiati senza analisi delle dipendenze (vedi sopra).
a.datas += [(f"bin/{os.path.basename(_p)}", _p, "DATA") for _p in _bundled_tools + _ffmpeg_libs]

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

# --- Testi di licenza accanto all'eseguibile ---------------------------------
# ffmpeg (GPL-3) e pandoc (GPL-2) vengono ridistribuiti nel bundle: il testo della
# loro licenza DEVE viaggiare con il binario. Prima THIRD_PARTY_NOTICES.md citava
# una cartella `licenses/` che nessuno copiava. Qui finiscono nella radice della
# release (dist/Converter/), accanto a Converter.exe, insieme a LICENSE e notices.
_dist_root = os.path.join(DISTPATH, "Converter")
_required_licenses = {"ffmpeg": "ffmpeg-GPLv3.txt", "ffprobe": "ffmpeg-GPLv3.txt",
                      "pandoc": "pandoc-GPLv2.txt"}
for _src in _bundled_tools:
    _tool = os.path.splitext(os.path.basename(_src))[0].lower()
    _needed = _required_licenses.get(_tool)
    if _needed and not os.path.isfile(os.path.join("licenses", _needed)):
        raise SystemExit(f"[converter.spec] {_tool} è nel bundle ma manca licenses/{_needed}: "
                         "non si distribuisce un binario GPL senza il testo della licenza.")
shutil.copytree("licenses", os.path.join(_dist_root, "licenses"), dirs_exist_ok=True)
for _doc in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
    shutil.copy2(_doc, os.path.join(_dist_root, _doc))
