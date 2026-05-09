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
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
        ))
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
    except Exception:
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
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid,
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


def _wait_for_parent_exit(pid: int, timeout_s: float) -> bool:
    """Block until the parent OSK process has exited or we time out."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _process_alive(pid):
            return True
        time.sleep(_POLL_INTERVAL_S)
    return False


def _wait_for_new_exe(
    target: Path, after_mtime: Optional[float], timeout_s: float,
) -> bool:
    """Block until ``target`` exists and looks like the freshly-written exe.

    ``after_mtime`` is the parent OSK's death time; an exe whose mtime
    predates that is the OLD exe (installer hasn't finished). Waiting
    for ``mtime > after_mtime`` is a much stronger signal than just
    "file exists." If we don't have a death time, fall back to the
    weaker existence-and-non-empty check.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if target.is_file():
                stat = target.stat()
                if stat.st_size > 0:
                    if after_mtime is None or stat.st_mtime > after_mtime:
                        return True
        except OSError:
            pass
        time.sleep(_POLL_INTERVAL_S)
    return False


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
            flags = (
                getattr(subprocess, "DETACHED_PROCESS", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
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
        args.parent_pid, args.new_version, args.target_exe, args.show_splash,
    )

    if args.show_splash:
        try:
            return _run_with_splash(args)
        except Exception as exc:                              # noqa: BLE001
            _logger.warning(
                "Splash path raised (%s); falling back to headless", exc,
            )
            # Fall through to the headless path. Better to relaunch
            # the OSK silently than to leave the user with nothing.
    return _run_headless(args)


def _run_headless(args: argparse.Namespace) -> int:
    """Original blocking-poll relauncher. Used by tests and as the
    splash-path fallback. See ``run_relauncher`` for the contract."""
    config_dir = Path(args.config_dir)

    if not _wait_for_parent_exit(args.parent_pid, _PARENT_EXIT_TIMEOUT_S):
        _logger.error("Parent OSK still alive after %.0fs — giving up",
                      _PARENT_EXIT_TIMEOUT_S)
        return 2

    parent_death_time = time.time()
    _logger.info("Parent OSK exited; waiting %.0fs for installer file copy",
                 _INSTALLER_GRACE_S)
    time.sleep(_INSTALLER_GRACE_S)

    target_exe = Path(args.target_exe)
    if not _wait_for_new_exe(target_exe, parent_death_time, _NEW_EXE_TIMEOUT_S):
        _logger.error("New exe never appeared at %s within %.0fs",
                      target_exe, _NEW_EXE_TIMEOUT_S)
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


def _run_with_splash(args: argparse.Namespace) -> int:
    """Splash-window implementation. Drives the same waits as the
    headless path but via QTimer ticks so the window can repaint and
    show phase-aware progress text."""
    # Lazy-import Qt so the headless path stays import-clean and
    # tests don't accidentally drag PySide6 into a fresh interpreter.
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QLabel,
        QVBoxLayout,
        QWidget,
    )

    config_dir = Path(args.config_dir)
    target_exe = Path(args.target_exe)

    existing_app = QApplication.instance()
    app = existing_app if isinstance(existing_app, QApplication) else QApplication([])

    splash = _build_splash_widget(QWidget, QFrame, QLabel, QVBoxLayout, QFont, Qt)
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

    def _finish(code: int) -> None:
        state.exit_code = code
        QTimer.singleShot(0, app.quit)

    def _poll_parent() -> None:
        if not _process_alive(args.parent_pid):
            state.parent_death_time = time.time()
            _set_message("Installing files…")
            QTimer.singleShot(int(_INSTALLER_GRACE_S * 1000), _start_new_exe_phase)
            return
        if time.monotonic() >= state.deadline:
            _logger.error("Parent OSK still alive after %.0fs — giving up",
                          _PARENT_EXIT_TIMEOUT_S)
            _finish(2)
            return
        QTimer.singleShot(int(_POLL_INTERVAL_S * 1000), _poll_parent)

    def _start_new_exe_phase() -> None:
        state.deadline = time.monotonic() + _NEW_EXE_TIMEOUT_S
        QTimer.singleShot(0, _poll_new_exe)

    def _poll_new_exe() -> None:
        if _new_exe_ready(target_exe, state.parent_death_time):
            _launch()
            return
        if time.monotonic() >= state.deadline:
            _logger.error("New exe never appeared at %s within %.0fs",
                          target_exe, _NEW_EXE_TIMEOUT_S)
            _set_message("Update finished, but the keyboard didn't appear.\n"
                         "Find Alpha-OSK in your Start Menu.")
            QTimer.singleShot(_FAILURE_DWELL_MS, lambda: _finish(3))
            return
        QTimer.singleShot(int(_POLL_INTERVAL_S * 1000), _poll_new_exe)

    def _launch() -> None:
        _set_message("Launching the new keyboard…")
        if not _launch_new_osk(target_exe):
            _logger.error("Launch failed")
            _set_message("Couldn't launch the new keyboard.\n"
                         "Find Alpha-OSK in your Start Menu.")
            QTimer.singleShot(_FAILURE_DWELL_MS, lambda: _finish(4))
            return
        _write_handoff(config_dir, args.new_version, args.previous_version)
        # Brief "Done" pause so the splash doesn't vanish a frame
        # before the new OSK draws its first window — otherwise
        # there's still a visible blank moment.
        _set_message("Done!")
        QTimer.singleShot(_DONE_DWELL_MS, lambda: _finish(0))

    state.deadline = time.monotonic() + _PARENT_EXIT_TIMEOUT_S
    _set_message("Waiting for the installer to finish…")
    QTimer.singleShot(0, _poll_parent)

    app.exec()
    _logger.info("Relauncher splash finished with code %d", state.exit_code)
    return state.exit_code


def _build_splash_widget(QWidget, QFrame, QLabel, QVBoxLayout, QFont, Qt):
    """Construct the splash window. Pulled out to keep ``_run_with_splash``
    short — and to make the styling tweakable in one place."""
    win = QWidget()
    win.setWindowTitle("Updating Alpha-OSK")
    win.setWindowFlags(
        Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
        | Qt.Tool
        | Qt.WindowDoesNotAcceptFocus
    )
    win.setAttribute(Qt.WA_ShowWithoutActivating, True)
    win.setFixedSize(420, 140)
    # Match the in-app toast colour (#1e3354 on #4a8eff border) so the
    # splash visually belongs to Alpha-OSK rather than looking like a
    # stray system dialog.
    win.setStyleSheet(
        "QWidget { background-color: #1e3354; }"
        "QLabel#title { color: #7ec8ff; font-weight: bold; }"
        "QLabel#msg { color: #cfe0ff; }"
    )

    frame = QFrame(win)
    frame.setStyleSheet(
        "QFrame { border: 1px solid #4a8eff; border-radius: 8px; }"
    )
    frame.setGeometry(0, 0, 420, 140)

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

    return win


if __name__ == "__main__":  # pragma: no cover — CLI entry
    sys.exit(run_relauncher(sys.argv))
