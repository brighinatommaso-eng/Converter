"""Analisi del layout di pagina con un modello AI locale (offline).

Il motore euristico legge i caratteri a coordinate e indovina colonne, tabelle e
titoli con soglie geometriche: funziona sui documenti "regolari", sbaglia sulle
riviste a colonne miste, sulle tabelle senza bordi, sulle infografiche e perde i
grafici vettoriali. Qui un modello di rilevamento del layout guarda la pagina
RENDERIZZATA, come la vede una persona, e restituisce le regioni (titolo, testo,
tabella, figura, formula, intestazione…) con il loro ordine di lettura.

Il testo resta quello del PDF (pdfminer): il modello decide solo DOVE sono i
blocchi e in che ordine leggerli, quindi non inventa né storpia parole. Le figure
(grafici vettoriali compresi) e le formule diventano immagini ritagliate dalla
pagina renderizzata.

Componenti, tutti con licenze compatibili con la distribuzione:
  * modello PP-DocLayoutV2 di PaddlePaddle (Apache-2.0), esportato in ONNX;
  * onnxruntime (MIT) per eseguirlo sulla CPU;
  * pypdfium2 / PDFium (Apache-2.0 / BSD-3) per renderizzare le pagine.

Se il modello o una di queste librerie manca, `get_analyzer()` restituisce None e
il lettore usa il motore euristico: la conversione funziona comunque e il verdetto
lo dichiara.
"""

from __future__ import annotations

import io
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

MODEL_FILENAME = "pp_doc_layoutv2.onnx"
MODEL_SHA256 = "0bd2ea0997fe0789f0300292291f8bbf897d890b44a9a3bd5be72afd6198aa90"

_INPUT_SIZE = 800           # il modello lavora su un'immagine 800x800
_RENDER_LONG_SIDE = 1100    # lato lungo della pagina renderizzata per il modello
_CROP_DPI = 200             # risoluzione dei ritagli (figure, formule)
_MIN_SCORE = 0.5

# PDFium NON è thread-safe (nemmeno tra documenti diversi) e la GUI converte in
# parallelo: ogni chiamata a pypdfium2 passa da questo lock. onnxruntime invece
# accetta run() concorrenti sulla stessa sessione.
_PDFIUM_LOCK = threading.Lock()
_LOAD_LOCK = threading.Lock()
_analyzer: LayoutAnalyzer | None = None
_load_error: str | None = None


@dataclass(slots=True)
class Region:
    """Regione rilevata, in coordinate PDF (origine in basso a sinistra)."""

    kind: str       # etichetta del modello: "text", "table", "image", "doc_title"…
    score: float
    x0: float
    y0: float
    x1: float
    y1: float
    order: int      # posizione nell'ordine di lettura (0 = prima)

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)

    def overlap(self, x0: float, y0: float, x1: float, y1: float) -> float:
        """Area dell'intersezione con un rettangolo."""
        w = min(self.x1, x1) - max(self.x0, x0)
        h = min(self.y1, y1) - max(self.y0, y0)
        return w * h if w > 0 and h > 0 else 0.0


def model_candidates() -> list[Path]:
    """Dove cercare il modello: variabile d'ambiente, bundle, cartella del pacchetto."""
    paths: list[Path] = []
    env = os.environ.get("CONVERTER_LAYOUT_MODEL")
    if env:
        paths.append(Path(env))
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        paths.append(Path(bundle) / "models" / MODEL_FILENAME)
    package = Path(__file__).resolve().parents[2]          # .../converter
    paths.append(package / "models" / MODEL_FILENAME)
    paths.append(package.parent / "models" / MODEL_FILENAME)  # radice del repo
    return paths


def find_model() -> Path | None:
    for path in model_candidates():
        if path.is_file() and path.stat().st_size > 1_000_000:
            return path
    return None


def unavailable_reason() -> str | None:
    """Perché l'analisi AI non è disponibile (None se lo è)."""
    if os.environ.get("CONVERTER_LAYOUT_AI", "1") == "0":
        return "AI layout analysis is turned off."
    try:
        import onnxruntime  # noqa: F401
        import pypdfium2  # noqa: F401
    except Exception:
        return "the AI layout engine (onnxruntime/pypdfium2) is not installed"
    if find_model() is None:
        return "the AI layout model is not installed"
    if _load_error:
        return _load_error
    return None


def get_analyzer() -> LayoutAnalyzer | None:
    """L'analizzatore condiviso, caricato al primo uso; None se non disponibile."""
    global _analyzer, _load_error
    if os.environ.get("CONVERTER_LAYOUT_AI", "1") == "0":
        return None         # spento ora: vale anche se il modello è già in memoria
    if _analyzer is not None:
        return _analyzer
    if unavailable_reason() is not None:
        return None
    with _LOAD_LOCK:
        if _analyzer is None and _load_error is None:
            try:
                _analyzer = LayoutAnalyzer(find_model())
            except Exception as exc:   # modello corrotto, CPU senza istruzioni…
                _load_error = f"the AI layout model could not be loaded ({exc})"
        return _analyzer


