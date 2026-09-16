"""Headless QML tests for edit-mode ownership.

The defect (Sept 2026 security audit, item 8): edit mode used to be one
shared bool, ``KeyboardBridge._edit_mode_active``, that any of three
surfaces -- the prediction-edit popup, the snippets editor, the
key-action editor -- could flip with no notion of who had turned it on.
Opening the Snippets window while the prediction popup was still open
called ``openList()``'s unconditional ``setEditMode(False)``, cutting the
popup's routing out from under it: the next OSK keystroke went to the app
behind the keyboard rather than the still-open popup. Opening two editors
the other way round handed ``editKeyTyped`` to both ``Connections``
blocks at once, so one tap inserted into two fields.

``beginEditSession(owner)`` / ``endEditSession(owner)`` fix this on the
Python side (see ``tests/test_keyboard_bridge.py::TestEditSessionsHaveAnOwner``
for the bridge-level coverage); this module is the QML half, which is
where the actual routing bug lived -- the bridge's flag was always a
single bool, the missing piece was each surface knowing whether *it* was
the one holding it. Loads the real `qml/Main.qml` against a real
`KeyboardBridge` under the `offscreen` platform plugin, the same approach
as `test_qml_snippets.py` and `test_qml_function_row.py`.

The failure mode this guards is a QML binding error (a runtime warning
rather than an import failure) and, more specifically, a keystroke
silently reaching the wrong destination -- neither of which the Python
suite alone can see, since the routing decision is split between the
bridge's flag and each surface's own `Connections` gate.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

# Must be set before QGuiApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# See the note in test_qml_compact_view.py: QtGui dlopens the host's
# libEGL / libGL at module scope, and an ImportError there aborts the whole
# run as a collection error rather than failing this module.
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

    # Imported for the side effect: without QQuickItem somewhere in the
    # module, reading an item's `contentItem` raises "Can't find converter
    # for 'QQuickItem*'". Walking the visual tree is the only way to reach a
    # Window's own children (see `_find_named` below).
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from src.snippets import SnippetStore  # noqa: E402
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
        "organisation; these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def qml_root(qapp, tmp_path: Path):
    """Load Main.qml with a mocked synth and a temp snippets store."""
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
    # Swapped before anything can write to it -- SnippetStore saves
    # synchronously on every mutation, and beginEdit() below is exercised
    # against a real store, not a mock.
    bridge._snippets = SnippetStore(tmp_path / "snippets.json")
    bridge._snippets.load()

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


def _find_named(window, name: str):
    """First item with *name* in *window*'s visual tree, or None.

    For a top-level `Window` (the snippets window here), `findChild` does
    not reach its own children: items declared in a Window's body are
    reparented under its contentItem, so the QObject parent chain the
    QObject-level search walks is not the tree they live in. Copied from
    `test_qml_snippets.py`, which documents the same trap.
    """
    found = []

    def walk(item):
        if item is None or found:
            return
        for child in item.childItems():
            if child.objectName() == name:
                found.append(child)
                return
            walk(child)

    walk(window.property("contentItem"))
    return found[0] if found else None


def _open_prediction_popup(root, word: str = "hello"):
    """Open the prediction-edit popup the way `editMa`'s onClicked does,
    without needing a live prediction pill and a positioned context menu:
    set the field's text directly and open() the popup, exactly what that
    handler does (see `qml/Main.qml` around `predEditField.originalWord =
    ...`).

    Returns (popup, field). `predEditPopup` is a `Popup` (QQuickPopup),
    not a `Window`, so unlike the snippets window its children -- and the
    popup itself -- are ordinary `QObject` children reachable straight
    off `root`, the same as `keyActionEditor` / `keyActionTextField` in
    `test_qml_function_row.py`.
    """
    popup = root.findChild(QObject, "predEditPopup")
    assert popup is not None, "predEditPopup not found in the loaded QML"
    field = root.findChild(QObject, "predEditField")
    assert field is not None, "predEditField not found in the loaded QML"
    field.setProperty("originalWord", word)
    field.setProperty("text", word)
    # Deterministic regardless of where Qt happens to leave the caret
    # after a programmatic text assignment.
    field.setProperty("cursorPosition", len(word))
    QMetaObject.invokeMethod(popup, "open")
    _pump()
    return popup, field


def _snippets_window(root):
    window = root.findChild(QObject, "snippetsWindow")
    assert window is not None, "snippetsWindow not found in the loaded QML"
    return window


class TestPredictionPopupThenSnippets:
    """Open the prediction popup, then the Snippets window: the exact
    sequence from the bug report.

    Before the fix, `openList()`'s unconditional `setEditMode(False)` cut
    the still-open popup's routing out from under it with no warning --
    the popup stayed on screen but the next OSK keystroke went straight
    to the app behind the keyboard instead of the field the user was
    looking at.
    """

    def test_opening_the_popup_claims_ownership(self, qml_root) -> None:
        root, warnings, bridge, synth = qml_root
        popup, _field = _open_prediction_popup(root, "hello")

        assert bridge.editOwner == "prediction"
        assert popup.property("opened") is True
        assert _real_warnings(warnings) == []

    def test_a_keystroke_reaches_the_popups_field(self, qml_root) -> None:
        root, warnings, bridge, synth = qml_root
        popup, field = _open_prediction_popup(root, "hello")
        synth.reset_mock()

        bridge.pressKey("a")
        _pump()

        assert field.property("text") == "helloa"
        assert synth.send_text.called is False
        assert synth.send_key.called is False
        assert _real_warnings(warnings) == []

    def test_opening_the_snippets_list_does_not_steal_the_popups_mode(self, qml_root) -> None:
        """`openList()` alone (browsing the tile grid, not editing a
        slot) must not touch a session it does not own -- this is the
        literal repro from the bug report."""
        root, warnings, bridge, synth = qml_root
        popup, field = _open_prediction_popup(root, "hello")

        window = _snippets_window(root)
        window.openList()
        _pump()

        assert bridge.editOwner == "prediction"
        assert popup.property("opened") is True

        synth.reset_mock()
        bridge.pressKey("a")
        _pump()

        assert field.property("text") == "helloa"
        assert synth.send_text.called is False
        assert synth.send_key.called is False
        assert _real_warnings(warnings) == []

    def test_opening_the_snippets_editor_takes_over_and_closes_the_popup(self, qml_root) -> None:
        root, warnings, bridge, synth = qml_root
        popup, popup_field = _open_prediction_popup(root, "hello")
        window = _snippets_window(root)
        window.openList()
        _pump()

        window.beginEdit(0)
        _pump()

        assert bridge.editOwner == "snippets"
        assert popup.property("opened") is False, (
            "the prediction popup is still open after the snippets editor "
            "took over edit mode -- it should have closed itself on the "
            "takeover (see Main.qml's onEditOwnerChanged)"
        )
        assert window.property("editingIndex") == 0

        value_field = _find_named(window, "snipValueField")
        assert value_field is not None, "snipValueField not found in the snippets editor"
        before = value_field.property("text")
        synth.reset_mock()

        bridge.pressKey("b")
        _pump()

        assert value_field.property("text") == before + "b"
        # The popup's own field is untouched: it is closed and no longer
        # the owner, so its Connections block must not have fired too --
        # the whole point of the ownership gate over the old shared bool.
        assert popup_field.property("text") == "hello", (
            "the closed prediction popup's field changed after it lost ownership"
        )
        assert synth.send_text.called is False
        assert synth.send_key.called is False
        assert _real_warnings(warnings) == []

    def test_leaving_the_snippets_editor_clears_the_mode_entirely(self, qml_root) -> None:
        root, warnings, bridge, synth = qml_root
        _popup, _field = _open_prediction_popup(root, "hello")
        window = _snippets_window(root)
        window.openList()
        _pump()
        window.beginEdit(0)
        _pump()
        assert bridge.editOwner == "snippets"

        window.endEdit()
        _pump()

        assert bridge.editOwner == ""
        assert bridge._edit_mode_active is False

        synth.reset_mock()
        bridge.pressKey("c")
        _pump()
        assert synth.send_text.called is True, (
            "with no session open, a keystroke should fall through to the "
            "synthesizer like ordinary typing"
        )
        assert _real_warnings(warnings) == []


class TestSnippetsThenPredictionPopup:
    """The reverse order: open the snippets editor first, then the
    prediction popup. Both surfaces raced to hold the mode before this
    fix; the assertion that matters is that exactly one owner is left
    standing, whichever order they opened in.
    """

    def test_the_popup_takes_over_and_the_editor_returns_to_the_list(self, qml_root) -> None:
        root, warnings, bridge, synth = qml_root
        window = _snippets_window(root)
        window.openList()
        _pump()
        window.beginEdit(0)
        _pump()
        assert bridge.editOwner == "snippets"
        assert window.property("editingIndex") == 0

        popup, field = _open_prediction_popup(root, "world")
        _pump()

        assert bridge.editOwner == "prediction"
        assert popup.property("opened") is True
        assert window.property("editingIndex") == -1, (
            "the snippets editor should have returned to the list on the "
            "takeover instead of sitting open with a stale session"
        )

        synth.reset_mock()
        bridge.pressKey("!")
        _pump()

        assert field.property("text") == "world!"
        assert synth.send_text.called is False
        assert synth.send_key.called is False
        assert _real_warnings(warnings) == []
