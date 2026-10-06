"""Detached helper that shows the update screen and relaunches Alpha-OSK.

Background
==========

The auto-updater downloads + verifies + launches the signed NSIS
installer with elevation (UAC). The installer's ``customInit`` taskkills
the running ``alpha-osk.exe`` so the new exe can be written. Without a
relaunch, and without anything on screen, the user is left with no
keyboard and no sign that one is coming back, until they manually find
the Start Menu: a hard problem for the accessibility audience this
keyboard serves. The requirement this module exists to meet is an update
screen that is on screen **from the moment the keyboard goes away until the
new keyboard window is actually visible**, so it never looks like the
keyboard is not coming back.

This helper is its own executable, ``alpha-osk-relauncher.exe``, built
without UIAccess (see ``build/windows/alpha-osk.spec``). The main exe
requests ``uiAccess="true"``, and Windows refuses to start such an image
from outside a secure location (WinError 740), so the original design, a
renamed copy of the main exe run from ``%TEMP%``, never started once 1.6.0
began requesting UIAccess. A plain exe has no such restriction.

Flow
====

The splash window opens at once, centred on the keyboard it replaces, and
walks four phases (``UpdateFlow``):

1. **Approve**: "Waiting for you to approve the update". The updater
   spawns this helper *before* the UAC prompt, so the prompt can take as
   long as the user needs. The parent marks the moment the installer
   actually launched (``update_signals.INSTALLER_LAUNCHED_FILE``), and
   every timeout below starts counting from that mark, not from the spawn.
   A declined prompt or a failed launch writes a cancel marker instead and
   the helper leaves without a word (the keyboard's own toast reports it).
2. **Closing**: the parent keyboard exits (the installer's taskkill).
3. **Installing**: wait a grace period, then for the installed exe to be
   different from the pre-install snapshot (``_new_exe_looks_fresh``).
   Newer-than-parent-death was tried and can never fire: NSIS restores each
   extracted file's build-time timestamp (``SetDateSave``).
4. **Starting**: launch the new exe through ``explorer.exe``
   (``_launch_command``: a UIAccess exe started by anything other than
   Explorer comes up without UIAccess), then wait for the new keyboard to
   announce that its window is on screen (the named event in
   ``update_signals``), not merely that its process exists.

Then "Done", briefly, and ``update_handoff.json`` is written so the new
keyboard can flash its "Updated" toast.

A failure never closes the window on a timer. It stays up with a message
and large buttons (Start Alpha-OSK, Open log folder, Close) until the user
acts, because a message that vanishes after six seconds is the same as no
message for someone who reads and clicks slowly.

``--show-splash`` absent (tests, and the fallback when Qt cannot start)
runs the same waits headless, with no UI. Everything goes to
``$APPDATA/alpha-osk/relauncher.log`` for post-mortem.
"""

from __future__ import annotations

import argparse
import enum
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from . import update_signals

_logger = logging.getLogger("UpdateRelauncher")

# Polling cadence — fast enough to feel snappy, slow enough not to peg
# a CPU core. Total budget for the whole flow is ~3 minutes; in practice
# the install finishes inside 30 s.
_POLL_INTERVAL_S = 0.5
_PARENT_EXIT_TIMEOUT_S = 60
_NEW_EXE_TIMEOUT_S = 180
_INSTALLER_GRACE_S = 5  # after parent dies, wait for installer file copy

# After the launch: how long the new keyboard's process has to appear,
# and how often we look. A successful Popen only proves Explorer started
# (see _launch_command); the keyboard is Explorer's child, not ours, so
# nothing else tells us it came up. The relay itself is quick, so 15 s is
# headroom for whatever can delay the first start of an exe written
# seconds ago (an antivirus scan is the likely one). It waits for the
# process to exist, not for its window, so the keyboard's own few seconds
# of startup are not inside it. Each look is a whole-system process
# snapshot, hence 250 ms steps rather than a tight loop.
_NEW_OSK_APPEAR_TIMEOUT_S = 15
_NEW_OSK_POLL_INTERVAL_S = 0.25

# Ceiling on the whole run, enforced by clamping each phase to what is
# left of it rather than by a watchdog thread.
#
# The phases already sum to 260 s, so this changes nothing today, and
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

# How long the user may take over the UAC prompt before the helper stops
# waiting for the parent to say the installer launched. The updater's own
# ShellExecuteW call blocks until the prompt is answered, so this only
# bounds a parent that died without telling us.
_APPROVAL_TIMEOUT_S = 600

# How long the new keyboard's window has to appear once it is launched. The
# window, not the process: a cold start of a freshly written exe can sit in
# an antivirus scan first, and the keyboard builds its UI before showing it.
_KEYBOARD_SHOWN_TIMEOUT_S = 60

# The splash's state machine and its topmost re-assertion tick on these.
_TICK_MS = 250
_REASSERT_TOP_MS = 1000

# The "Done" pause is display time after the outcome is decided, so it is
# outside the ceiling like every other dwell.
_DONE_DWELL_MS = 800

