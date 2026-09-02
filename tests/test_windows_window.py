"""Tests for ``src/platform/windows_window.py``.

Covers the Win32 window-styling functions that used to live inline in
``src/keyboard_app.py`` (see the module docstring there for why they moved):
the always-on-top Z-order change, the taskbar-button dance, and the
per-function platform guard that lets this module carry no mypy exemption.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from src.platform.windows_window import (
    apply_extended_styles,
    set_app_user_model_id,
    surface_existing_instance,
)


class TestAlwaysOnTopIsAppliedAsAZOrderChange:
    """Reported as "always on top isn't working", and the cause was that
    the code asked for it the one way that does not work.

    ``WS_EX_TOPMOST`` and the topmost Z-order *band* are separate pieces
    of state. MSDN says the style is added and removed with
    ``SetWindowPos``; writing the bit into the style word with
    ``SetWindowLongW`` sets the first and leaves the second alone, which
    produces a window that reports itself as topmost and is not. A
    Z-order walk on the live keyboard found it **fifteenth**, below a
    dozen ordinary windows, with the bit reading true throughout, while
    Windows' own on-screen keyboard sat first.

    These assert the two halves of the call that actually moves it, so a
    future "tidy the flags into one SetWindowLong" reads as the change it
    would be. The Win32 layer is faked because this code is Windows-only
    and the suite runs on Linux too.
    """

    SWP_NOZORDER = 0x0004
    SWP_FRAMECHANGED = 0x0020
    HWND_TOPMOST = -1
    WS_EX_TOPMOST = 0x00000008
    WS_EX_NOACTIVATE = 0x08000000

    @pytest.fixture
    def win32(self, monkeypatch: pytest.MonkeyPatch):
        """A stand-in ctypes.windll that records what it was asked to do."""
        user32 = MagicMock()
        user32.GetWindowLongW.return_value = 0x00000100  # some pre-existing style
        user32.SetWindowLongW.return_value = 0x00000100
        user32.SetWindowPos.return_value = 1
        kernel32 = MagicMock()
        kernel32.GetLastError.return_value = 0

        import ctypes

        monkeypatch.setattr(
            ctypes, "windll", types.SimpleNamespace(user32=user32, kernel32=kernel32), raising=False
        )
        # The real function returns early off Windows so mypy can prune the
        # ctypes body under --platform linux.  The suite runs on Linux too,
        # and the fake windll above is what the body needs, so tell it it is
        # on Windows for the duration of the test.
        monkeypatch.setattr(sys, "platform", "win32")
        return user32

    @staticmethod
    def _root() -> MagicMock:
        root = MagicMock()
        root.winId.return_value = 0x1234
        return root

    def test_the_window_is_put_in_the_topmost_band(self, win32) -> None:
        apply_extended_styles(self._root(), taskbar_button=True)

        win32.SetWindowPos.assert_called_once()
        args = win32.SetWindowPos.call_args[0]
        assert args[1] == self.HWND_TOPMOST, (
            f"hWndInsertAfter was {args[1]}, not HWND_TOPMOST; the window keeps "
            "whatever Z-order it already had"
        )

    def test_the_call_does_not_decline_to_reorder(self, win32) -> None:
        """SWP_NOZORDER asks the system to leave the Z-order alone, which
        is the opposite of the point and is what the old code passed."""
        apply_extended_styles(self._root(), taskbar_button=True)

        flags = win32.SetWindowPos.call_args[0][6]
        assert not flags & self.SWP_NOZORDER, "SWP_NOZORDER cancels the whole call"
        assert flags & self.SWP_FRAMECHANGED, (
            "SWP_FRAMECHANGED is what makes the system re-read WS_EX_NOACTIVATE"
        )

    def test_noactivate_still_goes_through_the_style_word(self, win32) -> None:
        """The inverse: this one *is* settable that way, and must stay,
        or every key click steals focus from the app being typed into."""
        apply_extended_styles(self._root(), taskbar_button=True)

        style = win32.SetWindowLongW.call_args_list[0][0][2]
        assert style & self.WS_EX_NOACTIVATE

    def test_topmost_is_not_written_into_the_style_word(self, win32) -> None:
        """Writing it there is what knocked the window out of the band:
        Qt's WindowStaysOnTopHint had already put it in."""
        apply_extended_styles(self._root(), taskbar_button=True)

        style = win32.SetWindowLongW.call_args_list[0][0][2]
        assert not style & self.WS_EX_TOPMOST


