"""Detached helper that relaunches Alpha-OSK after an auto-update.

Background
==========

The auto-updater downloads + verifies + launches the signed NSIS
installer with elevation (UAC). The installer's ``customInit`` taskkills
the running ``alpha-osk.exe`` so the new exe can be written. Without a
relaunch, the user is left with no keyboard until they manually find
the Start Menu — a hard problem for the accessibility audience this
keyboard serves.

The previous mechanism was a one-line ``Exec '"$WINDIR\\explorer.exe"
"$INSTDIR\\alpha-osk.exe"'`` inside ``installer.nsh``. That trick
works in theory (explorer running at the user's medium IL spawns the
new exe at medium IL too) but in practice fails silently: the elevated
installer's ``Exec`` ends up handing off across the IL boundary, and
Windows can refuse the relay without surfacing any error. Result:
"the new keyboard never opens" — reported by users.

This module is the replacement. It runs as a detached process owned by
the user session (spawned by the updater BEFORE elevation kicks in),
polls for the install to finish, then launches the new exe directly.
Because the helper was already running at user IL when the elevated
installer started, there is no IL handoff to fail.

Flow
====

1. Wait for the parent ``alpha-osk.exe`` to exit (the installer's
   taskkill in ``customInit``).
2. Wait an extra grace period for the installer to finish writing
   files. Polling ``$INSTDIR\\alpha-osk.exe`` for an mtime newer than
   parent-death is the strongest signal we have without parsing PE
   headers; "exists + readable + non-zero size" is the floor.
3. Launch the new exe via ``subprocess.Popen`` from the user session.
4. Write ``update_handoff.json`` next to ``$APPDATA/alpha-osk/`` so the
   newly launched OSK can flash a "✓ Updated to vX.Y.Z" toast.

Failure modes are deliberately silent — there is no UI surface to
report into and the user already lacks a keyboard. Everything goes to
the relauncher log file at ``$APPDATA/alpha-osk/relauncher.log`` for
post-mortem.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

_logger = logging.getLogger("UpdateRelauncher")

# Polling cadence — fast enough to feel snappy, slow enough not to peg
# a CPU core. Total budget for the whole flow is ~3 minutes; in practice
# the install finishes inside 30 s.
_POLL_INTERVAL_S = 0.5
_PARENT_EXIT_TIMEOUT_S = 60
_NEW_EXE_TIMEOUT_S = 180
_INSTALLER_GRACE_S = 5  # after parent dies, wait for installer file copy

# Ceiling on the whole run, enforced by clamping each phase to what is
# left of it rather than by a watchdog thread.
#
# The phases already sum to 245 s, so this changes nothing today, and
# that is the point: it is here so that a future phase, or a phase whose
# timeout someone raises, cannot extend the total without saying so.
# This process is *detached* and has no console, so anything it fails to
# bound is invisible until someone opens a process list. There is no
# supervisor to notice, and no user-visible surface to complain into.
#
# It covers every *wait*, on both paths: the splash path's installer
# grace used to sit outside it as a bare QTimer, so the ceiling bound
# only the path the tests exercise and not the one production runs.
# The dwells below are deliberately outside it -- they are display time
# after the outcome is already decided, and clamping them would cut the
# message the user is meant to read rather than shorten any waiting.
_MAX_TOTAL_RUNTIME_S = 300

# Splash-window dwell times. The "Done!" pause hides the brief gap
# between us closing the splash and the new OSK drawing its first
# frame; without it the user still sees a flash of nothing. The
# failure dwell keeps an error message visible long enough to read.
_DONE_DWELL_MS = 800
_FAILURE_DWELL_MS = 6000


def _configure_log(log_dir: Path) -> None:
    """Set up a file logger for the detached process.

    Stdout/stderr aren't visible (the helper runs hidden), so log
    aggressively to a known path. Failures during log setup are
    swallowed — there's no fallback surface.
    """
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "relauncher.log", encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s",
            )
        )
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    except Exception:
        # No fallback log surface for the relauncher (no console attached
        # in detached mode); swallow so a logging-init failure never kills
        # the relauncher itself.
        pass


def _process_alive(pid: int) -> bool:
    """Cross-platform "is this PID still around" check.

    Uses ``OpenProcess`` on Windows (the cheapest signal) and
    ``os.kill(pid, 0)`` on POSIX. Returns False on any error — a dead
    process is the safer assumption since we want the relauncher to
    proceed once the OSK is gone.
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                pid,
            )
            if not handle:
                return False
            # GetExitCodeProcess returns STILL_ACTIVE (259) for a live
            # process; any other value means it has exited.
            STILL_ACTIVE = 259
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel32.CloseHandle(handle)
            if not ok:
                return False
            return exit_code.value == STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _remaining(overall_deadline: float, phase_timeout: float) -> float:
    """This phase's budget, clipped to what is left of the whole run.

    Never negative: a phase that starts with nothing left gets 0, which
    every wait here treats as "check once, then give up" rather than as
    "wait forever".  That is a property of the waits, not of this
    arithmetic: see the do-while in :func:`_wait_for_parent_exit`.
    """
    return max(0.0, min(phase_timeout, overall_deadline - time.monotonic()))


