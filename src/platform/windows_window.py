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
from typing import Callable, Optional

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer
from PySide6.QtGui import QWindow

_logger = logging.getLogger("windows_window")


def surface_existing_instance(title: str = "Alpha-OSK") -> None:
    """Best-effort: bring the running instance back without taking focus.

    Runs in the *second* process, the one that lost the single-instance
    race, which is what a launcher hotkey, a Start-menu click or an
    assistive device's "open keyboard" button all start. It finds the
    running keyboard's top-level window by title and, if it is minimized,
    restores it with ``SW_SHOWNOACTIVATE``.

    **It never asks for the foreground.** It used to restore with
    ``SW_RESTORE`` and then call ``AllowSetForegroundWindow`` and
    ``SetForegroundWindow``, and measured on the installed keyboard (a
    shortcut-key launch with Notepad in front) that left the keyboard as
    the foreground window for as long as anyone watched: the next key
    clicked was sent to the keyboard itself, and the user had to click back
    into their application before typing. An on-screen keyboard has no use
    for the foreground (see ``QuietRestoreFilter``, which takes the
    activation out of every other restore route for the same reason).

    A keyboard that is already on screen is left exactly as it is: it sits
    in the topmost band, so there is nothing to bring forward. All failures
    are silent: this is a courtesy, not a correctness requirement.
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
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = ctypes.c_bool

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
            restore_without_activating(
                target[0], is_iconic=user32.IsIconic, show_window=user32.ShowWindow
            )
    except Exception as exc:
        _logger.debug("Surfacing existing instance failed: %s", exc)


def restore_without_activating(
    hwnd: int,
    *,
    is_iconic: Callable[[int], object],
    show_window: Callable[[int, int], object],
) -> None:
    """Un-minimize ``hwnd`` without making it the foreground window.

    The decision half of ``surface_existing_instance``, with the two Win32
    calls injected so it can be tested on any platform. A window that is
    not minimized is not touched at all.
    """
    if is_iconic(hwnd):
        show_window(hwnd, SW_SHOWNOACTIVATE)


def apply_extended_styles(
    root: QWindow, *, taskbar_button: bool = False, topmost: bool = True
) -> None:
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

    ``topmost`` is the *Always on Top* setting.  It decides which band
    that one call asks for, so a user who turned it off never gets a
    topmost keyboard between the style write and a later correction.

    Requires the window to have a valid ``winId()`` (i.e. the native
    window handle has been created).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        hwnd = int(root.winId())

        # Before the style writes, not after: each of those returns early
        # on failure, and the corner needs nothing they compute.
        _prefer_dwm_rounded_corners(hwnd)

        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL

        # The taskbar decides whether a window gets a button at the moment
        # it becomes visible, and this window became visible as a tool
        # window (QML's `visible: true` runs before we do).  Rewriting the
        # style word afterwards does not make the shell look again: the
        # live keyboard sat with APPWINDOW set, TOOLWINDOW clear and no
        # running-window button at all, only the 66 px pinned stub with no
        # label and no dot, until something activated it.  Reported as the
        # taskbar icon "not inflating until you click it".
        #
        # MSDN's rule for changing a visible window's taskbar presence is
        # to hide it, change the style, then show it again, and that is
        # what the shell responds to: hide + SW_SHOWNOACTIVATE on the
        # running keyboard, with no style change at all, attached the
        # window as "Alpha-OSK - 1 running window" at once and left the
        # foreground alone.  The re-show lives in a `finally` so a failed
        # style write can never leave the keyboard hidden, and it is
        # SW_SHOWNOACTIVATE, never SW_SHOW: this window must not activate.
        reshow = taskbar_button and bool(user32.IsWindowVisible(hwnd))
        if reshow:
            user32.ShowWindow(hwnd, SW_HIDE)
        try:
            _write_styles(hwnd, taskbar_button=taskbar_button, topmost=topmost)
        finally:
            if reshow:
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    except Exception as e:
        _logger.warning("Failed to apply Windows extended styles: %s", e)


def _write_styles(hwnd: int, *, taskbar_button: bool, topmost: bool = True) -> None:
    """The style writes and the frame flush behind :func:`apply_extended_styles`.

    Split out so the hide / re-show around it can wrap every early return
    in one ``finally``.  Windows-only; the caller has already checked.
    """
    if sys.platform != "win32":
        return
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
    HWND_NOTOPMOST = -2
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
        HWND_TOPMOST if topmost else HWND_NOTOPMOST,
        0,
        0,
        0,
        0,
        SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )
    if not ok:
        _logger.warning(
            "SetWindowPos(HWND_TOPMOST) failed (err=%d); the keyboard may sit behind other windows",
            kernel32.GetLastError(),
        )

    _logger.info(
        "Applied WS_EX_NOACTIVATE and placed the window in the %s band",
        "topmost" if topmost else "ordinary",
    )


_HWND_TOP = 0
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
# SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE.  The NOACTIVATE is the point:
# no Z-order change here may ever make the keyboard the foreground window.
_BAND_SWP_FLAGS = 0x0001 | 0x0002 | 0x0010


def _place(hwnd: int, insert_after: int) -> bool:
    """One non-activating ``SetWindowPos`` that changes only the Z-order."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
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
        return bool(user32.SetWindowPos(hwnd, insert_after, 0, 0, 0, 0, _BAND_SWP_FLAGS))
    except Exception as e:
        _logger.warning("SetWindowPos failed: %s", e)
        return False


