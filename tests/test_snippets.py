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
    SNIPPET_COLORS,
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
    assert store.get_all()[1] == {
        "label": "Work email",
        "value": "work@example.com",
        "color": "",
    }


def test_set_out_of_range_returns_false(store):
    store.load()
    assert store.set(999, "x", "y") is False


def test_add_appends(store):
    store.load()
    before = len(store.get_all())
    assert store.add("Signature", "Best, Owen") is True
    after = store.get_all()
    assert len(after) == before + 1
    assert after[-1] == {"label": "Signature", "value": "Best, Owen", "color": ""}


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


def test_value_strips_carriage_return(store):
    """A raw \\r has no legitimate place in a typed value: on Linux
    xdotool turns an embedded newline into a real Return keypress, and a
    stray \\r reaching a Windows console behaves the same way. \\n stays
    permitted (see test_value_preserves_newlines above); only \\r is
    stripped."""
    store.load()
    store.set(0, "Address", "123 Main St\r\nApt 4\r\nAnytown")
    value = store.get_value(0)
    assert "\r" not in value
    assert value == "123 Main St\nApt 4\nAnytown"


def test_value_strips_other_c0_control_characters(store):
    """Every C0 control character except tab and newline is stripped."""
    store.load()
    store.set(0, "Weird", "a\x07b\x1bc\td\ne")
    assert store.get_value(0) == "abc\td\ne"


def test_value_strips_del(store):
    """DEL (0x7F) is a control character too, even though it sorts above
    the C0 range: an `ord(ch) >= 0x20` filter alone would let it through."""
    store.load()
    store.set(0, "Weird", "a\x7fb")
    assert store.get_value(0) == "ab"


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "snippets.json"
    path.write_text("this is not json {{{", encoding="utf-8")
    store = SnippetStore(path)
    store.load()
    assert [s["label"] for s in store.get_all()] == list(_DEFAULT_LABELS)


