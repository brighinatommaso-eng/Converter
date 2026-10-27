# Converter — Project Overview & Work Log

A complete picture of the app: what it is, how it's built, the principles and conventions,
the work done so far, the bugs solved, and my (the owner's) working preferences. Written so a
future session can pick up with full context without re-deriving everything.

- **App:** Converter `0.1.4` — *"Convert files locally — honest about quality."*
- **Stack:** Python 3.13 + PySide6 (Qt6), offline desktop app, Windows-first.
- **Last updated:** 2026-10-26.
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
  backends (Pillow, ffmpeg, pandas, LibreOffice, pdfminer, markdown-it-py/odfpy, striprtf).
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
| `deps.py` | Runtime detection of external tools + controlled degradation. `find_ffmpeg/find_ffprobe/find_soffice/find_tesseract/find_ghostscript/find_ocrmypdf`, `clean_subprocess_env()`. |
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
| `markup.py` | in-process markup engine (`markupdoc/`: markdown-it-py, stdlib HTML parser, odfpy, python-docx) — replaced pandoc on 2026-10-27 | markup/document interchange | md/html/epub/txt → md/html/epub/txt/rtf/docx/odt and odt → md/txt; priority 12; sanitized HTML tree as the intermediate model; images never downloaded; docx/odt→**html** is NOT claimed here (see §19), so it routes to LibreOffice. |
| `pdf.py` | pdfminer.six (pure-Python, MIT) | pdf→txt / pdf→html fallback | works in Portable; priority 10, so the structured engine normally wins those pairs. |
| `textdoc.py` | pdfminer.six + python-docx (both MIT, in-process) | pdf→docx/odt/rtf/md/txt/html; docx→md/txt | structured IR (`docmodel/`); lossy with target-specific reasons; docx/md/txt/html in both modes, odt/rtf need FULL + soffice. See §21. |
| `docmodel/` | — | IR + readers (`read_pdf`, `read_docx`) + writers (`write_docx`, `write_markdown`, `write_text`, `write_html`) | the single structure model behind `textdoc.py`; documented seam for optional AI refinement. |
| `_ocr.py` | OCRmyPDF (external) | scanned-PDF preprocessing | OCR only adds an invisible text layer; the structured reader then runs unchanged. |
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
  `bin/`. ffmpeg/ffprobe are bundled into the frozen app's `_internal/bin` (pandoc was, until the in-process markup engine replaced it on 2026-10-27).
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
- **Current status in this workspace: 168 collected — 148 passing, 20 xfailed** (measured
  2026-10-26 with ffmpeg, LibreOffice, pandoc, python-docx and trimesh present). The 20
  xfails are corrupt/encrypted files in the local real-document corpus (see
  `tests/corpus/README.md`); the 147 readable corpus documents are regression-locked.
  Without `tests/corpus/local/`, the corpus test skips and the suite is 288 passed.
- GUI tests set `QT_QPA_PLATFORM=offscreen` (keep that for new GUI tests, or they need a
  display). GUI tests are behavioral (`test_gui_engine_toggle`, `test_gui_history`,
  `test_gui_options`, `test_gui_batch`) — they don't assert theme/layout, so visual
  changes don't break them.
- Audio/video tests `skipif` ffmpeg/ffprobe absent; document tests `skipif` LibreOffice
  absent; `test_mesh3d.py` `importorskip`s trimesh — a green run may be skipping these,
  so check the skip count.
- Test files span detection, quality, registry resolution (`test_registry_resolve.py`),
  each converter, engine/modes, presets, history, settings, validation, bootstrap, the
  Phase 2/3 matrices, output-path collision (`test_naming.py`), the batch GUI
  (`test_gui_batch.py`), the risk-9–12 fixes (`test_robustness_risks.py`), the
  risk-13–17 fixes (`test_risks_13_to_17.py`), and the structured document engine
  (`test_textdoc.py`).

---

## 10. Packaging (PyInstaller)

- Build: `pyinstaller converter.spec --noconfirm --clean` (onedir).
- Output: `dist/Converter/Converter.exe` (~26.8 MB, 28,139,722 bytes on the 2026-07-04
  post-mesh3d build) + `_internal/` containing `assets/fonts/*.otf`, `assets/converter.ico`,
  `assets/*.png`, and `bin/{ffmpeg,ffprobe,pandoc}.exe`.
- Bundle total was ≈1.07 GB with the old pdf2docx tree (`du -sh dist/Converter` → 1.1 GB
  on the 2026-07-04 build). The largest parts remain `bin/` (ffmpeg+ffprobe+pandoc)
  655 MB and PySide6; dropping pdf2docx removes cv2 (111 MB) and pymupdf (38 MB), so a
  rebuild should be smaller. `_internal/torch` must not exist (tripwire).
- `hiddenimports` includes `docx` (structured document engine), `ocrmypdf`, `pikepdf`,
  `img2pdf`, plus `collect_submodules("trimesh")` and `collect_data_files("trimesh")`
  (added with the 3D-model work — trimesh lazy-imports its exchange loaders and ships
  `resources/` schema/templates; see §24). `datas` also collects `pdfminer` resources and
  `collect_data_files("docx")` (`default.docx` / default-*.xml are opened at runtime).
  pdf2docx/PyMuPDF are no longer dependencies at all (§21); `lxml` (for 3mf) arrives via
  python-docx and pikepdf.
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
  fix + two minor GUI fixes of §23 applied; the 3D-model converter of §24 added; the
  structured document engine of §21 replaced the pdf2docx import; **428 tests passing,
  20 xfailed** with the local real-document corpus (280 passing without it), measured
  2026-10-25 in an environment with all external tools present, trimesh included.
- The shipped exe (`dist/Converter/Converter.exe`) was rebuilt on 2026-10-25 with
  `converter.spec` and now runs the structured engine: `--selftest` passes (including
  `pdf struct writer-targets`) and `--convert` on `multi-column.pdf` produced
  `ok=True` for docx/md/txt with the expected tables, headings and prose.
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

## 21. Structured document engine (2026-10-24) — pdf/docx → every text format

Replaces the former `pdf_import.py` (pdf2docx + PyMuPDF, AGPL) with a
deterministic, MIT-licensed engine. The trigger was empirical: on a realistic
two-page PDF (title, numbered headings, a 3×3 table, bullets, page break) the old
paths gave bad results — pdf→md did not exist at all, pdf→txt flattened the table
into column order and moved a heading after the cells, pdf→html was an
absolute-positioned div dump, and pdf→docx produced only `Normal` paragraphs with
Symbol bullets and split paragraphs. A PDF has no structural text model, so the
structure has to be **inferred from the page geometry**; an LLM would infer it
non-reproducibly, not add it. Hence: one intermediate representation (IR) and
writers per format, with the IR as the documented seam for any future AI
refinement (`Document in → Document out`, off by default).

**IR (`converters/docmodel/types.py`).** `Document`, `Paragraph` (style
title/heading/body + level), `ListBlock`/`ListItem`, `Table`/`TableCell`,
`ImageBlock`, `PageBreak`, `Line`/`Run` (bold, italic, underline, font, size,
colour). `Paragraph.text` joins lines with dehyphenation. `body_size()` is the
dominant size used to rank headings.

**PDF reader (`docmodel/pdfreader.py`, pdfminer.six).** Reads `LTTextLine` +
`LTLine`/`LTRect` instead of `LTTextBox` (pdfminer groups table cells into boxes
and mixes the preceding heading into the first cell). Pipeline:
1. characters → `Line` + `Run`s (style from `fontname`, size, colour);
2. thin rule segments → connected components → table grid; cells are filled by
   line position;
3. borderless tables: rows of words with a stable column grid (numeric 2-column
   tables with ≥3 rows; 3+ columns only with a `Table N` caption, ≥50 % numeric
   cells, or a bold short header over dense rows) — conservative on purpose, so
   two-column prose, infographics, TOC pages and layout boxes stay paragraphs;
4. reading order via recursive xy-cut: columns left-to-right, then horizontal
   bands at the widest gap (a full-width title does not mix the columns below
   it); the same ordering is applied to paragraphs, tables and images;
5. paragraph merging (line gaps/indent), dehyphenation across line breaks;
6. heading/title inference by size rank + numbering (`1.`, `2.1`, `Chapter …`);
7. bullet/number markers grouped into `ListBlock`s with the marker stripped;
8. repeated header/footer lines removed (top/bottom margin bands);
9. embedded images extracted through pdfminer's `ImageWriter` into `ImageBlock`.

**DOCX reader (`docmodel/docxreader.py`).** Maps real Word styles (`Title`,
`Heading N`, `List Bullet`/`List Number`, tables) to the same IR, so docx→md/txt
no longer goes through pandoc's grid tables and front-matter titles. Lists are
resolved from `w:numPr` + `numbering.xml` (ordered vs bullet, level), because most
real Word lists use `List Paragraph`, not a list style. Horizontally merged cells
are emitted once instead of once per covered column.

**Writers (`docmodel/writers.py`).**
- `write_docx(..., layout=...)`: `structure` (default) emits real Word styles
  (`Title`, `Heading N`, `List Bullet`), real tables and carried-over font
  size/name/colour; `visual` anchors every printed line to the page with
  `w:framePr` (`w:x`/`w:y` in twips), re-attaches list markers and inserts a
  page break between PDF pages. `page_breaks` toggles the breaks.
- `write_markdown`: GFM headings, lists, pipe tables, embedded images.
- `write_text`: reading order, bullet lines, tab-separated table rows.
- `write_html`: semantic `h1–h6`/`ul`/`ol`/`table` with inline font/colour and
  data-URI images (no `position:absolute`).
- odt/rtf have no dedicated writer: the engine writes a docx and runs an
  **internal** LibreOffice docx→odt/rtf second hop (`run_convert`).

**Converter (`converters/textdoc.py`, `TextDocConverter`, `priority=20`).**
Claims `pdf → {docx, odt, rtf, md, txt, html}` and `docx → {md, txt}`; every
other pair stays on its existing engine (LibreOffice for office↔office and
docx→html, the markup engine for markup→office). `pdf.py` (pdfminer flat dump, priority 10)
remains the fallback for pdf→txt/html if the structured engine is unavailable.
- **Availability:** in-process pdfminer.six + python-docx, so pdf→docx/md/txt/html
  work in **both** FULL and PORTABLE. odt/rtf need FULL mode + soffice for the
  second hop (checked in `pair_available`, not `available`).
- **Options:** pdf→docx exposes `docx_layout` (`structure` | `visual`) and
  `page_breaks` (default true).
- **Verdicts:** always honestly `lossy` with a target-specific reason. Structure
  mode states that absolutely positioned elements are not reproduced; visual
  mode states that lines are separate frames and editing/rewrapping will not
  reflow.

**OCR (`converters/_ocr.py`).** The OCR helpers moved out of the deleted
`pdf_import.py`. OCRmyPDF (`-l eng+ita+fra --skip-text`, 600 s hard timeout via
`_libreoffice.run_with_timeout`, clean env, no window) only preprocesses a scanned
PDF; its text layer is invisible (alpha 0) but pdfminer extracts it, so the same
structured reader handles native and scanned PDFs. Without the OCR chain,
`validate()` refuses scans with an install message; text PDFs always convert.

**Removed:** `converter/converters/pdf_import.py` (`PdfImportConverter`,
`_pdf_to_docx`, OCR docx rebuild) and the `converter_app.py --pdf2docx` child
flag. `--selftest` now covers `portable pdf->txt/md/docx/html` and
`pdf struct writer-targets` (docx/md/txt/html available).

**Dependencies/packaging:** `python-docx>=1.1` is now a direct MIT dependency
(`pdf-import` extra removed). `converter.spec` collects `docx` data
(`default.docx` templates) and no longer mentions/excludes pdf2docx/fitz.
`THIRD_PARTY_NOTICES.md` lists python-docx + lxml and states no AGPL PDF engine
is used.

**Tests:** `tests/test_textdoc.py` (47 collected) — routing/availability in both
modes, the OCR validate gate (mocked chain), a broken PDF, pure-IR unit tests,
reader robustness (positioned words, `w:numPr` lists, comments in the body,
missing page size/table grid, merged cells), aligned-table detection (numeric
2-column, caption-gated 3-column, two-column prose rejection, fragment merge),
xy-cut column order, and end-to-end assertions on a LibreOffice-built rich PDF
for md/txt/docx structure/visual/html/odt plus docx→md GFM tables. DOCX fidelity
is covered separately: hyperlinks reach every writer, inline images become
`ImageBlock`s, header/footer survive md/txt/html/docx, `vMerge` becomes one cell
with `row_span` (and `hMerge` one cell with `col_span`), a rotated font name
cannot escape the HTML `style` attribute, and unsafe link schemes are dropped.
`tests/test_corpus.py` is the real-document harness (structure digest vs JSON
baselines — see `tests/corpus/README.md`): 147 readable documents pass, 20
corrupt/encrypted files xfail; in `CORPUS_UPDATE=1` mode unreadable files
without a baseline skip instead of failing. Stale `pdf_import` tests were
deleted/updated (matrix, document converter, risk 9/17).
Full suite: **148 passed, 20 xfailed** with the local corpus (288 passed without
it), 2026-10-26.

**Corpus pass (2026-10-24).** 167 real PDF/DOCX files (Unstructured
`example-docs`, Apache POI `test-data/document`) through the harness. Fixes driven
by the failures:
- PDF word spacing: pdfminer puts synthetic spaces in `LTAnno`, not `LTChar`;
  skipping them glued every word (`LayoutParser:AUnifiedToolkit`).
- DOCX lists from `w:numPr` + `numbering.xml`: 10 corpus documents now yield
  `ListBlock` instead of plain paragraphs.
- DOCX robustness: comment/PI nodes in the body, missing `w:pgSz`/`w:pgMar`,
  malformed tables without `w:tblGrid`.
- Merged cells: one `w:tc` per merged cell, not one per covered column (a
  63-column header was repeated 63 times).
- Encrypted PDFs: explicit "password-protected" error instead of an empty message.

Known gaps, in priority order: borderless tables with only two rows, or with no
numbers/caption/bold header, are still missed (e.g. the Korean 2-row table);
multi-line table cells (wrapped continuation lines) still split a table into
fragments, because the continuation line lands outside the row's vertical span;
image-only PDFs need the OCR chain.

**Aligned tables beside a text column (2026-10-25).** A table in the right column
of a two-column page shares its row bands with the prose of the left column. Three
changes in `pdfreader.py` keep both intact:
- `_continues_table` groups rows across an intervening non-candidate band only when
  that band lies outside the previous row's horizontal span and the two rows share
  a median font size (±2 pt), so prose and page headings neither break nor join a
  table.
- `_split_at_gutters` drops page-column groups that are prose (`_is_prose`: median
  ≥4 words per cell) and only to the *left* of the table's column run; a wide gap
  inside a table (letters column, description column) never splits it.
- A split is used only when the remaining columns still pass the table guards and
  the page has text beside them (`_has_text_beside`), so a table alone on a page is
  never cut. Empty rows left by the removed prose are dropped.
Effect on the corpus: `multi-column.pdf` goes from 7 fragmented tables to 6 complete
ones (the Dataset table is 6×5, Tables 2/3/4 merge their continuation rows) and its
prose returns to the body; `pdf2image-memory-error-test-400p.pdf` is unchanged apart
from one TOC line that is now a heading instead of a one-row fake table. Two
regression tests in `test_textdoc.py` cover a table beside a text column and a wide
gap before a description column.

**Rotated margin text and numbered section headings (2026-10-25).** arXiv papers
carry a vertical `arXiv:...` stamp in the left margin. pdfminer splits rotated text
into one glyph per line; those glyphs shared a baseline with body lines, so a digit
could be absorbed as a bullet and a large glyph (`C`, `X`) passed the heading
threshold. `_collect_page` now keeps rotated lines (`LTChar.upright` false) apart
and drops them, unless the whole page is rotated (then they are the only text).
This also restores `text_left` to the real body margin, which had been 16pt and
inflated every list level to 5. Separately, a numbered non-bold line slightly
larger than the body (`1 Introduction`, 11pt on 10pt) is now a heading and no
longer merges into the following paragraph. Corpus effect: 9 baselines changed,
all removals of stamp junk; the two DPR papers gained their section headings
(`1 Introduction` … `8 Conclusion`) and lost the fake `C`/`X` headings. Two
regression tests in `test_textdoc.py` cover the stamp and the numbered heading.

**Compact PDF images (2026-10-25).** pdfminer exports most PDF images as raw
BMP: `layout-parser-paper.pdf` carried 40 MB of images, which became 53 MB of
Markdown once base64-encoded. `_compact_image` now re-encodes uncompressed
formats (BMP, TIFF) to lossless PNG at extraction time, so the IR, the DOCX and
the Markdown/HTML all shrink; already-compressed formats are kept untouched and
an unreadable stream falls back to the original bytes. Same document: 40 MB →
4.05 MB of image data, 53 MB → 5.4 MB of Markdown. The corpus baselines only
record image sizes, so they are unchanged; one regression test covers the
conversion and the two fallbacks.

**DOCX fidelity: links, images, header/footer, merges (2026-10-26).** The DOCX
reader used `paragraph.runs`, which **skips `w:hyperlink` children**: every link
display text silently disappeared from md/txt/html/docx. It now walks the
paragraph in document order (`_iter_runs`) and carries `Run.link` through every
writer: Markdown emits `[text](<url>)`, HTML an `<a>` (scheme-checked:
`http`/`https`/`mailto`/`tel`/relative/anchor only, so `javascript:` is dropped),
DOCX a real `w:hyperlink` with an external relationship. Inline `w:drawing`
images become `ImageBlock`s with their display size in points; `w:headerReference`
/ `w:footerReference` paragraphs land in `Document.headers`/`footers` and are
written back to the first section. Table merges are now structural: `w:gridSpan`
becomes `col_span`, `w:vMerge` restart/continue becomes `row_span` on the origin
cell (continuations no longer duplicate text), and `Table.text_grid()` produces
the compact logical grid used by md/txt and the corpus digest — one column per
cell that actually starts there, with merged text repeated down the covered rows.
`writers.py` sanitises CSS values and HTML attributes (`html.escape`), because a
font name comes from the source document. Fixed a reader bug where
`_paragraph_parts.flush()` cleared the shared run list before building the
paragraph. Corpus effect: 24 baselines changed, all improvements (recovered link
text, DOCX images that were absent, PDF cells in top-down order) and zero text
loss; `Bug54771b.docx` keeps its three empty rows (cells inside `w:sdt`).

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
  its temp dir; it added the `--pdf2docx` flag (removed with the structured engine, §21).
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
  (historical: the child process `Converter.exe --pdf2docx` ran; that path was removed in
  §21 and replaced by the in-process structured engine).
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
  normally present (required by python-docx). Same optional-dep pattern as HEIC/AVIF in `image.py`.
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
  conversion). lxml not duplicated (arrives via python-docx/pikepdf). Rebuilt
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

