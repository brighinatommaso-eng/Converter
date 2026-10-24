# Converter — Project Overview & Work Log

A complete picture of the app: what it is, how it's built, the principles and conventions,
the work done so far, the bugs solved, and my (the owner's) working preferences. Written so a
future session can pick up with full context without re-deriving everything.

- **App:** Converter `0.1.1` — *"Convert files locally — honest about quality."*
- **Stack:** Python 3.13 + PySide6 (Qt6), offline desktop app, Windows-first.
- **Last updated:** 2026-07-04.
- **Note on authorship:** §§1–13 document how the application is built (reference, derived
  from the codebase). §14 onward is the work done in recent sessions (GUI redesign
  integration + bug fixes, the docx/odt→html routing change, the PDF import converter with
  its OCR branch — §21, the review-pass fixes — §22, the same-format data fix + minor GUI
  fixes — §23, and the 3D-model converter — §24), which is the part I directly worked on.

---

## 1. What Converter is

An offline desktop app that converts **images, audio, video, data, documents, and 3D models**
between formats — 100% locally, never touching the network. Its guiding rule: **never lie about
quality.** Every conversion produces a verdict shown as a badge:

- 🟢 **LOSSLESS** — no data lost (PNG→BMP, WAV→FLAC, CSV→TSV).
- 🟠 **LOSSY** — some data lost; the app explains *why* before converting and uses
  maximum-quality defaults (PNG→JPG, WAV→MP3).
- ⚪ **UNAVAILABLE / UNSUPPORTED** — needs an external tool/codec that isn't installed
  (greyed out, with install instructions). Impossible pairs (MP4→XLSX) aren't offered at all.

This honesty principle drives the whole verdict/availability system — it's not a cosmetic
badge, it's the product's reason to exist.

---

## 2. Architecture

Three layers with a strict dependency direction (`gui → core ← converters`):

- **`core/`** — the framework. No GUI, no I/O backends.
  - `pipeline.run_job()` is the **single execution path** for every conversion (GUI and CLI
    both go through it). It resolves the converter, checks availability, converts — and
    **never raises**; failures come back as `ConversionResult(ok=False)`.
  - Converters self-register via the `@register` decorator (`core/registry.py`). The GUI only
    ever talks to the registry, never to converter classes by name. Adding a format = add or
    extend a converter, with **zero UI changes**.
  - `Converter.verdict()`, `format_available()`, and `pair_available()` (src→dst) drive what
    the UI offers vs. greys out.
- **`converters/`** — one module per category, each `@register`-ed. Contains the only I/O
  backends (Pillow, ffmpeg, pandas, LibreOffice, pandoc, pdfminer, striprtf).
- **`gui/`** — PySide6 UI. Contains **zero** conversion logic.
- **`cli/`** — command-line front-end (Phase 3 stub; skeleton documents the intended shape).

Entry points: `converter_app.py` (absolute import — used by PyInstaller) and
`python -m converter` (via `converter/__main__.py`, relative imports — works from source).

God nodes (most-connected core abstractions): `ConversionJob`, `MainWindow`, `resolve()`,
`QualityVerdict`, `OptionsPanel`, `detect()`, `QueueModel`, `Converter`.

---

## 3. Core modules (`converter/core/`)

| Module | Responsibility |
|--------|----------------|
| `base.py` | `Converter` interface: `verdict(src,dst,options)`, `options_schema(src,dst)`, `format_available`, `pair_available`, `convert`. |
| `registry.py` | Converter registry; `@register`, `resolve(src,dst)`, `targets_for(src)`, `TargetOption`. |
| `pipeline.py` | `run_job()` — the single, never-raising execution path shared by GUI and CLI. |
| `job.py` | Data types crossing the GUI↔core boundary: `ConversionJob`, `ConversionResult`, `ProgressEvent`, `CancelToken`. |
| `quality.py` | `QualityVerdict` (level: lossless/lossy/unsupported + human reason + badge). |
| `detection.py` | Content-based format detection (magic bytes / structure), not by extension. `detect()` → `DetectedFormat` (format_id, method, confidence). |
| `formats.py` | Canonical table of supported formats. |
| `options.py` | Declarative options schema: `Option`, `IntOption`, `ChoiceOption`, `BoolOption`, `TextOption`. The GUI builds controls from this. |
| `engine.py` | **Engine mode** FULL vs PORTABLE (see §6). |
| `deps.py` | Runtime detection of external tools + controlled degradation. `find_ffmpeg/find_ffprobe/find_soffice/find_pandoc/find_tesseract/find_ghostscript/find_ocrmypdf`, `clean_subprocess_env()`. |
| `paths.py` | User config dir (`%APPDATA%\Converter`), `config_dir/presets_file/history_file/libreoffice_profile_dir`, `atomic_write_json`. |
| `presets.py` | Named conversion configs persisted to disk: `Preset`, `PresetStore`, `reconcile_options`. |
| `history.py` | Completed-conversion history persisted to disk: `HistoryEntry`, `HistoryStore` (enables re-run). |
| `naming.py` | Output path construction + collision handling (`build_output_path`). |

---

## 4. Converters (`converter/converters/`)

| Module | Backend | Covers | Verdict logic (summary) |
|--------|---------|--------|--------------------------|
| `image.py` | Pillow (+ optional HEIC/AVIF plugins) | PNG/JPG/BMP/GIF/TIFF/WEBP/HEIC/AVIF | lossless to lossless containers; lossy to JPG/etc.; metadata strip option (EXIF/GPS). |
| `audio.py` | ffmpeg | WAV/FLAC/MP3/AAC/OGG/… | lossless to FLAC/WAV; lossy to MP3/AAC; sample-rate/channel options validated. |
| `video.py` | ffmpeg | MP4/MKV/MOV/… incl. **video→GIF** | re-encode is lossy; advanced options (fps, duration) validated by a pure validator. |
| `data.py` | pandas (+ openpyxl/odfpy/PyYAML/defusedxml) | csv/tsv/json/xlsx/ods/xml/yaml | row-faithful → lossless; XML parsed safely (defusedxml blocks billion-laughs/XXE). |
| `document.py` | LibreOffice headless | Office docs (Writer/Calc/…) | FULL mode only; faithful Office conversions; greys out without LibreOffice. |
| `markup.py` | pandoc (bundled binary) | markup/document interchange | pandoc-driven; `_OFFICE_OVERRIDE_OUT = {md, txt}` — docx/odt→**html** is NOT claimed here anymore (see §19), so it routes to LibreOffice. |
| `pdf.py` | pdfminer.six (pure-Python, MIT) | pdf→txt / pdf→html | works in Portable (no external tool). |
| `pdf_import.py` | pdf2docx (+ LibreOffice for odt/rtf; OCRmyPDF for scans) | pdf→docx/odt/rtf | lossy for all three; Full-only; priority 10; scanned PDFs go through OCR when the chain is installed, otherwise `validate()` returns an install message (see §21). |
| `rtf.py` | striprtf (pure-Python, MIT) | rtf→txt | works in Portable. |
| `mesh3d.py` | trimesh (pure-Python, MIT; numpy) | obj/stl/ply/glb/gltf/3mf → obj/stl/ply/glb/gltf | in-process (Portable-friendly); 3mf input-only, gated on optional lxml; glb↔gltf lossless, →stl/obj lossy, multi-object→single-mesh downgraded to lossy at runtime (see §24). |
| `_ffmpeg.py` | — | shared ffmpeg helpers (progress parse, probe duration, cancellation) | used by audio + video. |
| `_libreoffice.py` | — | hardened headless LibreOffice runner (serialized, 60s timeout, profile rebuild on crash) | used by document. |

---

## 5. Detection, options, and the verdict flow

1. File added → `detect()` reads **content** (magic bytes / structure) → `DetectedFormat`.
2. `targets_for(format_id)` → the valid destination formats, each a `TargetOption` carrying
   its `verdict` + `available` + `reason`. Impossible pairs never appear.
3. The options panel reads `converter.options_schema(src,dst)` and renders controls.
4. Live verdict: `resolve(src,dst).verdict(src,dst,options)` updates the badge as options
   change. `pipeline.run_job` is the backstop that refuses a lossy/unavailable pair.

---

## 6. Engine modes — FULL vs PORTABLE (key concept)

`core/engine.py` exposes a session mode that the user picks on first launch (remembered):

- **FULL** — best quality, including faithful Office documents. Requires LibreOffice. If a
  required tool is missing, a blocking dialog offers to install it (winget) or switch to
  Portable.
- **PORTABLE** — works anywhere, no installs; Office-to-Office documents are limited (uses the
  pure-Python converters: pdf/rtf and the pandas/Pillow/ffmpeg paths).

The segmented Mode control in the toolbar switches mode live (no restart) and recomputes the
queue — a target that was greyed out in Portable becomes available in Full and vice-versa.
API: `get_mode/set_mode/load_persisted/persist/refresh_deps/full_mode_missing`.

---

## 7. External tools & dependency hygiene

- External tools are **detected at runtime, never assumed on PATH**:
  `deps.find_ffmpeg/find_ffprobe/find_soffice` also search standard install dirs and a bundled
  `bin/`. ffmpeg/ffprobe/pandoc are bundled into the frozen app's `_internal/bin`.
- **Subprocess gotcha (critical):** spawn **every** external tool (soffice, ffmpeg, ffprobe)
  with `deps.clean_subprocess_env()`. A PyInstaller-frozen exe injects bundle dirs into
  `PATH`; without cleaning, LibreOffice's own Python loads our `python313.dll` and export
  filters fail ("no export filter for docx"). `_libreoffice.py` and `_ffmpeg.py` already do
  this — don't bypass it.

---

## 8. Configuration & data locations

Config, presets, and history always live in `%APPDATA%\Converter` (never the bundle):
`settings.json` (incl. `engine_mode`, `last_input_dir`), `presets.json`, `history.json`,
`lo_profile/` (dedicated LibreOffice profile), and the generated theme `check_*.svg` glyphs.
Theme preference is stored via `QSettings("Converter","Converter")`.

---

## 9. Testing

- Run: `python -m pytest -q` (config in `pyproject.toml`, `testpaths=["tests"]`).
- **Current status: 246 collected, 246 passing** (measured 2026-10-24 in a dev
  environment with ffmpeg, LibreOffice, pandoc, pdf2docx, trimesh, and the OCR chain
  present; 0 skipped in that environment).
- GUI tests set `QT_QPA_PLATFORM=offscreen` (keep that for new GUI tests, or they need a
  display). GUI tests are behavioral (`test_gui_engine_toggle`, `test_gui_history`,
  `test_gui_options`, `test_gui_batch`) — they don't assert theme/layout, so visual
  changes don't break them.
- Audio/video tests `skipif` ffmpeg/ffprobe absent; document tests `skipif` LibreOffice
  absent; some pdf-import tests `skipif` the OCR chain (ocrmypdf+Ghostscript+Tesseract)
  or `pdf2docx` absent; `test_mesh3d.py` `importorskip`s trimesh — a green run may be
  skipping these, so check the skip count.
- Test files span detection, quality, registry resolution (`test_registry_resolve.py`),
  each converter, engine/modes, presets, history, settings, validation, bootstrap, the
  Phase 2/3 matrices, output-path collision (`test_naming.py`), the batch GUI
  (`test_gui_batch.py`), the risk-9–12 fixes (`test_robustness_risks.py`), the
  risk-13–17 fixes (`test_risks_13_to_17.py`), and the PDF import
  (`test_pdf_import.py`).

---

## 10. Packaging (PyInstaller)

- Build: `pyinstaller converter.spec --noconfirm --clean` (onedir).
- Output: `dist/Converter/Converter.exe` (~26.8 MB, 28,139,722 bytes on the 2026-07-04
  post-mesh3d build) + `_internal/` containing `assets/fonts/*.otf`, `assets/converter.ico`,
  `assets/*.png`, and `bin/{ffmpeg,ffprobe,pandoc}.exe`.
- Bundle total ≈1.07 GB (`du -sh dist/Converter` → 1.1 GB on the 2026-07-04 build;
  previously ~700 MB before the PDF-import dependencies). Breakdown of the largest
  parts: `bin/` (ffmpeg+ffprobe+pandoc) 655 MB, cv2 111 MB, PySide6 92 MB, pymupdf 38 MB
  — cv2/pymupdf/pikepdf/lxml enter via pdf2docx (see §21). `_internal/torch` must not
  exist (tripwire).
- `hiddenimports` includes `pdf2docx`, `fitz`, `ocrmypdf`, `pikepdf`, `img2pdf` (added
  with the PDF import work), plus `collect_submodules("trimesh")` and
  `collect_data_files("trimesh")` (added with the 3D-model work — trimesh lazy-imports
  its exchange loaders and ships `resources/` schema/templates; see §24). `lxml` (for
  3mf) is not added explicitly — it already arrives via pdf2docx→python-docx and pikepdf.
- Entry point is **`converter_app.py`** (absolute import). Do **not** point PyInstaller at
  `converter/__main__.py` — its relative imports have no package context when frozen and Qt
  silently drops from the bundle.

---

## 11. Fonts

Bundled `assets/fonts/`: Inter Regular/Medium/SemiBold/Bold (static OTFs, SIL OFL 1.1,
license text shipped as `assets/fonts/OFL.txt`), loaded at startup (`app._load_fonts()`).
They register as one family:
- `Inter` — styles Regular(400), Medium(500), **SemiBold(600)**, Bold(700)

→ Prefer requesting weights from the **family** (`"Inter"` @600 for semibold) for an exact
match; the QSS family name must match exactly or text silently falls back to Segoe UI.

---

## 12. The theme system (`gui/theme.py`)

- One palette dict per theme (`_LIGHT` / `_DARK`); the whole QSS + badge colors derive from
  it, so changing a color propagates everywhere. Default theme is **light**.
- Public API: `apply_theme(app)`, `toggle_theme(app=None)`, `current_theme()`,
  `badge_color(level)`, `badge_bg(level)`, plus the helpers added this session
  (`apply_titlebar`, `_check_icon`).
- `apply_theme` sets Fusion + the stylesheet, and (this session) the font hinting preference
  (see §15, Bug 4).

---

## 13. Conventions

- Code comments are written in **Italian** (match the surrounding style when editing).
- Not a git repository.
- GUI contains zero conversion logic; adding a format never requires UI changes.

---

## 14. Work done in the most recent session — GUI redesign integration

The redesigned GUI lived in `nuova-gui/` and was integrated into `converter/gui/`. It was
diffed first: **the redesign was almost entirely visual** — core-calling logic was unchanged.

- New file `badge_delegate.py` paints Target/Quality/Status cells as colored **pill badges**.
- `theme.py` was a full visual redesign (per-theme palettes, large QSS, default dark→light).
- `app.py` added a font loader + `.ico` app icon; `main_window.py` added button icons +
  the badge delegate + taller rows; `options_panel.py` had font-family swaps only.

**Core-call verification:** every GUI→core call was checked against the live core —
**zero mismatches** (`resolve`, `targets_for`, `verdict`, `options_schema`, `engine.*`,
`run_job`, options classes, etc.). Theme persistence store matched (`APP_NAME=="Converter"`).
Integration = copy `nuova-gui/*` over `converter/gui/*` + the new delegate. Tests stayed at
165 passing; no GUI test needed updating (they're behavioral).

---

## 15. Bugs solved (this session)

### Bug 1 — Checkbox "checked" state was a solid blue block
- **Cause:** `QCheckBox::indicator:checked` used `background: {accent}` with no glyph.
- **Fix (`theme.py`):** light accent-tint background + accent border + a **checkmark glyph**
  (`_check_icon()` writes a per-theme SVG `check_<accenthex>.svg` to `%APPDATA%\Converter`,
  stroke = theme accent). Generated at runtime in the writable config dir → works frozen, no
  bundled asset. Verified native, both themes.

### Bug 2 — Black title bar clashing with the light theme
- **Cause:** Qt can't style the native window caption via QSS.
- **Fix (`theme.py::apply_titlebar()` + `main_window.py`):** Windows 11 DWM —
  `DwmSetWindowAttribute` with `DWMWA_CAPTION_COLOR` (= palette `window` color) and
  `DWMWA_USE_IMMERSIVE_DARK_MODE`. Called from `MainWindow.showEvent` and `_toggle_theme`, so
  it **follows the theme toggle live**. No-op + silent on non-Windows/old builds. Verified
  with a single-session live toggle (caption goes light→dark). DWM is reliable on this build
  (Win11 26200) — no frameless title bar needed.

### Bug 3 — Button icons removed by mistake, then RESTORED
- Initially all 10 `QStyle.SP_*` icons were removed (they're skeuomorphic and clashed). The
  owner actually wanted icons → **all 10 restored** exactly (Add files, Clear, Toggle theme,
  Browse, Apply ×2, Save, Delete, Cancel, Convert checked) + the `QStyle` import + `st`. The
  `_toggle_theme` method was kept (it carries the title-bar fix).

### Bug 4 — Primary (accent) button text rendered "wobbly" / uneven
- **Owner's hypothesis:** faux-bold synthesis. **Empirical diagnosis proved otherwise:**
  - The QSS already used the real Semibold face (`exactMatch=False` but same glyphs);
    changing the family reference left the render pixel-identical → not faux-bold.
  - Isolation (weight vs color vs widget vs size): clean at 22px, wobbly only at the real
    **13px** → the cause is **font hinting**. Default/full hinting snaps stems to the pixel
    grid unevenly; `PreferNoHinting` renders smooth, even glyphs.
- **Fix (`theme.py::apply_theme`):** set the app font's hinting preference to
  `PreferNoHinting` — it propagates through QSS to every widget (verified). One place, fixes
  all accent buttons, both themes.
- **Also tidied:** `#Primary` now uses family `"Inter"` @ `font-weight:600`
  (`exactMatch=True`) — the canonical exact-semibold request (correctness, not the visual fix).

---

## 16. Verification methodology (how claims were proven)

- **Tests:** `python -m pytest -q` → 165 passed at each checkpoint (offscreen for GUI).
- **Native GUI rendering:** harnesses run on the real `windows` Qt platform (NOT offscreen).
  `QWidget.grab()` for content; **Win32 `PrintWindow`** (PowerShell or in-process
  ctypes+GDI) for the real window frame incl. the DWM title bar. Screenshots inspected by eye
  in both themes. (`PIL.ImageGrab` returns black in a headless background session — use
  `PrintWindow`, which renders the window directly.)
- **Font diagnosis:** `QFontInfo` (family/styleName/weight/exactMatch) + `QFontDatabase` +
  controlled render matrices at 13px/22px.
- **Frozen exe:** launched the built `Converter.exe`, captured the live window via
  `PrintWindow`, and proved the new theme code runs frozen by deleting the generated
  `check_*.svg` and watching the exe regenerate it.

---

## 17. Owner's working preferences

- **Terse, evidence-first.** Short answers backed by real output (test counts, command
  results, screenshots). State failures plainly with the actual error.
- **Prove every "done / fixed / passing" claim** — run the tests, render the GUI, run the
  built exe. No success assertions without evidence.
- **Still learning the codebase** — explain architecture and non-obvious decisions when
  relevant; don't assume prior knowledge of *why* something works.
- **The GUI design is intentional and final.** When integrating redesigns: never change the
  visual design/layout/colors/UX; DO fix functional bugs and integration mismatches; adapt the
  GUI to the current core API — don't change core to match the GUI unless core is genuinely
  wrong.
- **Verify GUI claims on the NATIVE Qt platform** (real Windows screen + QTest), not offscreen.
- **Staged work:** show diffs/plans before touching things when asked; don't rebuild the exe
  until explicitly asked.

(Two persistent memories also capture: "Portable mode must be proven with the dependency
actually absent — don't trust dev-env-present tests" and "GUI modal/blocking claims need a
real Windows Qt run, not offscreen.")

---

## 18. Current state & open items

- Redesigned GUI integrated; the review-pass fixes of §22 applied; the same-format data
  fix + two minor GUI fixes of §23 applied; the 3D-model converter of §24 added;
  **246 tests passing** (measured 2026-10-24, 0 skipped in an environment with all
  external tools present, trimesh included).
- Exe rebuilt (`dist/Converter/Converter.exe`, 2026-07-04, incl. the PDF import, the 3D
  converter, the `--convert` and `--pdf2docx` flags, and the §22–24 changes); from the
  exe: pdf→docx and pdf→odt via `--convert` returned `ok=True` with the expected text;
  `--selftest` reports PASS 15/15; stl→glb and 3mf→stl via `--convert` returned `ok=True`
  with re-loadable output (8 and 650 vertices).
- **Open item:** `--selftest` does not yet include a model3d case (it covers image/audio/
  video/data/document/portable). Adding one would give the frozen 3D path a permanent
  smoke test; for now it's verified via `--convert`.
- `nuova-gui/` already removed (was redundant, already integrated).
- The Title/header QSS still reference the separate `"...Semibold"`/`"...Medium"` families;
  they render fine but could be aligned to the base-family + numeric-weight pattern for
  consistency.
- README status line already reflects reality (**"Status: Phases 1–2"**, documents/pdf/rtf/
  pandoc listed, CLI noted as Phase 3 stub) — earlier note here was itself stale.

---

## 19. Bug fixed (2026-07-02) — docx/odt→html silently lost colors

- **Symptom:** docx→html routed through pandoc (`MarkupConverter`, `priority=10`), which
  discards visual formatting (cell colors/shading, fonts). Output showed
  `<meta name="generator" content="pandoc">` and colored table-header cells came out as plain
  `<td><strong>...</strong></td>` — no background.
- **Root cause:** `registry.resolve()` picks the first converter (by descending `priority`)
  that claims a pair. `MarkupConverter._OFFICE_OVERRIDE_OUT` used to include `"html"` alongside
  `"md"`/`"txt"`, so pandoc (priority 10) always beat `DocumentConverter` (LibreOffice,
  default priority 0) for docx/odt→html — even though LibreOffice already claimed that pair
  with a correct `lossy` verdict and preserves formatting via inline CSS.
- **Fix (`converters/markup.py`):** dropped `"html"` from `_OFFICE_OVERRIDE_OUT` (now just
  `{"md", "txt"}`). md/txt keep pandoc — colors are irrelevant there by definition, and the
  Portable path stays intact. `document.py` untouched.
- **Behavior change (intentional):** docx/odt→html now resolves to `DocumentConverter` in
  FULL (colors preserved). In FULL without LibreOffice or in PORTABLE, the pair still *appears*
  in `targets_for` (DocumentConverter claims it regardless of availability) but greyed out with
  the "Full mode" reason — no more silent color-losing fallback to pandoc. This trades away
  the old Portable docx→html-via-pandoc path in exchange for fidelity in Full — deliberate.
- **odt→html** is affected the same way (same `_OFFICE_SRC` set) — no test asserted its engine
  choice, confirmed as an acceptable, consistent side effect.
- **Verified:** `python -m pytest -q` → 166 passed (was 165, +1 new routing test
  `test_docx_html_routes_to_libreoffice_in_full`); two existing assertions in
  `test_phase2_matrix.py` updated to match (`docx→html` engine, PORTABLE greyed-not-absent).
  Real end-to-end proof: built a docx with colored table cells, ran it through the actual
  `run_job`/registry pipeline in FULL — output HTML had `bgcolor="#4472c4"` / `background:
  #4472c4` and no pandoc generator tag; the same file run through pandoc directly showed
  `<meta name="generator" content="pandoc">` and zero color info, confirming the before/after
  contrast.

---

## 20. Bug fixed (2026-07-02) — "Convert to:" combo box had no visible down-arrow

- **Symptom:** the target-format `QComboBox` in `options_panel.py` (`Convert to:`) gave no
  visual hint it was clickable — no dropdown glyph on the right edge, in either theme.
- **Root cause (`theme.py`):** `QComboBox::drop-down { border: none; width: 22px; }` reserved
  space for the arrow but there was **no `::down-arrow` rule at all** — no image, no glyph —
  so Qt drew an empty box.
- **Fix (`theme.py`):** added `_arrow_icon(color)`, mirroring the existing `_check_icon()`
  pattern (checkbox glyph) — writes a small 10×10 chevron SVG to the writable config dir
  (`%APPDATA%\Converter`, one file per color, works frozen) and wires it via
  `QComboBox::down-arrow { image: url(...); width: 10px; height: 10px; }`. Color =
  `p["text_muted"]` (neutral grey, not accent) so it reads like a standard Fluent combo arrow
  in both palettes. Drop-down width/border/padding and combo behavior untouched — arrow only.
- **Verified:** native Qt render (`windows` platform, not offscreen) of `OptionsPanel`,
  captured via Win32 `PrintWindow` (`PW_RENDERFULLCONTENT`), in both light and dark — arrow
  visible in the "Convert to:" box in both screenshots. Confirmed fresh generation:
  `arrow_6b6b6b.svg` (light) and `arrow_a0a0a0.svg` (dark) created in the config dir at
  runtime. `python -m pytest -q` → 166 passed, no regression. Exe rebuilt and relaunched
  (PID confirmed, killed cleanly).

---

## 21. PDF import (2026-07-02/03) — pdf→docx/odt/rtf + OCR branch

New converter `converters/pdf_import.py` (`PdfImportConverter`): input `pdf`, outputs
`docx/odt/rtf`, `category="document"`, `priority=10`. Reason for the priority:
`registry.resolve()` does not consult `pair_available()`, so `DocumentConverter`
(priority 0) also claims pdf→docx/odt/rtf (while blocking them via `pair_available`);
priority 10 routes the pairs to `PdfImportConverter`.

**Availability** (`available()`): in PORTABLE → unavailable ("PDF import requires Full
mode.", no install hint). In FULL → requires the `pdf2docx` module. LibreOffice is
**not** part of `available()`; the soffice requirement for the odt/rtf second hop lives
in `pair_available(src, dst)`, which returns unavailable for dst in `{odt, rtf}` when
`find_soffice()` is None. So pdf→docx is offered/runs without LibreOffice; pdf→odt/rtf
grey out with a reason when LibreOffice is absent. The OCR chain is not part of either gate.

**Conversion paths** (`convert()`):
- Text PDFs: pdf→docx via pdf2docx run in a **child process** — `_pdf_to_docx` builds a
  command (`converter_app --pdf2docx <src> <dst>`; frozen: the exe with the flag, from
  source: the interpreter on `converter_app.py`) and runs it through
  `_libreoffice.run_with_timeout` (hard timeout `_PDF2DOCX_TIMEOUT = 300 s`, tree kill,
  clean env). The child calls `_pdf_to_docx_inprocess` (the pdf2docx call). If the command
  cannot be built (`_pdf2docx_command` returns None), `_pdf_to_docx` falls back to
  `_pdf_to_docx_inprocess`. For odt/rtf the produced docx goes through
  `_libreoffice.run_convert` (internal chain — `resolve()` does not chain converters on its own).
- Scanned PDFs: OCRmyPDF as a subprocess (`--skip-text`, `-l eng+ita+fra`, 600 s hard
  timeout via `_libreoffice.run_with_timeout`, clean env, no console window) produces a
  PDF with a text layer; the docx is then rebuilt from the extracted text with
  python-docx (`_ocr_pdf_to_docx`), not with pdf2docx — see the technical fact below.

**Scanned detection**: `validate()` (called by `run_job` before `convert()`) extracts
text with pdfminer over the first `_SCAN_CHECK_MAX_PAGES = 5` pages
(`extract_text(str(src), maxpages=5)`); under `_MIN_TEXT_CHARS = 10` non-whitespace
characters the PDF is treated as scanned. With the OCR chain present (`_ocr_available()`: ocrmypdf exe + Ghostscript +
Tesseract), validation passes and OCR happens in `convert()`. Without it, validation
returns a message naming what to install. Text PDFs convert in both cases (the OCR
branch degrades without disabling the converter).

**Technical fact — `ocr-invisible-text-layer`**: OCRmyPDF's default "sandwich" output
stores the OCR text with alpha=0 (invisible). pdfminer and PyMuPDF extract that text;
pdf2docx drops it (its own log: "Words count: 0") and returns success with a docx that
contains no text. This is why the scanned branch rebuilds the docx from extracted text
instead of calling pdf2docx. Also recorded as the auto-memory `ocr-invisible-text-layer`.

**deps.py additions**: `find_tesseract()` (bin/ → PATH → `C:\Program Files\Tesseract-OCR`),
`find_ghostscript()` (`gswin64c`; searches `C:\Program Files\gs\gs<ver>\bin` because the
Ghostscript installer does not add it to PATH — at runtime OCRmyPDF locates Ghostscript
itself via the Windows registry, which `clean_subprocess_env()` does not affect),
`find_ocrmypdf()` (pip console script). All three have install hints, entries in
`deps.report()`, and are cleared by `engine.refresh_deps()`.

**Verdict**: lossy for all three targets. The reason text states that layout is
reconstructed and that scanned PDFs become text-only documents.

**Entry point (`converter_app.py`)**: `--convert <src> <dst_format> <out> [report]`
flag — one conversion via `run_job`, result written to the report file (the exe is
windowed; stdout is not visible without a console). `--pdf2docx <src> <dst>` runs the
single pdf2docx step (`_pdf_to_docx_inprocess`) and is the command the PDF-import child
process invokes; it exits 0 on success, 1 on failure (error on stderr), 2 on missing
args. Neither is the Phase 3 CLI (`converter/cli/` remains the stub). The `--selftest`
check `pdf writer-targets greyed` was replaced by `pdf writer-targets`: expects the pairs
available when `pdf2docx` is importable, greyed otherwise.

**Dependencies**: `pdf2docx` (pulls PyMuPDF/AGPL, opencv-python-headless, python-docx,
lxml) and `ocrmypdf` added to `requirements.txt`; `pdf2docx`, `fitz`, `ocrmypdf`,
`pikepdf`, `img2pdf` added to `hiddenimports` in `converter.spec`. Tesseract,
Ghostscript and the ocrmypdf console script are install-yourself dependencies (not
bundled).

**Tests**: 166 → 179 collected. `tests/test_pdf_import.py` (11 test functions, 13
collected items — one parametrized ×3): routing in FULL, availability per mode and per
missing dependency (dependency mocked, not the mode), scanned-vs-text `validate()` with
the OCR chain mocked present/absent, docx rebuild logic, and one test that runs OCRmyPDF
on a generated image-PDF and checks the rebuilt docx is non-empty (skips without the
OCR chain). Three pre-existing tests that asserted "pdf→writer greyed" were updated to
the new routing (`test_document_converter.py` ×2, `test_phase2_matrix.py` ×1).

**Frozen build (2026-07-03)**: `pyinstaller converter.spec --noconfirm --clean` exit 0;
`dist/Converter/Converter.exe`; bundle ≈1.07 GB (see §10); no `_internal/torch`; no
"missing module" build warnings for pdf2docx/fitz/ocrmypdf/pikepdf/img2pdf/cv2/docx.
Runs from the exe: text PDF → docx (`ok=True`, marker string present in the extracted
text); scanned 8-page PDF → docx (`ok=True`, 24,076 non-whitespace characters extracted
from the output; the source has no extractable text, so the text comes from the OCR
branch); `--selftest` → PASS, 15/15 entries True.

---

## 22. Fixes from the 2026-07-03/04 review pass (behavior changes)

Source of the list: `BUGS.md` (a read-only review that recorded 8 correctness items and
9 "risk" items with before/why for each). Each item below states what the code does now
and where. Test count went 179 → 215. Every item has a regression test named in `BUGS.md`.

- **`data.py` json↔yaml, json→json, yaml→yaml** route through `DataConverter._convert_tree`
  (`json.load`/`yaml.safe_load` → dump of the other), not `pd.json_normalize`. Nested
  structures are preserved. Tabular targets (json→csv/xlsx/…) still use the DataFrame path.
- **`data.py` csv/tsv reading** uses `pd.read_csv(..., dtype=str, keep_default_na=False)`
  for every destination, so values such as `01234`, `1.10`, `00500` are preserved
  unchanged (previously pandas type inference produced `1234`, `1.1`, `500`).
- **`core/naming.py`** `resolve_collision(path, reserved=None)` and
  `build_output_path(..., reserved=None)` treat a name as taken if it exists on disk or is
  in `reserved`. `main_window._convert_all` keeps a per-batch `set` of assigned paths and
  passes it, so two sources with the same stem get distinct output paths.
- **`main_window._on_finished`** guards `0 <= row < len(self.model.items)` and increments
  `_completed` regardless; `clear_btn` is an instance attribute disabled in `_convert_all`
  and re-enabled in `_finish_batch`.
- **`core/registry.resolve`** returns, among converters that claim the pair (ordered by
  descending `priority`), the first whose `_pair_fully_available` is true (`available().ok`
  + `format_available(src)` + `format_available(dst)` + `pair_available`); if none qualify
  it returns the highest-priority one. (Previously it returned the first by priority
  without checking availability.)
- **`core/detection._magic`** checks the ADTS AAC sync (`\xff\xf1`/`\xff\xf9`) before the
  mp3 branch. **`detect`** attempts a full-file JSON parse whenever the lstripped head
  starts with `{` or `[` (condition `head.lstrip()[:1]`, previously `head[:1].lstrip()`).
- **`_libreoffice.run_convert`** returns success only when `not timed_out and code == 0 and
  produced.exists()`; on timeout/crash it removes any partial output before rebuilding the
  profile.
- **`_ffmpeg.probe_duration`** passes `timeout=_PROBE_TIMEOUT` (15 s). **`run_ffmpeg`** runs
  a watchdog thread that kills ffmpeg on cancel-while-blocked or after `_STALL_TIMEOUT`
  (120 s) with no output, and removes the output file on cancel/stall/non-zero exit.
- **Partial-output removal on failure/cancel** via `unlink(missing_ok=True)` in
  `run_ffmpeg`, `markup.py` (pandoc timeout / non-zero exit), and `pdf.py` (pdfminer except).
- **`converter_app.py`** guards `if sys.stdout:` in `_selftest`/`_dumpenv`; `gen_media`
  passes `env=clean_subprocess_env(), creationflags=CREATE_NO_WINDOW`; `_selftest` removes
  its temp dir; adds the `--pdf2docx` flag.
- **`document.py` convert** copies the file (`shutil.copy`) when `src == dst` instead of a
  LibreOffice round-trip.
- **`image.py` convert** passes `save_all=True` for multi-frame sources to gif/webp/tiff;
  applies `ImageOps.exif_transpose` on single-frame sources; returns a `lossy` result
  verdict when a multi-frame source is written to a single-frame target (png/bmp/jpg/ico/
  heic/avif).
- **`deps.find_ghostscript`** orders version directories with a numeric key
  (`_gs_version_key`) instead of a string sort.

**Frozen build (2026-07-04)** — rebuilt after the fixes above:
`pyinstaller converter.spec --noconfirm --clean` exit 0; `dist/Converter/Converter.exe`
(mtime 2026-07-04); no `_internal/torch`; `du -sh dist/Converter` → 1.1 GB. Checks run
against this exe:
- `--selftest` → `SELFTEST PASS`, 15/15 entries True, exit 0.
- `--convert doc.pdf docx …` → `ok=True`, output 36765 bytes, marker text present
  (the child process `Converter.exe --pdf2docx` ran; in frozen mode `_pdf2docx_command`
  returns the subprocess command with no in-process fallback, so a produced docx implies
  the child ran and imported pdf2docx).
- `--convert doc.pdf odt …` → `ok=True`, output 14407 bytes, marker text present (docx→odt
  second hop via `run_convert`).
- `--convert data.csv xlsx …` → `ok=True`, verdict lossless, cells `A2='01234'`,
  `B2='1.10'`, `A3='00500'` (the csv/tsv `dtype=str` read is in this binary).
- `--convert data.csv json …` → `ok=True`, verdict lossless, first record
  `{'code': '01234', 'price': '1.10'}`.

---

## 23. Same-format data fix + minor GUI fixes (2026-07-04)

A follow-up read-only review (recorded as **bug 0** in `BUGS.md`) plus two minor
observations, all fixed with regression tests. Test count 218 → 221 before the 3D work
(§24 then took it to 236).

- **`data.py` — xlsx→xlsx / ods→ods / xml→xml declared LOSSLESS "Same format." but ran a
  lossy pandas round-trip** (same class as the document bug 13 in §22, missed for data).
  `verdict()` short-circuits `src == dst` → lossless **before** the spreadsheet/XML lossy
  checks, while `convert()` routed those formats through pandas (values-only, first sheet
  only). Proven live: a 2-sheet xlsx with a formula came out as 1 sheet, formula gone,
  badge still LOSSLESS. **Fix (owner chose byte-copy, preserving data — like `document.py`):**
  new `DataConverter._same_format_copy` — for `src == dst` in {xlsx, ods, xml}, `shutil.copy`
  the file so lossless is honest and sheets/formulas/structure survive; if `strip_metadata`
  is on, `_strip_spreadsheet_metadata` clears document properties after the copy (openpyxl
  zeroes properties; odfpy empties `meta`), leaving data intact — consistent with images.
  csv/tsv and json/yaml are excluded (they have meaningful same-format options — header,
  pretty/orient — and already-faithful paths). Regressions:
  `test_same_format_xlsx_preserves_sheets_and_formulas`, `test_same_format_xml_is_byte_identical`,
  `test_same_format_xlsx_strips_metadata_only_on_request` (replaced the old
  `test_document_metadata_does_not_leak`, which had enshrined the lossy behavior as a
  privacy feature).
- **`gui/dialogs.py` — History "Result" column always tinted green.** A successful-but-lossy
  conversion read as "lossless". Fixed to color by the entry's real verdict level:
  `ok`+lossy → amber, `ok`+lossless → green, `failed` → red.
- **`gui/main_window.py::_add_paths` — crashed if a dropped/selected file vanished** before
  processing (`path.stat()` raising). Wrapped `stat()`+`detect()` in `try/except OSError`;
  the file is skipped, the rest of the batch loads, and the status line reports the skip
  count. Regression: `test_add_paths_skips_vanished_files_without_crashing`.

---

## 24. 3D-model converter (2026-07-04) — `mesh3d.py` (trimesh)

New category **`model3d`** and converter `converters/mesh3d.py` (`Mesh3DConverter`), backed
by **trimesh** (MIT, pure-Python; only hard dep is numpy, already pulled by pandas). Runs
in-process like `image.py`, so it works in both Full and Portable — no external tool.

- **Formats:** inputs obj/stl/ply/glb/gltf/3mf; outputs obj/stl/ply/glb/gltf. **3mf is
  input-only** (deliberate scope decision). Added to `core/formats.py` (`CATEGORIES` gains
  `model3d`, six `Format` entries).
- **Availability & gating:** `available()` needs the `trimesh` module; `format_available("3mf")`
  greys out when the optional `lxml` is absent (`_LXML_OK = has_module("lxml")`). lxml is
  normally present (transitive of pdf2docx). Same optional-dep pattern as HEIC/AVIF in `image.py`.
- **Verdicts** (structural; reasons are the exact badge text): same-format lossless;
  glb↔gltf lossless (geometry+materials+scene graph); stl/ply→ply lossless; obj/glb/gltf/3mf→ply
  lossy (textures/materials dropped); anything→stl lossy (triangle-only); anything→obj lossy
  (scene graph flattened, materials to sidecar .mtl); obj/stl/ply/3mf→glb/gltf lossy (materials
  approximated into glTF PBR). **Runtime override:** a multi-object scene (glb/gltf/3mf/obj)
  going to a single-mesh target (stl/obj/ply) is flattened via `Scene.to_geometry()` and the
  verdict is downgraded to lossy with a "multiple objects merged" reason — mirrors `image.py`'s
  multi-frame override. (`to_geometry()` is used because `Scene.dump(concatenate=True)` is
  deprecated for removal in trimesh 4.12.2.)
- **Options:** stl and ply expose a binary/ASCII `encoding` choice (trimesh file_type `stl`
  vs `stl_ascii`; ply `encoding=` kwarg).
- **Detection** (`core/detection.py`): glb via magic `glTF`, ply via magic `ply\n`/`ply\r`,
  3mf via ZIP inspection (`3D/3dmodel.model` / `3dmanufacturing` in Content_Types), gltf via
  full-JSON structure (requires `asset` + one of meshes/scenes/nodes, so it doesn't steal
  generic `.json` from the `data` category), ASCII STL sniffed via `solid`+`facet normal`.
  **Binary STL has no reliable magic → falls back to the extension tiebreaker (0.3)** — the one
  documented place content-detection can't fully apply.
- **Frozen build:** `converter.spec` gained `datas += collect_data_files("trimesh")` and
  `hiddenimports += collect_submodules("trimesh")` (trimesh lazy-imports its exchange loaders,
  which static analysis misses — without this the exe imports trimesh but fails every 3D
  conversion). lxml not duplicated (rides in via pdf2docx/pikepdf). Rebuilt
  (`pyinstaller converter.spec --noconfirm --clean`, exit 0); bundle 1.1 GB (trimesh ~300 KB);
  no `_internal/torch`; trimesh+resources+lxml present. **Verified in the frozen exe** via
  `--convert`: stl→glb `ok=True` (re-loads to 8 vertices) and 3mf→stl `ok=True` (re-loads to
  650 vertices — the multi-object 3mf loaded through the bundled threemf loader + lxml, then
  merged). GUI untouched: the new pairs surface purely through `targets_for`/`resolve`.
- **Tests:** `tests/test_mesh3d.py` (18 collected, `importorskip` trimesh) — registry routing,
  per-format roundtrip vertex counts, the verdict matrix, the scene-flattening runtime override,
  the 3mf/lxml gate (mocked, not uninstalled), and content detection of glb/ply/stl/gltf.
- **Docs:** `README.md` gained a `### 3D models (trimesh)` matrix section and the Status line
  now lists 3D models; `requirements.txt` gained `trimesh` with a note that lxml enables 3mf.

## 25. Release-preparation pass (2026-10-24)

- **`mesh3d.py`: same-format conversions were not a copy.** `verdict("stl","stl")` said
  LOSSLESS "geometry is copied unchanged", but `convert()` always re-exported through
  trimesh — and a multi-object OBJ became one flattened mesh. `src == dst` now does a
  byte-for-byte `shutil.copy`, like `document.py`/`data.py`; regression test asserts the
  output bytes equal the source bytes.
- **Config files with valid-but-wrong-shape JSON crashed the GUI.** `PresetStore.load()`
  runs while `MainWindow` is built, so `presets.json` containing `[]` (or `"presets": 42`)
  raised `AttributeError` at startup; `history.json` had the same hole on opening History.
  Both now require a dict with a list under `presets`/`entries`, skipping non-dict items.
- **GUI: an unwritable output folder raised from the Convert click and wedged the batch**
  (exception mid-planning left the remaining rows "Converting", Convert disabled forever).
  `_convert_all` now catches `OSError` per row, marks it Failed with a clear message, counts
  it as completed; if no job started, the batch closes immediately. Row lookup is by
  identity instead of `list.index` (`__eq__` could route signals to the wrong row).
- **GUI: changing target/options on a `Done` row did nothing.** `items_to_convert()`
  excludes `Done`, so the edit was silently skipped; `_on_options_changed` now resets the
  row to `Ready` (clearing `out_size`/`output_path`).
- **`deps.find_soffice()`: macOS LibreOffice was undetectable** (the `.app` binary is not on
  PATH). Added `/Applications/...` and `~/Applications/...`; the Windows common dirs are now
  only consulted on Windows (`_windows_common()` used to leak Windows defaults everywhere).
- Docs: user-facing dependency labels no longer say "Phase 2"; `docs/screenshot.png` (broken
  in the README) now exists; test counts updated. Full suite: **246 passed**.
