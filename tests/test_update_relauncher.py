"""Tests for the post-update relauncher helper."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from unittest.mock import patch

import pytest

from src import _update_relauncher as relauncher
from src import updater

# Taken before the autouse stub below replaces it, for the tests that are
# about the rule itself rather than about the flow around it.
_REAL_PROCESS_IMAGE_RUNNING = relauncher._process_image_running


@pytest.fixture(autouse=True)
def _the_relaunched_keyboard_appears(monkeypatch):
    """Every test here sees the new keyboard come up unless it says otherwise.

    The real check reads this machine's process list. Left alone, a flow
    test would pass on a developer's machine because their own keyboard
    is running, and on CI wait out the 15 s budget and then fail: the
    result decided by something outside the test. A test wanting another
    answer patches the same name and wins, since its patch is applied
    after this one.
    """
    monkeypatch.setattr(relauncher, "_process_image_running", lambda name: True)


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


class TestAFreshInstallIsSeenThroughItsBuildTimestamp:
    """The readiness check asks "did the mtime change", never "is it newer".

    NSIS restores each extracted file's build-machine timestamp
    (``SetDateSave`` defaults on), so the freshly-installed exe's mtime
    *predates* the install by however old the build is; measured on a
    real machine, an install that ran at 19:13 left an exe stamped two
    days earlier.  The pre-fix gate required ``mtime > parent-death``
    and therefore could never fire in production: every real update
    burned the full new-exe timeout and then reported failure.  The
    updater now snapshots the old exe's mtime before the install and
    the helper waits for it to *differ*.
    """

    def test_a_build_stamped_exe_older_than_the_kill_is_ready(self, tmp_path):
        # The production case this class exists for: the new exe's mtime
        # is in the past relative to the parent's death, but differs
        # from the pre-install snapshot.
        exe = tmp_path / "alpha-osk.exe"
        exe.write_bytes(b"new build")
        build_time = time.time() - 2 * 86400
        os.utime(exe, (build_time, build_time))
        old_snapshot = build_time - 30 * 86400  # the previous build's stamp
        parent_death = time.time()
        assert relauncher._new_exe_looks_fresh(exe, old_snapshot, parent_death) is True

    def test_the_old_exe_still_in_place_is_not_ready(self, tmp_path):
        # Mid-install, before extraction reaches the exe: identical
        # mtime to the snapshot means nothing has been written yet.
        exe = tmp_path / "alpha-osk.exe"
        exe.write_bytes(b"old build")
        build_time = time.time() - 30 * 86400
        os.utime(exe, (build_time, build_time))
        snapshot = exe.stat().st_mtime
        assert relauncher._new_exe_looks_fresh(exe, snapshot, time.time()) is False

    def test_no_snapshot_falls_back_to_the_legacy_comparison(self, tmp_path):
        # A helper spawned by an updater too old to pass --old-exe-mtime
        # must keep the behaviour it always had, in both directions.
        exe = tmp_path / "alpha-osk.exe"
        exe.write_bytes(b"contents")
        fresh = time.time() + 3600
        os.utime(exe, (fresh, fresh))
        assert relauncher._new_exe_looks_fresh(exe, 0.0, time.time()) is True
        stale = time.time() - 3600
        os.utime(exe, (stale, stale))
        assert relauncher._new_exe_looks_fresh(exe, 0.0, time.time()) is False


class TestAnExeStillBeingWrittenIsNotReady:
    """While the installer writes the exe, its mtime has already changed and its
    size is already non-zero, so the mtime rule alone says "ready" for the
    whole write; launching then hands Explorer a half-written image."""

    def _changed_exe(self, tmp_path):
        exe = tmp_path / "alpha-osk.exe"
        exe.write_bytes(b"new build")
        return exe, exe.stat().st_mtime - 86400  # the snapshot differs

    def test_a_file_open_for_writing_is_not_ready(self, tmp_path, monkeypatch):
        exe, snapshot = self._changed_exe(tmp_path)
        monkeypatch.setattr(relauncher, "_file_is_open_for_writing", lambda path: True)
        assert relauncher._new_exe_looks_fresh(exe, snapshot, None) is False

    def test_the_same_file_once_closed_is_ready(self, tmp_path, monkeypatch):
        exe, snapshot = self._changed_exe(tmp_path)
        monkeypatch.setattr(relauncher, "_file_is_open_for_writing", lambda path: False)
        assert relauncher._new_exe_looks_fresh(exe, snapshot, None) is True

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 share modes")
    def test_a_real_writer_is_seen_and_its_close_is_too(self, tmp_path):
        exe, snapshot = self._changed_exe(tmp_path)
        with open(exe, "ab") as writer:
            writer.write(b"more")
            writer.flush()
            assert relauncher._file_is_open_for_writing(exe) is True
            assert relauncher._new_exe_looks_fresh(exe, snapshot, None) is False
        assert relauncher._file_is_open_for_writing(exe) is False
        assert relauncher._new_exe_looks_fresh(exe, snapshot, None) is True

    @pytest.mark.skipif(sys.platform != "win32", reason="Win32 share modes")
    def test_a_reader_is_not_mistaken_for_a_writer(self, tmp_path):
        """An antivirus scan or Explorer reading the icon must not hold it up."""
        exe, _ = self._changed_exe(tmp_path)
        with open(exe, "rb"):
            assert relauncher._file_is_open_for_writing(exe) is False

    def test_a_probe_that_cannot_run_does_not_block_the_relaunch(self, tmp_path):
        assert relauncher._file_is_open_for_writing(tmp_path / "missing.exe") is False

    def test_the_wait_loop_and_the_splash_check_share_the_rule(self, tmp_path):
        # Both entry points must see the production case; a fix applied
        # to only one of them leaves the other burning its timeout.
        exe = tmp_path / "alpha-osk.exe"
        exe.write_bytes(b"new build")
        build_time = time.time() - 2 * 86400
        os.utime(exe, (build_time, build_time))
        old_snapshot = build_time - 30 * 86400
        parent_death = time.time()
        assert relauncher._wait_for_new_exe(exe, parent_death, 0.6, old_snapshot) is True
        assert relauncher._new_exe_ready(exe, parent_death, old_snapshot) is True

    def test_run_relauncher_carries_the_snapshot_end_to_end(self, tmp_path):
        # The argv plumbing: a build-stamped exe (older than the kill)
        # plus a differing --old-exe-mtime must relaunch, where the
        # legacy gate would have timed out and returned 3.
        exe = tmp_path / "alpha-osk.exe"
        exe.write_bytes(b"freshly installed")
        build_time = time.time() - 2 * 86400
        os.utime(exe, (build_time, build_time))
        config_dir = tmp_path / "config"

        argv = [
            "alpha-osk-relauncher.exe",
            "--update-relauncher",
            "--parent-pid",
            "999999999",  # already dead
            "--new-version",
            "1.0.16",
            "--previous-version",
            "1.0.15",
            "--target-exe",
            str(exe),
            "--old-exe-mtime",
            str(build_time - 30 * 86400),
            "--config-dir",
            str(config_dir),
        ]

        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
            patch.object(relauncher, "_launch_new_osk", return_value=True) as mock_launch,
        ):
            rc = relauncher.run_relauncher(argv)

        assert rc == 0
        mock_launch.assert_called_once()


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
    phases have always had a bounded sum).  The real waste was
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


class TestTheKeyboardComesBackThroughExplorer:
    """_launch_command: a UIAccess exe only gets UIAccess when Explorer starts it.

    Measured 2026-10-04 with Windows' own osk.exe: launched by CreateProcess
    or ShellExecuteEx from a process without UIAccess it comes up with
    TokenUIAccess=0; launched by explorer.exe it comes up with 1. The helper
    runs from %TEMP% and has none to hand down, so it must go via Explorer.
    """

    def test_on_windows_explorer_is_the_launcher(self, tmp_path, monkeypatch):
        monkeypatch.setattr(relauncher.sys, "platform", "win32")
        monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
        exe = tmp_path / "Alpha-OSK" / "alpha-osk.exe"
        argv = relauncher._launch_command(exe)
        assert argv == [str(tmp_path / "Windows" / "explorer.exe"), str(exe)]

    def test_elsewhere_the_exe_is_launched_directly(self, tmp_path, monkeypatch):
        monkeypatch.setattr(relauncher.sys, "platform", "linux")
        exe = tmp_path / "alpha-osk"
        assert relauncher._launch_command(exe) == [str(exe)]

    def test_the_launcher_hands_that_argv_to_popen(self, tmp_path, monkeypatch):
        monkeypatch.setattr(relauncher.sys, "platform", "win32")
        monkeypatch.setenv("WINDIR", str(tmp_path / "Windows"))
        exe = tmp_path / "Alpha-OSK" / "alpha-osk.exe"
        calls = []
        with patch.object(relauncher.subprocess, "Popen", lambda argv, **kw: calls.append(argv)):
            assert relauncher._launch_new_osk(exe) is True
        assert calls == [[str(tmp_path / "Windows" / "explorer.exe"), str(exe)]]

    def test_a_failed_spawn_is_reported_not_raised(self, tmp_path):
        def boom(*a, **kw):
            raise OSError("no")

        with patch.object(relauncher.subprocess, "Popen", boom):
            assert relauncher._launch_new_osk(tmp_path / "alpha-osk.exe") is False


class TestTheLaunchIsConfirmedByTheKeyboardAppearing:
    """With Explorer in between, a successful Popen only proves Explorer started.

    Both paths treated it as success, so the splash could say "Done!"
    with no keyboard on screen. They now wait, bounded, for a process
    with the keyboard's exact image name, and a launch that never
    produces one is reported as the failed launch it is.
    """

    def _argv(self, target_exe, config_dir):
        return [
            "alpha-osk-relauncher.exe",
            "--update-relauncher",
            "--parent-pid",
            "999999999",  # already dead
            "--new-version",
            "1.5.1",
            "--previous-version",
            "1.5.0",
            "--target-exe",
            str(target_exe),
            "--config-dir",
            str(config_dir),
        ]

    def _fresh_exe(self, tmp_path):
        exe = tmp_path / "alpha-osk.exe"
        exe.write_bytes(b"freshly installed")
        future = time.time() + 3600
        os.utime(exe, (future, future))
        return exe

    def test_a_relay_that_starts_no_keyboard_is_a_failed_launch(self, tmp_path, caplog):
        exe = self._fresh_exe(tmp_path)
        config_dir = tmp_path / "config"
        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
            patch.object(relauncher, "_NEW_OSK_APPEAR_TIMEOUT_S", 0.3),
            patch.object(relauncher, "_launch_new_osk", return_value=True),
            patch.object(relauncher, "_process_image_running", lambda name: False),
            caplog.at_level("ERROR", logger="UpdateRelauncher"),
        ):
            rc = relauncher.run_relauncher(self._argv(exe, config_dir))

        assert rc == 4, "a keyboard that never appeared must not be reported as success"
        assert not (config_dir / "update_handoff.json").is_file()
        assert any("alpha-osk.exe" in r.getMessage() for r in caplog.records), caplog.text

    def test_a_relay_that_brings_the_keyboard_back_succeeds(self, tmp_path):
        """The inverse, so "always report failure" cannot pass as the fix.

        The keyboard turns up on the third look, as a relay through
        Explorer does: a moment after Popen returns, not at once.
        """
        exe = self._fresh_exe(tmp_path)
        config_dir = tmp_path / "config"
        asked: list[str] = []

        def appears_on_the_third_look(name):
            asked.append(name)
            return len(asked) >= 3

        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
            patch.object(relauncher, "_NEW_OSK_POLL_INTERVAL_S", 0.01),
            patch.object(relauncher, "_launch_new_osk", return_value=True),
            patch.object(relauncher, "_process_image_running", appears_on_the_third_look),
        ):
            rc = relauncher.run_relauncher(self._argv(exe, config_dir))

        assert rc == 0
        assert asked == ["alpha-osk.exe"] * 3, "it must ask for the keyboard, by its image name"
        assert (config_dir / "update_handoff.json").is_file()

    def test_the_helpers_own_image_does_not_count_as_the_keyboard(self):
        # The helper is running while it waits, under a name that starts
        # with the keyboard's. A prefix or substring match would confirm
        # the launch on the strength of the helper itself.
        assert updater._RELAUNCHER_EXE_NAME.startswith("alpha-osk")
        running = ["System", "explorer.exe", updater._RELAUNCHER_EXE_NAME]
        with patch.object(relauncher, "_running_image_names", lambda: running):
            assert _REAL_PROCESS_IMAGE_RUNNING("alpha-osk.exe") is False

    def test_the_keyboard_counts_whatever_the_case_of_its_image_name(self):
        # Windows compares image names case-insensitively, and so must we.
        running = ["explorer.exe", updater._RELAUNCHER_EXE_NAME, "Alpha-OSK.EXE"]
        with patch.object(relauncher, "_running_image_names", lambda: running):
            assert _REAL_PROCESS_IMAGE_RUNNING("alpha-osk.exe") is True

    def test_an_unreadable_process_list_is_not_a_failed_launch(self):
        # No evidence either way falls back to what the helper reported
        # before the check existed, rather than a false failure message.
        with patch.object(relauncher, "_running_image_names", lambda: None):
            assert _REAL_PROCESS_IMAGE_RUNNING("alpha-osk.exe") is True

    def test_the_wait_steps_rather_than_spinning(self):
        looks: list[str] = []

        def never(name):
            looks.append(name)
            return False

        with patch.object(relauncher, "_process_image_running", never):
            assert relauncher._wait_for_new_osk_process("alpha-osk.exe", 0.6) is False
        # 0.6 s at 250 ms steps is three or four looks; a tight loop
        # would be thousands, each one a whole-system process snapshot.
        assert 2 <= len(looks) <= 6, len(looks)

    def test_an_exhausted_budget_still_looks_once(self):
        looks: list[str] = []

        def present(name):
            looks.append(name)
            return True

        with patch.object(relauncher, "_process_image_running", present):
            assert relauncher._wait_for_new_osk_process("alpha-osk.exe", 0.0) is True
        assert looks == ["alpha-osk.exe"], "the look was skipped, not merely cut short"

    @pytest.mark.skipif(
        sys.platform != "win32" and not os.path.isdir("/proc"),
        reason="no process enumeration on this platform",
    )
    def test_the_real_enumerator_finds_this_process(self):
        """The ctypes structure has to be laid out right to list anything.

        A wrong ``dwSize`` fails ``Process32FirstW`` and reads as "cannot
        tell"; a wrong field offset garbles every name. Either way this
        process, which is certainly running, would not be found.
        """
        if sys.platform == "win32":
            own_name = os.path.basename(sys.executable)
        else:
            own_name = os.path.basename(os.readlink("/proc/self/exe"))
        names = relauncher._running_image_names()
        assert names is not None
        assert own_name.casefold() in {n.casefold() for n in names}, own_name


# ---------------------------------------------------------------------------
#  The update screen: always visible until the new keyboard window is up
# ---------------------------------------------------------------------------


class _World:
    """The facts an ``UpdateFlow`` reads, under test control, with a fake clock."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.parent_alive = True
        self.launched = False
        self.cancel = False
        self.exe_ready = False
        self.shown = False
        self.launch_ok = True
        self.launches = 0
        self.handoffs = 0

    def _launch(self) -> bool:
        self.launches += 1
        return self.launch_ok

    def _handoff(self) -> None:
        self.handoffs += 1

    def flow(self, **kwargs) -> relauncher.UpdateFlow:
        return relauncher.UpdateFlow(
            version="1.7.1",
            parent_alive=lambda: self.parent_alive,
            installer_launched=lambda: self.launched,
            cancelled=lambda: self.cancel,
            new_exe_ready=lambda death_time: self.exe_ready,
            launch_keyboard=self._launch,
            keyboard_shown=lambda: self.shown,
            write_handoff=self._handoff,
            clock=lambda: self.now,
            wall_clock=lambda: 5000.0,
            **kwargs,
        )

    def advance(self, flow: relauncher.UpdateFlow, seconds: float, step: float = 0.25) -> None:
        """Let ``seconds`` pass, stepping the flow every ``step`` like the QTimer does."""
        end = self.now + seconds
        while self.now < end:
            self.now += step
            flow.step()