---

## 26. Structured document engine (2026-10-24) — implementation log

- **Diagnosis (evidence, not intuition).** A realistic two-page PDF was produced by
  LibreOffice from a python-docx fixture (title, `1. Summary` H1, `2. Figures` H2, a 3×3
  table, three bullets, page break, appendix). Old results: pdf→md was not offered at all;
  pdf→txt put the table cells in column order and the `2. Figures` heading after them;
  pdf→html emitted absolute-positioned divs; pdf→docx had every paragraph as `Normal`,
  Symbol bullets and split paragraphs.
- **New engine.** `converters/docmodel/` (IR, PDF reader, DOCX reader, writers) +
  `converters/textdoc.py` (`TextDocConverter`, priority 20). Removed
  `converter/converters/pdf_import.py` and the `--pdf2docx` child flag; OCR helpers moved
  to `converter/converters/_ocr.py`. Full description and routing in §21.
- **Verified manually on the probe PDF.** md: `# 1. Summary`, `## 2. Figures`, GFM table,
  three bullets. txt: `Region\tRevenue\tGrowth` in correct reading order. Structure docx:
  `Title`/`Heading 1`/`Heading 2`, `List Bullet`, real 3×3 table, Calibri 14 pt heading.
  Visual docx: `w:framePr` x/y on every printed line, page break between pages.
