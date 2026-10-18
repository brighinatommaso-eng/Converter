"""Test del rilevamento basato sul contenuto (le estensioni mentono)."""

import json
import zipfile

import pandas as pd
from PIL import Image

from converter.core.detection import detect


def test_png_detected_despite_wrong_extension(tmp_path):
    # File PNG reale ma con estensione .txt: deve essere riconosciuto come PNG.
    path = tmp_path / "image.txt"
    Image.new("RGB", (8, 8), (255, 0, 0)).save(path, format="PNG")
    result = detect(path)
    assert result.format_id == "png"
    assert result.category == "image"
    assert result.method == "magic"


def test_jpeg_detected(tmp_path):
    path = tmp_path / "photo.bin"
    Image.new("RGB", (8, 8), (0, 128, 255)).save(path, format="JPEG")
    assert detect(path).format_id == "jpg"


def test_json_detected(tmp_path):
    path = tmp_path / "data.dat"
    path.write_text(json.dumps({"a": 1, "b": [1, 2, 3]}))
    assert detect(path).format_id == "json"


def test_csv_detected(tmp_path):
    path = tmp_path / "rows.dat"
    path.write_text("name,age\nAlice,30\nBob,25\n")
    assert detect(path).format_id == "csv"


def test_tsv_detected(tmp_path):
    path = tmp_path / "rows.dat"
    path.write_text("name\tage\nAlice\t30\nBob\t25\n")
    assert detect(path).format_id == "tsv"


def test_yaml_detected(tmp_path):
    path = tmp_path / "conf.dat"
    path.write_text("name: Alice\nage: 30\nhobbies:\n  - reading\n")
    assert detect(path).format_id == "yaml"


def test_svg_detected(tmp_path):
    path = tmp_path / "vector.dat"
    path.write_text('<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"></svg>')
    assert detect(path).format_id == "svg"


def _wrong_ext(tmp_path, name):
    # Tutti i pacchetti ZIP usano la stessa estensione fuorviante .zip per
    # dimostrare che la disambiguazione avviene guardando DENTRO l'archivio.
    return tmp_path / name


def test_xlsx_detected_inside_zip(tmp_path):
    path = _wrong_ext(tmp_path, "book.zip")
    pd.DataFrame({"a": [1, 2]}).to_excel(path, index=False, engine="openpyxl")
    result = detect(path)
    assert result.format_id == "xlsx"
    assert result.method == "zip"


def test_ods_detected_inside_zip(tmp_path):
    path = _wrong_ext(tmp_path, "sheet.zip")
    pd.DataFrame({"a": [1, 2]}).to_excel(path, index=False, engine="odf")
    assert detect(path).format_id == "ods"


def test_docx_detected_via_content_types(tmp_path):
    # docx minimale: [Content_Types].xml dichiara wordprocessingml.
    path = _wrong_ext(tmp_path, "doc.zip")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/></Types>',
        )
        zf.writestr("word/document.xml", "<document/>")
    assert detect(path).format_id == "docx"


def test_epub_detected_via_mimetype(tmp_path):
    path = _wrong_ext(tmp_path, "book.zip")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", "<container/>")
    assert detect(path).format_id == "epub"


def test_odt_detected_via_mimetype(tmp_path):
    path = _wrong_ext(tmp_path, "text.zip")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
    assert detect(path).format_id == "odt"


# --- BUG 6: ADTS AAC scambiato per mp3 (maschera mp3 troppo lasca) ---

def test_aac_adts_not_detected_as_mp3(tmp_path):
    # Frame ADTS (sync 0xFFF1) + payload fittizio: NON deve risultare mp3.
    path = tmp_path / "sound.dat"
    path.write_bytes(b"\xff\xf1\x50\x80\x00\x1f\xfc" + b"\x00" * 64)
    result = detect(path)
    assert result.format_id == "aac"
    assert result.category == "audio"


def test_real_mp3_frame_still_detected(tmp_path):
    # Sync mp3 tipico (0xFFFB, MPEG1 Layer III): deve restare mp3.
    path = tmp_path / "song.dat"
    path.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 64)
    assert detect(path).format_id == "mp3"


# --- BUG 7: JSON "pretty" più grande dello head (4KB) scambiato per yaml/csv ---

def test_large_pretty_json_detected(tmp_path):
    path = tmp_path / "big.dat"
    # Oggetto indentato ben oltre i 4096 byte dello head troncato.
    payload = {"items": [{"id": i, "name": f"item-{i}", "tags": ["a", "b"]} for i in range(400)]}
    path.write_text(json.dumps(payload, indent=2))
    assert path.stat().st_size > 4096
    result = detect(path)
    assert result.format_id == "json"
    assert result.method == "json-parse"


def test_json_with_leading_whitespace_detected(tmp_path):
    # Whitespace iniziale: il vecchio `head[:1].lstrip()` guardava un solo byte
    # e non rientrava mai nel ramo json.
    path = tmp_path / "ws.dat"
    path.write_text("\n\n   " + json.dumps({"a": 1, "b": [1, 2, 3]}))
    assert detect(path).format_id == "json"


def test_unknown_binary(tmp_path):
    path = tmp_path / "blob.dat"
    path.write_bytes(b"\x00\x01\x02\x03not a known format")
    result = detect(path)
    assert result.format_id is None or result.confidence <= 0.3