# Exit codes. 0 is success.
EXIT_PARENT_STUCK = 2
EXIT_NEW_EXE_MISSING = 3
EXIT_LAUNCH_FAILED = 4
EXIT_CANCELLED = 5
EXIT_NOT_APPROVED = 6


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


def _new_exe_looks_fresh(
    target: Path,
    old_mtime: float,
    after_mtime: Optional[float],
) -> bool:
    """Does ``target`` look like the freshly-installed exe right now?

    The strong signal is ``old_mtime``, the installed exe's mtime as the
    updater snapshotted it immediately before handing off to the
    installer: a completed install guarantees the mtime is *different*,
    not newer.  NSIS restores each extracted file's build-machine
    timestamp (``SetDateSave`` defaults on), so the check this replaces,
    ``mtime > parent-death-time``, could never fire in production: the
    fresh exe's mtime predates the install by however old the build is,
    and every real update burned the full new-exe timeout and then
    reported failure while the installer's explorer fallback quietly did
    the relaunch.  Two builds never share a timestamp, so inequality
    against the snapshot is exact; the one case it cannot see is a
    reinstall of the *same* build, which the auto-updater never performs
    (it only ever moves to a newer version).

    Without a snapshot (``old_mtime <= 0``) fall back to the legacy
    comparison, so a helper spawned by an updater too old to pass one
    behaves as it always did.

    Transient ``OSError`` reads as "not yet": the installer may be
    mid-write, and the caller polls.

    **A changed mtime is not a finished file.** While NSIS is still writing
    the exe its mtime is the write time (the build stamp is set only once
    the data is in) and its size is already non-zero, so every rule above
    says "ready" for the whole of the write. The exe is several megabytes of
    LZMA output and the poll is 4 Hz, so a poll landing inside that window
    is likely rather than rare, and launching it then hands Explorer a
    half-written image, which it answers with a modal error box. So a file
    some other handle still holds open for writing is never ready
    (:func:`_file_is_open_for_writing`).
    """
    try:
        if not target.is_file():
            return False
        stat = target.stat()
        if stat.st_size <= 0:
            return False
        if old_mtime > 0:
            changed = abs(stat.st_mtime - old_mtime) > 1e-6
        elif after_mtime is None:
            changed = True
        else:
            changed = stat.st_mtime > after_mtime
        return changed and not _file_is_open_for_writing(target)
    except OSError:
        return False


_ERROR_SHARING_VIOLATION = 32


def _file_is_open_for_writing(path: Path) -> bool:
    """Does another handle hold ``path`` open for writing right now?  Windows only.

    Asked by opening the file for reading while sharing *only* reading: the
    open is refused with a sharing violation exactly when some existing
    handle has write (or delete) access, which is what the installer holds
    while it extracts the file. Python's own ``open`` cannot ask this, since
    it shares everything. Any other failure reads as "not being written",
    which is the behaviour the readiness rule had before this check, so a
    probe that cannot run never blocks a relaunch outright.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        generic_read = 0x80000000
        file_share_read = 0x00000001
        open_existing = 3
        invalid_handle = ctypes.c_void_p(-1).value
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateFileW.restype = wintypes.HANDLE
        kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.CreateFileW(
            str(path), generic_read, file_share_read, None, open_existing, 0, None
        )
        if handle is None or handle == invalid_handle:
            return bool(ctypes.get_last_error() == _ERROR_SHARING_VIOLATION)
        kernel32.CloseHandle(handle)
        return False
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Could not check whether %s is being written: %s", path.name, exc)
        return False


def _wait_for_new_exe(
    target: Path,
    after_mtime: Optional[float],
    timeout_s: float,
    old_mtime: float = 0.0,
) -> bool:
    """Block until ``target`` looks freshly installed or we time out.

    The readiness rule lives in :func:`_new_exe_looks_fresh`; this adds
    the polling.  Checks before the clock for the same reason as
    ``_wait_for_parent_exit``: a budget already clamped to 0 must still
    stat the file once.  An installer that finished while an earlier
    phase overran is exactly the case where the answer is "yes, it is
    there", and skipping the look reports it as never having arrived.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if _new_exe_looks_fresh(target, old_mtime, after_mtime):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_INTERVAL_S)


def _launch_command(exe_path: Path) -> list[str]:
    """The argv that brings the freshly-installed keyboard back.

    On Windows the keyboard is launched **through Explorer**, never by
    creating the process ourselves. Measured on 2026-10-04 against
    Windows' own ``osk.exe`` (signed, in System32, ``uiAccess="true"``):
    a UIAccess application started with ``CreateProcess`` or even
    ``ShellExecuteEx`` from a process that has no UIAccess itself comes
    up with ``TokenUIAccess=0``, while the same exe started by
    ``explorer.exe`` comes up with ``TokenUIAccess=1``. This helper is
    built without UIAccess and runs from a staged copy in %TEMP%, so it
    has none to hand down, and a direct launch
    would bring the keyboard back unable to type into elevated windows
    until the user next started it from the Start menu. Explorer runs at
    the user's integrity level, exactly as this helper does, so the relay
    that failed when the *elevated installer* tried it (see the module
    docstring) has no integrity boundary to fail across here.
    """
    if sys.platform == "win32":
        explorer = Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe"
        return [str(explorer), str(exe_path)]
    return [str(exe_path)]


