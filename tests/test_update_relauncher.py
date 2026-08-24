"""Tests for the post-update relauncher helper."""

from __future__ import annotations

import argparse
import json
import os
import time
from unittest.mock import patch

import pytest

from src import _update_relauncher as relauncher


class TestProcessAlive:
    """_process_alive — cross-platform PID check."""

    def test_zero_pid_returns_false(self):
        assert relauncher._process_alive(0) is False

    def test_negative_pid_returns_false(self):
        assert relauncher._process_alive(-1) is False

    def test_current_pid_returns_true(self):
        # Our own process is definitely alive.
        import os

        assert relauncher._process_alive(os.getpid()) is True

    def test_nonexistent_pid_returns_false(self):
        # PID 999_999_999 is virtually guaranteed to be unused on every
        # supported platform.
        assert relauncher._process_alive(999_999_999) is False


class TestWaitForParentExit:
    """_wait_for_parent_exit — polls until process is gone or timeout."""

    def test_immediate_return_when_already_dead(self):
        with patch.object(relauncher, "_process_alive", return_value=False):
            start = time.monotonic()
            ok = relauncher._wait_for_parent_exit(12345, timeout_s=5.0)
            elapsed = time.monotonic() - start
        assert ok is True
        # Should be near-instant.
        assert elapsed < 0.5

    def test_timeout_when_parent_never_exits(self):
        with patch.object(relauncher, "_process_alive", return_value=True):
            ok = relauncher._wait_for_parent_exit(12345, timeout_s=0.6)
        assert ok is False