def _installer_grace_s(overall_deadline: float) -> float:
    """The post-parent-death pause, clipped to the run's remaining budget.

    A named helper rather than an inline ``_remaining`` call because
    both paths have to apply it and only one did: the splash path's
    grace was a bare ``QTimer.singleShot(_INSTALLER_GRACE_S * 1000)``
    outside the ceiling, so raising that constant would have extended
    the total on the path production runs while the path the tests
    exercise stayed inside it.
    """
    return _remaining(overall_deadline, _INSTALLER_GRACE_S)


def _wait_for_parent_exit(pid: int, timeout_s: float) -> bool:
    """Block until the parent OSK process has exited or we time out.

    The check comes before the clock, so a zero budget still gets one
    look.  Written as ``while time.monotonic() < deadline`` it skipped
    the body outright whenever ``_remaining`` had clamped the budget to
    0, which reports a parent that is already dead as still alive and
    aborts the relaunch: the user is left with no keyboard at the one
    moment there is nothing to fall back on.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if not _process_alive(pid):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_INTERVAL_S)


def _wait_for_new_exe(
    target: Path,
    after_mtime: Optional[float],
    timeout_s: float,
) -> bool:
    """Block until ``target`` exists and looks like the freshly-written exe.

    ``after_mtime`` is the parent OSK's death time; an exe whose mtime
    predates that is the OLD exe (installer hasn't finished). Waiting
    for ``mtime > after_mtime`` is a much stronger signal than just
    "file exists." If we don't have a death time, fall back to the
    weaker existence-and-non-empty check.

    Checks before the clock for the same reason as
    ``_wait_for_parent_exit``: a budget already clamped to 0 must still
    stat the file once.  An installer that finished while an earlier
    phase overran is exactly the case where the answer is "yes, it is
    there", and skipping the look reports it as never having arrived.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            if target.is_file():
                stat = target.stat()
                if stat.st_size > 0:
                    if after_mtime is None or stat.st_mtime > after_mtime:
                        return True
        except OSError:
            # Transient stat race against the installer mid-write; retry
            # on the next poll tick rather than aborting the wait.
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_INTERVAL_S)


def _launch_new_osk(exe_path: Path) -> bool:
    """Spawn the freshly-installed ``alpha-osk.exe`` as a detached process.

    Returns True on launch success (i.e. ``Popen`` didn't raise). Note
    that "spawn succeeded" is not "OSK is running" — but if Popen fails
    we know to log the error rather than silently exiting.
    """
    try:
        flags = 0
        if sys.platform == "win32":
            # Detach so we can exit immediately. CREATE_NEW_PROCESS_GROUP
            # also prevents Ctrl+C in any future console attach from
            # bubbling into the new OSK.
            flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        subprocess.Popen(
            [str(exe_path)],
            creationflags=flags,
            close_fds=True,
            cwd=str(exe_path.parent),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.error("Failed to launch %s: %s", exe_path, exc)
        return False


def _write_handoff(
    config_dir: Path,
    new_version: str,
    previous_version: str,
) -> None:
    """Drop the breadcrumb the new OSK reads to surface its toast.

    Format is forward-compatible — adding fields is fine, but the new
    OSK must tolerate missing fields since users can update across
    multiple versions.
    """
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": new_version,
            "previous_version": previous_version,
            "completed_at": time.time(),
        }
        path = config_dir / "update_handoff.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Failed to write handoff file: %s", exc)


