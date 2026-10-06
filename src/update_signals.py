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

_SYNCHRONIZE = 0x00100000
_WAIT_OBJECT_0 = 0

# Kept for the life of the process: the event exists only while a handle to
# it is open, and the helper may open it long after the window appeared.
_shown_event_handle: Optional[int] = None


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