class TestWaitForNewExe:
    """_wait_for_new_exe — confirms the install actually wrote a new exe."""

    def test_returns_true_when_file_appears_with_fresh_mtime(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        # Write the file with an mtime *after* a fixed reference.
        target.write_bytes(b"binary contents")
        ref = time.time() - 60  # parent died a minute ago
        ok = relauncher._wait_for_new_exe(target, ref, timeout_s=2.0)
        assert ok is True

    def test_rejects_stale_mtime(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        target.write_bytes(b"binary contents")
        # Pretend the parent died well after the file was written —
        # that means the file is the OLD exe.
        ref = time.time() + 60
        ok = relauncher._wait_for_new_exe(target, ref, timeout_s=0.6)
        assert ok is False

    def test_returns_true_when_file_exists_and_no_reference(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        target.write_bytes(b"x")
        ok = relauncher._wait_for_new_exe(target, None, timeout_s=2.0)
        assert ok is True

    def test_zero_byte_file_is_rejected(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        target.touch()
        ok = relauncher._wait_for_new_exe(target, None, timeout_s=0.6)
        assert ok is False

    def test_timeout_when_file_never_appears(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        ok = relauncher._wait_for_new_exe(target, None, timeout_s=0.6)
        assert ok is False


class TestWriteHandoff:
    """_write_handoff — drops a JSON breadcrumb for the new OSK to read."""

    def test_writes_expected_fields(self, tmp_path):
        relauncher._write_handoff(tmp_path, "1.0.16", "1.0.15")
        path = tmp_path / "update_handoff.json"
        assert path.is_file()
        data = json.loads(path.read_text())
        assert data["version"] == "1.0.16"
        assert data["previous_version"] == "1.0.15"
        assert isinstance(data["completed_at"], (int, float))
        # Completed-at must be a recent timestamp.
        assert abs(data["completed_at"] - time.time()) < 5

    def test_creates_config_dir_if_missing(self, tmp_path):
        nested = tmp_path / "nested" / "config"
        relauncher._write_handoff(nested, "1.0.16", "1.0.15")
        assert (nested / "update_handoff.json").is_file()


class TestRunRelauncherIntegration:
    """End-to-end-ish: simulate the full flow with mocked subprocess + PID."""

    def test_happy_path(self, tmp_path):
        # Stage a "freshly installed" exe with an mtime in the future
        # so the relauncher's "newer than parent_death_time" check
        # passes deterministically. In production the installer's file
        # write happens after the parent's death, so mtime > death by
        # whatever the install took (seconds at minimum).
        import os as _os

        target_exe = tmp_path / "alpha-osk.exe"
        target_exe.write_bytes(b"freshly installed")
        future_mtime = time.time() + 3600
        _os.utime(target_exe, (future_mtime, future_mtime))
        config_dir = tmp_path / "config"

        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            "999999999",  # already dead
            "--new-version",
            "1.0.16",
            "--previous-version",
            "1.0.15",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(config_dir),
        ]

        # Bypass the 5-second installer-grace sleep so the test is fast.
        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
        ):
            with patch.object(relauncher, "_launch_new_osk", return_value=True) as mock_launch:
                rc = relauncher.run_relauncher(argv)

        assert rc == 0
        mock_launch.assert_called_once()
        # Handoff was written.
        handoff = config_dir / "update_handoff.json"
        assert handoff.is_file()
        data = json.loads(handoff.read_text())
        assert data["version"] == "1.0.16"
        assert data["previous_version"] == "1.0.15"

    def test_returns_error_when_parent_never_dies(self, tmp_path):
        target_exe = tmp_path / "alpha-osk.exe"
        target_exe.write_bytes(b"x")
        config_dir = tmp_path / "config"

        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            "12345",
            "--new-version",
            "1.0.16",
            "--previous-version",
            "1.0.15",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(config_dir),
        ]

        with (
            patch.object(relauncher, "_PARENT_EXIT_TIMEOUT_S", 0.5),
            patch.object(relauncher, "_process_alive", return_value=True),
        ):
            rc = relauncher.run_relauncher(argv)

        assert rc == 2  # parent-exit timeout

    def test_returns_error_when_new_exe_never_appears(self, tmp_path):
        # Path doesn't exist — install "fails" to write.
        target_exe = tmp_path / "alpha-osk.exe"
        config_dir = tmp_path / "config"

        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            "999999999",
            "--new-version",
            "1.0.16",
            "--previous-version",
            "1.0.15",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(config_dir),
        ]

        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 0.5),
        ):
            rc = relauncher.run_relauncher(argv)

        assert rc == 3  # new-exe timeout

    def test_returns_error_when_launch_fails(self, tmp_path):
        import os as _os

        target_exe = tmp_path / "alpha-osk.exe"
        target_exe.write_bytes(b"x")
        future_mtime = time.time() + 3600
        _os.utime(target_exe, (future_mtime, future_mtime))
        config_dir = tmp_path / "config"

        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            "999999999",
            "--new-version",
            "1.0.16",
            "--previous-version",
            "1.0.15",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(config_dir),
        ]

        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
            patch.object(relauncher, "_launch_new_osk", return_value=False),
        ):
            rc = relauncher.run_relauncher(argv)

        assert rc == 4  # launch failed
        # Handoff should NOT be written if launch failed — would be
        # misleading on the next manual launch.
        assert not (config_dir / "update_handoff.json").is_file()


