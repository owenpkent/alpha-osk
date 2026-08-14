"""
Pointer Introspection
=====================

Answers one question for the bridge: *did the user just click somewhere
that isn't us?*

The prediction context (``_current_word`` / ``_context_buffer`` /
``_sentence_buffer``) mirrors the text sitting beside the caret in
whatever app holds focus.  A click that lands outside the keyboard's own
window is the user moving that caret, so the mirror goes stale.

The three signals the bridge already polls each miss the most common way
that happens.  ``GetForegroundWindow`` only sees whole-app switches.
``focused_element_token`` (UIA RuntimeId) sees a different *control*, but
browsers and Electron apps routinely expose a single element for an
entire document, so two fields on one page share an id.  And
``caret_position_token`` needs the app to publish a caret rectangle,
which most of that same browser world never does.  Both of the latter
fail closed on purpose, which leaves clicking from one field to another
inside one window with no signal at all.  A click has one.

**Polling, not hooking.**  The click can't be observed through Qt:
``WS_EX_NOACTIVATE`` keeps our window off the focus path and the event
belongs to another process anyway.  A low-level mouse hook
(``WH_MOUSE_LL``) would report exact press coordinates, but it puts this
process on the input path of every mouse event on the desktop, which is a
latency and antivirus-heuristic cost out of proportion to a signal this
coarse.

**And the poll reads only what it can read without taking it.**
``GetAsyncKeyState``'s low bit is consumed by the reader, so polling it
would steal every press from any other process watching the same way --
including the dwell-click and switch-access utilities an on-screen
keyboard user is most likely to be running.  See
:func:`_left_button_pressed_since_last_call`.

Windows only.  Everywhere else :func:`external_click_detected` returns
False and the caller keeps whatever the older signals give it.
"""

from __future__ import annotations

import ctypes
import os
import sys
from typing import Optional

_IS_WINDOWS = sys.platform == "win32"

_VK_LBUTTON = 0x01
# GetAsyncKeyState's high bit: the button's physical state right now.
# Its low bit ("pressed since the last call") is deliberately NOT read --
# see _left_button_pressed_since_last_call.
_KEY_IS_DOWN = 0x8000

# Whether the left button was down at the previous poll, so a press can be
# recognised as a transition rather than a level.
_button_was_down = False

_user32: Optional[ctypes.CDLL] = None


def external_click_detected() -> bool:
    """Whether a left-click landed outside this process since the last call.

    Stateful: each call consumes the presses that happened since the
    previous one, so exactly one caller may poll it.

    Returns False on any non-Windows platform and on any internal
    failure.  False is also the answer when the pointer's owner can't be
    determined, which makes the whole thing fail closed the same way
    ``focused_element_token`` does: an unreadable signal must never be
    the reason typing context is thrown away.
    """
    if not _IS_WINDOWS:
        return False
    try:
        if not _left_button_pressed_since_last_call():
            return False
        pid = _pid_under_cursor()
        if pid is None:
            return False
        return pid != os.getpid()
    except Exception:
        return False


def _left_button_pressed_since_last_call() -> bool:
    """Detect a left-button press from the high bit alone, as an edge.

    ``GetAsyncKeyState`` also has a low bit meaning "pressed since the
    previous call", which would catch a click that opens and closes
    entirely between two polls.  **Reading it is not free and we do not
    read it.**  That bit is system-wide state, and the read *clears* it
    for whoever asks next, so polling 20x a second would quietly steal
    every press from any other process watching the same way.  On this
    machine that is not hypothetical: dwell-click and switch-access
    utilities are exactly the software an on-screen keyboard user runs
    alongside this one, and breaking another assistive tool to sharpen a
    signal this coarse is not a trade worth making.

    The high bit is the button's physical state and reading it takes
    nothing from anyone, so the press is recovered as a transition
    against the previous poll.  What that misses is a click shorter than
    the 50 ms interval -- shorter than a typical human click, and far
    shorter than a dwell click -- and missing one costs a single
    next-word suggestion, since the next click detects it anyway.
    """
    global _button_was_down
    state = _read_left_button_state()
    if state is None:
        _button_was_down = False
        return False
    is_down = bool(state & _KEY_IS_DOWN)
    was_down = _button_was_down
    _button_was_down = is_down
    return is_down and not was_down


def _read_left_button_state() -> Optional[int]:
    """Raw ``GetAsyncKeyState(VK_LBUTTON)``, or None if it can't be read."""
    user32 = _load_user32()
    if user32 is None:
        return None
    return int(user32.GetAsyncKeyState(_VK_LBUTTON))


def _pid_under_cursor() -> Optional[int]:
    """Process id owning the window under the pointer, or None if unknown.

    Read at poll time rather than at press time, which is the price of
    polling: the position is up to one interval stale.  That holds in
    practice because a click on a key leaves the pointer resting on that
    key, while a click in the target app is followed by the pointer
    travelling all the way back to the keyboard, which takes far longer
    than one interval.  A wrong answer costs one dropped or one extra
    context reset; it can never cost a keystroke.

    The desktop itself has no window and no owner, so a click on bare
    desktop reads as None rather than as "somebody else".  It is not a
    caret move, and treating "no answer" as "not us" would make every
    unreadable reading clear the user's context.
    """
    user32 = _load_user32()
    if user32 is None:
        return None
    import ctypes.wintypes as wintypes

    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return None
    hwnd = user32.WindowFromPoint(point)
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


def _load_user32() -> Optional[ctypes.CDLL]:
    """Cache user32 with explicit signatures, or None if unavailable.

    The restypes are not decoration.  ``GetAsyncKeyState`` returns a
    SHORT, so ctypes' default int would read whatever the rest of the
    register held, and ``WindowFromPoint`` returns an HWND, which the
    default 32-bit int truncates on x64 -- the truncated handle would
    then resolve to no process at all.
    """
    global _user32
    if _user32 is not None:
        return _user32
    if not _IS_WINDOWS:
        return None
    import ctypes.wintypes as wintypes

    # Annotated, not inferred: ``ctypes.WinDLL`` does not exist in typeshed
    # off Windows, so on a Linux type-check run the call degrades to ``Any``
    # and returning it trips ``warn_return_any``.
    user32: ctypes.CDLL = ctypes.WinDLL(  # type: ignore[attr-defined]
        "user32", use_last_error=True
    )
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32 = user32
    return user32
