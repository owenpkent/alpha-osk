"""
Keyboard Application - QML engine setup and window configuration.

Launches the on-screen keyboard as a PySide6/QML application with
proper window flags for an OSK (stays on top, doesn't steal focus).

Cross-Platform Behaviour
------------------------
- **Linux (X11)**: Sets ``QT_QPA_PLATFORM=xcb`` and uses Qt window flags
  ``WindowStaysOnTopHint | FramelessWindowHint | WindowDoesNotAcceptFocus``
  to stay above other windows without stealing keyboard focus.

- **Windows**: Uses the same Qt flags.  When the binary is EV code-signed
  with a ``UIAccess="true"`` manifest, the keyboard can also appear above
  UAC prompts and elevated windows.  Additionally, on Windows we call
  ``SetWindowLong`` to apply ``WS_EX_NOACTIVATE`` (focus-suppression).
  ``WS_EX_TOPMOST`` is deliberately *not* written into the style word:
  always-on-top is applied separately with
  ``SetWindowPos(HWND_TOPMOST)``, because writing the style bit directly
  leaves the Z-order band untouched and the window reads as topmost
  without behaving like it.  ``WS_EX_TOOLWINDOW`` is actively cleared
  (Qt adds it on its own once these flags reach an already-shown window)
  and ``WS_EX_APPWINDOW`` is set, so the keyboard keeps a normal taskbar
  entry for the standard minimize button to drop into; the accepted
  trade-off is that the OSK also shows up in Alt+Tab.  ``Qt.Tool`` is
  not applied on Windows either, for the same taskbar reason.

- **macOS**: Same Qt flags (``WindowDoesNotAcceptFocus`` maps to
  ``-canBecomeKeyWindow`` returning NO).  On top of that, pyobjc is
  used to set the NSWindow level to ``NSFloatingWindowLevel``,
  collection behavior to ``CanJoinAllSpaces | Transient | FullScreenAuxiliary``
  (so the keyboard follows the user across Spaces and floats over
  fullscreen apps), and ``hidesOnDeactivate=NO`` so the window stays
  visible when another app gains focus — without that, clicking into
  a text editor would make the keyboard vanish on the next event.

See Also
--------
- ``src/platform/`` — OS-specific key synthesis backends.
- ``docs/architecture/PLATFORM_ARCHITECTURE.md`` — design rationale.
- ``docs/build/WINDOWS.md`` — Windows build / signing guide.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

from PySide6.QtCore import QSettings, QSharedMemory, Qt, QUrl
from PySide6.QtGui import QIcon, QWindow
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .__version__ import __version__
from .keyboard_bridge import KeyboardBridge
from .platform import (
    CURRENT_PLATFORM,
    LOG_FILENAME,
    get_config_dir,
    get_platform_info,
)

_logger = logging.getLogger("KeyboardApp")

# Module-level holder for the single-instance lock.  QSharedMemory's
# segment is released when this object is destroyed, so it must outlive
# the QApplication for the lock to mean anything.
_SINGLETON_LOCK: QSharedMemory | None = None


def _acquire_singleton_or_surface() -> bool:
    """Take the single-instance lock; surface the running instance otherwise.

    Returns True if this process is the (sole) running instance and
    should continue starting up.  Returns False if another Alpha-OSK is
    already running — in that case we attempt to un-minimise / focus the
    existing window on Windows so the user gets some visible response.

    The lock is a QSharedMemory segment keyed on a stable name.  On
    Windows the OS reclaims the segment automatically when the owning
    process exits, so a crashed prior instance never strands the lock.
    On Linux/X11 the SysV segment can persist after a crash; we recover
    by attach-then-detach which forces cleanup if no one holds it, then
    retry create.
    """
    global _SINGLETON_LOCK
    lock = QSharedMemory("alpha-osk-singleton-v1")

    if lock.create(1):
        _SINGLETON_LOCK = lock
        return True

    # On POSIX a crashed process leaves the segment behind.  attach()
    # binds us to it; detach() will free it if we were the last
    # reference (i.e. the previous owner is gone).  Then retry.
    if lock.error() == QSharedMemory.SharedMemoryError.AlreadyExists:
        if lock.attach():
            lock.detach()
        if lock.create(1):
            _SINGLETON_LOCK = lock
            return True

    # A real duplicate is running.  Try to bring its window forward
    # (Windows-only — there's no portable way to do this on Linux
    # without a DBus IPC layer we don't have yet).
    _logger.info("Another Alpha-OSK is already running; surfacing it.")
    if sys.platform == "win32":
        _surface_existing_alpha_osk()
    return False


def _surface_existing_alpha_osk() -> None:
    """Best-effort: un-minimise and bring the running instance forward.

    Walks top-level windows looking for one titled "Alpha-OSK", then
    calls ``ShowWindow(SW_RESTORE)`` and ``SetForegroundWindow``.  All
    failures are silent — this is a courtesy to the user, not a
    correctness requirement.
    """
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
            if buf.value == "Alpha-OSK":
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


def _project_root() -> Path:
    """Resolve the project root (handles both dev and PyInstaller frozen)."""
    here = Path(__file__).resolve().parent
    return here.parent


def qml_path() -> Path:
    """Resolve the path to Main.qml relative to this file."""
    return _project_root() / "qml" / "Main.qml"


def _icon_path() -> Path | None:
    """Find the app icon for the system tray.

    The native-format icon list is chosen per platform:

    - macOS:   ``.icns`` (multi-resolution Apple format)
    - Windows: ``.ico`` (multi-resolution Win32 format)
    - Linux:   the PNG directly — neither .ico nor .icns is native

    Then a PNG fallback so a stripped-down dev checkout still gets
    *some* icon.  Without per-platform gating, every platform would
    pick whichever native asset happens to sit earliest in the
    candidate list — the macOS build was loading
    ``build/windows/alpha-osk.ico`` because the iteration order was
    Windows-first.
    """
    root = _project_root()
    exe_dir = Path(sys.executable).parent

    native_candidates: list[Path]
    if CURRENT_PLATFORM == "macos":
        native_candidates = [
            root / "build" / "macos" / "alpha-osk.icns",
            exe_dir / "alpha-osk.icns",
        ]
    elif CURRENT_PLATFORM == "windows":
        native_candidates = [
            root / "build" / "windows" / "alpha-osk.ico",
            root / "alpha-osk.ico",
            exe_dir / "alpha-osk.ico",
        ]
    else:
        # Linux + unsupported — PNG only
        native_candidates = []

    candidates = native_candidates + [
        root / "assets" / "logo-1024.png",
        exe_dir / "_internal" / "assets" / "logo-1024.png",
        exe_dir / "assets" / "logo-1024.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _setup_platform_env() -> None:
    """
    Apply platform-specific environment variables before QGuiApplication
    is created.

    - **Linux**: Force the ``xcb`` (X11) Qt platform adapter so the
      keyboard works correctly with ``xdotool``.  Wayland users who
      prefer ``ydotool`` can override with ``QT_QPA_PLATFORM=wayland``.
    - **Windows**: No environment overrides needed — the ``windows``
      platform adapter is used automatically.
    - **macOS**: No environment overrides needed — the ``cocoa``
      platform adapter is used automatically.  NSWindow tuning
      happens in ``_apply_macos_window_flags`` after the QML root
      window is created.
    """
    if CURRENT_PLATFORM == "linux":
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    # Use Basic style so ScrollBar/Switch customization works without warnings
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")


def _apply_window_flags(root) -> None:
    """
    Apply OS-specific window flags to make the keyboard behave as a
    proper on-screen keyboard:

    - Stays on top of all other windows.
    - Never steals keyboard focus from the user's active application.
    - Frameless (Alpha-OSK draws its own title bar in QML).
    - Has a normal taskbar entry so the standard Windows minimize
      button can drop the OSK to the taskbar and clicking the
      taskbar entry restores it.  (Earlier builds used ``Qt.Tool``
      and ``WS_EX_TOOLWINDOW`` to suppress the taskbar entry, which
      meant minimize had to ``hide()`` and the only way back was the
      tray icon — easy to miss.  Trade-off: the OSK now appears in
      Alt+Tab.  Acceptable since ``WS_EX_NOACTIVATE`` still prevents
      focus theft on every click.)
    """
    # Qt flags — work on all platforms.  WindowDoesNotAcceptFocus
    # is the Linux/Wayland equivalent of WS_EX_NOACTIVATE; on
    # Windows the Win32 path below handles focus suppression.
    base_flags = (
        Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
    )

    # macOS needs Qt.Tool ON TOP of the above.  On macOS, Qt.Tool
    # makes the QML root a native NSPanel rather than a plain
    # NSWindow.  Only NSPanel honors the "non-activating" semantics
    # we need so clicks on the OSK don't pull Alpha-OSK to the
    # foreground.  Qt.WindowDoesNotAcceptFocus alone maps to
    # ``canBecomeKeyWindow: NO`` — that stops keyboard input but
    # does NOT stop app-level activation on mouse-down.  Without
    # Qt.Tool, clicking a key activates the OSK as the foreground
    # app and ``CGEventPost`` then delivers the synthesised
    # keystroke to Alpha-OSK itself rather than to the editor
    # behind us.  Confirmed in dev logs as
    # ``POST send_text('h') → frontmost=Python``.
    #
    # On Windows and Linux, Qt.Tool *also* removes the taskbar entry
    # (Windows) and changes WM hinting in ways the bridge / minimise
    # / tray icons don't expect — see the WS_EX_TOOLWINDOW note in
    # ``_apply_windows_extended_styles`` for the full rationale.  So
    # we only add Qt.Tool when we're actually on macOS, where the
    # Accessory activation policy has already eliminated the
    # taskbar/Dock entry anyway.
    if CURRENT_PLATFORM == "macos":
        base_flags = base_flags | Qt.WindowType.Tool

    root.setFlags(base_flags)

    # Windows-specific: apply WS_EX_NOACTIVATE via Win32 API
    if CURRENT_PLATFORM == "windows":
        _apply_windows_extended_styles(root, taskbar_button=True)
    elif CURRENT_PLATFORM == "macos":
        _apply_macos_window_flags(root)


def _apply_windows_extended_styles(root, *, taskbar_button: bool = False) -> None:
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
    is already shown when :func:`_apply_window_flags` calls ``setFlags``,
    and applying a non-activating, frameless, always-on-top flag set to
    an *already shown* window is the case where Qt decides the window
    does not belong in the taskbar.  Applying the same flags before the
    first show does not do it, which is why the comments here claimed for
    a long time that the style "was removed" while the shipped window
    carried it.  ``WS_EX_APPWINDOW`` is set too, so the taskbar entry
    does not depend on Qt leaving the rest of the style word alone.  The
    trade-off is that the OSK appears in Alt+Tab, which is acceptable.

    Requires the window to have a valid ``winId()`` (i.e. the native
    window handle has been created).
    """
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


