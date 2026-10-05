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


class TestAPickerOpenedDuringATaostIsDemotedToo:
    """A picker opened mid-yield is styled topmost by its own show path, and
    its show event is not a shell popup's, so the first version never
    demoted it: HWND_TOP raises within a band and does not leave one.
    Found in review by adding a topmost picker during an active toast and
    watching it keep WS_EX_TOPMOST through the poll."""

    def test_the_show_path_demotes_it_at_once(self) -> None:
        h = Harness()
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.windows.append(PICKER)
        h.yielder.window_shown(PICKER)
        assert h.calls == [(KEYBOARD, False), (PICKER, False)]

    def test_the_poll_catches_one_the_show_path_missed(self) -> None:
        h = Harness()
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.windows.append(PICKER)
        h.run_timers(1000)
        assert h.calls == [(KEYBOARD, False), (PICKER, False)]
        assert h.raised == [KEYBOARD, PICKER], "demoted before it is raised"

    def test_a_foreground_change_catches_it_too(self) -> None:
        h = Harness()
        h.appear(PREVIEW)
        h.windows.append(PICKER)
        h.activate(APP)
        assert h.calls == [(KEYBOARD, False), (PICKER, False)]

    def test_the_poll_and_foreground_paths_demote_a_window_once(self) -> None:
        h = Harness(windows=[KEYBOARD, PICKER])
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.run_timers(1000)
        h.activate(APP)
        assert h.calls == [(KEYBOARD, False), (PICKER, False)]

    def test_a_picker_hidden_and_shown_again_is_demoted_again(self) -> None:
        # Showing it re-runs the style write, which makes it topmost again,
        # so the show path demotes unconditionally while aside.
        h = Harness(windows=[KEYBOARD, PICKER])
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.yielder.window_shown(PICKER)
        assert h.calls == [(KEYBOARD, False), (PICKER, False), (PICKER, False)]

    def test_the_restore_puts_it_back_on_top_with_the_rest(self) -> None:
        h = Harness()
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.windows.append(PICKER)
        h.yielder.window_shown(PICKER)
        h.vanish(TOAST, EVENT_OBJECT_CLOAKED)
        h.run_timers(150)
        assert h.calls[-2:] == [(KEYBOARD, True), (PICKER, True)]

    def test_a_picker_shown_while_on_top_is_left_alone(self) -> None:
        # The inverse: with no popup up, topmost is where it belongs.
        h = Harness()
        h.windows.append(PICKER)
        h.yielder.window_shown(PICKER)
        assert h.calls == []
        assert h.raised == []

    def test_the_next_yield_starts_from_scratch(self) -> None:
        # Demotions are forgotten on restore, or a picker closed and reopened
        # topmost in a later yield would be taken for already demoted.
        h = Harness(windows=[KEYBOARD, PICKER])
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        h.vanish(TOAST, EVENT_OBJECT_CLOAKED)
        h.run_timers(150)
        h.appear(TOAST, EVENT_OBJECT_UNCLOAKED)
        assert h.calls[-2:] == [(KEYBOARD, False), (PICKER, False)]


class TestThePickerShowPathReachesTheYielder:
    def test_the_handler_feeds_the_window_handle_in(self) -> None:
        from src import keyboard_app

        h = Harness()
        h.appear(PREVIEW)
        handler = keyboard_app._picker_shown_handler(h.yielder)
        assert handler is not None
        window = MagicMock()
        window.winId.return_value = PICKER
        handler(window)
        assert h.calls[-1] == (PICKER, False)

    def test_no_yielder_means_no_handler(self) -> None:
        from src import keyboard_app

        assert keyboard_app._picker_shown_handler(None) is None

    def test_wiring_styles_then_reports_each_picker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src import keyboard_app

        order: list[tuple[str, int]] = []
        monkeypatch.setattr(keyboard_app, "CURRENT_PLATFORM", "windows")
        monkeypatch.setattr(
            keyboard_app.windows_window,
            "apply_extended_styles",
            lambda w: order.append(("styled", w.winId())),
        )
        monkeypatch.setattr(
            keyboard_app.windows_window, "prefer_dwm_rounded_corners", lambda w: None
        )

        class FakeWindow:
            """Just the surface _wire_floating_windows touches."""

            def __init__(self, hwnd: int) -> None:
                self._hwnd = hwnd
                self.handlers: list[Callable[[], None]] = []
                self.visibleChanged = types.SimpleNamespace(connect=self.handlers.append)

            def winId(self) -> int:  # noqa: N802
                return self._hwnd

            def property(self, name: str) -> bool:
                return name == "visible"

        windows = {
            "snippetsWindow": FakeWindow(201),
            "symbolsWindow": FakeWindow(202),
            "studyWindow": FakeWindow(203),
            "vizWindow": FakeWindow(300),
        }
        root = MagicMock()
        root.findChild.side_effect = lambda _cls, name: windows[name]

        keyboard_app._wire_floating_windows(
            root, on_shown=lambda w: order.append(("shown", w.winId()))
        )
        for win in windows.values():
            for fn in win.handlers:
                fn()

        assert order == [
            ("styled", 201),
            ("shown", 201),
            ("styled", 202),
            ("shown", 202),
            ("styled", 203),
            ("shown", 203),
        ], "every picker is styled first and then reported; the Dashboard is neither"


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
