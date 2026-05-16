"""
Alpha-OSK Platform Abstraction Layer
=====================================

Provides cross-platform key synthesis and window management for Alpha-OSK.

The platform layer detects the current operating system at import time and
exposes a factory function ``create_key_synthesizer()`` that returns the
correct backend:

- **Linux**: Uses ``xdotool`` (X11) or ``ydotool`` (Wayland) via subprocess.
- **Windows**: Uses the Win32 ``SendInput`` API via ctypes for low-level
  keyboard injection, with optional UIAccess elevation for secure-desktop
  support (requires an EV-signed binary with a UIAccess manifest).
- **macOS**: Uses Quartz Event Services
  (``CGEventCreateKeyboardEvent`` / ``CGEventPost``) via pyobjc.  The
  process must hold the *Accessibility* TCC grant (System Settings →
  Privacy & Security → Accessibility) or posts silently fail — the
  first attempt prompts the user and the synthesizer no-ops until the
  grant arrives.

Usage::

    from src.platform import create_key_synthesizer, get_platform_info

    synth = create_key_synthesizer()
    if synth.is_available():
        synth.send_key("a")
        synth.send_text("hello world")
        synth.send_combination(["ctrl", "c"])

Architecture
------------
::

    src/platform/
    ├── __init__.py          # This file — factory + detection
    ├── base.py              # Abstract base class (KeySynthesizerBase)
    ├── linux.py             # Linux backend (xdotool / ydotool)
    ├── windows.py           # Windows backend (SendInput via ctypes)
    └── macos.py             # macOS backend (Quartz CGEvent via pyobjc)

See also: ``docs/PLATFORM_ARCHITECTURE.md`` for detailed design rationale.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import KeySynthesizerBase

_logger = logging.getLogger("Platform")

# Current platform identifier: "windows", "linux", "macos", or "unsupported"
CURRENT_PLATFORM: str

if sys.platform == "win32":
    CURRENT_PLATFORM = "windows"
elif sys.platform.startswith("linux"):
    CURRENT_PLATFORM = "linux"
elif sys.platform == "darwin":
    CURRENT_PLATFORM = "macos"
else:
    CURRENT_PLATFORM = "unsupported"


def create_key_synthesizer() -> "KeySynthesizerBase":
    """
    Factory: create the appropriate key synthesizer for the current OS.

    Returns:
        A concrete ``KeySynthesizerBase`` subclass instance.

    Raises:
        RuntimeError: If the current platform is not supported.

    Examples::

        synth = create_key_synthesizer()
        synth.send_key("Return")
        synth.send_text("Hello!")
    """
    if CURRENT_PLATFORM == "linux":
        from .linux import LinuxKeySynthesizer
        _logger.info("Creating Linux key synthesizer")
        return LinuxKeySynthesizer()
    elif CURRENT_PLATFORM == "windows":
        from .windows import WindowsKeySynthesizer
        _logger.info("Creating Windows key synthesizer")
        return WindowsKeySynthesizer()
    elif CURRENT_PLATFORM == "macos":
        from .macos import MacOSKeySynthesizer
        _logger.info("Creating macOS key synthesizer")
        return MacOSKeySynthesizer()
    else:
        raise RuntimeError(
            f"Unsupported platform: {sys.platform}. "
            "Alpha-OSK supports Linux, Windows, and macOS."
        )


def get_platform_info() -> dict:
    """
    Return a dictionary of platform diagnostic information.

    Useful for logging, debugging, and the settings panel.

    Returns:
        Dict with keys: ``platform``, ``python``, ``synthesizer``,
        ``display_server`` (Linux only), ``ui_access`` (Windows only).
    """
    info: dict = {
        "platform": CURRENT_PLATFORM,
        "python": sys.version,
        "sys_platform": sys.platform,
    }

    if CURRENT_PLATFORM == "linux":
        import os
        info["display_server"] = (
            "wayland" if os.environ.get("WAYLAND_DISPLAY") else "x11"
        )
        import shutil
        info["xdotool"] = shutil.which("xdotool") is not None
        info["ydotool"] = shutil.which("ydotool") is not None
    elif CURRENT_PLATFORM == "windows":
        info["ui_access"] = _check_ui_access()
        info["windows_version"] = sys.getwindowsversion().major  # type: ignore[attr-defined]
    elif CURRENT_PLATFORM == "macos":
        info["accessibility_trusted"] = _check_macos_accessibility()
        info["display_server"] = "quartz"

    return info


def get_config_dir() -> Path:
    """
    Return the platform-appropriate configuration directory for Alpha-OSK.

    - **Windows**: ``%APPDATA%/alpha-osk``
    - **Linux**:   ``~/.config/alpha-osk``
    - **macOS**:   ``~/Library/Application Support/alpha-osk``

    The directory is created if it does not exist.

    Returns:
        pathlib.Path to the config directory.
    """
    from pathlib import Path

    if CURRENT_PLATFORM == "windows":
        import os
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        config_dir = base / "alpha-osk"
    elif CURRENT_PLATFORM == "macos":
        config_dir = Path.home() / "Library" / "Application Support" / "alpha-osk"
    else:
        config_dir = Path.home() / ".config" / "alpha-osk"

    config_dir.mkdir(parents=True, exist_ok=True)
    # Restrict permissions on POSIX (model files contain typed word history).
    # Windows uses ACLs on the user profile already; macOS Application Support
    # is per-user but tightening to 0700 is still defence in depth.
    if CURRENT_PLATFORM != "windows":
        config_dir.chmod(0o700)
    return config_dir


def get_model_dir() -> Path:
    """
    Return the platform-appropriate model storage directory.

    - **Windows**: ``%APPDATA%/alpha-osk/models``
    - **Linux**:   ``~/.config/alpha-osk/models``
    - **macOS**:   ``~/Library/Application Support/alpha-osk/models``

    Returns:
        pathlib.Path to the models directory (created if needed).
    """
    model_dir = get_config_dir() / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    if CURRENT_PLATFORM != "windows":
        model_dir.chmod(0o700)
    return model_dir


def _check_ui_access() -> bool:
    """
    Check whether the current process has UIAccess privileges on Windows.

    UIAccess allows an on-screen keyboard to send input to elevated windows
    and appear above the secure desktop (UAC prompts, lock screen). This
    requires the binary to be EV code-signed and have a UIAccess manifest.

    Returns:
        True if running with UIAccess privileges.
    """
    if CURRENT_PLATFORM != "windows":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        TOKEN_QUERY = 0x0008
        TokenUIAccess = 26  # TOKEN_INFORMATION_CLASS value

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        advapi32 = ctypes.windll.advapi32  # type: ignore[attr-defined]

        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
        ):
            return False

        ui_access = wintypes.DWORD(0)
        ret_len = wintypes.DWORD(0)
        result = advapi32.GetTokenInformation(
            token,
            TokenUIAccess,
            ctypes.byref(ui_access),
            ctypes.sizeof(ui_access),
            ctypes.byref(ret_len),
        )
        kernel32.CloseHandle(token)
        return bool(result and ui_access.value)
    except Exception:
        return False


def _check_macos_accessibility() -> bool:
    """Return True if this process is trusted for Accessibility on macOS.

    Posting synthetic keyboard events (``CGEventPost``) silently fails
    unless the process holds the Accessibility TCC grant in System
    Settings → Privacy & Security → Accessibility.  This helper does a
    *non-prompting* check using ``AXIsProcessTrusted`` — useful for
    surfacing the state in logs / the platform-info dialog without
    triggering the prompt repeatedly at import time.

    The synthesizer itself triggers the prompt the first time it tries
    to post an event; surfacing it here is purely informational.

    Returns False on any error (no pyobjc, framework call raised, etc.).
    """
    if CURRENT_PLATFORM != "macos":
        return False
    try:
        from ApplicationServices import AXIsProcessTrusted  # type: ignore[import-not-found]
    except ImportError:
        # pyobjc not installed — the synthesizer will warn at first use.
        return False
    try:
        return bool(AXIsProcessTrusted())
    except Exception:
        return False


__all__ = [
    "CURRENT_PLATFORM",
    "create_key_synthesizer",
    "get_platform_info",
    "get_config_dir",
    "get_model_dir",
]
