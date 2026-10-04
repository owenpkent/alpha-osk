"""The keyboard steps out of always-on-top while a notification or preview is up.

A UIAccess window that is always-on-top sits in a Z-order band above
notifications and taskbar previews, so from 1.6.0 they opened behind the
keyboard.  ``ShellPopupYielder`` drops our windows to ``HWND_NOTOPMOST``
while one is showing and puts them back afterwards.  Every Win32 call is
injected, so this drives it with fakes on any platform.
"""

from __future__ import annotations

import sys
import types
from typing import Callable, Optional
from unittest.mock import MagicMock

import pytest

from src.platform.windows_window import (
    EVENT_OBJECT_CLOAKED,
    EVENT_OBJECT_DESTROY,
    EVENT_OBJECT_HIDE,
    EVENT_OBJECT_SHOW,
    EVENT_OBJECT_UNCLOAKED,
    EVENT_SYSTEM_FOREGROUND,
    ZBID_IMMERSIVE_NOTIFICATION,
    ShellPopupYielder,
    install_shell_popup_yield,
    is_shell_popup,
)

KEYBOARD = 100
PICKER = 200
TOAST = 1
PREVIEW = 2
TOOLTIP = 3
APP_COREWINDOW = 4
APP = 5  # an ordinary application window, say a maximised browser

DESKTOP_BAND = 1
CLASSES: dict[int, tuple[str, Optional[int]]] = {
    TOAST: ("Windows.UI.Core.CoreWindow", ZBID_IMMERSIVE_NOTIFICATION),
    PREVIEW: ("XamlExplorerHostIslandWindow", None),
    TOOLTIP: ("tooltips_class32", None),
    # A store app's own window shares the toast's class, in the ordinary band.
    APP_COREWINDOW: ("Windows.UI.Core.CoreWindow", DESKTOP_BAND),
}


class Harness:
    def __init__(self, windows: list[int] | None = None) -> None:
        self.windows = [KEYBOARD] if windows is None else windows
        self.calls: list[tuple[int, bool]] = []
        self.raised: list[int] = []
        self.showing: set[int] = set()
        self.timers: list[tuple[int, Callable[[], None]]] = []
        self.yielder = ShellPopupYielder(
            windows=lambda: list(self.windows),
            set_topmost=lambda h, top: self.calls.append((h, top)),
            raise_window=self.raised.append,
            describe=CLASSES.get,
            still_showing=lambda h: h in self.showing,
            schedule=lambda ms, fn: self.timers.append((ms, fn)),
        )

    def appear(self, hwnd: int, event: int = EVENT_OBJECT_SHOW) -> None:
        self.showing.add(hwnd)
        self.yielder.on_event(event, hwnd)

    def vanish(self, hwnd: int, event: int = EVENT_OBJECT_HIDE) -> None:
        self.showing.discard(hwnd)
        self.yielder.on_event(event, hwnd)

    def activate(self, hwnd: int) -> None:
        """The user switches to (or an app opens) an ordinary window."""
        self.yielder.on_event(EVENT_SYSTEM_FOREGROUND, hwnd)

    def run_timers(self, ms: Optional[int] = None) -> None:
        """Fire the pending timers (only those of one interval, if given)."""
        due = [t for t in self.timers if ms is None or t[0] == ms]
        self.timers = [t for t in self.timers if t not in due]
        for _, fn in due:
            fn()


class TestWhichWindowsCount:
    def test_a_toast_counts(self) -> None:
        assert is_shell_popup("Windows.UI.Core.CoreWindow", ZBID_IMMERSIVE_NOTIFICATION)

    def test_the_toast_class_outside_the_notification_band_does_not(self) -> None:
        # Every UWP app window is a CoreWindow; only the band marks a toast.
        assert not is_shell_popup("Windows.UI.Core.CoreWindow", DESKTOP_BAND)
        assert not is_shell_popup("Windows.UI.Core.CoreWindow", None)

    @pytest.mark.parametrize("cls", ["XamlExplorerHostIslandWindow", "TaskListThumbnailWnd"])
    def test_taskbar_previews_count(self, cls: str) -> None:
        assert is_shell_popup(cls, None)

    @pytest.mark.parametrize("cls", ["tooltips_class32", "Chrome_WidgetWin_1", "#32768", ""])
    def test_ordinary_windows_do_not(self, cls: str) -> None:
        assert not is_shell_popup(cls, None)


