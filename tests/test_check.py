"""Tests for the pre-push gate script (check.py).

Only the parts with a decision in them.  The step list and the summary
printing are proven by the script running at all; what is worth pinning
is the Windows console handling, because it is invisible in the case it
exists for.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

import check


class TestChildProcessConsoleWindows:
    """The gate must not pop blank console windows when run from a hook.

    Every step launches a console-subsystem binary without capturing its
    output, so the child inherits the parent's console and streams to the
    terminal.  When there is no console to inherit -- Git for Windows
    runs hooks through ``sh.exe``, and a push started from a GUI has no
    console anywhere in the chain -- Windows allocates a fresh one per
    child, which is two blank windows titled with the repo path for the
    two ruff steps alone.

    The failure is invisible from a terminal, which is the only place
    anyone runs this by hand, so it needs a test rather than a look.
    """

    def test_no_flag_when_a_console_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The ordinary terminal path must stay byte-identical.

        Nothing is gained by setting the flag there: a child inheriting a
        console opens no window of its own.
        """
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(check, "_get_console_window", lambda: 12345)
        assert check._child_creationflags() == 0

    def test_a_tty_alone_is_enough_to_stay_out_of_the_way(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Running it by hand must never take this branch.

        `GetConsoleWindow` is the direct question and an unreliable
        answer: it reports 0 for a process on a pseudo-console and for
        one with its output on a pipe, both of which happen in ordinary
        terminals. Measured returning 0 under a plain `python.exe`, which
        is exactly the case that must not change. The tty check is what
        makes the hand-run path unreachable regardless.
        """

        class _Tty:
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "stdout", _Tty())
        monkeypatch.setattr(check, "_get_console_window", lambda: 0)
        assert check._child_creationflags() == 0

    @pytest.mark.skipif(
        not hasattr(subprocess, "CREATE_NO_WINDOW"),
        reason="CREATE_NO_WINDOW is a Windows-only constant, so there is "
        "nothing but 0 to assert against elsewhere",
    )
    def test_flag_set_when_there_is_no_console(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # stdout is stubbed explicitly, like its two siblings.  Left
        # unstubbed this passed only because pytest had replaced
        # sys.stdout with a non-tty capture object: under `pytest -s`
        # from a Windows terminal the real stdout is a tty,
        # `_child_creationflags` short-circuits to 0, and the assertion
        # failed inside a test that was never about ttys.
        class _NotATty:
            def isatty(self) -> bool:
                return False

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "stdout", _NotATty())
        monkeypatch.setattr(check, "_get_console_window", lambda: 0)
        assert check._child_creationflags() == subprocess.CREATE_NO_WINDOW

    def test_non_windows_is_always_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """CREATE_NO_WINDOW does not exist off Windows; 0 is the no-op."""
        monkeypatch.setattr(sys, "platform", "linux")
        assert check._child_creationflags() == 0

    def test_an_unavailable_probe_does_not_break_the_gate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Failing open costs a blank window; failing closed costs the gate."""

        def boom() -> int:
            raise OSError("no kernel32 here")

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(check, "_get_console_window", boom)
        assert check._child_creationflags() == 0

    def test_the_flag_reaches_the_subprocess(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The probe is worthless if `run` drops what it returns."""
        seen: dict = {}

        class _Result:
            returncode = 0

        def fake_run(cmd, **kwargs):
            seen.update(kwargs)
            return _Result()

        monkeypatch.setattr(check.subprocess, "run", fake_run)
        ok, _ = check.run("ruff", [sys.executable, "--version"], creationflags=0x08000000)
        assert ok is True
        assert seen["creationflags"] == 0x08000000