def run_relauncher(argv: list[str]) -> int:
    """CLI entry point. Returns a process exit code (0 = success).

    Dispatches between two implementations:

    * ``--show-splash`` (production): drives the same wait phases via
      a QTimer state machine so a small "Updating Alpha-OSK…" window
      can stay painted on screen during the gap, with a phase-aware
      message ("Waiting for installer to finish…" → "Installing
      files…" → "Launching new keyboard…"). Without this window, the
      user has no UI between the installer's taskkill and the new OSK
      drawing its first frame, which can be ~30 s of total silence.
    * default (tests + fallback): the original blocking-poll
      implementation. Tests target this path so they don't have to
      stand up a QApplication.

    If the splash path fails to start (e.g. PySide6 import error, no
    display server), we log and fall back to headless rather than
    aborting the relaunch.
    """
    parser = argparse.ArgumentParser(prog="alpha-osk --update-relauncher")
    parser.add_argument("--update-relauncher", action="store_true")
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--new-version", type=str, required=True)
    parser.add_argument("--previous-version", type=str, default="")
    parser.add_argument("--target-exe", type=str, required=True)
    parser.add_argument("--config-dir", type=str, required=True)
    parser.add_argument("--show-splash", action="store_true")
    args = parser.parse_args(argv[1:])

    config_dir = Path(args.config_dir)
    _configure_log(config_dir)
    _logger.info(
        "Relauncher starting — parent_pid=%d new_version=%s target=%s splash=%s",
        args.parent_pid,
        args.new_version,
        args.target_exe,
        args.show_splash,
    )

    # The whole-run ceiling is taken once, here, and handed to whichever
    # path runs. Derived inside each path instead, a splash that raised
    # after four minutes of waiting handed the headless fallback a fresh
    # 300 s, and the documented ceiling was quietly a 600 s one.
    overall_deadline = time.monotonic() + _MAX_TOTAL_RUNTIME_S

    # Nothing is going to arrive, so do not wait to find that out. A
    # dev-mode spawn points --target-exe at the python interpreter
    # running the OSK (see _is_dev_target), which means there is no
    # installer, no new exe, and no mtime that can ever advance past the
    # parent's death. Left to run, the new-exe wait burned its full
    # _NEW_EXE_TIMEOUT_S every time and *then* reported failure, leaving
    # a detached, console-less process alive for ~185 s after every
    # dev-mode update attempt: the stranded processes TODO.md recorded.
    #
    # Decided here, before either path starts, because it depends only
    # on argv, which is fixed by the time we are called. Tested after
    # the parent-exit wait, as it first was, a parent slow to die still
    # bought a 60 s stranded process for a target we already knew we
    # would never relaunch.
    #
    # It returns without launching, deliberately. The launch would be
    # `python.exe` with no arguments, which is not the keyboard and is
    # its own stranded-process risk. 0 rather than an error code
    # because "there was nothing here to relaunch" is the correct
    # outcome for a dev target, not a failure.
    if _is_dev_target(args.target_exe):
        _logger.info("Dev-mode target (%s); no installer to wait for, exiting", args.target_exe)
        return 0

    if args.show_splash:
        try:
            return _run_with_splash(args, overall_deadline)
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Splash path raised (%s); falling back to headless",
                exc,
            )
            # Fall through to the headless path. Better to relaunch
            # the OSK silently than to leave the user with nothing. It
            # continues the ceiling above rather than starting one.
    return _run_headless(args, overall_deadline)


def _is_dev_target(target_exe: str) -> bool:
    """Detect dev-mode invocation: target_exe pointing at a python
    interpreter rather than an installed alpha-osk.exe.

    In dev mode the relauncher is spawned by ``updater._spawn_relauncher``
    with ``--target-exe sys.executable`` (the python that's running the
    OSK), since there's no real install dir to poll. Production
    spawns it with the installed alpha-osk.exe path. Matching on
    ``python`` / ``pythonw`` in the basename is enough: there's no
    realistic case where a real install lives at a path containing
    ``python`` in the exe name.

    Normalises backslashes to forward slashes before splitting so the
    function gives the same answer on Linux (where tests run) as on
    Windows (where production runs). Without that, `Path` on POSIX
    treats the whole `C:\\...\\python.exe` string as a single name.
    """
    normalised = target_exe.replace("\\", "/")
    name = Path(normalised).name.lower()
    return name.startswith("python") or name.startswith("pythonw")


