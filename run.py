#!/usr/bin/env python3
"""
Alpha-OSK Launcher
==================

Cross-platform launcher that handles virtual environment setup, dependency
checking, and application startup for **Linux**, **Windows**, and **macOS**.

Usage::

    python run.py              # Launch the on-screen keyboard
    python run.py --dashboard  # Launch the project dashboard

What it does:

1. Detects the current platform (Linux / Windows / macOS).
2. Creates a Python virtual environment (``venv/``) if it doesn't exist.
3. Installs PySide6 and other dependencies from ``requirements.txt``.
4. Checks for platform-specific system dependencies:
   - **Linux**: ``xdotool`` (X11) or ``ydotool`` (Wayland).
   - **Windows**: No external tools needed — uses Win32 ``SendInput``.
   - **macOS**: pyobjc frameworks (auto-installed in the venv); user
     must grant Accessibility permission in System Settings on first
     keystroke attempt.
5. Launches ``src.keyboard_app`` inside the virtual environment.

See Also:
    - ``docs/WINDOWS.md`` — Windows-specific setup guide.
    - ``docs/PLATFORM_ARCHITECTURE.md`` — cross-platform design.
"""

import sys
import subprocess
import os
import shutil
import venv
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()

# Platform detection
IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"


def check_python_version():
    """Check if Python version is compatible (3.9+)."""
    if sys.version_info < (3, 9):
        print("ERROR: Python 3.9 or higher is required")
        print(f"Current version: {sys.version}")
        return False
    return True


def check_system_deps():
    """
    Check for platform-specific system-level dependencies.

    - **Linux**: Warns if neither xdotool nor ydotool are found.
    - **Windows**: No external dependencies needed (SendInput is built-in).
    - **macOS**: pyobjc frameworks are installed as Python deps; the
      Accessibility TCC grant is the only runtime requirement, and we
      can't check it without prompting (the prompt itself happens on
      first ``CGEventPost``), so we just print a reminder.

    Returns:
        List of warning strings (empty if all deps are satisfied).
    """
    warnings = []

    if IS_LINUX:
        if not shutil.which("xdotool") and not shutil.which("ydotool"):
            warnings.append(
                "  WARNING: Neither xdotool nor ydotool found.\n"
                "  Key synthesis won't work. Install with:\n"
                "    sudo apt install xdotool"
            )
    elif IS_WINDOWS:
        # SendInput is always available via ctypes — no external deps.
        # But warn if running from a non-standard location without UIAccess.
        pass
    elif IS_MACOS:
        # First-run reminder. The TCC prompt itself appears the first
        # time the app tries to post a key event; surfacing this hint
        # up front saves the user a "why isn't it typing" debugging
        # round.
        warnings.append(
            "  NOTE: On first keystroke, macOS will prompt for Accessibility\n"
            "  permission. Open System Settings → Privacy & Security →\n"
            "  Accessibility and enable Alpha-OSK (or your terminal /\n"
            "  Python interpreter, while running from source)."
        )
    else:
        warnings.append(
            f"  WARNING: Unsupported platform ({sys.platform}). "
            "Alpha-OSK supports Linux, Windows, and macOS."
        )

    return warnings


def setup_virtual_environment():
    """Create virtual environment if it doesn't exist."""
    venv_path = SCRIPT_DIR / "venv"

    if not venv_path.exists():
        print("Creating virtual environment...")
        try:
            venv.create(str(venv_path), with_pip=True)
            print("Virtual environment created successfully!")
        except Exception as e:
            print(f"ERROR: Failed to create virtual environment: {e}")
            return False

    return True


def get_venv_python():
    """
    Get the path to the Python executable inside the virtual environment.

    - **Linux / macOS**: ``venv/bin/python``
    - **Windows**: ``venv/Scripts/python.exe``
    """
    if IS_WINDOWS:
        return SCRIPT_DIR / "venv" / "Scripts" / "python.exe"
    return SCRIPT_DIR / "venv" / "bin" / "python"