@pytest.fixture
def world(monkeypatch) -> _World:
    monkeypatch.setattr(relauncher, "_INSTALLER_GRACE_S", 0)
    return _World()


class TestTheScreenOpensAtOnceAndWaitsForApproval:
    """The first bug: the helper's 60 s parent-exit wait started at spawn,
    before the UAC prompt, so a slow approval gave up and a declined prompt
    left a splash that silently expired."""

    def test_it_starts_on_the_approval_message(self, world):
        flow = world.flow()
        assert flow.phase is relauncher.Phase.APPROVE
        assert flow.message == "Waiting for you to approve the update"
        assert not flow.finished

    def test_a_slow_approval_does_not_use_up_the_parent_budget(self, world):
        flow = world.flow()
        world.advance(flow, 300)  # five minutes on the prompt
        assert flow.phase is relauncher.Phase.APPROVE, "the prompt may take as long as it takes"

        world.launched = True
        world.advance(flow, 1)
        assert flow.phase is relauncher.Phase.CLOSING

        # The parent budget starts now, in full, not from the spawn.
        world.advance(flow, relauncher._PARENT_EXIT_TIMEOUT_S - 5)
        assert flow.phase is relauncher.Phase.CLOSING
        world.advance(flow, 10)
        assert flow.phase is relauncher.Phase.FAILED
        assert flow.exit_code == relauncher.EXIT_PARENT_STUCK

    def test_a_cancel_sends_the_screen_away_without_a_failure_message(self, world):
        flow = world.flow()
        world.advance(flow, 2)
        world.cancel = True
        world.advance(flow, 1)
        assert flow.phase is relauncher.Phase.CANCELLED
        assert flow.exit_code == relauncher.EXIT_CANCELLED
        assert flow.failure == "", "the keyboard's own toast reports a declined prompt"

    def test_a_cancel_beats_a_launch_marker_in_the_same_look(self, world):
        flow = world.flow()
        world.launched = True
        world.cancel = True
        world.advance(flow, 1)
        assert flow.phase is relauncher.Phase.CANCELLED

    def test_a_parent_that_vanished_without_a_marker_counts_as_launched(self, world):
        """The installer's taskkill can land before the marker is written."""
        flow = world.flow()
        world.parent_alive = False
        world.advance(flow, 1)
        assert flow.phase in (relauncher.Phase.CLOSING, relauncher.Phase.INSTALLING)

    def test_an_approval_that_never_comes_ends_on_a_screen_not_in_silence(self, world):
        flow = world.flow()
        world.advance(flow, relauncher._APPROVAL_TIMEOUT_S + 5)
        assert flow.phase is relauncher.Phase.FAILED
        assert flow.exit_code == relauncher.EXIT_NOT_APPROVED
        assert flow.failure

    def test_an_unsignalled_helper_starts_at_closing(self, world):
        flow = world.flow(wait_for_approval=False)
        assert flow.phase is relauncher.Phase.CLOSING
        assert flow.message == "Closing the keyboard"


