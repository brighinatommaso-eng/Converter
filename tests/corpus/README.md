# Document corpus (real-world regression set)

This folder is the measurement harness for the structured document engine
(`converter/converters/docmodel/`). It answers one question: *does a change make
real documents better, or just different?*

## Where do the files go?

Put the source documents in:

```
tests/corpus/local/
```

That folder is **gitignored**: real contracts/reports may be sensitive and must
never be committed. The baselines under `expected/` are gitignored too, because
they contain the extracted text of those same documents. You can also point the
harness at another folder:

```powershell
$env:CONVERTER_CORPUS_DIR = "D:\Documenti\corpus"
```

Supported inputs: `.pdf` and `.docx` (recursively). Subfolders are allowed; the
expected file mirrors the relative path.

## First run: create the baseline

```powershell
$env:CORPUS_UPDATE = "1"
python -m pytest -q tests/test_corpus.py
Remove-Item Env:CORPUS_UPDATE
```

This writes one JSON per document under `tests/corpus/expected/`, mirroring the
source path (`local/report.pdf` → `expected/report.pdf.json`). Files that cannot
be read and have no baseline are skipped in update mode (a corrupt file must not
abort the whole regeneration); if a baseline exists, reading must still succeed.

**Review those JSON files.** They are a snapshot of what the engine currently
produces, not automatically the truth. Where the engine is wrong (a heading
classified as body text, a table flattened into paragraphs, wrong reading order),
edit the JSON to the *desired* structure. From then on the test is a target, not
just a regression lock.

## Normal run

```powershell
python -m pytest -q tests/test_corpus.py
```

When a document changes structure, the failure shows a unified diff of the
expected vs actual JSON.

A document that cannot be read and has **no** baseline is reported as `xfail`
(the file is corrupt, encrypted, or not supported yet). If a baseline exists and
reading fails, the test fails: a document that used to work must keep working.

## What is compared

- document title;
- block order (reading order);
- paragraphs: kind (`title` / `heading` / `body`), heading level, normalized text;
- lists: ordered flag, item levels and text (markers excluded);
- tables: full cell grid;
- images: width/height in points (the binary is not stored);
- page breaks.

Formatting (fonts, colours, sizes) is intentionally **not** in the digest: it is
covered by the unit tests and would make the corpus noisy. The goal here is
structure and reading order.

## Adding a case

1. Copy the real file into `tests/corpus/local/` (or the folder in
   `CONVERTER_CORPUS_DIR`).
2. Run with `CORPUS_UPDATE=1`.
3. Open the generated JSON and correct anything the engine got wrong.
4. Run normally; it must pass.
5. If the file is shareable, consider adding a reduced/synthetic version to the
   committed tests (`tests/test_textdoc.py`) so CI covers the same failure.
