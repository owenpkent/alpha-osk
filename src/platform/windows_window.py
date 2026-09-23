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
            _write_styles(hwnd, taskbar_button=taskbar_button)
        finally:
            if reshow:
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    except Exception as e:
        _logger.warning("Failed to apply Windows extended styles: %s", e)


def _write_styles(hwnd: int, *, taskbar_button: bool) -> None:
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
            "SetWindowPos(HWND_TOPMOST) failed (err=%d); the keyboard may sit behind other windows",
            kernel32.GetLastError(),
        )

    _logger.info("Applied WS_EX_NOACTIVATE and placed the window in the topmost band")


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
    ) -> None:
        super().__init__()
        self._window = window
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


def install_quiet_restore(window: QWindow) -> Optional[QuietRestoreFilter]:
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

        flt = QuietRestoreFilter(window, show_window=show_window)
        app = QCoreApplication.instance()
        if app is None:
            return None
        app.installNativeEventFilter(flt)
        _logger.info("Restoring the keyboard will not take the foreground")
        return flt
    except Exception as e:
        _logger.warning("Could not install the quiet-restore filter: %s", e)
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