class TestTheWholeRunNamesEveryPhase:
    def test_the_messages_in_order(self, world):
        flow = world.flow()
        seen = [flow.message]

        def note() -> None:
            if flow.message != seen[-1]:
                seen.append(flow.message)

        world.launched = True
        world.advance(flow, 1)
        note()
        world.parent_alive = False
        world.advance(flow, 1)
        note()
        world.exe_ready = True
        world.advance(flow, 1)
        note()
        world.shown = True
        world.advance(flow, 1)
        note()
        assert seen == [
            "Waiting for you to approve the update",
            "Closing the keyboard",
            "Installing Alpha-OSK 1.7.1",
            "Starting the keyboard",
            "Done",
        ]
        assert flow.phase is relauncher.Phase.DONE
        assert flow.exit_code == 0


class TestDoneMeansTheWindowIsOnScreen:
    """The third gap: "Done!" fired when the new *process* existed, before
    its window showed, which is exactly the blank gap the screen is for."""

    def _to_starting(self, world):
        flow = world.flow(wait_for_approval=False)
        world.parent_alive = False
        world.exe_ready = True
        world.advance(flow, 2)
        assert flow.phase is relauncher.Phase.STARTING
        return flow

    def test_a_launched_keyboard_whose_window_is_not_up_is_not_done(self, world):
        flow = self._to_starting(world)
        assert world.launches == 1
        world.advance(flow, 30)
        assert flow.phase is relauncher.Phase.STARTING, (
            "the process existing is not the window showing; there is no "
            "probe of the process list here at all"
        )
        assert world.handoffs == 0

    def test_the_window_event_finishes_it_and_writes_the_handoff_once(self, world):
        flow = self._to_starting(world)
        world.shown = True
        world.advance(flow, 2)
        assert flow.phase is relauncher.Phase.DONE
        assert world.handoffs == 1
        world.advance(flow, 5)
        assert world.handoffs == 1

    def test_a_keyboard_already_up_is_not_launched_a_second_time(self, world):
        """The installer's own explorer fallback, or the user, got there first."""
        flow = world.flow(wait_for_approval=False)
        world.parent_alive = False
        world.exe_ready = True
        world.shown = True
        world.advance(flow, 2)
        assert flow.phase is relauncher.Phase.DONE
        assert world.launches == 0

    def test_a_failed_launch_is_a_failure_screen(self, world):
        world.launch_ok = False
        flow = world.flow(wait_for_approval=False)
        world.parent_alive = False
        world.exe_ready = True
        world.advance(flow, 2)
        assert flow.phase is relauncher.Phase.FAILED
        assert flow.exit_code == relauncher.EXIT_LAUNCH_FAILED
        assert world.handoffs == 0