def _run_headless(args: argparse.Namespace, overall_deadline: Optional[float] = None) -> int:
    """Original blocking-poll relauncher. Used by tests and as the
    splash-path fallback. See ``run_relauncher`` for the contract.

    ``overall_deadline`` is the whole-run ceiling, passed in so that a
    splash which raised part way through does not hand this path a
    fresh one. Defaulted only for direct callers (the tests); the real
    dispatch always supplies it.
    """
    config_dir = Path(args.config_dir)
    if overall_deadline is None:
        overall_deadline = time.monotonic() + _MAX_TOTAL_RUNTIME_S

    # Log the budget actually waited, never the nominal constant. With
    # the ceiling engaged the two differ, and this log is the only
    # post-mortem surface a detached, console-less process has: "still
    # alive after 60s" for a 12 s wait sends the next reader looking for
    # a hang that never happened.
    parent_budget = _remaining(overall_deadline, _PARENT_EXIT_TIMEOUT_S)
    if not _wait_for_parent_exit(args.parent_pid, parent_budget):
        _logger.error("Parent OSK still alive after %.0fs, giving up", parent_budget)
        return 2

    parent_death_time = time.time()

    grace_s = _installer_grace_s(overall_deadline)
    _logger.info("Parent OSK exited; waiting %.0fs for installer file copy", grace_s)
    time.sleep(grace_s)

    target_exe = Path(args.target_exe)
    new_exe_budget = _remaining(overall_deadline, _NEW_EXE_TIMEOUT_S)
    if not _wait_for_new_exe(target_exe, parent_death_time, new_exe_budget):
        _logger.error("New exe never appeared at %s within %.0fs", target_exe, new_exe_budget)
        return 3

    _logger.info("New exe ready at %s — launching", target_exe)
    if not _launch_new_osk(target_exe):
        return 4

    _write_handoff(config_dir, args.new_version, args.previous_version)
    _logger.info("Relauncher done")
    return 0


def _new_exe_ready(target: Path, after_mtime: Optional[float]) -> bool:
    """Single-shot version of ``_wait_for_new_exe``. Returns True if the
    new exe is in place right now. Used by the QTimer-driven splash
    path so we can yield back to the event loop between checks."""
    try:
        if not target.is_file():
            return False
        stat = target.stat()
        if stat.st_size <= 0:
            return False
        if after_mtime is None:
            return True
        return stat.st_mtime > after_mtime
    except OSError:
        return False


