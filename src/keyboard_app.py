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

See Also
--------
- ``src/platform/`` — OS-specific key synthesis backends.
- ``docs/PLATFORM_ARCHITECTURE.md`` — design rationale.
- ``docs/WINDOWS.md`` — Windows build / signing guide.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSharedMemory, QTimer, QUrl
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

    Prefers ``.ico`` on Windows and falls back to the PNG shipped in
    ``assets/`` on Linux (the PyInstaller Linux spec copies it into
    ``_internal/assets/`` next to the executable).
    """
    root = _project_root()
    exe_dir = Path(sys.executable).parent
    candidates = [
        root / "build" / "windows" / "alpha-osk.ico",
        root / "alpha-osk.ico",
        exe_dir / "alpha-osk.ico",
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
    root.setFlags(
        Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.FramelessWindowHint
        | Qt.WindowType.WindowDoesNotAcceptFocus
    )

    # Windows-specific: apply WS_EX_NOACTIVATE via Win32 API
    if CURRENT_PLATFORM == "windows":
        _apply_windows_extended_styles(root)


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
