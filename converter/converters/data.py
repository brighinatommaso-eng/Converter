"""Converter dati (csv/tsv/json/xlsx/ods/xml/yaml) basato su pandas.

Verdetto onesto: csv<->tsv e json->yaml preservano i dati (LOSSLESS; yaml->json
perde commenti/date, quindi è LOSSY); leggere
fogli di calcolo via pandas tiene solo i valori (formule/formattazione/più fogli
si perdono, LOSSY); appiattire strutture annidate in tabelle è LOSSY; tutto ciò
che coinvolge XML è approssimato (mappatura elementi/attributi ambigua).
"""

from __future__ import annotations

import csv
import io
import json
import shutil
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

from ..core.base import Converter
from ..core.deps import has_module
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.options import BoolOption, ChoiceOption, Option
from ..core.quality import QualityVerdict, lossless, lossy, unsupported
from ..core.registry import register

_TABULAR_TEXT = {"csv", "tsv"}
_TREE = {"json", "yaml"}
_SPREADSHEET = {"xlsx", "ods"}
_ALL = frozenset(_TABULAR_TEXT | _TREE | _SPREADSHEET | {"xml"})


def _json_default(value):
    """Tipi YAML senza equivalente JSON: le date diventano stringhe ISO 8601."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        import base64
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, set):
        return sorted(value, key=str)
    raise TypeError(f"Value of type {type(value).__name__} has no JSON representation.")


@register
class DataConverter(Converter):
    category = "data"
    name = "Data / tables (pandas)"
    input_formats = _ALL
    output_formats = _ALL

    def format_available(self, format_id: str) -> tuple[bool, str]:
        # I formati office richiedono engine opzionali; senza, restano in grigio.
        if format_id == "xlsx" and not has_module("openpyxl"):
            return False, "XLSX needs the 'openpyxl' package (pip install openpyxl)."
        if format_id == "ods" and not has_module("odf"):
            return False, "ODS needs the 'odfpy' package (pip install odfpy)."
        return True, ""

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src not in _ALL or dst not in _ALL:
            return unsupported()
        if src == dst:
            return lossless("Same format.")
        if "xml" in (src, dst):
            return lossy("XML ↔ table/tree mapping is approximate (element/attribute ambiguity).")
        if src in _SPREADSHEET:
            return lossy("Spreadsheets are read as values only; formulas, formatting and extra sheets are dropped.")
        if src in _TABULAR_TEXT and dst in _TABULAR_TEXT:
            return lossless("Only the field delimiter changes; all data is preserved.")
        if src == "yaml" and dst == "json":
            # YAML ha più del modello JSON: commenti, ancore, tag e date. Inoltre
            # PyYAML legge con le regole YAML 1.1 (NO/yes/on non quotati → booleani).
            return lossy("YAML comments and anchors are dropped, dates become ISO strings, and "
                         "unquoted words like NO/yes/on are read as booleans (YAML 1.1 rules).")
        if src in _TREE and dst in _TREE:
            return lossless("JSON and YAML share the same data model.")
        if src in _TABULAR_TEXT and dst in (_TREE | _SPREADSHEET):
            return lossless("Each row maps faithfully to a record/cell.")
        if src in _TREE and dst in (_TABULAR_TEXT | _SPREADSHEET):
            return lossy("Nested structures are flattened into columns.")
        return lossy("Some structural detail may not survive this conversion.")

    def options_schema(self, src: str, dst: str) -> list[Option]:
        opts: list[Option] = []
        if dst == "json":
            opts.append(BoolOption("pretty", "Pretty-print (indented)", True))
            opts.append(ChoiceOption("orient", "JSON shape", "records",
                                     choices=[("records", "List of row objects"),
                                              ("columns", "Column → values")]))
        if dst in ("csv", "tsv"):
            opts.append(BoolOption("header", "Write header row", True))
        return opts

    # --- Lettura ---

    def _read(self, job: ConversionJob) -> pd.DataFrame:
        src, path = job.src_format, job.src_path
        if src in _TABULAR_TEXT:
            # Testo senza tipi, separatore e codifica reali: vedi _read_delimited.
            return self._read_delimited(path, src)
        if src == "xlsx":
            return pd.read_excel(path, engine="openpyxl")
        if src == "ods":
            return pd.read_excel(path, engine="odf")
        if src == "xml":
            # SICUREZZA: i file XML arrivano dall'utente e NON sono fidati. La
            # ElementTree della stdlib è vulnerabile a billion-laughs/XXE, perciò
            # validiamo prima con defusedxml (che vieta DTD/entità/riferimenti
            # esterni). Solo se il file è sicuro lo passiamo a pandas.
            self._assert_safe_xml(path)
            return pd.read_xml(path, parser="etree")
        if src in ("json", "yaml"):
            if src == "json":
                with open(path, encoding="utf-8-sig") as fh:
                    obj = json.load(fh)
            else:
                with open(path, encoding="utf-8") as fh:
                    obj = yaml.safe_load(fh)
            # json_normalize gestisce sia liste di dict sia singoli dict annidati.
            if isinstance(obj, list):
                return pd.json_normalize(obj)
            return pd.json_normalize(obj)
        raise ValueError(f"Unsupported source format: {src}")

    @staticmethod
    def _decode_text(path) -> str:
        """Testo del file: UTF-8 (con o senza BOM), altrimenti cp1252.

        Excel su Windows (impostazioni italiane/europee) esporta i CSV in cp1252:
        senza il fallback la conversione moriva con un UnicodeDecodeError grezzo.
        cp1252 non decodifica 5 byte; latin-1 copre tutto come ultima spiaggia.
        """
        raw = Path(path).read_bytes()
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1", errors="replace")

    @staticmethod
    def _sniff_delimiter(text: str, src: str) -> str:
        """Separatore reale del file: ';' è il default dei CSV europei."""
        if src == "tsv":
            return "\t"
        sample = "\n".join(text.splitlines()[:50])
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;|\t").delimiter
        except csv.Error:
            return ","

    def _read_delimited(self, path, src: str) -> pd.DataFrame:
        """CSV/TSV come testo puro, con separatore e codifica reali.

        * dtype=str + keep_default_na=False: un CSV non ha tipi; inferirli
          cambierebbe i valori (01234→1234, 1.10→1.1, "NA"→vuoto).
        * header=None e intestazione assegnata a mano: pandas rinominava le
          colonne duplicate ("a,a" → "a, a.1"), cambiando i dati in silenzio.
        """
        text = self._decode_text(path)
        sep = self._sniff_delimiter(text, src)
        df = pd.read_csv(io.StringIO(text), sep=sep, dtype=str, keep_default_na=False,
                         header=None)
        df.attrs["delimiter"] = sep
        if df.empty:
            return df
        header = [str(value) for value in df.iloc[0]]
        df = df.iloc[1:].reset_index(drop=True)
        df.columns = header
        df.attrs["delimiter"] = sep
        return df

    @staticmethod
    def _assert_safe_xml(path) -> None:
        """Difesa contro gli 'XML bomb' (billion laughs) e XXE su file non fidati.

        defusedxml solleva EntitiesForbidden/DTDForbidden/ExternalReferenceForbidden
        quando il documento contiene definizioni pericolose: le convertiamo in un
        errore chiaro e leggibile invece di lasciar esplodere il parser.
        """
        from defusedxml.common import DefusedXmlException
        from defusedxml.ElementTree import parse as safe_parse

        try:
            safe_parse(path)
        except DefusedXmlException as exc:
            raise ValueError(
                "This XML file uses DTDs or entities that are unsafe to expand "
                f"(possible XML bomb / XXE): {type(exc).__name__}. Refusing to parse."
            ) from exc

    # --- Scrittura ---

    def _write(self, df: pd.DataFrame, job: ConversionJob) -> None:
        dst, path, opts = job.dst_format, job.output_path, job.options
        if dst == "csv":
            # csv→csv conserva il separatore della sorgente (';' resta ';').
            sep = df.attrs.get("delimiter", ",") if job.src_format == "csv" else ","
            df.to_csv(path, sep=sep, index=False, header=opts.get("header", True))
        elif dst == "tsv":
            df.to_csv(path, sep="\t", index=False, header=opts.get("header", True))
        elif dst == "xlsx":
            df.to_excel(path, index=False, engine="openpyxl")
        elif dst == "ods":
            df.to_excel(path, index=False, engine="odf")
        elif dst == "xml":
            df.to_xml(path, index=False, parser="etree")
        elif dst == "json":
            indent = 2 if opts.get("pretty", True) else None
            df.to_json(path, orient=opts.get("orient", "records"), indent=indent, force_ascii=False)
        elif dst == "yaml":
            records = df.to_dict(orient="records")
            with open(path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(records, fh, allow_unicode=True, sort_keys=False)
        else:
            raise ValueError(f"Unsupported target format: {dst}")

    def _convert_tree(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        """Percorso albero->albero per json<->yaml (e json->json, yaml->yaml).

        NON passa da pandas/json_normalize: caricare l'albero e ridumparlo
        preserva l'annidamento esatto (il verdetto dichiara 'stesso modello di
        dati' → LOSSLESS, e ora è vero). json_normalize invece appiattiva le
        chiavi col punto e avvolgeva gli oggetti in una lista di record.
        """
        progress(ProgressEvent(0.2, "Reading data…"))
        if job.src_format == "json":
            with open(job.src_path, encoding="utf-8-sig") as fh:
                obj = json.load(fh)
        else:
            with open(job.src_path, encoding="utf-8") as fh:
                obj = yaml.safe_load(fh)
        progress(ProgressEvent(0.6, "Writing data…"))
        if job.dst_format == "json":
            # 'orient' non ha senso su un albero arbitrario: qui è un passthrough.
            indent = 2 if job.options.get("pretty", True) else None
            with open(job.output_path, "w", encoding="utf-8") as fh:
                json.dump(obj, fh, ensure_ascii=False, indent=indent, default=_json_default)
        else:
            with open(job.output_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(obj, fh, allow_unicode=True, sort_keys=False)
        size = job.output_path.stat().st_size
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=self.verdict(job.src_format, job.dst_format))

    def _same_format_copy(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        """src==dst per xlsx/ods/xml/yaml: copia FEDELE, mai un round-trip.

        yaml→yaml ridumpato perdeva i commenti e cambiava i dati (`NO` → `false`
        con le regole YAML 1.1) sotto un badge LOSSLESS "Same format.".

        Il round-trip pandas terrebbe solo i valori del PRIMO foglio (xlsx/ods:
        formule, formattazione e fogli extra persi) o riscriverebbe l'XML in modo
        approssimato — mentre il verdict src==dst è lossless "Same format.". La
        copia byte-a-byte rende quel verdetto ONESTO. Se l'utente ha chiesto lo
        strip dei metadati, le proprietà del documento vengono rimosse dopo la
        copia (i dati restano intatti), coerente con le immagini.
        """
        progress(ProgressEvent(0.5, "Copying…"))
        shutil.copy(job.src_path, job.output_path)
        if job.strip_metadata and job.src_format in _SPREADSHEET:
            self._strip_spreadsheet_metadata(job.output_path, job.src_format)
        size = job.output_path.stat().st_size
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=self.verdict(job.src_format, job.dst_format))

    @staticmethod
    def _strip_spreadsheet_metadata(path, fmt: str) -> None:
        """Rimuove le proprietà del documento (autore/titolo/…) preservando i
        dati. Usato solo quando strip_metadata è attivo sul percorso di copia."""
        if fmt == "xlsx":
            from openpyxl import load_workbook
            from openpyxl.packaging.core import DocumentProperties
            wb = load_workbook(path)
            wb.properties = DocumentProperties()  # proprietà azzerate
            wb.save(path)
        else:  # ods
            from odf.opendocument import load
            doc = load(str(path))
            for child in list(doc.meta.childNodes):
                doc.meta.removeChild(child)
            doc.save(str(path))

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        # Stesso formato per xlsx/ods/xml/yaml: copia fedele (vedi _same_format_copy).
        if job.src_format == job.dst_format and job.src_format in (_SPREADSHEET | {"xml", "yaml"}):
            return self._same_format_copy(job, progress)
        # Albero->albero (json/yaml): passthrough fedele, mai via pandas.
        if job.src_format in _TREE and job.dst_format in _TREE:
            return self._convert_tree(job, progress)
        progress(ProgressEvent(0.2, "Reading data…"))
        df = self._read(job)
        verdict = self.verdict(job.src_format, job.dst_format, job.options)
        renamed = self._dedupe_for_records(df, job.dst_format)
        if renamed:
            # Onestà a runtime (come i multi-frame di image.py): un record JSON/YAML
            # non può avere due chiavi uguali, quindi i duplicati vengono rinominati.
            verdict = lossy("Duplicate column names cannot be keys of one record; renamed: "
                            + ", ".join(renamed) + ".")
        progress(ProgressEvent(0.6, "Writing data…"))
        self._write(df, job)
        size = job.output_path.stat().st_size
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=verdict)

    @staticmethod
    def _dedupe_for_records(df: pd.DataFrame, dst: str) -> list[str]:
        """Rinomina le colonne duplicate SOLO verso formati a record (json/yaml/xml).

        csv/tsv/xlsx/ods reggono intestazioni ripetute e le conservano intatte.
        Ritorna le rinomine fatte ("a → a_2") per il verdetto."""
        if dst not in ("json", "yaml", "xml") or df.columns.is_unique:
            return []
        seen: dict[str, int] = {}
        names: list[str] = []
        renamed: list[str] = []
        for name in map(str, df.columns):
            count = seen.get(name, 0) + 1
            seen[name] = count
            if count == 1:
                names.append(name)
                continue
            # "_2" e non " (2)": deve restare un nome valido anche come tag XML.
            new = f"{name}_{count}"
            while new in seen:
                count += 1
                new = f"{name}_{count}"
            seen[new] = 1
            names.append(new)
            renamed.append(f"'{name}' → '{new}'")
        df.columns = names
        return renamed
