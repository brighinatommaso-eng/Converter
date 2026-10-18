"""Fixture condivise.

La modalità motore è stato globale di processo: va riportata a FULL (default
storico) attorno a ogni test, così un test che passa a PORTABLE non contamina gli
altri (la suite esistente assume FULL/LibreOffice)."""

import pytest

from converter.core import engine


@pytest.fixture(autouse=True)
def _reset_engine_mode():
    engine.set_mode("FULL")
    yield
    engine.set_mode("FULL")
