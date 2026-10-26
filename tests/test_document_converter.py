"""Test del converter documenti (LibreOffice) e del degrado controllato.

Due classi di test:
  * SEMPRE eseguiti: degrado quando LibreOffice è ASSENTE (monkeypatch), verdetti,
    e la primitiva di timeout/kill (testata senza LibreOffice con un processo
    finto che dorme).
  * skipif LibreOffice assente: conversioni reali.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import converter.converters  # noqa: F401
import converter.converters._libreoffice as lo
from converter.converters._libreoffice import run_with_timeout
from converter.core import deps, paths
from converter.core.job import ConversionJob
from converter.core.pipeline import run_job
from converter.core.registry import resolve, targets_for

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --- Degrado controllato: LibreOffice ASSENTE (sempre eseguito) --------------

def test_documents_grey_out_without_libreoffice(monkeypatch):
    monkeypatch.setattr(deps, "find_soffice", lambda: None)
    targets = {t.dst_format: t for t in targets_for("docx")}
    assert targets, "il converter documenti deve comunque comparire (grigio)"
    pdf = targets.get("pdf")
    assert pdf is not None and not pdf.available
    assert "libreoffice" in pdf.reason_unavailable.lower()


def test_other_categories_stay_live_without_libreoffice(monkeypatch):
    """Niente LibreOffice non deve spegnere il resto dell'app."""
    monkeypatch.setattr(deps, "find_soffice", lambda: None)
    image_targets = {t.dst_format: t for t in targets_for("png")}
    assert image_targets["jpg"].available  # immagini vive
    data_targets = {t.dst_format: t for t in targets_for("csv")}
    assert data_targets["json"].available  # dati vivi


def test_run_job_clean_error_without_libreoffice(monkeypatch, tmp_path):
    monkeypatch.setattr(deps, "find_soffice", lambda: None)
    src = tmp_path / "a.docx"
    src.write_bytes(b"PK\x03\x04 fake docx")
    result = run_job(ConversionJob(src, "docx", "pdf", tmp_path / "a.pdf"))
    assert not result.ok
    assert "libreoffice" in result.error.lower()
    assert not (tmp_path / "a.pdf").exists()


# --- Verdetti onesti (non serve LibreOffice) ---------------------------------

def test_pdf_to_docx_is_lossy():
    conv = resolve("pdf", "docx")
    assert conv is not None
    v = conv.verdict("pdf", "docx")
    assert v.is_lossy
    assert "inferred" in v.reason.lower() or "geometry" in v.reason.lower()


def test_same_format_is_lossless():
    assert resolve("docx", "docx").verdict("docx", "docx").is_lossless


def test_to_txt_is_lossy():
    assert resolve("docx", "txt").verdict("docx", "txt").is_lossy


# --- PDF come sorgente: onestà (pdf->Writer non è fattibile) ------------------

def test_pdf_to_writer_formats_marked_unavailable():
    """Il DocumentConverter (LibreOffice) blocca pdf→{docx,odt,rtf,txt} con motivo
    chiaro; pdf→html resta nella sua competenza. NB: il routing globale ora dà
    pdf→txt/html a pdfminer (priorità), perciò si interroga il DocumentConverter
    DIRETTAMENTE, non via resolve()."""
    from converter.converters.document import DocumentConverter
    conv = DocumentConverter()
    for dst in ("docx", "odt", "rtf", "txt"):
        ok, reason = conv.pair_available("pdf", dst)
        assert ok is False
        assert "Draw" in reason and "html" in reason.lower()
    assert conv.pair_available("pdf", "html") == (True, "")


def test_targets_for_pdf_writer_targets_available(monkeypatch):
    """Tutti i target testuali del PDF sono coperti dal motore strutturale
    (pdfminer + python-docx, in-process): niente grigio in nessuna modalità,
    tranne odt/rtf che passano da LibreOffice per il secondo salto."""
    monkeypatch.setattr(deps, "find_soffice", lambda: "C:/fake/soffice.exe")
    real_has = deps.has_module
    monkeypatch.setattr(deps, "has_module",
                        lambda n: True if n == "docx" else real_has(n))
    targets = {t.dst_format: t for t in targets_for("pdf")}

    for dst in ("html", "txt", "md", "docx", "odt", "rtf"):
        assert targets[dst].available is True, dst


