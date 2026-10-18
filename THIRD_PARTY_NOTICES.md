# Third-Party Notices

Converter itself is licensed under the MIT License (see [`LICENSE`](LICENSE)).
It bundles and/or uses the third-party components listed below, each of which
remains under its own license. This file is included in the Windows release
archive so the notices travel with the binary.

Versions are indicative (the ones used for the reference build); a build may
differ slightly.

## Python packages bundled in the Windows release

| Component | License | Notes |
|-----------|---------|-------|
| Python runtime | PSF-2.0 | interpreter bundled by PyInstaller |
| **PySide6 / Qt 6** | **LGPL-3.0** (or GPL-2.0/3.0) | see the dynamic-linking note below |
| Pillow | MIT-CMU (HPND) | image conversions |
| pandas | BSD-3-Clause | data conversions |
| numpy | BSD-3-Clause | required by pandas |
| openpyxl | MIT | XLSX read/write |
| odfpy | Apache-2.0 **or** GPL-2.0+ | used here under Apache-2.0 |
| PyYAML | MIT | YAML |
| defusedxml | PSFL | safe XML parsing |
| pdfminer.six | MIT | pdf→txt/html |
| striprtf | BSD | rtf→txt |
| trimesh | MIT | 3D mesh conversions |
| ocrmypdf | MPL-2.0 | OCR orchestration (only if present at build time) |
| pikepdf | MPL-2.0 | required by ocrmypdf |
| img2pdf | LGPL-3.0 | required by ocrmypdf |

## Bundled command-line tools (separate executables)

If they were present on the build machine, these binaries are copied into the
bundle's `_internal/bin` and invoked as **separate subprocesses**. They are
**independent executables distributed alongside Converter, not linked into its
code** (mere aggregation); Converter only launches them. Their license texts are
shipped in the `licenses/` folder of the release archive.

### ffmpeg / ffprobe

| | |
|---|---|
| **Version** | 8.1.1 — "full build" |
| **Build/vendor** | www.gyan.dev (`ffmpeg version 8.1.1-full_build-www.gyan.dev`) |
| **License** | **GPL-3.0** — this build is configured with `--enable-gpl --enable-version3` |
| **Official project site** | <https://ffmpeg.org/> |
| **Source code** | <https://ffmpeg.org/download.html> · this version: <https://github.com/FFmpeg/FFmpeg/tree/n8.1.1> |
| **Binary build page** | <https://www.gyan.dev/ffmpeg/builds/> |
| **License texts shipped** | `licenses/ffmpeg-GPLv3.txt`, `licenses/ffmpeg-LGPLv2.1.txt` |

Upstream FFmpeg core is LGPL-2.1+; when built with `--enable-gpl` (as here) the
whole binary is covered by the GPL, and `--enable-version3` selects GPLv3.

### pandoc

| | |
|---|---|
| **Version** | 3.10 |
| **License** | **GPL-2.0-or-later** |
| **Official project site** | <https://pandoc.org/> |
| **Source code** | <https://github.com/jgm/pandoc> · this version: <https://github.com/jgm/pandoc/tree/3.10> |
| **Official binaries** | <https://github.com/jgm/pandoc/releases> · this version: <https://github.com/jgm/pandoc/releases/tag/3.10> |
| **License text shipped** | `licenses/pandoc-GPLv2.txt` |

## Qt / PySide6 (LGPL-3.0) — dynamic linking

PySide6 and the Qt 6 libraries are used under the **LGPL-3.0**. In the PyInstaller
**onedir** build the Qt libraries are shipped as separate `.dll` files next to the
executable and are **dynamically linked** (not statically linked into the app).
Under the LGPL you may:

- use, modify and redistribute Converter under its own (MIT) terms;
- **replace or relink** the bundled Qt/PySide6 libraries with a modified version
  of your own — simply swap the corresponding files in the `_internal` folder.

The full LGPL-3.0 text is available from the PySide6/Qt distribution
(<https://www.gnu.org/licenses/lgpl-3.0.html>) and from the project's own
`licenses/` metadata when installed via `pip`.

## External tools detected at runtime (NOT bundled)

Converter detects these on the user's system and uses them only if present;
they are **not** redistributed with Converter:

- **LibreOffice** (MPL-2.0) — office document conversions
- **Tesseract** (Apache-2.0) and **Ghostscript** (AGPL-3.0) — OCR for scanned PDFs

Users install these themselves; each is governed by its own license.

## Deliberately excluded (AGPL)

**pdf2docx** and its dependency **PyMuPDF (`fitz`), which is AGPL-3.0**, are
**not** included in the Windows release. They are excluded on purpose so the
distributed binary stays free of AGPL obligations (see `converter.spec`
`excludes`). As a result the PDF → DOCX/ODT/RTF conversions are **not available
in the release**; a user who wants them can install pdf2docx locally
(`pip install pdf2docx`).
