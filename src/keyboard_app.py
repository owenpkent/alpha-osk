"""
Keyboard Application - QML engine setup and window configuration.

Launches the on-screen keyboard as a PySide6/QML application with
proper window flags for an OSK (stays on top, doesn't steal focus).

Cross-Platform Behaviour
------------------------
- **Linux (X11)**: Sets ``QT_QPA_PLATFORM=xcb`` and uses Qt window flags
  ``WindowStaysOnTopHint | Tool | FramelessWindowHint |
  WindowDoesNotAcceptFocus`` to stay above other windows without stealing
  keyboard focus.

- **Windows**: Uses the same Qt flags.  When the binary is EV code-signed
  with a ``UIAccess="true"`` manifest, the keyboard can also appear above
  UAC prompts and elevated windows.  Additionally, on Windows we call
  ``SetWindowLong`` to apply ``WS_EX_NOACTIVATE`` and
  ``WS_EX_TOOLWINDOW`` so the OS never gives our window keyboard focus
  and it doesn't appear in the Alt+Tab list.

See Also
--------
- ``src/platform/`` — OS-specific key synthesis backends.
- ``docs/PLATFORM_ARCHITECTURE.md`` — design rationale.
- ``docs/WINDOWS.md`` — Windows build / signing guide.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .keyboard_bridge import KeyboardBridge
from .platform import CURRENT_PLATFORM, get_platform_info

_logger = logging.getLogger("KeyboardApp")


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
    - Classified as a Tool window (not shown in taskbar on Linux).

    On Windows, additional Win32 extended styles are applied via
    ``SetWindowLongW`` to ensure ``WS_EX_NOACTIVATE`` (never receives
    focus on click) and ``WS_EX_TOOLWINDOW`` (hidden from Alt+Tab).
    """
    # Qt flags — work on all platforms
    root.setFlags(
        Qt.WindowType.WindowStaysOnTopHint
        | Qt.WindowType.Tool
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
    - **WS_EX_TOOLWINDOW** (``0x00000080``): The window is not shown in
      the taskbar or Alt+Tab switcher.
    - **WS_EX_TOPMOST** (``0x00000008``): Redundant with Qt's
      ``WindowStaysOnTopHint`` but set explicitly for defence-in-depth.

    Requires the window to have a valid ``winId()`` (i.e. the native
    window handle has been created).
    """
    try:
        import ctypes

        GWL_EXSTYLE = -20
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TOOLWINDOW = 0x00000080
        WS_EX_TOPMOST = 0x00000008

        user32 = ctypes.windll.user32
        hwnd = int(root.winId())

        # Get current extended style
        current = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        # Add our flags
        new_style = current | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)

        # SetWindowPos with SWP_FRAMECHANGED forces the system to re-read the
        # extended style we just set.  Without this, WS_EX_NOACTIVATE may not
        # take effect and clicks on key buttons will steal focus before
        # SendInput fires.
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOZORDER = 0x0004
        SWP_NOACTIVATE = 0x0010
        SWP_FRAMECHANGED = 0x0020
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )

        _logger.info(
            "Applied Windows extended styles: "
            "WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST"
        )
    except Exception as e:
        _logger.warning("Failed to apply Windows extended styles: %s", e)


def main() -> int:
    """Launch the Alpha-OSK on-screen keyboard."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(name)s] %(levelname)s: %(message)s",
    )
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
        """Send the window to the Windows taskbar minimized state.

        Matches the in-window ``−`` button (``Window.Minimized`` in QML).
        Triggered by a double-click on the tray icon — the single-click
        path still toggles show/hide.
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
