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
  ``SetWindowLong`` to apply ``WS_EX_NOACTIVATE`` (focus-suppression)
  and ``WS_EX_TOPMOST`` (defence-in-depth on the always-on-top behaviour).
  ``WS_EX_TOOLWINDOW`` and ``Qt.Tool`` are deliberately *not* applied —
  they suppressed the taskbar entry, leaving the standard minimize
  button with nowhere to go.

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
from pathlib import Path

from PySide6.QtCore import QSettings, QSharedMemory, Qt, QTimer, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .keyboard_bridge import KeyboardBridge
from .platform import CURRENT_PLATFORM, get_config_dir, get_platform_info

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

        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )
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
        _apply_windows_extended_styles(root)
    elif CURRENT_PLATFORM == "macos":
        _apply_macos_window_flags(root)


def _apply_windows_extended_styles(root) -> None:
    """
    Use Win32 ``SetWindowLongW`` to add extended window styles that Qt
    cannot express through its own flag system.

    Styles applied:

    - **WS_EX_NOACTIVATE** (``0x08000000``): The window is never
      activated when clicked.  This is *critical* for an OSK — without
      it, clicking a key would move focus away from the user's text
      editor.
    - **WS_EX_TOPMOST** (``0x00000008``): Redundant with Qt's
      ``WindowStaysOnTopHint`` but set explicitly for defence-in-depth.

    ``WS_EX_TOOLWINDOW`` was *removed* because it suppressed the
    taskbar entry, leaving minimize with nowhere to go — the user
    expects standard Windows behaviour (click the taskbar to
    restore).  The trade-off is that the OSK now appears in Alt+Tab,
    which is acceptable.

    Requires the window to have a valid ``winId()`` (i.e. the native
    window handle has been created).
    """
    try:
        import ctypes
        from ctypes import wintypes

        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOPMOST = 0x00000008

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

        new_style = current | WS_EX_NOACTIVATE | WS_EX_TOPMOST
        kernel32.SetLastError(0)
        prev = user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
        if prev == 0 and kernel32.GetLastError() != 0:
            _logger.warning(
                "SetWindowLongW failed (err=%d); WS_EX_NOACTIVATE may not be active",
                kernel32.GetLastError(),
            )
            return

        # SetWindowPos with SWP_FRAMECHANGED forces the system to re-read the
        # extended style we just set.  Without this, WS_EX_NOACTIVATE may not
        # take effect and clicks on key buttons will steal focus before
        # SendInput fires.
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020
        ok = user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        if not ok:
            _logger.warning(
                "SetWindowPos refresh failed (err=%d); flags set but may not be live yet",
                kernel32.GetLastError(),
            )

        _logger.info(
            "Applied Windows extended styles: "
            "WS_EX_NOACTIVATE | WS_EX_TOPMOST"
        )
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
            "Failed to set Accessory activation policy: %s — OSK may "
            "steal focus when clicked",
            exc,
        )


def _apply_macos_window_flags(root) -> None:
    """Configure the NSWindow backing the QML root for OSK behaviour.

    Three things we ask Cocoa for that Qt does not surface as flags:

    1. **Level = NSFloatingWindowLevel** (3) — float above ordinary
       windows.  Qt's ``WindowStaysOnTopHint`` already requests this
       on macOS, but we restate it for defence-in-depth and to match
       the Windows path (``WS_EX_TOPMOST``).
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
                "Could not obtain NSWindow from QML root — macOS window "
                "flags not applied"
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
            cls_name, is_panel,
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
                current_mask, new_mask,
            )

        _logger.info(
            "Applied macOS NSWindow flags: "
            "floating level, all-Spaces, fullscreen-aux, hides-on-deactivate=NO"
        )
    except Exception as exc:
        _logger.warning("Failed to apply macOS NSWindow flags: %s", exc)


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
                legacy_manual_key, legacy_manual,
            )
        if settings.contains(legacy_auto_key):
            legacy_auto = settings.value(legacy_auto_key, True, type=bool)
            settings.setValue("savedCompatAutoDetect", legacy_auto)
            settings.remove(legacy_auto_key)
            _logger.info(
                "Migrated %s=%s → savedCompatAutoDetect",
                legacy_auto_key, legacy_auto,
            )
        settings.setValue("compatSettingsMigrated", True)
    finally:
        settings.endGroup()
    settings.sync()


def _configure_logging() -> Path | None:
    """Wire up stderr + rotating file logging.

    The frozen build runs without a console, so stderr is /dev/null —
    file logging is the only way users can capture updater errors,
    crash tracebacks, etc. Returns the log path (or None on failure).
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
    try:
        log_path = get_config_dir() / "alpha-osk.log"
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(fmt))
        root.addHandler(file_handler)
    except OSError as e:
        # Non-fatal: stderr handler still works in dev. Frozen users
        # without a writable APPDATA are vanishingly rare.
        root.warning("Could not open log file %s: %s", log_path, e)
        log_path = None

    return log_path


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
    if log_path is not None:
        _logger.info("Log file: %s", log_path)
    # Enable debug logging for prediction to see sources
    logging.getLogger("HybridPredictor").setLevel(logging.DEBUG)

    # Platform-specific environment setup (must happen before QApp)
    _setup_platform_env()

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
                "No key synthesis tool found. "
                "Install xdotool: sudo apt install xdotool"
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
                "Key synthesis not available. "
                "Keystrokes will not be sent to other applications."
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

    # --- System tray icon ---
    tray = QSystemTrayIcon(app_icon, app)
    tray_menu = QMenu()
    show_action = tray_menu.addAction("Show / Hide")
    tray_menu.addSeparator()
    quit_action = tray_menu.addAction("Quit Alpha-OSK")

    def _toggle_visibility() -> None:
        if root.isVisible():
            root.hide()
        else:
            root.show()
            root.raise_()

    def _minimize_window() -> None:
        """Minimize on tray double-click, matching the in-window − button.

        Now that the OSK has a normal taskbar entry (no WS_EX_TOOLWINDOW),
        ``showMinimized()`` does what users expect: drops to the taskbar,
        clicking the taskbar restores. The tray icon stays as a backup
        path — the single-click toggle still works — but isn't the only
        way back anymore.
        """
        root.showMinimized()

    # Tray single-click vs. double-click: we want a single click to
    # toggle show/hide (current behaviour) and a double click to
    # minimize.  On Windows, Qt delivers Trigger first, then DoubleClick,
    # for a double click — so we start a timer on Trigger and only
    # fire the single-click action if no DoubleClick arrives within the
    # system's double-click interval.  If DoubleClick arrives first,
    # the pending Trigger is cancelled.
    tray_single_click_timer = QTimer(app)
    tray_single_click_timer.setSingleShot(True)
    tray_single_click_timer.setInterval(app.doubleClickInterval())
    tray_single_click_timer.timeout.connect(_toggle_visibility)

    def _on_tray_activated(reason: "QSystemTrayIcon.ActivationReason") -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            tray_single_click_timer.start()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            tray_single_click_timer.stop()
            _minimize_window()

    show_action.triggered.connect(_toggle_visibility)
    tray.activated.connect(_on_tray_activated)
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
