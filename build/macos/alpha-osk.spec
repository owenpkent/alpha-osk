# -*- mode: python ; coding: utf-8 -*-
"""
Alpha-OSK PyInstaller Build Specification (macOS)
==================================================

macOS counterpart to ``build/windows/alpha-osk.spec`` and
``build/linux/alpha-osk.spec``.  Produces a one-directory bundle under
``dist/alpha-osk/`` plus a wrapped ``Alpha-OSK.app`` under ``dist/``
via PyInstaller's ``BUNDLE`` step.

Differences vs the Linux spec:

- Adds an ``Alpha-OSK.app`` bundle target with Info.plist metadata.
- Includes ``src.platform.macos`` in hidden imports.
- Excludes ``src.platform.windows`` and ``src.platform.linux`` to keep
  the bundle slim — the platform layer imports them lazily by name
  inside ``create_key_synthesizer``, so excluded modules never run.
- Drops QtWebEngine ``.dylib`` files the same way the Linux spec drops
  the ``.so`` variants — prefix match covers both.

Usage
-----
From the project root::

    pip install pyinstaller
    pyinstaller build/macos/alpha-osk.spec

Output:
- ``dist/alpha-osk/alpha-osk``       — raw executable bundle
- ``dist/Alpha-OSK.app/``            — macOS app bundle (drag into /Applications)

Code signing and notarization happen in a later phase — this spec does
not embed any signing identity.  An unsigned .app will warn on first
launch and can be opened via right-click → Open.
"""

import os
from pathlib import Path

# This spec lives at build/macos/alpha-osk.spec — project root is 2 levels up.
PROJECT_ROOT = Path(SPECPATH).parent.parent

# Pull __version__ in without importing the whole src package.
_version_ns: dict = {}
exec((PROJECT_ROOT / "src" / "__version__.py").read_text(), _version_ns)
APP_VERSION = _version_ns["__version__"]

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
        'src.platform.macos',
        'src.platform.password_detect',
        'src.analytics',
        'src.prediction',
        'src.prediction.ngram_predictor',
        'src.prediction.ppm_predictor',
        'src.prediction.fuzzy_recognizer',
        'src.prediction.hybrid_predictor',
        'src.prediction.vocabulary_pack',
        # pyobjc framework entry points used at runtime
        'Quartz',
        'AppKit',
        'ApplicationServices',
        'objc',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'src.platform.windows',
        'src.platform.linux',
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

# Strip QtWebEngine shared libs.  On macOS these arrive as
# ``QtWebEngineCore.framework/Versions/A/QtWebEngineCore`` (a Mach-O
# inside a framework directory) and as ``libQt6WebEngineCore.dylib`` in
# some packaging modes — match both via the basename prefix.
_DROP_BINARY_PREFIXES = (
    'libQt6WebEngineCore', 'libQt6WebEngineQuick', 'libQt6WebEngine',
    'libQt6WebChannel', 'libQt6WebChannelQuick',
    'libQt6WebView', 'libQt6WebViewQuick',
    'Qt6WebEngineCore', 'Qt6WebEngineQuick', 'Qt6WebEngine',
    'Qt6WebChannel', 'Qt6WebChannelQuick',
    'Qt6WebView', 'Qt6WebViewQuick',
    'QtWebEngineCore', 'QtWebEngineQuick', 'QtWebEngine',
    'QtWebChannel', 'QtWebChannelQuick',
    'QtWebView', 'QtWebViewQuick',
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

# ---------------------------------------------------------------------------
#  .app bundle
# ---------------------------------------------------------------------------
#
# BUNDLE() wraps the COLLECT output into a proper macOS app.  The
# Info.plist below is the minimum to:
#   - Show the right name in the Dock and About dialog.
#   - Survive Gatekeeper's "damaged" check for unsigned builds (only
#     when launched via right-click → Open; signing comes later).
#   - Let macOS know we're a regular app (Dock icon, app switcher) by
#     leaving LSUIElement at the default (False).
#
# When a Developer ID certificate is wired up, BUNDLE() can take a
# ``codesign_identity`` argument; today we leave it out so unsigned
# builds work from a clean checkout.

# Icon: prefer .icns (native), fall back to .png so unsigned dev
# builds still surface *something*.  Generating the .icns asset is a
# follow-up alongside Windows .ico and Linux .png in docs/build/BRANDING.md.
_icns = PROJECT_ROOT / 'build' / 'macos' / 'alpha-osk.icns'
_png = PROJECT_ROOT / 'assets' / 'logo-1024.png'
_icon = str(_icns) if _icns.exists() else (str(_png) if _png.exists() else None)

app = BUNDLE(
    coll,
    name='Alpha-OSK.app',
    icon=_icon,
    bundle_identifier='com.okstudio1.alpha-osk',
    version=APP_VERSION,
    info_plist={
        'CFBundleName': 'Alpha-OSK',
        'CFBundleDisplayName': 'Alpha-OSK',
        'CFBundleIdentifier': 'com.okstudio1.alpha-osk',
        'CFBundleVersion': APP_VERSION,
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleExecutable': 'alpha-osk',
        'CFBundlePackageType': 'APPL',
        'CFBundleSignature': '????',
        # LSMinimumSystemVersion: Quartz + pyobjc + PySide6 require a
        # modern macOS.  Pin to 11.0 (Big Sur) as the practical floor;
        # PySide6 6.6+ drops 10.15 anyway.
        'LSMinimumSystemVersion': '11.0',
        # Regular app: keep the Dock icon and app-switcher entry.  The
        # OSK still doesn't steal keyboard focus because we set
        # WindowDoesNotAcceptFocus + NSWindow level/collection-behavior
        # at runtime in keyboard_app.py.
        'LSUIElement': False,
        'NSHumanReadableCopyright': 'Copyright © Owen Kent',
        # Hi-DPI displays — PySide6/Qt handles scaling automatically
        # once we declare support here.
        'NSHighResolutionCapable': True,
        # Tell Cocoa we expect to be the only instance of this bundle
        # ID.  The Python-side QSharedMemory check still enforces
        # singleton-ness; this is a hint to Launch Services so a
        # second double-click on the .app surfaces the existing one.
        'LSMultipleInstancesProhibited': True,
    },
)
