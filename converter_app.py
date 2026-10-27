"""Entry point per il bundle PyInstaller.

PyInstaller esegue lo script di ingresso SENZA contesto di package, quindi gli
import relativi di converter/__main__.py (`from .gui.app import run`) falliscono
con "attempted relative import with no known parent package". Qui usiamo un
import ASSOLUTO del package: così l'analisi di PyInstaller segue correttamente
converter.gui.app (e quindi PySide6 e i converter). `python -m converter` continua
a funzionare tramite converter/__main__.py.

`--selftest <file>`: esegue una conversione per categoria via run_job (senza GUI)
e scrive l'esito nel file indicato. Serve a verificare l'eseguibile impacchettato
(windowed, senza console) da una cartella pulita.

`--convert <src> <dst_format> <out> [report]`: UNA conversione reale via run_job,
senza GUI — strumento di verifica del bundle (non è la CLI di Fase 3). L'esito
va nel file `report` (l'exe è windowed: senza console lo stdout si perde).
"""

import sys


def _bundle_has_no_agpl_pdf_engine() -> bool:
    """Tripwire: il bundle non deve contenere i motori PDF AGPL.

    PyMuPDF/fitz, pdf2docx e img2pdf possono entrare come dipendenze opzionali
    di un altro pacchetto (era il caso di img2pdf -> fitz). Nel sorgente sono
    installati per altri usi, quindi il controllo vale solo da congelato.
    """
    if not getattr(sys, "frozen", False):
        return True
    import importlib.util
    from pathlib import Path

    names = ("fitz", "pymupdf", "pdf2docx", "img2pdf", "ocrmypdf", "pikepdf")
    for name in names:
        try:
            if importlib.util.find_spec(name) is not None:
                return False
        except (ImportError, ValueError, AttributeError):
            pass
    base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return not any((base / name).exists() for name in names)


