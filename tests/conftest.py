"""Fixture condivise.

La modalità motore è stato globale di processo: va riportata a FULL (default
storico) attorno a ogni test, così un test che passa a PORTABLE non contamina gli
altri (la suite esistente assume FULL/LibreOffice)."""

import os

import pytest

from converter.core import engine


@pytest.fixture(autouse=True)
def _reset_engine_mode():
    engine.set_mode("FULL")
    yield
    engine.set_mode("FULL")


@pytest.fixture(autouse=True)
def _geometric_engine_unless_asked(request, monkeypatch):
    """I test del lettore PDF misurano il motore GEOMETRICO, deterministico e senza
    modello. L'analisi AI del layout ha i suoi test (marcati `layout_ai`): se il
    modello è installato in locale non deve cambiare l'esito degli altri."""
    if request.node.get_closest_marker("layout_ai") is None:
        monkeypatch.setenv("CONVERTER_LAYOUT_AI", "0")
    elif "CONVERTER_LAYOUT_AI" in os.environ:
        monkeypatch.delenv("CONVERTER_LAYOUT_AI")