def _run_with_splash(args: argparse.Namespace, overall_deadline: Optional[float] = None) -> int:
    """Splash-window implementation. Drives the same waits as the
    headless path but via QTimer ticks so the window can repaint and
    show phase-aware progress text.

    ``overall_deadline`` is the whole-run ceiling; see ``_run_headless``
    for why it is passed in rather than derived here. Bound to a local
    because the QTimer closures below read it, and narrowing an
    ``Optional`` does not reach into a nested function.
    """
    ceiling = (
        time.monotonic() + _MAX_TOTAL_RUNTIME_S if overall_deadline is None else overall_deadline
    )
    # Lazy-import Qt so the headless path stays import-clean and
    # tests don't accidentally drag PySide6 into a fresh interpreter.
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QLabel,
        QProgressBar,
        QVBoxLayout,
        QWidget,
    )

    config_dir = Path(args.config_dir)
    target_exe = Path(args.target_exe)

    existing_app = QApplication.instance()
    app = existing_app if isinstance(existing_app, QApplication) else QApplication([])

    splash = _build_splash_widget(
        QWidget,
        QFrame,
        QLabel,
        QProgressBar,
        QVBoxLayout,
        QFont,
        Qt,
    )
    # The close button hides the splash but lets the polling continue —
    # the user is dismissing the visual, not aborting the relaunch.
    # The new OSK still gets launched when the install completes; the
    # app.quit() in _finish ends the process cleanly. If the user
    # closes during a terminal phase (Done / failure dwell) the timer
    # already scheduled will quit the app shortly after.
    close_btn = splash.findChild(QWidget, "close")
    if close_btn is not None:
        close_btn.mousePressEvent = lambda ev: splash.hide()  # type: ignore[assignment]

    splash.show()
    # Centre on the primary screen — frameless windows don't get a
    # default position, so we'd otherwise land at (0, 0).
    screen = app.primaryScreen()
    if screen is not None:
        geo = screen.availableGeometry()
        splash.move(
            geo.x() + (geo.width() - splash.width()) // 2,
            geo.y() + (geo.height() - splash.height()) // 3,
        )

    # Mutable state held by the QTimer-driven state machine. A small
    # class beats a dict here: typed fields keep mypy happy and the
    # closure reads (`state.exit_code`) are clearer than dict lookups.
    class _SplashState:
        exit_code: int = 0
        parent_death_time: Optional[float] = None
        deadline: float = 0.0
        # What each phase was actually given, which is the nominal
        # timeout only while the ceiling has room. The give-up logs
        # report these rather than the constants: this process has no
        # console, so the log is the only post-mortem there is.
        parent_budget: float = 0.0
        new_exe_budget: float = 0.0

    state = _SplashState()

    def _set_message(text: str) -> None:
        label = splash.findChild(QLabel, "msg")
        if label is not None:
            label.setText(text)
        # Force a repaint immediately — QTimer ticks are short enough
        # that the natural paint cycle is fine, but during the
        # transitions between phases we want the message to swap
        # before any further processing happens.
        splash.repaint()

    def _settle_progress(full: bool) -> None:
        """Stop the marquee and pin the bar full or empty.

        Called on terminal phases (Done / failure) so the bar visibly
        stops moving. Without this, a successful update would close the
        splash with the marquee still sliding, which reads as "still
        working" the instant before the window vanishes.
        """
        bar = splash.findChild(QProgressBar, "progress")
        if bar is not None:
            bar.setRange(0, 1)
            bar.setValue(1 if full else 0)
            splash.repaint()

    def _finish(code: int) -> None:
        state.exit_code = code
        QTimer.singleShot(0, app.quit)

    def _poll_parent() -> None:
        if not _process_alive(args.parent_pid):
            state.parent_death_time = time.time()
            _set_message("Installing files…")
            QTimer.singleShot(int(_installer_grace_s(ceiling) * 1000), _start_new_exe_phase)
            return
        if time.monotonic() >= state.deadline:
            _logger.error("Parent OSK still alive after %.0fs, giving up", state.parent_budget)
            _finish(2)
            return
        QTimer.singleShot(int(_POLL_INTERVAL_S * 1000), _poll_parent)

    def _start_new_exe_phase() -> None:
        state.new_exe_budget = _remaining(ceiling, _NEW_EXE_TIMEOUT_S)
        state.deadline = time.monotonic() + state.new_exe_budget
        QTimer.singleShot(0, _poll_new_exe)

    def _poll_new_exe() -> None:
        if _new_exe_ready(target_exe, state.parent_death_time):
            _launch()
            return
        if time.monotonic() >= state.deadline:
            _logger.error(
                "New exe never appeared at %s within %.0fs", target_exe, state.new_exe_budget
            )
            _set_message(
                "Update finished, but the keyboard didn't appear.\n"
                "Find Alpha-OSK in your Start Menu."
            )
            _settle_progress(full=False)
            QTimer.singleShot(_FAILURE_DWELL_MS, lambda: _finish(3))
            return
        QTimer.singleShot(int(_POLL_INTERVAL_S * 1000), _poll_new_exe)

    def _launch() -> None:
        _set_message("Launching the new keyboard…")
        if not _launch_new_osk(target_exe):
            _logger.error("Launch failed")
            _set_message("Couldn't launch the new keyboard.\nFind Alpha-OSK in your Start Menu.")
            _settle_progress(full=False)
            QTimer.singleShot(_FAILURE_DWELL_MS, lambda: _finish(4))
            return
        _write_handoff(config_dir, args.new_version, args.previous_version)
        # Brief "Done" pause so the splash doesn't vanish a frame
        # before the new OSK draws its first window — otherwise
        # there's still a visible blank moment.
        _set_message("Done!")
        _settle_progress(full=True)
        QTimer.singleShot(_DONE_DWELL_MS, lambda: _finish(0))

    state.parent_budget = _remaining(ceiling, _PARENT_EXIT_TIMEOUT_S)
    state.deadline = time.monotonic() + state.parent_budget
    _set_message("Waiting for the installer to finish…")
    QTimer.singleShot(0, _poll_parent)

    app.exec()
    _logger.info("Relauncher splash finished with code %d", state.exit_code)
    return state.exit_code


