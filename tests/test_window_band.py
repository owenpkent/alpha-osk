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

    def test_reapply_raises_only_when_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        topmost = MagicMock()
        raised = MagicMock()
        monkeypatch.setattr(wb, "set_keyboard_topmost", topmost)
        monkeypatch.setattr(wb, "raise_keyboard", raised)

        wb.reapply_keyboard_band(_root(), True)
        topmost.assert_called_with(topmost.call_args[0][0], True)
        raised.assert_not_called()

        wb.reapply_keyboard_band(_root(), False)
        assert topmost.call_args[0][1] is False
        raised.assert_called_once()


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
        root = MagicMock()

        keyboard_app._apply_always_on_top(root, False, types.SimpleNamespace(stepped_aside=True))

        band.set_keyboard_topmost.assert_called_once_with(root, False)
        band.raise_keyboard.assert_called_once_with(root)


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
