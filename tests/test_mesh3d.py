"""Test end-to-end del converter di modelli 3D (trimesh).

Il modulo è saltato interamente se trimesh non è installato (come i test audio
sono skipif ffmpeg assente): il converter è opzionale.
"""

import pytest

trimesh = pytest.importorskip("trimesh")

from converter.converters import mesh3d  # noqa: E402
from converter.core.detection import detect  # noqa: E402
from converter.core.job import ConversionJob  # noqa: E402
from converter.core.pipeline import run_job  # noqa: E402
from converter.core.registry import resolve, targets_for  # noqa: E402


def _box(path):
    trimesh.creation.box().export(str(path))
    return path


# --- 1) Routing nel registry ---

def test_resolve_routes_mesh_pairs():
    assert isinstance(resolve("stl", "obj"), mesh3d.Mesh3DConverter)
    assert isinstance(resolve("glb", "ply"), mesh3d.Mesh3DConverter)


def test_targets_for_glb_offers_mesh_outputs():
    tg = {t.dst_format: t.available for t in targets_for("glb")}
    for dst in ("obj", "stl", "ply", "gltf"):
        assert tg.get(dst) is True, f"{dst} dovrebbe essere un target disponibile da glb"
    # glb non esporta 3mf: non deve comparire tra i target.
    assert "3mf" not in tg


# --- 2) Roundtrip reale per ogni formato di output ---

@pytest.mark.parametrize("dst", ["obj", "stl", "ply", "glb", "gltf"])
def test_roundtrip_preserves_vertex_count(tmp_path, dst):
    src = _box(tmp_path / "in.stl")
    out = tmp_path / f"out.{dst}"
    result = run_job(ConversionJob(src_path=src, src_format="stl", dst_format=dst,
                                   output_path=out, options={}))
    assert result.ok and out.exists(), result.error
    back = trimesh.load(str(out), force="mesh")
    assert len(back.vertices) == 8  # il cubo ha 8 vertici, invariati


# --- 3) Matrice dei verdetti (strutturali) ---

@pytest.mark.parametrize("src,dst,level", [
    ("glb", "gltf", "lossless"),
    ("obj", "stl", "lossy"),
    ("stl", "stl", "lossless"),
    ("stl", "glb", "lossy"),
    ("obj", "ply", "lossy"),
    ("stl", "ply", "lossless"),
])
def test_verdict_matrix(src, dst, level):
    v = mesh3d.Mesh3DConverter().verdict(src, dst)
    assert v.level == level, f"{src}->{dst}: atteso {level}, ottenuto {v.level} ({v.reason})"


# --- 4) Override a runtime: scena multi-oggetto -> mesh singola = lossy ---

def test_scene_flattening_is_lossy_at_runtime(tmp_path):
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box(), geom_name="a")
    scene.add_geometry(trimesh.creation.icosphere(), geom_name="b")
    glb = tmp_path / "multi.glb"
    scene.export(str(glb))

    out = tmp_path / "flat.stl"
    result = run_job(ConversionJob(src_path=glb, src_format="glb", dst_format="stl",
                                   output_path=out, options={}))
    assert result.ok
    assert result.verdict.level == "lossy"
    assert "multiple objects" in result.verdict.reason.lower()


def test_scene_to_glb_stays_lossless_from_glb(tmp_path):
    # glb->glb con più oggetti NON è un appiattimento (glb regge la scena):
    # niente override, il verdict resta quello per-coppia (lossless).
    scene = trimesh.Scene()
    scene.add_geometry(trimesh.creation.box())
    scene.add_geometry(trimesh.creation.icosphere())
    glb = tmp_path / "multi.glb"
    scene.export(str(glb))
    out = tmp_path / "out.glb"
    result = run_job(ConversionJob(src_path=glb, src_format="glb", dst_format="glb",
                                   output_path=out, options={}))
    assert result.ok and result.verdict.level == "lossless"


