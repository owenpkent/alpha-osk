"""Single source of truth for the Alpha-OSK version.

Read by ``src/updater.py`` (to compare against GitHub Releases) and
``build/windows/build.py`` (to name installer + write registry version).
Bumping the version is a one-line change here.
"""

__version__ = "1.0.15"