class LayoutAnalyzer:
    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(model_path), options,
                                            providers=["CPUExecutionProvider"])
        labels = self.session.get_modelmeta().custom_metadata_map.get("character", "")
        self.labels = [label.strip() for label in labels.split("\n") if label.strip()]
        if not self.labels:
            raise ValueError("model without class labels")

    def open(self, path: Path) -> PdfPages:
        return PdfPages(path, self)

    def detect(self, image) -> list[tuple[str, float, float, float, float, float, float]]:
        """(etichetta, punteggio, x0, y0, x1, y1, ordine) in pixel dell'immagine."""
        import numpy as np
        from PIL import Image

        width, height = image.size
        resized = image.convert("RGB").resize((_INPUT_SIZE, _INPUT_SIZE), Image.BICUBIC)
        tensor = (np.asarray(resized, dtype=np.float32) / 255.0).transpose(2, 0, 1)[None]
        outputs = self.session.run(None, {
            "im_shape": np.array([[_INPUT_SIZE, _INPUT_SIZE]], dtype=np.float32),
            "image": tensor,
            "scale_factor": np.array([[_INPUT_SIZE / height, _INPUT_SIZE / width]],
                                     dtype=np.float32),
        })
        boxes = outputs[0][: int(outputs[1][0])]
        result = []
        for row in boxes:
            label_index, score = int(row[0]), float(row[1])
            if label_index < 0 or label_index >= len(self.labels) or score < _MIN_SCORE:
                continue
            x0, y0, x1, y1 = (float(v) for v in row[2:6])
            order = float(row[6]) if len(row) > 6 else 0.0
            result.append((self.labels[label_index], score, x0, y0, x1, y1, order))
        return _suppress_duplicates(result)


def _suppress_duplicates(boxes):
    """Toglie i doppioni: stessa etichetta e sovrapposizione forte (NMS semplice)."""
    boxes = sorted(boxes, key=lambda b: -b[1])
    kept: list = []
    for box in boxes:
        duplicate = False
        for other in kept:
            w = min(box[4], other[4]) - max(box[2], other[2])
            h = min(box[5], other[5]) - max(box[3], other[3])
            if w <= 0 or h <= 0:
                continue
            inter = w * h
            area = (box[4] - box[2]) * (box[5] - box[3])
            other_area = (other[4] - other[2]) * (other[5] - other[3])
            iou = inter / max(1e-6, area + other_area - inter)
            same = box[0] == other[0]
            if (same and iou > 0.6) or iou > 0.98:
                duplicate = True
                break
        if not duplicate:
            kept.append(box)
    return kept


class PdfPages:
    """PDF aperto con PDFium per il rilevamento e i ritagli (accesso serializzato)."""

    def __init__(self, path: Path, analyzer: LayoutAnalyzer) -> None:
        import pypdfium2 as pdfium

        self.analyzer = analyzer
        with _PDFIUM_LOCK:
            self.document = pdfium.PdfDocument(str(path))
            self.count = len(self.document)

    def close(self) -> None:
        with _PDFIUM_LOCK:
            self.document.close()

    def __enter__(self) -> PdfPages:
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _page_box(self, page) -> tuple[float, float, float, float]:
        """Crop box nelle coordinate di pdfminer, che sposta l'origine nell'angolo
        in basso a sinistra del MEDIA box (ctm di PDFPageAggregator)."""
        mx, my = (float(v) for v in page.get_mediabox()[:2])
        left, bottom, right, top = (float(v) for v in page.get_cropbox())
        return left - mx, bottom - my, right - mx, top - my

    def regions(self, index: int) -> list[Region] | None:
        """Regioni della pagina `index`, o None se la pagina non è analizzabile."""
        if index >= self.count:
            return None
        with _PDFIUM_LOCK:
            page = self.document[index]
            try:
                if page.get_rotation() % 360:
                    return None   # pagina ruotata: coordinate pdfminer non allineate
                left, bottom, right, top = self._page_box(page)
                width, height = right - left, top - bottom
                if width <= 0 or height <= 0:
                    return None
                scale = _RENDER_LONG_SIDE / max(width, height)
                image = page.render(scale=scale).to_pil()
            finally:
                page.close()
        boxes = self.analyzer.detect(image)
        regions = []
        for rank, (label, score, px0, py0, px1, py1, _order) in enumerate(
                sorted(boxes, key=lambda b: b[6])):
            regions.append(Region(
                kind=label, score=score,
                x0=left + px0 / scale, x1=left + px1 / scale,
                y0=top - py1 / scale, y1=top - py0 / scale,
                order=rank))
        return regions

    def crop_png(self, index: int, x0: float, y0: float, x1: float, y1: float) -> bytes | None:
        """Ritaglio PNG della pagina (coordinate PDF) a `_CROP_DPI`."""
        with _PDFIUM_LOCK:
            page = self.document[index]
            try:
                left, bottom, right, top = self._page_box(page)
                x0, x1 = max(left, x0), min(right, x1)
                y0, y1 = max(bottom, y0), min(top, y1)
                if x1 - x0 < 2 or y1 - y0 < 2:
                    return None
                bitmap = page.render(scale=_CROP_DPI / 72.0,
                                     crop=(x0 - left, y0 - bottom, right - x1, top - y1))
                image = bitmap.to_pil()
            finally:
                page.close()
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()
