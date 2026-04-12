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
        # Show error dialog since there's no console
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(
                None, "Alpha-OSK Error",
                f"Failed to start Alpha-OSK:\n\n{e}\n\n"
                f"Please report this at:\n"
                f"https://github.com/okstudio1/alpha-osk/issues"
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