def _apply_macos_activation_policy() -> None:
    """Switch the NSApplication into ``Accessory`` activation policy.

    This is the **critical** fix for OSK focus theft on macOS.  Qt's
    ``WindowDoesNotAcceptFocus`` flag maps to ``canBecomeKeyWindow:
    NO``, which stops the window from receiving keyboard input — but
    on macOS, clicking on a window *also* activates the owning
    application (yanks it to the foreground, owns the menu bar).  The
    NSWindow-level flag does not prevent that.

    Result before this fix: clicking any OSK key activated Alpha-OSK
    as the foreground app, kicking TextEdit out of the frontmost slot.
    ``CGEventPost`` then sent the synthesised keystroke to Alpha-OSK
    itself (the new foreground), so nothing reached the editor — user
    saw "keystrokes not sending".

    ``NSApplicationActivationPolicyAccessory`` tells AppKit that this
    app should never become the active app: clicks on its windows do
    not steal application focus, and the previously frontmost app
    keeps receiving input.  Same model used by macOS's own
    "Accessibility Keyboard" and by menu-bar utilities like Magnet /
    Rectangle / AltTab.

    Trade-offs:
    - **No Dock icon.** The system tray icon (already wired in
      ``main()``) carries show/hide/quit.
    - **No Cmd+Tab entry.** The OSK isn't an app in the switcher
      sense; it's a system overlay.  Users who want a Cmd+Tab entry
      can comment this out and pay the focus-theft cost, but for
      first ship Accessory is the right answer.
    - **No menu bar.** Qt was already not driving a menu bar for us.

    Must run AFTER ``QApplication(sys.argv)`` so ``NSApp`` exists,
    and BEFORE ``app.exec()``.  Silently no-ops if pyobjc isn't
    available — degraded behaviour is "OSK works but steals focus",
    same as without this function at all.
    """
    try:
        from AppKit import (  # type: ignore[import-not-found]
            NSApp,
            NSApplicationActivationPolicyAccessory,
        )
    except ImportError as exc:
        _logger.warning(
            "pyobjc not available (%s) — cannot set Accessory activation "
            "policy. OSK will likely steal focus on click. "
            "Install: pip install pyobjc-framework-Cocoa",
            exc,
        )
        return

    try:
        # NSApp is the global NSApplication singleton — created by Qt
        # the moment QApplication is instantiated.
        if NSApp is None:
            _logger.warning(
                "NSApp is None — QApplication probably not yet created. "
                "Call _apply_macos_activation_policy() after "
                "QApplication(sys.argv)."
            )
            return
        NSApp.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
        _logger.info(
            "Applied NSApplicationActivationPolicyAccessory — "
            "OSK will not steal application focus on click"
        )
    except Exception as exc:
        _logger.warning(
            "Failed to set Accessory activation policy: %s — OSK may steal focus when clicked",
            exc,
        )


