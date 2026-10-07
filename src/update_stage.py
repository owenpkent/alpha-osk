"""Staging the update helper's files in %TEMP%, and sweeping old stages.

The helper (``alpha-osk-relauncher.exe``) must run from outside the install
directory: a running process holds its exe and every loaded DLL mapped, the
installer cannot overwrite a mapped file, and a silent NSIS install that
cannot write one aborts.  So the updater copies what the helper needs into a
private directory under %TEMP% before it launches the installer.

What it copies
==============

Only the files the helper itself uses, about a third of the bundle.  The
helper imports QtWidgets and nothing else of Qt, so the QML runtime, the
multimedia and websocket modules, and the keyboard's data files have no
business in the stage, and copying the whole 250 MB bundle cost 1.5 s warm
and 6.8 s cold, on the critical path before the UAC prompt.

The list is **derived at build time, never written by hand**:
``build/windows/alpha-osk.spec`` takes the helper's own PyInstaller
analysis (the complete set of binaries and data its exe needs to start) and
writes it to ``_internal/relauncher-files.txt``, and the build then stages
that list into a scratch directory and runs the staged helper with
``--self-test`` (``build.py::verify_helper_stage_runs``), failing the build if
it cannot start.  A list that went stale therefore cannot ship.

At run time a missing or unreadable list, or a listed file that is not there,
falls back to copying everything, so the worst a bad list costs is the old
slow path, never an update screen that does not start.

The sweep
=========

Each update leaves its stage behind (a running helper cannot delete its own
image) and the next spawn removes the previous ones.  It must never touch the
stage of a helper that is still running, which would delete the marker files
and unmapped DLLs out from under it, so it skips a stage whose lock the
helper still holds (``update_signals.HELPER_LOCK_FILE``) and any stage
changed in the last ``MIN_STAGE_AGE_S``, which covers the gap between a
stage's creation and its helper taking the lock.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Optional

from . import update_signals

_logger = logging.getLogger("UpdateStage")

STAGE_PREFIX = "alpha-osk-relauncher-"
MANIFEST_RELPATH = "_internal/relauncher-files.txt"

# A stage younger than this is never swept: its helper may not have started
# yet (so no lock to see), or the user may have retried an update within
# moments of cancelling one.
MIN_STAGE_AGE_S = 15 * 60

SUBSET = "subset"
FULL = "full"


def _safe_relative(entry: str) -> Optional[str]:
    """``entry`` as a clean relative path with forward slashes, or None.

    The list sits in the install directory, which a standard user cannot
    write, but it is read from disk and copied from by path, so an entry that
    could leave the bundle (absolute, drive-lettered, ``..``) invalidates the
    whole list rather than being skipped.
    """
    text = entry.strip().replace("\\", "/")
    if not text:
        return None
    if PurePosixPath(text).is_absolute() or PureWindowsPath(text).drive or text.startswith("/"):
        return None
    parts = PurePosixPath(text).parts
    if ".." in parts or "." in parts:
        return None
    return "/".join(parts)


def read_manifest(source: Path) -> Optional[list[str]]:
    """The helper's file list from ``source``, or None if there is no usable one."""
    path = source / MANIFEST_RELPATH
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    files: list[str] = []
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        rel = _safe_relative(line)
        if rel is None:
            _logger.warning("Unusable entry in %s; staging the whole bundle", MANIFEST_RELPATH)
            return None
        files.append(rel)
    return files or None


def stage_bundle(source: Path, dest: Path, *, helper_exe: str, main_exe: str) -> str:
    """Copy what the helper needs from ``source`` into ``dest``.

    Returns ``SUBSET`` when the build's list was used and ``FULL`` when it
    fell back to the whole bundle (minus ``main_exe``, which nothing in a
    stage runs and which is the largest single file).  Raises ``OSError`` if
    the copy fails or the helper exe did not land, so the caller treats it as
    a failed spawn.
    """
    files = read_manifest(source)
    if files is not None:
        # Whatever the list says, the main exe is not staged (see above).
        files = [rel for rel in files if rel != main_exe]
    if files is not None and helper_exe not in files:
        files = None
    if files is not None:
        missing = [rel for rel in files if not (source / rel).is_file()]
        if missing:
            _logger.warning(
                "%d file(s) in %s are not in the install (first: %s); staging the whole bundle",
                len(missing),
                MANIFEST_RELPATH,
                missing[0],
            )
            files = None
    if files is not None:
        for rel in files:
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / rel, target)
        mode = SUBSET
    else:
        shutil.copytree(source, dest, ignore=shutil.ignore_patterns(main_exe))
        mode = FULL
    if not (dest / helper_exe).is_file():
        raise OSError(f"{helper_exe} was not staged")
    return mode


def stage_is_live(stage: Path, *, now: Optional[float] = None, min_age_s: float) -> bool:
    """Might a helper still be using ``stage``?  True means: leave it alone."""
    try:
        age = (time.time() if now is None else now) - stage.stat().st_mtime
    except OSError:
        return True
    if age < min_age_s:
        return True
    return update_signals.lock_is_held(stage / update_signals.HELPER_LOCK_FILE)


def purge_stale_stages(
    temp_root: Path, *, now: Optional[float] = None, min_age_s: float = MIN_STAGE_AGE_S
) -> list[Path]:
    """Remove the previous updates' stages under ``temp_root``; return what was tried.

    Skips any stage that :func:`stage_is_live` says may still be in use.
    Best effort: a file that will not go (a mapped DLL of a helper that
    slipped past both guards) simply survives.
    """
    swept: list[Path] = []
    try:
        for stage in temp_root.glob(STAGE_PREFIX + "*"):
            if not stage.is_dir():
                continue
            if stage_is_live(stage, now=now, min_age_s=min_age_s):
                _logger.info("Leaving %s alone: its helper may still be running", stage.name)
                continue
            shutil.rmtree(stage, ignore_errors=True)
            swept.append(stage)
    except OSError:
        pass
    return swept