def set_window_band(hwnd: int, topmost: bool) -> bool:
    """Move ``hwnd`` into the topmost band, or back to the ordinary one.

    Used for the *Always on Top* setting and by :class:`ShellPopupYielder`.
    Never activates the window.
    """
    return _place(hwnd, _HWND_TOPMOST if topmost else _HWND_NOTOPMOST)


def native_window_rect(window: QWindow) -> Optional[tuple[int, int, int, int]]:
    """``(x, y, width, height)`` of ``window`` in physical screen pixels.

    Read from Win32 rather than Qt's geometry because the update helper is a
    separate process that places its own window with Win32 too, and Qt's
    logical (device-independent) coordinates do not agree across monitors of
    different scale.  None when the window has no handle, is minimized
    (Windows parks those at -32000), or the call fails.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        rect = wintypes.RECT()
        if not user32.GetWindowRect(int(window.winId()), ctypes.byref(rect)):
            return None
        if rect.left <= -30000 or rect.top <= -30000:
            return None
        return (rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)
    except Exception as e:
        _logger.debug("GetWindowRect failed: %s", e)
        return None


def monitor_work_area_at(x: int, y: int) -> Optional[tuple[int, int, int, int]]:
    """``(left, top, right, bottom)`` work area of the monitor nearest ``(x, y)``."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MonitorInfo)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        monitor_defaulttonearest = 2
        monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), monitor_defaulttonearest)
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        work = info.rcWork
        return (work.left, work.top, work.right, work.bottom)
    except Exception as e:
        _logger.debug("GetMonitorInfoW failed: %s", e)
        return None


def window_size(hwnd: int) -> Optional[tuple[int, int]]:
    """``(width, height)`` of ``hwnd`` in physical pixels."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.right - rect.left, rect.bottom - rect.top)
    except Exception as e:
        _logger.debug("GetWindowRect failed: %s", e)
        return None


def move_window_noactivate(hwnd: int, x: int, y: int) -> bool:
    """Move ``hwnd`` to physical ``(x, y)`` without resizing, restacking or activating it."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
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
        swp_nosize, swp_nozorder, swp_noactivate = 0x0001, 0x0004, 0x0010
        return bool(
            user32.SetWindowPos(hwnd, 0, x, y, 0, 0, swp_nosize | swp_nozorder | swp_noactivate)
        )
    except Exception as e:
        _logger.debug("SetWindowPos (move) failed: %s", e)
        return False


