# Linux Build Guide

End-to-end guide for running, bundling, and packaging Alpha-OSK on Linux.
The Windows counterpart lives in [WINDOWS.md](WINDOWS.md).

---

## Running from source

```bash
# System dependency for key synthesis (pick one)
sudo apt install xdotool    # X11 (most desktops)
sudo apt install ydotool    # Wayland (requires user-space daemon setup)

# Qt 6.5+ xcb platform plugin dependency (X11 sessions) — without it the
# keyboard exits with "Could not load the Qt platform plugin xcb"
sudo apt install libxcb-cursor0

# Optional: enable password-field auto-detection (see Privacy Mode below)
sudo apt install python3-gi gir1.2-atspi-2.0

# Launch — run.py auto-creates a venv and installs PySide6
python run.py
```

`run.py` uses Qt's `xcb` platform plugin by default so `xdotool` works
out of the box. Wayland users who prefer `ydotool` can override it:

```bash
QT_QPA_PLATFORM=wayland python run.py
```

Dictation capture does not go through `xdotool` at all: it opens the
microphone through Qt's multimedia backend, which needs a working
**PipeWire or PulseAudio** sound server on the host. Most desktops ship
one; without it the mic button reports that the microphone could not be
opened.

---

## Platform-parity features

Features whose Linux behaviour is worth stating explicitly, either because
they were written for Windows first and now also work here, or because how
they behave under X11 versus Wayland is not obvious:

| Feature | X11 | Wayland | Mechanism |
|---------|-----|---------|-----------|
| Atomic prediction replacement (`replace_text`) | ✅ | ✅ | `xdotool key shift+Left…` chord chain; `ydotool --key-down shift` + Left×N + `--key-up shift` |
| App-switch context reset | ✅ | ❌ | `xdotool getactivewindow` polled every 250 ms; Wayland compositors don't expose focused window to unprivileged clients |
| Password-field auto privacy mode | ✅ | ✅ (if toolkit speaks AT-SPI) | `gi.repository.Atspi` focus listener — needs `python3-gi` + `gir1.2-atspi-2.0` |
| Sticky-modifier hold / release | ✅ | ✅ | `xdotool keydown/keyup` or `ydotool key --key-down/--key-up`. **Super/Meta (Win) is never held** — see note below |
| Defensive modifier release on startup | ✅ | ✅ | `LinuxKeySynthesizer.reset_modifier_state()` (see Troubleshooting) |
| Dictation microphone capture | ✅ | ✅ | `QAudioSource` (QtMultimedia) talking to the host's PipeWire or PulseAudio server; no X11 / Wayland involvement either way |

> **Super/Meta (the Win key) is sent only as a chord, never held.** Holding Super down makes the window manager (Mutter/KWin) grab the pointer for window move/resize gestures, so every click — including on the OSK's own keys — is swallowed as a WM gesture and the keyboard becomes unusable until the hold is released. `LinuxKeySynthesizer.hold_modifier()` therefore skips `win`/`super`; Super+`<key>` combos (Win+D, Win+L, Win+arrow) still work because they go out as an atomic `xdotool key super+<key>` chord. Other modifiers (Shift/Ctrl/Alt) are still held so Shift+drag selection etc. work in the target app.

### Privacy Mode (password auto-detection)

When Alpha-OSK can see that the focused text field is a password box, it
suppresses prediction learning and clears typing state so sensitive
characters never reach the n-gram cache or model JSON on disk. The title
bar icon flips to the padlock state and the prediction bar shows
"Learning paused".

How it works on Linux:

1. `src/platform/password_detect.py::_LinuxATSPIDetector` tries to
   `import gi` and initialise AT-SPI 2. If either step fails (package
   missing, at-spi-2-core daemon not running, no D-Bus session), the
   detector reports `available=False` and we fall back to the null
   detector; the manual "Learning" switch in the title bar
   still works.
2. On success, a daemon thread owns a dedicated GLib main loop that
   listens for `object:state-changed:focused` (and the legacy `focus:`
   event for older toolkits). Each arrival event reads the source
   accessible's state set; if it contains
   `Atspi.StateType.PASSWORD_TEXT`, the shared `_is_password` flag
   flips on. Defocus events (detail1=0) are ignored — they describe
   focus *leaving* the source, not arriving anywhere specific.
3. The bridge polls `is_password_field()` on a 200 ms timer *and* on
   every keystroke (rate-limited to 50 ms) to close the race window
   where the first characters after focus-change would otherwise leak
   into the prediction cache.

Tested toolkits: GTK 3 / 4 (`GtkEntry` with `visibility=false`), Qt
(`QLineEdit` with `EchoMode.Password`), Firefox + Chromium
(`<input type="password">`). If your app doesn't trip the detector,
`accerciser` (Ubuntu: `sudo apt install accerciser`) will show you
whether AT-SPI sees it as a password field at all.

### App-switch context reset

