# Stardance devlog caption

## English (recommended)

I rebuilt Converter's PDF → DOCX conversion so it preserves document structure instead of dumping raw text.

Now one paper comes out with 9 real headings, 6 intact tables, and no ghost lines from rotated arXiv stamps.

The hardest part was identifying rotated margin text and separating numbered headings from body paragraphs.

I also re-encoded PDF images to lossless PNG, cutting the Markdown output from 53 MB to 5.4 MB.

No AI: a deterministic, offline engine powered by pdfminer.six and python-docx.

## Italiano

Ho riscritto la conversione PDF → DOCX di Converter per preservare la struttura del documento invece di svuotarne il testo.

Ora un paper produce 9 titoli reali, 6 tabelle intere e zero righe fantasma dai timbri arXiv ruotati.

La parte più difficile è stata riconoscere il testo ruotato nel margine e separare i titoli numerati dal testo normale.

Ho anche ricodificato le immagini PDF in PNG lossless: il Markdown è passato da 53 MB a 5,4 MB.

Nessuna AI: un motore deterministico e offline basato su pdfminer.six e python-docx.