def _build_splash_widget(QWidget, QFrame, QLabel, QProgressBar, QVBoxLayout, QFont, Qt):
    """Construct the splash window. Pulled out to keep ``_run_with_splash``
    short — and to make the styling tweakable in one place."""
    win = QWidget()
    win.setWindowTitle("Updating Alpha-OSK")
    win.setWindowFlags(
        Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
    )
    win.setAttribute(Qt.WA_ShowWithoutActivating, True)
    # Slightly taller than the original 140 px to accommodate the
    # indeterminate progress bar below the message. Without the extra
    # row the bar overlaps the message text.
    win.setFixedSize(420, 170)
    # Match the in-app toast colour (#1e3354 on #4a8eff border) so the
    # splash visually belongs to Alpha-OSK rather than looking like a
    # stray system dialog.
    win.setStyleSheet(
        "QWidget { background-color: #1e3354; }"
        "QLabel#title { color: #7ec8ff; font-weight: bold; }"
        "QLabel#msg { color: #cfe0ff; }"
        "QLabel#close { color: #7ec8ff; }"
        "QLabel#close:hover { color: #ffffff; background-color: #2a4570; border-radius: 4px; }"
        # Indeterminate marquee bar. NSIS silent (/S) install gives us
        # no real percentage to report, but a moving bar is the
        # difference between "is it stuck?" and "still working" — every
        # commercial installer ships some motion during the silent phase.
        "QProgressBar { background-color: #14233a; border: 1px solid #2a4570;"
        " border-radius: 4px; height: 10px; }"
        "QProgressBar::chunk { background-color: #4a8eff; border-radius: 3px; }"
    )

    frame = QFrame(win)
    frame.setStyleSheet("QFrame { border: 1px solid #4a8eff; border-radius: 8px; }")
    frame.setGeometry(0, 0, 420, 170)

    # Close button — top-right corner. The user can dismiss the splash
    # if it ever gets stuck (network glitch during install, AV scanning
    # the new exe forever, dev-mode test). Clicking only HIDES the
    # window — the relauncher keeps polling and still launches the new
    # OSK when ready, since "I don't need to look at this" is different
    # from "abort the relaunch". See _run_with_splash.
    close = QLabel("✕", win)
    close.setObjectName("close")
    close_font = QFont()
    close_font.setPointSize(11)
    close_font.setBold(True)
    close.setFont(close_font)
    close.setAlignment(Qt.AlignCenter)
    close.setFixedSize(22, 22)
    close.move(420 - 22 - 10, 8)
    close.setCursor(Qt.PointingHandCursor)
    close.setToolTip("Hide this window (the keyboard will still come back)")

    layout = QVBoxLayout(win)
    layout.setContentsMargins(20, 18, 20, 18)
    layout.setSpacing(8)

    title = QLabel("Updating Alpha-OSK", win)
    title.setObjectName("title")
    title_font = QFont()
    title_font.setPointSize(13)
    title_font.setBold(True)
    title.setFont(title_font)
    title.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
    layout.addWidget(title)

    msg = QLabel("", win)
    msg.setObjectName("msg")
    msg_font = QFont()
    msg_font.setPointSize(10)
    msg.setFont(msg_font)
    msg.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
    msg.setWordWrap(True)
    layout.addWidget(msg)

    # Indeterminate (marquee) progress bar. setRange(0, 0) puts Qt's
    # built-in progress bar into a busy/marquee state where the chunk
    # slides back and forth without representing real percentage. We
    # cannot get a real % out of the silent NSIS installer (it suppresses
    # its own UI under /S), but constant motion still tells the user
    # the relauncher is alive and the install hasn't hung.
    progress = QProgressBar(win)
    progress.setObjectName("progress")
    progress.setRange(0, 0)
    progress.setTextVisible(False)
    progress.setFixedHeight(10)
    layout.addWidget(progress)

    # Raise the close label above the layout-managed children so it
    # always sits on top. The QLabel is a sibling of the layout host,
    # not part of the layout.
    close.raise_()

    return win


if __name__ == "__main__":  # pragma: no cover — CLI entry
    sys.exit(run_relauncher(sys.argv))