class TestSteppingAside:
    def test_a_toast_drops_the_keyboard_out_of_always_on_top(self) -> None:
        h = Harness()
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        assert h.calls == [(KEYBOARD, False)]
        assert h.yielder.stepped_aside

    def test_a_preview_drops_it_too(self) -> None:
        h = Harness()
        h.appear(PREVIEW)
        assert h.calls == [(KEYBOARD, False)]

    @pytest.mark.parametrize("hwnd", [TOOLTIP, APP_COREWINDOW, 999])
    def test_other_windows_leave_it_alone(self, hwnd: int) -> None:
        h = Harness()
        h.appear(hwnd)
        assert h.calls == []
        assert not h.yielder.stepped_aside

    def test_it_comes_back_once_the_toast_is_dismissed(self) -> None:
        h = Harness()
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.vanish(TOAST, EVENT_OBJECT_CLOAKED)
        assert h.calls == [(KEYBOARD, False)], "restores only after the delay"
        h.run_timers(150)
        assert h.calls == [(KEYBOARD, False), (KEYBOARD, True)]
        assert not h.yielder.stepped_aside

    @pytest.mark.parametrize("gone", [EVENT_OBJECT_HIDE, EVENT_OBJECT_DESTROY])
    def test_hide_and_destroy_both_end_a_preview(self, gone: int) -> None:
        h = Harness()
        h.appear(PREVIEW)
        h.vanish(PREVIEW, gone)
        h.run_timers(150)
        assert h.calls[-1] == (KEYBOARD, True)

    def test_it_stays_aside_while_any_popup_is_still_up(self) -> None:
        h = Harness()
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.appear(PREVIEW)
        h.vanish(TOAST, EVENT_OBJECT_CLOAKED)
        h.run_timers(150)
        assert h.calls == [(KEYBOARD, False)]
        h.vanish(PREVIEW)
        h.run_timers(150)
        assert h.calls == [(KEYBOARD, False), (KEYBOARD, True)]

    def test_a_popup_that_returns_within_the_delay_does_not_flicker(self) -> None:
        # The next toast reuses the same window: cloak, then uncloak at once.
        h = Harness()
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.vanish(TOAST, EVENT_OBJECT_CLOAKED)
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.run_timers(150)
        assert h.calls == [(KEYBOARD, False)]
        assert h.yielder.stepped_aside

    def test_a_repeated_show_steps_aside_once(self) -> None:
        h = Harness()
        h.appear(PREVIEW)
        h.appear(PREVIEW)
        assert h.calls == [(KEYBOARD, False)]

    def test_hiding_an_unrelated_window_restores_nothing(self) -> None:
        h = Harness()
        h.appear(PREVIEW)
        h.vanish(TOOLTIP)
        h.run_timers(150)
        assert h.calls == [(KEYBOARD, False)]

    def test_a_lost_hide_event_is_caught_by_the_poll(self) -> None:
        h = Harness()
        h.appear(PREVIEW)
        h.run_timers(1000)
        assert h.calls == [(KEYBOARD, False)], "still showing: no restore"
        h.showing.discard(PREVIEW)  # gone, with no event
        h.run_timers(1000)
        assert h.calls == [(KEYBOARD, False), (KEYBOARD, True)]

    def test_the_poll_stops_once_restored(self) -> None:
        h = Harness()
        h.appear(PREVIEW)
        h.vanish(PREVIEW)
        h.run_timers(150)
        h.run_timers(1000)
        assert h.timers == []


class TestThePickersStayAboveTheKeyboard:
    def test_both_passes_walk_the_windows_in_the_same_order(self) -> None:
        # The pickers were raised after the keyboard, so they sit above it.
        # Restoring the keyboard last would bury an open picker under it.
        h = Harness(windows=[KEYBOARD, PICKER])
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.vanish(TOAST, EVENT_OBJECT_CLOAKED)
        h.run_timers(150)
        assert h.calls == [
            (KEYBOARD, False),
            (PICKER, False),
            (KEYBOARD, True),
            (PICKER, True),
        ]

    def test_a_hidden_keyboard_is_not_touched(self) -> None:
        h = Harness(windows=[])
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        assert h.calls == []