def test_oversize_file_rejected(tmp_path):
    path = tmp_path / "snippets.json"
    # Write a valid-but-huge file (> 1 MB cap).
    payload = {"version": 1, "snippets": [{"label": "x", "value": "y" * 2000} for _ in range(1000)]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert path.stat().st_size > 1024 * 1024
    store = SnippetStore(path)
    store.load()
    # Falls back to defaults rather than loading the oversized file.
    assert [s["label"] for s in store.get_all()] == list(_DEFAULT_LABELS)


def test_empty_entries_dropped_on_load(tmp_path):
    path = tmp_path / "snippets.json"
    payload = {
        "version": 1,
        "snippets": [
            {"label": "", "value": ""},
            {"label": "Keep", "value": "kept"},
            {"label": "", "value": ""},
        ],
    }
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


# --------------------------------------------------------------------------
#  Colour tags
#
#  Tags exist so a grid of a dozen snippets can be scanned by colour
#  instead of read.  They are stored as *names* from an allow-list rather
#  than as the hex the UI draws, because snippets.json is replace-on-import
#  from an archive the user picked and the stored string ends up in a QML
#  `color` property.  Every positive case below is paired with the near-miss
#  it has to reject.
# --------------------------------------------------------------------------


def test_seeded_snippets_are_untagged(store):
    store.load()
    assert [s["color"] for s in store.get_all()] == [""] * len(_DEFAULT_LABELS)


def test_set_color_tags_and_persists(tmp_path):
    path = tmp_path / "snippets.json"
    store = SnippetStore(path)
    store.load()
    assert store.set_color(1, "blue") is True

    reopened = SnippetStore(path)
    reopened.load()
    assert reopened.get_all()[1]["color"] == "blue"


def test_set_color_rejects_a_name_outside_the_allow_list(store):
    """A tag the store does not know degrades to untagged, never verbatim.

    The inverse of the test above: without this, a hand-edited or imported
    archive could put an arbitrary string into a QML `color` property.
    """
    store.load()
    store.set_color(1, "blue")
    store.set_color(1, "#ff0000")
    assert store.get_all()[1]["color"] == ""


def test_set_color_is_a_noop_for_the_tag_already_set(store):
    """Returns False so the bridge does not emit, and QML does not rebuild."""
    store.load()
    assert store.set_color(1, "green") is True
    assert store.set_color(1, "green") is False


def test_set_color_out_of_range_returns_false(store):
    store.load()
    assert store.set_color(999, "red") is False


def test_editing_a_snippet_keeps_its_tag(store):
    """The editor edits label + value only; a save must not clear the tag."""
    store.load()
    store.set_color(1, "purple")
    store.set(1, "Work email", "work@example.com")
    assert store.get_all()[1]["color"] == "purple"


def test_set_can_clear_the_tag_explicitly(store):
    store.load()
    store.set_color(1, "purple")
    store.set(1, "Work email", "work@example.com", color="")
    assert store.get_all()[1]["color"] == ""


def test_a_tag_moves_with_its_snippet(store):
    store.load()
    store.set_color(0, "amber")
    store.move(0, 1)
    assert store.get_all()[1]["color"] == "amber"
    assert store.get_all()[0]["color"] == ""


@pytest.mark.parametrize(
    "hostile", ["#ff0000", "red;background:url(x)", "reddish", 5, None, {}, []]
)
def test_a_colour_from_an_untrusted_file_never_survives_load(tmp_path, hostile):
    """snippets.json is replace-on-import, so its colours are attacker-chosen."""
    path = tmp_path / "snippets.json"
    payload = {
        "version": 2,
        "snippets": [{"label": "Email", "value": "a@b.com", "color": hostile}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    store = SnippetStore(path)
    store.load()
    assert store.get_all()[0]["color"] == ""
    # The snippet itself is kept: a bad tag is not a reason to lose data.
    assert store.get_all()[0]["value"] == "a@b.com"


def test_a_tag_is_normalised_into_the_allow_list(store):
    """Case and surrounding whitespace are normalised, not rejected.

    The property is about the *output*: whatever goes in, what is stored is
    a member of the allow-list or the empty string, so QML is never handed a
    string the store did not choose.
    """
    store.load()
    assert store.set_color(1, "  BLUE  ") is True
    assert store.get_all()[1]["color"] == "blue"


def test_a_known_tag_from_a_file_is_kept(tmp_path):
    """The inverse of the case above: an allow-list that rejected
    everything would satisfy it while making tags a no-op."""
    path = tmp_path / "snippets.json"
    payload = {
        "version": 2,
        "snippets": [{"label": "Email", "value": "a@b.com", "color": "green"}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    store = SnippetStore(path)
    store.load()
    assert store.get_all()[0]["color"] == "green"


def test_a_file_written_before_tags_existed_loads_untagged(tmp_path):
    path = tmp_path / "snippets.json"
    payload = {"version": 1, "snippets": [{"label": "Email", "value": "a@b.com"}]}
    path.write_text(json.dumps(payload), encoding="utf-8")

    store = SnippetStore(path)
    store.load()
    assert store.get_all()[0] == {"label": "Email", "value": "a@b.com", "color": ""}


# --------------------------------------------------------------------------
#  Bridge slots: colour tags and the cap
# --------------------------------------------------------------------------


def test_get_snippet_colors_matches_the_store_allow_list(bridge):
    colors = bridge.getSnippetColors()
    assert colors == list(SNIPPET_COLORS)
    # QML draws the first entry as the "no tag" swatch.
    assert colors[0] == ""


def test_set_snippet_color_emits_snippets_changed(bridge, tmp_path):
    _attach_temp_store(bridge, tmp_path)
    seen = []
    bridge.snippetsChanged.connect(seen.append)

    bridge.setSnippetColor(1, "blue")
    assert len(seen) == 1
    assert seen[0][1]["color"] == "blue"

    # An unchanged tag must not emit: QML rebuilds the whole grid on it.
    bridge.setSnippetColor(1, "blue")
    assert len(seen) == 1


def test_bridge_set_snippet_keeps_the_colour_tag(bridge, tmp_path):
    """The editor's save path. Regression: it used to replace the record."""
    _attach_temp_store(bridge, tmp_path)
    bridge.setSnippetColor(1, "amber")
    bridge.setSnippet(1, "Work email", "work@example.com")
    assert bridge.getSnippets()[1]["color"] == "amber"


def test_get_snippet_limit_matches_the_store_cap(bridge):
    """QML disables Add against this; a drift would make Add silently inert
    one snippet early, or open the editor on somebody else's snippet."""
    assert bridge.getSnippetLimit() == MAX_SNIPPETS
