"""The small signals the keyboard, the updater and the update helper pass each other.

Three processes take part in an auto-update and none of them can wait on
another directly: the keyboard hands off to an elevated installer that kills
it, and the helper (``_update_relauncher``) has to outlive both.  What they
need to tell each other is tiny, so each message is the cheapest durable
thing that survives the sender dying:

* **"The installer is running"** (keyboard -> helper) and **"cancel"**
  (keyboard -> helper) are marker files in the helper's private stage
  directory.  A file outlives the keyboard, which a named kernel object
  would not (it is destroyed with its last handle, and the installer kills
  the keyboard seconds after launching).
* **"The keyboard window is on screen"** (new keyboard -> helper) is a named
  Win32 event, ``Local\\AlphaOSK.KeyboardShown``.  The new keyboard is
  started by Explorer, so it is not the helper's child and the helper has no
  handle to wait on; and "the process exists" is not the same as "the window
  is visible", which is the thing the user is actually waiting for.

The keyboard *creates* the event and the helper only *opens* it for
``SYNCHRONIZE``.  The other way round (the helper creating it) would ask for
write access to an object the keyboard may already have created with a
higher integrity label, and a lower-integrity opener is refused write access
to those; waiting needs read access only.

Imports nothing heavy on purpose: the helper loads this before it has built
a window.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

_logger = logging.getLogger("UpdateSignals")

# Per-session namespace: one keyboard per desktop session, and a Local\
# name cannot be opened from another user's session.
KEYBOARD_SHOWN_EVENT = "Local\\AlphaOSK.KeyboardShown"

# Files inside the helper's stage directory.
INSTALLER_LAUNCHED_FILE = "installer-launched"
CANCEL_FILE = "cancel"
# helper -> updater: "my window has painted".  The updater waits for it, with
# a ceiling, instead of sleeping a fixed time before it launches the installer.
UI_SHOWN_FILE = "ui-shown"
# Held open, exclusively, by the helper for as long as it runs.  A stage whose
# lock cannot be opened belongs to a live helper and must not be swept.
HELPER_LOCK_FILE = "helper.lock"

_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0

# Kept for the life of the process: the event exists only while a handle to
# it is open, and the helper may open it long after the window appeared.
_shown_event_handle: Optional[int] = None

# Kept for the life of the process too: the lock is the open handle.
_held_locks: list[object] = []


def touch(path: Path) -> bool:
    """Create the marker file ``path``.  False when it could not be written."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return True
    except OSError as exc:
        _logger.warning("Could not write update marker %s: %s", path.name, exc)
        return False


def announce_keyboard_shown() -> bool:
    """Signal that the keyboard window is on screen.  Windows only.

    Safe to call twice and from any thread.  Returns True when the event is
    now set.  A failure is logged and swallowed: the helper's ceiling ends in
    a screen with a Start button, which is a worse outcome than a missing
    signal only for the one run, and never worth failing startup over.
    """
    global _shown_event_handle
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.SetEvent.argtypes = [ctypes.c_void_p]

        if _shown_event_handle is None:
            # Manual-reset, initially clear: once set it stays set, so a
            # helper that opens it late still sees the answer.
            handle = kernel32.CreateEventW(None, True, False, KEYBOARD_SHOWN_EVENT)
            if not handle:
                _logger.warning("CreateEventW failed (error %d)", ctypes.get_last_error())
                return False
            _shown_event_handle = int(handle)
        if not kernel32.SetEvent(ctypes.c_void_p(_shown_event_handle)):
            _logger.warning("SetEvent failed (error %d)", ctypes.get_last_error())
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Could not announce the keyboard window: %s", exc)
        return False


def open_keyboard_shown_event() -> Optional[int]:
    """Open the keyboard's event for waiting, or None if it does not exist yet.

    None is the normal answer until the new keyboard has created it.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenEventW.restype = ctypes.c_void_p
        kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.OpenEventW(_SYNCHRONIZE, False, KEYBOARD_SHOWN_EVENT)
        return int(handle) if handle else None
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Could not open the keyboard-shown event: %s", exc)
        return None


def event_is_set(handle: int) -> bool:
    """Is the event signalled right now?  A zero-timeout wait, never blocks."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, wintypes.DWORD]
        return int(kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0)) == _WAIT_OBJECT_0
    except Exception:  # noqa: BLE001
        return False


def close_event(handle: int) -> None:
    """Release a handle from :func:`open_keyboard_shown_event`."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle(ctypes.c_void_p(handle))
    except Exception:  # noqa: BLE001
        pass


def hold_lock(path: Path) -> bool:
    """Take ``path`` exclusively and keep it for the life of this process.

    The helper calls this on its stage's lock file.  The operating system
    drops the lock when the process dies, however it dies, which is what a
    pid written into a file cannot offer: a pid outlives its process and gets
    handed to a stranger.  Returns False when the lock could not be taken
    (logged and swallowed: a helper without a lock is merely sweepable).
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

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
            generic_read = 0x80000000
            generic_write = 0x40000000
            open_always = 4
            invalid = ctypes.c_void_p(-1).value
            # Share mode 0: no other open of this file, for reading, writing
            # or deleting, succeeds while this handle lives.
            handle = kernel32.CreateFileW(
                str(path), generic_read | generic_write, 0, None, open_always, 0, None
            )
            if handle is None or handle == invalid:
                _logger.warning("Could not lock %s (error %d)", path.name, ctypes.get_last_error())
                return False
            _held_locks.append(handle)
            return True
        import fcntl

        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        _held_locks.append(fd)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.warning("Could not lock %s: %s", path.name, exc)
        return False


def lock_is_held(path: Path) -> bool:
    """Is ``path`` locked by a live process right now?

    False for a file that does not exist: nothing holds a lock nobody took.
    Anything else that stops us asking (permissions, odd errors) reads as
    "not held" too, which is the sweep's pre-lock behaviour, and the sweep's
    age guard still stands between it and a fresh stage.
    """
    if not path.exists():
        return False
    if sys.platform == "win32":
        try:
            with open(path, "rb"):
                return False
        except PermissionError:
            return True
        except OSError:
            return False
    try:
        import fcntl

        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def release_held_locks() -> None:
    """Drop every lock this process holds.  For tests; the helper holds its lock until it exits."""
    while _held_locks:
        held = _held_locks.pop()
        try:
            if sys.platform == "win32":
                import ctypes
                from ctypes import wintypes

                kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
                kernel32.CloseHandle.restype = wintypes.BOOL
                kernel32.CloseHandle(held)
            else:
                os.close(int(held))  # type: ignore[call-overload]
        except Exception:  # noqa: BLE001
            pass