class TestTheRunIsBounded:
    """Nothing here may outlive its usefulness unnoticed.

    This process is detached and has no console, so an unbounded wait is
    not a hang anyone sees: it is a process sitting in a task list that
    nobody opens.  TODO.md used to claim the cause was a missing exit
    path for an already-dead parent.  That was wrong, and measurably so
    (``_wait_for_parent_exit`` returns in 0.00 s for a dead pid, and the
    phases have always summed to a bounded 245 s).  The real waste was
    narrower: a dev-mode target waits the full new-exe timeout for an
    mtime that cannot advance.
    """

    def _argv(self, target_exe, config_dir, parent_pid="999999999"):
        return [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            parent_pid,
            "--new-version",
            "1.0.18",
            "--previous-version",
            "1.0.17",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(config_dir),
        ]

    def test_a_dev_target_does_not_wait_for_an_installer(self, tmp_path):
        """The 185 seconds, measured against the real timeouts.

        Deliberately does *not* patch the timeouts down: patching them
        would make this pass just as happily against the version that
        waits, which is the whole thing being tested.
        """
        target_exe = tmp_path / "python.exe"
        target_exe.write_bytes(b"x")
        started = time.monotonic()
        rc = relauncher.run_relauncher(self._argv(target_exe, tmp_path / "config"))
        elapsed = time.monotonic() - started
        assert rc == 0
        assert elapsed < 2.0, f"dev target still waited {elapsed:.1f}s"

    def test_a_dev_target_launches_nothing(self, tmp_path):
        """`python.exe` with no arguments is not the keyboard.

        Launching it would swap one stranded process for another, so the
        dev-mode path returns without launching at all.
        """
        target_exe = tmp_path / "python.exe"
        target_exe.write_bytes(b"x")
        launched: list[object] = []
        with patch.object(relauncher, "_launch_new_osk", lambda t: launched.append(t) or True):
            relauncher.run_relauncher(self._argv(target_exe, tmp_path / "config"))
        assert launched == []

    def test_a_real_target_still_waits_and_launches(self, tmp_path):
        """The inverse, so "always exit early" cannot pass as the fix.

        A real install is `alpha-osk.exe`, which `_is_dev_target` cannot
        match, and it must still go through the whole flow.
        """
        target_exe = tmp_path / "alpha-osk.exe"
        target_exe.write_bytes(b"x")
        future = time.time() + 3600
        os.utime(target_exe, (future, future))
        launched: list[object] = []
        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
            patch.object(relauncher, "_launch_new_osk", lambda t: launched.append(t) or True),
        ):
            rc = relauncher.run_relauncher(self._argv(target_exe, tmp_path / "config"))
        assert rc == 0
        assert launched == [target_exe]


