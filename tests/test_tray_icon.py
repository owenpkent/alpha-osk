"""Tests for system-tray click routing.

The behaviour under test is small but easy to regress silently, because
nothing about it is visible in a unit test of the surrounding code: the
router used to map a tray double-click to ``showMinimized()``, and a
single click sat behind a timer for the system's double-click interval so
the two gestures could be told apart.  Both are gone; these tests exist so
neither comes back unnoticed.

No ``QApplication`` is created here.  ``_TrayClickRouter`` takes its clock
and its double-click interval as callables precisely so it can be driven
with fakes, and ``QSystemTrayIcon.ActivationReason`` is a plain enum that
imports fine without a running app.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QSystemTrayIcon

from src.keyboard_app import _TrayClickRouter

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


class TestTrayNeverMinimizes:
    def test_nothing_in_keyboard_app_calls_showminimized(self) -> None:
        """No code path in the module minimizes the window.

        This is a source-level assertion because the tray wiring lives
        inside ``main()``, which cannot be exercised headlessly.  It walks
        the AST rather than grepping the text so that *documenting* the
        removed behaviour (this file and the router's docstring both name
        ``showMinimized``) can't fail the test: only a real attribute
        access counts.  Minimize belongs to the title-bar minus button,
        which lives in QML.
        """
        import ast
        from pathlib import Path

        import src.keyboard_app as keyboard_app

        tree = ast.parse(Path(keyboard_app.__file__).read_text(encoding="utf-8"))
        accessed = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "showMinimized" not in accessed
