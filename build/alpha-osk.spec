# -*- mode: python ; coding: utf-8 -*-
"""
Alpha-OSK PyInstaller Build Specification
==========================================

Builds a standalone Windows executable (.exe) for Alpha-OSK with:

- All Python dependencies bundled (PySide6, prediction engine, etc.)
- QML files and data files included as data resources.
- Windows UIAccess manifest embedded for EV code-signed builds.
- Single-directory output (not one-file) for faster startup.

Usage
-----
From the project root::

    pip install pyinstaller
    pyinstaller build/alpha-osk.spec

Output
------
The built application will be in ``dist/alpha-osk/``.  The main
executable is ``dist/alpha-osk/alpha-osk.exe``.

After Building
--------------
1. Sign with your EV certificate (see docs/WINDOWS.md).
2. Copy to ``C:\\Program Files\\Alpha-OSK\\`` for UIAccess to work.
3. Create a Start Menu shortcut if desired.

Notes
-----
- We use ``--windowed`` (no console window) since Alpha-OSK is a GUI app.
- The manifest is embedded via the ``manifest`` parameter.
- Hidden imports are listed for PySide6 QML plugins that PyInstaller
  doesn't auto-detect.
"""

import os
from pathlib import Path

# Project root is one level up from this spec file
PROJECT_ROOT = Path(SPECPATH).parent

block_cipher = None

a = Analysis(
    # Entry point — launcher handles frozen vs dev import paths
    [str(PROJECT_ROOT / 'build' / 'launcher.py')],

    pathex=[str(PROJECT_ROOT)],

    binaries=[],

    # Data files to bundle (source, destination_in_bundle)
    datas=[
        # QML UI files
        (str(PROJECT_ROOT / 'qml'), 'qml'),
        # Data files (dictionaries, training corpus)
        (str(PROJECT_ROOT / 'data'), 'data'),
        # Templates (dashboard)
        (str(PROJECT_ROOT / 'templates'), 'templates'),
        # App icon (used at runtime for system tray)
        (str(PROJECT_ROOT / 'build' / 'alpha-osk.ico'), '.'),
    ],

    # Hidden imports that PyInstaller misses
    hiddenimports=[
        # PySide6 QML modules
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickControls2',
        # Our modules
        'src',
        'src.keyboard_app',
        'src.keyboard_bridge',
        'src.platform',
        'src.platform.base',
        'src.platform.windows',
        'src.platform.linux',
        'src.platform.password_detect',
        'src.analytics',
        'src.prediction',
        'src.prediction.ngram_predictor',
        'src.prediction.ppm_predictor',
        'src.prediction.fuzzy_recognizer',
        'src.prediction.hybrid_predictor',
        # transformer_predictor excluded — requires torch (optional, not bundled)
        'src.prediction.vocabulary_pack',
    ],

    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Exclude Linux-only modules on Windows builds
        'ydotool',
        'xdotool',
        # Exclude heavy ML/science libraries (not needed — LLM predictor is optional)
        'torch', 'torchvision', 'torchaudio',
        'transformers', 'huggingface_hub', 'tokenizers', 'safetensors',
        'numpy', 'scipy', 'pandas', 'matplotlib',
        'sklearn', 'scikit-learn',
        'PIL', 'cv2', 'openai',
        # Exclude dev/test tools
        'pytest', 'pytest_cov', 'coverage', 'mypy', 'ruff',
        'IPython', 'notebook', 'jupyter',
        'pygments', 'pyinstaller',
        # Exclude other unneeded heavy packages
        'pygame', 'pyvjoy',
        # PySide6.QtWebEngineCore alone is ~193 MB — half the bundle —
        # and Alpha-OSK never embeds a web view.  PyInstaller pulls it
        # in transitively through PySide6's all-modules hook, so we
        # have to name every WebEngine / WebView / WebChannel module
        # explicitly to drop them.  If we ever add an in-app browser
        # for release notes etc., re-include these and re-measure.
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel',
        'PySide6.QtWebChannelQuick',
        'PySide6.QtWebView',
        'PySide6.QtWebViewQuick',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Filter unwanted Qt binaries out of the bundle.
#
# Module-level excludes (above) only stop Python imports — the PySide6
# PyInstaller hook still copies the matching Qt DLLs verbatim from
# site-packages.  To actually drop them we have to walk a.binaries
# (and a.datas) and remove entries by filename pattern.
#
# Qt6WebEngineCore.dll alone is 193 MB — half the bundle — and
# Alpha-OSK never embeds a browser.  If you ever add an in-app
# browser, prune this list and re-measure.
_DROP_BINARY_PREFIXES = (
    'Qt6WebEngineCore', 'Qt6WebEngineQuick', 'Qt6WebEngine',
    'Qt6WebChannel', 'Qt6WebChannelQuick',
    'Qt6WebView', 'Qt6WebViewQuick',
)


def _keep(entry):
    name = os.path.basename(entry[0]).lower()
    return not any(name.startswith(p.lower()) for p in _DROP_BINARY_PREFIXES)


_before_bins = len(a.binaries)
_before_data = len(a.datas)
a.binaries = [b for b in a.binaries if _keep(b)]
a.datas = [d for d in a.datas if _keep(d)]
print(f"[spec] Stripped {_before_bins - len(a.binaries)} binaries "
      f"and {_before_data - len(a.datas)} data entries "
      f"matching {_DROP_BINARY_PREFIXES!r}")

pyz = PYZ(
    a.pure,
    a.zipped_data,
    cipher=block_cipher,
)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='alpha-osk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # GUI application — no console window
    console=False,
    # Disable UPX for PySide6 DLLs (they don't compress well)
    upx_exclude=['PySide6'],
    # Embed the UIAccess manifest
    manifest=str(PROJECT_ROOT / 'build' / 'alpha-osk.exe.manifest'),
    # Icon for the executable (replace with a professional .ico if desired)
    icon=str(PROJECT_ROOT / 'build' / 'alpha-osk.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['PySide6'],
    name='alpha-osk',
)
