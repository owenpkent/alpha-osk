"""Tests for the *Always on Top* setting's native Z-band handling.

The Win32 and Xlib layers are faked: nothing here touches the real desktop.
Every positive case is paired with its inverse (on vs off), because a band
change that always did the same thing would satisfy either alone.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtGui import QMouseEvent

import src.keyboard_app as keyboard_app
import src.platform.window_band as wb
import src.platform.x11_window as x11w
from src.keyboard_bridge import KeyboardBridge
from src.platform.windows_window import (
    QuietRestoreFilter,
    ShellPopupYielder,
    apply_extended_styles,
    bring_to_front_noactivate,
    raise_window_noactivate,
    set_window_band,
)

HWND_TOP = 0
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010


@pytest.fixture
def user32(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    u = MagicMock()
    u.GetWindowLongW.return_value = 0x100
    u.SetWindowLongW.return_value = 0x100
    u.SetWindowPos.return_value = 1
    k = MagicMock()
    k.GetLastError.return_value = 0
    d = MagicMock()
    d.DwmSetWindowAttribute.return_value = 0
    import ctypes

    monkeypatch.setattr(
        ctypes, "windll", types.SimpleNamespace(user32=u, kernel32=k, dwmapi=d), raising=False
    )
    monkeypatch.setattr(sys, "platform", "win32")
    return u


def _root() -> MagicMock:
    root = MagicMock()
    root.winId.return_value = 0x1234
    return root


def _pos_calls(u: MagicMock) -> list[tuple]:
    return [c[0] for c in u.SetWindowPos.call_args_list]


class TestStartupEndsInTheSavedBand:
    def test_default_issues_hwnd_topmost_as_before(self, user32) -> None:
        apply_extended_styles(_root(), taskbar_button=True)

        (call,) = _pos_calls(user32)
        assert call[1] == HWND_TOPMOST

    def test_saved_off_issues_hwnd_notopmost(self, user32) -> None:
        apply_extended_styles(_root(), taskbar_button=True, topmost=False)

        (call,) = _pos_calls(user32)
        assert call[1] == HWND_NOTOPMOST
        assert not call[6] & SWP_NOZORDER
        assert call[6] & SWP_NOACTIVATE

    def test_apply_window_flags_hands_the_setting_to_the_styles(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        styles = MagicMock()
        monkeypatch.setattr(keyboard_app, "CURRENT_PLATFORM", "windows")
        monkeypatch.setattr(keyboard_app.windows_window, "apply_extended_styles", styles)

        keyboard_app._apply_window_flags(MagicMock(), False)
        assert styles.call_args.kwargs["topmost"] is False

        styles.reset_mock()
        keyboard_app._apply_window_flags(MagicMock())
        assert styles.call_args.kwargs["topmost"] is True

    def test_qt_flags_keep_the_topmost_hint_when_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Flags are never changed at runtime: the hint stays, the band moves."""
        monkeypatch.setattr(keyboard_app, "CURRENT_PLATFORM", "windows")
        monkeypatch.setattr(keyboard_app.windows_window, "apply_extended_styles", MagicMock())
        root = MagicMock()

        keyboard_app._apply_window_flags(root, False)

        flags = root.setFlags.call_args[0][0]
        assert flags & Qt.WindowType.WindowStaysOnTopHint


class TestBridgeHoldsTheSetting:
    def test_defaults_on_and_announces_only_changes(self) -> None:
        bridge = KeyboardBridge()
        seen: list[bool] = []
        bridge.alwaysOnTopChanged.connect(seen.append)

        assert bridge.alwaysOnTop is True
        bridge.setAlwaysOnTop(True)
        assert seen == []
        bridge.setAlwaysOnTop(False)
        assert bridge.alwaysOnTop is False
        assert seen == [False]