def test_run_job_refuses_broken_pdf_to_docx_cleanly(monkeypatch, tmp_path):
    """pdf→docx è instradato sul motore strutturale; un PDF illeggibile viene
    rifiutato in validate() con un messaggio chiaro, senza lanciare soffice
    né creare output."""
    monkeypatch.setattr(deps, "find_soffice", lambda: "C:/fake/soffice.exe")
    real_has = deps.has_module
    monkeypatch.setattr(deps, "has_module",
                        lambda n: True if n == "docx" else real_has(n))
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    out = tmp_path / "a.docx"
    result = run_job(ConversionJob(src, "pdf", "docx", out))
    assert not result.ok
    assert "Could not read this PDF" in result.error
    assert not out.exists()


# --- Primitiva timeout/kill (senza LibreOffice) ------------------------------

def test_run_with_timeout_kills_hung_process(tmp_path):
    """Un processo che si impicca viene ucciso e non completa il suo lavoro:
    è la garanzia che la coda non gela mai su LibreOffice."""
    marker = tmp_path / "done.txt"
    code = f"import time; time.sleep(30); open(r'{marker}', 'w').close()"
    start = time.time()
    timed_out, rc, out, err = run_with_timeout([sys.executable, "-c", code], timeout=2)
    elapsed = time.time() - start

    assert timed_out is True
    assert rc is None
    assert elapsed < 20, "il kill deve avvenire subito dopo il timeout, non al termine"
    time.sleep(1)
    assert not marker.exists(), "il processo è stato ucciso prima di completare il lavoro"


def test_run_with_timeout_returns_output_when_fast():
    timed_out, rc, out, err = run_with_timeout(
        [sys.executable, "-c", "print('hi')"], timeout=30)
    assert timed_out is False
    assert rc == 0
    assert "hi" in out


# --- Resolver soffice nei percorsi standard (skipif assente) -----------------

@pytest.mark.skipif(deps.find_soffice() is None, reason="LibreOffice not installed")
def test_soffice_resolver_finds_install_path():
    path = deps.find_soffice()
    assert path is not None and Path(path).exists()
    assert Path(path).name.lower().startswith("soffice")
    # Conferma che NON è stato trovato solo su PATH: il merito è dei percorsi
    # d'installazione standard (su questa macchina soffice non è nel PATH).
    if shutil.which("soffice") is None:
        assert "libreoffice" in path.lower()


# --- Conversione reale (skipif assente) --------------------------------------

@pytest.mark.skipif(deps.find_soffice() is None, reason="LibreOffice not installed")
def test_txt_to_pdf_real(tmp_path):
    src = tmp_path / "note.txt"
    src.write_text("Hello from the converter test.\nالسطر الثاني.\n", encoding="utf-8")
    out = tmp_path / "note.pdf"
    result = resolve("txt", "pdf").convert(
        ConversionJob(src, "txt", "pdf", out), lambda e: None)
    assert result.ok and out.exists()
    assert out.stat().st_size > 0
    assert out.read_bytes()[:5] == b"%PDF-"  # è davvero un PDF
    assert result.verdict.is_lossy


@pytest.mark.skipif(deps.find_soffice() is None, reason="LibreOffice not installed")
def test_txt_to_odt_real(tmp_path):
    src = tmp_path / "note.txt"
    src.write_text("Round-trip body text.", encoding="utf-8")
    out = tmp_path / "note.odt"
    result = run_job(ConversionJob(src, "txt", "odt", out))
    assert result.ok and out.exists()
    assert out.stat().st_size > 0
    # ODT è uno zip: firma PK.
    assert out.read_bytes()[:2] == b"PK"


# --- Recreate-on-failure del profilo (senza LibreOffice, deterministico) -----

def _fake_profile(monkeypatch, prof: Path):
    """Reindirizza il profilo LibreOffice a una cartella di test che si ricrea."""
    def factory():
        prof.mkdir(parents=True, exist_ok=True)
        return prof
    monkeypatch.setattr(paths, "libreoffice_profile_dir", factory)


