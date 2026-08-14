"""Tests for system-tray click routing and what a tray click does.

Two halves, both easy to regress silently because neither is visible in a
unit test of the surrounding code:

``_TrayClickRouter`` collapses a raw activation burst into one toggle.  A
single click used to sit behind a timer for the system's double-click
interval so a double click could be told apart, which made the keyboard
appear half a second late; that's gone and shouldn't come back.

``_toggle_keyboard_window`` decides where the keyboard goes.  It used to
``hide()`` the window, which dropped its taskbar entry and left the tray
icon as the only route back; it now minimizes everywhere that has a
taskbar to minimize into.

No ``QApplication`` is created here.  The router takes its clock and its
double-click interval as callables, and the toggle takes the window, so
both can be driven with fakes; ``QSystemTrayIcon.ActivationReason`` and
``QWindow.Visibility`` are plain enums that import fine without an app.
"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QSystemTrayIcon

from src.keyboard_app import _toggle_keyboard_window, _TrayClickRouter

TRIGGER = QSystemTrayIcon.ActivationReason.Trigger
DOUBLE_CLICK = QSystemTrayIcon.ActivationReason.DoubleClick
CONTEXT = QSystemTrayIcon.ActivationReason.Context
MIDDLE_CLICK = QSystemTrayIcon.ActivationReason.MiddleClick

INTERVAL_MS = 500


class FakeClock:
    """A monotonic clock the test advances by hand, in seconds."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, ms: float) -> None:
        self.now += ms / 1000.0


@pytest.fixture
def router() -> tuple[_TrayClickRouter, list[str], FakeClock]:
    toggles: list[str] = []
    clock = FakeClock()
    r = _TrayClickRouter(
        toggle=lambda: toggles.append("toggle"),
        double_click_interval_ms=lambda: INTERVAL_MS,
        clock=clock,
    )
    return r, toggles, clock


class TestSingleClick:
    def test_a_single_click_toggles_once(self, router) -> None:
        r, toggles, _clock = router
        r(TRIGGER)
        assert toggles == ["toggle"]

    def test_the_toggle_fires_immediately(self, router) -> None:
        """No waiting out the double-click interval.

        The old implementation started a QTimer on Trigger and only ran the
        toggle if no DoubleClick arrived within ``doubleClickInterval()``,
        so the keyboard appeared ~500 ms after the click.  Nothing should
        need to elapse now.
        """
        r, toggles, clock = router
        start = clock.now
        r(TRIGGER)
        assert toggles == ["toggle"]
        assert clock.now == start

    def test_clicks_spaced_beyond_the_interval_each_toggle(self, router) -> None:
        r, toggles, clock = router
        r(TRIGGER)
        clock.advance_ms(INTERVAL_MS + 1)
        r(TRIGGER)
        clock.advance_ms(INTERVAL_MS + 1)
        r(TRIGGER)
        assert toggles == ["toggle"] * 3


class TestDoubleClick:
    def test_a_windows_double_click_toggles_exactly_once(self, router) -> None:
        """Windows delivers Trigger, DoubleClick, Trigger for one double click.

        Acting on all three would toggle three times and land the window
        back where it started (or worse, mid-flicker).
        """
        r, toggles, clock = router
        r(TRIGGER)
        clock.advance_ms(40)
        r(DOUBLE_CLICK)
        clock.advance_ms(40)
        r(TRIGGER)
        assert toggles == ["toggle"]

    def test_a_double_click_that_leads_with_doubleclick_still_toggles_once(self, router) -> None:
        """Don't assume Trigger always arrives first; other platforms differ."""
        r, toggles, clock = router
        r(DOUBLE_CLICK)
        clock.advance_ms(40)
        r(TRIGGER)
        assert toggles == ["toggle"]

    def test_the_window_is_measured_from_the_last_toggle_not_the_last_event(self, router) -> None:
        """Suppressed activations must not push the debounce window forward.

        If the guard stamped its timestamp on *every* activation rather than
        only the ones that toggled, each swallowed event would re-arm it and
        the tray icon would go dead for as long as clicks kept arriving.
        The timings below are chosen so that the correct implementation
        toggles on the final click and the re-arming one does not.
        """
        r, toggles, clock = router
        r(TRIGGER)  # t=0, toggles
        clock.advance_ms(40)
        r(DOUBLE_CLICK)  # t=40, suppressed
        clock.advance_ms(40)
        r(TRIGGER)  # t=80, suppressed (tail of the double click)
        clock.advance_ms(INTERVAL_MS - 80 + 1)
        r(TRIGGER)  # t=501, a genuinely new click, 501 ms after the toggle
        assert toggles == ["toggle"] * 2


class TestNonClickActivations:
    @pytest.mark.parametrize("reason", [CONTEXT, MIDDLE_CLICK])
    def test_non_click_reasons_never_toggle(self, router, reason) -> None:
        """Right-click opens the menu; it must not also hide the keyboard."""
        r, toggles, _clock = router
        r(reason)
        assert toggles == []

    def test_a_context_activation_does_not_consume_the_debounce(self, router) -> None:
        """Opening the menu must not swallow a click that follows it."""
        r, toggles, _clock = router
        r(CONTEXT)
        r(TRIGGER)
        assert toggles == ["toggle"]