def _running_image_names() -> Optional[list[str]]:
    """The image name of every process we can see, or None if we cannot look.

    Windows is the real implementation, and the only platform the
    updater runs on: a Toolhelp process snapshot read through ctypes,
    rather than a ``tasklist`` subprocess, which would cost a process
    per poll and need ``CREATE_NO_WINDOW``. Elsewhere it reads the
    target of each ``/proc/<pid>/exe`` where there is a ``/proc``, and
    returns None where there is not (macOS).

    None means "cannot tell", never "nothing is running", and that
    includes a ``Process32FirstW`` that fails on the first entry, which
    is what a wrong structure size looks like: a broken enumeration must
    not read as a keyboard that never started.
    """
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class _ProcessEntry32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),  # ULONG_PTR
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),  # MAX_PATH
                ]

            th32cs_snapprocess = 0x00000002
            invalid_handle_value = ctypes.c_void_p(-1).value
            # A private WinDLL so the prototypes below do not leak into
            # every other caller of ctypes.windll.kernel32. An undeclared
            # restype is c_int, which truncates a 64-bit handle.
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            entry_ptr = ctypes.POINTER(_ProcessEntry32W)
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            kernel32.Process32FirstW.restype = wintypes.BOOL
            kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, entry_ptr]
            kernel32.Process32NextW.restype = wintypes.BOOL
            kernel32.Process32NextW.argtypes = [wintypes.HANDLE, entry_ptr]
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

            snapshot = kernel32.CreateToolhelp32Snapshot(th32cs_snapprocess, 0)
            if snapshot is None or snapshot == invalid_handle_value:
                return None
            try:
                entry = _ProcessEntry32W()
                entry.dwSize = ctypes.sizeof(_ProcessEntry32W)
                if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                    return None
                names: list[str] = []
                while True:
                    names.append(str(entry.szExeFile))
                    if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        return names
            finally:
                kernel32.CloseHandle(snapshot)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Could not snapshot the process list: %s", exc)
            return None
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    found: list[str] = []
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            found.append(Path(os.readlink(pid_dir / "exe")).name)
        except OSError:
            # Another user's process, a kernel thread, or one that has
            # just exited: none of them is the keyboard we launched.
            continue
    return found


def _process_image_running(image_name: str) -> bool:
    """Is a process with exactly this image name running right now?

    The whole name, compared case-insensitively as Windows compares
    image names, so the helper's own ``alpha-osk-relauncher.exe`` never
    counts as the keyboard's ``alpha-osk.exe``. The old keyboard cannot
    be what this finds either: the helper waited for its parent to exit
    before launching, and the installer force-kills every
    ``alpha-osk.exe`` before it writes the new one. What can match
    besides our launch is a keyboard the user started by hand in the
    meantime, or the one the installer's own Explorer fallback starts;
    both are a keyboard on screen, which is the question being asked, so
    neither is a false positive worth guarding against.

    True when the process list cannot be read at all. The check exists
    to stop a false "Done!", and an unreadable list is no evidence that
    the launch failed, so it falls back to what the helper reported
    before the check existed.
    """
    names = _running_image_names()
    if names is None:
        _logger.warning("Could not list running processes; assuming %s started", image_name)
        return True
    wanted = image_name.casefold()
    return any(name.casefold() == wanted for name in names)


def _wait_for_new_osk_process(image_name: str, timeout_s: float) -> bool:
    """Block until a process named ``image_name`` is running, or time out.

    The headless path's confirmation that the launch brought the
    keyboard back. The splash path asks :func:`_process_image_running`
    once per QTimer tick instead, so its window keeps painting through
    the wait; both go through that one rule. Looks before the clock, like
    the other waits, so a budget clamped to 0 still gets one look.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if _process_image_running(image_name):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_NEW_OSK_POLL_INTERVAL_S)


def _log_new_osk_missing(image_name: str, budget_s: float) -> None:
    """The one log line both paths write when the keyboard never appears.

    Names the image and the budget actually waited, and nothing else.
    """
    _logger.error(
        "No %s process appeared within %.0fs of the launch; reporting failure",
        image_name,
        budget_s,
    )


def _launch_new_osk(exe_path: Path) -> bool:
    """Spawn the freshly-installed ``alpha-osk.exe`` as a detached process.

    Returns True when ``Popen`` did not raise. On Windows that proves
    only that Explorer started (see ``_launch_command`` for why Windows
    goes through it), not that the keyboard did, so both callers go on
    to confirm the keyboard's own process appeared before reporting
    success: :func:`_wait_for_new_osk_process` on the headless path, and
    :func:`_process_image_running` per tick on the splash path. A False
    here is logged rather than silently exiting.
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
            _launch_command(exe_path),
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

    * ``--show-splash`` (production): drives the wait phases through
      ``UpdateFlow`` on a QTimer so the update screen stays painted from
      the moment it starts until the new keyboard's window is visible,
      and stays up on a failure until the user acts.
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
    # The installed exe's mtime as the updater saw it just before the
    # install; 0.0 (the default) means "no snapshot" and falls back to
    # the legacy readiness check. See _new_exe_looks_fresh.
    parser.add_argument("--old-exe-mtime", type=float, default=0.0)
    parser.add_argument("--config-dir", type=str, required=True)
    parser.add_argument("--show-splash", action="store_true")
    # Where the updater leaves its markers (installer launched, cancel).
    # Empty means "no updater is telling us anything": start counting from
    # now, as a helper spawned by an updater too old to pass one would.
    parser.add_argument("--signal-dir", type=str, default="")
    # "x,y,w,h" of the keyboard window being replaced, in physical pixels,
    # so the splash opens on top of where the keyboard was.
    parser.add_argument("--anchor-rect", type=str, default="")
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


