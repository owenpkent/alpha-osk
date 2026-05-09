# Alpha-OSK on Windows

Complete guide to running, building, and deploying Alpha-OSK on Windows.

---

## Table of Contents

- [Quick Start](#quick-start)
- [How It Works](#how-it-works)
- [Key Synthesis: SendInput API](#key-synthesis-sendinput-api)
- [Window Behaviour](#window-behaviour)
- [UIAccess and EV Code Signing](#uiaccess-and-ev-code-signing)
- [UAC and the Secure Desktop](#uac-and-the-secure-desktop)
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
git clone https://github.com/okstudio1/alpha-osk.git
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

### Three Injection Modes

| Mode | Used For | How It Works |
|------|----------|--------------|
| **Virtual-Key** | Special keys, modifier combos, modifier+punctuation chords | Sends `KEYBDINPUT` with the virtual-key code in `wVk` and the layout scancode in `wScan` |
| **Scancode** | ASCII text characters (default) | Sends `KEYBDINPUT` with `wVk = 0`, the layout scancode in `wScan`, and the `KEYEVENTF_SCANCODE` flag set |
| **Unicode** | Per-character fallback when scancode is unsafe | Sends `KEYBDINPUT` with `wVk = 0`, the UTF-16 code point in `wScan`, and the `KEYEVENTF_UNICODE` flag set |

### Why Scancode Mode for ASCII

`KEYEVENTF_UNICODE` synthesises a `WM_KEYDOWN` event with the sentinel virtual-key code `VK_PACKET` (`0xE7`), followed by `WM_CHAR`. Many applications filter on real virtual-key codes and ignore `VK_PACKET`, or read raw scancodes directly via `RegisterRawInputDevices` and never see the Unicode-injected event at all. The list of confirmed cases that broke under pure-Unicode injection: Blender (the GHOST input layer keys off the real VK and scancode for shortcuts and viewport ops), VirtualBox (the kernel-mode keyboard filter forwards by scancode to the guest VM), DirectInput-based games and DAWs, raw-input-based 3D and CAD tools (Maya, Houdini, ZBrush, SolidWorks, Fusion 360 and similar). The general pattern: any application that wants `WM_KEYDOWN` rather than `WM_CHAR` was unreachable from Unicode mode.

`KEYEVENTF_SCANCODE` instead tells the OS "this is a physical key with this scancode." The OS looks up the virtual key from the scancode using the active layout and dispatches a normal `WM_KEYDOWN(VK_X)` plus `WM_CHAR`. Indistinguishable from a real keypress, which is why the Windows on-screen keyboard uses this mode.

Resolution path for a single ASCII character:

1. `VkKeyScanW(char)` returns the VK plus the layout shift state.
2. `MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)` returns the hardware scancode under the active layout.
3. `MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR)` returns the unshifted character with bit 31 set when the VK is a dead-key trigger on this layout (apostrophe on US-International, grave on French AZERTY). Bit-31 results are skipped: arming a dead-key composition would consume the next keypress instead of producing the character.
4. The OS Caps Lock state is folded in via `GetKeyState(VK_CAPITAL)`, so a clicked lowercase `a` produces `a` even when the OS Caps Lock LED is on.
5. A synthetic Shift press/release wraps the keystroke when needed and not already held. The wrap is skipped when Shift is already physically held (whether by the user or the OSK's sticky modifier state) so the trailing release does not unbalance the user's held key.

### When Scancode Mode Falls Back to Unicode

Per-character fallback to `KEYEVENTF_UNICODE` (the call still completes; only that one character takes the alternate path) when any of the following holds:

- The character is non-ASCII (≥ U+0080). Unicode mode covers the entire Unicode range including emoji, CJK, and accented chars not on the active layout.
- `VkKeyScanW` returns -1 (no single-keystroke mapping on the active layout, e.g. typing `ñ` on US English).
- The layout requires AltGr (Ctrl+Alt) or a bare Ctrl modifier to produce the char (German `@` is AltGr+Q). We do not synthesise AltGr because its semantics vary across layouts.
- The VK is a dead-key trigger on the active layout (bit 31 set on the `MAPVK_VK_TO_CHAR` probe).
- Shift is currently physically held *and* the character does not need shift. We cannot safely release a key the user is holding; Unicode mode bypasses shift state entirely for that character.

### Implementation

The Windows synthesizer lives in `src/platform/windows.py`. Key features:

- **Zero external dependencies** — uses only `ctypes` (Python stdlib).
- **Atomic injection** — all events for a keystroke (modifier press → key
  press → key release → modifier release) are sent in a single `SendInput`
  call, preventing race conditions with other input.
- **Per-character mode dispatch** — `send_text` and the typed portion of `replace_text` try the scancode path first per character, falling back to the Unicode path on a per-character basis. Mixed strings (e.g. `"Hi 👋"`) interleave the modes naturally; the target app sees the events in order.
- **Select-and-replace for predictions** — when a prediction is selected,
  the typed prefix is selected via Shift+Left (not deleted via Backspace),
  then the replacement text overwrites the selection. This prevents Electron
  apps (Slack, Teams, Discord) from closing the compose area when Backspace
  would empty the input field.
- **Extended key handling** — correctly sets `KEYEVENTF_EXTENDEDKEY` for
  navigation keys (arrows, Home, End, Insert, Delete, Page Up/Down) which
  require it on Windows.
- **Surrogate pair support** — characters outside the Basic Multilingual
  Plane (code point > 0xFFFF) take the Unicode path automatically and are sent as UTF-16 surrogate pairs.

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

For an on-screen keyboard used by someone with a motor disability, this is
a serious accessibility barrier.

### The Solution: UIAccess

Windows provides a mechanism called **UIAccess** specifically designed for
assistive technology like on-screen keyboards.  When a process has UIAccess
privileges:

- ✅ `SendInput` reaches **all** elevated windows on the regular desktop,
  regardless of integrity level.
- ⚠️ **Does not** grant access to the Secure Desktop (UAC consent prompts,
  Ctrl+Alt+Del, lock screen).  See [UAC and the Secure Desktop](#uac-and-the-secure-desktop)
  below for the only available workaround.

### Requirements for UIAccess

All **three** conditions must be met:

1. **UIAccess manifest**: The `.exe` must embed a manifest with
   `uiAccess="true"`.  Alpha-OSK's manifest is at
   `build/windows/alpha-osk.exe.manifest`.

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

### UAC and the Secure Desktop

UIAccess solves elevated-window injection on the **regular desktop** but
does **not** get Alpha-OSK onto the **Secure Desktop**, which is where
Windows draws:

- UAC consent prompts (the password box that appears when an app asks for
  admin rights),
- the Ctrl+Alt+Del screen,
- the login and lock screens.

The Secure Desktop is a separate, isolated session that only allows
specific Microsoft-signed processes (`winlogon`, the built-in `osk.exe`,
Magnifier, Narrator).  This is intentional: it prevents malware from
spoofing the prompt or simulating clicks to approve elevation.  No
EV-signed third-party app can join — there is no public API.

#### Workaround for UAC consent prompts only

You can tell Windows to display UAC prompts on the **regular desktop**
instead of the Secure Desktop.  Once it's there, Alpha-OSK's existing
UIAccess privilege is enough to type into it.

**Trade-off:** with the Secure Desktop disabled, any same-session process
running as the user can theoretically observe or interact with the UAC
prompt.  This weakens UAC's spoofing protection.  Users with
accessibility needs often accept this trade-off; document it clearly in
release notes if you ship guidance on enabling it.

**Via `secpol.msc`** (preferred — survives Windows Update):

1. Run `secpol.msc` as administrator.
2. Navigate to **Local Policies → Security Options**.
3. Set **"User Account Control: Switch to the secure desktop when
   prompting for elevation"** to **Disabled**.
4. Reboot.

**Via the registry** (equivalent — useful for automation or Home edition,
which lacks `secpol.msc`):

```
HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System
    PromptOnSecureDesktop = 0    (REG_DWORD)
```

#### What about the login/lock screen?

There is no override for these.  They are always on the Secure Desktop.
The only on-screen keyboard available there is Microsoft's `osk.exe`,
launched via the Ease of Access (wheelchair) menu in the bottom-left of
the login screen.  Users who need an OSK at login should rely on that.

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
python build/windows/build.py

# Unsigned dev build (no eToken needed)
python build/windows/build.py --no-sign

# Re-sign / re-package without rebuilding
python build/windows/build.py --skip-build

# Verify signatures on existing build
python build/windows/build.py --verify-only

# Portable only (skip NSIS installer)
python build/windows/build.py --no-installer
```

### Manual Build (Step by Step)

```powershell
# 1. Build with PyInstaller
pyinstaller build/windows/alpha-osk.spec --noconfirm

# 2. Sign all .exe/.dll in the output
python build/windows/sign.py dist/alpha-osk/

# 3. Verify
python build/windows/sign.py dist/alpha-osk/alpha-osk.exe --verify
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

The `.spec` file (`build/windows/alpha-osk.spec`) can be customized:

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
| **Sign script** | `build/windows/sign.py` (retry logic for Defender file locks) |

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
python build/windows/build.py
```

> **Why non-elevated?** The SafeNet eToken driver makes the certificate
> available to the **current user session**, not to elevated (admin)
> processes.  Running from an elevated shell causes "Cannot find
> certificate."  Always build from a normal shell.

**Verify signatures:**

```powershell
# Verify main exe
python build/windows/sign.py dist/alpha-osk/alpha-osk.exe --verify

# Verify installer
python build/windows/sign.py release/Alpha-OSK-Setup-{version}.exe --verify

# Quick PowerShell check
(Get-AuthenticodeSignature "dist\alpha-osk\alpha-osk.exe").Status
```

### `build/windows/sign.py` — Retry Script

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

The installer customizations live in `build/windows/installer.nsh`, following the
same macro patterns as gitconnect's `build/windows/installer.nsh`.

### Installer Upgrade Behavior

| Scenario | Behavior |
|----------|----------|
| Same directory (default `C:\Program Files\Alpha-OSK`) | Silently runs old `uninstall.exe /S` before extracting new files. Preserves `%APPDATA%\alpha-osk` (learned vocabulary). |
| Different directory | Prompts user: "Remove previous version?" If yes, runs old uninstaller. If no, both coexist. |
| Running instance detected | Prompts to close, then kills `alpha-osk.exe` via `taskkill`. |
| Interactive uninstall | Prompts whether to delete `%APPDATA%\alpha-osk` (learned vocabulary and settings). |

---

## Release Checklist

End-to-end process for shipping a new Windows version. **Do not skip steps** — unsigned builds won't get UIAccess, and forgetting to bump the version means the installer overwrites without proper upgrade logic.

### 1. Bump the version

Single source of truth: `src/__version__.py`. `build/windows/build.py` reads from it; the auto-updater compares against it.

```python
__version__ = "1.0.8"  # was 1.0.7
```

This flows into the installer filename (`Alpha-OSK-Setup-1.0.8.exe`), NSIS `APP_VERSION` (Add/Remove Programs), and the registry `DisplayVersion`.

### 2. Update `CHANGELOG.md`

Add a new `## [x.y.z] — YYYY-MM-DD` section at the top under `[Unreleased]`. Categorize under `### Added` / `### Fixed` / `### Changed` / `### Chores`.

### 2a. Verify telemetry endpoint (if telemetry is in scope for this release)

Check `src/telemetry.py::DEFAULT_ENDPOINT`. It must be either:
- the empty string (telemetry stays inert; safe for any release), **or**
- the **production** Cloudflare Worker URL (not a staging URL, not localhost).

Shipping a release with a staging URL would route real users' opt-in submissions to the wrong database. Shipping with localhost would silently fail every submit. Full deployment workflow is in `docs/TELEMETRY.md` § "Deployment & release".

### 3. Commit

```bash
git add src/__version__.py CHANGELOG.md
git commit -m "chore: bump version to x.y.z"
```

### 4. Build + sign

**From a normal (non-elevated) shell, with the eToken plugged in:**

```bash
python build/windows/build.py
```

The script: checks prereqs → runs PyInstaller → signs all `.exe` in `dist/alpha-osk/` → builds NSIS installer → signs installer → verifies signatures.

### 5. Test the installer

1. Run `release/Alpha-OSK-Setup-x.y.z.exe`.
2. Verify it detects and removes the previous version (same directory: silent uninstall; different: prompts).
3. Verify install to `C:\Program Files\Alpha-OSK` and Desktop + Start Menu shortcuts.
4. Launch via the installer's "Launch Alpha-OSK" checkbox.
5. **Test UIAccess**: open an elevated Command Prompt (Run as Admin) and verify keystrokes reach it.
6. Verify `Settings → Updates` shows the new version.

### 6. Tag

```bash
git tag vX.Y.Z
git push origin main
git push origin vX.Y.Z
```

### 7. Create the GitHub release — on the PUBLIC releases repo

```bash
gh release create vX.Y.Z release/Alpha-OSK-Setup-X.Y.Z.exe \
  --repo okstudio1/alpha-osk-releases \
  --title "vX.Y.Z" \
  --notes "See https://github.com/okstudio1/alpha-osk/blob/main/CHANGELOG.md"
```

> ⚠️ **The `--repo okstudio1/alpha-osk-releases` flag is mandatory.** The source repo is private; the auto-updater can't see private releases (returns 404 to unauthenticated callers). Forgetting `--repo` will create the release in the source repo where end users' updaters won't find it. Tag the source repo for changelog tracking; publish binaries in the public repo.

### Tracking downloads

GitHub stamps a `download_count` on every release asset. To see the per-release breakdown and total:

```bash
python scripts/downloads.py
```

The script just wraps `gh api repos/okstudio1/alpha-osk-releases/releases --paginate` and sums each release's asset counts. Requires `gh` to be authenticated against an account with read access to the releases repo.

Caveat: the count includes auto-updater fetches as well as manual clicks from the release page — GitHub doesn't distinguish. Treat it as a directional number (downloads, not unique installs). If you ever need true install / DAU numbers, that requires a separate telemetry endpoint (off by default, opt-in setting) — see the auto-update doc for the model.

### Bundle size

PyInstaller spec at `build/windows/alpha-osk.spec` excludes `Qt6WebEngineCore.dll` (193 MB by itself) and the WebEngine / WebView / WebChannel families. Installer is ~85 MB instead of ~165 MB. If you ever add an in-app browser, re-include them in `excludes` and re-measure — losing 100 MB of installer in one careless re-include is easy.

To inspect the bundle:

```bash
du -sm dist/alpha-osk/PySide6/* | sort -rn | head -20
```

### Regenerating the app icon

Source logos in `assets/`. To rebuild `build/windows/alpha-osk.ico` from a new PNG:

```python
from PIL import Image
img = Image.open("assets/logo-1024.png").convert("RGBA")
sizes = [(256,256),(128,128),(64,64),(48,48),(32,32),(16,16)]
resized = [img.resize(s, Image.LANCZOS) for s in sizes]
resized[0].save("build/windows/alpha-osk.ico", format="ICO", sizes=sizes, append_images=resized[1:])
```

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
| Keys don't appear in **Blender / VirtualBox / a DirectInput game** | Pre-1.x.y versions used Unicode-only injection (`VK_PACKET`) which raw-input apps filter out | Update to a build that includes scancode-mode dispatch (see "Three Injection Modes" above). If still broken, file a bug with the app name and the foreground-window class. |
| Keys appear but **wrong case** in a specific app | OS Caps Lock LED out of sync with the OSK's Caps button | Toggle the OSK Caps button to resync, or press the physical Caps Lock once. The scancode path queries the OS Caps Lock LED, so the OSK side reflects whatever the OS thinks. |
| Keys appear but wrong characters on a non-US layout | Most chars take the scancode path which is layout-aware via `VkKeyScanW`; a few exotic chars fall back to Unicode mode (also layout-independent). File a bug with the layout name and the failing chars. |

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
| **Text injection** | xdotool `type` (per-char Unicode) | Per-char dispatch: `KEYEVENTF_SCANCODE` for ASCII, `KEYEVENTF_UNICODE` fallback for non-ASCII / dead-key / AltGr / unsafe-shift |
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
| `build/windows/alpha-osk.exe.manifest` | NEW — UIAccess manifest for EV signing |
| `build/windows/alpha-osk.spec` | NEW — PyInstaller build specification |

### Key Design Decisions

1. **ctypes, not pywin32**: We use `ctypes` to call `SendInput` directly
   rather than depending on `pywin32`.  This means **zero additional
   dependencies** on Windows — `ctypes` is part of the Python stdlib.

2. **Scancode mode for ASCII text, Unicode mode as fallback**: Originally we used `KEYEVENTF_UNICODE` for every printable character because it is layout-independent and supports the full Unicode range. That choice broke any application that filters on real virtual-key codes or reads raw scancodes (Blender, VirtualBox, DirectInput games, raw-input 3D / CAD / audio software) because Unicode injection synthesises a `WM_KEYDOWN(VK_PACKET)` that those apps ignore. The current default is `KEYEVENTF_SCANCODE`, which produces a normal `WM_KEYDOWN(VK_X)` derived from the scancode under the active layout. Per-character fallback to `KEYEVENTF_UNICODE` covers non-ASCII chars, dead-key triggers, AltGr-required chars, and the unsafe corner case where the user is physically holding Shift but the char does not need shift. See "Three Injection Modes" above for the full resolution path.

3. **Virtual-key mode for specials**: Special keys (Backspace, Enter,
   F-keys, arrows) and modifier combinations (Ctrl+C) use virtual-key
   codes, which is the correct approach for non-character keys. The scancode is also populated in `wScan` so remote-desktop forwarding works.

4. **UIAccess via manifest**: Rather than requiring the user to run as
   Administrator (which has security implications), we use the UIAccess
   mechanism designed specifically for assistive technology.

---

*Last updated: April 2026*
