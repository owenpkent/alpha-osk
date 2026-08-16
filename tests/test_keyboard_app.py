"""Tests for app-level wiring in ``src/keyboard_app.py``.

Covers the one-time purge of diagnostic logs written before the
typed-content fix (those files could hold a transcript of what the user
typed, including a password typed while privacy mode was active, so
removing them on upgrade is part of that fix rather than cleanup), and
how the Windows window styles are applied.
"""

from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.keyboard_app import (
    _LOG_PURGE_SENTINEL,
    _apply_windows_extended_styles,
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
        _apply_windows_extended_styles(self._root())

        win32.SetWindowPos.assert_called_once()
        args = win32.SetWindowPos.call_args[0]
        assert args[1] == self.HWND_TOPMOST, (
            f"hWndInsertAfter was {args[1]}, not HWND_TOPMOST; the window keeps "
            "whatever Z-order it already had"
        )

    def test_the_call_does_not_decline_to_reorder(self, win32) -> None:
        """SWP_NOZORDER asks the system to leave the Z-order alone, which
        is the opposite of the point and is what the old code passed."""
        _apply_windows_extended_styles(self._root())

        flags = win32.SetWindowPos.call_args[0][6]
        assert not flags & self.SWP_NOZORDER, "SWP_NOZORDER cancels the whole call"
        assert flags & self.SWP_FRAMECHANGED, (
            "SWP_FRAMECHANGED is what makes the system re-read WS_EX_NOACTIVATE"
        )

    def test_noactivate_still_goes_through_the_style_word(self, win32) -> None:
        """The inverse: this one *is* settable that way, and must stay,
        or every key click steals focus from the app being typed into."""
        _apply_windows_extended_styles(self._root())

        style = win32.SetWindowLongW.call_args[0][2]
        assert style & self.WS_EX_NOACTIVATE

    def test_topmost_is_not_written_into_the_style_word(self, win32) -> None:
        """Writing it there is what knocked the window out of the band:
        Qt's WindowStaysOnTopHint had already put it in."""
        _apply_windows_extended_styles(self._root())

        style = win32.SetWindowLongW.call_args[0][2]
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

    @pytest.fixture
    def win32(self, monkeypatch: pytest.MonkeyPatch):
        user32 = MagicMock()
        # Qt has already added TOOLWINDOW by the time this runs, which is
        # the whole point: the fix has to *clear* it, not just not add it.
        user32.GetWindowLongW.return_value = 0x08000088
        user32.SetWindowLongW.return_value = 0x08000088
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

    def test_toolwindow_is_cleared(self, win32) -> None:
        _apply_windows_extended_styles(self._root())

        style = win32.SetWindowLongW.call_args[0][2]
        assert not style & self.WS_EX_TOOLWINDOW, (
            "WS_EX_TOOLWINDOW survived, so the keyboard has no taskbar button "
            "and the minimise button has nowhere to put it"
        )

    def test_appwindow_is_asserted(self, win32) -> None:
        """Belt and braces: clearing TOOLWINDOW asks Qt not to have opted
        us out, setting APPWINDOW says we want in regardless."""
        _apply_windows_extended_styles(self._root())

        style = win32.SetWindowLongW.call_args[0][2]
        assert style & self.WS_EX_APPWINDOW

    def test_the_rest_of_the_style_word_survives(self, win32) -> None:
        """The inverse: this must clear one bit, not rewrite the word.

        Qt puts real state in there, LAYERED among it, which is what
        window opacity rides on.
        """
        _apply_windows_extended_styles(self._root())

        style = win32.SetWindowLongW.call_args[0][2]
        assert style & 0x08000000, "NOACTIVATE was dropped"
