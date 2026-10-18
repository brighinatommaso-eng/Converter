# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Converter — an offline desktop app (PySide6) that converts files between formats and is **honest about quality**: every conversion yields a `QualityVerdict` (lossless / lossy / unsupported) shown as a badge with a reason. Never advertise or silently perform a conversion that loses data or can't actually run without saying so — this principle drives the verdict/availability logic.

## Architecture

- `core/` — framework, no GUI and no I/O backends. Key pieces:
  - `pipeline.run_job()` is the **single execution path** for every conversion (GUI and CLI both go through it). It resolves the converter, checks availability, then converts — and never raises (failures come back as `ConversionResult(ok=False)`).
  - Converters self-register via the `@register` decorator (`core/registry.py`); the GUI only talks to the registry, never to converter classes by name. Adding a format = add/extend a converter, no UI changes.
  - `Converter.verdict()`, `format_available()`, and `pair_available()` (src→dst direction) drive what the UI offers vs. greys out.
- `converters/` — one module per category (image/audio/video/data/document/**model3d**), each `@register`-ed.
  - `mesh3d.py` (trimesh, MIT, pure-Python) converts 3D meshes in-process like `image.py` (no external tool, works in PORTABLE). Inputs obj/stl/ply/glb/gltf/3mf; outputs obj/stl/ply/glb/gltf (**3mf is input-only**). 3mf import is gated on the optional `lxml` (`format_available("3mf")` greys out without it; lxml normally rides in via pdf2docx). A multi-object scene going to a single-mesh target (stl/obj/ply) is flattened and the verdict is **downgraded to lossy at runtime** (mirrors `image.py`'s multi-frame override). Detection: glb/ply via magic bytes, 3mf via ZIP inspection, gltf via JSON structure (distinguished from `data` json by requiring `asset` + meshes/scenes/nodes), ASCII STL via `solid`+`facet normal` sniff — **binary STL has no magic and falls back to the extension tiebreaker (0.3)**, the one place content-detection can't fully apply.
  - `registry.resolve(src,dst)`, among converters that claim the pair ordered by **descending `priority`**, returns the first whose `_pair_fully_available` holds (`available().ok` + `format_available(src)` + `format_available(dst)` + `pair_available`); if none qualify it returns the highest-priority claimant (so `run_job`'s error message stays sensible). This mirrors `targets_for`, which prefers the available converter — otherwise the UI could offer a pair via an available low-priority converter while `resolve` routed it to an unavailable high-priority one. `MarkupConverter` (pandoc) sets `priority=10` to win the office→markup pairs it explicitly overrides (`_OFFICE_OVERRIDE_OUT` in `markup.py`, currently `{md, txt}` — do **not** add `html` back there: docx/odt→html must stay on `DocumentConverter`/LibreOffice, which is the only path that preserves cell colors/shading via inline CSS; pandoc silently drops them).
- `gui/` — PySide6 UI; contains **zero** conversion logic.
- External tools are detected at runtime, never assumed on PATH: `deps.find_ffmpeg/find_ffprobe/find_soffice` also search standard install dirs and a bundled `bin/`.
- **Engine mode** (`core/engine.py`): session-persisted **FULL vs PORTABLE**, toggled live from the toolbar. Drives which converter pairs are offered — FULL requires LibreOffice for faithful Office conversions; PORTABLE falls back to the pure-Python paths (pdf/rtf/pandas/Pillow/ffmpeg). Same "honest availability" principle as the verdict system: a pair unavailable in the current mode shows greyed out with a reason, never silently downgrades.

## Testing

- Run: `python -m pytest -q` (config in `pyproject.toml`, `testpaths=["tests"]`).
- GUI tests set `QT_QPA_PLATFORM=offscreen` — keep that when adding GUI tests, or they need a display.
- Audio/video tests `skipif` ffmpeg/ffprobe absent; document tests `skipif` LibreOffice absent; one pdf-import test `skipif` the OCR chain (ocrmypdf+Ghostscript+Tesseract) absent; `test_mesh3d.py` `importorskip`s trimesh. A passing run may be skipping these — check the skip count. (Full run in a complete dev env: **236 passed, 0 skipped**, measured 2026-07-04.)
- To verify GUI visuals truthfully (icons, focus rings, real fonts), render on the **native** Qt platform, not offscreen (offscreen lacks window activation and font glyphs).

## Linting

- ruff (`pyproject.toml`, `[tool.ruff]`): line-length 120, target py310, `select = ["F","I","W","B","UP"]` — deliberately does **not** enable whitespace/line-length rules, to avoid fighting the codebase's hand-aligned formatting style.
- A `PostToolUse` hook (`.claude/settings.local.json`) already runs `ruff check --fix --unfixable F401` automatically after every Write/Edit on a `.py` file — no need to manually re-run ruff after editing.

## Subprocess gotcha (important)

Spawn **every external tool** (soffice, ffmpeg, ffprobe, ocrmypdf) with `deps.clean_subprocess_env()`. A PyInstaller-frozen exe injects bundle dirs into `PATH`; without cleaning, LibreOffice's own Python loads our `python313.dll` and export filters fail ("no export filter for docx"). `_libreoffice.py` and `_ffmpeg.py` already do this — don't bypass it. `pdf_import.py` runs OCRmyPDF through `_libreoffice.run_with_timeout` (same env cleaning + hard timeout); OCRmyPDF locates Ghostscript via the Windows registry, so the cleaned PATH does not break it. `pdf_import._pdf_to_docx` also runs pdf2docx as a **child process** (`converter_app --pdf2docx <src> <dst>`; frozen: `[sys.executable, "--pdf2docx", …]`, from source: `[sys.executable, converter_app.py, "--pdf2docx", …]`) through `run_with_timeout` (hard 300 s timeout + tree kill + clean env); it falls back to in-process pdf2docx only when the command can't be built.

`run_ffmpeg` also has a watchdog thread (kills ffmpeg on cancel-while-blocked or after 120 s with no `-progress` output) and `probe_duration` passes `timeout=15`; ffmpeg/pandoc/pdfminer remove their partial output on failure/cancel.

## Packaging (PyInstaller)

- Build: `pyinstaller converter.spec --noconfirm --clean` (onedir; see `/package` skill for the full build+verify flow).
- **Kill any running `Converter.exe` before rebuilding** — a live process locks files under `_internal`, and `--clean` fails with `PermissionError: [WinError 32]` trying to remove `dist/Converter`.
- Entry point is **`converter_app.py`** (absolute import). Do **not** point PyInstaller at `converter/__main__.py` — its relative imports have no package context when frozen, which silently drops Qt from the bundle. `python -m converter` still works via `__main__.py`.
- ffmpeg/ffprobe bundle into `_internal/bin`; assets into `_internal/assets`. Config/presets/history always live in `%APPDATA%\Converter`, never the bundle.
- **Verification tripwires** (see `/package` skill): `_internal/torch` must **NOT** exist (was accidentally bundled once at 4GB); total bundle size is ≈1.07 GB (`du -sh dist/Converter` → 1.1 GB, measured 2026-07-04; `bin/` ffmpeg+ffprobe+pandoc 655 MB + cv2/PyMuPDF and the rest of the pdf2docx dependency tree ≈165 MB) — multi-GB still indicates a bundling error. (`trimesh` adds only ~300 KB.)
- `trimesh` lazy-imports its exchange loaders (stl/gltf/ply/obj/threemf), so the spec collects them explicitly: `hiddenimports += collect_submodules("trimesh")` and `datas += collect_data_files("trimesh")` (its `resources/` schema+templates). Without these the exe imports trimesh but fails every 3D conversion. `lxml` (for 3mf) is **not** added explicitly — it already rides in via pdf2docx→python-docx→lxml and pikepdf.
- The shipped exe has a built-in **`--selftest <path>` CLI flag** — runs one conversion per category via `run_job` with Python stripped from PATH, writes PASS/FAIL + per-category results to a file. Use it as a real smoke test of the frozen build, not just a launch check. **It does not yet cover model3d**; the frozen 3D path is verified with `--convert <src> <dst> <out> <report>` instead (proven: stl→glb and 3mf→stl, the latter exercising the bundled threemf loader + lxml).
- The frozen exe **re-invokes itself** for PDF import: `pdf_import._pdf_to_docx` spawns `Converter.exe --pdf2docx <src> <dst>` as a child process. So `pdf2docx`/`fitz` must be importable in the child (they are in `hiddenimports`); a pdf→docx run from the exe (or `--selftest`, which exercises `pdf writer-targets`) verifies the child path.

## Conventions

- Code comments are written in **Italian** (match the surrounding style when editing).
- Not a git repository.
- `gui/theme.py` QSS font-family names must match the loaded font family **exactly** — a mismatch silently falls back to Segoe UI instead of erroring.
