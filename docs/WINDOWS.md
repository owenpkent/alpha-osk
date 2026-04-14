# Alpha-OSK on Windows

Complete guide to running, building, and deploying Alpha-OSK on Windows.

---

## Table of Contents

- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Key Synthesis: SendInput API](#key-synthesis-sendinput-api)
- [Window Behaviour](#window-behaviour)
- [UIAccess and EV Code Signing](#uiaccess-and-ev-code-signing)
- [Building a Standalone Executable](#building-a-standalone-executable)
- [Code Signing Walkthrough](#code-signing-walkthrough)
- [Installation for UIAccess](#installation-for-uiaccess)
- [Security Notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Differences from the Linux Version](#differences-from-the-linux-version)
- [Architecture Reference](#architecture-reference)

---

## Quick Start

### Prerequisites

- **Python 3.9+** (download from [python.org](https://www.python.org/downloads/))
- **No additional system dependencies** — unlike Linux, Windows key synthesis
  uses the built-in `SendInput` API via Python's `ctypes`.

### Run from Source

```powershell
# Clone the repository
git clone https://github.com/owenpkent/alpha-osk.git
cd alpha-osk

# Launch the keyboard (auto-creates venv, installs PySide6)
python run.py
```

The launcher automatically:
1. Creates a virtual environment (`venv/`) if it doesn't exist.
2. Installs PySide6 and dependencies from `requirements.txt`.
3. Launches the on-screen keyboard.

### Run the Dashboard

```powershell
python run.py --dashboard
```

Dashboard opens at `http://localhost:8080`.

---

## How It Works

Alpha-OSK uses the same Python + PySide6 + QML architecture on Windows as it
does on Linux.  The only difference is the **key synthesis backend** — the
component that injects keystrokes into other applications.

```
┌────────────────────────────────────────────────────────────────┐
│                      Alpha-OSK (Python)                        │
│                                                                │
│  ┌──────────────┐   ┌──────────────────┐   ┌───────────────┐  │
│  │  QML / Qt UI │──▶│  KeyboardBridge   │──▶│  Prediction   │  │
│  │  (Main.qml)  │   │  (Python↔QML)    │   │  Engine       │  │
│  └──────────────┘   └───────┬──────────┘   └───────────────┘  │
│                             │                                  │
│                    ┌────────▼─────────┐                        │
│                    │  Platform Layer  │                        │
│                    │  (src/platform/) │                        │
│                    └────────┬─────────┘                        │
└─────────────────────────────┼──────────────────────────────────┘
                              │
           ┌──────────────────┼──────────────────┐
           │                  │                  │
     ┌─────▼──────┐   ┌──────▼──────┐   ┌──────▼──────┐
     │  Linux:    │   │  Windows:   │   │  Windows:   │
     │  xdotool   │   │  SendInput  │   │  SendInput  │
     │  ydotool   │   │  (standard) │   │  + UIAccess │
     └────────────┘   └─────────────┘   └─────────────┘
```

---

## Key Synthesis: SendInput API

On Windows, Alpha-OSK injects keystrokes using the Win32 **`SendInput()`**
function from `user32.dll`, accessed via Python's built-in `ctypes` module.

### Two Injection Modes

| Mode | Used For | How It Works |
|------|----------|--------------|
| **Virtual-Key** | Special keys, modifier combos | Sends `KEYBDINPUT` with virtual-key code (`wVk`) |
| **Unicode** | Normal text characters | Sends `KEYBDINPUT` with `KEYEVENTF_UNICODE` flag and UTF-16 code point in `wScan` |

### Why Unicode Mode?

Unicode mode (`KEYEVENTF_UNICODE`) is layout-independent — it sends the
character's code point directly, so it works regardless of the user's
keyboard layout.  This means Alpha-OSK correctly types accented characters,
CJK, emoji, and any other Unicode character without needing to know whether
the user has a US, UK, French, or Japanese keyboard layout.

### Implementation

The Windows synthesizer lives in `src/platform/windows.py`.  Key features:

- **Zero external dependencies** — uses only `ctypes` (Python stdlib).
- **Atomic injection** — all events for a keystroke (modifier press → key
  press → key release → modifier release) are sent in a single `SendInput`
  call, preventing race conditions with other input.
- **Extended key handling** — correctly sets `KEYEVENTF_EXTENDEDKEY` for
  navigation keys (arrows, Home, End, Insert, Delete, Page Up/Down) which
  require it on Windows.
- **Surrogate pair support** — characters outside the Basic Multilingual
  Plane (code point > 0xFFFF) are sent as UTF-16 surrogate pairs.

---

## Window Behaviour

Alpha-OSK must behave differently from a normal application window:

1. **Stay on top** of all other windows.
2. **Never steal keyboard focus** from the user's active application.
3. **Not appear in the taskbar** or Alt+Tab switcher.
4. **Be draggable** by its custom title bar.

### How This Is Achieved on Windows

| Mechanism | What It Does |
|-----------|-------------|
| Qt `WindowStaysOnTopHint` | Keeps the window above normal windows |
| Qt `Tool` | Tells the window manager this is a utility window |
| Qt `FramelessWindowHint` | Removes the OS title bar (we draw our own in QML) |
| Qt `WindowDoesNotAcceptFocus` | Qt-level focus prevention |
| Win32 `WS_EX_NOACTIVATE` | OS-level: window is **never** activated on click |
| Win32 `WS_EX_TOOLWINDOW` | Hidden from Alt+Tab and taskbar |
| Win32 `WS_EX_TOPMOST` | Defence-in-depth topmost flag |

The Win32 extended styles are applied in `src/keyboard_app.py` via
`SetWindowLongW()` after the Qt window is created.  This is necessary
because Qt's flag system doesn't expose `WS_EX_NOACTIVATE`, which is
**critical** — without it, clicking a key on the OSK would steal focus
from the user's text editor.

---

## UIAccess and EV Code Signing

### The Problem

By default, a standard-privilege process **cannot** send keystrokes to
windows running at a higher integrity level.  This means:

- ❌ Cannot type into an **elevated Command Prompt** (Run as Admin).
- ❌ Cannot type into **Task Manager** or **Registry Editor**.
- ❌ Cannot appear above **UAC consent prompts**.
- ❌ Cannot appear on the **Secure Desktop** (Ctrl+Alt+Del screen).

For an on-screen keyboard used by someone with a motor disability, this is
a serious accessibility barrier.

### The Solution: UIAccess

Windows provides a mechanism called **UIAccess** specifically designed for
assistive technology like on-screen keyboards.  When a process has UIAccess
privileges:

- ✅ `SendInput` reaches **all** windows, regardless of integrity level.
- ✅ The keyboard can appear **above UAC prompts**.
- ✅ The keyboard can appear on the **Secure Desktop**.

### Requirements for UIAccess

All **three** conditions must be met:

1. **UIAccess manifest**: The `.exe` must embed a manifest with
   `uiAccess="true"`.  Alpha-OSK's manifest is at
   `build/alpha-osk.exe.manifest`.

2. **EV code signing**: The `.exe` must be signed with an **Extended
   Validation (EV)** code signing certificate.  Standard OV certificates
   are **not** sufficient for UIAccess.

3. **Secure location**: The `.exe` must reside in one of:
   - `C:\Program Files\`
   - `C:\Program Files (x86)\`
   - `C:\Windows\System32\`

If any condition is not met, Windows silently ignores `uiAccess="true"` and
launches the process with standard privileges.  The keyboard still works,
but cannot send input to elevated windows.

### How to Check if UIAccess Is Active

Alpha-OSK checks UIAccess status at startup and logs it:

```
[KeyboardApp] INFO: UIAccess: active
```

or:

```
[KeyboardApp] INFO: UIAccess: not active
```

You can also check programmatically via `src/platform/__init__.py`:

```python
from src.platform import get_platform_info
info = get_platform_info()
print(info["ui_access"])  # True or False
```

---

## Building a Standalone Executable

### Prerequisites

```powershell
pip install pyinstaller
```

Optionally, install **NSIS** for an installer (otherwise you get a portable build):

```powershell
winget install NSIS.NSIS
```

### One-Command Build

The build script handles everything — PyInstaller, signing, and NSIS packaging:

```powershell
# Full signed release (eToken must be plugged in)
python build/build_windows.py

# Unsigned dev build (no eToken needed)
python build/build_windows.py --no-sign

# Re-sign / re-package without rebuilding
python build/build_windows.py --skip-build

# Verify signatures on existing build
python build/build_windows.py --verify-only

# Portable only (skip NSIS installer)
python build/build_windows.py --no-installer
```

### Manual Build (Step by Step)

```powershell
# 1. Build with PyInstaller
pyinstaller build/alpha-osk.spec --noconfirm

# 2. Sign all .exe/.dll in the output
python build/sign.py dist/alpha-osk/

# 3. Verify
python build/sign.py dist/alpha-osk/alpha-osk.exe --verify
```

### Output

| Path | Description |
|------|-------------|
| `dist/alpha-osk/alpha-osk.exe` | Portable executable + dependencies |
| `release/Alpha-OSK-Setup-{version}.exe` | NSIS installer (if NSIS is installed) |

### What the Build Includes

- Python runtime (bundled).
- PySide6 and Qt6 libraries.
- QML UI files (`qml/`).
- Data files (`data/` — dictionaries, training corpus).
- Dashboard templates (`templates/`).
- Windows UIAccess manifest (embedded in `.exe`).

### Build Options

The `.spec` file (`build/alpha-osk.spec`) can be customized:

| Setting | Default | Description |
|---------|---------|-------------|
| `console` | `False` | Set `True` to show a console window for debugging |
| `upx` | `True` | Compress binaries with UPX |
| `manifest` | `alpha-osk.exe.manifest` | Path to the UIAccess manifest |
| `icon` | (none) | Path to `.ico` file for the exe icon |

---

## Code Signing — EV Certificate (Current Setup)

Uses the **same EV certificate and signing workflow** as
[gitconnect's windows-desktop](../../../gitconnect/windows-desktop/docs/PACKAGING.md).

### What We Have

| Field | Value |
|-------|-------|
| **Certificate** | Sectigo Public Code Signing CA EV R36 |
| **Issued to** | OK Studio Inc. |
| **Type** | EV (Extended Validation) — **immediate SmartScreen trust** |
| **Hardware** | SafeNet USB eToken (physical USB key) |
| **Thumbprint** | `fc22b5221318f3f3f6b3eb2d969d7f99091557bf` |
| **Timestamp server** | `http://timestamp.digicert.com` |
| **Sign script** | `build/sign.py` (retry logic for Defender file locks) |

### What Gets Signed

`build_windows.py` signs **every** `.exe` and `.dll` in the `dist/alpha-osk/`
directory — including the bundled Python DLLs, PySide6 DLLs, and the main
`alpha-osk.exe`.  It also signs the final NSIS installer `.exe`.

### Step-by-Step: Building a Signed Release

**Prerequisites:**

- SafeNet Authentication Client installed (comes with the USB eToken)
- USB eToken physically plugged in
- Certificate visible in Windows cert store:

  ```powershell
  certutil -store -user My
  # Look for "OK Studio Inc." with thumbprint fc22b522...
  ```

**Build (from a normal, non-elevated PowerShell):**

```powershell
python build/build_windows.py
```

> **Why non-elevated?** The SafeNet eToken driver makes the certificate
> available to the **current user session**, not to elevated (admin)
> processes.  Running from an elevated shell causes "Cannot find
> certificate."  Always build from a normal shell.

**Verify signatures:**

```powershell
# Verify main exe
python build/sign.py dist/alpha-osk/alpha-osk.exe --verify

# Verify installer
python build/sign.py release/Alpha-OSK-Setup-{version}.exe --verify

# Quick PowerShell check
(Get-AuthenticodeSignature "dist\alpha-osk\alpha-osk.exe").Status
```

### `build/sign.py` — Retry Script

The signing script handles Windows Defender temporarily locking `.exe` files
during scanning (same problem gitconnect's `sign.js` solves).  Key behaviour:

- Finds `signtool.exe` across common Windows SDK paths.
- Signs with SHA-256 + RFC 3161 timestamp.
- Retries up to 5× with exponential backoff.
- Throws on permanent failure so the build pipeline fails loudly.

### Troubleshooting Signing

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Cannot find certificate` | Running from elevated PowerShell | **Use normal shell** — eToken not visible to admin context |
| `SignTool Error: file being used by another process` | Windows Defender scanning | `sign.py` retry logic handles this automatically |
| `Cannot find certificate` by subject | Multiple certs | Config uses `certificateSha1` thumbprint — verify with `certutil -store -user My` |
| Timestamp server timeout | DigiCert slow | Alternative: `http://timestamp.sectigo.com` — edit `TIMESTAMP_SERVER` in `sign.py` |

---

## NSIS Installer

The build script generates a proper NSIS installer that:

- **Defaults to `C:\Program Files\Alpha-OSK`** — required for UIAccess.
- **Lets the user choose** a different install directory.
- **Kills running instances** before upgrading.
- **Detects previous installs** at different paths and offers to uninstall.
- **Creates shortcuts**: Desktop + Start Menu.
- **Registers in Add/Remove Programs** for clean uninstall.
- **Asks about AppData** on uninstall (keep learned vocabulary or delete).

The installer customizations live in `build/installer.nsh`, following the
same macro patterns as gitconnect's `build/installer.nsh`.

---

## Installation for UIAccess

The NSIS installer handles this automatically — it defaults to
`C:\Program Files\Alpha-OSK\`, creates shortcuts, and registers the
uninstaller.

### Manual Installation (without NSIS)

```powershell
# Create installation directory
mkdir "C:\Program Files\Alpha-OSK"

# Copy build output
xcopy /E /I "dist\alpha-osk\*" "C:\Program Files\Alpha-OSK\"
```

Run from the installed location:

```powershell
"C:\Program Files\Alpha-OSK\alpha-osk.exe"
```

### Programmatic Shortcut Creation

Alpha-OSK includes helper functions for shortcut management:

```python
from src.platform.windows import (
    create_start_menu_shortcut,
    create_desktop_shortcut,
    add_to_startup,
    remove_from_startup,
)

exe = r"C:\Program Files\Alpha-OSK\alpha-osk.exe"

create_start_menu_shortcut(exe)   # Start Menu → Alpha-OSK
create_desktop_shortcut(exe)       # Desktop shortcut
add_to_startup(exe)                # Launch on login
remove_from_startup()              # Remove from Startup
```

---

## Security Notes

### Administrator Privileges and UIPI

Alpha-OSK requests administrator privileges on Windows so that `SendInput` can
reach windows running at a higher integrity level (e.g. an elevated Command
Prompt).  If the UAC prompt is declined, the keyboard falls back to standard
privileges and logs a warning — it will still work for most applications.

The privilege re-launch in `run.py` passes only the same command-line arguments
the user already supplied; no new arguments are constructed or injected.

### Shortcut Creation

The `create_shortcut` helper in `src/platform/windows.py` constructs a
PowerShell script to create `.lnk` files.  All string values passed to the
script (paths, description, icon path) have `"` characters escaped as `""`
before interpolation, preventing PowerShell string-literal breakout if a path
contains embedded quotes.

### File Import (Training Data)

`importTextFile` and `importFolder` in `src/keyboard_bridge.py` accept
user-supplied paths to read text files for training the prediction model.
These functions are intended to be driven by the user via the file picker UI
and operate entirely within the user's own filesystem permissions — no
server-side or cross-user data access is possible.

### UIAccess vs. Always-Admin

Alpha-OSK prefers the UIAccess mechanism (EV code signing + `Program Files`
install) over requiring permanent administrator rights.  UIAccess grants only
the specific capability needed for an on-screen keyboard (injecting input into
higher-integrity windows) without granting broad admin access.  See the
[UIAccess and EV Code Signing](#uiaccess-and-ev-code-signing) section above.

---

## Troubleshooting

### Keystrokes Not Reaching Target Application

| Symptom | Cause | Fix |
|---------|-------|-----|
| Keys don't appear in any app | SendInput failing | Check logs for errors; restart Alpha-OSK |
| Keys don't appear in **elevated** apps | No UIAccess | Sign with EV cert and install to Program Files |
| Keys appear but wrong characters | Layout mismatch | Alpha-OSK uses Unicode mode — should be layout-independent. File a bug. |

### Window Behaviour Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Keyboard steals focus on click | `WS_EX_NOACTIVATE` not applied | Check logs for "Failed to apply Windows extended styles" |
| Keyboard appears in Alt+Tab | `WS_EX_TOOLWINDOW` not applied | Same as above |
| Keyboard disappears behind other windows | Topmost not working | Try restarting Alpha-OSK |
| **Window becomes massive after moving to a different monitor** | Qt's default DPI rounding multiplies logical window dimensions when crossing monitors with different scale factors | Fixed: `PassThrough` DPI rounding policy set in `keyboard_app.py`; `onScreenChanged` in `Main.qml` clamps width to the new screen's available width |

### Build Issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError` at runtime | Missing hidden import | Add module to `hiddenimports` in `.spec` file |
| QML files not found | Data files not bundled | Check `datas` list in `.spec` file |
| Manifest not embedded | Wrong path in `.spec` | Verify `manifest` path is correct |

### Checking UIAccess Status

```python
# In Python
from src.platform import get_platform_info
info = get_platform_info()
print(f"UIAccess: {info.get('ui_access', False)}")
```

Or check the startup log output:

```
[KeyboardApp] INFO: UIAccess: active
```

---

## Differences from the Linux Version

| Aspect | Linux | Windows |
|--------|-------|---------|
| **Key synthesis** | `xdotool` (X11) or `ydotool` (Wayland) via subprocess | `SendInput` API via `ctypes` |
| **External deps** | `sudo apt install xdotool` | None (built-in) |
| **Unicode handling** | xdotool `type` command | `KEYEVENTF_UNICODE` flag |
| **Focus prevention** | Qt flags only | Qt flags + `WS_EX_NOACTIVATE` |
| **Elevated access** | Not applicable (X11/Wayland don't have integrity levels) | UIAccess with EV signing |
| **Config directory** | `~/.config/alpha-osk/` | `%APPDATA%\alpha-osk\` |
| **Model storage** | `~/.config/alpha-osk/models/` | `%APPDATA%\alpha-osk\models\` |
| **Venv Python path** | `venv/bin/python` | `venv\Scripts\python.exe` |
| **Build/packaging** | Not yet defined | PyInstaller (`.spec` file in `build/`) |
| **Display server** | X11 or Wayland | Windows Desktop Window Manager |

### What's Identical

- QML UI (all `.qml` files) — completely shared.
- Prediction engine (n-gram, PPM, fuzzy, transformer) — completely shared.
- Keyboard bridge logic (modifier state, predictions, context) — shared.
- Data files (dictionaries, training corpus) — shared.

---

## Architecture Reference

### Files Changed for Windows Support

| File | Change |
|------|--------|
| `src/platform/__init__.py` | NEW — Platform detection, factory, config paths |
| `src/platform/base.py` | NEW — Abstract key synthesizer interface |
| `src/platform/linux.py` | NEW — Linux backend (extracted from old bridge) |
| `src/platform/windows.py` | NEW — Windows SendInput backend |
| `src/keyboard_bridge.py` | MODIFIED — Uses platform layer instead of direct xdotool |
| `src/keyboard_app.py` | MODIFIED — Cross-platform env setup, Win32 window styles |
| `src/prediction/hybrid_predictor.py` | MODIFIED — Cross-platform model dir |
| `run.py` | MODIFIED — Cross-platform venv paths and dep checks |
| `build/alpha-osk.exe.manifest` | NEW — UIAccess manifest for EV signing |
| `build/alpha-osk.spec` | NEW — PyInstaller build specification |

### Key Design Decisions

1. **ctypes, not pywin32**: We use `ctypes` to call `SendInput` directly
   rather than depending on `pywin32`.  This means **zero additional
   dependencies** on Windows — `ctypes` is part of the Python stdlib.

2. **Unicode mode for text**: Rather than simulating individual virtual-key
   presses (which depends on keyboard layout), we use
   `KEYEVENTF_UNICODE` for all printable characters.  This is
   layout-independent and supports the full Unicode range.

3. **Virtual-key mode for specials**: Special keys (Backspace, Enter,
   F-keys, arrows) and modifier combinations (Ctrl+C) use virtual-key
   codes, which is the correct approach for non-character keys.

4. **UIAccess via manifest**: Rather than requiring the user to run as
   Administrator (which has security implications), we use the UIAccess
   mechanism designed specifically for assistive technology.

---

*Last updated: April 2026*