def _marker(signal_dir: str, name: str) -> bool:
    """Is the marker file ``name`` present in ``signal_dir``?  False with no dir."""
    if not signal_dir:
        return False
    try:
        return (Path(signal_dir) / name).exists()
    except OSError:
        return False


def _wait_for_installer_launch(signal_dir: str, parent_pid: int, timeout_s: float) -> str:
    """Block until the parent says the installer launched, or cancels.

    Returns ``"launched"``, ``"cancelled"`` or ``"timeout"``. A parent that
    is gone without having said either counts as launched: the installer's
    taskkill is the only thing that normally ends it, and it can land in the
    microseconds between the installer starting and the marker being
    written. A parent that really crashed costs one pass through the later
    waits, which end in a failure message.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if _marker(signal_dir, update_signals.CANCEL_FILE):
            return "cancelled"
        if _marker(signal_dir, update_signals.INSTALLER_LAUNCHED_FILE):
            return "launched"
        if not _process_alive(parent_pid):
            return "launched"
        if time.monotonic() >= deadline:
            return "timeout"
        time.sleep(_POLL_INTERVAL_S)


def _run_headless(args: argparse.Namespace, overall_deadline: Optional[float] = None) -> int:
    """Original blocking-poll relauncher. Used by tests and as the
    splash-path fallback. See ``run_relauncher`` for the contract.

    ``overall_deadline`` is the whole-run ceiling, passed in so that a
    splash which raised part way through does not hand this path a
    fresh one. Defaulted only for direct callers (the tests); the real
    dispatch always supplies it.

    With a ``--signal-dir`` it first waits for the parent to say the
    installer launched, and the ceiling restarts there: time the user spent
    on the UAC prompt is not time the install has used.
    """
    signal_dir = getattr(args, "signal_dir", "")
    if signal_dir:
        outcome = _wait_for_installer_launch(signal_dir, args.parent_pid, _APPROVAL_TIMEOUT_S)
        if outcome == "cancelled":
            _logger.info("The updater cancelled the relaunch")
            return EXIT_CANCELLED
        if outcome == "timeout":
            _logger.error("The installer was never launched, giving up")
            return EXIT_NOT_APPROVED
        overall_deadline = time.monotonic() + _MAX_TOTAL_RUNTIME_S
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
    old_mtime = getattr(args, "old_exe_mtime", 0.0)
    if not _wait_for_new_exe(target_exe, parent_death_time, new_exe_budget, old_mtime):
        _logger.error("New exe never appeared at %s within %.0fs", target_exe, new_exe_budget)
        return 3

    _logger.info("New exe ready at %s — launching", target_exe)
    if not _launch_new_osk(target_exe):
        return 4

    # Popen succeeding only proves Explorer started. A keyboard that then
    # never appears is a failed launch: same exit code, and, like one, no
    # handoff for a later manual start to read.
    appear_budget = _remaining(overall_deadline, _NEW_OSK_APPEAR_TIMEOUT_S)
    if not _wait_for_new_osk_process(target_exe.name, appear_budget):
        _log_new_osk_missing(target_exe.name, appear_budget)
        return 4

    _write_handoff(config_dir, args.new_version, args.previous_version)
    _logger.info("Relauncher done")
    return 0


def _new_exe_ready(target: Path, after_mtime: Optional[float], old_mtime: float = 0.0) -> bool:
    """Single-shot version of ``_wait_for_new_exe``. Returns True if the
    new exe is in place right now. Used by the QTimer-driven splash
    path so we can yield back to the event loop between checks."""
    return _new_exe_looks_fresh(target, old_mtime, after_mtime)


def parse_anchor_rect(text: str) -> Optional[tuple[int, int, int, int]]:
    """``"x,y,w,h"`` -> a rect, or None for anything that is not four integers.

    The text came from our own updater but crosses a process boundary, so a
    malformed one means "no anchor", never an exception: the splash then
    centres on the primary screen instead.
    """
    parts = text.split(",")
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (int(part) for part in parts)
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return (x, y, w, h)


def centred_position(
    anchor: tuple[int, int, int, int],
    size: tuple[int, int],
    work: tuple[int, int, int, int],
) -> tuple[int, int]:
    """Top-left that centres a window of ``size`` on ``anchor``, kept inside ``work``.

    ``work`` is ``(left, top, right, bottom)`` of the anchor's own monitor.
    A window larger than the work area is pinned to its top-left corner,
    which keeps the message readable rather than centred off both edges.
    """
    ax, ay, aw, ah = anchor
    width, height = size
    left, top, right, bottom = work
    x = ax + (aw - width) // 2
    y = ay + (ah - height) // 2
    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    return (x, y)


class Phase(enum.Enum):
    APPROVE = "approve"
    CLOSING = "closing"
    INSTALLING = "installing"
    STARTING = "starting"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_PHASES = frozenset({Phase.DONE, Phase.FAILED, Phase.CANCELLED})


class UpdateFlow:
    """The update screen's state machine, with every outside fact injected.

    Pure logic and no Qt: the splash calls :meth:`step` on a timer and draws
    ``phase`` / ``message`` / ``detail``, and the tests call it with a fake
    clock and fake probes. Holding the transitions here, rather than in
    closures inside the window code, is what makes "it never goes quiet"
    something a test can say.

    ``installer_launched`` / ``cancelled`` are the parent's markers.
    ``keyboard_shown`` is the new keyboard's "my window is on screen"
    event. ``new_exe_ready`` receives the parent's death time (the legacy
    mtime fallback). With ``wait_for_approval=False`` the flow starts at
    *Closing*, for a helper nobody is signalling.

    The whole-run ceiling (``_MAX_TOTAL_RUNTIME_S``) starts when the
    installer launched, not when the helper did: the UAC prompt can take
    as long as the user does, and that time is not the install's.
    """

    def __init__(
        self,
        *,
        version: str,
        parent_alive: Callable[[], bool],
        installer_launched: Callable[[], bool],
        cancelled: Callable[[], bool],
        new_exe_ready: Callable[[Optional[float]], bool],
        launch_keyboard: Callable[[], bool],
        keyboard_shown: Callable[[], bool],
        write_handoff: Callable[[], None],
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        wait_for_approval: bool = True,
        ceiling_deadline: Optional[float] = None,
    ) -> None:
        self._version = version
        self._parent_alive = parent_alive
        self._installer_launched = installer_launched
        self._cancelled = cancelled
        self._new_exe_ready = new_exe_ready
        self._launch_keyboard = launch_keyboard
        self._keyboard_shown = keyboard_shown
        self._write_handoff = write_handoff
        self._clock = clock
        self._wall_clock = wall_clock

        self.phase = Phase.APPROVE
        self.message = "Waiting for you to approve the update"
        self.detail = "Choose Yes when Windows asks."
        self.exit_code = 0
        self.failure = ""
        self.parent_death_time: Optional[float] = None

        self._ceiling_override = None if wait_for_approval else ceiling_deadline
        self._ceiling = clock() + _MAX_TOTAL_RUNTIME_S
        self._approval_deadline = clock() + _APPROVAL_TIMEOUT_S
        self._deadline = 0.0
        self._parent_budget = 0.0
        self._new_exe_budget = 0.0
        self._shown_budget = 0.0
        self._grace_until: Optional[float] = None
        # After a failure that came once the old keyboard was gone, keep
        # looking for the new one's window (see step()).
        self._watch_after_failure = True
        if not wait_for_approval:
            self._enter_closing()

    @property
    def finished(self) -> bool:
        return self.phase in _TERMINAL_PHASES

    def _remaining(self, phase_timeout: float) -> float:
        """A phase's budget, clipped to what is left of the whole run (never negative)."""
        return max(0.0, min(phase_timeout, self._ceiling - self._clock()))

    def _fail(self, code: int, text: str) -> None:
        self.phase = Phase.FAILED
        self.exit_code = code
        self.failure = text
        self.message = text
        self.detail = "Press Start Alpha-OSK to open the keyboard."
        _logger.error("Update screen failed (code %d): %s", code, text)

    def _enter_closing(self) -> None:
        self.phase = Phase.CLOSING
        self.message = "Closing the keyboard"
        self.detail = "This takes a few seconds."
        # The run's clock starts here: the installer is going.
        if self._ceiling_override is not None:
            self._ceiling = self._ceiling_override
        else:
            self._ceiling = self._clock() + _MAX_TOTAL_RUNTIME_S
        self._parent_budget = self._remaining(_PARENT_EXIT_TIMEOUT_S)
        self._deadline = self._clock() + self._parent_budget

    def _enter_installing(self) -> None:
        self.phase = Phase.INSTALLING
        self.message = f"Installing Alpha-OSK {self._version}".rstrip()
        self.detail = "The keyboard will come back by itself."
        self._grace_until = self._clock() + _installer_grace_s(self._ceiling)

    def _enter_starting(self) -> None:
        self.phase = Phase.STARTING
        self.message = "Starting the keyboard"
        self.detail = "Almost there."
        if self._keyboard_shown():
            # Something (the installer's own fallback, the user) already
            # brought it up; launching a second one would only be handed off.
            self._succeed()
            return
        if not self._launch_keyboard():
            self._fail(EXIT_LAUNCH_FAILED, "The update installed, but the keyboard did not start.")
            return
        self._shown_budget = self._remaining(_KEYBOARD_SHOWN_TIMEOUT_S)
        self._deadline = self._clock() + self._shown_budget

    def _succeed(self) -> None:
        self._write_handoff()
        self.phase = Phase.DONE
        self.exit_code = 0
        self.message = "Done"
        self.detail = ""

    def fail_unexpectedly(self) -> None:
        """For the driver: a probe raised, so show a failure rather than freeze."""
        # The probe that raised may be the very one the post-failure watch
        # calls, and it would raise again on every tick.
        self._watch_after_failure = False
        self._fail(EXIT_LAUNCH_FAILED, "Something went wrong while updating.")

    def step(self) -> None:
        """Advance as far as the current facts allow. Cheap, never blocks."""
        if self.phase is Phase.FAILED:
            # A failure screen stays until the user acts, except when the
            # keyboard it is apologising for turns up after all: the
            # installer's own explorer fallback, a slow install, a slow first
            # start. Left up, the screen would sit topmost over a working
            # keyboard saying it did not start. Only once the old keyboard is
            # known to be gone (a death time), because until then its own
            # announcement is still set and would read as the new one's.
            if (
                self._watch_after_failure
                and self.parent_death_time is not None
                and self._keyboard_shown()
            ):
                _logger.info("The keyboard window appeared after the failure; finishing")
                self._succeed()
            return
        if self.finished:
            return

        if self.phase is Phase.APPROVE:
            if self._cancelled():
                self.phase = Phase.CANCELLED
                self.exit_code = EXIT_CANCELLED
                return
            # A parent that has vanished without a word counts as launched
            # (see _wait_for_installer_launch).
            if self._installer_launched() or not self._parent_alive():
                self._enter_closing()
            elif self._clock() >= self._approval_deadline:
                self._fail(EXIT_NOT_APPROVED, "The update was not approved, so nothing changed.")
                return
            else:
                return

        if self.phase is Phase.CLOSING:
            if not self._parent_alive():
                self.parent_death_time = self._wall_clock()
                self._enter_installing()
            elif self._clock() >= self._deadline:
                _logger.error("Parent OSK still alive after %.0fs", self._parent_budget)
                self._fail(
                    EXIT_PARENT_STUCK,
                    "Alpha-OSK did not close in time, so the update could not finish.",
                )
            return

        if self.phase is Phase.INSTALLING:
            now = self._clock()
            if self._grace_until is not None:
                if now < self._grace_until:
                    return
                self._grace_until = None
                self._new_exe_budget = self._remaining(_NEW_EXE_TIMEOUT_S)
                self._deadline = now + self._new_exe_budget
            # Looks before the clock, so a budget clamped to zero still
            # gets one look at the file.
            if self._new_exe_ready(self.parent_death_time):
                self._enter_starting()
            elif now >= self._deadline:
                _logger.error("New exe not in place within %.0fs", self._new_exe_budget)
                self._fail(EXIT_NEW_EXE_MISSING, "The update did not finish installing.")
            return

        if self.phase is Phase.STARTING:
            if self._keyboard_shown():
                self._succeed()
            elif self._clock() >= self._deadline:
                _logger.error("No keyboard window within %.0fs of the launch", self._shown_budget)
                self._fail(
                    EXIT_LAUNCH_FAILED,
                    "The update installed, but the keyboard window did not appear.",
                )