class TestTheKeyboardKeepsItsTaskbarButton:
    """Reported as the keyboard having no taskbar entry, so the minimise
    button had nowhere to go and clicking the pinned icon did nothing.

    Qt adds ``WS_EX_TOOLWINDOW`` on its own. QML declares ``visible:
    true``, so the window is already shown when ``_apply_window_flags``
    calls ``setFlags``, and applying a non-activating, frameless,
    always-on-top flag set to an *already shown* window is the case where
    Qt decides it does not belong in the taskbar. Applying the same flags
    before the first show does not, which is why this went unseen: the
    comments in that module claimed the style "was removed" for a long
    time while every shipped window carried it.

    Measured rather than reasoned about. A window shown and then given
    those flags came out ``0x08000088``, matching the live keyboard's
    ``0x08080088`` bit for bit apart from the LAYERED bit that window
    opacity adds.
    """

    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_APPWINDOW = 0x00040000
    WS_EX_LAYERED = 0x00080000
    WS_EX_TOPMOST = 0x00000008

    @pytest.fixture
    def win32(self, monkeypatch: pytest.MonkeyPatch):
        user32 = MagicMock()
        # Qt has already added TOOLWINDOW by the time this runs, which is
        # the whole point: the fix has to *clear* it, not just not add it.
        # Seeded with the live keyboard's own value (see the class
        # docstring, 0x08080088) rather than the bare pre-fix measurement,
        # so LAYERED (what window opacity rides on) and Qt's own TOPMOST
        # bit are both present for the style-word-survives test below to
        # assert against.
        user32.GetWindowLongW.return_value = 0x08080088

        # A shared list recording which of SetWindowLongW / SetWindowPos
        # ran first, in call order, for the frame-flush-ordering test
        # below.  call_args_list already gives each mock's own calls in
        # order; this is what lets a test compare order *across* the two.
        user32.call_order = []

        def _set_window_long(hwnd, index, value):
            user32.call_order.append(("SetWindowLongW", index))
            return 0x08080088

        def _set_window_pos(*args, **kwargs):
            user32.call_order.append(("SetWindowPos", None))
            return 1

        user32.SetWindowLongW.side_effect = _set_window_long
        user32.SetWindowPos.side_effect = _set_window_pos
        kernel32 = MagicMock()
        kernel32.GetLastError.return_value = 0

        import ctypes

        monkeypatch.setattr(
            ctypes, "windll", types.SimpleNamespace(user32=user32, kernel32=kernel32), raising=False
        )
        # The real function returns early off Windows so mypy can prune the
        # ctypes body under --platform linux.  The suite runs on Linux too,
        # and the fake windll above is what the body needs, so tell it it is
        # on Windows for the duration of the test.
        monkeypatch.setattr(sys, "platform", "win32")
        return user32

    @staticmethod
    def _root() -> MagicMock:
        root = MagicMock()
        root.winId.return_value = 0x1234
        return root

    def test_toolwindow_is_cleared(self, win32) -> None:
        apply_extended_styles(self._root(), taskbar_button=True)

        style = win32.SetWindowLongW.call_args_list[0][0][2]
        assert not style & self.WS_EX_TOOLWINDOW, (
            "WS_EX_TOOLWINDOW survived, so the keyboard has no taskbar button "
            "and the minimise button has nowhere to put it"
        )

    def test_appwindow_is_asserted(self, win32) -> None:
        """Belt and braces: clearing TOOLWINDOW asks Qt not to have opted
        us out, setting APPWINDOW says we want in regardless."""
        apply_extended_styles(self._root(), taskbar_button=True)

        style = win32.SetWindowLongW.call_args_list[0][0][2]
        assert style & self.WS_EX_APPWINDOW

    def test_the_taskbar_button_can_minimise(self, win32) -> None:
        """A button can *restore* a window without this, which is why
        minimising and clicking the button both worked while a second
        click did nothing: the shell decides whether a button may minimise
        from WS_MINIMIZEBOX / WS_SYSMENU in the ordinary style word, and
        this window is a bare WS_POPUP.

        Windows' own on-screen keyboard is the proof it composes with
        never taking focus: osk.exe runs the identical extended style and
        carries both of these.
        """
        apply_extended_styles(self._root(), taskbar_button=True)

        writes = win32.SetWindowLongW.call_args_list
        assert len(writes) == 2, "expected an extended-style write and a style write"
        gwl_style, style = writes[1][0][1], writes[1][0][2]
        assert gwl_style == -16, "the second write must target GWL_STYLE"
        assert style & 0x00020000, "WS_MINIMIZEBOX missing"
        assert style & 0x00080000, "WS_SYSMENU missing"

    def test_a_subordinate_window_stays_out_of_the_taskbar(self, win32) -> None:
        """The snippets window shares this function, and a floating palette
        with its own taskbar button is clutter. It asks for focus
        suppression only, so nothing here may touch its taskbar presence or
        give it a minimise box."""
        apply_extended_styles(self._root())

        writes = win32.SetWindowLongW.call_args_list
        assert len(writes) == 1, "a style write happened for a non-taskbar window"
        style = writes[0][0][2]
        assert style & 0x08000000, "NOACTIVATE is the one thing it does want"
        assert not style & 0x00040000, "APPWINDOW was forced on"

    def test_the_rest_of_the_style_word_survives(self, win32) -> None:
        """The inverse: this must clear one bit, not rewrite the word.

        Qt puts real state in there, LAYERED among it (what window
        opacity rides on) and its own TOPMOST bit, and this function has
        no business touching either. A `new_style = WS_EX_NOACTIVATE`
        that dropped the `current |` would still set NOACTIVATE and would
        still pass the other tests in this class, since they only check
        which bits are set or cleared on the *new* ones; only reading back
        a pre-existing bit that the write never touches proves the rest
        of the word survived rather than being rebuilt from scratch.
        """
        apply_extended_styles(self._root(), taskbar_button=True)

        style = win32.SetWindowLongW.call_args_list[0][0][2]
        assert style & 0x08000000, "NOACTIVATE was dropped"
        assert style & self.WS_EX_LAYERED, (
            "WS_EX_LAYERED was dropped: the write rebuilt the style word "
            "instead of OR-ing onto it, and window opacity would break"
        )
        assert style & self.WS_EX_TOPMOST, (
            "the extended style's own TOPMOST bit was dropped: this "
            "function must not rewrite bits it doesn't own"
        )

    def test_the_style_write_is_flushed_by_the_frame_changed_call(self, win32) -> None:
        """MSDN: after a frame-style change via SetWindowLong, the cached
        frame data isn't updated until SetWindowPos(SWP_FRAMECHANGED)
        runs. WS_MINIMIZEBOX / WS_SYSMENU are frame styles like any other,
        so the GWL_STYLE write has to land before the one
        SWP_FRAMECHANGED call in this function, not after it.
        """
        apply_extended_styles(self._root(), taskbar_button=True)

        order = win32.call_order
        style_writes = [
            i for i, (call, index) in enumerate(order) if call == "SetWindowLongW" and index == -16
        ]
        frame_changed_calls = [
            i for i, (call, _index) in enumerate(order) if call == "SetWindowPos"
        ]
        assert style_writes, "GWL_STYLE was never written"
        assert frame_changed_calls, "SetWindowPos was never called"
        assert style_writes[0] < frame_changed_calls[0], (
            "GWL_STYLE was written after SetWindowPos(SWP_FRAMECHANGED), so the "
            "MINIMIZEBOX / SYSMENU bits just written are not guaranteed to have "
            "been flushed into the cached frame data"
        )


class TestTheModuleIsASafeNoOpOffWindows:
    """Every public function here is guarded by a literal ``if sys.platform
    != "win32": return`` (see the module docstring for why: it is what lets
    mypy prune the ctypes body under ``--platform linux`` and check it for
    real under ``--platform win32``, so the module carries no blanket
    exemption). This pins the runtime half of that: called off Windows, each
    function returns immediately and touches no ctypes API at all.
    """

    def test_apply_extended_styles_is_a_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        root = MagicMock()

        apply_extended_styles(root, taskbar_button=True)

        root.winId.assert_not_called()

    def test_surface_existing_instance_is_a_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")

        # Absence of an exception is the assertion: on a real Windows host
        # this reaches EnumWindows via ctypes.windll, which does not exist
        # off Windows at all.
        surface_existing_instance("Alpha-OSK")

    def test_set_app_user_model_id_is_a_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")

        set_app_user_model_id("OKStudio.AlphaOSK")


def test_module_imports_cleanly() -> None:
    """A bare import must never touch ctypes.windll -- every Win32 call is
    inside a function body, guarded by the platform check above, so the
    module has to be importable on every OS with no side effects."""
    import src.platform.windows_window  # noqa: F401
