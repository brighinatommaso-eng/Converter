"""Test end-to-end del converter dati (un converter per categoria)."""

import json

import pandas as pd
import pytest
import yaml

import converter.converters  # noqa: F401
from converter.core.job import ConversionJob
from converter.core.pipeline import run_job
from converter.core.registry import resolve

# Classico "billion laughs": DTD con entità che si espandono esponenzialmente.
_XML_BOMB = """<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<lolz>&lol3;</lolz>"""


def test_xml_to_csv_works(tmp_path):
    src = tmp_path / "d.xml"
    out = tmp_path / "d.csv"
    src.write_text("<data><row><a>1</a><b>2</b></row><row><a>3</a><b>4</b></row></data>")
    result = resolve("xml", "csv").convert(ConversionJob(src, "xml", "csv", out), lambda e: None)
    assert result.ok and out.exists()
    assert "a,b" in out.read_text()


def test_xml_bomb_is_rejected_gracefully(tmp_path):
    src = tmp_path / "bomb.xml"
    out = tmp_path / "bomb.csv"
    src.write_text(_XML_BOMB)
    # Deve fallire in modo pulito (niente crash, niente esplosione di memoria).
    result = run_job(ConversionJob(src, "xml", "csv", out))
    assert not result.ok
    assert "xml" in result.error.lower()
    assert not out.exists()


def test_csv_to_json_roundtrips_values(tmp_path):
    src = tmp_path / "d.csv"
    out = tmp_path / "d.json"
    src.write_text("name,age\nAlice,30\nBob,25\n")
    conv = resolve("csv", "json")
    result = conv.convert(
        ConversionJob(src, "csv", "json", out, options={"pretty": True, "orient": "records"}),
        lambda e: None,
    )
    assert result.ok and out.exists()
    data = json.loads(out.read_text())
    assert data[0]["name"] == "Alice"
    assert int(data[0]["age"]) == 30
    assert len(data) == 2


def test_csv_to_tsv_is_lossless(tmp_path):
    src = tmp_path / "d.csv"
    out = tmp_path / "d.tsv"
    src.write_text("a,b\n1,2\n3,4\n")
    conv = resolve("csv", "tsv")
    result = conv.convert(
        ConversionJob(src, "csv", "tsv", out, options={"header": True}), lambda e: None
    )
    assert result.ok and out.exists()
    assert result.verdict.is_lossless
    content = out.read_text()
    assert "\t" in content and "a\tb" in content


# --- Bug 1: json<->yaml e json->json dichiarati LOSSLESS ma pandas appiattiva ---

def test_json_to_yaml_preserves_nesting(tmp_path):
    """Regressione BUG 1: json->yaml passava da json_normalize e distruggeva
    l'annidamento (chiavi appiattite col punto, oggetto avvolto in lista)."""
    src = tmp_path / "d.json"
    out = tmp_path / "d.yaml"
    payload = {"user": {"name": "Ada", "roles": ["a", "b"]}, "n": 1}
    src.write_text(json.dumps(payload))
    conv = resolve("json", "yaml")
    result = conv.convert(ConversionJob(src, "json", "yaml", out), lambda e: None)
    assert result.ok and out.exists()
    assert result.verdict.is_lossless
    assert yaml.safe_load(out.read_text()) == payload


def test_json_to_json_preserves_object(tmp_path):
    """Regressione BUG 1: json->json ('Same format.' LOSSLESS) trasformava
    l'oggetto in una LISTA di record appiattiti."""
    src = tmp_path / "d.json"
    out = tmp_path / "o.json"
    payload = {"user": {"name": "Ada", "roles": ["a", "b"]}, "n": 1}
    src.write_text(json.dumps(payload))
    result = resolve("json", "json").convert(
        ConversionJob(src, "json", "json", out, options={"pretty": True}), lambda e: None
    )
    assert result.ok and out.exists()
    assert json.loads(out.read_text()) == payload


def test_yaml_to_json_preserves_nesting(tmp_path):
    src = tmp_path / "d.yaml"
    out = tmp_path / "d.json"
    payload = {"user": {"name": "Ada", "roles": ["a", "b"]}, "n": 1}
    src.write_text(yaml.safe_dump(payload))
    result = resolve("yaml", "json").convert(
        ConversionJob(src, "yaml", "json", out), lambda e: None
    )
    assert result.ok and out.exists()
    assert json.loads(out.read_text()) == payload


# --- Bug 2: csv<->tsv dichiarato LOSSLESS ma pandas re-inferiva i tipi ---

def test_csv_to_tsv_preserves_leading_zeros_and_decimals(tmp_path):
    """Regressione BUG 2: pd.read_csv senza dtype inferiva i tipi
    (01234->1234, 1.10->1.1, 00500->500)."""
    src = tmp_path / "d.csv"
    out = tmp_path / "d.tsv"
    src.write_text("code,price\n01234,1.10\n00500,2.00\n")
    result = resolve("csv", "tsv").convert(
        ConversionJob(src, "csv", "tsv", out, options={"header": True}), lambda e: None
    )
    assert result.ok and out.exists()
    assert result.verdict.is_lossless
    lines = out.read_text().splitlines()
    assert lines[0] == "code\tprice"
    assert lines[1] == "01234\t1.10"
    assert lines[2] == "00500\t2.00"


