"""
Alpha-OSK Update Helper (PyInstaller Entry Point)
==================================================

Entry point for ``alpha-osk-relauncher.exe``, the second exe in the Windows
bundle (see ``build/windows/alpha-osk.spec``). It shows the update screen
and relaunches the keyboard after an auto-update, and it is a separate exe
because it must not request UIAccess: the keyboard does, and Windows
refuses to start a UIAccess image from ``%TEMP%``, where the updater stages
this helper. See ``src/_update_relauncher.py`` for the flow.

Deliberately has none of the launcher's startup: no singleton lock, no
QML engine, no prediction model.  It is started by the updater with explicit
arguments and nothing else.
"""

import os
import sys


def _log_crash(exc):
    """A crash before the helper's own logging is up is otherwise invisible.

    The helper is windowed and detached, so there is no console to print to;
    a line in ``relauncher.log`` is the only post-mortem there will be.
    Best-effort: a failure here must never replace the crash it reports.
    """
    try:
        import logging

        from src.platform import get_config_dir

        config_dir = get_config_dir()
        config_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(config_dir / "relauncher.log"),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        logging.getLogger("RelauncherEntry").critical(
            "The update helper failed to start", exc_info=exc
        )
    except Exception:
        pass


def main():
    # When frozen, ensure the bundle directory is on the path
    if getattr(sys, "frozen", False):
        bundle_dir = os.path.dirname(sys.executable)
        if bundle_dir not in sys.path:
            sys.path.insert(0, bundle_dir)

    try:
        from src._update_relauncher import run_relauncher

        return run_relauncher(sys.argv)
    except Exception as exc:
        _log_crash(exc)
        raise


if __name__ == "__main__":
    sys.exit(main())
