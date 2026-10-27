"""Import di tutti i moduli converter per innescare l'auto-registrazione.

Importare questo package popola il registry (ogni modulo usa @register). La GUI
fa `import converter.converters` una volta sola all'avvio.
"""

# audio e video richiedono ffmpeg ma si registrano comunque: se ffmpeg manca,
# i loro target appaiono in grigio invece di sparire (l'utente vede il perché).
# documenti via LibreOffice: stessa disciplina (si registra sempre, grigio se assente).
# motori "puri" (Fase 2), disponibili anche in PORTABLE; priorità alta per vincere
# le coppie sovrapposte con LibreOffice: pdf (pdfminer), markup (md/html/epub in-process), rtf (striprtf).
# textdoc è il motore strutturale (IR): pdf→docx/odt/rtf/md/txt/html e docx→md/txt.
from . import (  # noqa: F401,E402  (import per side-effect: registrazione)
    audio,
    data,
    document,
    image,
    markup,
    mesh3d,
    pdf,
    plaintext,
    rtf,
    textdoc,
    video,
)