def _apply_macos_window_flags(root) -> None:
    """Configure the NSWindow backing the QML root for OSK behaviour.

    Three things we ask Cocoa for that Qt does not surface as flags:

    1. **Level = NSFloatingWindowLevel** (3) — float above ordinary
       windows.  Qt's ``WindowStaysOnTopHint`` already requests this
       on macOS, but we restate it for defence-in-depth and to match
       the Windows path, which places the window in the topmost
       Z-order band via ``SetWindowPos(HWND_TOPMOST)`` rather than a
       style bit (see :func:`_apply_windows_extended_styles`).
    2. **Collection behavior** — join all Spaces so the keyboard
       follows the user when they switch desktops, mark it transient
       so Mission Control won't try to tile it as a real window, and
       add the fullscreen-auxiliary flag so it appears above other
       apps that have entered fullscreen mode.
    3. **hidesOnDeactivate = NO** — keep the keyboard visible the
       moment focus moves to the text editor the user is typing into.
       The default is NO for NSWindow, but Qt sometimes flips it for
       Tool-class windows; setting it explicitly is cheap insurance.

    Qt's ``WindowDoesNotAcceptFocus`` already prevents the window
    from becoming key on macOS (it maps to ``canBecomeKeyWindow`` →
    NO), so we don't need to subclass NSWindow here.  If a future
    regression brings focus-theft back, swizzling
    ``canBecomeKeyWindow`` on the live window is the next step.

    Silently no-ops if pyobjc isn't installed — the OSK will still
    work, the keyboard just won't follow Spaces and may dip behind
    fullscreen apps.
    """
    try:
        import objc  # type: ignore[import-not-found]
        from AppKit import (  # type: ignore[import-not-found]
            NSFloatingWindowLevel,
            NSPanel,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorTransient,
        )
    except ImportError as exc:
        _logger.warning(
            "pyobjc not available (%s) — skipping macOS NSWindow tuning. "
            "Install with: pip install pyobjc-framework-Cocoa",
            exc,
        )
        return

    try:
        # root.winId() returns the native NSView pointer on macOS.
        # Wrap it as a real ObjC object and walk up to the NSWindow.
        ns_view = objc.objc_object(c_void_p=int(root.winId()))
        ns_window = ns_view.window()
        if ns_window is None:
            _logger.warning(
                "Could not obtain NSWindow from QML root — macOS window flags not applied"
            )
            return

        # The actual NSWindow class.  On Qt 6 / PySide6, QQuickWindow
        # produces ``QNSWindow`` here regardless of Qt.Tool flag —
        # Qt 5's Tool→NSPanel mapping was dropped.  We log at DEBUG
        # in case a future Qt version restores the panel mapping (we'd
        # see ``is_panel=True`` here and the NonactivatingPanel style
        # bit below would actually do something).  Focus theft is
        # *not* solved by the NSWindow tuning in this function — the
        # working solution is the ``CGEventPostToPid`` routing in
        # ``MacOSKeySynthesizer._post_event``.
        cls_name = ns_window.className()
        is_panel = bool(ns_window.isKindOfClass_(NSPanel))
        _logger.debug(
            "QML root NSWindow class=%s is_panel=%s",
            cls_name,
            is_panel,
        )

        ns_window.setLevel_(NSFloatingWindowLevel)
        ns_window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorTransient
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        ns_window.setHidesOnDeactivate_(False)

        # NSWindowStyleMaskNonactivatingPanel = 1 << 7 (0x80).  Only
        # NSPanel honors this bit; plain NSWindow ignores it.  We OR
        # it in opportunistically so that a future Qt version that
        # restores the Tool→NSPanel mapping would automatically pick
        # up the non-activating semantics with no further changes
        # here.  Today (Qt 6.10.x) ``is_panel`` is False and this
        # branch is dead — the focus story is handled by
        # CGEventPostToPid in the synthesizer.
        if is_panel:
            NS_WINDOW_STYLE_MASK_NONACTIVATING_PANEL = 1 << 7
            current_mask = int(ns_window.styleMask())
            new_mask = current_mask | NS_WINDOW_STYLE_MASK_NONACTIVATING_PANEL
            ns_window.setStyleMask_(new_mask)
            try:
                ns_window.setWorksWhenModal_(True)
            except Exception:
                pass
            _logger.debug(
                "NSPanel styleMask: %#x → %#x (added NonactivatingPanel)",
                current_mask,
                new_mask,
            )

        _logger.info(
            "Applied macOS NSWindow flags: "
            "floating level, all-Spaces, fullscreen-aux, hides-on-deactivate=NO"
        )
    except Exception as exc:
        _logger.warning("Failed to apply macOS NSWindow flags: %s", exc)


