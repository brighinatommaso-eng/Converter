"""Test dei preset: persistenza, riconciliazione opzioni, validità."""

import json

import converter.converters  # noqa: F401  (popola il registry)
from converter.core.presets import Preset, PresetStore, reconcile_options


def test_store_roundtrip(tmp_path):
    store = PresetStore(path=tmp_path / "presets.json")
    assert store.load() == []
    store.add(Preset("Web JPG 85%", "jpg", {"quality": 85}, strip_metadata=False))
    store.add(Preset("Podcast MP3", "mp3", {"bitrate": "192k"}, strip_metadata=True))
    loaded = store.load()
    assert {p.name for p in loaded} == {"Web JPG 85%", "Podcast MP3"}
    mp3 = next(p for p in loaded if p.name == "Podcast MP3")
    assert mp3.dst_format == "mp3" and mp3.strip_metadata is True


def test_add_same_name_overwrites(tmp_path):
    store = PresetStore(path=tmp_path / "presets.json")
    store.add(Preset("X", "jpg", {"quality": 50}))
    store.add(Preset("X", "jpg", {"quality": 90}))
    presets = store.load()
    assert len(presets) == 1
    assert presets[0].options["quality"] == 90


def test_delete(tmp_path):
    store = PresetStore(path=tmp_path / "presets.json")
    store.add(Preset("X", "jpg", {}))
    store.delete("X")
    assert store.load() == []


def test_persisted_file_has_version(tmp_path):
    path = tmp_path / "presets.json"
    PresetStore(path=path).add(Preset("X", "jpg", {"quality": 80}))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert data["presets"][0]["name"] == "X"


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "presets.json"
    path.write_text("{ this is not valid json")
    assert PresetStore(path=path).load() == []  # niente crash


def test_valid_json_with_wrong_shape_is_ignored(tmp_path):
    """JSON valido ma non un oggetto (o "presets" non lista) non deve crashare
    l'avvio: la GUI carica i preset mentre costruisce la finestra."""
    path = tmp_path / "presets.json"
    path.write_text('["not", "a", "dict"]')
    assert PresetStore(path=path).load() == []
    path.write_text('{"presets": {"name": "X"}}')
    assert PresetStore(path=path).load() == []
    path.write_text('{"presets": ["not-a-preset", {"name": "X", "dst_format": "jpg"}]}')
    loaded = PresetStore(path=path).load()
    assert [p.name for p in loaded] == ["X"]  # la voce malformata si salta


def test_reconcile_drops_unknown_and_fills_defaults():
    # 'bogus' non è nello schema JPG e va scartato; 'quality' valido va tenuto.
    result = reconcile_options("png", "jpg", {"quality": 70, "bogus": 1})
    assert result == {"quality": 70}
    # Opzione mancante -> riempita col default dello schema (95).
    assert reconcile_options("png", "jpg", {}) == {"quality": 95}


def test_reconcile_simultaneous_add_and_remove(monkeypatch):
    # Scenario reale di aggiornamento converter: un preset salvato col VECCHIO
    # schema {quality, progressive}. Il NUOVO schema rimuove 'progressive' e
    # aggiunge 'sharpen'. La riconciliazione, in un'unica passata, deve:
    #   - preservare 'quality' (ancora valido, valore utente 70),
    #   - scartare 'progressive' (rimosso),
    #   - aggiungere 'sharpen' col default del nuovo schema.
    from converter.core import registry
    from converter.core.options import ChoiceOption, IntOption

    conv = registry.resolve("png", "jpg")

    def new_schema(self, src, dst):
        return [
            IntOption("quality", "Quality", 95, 1, 100),
            ChoiceOption("sharpen", "Sharpen", "none",
                         choices=[("none", "None"), ("soft", "Soft")]),
        ]

    monkeypatch.setattr(type(conv), "options_schema", new_schema)

    saved = {"quality": 70, "progressive": True}  # preset col vecchio schema
    result = reconcile_options("png", "jpg", saved)
    assert result == {"quality": 70, "sharpen": "none"}


def test_preset_validity():
    assert Preset("a", "jpg", {}).valid is True
    assert Preset("a", "pdf", {}).valid is True          # ora prodotto dal converter documenti (Tier E)
    assert Preset("a", "nope", {}).valid is False        # formato inesistente: nessun converter lo produce
    assert Preset("a", "epub", {}).valid is True         # Fase 2: ora prodotto da MarkupConverter (pandoc)