class TestAnExhaustedBudgetStillLooksOnce:
    """Zero left means "check once", never "skip the check".

    ``_remaining`` clamps a phase to what is left of the run, and an
    overrun earlier in the run hands the next phase 0.  Written as
    ``while time.monotonic() < deadline`` the waits then ran *no*
    iterations, which is not a shorter wait but a different answer: a
    parent that is already dead reported as still alive (exit 2, and the
    user is left with no keyboard), and an installer that finished while
    an earlier phase overran reported as never having arrived.
    """

    def test_a_dead_parent_is_seen_even_with_no_budget_left(self):
        seen: list[int] = []

        def dead(pid):
            seen.append(pid)
            return False

        with patch.object(relauncher, "_process_alive", dead):
            assert relauncher._wait_for_parent_exit(1234, 0.0) is True
        assert seen == [1234], "the check was skipped, not merely cut short"

    def test_a_live_parent_with_no_budget_left_gives_up_after_looking(self):
        seen: list[int] = []

        def alive(pid):
            seen.append(pid)
            return True

        with patch.object(relauncher, "_process_alive", alive):
            assert relauncher._wait_for_parent_exit(1234, 0.0) is False
        assert seen == [1234]

    def test_an_exe_already_in_place_is_seen_with_no_budget_left(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        target.write_bytes(b"x")
        assert relauncher._wait_for_new_exe(target, None, 0.0) is True

    def test_a_missing_exe_with_no_budget_left_still_reports_missing(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        assert relauncher._wait_for_new_exe(target, None, 0.0) is False

    def test_an_exhausted_ceiling_gives_up_promptly_rather_than_waiting(self, tmp_path):
        """The clamp reaching the wait, not just the arithmetic.

        A live pid (our own) and a ceiling that expired 30 s ago: the
        run must end on one look rather than sit out the nominal 60 s.
        """
        args = argparse.Namespace(
            parent_pid=os.getpid(),
            new_version="1.0.18",
            previous_version="1.0.17",
            target_exe=str(tmp_path / "alpha-osk.exe"),
            config_dir=str(tmp_path / "config"),
        )
        started = time.monotonic()
        rc = relauncher._run_headless(args, overall_deadline=time.monotonic() - 30)
        assert rc == 2
        assert time.monotonic() - started < 2.0

    def test_the_give_up_log_names_the_budget_it_waited_not_the_constant(self, tmp_path, caplog):
        """The only post-mortem surface this process has.

        It is detached and has no console, so relauncher.log is where a
        failure is read.  Reporting the nominal 60 s for a wait that was
        clamped to nothing sends the next reader looking for a hang that
        never happened.
        """
        args = argparse.Namespace(
            parent_pid=os.getpid(),
            new_version="1.0.18",
            previous_version="1.0.17",
            target_exe=str(tmp_path / "alpha-osk.exe"),
            config_dir=str(tmp_path / "config"),
        )
        with caplog.at_level("ERROR", logger="UpdateRelauncher"):
            relauncher._run_headless(args, overall_deadline=time.monotonic() - 30)
        assert any("after 0s" in r.getMessage() for r in caplog.records), caplog.text

    def test_the_fallback_continues_the_ceiling_it_was_given(self, tmp_path):
        """A splash that raises must not hand headless a fresh 300 s.

        Derived per path, the two ceilings ran back to back and the
        documented one silently became double.  ``run_relauncher`` takes
        it once and passes it down; here the splash raises immediately
        and the headless fallback inherits an already-expired one, so it
        gives up rather than starting its own budget.
        """
        target_exe = tmp_path / "alpha-osk.exe"
        target_exe.write_bytes(b"x")
        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            str(os.getpid()),
            "--new-version",
            "1.0.18",
            "--previous-version",
            "1.0.17",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(tmp_path / "config"),
            "--show-splash",
        ]

        def boom(args, ceiling):
            raise RuntimeError("no display server")

        started = time.monotonic()
        with (
            patch.object(relauncher, "_MAX_TOTAL_RUNTIME_S", 0),
            patch.object(relauncher, "_run_with_splash", boom),
        ):
            rc = relauncher.run_relauncher(argv)
        assert rc == 2
        assert time.monotonic() - started < 2.0, "the fallback started a fresh ceiling"


class TestTheDevTargetIsDecidedBeforeAnyWaiting:
    """It depends only on argv, so nothing may be waited on first.

    Tested after the parent-exit wait, as it first was, a parent slow to
    die still bought a 60 s detached, console-less process for a target
    the code already knew it would never relaunch -- a shorter version of
    the very thing being fixed.
    """

    def test_no_wait_is_entered_for_a_dev_target(self, tmp_path):
        target_exe = tmp_path / "python.exe"
        target_exe.write_bytes(b"x")
        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            str(os.getpid()),  # alive, so any wait would run its full budget
            "--new-version",
            "1.0.18",
            "--previous-version",
            "1.0.17",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(tmp_path / "config"),
        ]

        def never(*a, **kw):
            raise AssertionError("a dev target must not wait for anything")

        with (
            patch.object(relauncher, "_wait_for_parent_exit", never),
            patch.object(relauncher, "_wait_for_new_exe", never),
        ):
            assert relauncher.run_relauncher(argv) == 0