def raise_window_noactivate(hwnd: int) -> bool:
    """Raise ``hwnd`` to the top of its own band without activating it.

    For a non-topmost window that is above every ordinary application window.
    """
    return _place(hwnd, _HWND_TOP)


def bring_to_front_noactivate(hwnd: int) -> bool:
    """Bring a non-topmost ``hwnd`` above every application window, unactivated.

    ``HWND_TOP`` alone is not enough for the keyboard: Windows ignores a
    background process's request to stack a window above the foreground
    app, and a click on a ``WS_EX_NOACTIVATE`` window does not make our
    process the foreground one (measured live: the click left the keyboard
    behind, while the tray icon, whose click does grant the foreground,
    raised it). Entering the topmost band and leaving it again is not
    subject to that rule, and leaves the window at the top of the ordinary
    band. Both calls carry ``SWP_NOACTIVATE``, so focus never moves.

    Not for the shell-popup yielder: there the momentary topmost step would
    flash the keyboard over the notification it is stepping aside for.
    """
    raised = _place(hwnd, _HWND_TOPMOST)
    lowered = _place(hwnd, _HWND_NOTOPMOST)
    return raised and lowered


WM_QUERYOPEN = 0x0013
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
# MSG layout on 64-bit Windows: HWND hwnd (8 bytes), then UINT message.
_MSG_MESSAGE_OFFSET = 8