def _wire_snippets_window(root) -> None:
    """Apply OSK focus-suppression to the floating Snippets window.

    The Snippets window is a separate top-level ``Window`` declared in
    Main.qml (objectName ``snippetsWindow``) so it can float anywhere on
    the desktop, outside the keyboard. Like the main window it must never
    steal focus from the app the user is typing into, so on Windows it
    needs ``WS_EX_NOACTIVATE`` applied via Win32 — the Qt
    ``WindowDoesNotAcceptFocus`` flag alone doesn't stop click-activation
    there. The native handle only exists once the window has been shown,
    so we (re)apply on every visibility change rather than once at
    startup.

    No-op on non-Windows (the Qt flag is sufficient on X11/Wayland, and
    macOS uses the Accessory activation policy applied app-wide). Silent
    on any failure — the feature still works, it just might briefly take
    focus when first shown.
    """
    if CURRENT_PLATFORM != "windows":
        return
    try:
        from PySide6.QtCore import QObject

        win = root.findChild(QObject, "snippetsWindow")
        if win is None:
            _logger.warning("snippetsWindow not found; skipping focus-suppression")
            return

        def _apply() -> None:
            try:
                if win.property("visible"):
                    _apply_windows_extended_styles(win)
            except Exception as exc:  # pragma: no cover — defensive
                _logger.debug("snippetsWindow style apply failed: %s", exc)

        win.visibleChanged.connect(_apply)
        _logger.info("Wired snippetsWindow focus-suppression")
    except Exception as exc:  # pragma: no cover — defensive
        _logger.warning("Failed to wire snippetsWindow: %s", exc)