Polling-based, 4 Hz. When the focused window ID changes, the bridge
wipes `_current_word`, `_context_buffer`, `_sentence_buffer`, and
current predictions so suggestions from the previous app's context
don't leak into the new one. Cost: one `xdotool getactivewindow`
subprocess every 250 ms (~5 ms wall time each). On Wayland the helper
returns 0 and the clearing path is simply skipped — there's no
unprivileged API to ask the compositor which window has focus.

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
| `release/Alpha-OSK-<version>-linux-requirements.lock.txt` | `pip freeze --all` of the build venv — human/pip-friendly. Always emitted. |
| `release/Alpha-OSK-<version>-linux-sbom.cyclonedx.json` | CycloneDX 1.6 SBOM of the build venv — machine/scanner-friendly (purl, license, hashes). Always emitted. |

Run the bundle directly with `./dist/alpha-osk/alpha-osk` — no install
needed. Runtime still requires `xdotool` or `ydotool` on the host,
because those are OS-level tools (not Python libraries) and are not
bundled.

**If dictation is in scope, complete one run against a real microphone
from the bundle**, not only from source. Launching the bundle is not
sufficient on its own: naming `PySide6.QtMultimedia` in the spec bundles
the *module*, while opening a device also needs Qt's `multimedia`
**plugin** directory, and a missing plugin surfaces as the message "The
microphone could not be opened." rather than as a crash, so it will not
show up in any other step. Check the device picker lists your inputs too,
since it goes through the same backend. Same check as `WINDOWS.md` §
*Test the installer* step 9.

### Dependency Lockfile & SBOM

`build/linux/build.py` emits both a plaintext lockfile
(`freeze_lockfile`) and a CycloneDX 1.6 SBOM (`emit_sbom`) alongside
the AppImage / .deb / tarball. Same shape and rationale as the Windows
build — see `WINDOWS.md` § *Dependency Lockfile & SBOM* for what
each artefact is for, the CI-time `osv-scan` job that reads both
lockfiles for transitive CVEs, and how to bump the toolchain.

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

### appimagetool provenance

`--fetch-appimagetool` used to download `appimagetool` from GitHub's mutable
`continuous` tag, which is rebuilt on every upstream merge with no fixed
content to check against. `build/linux/build.py` now pins a specific tagged
release (`APPIMAGETOOL_URL`, currently `1.9.1`) and verifies its SHA256
(`APPIMAGETOOL_SHA256`) before the downloaded binary is ever executed. This
is trust-on-first-use, not a signed guarantee (upstream doesn't ship a signed
checksum manifest), but it does catch a later swap, a re-pointed tag, or a
corrupted or intercepted download.

**If you ever bump the appimagetool version, bump `APPIMAGETOOL_URL` and
`APPIMAGETOOL_SHA256` in the same change.** Updating the URL without updating
the hash makes every build fail closed (the verification step refuses to run
an unexpected binary, it doesn't skip the check), and updating the hash
without the URL pins to a version you didn't intend. Compute the new hash by
downloading the release asset once by hand and hashing it locally; don't
trust a hash pasted from anywhere other than a file you fetched yourself.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `ModuleNotFoundError` at runtime | Missing hidden import in spec | Add it to `hiddenimports` in `build/linux/alpha-osk.spec` |
| Bundle runs but no keys typed | `xdotool` / `ydotool` not installed on host | `sudo apt install xdotool` |
| `Could not load the Qt platform plugin "xcb"` at launch | Qt 6.5+ needs `libxcb-cursor`, missing on the host | `sudo apt install libxcb-cursor0` (`run.py` now preflight-warns for this on X11) |
| Window flashes and exits under Wayland | Qt picked Wayland plugin; `xdotool` is X11-only | `QT_QPA_PLATFORM=xcb ./alpha-osk` (or use `ydotool` for native Wayland) |
| AppImage won't run | Missing `libfuse2` on the host | `sudo apt install libfuse2` (required to mount AppImages) |
| `libtiff.so.5` warning at build time | Qt imageformats plugin looks for it | Benign — we don't use TIFF; warning doesn't affect the bundle |
| Real keyboard feels like Ctrl/Shift is held | Another OSK (e.g. GNOME On-Board) is still running and has its own `keydown` pinned | `killall onboard`; check with `pgrep -a onboard`. Press-and-release Ctrl/Shift on the physical keyboard to clear, or `xdotool keyup ctrl shift alt super` |
| Alpha-OSK sticky modifier stays held after quit | Old builds fired `xdotool keydown` without a matching `keyup` on shutdown | Fixed — `KeyboardBridge.shutdown()` now releases Ctrl/Alt/Win on `aboutToQuit`. Rebuild if you're on an older bundle |
| Fresh Alpha-OSK launch inherits a stuck modifier from a prior crash | Previous instance was killed before it could release | Fixed — `KeyboardBridge.__init__` issues a defensive `keyup` on Ctrl/Alt/Shift/Super at startup (see `LinuxKeySynthesizer.reset_modifier_state()`). Launching Alpha-OSK alone clears the stuck state |

---

## See also

- [WINDOWS.md](WINDOWS.md) — Windows build / EV signing / NSIS installer.
- [PLATFORM_ARCHITECTURE.md](../architecture/PLATFORM_ARCHITECTURE.md) — cross-platform
  design rationale for the key-synthesizer abstraction.
- [AUTO_UPDATE.md](AUTO_UPDATE.md) — the auto-updater (currently
  Windows-only, Linux story TBD).
