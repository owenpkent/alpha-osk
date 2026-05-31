"""Tests for the Snippets feature (src/snippets.py + bridge slots)."""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.snippets import (  # noqa: E402
    _DEFAULT_LABELS,
    MAX_LABEL_LEN,
    MAX_SNIPPETS,
    MAX_VALUE_LEN,
    SnippetStore,
)

# --------------------------------------------------------------------------
#  SnippetStore — pure logic, no Qt
# --------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    return SnippetStore(tmp_path / "snippets.json")


def test_seeds_defaults_on_first_load(store, tmp_path):
    store.load()
    snippets = store.get_all()
    assert [s["label"] for s in snippets] == list(_DEFAULT_LABELS)
    assert all(s["value"] == "" for s in snippets)
    # First load writes the seed file to disk.
    assert (tmp_path / "snippets.json").exists()


def test_save_load_round_trip(tmp_path):
    path = tmp_path / "snippets.json"
    store = SnippetStore(path)
    store.load()
    store.set(0, "Email", "owen@example.com")

    reopened = SnippetStore(path)
    reopened.load()
    assert reopened.get_value(0) == "owen@example.com"
    assert reopened.get_all()[0]["label"] == "Email"


def test_set_updates_entry(store):
    store.load()
    assert store.set(1, "Work email", "work@example.com") is True
    assert store.get_all()[1] == {"label": "Work email", "value": "work@example.com"}


def test_set_out_of_range_returns_false(store):
    store.load()
    assert store.set(999, "x", "y") is False


def test_add_appends(store):
    store.load()
    before = len(store.get_all())
    assert store.add("Signature", "Best, Owen") is True
    after = store.get_all()
    assert len(after) == before + 1
    assert after[-1] == {"label": "Signature", "value": "Best, Owen"}


def test_add_respects_cap(tmp_path):
    store = SnippetStore(tmp_path / "snippets.json")
    store.load()
    # Fill to the cap.
    while len(store.get_all()) < MAX_SNIPPETS:
        assert store.add("x", "y") is True
    assert len(store.get_all()) == MAX_SNIPPETS
    assert store.add("overflow", "z") is False
    assert len(store.get_all()) == MAX_SNIPPETS


def test_delete_removes(store):
    store.load()
    store.set(0, "Name", "Owen")
    store.set(1, "Email", "owen@example.com")
    assert store.delete(0) is True
    assert store.get_all()[0]["label"] == "Email"


def test_delete_out_of_range_returns_false(store):
    store.load()
    assert store.delete(999) is False


def test_move_swaps_neighbours(store):
    store.load()
    labels_before = [s["label"] for s in store.get_all()]
    assert store.move(0, 1) is True
    labels_after = [s["label"] for s in store.get_all()]
    assert labels_after[0] == labels_before[1]
    assert labels_after[1] == labels_before[0]


def test_move_up_from_top_is_noop(store):
    store.load()
    assert store.move(0, -1) is False


def test_move_down_from_bottom_is_noop(store):
    store.load()
    last = len(store.get_all()) - 1
    assert store.move(last, 1) is False


def test_move_rejects_bad_direction(store):
    store.load()
    assert store.move(0, 2) is False


def test_label_and_value_length_caps(store):
    store.load()
    store.set(0, "L" * (MAX_LABEL_LEN + 50), "V" * (MAX_VALUE_LEN + 50))
    entry = store.get_all()[0]
    assert len(entry["label"]) == MAX_LABEL_LEN
    assert len(entry["value"]) == MAX_VALUE_LEN


def test_label_newlines_collapsed(store):
    store.load()
    store.set(0, "multi\nline\rlabel", "value")
    assert "\n" not in store.get_all()[0]["label"]
    assert "\r" not in store.get_all()[0]["label"]


def test_value_preserves_newlines(store):
    """A value may legitimately be a multi-line block (e.g. an address)."""
    store.load()
    store.set(0, "Address", "123 Main St\nApt 4\nAnytown")
    assert store.get_value(0) == "123 Main St\nApt 4\nAnytown"


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "snippets.json"
    path.write_text("this is not json {{{", encoding="utf-8")
    store = SnippetStore(path)
    store.load()
    assert [s["label"] for s in store.get_all()] == list(_DEFAULT_LABELS)


