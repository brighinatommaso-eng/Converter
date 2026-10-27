"""Interfaccia base di ogni converter.

Un Converter è dichiarativo e stateless tra le chiamate: dichiara cosa sa
leggere/scrivere, sa dare un verdetto di qualità e uno schema di opzioni, e
sa eseguire una conversione. La GUI non conosce mai le sottoclassi per nome:
le raggiunge solo tramite il registry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .deps import DependencyStatus
from .job import ConversionJob, ConversionResult, ProgressCallback
from .options import Option
from .quality import QualityVerdict, unsupported


class Converter(ABC):
    # Attributi di classe da definire in ogni sottoclasse.
    category: str = ""
    name: str = ""
    input_formats: frozenset[str] = frozenset()
    output_formats: frozenset[str] = frozenset()

    # Priorità di routing: a parità di coppia gestibile vince la priorità più alta.
    # I motori specifici (pdfminer, markup, striprtf) la alzano per battere il
    # converter generico LibreOffice sulle coppie sovrapposte (es. docx→html).
    priority: int = 0

    # --- Disponibilità (degrado controllato) ---

    @classmethod
    def available(cls) -> DependencyStatus:
        """Disponibilità dell'intero converter (es. ffmpeg presente o meno)."""
        return DependencyStatus(True)

    def format_available(self, format_id: str) -> tuple[bool, str]:
        """Disponibilità granulare per singolo formato (es. HEIC richiede un
        plugin opzionale). Ritorna (disponibile, motivo_se_no)."""
        return True, ""

    def pair_available(self, src: str, dst: str) -> tuple[bool, str]:
        """Disponibilità per la COPPIA (src, dst), oltre ai singoli formati.

        Serve quando un converter sa leggere src e scrivere dst separatamente ma
        NON quella direzione specifica. Es.: LibreOffice importa un PDF come
        documento Draw e non ha un filtro per esportarlo in un formato Writer,
        quindi pdf→docx/odt/rtf/txt non è fattibile pur essendo pdf e docx
        gestiti singolarmente. Default: disponibile. La GUI mostra la coppia in
        grigio col motivo; run_job la rifiuta con un messaggio chiaro."""
        return True, ""

    # --- Capacità e qualità ---

    def can_convert(self, src: str, dst: str) -> bool:
        if src not in self.input_formats or dst not in self.output_formats:
            return False
        return self.verdict(src, dst).is_supported

    @abstractmethod
    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        """Verdetto qualitativo della coppia. `options` è opzionale: alcuni
        formati (es. WebP lossless on/off) cambiano verdetto in base alle
        impostazioni, perciò la logica resta nel converter, non nella GUI."""
        return unsupported()

    def options_schema(self, src: str, dst: str) -> list[Option]:
        """Opzioni esposte in UI per questa coppia. Default: nessuna."""
        return []

    def estimate_size(self, job: ConversionJob) -> int | None:
        """Stima (in byte) della dimensione di output, o None se non stimabile."""
        return None

    # --- Validazione input (prima di ffmpeg) ---

    def validate(self, job: ConversionJob) -> str | None:
        """Controllo completo prima di eseguire: può leggere il file sorgente
        (es. durata) per validare opzioni dipendenti dal contenuto. Ritorna un
        messaggio CHIARO e leggibile se l'input è invalido, altrimenti None.

        Chiamato da run_job PRIMA di convert(): così un input cattivo non
        raggiunge mai ffmpeg e l'utente vede un messaggio sensato, mai uno
        stderr grezzo di ffmpeg."""
        return None

    def validate_quick(self, src: str, dst: str, options: dict) -> str | None:
        """Validazione strutturale SENZA accesso al file (no durata): usata
        dalla GUI per un feedback immediato mentre l'utente digita. Sottoinsieme
        di validate(); i controlli dipendenti dal contenuto restano a validate()."""
        return None

    # --- Esecuzione ---

    @abstractmethod
    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        """Esegue la conversione. Non deve sollevare: incapsula gli errori in
        un ConversionResult(ok=False)."""
        raise NotImplementedError
