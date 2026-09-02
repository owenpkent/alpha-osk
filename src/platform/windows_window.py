"""
Windows Window Styling
=======================

Win32-specific window manipulation for the on-screen keyboard: the extended
styles that make the QML root behave as an OSK (``WS_EX_NOACTIVATE``,
always-on-top applied as a Z-order change rather than a style bit, the
taskbar-button dance), the ``AppUserModelID`` that keeps the taskbar icon
from reverting to the generic default, and the best-effort "surface the
already-running instance" used by the single-instance check.

This used to live inline in ``keyboard_app.py``, which is why
``pyproject.toml`` carried a blanket ``ignore_errors`` for that whole file:
about a third of it was this ctypes code, and the override threw away type
checking for the other two thirds (logging setup, the singleton lock, the
tray, the exception hooks, ``main()``) as collateral. Window styling is also
an OS-abstraction concern like the rest of ``src/platform/`` -- see
``x11_window.py`` for the X11 counterpart -- so it belongs in this package
on its own merits, not only for the mypy split.

Every public function here is guarded by a literal ``if sys.platform !=
"win32": return`` at the top, mirroring ``src/platform/pointer.py``'s
Windows-only style. That is not just a runtime safety net: mypy prunes the
unreachable branch under ``--platform linux`` (so the ``ctypes.windll``
calls below are never checked against a platform that doesn't have them)
and checks it for real under ``--platform win32``, which is what lets this
module carry no blanket exemption at all. See CLAUDE.md's mypy note under
"Build, run, test" for the full mechanism.

Every function also swallows its own failures: a styling glitch must never
be the reason the keyboard fails to start.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtGui import QWindow

_logger = logging.getLogger("windows_window")


def surface_existing_instance(title: str = "Alpha-OSK") -> None:
    """Best-effort: un-minimise and bring the running instance forward.

    Walks top-level windows looking for one titled ``title``, then calls
    ``ShowWindow(SW_RESTORE)`` and ``SetForegroundWindow``. All failures are
    silent -- this is a courtesy to the user, not a correctness requirement.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
        user32.EnumWindows.restype = ctypes.c_bool
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = ctypes.c_bool

        SW_RESTORE = 9
        target: list[int] = []

        def _enum(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, buf, 64)
            if buf.value == title:
                target.append(hwnd)
                return False  # stop enumerating
            return True

        user32.EnumWindows(EnumWindowsProc(_enum), 0)
        if target:
            hwnd = target[0]
            user32.ShowWindow(hwnd, SW_RESTORE)
            # AllowSetForegroundWindow first lets SetForegroundWindow
            # succeed across processes; ASFW_ANY = -1.
            try:
                user32.AllowSetForegroundWindow(-1)
            except Exception:
                # Probe-only: if AllowSetForegroundWindow isn't available
                # the next SetForegroundWindow may flash the taskbar
                # instead of stealing focus, which is acceptable degraded
                # behaviour for a single-instance surface.
                pass
            user32.SetForegroundWindow(hwnd)
    except Exception as exc:
        _logger.debug("Surfacing existing instance failed: %s", exc)


