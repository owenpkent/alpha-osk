"""Tests for app-level wiring in ``src/keyboard_app.py``.

Covers the one-time purge of diagnostic logs written before the
typed-content fix (those files could hold a transcript of what the user
typed, including a password typed while privacy mode was active, so
removing them on upgrade is part of that fix rather than cleanup), the
exception hooks that put a crash into that log in the first place, and
how the Windows window styles are applied.
"""

from __future__ import annotations

import logging
import sys
import threading
import types
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import src.keyboard_app as keyboard_app
from src.keyboard_app import (
    _LOG_PURGE_SENTINEL,
    _apply_windows_extended_styles,
    _configure_logging,
    _install_exception_hooks,
    _purge_pre_fix_logs,
)


def _seed_logs(config_dir: Path) -> None:
    """Write a main log plus the three rotations the handler can produce."""
    (config_dir / "alpha-osk.log").write_text("secret typed text", encoding="utf-8")
    for n in (1, 2, 3):
        (config_dir / f"alpha-osk.log.{n}").write_text(f"rotation {n}", encoding="utf-8")


class TestPurgePreFixLogs:
    def test_removes_the_log_and_every_rotation(self, tmp_path: Path) -> None:
        _seed_logs(tmp_path)

        removed = _purge_pre_fix_logs(tmp_path)

        assert removed == 4
        assert sorted(tmp_path.glob("alpha-osk.log*")) == []

    def test_writes_a_sentinel_so_it_runs_once(self, tmp_path: Path) -> None:
        _seed_logs(tmp_path)

        _purge_pre_fix_logs(tmp_path)

        assert (tmp_path / _LOG_PURGE_SENTINEL).is_file()

    def test_second_run_is_a_no_op(self, tmp_path: Path) -> None:
        """A user who wants logs kept across restarts must not fight us."""
        _seed_logs(tmp_path)
        _purge_pre_fix_logs(tmp_path)

        # Logs written by the *fixed* build are safe and must survive.
        (tmp_path / "alpha-osk.log").write_text("post-fix, no typed content", encoding="utf-8")

        removed = _purge_pre_fix_logs(tmp_path)

        assert removed == 0
        assert (tmp_path / "alpha-osk.log").exists()

    def test_no_logs_present_still_marks_done(self, tmp_path: Path) -> None:
        """A fresh install has nothing to purge but must not re-check forever."""
        removed = _purge_pre_fix_logs(tmp_path)

        assert removed == 0
        assert (tmp_path / _LOG_PURGE_SENTINEL).is_file()

    def test_leaves_unrelated_files_alone(self, tmp_path: Path) -> None:
        """The glob must not reach the model, snippets or analytics files."""
        _seed_logs(tmp_path)
        (tmp_path / "snippets.json").write_text("{}", encoding="utf-8")
        (tmp_path / "analytics.json").write_text("{}", encoding="utf-8")

        _purge_pre_fix_logs(tmp_path)

        assert (tmp_path / "snippets.json").exists()
        assert (tmp_path / "analytics.json").exists()

    def test_unreadable_config_dir_does_not_raise(self, tmp_path: Path) -> None:
        """Logging setup must never be the reason the keyboard fails to start."""
        missing = tmp_path / "does-not-exist"

        removed = _purge_pre_fix_logs(missing)

        assert removed == 0