class TestAFailureStaysUntilTheUserActs:
    """The last gap: failure messages auto-closed after six seconds."""

    def test_a_window_that_never_appears_ends_on_a_screen_that_stays(self, world):
        flow = world.flow(wait_for_approval=False)
        world.parent_alive = False
        world.exe_ready = True
        world.advance(flow, relauncher._KEYBOARD_SHOWN_TIMEOUT_S + 5)
        assert flow.phase is relauncher.Phase.FAILED
        assert flow.exit_code == relauncher.EXIT_LAUNCH_FAILED
        assert "did not appear" in flow.failure
        assert flow.detail, "it says what to do next"

        # Nothing in the flow dismisses it, however long it is left.
        world.advance(flow, 10_000)
        assert flow.phase is relauncher.Phase.FAILED
        assert flow.finished

    def test_the_new_exe_never_arriving_is_a_failure_screen_too(self, world):
        flow = world.flow(wait_for_approval=False)
        world.parent_alive = False
        world.advance(flow, relauncher._NEW_EXE_TIMEOUT_S + 10)
        assert flow.phase is relauncher.Phase.FAILED
        assert flow.exit_code == relauncher.EXIT_NEW_EXE_MISSING

    def test_the_ceiling_ends_in_the_failure_screen_never_a_silent_exit(self, world):
        """Parent slow to die, then an install that never lands: the phases sum
        past 300 s, and the clip must still end on a failure, not a hang."""
        flow = world.flow()
        world.launched = True
        launched_at = world.now
        world.advance(flow, 50)
        world.parent_alive = False
        world.advance(flow, relauncher._MAX_TOTAL_RUNTIME_S)
        assert flow.phase is relauncher.Phase.FAILED
        assert world.now - launched_at <= relauncher._MAX_TOTAL_RUNTIME_S + 50 + 1

    def test_a_keyboard_that_turns_up_after_the_failure_clears_it(self, world):
        """The installer's own explorer fallback, or a slow first start: the
        screen must not sit topmost over a working keyboard saying it failed."""
        flow = world.flow(wait_for_approval=False)
        world.parent_alive = False
        world.exe_ready = True
        world.advance(flow, relauncher._KEYBOARD_SHOWN_TIMEOUT_S + 5)
        assert flow.phase is relauncher.Phase.FAILED
        world.shown = True
        world.advance(flow, 1)
        assert flow.phase is relauncher.Phase.DONE
        assert flow.exit_code == 0
        assert world.handoffs == 1

    def test_the_old_keyboard_still_running_never_clears_a_failure(self, world):
        """Before the old keyboard is gone its own announcement is still set."""
        flow = world.flow()
        world.launched = True
        world.shown = True  # the old keyboard's event, alive with it
        world.advance(flow, relauncher._PARENT_EXIT_TIMEOUT_S + 5)
        assert flow.phase is relauncher.Phase.FAILED
        assert flow.exit_code == relauncher.EXIT_PARENT_STUCK
        world.advance(flow, 30)
        assert flow.phase is relauncher.Phase.FAILED
        assert world.handoffs == 0

    def test_a_probe_that_raised_is_not_asked_again(self, world):
        flow = world.flow(wait_for_approval=False)
        world.parent_alive = False
        world.exe_ready = True
        world.advance(flow, 2)
        flow.fail_unexpectedly()
        world.shown = True
        world.advance(flow, 5)
        assert flow.phase is relauncher.Phase.FAILED

    def test_an_unexpected_error_is_shown_not_swallowed(self, world):
        flow = world.flow()
        flow.fail_unexpectedly()
        assert flow.phase is relauncher.Phase.FAILED
        assert flow.failure

    def test_a_budget_clamped_to_zero_still_looks_once_at_the_exe(self, world, monkeypatch):
        """The same rule the headless waits have: zero left means "check once"."""
        flow = world.flow(wait_for_approval=False, ceiling_deadline=world.now + 1)
        world.now += 10  # the ceiling is long gone
        world.parent_alive = False
        world.exe_ready = True
        flow.step()  # the parent is seen to be gone
        flow.step()  # the exe is looked at once, with no budget left
        assert flow.phase is relauncher.Phase.STARTING


