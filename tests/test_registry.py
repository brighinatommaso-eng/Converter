"""Test del registry: auto-registrazione, target dinamici, coppie impossibili."""

import converter.converters  # noqa: F401  (import per side-effect: registrazione)
from converter.core.registry import resolve, targets_for


def test_png_targets_include_lossless_and_lossy():
    targets = {t.dst_format: t for t in targets_for("png")}
    assert "bmp" in targets and targets["bmp"].verdict.level == "lossless"
    assert "jpg" in targets and targets["jpg"].verdict.level == "lossy"


def test_impossible_cross_category_pair_absent():
    # mp4 -> xlsx non ha senso: nessun converter lo offre.
    assert resolve("mp4", "xlsx") is None
    assert "xlsx" not in {t.dst_format for t in targets_for("mp4")}


def test_resolve_known_pair():
    assert resolve("png", "bmp") is not None
    assert resolve("csv", "json") is not None


def test_every_options_schema_builds():
    # Regressione: options_schema deve costruirsi senza errori per OGNI coppia
    # input->output di ogni converter (es. argomenti posizionali delle Option).
    from converter.core.registry import _all

    for conv in _all():
        for src in conv.input_formats:
            for dst in conv.output_formats:
                schema = conv.options_schema(src, dst)
                assert isinstance(schema, list)
                for opt in schema:
                    assert opt.key and opt.label


def test_svg_target_listed_but_unavailable():
    # SVG (tracing) è rinviato: compare ma marcato non disponibile.
    targets = {t.dst_format: t for t in targets_for("png")}
    assert "svg" in targets
    assert targets["svg"].available is False
