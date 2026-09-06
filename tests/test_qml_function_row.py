"""Headless QML tests for the function rows and the key-action editor.

The parts of this feature that can break silently all live in QML: which
keys a row draws, which keycap label it shows, whether a tap sends the key
or opens the editor, and whether the editor can be reached at all by a
pointer that cannot right-click. None of that is reachable from the Python
suite, and a broken binding here is a runtime warning rather than an
import error, so it would ship as a blank keyboard.

Two traps carried over from the other QML modules, both of which have
already produced a test that could not fail:

* ``root.findChildren`` does not reach a Repeater's delegates. They are
  re-parented as *visual* children, so their QObject parent is the
  delegate model. The helper below walks ``childItems()`` instead, and
  every caller asserts the result is non-empty.
* The QML ``Settings`` element resolves to a process-external store, so
  the org/app scope has to be the per-worker one from
  ``tests.qt_settings_scope`` or parallel workers clear each other's.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import (  # noqa: E402
        QCoreApplication,
        QMetaObject,
        QObject,
        QSettings,
        QUrl,
    )
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.key_actions import KeyActionStore  # noqa: E402
from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)]


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation - these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def qml_root(qapp, tmp_path: Path):
    """Load Main.qml with a mocked synth and a temp key-action store."""
    warnings: list[str] = []

    QSettings(TEST_ORG, TEST_APP).clear()
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()
    # The store saves synchronously on every mutation. Swapped for a temp
    # one before the engine can touch it, the same precaution the snippet
    # store gets: otherwise this file rewrites the developer's own key
    # assignments.
    bridge._key_actions = KeyActionStore(tmp_path / "key_actions.json")
    bridge._key_actions.load()

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    try:
        yield root, warnings, bridge, synth
    finally:
        del engine


def _pump(times: int = 6) -> None:
    for _ in range(times):
        QCoreApplication.processEvents()


def _panel(root, name: str):
    panel = root.findChild(QQuickItem, name)
    assert panel is not None, f"no panel named {name!r}"
    return panel


def _keys(panel) -> dict[str, QQuickItem]:
    """Every F-key in *panel*, by key id.

    Walks ``childItems()`` because ``findChildren`` cannot see a Repeater's
    delegates: they are re-parented as visual children, so their QObject
    parent is the delegate model rather than the item tree. Every test
    below asserts the result is non-empty at the call site, which is the
    half that stops this quietly running against zero keys.
    """
    found: dict[str, QQuickItem] = {}

    def walk(item) -> None:
        for child in item.childItems():
            name = child.objectName()
            if name.startswith("fnKey_"):
                found[name[len("fnKey_") :]] = child
            walk(child)

    walk(panel)
    return found


class TestTheExtraRowExists:
    """F13-F24 render, and only when their own toggle is on."""

    def test_it_is_hidden_by_default(self, qml_root) -> None:
        root, warnings, _, _ = qml_root
        assert root.property("showExtraFunctionRow") is False
        assert _panel(root, "extraFunctionRowPanel").isVisible() is False
        assert _real_warnings(warnings) == []

    def test_it_draws_exactly_f13_to_f24(self, qml_root) -> None:
        root, warnings, _, _ = qml_root
        root.setProperty("showExtraFunctionRow", True)
        _pump()
        keys = _keys(_panel(root, "extraFunctionRowPanel"))
        assert keys, "no keys rendered in the extra function row"
        assert sorted(keys, key=lambda k: int(k[1:])) == [f"f{n}" for n in range(13, 25)]
        assert _real_warnings(warnings) == []

    def test_the_two_rows_are_independent(self, qml_root) -> None:
        """The whole reason it is its own toggle.

        Someone who wants only the twelve macro keys must not have to
        spend the height of the standard row to get them.
        """
        root, warnings, _, _ = qml_root
        root.setProperty("showExtraFunctionRow", True)
        root.setProperty("showFunctionRow", False)
        _pump()
        assert _panel(root, "extraFunctionRowPanel").isVisible() is True
        assert _panel(root, "functionRowPanel").isVisible() is False
        assert _real_warnings(warnings) == []

    def test_the_standard_row_still_draws_f1_to_f12(self, qml_root) -> None:
        """The inverse half: generalising the component must not have
        changed the row that already existed."""
        root, warnings, _, _ = qml_root
        root.setProperty("showFunctionRow", True)
        _pump()
        keys = _keys(_panel(root, "functionRowPanel"))
        assert keys
        assert sorted(keys, key=lambda k: int(k[1:])) == [f"f{n}" for n in range(1, 13)]
        assert _real_warnings(warnings) == []


class TestKeycapLabels:
    """A programmed key has to be findable, which means it must not read F17."""

    def test_a_custom_label_replaces_the_key_name(self, qml_root) -> None:
        root, warnings, bridge, _ = qml_root
        root.setProperty("showExtraFunctionRow", True)
        _pump()
        assert bridge.setKeyAction(
            "f17", {"type": "hotkey", "key": "s", "modifiers": ["ctrl"], "label": "Save"}
        )
        _pump()
        keys = _keys(_panel(root, "extraFunctionRowPanel"))
        assert keys
        assert keys["f17"].property("displayText") == "Save"
        assert keys["f18"].property("displayText") == "F18"
        assert _real_warnings(warnings) == []

    def test_the_cap_reverts_when_the_action_is_cleared(self, qml_root) -> None:
        """The signal has to reach the row, not just the store.

        The map is passed as data precisely so this re-evaluates; a lookup
        function would have needed a revision counter threaded through
        every binding to do the same, which is state that can go stale.
        """
        root, warnings, bridge, _ = qml_root
        root.setProperty("showExtraFunctionRow", True)
        _pump()
        bridge.setKeyAction("f19", {"type": "text", "text": "hi", "label": "Hi"})
        _pump()
        assert _keys(_panel(root, "extraFunctionRowPanel"))["f19"].property("displayText") == "Hi"
        bridge.clearKeyAction("f19")
        _pump()
        assert _keys(_panel(root, "extraFunctionRowPanel"))["f19"].property("displayText") == "F19"
        assert _real_warnings(warnings) == []

    def test_a_reassigned_key_is_marked_and_a_relabelled_one_is_not(self, qml_root) -> None:
        """ "Carries an entry" is not "no longer sends what its cap says".

        The `key` type keeps the original keystroke, so marking it as
        reassigned would be a lie about what tapping it does.
        """
        root, warnings, bridge, _ = qml_root
        root.setProperty("showExtraFunctionRow", True)
        _pump()
        bridge.setKeyAction("f20", {"type": "hotkey", "key": "s", "modifiers": ["ctrl"]})
        bridge.setKeyAction("f21", {"type": "key", "label": "Talk"})
        _pump()
        keys = _keys(_panel(root, "extraFunctionRowPanel"))
        assert keys
        assert keys["f20"].property("isActive") is True
        assert keys["f21"].property("isActive") is False
        assert keys["f22"].property("isActive") is False
        assert _real_warnings(warnings) == []


class TestTheSettingsListIsTheLeftClickRoute:
    """Right-click alone would be a reachability regression.

    A dwell-click, switch-access, head- or eye-tracker pointer, and a
    single-button adaptive mouse all have no right button, so without a
    second route such a user could press an F-key and never program one.

    That route used to be an Edit toggle on the row itself, which put
    every key into "tap to program". It is now *Settings -> Function
    Keys*, which answers the same requirement better: the rows are far
    bigger targets than a keycap, there is no mode to get into or out of,
    and it is the only surface that shows an assignment the user has
    forgotten making. Removing the toggle also gave the row back the
    thirteenth key's width.
    """

    @staticmethod
    def _rows(root):
        """`fkeyRows` is a `property var` holding a JS array, so it comes
        back as a QJSValue rather than a list; reading it without the
        conversion raises rather than quietly returning nothing."""
        return root.property("fkeyRows").toVariant()

    @staticmethod
    def _settings(root):
        panel = root.findChild(QQuickItem, "settingsPanel")
        assert panel is not None, "no settings panel"
        return panel

    @staticmethod
    def _editor(root):
        # QObject, not QQuickItem: a QML `Popup` is a QQuickPopup, which is
        # not an Item, so an Item-typed findChild silently returns None and
        # every assertion after it never runs.
        editor = root.findChild(QObject, "keyActionEditor")
        assert editor is not None, "no key action editor"
        return editor

    def test_the_list_carries_every_programmable_key(self, qml_root) -> None:
        root, warnings, _, _ = qml_root
        _pump()
        rows = self._rows(root)
        assert [r["name"] for r in rows] == [f"F{n}" for n in range(1, 25)]
        # The split the two sections render from. F13-F24 are the keys
        # nothing binds, which is the whole reason they are listed first.
        unbound = [r["name"] for r in rows if r["unbound"]]
        assert unbound == [f"F{n}" for n in range(13, 25)]
        assert _real_warnings(warnings) == []

    def test_a_row_says_what_the_key_does(self, qml_root) -> None:
        """The label and the description both, or the list is 24 identical
        rows and no better than the twelve identical keycaps it exists to
        tell apart."""
        root, warnings, bridge, _ = qml_root
        assert bridge.setKeyAction("f17", {"type": "text", "label": "Email", "text": "hi"})
        _pump()
        rows = {r["name"]: r for r in self._rows(root)}
        assert rows["F17"]["label"] == "Email"
        assert rows["F17"]["detail"] != ""
        assert rows["F17"]["programmed"] is True
        # An untouched key carries neither, so the row falls back to
        # saying it sends itself.
        assert rows["F18"]["label"] == ""
        assert rows["F18"]["programmed"] is False
        assert _real_warnings(warnings) == []

    def test_tapping_a_row_opens_the_editor_and_gets_settings_out_of_the_way(
        self, qml_root
    ) -> None:
        """The hand-off is the half that can silently half-work.

        The editor is typed into with the OSK's own keys, and the settings
        window is parked in the middle of the screen at 360x540, so an
        editor opened behind it is an editor that cannot be used.
        """
        root, warnings, _, _ = qml_root
        root.setProperty("showSettings", True)
        _pump()
        panel = self._settings(root)
        panel.setProperty("currentView", "fkeys")
        _pump()

        panel.editKeyRequested.emit("f17")
        _pump()

        editor = self._editor(root)
        assert editor.property("keyId") == "f17"
        assert editor.property("opened") is True
        assert root.property("showSettings") is False, (
            "the settings window is still up; it can cover both the editor "
            "and the letter grid the editor is typed with"
        )
        assert _real_warnings(warnings) == []

    def test_closing_the_editor_returns_to_the_same_page(self, qml_root) -> None:
        """A drill-down, so it comes back where it left.

        Settings normally resets to its home grid on open, because landing
        on a deep page reads as "the menu changed". Returning from the
        editor is the exception: dumping the user at the home grid loses
        their place in a list of twenty-four.
        """
        root, warnings, _, _ = qml_root
        root.setProperty("showSettings", True)
        _pump()
        panel = self._settings(root)
        panel.setProperty("currentView", "fkeys")
        _pump()
        panel.editKeyRequested.emit("f17")
        _pump()

        QMetaObject.invokeMethod(self._editor(root), "close")
        _pump()

        assert root.property("showSettings") is True
        assert panel.property("currentView") == "fkeys"
        assert _real_warnings(warnings) == []

    def test_an_editor_opened_from_a_key_does_not_open_settings(self, qml_root) -> None:
        """The inverse half. Without it, "always re-show settings" passes,
        and right-clicking a key would pop a settings window over the
        editor it just opened."""
        root, warnings, _, _ = qml_root
        root.setProperty("showExtraFunctionRow", True)
        _pump()
        assert root.property("showSettings") is False
        root.openKeyActionEditor("f13")
        _pump()
        QMetaObject.invokeMethod(self._editor(root), "close")
        _pump()
        assert root.property("showSettings") is False
        assert _real_warnings(warnings) == []

    def test_a_left_tap_on_a_key_types_it(self, qml_root) -> None:
        """No mode can intercept this any more.

        This used to be the inverse of assign mode; with the mode gone it
        is unconditional, which is what fails if a "tap to program" toggle
        is ever put back on the row without reading why it left.
        """
        root, warnings, _, synth = qml_root
        root.setProperty("showExtraFunctionRow", True)
        _pump()
        synth.reset_mock()
        keys = _keys(_panel(root, "extraFunctionRowPanel"))
        assert keys
        # The key's own keyPressed signal, which is what its MouseArea
        # emits, so this drives the real branch rather than a helper.
        keys["f13"].keyPressed.emit()
        _pump()
        assert synth.send_key.called
        assert synth.send_key.call_args[0][0] == "F13"
        editor = root.findChild(QObject, "keyActionEditor")
        assert editor is None or editor.property("opened") is not True
        assert _real_warnings(warnings) == []


class TestTheEditor:
    """Enough of the editor to catch a broken binding or a lost save."""

    def _editor(self, root):
        editor = root.findChild(QObject, "keyActionEditor")
        assert editor is not None, "no key action editor"
        return editor

    def test_it_loads_the_stored_action(self, qml_root) -> None:
        root, warnings, bridge, _ = qml_root
        bridge.setKeyAction(
            "f13", {"type": "hotkey", "key": "s", "modifiers": ["ctrl"], "label": "Save"}
        )
        _pump()
        root.openKeyActionEditor("f13")
        _pump()
        editor = self._editor(root)
        assert editor.property("keyId") == "f13"
        assert editor.property("typeId") == "hotkey"
        assert editor.property("labelText") == "Save"
        assert editor.property("chordKey") == "s"
        # .toVariant(): a QML `var` holding a JS array arrives as a
        # QJSValue, which is not iterable from Python.
        assert editor.property("chordMods").toVariant() == ["ctrl"]
        assert _real_warnings(warnings) == []

    def test_an_unassigned_key_opens_on_the_default(self, qml_root) -> None:
        root, warnings, _, _ = qml_root
        root.openKeyActionEditor("f14")
        _pump()
        editor = self._editor(root)
        assert editor.property("typeId") == "key"
        assert editor.property("labelText") == ""
        assert _real_warnings(warnings) == []

    def test_it_never_installs_a_blocking_overlay(self, qml_root) -> None:
        """A modal popup swallows the MouseArea clicks on the keys below,
        so no OSK key would fire and the label could never be typed. Every
        OSK key click is also a press-outside, so the close policy must not
        include CloseOnPressOutside."""
        root, warnings, _, _ = qml_root
        root.openKeyActionEditor("f15")
        _pump()
        editor = self._editor(root)
        assert editor.property("modal") is False
        assert editor.property("dim") is False
        # Asserted as bits rather than as one magic number: what matters
        # is that Escape closes it and that *no* press- or release-outside
        # bit is set, since every OSK key click is an outside press.
        policy = int(editor.property("closePolicyBits"))
        assert policy & 0x10, "Escape no longer closes the editor"
        assert policy & 0x0F == 0, "a press/release-outside bit is set"
        assert _real_warnings(warnings) == []

    def _text_field(self, root):
        """The editor's phrase field, found by objectName.

        `findChild` reaches it because the field is a plain child of the
        editor, not a Repeater delegate -- the trap this module's
        docstring records applies to the key rows, not here.
        """
        field = root.findChild(QObject, "keyActionTextField")
        assert field is not None, "no phrase field in the editor"
        return field

    def test_shift_and_an_arrow_select_rather_than_moving_the_caret(self, qml_root) -> None:
        """This window never holds OS focus, so Qt's own key handling never
        sees the modifier and the editor has to apply it itself. Without
        this there is no way at all to select a range in a 500-character
        phrase with an imprecise pointer."""
        root, warnings, bridge, _ = qml_root
        bridge.setKeyAction("f13", {"type": "text", "text": "hello"})
        _pump()
        root.openKeyActionEditor("f13")
        _pump()
        editor = self._editor(root)
        editor.setProperty("editTarget", "text")
        field = self._text_field(root)
        field.setProperty("cursorPosition", 5)
        editor.setProperty("shiftOn", True)
        _pump()

        bridge.editSpecialPressed.emit("left")
        bridge.editSpecialPressed.emit("left")
        _pump()

        assert field.property("selectedText") == "lo", (
            "Shift+Left moved the caret instead of extending the selection"
        )
        assert _real_warnings(warnings) == []

    def test_an_arrow_on_its_own_still_just_moves_the_caret(self, qml_root) -> None:
        """The inverse half: an unconditional moveCursorSelection would pass
        the test above and leave the caret impossible to move alone."""
        root, warnings, bridge, _ = qml_root
        bridge.setKeyAction("f13", {"type": "text", "text": "hello"})
        _pump()
        root.openKeyActionEditor("f13")
        _pump()
        editor = self._editor(root)
        editor.setProperty("editTarget", "text")
        field = self._text_field(root)
        field.setProperty("cursorPosition", 5)
        editor.setProperty("shiftOn", False)
        _pump()

        bridge.editSpecialPressed.emit("left")
        _pump()

        assert field.property("selectedText") == ""
        assert field.property("cursorPosition") == 4
        assert _real_warnings(warnings) == []

    def test_the_action_type_picker_comes_from_the_bridge(self, qml_root) -> None:
        """A new type in key_actions.ACTION_TYPES must reach the UI with no
        QML edit, which is only true while this list is not hardcoded."""
        root, warnings, bridge, _ = qml_root
        _pump()
        ids = [entry["id"] for entry in root.property("keyActionTypes")]
        assert ids == [entry["id"] for entry in bridge.getKeyActionTypes()]
        assert "hotkey" in ids
        assert _real_warnings(warnings) == []