def _migrate_legacy_compat_settings() -> None:
    """Rename legacy compat-mode setting keys to the current names.

    Pre-rename keys: ``savedRemoteCompatMode``, ``savedRemoteCompatAuto``.
    Current keys:    ``savedCompatMode``,       ``savedCompatAutoDetect``.

    The rename happened when compat mode grew from "remote desktop only"
    to "remote desktop + IDEs that intercept keystrokes" — see CHANGELOG.
    Without migration, every existing user who had explicitly toggled
    either flag would silently revert to the new defaults.

    Idempotent: a ``compatSettingsMigrated`` flag prevents re-running.
    The legacy keys are removed once their values have been copied so
    they don't sit around polluting the registry indefinitely.

    Reads the QML ``Settings``-managed registry section directly via
    ``QSettings`` (with the same org/app names QML uses), so it must
    run after ``setOrganizationName`` / ``setApplicationName`` and
    before the QML engine instantiates its ``Settings`` element.
    """
    # QML's Settings element in Main.qml uses `category: "ui"`, which
    # scopes every key under a "ui" group in QSettings.  Match that
    # scope here so the keys we read/write line up.
    settings = QSettings()
    settings.beginGroup("ui")
    try:
        if settings.value("compatSettingsMigrated", False, type=bool):
            return
        legacy_manual_key = "savedRemoteCompatMode"
        legacy_auto_key = "savedRemoteCompatAuto"
        if settings.contains(legacy_manual_key):
            legacy_manual = settings.value(legacy_manual_key, False, type=bool)
            settings.setValue("savedCompatMode", legacy_manual)
            settings.remove(legacy_manual_key)
            _logger.info(
                "Migrated %s=%s → savedCompatMode",
                legacy_manual_key,
                legacy_manual,
            )
        if settings.contains(legacy_auto_key):
            legacy_auto = settings.value(legacy_auto_key, True, type=bool)
            settings.setValue("savedCompatAutoDetect", legacy_auto)
            settings.remove(legacy_auto_key)
            _logger.info(
                "Migrated %s=%s → savedCompatAutoDetect",
                legacy_auto_key,
                legacy_auto,
            )
        settings.setValue("compatSettingsMigrated", True)
    finally:
        settings.endGroup()
    settings.sync()


#: Sentinel recording that the one-time pre-fix log purge has run.  Its
#: presence is the only state: the contents are informational.
_LOG_PURGE_SENTINEL = ".log-privacy-purge"


def _purge_pre_fix_logs(config_dir: Path) -> int:
    """Delete diagnostic logs written before the typed-content fix.

    Releases up to and including 1.0.30 logged the user's typed text to
    ``alpha-osk.log`` at INFO, including up to 200 characters of the
    context buffer, and did so even while privacy mode was active.  The
    logging sites are fixed, but that only stops *new* leakage: an
    upgrading user still has up to four rotated files on disk holding a
    transcript of what they typed, potentially including a password.
    Purging them is part of the fix, not housekeeping.

    Runs once, guarded by a sentinel, so a user who later wants to keep
    logs across restarts is not fighting us.  Returns the number of
    files removed.  Never raises: a failure here must not stop the
    keyboard from starting.
    """
    sentinel = config_dir / _LOG_PURGE_SENTINEL
    if sentinel.exists():
        return 0

    removed = 0
    try:
        # Matches "alpha-osk.log" plus RotatingFileHandler's .1 / .2 / .3.
        for stale in sorted(config_dir.glob(f"{LOG_FILENAME}*")):
            try:
                stale.unlink()
                removed += 1
            except OSError:
                # A locked or already-gone file is not worth failing over;
                # the sentinel still gets written so we do not retry forever.
                continue
        sentinel.write_text(
            f"purged={removed} version={__version__}\n",
            encoding="utf-8",
        )
    except OSError:
        return removed
    return removed