class TestRemaining:
    """The clamp that keeps the phases summing to the ceiling."""

    def test_a_phase_gets_its_own_budget_when_there_is_room(self):
        deadline = time.monotonic() + 1000
        assert relauncher._remaining(deadline, 60) == pytest.approx(60, abs=0.1)

    def test_a_phase_is_clipped_to_what_is_left(self):
        deadline = time.monotonic() + 5
        assert relauncher._remaining(deadline, 60) == pytest.approx(5, abs=0.5)

    def test_an_expired_budget_is_zero_not_negative(self):
        """Negative would be worse than useless.

        Every wait here compares against ``time.monotonic() + timeout``,
        so a negative budget still works out as "already expired"; but a
        caller that ever multiplies or sums these would silently get time
        back. Zero means "check once and give up" -- which is a property
        of the waits rather than of this arithmetic, and is asserted
        against them in ``TestAnExhaustedBudgetStillLooksOnce``.
        """
        deadline = time.monotonic() - 30
        assert relauncher._remaining(deadline, 60) == 0.0

    def test_the_installer_grace_is_clamped_by_the_same_ceiling(self):
        """Both paths take it from here, which is why it has a name.

        The splash path's grace was a bare
        ``QTimer.singleShot(_INSTALLER_GRACE_S * 1000)`` outside the
        budget, so the ceiling bound the path the tests exercise and not
        the one production runs.
        """
        assert relauncher._installer_grace_s(time.monotonic() + 1000) == pytest.approx(
            relauncher._INSTALLER_GRACE_S, abs=0.1
        )
        assert relauncher._installer_grace_s(time.monotonic() - 30) == 0.0


class TestIsDevTarget:
    """_is_dev_target — distinguishes a real installed alpha-osk.exe
    target from a python interpreter target (dev-mode invocation)."""

    def test_python_exe_is_dev_target(self):
        assert (
            relauncher._is_dev_target(
                r"C:\Users\Owen\AppData\Local\Programs\Python\Python311\python.exe"
            )
            is True
        )

    def test_pythonw_is_dev_target(self):
        assert relauncher._is_dev_target(r"C:\Python311\pythonw.exe") is True

    def test_alpha_osk_exe_is_not_dev_target(self):
        assert relauncher._is_dev_target(r"C:\Program Files\Alpha-OSK\alpha-osk.exe") is False

    def test_case_insensitive(self):
        assert relauncher._is_dev_target(r"C:\PYTHON311\PYTHON.EXE") is True
        assert relauncher._is_dev_target(r"C:\Foo\Alpha-OSK.exe") is False