class TestThePlacement:
    def test_it_is_centred_on_the_keyboard(self):
        pos = relauncher.centred_position((100, 200, 800, 300), (400, 200), (0, 0, 1920, 1080))
        assert pos == (300, 250)

    def test_it_is_clamped_to_the_keyboards_own_monitor(self):
        # A keyboard parked in the corner of a second monitor to the left.
        work = (-1920, 0, 0, 1040)
        pos = relauncher.centred_position((-1900, 1000, 100, 30), (400, 200), work)
        assert work[0] <= pos[0] and pos[0] + 400 <= work[2]
        assert work[1] <= pos[1] and pos[1] + 200 <= work[3]

    def test_a_window_bigger_than_the_screen_is_pinned_top_left(self):
        assert relauncher.centred_position((0, 0, 10, 10), (500, 500), (0, 0, 300, 300)) == (0, 0)

    def test_the_anchor_text_round_trips(self):
        assert relauncher.parse_anchor_rect("10,20,900,300") == (10, 20, 900, 300)
        assert relauncher.parse_anchor_rect("-1920,5,100,40") == (-1920, 5, 100, 40)

    @pytest.mark.parametrize("text", ["", "1,2,3", "1,2,3,4,5", "a,b,c,d", "1,2,0,4", "1,2,3,-4"])
    def test_junk_means_no_anchor_not_an_exception(self, text):
        assert relauncher.parse_anchor_rect(text) is None