def _configure_logging() -> Path | None:
    """Wire up stderr + rotating file logging.

    The frozen build runs without a console, so stderr is /dev/null, and
    file logging is the only way users can capture updater errors,
    crash tracebacks, etc. Returns the log path (or None on failure).

    Nothing written here may contain typed content: see the diagnostic
    log invariant in CLAUDE.md under "Where User Data Lives".
    """
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(fmt))
    root.addHandler(stream)

    log_path: Path | None = None
    purged = 0
    try:
        config_dir = get_config_dir()
        # Must happen before the handler opens the file: on Windows the
        # active log cannot be unlinked while the handler holds it.
        purged = _purge_pre_fix_logs(config_dir)
        log_path = config_dir / LOG_FILENAME
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(file_handler)
    except OSError as e:
        # Non-fatal: stderr handler still works in dev. Frozen users
        # without a writable APPDATA are vanishingly rare.
        root.warning("Could not open log file %s: %s", log_path, e)
        log_path = None

    if purged:
        # Logged after the handler is attached so it lands in the new file,
        # giving the user a record of why their old logs are gone.
        root.info(
            "Removed %d diagnostic log file(s) written before the "
            "typed-content fix; they could contain text you typed.",
            purged,
        )

    return log_path


#: Guards ``_install_exception_hooks`` against chaining onto itself if
#: ``main()`` is ever re-entered in one process (the test suite does it
#: deliberately).  Two copies of the hook would log every traceback twice.
_exception_hooks_installed = False


def _install_exception_hooks() -> None:
    """Route uncaught tracebacks into the diagnostic log.

    Without this the log only ever holds the failures somebody
    remembered to wrap in ``try`` / ``except`` + ``_logger.exception``.
    Everything else goes to ``sys.excepthook``, which writes to stderr,
    and a windowed PyInstaller build has no stderr: ``sys.stderr`` is
    ``None``, so the traceback for an actual crash is discarded at the
    exact moment it is worth the most.  That is the file we ask users to
    attach to a bug report, so it has to contain the crash.

    Both hooks chain to whatever was there before, so stderr still gets
    the traceback in a dev run and PySide's own handling is untouched.
    ``KeyboardInterrupt`` is passed straight through unlogged (Ctrl-C is
    a user action, not a fault), and the log call is wrapped because a
    hook that raises replaces the crash being reported with its own.

    Threads get the same treatment via ``threading.excepthook``: the
    dictation capture, the updater's download worker and Linux's AT-SPI
    listener all run off the main thread, and an exception there never
    reaches ``sys.excepthook`` at all.
    """
    global _exception_hooks_installed
    if _exception_hooks_installed:
        return

    previous_hook = sys.excepthook

    def _log_uncaught(
        exc_type: type[BaseException],
        exc: BaseException,
        tb: TracebackType | None,
    ) -> None:
        if not issubclass(exc_type, KeyboardInterrupt):
            try:
                _logger.critical("Uncaught exception", exc_info=(exc_type, exc, tb))
            except Exception:  # pragma: no cover - logging itself failed
                pass
        previous_hook(exc_type, exc, tb)

    previous_thread_hook = threading.excepthook

    def _log_uncaught_in_thread(args: threading.ExceptHookArgs) -> None:
        # SystemExit in a thread is how a worker asks to stop; it is not
        # a fault and the default hook already ignores it.
        if args.exc_type is not None and not issubclass(args.exc_type, SystemExit):
            try:
                _logger.critical(
                    "Uncaught exception in thread %s",
                    args.thread.name if args.thread is not None else "<unknown>",
                    exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
                )
            except Exception:  # pragma: no cover - logging itself failed
                pass
        previous_thread_hook(args)

    sys.excepthook = _log_uncaught
    threading.excepthook = _log_uncaught_in_thread
    _exception_hooks_installed = True


# Stable per-application identity for the Windows taskbar.  Must match the
# AppUserModelID set on the installer's Start Menu / Desktop shortcuts so a
# pinned shortcut groups with the running window.  Format convention is
# ``Company.Product`` (see Microsoft's AppUserModelID guidance).
APP_USER_MODEL_ID = "OKStudio.AlphaOSK"


