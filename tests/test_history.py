"""Test della cronologia: persistenza, cap/potatura, robustezza."""

import json

from converter.core.history import HistoryEntry, HistoryStore


def test_roundtrip(tmp_path):
    store = HistoryStore(path=tmp_path / "h.json")
    assert store.load() == []
    store.append(HistoryEntry("a.png", "png", "jpg", {"quality": 85},
                              result="ok", output_size=10, verdict="lossy"))
    entries = store.load()
    assert len(entries) == 1
    assert entries[0].dst_format == "jpg"
    assert entries[0].result == "ok"
    assert entries[0].options == {"quality": 85}


def test_cap_prunes_oldest(tmp_path):
    path = tmp_path / "h.json"
    store = HistoryStore(path=path, max_entries=3)
    for i in range(5):
        store.append(HistoryEntry(f"f{i}.png", "png", "jpg"))
    entries = store.load()
    assert len(entries) == 3
    # Tenute le 3 più recenti, potate le più vecchie (f0, f1).
    assert [e.source_path for e in entries] == ["f2.png", "f3.png", "f4.png"]
    # Il file su disco non cresce oltre il cap: potatura persistita, non solo in load().
    on_disk = json.loads(path.read_text(encoding="utf-8"))["entries"]
    assert len(on_disk) == 3
    assert [e["source_path"] for e in on_disk] == ["f2.png", "f3.png", "f4.png"]


def test_failed_entry_preserves_error(tmp_path):
    store = HistoryStore(path=tmp_path / "h.json")
    store.append(HistoryEntry("bad.png", "png", "jpg", result="failed",
                              error="UnidentifiedImageError: cannot identify image"))
    entry = store.load()[0]
    assert entry.result == "failed"
    assert "cannot identify" in entry.error


def test_clear(tmp_path):
    store = HistoryStore(path=tmp_path / "h.json")
    store.append(HistoryEntry("a.png", "png", "jpg"))
    store.clear()
    assert store.load() == []


def test_version_field(tmp_path):
    path = tmp_path / "h.json"
    HistoryStore(path=path).append(HistoryEntry("a.png", "png", "jpg"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("not json at all {{{")
    assert HistoryStore(path=path).load() == []


def test_valid_json_with_wrong_shape_is_ignored(tmp_path):
    """JSON valido ma non un oggetto (o "entries" non lista) non deve far
    crashare l'apertura della cronologia."""
    path = tmp_path / "h.json"
    path.write_text('[{"source_path": "a.png"}]')
    assert HistoryStore(path=path).load() == []
    path.write_text('{"entries": 42}')
    assert HistoryStore(path=path).load() == []
    path.write_text('{"entries": ["not-a-dict", {"source_path": "a.png", '
                    '"source_format": "png", "dst_format": "jpg"}]}')
    assert [e.source_path for e in HistoryStore(path=path).load()] == ["a.png"]