class TestBandCalls:
    def test_toggle_issues_the_right_calls_without_activating(self, user32) -> None:
        set_window_band(7, True)
        set_window_band(7, False)
        raise_window_noactivate(7)

        calls = _pos_calls(user32)
        assert [c[1] for c in calls] == [HWND_TOPMOST, HWND_NOTOPMOST, HWND_TOP]
        for c in calls:
            assert c[6] & SWP_NOACTIVATE
            assert not c[6] & SWP_NOZORDER
        user32.SetForegroundWindow.assert_not_called()

    def test_bring_to_front_passes_through_the_topmost_band(self, user32) -> None:
        """A plain HWND_TOP from a background process is ignored above the
        foreground app (seen live), so the keyboard's raise enters the topmost
        band and leaves it, ending at the top of the ordinary band. The inverse:
        it must end NOT topmost, or the setting would be silently undone."""
        bring_to_front_noactivate(7)

        calls = _pos_calls(user32)
        assert [c[1] for c in calls] == [HWND_TOPMOST, HWND_NOTOPMOST]
        for c in calls:
            assert c[6] & SWP_NOACTIVATE
        user32.SetForegroundWindow.assert_not_called()

    def test_dispatch_on_windows_uses_the_window_handle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mod = MagicMock()
        monkeypatch.setattr(wb, "CURRENT_PLATFORM", "windows")
        monkeypatch.setattr(wb, "windows_window", mod)

        wb.set_keyboard_topmost(_root(), False)
        mod.set_window_band.assert_called_once_with(0x1234, False)

        wb.raise_keyboard(_root())
        mod.bring_to_front_noactivate.assert_called_once_with(0x1234)
        mod.raise_window_noactivate.assert_not_called()

    def test_reapply_after_a_restore_raises_only_when_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        band = MagicMock()
        monkeypatch.setattr(keyboard_app, "window_band", band)
        monkeypatch.setattr(keyboard_app, "_floating_on_top_windows", lambda root: [])
        root = MagicMock()

        keyboard_app._reapply_band(root, types.SimpleNamespace(alwaysOnTop=True), None)
        band.set_keyboard_topmost.assert_called_once_with(root, True)
        band.raise_keyboard.assert_not_called()

        band.reset_mock()
        keyboard_app._reapply_band(root, types.SimpleNamespace(alwaysOnTop=False), None)
        band.set_keyboard_topmost.assert_called_once_with(root, False)
        band.raise_keyboard.assert_called_once_with(root)


class TestPressToRaise:
    @staticmethod
    def _press() -> QMouseEvent:
        return QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(1, 1),
            QPointF(1, 1),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )

    def test_raises_on_a_press_when_off_and_lets_it_through(self) -> None:
        window = QObject()
        raised = MagicMock()
        flt = wb.RaiseOnPressFilter(window, always_on_top=lambda: False, raise_fn=raised)

        assert flt.eventFilter(window, self._press()) is False
        raised.assert_called_once_with(window)

    def test_does_nothing_when_on(self) -> None:
        window = QObject()
        raised = MagicMock()
        flt = wb.RaiseOnPressFilter(window, always_on_top=lambda: True, raise_fn=raised)

        assert flt.eventFilter(window, self._press()) is False
        raised.assert_not_called()

    def test_ignores_other_events_and_other_objects(self) -> None:
        window = QObject()
        raised = MagicMock()
        flt = wb.RaiseOnPressFilter(window, always_on_top=lambda: False, raise_fn=raised)

        assert flt.eventFilter(window, QEvent(QEvent.Type.Show)) is False
        assert flt.eventFilter(QObject(), self._press()) is False
        raised.assert_not_called()

    def test_a_failing_raise_never_blocks_the_press(self) -> None:
        window = QObject()
        flt = wb.RaiseOnPressFilter(
            window, always_on_top=lambda: False, raise_fn=MagicMock(side_effect=OSError)
        )
        assert flt.eventFilter(window, self._press()) is False

    def test_quiet_restore_reapplies_the_band(self) -> None:
        fixed = MagicMock()
        shown = MagicMock()
        flt = QuietRestoreFilter(
            MagicMock(), show_window=shown, defer=lambda fn: fn(), after_restore=fixed
        )
        flt._restore_quietly(5)

        shown.assert_called_once_with(5, 4)
        fixed.assert_called_once_with()