class TestNewExeReady:
    """_new_exe_ready — single-shot mirror of _wait_for_new_exe used by
    the splash path so it can yield to the Qt event loop between
    checks instead of blocking inside a sleep loop."""

    def test_returns_false_for_missing_file(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        assert relauncher._new_exe_ready(target, after_mtime=None) is False

    def test_returns_false_for_zero_byte_file(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        target.write_bytes(b"")
        assert relauncher._new_exe_ready(target, after_mtime=None) is False

    def test_returns_true_for_non_empty_file_with_no_mtime_floor(self, tmp_path):
        target = tmp_path / "alpha-osk.exe"
        target.write_bytes(b"x")
        assert relauncher._new_exe_ready(target, after_mtime=None) is True

    def test_rejects_stale_exe_when_after_mtime_set(self, tmp_path):
        # File predates parent death — this is the OLD exe, installer
        # hasn't finished writing yet. Returning True here would race.
        import os as _os

        target = tmp_path / "alpha-osk.exe"
        target.write_bytes(b"x")
        old_mtime = time.time() - 3600
        _os.utime(target, (old_mtime, old_mtime))
        assert relauncher._new_exe_ready(target, after_mtime=time.time()) is False

    def test_accepts_fresh_exe_when_after_mtime_set(self, tmp_path):
        import os as _os

        target = tmp_path / "alpha-osk.exe"
        target.write_bytes(b"x")
        future_mtime = time.time() + 3600
        _os.utime(target, (future_mtime, future_mtime))
        assert relauncher._new_exe_ready(target, after_mtime=time.time()) is True


class TestShowSplashFlag:
    """The --show-splash flag opts into the Qt splash path. Tests
    deliberately don't pass it, so they exercise the headless code
    path; this class just confirms the flag parses without breaking
    the existing CLI surface."""

    def test_argv_without_show_splash_runs_headless(self, tmp_path):
        # Same setup as TestRunRelauncherIntegration.test_happy_path
        # but explicitly assert the headless dispatch path is taken.
        import os as _os

        target_exe = tmp_path / "alpha-osk.exe"
        target_exe.write_bytes(b"freshly installed")
        future_mtime = time.time() + 3600
        _os.utime(target_exe, (future_mtime, future_mtime))
        config_dir = tmp_path / "config"

        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            "999999999",
            "--new-version",
            "1.0.18",
            "--previous-version",
            "1.0.17",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(config_dir),
        ]

        called: list[bool] = []
        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
            patch.object(
                relauncher, "_run_with_splash", lambda args, ceiling: called.append(True) or 0
            ),
            patch.object(relauncher, "_launch_new_osk", return_value=True),
        ):
            rc = relauncher.run_relauncher(argv)

        assert rc == 0
        assert called == [], "headless path must not invoke the splash"

    def test_argv_with_show_splash_dispatches_to_splash(self, tmp_path):
        config_dir = tmp_path / "config"
        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            "1",
            "--new-version",
            "1.0.18",
            "--previous-version",
            "1.0.17",
            "--target-exe",
            str(tmp_path / "alpha-osk.exe"),
            "--config-dir",
            str(config_dir),
            "--show-splash",
        ]

        observed: list[object] = []
        with patch.object(
            relauncher,
            "_run_with_splash",
            lambda args, ceiling: observed.append(args.show_splash) or 0,
        ):
            rc = relauncher.run_relauncher(argv)

        assert rc == 0
        assert observed == [True]

    def test_dev_mode_target_skips_splash(self, tmp_path):
        # When the target_exe is a python interpreter (dev mode), the
        # splash would sit at "Installing files…" until _NEW_EXE_TIMEOUT_S
        # because python.exe never gets a fresh mtime, leaving a stuck
        # window. The dispatcher must short-circuit to headless instead.
        import os as _os

        # Point target_exe at this Python interpreter to mimic dev mode.
        target_exe = tmp_path / "python.exe"
        target_exe.write_bytes(b"x")
        future_mtime = time.time() + 3600
        _os.utime(target_exe, (future_mtime, future_mtime))
        config_dir = tmp_path / "config"

        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            "999999999",
            "--new-version",
            "1.0.18",
            "--previous-version",
            "1.0.17",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(config_dir),
            "--show-splash",
        ]

        splash_called: list[bool] = []
        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
            patch.object(
                relauncher,
                "_run_with_splash",
                lambda args, ceiling: splash_called.append(True) or 0,
            ),
            patch.object(relauncher, "_launch_new_osk", return_value=True),
        ):
            rc = relauncher.run_relauncher(argv)

        assert rc == 0
        assert splash_called == [], (
            "dev-mode target must NOT spin up the splash — it would "
            "hang at 'Installing files…' for 3 minutes"
        )

    def test_splash_failure_falls_back_to_headless(self, tmp_path):
        # If PySide6 imports raise (no display, frozen-mode mishap),
        # the relauncher MUST still get the keyboard back. Falling
        # back to the silent headless path is the right behaviour.
        import os as _os

        target_exe = tmp_path / "alpha-osk.exe"
        target_exe.write_bytes(b"x")
        future_mtime = time.time() + 3600
        _os.utime(target_exe, (future_mtime, future_mtime))
        config_dir = tmp_path / "config"

        argv = [
            "alpha-osk.exe",
            "--update-relauncher",
            "--parent-pid",
            "999999999",
            "--new-version",
            "1.0.18",
            "--previous-version",
            "1.0.17",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(config_dir),
            "--show-splash",
        ]

        def boom(args, ceiling):
            raise RuntimeError("no display server")

        launch_calls: list[object] = []
        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
            patch.object(relauncher, "_run_with_splash", boom),
            patch.object(relauncher, "_launch_new_osk", lambda p: launch_calls.append(p) or True),
        ):
            rc = relauncher.run_relauncher(argv)

        assert rc == 0
        assert len(launch_calls) == 1, "headless fallback must still launch the OSK"