class FakeWindow:
    """The two QWindow calls the toggle makes, plus a settable visibility.

    ``_toggle_keyboard_window`` touches nothing else on the root object, so
    a stand-in is enough to drive every branch without a QApplication or a
    loaded QML tree (``main()`` cannot be exercised headlessly).  Each
    method records the call *and* moves ``_visibility`` the way the real
    window would, so a test can chain calls and assert on the round trip.
    """

    def __init__(
        self,
        visibility: QWindow.Visibility = QWindow.Visibility.Windowed,
        tucked: object = None,
    ) -> None:
        self._visibility = visibility
        self._tucked = tucked
        self.calls: list[str] = []

    def visibility(self) -> QWindow.Visibility:
        return self._visibility

    def property(self, name: str) -> object:
        """``QObject.property`` returns None for a name the object lacks.

        The default stands in for every window that isn't the X11 tuck
        case — including the real one on Windows and macOS, where
        ``tucked`` exists in QML but is never true.
        """
        return self._tucked if name == "tucked" else None

    def showMinimized(self) -> None:
        self.calls.append("showMinimized")
        self._visibility = QWindow.Visibility.Minimized

    def showNormal(self) -> None:
        self.calls.append("showNormal")
        self._visibility = QWindow.Visibility.Windowed

    def hide(self) -> None:
        self.calls.append("hide")
        self._visibility = QWindow.Visibility.Hidden

    def raise_(self) -> None:
        self.calls.append("raise_")


class TestTrayMinimizes:
    """A tray click parks the keyboard in the taskbar, not out of existence.

    The window carries a normal taskbar entry (no ``WS_EX_TOOLWINDOW``), so
    minimizing leaves the user a second, more discoverable way back than
    the tray icon.  ``hide()`` removed that entry and made the tray the
    only route — the behaviour these tests exist to keep from returning.
    """

    @pytest.mark.parametrize("platform_name", ["windows", "linux"])
    def test_a_visible_window_is_minimized_not_hidden(self, platform_name) -> None:
        win = FakeWindow()
        _toggle_keyboard_window(win, platform_name)
        assert win.calls == ["showMinimized"]

    @pytest.mark.parametrize("platform_name", ["windows", "linux", "macos"])
    def test_a_minimized_window_is_restored(self, platform_name) -> None:
        win = FakeWindow(QWindow.Visibility.Minimized)
        _toggle_keyboard_window(win, platform_name)
        assert win.calls == ["showNormal", "raise_"]

    @pytest.mark.parametrize("platform_name", ["windows", "linux"])
    def test_two_clicks_return_the_window_to_where_it_started(self, platform_name) -> None:
        win = FakeWindow()
        _toggle_keyboard_window(win, platform_name)
        _toggle_keyboard_window(win, platform_name)
        assert win.calls == ["showMinimized", "showNormal", "raise_"]
        assert win.visibility() == QWindow.Visibility.Windowed

    def test_a_window_minimized_by_the_title_bar_button_is_restored(self) -> None:
        """The restore branch keys on the window, not on what we did last.

        The title-bar minus button and the taskbar both minimize without
        going through this function, and a tray click has to bring those
        back too — otherwise it would try to minimize an already-minimized
        window and read as a dead icon.
        """
        win = FakeWindow()
        win.showMinimized()  # stands in for the QML minus button
        win.calls.clear()
        _toggle_keyboard_window(win, "windows")
        assert win.calls == ["showNormal", "raise_"]


class TestTuckedWindowStillHides:
    """A tucked (DOCK-typed) window has no taskbar entry and can't minimize.

    ``showMinimized()`` is inert while the window is DOCK-typed, so the
    minimize branch would leave the tray icon looking dead.  Hiding keeps
    the round trip working: the ``onVisibilityChanged`` handler in
    ``Main.qml`` untucks and restores the on-screen position on the way
    back up.
    """

    def test_a_tucked_window_is_hidden_not_minimized(self) -> None:
        win = FakeWindow(tucked=True)
        _toggle_keyboard_window(win, "linux")
        assert win.calls == ["hide"]

    def test_an_untucked_window_still_minimizes(self) -> None:
        """The inverse, so 'always hide' can't pass as a fix."""
        win = FakeWindow(tucked=False)
        _toggle_keyboard_window(win, "linux")
        assert win.calls == ["showMinimized"]


class TestMacOSStillHides:
    """macOS has nothing to minimize into, so it keeps the hide/show pair.

    The app runs under the Accessory activation policy: no Dock icon, no
    taskbar entry.  Minimizing there would be the branch that leaves the
    tray icon as the only way back, which is the opposite of the point.
    """

    def test_a_visible_window_is_hidden(self) -> None:
        win = FakeWindow()
        _toggle_keyboard_window(win, "macos")
        assert win.calls == ["hide"]

    def test_a_hidden_window_is_restored(self) -> None:
        win = FakeWindow(QWindow.Visibility.Hidden)
        _toggle_keyboard_window(win, "macos")
        assert win.calls == ["showNormal", "raise_"]