class TestYielderInterplay:
    TOAST = ("Windows.UI.Core.CoreWindow", 4)

    def _setup(self, monkeypatch: pytest.MonkeyPatch, kb_on: list[bool]):
        keyboard = MagicMock()
        keyboard.isVisible.return_value = True
        picker = MagicMock()
        picker.isVisible.return_value = True
        picker.flags.return_value = Qt.WindowType.WindowStaysOnTopHint
        ids = {id(keyboard): 1, id(picker): 2}
        monkeypatch.setattr(
            keyboard_app.QApplication, "topLevelWindows", staticmethod(lambda: [keyboard, picker])
        )
        calls: list[tuple[int, bool]] = []
        yielder = ShellPopupYielder(
            windows=lambda: [
                ids[id(w)] for w in keyboard_app._always_on_top_windows(keyboard, lambda: kb_on[0])
            ],
            set_topmost=lambda h, t: calls.append((h, t)),
            raise_window=lambda h: None,
            describe=lambda h: self.TOAST,
            still_showing=lambda h: True,
            schedule=lambda ms, fn: None,
        )
        return yielder, calls

    def test_keyboard_is_in_the_set_when_on_and_out_when_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kb_on = [True]
        yielder, calls = self._setup(monkeypatch, kb_on)
        yielder.on_event(0x8002, 99)
        assert calls == [(1, False), (2, False)]

        # Live, not captured once: flip the setting and a fresh pass changes.
        calls.clear()
        kb_on[0] = False
        yielder._restore()
        assert calls == [(2, True)]

    def test_never_retopmosts_the_keyboard_while_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        yielder, calls = self._setup(monkeypatch, [False])
        yielder.on_event(0x8002, 99)
        yielder._restore()

        assert (1, True) not in calls and (1, False) not in calls
        assert (2, False) in calls and (2, True) in calls

    def test_turning_on_mid_aside_recovers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        kb_on = [False]
        yielder, calls = self._setup(monkeypatch, kb_on)
        yielder.on_event(0x8002, 99)
        kb_on[0] = True
        assert yielder.stepped_aside
        yielder._restore()

        assert (1, True) in calls

    def test_app_layer_defers_turning_on_while_aside(self, monkeypatch: pytest.MonkeyPatch) -> None:
        band = MagicMock()
        monkeypatch.setattr(keyboard_app, "window_band", band)
        aside = types.SimpleNamespace(stepped_aside=True)
        idle = types.SimpleNamespace(stepped_aside=False)

        keyboard_app._apply_always_on_top(MagicMock(), True, aside)
        band.set_keyboard_topmost.assert_not_called()

        keyboard_app._apply_always_on_top(MagicMock(), True, idle)
        assert band.set_keyboard_topmost.call_args[0][1] is True

    def test_app_layer_turning_off_demotes_and_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        band = MagicMock()
        monkeypatch.setattr(keyboard_app, "window_band", band)
        monkeypatch.setattr(keyboard_app, "_floating_on_top_windows", lambda root: [])
        root = MagicMock()

        keyboard_app._apply_always_on_top(root, False, types.SimpleNamespace(stepped_aside=True))

        band.set_keyboard_topmost.assert_called_once_with(root, False)
        band.raise_keyboard.assert_called_once_with(root)


