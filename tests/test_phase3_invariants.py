"""Fase 3 — audit & lock della matrice di capacità.

Niente nuovo comportamento: invarianti ESAUSTIVE sul prodotto cartesiano di tutti
i formati 'document' × {FULL, PORTABLE}. Bloccano regressioni future:

  1. PORTABLE non tocca MAI LibreOffice: ogni coppia disponibile è instradata a un
     motore puro (pdfminer/markup/striprtf), non al DocumentConverter.
  2. Onestà del greying: ogni coppia in grigio ha un motivo NON vuoto; le coppie
     disponibili in FULL ma in grigio in PORTABLE citano "Full mode".
  3. Coerenza del verdetto: targets_for(...).verdict == resolve(...).verdict.
"""

import pytest

import converter.converters  # noqa: F401  popola il registry
from converter.core import deps, engine
from converter.core.formats import FORMATS
from converter.core.registry import resolve, targets_for

DOC_FORMATS = sorted(f for f, meta in FORMATS.items() if meta.category == "document")


@pytest.fixture(autouse=True)
def all_engines(monkeypatch):
    """Tutti i motori 'presenti': la matrice non deve dipendere da cosa è installato."""
    monkeypatch.setattr(deps, "find_soffice", lambda: r"C:\LibreOffice\program\soffice.exe")
    real_has = deps.has_module
    monkeypatch.setattr(deps, "has_module",
                        lambda n: True if n in ("pdfminer", "striprtf") else real_has(n))


def _offered(src):
    """(dst -> TargetOption) per il formato sorgente nella modalità corrente."""
    return {t.dst_format: t for t in targets_for(src)}


# --- 1. PORTABLE non instrada mai a LibreOffice -----------------------------

def test_portable_available_pairs_never_use_libreoffice():
    engine.set_mode("PORTABLE")
    for src in DOC_FORMATS:
        for dst, opt in _offered(src).items():
            if not opt.available:
                continue
            conv = resolve(src, dst)
            assert conv is not None, f"{src}->{dst} offerto ma non risolvibile"
            assert conv.__class__.__name__ != "DocumentConverter", \
                f"PORTABLE instrada {src}->{dst} a LibreOffice!"
            # il motore risolto deve essere davvero eseguibile nella modalità.
            assert conv.available().ok, f"{src}->{dst} risolve a un motore non disponibile"


# --- 2. Onestà del greying --------------------------------------------------

@pytest.mark.parametrize("mode", ["FULL", "PORTABLE"])
def test_every_greyed_pair_has_a_reason(mode):
    engine.set_mode(mode)
    for src in DOC_FORMATS:
        for dst, opt in _offered(src).items():
            if not opt.available:
                assert opt.reason_unavailable.strip(), \
                    f"{mode}: {src}->{dst} in grigio senza motivo"


def test_portable_only_greyings_cite_full_mode():
    engine.set_mode("FULL")
    full_avail = {(s, dst): opt.available
                  for s in DOC_FORMATS for dst, opt in _offered(s).items()}
    engine.set_mode("PORTABLE")
    for src in DOC_FORMATS:
        for dst, opt in _offered(src).items():
            # disponibile in FULL ma in grigio in PORTABLE => è una limitazione di modalità
            if not opt.available and full_avail.get((src, dst)):
                assert "Full mode" in opt.reason_unavailable, \
                    f"{src}->{dst} greyed in PORTABLE ma il motivo non cita 'Full mode': " \
                    f"{opt.reason_unavailable!r}"


# --- 3. Coerenza del verdetto fra targets_for e resolve ---------------------

@pytest.mark.parametrize("mode", ["FULL", "PORTABLE"])
def test_target_verdict_matches_resolver(mode):
    engine.set_mode(mode)
    for src in DOC_FORMATS:
        for dst, opt in _offered(src).items():
            conv = resolve(src, dst)
            if conv is None:
                continue
            assert conv.verdict(src, dst).level == opt.verdict.level, \
                f"{mode}: {src}->{dst} verdetto incoerente UI vs pipeline"


# --- Bonus: il prodotto cartesiano è coperto (nessun formato document saltato) -

def test_doc_formats_present():
    # Sanità: la lista include i nuovi formati di Fase 2.
    for f in ("pdf", "docx", "odt", "rtf", "md", "txt", "html", "epub"):
        assert f in DOC_FORMATS