class _KeyboardShownProbe:
    """Has the new keyboard said its window is on screen?

    The event does not exist until the new keyboard creates it, so the
    handle is opened lazily on each look until it is.
    """

    def __init__(self) -> None:
        self._handle: Optional[int] = None

    def __call__(self) -> bool:
        if self._handle is None:
            self._handle = update_signals.open_keyboard_shown_event()
            if self._handle is None:
                return False
        return update_signals.event_is_set(self._handle)

    def close(self) -> None:
        if self._handle is not None:
            update_signals.close_event(self._handle)
            self._handle = None


def _open_log_folder(config_dir: Path) -> None:
    """Show the folder holding ``alpha-osk.log`` and ``relauncher.log``."""
    try:
        if sys.platform == "win32":
            explorer = Path(os.environ.get("WINDIR", r"C:\Windows")) / "explorer.exe"
            subprocess.Popen(
                [str(explorer), str(config_dir)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
        else:
            subprocess.Popen(["xdg-open", str(config_dir)], close_fds=True)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Could not open the log folder: %s", exc)


def _run_with_splash(args: argparse.Namespace, overall_deadline: Optional[float] = None) -> int:
    """The update screen. See the module docstring for the flow.

    The window is built and shown before anything is waited on, so it is on
    screen while the UAC prompt is up, and only :class:`UpdateFlow` decides
    when it may go. ``overall_deadline`` is only used when nothing signals
    the helper (no ``--signal-dir``).
    """
    # Lazy-import Qt so the headless path stays import-clean and
    # tests don't accidentally drag PySide6 into a fresh interpreter.
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication, QLabel, QProgressBar, QPushButton, QWidget

    config_dir = Path(args.config_dir)
    target_exe = Path(args.target_exe)
    signal_dir = getattr(args, "signal_dir", "")
    anchor = parse_anchor_rect(getattr(args, "anchor_rect", ""))

    existing_app = QApplication.instance()
    app = existing_app if isinstance(existing_app, QApplication) else QApplication([])
    # Only the flow may end this process. A WM_CLOSE from outside (a stray
    # `taskkill` without /F posts one to every window of the image) must
    # not end the run early.
    app.setQuitOnLastWindowClosed(False)

    shown = _KeyboardShownProbe()
    old_mtime = getattr(args, "old_exe_mtime", 0.0)

    flow = UpdateFlow(
        version=args.new_version,
        parent_alive=lambda: _process_alive(args.parent_pid),
        installer_launched=lambda: _marker(signal_dir, update_signals.INSTALLER_LAUNCHED_FILE),
        cancelled=lambda: _marker(signal_dir, update_signals.CANCEL_FILE),
        new_exe_ready=lambda death_time: _new_exe_ready(target_exe, death_time, old_mtime),
        launch_keyboard=lambda: _launch_new_osk(target_exe),
        keyboard_shown=shown,
        write_handoff=lambda: _write_handoff(config_dir, args.new_version, args.previous_version),
        wait_for_approval=bool(signal_dir),
        ceiling_deadline=overall_deadline,
    )

    splash: QWidget = _build_splash_widget(QWidget, QLabel, QProgressBar, QPushButton, Qt)

    # A close request is never an abort: there is no close button while
    # working, and an Alt+F4 or WM_CLOSE from elsewhere must not hide the
    # one thing telling the user the keyboard is coming back.
    def _ignore_close(ev):  # pragma: no cover - needs a live window server
        ev.ignore()

    splash.closeEvent = _ignore_close  # type: ignore[method-assign]

    def _label(name: str) -> QLabel:
        found = splash.findChild(QLabel, name)
        assert found is not None
        return found

    def _button(name: str) -> QPushButton:
        found = splash.findChild(QPushButton, name)
        assert found is not None
        return found

    progress = splash.findChild(QProgressBar, "progress")
    assert progress is not None
    buttons = splash.findChild(QWidget, "buttons")
    assert buttons is not None

    def _place() -> None:
        """Centre on the keyboard being replaced; the primary screen if unknown."""
        if sys.platform == "win32" and anchor is not None:
            from .platform import windows_window

            hwnd = int(splash.winId())
            size = windows_window.window_size(hwnd)
            work = windows_window.monitor_work_area_at(
                anchor[0] + anchor[2] // 2, anchor[1] + anchor[3] // 2
            )
            if size is not None and work is not None:
                x, y = centred_position(anchor, size, work)
                if windows_window.move_window_noactivate(hwnd, x, y):
                    return
        screen = app.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            splash.move(
                geo.x() + (geo.width() - splash.width()) // 2,
                geo.y() + (geo.height() - splash.height()) // 3,
            )

    def _style_native() -> None:
        """Never take focus, and sit in the topmost band."""
        if sys.platform != "win32":
            return
        try:
            from .platform import windows_window

            handle = splash.windowHandle()
            if handle is not None:
                windows_window.apply_extended_styles(handle, taskbar_button=False, topmost=True)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Could not style the update window: %s", exc)

    def _reassert_topmost() -> None:
        """The installer and other windows must not be able to bury us."""
        if sys.platform != "win32":
            return
        try:
            from .platform import windows_window

            windows_window.set_window_band(int(splash.winId()), True)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("Could not re-assert topmost: %s", exc)

    shown_failure = [False]

    def _render() -> None:
        _label("msg").setText(flow.message)
        _label("detail").setText(flow.detail)
        _label("detail").setVisible(bool(flow.detail))
        if flow.phase is Phase.FAILED and not shown_failure[0]:
            shown_failure[0] = True
            progress.setRange(0, 1)
            progress.setValue(0)
            buttons.setVisible(True)
            splash.adjustSize()
            _place()
        elif flow.phase is Phase.DONE:
            progress.setRange(0, 1)
            progress.setValue(1)
            # A failure screen the keyboard's late arrival cleared: its
            # buttons have nothing left to do.
            buttons.setVisible(False)

    finishing = [False]

    def _leave_event_loop() -> None:
        # exit(), not quit(): Qt 6's quit() first sends a close event to every
        # window and is abandoned if one refuses it, and this window refuses
        # every close on purpose (see _ignore_close). The run ends here, by
        # the flow's say-so, and by nothing else.
        app.exit(0)

    def _quit() -> None:
        if finishing[0]:
            return
        finishing[0] = True
        QTimer.singleShot(0, _leave_event_loop)

    def _tick() -> None:
        if finishing[0]:
            return
        try:
            flow.step()
        except Exception as exc:  # noqa: BLE001
            # A bug in a probe must not leave an update screen that never
            # changes. Show it as a failure the user can act on.
            _logger.exception("Update flow raised: %s", exc)
            flow.fail_unexpectedly()
        _render()
        if flow.phase is Phase.CANCELLED:
            _quit()
        elif flow.phase is Phase.DONE:
            finishing[0] = True
            QTimer.singleShot(_DONE_DWELL_MS, _leave_event_loop)

    def _start_keyboard() -> None:
        _launch_new_osk(target_exe)
        _quit()

    _button("start").clicked.connect(_start_keyboard)
    _button("logs").clicked.connect(lambda: _open_log_folder(config_dir))
    _button("close").clicked.connect(_quit)

    buttons.setVisible(False)
    _render()
    splash.show()
    _style_native()
    _place()

    tick_timer = QTimer(splash)
    tick_timer.setInterval(_TICK_MS)
    tick_timer.timeout.connect(_tick)
    tick_timer.start()
    top_timer = QTimer(splash)
    top_timer.setInterval(_REASSERT_TOP_MS)
    top_timer.timeout.connect(_reassert_topmost)
    top_timer.start()

    app.exec()
    tick_timer.stop()
    top_timer.stop()
    shown.close()
    _logger.info("Update screen finished with code %d", flow.exit_code)
    return flow.exit_code


def _build_splash_widget(QWidget, QLabel, QProgressBar, QPushButton, Qt):
    """Construct the update window, buttons hidden. Pulled out of
    ``_run_with_splash`` to keep the styling tweakable in one place.

    Deliberately no close or hide control while the update is working: it
    is the only thing telling the user the keyboard is coming back. The
    buttons appear only on a failure, and every one is a large target for
    an imprecise pointer.
    """
    from PySide6.QtWidgets import QHBoxLayout, QLayout, QVBoxLayout

    win = QWidget()
    win.setObjectName("splash")
    win.setWindowTitle("Updating Alpha-OSK")
    win.setWindowFlags(
        Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
    )
    win.setAttribute(Qt.WA_ShowWithoutActivating, True)
    win.setAttribute(Qt.WA_StyledBackground, True)
    # Match the in-app toast colour so the window visibly belongs to
    # Alpha-OSK rather than looking like a stray system dialog. A square
    # border: a rounded one would need the layered-window corner handling
    # the keyboard's own windows carry.
    win.setStyleSheet(
        "QWidget#splash { background-color: #1e3354; border: 2px solid #4a8eff; }"
        "QLabel { background: transparent; }"
        "QLabel#title { color: #7ec8ff; font-size: 17pt; font-weight: bold; }"
        "QLabel#msg { color: #ffffff; font-size: 14pt; font-weight: bold; }"
        "QLabel#detail { color: #cfe0ff; font-size: 11pt; }"
        # Indeterminate marquee bar. NSIS silent (/S) install gives us no
        # real percentage, but constant motion is the difference between
        # "is it stuck?" and "still working".
        "QProgressBar { background-color: #14233a; border: 1px solid #2a4570; height: 14px; }"
        "QProgressBar::chunk { background-color: #4a8eff; }"
        "QPushButton { background-color: #2a4570; color: #ffffff; border: 2px solid #4a8eff;"
        " font-size: 13pt; font-weight: bold; min-height: 56px; padding: 0 18px; }"
        "QPushButton:hover { background-color: #36588c; }"
        "QPushButton#start { background-color: #4a8eff; color: #0b1626; min-height: 64px; }"
        "QPushButton#start:hover { background-color: #6ba2ff; }"
    )

    layout = QVBoxLayout(win)
    layout.setContentsMargins(28, 24, 28, 24)
    layout.setSpacing(12)
    # The window follows its content, so showing the buttons on a failure
    # grows it instead of clipping them.
    layout.setSizeConstraint(QLayout.SetFixedSize)

    def _centred_label(name: str, text: str) -> None:
        label = QLabel(text, win)
        label.setObjectName(name)
        label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        label.setWordWrap(True)
        label.setMinimumWidth(440)
        label.setTextFormat(Qt.PlainText)
        layout.addWidget(label)

    _centred_label("title", "Updating Alpha-OSK")
    _centred_label("msg", "")
    _centred_label("detail", "")

    # Indeterminate (marquee) bar: setRange(0, 0) is Qt's busy state.
    progress = QProgressBar(win)
    progress.setObjectName("progress")
    progress.setRange(0, 0)
    progress.setTextVisible(False)
    progress.setFixedHeight(14)
    layout.addWidget(progress)

    buttons = QWidget(win)
    buttons.setObjectName("buttons")
    buttons_layout = QVBoxLayout(buttons)
    buttons_layout.setContentsMargins(0, 8, 0, 0)
    buttons_layout.setSpacing(10)
    start = QPushButton("Start Alpha-OSK", buttons)
    start.setObjectName("start")
    buttons_layout.addWidget(start)
    row = QHBoxLayout()
    row.setSpacing(10)
    logs = QPushButton("Open log folder", buttons)
    logs.setObjectName("logs")
    close = QPushButton("Close", buttons)
    close.setObjectName("close")
    row.addWidget(logs)
    row.addWidget(close)
    buttons_layout.addLayout(row)
    layout.addWidget(buttons)

    return win


if __name__ == "__main__":  # pragma: no cover — CLI entry
    sys.exit(run_relauncher(sys.argv))