def _set_windows_app_user_model_id() -> None:
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
    costs the taskbar icon, never startup).
    """
    if CURRENT_PLATFORM != "windows":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception as exc:  # pragma: no cover - platform/runtime dependent
        _logger.debug("SetCurrentProcessExplicitAppUserModelID failed: %s", exc)


def _toggle_keyboard_window(root, platform_name: str = CURRENT_PLATFORM) -> None:
    """Put the keyboard away, or bring it back.  One click each way.

    On Windows and Linux "away" means **minimized**, not hidden.  The OSK
    carries a normal taskbar entry (``WS_EX_TOOLWINDOW`` is actively
    cleared and ``WS_EX_APPWINDOW`` set, see
    :func:`_apply_windows_extended_styles`), so a minimized keyboard is
    still visibly parked somewhere the user can get at it, and the tray
    icon and the taskbar entry agree about where the window went.  Hiding
    it outright threw that away: the window vanished from the taskbar and
    the tray icon became the only route back.

    macOS keeps the hide/show pair because it has no taskbar or Dock entry
    to minimize *into* (the app runs under the Accessory activation policy
    — see :func:`_apply_macos_window_flags`), so there minimizing would be
    the version that leaves the user with only the tray icon.

    A **tucked** window falls back to hiding for the same reason: while
    parked off-screen it is DOCK-typed, which costs it the taskbar entry
    and makes ``showMinimized()`` inert, so minimizing there would be a
    dead tray icon rather than a stash.  ``tucked`` is QML-side state
    (X11 only, see ``toggleTuck`` in ``Main.qml``); reading it through
    ``property()`` yields ``None`` on any window that doesn't declare it,
    which is the right answer everywhere else.

    The restore branch is keyed on the window's own visibility rather than
    on what this function did last, so it also picks up a window the user
    minimized with the title-bar minus button or the taskbar.

    ``platform_name`` is a parameter rather than a direct read of
    ``CURRENT_PLATFORM`` so the tests can drive every branch on one host.
    """
    if root.visibility() in (QWindow.Visibility.Hidden, QWindow.Visibility.Minimized):
        root.showNormal()
        root.raise_()
    elif platform_name == "macos" or bool(root.property("tucked")):
        root.hide()
    else:
        root.showMinimized()


class _TrayClickRouter:
    """Turn raw tray activations into exactly one window toggle per click.

    *Which* activations count lives here; *what* the toggle does lives in
    :func:`_toggle_keyboard_window`.  Lives at module level, rather than as
    a closure in ``main()``, purely so the tests can reach it without a
    running ``QApplication``.

    **A double click toggles once, not twice.** Windows delivers a double
    click as Trigger, DoubleClick, Trigger, so acting on every activation
    would flip the window straight back to where it started.  Collapsing a
    burst into one toggle is also what lets the toggle fire *immediately*:
    the old code had to sit on each click for the full double-click
    interval to learn whether a second one was coming, and a tray click
    that lags half a second reads as broken.

    ``double_click_interval_ms`` is a callable (normally
    ``QApplication.doubleClickInterval``) so the live system setting is
    read per click rather than snapshotted at startup.
    """

    def __init__(
        self,
        toggle: Callable[[], None],
        double_click_interval_ms: Callable[[], int],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._toggle = toggle
        self._double_click_interval_ms = double_click_interval_ms
        self._clock = clock
        # Time of the last *toggle*, not the last activation: stamping this
        # on suppressed events too would let a stream of clicks keep
        # re-arming the guard, and the tray icon would go dead.
        self._last_toggle: float | None = None

    def __call__(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason not in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            return
        now = self._clock()
        if self._last_toggle is not None:
            if (now - self._last_toggle) * 1000.0 < self._double_click_interval_ms():
                return
        self._last_toggle = now
        self._toggle()


def main() -> int:
    """Launch the Alpha-OSK on-screen keyboard."""
    # CLI dispatch — the post-update relauncher re-invokes this binary
    # with ``--update-relauncher`` and runs in a detached process owned
    # by the user session, so it can launch the freshly-installed OSK
    # at user IL after the elevated installer has exited. Skipping the
    # singleton lock and the QApplication setup here keeps the helper
    # cheap and side-effect-free; see ``src/_update_relauncher.py``
    # for the polling logic and rationale.
    if "--update-relauncher" in sys.argv:
        from src._update_relauncher import run_relauncher

        return run_relauncher(sys.argv)

    log_path = _configure_logging()
    # Must follow _configure_logging: the hook is only worth anything
    # once there is a file handler for it to write into.
    _install_exception_hooks()
    if log_path is not None:
        _logger.info("Log file: %s", log_path)
    # Enable debug logging for prediction to see sources
    logging.getLogger("HybridPredictor").setLevel(logging.DEBUG)

    # Platform-specific environment setup (must happen before QApp)
    _setup_platform_env()

    # Claim an explicit taskbar identity before any window exists, so the
    # taskbar button keeps our icon instead of reverting to the generic
    # default once the OSK window appears.  No-op off Windows.
    _set_windows_app_user_model_id()

    # Log platform info
    pinfo = get_platform_info()
    _logger.info("Platform: %s", pinfo.get("platform"))
    if CURRENT_PLATFORM == "windows":
        _logger.info(
            "UIAccess: %s",
            "active" if pinfo.get("ui_access") else "not active",
        )

    # Use PassThrough rounding so Qt does not multiply logical window sizes
    # by a rounded scale factor when moving between monitors with different DPI.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("Alpha-OSK")
    app.setOrganizationName("alpha-osk")

    # macOS: drop into Accessory activation policy so clicking the
    # OSK doesn't yank the app to the foreground (and thereby steal
    # focus from the text field the user is typing into).  Must
    # happen after QApplication() because NSApp is created during
    # QApplication's __init__.
    if CURRENT_PLATFORM == "macos":
        _apply_macos_activation_policy()

    # Migrate any legacy "Remote Desktop Mode" setting keys to the new
    # "Compatibility Mode" names before QML's Settings element binds.
    # Idempotent — guarded by a flag, so it costs nothing after the
    # first run.
    _migrate_legacy_compat_settings()

    # Single-instance check.  Run before any expensive setup (QML
    # engine, prediction model load) so a duplicate launch returns
    # almost immediately.  We need QApplication for the message-loop
    # plumbing QSharedMemory uses on some platforms; otherwise this
    # would be cheaper still.
    if not _acquire_singleton_or_surface():
        return 0

    # Set app icon
    icon_file = _icon_path()
    if icon_file:
        app_icon = QIcon(str(icon_file))
        app.setWindowIcon(app_icon)
        _logger.info("App icon loaded: %s", icon_file)
    else:
        app_icon = QIcon()
        _logger.warning("App icon not found")

    # Create the bridge (auto-detects platform key synthesizer)
    bridge = KeyboardBridge()

    if not bridge.synthAvailable:
        if CURRENT_PLATFORM == "linux":
            _logger.warning(
                "No key synthesis tool found. Install xdotool: sudo apt install xdotool"
            )
        elif CURRENT_PLATFORM == "macos":
            _logger.warning(
                "macOS key synthesis unavailable. "
                "Install pyobjc-framework-Quartz, and grant Alpha-OSK "
                "Accessibility permission in System Settings → "
                "Privacy & Security → Accessibility."
            )
        else:
            _logger.warning(
                "Key synthesis not available. Keystrokes will not be sent to other applications."
            )

    # Set up QML engine
    engine = QQmlApplicationEngine()

    # Surface QML diagnostics through the Python logger.  Without this,
    # QQmlApplicationEngine silently swallows parse / binding errors and
    # rootObjects() just returns empty — past startup crashes were much
    # harder to diagnose than they needed to be.
    def _on_qml_warnings(warnings: list) -> None:
        for w in warnings:
            _logger.warning("QML: %s", w.toString())

    engine.warnings.connect(_on_qml_warnings)

    # Expose bridge to QML
    engine.rootContext().setContextProperty("keyboard", bridge)

    # Load QML
    main_qml = qml_path()
    if not main_qml.exists():
        _logger.error("QML file not found: %s", main_qml)
        return 1

    _logger.info("Loading QML from: %s", main_qml)
    engine.load(QUrl.fromLocalFile(str(main_qml)))

    if not engine.rootObjects():
        _logger.error("Failed to load QML — see preceding QML: warnings")
        return 1

    # Apply window flags for OSK behavior (cross-platform + Windows extras)
    root = engine.rootObjects()[0]
    if root:
        _apply_window_flags(root)
        _wire_snippets_window(root)

    # --- System tray icon ---
    tray = QSystemTrayIcon(app_icon, app)
    tray_menu = QMenu()
    show_action = tray_menu.addAction(
        "Show / Hide" if CURRENT_PLATFORM == "macos" else "Show / Minimize"
    )
    tray_menu.addSeparator()
    quit_action = tray_menu.addAction("Quit Alpha-OSK")

    def _toggle_visibility() -> None:
        _toggle_keyboard_window(root)

    # Held in a local (not inlined into connect) so the router outlives the
    # call: PySide does not promise to keep a strong reference to a callable
    # object used as a slot, and main() is on the stack for the app's life.
    tray_click_router = _TrayClickRouter(_toggle_visibility, app.doubleClickInterval)

    show_action.triggered.connect(_toggle_visibility)
    tray.activated.connect(tray_click_router)
    quit_action.triggered.connect(app.quit)
    tray.setContextMenu(tray_menu)
    tray.setToolTip("Alpha-OSK")
    tray.show()
    _logger.info("System tray icon active")

    # Save state on quit, then stop background timers so nothing fires
    # after the bridge / predictor start being torn down.
    def _on_about_to_quit() -> None:
        if bridge.autoSaveOnExit:
            _logger.info("Auto-saving prediction model on exit...")
            bridge.savePredictionModel()
        bridge.saveAnalytics()
        bridge.shutdown()

    app.aboutToQuit.connect(_on_about_to_quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
