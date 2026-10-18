"""Runner LibreOffice headless, blindato contro i blocchi.

LibreOffice è un sottoprocesso che PUÒ impiccarsi (profilo corrotto, documento
malformato, dialog invisibile in headless). La coda non deve mai gelare per
questo, perciò qui:

* **timeout duro** per ogni invocazione; allo scadere si uccide l'intero albero
  di processi (soffice forka `soffice.bin`: uccidere solo il padre lascerebbe il
  figlio vivo e il blocco persisterebbe) e si torna un errore pulito;
* **serializzazione** con un lock di modulo: una sola istanza headless alla
  volta (la GUI gira più worker, ma istanze concorrenti sullo stesso profilo lo
  corrompono);
* **profilo dedicato e riusato**, isolato da quello reale dell'utente, così le
  esecuzioni successive restano "calde" (warm-up).
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import threading
from pathlib import Path

from ..core import paths
from ..core.deps import clean_subprocess_env, find_soffice

# Una sola istanza LibreOffice headless alla volta (profilo condiviso serializzato).
# NOTA SUI CONFINI: il lock previene STRUTTURALMENTE la corruzione del profilo da
# istanze concorrenti (è impossibile che due conversioni entrino insieme nella
# sezione LibreOffice). Che il lock serializzi è verificato da un test dedicato
# (test_lock_serializes_libreoffice_section). L'ASSENZA di corruzione sotto
# concorrenza NON è testata direttamente: è una corsa non deterministica e un
# test "verde" non proverebbe nulla. È quindi un confine noto, non un assunto
# nascosto: la protezione è il lock, non una verifica end-to-end.
_LOCK = threading.Lock()
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DEFAULT_TIMEOUT = 60.0  # secondi per invocazione (deciso in Tier E)
_IS_WINDOWS = platform.system() == "Windows"


def _profile_url() -> str:
    return paths.libreoffice_profile_dir().as_uri()


def _popen(args: list[str]) -> subprocess.Popen:
    # env PULITO: senza le variabili Python/PyInstaller, altrimenti il Python
    # interno di LibreOffice non trova le sue librerie e i filtri di export.
    kwargs: dict = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        text=True, creationflags=_NO_WINDOW, env=clean_subprocess_env())
    if not _IS_WINDOWS:
        # Nuovo process group così possiamo uccidere l'intero albero su POSIX.
        kwargs["start_new_session"] = True
    return subprocess.Popen(args, **kwargs)


def _kill_tree(proc: subprocess.Popen) -> None:
    """Uccide il processo E i suoi figli (soffice.bin). Senza l'albero, il
    blocco sopravviverebbe alla morte del solo padre."""
    if _IS_WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=_NO_WINDOW)
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


def run_with_timeout(args: list[str], timeout: float) -> tuple[bool, int | None, str, str]:
    """Esegue `args`, applicando un timeout duro con kill dell'albero.

    Ritorna (timed_out, returncode, stdout, stderr). Questa è la primitiva di
    controllo del blocco, isolata apposta per poterla testare senza LibreOffice.
    """
    proc = _popen(args)
    try:
        out, err = proc.communicate(timeout=timeout)
        return False, proc.returncode, out or "", err or ""
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = "", ""
        return True, None, out or "", err or ""


def run_convert(src: Path, dst_ext: str, out_dir: Path,
                timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """Converte `src` in `dst_ext` dentro `out_dir` via LibreOffice headless.

    Ritorna (ok, payload): se ok, payload è il percorso del file prodotto;
    altrimenti payload è un messaggio d'errore CHIARO (mai uno stderr grezzo
    riversato addosso all'utente)."""
    soffice = find_soffice()
    if not soffice:
        return False, "LibreOffice was not found."

    args = [
        soffice, f"-env:UserInstallation={_profile_url()}",
        "--headless", "--norestore", "--nologo",
        "--convert-to", dst_ext, "--outdir", str(out_dir), str(src),
    ]

    with _LOCK:  # una sola istanza headless alla volta
        timed_out, code, out, err = run_with_timeout(args, timeout)
        produced = out_dir / f"{src.stem}.{dst_ext}"
        # Successo SOLO con uscita pulita (niente timeout, exit 0) E file presente:
        # un processo killato a metà scrittura può lasciare un file TRONCATO, che
        # non va spacciato per conversione riuscita (guardare solo produced.exists()
        # era il bug).
        if not timed_out and code == 0 and produced.exists():
            return True, str(produced)

        # Fallita. Distinguo COSA ha fallito, per decidere se il profilo è da
        # buttare (un profilo avvelenato romperebbe ogni conversione futura):
        #   * timeout/kill o uscita ANOMALA (crash) -> il profilo può essere
        #     corrotto (lock file orfani, scrittura interrotta): lo ricostruisco,
        #     e cancello l'eventuale output parziale rimasto su disco.
        #   * uscita 0 senza output -> fallimento "soft" del documento (input
        #     problematico): il profilo è sano, NON lo tocco (sarebbe inutile e
        #     lento) — esattamente il caso "errore pulito" che non va punito.
        crashed = timed_out or (code is not None and code != 0)
        if crashed:
            if produced.exists():
                try:
                    produced.unlink()
                except OSError:
                    pass
            _reset_profile_locked(timeout)

    if timed_out:
        return False, f"LibreOffice timed out after {int(timeout)}s and was stopped."

    tail = (err or out or "").strip().splitlines()[-4:]
    detail = (" " + " ".join(s.strip() for s in tail)) if tail else ""
    return False, f"LibreOffice could not convert this document (exit {code}).{detail}"


def _warm_up_locked(timeout: float) -> None:
    """Corpo del warm-up, da chiamare col _LOCK GIÀ acquisito (così può essere
    riusato dal reset del profilo senza ri-acquisire il lock non rientrante)."""
    soffice = find_soffice()
    if not soffice:
        return
    args = [soffice, f"-env:UserInstallation={_profile_url()}",
            "--headless", "--norestore", "--nologo", "--terminate_after_init"]
    run_with_timeout(args, timeout)


def _reset_profile_locked(timeout: float) -> None:
    """Profilo (potenzialmente) avvelenato da un hang/crash: lo butto e lo
    ricostruisco vuoto, poi lo riscaldo. Da chiamare col _LOCK già acquisito."""
    shutil.rmtree(paths.libreoffice_profile_dir(), ignore_errors=True)
    paths.libreoffice_profile_dir()  # ricrea la cartella vuota
    _warm_up_locked(timeout)


def warm_up(timeout: float = DEFAULT_TIMEOUT) -> None:
    """Avvio "a freddo" anticipato: inizializza il profilo e i moduli così la
    PRIMA conversione reale non paga la lentezza del primo lancio. Va chiamato
    fuori dal thread della GUI. Silenzioso: il warm-up non deve mai disturbare."""
    with _LOCK:
        _warm_up_locked(timeout)