class TestTheHeadlessFallbackAlsoWaitsForApproval:
    """If Qt cannot start, the fallback must not begin its 60 s parent wait
    while the UAC prompt is still up, which was the bug on the splash path."""

    def _args(self, tmp_path, signal_dir):
        target_exe = tmp_path / "alpha-osk.exe"
        target_exe.write_bytes(b"x")
        future = time.time() + 3600
        os.utime(target_exe, (future, future))
        return argparse.Namespace(
            parent_pid=999999999,
            new_version="1.7.1",
            previous_version="1.7.0",
            target_exe=str(target_exe),
            old_exe_mtime=0.0,
            config_dir=str(tmp_path / "config"),
            signal_dir=str(signal_dir),
        )

    def test_a_cancel_marker_ends_it_without_launching(self, tmp_path):
        signals = tmp_path / "signals"
        signals.mkdir()
        (signals / "cancel").write_bytes(b"")
        launched: list[object] = []
        with patch.object(relauncher, "_launch_new_osk", lambda t: launched.append(t) or True):
            rc = relauncher._run_headless(self._args(tmp_path, signals))
        assert rc == relauncher.EXIT_CANCELLED
        assert launched == []

    def test_the_launch_marker_lets_the_normal_flow_run(self, tmp_path):
        signals = tmp_path / "signals"
        signals.mkdir()
        (signals / "installer-launched").write_bytes(b"")
        with (
            patch.object(relauncher, "_INSTALLER_GRACE_S", 0),
            patch.object(relauncher, "_NEW_EXE_TIMEOUT_S", 2),
            patch.object(relauncher, "_launch_new_osk", return_value=True),
        ):
            rc = relauncher._run_headless(self._args(tmp_path, signals))
        assert rc == 0

    def test_a_live_parent_with_no_marker_waits_rather_than_starting_the_clock(
        self, tmp_path, monkeypatch
    ):
        signals = tmp_path / "signals"
        signals.mkdir()
        monkeypatch.setattr(relauncher, "_process_alive", lambda pid: True)
        monkeypatch.setattr(relauncher, "_APPROVAL_TIMEOUT_S", 0.3)
        monkeypatch.setattr(relauncher, "_POLL_INTERVAL_S", 0.05)
        rc = relauncher._run_headless(self._args(tmp_path, signals))
        assert rc == relauncher.EXIT_NOT_APPROVED

    def test_the_new_flags_parse(self, tmp_path):
        """No ``--update-relauncher`` any more: the exe is the helper."""
        argv = [
            "alpha-osk-relauncher.exe",
            "--parent-pid",
            "1",
            "--new-version",
            "1.7.1",
            "--target-exe",
            str(tmp_path / "python.exe"),
            "--config-dir",
            str(tmp_path),
            "--signal-dir",
            str(tmp_path),
            "--anchor-rect",
            "1,2,3,4",
            "--show-splash",
        ]
        # A dev target returns before any wait, which is all this needs.
        assert relauncher.run_relauncher(argv) == 0


