"""The update window: moving it, its band, Start during an install, the lock.

Companion to ``test_update_relauncher.py``.  The state machine for the move
itself is in ``test_update_window_move.py``; here is what wraps it.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

import pytest

from src import _update_relauncher as relauncher
from tests.test_update_relauncher import _World

REPO = str(Path(__file__).resolve().parent.parent)


@pytest.fixture(autouse=True)
def _the_relaunched_keyboard_appears(monkeypatch):
    monkeypatch.setattr(relauncher, "_process_image_running", lambda name: True)


@pytest.fixture
def world(monkeypatch) -> _World:
    monkeypatch.setattr(relauncher, "_INSTALLER_GRACE_S", 0)
    return _World()


def _has_widgets() -> bool:
    try:
        import PySide6.QtWidgets  # noqa: F401
    except ImportError:
        return False
    return True


class TestTheWindowIsNotTopmostWhileItWaitsForUac:
    """A topmost window could cover a consent dialog that is not on the
    secure desktop.  It is topmost from the installer's launch on."""

    def test_not_topmost_while_waiting_for_approval(self, world):
        flow = world.flow()
        assert flow.phase is relauncher.Phase.APPROVE
        assert flow.wants_topmost is False

    def test_topmost_once_the_installer_launched(self, world):
        flow = world.flow()
        world.launched = True
        world.advance(flow, 1)
        assert flow.phase is relauncher.Phase.CLOSING
        assert flow.wants_topmost is True

    def test_topmost_on_a_failure_so_the_message_is_seen(self, world):
        flow = world.flow()
        world.advance(flow, relauncher._APPROVAL_TIMEOUT_S + 5)
        assert flow.phase is relauncher.Phase.FAILED
        assert flow.wants_topmost is True

    def test_an_unsignalled_helper_is_topmost_from_the_start(self, world):
        # No UAC prompt to wait for: it starts at Closing.
        assert world.flow(wait_for_approval=False).wants_topmost is True


class TestNoGracePeriodWhenTheSnapshotMakesReadinessExact:
    """The 5 s pause after the keyboard closes bought nothing once the
    readiness rule compares against the installed exe's pre-install mtime and
    refuses a file still open for writing: the installer writes alpha-osk.exe
    last, so a changed, closed exe is a finished extraction."""

    def _reach_installing(self, world, **kwargs):
        flow = world.flow(wait_for_approval=False, **kwargs)
        world.parent_alive = False
        world.advance(flow, 0.5)
        assert flow.phase in (relauncher.Phase.INSTALLING, relauncher.Phase.STARTING)
        return flow

    def test_with_a_snapshot_a_ready_exe_is_launched_at_the_first_look(self, world, monkeypatch):
        monkeypatch.setattr(relauncher, "_INSTALLER_GRACE_S", 5)
        world.exe_ready = True
        flow = self._reach_installing(world, has_exe_snapshot=True)
        world.advance(flow, 0.5)
        assert flow.phase is relauncher.Phase.STARTING

    def test_without_a_snapshot_the_pause_stays(self, world, monkeypatch):
        # The flow's clock is a fake one, which the helper that clips the
        # grace to the run's budget does not share, so pin its answer.
        monkeypatch.setattr(relauncher, "_installer_grace_s", lambda deadline: 5.0)
        world.exe_ready = True
        flow = self._reach_installing(world, has_exe_snapshot=False)
        world.advance(flow, 3)
        assert flow.phase is relauncher.Phase.INSTALLING, "the legacy comparison is weaker"
        world.advance(flow, 3)
        assert flow.phase is relauncher.Phase.STARTING

    def test_an_exe_that_is_not_ready_is_still_waited_for(self, world, monkeypatch):
        monkeypatch.setattr(relauncher, "_INSTALLER_GRACE_S", 5)
        flow = self._reach_installing(world, has_exe_snapshot=True)
        world.advance(flow, 10)
        assert flow.phase is relauncher.Phase.INSTALLING
        world.exe_ready = True
        world.advance(flow, 1)
        assert flow.phase is relauncher.Phase.STARTING

    def test_the_headless_path_skips_the_pause_with_a_snapshot(self, tmp_path, monkeypatch):
        slept: list[float] = []
        monkeypatch.setattr(relauncher.time, "sleep", lambda s: slept.append(s))
        monkeypatch.setattr(relauncher, "_wait_for_parent_exit", lambda pid, t: True)
        monkeypatch.setattr(relauncher, "_wait_for_new_exe", lambda *a, **k: True)
        monkeypatch.setattr(relauncher, "_launch_new_osk", lambda p: True)
        monkeypatch.setattr(relauncher, "_wait_for_new_osk_process", lambda n, t: True)
        monkeypatch.setattr(relauncher, "_write_handoff", lambda *a: None)

        def run(old_mtime: float) -> float:
            slept.clear()
            args = argparse.Namespace(
                parent_pid=1,
                new_version="9.9.9",
                previous_version="9.9.8",
                target_exe=str(tmp_path / "alpha-osk.exe"),
                old_exe_mtime=old_mtime,
                config_dir=str(tmp_path),
                signal_dir="",
            )
            assert relauncher._run_headless(args) == 0
            return sum(slept)

        assert run(123.0) == 0
        assert run(0.0) == pytest.approx(relauncher._INSTALLER_GRACE_S, abs=0.1)


