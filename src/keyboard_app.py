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

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from .keyboard_bridge import KeyboardBridge
from .platform import CURRENT_PLATFORM, get_platform_info

_logger = logging.getLogger("KeyboardApp")


def qml_path() -> Path:
    """Resolve the path to Main.qml relative to this file."""
    here = Path(__file__).resolve().parent
    project_root = here.parent
    return project_root / "qml" / "Main.qml"


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
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QGuiApplication(sys.argv)
    app.setApplicationName("Alpha-OSK")
    app.setOrganizationName("alpha-osk")

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
        _logger.error("Failed to load QML")
        return 1

    # Apply window flags for OSK behavior (cross-platform + Windows extras)
    root = engine.rootObjects()[0]
    if root:
        _apply_window_flags(root)

    # Save state on quit
    def _on_about_to_quit() -> None:
        if bridge.autoSaveOnExit:
            _logger.info("Auto-saving prediction model on exit...")
            bridge.savePredictionModel()
        bridge.saveAnalytics()

    app.aboutToQuit.connect(_on_about_to_quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
