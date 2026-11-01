#!/usr/bin/env python3
"""Genera converter/assets/converter.ico dalle icone PNG dell'app.

Perche' serve (e non basta un .ico qualsiasi):
  * Windows incrocia i frame PNG-compressi solo da Vista in poi e con il
    supporto PNG di Explorer. Molti consumer di GDI+ (System.Drawing,
    alcuni tool del sistema, il dialog "Proprieta'") NON leggono i frame PNG
    dentro un .ico: ricavano rumore. Con un .ico a frame "classici" (DIB) il
    file si legge ovunque, quindi l'icona del collegamento, quella del file
    .exe e quella mostrata dalla taskbar sono sempre quella giusta.
  * Il frame da 256 px resta PNG: e' l'unica dimensione per cui il PNG e' lo
    standard (un DIB a 256 px occuperebbe 256 KB).

Uso (dalla radice del repo):
    python installer/tools/make-ico.py

Le sorgenti sono i PNG in converter/assets/icons/ (16, 32, 48, 64, 128, 256):
stessa artefazione che la GUI usa a runtime, quindi .ico e icona di finestra
non possono divergere.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
ICON_DIR = ROOT / "converter" / "assets" / "icons"
ICO_PATH = ROOT / "converter" / "assets" / "converter.ico"

# (dimensione, compressione): BMP/DIB per le piccole, PNG per la 256.
FRAMES = [(16, "bmp"), (32, "bmp"), (48, "bmp"), (64, "bmp"), (128, "bmp"),
          (256, "png")]


def dib_frame(image: Image.Image) -> bytes:
    """Frame ICO in formato DIB: BITMAPINFOHEADER + pixel BGRA + maschera AND."""
    image = image.convert("RGBA")
    width, height = image.size
    pixels = image.load()

    xor = bytearray()
    for y in range(height - 1, -1, -1):        # le bitmap DIB sono capovolte
        for x in range(width):
            r, g, b, a = pixels[x, y]
            xor += bytes((b, g, r, a))

    stride = ((width + 31) // 32) * 4          # righe a 1 bit allineate a 4 byte
    and_mask = bytearray()
    for y in range(height - 1, -1, -1):
        row = bytearray(stride)
        for x in range(width):
            if pixels[x, y][3] == 0:
                row[x // 8] |= 0x80 >> (x % 8)
        and_mask += row

    header = struct.pack(
        "<IiiHHIIiiII",
        40,                 # biSize
        width,
        height * 2,         # altezza di XOR + AND
        1,                  # biPlanes
        32,                 # biBitCount
        0,                  # biCompression = BI_RGB
        len(xor) + len(and_mask),
        0, 0, 0, 0,
    )
    return header + bytes(xor) + bytes(and_mask)


def png_frame(image: Image.Image) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    image.convert("RGBA").save(buffer, format="PNG")
    return buffer.getvalue()


def build_ico() -> bytes:
    entries: list[tuple[int, int, bytes]] = []
    for size, kind in FRAMES:
        source = ICON_DIR / f"converter_{size}.png"
        if not source.is_file():
            raise SystemExit(f"manca l'icona sorgente: {source}")
        image = Image.open(source)
        payload = dib_frame(image) if kind == "bmp" else png_frame(image)
        entries.append((image.width, image.height, payload))

    offset = 6 + 16 * len(entries)
    directory = bytearray(struct.pack("<HHH", 0, 1, len(entries)))
    body = bytearray()
    for width, height, payload in entries:
        directory += struct.pack(
            "<BBBBHHII",
            width if width < 256 else 0,
            height if height < 256 else 0,
            0,                  # colori della tavolozza
            0,                  # riservato
            1,                  # piani
            32,                 # bit per pixel
            len(payload),
            offset,
        )
        body += payload
        offset += len(payload)
    return bytes(directory + body)


def main() -> None:
    data = build_ico()
    ICO_PATH.write_bytes(data)
    print(f"{ICO_PATH.relative_to(ROOT)}: {len(data)} byte, "
          f"{len(FRAMES)} frame ({', '.join(f'{s}:{k}' for s, k in FRAMES)})")


if __name__ == "__main__":
    main()
