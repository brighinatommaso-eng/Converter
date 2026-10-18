"""Punto d'ingresso: `python -m converter` avvia la GUI."""

import sys

from .gui.app import run

if __name__ == "__main__":
    sys.exit(run())
