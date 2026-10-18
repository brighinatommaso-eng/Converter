"""Converter dati (csv/tsv/json/xlsx/ods/xml/yaml) basato su pandas.

Verdetto onesto: csv<->tsv e json<->yaml preservano i dati (LOSSLESS); leggere
fogli di calcolo via pandas tiene solo i valori (formule/formattazione/più fogli
si perdono, LOSSY); appiattire strutture annidate in tabelle è LOSSY; tutto ciò
che coinvolge XML è approssimato (mappatura elementi/attributi ambigua).
"""

from __future__ import annotations

import json
import shutil

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
            sep = "," if src == "csv" else "\t"
            # Un CSV/TSV è testo SENZA tipi: leggerlo come stringa
            # (dtype=str, keep_default_na=False) preserva zeri iniziali e
            # precisione decimale (01234, 1.10) verso QUALUNQUE target. Con
            # l'inferenza di pandas quei valori cambierebbero (01234→1234,
            # 1.10→1.1) e i verdetti LOSSLESS/"maps faithfully" di csv→tsv,
            # csv→xlsx/ods e csv→json mentirebbero. Preservare il testo è la
            # lettura fedele alla sorgente.
            return pd.read_csv(path, sep=sep, dtype=str, keep_default_na=False)
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
            df.to_csv(path, index=False, header=opts.get("header", True))
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
                json.dump(obj, fh, ensure_ascii=False, indent=indent)
        else:
            with open(job.output_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(obj, fh, allow_unicode=True, sort_keys=False)
        size = job.output_path.stat().st_size
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=self.verdict(job.src_format, job.dst_format))

    def _same_format_copy(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        """src==dst per xlsx/ods/xml: copia FEDELE, mai il round-trip pandas.

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
        # Stesso formato per xlsx/ods/xml: copia fedele (vedi _same_format_copy).
        if job.src_format == job.dst_format and job.src_format in (_SPREADSHEET | {"xml"}):
            return self._same_format_copy(job, progress)
        # Albero->albero (json/yaml): passthrough fedele, mai via pandas.
        if job.src_format in _TREE and job.dst_format in _TREE:
            return self._convert_tree(job, progress)
        progress(ProgressEvent(0.2, "Reading data…"))
        df = self._read(job)
        progress(ProgressEvent(0.6, "Writing data…"))
        self._write(df, job)
        size = job.output_path.stat().st_size
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=self.verdict(job.src_format, job.dst_format))
