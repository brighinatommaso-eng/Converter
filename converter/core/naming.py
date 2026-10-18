"""Costruzione del percorso di output e gestione delle collisioni.

Regola: non sovrascrivere mai silenziosamente. Se il file esiste già (incluso
il file sorgente stesso, es. png->png nella stessa cartella) si aggiunge un
suffisso " (1)", " (2)", ...
"""

from __future__ import annotations

from pathlib import Path

from .formats import primary_ext


def resolve_collision(path: Path, reserved: set[Path] | None = None) -> Path:
    """Restituisce un path libero. Un nome è preso se esiste su disco OPPURE se
    è già stato assegnato altrove nel batch corrente (`reserved`): in un batch i
    file di output non esistono ancora quando i path vengono calcolati, quindi il
    solo `path.exists()` non basta a evitare che due sorgenti collidano."""
    taken = reserved or set()

    def is_free(p: Path) -> bool:
        return not p.exists() and p not in taken

    if is_free(path):
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if is_free(candidate):
            return candidate
        i += 1


def build_output_path(
    src_path: Path,
    dst_format: str,
    output_dir: Path | None,
    same_as_source: bool,
    reserved: set[Path] | None = None,
) -> Path:
    """Determina dove scrivere l'output, evitando collisioni (anche con i path
    già riservati nel batch, vedi `reserved`)."""
    folder = src_path.parent if (same_as_source or output_dir is None) else Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{src_path.stem}.{primary_ext(dst_format)}"
    return resolve_collision(target, reserved)
