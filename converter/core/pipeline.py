"""Esecuzione dei job: punto unico riusato da GUI e CLI.

run_job() è l'unico modo per eseguire una conversione: risolve il converter dal
registry, verifica la disponibilità e incapsula qualsiasi eccezione in un
ConversionResult(ok=False). Così nessun fallimento propaga e un file rotto in un
batch non ferma gli altri.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from . import registry
from .job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent


def run_job(job: ConversionJob, progress: ProgressCallback | None = None) -> ConversionResult:
    cb: ProgressCallback = progress or (lambda _evt: None)

    conv = registry.resolve(job.src_format, job.dst_format)
    if conv is None:
        return ConversionResult(
            ok=False,
            error=f"No converter available for {job.src_format} → {job.dst_format}.",
        )

    status = conv.available()
    if not status.ok:
        hint = f" {status.install_hint}" if status.install_hint else ""
        return ConversionResult(ok=False, error=f"{status.reason}{hint}")

    src_ok, src_reason = conv.format_available(job.src_format)
    if not src_ok:
        return ConversionResult(ok=False, error=src_reason)
    dst_ok, dst_reason = conv.format_available(job.dst_format)
    if not dst_ok:
        return ConversionResult(ok=False, error=dst_reason)

    pair_ok, pair_reason = conv.pair_available(job.src_format, job.dst_format)
    if not pair_ok:
        return ConversionResult(ok=False, error=pair_reason)

    # Validazione input PRIMA di convert(): un input invalido non deve mai
    # raggiungere ffmpeg (eviteremmo un messaggio chiaro per uno stderr grezzo).
    invalid = conv.validate(job)
    if invalid:
        return ConversionResult(ok=False, error=invalid)

    try:
        cb(ProgressEvent(0.0, "Starting…"))
        result = conv.convert(job, cb)
        cb(ProgressEvent(1.0, "Done"))
        return result
    except Exception as exc:  # rete di sicurezza: nessuna eccezione esce da qui
        return ConversionResult(ok=False, error=f"{type(exc).__name__}: {exc}")


def run_batch(
    jobs: list[ConversionJob],
    on_result: Callable[[int, ConversionResult], None] | None = None,
    max_workers: int = 4,
) -> list[ConversionResult]:
    """Esegue più job in parallelo (usato da CLI/headless). La GUI usa invece i
    propri worker Qt ma chiama comunque run_job per ogni file."""
    results: list[ConversionResult] = [None] * len(jobs)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(run_job, job): idx for idx, job in enumerate(jobs)}
        for future in futures:
            idx = futures[future]
            results[idx] = future.result()
            if on_result:
                on_result(idx, results[idx])
    return results
