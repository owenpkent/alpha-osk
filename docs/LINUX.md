# Linux Build Guide

End-to-end guide for running, bundling, and packaging Alpha-OSK on Linux.
The Windows counterpart lives in [WINDOWS.md](WINDOWS.md).

---

## Running from source

```bash
# System dependency for key synthesis (pick one)
sudo apt install xdotool    # X11 (most desktops)
sudo apt install ydotool    # Wayland (requires user-space daemon setup)

# Launch — run.py auto-creates a venv and installs PySide6
python run.py
```

`run.py` uses Qt's `xcb` platform plugin by default so `xdotool` works
out of the box. Wayland users who prefer `ydotool` can override it:

```bash
QT_QPA_PLATFORM=wayland python run.py
```

---

## Building a standalone bundle

The Linux build pipeline mirrors the Windows one — PyInstaller produces
a single-directory bundle under `dist/alpha-osk/` that can be run
directly or wrapped into an AppImage for distribution.

```bash
# One-time: install PyInstaller into the project venv
venv/bin/pip install pyinstaller

# Bundle only (≈300 MB folder with bundled Qt)
python build/linux/build.py

# Bundle + AppImage (downloads appimagetool on first run)
python build/linux/build.py --appimage --fetch-appimagetool

# Skip PyInstaller, only re-wrap an existing dist/ into an AppImage
python build/linux/build.py --skip-build --appimage
```

Outputs:

| Path | Produced by |
|------|-------------|
| `dist/alpha-osk/alpha-osk` | PyInstaller single-directory bundle |
| `release/Alpha-OSK-<version>-x86_64.AppImage` | `--appimage` wrapper |

Run the bundle directly with `./dist/alpha-osk/alpha-osk` — no install
needed. Runtime still requires `xdotool` or `ydotool` on the host,
because those are OS-level tools (not Python libraries) and are not
bundled.

---

## Layout

```
build/
├── launcher.py              # Shared PyInstaller entry point
└── linux/
    ├── alpha-osk.spec       # PyInstaller spec (datas, hidden imports, excludes)
    ├── build.py             # Pipeline driver (PyInstaller → AppDir → appimagetool)
    ├── AppRun               # AppImage entry script (sets Qt plugin paths)
    └── alpha-osk.desktop    # Desktop integration for AppImage
```

### What the spec excludes

To keep the bundle small, `build/linux/alpha-osk.spec` drops:

- **QtWebEngine** (`libQt6WebEngineCore.so` is ≈100 MB — we never embed a
  browser). If you add an in-app browser later, re-include the WebEngine
  / WebView / WebChannel modules in both `excludes` and
  `_DROP_BINARY_PREFIXES`, then re-measure the bundle size.
- **Heavy ML libraries** (torch, transformers, numpy, etc.) — the
  LLM-based predictor is optional and not shipped in the default bundle.
- **Windows-only modules** (`src.platform.windows`).

---

## AppImage internals

An AppImage is a self-extracting squashfs that runs `AppRun` as the
entry point. Our `AppRun` at `build/linux/AppRun`:

1. Points `PATH`, `QT_PLUGIN_PATH`, and `QML2_IMPORT_PATH` at the
   bundled Qt plugins and QML modules under `usr/bin/_internal/`.
2. Defaults `QT_QPA_PLATFORM=xcb` (overridable by the user).
3. `exec`s the PyInstaller bootloader.

The `.desktop` file declares the app's name, icon, and
`Categories=Utility;Accessibility;` so it shows up in accessibility
menus once the user integrates the AppImage (e.g. via
[appimaged](https://github.com/probonopd/go-appimage)).

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ModuleNotFoundError` at runtime | Missing hidden import in spec | Add it to `hiddenimports` in `build/linux/alpha-osk.spec` |
| Bundle runs but no keys typed | `xdotool` / `ydotool` not installed on host | `sudo apt install xdotool` |
| Window flashes and exits under Wayland | Qt picked Wayland plugin; `xdotool` is X11-only | `QT_QPA_PLATFORM=xcb ./alpha-osk` (or use `ydotool` for native Wayland) |
| AppImage won't run | Missing `libfuse2` on the host | `sudo apt install libfuse2` (required to mount AppImages) |
| `libtiff.so.5` warning at build time | Qt imageformats plugin looks for it | Benign — we don't use TIFF; warning doesn't affect the bundle |
| Real keyboard feels like Ctrl/Shift is held | Another OSK (e.g. GNOME On-Board) is still running and has its own `keydown` pinned | `killall onboard`; check with `pgrep -a onboard`. Press-and-release Ctrl/Shift on the physical keyboard to clear, or `xdotool keyup ctrl shift alt super` |
| Alpha-OSK sticky modifier stays held after quit | Old builds fired `xdotool keydown` without a matching `keyup` on shutdown | Fixed — `KeyboardBridge.shutdown()` now releases Ctrl/Alt/Win on `aboutToQuit`. Rebuild if you're on an older bundle |

---

## See also

- [WINDOWS.md](WINDOWS.md) — Windows build / EV signing / NSIS installer.
- [PLATFORM_ARCHITECTURE.md](PLATFORM_ARCHITECTURE.md) — cross-platform
  design rationale for the key-synthesizer abstraction.
- [AUTO_UPDATE.md](AUTO_UPDATE.md) — the auto-updater (currently
  Windows-only, Linux story TBD).