class TestTheLogGoesWhereTheUIPointsUsers:
    """*Settings -> Data & Privacy -> Diagnostics* shows a path.

    It comes from ``platform.get_log_path()``, while the handler is
    opened by ``_configure_logging``.  A panel pointing at a file
    nothing writes is worse than no panel, so this pins the two
    together rather than asserting either one on its own.
    """

    @pytest.fixture
    def _restore_root_logging(self) -> Iterator[None]:
        """``_configure_logging`` replaces the root handlers process-wide.

        The suite shards with xdist, so leaving them swapped would break
        whatever ran next in this worker (caplog included).  The handler
        it opened is also closed here, or Windows will not let tmp_path
        be cleaned up afterwards.
        """
        root = logging.getLogger()
        original = list(root.handlers)
        level = root.level
        try:
            yield
        finally:
            for handler in list(root.handlers):
                root.removeHandler(handler)
                if handler not in original:
                    handler.close()
            for handler in original:
                root.addHandler(handler)
            root.setLevel(level)

    def test_the_handler_writes_the_file_the_panel_names(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _restore_root_logging: None,
    ) -> None:
        import src.platform as platform_mod

        monkeypatch.setattr(platform_mod, "get_config_dir", lambda: tmp_path)
        monkeypatch.setattr(keyboard_app, "get_config_dir", lambda: tmp_path)

        opened = _configure_logging()
        logging.getLogger("KeyboardApp").error("a record")

        assert opened == platform_mod.get_log_path()
        assert opened is not None
        assert "a record" in opened.read_text(encoding="utf-8")


def _boom() -> tuple:
    """Return a real (type, value, traceback) triple for a raised error."""
    try:
        raise ValueError("the-canary-blew-up")
    except ValueError:
        return sys.exc_info()