- **Tests.** New `tests/test_textdoc.py` (24 collected) covers routing/availability in
  FULL and PORTABLE, the OCR validate gate (mocked chain), a broken PDF, pure-IR units,
  and end-to-end assertions for md/txt/docx structure/visual/html/odt and docx→md GFM.
  Deleted `tests/test_pdf_import.py`; updated `test_phase2_matrix.py`,
  `test_document_converter.py`, `test_risks_13_to_17.py`, `test_robustness_risks.py`
  (risk 9 now imports `_ocr`), `test_mesh3d.py` comment, `test_phase2_converters.py`
  (PORTABLE pdf→txt now routes to `TextDocConverter`).
- **Packaging/licensing.** `python-docx>=1.1` added as a direct MIT dependency;
  `pdf-import` extra removed; `converter.spec` collects `docx` templates and no longer
  excludes/cites pdf2docx/fitz; `THIRD_PARTY_NOTICES.md` states no AGPL PDF engine is
  used. README/CLAUDE.md updated accordingly.
- **Proof:** `ruff check .` clean; full suite `428 passed, 20 xfailed` with the local
  corpus (2026-10-25; the xfails are corrupt/encrypted corpus files, not the empty
  corpus). The Windows exe was rebuilt on 2026-10-25 (see §18).