class TestStartWaitsForARunningInstaller:
    """Pressing Start Alpha-OSK on a failure screen while the installer is
    still going would launch the old exe, or a half-written new one, in the
    middle of the install (the failure timeout can be shorter than a slow
    install)."""

    def _failed(self, world, busy):
        flow = world.flow(installer_busy=lambda: busy[0])
        world.advance(flow, relauncher._APPROVAL_TIMEOUT_S + 5)
        assert flow.phase is relauncher.Phase.FAILED
        return flow

    def test_start_launches_when_no_install_is_running(self, world):
        flow = self._failed(world, [False])
        assert flow.request_start() is True
        assert flow.launch_requested is False

    def test_start_says_it_is_still_installing_and_does_not_launch(self, world):
        flow = self._failed(world, [True])
        assert flow.request_start() is False
        assert "still running" in flow.detail
        world.advance(flow, 5)
        assert flow.launch_requested is False, "launching mid-install is the bug"

    def test_the_press_is_remembered_and_acted_on_when_the_installer_ends(self, world):
        busy = [True]
        flow = self._failed(world, busy)
        flow.request_start()
        busy[0] = False
        world.advance(flow, 0.5)
        assert flow.launch_requested is True

    def test_a_press_that_was_never_made_does_not_launch(self, world):
        busy = [True]
        flow = self._failed(world, busy)
        busy[0] = False
        world.advance(flow, 5)
        assert flow.launch_requested is False

    def test_a_press_when_free_does_not_leave_a_pending_launch(self, world):
        busy = [False]
        flow = self._failed(world, busy)
        flow.request_start()
        busy[0] = True
        world.advance(flow, 1)
        busy[0] = False
        world.advance(flow, 1)
        assert flow.launch_requested is False