class TestUncaughtTracebacksReachTheLog:
    """The log exists so a crash in a frozen build leaves a trace.

    Only failures somebody wrapped in ``try`` / ``except`` +
    ``_logger.exception`` ever reached it before: everything else went to
    ``sys.excepthook``, which writes to stderr, and a windowed
    PyInstaller build has no stderr at all (``sys.stderr`` is ``None``).
    The traceback was discarded at the moment it was worth the most, in
    the file users are asked to attach to a bug report.
    """

    @pytest.fixture
    def hooks(self) -> Iterator[MagicMock]:
        """Install the hooks over a recorder, and put the world back.

        The recorder stands in for the interpreter's real hook so the
        chaining assertion has something to check *and* so a deliberately
        raised traceback is not printed across the test output.  The
        install flag is reset both ways: these are process-global, and
        the suite shards with xdist.
        """
        previous_sys = sys.excepthook
        previous_thread = threading.excepthook
        previous_flag = keyboard_app._exception_hooks_installed

        recorder = MagicMock()
        sys.excepthook = recorder
        threading.excepthook = recorder
        keyboard_app._exception_hooks_installed = False
        _install_exception_hooks()
        try:
            yield recorder
        finally:
            sys.excepthook = previous_sys
            threading.excepthook = previous_thread
            keyboard_app._exception_hooks_installed = previous_flag

    def test_an_uncaught_traceback_is_logged(
        self, hooks: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        exc_type, exc, tb = _boom()

        with caplog.at_level(logging.CRITICAL):
            sys.excepthook(exc_type, exc, tb)

        assert "Uncaught exception" in caplog.text
        # The traceback itself, not just the message: a one-line record
        # would name the error without saying where it came from.
        assert "the-canary-blew-up" in caplog.text
        assert "ValueError" in caplog.text
        assert "_boom" in caplog.text

    def test_the_previous_hook_still_runs(self, hooks: MagicMock) -> None:
        """Chaining, not replacing: a dev run keeps its stderr traceback."""
        exc_type, exc, tb = _boom()

        sys.excepthook(exc_type, exc, tb)

        hooks.assert_called_once_with(exc_type, exc, tb)

    def test_ctrl_c_is_not_logged_as_a_crash(
        self, hooks: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Ctrl-C is a user action.  It still chains, it just isn't a fault."""
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            exc_type, exc, tb = sys.exc_info()

        with caplog.at_level(logging.CRITICAL):
            sys.excepthook(exc_type, exc, tb)

        assert caplog.text == ""
        hooks.assert_called_once()

    def test_a_thread_crash_reaches_the_log(
        self, hooks: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``sys.excepthook`` never sees a worker thread's exception.

        The dictation capture, the updater's download worker and Linux's
        AT-SPI listener all run off the main thread, so without the
        threading hook a crash in any of them is silent.
        """
        exc_type, exc, tb = _boom()
        args = threading.ExceptHookArgs((exc_type, exc, tb, threading.current_thread()))

        with caplog.at_level(logging.CRITICAL):
            threading.excepthook(args)

        assert "the-canary-blew-up" in caplog.text
        assert threading.current_thread().name in caplog.text
        hooks.assert_called_once_with(args)

    def test_installing_twice_does_not_double_log(
        self, hooks: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Re-entering main() in one process must not chain hook onto hook."""
        _install_exception_hooks()
        exc_type, exc, tb = _boom()

        with caplog.at_level(logging.CRITICAL):
            sys.excepthook(exc_type, exc, tb)

        assert caplog.text.count("Uncaught exception") == 1
        hooks.assert_called_once()


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
        return user32

    @staticmethod
    def _root() -> MagicMock:
        root = MagicMock()
        root.winId.return_value = 0x1234
        return root

    def test_the_window_is_put_in_the_topmost_band(self, win32) -> None:
        _apply_windows_extended_styles(self._root(), taskbar_button=True)

        win32.SetWindowPos.assert_called_once()
        args = win32.SetWindowPos.call_args[0]
        assert args[1] == self.HWND_TOPMOST, (
            f"hWndInsertAfter was {args[1]}, not HWND_TOPMOST; the window keeps "
            "whatever Z-order it already had"
        )

    def test_the_call_does_not_decline_to_reorder(self, win32) -> None:
        """SWP_NOZORDER asks the system to leave the Z-order alone, which
        is the opposite of the point and is what the old code passed."""
        _apply_windows_extended_styles(self._root(), taskbar_button=True)

        flags = win32.SetWindowPos.call_args[0][6]
        assert not flags & self.SWP_NOZORDER, "SWP_NOZORDER cancels the whole call"
        assert flags & self.SWP_FRAMECHANGED, (
            "SWP_FRAMECHANGED is what makes the system re-read WS_EX_NOACTIVATE"
        )

    def test_noactivate_still_goes_through_the_style_word(self, win32) -> None:
        """The inverse: this one *is* settable that way, and must stay,
        or every key click steals focus from the app being typed into."""
        _apply_windows_extended_styles(self._root(), taskbar_button=True)

        style = win32.SetWindowLongW.call_args_list[0][0][2]
        assert style & self.WS_EX_NOACTIVATE

    def test_topmost_is_not_written_into_the_style_word(self, win32) -> None:
        """Writing it there is what knocked the window out of the band:
        Qt's WindowStaysOnTopHint had already put it in."""
        _apply_windows_extended_styles(self._root(), taskbar_button=True)

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
        return user32

    @staticmethod
    def _root() -> MagicMock:
        root = MagicMock()
        root.winId.return_value = 0x1234
        return root

    def test_toolwindow_is_cleared(self, win32) -> None:
        _apply_windows_extended_styles(self._root(), taskbar_button=True)

        style = win32.SetWindowLongW.call_args_list[0][0][2]
        assert not style & self.WS_EX_TOOLWINDOW, (
            "WS_EX_TOOLWINDOW survived, so the keyboard has no taskbar button "
            "and the minimise button has nowhere to put it"
        )

    def test_appwindow_is_asserted(self, win32) -> None:
        """Belt and braces: clearing TOOLWINDOW asks Qt not to have opted
        us out, setting APPWINDOW says we want in regardless."""
        _apply_windows_extended_styles(self._root(), taskbar_button=True)

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
        _apply_windows_extended_styles(self._root(), taskbar_button=True)

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
        _apply_windows_extended_styles(self._root())

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
        _apply_windows_extended_styles(self._root(), taskbar_button=True)

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
        _apply_windows_extended_styles(self._root(), taskbar_button=True)

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