def check_dependencies():
    """Check if required packages are installed; install if missing."""
    venv_python = get_venv_python()

    if not venv_python.exists():
        print("ERROR: Virtual environment Python not found")
        print(f"  Expected at: {venv_python}")
        return False

    # Check if PySide6 is importable
    try:
        result = subprocess.run(
            [str(venv_python), "-c", "import PySide6"],
            capture_output=True,
            text=True,
            # Output is captured; CREATE_NO_WINDOW suppresses the flash of a
            # console window that Windows would otherwise open when the
            # parent has no inherited console (e.g. launched via Explorer
            # or after re-elevation).  No effect on POSIX.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if result.returncode != 0:
            print("Installing dependencies in virtual environment...")
            subprocess.check_call(
                [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"],
                stdout=subprocess.DEVNULL,
            )
            subprocess.check_call(
                [str(venv_python), "-m", "pip", "install", "-r",
                 str(SCRIPT_DIR / "requirements.txt")],
            )
            print("Dependencies installed successfully!")

        return True

    except subprocess.CalledProcessError as e:
        print(f"ERROR: Failed to install dependencies: {e}")
        return False
    except Exception as e:
        print(f"ERROR: Unexpected error during dependency check: {e}")
        return False


def run_keyboard():
    """Launch the on-screen keyboard via the virtual environment."""
    venv_python = get_venv_python()
    try:
        result = subprocess.run(
            [str(venv_python), "-m", "src.keyboard_app"],
            cwd=str(SCRIPT_DIR),
        )
        return result.returncode
    except Exception as e:
        print(f"ERROR: Failed to run keyboard: {e}")
        return 1


def run_dashboard():
    """Launch the project dashboard (simple HTTP server)."""
    import http.server
    import socketserver
    import webbrowser
    from functools import partial

    port = 8080
    templates_dir = SCRIPT_DIR / "templates"

    if not templates_dir.exists():
        print(f"Error: Templates directory not found: {templates_dir}")
        return 1

    class DashboardHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/" or self.path == "":
                self.path = "/dashboard.html"
            elif self.path == "/slides" or self.path == "/slides/":
                self.path = "/slides.html"
            return super().do_GET()

        def log_message(self, format, *args):
            print(f"[Dashboard] {args[0]}")

    handler = partial(DashboardHandler, directory=str(templates_dir))
    with socketserver.TCPServer(("", port), handler) as httpd:
        url = f"http://localhost:{port}"
        print(f"Dashboard: {url}")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down dashboard...")
    return 0


def ensure_admin_windows():
    """
    On Windows, re-launch the process with administrator privileges if needed.

    SendInput is blocked by UIPI when the keyboard process runs at a lower
    integrity level than the focused window.  Running as admin bypasses UIPI
    for all standard applications.  (Full elevated-window + UAC-screen support
    still requires an EV-signed UIAccess binary — see docs/WINDOWS.md.)

    If already admin, returns immediately.  Otherwise, triggers a UAC prompt
    and re-launches; the original process then exits.
    """
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        if ctypes.windll.shell32.IsUserAnAdmin():
            return  # Already elevated
        print("Re-launching with administrator privileges (required for keystroke injection)...")
        args = " ".join(f'"{a}"' for a in sys.argv)
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, args, str(SCRIPT_DIR), 1
        )
        if ret > 32:
            sys.exit(0)  # Elevated process launched successfully; exit this one
        print("WARNING: Could not obtain admin rights. Keystroke injection may not work.")
    except Exception as e:
        print(f"WARNING: Admin check failed ({e}). Keystroke injection may not work.")


def main():
    """Main launcher function."""
    if IS_WINDOWS:
        platform_name = "Windows"
    elif IS_LINUX:
        platform_name = "Linux"
    elif IS_MACOS:
        platform_name = "macOS"
    else:
        platform_name = sys.platform
    print("=" * 50)
    print(f"  Alpha-OSK — On-Screen Keyboard for {platform_name}")
    print("=" * 50)
    print()

    # Windows: elevate before doing anything else so the keyboard process
    # inherits admin rights and SendInput can reach all windows.
    ensure_admin_windows()

    os.chdir(SCRIPT_DIR)

    # Dashboard mode
    if "--dashboard" in sys.argv:
        return run_dashboard()

    # Check Python version
    if not check_python_version():
        return 1

    # Check system dependencies
    warnings = check_system_deps()
    for w in warnings:
        print(w)
    if warnings:
        print()

    # Setup virtual environment
    if not setup_virtual_environment():
        return 1

    # Check/install Python dependencies
    if not check_dependencies():
        return 1

    print("Starting Alpha-OSK keyboard...")
    print()

    try:
        return run_keyboard()
    except KeyboardInterrupt:
        print("\nKeyboard closed.")
        return 0
    except Exception as e:
        print(f"\nERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
