"""Tests for programmable function-key actions.

Every positive case here is paired with the near-miss it must reject,
and the pairs that bite are the ones where a payload *looks* like a valid
action and is not: a hotkey with no action key, a modifier name the synth
layer has never heard of, a label carrying a newline. Those are the
shapes a hand-edited ``key_actions.json`` actually produces.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Sequence, Tuple

import pytest

from src.key_actions import (
    ACTION_TYPES,
    FUNCTION_KEYS,
    MAX_LABEL_LEN,
    MAX_TEXT_LEN,
    UNBOUND_FUNCTION_KEYS,
    ActionExecutor,
    KeyActionStore,
    action_type_info,
    clean_action,
    describe_action,
    get_action_type,
)


class RecordingExecutor(ActionExecutor):
    """The two-method surface an action type is allowed to depend on.

    Five lines, which is the point of the executor indirection: the action
    types are testable without constructing a ``KeyboardBridge`` (a 20k-word
    dictionary and a SymSpell index) to observe them.
    """

    def __init__(self) -> None:
        self.chords: List[Tuple[str, List[str]]] = []
        self.texts: List[str] = []

    def send_chord(self, key: str, modifiers: Sequence[str]) -> None:
        self.chords.append((key, list(modifiers)))

    def send_text(self, text: str) -> None:
        self.texts.append(text)


@pytest.fixture
def store(tmp_path: Path) -> KeyActionStore:
    return KeyActionStore(tmp_path / "key_actions.json")


class TestTheKeySet:
    """Which keys exist, and which are safe to reassign."""

    def test_f1_through_f24_are_programmable(self) -> None:
        assert FUNCTION_KEYS == tuple(f"f{n}" for n in range(1, 25))

    def test_the_unbound_set_is_f13_upwards(self) -> None:
        """F1-F12 carry real bindings in real software; F13-F24 do not.

        The distinction drives what the editor tells the user before they
        commit, so it is worth pinning rather than leaving to a comment:
        reassigning F5 costs them refresh in every app, reassigning F17
        costs nothing.
        """
        assert UNBOUND_FUNCTION_KEYS == tuple(f"f{n}" for n in range(13, 25))
        assert set(UNBOUND_FUNCTION_KEYS) < set(FUNCTION_KEYS)
        assert "f12" not in UNBOUND_FUNCTION_KEYS


class TestTheRegistry:
    """The action-type registry is what QML and the bridge both drive off."""

    def test_every_type_is_described_for_qml(self) -> None:
        info = action_type_info()
        assert [entry["id"] for entry in info] == [a.id for a in ACTION_TYPES]
        for entry in info:
            assert entry["label"]
            assert entry["description"]
            assert isinstance(entry["fields"], list)

    def test_the_shipped_types_are_key_hotkey_and_text(self) -> None:
        assert {a.id for a in ACTION_TYPES} == {"key", "hotkey", "text"}

    def test_an_unknown_type_is_not_resolved(self) -> None:
        assert get_action_type("launch") is None
        assert get_action_type(None) is None
        assert get_action_type(17) is None


class TestCleaning:
    """Sanitisation, which is where a hostile or hand-edited file lands."""

    def test_a_hotkey_keeps_its_key_and_modifiers(self) -> None:
        cleaned = clean_action({"type": "hotkey", "key": "S", "modifiers": ["ctrl"]})
        assert cleaned == {"type": "hotkey", "key": "s", "modifiers": ["ctrl"]}

    def test_a_hotkey_with_no_action_key_is_dropped(self) -> None:
        """Modifiers alone are not a chord.

        Storing one leaves a key that looks programmed and does nothing
        when tapped, which is indistinguishable from a tap that failed to
        register, so the user taps it again.
        """
        assert clean_action({"type": "hotkey", "modifiers": ["ctrl", "shift"]}) is None
        assert clean_action({"type": "hotkey", "key": "", "modifiers": ["ctrl"]}) is None

    def test_modifiers_are_an_allow_list_in_canonical_order(self) -> None:
        """Unknown names are dropped, not passed through.

        This list is handed to the platform synthesiser, which on Linux
        turns it into argv for xdotool.  The canonical order matters
        separately: two spellings of one chord must not compare or display
        as two different chords.
        """
        cleaned = clean_action(
            {"type": "hotkey", "key": "s", "modifiers": ["shift", "hyper", "ctrl", "ctrl"]}
        )
        assert cleaned is not None
        assert cleaned["modifiers"] == ["ctrl", "shift"]

    def test_modifiers_from_a_non_list_degrade_to_none(self) -> None:
        cleaned = clean_action({"type": "hotkey", "key": "s", "modifiers": "ctrl"})
        assert cleaned == {"type": "hotkey", "key": "s", "modifiers": []}

    def test_a_named_special_key_is_accepted(self) -> None:
        """Ctrl+Enter and Alt+Left have to be expressible."""
        for name in ("return", "tab", "left", "escape", "f13"):
            cleaned = clean_action({"type": "hotkey", "key": name, "modifiers": []})
            assert cleaned is not None, name
            assert cleaned["key"] == name

    def test_a_key_that_is_neither_a_name_nor_one_ascii_char_is_dropped(self) -> None:
        """The near-miss half: a plausible-looking key name we cannot send.

        The synth layers translate printable ASCII and the names above and
        have nothing to say about anything else, so admitting it would
        store a chord that silently does nothing.
        """
        for bad in ("mediaplay", "ctrl+s", "⌘", "\x01", "  ", "ab"):
            assert clean_action({"type": "hotkey", "key": bad, "modifiers": []}) is None, bad

    def test_punctuation_is_a_legitimate_chord_key(self) -> None:
        cleaned = clean_action({"type": "hotkey", "key": ",", "modifiers": ["ctrl"]})
        assert cleaned == {"type": "hotkey", "key": ",", "modifiers": ["ctrl"]}

    def test_a_label_is_collapsed_to_one_line_and_capped(self) -> None:
        cleaned = clean_action({"type": "key", "label": "  Push\r\nto talk forever  "})
        assert cleaned is not None
        assert cleaned["label"] == "Push to talk"[:MAX_LABEL_LEN]
        assert "\n" not in cleaned["label"]
        assert len(cleaned["label"]) <= MAX_LABEL_LEN

    def test_an_empty_label_is_omitted_rather_than_stored_blank(self) -> None:
        assert clean_action({"type": "key", "label": "   "}) == {"type": "key"}

    def test_text_keeps_newlines_and_tabs_but_no_other_controls(self) -> None:
        """Same rule as a locally authored snippet, and for the same reason.

        A multi-line signature is a legitimate payload and the Return
        between its lines is the intent; a bare carriage return or a DEL
        is not, and this string is typed verbatim into whatever app has
        focus.  DEL is the one an ``ord(ch) >= 0x20`` test alone misses.
        """
        cleaned = clean_action({"type": "text", "text": "Best,\nOwen\r\x07\x7f\tx"})
        assert cleaned == {"type": "text", "text": "Best,\nOwen\tx"}

    def test_text_is_capped(self) -> None:
        cleaned = clean_action({"type": "text", "text": "x" * (MAX_TEXT_LEN + 50)})
        assert cleaned is not None
        assert len(cleaned["text"]) == MAX_TEXT_LEN

    def test_empty_text_is_dropped(self) -> None:
        assert clean_action({"type": "text", "text": ""}) is None
        assert clean_action({"type": "text", "text": "\x00\x01"}) is None

    def test_a_non_dict_or_unknown_type_is_dropped(self) -> None:
        assert clean_action(None) is None
        assert clean_action("hotkey") is None
        assert clean_action({"type": "launch", "path": "cmd.exe"}) is None

    def test_a_chord_is_described_in_canonical_order(self) -> None:
        assert describe_action({"type": "hotkey", "key": "s", "modifiers": ["shift", "ctrl"]}) == (
            "Ctrl+Shift+S"
        )
        assert describe_action({"type": "hotkey", "key": "return", "modifiers": []}) == "Return"


class TestExecution:
    """Dispatch, and the one type that deliberately does not handle its tap."""

    def test_a_hotkey_fires_its_chord(self, store: KeyActionStore) -> None:
        store.set("f13", {"type": "hotkey", "key": "s", "modifiers": ["ctrl"]})
        ex = RecordingExecutor()
        assert store.execute("f13", ex) is True
        assert ex.chords == [("s", ["ctrl"])]
        assert ex.texts == []

    def test_a_text_action_types_its_phrase(self, store: KeyActionStore) -> None:
        store.set("f14", {"type": "text", "text": "Best,\nOwen"})
        ex = RecordingExecutor()
        assert store.execute("f14", ex) is True
        assert ex.texts == ["Best,\nOwen"]

    def test_a_relabelled_key_still_sends_itself(self, store: KeyActionStore) -> None:
        """ "Carries an entry" is not "handles its own tap".

        The ``key`` type exists so a key bound *inside another app* can be
        labelled on screen without changing what it sends.  Returning True
        here would swallow the keystroke and the label would silently
        break the binding it was added to document.
        """
        assert store.set("f15", {"type": "key", "label": "Talk"}) is True
        ex = RecordingExecutor()
        assert store.execute("f15", ex) is False
        assert ex.chords == [] and ex.texts == []

    def test_an_unassigned_key_sends_itself(self, store: KeyActionStore) -> None:
        ex = RecordingExecutor()
        assert store.execute("f24", ex) is False


class TestPersistence:
    """The file, and every way it can come back wrong."""

    def test_an_assignment_survives_a_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "key_actions.json"
        first = KeyActionStore(path)
        assert first.set(
            "f13", {"type": "hotkey", "key": "s", "modifiers": ["ctrl"], "label": "Save"}
        )
        second = KeyActionStore(path)
        assert second.get("f13") == {
            "type": "hotkey",
            "key": "s",
            "modifiers": ["ctrl"],
            "label": "Save",
        }
        assert second.label_for("f13") == "Save"

    def test_a_missing_file_leaves_every_key_unassigned(self, tmp_path: Path) -> None:
        store = KeyActionStore(tmp_path / "nope.json")
        store.load()
        assert store.get_all() == {}

    def test_a_corrupt_file_does_not_raise(self, tmp_path: Path) -> None:
        """An unassigned function key still works, so this must never block startup."""
        path = tmp_path / "key_actions.json"
        path.write_text("{not json", encoding="utf-8")
        store = KeyActionStore(path)
        store.load()
        assert store.get_all() == {}

    def test_an_oversized_file_is_ignored(self, tmp_path: Path) -> None:
        path = tmp_path / "key_actions.json"
        path.write_text(json.dumps({"actions": {"f13": {"type": "key"}}}) + " " * (300 * 1024))
        store = KeyActionStore(path)
        store.load()
        assert store.get_all() == {}

    def test_a_bad_entry_is_dropped_without_losing_the_good_ones(self, tmp_path: Path) -> None:
        """Per-entry, not per-file.

        One unusable assignment is not a reason to throw away the eleven
        the user got right, which is the same call ``SnippetStore`` makes
        about a bad colour tag.
        """
        path = tmp_path / "key_actions.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "actions": {
                        "f13": {"type": "hotkey", "key": "s", "modifiers": ["ctrl"]},
                        "f14": {"type": "hotkey", "modifiers": ["ctrl"]},
                        "f15": {"type": "wat"},
                        "nope": {"type": "key", "label": "x"},
                        "f16": {"type": "text", "text": "hi"},
                    },
                }
            ),
            encoding="utf-8",
        )
        store = KeyActionStore(path)
        store.load()
        assert set(store.get_all()) == {"f13", "f16"}

    def test_a_key_outside_the_set_is_refused(self, store: KeyActionStore) -> None:
        assert store.set("f25", {"type": "hotkey", "key": "s"}) is False
        assert store.set("enter", {"type": "hotkey", "key": "s"}) is False
        assert store.get_all() == {}

    def test_an_invalid_payload_reports_failure(self, store: KeyActionStore) -> None:
        """The bool is what stops QML flashing "Saved" over a write that never landed."""
        assert store.set("f13", {"type": "hotkey", "modifiers": ["ctrl"]}) is False
        assert store.get("f13") is None

    def test_setting_the_same_action_twice_reports_no_change(self, store: KeyActionStore) -> None:
        payload = {"type": "hotkey", "key": "s", "modifiers": ["ctrl"]}
        assert store.set("f13", payload) is True
        assert store.set("f13", dict(payload)) is False

    def test_clearing_restores_the_plain_keystroke(self, store: KeyActionStore) -> None:
        store.set("f13", {"type": "text", "text": "hi"})
        assert store.clear("f13") is True
        assert store.get("f13") is None
        assert store.clear("f13") is False

    def test_get_all_hands_out_copies(self, store: KeyActionStore) -> None:
        """QML gets this map; a caller mutating it must not reach the store."""
        store.set("f13", {"type": "text", "text": "hi"})
        snapshot = store.get_all()
        snapshot["f13"]["text"] = "clobbered"
        snapshot["f14"] = {"type": "key"}
        assert store.get("f13") == {"type": "text", "text": "hi"}
        assert store.get("f14") is None

    def test_reload_from_disk_picks_up_an_external_write(self, tmp_path: Path) -> None:
        path = tmp_path / "key_actions.json"
        store = KeyActionStore(path)
        store.load()
        path.write_text(
            json.dumps({"version": 1, "actions": {"f20": {"type": "text", "text": "hi"}}}),
            encoding="utf-8",
        )
        assert store.get("f20") is None
        store.reload_from_disk()
        assert store.get("f20") == {"type": "text", "text": "hi"}