def test_timeout_recreates_profile(monkeypatch, tmp_path):
    """Un timeout/kill butta e ricostruisce il profilo (potenzialmente avvelenato)."""
    prof = tmp_path / "lo_profile"
    _fake_profile(monkeypatch, prof)
    prof.mkdir()
    (prof / "poison.lock").write_text("orfano di un'istanza impiccata")

    monkeypatch.setattr(lo, "find_soffice", lambda: "C:/fake/soffice.exe")
    # Simula un hang: run_with_timeout segnala timed_out.
    monkeypatch.setattr(lo, "run_with_timeout", lambda args, t: (True, None, "", ""))
    warmed = {"n": 0}
    monkeypatch.setattr(lo, "_warm_up_locked", lambda t: warmed.__setitem__("n", warmed["n"] + 1))

    ok, msg = lo.run_convert(Path("x.docx"), "pdf", tmp_path / "out", timeout=1)

    assert not ok and "timed out" in msg
    assert not (prof / "poison.lock").exists()  # profilo buttato
    assert prof.exists()                         # ricostruito vuoto
    assert warmed["n"] == 1                      # ri-scaldato


def test_crash_recreates_profile(monkeypatch, tmp_path):
    """Anche un'uscita anomala (crash, exit != 0) ricostruisce il profilo."""
    prof = tmp_path / "lo_profile"
    _fake_profile(monkeypatch, prof)
    prof.mkdir()
    (prof / "poison.lock").write_text("x")

    monkeypatch.setattr(lo, "find_soffice", lambda: "C:/fake/soffice.exe")
    monkeypatch.setattr(lo, "run_with_timeout", lambda args, t: (False, 139, "", "Segmentation fault"))
    warmed = {"n": 0}
    monkeypatch.setattr(lo, "_warm_up_locked", lambda t: warmed.__setitem__("n", warmed["n"] + 1))

    ok, msg = lo.run_convert(Path("x.docx"), "pdf", tmp_path / "out", timeout=1)

    assert not ok and "could not convert" in msg
    assert not (prof / "poison.lock").exists()
    assert warmed["n"] == 1


def test_soft_failure_keeps_profile(monkeypatch, tmp_path):
    """Un fallimento "soft" (exit 0, nessun output: input problematico) NON deve
    buttare il profilo — sarebbe inutile e lento, e il profilo è sano."""
    prof = tmp_path / "lo_profile"
    _fake_profile(monkeypatch, prof)
    prof.mkdir()
    (prof / "keep.txt").write_text("profilo sano")

    monkeypatch.setattr(lo, "find_soffice", lambda: "C:/fake/soffice.exe")
    monkeypatch.setattr(lo, "run_with_timeout", lambda args, t: (False, 0, "", "cannot open document"))
    warmed = {"n": 0}
    monkeypatch.setattr(lo, "_warm_up_locked", lambda t: warmed.__setitem__("n", warmed["n"] + 1))

    ok, msg = lo.run_convert(Path("bad.docx"), "pdf", tmp_path / "out", timeout=1)

    assert not ok and "could not convert" in msg
    assert (prof / "keep.txt").exists()  # profilo intatto
    assert warmed["n"] == 0              # nessuna ricostruzione


# --- BUG 8: output parziale dopo timeout/kill NON deve passare per successo ---

def test_timeout_with_partial_output_is_not_success(monkeypatch, tmp_path):
    """soffice killato a metà scrittura lascia un file troncato: prima veniva
    spacciato per conversione riuscita perché si guardava solo produced.exists()."""
    _fake_profile(monkeypatch, tmp_path / "lo_profile")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    partial = out_dir / "x.pdf"
    partial.write_bytes(b"%PDF-1.4 troncato")  # output parziale lasciato dal kill

    monkeypatch.setattr(lo, "find_soffice", lambda: "C:/fake/soffice.exe")
    monkeypatch.setattr(lo, "run_with_timeout", lambda args, t: (True, None, "", ""))
    monkeypatch.setattr(lo, "_warm_up_locked", lambda t: None)

    ok, msg = lo.run_convert(Path("x.docx"), "pdf", out_dir, timeout=1)

    assert not ok and "timed out" in msg
    assert not partial.exists()  # file parziale rimosso, non spacciato per output valido


def test_crash_with_partial_output_is_not_success(monkeypatch, tmp_path):
    _fake_profile(monkeypatch, tmp_path / "lo_profile")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    partial = out_dir / "x.pdf"
    partial.write_bytes(b"partial")

    monkeypatch.setattr(lo, "find_soffice", lambda: "C:/fake/soffice.exe")
    monkeypatch.setattr(lo, "run_with_timeout", lambda args, t: (False, 139, "", "Segmentation fault"))
    monkeypatch.setattr(lo, "_warm_up_locked", lambda t: None)

    ok, msg = lo.run_convert(Path("x.docx"), "pdf", out_dir, timeout=1)

    assert not ok and "could not convert" in msg
    assert not partial.exists()


