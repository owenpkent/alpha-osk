"""Where Settings, Help and the Dashboard open.

All three used to open at ``Screen.width / 2 - width / 2``, which centres
them on the *primary* screen whatever screen the keyboard is on, and can
drop them on top of the keyboard the user types into them with. None of the
three has an OS title bar to drag it back by, and Settings cannot take focus
either, so a window that opens somewhere unreachable stays unreachable.

The multi-monitor half of that cannot be exercised here: the offscreen
platform gives exactly one screen, so what these tests pin is the half a
single screen can prove, namely that the position is derived from the
KEYBOARD's own geometry rather than from the screen's centre. That is the
property the old code lacked: its result was invariant to where the keyboard
was. The screen-selection half is asserted by construction, in
``screenBoundsAt``, which reads ``Qt.application.screens`` rather than
``Screen.width`` for the reason the snippets restore documents one window
over.

Every case is paired with the inverse it has to keep rejecting: "the panel
is on screen" is satisfied perfectly by a rule that pins every panel to the
top-left corner, and "the panel clears the keyboard" by one that always
parks it at the top of the display.
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
    from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression  # noqa: E402
    from PySide6.QtQuick import QQuickWindow  # noqa: E402,F401
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

GAP = 8  # root.safePanelPos's own clearance, in logical pixels.


def _settle(passes: int = 8) -> None:
    for _ in range(passes):
        QCoreApplication.processEvents()


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
def qml(qapp):
    warnings: list[str] = []
    QSettings(TEST_ORG, TEST_APP).clear()
    settings = QSettings(TEST_ORG, TEST_APP)
    # The startup update check fires a real HTTPS request from a daemon
    # thread that outlives the fixture.
    settings.setValue("ui/savedAutoCheckUpdates", False)
    # The offscreen platform gives one 800x800 screen, and the default
    # full-size board is wider than that, which would pin every position
    # against the clamp and make each assertion below true by accident.
    # Compact with both side panels off is the smallest real keyboard.
    settings.setValue("ui/savedCompactView", True)
    settings.setValue("ui/savedShowNavigation", False)
    settings.setValue("ui/savedShowNumpad", False)
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
    root.setProperty("width", root.property("minimumWidth"))
    _settle()
    left, top, right, bottom = _screen()
    assert (right - left) - root.property("width") >= 150, (
        "the keyboard fills the test screen; every placement would clamp"
    )
    # Room for a 100px panel plus its gap both above and below the board,
    # or the two "clears the keyboard" cases prove nothing.
    assert (bottom - top) - root.property("height") >= 2 * (100 + GAP)
    try:
        yield engine, root
    finally:
        del engine


def _call(engine, root, js: str):
    """Evaluate one JS expression in the root window's own scope.

    A QML `var` holding a JS object arrives as a QJSValue, which Python
    cannot index; `.toVariant()` is what turns it into a dict.
    """
    expr = QQmlExpression(engine.rootContext(), root, js)
    value, _undefined = expr.evaluate()
    assert not expr.hasError(), expr.error().toString()
    return value.toVariant() if hasattr(value, "toVariant") else value


def _screen():
    geo = QGuiApplication.primaryScreen().geometry()
    return geo.left(), geo.top(), geo.right() + 1, geo.bottom() + 1


def _park(root, x: int, y: int) -> tuple[int, int, int, int]:
    """Put the keyboard somewhere and report where it actually landed.

    The offscreen plugin adjusts a window's position by a few pixels, so
    every assertion below measures the keyboard rather than assuming it.
    """
    root.setProperty("x", x)
    root.setProperty("y", y)
    _settle()
    return (
        root.property("x"),
        root.property("y"),
        root.property("width"),
        root.property("height"),
    )


class TestThePanelFollowsTheKeyboard:
    """The position has to be derived from the keyboard, not the screen."""

    def test_it_is_centred_on_the_keyboard(self, qml) -> None:
        engine, root = qml
        left, _top, right, _bottom = _screen()
        kx, _ky, kw, _kh = _park(root, left + 40, 0)
        pos = _call(engine, root, "safePanelPos(200, 100)")
        expected = kx + (kw - 200) / 2
        assert abs(pos["x"] - max(left, min(expected, right - 200))) <= 1

    def test_moving_the_keyboard_moves_the_panel(self, qml) -> None:
        # The half the old code failed: `Screen.width / 2 - width / 2` is
        # the same number wherever the keyboard is, so a panel opened next
        # to a keyboard on another display never moved with it.
        engine, root = qml
        _park(root, 0, 0)
        near_left = _call(engine, root, "safePanelPos(200, 100)")["x"]
        left, _t, right, _b = _screen()
        _park(root, right - root.property("width") - 1, 0)
        near_right = _call(engine, root, "safePanelPos(200, 100)")["x"]
        assert near_right > near_left, "the panel did not follow the keyboard across the screen"


class TestThePanelClearsTheKeyboard:
    """These windows are typed into with the keyboard they must not cover."""

    def test_a_keyboard_at_the_bottom_puts_the_panel_above_it(self, qml) -> None:
        engine, root = qml
        _left, _top, _right, bottom = _screen()
        _kx, ky, _kw, kh = _park(root, 0, bottom - 260)
        pos = _call(engine, root, "safePanelPos(200, 100)")
        assert pos["y"] + 100 <= ky, "the panel overlapped the keyboard"
        assert pos["y"] == pytest.approx(ky - GAP - 100, abs=1)
        assert kh > 0

    def test_a_keyboard_at_the_top_puts_the_panel_below_it(self, qml) -> None:
        # The inverse of the case above: a rule that always parked the
        # panel at the top of the display would satisfy that one and land
        # this one squarely on the keyboard.
        engine, root = qml
        _kx, ky, _kw, kh = _park(root, 0, 0)
        pos = _call(engine, root, "safePanelPos(200, 100)")
        assert pos["y"] >= ky + kh, "the panel overlapped the keyboard"
        assert pos["y"] == pytest.approx(ky + kh + GAP, abs=1)


class TestThePanelIsNeverOffScreen:
    """It has no title bar to drag it back by, so it cannot open outside."""

    def test_a_panel_with_no_room_either_side_is_still_on_screen(self, qml) -> None:
        engine, root = qml
        left, top, right, bottom = _screen()
        _park(root, 0, (bottom - top) // 3)
        # Taller than the space above the keyboard and the space below it.
        pos = _call(engine, root, "safePanelPos(360, 540)")
        assert left <= pos["x"] and pos["x"] + 360 <= right
        assert top <= pos["y"] and pos["y"] + 540 <= bottom

    def test_a_panel_larger_than_the_screen_is_pinned_to_its_top_left(self, qml) -> None:
        # The clamp has to be stable rather than merely arithmetic: with
        # `bottom - h` below `top`, one order of Math.min/Math.max puts the
        # panel off the top of the display instead of the bottom.
        engine, root = qml
        left, top, right, bottom = _screen()
        _park(root, 0, 0)
        pos = _call(
            engine,
            root,
            "safePanelPos(%d, %d)" % ((right - left) + 400, (bottom - top) + 400),
        )
        assert pos["x"] == left
        assert pos["y"] == top

    def test_a_point_on_no_screen_falls_back_to_the_whole_desktop(self, qml) -> None:
        # `screenBoundsAt` must never return nothing: a keyboard dragged to
        # a monitor that has since been unplugged still has to be able to
        # open its settings.
        engine, root = qml
        _left, _top, right, bottom = _screen()
        bounds = _call(engine, root, "screenBoundsAt(%d, %d)" % (right + 5000, bottom + 5000))
        desktop = _call(engine, root, "desktopBounds()")
        assert bounds == desktop


class TestTheRealWindowsUseIt:
    """A helper nothing calls is worth nothing."""

    @pytest.mark.parametrize(
        "flag,name",
        [
            ("showSettings", "settingsWindow"),
            ("showHelp", "helpWindow"),
            ("showVisualization", "vizWindow"),
        ],
    )
    def test_each_window_opens_on_screen_beside_the_keyboard(self, qml, flag, name) -> None:
        engine, root = qml
        left, top, right, bottom = _screen()
        kx, _ky, kw, _kh = _park(root, left + 30, top + 30)
        window = root.findChild(QObject, name)
        assert window is not None, f"{name} is not reachable by objectName"
        root.setProperty(flag, True)
        _settle()
        try:
            w = window.property("width")
            h = window.property("height")
            x = window.property("x")
            y = window.property("y")
            assert left <= x and x + w <= right, f"{name} opened off the side"
            assert top <= y and y + h <= bottom, f"{name} opened off the top or bottom"
            # And it is placed against the keyboard, not the screen: the
            # keyboard is parked left of centre here, so a screen-centred
            # window would sit further right than a keyboard-centred one.
            assert x == pytest.approx(max(left, min(kx + (kw - w) / 2, right - w)), abs=1), (
                f"{name} was not centred on the keyboard"
            )
        finally:
            root.setProperty(flag, False)
            _settle()
