"""Converter di modelli 3D basato su trimesh (MIT, pure-Python; dip. dura: numpy).

Come image.py gira IN-PROCESS senza tool esterni, quindi vive bene nel modello
PORTABLE. Verdetto onesto: il badge riflette il passo di conversione — STL scarta
colori/materiali (triangle soup), OBJ appiattisce il grafo di scena, glTF/GLB
preservano geometria+materiali+scena, PLY tiene geometria e colori per-vertice.

L'appiattimento di una scena multi-oggetto verso un formato a mesh singola
(stl/obj/ply) è dichiarato a RUNTIME in convert(), esattamente come image.py
declassa il verdetto quando una sorgente multi-frame va verso un formato a frame
singolo.
"""

from __future__ import annotations

from ..core.base import Converter
from ..core.deps import DependencyStatus, has_module
from ..core.job import ConversionJob, ConversionResult, ProgressCallback, ProgressEvent
from ..core.options import ChoiceOption, Option
from ..core.quality import QualityVerdict, lossless, lossy, unsupported
from ..core.registry import register

# Rilevamento delle dipendenze al caricamento del modulo (come image.py per HEIC).
# trimesh è il motore; lxml è opzionale e serve SOLO al 3mf (import ed export).
_TRIMESH_OK = has_module("trimesh")
_LXML_OK = has_module("lxml")

_INPUT = frozenset({"obj", "stl", "ply", "glb", "gltf", "3mf"})
_OUTPUT = frozenset({"obj", "stl", "ply", "glb", "gltf"})
_SCENE_DST = ("glb", "gltf")       # formati che reggono il grafo di scena
_SINGLE_MESH_DST = ("stl", "obj", "ply")  # formati a mesh singola (appiattiscono)


@register
class Mesh3DConverter(Converter):
    category = "model3d"
    name = "3D models (trimesh)"
    input_formats = _INPUT
    output_formats = _OUTPUT
    priority = 0

    # --- Disponibilità ---

    @classmethod
    def available(cls) -> DependencyStatus:
        if _TRIMESH_OK:
            return DependencyStatus(True)
        return DependencyStatus(False, "3D conversion needs the 'trimesh' package.",
                                "pip install trimesh")

    def format_available(self, format_id: str) -> tuple[bool, str]:
        # Il 3mf (import) richiede lxml: senza, resta in grigio col motivo.
        if format_id == "3mf" and not _LXML_OK:
            return False, "3MF needs the optional 'lxml' package (pip install lxml)."
        return True, ""

    # --- Qualità ---

    def verdict(self, src: str, dst: str, options: dict | None = None) -> QualityVerdict:
        if src not in _INPUT or dst not in _OUTPUT:
            return unsupported()
        if src == dst:
            return lossless("Same format; geometry is copied unchanged.")
        if dst == "stl":
            return lossy("STL stores only triangle geometry; colours, materials and "
                         "per-vertex normals are discarded.")
        if dst == "obj":
            return lossy("OBJ flattens the scene graph and writes materials to a separate "
                         ".mtl file; texture references may be lost.")
        if dst in ("glb", "gltf"):
            if src in ("glb", "gltf"):
                return lossless("glTF/GLB preserve geometry, materials and scene graph.")
            return lossy("Geometry is preserved, but source materials are approximated "
                         "into the glTF PBR model.")
        if dst == "ply":
            if src in ("obj", "glb", "gltf", "3mf"):
                return lossy("PLY keeps geometry and vertex colours, but textures and "
                             "material definitions are dropped.")
            return lossless("PLY stores exact geometry and vertex colours.")
        return unsupported()

    # --- Opzioni dichiarative ---

    def options_schema(self, src: str, dst: str) -> list[Option]:
        if dst == "stl":
            return [ChoiceOption("encoding", "STL encoding", "binary",
                                 choices=[("binary", "Binary (smaller, default)"),
                                          ("ascii", "ASCII (human-readable)")])]
        if dst == "ply":
            return [ChoiceOption("encoding", "PLY encoding", "binary",
                                 choices=[("binary", "Binary (smaller, default)"),
                                          ("ascii", "ASCII (human-readable)")])]
        return []

    # --- Esecuzione ---

    def convert(self, job: ConversionJob, progress: ProgressCallback) -> ConversionResult:
        import trimesh

        progress(ProgressEvent(0.1, "Reading model…"))
        # Un solo load: trimesh.load ritorna Trimesh o Scene a seconda del contenuto.
        loaded = trimesh.load(str(job.src_path))
        # Sorgente multi-oggetto: verso una mesh singola i vari oggetti vengono
        # fusi (grafo di scena perso). Serve per il declassamento a runtime sotto.
        multi = isinstance(loaded, trimesh.Scene) and len(loaded.geometry) > 1

        if job.dst_format in _SCENE_DST:
            # glb/gltf reggono il grafo: esporta l'oggetto così com'è (scena o mesh).
            export_obj = loaded
        elif isinstance(loaded, trimesh.Scene):
            # Verso stl/obj/ply serve UNA mesh: to_geometry() concatena la scena
            # (sostituisce il deprecato dump(concatenate=True)).
            export_obj = loaded.to_geometry()
        else:
            export_obj = loaded

        # I formati mesh non hanno un canale metadati tipo EXIF; se richiesto lo
        # strip, si azzera solo il piccolo dict metadata di trimesh.
        if job.strip_metadata and hasattr(export_obj, "metadata"):
            export_obj.metadata = {}

        progress(ProgressEvent(0.5, "Encoding…"))
        encoding = (job.options or {}).get("encoding", "binary")
        if job.dst_format == "stl":
            # trimesh: file_type 'stl' = binario, 'stl_ascii' = ASCII (verificato 4.12.2).
            file_type = "stl_ascii" if encoding == "ascii" else "stl"
            export_obj.export(str(job.output_path), file_type=file_type)
        elif job.dst_format == "ply":
            # PLY: l'ASCII/binario si sceglie col kwarg encoding (verificato 4.12.2).
            export_obj.export(str(job.output_path), file_type="ply", encoding=encoding)
        else:  # obj, glb, gltf
            export_obj.export(str(job.output_path), file_type=job.dst_format)

        size = job.output_path.stat().st_size
        verdict = self.verdict(job.src_format, job.dst_format, job.options)
        # Onestà: scena multi-oggetto verso un formato a mesh singola = grafo perso,
        # a prescindere dal verdetto per-coppia (come il multi-frame di image.py).
        if multi and job.dst_format in _SINGLE_MESH_DST:
            verdict = lossy(f"Source contains multiple objects; they are merged into one "
                            f"mesh in {job.dst_format.upper()} (scene graph is lost).")
        return ConversionResult(ok=True, output_path=job.output_path, output_size=size,
                                verdict=verdict)