def test_oversize_file_rejected(tmp_path):
    path = tmp_path / "snippets.json"
    # Write a valid-but-huge file (> 1 MB cap).
    payload = {"version": 1, "snippets": [{"label": "x", "value": "y" * 2000}
                                          for _ in range(1000)]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert path.stat().st_size > 1024 * 1024
    store = SnippetStore(path)
    store.load()
    # Falls back to defaults rather than loading the oversized file.
    assert [s["label"] for s in store.get_all()] == list(_DEFAULT_LABELS)


def test_empty_entries_dropped_on_load(tmp_path):
    path = tmp_path / "snippets.json"
    payload = {"version": 1, "snippets": [
        {"label": "", "value": ""},
        {"label": "Keep", "value": "kept"},
        {"label": "", "value": ""},
    ]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = SnippetStore(path)
    store.load()
    assert [s["label"] for s in store.get_all()] == ["Keep"]


def test_all_empty_file_reseeds(tmp_path):
    path = tmp_path / "snippets.json"
    payload = {"version": 1, "snippets": [{"label": "", "value": ""}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    store = SnippetStore(path)
    store.load()
    assert [s["label"] for s in store.get_all()] == list(_DEFAULT_LABELS)


def test_get_value_out_of_range_returns_none(store):
    store.load()
    assert store.get_value(-1) is None
    assert store.get_value(999) is None


def test_reload_from_disk_picks_up_external_change(tmp_path):
    path = tmp_path / "snippets.json"
    store = SnippetStore(path)
    store.load()
    # Simulate a data import overwriting the file.
    payload = {"version": 1, "snippets": [{"label": "Imported", "value": "yes"}]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    store.reload_from_disk()
    assert [s["label"] for s in store.get_all()] == ["Imported"]


def test_get_all_returns_copies(store):
    """Mutating the returned list must not corrupt the store."""
    store.load()
    snippets = store.get_all()
    snippets[0]["label"] = "MUTATED"
    assert store.get_all()[0]["label"] != "MUTATED"


# --------------------------------------------------------------------------
#  Bridge slots — insertSnippet routing
# --------------------------------------------------------------------------

@pytest.fixture
def bridge():
    # Construct the bridge with a mocked synthesizer, mirroring
    # tests/test_keyboard_bridge.py.  No qtbot / QApplication needed — a
    # QObject builds fine without an event loop, and these tests only
    # exercise plain slot calls.
    from src.keyboard_bridge import KeyboardBridge
    with patch("src.keyboard_bridge.create_key_synthesizer") as mock_synth:
        mock_synth.return_value = MagicMock()
        b = KeyboardBridge()
        yield b
        b.shutdown()


def _attach_temp_store(bridge, tmp_path):
    bridge._snippets = SnippetStore(tmp_path / "snippets.json")
    bridge._snippets.load()


def test_insert_snippet_sends_value(bridge, tmp_path):
    _attach_temp_store(bridge, tmp_path)
    bridge.setSnippet(0, "Email", "owen@example.com")
    bridge._synth.send_text.reset_mock()
    bridge.insertSnippet(0)
    bridge._synth.send_text.assert_called_once_with("owen@example.com")


def test_insert_empty_snippet_is_noop(bridge, tmp_path):
    _attach_temp_store(bridge, tmp_path)  # default slot 0 has an empty value
    bridge._synth.send_text.reset_mock()
    bridge.insertSnippet(0)
    bridge._synth.send_text.assert_not_called()


def test_insert_snippet_blocked_in_edit_mode(bridge, tmp_path):
    _attach_temp_store(bridge, tmp_path)
    bridge.setSnippet(0, "Email", "x@y.com")
    bridge.setEditMode(True)
    bridge._synth.send_text.reset_mock()
    bridge.insertSnippet(0)
    bridge._synth.send_text.assert_not_called()
    bridge.setEditMode(False)


def test_get_snippets_slot_returns_list(bridge, tmp_path):
    _attach_temp_store(bridge, tmp_path)
    result = bridge.getSnippets()
    assert isinstance(result, list)
    assert [s["label"] for s in result] == list(_DEFAULT_LABELS)
