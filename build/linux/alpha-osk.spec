# -*- mode: python ; coding: utf-8 -*-
"""
Alpha-OSK PyInstaller Build Specification (Linux)
==================================================

Linux counterpart to ``build/windows/alpha-osk.spec``. Produces a
single-directory bundle under ``dist/alpha-osk/`` that can be run
directly or wrapped in an AppImage by ``build/linux/build.py``.

Differences vs the Windows spec:

- No ``.ico`` embedding and no UIAccess manifest (neither concept exists
  on Linux; the icon is declared via ``.desktop`` for AppImage/desktop
  integration).
- Excludes ``src.platform.windows`` (pulls in ``ctypes.windll`` at module
  scope via a type-ignore, but harmless to leave out of the Linux bundle).
- Drops the same QtWebEngine binaries — they land as ``.so`` files on
  Linux instead of ``.dll``, so the prefix filter handles both.

Usage
-----
From the project root::

    pip install pyinstaller
    pyinstaller build/linux/alpha-osk.spec

Output is ``dist/alpha-osk/alpha-osk`` (no extension).
"""

import os
from pathlib import Path

# This spec lives at build/linux/alpha-osk.spec — project root is 2 levels up.
PROJECT_ROOT = Path(SPECPATH).parent.parent

block_cipher = None

a = Analysis(
    [str(PROJECT_ROOT / 'build' / 'launcher.py')],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (str(PROJECT_ROOT / 'qml'), 'qml'),
        (str(PROJECT_ROOT / 'data'), 'data'),
        (str(PROJECT_ROOT / 'templates'), 'templates'),
        (str(PROJECT_ROOT / 'assets' / 'logo-1024.png'), 'assets'),
    ],
    hiddenimports=[
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtQml',
        'PySide6.QtQuick',
        'PySide6.QtQuickControls2',
        'src',
        'src.keyboard_app',
        'src.keyboard_bridge',
        'src.platform',
        'src.platform.base',
        'src.platform.linux',
        'src.platform.password_detect',
        'src.analytics',
        'src.prediction',
        'src.prediction.ngram_predictor',
        'src.prediction.ppm_predictor',
        'src.prediction.fuzzy_recognizer',
        'src.prediction.hybrid_predictor',
        'src.prediction.vocabulary_pack',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'src.platform.windows',
        'torch', 'torchvision', 'torchaudio',
        'transformers', 'huggingface_hub', 'tokenizers', 'safetensors',
        'numpy', 'scipy', 'pandas', 'matplotlib',
        'sklearn', 'scikit-learn',
        'PIL', 'cv2', 'openai',
        'pytest', 'pytest_cov', 'coverage', 'mypy', 'ruff',
        'IPython', 'notebook', 'jupyter',
        'pygments', 'pyinstaller',
        'pygame', 'pyvjoy',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineQuick',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel',
        'PySide6.QtWebChannelQuick',
        'PySide6.QtWebView',
        'PySide6.QtWebViewQuick',
    ],
    cipher=block_cipher,
    noarchive=False,
)

# Strip QtWebEngine shared libs — same reasoning as the Windows spec,
# except on Linux the files are libQt6WebEngineCore.so.6 etc.
_DROP_BINARY_PREFIXES = (
    'libQt6WebEngineCore', 'libQt6WebEngineQuick', 'libQt6WebEngine',
    'libQt6WebChannel', 'libQt6WebChannelQuick',
    'libQt6WebView', 'libQt6WebViewQuick',
    # Also match PySide6's naming variants
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

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='alpha-osk',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX is rarely worth it on Linux Qt bundles (slower startup, little
    # size win on already-stripped .so files). Leave disabled.
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='alpha-osk',
)