class TestTheInstallerProbe:
    def test_the_installer_by_image_name_counts_as_running(self, monkeypatch):
        monkeypatch.setattr(
            relauncher,
            "_running_image_names",
            lambda: ["explorer.exe", "ALPHA-OSK-SETUP-1.7.1.EXE"],
        )
        assert relauncher._installer_is_running("Alpha-OSK-Setup-1.7.1.exe") is True
        assert relauncher._installer_is_running("Alpha-OSK-Setup-1.7.2.exe") is False

    def test_an_unreadable_process_list_never_blocks_the_user(self, monkeypatch):
        monkeypatch.setattr(relauncher, "_running_image_names", lambda: None)
        assert relauncher._installer_is_running("Alpha-OSK-Setup-1.7.1.exe") is False

    def test_no_image_name_means_not_running(self):
        assert relauncher._installer_is_running("") is False

    def test_an_exe_still_open_for_writing_is_busy_even_without_the_process(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(relauncher, "_running_image_names", lambda: [])
        monkeypatch.setattr(relauncher, "_file_is_open_for_writing", lambda p: True)
        assert relauncher.installer_busy("Setup.exe", tmp_path / "alpha-osk.exe") is True
        monkeypatch.setattr(relauncher, "_file_is_open_for_writing", lambda p: False)
        assert relauncher.installer_busy("Setup.exe", tmp_path / "alpha-osk.exe") is False


class TestTheHelperTakesItsLock:
    def test_the_stage_is_locked_for_the_life_of_the_run(self, tmp_path, monkeypatch):
        from src import update_signals

        seen = {}

        def fake_splash(args, deadline=None):
            seen["held"] = update_signals.lock_is_held(tmp_path / update_signals.HELPER_LOCK_FILE)
            return 0

        monkeypatch.setattr(relauncher, "_run_with_splash", fake_splash)
        argv = [
            "x",
            "--parent-pid",
            "1",
            "--new-version",
            "9.9.9",
            "--target-exe",
            str(tmp_path / "alpha-osk.exe"),
            "--config-dir",
            str(tmp_path / "cfg"),
            "--signal-dir",
            str(tmp_path),
            "--show-splash",
        ]
        try:
            assert relauncher.run_relauncher(argv) == 0
            assert seen["held"] is True
        finally:
            update_signals.release_held_locks()


class TestNoEmDashesInTheLog:
    def test_the_relauncher_source_has_none(self):
        text = Path(relauncher.__file__).read_text(encoding="utf-8")
        assert "—" not in text, "relauncher.log lines (and the code) carry no em dashes"

    def test_the_startup_line_has_none(self, tmp_path, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="UpdateRelauncher"):
            relauncher.run_relauncher(
                [
                    "x",
                    "--parent-pid",
                    "1",
                    "--new-version",
                    "9.9.9",
                    "--target-exe",
                    "python.exe",
                    "--config-dir",
                    str(tmp_path),
                ]
            )
        assert caplog.records
        assert all("—" not in r.getMessage() for r in caplog.records)


_MOVE_SCRIPT = r"""
import argparse, os, sys
sys.path.insert(0, {repo!r})
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from src import _update_relauncher as r
from src.platform import windows_window as wm

app = QApplication([])
r._TICK_MS = 30
r._INSTALLER_GRACE_S = 0
r._DONE_DWELL_MS = 50
r._CARRY_TICK_MS = 10
tmp = {tmp!r}
signals = os.path.join(tmp, "signals")
os.makedirs(signals, exist_ok=True)
launched_marker = os.path.join(signals, "installer-launched")
r._process_alive = lambda pid: not os.path.exists(launched_marker)

class Shown:
    value = False
    def __call__(self): return Shown.value
    def close(self): pass
r._KeyboardShownProbe = Shown

def launch(target):
    Shown.value = True
    return True
r._launch_new_osk = launch
r._new_exe_ready = lambda *a, **k: True

cur = [(500, 400)]
moves, bands = [], []
if sys.platform == "win32":
    wm.cursor_position = lambda: cur[0]
    wm.hwnd_origin = lambda h: (200, 300)
    wm.window_size = lambda h: (440, 300)
    wm.monitor_rects = lambda: [(0, 0, 3000, 2000)]
    wm.move_window_noactivate = lambda h, x, y: (moves.append((x, y)), True)[1]
    wm.apply_extended_styles = (
        lambda w, taskbar_button=False, topmost=True: bands.append(("style", topmost))
    )
    wm.set_window_band = lambda h, t: (bands.append(("band", t)), True)[1]

args = argparse.Namespace(
    parent_pid=1, new_version="9.9.9", previous_version="9.9.8",
    target_exe=os.path.join(tmp, "alpha-osk.exe"), old_exe_mtime=0.0,
    config_dir=tmp, signal_dir=signals, anchor_rect="",
)
open(args.target_exe, "wb").write(b"x")

def splash():
    return [w for w in app.topLevelWidgets() if w.objectName() == "splash"][0]

report = {{}}

def step1():
    w = splash()
    move = w.findChild(QPushButton, "move")
    overlay = w.findChild(QWidget, "moveOverlay")
    report["move_visible"] = move.isVisibleTo(w)
    report["move_height"] = move.height()
    report["overlay_hidden_at_rest"] = not overlay.isVisibleTo(w)
    report["ui_shown_marker"] = os.path.exists(os.path.join(signals, "ui-shown"))
    report["flags_topmost_hint"] = bool(w.windowFlags() & Qt.WindowStaysOnTopHint)
    report["bands_while_approving"] = list(bands)
    move.click()
    report["overlay_shown_when_carrying"] = overlay.isVisibleTo(w)
    cur[0] = (600, 450)

def step2():
    w = splash()
    overlay = w.findChild(QWidget, "moveOverlay")
    report["carried_to"] = moves[-1] if moves else None
    overlay.on_click(Qt.LeftButton)
    report["overlay_hidden_after_put_down"] = not overlay.isVisibleTo(w)
    report["_n"] = len(moves)
    cur[0] = (900, 900)

def step3():
    w = splash()
    report["put_down_stays"] = len(moves) == report["_n"]
    w.findChild(QPushButton, "move").click()
    cur[0] = (1000, 1000)

def step4():
    w = splash()
    overlay = w.findChild(QWidget, "moveOverlay")
    overlay.on_click(Qt.RightButton)
    report["put_back_to"] = moves[-1] if moves else None
    report["overlay_hidden_after_put_back"] = not overlay.isVisibleTo(w)
    open(launched_marker, "wb").write(b"")

QTimer.singleShot(150, step1)
QTimer.singleShot(350, step2)
QTimer.singleShot(550, step3)
QTimer.singleShot(750, step4)
QTimer.singleShot(8000, lambda: (print("TIMEOUT"), os._exit(99)))
rc = r._run_with_splash(args)
report["rc"] = rc
report["bands"] = list(bands)
print("REPORT", report)
"""


def _run_move_script(tmp_path):
    script = _MOVE_SCRIPT.format(repo=REPO, tmp=str(tmp_path))
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


@pytest.fixture(scope="module")
def report(tmp_path_factory):
    return _run_move_script(tmp_path_factory.mktemp("move"))


win_only = pytest.mark.skipif(sys.platform != "win32", reason="driven through Win32 fakes")


@pytest.mark.skipif(not _has_widgets(), reason="needs PySide6 widgets")
class TestTheWindowMovesOffscreen:
    """Run in a child process on Qt's offscreen platform: nothing is shown."""

    def test_there_is_a_big_visible_move_button_and_no_cover_at_rest(self, report):
        assert report["move_visible"] is True
        assert report["move_height"] >= 48, "a large target for an imprecise pointer"
        assert report["overlay_hidden_at_rest"] is True

    def test_the_first_paint_is_announced_to_the_updater(self, report):
        assert report["ui_shown_marker"] is True

    def test_it_is_not_forced_topmost_by_a_qt_flag(self, report):
        assert report["flags_topmost_hint"] is False, (
            "the band follows the phase through Win32; a Qt flag would pin it"
        )

    def test_picking_it_up_covers_the_window_so_no_button_takes_the_click(self, report):
        assert report["overlay_shown_when_carrying"] is True

    @win_only
    def test_it_follows_the_pointer_with_no_button_held(self, report):
        # The window was at (200, 300); the pointer travelled (+100, +50).
        assert report["carried_to"] == (300, 350)

    @win_only
    def test_a_left_click_puts_it_down_and_it_stops_following(self, report):
        assert report["overlay_hidden_after_put_down"] is True
        assert report["put_down_stays"] is True

    @win_only
    def test_a_right_click_puts_it_back_where_it_was(self, report):
        assert report["put_back_to"] == (200, 300)
        assert report["overlay_hidden_after_put_back"] is True

    @win_only
    def test_it_waits_for_uac_below_the_topmost_band_and_rises_after(self, report):
        assert report["bands_while_approving"] == [("style", False)]
        assert ("band", True) in report["bands"], "topmost once the installer launched"

    def test_the_run_still_ends_normally(self, report):
        assert report["rc"] == 0


class TestTheSelfTest:
    """What the build runs, on a hidden desktop, against the staged helper."""

    def test_it_passes_in_a_normal_environment(self, tmp_path):
        pytest.importorskip("PySide6.QtWidgets")
        report = tmp_path / "report.txt"
        code = (
            f"import sys; sys.path.insert(0, {REPO!r});"
            "from src import _update_relauncher as r;"
            f"sys.exit(r.run_relauncher(['x', '--self-test', {str(report)!r}]))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stderr[-2000:]
        assert report.read_text(encoding="utf-8") == "ok"

    def test_it_reports_a_failure_instead_of_raising(self, tmp_path, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("no widgets today")

        monkeypatch.setattr(relauncher, "_build_splash_widget", boom)
        report = tmp_path / "report.txt"
        # The widget is built after the QApplication, which this process may
        # already hold as a QGuiApplication; the failure still has to be
        # reported, not raised.
        assert relauncher.run_self_test(str(report)) == 1
        assert report.read_text(encoding="utf-8")