class TestThePickersKeepTheirBand:
    """The pickers and the Settings window are the keyboard's owned windows,
    and Win32 moves owned windows with their owner: HWND_NOTOPMOST on the
    keyboard took them out of the topmost band too, and so did every
    press-to-front (topmost, then not).  Found in review with two nested
    QML windows: after the setting went off both had lost WS_EX_TOPMOST,
    and a re-topmosted picker lost it again on the next keyboard press.
    The fake here models that propagation, which the plain mocks cannot."""

    @staticmethod
    def _desktop(monkeypatch: pytest.MonkeyPatch, *, picker_visible: bool = True):
        """A keyboard owning one picker, with a band table that propagates."""
        keyboard = MagicMock(name="keyboard")
        keyboard.isVisible.return_value = True
        picker = MagicMock(name="picker")
        picker.isVisible.return_value = picker_visible
        picker.flags.return_value = Qt.WindowType.WindowStaysOnTopHint
        monkeypatch.setattr(
            keyboard_app.QApplication, "topLevelWindows", staticmethod(lambda: [keyboard, picker])
        )
        topmost = {keyboard: True, picker: True}
        owned = {keyboard: [picker]}
        log: list[tuple[str, object, bool]] = []

        def set_topmost(window, on: bool) -> None:
            topmost[window] = on
            for child in owned.get(window, []):
                topmost[child] = on  # what SetWindowPos does to owned windows
            log.append(("band", window, on))

        def raise_keyboard(window) -> None:
            # bring_to_front_noactivate: through the topmost band and out.
            set_topmost(window, True)
            set_topmost(window, False)

        band = types.SimpleNamespace(
            set_keyboard_topmost=set_topmost,
            raise_keyboard=raise_keyboard,
            RaiseOnPressFilter=wb.RaiseOnPressFilter,
        )
        monkeypatch.setattr(keyboard_app, "window_band", band)
        return keyboard, picker, topmost, log

    def test_turning_the_setting_off_leaves_an_open_picker_topmost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keyboard, picker, topmost, _ = self._desktop(monkeypatch)

        keyboard_app._apply_always_on_top(keyboard, False, None)

        assert topmost[keyboard] is False
        assert topmost[picker] is True

    def test_a_press_to_front_leaves_an_open_picker_topmost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keyboard, picker, topmost, _ = self._desktop(monkeypatch)
        topmost[keyboard] = False

        keyboard_app._raise_keyboard_keeping_pickers(keyboard, None)

        assert topmost[keyboard] is False, "the raise must not undo the setting"
        assert topmost[picker] is True

    def test_a_quiet_restore_with_the_setting_off_does_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        keyboard, picker, topmost, _ = self._desktop(monkeypatch)

        keyboard_app._reapply_band(keyboard, types.SimpleNamespace(alwaysOnTop=False), None)

        assert topmost[keyboard] is False
        assert topmost[picker] is True

    def test_the_picker_is_put_back_after_the_keyboard_moves_not_before(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Owner first: a picker re-topmosted before the owner's demotion
        # would be taken along again.
        keyboard, picker, _, log = self._desktop(monkeypatch)

        keyboard_app._apply_always_on_top(keyboard, False, None)

        assert log[-1] == ("band", picker, True)
        assert all(window is keyboard for _, window, _ in log[:-1])

    def test_a_hidden_picker_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        keyboard, picker, _, log = self._desktop(monkeypatch, picker_visible=False)

        keyboard_app._apply_always_on_top(keyboard, False, None)

        assert all(window is keyboard for _, window, _ in log)

    def test_not_while_the_yielder_has_them_aside(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The inverse: during a notification the ordinary band is where the
        # picker belongs, and re-topmosting it would put it over the toast.
        keyboard, picker, topmost, _ = self._desktop(monkeypatch)
        topmost[keyboard] = False
        topmost[picker] = False
        aside = types.SimpleNamespace(stepped_aside=True)

        keyboard_app._raise_keyboard_keeping_pickers(keyboard, aside)

        assert topmost[picker] is False

    def test_turning_the_setting_on_needs_no_repair(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # HWND_TOPMOST on the owner makes the owned windows topmost with it.
        keyboard, picker, topmost, log = self._desktop(monkeypatch)
        topmost[keyboard] = False

        keyboard_app._apply_always_on_top(keyboard, True, None)

        assert topmost[keyboard] is True and topmost[picker] is True
        assert log == [("band", keyboard, True)]

    def test_the_press_filter_in_main_uses_the_picker_keeping_raise(self) -> None:
        from pathlib import Path

        source = Path(keyboard_app.__file__).read_text(encoding="utf-8")
        body = source.split("def main(", 1)[1]
        assert "raise_fn=lambda w: _raise_keyboard_keeping_pickers(w, shell_popup_yield)" in body


class TestX11:
    @pytest.fixture
    def xlib(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        lib = MagicMock()
        lib.XDefaultRootWindow.return_value = 999
        monkeypatch.setattr(x11w, "is_x11", lambda: True)
        monkeypatch.setattr(x11w, "_ensure_display", lambda: 0xD15)
        monkeypatch.setattr(x11w, "_xlib", lib)
        atoms = {b"_NET_WM_STATE": 11, b"_NET_WM_STATE_ABOVE": 22}
        monkeypatch.setattr(x11w, "_atom", lambda name: atoms[name])
        return lib

    @pytest.mark.parametrize(("above", "action"), [(True, 1), (False, 0)])
    def test_client_message_has_the_right_atoms_and_action(
        self, xlib: MagicMock, above: bool, action: int
    ) -> None:
        assert x11w.set_window_above(42, above) is True

        args = xlib.XSendEvent.call_args[0]
        assert args[1] == 999  # the root window
        ev = args[4]._obj.client
        assert ev.type == 33  # ClientMessage
        assert ev.window == 42
        assert ev.message_type == 11
        assert ev.format == 32
        assert list(ev.data)[:2] == [action, 22]

    def test_wayland_or_no_display_is_a_no_op(
        self, xlib: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(x11w, "is_x11", lambda: False)

        assert x11w.set_window_above(42, True) is False
        assert x11w.raise_window(42) is False
        xlib.XSendEvent.assert_not_called()
        xlib.XRaiseWindow.assert_not_called()

    def test_raise_uses_xraisewindow(self, xlib: MagicMock) -> None:
        assert x11w.raise_window(42) is True
        xlib.XRaiseWindow.assert_called_once()


class TestAHiddenWindowGetsItsBandBackWhenShown:
    """Settings, Help and the Dashboard are owned windows too, and with the
    setting off a press-to-front drags them out of the topmost band even
    while hidden.  `_restore_floating_bands` repairs only visible windows,
    and these three had no show-time styling, so one closed and reopened
    after a keyboard click came back coverable.  Found in review: an owned
    Qt window demoted while hidden stayed demoted after show().  The fix
    is the show-time table in `_floating_window_styles`."""

    @pytest.fixture
    def desktop(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(keyboard_app.windows_window, "prefer_dwm_rounded_corners", MagicMock())
        return TestThePickersKeepTheirBand._desktop(monkeypatch, picker_visible=False)

    @pytest.mark.parametrize("name", ["settingsWindow", "helpWindow", "vizWindow"])
    def test_reopened_after_a_keyboard_press_it_is_topmost_again(self, desktop, name: str) -> None:
        keyboard, window, topmost, _ = desktop
        topmost[keyboard] = False

        # The reviewer's sequence: closed, a keyboard key pressed, reopened.
        keyboard_app._raise_keyboard_keeping_pickers(keyboard, None)
        assert topmost[window] is False, "the hidden window should have been taken along"
        window.isVisible.return_value = True
        keyboard_app._floating_window_styles()[name](window)

        assert topmost[window] is True
        assert topmost[keyboard] is False, "only the shown window goes back on top"

    @pytest.mark.parametrize("name", ["settingsWindow", "helpWindow", "vizWindow"])
    def test_the_yielder_hears_after_the_band_so_it_can_demote_again(
        self, desktop, name: str
    ) -> None:
        # The inverse: during a notification the window must end demoted,
        # so the yielder's hook has to run last.
        _, window, topmost, _ = desktop

        def yielder_window_shown(w) -> None:
            topmost[w] = False

        keyboard_app._floating_window_styles(yielder_window_shown)[name](window)

        assert topmost[window] is False

    def test_every_wired_window_reports_to_the_yielder(
        self, desktop, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, window, _, _ = desktop
        monkeypatch.setattr(keyboard_app.windows_window, "apply_extended_styles", MagicMock())
        heard: list[object] = []
        styles = keyboard_app._floating_window_styles(heard.append)
        for style in styles.values():
            style(window)
        assert set(styles) == {
            "snippetsWindow",
            "symbolsWindow",
            "studyWindow",
            "settingsWindow",
            "helpWindow",
            "vizWindow",
        }
        assert heard == [window] * len(styles)


class _ExposableWindow(QObject):
    def __init__(self) -> None:
        super().__init__()
        self.exposed = False

    def isExposed(self) -> bool:  # noqa: N802
        return self.exposed


class TestTheBandSurvivesAnX11Remap:
    """On X11 Qt writes _NET_WM_STATE_ABOVE back from the retained
    WindowStaysOnTopHint on every show() of a top-level window, so hiding a
    tucked keyboard from the tray and restoring it put it back on top while
    the setting said off.  The band is now re-asserted each time the window
    is exposed again, which Qt reports only once the window manager has
    mapped it, the point from which a _NET_WM_STATE client message counts."""

    @staticmethod
    def _filter(window: _ExposableWindow, after_map) -> wb.ReassertOnExposeFilter:
        return wb.ReassertOnExposeFilter(window, after_map=after_map, defer=lambda fn: fn())

    @staticmethod
    def _expose(flt: wb.ReassertOnExposeFilter, window: _ExposableWindow, exposed: bool) -> bool:
        window.exposed = exposed
        return flt.eventFilter(window, QEvent(QEvent.Type.Expose))

    def test_fires_once_per_mapping(self) -> None:
        window = _ExposableWindow()
        fired = MagicMock()
        flt = self._filter(window, fired)

        assert self._expose(flt, window, True) is False
        self._expose(flt, window, True)  # a repaint, not a remap
        assert fired.call_count == 1

        self._expose(flt, window, False)  # hidden
        assert fired.call_count == 1
        self._expose(flt, window, True)  # shown again
        assert fired.call_count == 2

    def test_ignores_other_events_and_other_objects(self) -> None:
        window = _ExposableWindow()
        window.exposed = True
        fired = MagicMock()
        flt = self._filter(window, fired)

        assert flt.eventFilter(window, QEvent(QEvent.Type.Show)) is False
        assert flt.eventFilter(QObject(), QEvent(QEvent.Type.Expose)) is False
        fired.assert_not_called()

    def test_a_failure_never_blocks_the_event(self) -> None:
        window = _ExposableWindow()
        flt = self._filter(window, MagicMock(side_effect=OSError))
        assert self._expose(flt, window, True) is False

    @pytest.mark.parametrize("setting", [False, True])
    def test_hide_and_restore_ends_in_the_saved_band(
        self, monkeypatch: pytest.MonkeyPatch, setting: bool
    ) -> None:
        window = _ExposableWindow()
        above = {window: setting}
        band = types.SimpleNamespace(
            set_keyboard_topmost=lambda w, on: above.__setitem__(w, on),
            raise_keyboard=lambda w: None,
        )
        monkeypatch.setattr(keyboard_app, "window_band", band)
        monkeypatch.setattr(keyboard_app.QApplication, "topLevelWindows", staticmethod(list))
        bridge = types.SimpleNamespace(alwaysOnTop=setting)
        flt = self._filter(window, lambda: keyboard_app._reapply_band(window, bridge, None))
        self._expose(flt, window, True)

        # The tray hides the tucked keyboard, then showNormal() remaps it,
        # and Qt's show() puts ABOVE back from the retained flag.
        self._expose(flt, window, False)
        above[window] = True
        self._expose(flt, window, True)

        assert above[window] is setting

    def test_main_installs_it_on_linux(self) -> None:
        from pathlib import Path

        source = Path(keyboard_app.__file__).read_text(encoding="utf-8")
        body = source.split("def main(", 1)[1]
        linux = body.split('if CURRENT_PLATFORM == "linux":', 1)[1].split(
            "def _on_always_on_top", 1
        )[0]
        assert "ReassertOnExposeFilter(" in linux
        assert "_reapply_band(root, bridge, shell_popup_yield)" in linux
        assert "root.installEventFilter(reassert_on_expose)" in linux