def test_same_format_is_a_byte_copy(tmp_path):
    """src==dst dichiara "lossless": deve essere una copia fedele, non un
    round-trip trimesh che riscrive il file (e appiattisce le scene multi-oggetto)."""
    src = _box(tmp_path / "in.glb")
    out = tmp_path / "out.glb"
    result = run_job(ConversionJob(src_path=src, src_format="glb", dst_format="glb",
                                   output_path=out, options={}))
    assert result.ok and result.verdict.level == "lossless"
    assert out.read_bytes() == src.read_bytes()


# --- 5) Gating del 3mf su lxml (mock, non disinstallando) ---

def test_3mf_greyed_when_lxml_absent(monkeypatch):
    monkeypatch.setattr(mesh3d, "_LXML_OK", False)
    ok, reason = mesh3d.Mesh3DConverter().format_available("3mf")
    assert ok is False
    assert "lxml" in reason.lower()
    # 3mf è solo input: come SORGENTE i suoi target vanno in grigio quando manca lxml.
    # (targets_for interroga format_available(src) per ogni coppia.)
    targets = targets_for("3mf")
    assert targets, "3mf dovrebbe comunque offrire target (in grigio), non sparire"
    assert all(not t.available for t in targets)
    assert any("lxml" in t.reason_unavailable.lower() for t in targets)


def test_3mf_available_when_lxml_present():
    # Precondizione dell'ambiente di test: lxml è presente (richiesto da python-docx).
    if not mesh3d._LXML_OK:
        pytest.skip("lxml non installato in questo ambiente")
    ok, _ = mesh3d.Mesh3DConverter().format_available("3mf")
    assert ok is True


# --- 6) Detection dei formati mesh ---

def test_detect_glb_ply_stl_gltf(tmp_path):
    assert detect(_box(tmp_path / "b.glb")).format_id == "glb"
    assert detect(_box(tmp_path / "b.ply")).format_id == "ply"
    # STL binario: nessun magic affidabile -> riconosciuto dall'estensione.
    assert detect(_box(tmp_path / "b.stl")).format_id == "stl"
    # glTF è JSON: va distinto dai dati json.
    d = detect(_box(tmp_path / "b.gltf"))
    assert d.format_id == "gltf" and d.category == "model3d"


# --- File collaterali: mai nomi fissi condivisi (review 2026-10-26) -----------

def test_gltf_is_a_single_self_contained_file(tmp_path):
    """trimesh scriveva gltf_buffer_0.bin a nome fisso: in batch si sovrascrivevano."""
    src = _box(tmp_path / "box.stl")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    for name in ("a.gltf", "b.gltf"):
        assert run_job(ConversionJob(src, "stl", "gltf", out_dir / name)).ok
    assert sorted(p.name for p in out_dir.iterdir()) == ["a.gltf", "b.gltf"]
    assert len(trimesh.load(out_dir / "a.gltf").geometry) >= 1


def test_obj_side_files_are_named_after_each_output(tmp_path):
    import numpy as np
    from PIL import Image

    box = trimesh.creation.box()
    uv = np.random.default_rng(0).random((len(box.vertices), 2))
    textured = trimesh.Trimesh(box.vertices, box.faces, visual=trimesh.visual.TextureVisuals(
        uv=uv, image=Image.new("RGB", (8, 8), "red")))
    src = tmp_path / "t.glb"
    textured.export(src)
    for name in ("t.obj", "t (1).obj"):
        result = run_job(ConversionJob(src, "glb", "obj", tmp_path / name))
        assert result.ok, result.error
    names = {p.name for p in tmp_path.iterdir()}
    assert {"t.mtl", "t_material_0.png", "t_(1).mtl", "t_(1)_material_0.png"} <= names
    assert "material.mtl" not in names
    assert trimesh.load(tmp_path / "t (1).obj").visual.kind == "texture"