def apply_extended_styles(root: QWindow, *, taskbar_button: bool = False) -> None:
    """
    Use Win32 ``SetWindowLongW`` to add extended window styles that Qt
    cannot express through its own flag system.

    Styles applied:

    - **WS_EX_NOACTIVATE** (``0x08000000``): The window is never
      activated when clicked.  This is *critical* for an OSK — without
      it, clicking a key would move focus away from the user's text
      editor.  This one is settable through ``SetWindowLongW``.

    **Always-on-top is applied with ``SetWindowPos(HWND_TOPMOST)``, not
    by writing ``WS_EX_TOPMOST`` into the style word, and that
    distinction is the whole feature.**  MSDN is explicit that the style
    is added and removed with ``SetWindowPos``; the bit and the Z-order
    *band* are separate pieces of state, and writing the bit directly
    sets the first while leaving the second alone.  The result is a
    window that reports itself as topmost and is not: this was reported
    as "always on top isn't working", and a Z-order walk found the
    keyboard sitting **fifteenth**, below a dozen ordinary windows,
    with ``WS_EX_TOPMOST`` reading true the whole time.  Qt's
    ``WindowStaysOnTopHint`` does place the window in the band, so the
    old code appeared to work; writing the style word afterwards is what
    knocked it back out.

    So the ``SetWindowPos`` call below carries ``HWND_TOPMOST`` and must
    **not** carry ``SWP_NOZORDER``, which would ask the system to leave
    the Z-order exactly as it found it, which was the bug.  Anything
    that re-applies window flags later has to re-assert this, because
    ``setFlags`` on Windows can recreate the native window.

    **``WS_EX_TOOLWINDOW`` is actively cleared here, not merely left
    unset.**  It suppresses the taskbar entry, which leaves the minimise
    button with nowhere to go and the tray icon as the only way back.
    Qt adds it on its own: QML declares ``visible: true``, so the window
    is already shown when :func:`keyboard_app._apply_window_flags` calls
    ``setFlags``, and applying a non-activating, frameless, always-on-top
    flag set to an *already shown* window is the case where Qt decides
    the window does not belong in the taskbar.  Applying the same flags
    before the first show does not do it, which is why the comments here
    claimed for a long time that the style "was removed" while the
    shipped window carried it.  ``WS_EX_APPWINDOW`` is set too, so the
    taskbar entry does not depend on Qt leaving the rest of the style
    word alone.  The trade-off is that the OSK appears in Alt+Tab, which
    is acceptable.

    Requires the window to have a valid ``winId()`` (i.e. the native
    window handle has been created).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        GWL_EXSTYLE = -20
        GWL_STYLE = -16
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_APPWINDOW = 0x00040000
        WS_MINIMIZEBOX = 0x00020000
        WS_SYSMENU = 0x00080000

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Pin signatures so 64-bit Windows doesn't truncate handles.
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long

        hwnd = int(root.winId())

        # Read current extended style.  Both Get/Set return 0 on real
        # failure but 0 is also a valid style value, so disambiguate
        # via SetLastError(0) + GetLastError per MSDN guidance.
        kernel32.SetLastError(0)
        current = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if current == 0 and kernel32.GetLastError() != 0:
            _logger.warning(
                "GetWindowLongW failed (err=%d); skipping extended-style apply",
                kernel32.GetLastError(),
            )
            return

        # WS_EX_TOPMOST is deliberately NOT in this write.  See the
        # docstring: the style word is not where always-on-top lives, and
        # writing it here is what broke it.
        #
        # WS_EX_TOOLWINDOW is cleared and WS_EX_APPWINDOW set, because Qt
        # adds the former behind our back and it is what removes a window
        # from the taskbar.  QML declares `visible: true`, so the window
        # is already on screen when `_apply_window_flags` calls setFlags,
        # and applying these flags to a *shown* window is the case where
        # Qt decides a non-activating window does not belong in the
        # taskbar.  Setting the same flags before the first show does not
        # do it, which is why this went unnoticed and why the comments
        # here have claimed for a long time that the style "was removed":
        # that was the intent, and the intent was not what shipped.
        #
        # Reported as the keyboard having no taskbar button, so the
        # minimise button had nowhere to go and clicking the pinned icon
        # did nothing.  APPWINDOW is set as well as TOOLWINDOW cleared,
        # so the answer does not depend on Qt leaving the rest alone.
        new_style = current | WS_EX_NOACTIVATE
        if taskbar_button:
            new_style = (new_style | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
        kernel32.SetLastError(0)
        prev = user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        if prev == 0 and kernel32.GetLastError() != 0:
            _logger.warning(
                "SetWindowLongW failed (err=%d); WS_EX_NOACTIVATE may not be active",
                kernel32.GetLastError(),
            )
            return

        # A taskbar button can *restore* a window without this, which is
        # why minimising and clicking the button both worked while a
        # second click did nothing. The shell decides whether a button may
        # minimise from WS_MINIMIZEBOX / WS_SYSMENU in the ordinary style
        # word, and this window is a bare WS_POPUP.
        #
        # Windows' own on-screen keyboard is the proof that this composes
        # with never taking focus: osk.exe runs TOPMOST | APPWINDOW |
        # NOACTIVATE | LAYERED, an extended style identical to ours, and
        # carries MINIMIZEBOX | SYSMENU in its style word.
        #
        # No frame comes with them, which is the thing to check when
        # touching this on a frameless window: measured before and after
        # on a real window, the window rect stays equal to the client rect
        # and neither WS_CAPTION nor WS_THICKFRAME appears. Qt also leaves
        # the bits alone across a resize.
        #
        # Written here, before the SetWindowPos(SWP_FRAMECHANGED) call
        # below rather than after it: MSDN's guidance for SetWindowLong
        # is that a frame style change needs a following
        # SetWindowPos(SWP_FRAMECHANGED) before the cached frame data
        # picks it up, and WS_MINIMIZEBOX / WS_SYSMENU are frame styles
        # like any other. Writing this after the one SWP_FRAMECHANGED
        # call in this function left it unflushed until whatever next
        # touched the frame.
        if taskbar_button:
            kernel32.SetLastError(0)
            style = user32.GetWindowLongW(hwnd, GWL_STYLE)
            if style or kernel32.GetLastError() == 0:
                user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_MINIMIZEBOX | WS_SYSMENU)

        # One call doing two jobs.
        #
        # HWND_TOPMOST puts the window in the topmost Z-order band, which
        # is the only way to get there and the thing that was missing.
        # SWP_FRAMECHANGED forces the system to re-read the extended and
        # ordinary style words just written above; without it
        # WS_EX_NOACTIVATE may not take effect and clicks on keys steal
        # focus before SendInput fires, and the MINIMIZEBOX / SYSMENU bits
        # just added to the ordinary style word may not be honoured either.
        #
        # SWP_NOACTIVATE keeps us off the foreground while doing it, which
        # matters more here than usual: this window must never activate.
        HWND_TOPMOST = -1
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        kernel32.SetLastError(0)
        ok = user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        if not ok:
            _logger.warning(
                "SetWindowPos(HWND_TOPMOST) failed (err=%d); the keyboard may sit "
                "behind other windows",
                kernel32.GetLastError(),
            )

        _logger.info("Applied WS_EX_NOACTIVATE and placed the window in the topmost band")
    except Exception as e:
        _logger.warning("Failed to apply Windows extended styles: %s", e)


def set_app_user_model_id(app_id: str) -> None:
    """Give the process an explicit AppUserModelID on Windows.

    Without this, Windows can't tie the OSK window's taskbar button back
    to the application identity once the Qt window appears.  The button is
    created at launch with the exe's embedded icon, then re-derives an
    identity from the bare process and falls back to the generic default
    icon the moment the window shows — the "taskbar icon reverts to the
    default after opening" symptom.  ``SetCurrentProcessExplicitAppUserModelID``
    pins the identity up front so the taskbar keeps using our icon.

    Must run *before* the first top-level window is created (ideally before
    ``QApplication``), or Windows has already cached the derived identity.
    No-op on non-Windows and best-effort on Windows (a failure here only
    costs the taskbar icon, never startup).  ``app_id`` should be the
    caller's stable ``Company.Product`` identity string (see
    ``keyboard_app.APP_USER_MODEL_ID``), kept in the caller rather than
    here because it must also match the AppUserModelID stamped on the
    installer's shortcuts, which is app packaging concern, not a windowing
    one.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception as exc:  # pragma: no cover - platform/runtime dependent
        _logger.debug("SetCurrentProcessExplicitAppUserModelID failed: %s", exc)
