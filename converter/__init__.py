"""Converter — applicazione desktop offline per la conversione di file.

Questo package contiene l'intera logica: core/ (framework), converters/
(implementazioni per categoria), gui/ (interfaccia PySide6) e cli/ (front-end
a riga di comando, Fase 3). Il nome dell'app e la versione vivono qui come
unica fonte di verità (usati da titolo finestra, dialog "Informazioni" e
metadata del package).
"""

APP_NAME = "Converter"
__version__ = "0.1.5"
APP_TAGLINE = "Convert files locally — honest about quality."