def test_csv_to_xlsx_preserves_values(tmp_path):
    """Regressione BUG 2 (parte xlsx/ods): il verdetto csv→xlsx è LOSSLESS
    'maps faithfully' — deve esserlo davvero, niente inferenza dei tipi."""
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    src = tmp_path / "d.csv"
    out = tmp_path / "d.xlsx"
    src.write_text("code,price\n01234,1.10\n00500,2.00\n")
    result = resolve("csv", "xlsx").convert(ConversionJob(src, "csv", "xlsx", out), lambda e: None)
    assert result.ok and out.exists()
    assert result.verdict.is_lossless
    ws = load_workbook(out).active
    assert ws["A2"].value == "01234"  # non 1234
    assert ws["B2"].value == "1.10"   # non 1.1
    assert ws["A3"].value == "00500"


def test_csv_to_json_preserves_values(tmp_path):
    """csv→json è LOSSLESS 'maps faithfully': i valori testuali del CSV vanno
    preservati esattamente (un CSV non ha tipi)."""
    src = tmp_path / "d.csv"
    out = tmp_path / "d.json"
    src.write_text("code,price\n01234,1.10\n")
    result = resolve("csv", "json").convert(
        ConversionJob(src, "csv", "json", out, options={"pretty": False, "orient": "records"}),
        lambda e: None,
    )
    assert result.ok and out.exists()
    data = json.loads(out.read_text())
    assert data[0]["code"] == "01234" and data[0]["price"] == "1.10"


def test_same_format_xlsx_preserves_sheets_and_formulas(tmp_path):
    """Regressione: xlsx→xlsx ha verdict LOSSLESS 'Same format.' — deve esserlo
    davvero. Il vecchio round-trip pandas teneva solo i valori del PRIMO foglio
    (formule e fogli extra persi) pur dichiarandosi lossless. Ora è una copia
    fedele: tutti i fogli e le formule sopravvivono."""
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    src = tmp_path / "book.xlsx"
    out = tmp_path / "out.xlsx"
    with pd.ExcelWriter(src, engine="openpyxl") as w:
        pd.DataFrame({"a": [1, 2], "b": [3, 4]}).to_excel(w, sheet_name="Sheet1", index=False)
        pd.DataFrame({"z": [9]}).to_excel(w, sheet_name="Extra", index=False)
    wb = load_workbook(src)
    wb["Sheet1"]["D1"] = "=A2+B2"
    wb.save(src)

    result = resolve("xlsx", "xlsx").convert(ConversionJob(src, "xlsx", "xlsx", out), lambda e: None)
    assert result.ok and result.verdict.is_lossless
    got = load_workbook(out)
    assert got.sheetnames == ["Sheet1", "Extra"]      # niente foglio perso
    assert got["Sheet1"]["D1"].value == "=A2+B2"      # formula preservata


def test_same_format_xml_is_byte_identical(tmp_path):
    """xml→xml LOSSLESS: copia fedele, non un round-trip pandas approssimato."""
    src = tmp_path / "d.xml"
    out = tmp_path / "out.xml"
    src.write_bytes(b'<?xml version="1.0"?>\n<root attr="1"><item>x</item></root>\n')
    result = resolve("xml", "xml").convert(ConversionJob(src, "xml", "xml", out), lambda e: None)
    assert result.ok and result.verdict.is_lossless
    assert out.read_bytes() == src.read_bytes()


def test_same_format_xlsx_strips_metadata_only_on_request(tmp_path):
    """Con byte-copy i metadati del documento sopravvivono (l'utente non ha
    chiesto lo strip) — coerente con le immagini; con strip_metadata=True le
    proprietà vengono rimosse ma i dati restano."""
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook, load_workbook

    src = tmp_path / "doc.xlsx"
    wb = Workbook()
    wb.properties.creator = "SECRET-AUTHOR"
    wb.properties.title = "SECRET-DOC-TITLE"
    wb.active.append(["name", "age"])
    wb.active.append(["Alice", 30])
    wb.save(src)

    # Strip OFF: i metadati sono preservati (copia fedele).
    keep = tmp_path / "keep.xlsx"
    resolve("xlsx", "xlsx").convert(ConversionJob(src, "xlsx", "xlsx", keep), lambda e: None)
    assert load_workbook(keep).properties.creator == "SECRET-AUTHOR"

    # Strip ON: le proprietà vengono rimosse, i dati restano.
    strip = tmp_path / "strip.xlsx"
    resolve("xlsx", "xlsx").convert(
        ConversionJob(src, "xlsx", "xlsx", strip, strip_metadata=True), lambda e: None)
    props = load_workbook(strip).properties
    assert props.creator != "SECRET-AUTHOR"
    assert props.title != "SECRET-DOC-TITLE"
    assert [c.value for c in load_workbook(strip).active[2]] == ["Alice", 30]  # dati intatti
