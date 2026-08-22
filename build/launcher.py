"""
Alpha-OSK Launcher (PyInstaller Entry Point)
=============================================

This script is the entry point for the frozen (PyInstaller) executable.
It imports and runs the real app from the ``src`` package, which uses
relative imports that require a parent package context.

In development, ``run.py`` or ``python -m src.keyboard_app`` handles this.
In the frozen build, this launcher provides the bridge.
"""

import sys
import os


def _log_startup_failure(exc):
    """Get a startup crash into the diagnostic log before we report it.

    The frozen build has no console, so the ``raise`` at the end of
    ``main()`` writes the traceback to a ``sys.stderr`` that is ``None``
    and it is lost.  This is the one crash class the launcher catches,
    which makes it the one crash class that would otherwise leave the
    user with a dialog holding a single line of exception text and
    nothing to attach to a bug report.

    Best-effort throughout: if the failure happened before
    ``_configure_logging`` ran there are no handlers yet, so we try to
    wire them up here.  A failure inside this function must never
    replace the crash it is reporting.
    """
    try:
        import logging

        if not logging.getLogger().handlers:
            from src.keyboard_app import _configure_logging

            _configure_logging()
        logging.getLogger("Launcher").critical(
            "Alpha-OSK failed to start", exc_info=exc
        )
    except Exception:
        pass


def _log_path_hint():
    """Return the diagnostic log's path for the error dialog, or ""."""
    try:
        from src.platform import get_log_path

        return str(get_log_path())
    except Exception:
        return ""


def main():
    # When frozen, ensure the bundle directory is on the path
    if getattr(sys, 'frozen', False):
        bundle_dir = os.path.dirname(sys.executable)
        if bundle_dir not in sys.path:
            sys.path.insert(0, bundle_dir)

    try:
        from src.keyboard_app import main as app_main
        app_main()
    except Exception as e:
        _log_startup_failure(e)
        # Show error dialog since there's no console
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            log_path = _log_path_hint()
            log_line = (
                f"The full details are in:\n{log_path}\n\n" if log_path else ""
            )
            QMessageBox.critical(
                None, "Alpha-OSK Error",
                f"Failed to start Alpha-OSK:\n\n{e}\n\n"
                f"{log_line}"
                f"Please report this at:\n"
                f"https://github.com/owenpkent/alpha-osk/issues"
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