def test_clean_success_returns_produced_file(monkeypatch, tmp_path):
    """Contro-prova: uscita 0 con file presente resta un successo (niente
    cancellazioni di troppo)."""
    _fake_profile(monkeypatch, tmp_path / "lo_profile")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    produced = out_dir / "x.pdf"
    produced.write_bytes(b"%PDF-1.4 ok")

    monkeypatch.setattr(lo, "find_soffice", lambda: "C:/fake/soffice.exe")
    monkeypatch.setattr(lo, "run_with_timeout", lambda args, t: (False, 0, "", ""))

    ok, payload = lo.run_convert(Path("x.docx"), "pdf", out_dir, timeout=1)

    assert ok and payload == str(produced) and produced.exists()


# --- Ambiente pulito per i tool esterni (anti-contaminazione frozen) ---------

def test_clean_subprocess_env_strips_python_and_pyi_vars(monkeypatch):
    """Le variabili che farebbero usare al figlio il NOSTRO Python vanno via;
    il resto dell'ambiente resta. È la classe di bug che rompeva i filtri di
    export di LibreOffice nell'eseguibile PyInstaller."""
    monkeypatch.setenv("PYTHONHOME", r"C:\bundle")
    monkeypatch.setenv("PYTHONPATH", r"C:\bundle\lib")
    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    monkeypatch.setenv("_PYI_ARCHIVE_FILE", "x")
    monkeypatch.setenv("_MEIPASS2", "y")
    monkeypatch.setenv("KEEP_ME", "ok")

    env = deps.clean_subprocess_env()

    for gone in ("PYTHONHOME", "PYTHONPATH", "PYTHONNOUSERSITE", "_PYI_ARCHIVE_FILE", "_MEIPASS2"):
        assert gone not in env
    assert env.get("KEEP_ME") == "ok"  # tutto il resto è preservato


def test_clean_subprocess_env_frozen_purges_bundle_from_path(monkeypatch, tmp_path):
    """Da frozen: le voci di PATH dentro il bundle (che farebbero caricare le
    NOSTRE DLL al figlio) sono rimosse; LD_LIBRARY_PATH è ripristinato dall'_ORIG."""
    meipass = tmp_path / "_internal"
    meipass.mkdir()
    monkeypatch.setattr(deps.sys, "frozen", True, raising=False)
    monkeypatch.setattr(deps.sys, "_MEIPASS", str(meipass), raising=False)

    # Una cartella di sistema valida sull'OS corrente: con "C:\\..." su Linux il
    # separatore ':' spezzava il percorso e il test falliva per motivi suoi.
    sysdir = str(tmp_path / "system32")
    monkeypatch.setenv("PATH", str(meipass) + os.pathsep + sysdir)
    monkeypatch.setenv("LD_LIBRARY_PATH", str(meipass))
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/lib")

    env = deps.clean_subprocess_env()

    path_parts = env["PATH"].split(os.pathsep)
    assert str(meipass) not in path_parts        # voce del bundle rimossa
    assert sysdir in path_parts                   # PATH di sistema preservato
    assert env["LD_LIBRARY_PATH"] == "/usr/lib"   # ripristinato dall'_ORIG
    assert "LD_LIBRARY_PATH_ORIG" not in env


# --- Il lock serializza la sezione LibreOffice (deterministico) --------------

def test_lock_serializes_libreoffice_section(monkeypatch, tmp_path):
    """Verifica il MECCANISMO: due thread non entrano mai insieme nella sezione
    LibreOffice protetta da _LOCK. (Non prova l'assenza di corruzione — vedi il
    commento sul confine noto in _libreoffice.py.)"""
    _fake_profile(monkeypatch, tmp_path / "lo_profile")
    monkeypatch.setattr(lo, "find_soffice", lambda: "C:/fake/soffice.exe")

    state = {"active": 0, "max": 0}
    guard = threading.Lock()

    def fake_rwt(args, timeout):
        with guard:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.15)  # finestra ampia per cogliere una sovrapposizione
        with guard:
            state["active"] -= 1
        return (False, 0, "", "")  # exit 0 -> niente recreate

    monkeypatch.setattr(lo, "run_with_timeout", fake_rwt)

    def worker():
        lo.run_convert(Path("a.docx"), "pdf", tmp_path, timeout=5)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert state["max"] == 1, "due conversioni sono entrate insieme: il lock non serializza"
