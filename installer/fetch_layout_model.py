"""Scarica il modello AI di layout (PP-DocLayoutV2, Apache-2.0) in models/.

Il modello (~214 MB) non sta su PyPI e non va nel repository: lo scaricano la
build di release e la CI. L'hash SHA-256 è fisso (quello di
converter/converters/docmodel/layout_ai.py): un file diverso, troncato o
manomesso fa fallire lo script, mai una build con un modello sconosciuto.

    python installer/fetch_layout_model.py [cartella]   (default: models/)
"""

from __future__ import annotations

import hashlib
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from converter.converters.docmodel.layout_ai import MODEL_FILENAME, MODEL_SHA256  # noqa: E402

# Esportazione ONNX di RapidAI (Apache-2.0) del modello PaddlePaddle PP-DocLayoutV2.
URLS = [
    "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/"
    "pp_doc_layout/pp_doc_layoutv2.onnx",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "models"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MODEL_FILENAME
    if target.is_file() and sha256(target) == MODEL_SHA256:
        print(f"{target}: già presente, hash ok")
        return 0
    partial = target.with_suffix(".part")
    for attempt in range(1, 5):
        for url in URLS:
            try:
                print(f"download {url} (tentativo {attempt})", flush=True)
                with urllib.request.urlopen(url, timeout=120) as response, open(partial, "wb") as out:
                    while chunk := response.read(1 << 20):
                        out.write(chunk)
                actual = sha256(partial)
                if actual != MODEL_SHA256:
                    print(f"hash diverso: {actual}", flush=True)
                    continue
                partial.replace(target)
                print(f"{target}: {target.stat().st_size / 1e6:.1f} MB, hash ok")
                return 0
            except Exception as exc:  # rete instabile: si riprova
                print(f"fallito: {exc}", flush=True)
        time.sleep(10 * attempt)
    partial.unlink(missing_ok=True)
    print("impossibile scaricare il modello di layout", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