class QuietRestoreFilter(QAbstractNativeEventFilter):
    """Bring the minimized keyboard back without taking the foreground.

    **Why this exists.** Qt restores a minimized window with
    ``ShowWindow(SW_SHOWNORMAL)`` whatever its flags, and restoring a
    minimized window that way makes it the foreground window, the keyboard's
    ``WS_EX_NOACTIVATE`` notwithstanding.  Measured on this keyboard: the
    tray's restore code, a UI Automation client's
    ``WindowPattern.SetWindowVisualState(Normal)`` and a plain
    ``ShowWindow(SW_RESTORE)`` from another process all left the keyboard in
    the foreground.  Keystrokes the keyboard sends next then go to the
    keyboard rather than the application the user was typing into, and that
    application sees its focus leave.  For an external switch scanner that
    restores the keyboard on the user's behalf, that is a restore that
    silently redirects the next word.

    **How.** Windows asks a minimized window for permission with
    ``WM_QUERYOPEN`` before every one of those restores.  This declines it
    (a handled result of 0 means "do not open") and performs the restore
    itself, one event-loop turn later, with ``SW_SHOWNOACTIVATE``.  That
    restore asks permission too, so ``_restoring`` lets it through; without
    that flag the keyboard declined its own restore in a loop and never came
    back.  Stripping ``SWP_NOACTIVATE`` into ``WM_WINDOWPOSCHANGING`` was
    tried first and changes nothing, because the activation on restore does
    not go through the window-position flags.

    **What it does not do.** It adds no capability: every route above could
    already restore the window, and this only takes the activation out of
    them.  It does not touch minimizing, which never took the foreground.

    The message test is written against plain integers so it can be tested
    on any platform; only the ``ShowWindow`` call is Windows-specific, and it
    is injected.  Every message this process handles passes through
    ``nativeEventFilter``, so it reads the one field it needs and returns.
    """

    def __init__(
        self,
        window: QWindow,
        *,
        show_window: Optional[Callable[[int, int], object]] = None,
        defer: Optional[Callable[[Callable[[], None]], None]] = None,
        after_restore: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__()
        self._window = window
        self._after_restore = after_restore
        self._show_window = show_window
        self._defer = defer or (lambda fn: QTimer.singleShot(0, fn))
        self._restoring = False

    def declines(self, hwnd: int, message: int) -> bool:
        """Whether this message is a restore to decline and redo quietly."""
        if message != WM_QUERYOPEN or self._restoring:
            return False
        try:
            if hwnd != int(self._window.winId()):
                return False
        except Exception:
            return False
        self._defer(lambda: self._restore_quietly(hwnd))
        return True

    def _restore_quietly(self, hwnd: int) -> None:
        if self._show_window is None:
            return
        self._restoring = True
        try:
            self._show_window(hwnd, SW_SHOWNOACTIVATE)
        except Exception as e:
            _logger.warning("Quiet restore failed: %s", e)
        finally:
            self._restoring = False
        # Qt may re-assert its own band when the window is shown again, and
        # with Always on Top off a restored keyboard must come back above
        # the apps it was hiding behind.  The caller decides what to apply.
        if self._after_restore is not None:
            try:
                self._after_restore()
            except Exception as e:
                _logger.warning("Post-restore band fix failed: %s", e)

    def nativeEventFilter(self, eventType, message):  # type: ignore[no-untyped-def]
        try:
            import ctypes

            address = int(message)
            msg_id = ctypes.c_uint.from_address(address + _MSG_MESSAGE_OFFSET).value
            if msg_id != WM_QUERYOPEN:
                return False, 0
            hwnd = ctypes.c_void_p.from_address(address).value or 0
            if self.declines(int(hwnd), msg_id):
                return True, 0
        except Exception as e:
            _logger.debug("Quiet-restore filter could not read a message: %s", e)
        return False, 0


def install_quiet_restore(
    window: QWindow, *, after_restore: Optional[Callable[[], None]] = None
) -> Optional[QuietRestoreFilter]:
    """Make restoring the minimized keyboard leave the foreground alone.

    Returns the filter, which the caller must keep a reference to: Qt does
    not own an event filter installed from Python, and a collected one is a
    dangling pointer on the message path.  ``None`` off Windows, or if
    installation failed, which is never a reason to fail startup.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        from PySide6.QtCore import QCoreApplication

        user32 = ctypes.windll.user32
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL

        def show_window(hwnd: int, cmd: int) -> object:
            return user32.ShowWindow(wintypes.HWND(hwnd), cmd)

        flt = QuietRestoreFilter(window, show_window=show_window, after_restore=after_restore)
        app = QCoreApplication.instance()
        if app is None:
            return None
        app.installNativeEventFilter(flt)
        _logger.info("Restoring the keyboard will not take the foreground")
        return flt
    except Exception as e:
        _logger.warning("Could not install the quiet-restore filter: %s", e)
        return None


# WinEvent ids (winuser.h).  Plain integers so the yielder below can be
# driven by tests on any platform.
EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_DESTROY = 0x8001
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_HIDE = 0x8003
EVENT_OBJECT_CLOAKED = 0x8017
EVENT_OBJECT_UNCLOAKED = 0x8018
_APPEAR_EVENTS = frozenset({EVENT_OBJECT_SHOW, EVENT_OBJECT_UNCLOAKED})
_VANISH_EVENTS = frozenset({EVENT_OBJECT_HIDE, EVENT_OBJECT_CLOAKED, EVENT_OBJECT_DESTROY})

# Z-order band ids, from the undocumented GetWindowBand.
ZBID_IMMERSIVE_NOTIFICATION = 4

# The windows the keyboard steps aside for, matched on class and band rather
# than on title (localised) or owning process (an OpenProcess per event).
# Measured on Windows 11 24H2, 2026-10-04:
#   - a toast is ShellExperienceHost's ``Windows.UI.Core.CoreWindow`` in the
#     notification band; it is uncloaked to show and cloaked to dismiss, and
#     the same window is reused for the next toast.
#   - taskbar window previews are hosted in Explorer's full-screen
#     ``XamlExplorerHostIslandWindow``, shown when the pointer reaches a
#     taskbar button and hidden when it leaves the taskbar.  The per-button
#     ``Xaml_WindowedPopupClass`` popups live inside that session, so the
#     host alone covers them.
#   - ``TaskListThumbnailWnd`` is the Windows 10 preview window.
_TOAST_CLASS = "Windows.UI.Core.CoreWindow"
_PREVIEW_CLASSES = frozenset({"XamlExplorerHostIslandWindow", "TaskListThumbnailWnd"})


def is_shell_popup(class_name: str, band: Optional[int]) -> bool:
    """Whether a newly shown window is one the keyboard should not cover."""
    if class_name in _PREVIEW_CLASSES:
        return True
    return class_name == _TOAST_CLASS and band == ZBID_IMMERSIVE_NOTIFICATION


class ShellPopupYielder:
    """Drop the keyboard out of always-on-top while a notification or preview is up.

    **Why.** A UIAccess process's always-on-top window is placed in the
    ``ZBID_UIACCESS`` Z-order band, which sits above the notification band
    and above Explorer's own topmost windows.  So once 1.6.0 started running
    with UIAccess for real, toasts (Slack's included, which go through the
    Windows notification system) and taskbar previews opened *behind* the
    keyboard.  Windows' own ``osk.exe`` sits in the same band and has the
    same problem.

    **Why not just stay in the ordinary band.**  A signed probe measured
    how the band is assigned (2026-10-04): it follows topmost-ness, both
    ways.  ``SetWindowPos(HWND_TOPMOST)`` from a UIAccess process moves the
    window into ``ZBID_UIACCESS``, whether it was created topmost or not and
    even when it was created with ``CreateWindowInBand(ZBID_DESKTOP)``;
    ``HWND_NOTOPMOST`` moves it back to ``ZBID_DESKTOP``.  There is no
    topmost-but-ordinary-band state to settle into, and giving UIAccess up
    would cost typing into elevated windows.

    **How.** While at least one popup :func:`is_shell_popup` recognises is
    on screen, every one of our always-on-top windows is made
    ``HWND_NOTOPMOST``, which leaves it above every ordinary application
    window but below the shell's topmost ones, so the popup draws over it.
    When the last one goes, they are made topmost again.  Both passes walk
    the windows in the same order (the keyboard first), so the floating
    pickers still end up above the keyboard rather than under it.

    **The demotion is not a one-shot.**  ``HWND_NOTOPMOST`` places a window
    above the ordinary ones only at the moment of the call; the next
    application the user activates (or that raises a window of its own)
    goes above it, and the keyboard, which never takes focus, has no way of
    coming back on its own.  A maximised application would then cover the
    keys for as long as the toast stayed up.  So while stepped aside, every
    foreground change and every poll tick raises our windows to the top of
    the ordinary group again (``HWND_TOP``, without activating), keyboard
    first, which keeps the popup above them and the applications below.

    **A window of ours that appears mid-yield is demoted too.**  A picker
    opened while a toast is up is restyled topmost by its own show path
    (``apply_extended_styles``), its show event is not a shell popup's, and
    ``HWND_TOP`` is a raise within a band rather than a band change, so
    without this the picker would sit over the notification for the rest of
    its life.  The yielder remembers which windows it has demoted; a window
    in the set that it has not demoted is demoted before it is raised, and
    :meth:`window_shown` lets the show path do it at once rather than at the
    next poll.

    A short ``restore_delay_ms`` stops a toast being replaced by the next
    one, or the pointer sliding between taskbar buttons, from flickering
    the Z-order.  A slow ``poll_ms`` re-check covers a lost hide event: a
    window that is gone, hidden or cloaked no longer counts.

    Every Win32 call is injected, so the logic is testable anywhere.
    """

    def __init__(
        self,
        *,
        windows: Callable[[], list[int]],
        set_topmost: Callable[[int, bool], object],
        raise_window: Callable[[int], object],
        describe: Callable[[int], Optional[tuple[str, Optional[int]]]],
        still_showing: Callable[[int], bool],
        schedule: Optional[Callable[[int, Callable[[], None]], None]] = None,
        restore_delay_ms: int = 150,
        poll_ms: int = 1000,
    ) -> None:
        self._windows = windows
        self._set_topmost = set_topmost
        self._raise_window = raise_window
        self._describe = describe
        self._still_showing = still_showing
        self._schedule = schedule or (lambda ms, fn: QTimer.singleShot(ms, fn))
        self._restore_delay_ms = restore_delay_ms
        self._poll_ms = poll_ms
        self._popups: set[int] = set()
        self._aside = False
        # The windows made HWND_NOTOPMOST in this yield.  One of ours that is
        # not in here while stepped aside has come up topmost since.
        self._demoted: set[int] = set()
        # Bumped on every change, so a stale scheduled restore or poll can
        # tell it has been overtaken and do nothing.
        self._generation = 0

    @property
    def stepped_aside(self) -> bool:
        return self._aside

    def window_shown(self, hwnd: int) -> None:
        """One of our always-on-top windows has just been shown (and styled topmost).

        While stepped aside it is demoted at once, so a picker opened during
        a notification does not cover it until the next poll.  Nothing to do
        otherwise: on top is where it belongs.
        """
        if self._aside:
            self._demote(hwnd)

    def on_event(self, event: int, hwnd: int) -> None:
        """Feed one WinEvent (already filtered to whole windows)."""
        if event == EVENT_SYSTEM_FOREGROUND:
            # The newly active application is now above everything
            # non-topmost, our stepped-aside windows included.
            if self._aside:
                self._reassert()
            return
        if event in _VANISH_EVENTS:
            if hwnd in self._popups:
                self._popups.discard(hwnd)
                self._generation += 1
                if not self._popups:
                    self._schedule_restore()
            return
        if event not in _APPEAR_EVENTS or hwnd in self._popups:
            return
        info = self._describe(hwnd)
        if info is None or not is_shell_popup(*info):
            return
        self._popups.add(hwnd)
        self._generation += 1
        if not self._aside:
            self._step_aside()

    def _step_aside(self) -> None:
        self._aside = True
        for hwnd in self._windows():
            self._demote(hwnd)
        self._schedule_poll()

    def _demote(self, hwnd: int) -> None:
        self._set_topmost(hwnd, False)
        self._demoted.add(hwnd)

    def _restore(self) -> None:
        self._aside = False
        self._demoted.clear()
        for hwnd in self._windows():
            self._set_topmost(hwnd, True)

    def _reassert(self) -> None:
        """Put our windows back at the top of the ordinary group, keyboard first.

        A window that has come up since the yield began is still topmost,
        and a raise would leave it there, over the popup: it is demoted first.
        """
        for hwnd in self._windows():
            if hwnd not in self._demoted:
                self._demote(hwnd)
            self._raise_window(hwnd)

    def _schedule_restore(self) -> None:
        generation = self._generation

        def fire() -> None:
            if generation == self._generation and self._aside and not self._popups:
                self._restore()

        self._schedule(self._restore_delay_ms, fire)

    def _schedule_poll(self) -> None:
        def poll() -> None:
            if not self._aside:
                return
            gone = {h for h in self._popups if not self._still_showing(h)}
            if gone:
                self._popups -= gone
                self._generation += 1
            if not self._popups:
                self._restore()
                return
            # A window raised over us without a foreground change (an app
            # calling SetWindowPos itself) sends no event we hook.
            self._reassert()
            self._schedule_poll()

        self._schedule(self._poll_ms, poll)


def install_shell_popup_yield(
    windows: Callable[[], list[QWindow]],
) -> Optional[ShellPopupYielder]:
    """Let notifications and taskbar previews draw over the keyboard.

    ``windows`` returns our visible always-on-top windows, keyboard first.
    Returns the yielder, which the caller must keep a reference to: it
    owns the ctypes callback the WinEvent hook calls into, and a collected
    callback is a crash on the next window shown anywhere on the desktop.
    ``None`` off Windows or if the hook could not be installed, which is
    never a reason to fail startup (the keyboard just covers popups, as
    1.6.0 did).  See :class:`ShellPopupYielder` for the why.
    """
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        OBJID_WINDOW = 0
        WINEVENT_OUTOFCONTEXT = 0x0000
        DWMWA_CLOAKED = 14

        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        get_band = getattr(user32, "GetWindowBand", None)
        if get_band is not None:
            get_band.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            get_band.restype = wintypes.BOOL

        def describe(hwnd: int) -> Optional[tuple[str, Optional[int]]]:
            buf = ctypes.create_unicode_buffer(256)
            if not user32.GetClassNameW(hwnd, buf, 256):
                return None
            band: Optional[int] = None
            if buf.value == _TOAST_CLASS and get_band is not None:
                out = wintypes.DWORD()
                if get_band(hwnd, ctypes.byref(out)):
                    band = out.value
            return buf.value, band

        def still_showing(hwnd: int) -> bool:
            if not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
                return False
            cloaked = wintypes.DWORD()
            hr = dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
            )
            return hr != 0 or cloaked.value == 0

        # The same non-activating calls the Always on Top setting uses.
        # Raising goes to the top of the window's own group: for a
        # non-topmost window that is above every application and still below
        # the shell's popups.
        set_topmost = set_window_band
        raise_window = raise_window_noactivate

        def window_ids() -> list[int]:
            ids = []
            for win in windows():
                try:
                    ids.append(int(win.winId()))
                except Exception:
                    continue
            return ids

        yielder = ShellPopupYielder(
            windows=window_ids,
            set_topmost=set_topmost,
            raise_window=raise_window,
            describe=describe,
            still_showing=still_showing,
        )

        WinEventProc = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )

        def on_event(_hook, event, hwnd, id_object, id_child, _thread, _time):  # type: ignore[no-untyped-def]
            # Every window shown or hidden anywhere on the desktop lands
            # here, so the filter is the first thing and an exception must
            # never escape into ctypes.
            if not hwnd or id_object != OBJID_WINDOW or id_child != 0:
                return
            try:
                yielder.on_event(int(event), int(hwnd))
            except Exception as e:
                _logger.debug("Shell-popup yield could not handle an event: %s", e)

        callback = WinEventProc(on_event)
        user32.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            WinEventProc,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        user32.SetWinEventHook.restype = wintypes.HANDLE
        user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        # Out of context: the callback runs on this (the GUI) thread, which
        # Qt's event loop pumps, and nothing is injected into other processes.
        ranges = (
            (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_FOREGROUND),
            (EVENT_OBJECT_DESTROY, EVENT_OBJECT_HIDE),
            (EVENT_OBJECT_CLOAKED, EVENT_OBJECT_UNCLOAKED),
        )
        # Every hook that took is unhooked again if a later one does not, or
        # anything below raises.  A hook left behind outlives the callback
        # object it points at (Windows only drops it with the thread), and
        # the next window shown anywhere would call freed memory.
        hooks: list[int] = []
        try:
            for low, high in ranges:
                hook = user32.SetWinEventHook(
                    low, high, None, callback, 0, 0, WINEVENT_OUTOFCONTEXT
                )
                if not hook:
                    raise OSError("SetWinEventHook returned no hook")
                hooks.append(int(hook))
        except Exception:
            for hook in hooks:
                user32.UnhookWinEvent(hook)
            _logger.warning("Could not hook window events; popups may open behind the keyboard")
            return None
        # Pinned to the yielder so the callback lives exactly as long as it.
        yielder._callback = callback  # type: ignore[attr-defined]
        yielder._hooks = hooks  # type: ignore[attr-defined]
        _logger.info("Notifications and taskbar previews will draw over the keyboard")
        return yielder
    except Exception as e:
        _logger.warning("Could not install the shell-popup yield: %s", e)
        return None


def prefer_dwm_rounded_corners(window: QWindow) -> None:
    """Hand a shown window's corners to DWM, and nothing else.

    For a floating window that is allowed to take focus (the dashboard),
    which therefore must not go through :func:`apply_extended_styles` and
    its ``WS_EX_NOACTIVATE``.  Requires a valid ``winId()``.
    """
    if sys.platform != "win32":
        return
    try:
        _prefer_dwm_rounded_corners(int(window.winId()))
    except Exception as e:
        _logger.debug("Could not reach the window's native handle: %s", e)


def _prefer_dwm_rounded_corners(hwnd: int) -> None:
    """Ask Windows to round this window's corners, rather than doing it here.

    The transparent windows here are ``WS_EX_LAYERED``, and a QML ``radius``
    on a layered window leaves the corner pixels unpainted, which come back
    white rather than transparent.  So the QML side squares its background
    off on Windows (``Main.qml``'s ``selfRoundedCorners``) and this hands
    the corner to DWM, which masks an opaque window and antialiases it.
    The measurements and the full reasoning are under *Who rounds the
    window corners* in ``CLAUDE.md``.

    Best-effort by design.  ``DWMWA_WINDOW_CORNER_PREFERENCE`` is Windows 11
    (build 22000) and later; on Windows 10 the call fails and the window
    keeps the square corners QML gave it, which is what every other window
    on that desktop looks like anyway.  A window that fails to round is
    cosmetic, never a reason to fail startup, so nothing here may raise.

    Guarded on ``sys.platform`` like every other function in this file, so
    mypy prunes the ``ctypes.windll`` body under ``--platform linux``.
    """
    if sys.platform != "win32":
        return

    try:
        import ctypes
        from ctypes import wintypes

        DWMWA_WINDOW_CORNER_PREFERENCE = 33
        DWMWCP_ROUND = 2

        dwmapi = ctypes.windll.dwmapi
        preference = ctypes.c_int(DWMWCP_ROUND)
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        hresult = dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_WINDOW_CORNER_PREFERENCE,
            ctypes.byref(preference),
            ctypes.sizeof(preference),
        )
        if hresult != 0:
            # Expected on Windows 10, where the attribute does not exist.
            _logger.debug(
                "DWM corner rounding unavailable (hr=0x%08x); square corners",
                hresult & 0xFFFFFFFF,
            )
    except Exception as e:
        _logger.debug("Could not set the DWM corner preference: %s", e)


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


def cursor_position() -> Optional[tuple[int, int]]:
    """The pointer's position in physical screen pixels, or None if it cannot be read."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return None
        return (point.x, point.y)
    except Exception as e:
        _logger.debug("GetCursorPos failed: %s", e)
        return None


def hwnd_origin(hwnd: int) -> Optional[tuple[int, int]]:
    """The top-left of ``hwnd`` in physical screen pixels."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (rect.left, rect.top)
    except Exception as e:
        _logger.debug("GetWindowRect failed: %s", e)
        return None


def monitor_rects() -> list[tuple[int, int, int, int]]:
    """``(left, top, right, bottom)`` of every monitor, in physical pixels.

    Empty when they cannot be listed, which callers treat as "do not clamp".
    """
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found: list[tuple[int, int, int, int]] = []
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            wintypes.LPARAM,
        )

        def _each(_monitor, _dc, rect, _data):  # type: ignore[no-untyped-def]
            r = rect.contents
            found.append((r.left, r.top, r.right, r.bottom))
            return True

        user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(wintypes.RECT),
            callback_type,
            wintypes.LPARAM,
        ]
        user32.EnumDisplayMonitors.restype = wintypes.BOOL
        callback = callback_type(_each)
        user32.EnumDisplayMonitors(None, None, callback, 0)
        return found
    except Exception as e:
        _logger.debug("EnumDisplayMonitors failed: %s", e)
        return []