# ---------------------------------------------------------------------------
#  The window itself, driven offscreen in a child process
# ---------------------------------------------------------------------------

_SPLASH_SCRIPT = r"""
import argparse, os, sys, time
sys.path.insert(0, {repo!r})
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget
from src import _update_relauncher as r

app = QApplication([])
r._TICK_MS = 30
r._INSTALLER_GRACE_S = 0
r._DONE_DWELL_MS = 50
tmp0 = {tmp!r}
# The parent keyboard is alive until the installer is "launched".
launched_marker = os.path.join(tmp0, "signals", "installer-launched")
r._process_alive = lambda pid: not os.path.exists(launched_marker)
launches = []

class Shown:
    # The window is not up until the keyboard has been launched.
    value = False
    def __call__(self): return Shown.value
    def close(self): pass
r._KeyboardShownProbe = Shown

def launch(target):
    launches.append(str(target))
    Shown.value = True
    return True
r._launch_new_osk = launch

scenario = {scenario!r}
tmp = {tmp!r}
args = argparse.Namespace(
    parent_pid=1, new_version="9.9.9", previous_version="9.9.8",
    target_exe=os.path.join(tmp, "alpha-osk.exe"), old_exe_mtime=0.0,
    config_dir=tmp, signal_dir=os.path.join(tmp, "signals"), anchor_rect="",
)
os.makedirs(args.signal_dir, exist_ok=True)
open(args.target_exe, "wb").write(b"x")
r._new_exe_ready = lambda *a, **k: scenario != "never_installs"

def splash():
    return [w for w in app.topLevelWidgets() if w.objectName() == "splash"][0]

report = {{}}

def look_while_waiting():
    w = splash()
    report["approve_msg"] = w.findChild(QLabel, "msg").text()
    report["buttons_visible_while_working"] = w.findChild(QWidget, "buttons").isVisibleTo(w)
    report["has_close_x"] = w.findChild(QLabel, "close") is not None
    if scenario == "cancel":
        open(os.path.join(args.signal_dir, "cancel"), "wb").write(b"")
    else:
        open(os.path.join(args.signal_dir, "installer-launched"), "wb").write(b"")

def look_at_failure():
    w = splash()
    report["failure_msg"] = w.findChild(QLabel, "msg").text()
    start = w.findChild(QPushButton, "start")
    report["start_visible"] = start.isVisibleTo(w)
    report["start_height"] = start.height()
    report["logs_visible"] = w.findChild(QPushButton, "logs").isVisibleTo(w)
    if scenario == "never_installs":
        start.click()

QTimer.singleShot(300, look_while_waiting)
if scenario == "never_installs":
    r._NEW_EXE_TIMEOUT_S = 0.2
    QTimer.singleShot(1500, look_at_failure)

# Hard stop so a hung child cannot hang the suite.
QTimer.singleShot(8000, lambda: (print("TIMEOUT"), os._exit(99)))
rc = r._run_with_splash(args)
report["rc"] = rc
report["launches"] = launches
report["handoff"] = os.path.exists(os.path.join(tmp, "update_handoff.json"))
print("REPORT", report)
"""