class TestTheKeyboardStaysAboveApplicationsWhileAside:
    """HWND_NOTOPMOST is a one-shot: the next application the user activates
    goes above a stepped-aside keyboard, and a window that never takes focus
    cannot climb back on its own.  Found in review by raising an ordinary
    window over the demoted keyboard during a toast and watching it stay
    there, so the guarantee that the keyboard stays above applications has
    to be re-asserted for as long as the yield lasts."""

    def test_an_app_activated_during_a_toast_is_put_below_the_keyboard_again(self) -> None:
        h = Harness()
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.activate(APP)
        assert h.raised == [KEYBOARD]
        assert h.calls == [(KEYBOARD, False)], "re-raised within the ordinary group, not topmost"
        assert h.yielder.stepped_aside, "the toast is still up"

    def test_every_activation_re_raises(self) -> None:
        h = Harness()
        h.appear(PREVIEW)
        h.activate(APP)
        h.activate(APP + 1)
        assert h.raised == [KEYBOARD, KEYBOARD]

    def test_the_pickers_are_raised_after_the_keyboard(self) -> None:
        h = Harness(windows=[KEYBOARD, PICKER])
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.activate(APP)
        assert h.raised == [KEYBOARD, PICKER]

    def test_a_window_raised_without_activation_is_caught_by_the_poll(self) -> None:
        # SetWindowPos(HWND_TOP) from the app fires no event we hook.
        h = Harness()
        h.appear(PREVIEW)
        h.run_timers(1000)
        assert h.raised == [KEYBOARD]
        assert h.yielder.stepped_aside

    def test_an_activation_while_on_top_does_nothing(self) -> None:
        # Topmost already beats every application; there is nothing to fix.
        h = Harness()
        h.activate(APP)
        assert h.raised == []
        assert h.calls == []

    def test_an_activation_after_the_restore_does_nothing(self) -> None:
        h = Harness()
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.vanish(TOAST, EVENT_OBJECT_CLOAKED)
        h.run_timers(150)
        h.activate(APP)
        assert h.raised == []


@pytest.mark.skipif(sys.platform != "win32", reason="builds the real ctypes callback")
class TestInstallingTheHooks:
    """The installer registers three WinEvent hooks against one ctypes
    callback.  If a later registration fails, the earlier ones must be
    unhooked before the callback goes out of scope: Windows only drops a
    hook with its thread, so a hook left behind would call freed memory on
    the next window shown anywhere.  Found in review with injected hook
    results; the yielder's own tests never reach this path."""

    @pytest.fixture
    def user32(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        import ctypes

        user32 = MagicMock()
        user32.SetWindowPos.return_value = 1
        monkeypatch.setattr(
            ctypes,
            "windll",
            types.SimpleNamespace(user32=user32, dwmapi=MagicMock()),
        )
        return user32

    @staticmethod
    def _hooks(user32: MagicMock) -> list[tuple[int, int]]:
        return [(c.args[0], c.args[1]) for c in user32.SetWinEventHook.call_args_list]

    def test_all_three_hooks_take_and_the_callback_is_pinned(self, user32: MagicMock) -> None:
        user32.SetWinEventHook.side_effect = [11, 22, 33]
        yielder = install_shell_popup_yield(lambda: [])
        assert yielder is not None
        assert yielder._hooks == [11, 22, 33]
        assert yielder._callback is not None
        user32.UnhookWinEvent.assert_not_called()
        assert (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND) in self._hooks(user32)

    @pytest.mark.parametrize(
        "results, expect_unhooked",
        [
            ([0], []),
            ([11, 0], [11]),
            ([11, 22, 0], [11, 22]),
        ],
    )
    def test_a_failed_registration_unhooks_the_ones_before_it(
        self, user32: MagicMock, results: list[int], expect_unhooked: list[int]
    ) -> None:
        user32.SetWinEventHook.side_effect = results
        assert install_shell_popup_yield(lambda: []) is None
        assert [c.args[0] for c in user32.UnhookWinEvent.call_args_list] == expect_unhooked

    def test_an_exception_during_registration_unhooks_too(self, user32: MagicMock) -> None:
        user32.SetWinEventHook.side_effect = [11, RuntimeError("no more hooks")]
        assert install_shell_popup_yield(lambda: []) is None
        assert [c.args[0] for c in user32.UnhookWinEvent.call_args_list] == [11]
