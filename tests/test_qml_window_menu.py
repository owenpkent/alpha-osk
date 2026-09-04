"""Headless QML tests for the title bar's right-click window menu.

The whole feature is QML -- the right-button MouseArea under the caption
strip, the menu popup, and the click-free move mode -- so the Python suite
cannot reach any of it.  These load the real `qml/Main.qml` under the
`offscreen` platform plugin and drive real `QTest` mouse events at it.

Driving real events rather than the QML API is the point.  The menu has to
open on a right press *without* taking the left press the drag needs, and a
version that got that backwards would pass every test that only called
`showAt()` directly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

# Must be set before QGuiApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# See the note in tests/test_qml_compact_view.py: QtGui dlopens the host's
# libEGL / libGL at module scope, and an ImportError there is a *collection*
# error that aborts the whole run rather than failing this one module.
try:
    from PySide6.QtCore import QCoreApplication, QPoint, QSettings, Qt, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression  # noqa: E402
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)

# The title bar is 48 px tall, and dragArea leaves the right-hand 332 px of it
# to the caption buttons.  That split is exactly what the menu has to straddle.
TITLE_BAR_HEIGHT = 48
CAPTION_BUTTON_RESERVE = 332

# Somewhere with both coordinates comfortably positive.  The saved-geometry
# restore lands the window at a negative x under the offscreen plugin, and a
# negative origin makes that plugin report a window position 4 px adrift of
# where it was actually put -- so a move of 45 px measures as 49.  Nothing to
# do with this feature (y, which stays positive, is exact throughout), but it
# would make every displacement assertion below wrong by a constant.
PARKED_X = 200
PARKED_Y = 100


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)]


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        # A distinct org/app name keeps the QML `Settings` element off the
        # real user's registry section.
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation -- these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def qml_root(qapp):
    """Load Main.qml with a mocked-synth bridge, from clean settings."""
    warnings: list[str] = []
    QSettings(TEST_ORG, TEST_APP).clear()

    # Disarm the startup update check -- otherwise every test that lives
    # three seconds fires a real HTTPS request from a daemon thread that
    # then emits back into a bridge this fixture has already torn down.
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    qapp.processEvents()
    yield root, warnings
    del engine


def _eval(root, expression: str):
    """Evaluate a QML expression in the root object's own scope.

    Everything this feature adds is addressed by `id`, and an id is visible
    only from inside the component that declares it, so there is no
    object-tree walk that reaches `windowMenu` or `windowMoveOverlay`.
    """
    expr = QQmlExpression(QQmlEngine.contextForObject(root), root, expression)
    # PySide hands back (value, wasUndefined).  Only the value is interesting;
    # an undefined read arrives as None and fails the caller's assertion.
    value, _undefined = expr.evaluate()
    assert not expr.hasError(), f"{expression}: {expr.error().toString()}"
    return value


def _open_menu(root) -> None:
    """Open the menu and let it settle.

    The rows have to be read with it open: a closed Popup's contentItem is
    hidden, so every row reports `visible: false` and an assertion about
    which ones are showing passes against anything at all.
    """
    _eval(root, "windowMenu.open()")
    QGuiApplication.processEvents()
    assert _eval(root, "windowMenu.visible")


def _menu_rows(root) -> list[tuple[str, bool]]:
    """(label, visible) for every row the menu built, in order.

    Goes through `Array.prototype`: `children` is a QML list, not a JS array,
    so calling `.filter` on it directly yields nothing rather than raising --
    which reads like an empty menu.
    """
    raw = _eval(
        root,
        "JSON.stringify(Array.prototype.map.call("
        "  Array.prototype.filter.call(windowMenuCol.children,"
        "    function (c) { return c.rowLabel !== undefined }),"
        "  function (c) { return [c.rowLabel, c.visible] }))",
    )
    rows = [(label, visible) for label, visible in json.loads(raw)]
    assert rows, "no menu rows found -- the delegate walk is measuring nothing"
    return rows


def _press(root, x: int, y: int, button) -> None:
    QTest.mousePress(root, button, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    QTest.mouseRelease(root, button, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    QGuiApplication.processEvents()


def _park(root) -> None:
    """Put the window somewhere the offscreen plugin reports honestly."""
    _eval(root, f"root.x = {PARKED_X}; root.y = {PARKED_Y}")
    QGuiApplication.processEvents()


def _pos(root) -> tuple[int, int]:
    return root.property("x"), root.property("y")


def _hover(root, gx: int, gy: int) -> None:
    """Hover-move the pointer to the *desktop* point (gx, gy).

    Global rather than window-local, because in move mode the two are not
    interchangeable: the window slides out from under the pointer by exactly
    the delta, so the same local point maps back to the same global point and
    Qt drops the second event as a duplicate of the first.  Driving the
    pointer where it actually is keeps every event distinct, and makes the
    assertion the honest one anyway -- the window is supposed to follow the
    pointer across the desktop, not across itself.
    """
    QTest.mouseMove(root, QPoint(gx - root.property("x"), gy - root.property("y")))
    QGuiApplication.processEvents()


def _press(root, gx: int, gy: int, button) -> None:
    local = QPoint(gx - root.property("x"), gy - root.property("y"))
    QTest.mousePress(root, button, Qt.KeyboardModifier.NoModifier, local)
    QGuiApplication.processEvents()


def _click(root, gx: int, gy: int, button) -> None:
    local = QPoint(gx - root.property("x"), gy - root.property("y"))
    QTest.mousePress(root, button, Qt.KeyboardModifier.NoModifier, local)
    QTest.mouseRelease(root, button, Qt.KeyboardModifier.NoModifier, local)
    QGuiApplication.processEvents()


def _park(root) -> None:
    """Put the window somewhere the offscreen plugin reports honestly."""
    _eval(root, f"root.x = {PARKED_X}; root.y = {PARKED_Y}")
    QGuiApplication.processEvents()


def _pos(root) -> tuple[int, int]:
    return root.property("x"), root.property("y")


# Where the move-mode tests settle the pointer before measuring anything.
# Comfortably inside a parked 1160x406 window.
ANCHOR_GX, ANCHOR_GY = 600, 350


def _begin_move(root) -> tuple[int, int]:
    """Enter move mode with the anchor settled under the pointer at
    (ANCHOR_GX, ANCHOR_GY), and report where the window ended up.

    Two hovers, and both are needed.  Qt drops a move to the point the
    pointer already occupies, and the pointer is process-global while the
    window is rebuilt for every test, so a lone hover can inherit the
    previous test's final position and never be delivered -- which leaves the
    anchor wherever the platform's own opening hover put it, and the
    measurement that follows wrong by that much.  Two distinct points
    guarantee one lands.  The baseline is read afterwards, so whatever the
    settling itself moved is already accounted for.
    """
    _park(root)
    _eval(root, "root.beginWindowMove()")
    _hover(root, ANCHOR_GX + 60, ANCHOR_GY + 40)
    _hover(root, ANCHOR_GX, ANCHOR_GY)
    assert _eval(root, "windowMoveOverlay.anchored")
    return _pos(root)


class TestRightClickingTheTitleBarOpensTheMenu:
    """The gesture itself, and the left press it must not steal."""

    def test_a_right_press_on_the_drag_strip_opens_it(self, qml_root):
        root, warnings = qml_root
        assert not _eval(root, "windowMenu.visible")

        _click(
            root,
            root.property("x") + 160,
            root.property("y") + TITLE_BAR_HEIGHT // 2,
            Qt.MouseButton.RightButton,
        )

        assert _eval(root, "windowMenu.visible")
        assert _real_warnings(warnings) == []

    def test_a_right_press_over_the_caption_buttons_opens_it_too(self, qml_root):
        """The whole strip is a menu target, the way a real caption bar is.

        dragArea stops 332 px short of the right edge to leave the buttons
        alone, so a menu wired to *it* would be dead over the half of the bar
        the buttons occupy, including the gaps between them.
        """
        root, warnings = qml_root
        width = root.property("width")
        offset = width - 40
        assert offset > width - CAPTION_BUTTON_RESERVE, "the point must be inside the button strip"

        _click(
            root,
            root.property("x") + offset,
            root.property("y") + TITLE_BAR_HEIGHT // 2,
            Qt.MouseButton.RightButton,
        )

        assert _eval(root, "windowMenu.visible")
        assert _real_warnings(warnings) == []

    def test_a_left_drag_on_the_strip_still_moves_the_window(self, qml_root):
        """The inverse half, and it has to be the drag rather than the menu.

        Asserting only that a left press does not open the menu proves
        nothing: dragArea sits on top of the menu's MouseArea and would take
        that press whether or not the one underneath wanted it.  What the
        declaration order buys is that dragging still works, so that is what
        this measures -- press, travel, release, and the window followed.
        """
        root, _ = qml_root
        _park(root)
        before = _pos(root)
        grab = (before[0] + 160, before[1] + TITLE_BAR_HEIGHT // 2)

        QTest.mousePress(
            root,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(160, TITLE_BAR_HEIGHT // 2),
        )
        _hover(root, grab[0] + 60, grab[1] + 35)
        moved = _pos(root)
        QTest.mouseRelease(
            root,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            QPoint(grab[0] + 60 - moved[0], grab[1] + 35 - moved[1]),
        )
        QGuiApplication.processEvents()

        assert moved == (before[0] + 60, before[1] + 35)
        assert not _eval(root, "windowMenu.visible")

    def test_a_right_press_below_the_title_bar_does_not_open_it(self, qml_root):
        """It is the caption strip's menu, not the keyboard's.

        Right-clicking a key is already the shifted-character gesture and
        right-clicking a pill is already the prediction menu.
        """
        root, _ = qml_root

        _click(
            root,
            root.property("x") + 160,
            root.property("y") + TITLE_BAR_HEIGHT + 80,
            Qt.MouseButton.RightButton,
        )

        assert not _eval(root, "windowMenu.visible")


class TestWhatTheMenuOffers:
    def test_it_offers_move_minimize_and_close(self, qml_root):
        root, _ = qml_root
        _open_menu(root)
        assert [label for label, shown in _menu_rows(root) if shown] == [
            "Move",
            "Minimize",
            "Close",
        ]

    def test_the_tuck_row_collapses_where_tucking_is_impossible(self, qml_root):
        """Tuck is X11-only, and an inert row is worse than no row.

        The row is built either way (the model is not platform-conditional),
        so this pins that it takes no height rather than merely that it is
        missing from the labels above.
        """
        root, _ = qml_root
        _open_menu(root)
        assert not _eval(root, "root.tuckSupported"), "offscreen is not X11"

        rows = dict(_menu_rows(root))
        assert rows["Tuck away"] is False
        assert rows["Move"] is True, "the other rows must be showing, or this proves nothing"
        assert _eval(root, "windowMenuCol.children[2].height") == 0

    def test_close_is_set_apart_from_the_rows_above_it(self, qml_root):
        """It is the one row that ends the session, and there is no undo.

        The gap is what stops an imprecise pointer aimed at Minimize landing
        on it, so it is a property of the menu rather than decoration.
        """
        root, _ = qml_root
        _open_menu(root)
        heights = _eval(
            root,
            "JSON.stringify(Array.prototype.map.call("
            "  Array.prototype.filter.call(windowMenuCol.children,"
            "    function (c) { return c.rowLabel !== undefined && c.visible }),"
            "  function (c) { return c.height }))",
        )
        move, minimize, close = json.loads(heights)
        assert move == minimize
        assert close > minimize

    def test_choosing_move_is_what_starts_move_mode(self, qml_root):
        root, _ = qml_root
        _open_menu(root)

        _eval(root, "windowMenu.invoke('move')")

        assert _eval(root, "root.moveMode")
        assert not _eval(root, "windowMenu.visible"), "the menu closes behind the choice"


class TestMoveMode:
    """The click-free drag: pick the window up, move it, put it down."""

    def test_the_overlay_is_inert_until_move_mode_starts(self, qml_root):
        """It covers the entire window, so a live one would eat every key."""
        root, _ = qml_root
        assert not _eval(root, "windowMoveOverlay.visible")
        assert not _eval(root, "windowMoveOverlay.enabled")

    def test_the_window_follows_the_pointer_with_no_button_held(self, qml_root):
        root, _ = qml_root
        start = _begin_move(root)

        _hover(root, ANCHOR_GX + 45, ANCHOR_GY + 30)

        assert _pos(root) == (start[0] + 45, start[1] + 30)

    def test_opening_the_mode_drops_any_earlier_anchor(self, qml_root):
        """Otherwise the second move measures against where the first one
        ended, and the window teleports on the opening event."""
        root, _ = qml_root
        _begin_move(root)
        _eval(root, "root.endWindowMove(false)")

        _eval(root, "root.beginWindowMove()")

        assert not _eval(root, "windowMoveOverlay.anchored")

    def test_the_pointer_leaving_re_anchors_instead_of_resuming(self, qml_root):
        """A fast flick can outrun the window it is dragging.

        Measuring the way back in against the anchor the excursion started
        from would move the window by however far the pointer travelled while
        it was away, which is a teleport rather than a follow.  So the return
        has to cost nothing, exactly as opening the mode does.
        """
        root, _ = qml_root
        base = _begin_move(root)
        _hover(root, ANCHOR_GX + 60, ANCHOR_GY + 40)
        assert _pos(root) == (base[0] + 60, base[1] + 40), "the follow itself must work"

        _hover(root, -400, -400)  # off the overlay entirely
        assert not _eval(root, "windowMoveOverlay.anchored")
        away = _pos(root)

        _hover(root, ANCHOR_GX, ANCHOR_GY)

        assert _pos(root) == away

    def test_a_click_puts_the_keyboard_down_where_it_is(self, qml_root):
        root, _ = qml_root
        _begin_move(root)
        _hover(root, ANCHOR_GX + 40, ANCHOR_GY)
        moved = _pos(root)

        _press(root, ANCHOR_GX + 40, ANCHOR_GY, Qt.MouseButton.LeftButton)

        assert not _eval(root, "root.moveMode")
        assert _pos(root) == moved

    def test_a_right_click_puts_it_back_where_it_started(self, qml_root):
        """The mode is safe to try, which is what makes it worth offering to
        someone who cannot recover a window that lands somewhere unreachable."""
        root, _ = qml_root
        _park(root)
        before = _pos(root)
        _eval(root, "root.beginWindowMove()")
        _hover(root, ANCHOR_GX + 60, ANCHOR_GY + 40)
        _hover(root, ANCHOR_GX, ANCHOR_GY)

        _hover(root, ANCHOR_GX + 120, ANCHOR_GY + 60)
        assert _pos(root) != before

        _press(root, ANCHOR_GX + 120, ANCHOR_GY + 60, Qt.MouseButton.RightButton)

        assert not _eval(root, "root.moveMode")
        assert _pos(root) == before

    def test_the_window_stops_following_once_it_is_down(self, qml_root):
        """The overlay is disabled on the way out, not merely hidden."""
        root, _ = qml_root
        _begin_move(root)
        _eval(root, "root.endWindowMove(false)")
        settled = _pos(root)

        _hover(root, ANCHOR_GX + 200, ANCHOR_GY + 130)

        assert _pos(root) == settled