def _selftest(out_path: str | None) -> int:
    import shutil
    import subprocess
    import tempfile
    from pathlib import Path

    import converter.converters  # noqa: F401  (registra i converter)
    from converter.core import engine
    from converter.core.deps import clean_subprocess_env, find_ffmpeg, find_soffice, has_module
    from converter.core.job import ConversionJob
    from converter.core.pipeline import run_job

    _NW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    d = Path(tempfile.mkdtemp())
    ff = find_ffmpeg()
    results: dict[str, object] = {}

    # Licenze: il PDF passa solo da pdfminer.six (BSD). Un residuo AGPL nel
    # bundle è un errore di distribuzione, non un dettaglio del singolo test.
    results["bundle free of AGPL PDF engines"] = _bundle_has_no_agpl_pdf_engine()

    def gen_media(args, dst):
        # env pulito + niente finestra console: stessa disciplina dei tool esterni
        # nel frozen exe (altrimenti ffmpeg può caricare le NOSTRE dll dal bundle).
        subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-y", *args, str(dst)],
                       check=True, env=clean_subprocess_env(), creationflags=_NW)

    def make_min_pdf(path: Path, text: str = "Hello PDF from the selftest") -> None:
        """PDF minimo ma valido (xref con offset corretti): da' a pdfminer del testo
        da estrarre senza dipendenze esterne."""
        objs = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        ]
        stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode("latin-1") + b") Tj ET"
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                    + stream + b"\nendstream")
        objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
        xref_pos = len(out)
        out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
                b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF")
        path.write_bytes(bytes(out))

    # image: png -> jpg (Pillow, in-bundle)
    try:
        from PIL import Image
        src = d / "a.png"
        Image.new("RGB", (16, 16), (20, 120, 200)).save(src)
        results["image png->jpg"] = run_job(
            ConversionJob(src, "png", "jpg", d / "a.jpg", options={"quality": 90})).ok
    except Exception as e:  # noqa: BLE001
        results["image png->jpg"] = f"ERR {type(e).__name__}: {e}"

    # data: csv -> json (pandas, in-bundle)
    try:
        src = d / "a.csv"
        src.write_text("name,age\nAlice,1\nBob,2\n", encoding="utf-8")
        results["data csv->json"] = run_job(ConversionJob(src, "csv", "json", d / "a.json")).ok
    except Exception as e:  # noqa: BLE001
        results["data csv->json"] = f"ERR {type(e).__name__}: {e}"

    # audio: wav -> flac (ffmpeg in-bundle)
    try:
        if ff:
            src = d / "a.wav"
            gen_media(["-f", "lavfi", "-i", "sine=frequency=440:duration=1"], src)
            results["audio wav->flac"] = run_job(ConversionJob(src, "wav", "flac", d / "a.flac")).ok
        else:
            results["audio wav->flac"] = "SKIP no ffmpeg"
    except Exception as e:  # noqa: BLE001
        results["audio wav->flac"] = f"ERR {type(e).__name__}: {e}"

    # video: mp4 -> mkv (ffmpeg in-bundle)
    try:
        if ff:
            src = d / "a.mp4"
            gen_media(["-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=15"], src)
            results["video mp4->mkv"] = run_job(ConversionJob(src, "mp4", "mkv", d / "a.mkv")).ok
        else:
            results["video mp4->mkv"] = "SKIP no ffmpeg"
    except Exception as e:  # noqa: BLE001
        results["video mp4->mkv"] = f"ERR {type(e).__name__}: {e}"

    # --- PORTABLE: motori puri/bundlati, NIENTE LibreOffice --------------------
    # Il cuore della modalità Portable: queste conversioni devono riuscire da sole
    # (pdfminer+python-docx, motore markup con markdown-it/odfpy, striprtf: tutto in-bundle).
    # Le eseguiamo in PORTABLE proprio per provare che non passano da LibreOffice.
    engine.set_mode("PORTABLE")
    has_pdfminer = has_module("pdfminer")
    has_docx = has_module("docx")
    has_striprtf = has_module("striprtf")

    # pdf -> txt / md / docx / html (motore strutturale: pdfminer + python-docx)
    if has_pdfminer and has_docx:
        def ok_or_error(result) -> bool | str:
            return True if result.ok else f"FAIL {result.error}"

        try:
            pdf = d / "p.pdf"
            # > MIN_TEXT_CHARS (10) caratteri: altrimenti il gate OCR lo scambia
            # per una scansione e il selftest senza OCR fallisce per finta.
            make_min_pdf(pdf, "Hello PDF from the selftest")
            r = run_job(ConversionJob(pdf, "pdf", "txt", d / "p.txt"))
            text = ((d / "p.txt").read_text(encoding="utf-8")
                    if (d / "p.txt").exists() else "")
            results["portable pdf->txt"] = (
                True if r.ok and "Hello" in text
                else f"FAIL {r.error} text={text[:80]!r}")
            results["portable pdf->md"] = ok_or_error(run_job(
                ConversionJob(pdf, "pdf", "md", d / "p.md")))
            results["portable pdf->docx"] = ok_or_error(run_job(
                ConversionJob(pdf, "pdf", "docx", d / "p.docx")))
            results["portable pdf->html"] = ok_or_error(run_job(
                ConversionJob(pdf, "pdf", "html", d / "p.html")))
        except Exception as e:  # noqa: BLE001
            results["portable pdf->txt"] = f"ERR {type(e).__name__}: {e}"
            for key in ("portable pdf->md", "portable pdf->docx", "portable pdf->html"):
                results.setdefault(key, results["portable pdf->txt"])
    else:
        for key in ("portable pdf->txt", "portable pdf->md", "portable pdf->docx",
                    "portable pdf->html"):
            results[key] = "SKIP no pdfminer/python-docx"

    # Motore markup in-process (ha sostituito pandoc): md → tutti i target, e
    # ritorno da html/epub/odt. Controlla il contenuto, non solo ok=True: un
    # modulo mancante nel bundle (markdown_it, odf) farebbe fallire qui.
    results.update(_selftest_markup(d))
    results.update(_selftest_layout_ai(d))

    # rtf -> txt (striprtf)
    if has_striprtf:
        try:
            rtf = d / "r.rtf"
            rtf.write_text(r"{\rtf1\ansi Hello \b bold\b0 world}", encoding="utf-8")
            r = run_job(ConversionJob(rtf, "rtf", "txt", d / "r.txt"))
            results["portable rtf->txt"] = (
                r.ok and "Hello" in (d / "r.txt").read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            results["portable rtf->txt"] = f"ERR {type(e).__name__}: {e}"
    else:
        results["portable rtf->txt"] = "SKIP no striprtf"

    # In PORTABLE i target office-fedeli devono restare GREYED (onestà della modalità).
    try:
        from converter.core.registry import targets_for
        tg = {t.dst_format: t for t in targets_for("docx")}
        results["portable office targets greyed"] = bool(
            tg.get("odt") and not tg["odt"].available
            and tg.get("pdf") and not tg["pdf"].available)
    except Exception as e:  # noqa: BLE001
        results["portable office targets greyed"] = f"ERR {type(e).__name__}: {e}"

    # --- FULL: document (LibreOffice; saltato se assente). Copre i FILTRI DI EXPORT,
    # che sono la classe che falliva nel bundle per contaminazione dell'ambiente.
    engine.set_mode("FULL")
    if find_soffice():
        txt = d / "a.txt"
        txt.write_text("hello from selftest", encoding="utf-8")
        try:
            results["document txt->pdf"] = run_job(ConversionJob(txt, "txt", "pdf", d / "a.pdf")).ok
        except Exception as e:  # noqa: BLE001
            results["document txt->pdf"] = f"ERR {type(e).__name__}: {e}"
        # Filtro di export DOCX di LibreOffice: esattamente quello che dava 'no export
        # filter' nel bundle. txt->docx ora è in-process (motore markup), quindi qui
        # si passa da odt->docx, che resta a LibreOffice: l'odt lo scrive il motore.
        key = "document odt->docx (LibreOffice export filter)"
        try:
            from converter.core.registry import resolve

            odt = d / "a.odt"
            run_job(ConversionJob(txt, "txt", "odt", odt))
            routed = type(resolve("odt", "docx")).__name__
            if routed != "DocumentConverter":
                results[key] = f"FAIL odt->docx routed to {routed}"
            elif not odt.exists():
                results[key] = "FAIL txt->odt produced nothing"
            else:
                r = run_job(ConversionJob(odt, "odt", "docx", d / "a.docx"))
                results[key] = True if r.ok else f"FAIL {r.error}"
        except Exception as e:  # noqa: BLE001
            results[key] = f"ERR {type(e).__name__}: {e}"
        # Input BINARIO (docx) + export filter (odt): import+export di un
        # documento Writer. (NB: pdf->docx NON è testato qui perché LibreOffice
        # non sa convertire un PDF in formati Writer — limite suo, non del bundle.)
        try:
            docx = d / "a.docx"
            if docx.exists():
                results["document docx->odt"] = run_job(ConversionJob(docx, "docx", "odt", d / "c.odt")).ok
            else:
                results["document docx->odt"] = "SKIP no docx produced"
        except Exception as e:  # noqa: BLE001
            results["document docx->odt"] = f"ERR {type(e).__name__}: {e}"
        # PDF come sorgente: il motore strutturale (pdfminer + python-docx, MIT)
        # copre docx/md/txt/html in ENTRAMBE le modalità; odt/rtf aggiungono il
        # salto LibreOffice, quindi restano greyed in PORTABLE.
        try:
            from converter.core.registry import targets_for
            pdf = d / "a.pdf"
            results["document pdf->html"] = (
                run_job(ConversionJob(pdf, "pdf", "html", d / "a.html")).ok
                if pdf.exists() else "SKIP no pdf produced")
            tg = {t.dst_format: t for t in targets_for("pdf")}
            writer_ok = all(dst in tg and tg[dst].available
                            for dst in ("docx", "md", "txt", "html"))
            results["pdf struct writer-targets"] = bool(writer_ok)
        except Exception as e:  # noqa: BLE001
            results["pdf struct writer-targets"] = f"ERR {type(e).__name__}: {e}"
    else:
        for k in ("document txt->pdf", "document odt->docx (LibreOffice export filter)", "document docx->odt",
                  "document pdf->html", "pdf struct writer-targets"):
            results[k] = "SKIP no LibreOffice"

    results.update(_selftest_honesty_checks(d, ff, gen_media))

    def good(v) -> bool:
        return v is True or (isinstance(v, str) and v.startswith("SKIP"))

    ok = all(good(v) for v in results.values())
    lines = [f"{k}: {v}" for k, v in results.items()]
    lines.append("SELFTEST " + ("PASS" if ok else "FAIL"))
    text = "\n".join(lines) + "\n"
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
    if sys.stdout:  # nell'exe windowed sys.stdout può essere None
        sys.stdout.write(text)
    shutil.rmtree(d, ignore_errors=True)  # non lasciare la tmpdir a disco
    return 0 if ok else 1


def _article_pdf(path) -> None:
    """Pagina "da articolo" (titolo, sezione, paragrafi giustificati) per il modello AI:
    una riga sola non basta, il modello deve vedere blocchi di testo veri."""
    words = ("the method reads every page and rebuilds titles paragraphs tables and "
             "figures in the order a person would read them").split()
    parts = ["BT /F1 22 Tf 72 720 Td (Layout analysis selftest) Tj ET",
             "BT /F1 14 Tf 72 680 Td (1 Introduction) Tj ET"]
    y = 655
    for paragraph in range(3):
        for line in range(6):
            text = " ".join(words[(line * 5 + paragraph) % 7:][:11])
            parts.append(f"BT /F1 11 Tf 72 {y} Td ({text}) Tj ET")
            y -= 14
        y -= 12
    stream = "\n".join(parts).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref_pos = len(out)
    out += b"xref\n0 " + str(len(objs) + 1).encode() + b"\n0000000000 65535 f \n"
    out += b"".join(f"{off:010d} 00000 n \n".encode() for off in offsets)
    out += (b"trailer\n<< /Size " + str(len(objs) + 1).encode() + b" /Root 1 0 R >>\n"
            b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF")
    path.write_bytes(bytes(out))


def _selftest_layout_ai(d) -> dict[str, object]:
    """Analisi AI del layout PDF nell'exe: modello presente, onnxruntime e PDFium
    caricati (librerie native nel bundle) e una pagina davvero analizzata.

    Nell'exe il modello DEVE esserci (la build lo include); da sorgente, senza
    modello scaricato, i controlli sono SKIP."""
    from converter.converters.docmodel import layout_ai, read_pdf

    results: dict[str, object] = {}
    reason = layout_ai.unavailable_reason()
    if reason is not None and not getattr(sys, "frozen", False):
        results["pdf layout AI model loads"] = f"SKIP {reason}"
        results["pdf layout AI analyses a page"] = "SKIP no model"
        return results
    try:
        results["pdf layout AI model loads"] = (
            True if layout_ai.get_analyzer() is not None
            else f"FAIL {layout_ai.unavailable_reason()}")
    except Exception as e:  # noqa: BLE001
        results["pdf layout AI model loads"] = f"ERR {type(e).__name__}: {e}"
    try:
        pdf = d / "article.pdf"
        _article_pdf(pdf)
        document = read_pdf(pdf)
        results["pdf layout AI analyses a page"] = (
            True if document.ai_pages == 1 and not document.ai_unavailable
            else f"FAIL ai_pages={document.ai_pages} {document.ai_unavailable}")
    except Exception as e:  # noqa: BLE001
        results["pdf layout AI analyses a page"] = f"ERR {type(e).__name__}: {e}"
    return results


def _selftest_markup(d) -> dict[str, object]:
    """Motore markup in-process (ha sostituito pandoc): ogni scrittore e i lettori
    di ritorno, controllando il contenuto prodotto e non solo ok=True."""
    import zipfile

    from converter.core.job import ConversionJob
    from converter.core.pipeline import run_job

    out: dict[str, object] = {}
    md = d / "m.md"
    md.write_text("# Heading\n\nBody **bold** and `code`.\n\n- one\n- two\n\n"
                  "| a | b |\n|---|---|\n| 1 | 2 |\n", encoding="utf-8")

    def run(src, src_fmt, dst_fmt, name, check):
        try:
            target = d / name
            result = run_job(ConversionJob(src, src_fmt, dst_fmt, target))
            if not result.ok:
                return f"FAIL {result.error}"
            verdict = check(target)
            return True if verdict is True else f"FAIL {verdict}"
        except Exception as e:  # noqa: BLE001
            return f"ERR {type(e).__name__}: {e}"

    def html_ok(path):
        text = path.read_text(encoding="utf-8")
        return "<h1>Heading</h1>" in text and "<strong>bold</strong>" in text or text[:200]

    def docx_ok(path):
        from docx import Document

        styles = [p.style.name for p in Document(str(path)).paragraphs if p.text.strip()]
        return "Heading 1" in styles and "List Bullet" in styles or styles

    def odt_ok(path):
        with zipfile.ZipFile(path) as z:
            content = z.read("content.xml").decode("utf-8")
        return "text:h" in content and "table:table" in content or content[:200]

    def rtf_ok(path):
        text = path.read_text(encoding="ascii")
        return text.startswith("{\\rtf1") and "Heading" in text or text[:80]

    def epub_ok(path):
        with zipfile.ZipFile(path) as z:
            first = z.infolist()[0]
            ok = first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED
            return ok and "OEBPS/content.opf" in z.namelist() or z.namelist()[:4]

    out["portable md->html"] = run(md, "md", "html", "m.html", html_ok)
    out["portable md->docx"] = run(md, "md", "docx", "m.docx", docx_ok)
    out["portable md->odt"] = run(md, "md", "odt", "m.odt", odt_ok)
    out["portable md->rtf"] = run(md, "md", "rtf", "m.rtf", rtf_ok)
    out["portable md->epub"] = run(md, "md", "epub", "m.epub", epub_ok)
    out["portable epub->md (read back)"] = run(
        d / "m.epub", "epub", "md", "back.md",
        lambda p: "# Heading" in p.read_text(encoding="utf-8") or p.read_text(encoding="utf-8")[:120])
    out["portable odt->txt (read back)"] = run(
        d / "m.odt", "odt", "txt", "back.txt",
        lambda p: "Body bold and code." in p.read_text(encoding="utf-8") or p.read_text(encoding="utf-8")[:120])
    return out


def _selftest_honesty_checks(d, ff, gen_media) -> dict[str, object]:
    """Controlli del selftest sulle correzioni della review 2026-10-26.

    Ogni voce verifica un comportamento che nel bundle dipende da librerie e
    binari VERI (trimesh, Pillow, ffmpeg 8.x su Windows): un test in sorgente
    non prova che il frozen faccia lo stesso. True = ok, "FAIL ..." altrimenti,
    "SKIP ..." se manca il prerequisito (ffmpeg, trimesh)."""
    import subprocess
    from pathlib import Path

    from converter.core.deps import clean_subprocess_env, find_ffprobe, has_module
    from converter.core.detection import detect
    from converter.core.job import ConversionJob
    from converter.core.pipeline import run_job

    out: dict[str, object] = {}
    work = Path(d) / "honesty"
    work.mkdir(exist_ok=True)

    def check(name, fn) -> None:
        try:
            out[name] = fn()
        except Exception as exc:  # noqa: BLE001  il selftest non deve mai sollevare
            out[name] = f"ERR {type(exc).__name__}: {exc}"

    # --- 3D: il bundle deve contenere i loader/exporter lazy di trimesh ---------
    def mesh_roundtrip():
        if not has_module("trimesh"):
            return "SKIP no trimesh"
        import trimesh

        src = work / "box.stl"
        trimesh.creation.box().export(str(src))
        folder = work / "mesh_out"
        folder.mkdir(exist_ok=True)
        glb = run_job(ConversionJob(src, "stl", "glb", folder / "box.glb"))
        if not glb.ok:
            return f"FAIL stl->glb {glb.error}"
        for name in ("a.gltf", "b.gltf"):
            result = run_job(ConversionJob(src, "stl", "gltf", folder / name))
            if not result.ok:
                return f"FAIL stl->gltf {result.error}"
        names = sorted(p.name for p in folder.iterdir())
        if names != ["a.gltf", "b.gltf", "box.glb"]:
            return f"FAIL side files written next to glTF: {names}"
        return True

    check("model3d stl->glb/gltf (self-contained)", mesh_roundtrip)

    # --- Dati: CSV europeo con ';' -----------------------------------------------
    def semicolon_csv():
        import pandas as pd

        src = work / "eu.csv"
        src.write_text("nome;prezzo\nmela;1,50\n", encoding="cp1252")
        target = work / "eu.xlsx"
        result = run_job(ConversionJob(src, "csv", "xlsx", target))
        if not result.ok:
            return f"FAIL {result.error}"
        columns = list(pd.read_excel(target, dtype=str).columns)
        return True if columns == ["nome", "prezzo"] else f"FAIL columns={columns}"

    check("data csv(;)->xlsx keeps columns", semicolon_csv)

    # --- txt->md in-process (escape riga per riga, non il motore markup) -----------
    def text_to_md():
        src = work / "note.txt"
        src.write_text("# not a heading\nline two\n", encoding="utf-8")
        target = work / "note.md"
        result = run_job(ConversionJob(src, "txt", "md", target))
        if not (result.ok and result.verdict.is_lossless):
            return f"FAIL {result.error or result.verdict.reason}"
        text = target.read_text(encoding="utf-8")
        return True if text.startswith("\\# not a heading\\\n") else f"FAIL {text!r}"

    check("document txt->md escaped, lossless", text_to_md)

    # --- Rilevamento testo-contro-testo e BMP -----------------------------------
    def detection():
        readme = work / "README.md"
        readme.write_text("# Title\n\nAuthor: Tom\n\n- one\n", encoding="utf-8")
        bmi = work / "bmi.csv"
        bmi.write_text("BMI,age\n22.5,30\n", encoding="utf-8")
        found = (detect(readme).format_id, detect(bmi).format_id)
        return True if found == ("md", "csv") else f"FAIL detected {found}"

    check("detection md/csv not yaml/bmp", detection)

    # --- Audio: 24 bit resta 24 bit sotto LOSSLESS -------------------------------
    def audio_depth():
        if not ff or not find_ffprobe():
            return "SKIP no ffmpeg/ffprobe"
        src = work / "s24.wav"
        gen_media(["-f", "lavfi", "-i", "sine=d=0.3", "-c:a", "pcm_s24le"], src)
        target = work / "s24.flac"
        result = run_job(ConversionJob(src, "wav", "flac", target))
        if not (result.ok and result.verdict.is_lossless):
            return f"FAIL {result.error or result.verdict.reason}"
        probe = subprocess.run(
            [find_ffprobe(), "-v", "quiet", "-select_streams", "a:0", "-show_entries",
             "stream=bits_per_raw_sample", "-of", "csv=p=0", str(target)],
            capture_output=True, text=True, env=clean_subprocess_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=15)
        bits = probe.stdout.strip()
        return True if bits == "24" else f"FAIL bits={bits!r}"

    check("audio wav24->flac keeps 24-bit", audio_depth)

    # --- Annulla: un job già annullato non parte ---------------------------------
    def cancel():
        from PIL import Image

        src = work / "c.png"
        Image.new("RGB", (4, 4)).save(src)
        target = work / "c.jpg"
        job = ConversionJob(src, "png", "jpg", target)
        job.cancel.cancel()
        result = run_job(job)
        return True if result.cancelled and not target.exists() else f"FAIL {result}"

    check("cancel skips a queued job", cancel)

    # --- Licenze GPL accanto all'exe (solo da congelato) -------------------------
    def licenses():
        if not getattr(sys, "frozen", False):
            return "SKIP not frozen"
        root = Path(sys.executable).parent
        needed = ["LICENSE", "THIRD_PARTY_NOTICES.md", "licenses/ffmpeg-GPLv3.txt",
                  "licenses/Qt-PySide6-LGPLv3.txt"]
        missing = [name for name in needed if not (root / name).is_file()]
        return True if not missing else f"FAIL missing {missing}"

    check("license texts next to exe", licenses)

    # --- Bundle snello: le dipendenze opzionali escluse non devono esserci ------
    def no_unused_modules():
        if not getattr(sys, "frozen", False):
            return "SKIP not frozen"
        import importlib.util

        found = [name for name in _UNUSED_OPTIONAL_MODULES
                 if importlib.util.find_spec(name) is not None]
        return True if not found else f"FAIL bundled anyway: {found}"

    check("bundle free of unused optional modules", no_unused_modules)

    # --- Icona della finestra/taskbar: Qt deve riuscire a caricarla davvero ------
    def app_icon():
        # Sul file .exe l'icona la incorpora PyInstaller; quella di finestra e
        # taskbar la carica Qt a runtime: se è vuota Windows mostra quella generica.
        # QApplication (non solo QGuiApplication): il controllo dopo crea dei widget.
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([sys.argv[0]])  # noqa: F841
        from converter.gui.app import _app_icon

        icon = _app_icon()
        sizes = sorted({size.width() for size in icon.availableSizes()})
        if icon.isNull() or not {16, 32, 48} <= set(sizes):
            return f"FAIL icon sizes={sizes}"
        return True

    check("window/taskbar icon loads", app_icon)

    # --- La finestra principale si costruisce col Qt ridotto del bundle ---------
    def main_window():
        # Il bundle toglie i pezzi di Qt che l'app non usa (Quick/Qml/Pdf, tastiera
        # virtuale, traduzioni, OpenGL software): la finestra vera deve nascere lo stesso.
        from PySide6.QtWidgets import QApplication

        _app = QApplication.instance() or QApplication([sys.argv[0]])  # noqa: F841
        from converter.gui.main_window import MainWindow
        from converter.gui.theme import apply_theme

        apply_theme(_app)
        window = MainWindow()
        ok = window.centralWidget() is not None
        window.deleteLater()
        return True if ok else "FAIL no central widget"

    check("main window builds", main_window)

    def no_unused_qt():
        if not getattr(sys, "frozen", False):
            return "SKIP not frozen"
        from pathlib import Path

        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        found = sorted({p.name for p in base.rglob("*")
                        if p.suffix == ".qm" or any(m in p.name for m in _UNUSED_QT_MARKERS)})
        return True if not found else f"FAIL bundled anyway: {found[:8]}"

    check("bundle free of unused Qt parts", no_unused_qt)
    return out


# Stesso elenco di converter.spec (UNUSED_OPTIONAL_MODULES): escluse dal bundle.
_UNUSED_OPTIONAL_MODULES = (
    "pygame", "sqlalchemy", "botocore", "boto3", "s3transfer", "psycopg2", "pydantic",
    "pydantic_core", "rich", "jinja2", "markupsafe", "fsspec", "anyio", "pygments", "bcrypt",
    "requests", "urllib3", "certifi", "pyglet", "networkx", "shapely", "rtree", "psutil",
    "pkg_resources", "setuptools",
)


# Pezzi di Qt tolti dal bundle (converter.spec, UNUSED_QT_MARKERS): l'app usa solo
# QtCore/QtGui/QtWidgets. Stesso elenco dello spec, verificato da un test.
# Le traduzioni (.qm) non servono: l'app non carica nessun QTranslator.
_UNUSED_QT_MARKERS = (
    "Qt6Quick", "Qt6Qml", "Qt6Pdf", "Qt6VirtualKeyboard", "qtvirtualkeyboard", "qpdf",
    "opengl32sw",
)


def _dumpenv(out_path: str | None) -> int:
    """Scrive l'ambiente del processo (utile a diagnosticare la contaminazione
    PyInstaller verso i tool esterni)."""
    import os
    from pathlib import Path

    text = "\n".join(f"{k}={v}" for k, v in sorted(os.environ.items())) + "\n"
    if out_path:
        Path(out_path).write_text(text, encoding="utf-8")
    if sys.stdout:  # nell'exe windowed sys.stdout può essere None
        sys.stdout.write(text)
    return 0


def _convert(args: list[str]) -> int:
    """--convert <src> <dst_format> <out> [report]: una conversione reale via
    run_job. Strumento di verifica del bundle come --selftest, NON la CLI di
    Fase 3 (nessuna opzione, solo il percorso di esecuzione vero, in FULL)."""
    from pathlib import Path

    import converter.converters  # noqa: F401  (registra i converter)
    from converter.core import engine
    from converter.core.formats import by_extension
    from converter.core.job import ConversionJob
    from converter.core.pipeline import run_job

    if len(args) < 3:
        if sys.stderr:
            sys.stderr.write("usage: --convert <src> <dst_format> <out> [report]\n")
        return 2
    src, dst_format, out = Path(args[0]), args[1], Path(args[2])
    report = Path(args[3]) if len(args) > 3 else None
    engine.set_mode("FULL")
    src_format = by_extension(src.suffix)
    if not src_format:
        text = f"ok=False error=unknown source format: {src.suffix}\n"
        r_ok = False
    else:
        r = run_job(ConversionJob(src, src_format, dst_format, out))
        text = (f"ok={r.ok} error={r.error} size={r.output_size} "
                f"verdict={r.verdict.level if r.verdict else None}\n")
        r_ok = r.ok
    if report:
        report.write_text(text, encoding="utf-8")
    if sys.stdout:
        sys.stdout.write(text)
    return 0 if r_ok else 1


def _arg_after(flag: str) -> str | None:
    i = sys.argv.index(flag)
    return sys.argv[i + 1] if i + 1 < len(sys.argv) else None


if __name__ == "__main__":
    if "--dumpenv" in sys.argv:
        sys.exit(_dumpenv(_arg_after("--dumpenv")))
    if "--selftest" in sys.argv:
        sys.exit(_selftest(_arg_after("--selftest")))
    if "--convert" in sys.argv:
        sys.exit(_convert(sys.argv[sys.argv.index("--convert") + 1:]))

    from converter.gui.app import run
    sys.exit(run())
