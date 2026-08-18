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
    from PySide6.QtCore import QCoreApplication, QObject, QSettings, QUrl  # noqa: E402
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
    engine.rootContext().setContextProperty("keyboard", bridge)
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


def _toggle(panel):
    def walk(item):
        for child in item.childItems():
            if child.objectName() == "fnAssignToggle":
                return child
            hit = walk(child)
            if hit is not None:
                return hit
        return None

    found = walk(panel)
    assert found is not None, "no assign toggle in this row"
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


class TestAssignModeIsTheLeftClickRoute:
    """Right-click alone would be a reachability regression.

    A dwell-click, switch-access, head- or eye-tracker pointer, and a
    single-button adaptive mouse all have no right button, so without this
    mode such a user could press an F-key and never program one. That is
    the same hole the snippets grid documents having closed with its
    Manage toggle.
    """

    def test_every_visible_row_carries_a_toggle(self, qml_root) -> None:
        root, warnings, _, _ = qml_root
        root.setProperty("showFunctionRow", True)
        root.setProperty("showExtraFunctionRow", True)
        _pump()
        assert _toggle(_panel(root, "functionRowPanel")) is not None
        assert _toggle(_panel(root, "extraFunctionRowPanel")) is not None
        assert _real_warnings(warnings) == []

    def test_the_toggle_is_never_absent_when_only_one_row_shows(self, qml_root) -> None:
        """The toggle must not live on one row and vanish with it."""
        root, warnings, _, _ = qml_root
        root.setProperty("showFunctionRow", True)
        root.setProperty("showExtraFunctionRow", False)
        _pump()
        assert _toggle(_panel(root, "functionRowPanel")) is not None
        assert _real_warnings(warnings) == []

    def test_toggling_one_row_puts_both_in_assign_mode(self, qml_root) -> None:
        root, warnings, _, _ = qml_root
        root.setProperty("showFunctionRow", True)
        root.setProperty("showExtraFunctionRow", True)
        _pump()
        _panel(root, "functionRowPanel").assignToggled.emit()
        _pump()
        assert root.property("fkeyAssignMode") is True
        assert _panel(root, "extraFunctionRowPanel").property("assignMode") is True
        _panel(root, "extraFunctionRowPanel").assignToggled.emit()
        _pump()
        assert root.property("fkeyAssignMode") is False
        assert _real_warnings(warnings) == []

    def test_a_tap_in_assign_mode_opens_the_editor_instead_of_typing(self, qml_root) -> None:
        """The dispatch is a named function so it can be driven directly.

        A synthetic click cannot be delivered to a Repeater delegate
        reliably here (the offscreen window's layout has not settled, so
        every key maps to the same scene point), which is how the snippets
        suite ended up with a dispatch that had no coverage at all.
        """
        root, warnings, _, synth = qml_root
        root.setProperty("showExtraFunctionRow", True)
        root.setProperty("fkeyAssignMode", True)
        _pump()
        synth.reset_mock()
        keys = _keys(_panel(root, "extraFunctionRowPanel"))
        assert keys
        # The key's own keyPressed signal, which is what its MouseArea
        # emits, so this drives the real branch rather than a helper.
        keys["f13"].keyPressed.emit()
        _pump()
        # QObject, not QQuickItem: a QML `Popup` is a QQuickPopup, which
        # is not an Item, so an Item-typed findChild silently returns
        # None and every assertion after it never runs.
        editor = root.findChild(QObject, "keyActionEditor")
        assert editor is not None
        assert editor.property("keyId") == "f13"
        assert editor.property("opened") is True
        assert not synth.send_key.called
        assert _real_warnings(warnings) == []

    def test_a_tap_outside_assign_mode_types_the_key(self, qml_root) -> None:
        """The inverse half, without which "always open the editor" passes."""
        root, warnings, _, synth = qml_root
        root.setProperty("showExtraFunctionRow", True)
        root.setProperty("fkeyAssignMode", False)
        _pump()
        synth.reset_mock()
        keys = _keys(_panel(root, "extraFunctionRowPanel"))
        assert keys
        keys["f13"].keyPressed.emit()
        _pump()
        assert synth.send_key.called
        assert synth.send_key.call_args[0][0] == "F13"
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

    def test_the_action_type_picker_comes_from_the_bridge(self, qml_root) -> None:
        """A new type in key_actions.ACTION_TYPES must reach the UI with no
        QML edit, which is only true while this list is not hardcoded."""
        root, warnings, bridge, _ = qml_root
        _pump()
        ids = [entry["id"] for entry in root.property("keyActionTypes")]
        assert ids == [entry["id"] for entry in bridge.getKeyActionTypes()]
        assert "hotkey" in ids
        assert _real_warnings(warnings) == []


class TestEveryKeyIsHitTestable:
    """The swipe overlay takes every press inside its bounds and resolves it
    against ``tappableKeyRegistry``. Anything not in that registry is a dead
    tap whenever Swipe Typing is on, which is issue #15: it was fixed for the
    main grid, the Number Row and the F-keys, and the one new key added to
    this row (the assign toggle) is exactly the shape that reintroduces it.
    """

    @staticmethod
    def _actions(root, registry: str) -> set[str]:
        """The `kd.action` of every entry in *registry*.

        `.toVariant()` because the registries are QML `var` arrays, which
        arrive as a QJSValue that Python cannot iterate.
        """
        return {
            str((entry.get("kd") or {}).get("action", ""))
            for entry in root.property(registry).toVariant()
        }

    def test_every_f_key_and_the_toggle_are_registered(self, qml_root) -> None:
        root, warnings, _, _ = qml_root
        root.setProperty("showFunctionRow", True)
        root.setProperty("showExtraFunctionRow", True)
        _pump()

        registered = self._actions(root, "tappableKeyRegistry")
        assert registered, "nothing registered as tappable"
        for n in range(1, 25):
            assert f"f{n}" in registered, f"F{n} is not hit-testable"
        assert "assign" in registered, (
            "the assign toggle is not in the tappable registry, so it is a "
            "dead tap with Swipe Typing on"
        )
        assert _real_warnings(warnings) == []

    def test_no_f_key_reaches_the_swipe_shape_matcher(self, qml_root) -> None:
        """The inverse half, and the reason the two registries are separate.

        An "F7" or an "Edit" centre in the recogniser's key-centre map is a
        phantom letter in every shape match, so these must register as
        specials and stay out of ``charKeyRegistry``.
        """
        root, warnings, _, _ = qml_root
        root.setProperty("showFunctionRow", True)
        root.setProperty("showExtraFunctionRow", True)
        _pump()

        chars = self._actions(root, "charKeyRegistry")
        for n in range(1, 25):
            assert f"f{n}" not in chars, f"F{n} leaked into the swipe key-centre map"
        assert "assign" not in chars
        assert _real_warnings(warnings) == []