def _run_splash(tmp_path, scenario: str) -> dict:
    import ast
    import subprocess

    repo = str(__import__("pathlib").Path(__file__).resolve().parent.parent)
    script = _SPLASH_SCRIPT.format(repo=repo, scenario=scenario, tmp=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("REPORT ")]
    assert lines, f"rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr[-3000:]}"
    return ast.literal_eval(lines[-1][len("REPORT ") :])


def _has_widgets() -> bool:
    try:
        import PySide6.QtWidgets  # noqa: F401
    except ImportError:
        return False
    return True


@pytest.mark.skipif(not _has_widgets(), reason="needs PySide6 widgets")
class TestTheWindowOffscreen:
    """Run in a child process: this suite's other Qt modules build a
    QGuiApplication, which cannot host widgets, so a QApplication here would
    be the wrong kind of application for whichever module ran second."""

    def test_the_happy_path_ends_done_with_no_buttons_and_no_close(self, tmp_path):
        report = _run_splash(tmp_path, "happy")
        assert report["approve_msg"] == "Waiting for you to approve the update"
        assert report["buttons_visible_while_working"] is False
        assert report["has_close_x"] is False, "no way to dismiss the screen while it works"
        assert report["rc"] == 0
        assert report["handoff"] is True
        assert report["launches"] == [str(tmp_path / "alpha-osk.exe")]

    def test_a_cancel_closes_it_quietly(self, tmp_path):
        report = _run_splash(tmp_path, "cancel")
        assert report["rc"] == relauncher.EXIT_CANCELLED
        assert report["launches"] == []

    def test_a_failure_stays_up_with_big_buttons_until_one_is_pressed(self, tmp_path):
        report = _run_splash(tmp_path, "never_installs")
        assert report["failure_msg"] == "The update did not finish installing."
        assert report["start_visible"] is True
        assert report["logs_visible"] is True
        assert report["start_height"] >= 56, "a large target for an imprecise pointer"
        # Pressing Start launched the keyboard and let the helper leave.
        assert report["launches"] == [str(tmp_path / "alpha-osk.exe")]
        assert report["rc"] == relauncher.EXIT_NEW_EXE_MISSING
        assert report["handoff"] is False
