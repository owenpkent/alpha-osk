# Platform Architecture

How Alpha-OSK achieves cross-platform support for Linux and Windows while
keeping the codebase clean, maintainable, and well-separated.

---

## Table of Contents

- [Overview](#overview)
- [Design Principles](#design-principles)
- [Directory Structure](#directory-structure)
- [Platform Abstraction Layer](#platform-abstraction-layer)
- [Key Synthesis: How Each Platform Works](#key-synthesis-how-each-platform-works)
- [Window Management](#window-management)
- [Configuration and Data Storage](#configuration-and-data-storage)
- [What's Shared vs. Platform-Specific](#whats-shared-vs-platform-specific)
- [Adding a New Platform](#adding-a-new-platform)
- [Testing Strategy](#testing-strategy)
- [Decision Log](#decision-log)

---

## Overview

Alpha-OSK started as a Linux-only on-screen keyboard.  The Windows port
was added by introducing a **platform abstraction layer** (`src/platform/`)
that encapsulates all OS-specific behaviour behind a common interface.

The rest of the codebase — UI (QML), prediction engine, keyboard bridge
logic, data files — is **100% shared** between platforms.

```
                    ┌──────────────────────────────────────┐
                    │          Shared Code (~95%)           │
                    │                                      │
                    │  QML UI ─── KeyboardBridge ─── AI    │
                    │  Main.qml   keyboard_bridge.py       │
                    │  KeyButton   modifiers, predictions   │
                    │  Settings    context tracking         │
                    │                                      │
                    └──────────────┬───────────────────────┘
                                   │
                         ┌─────────▼──────────┐
                         │  Platform Layer     │
                         │  src/platform/      │
                         │                     │
                         │  __init__.py        │
                         │  base.py            │
                         │  linux.py           │
                         │  windows.py         │
                         └─────────┬───────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    │              │              │
              ┌─────▼────┐  ┌─────▼─────┐  ┌────▼──────┐
              │  Linux   │  │  Windows  │  │  Future   │
              │  xdotool │  │  SendInput│  │  macOS?   │
              │  ydotool │  │  + ctypes │  │  Wayland? │
              └──────────┘  └───────────┘  └───────────┘
```

---

## Design Principles

### 1. Shared Code by Default

The platform layer is the **only** place where `if windows` / `if linux`
logic exists.  Everything else — QML, bridge, prediction, data — is
platform-agnostic.

### 2. Program Against Interfaces

`keyboard_bridge.py` depends on `KeySynthesizerBase` (an abstract class),
never on `LinuxKeySynthesizer` or `WindowsKeySynthesizer` directly.  The
factory function `create_key_synthesizer()` returns the correct concrete
class at runtime.

### 3. Zero External Dependencies on Windows

The Windows backend uses only `ctypes` (Python standard library) to call
`SendInput`.  No `pywin32`, no `pynput`, no `keyboard` package.  This
eliminates dependency management complexity and version conflicts.

### 4. Fail Gracefully

If the platform layer can't find a working backend (e.g. no `xdotool` on
Linux), the keyboard still launches — it just can't send keystrokes.
Predictions, UI, settings all still work.

### 5. Document Everything

Every platform-specific decision is documented in this file.  Every
function has docstrings explaining what it does and why.

---

## Directory Structure

```
src/platform/
├── __init__.py      # Factory function, platform detection, config paths
├── base.py          # Abstract base class: KeySynthesizerBase
├── linux.py         # Linux: xdotool (X11) / ydotool (Wayland)
└── windows.py       # Windows: SendInput via ctypes + shortcut helpers

build/
├── alpha-osk.spec            # PyInstaller build specification
├── alpha-osk.exe.manifest    # UIAccess manifest for EV signing
├── alpha-osk.ico             # Application icon
├── sign.py                   # Code signing with retry logic (matches gitconnect)
├── build_windows.py          # Full pipeline: PyInstaller → Sign → NSIS → Verify
└── installer.nsh             # NSIS installer customizations (shortcuts, cleanup)
```

### `__init__.py` — The Public API

Exports:
- `CURRENT_PLATFORM` — `"windows"`, `"linux"`, or `"unsupported"`
- `create_key_synthesizer()` — Factory that returns the correct backend
- `get_platform_info()` — Diagnostic info dict for logging/UI
- `get_config_dir()` — Platform-appropriate config directory
- `get_model_dir()` — Platform-appropriate model storage directory

### `base.py` — The Interface

Defines `KeySynthesizerBase` with these abstract methods:
- `is_available()` — Can we send keys?
- `backend_name()` — Human-readable name for logs
- `send_key(key_name, modifiers)` — Send a single keystroke
- `send_text(text)` — Type a Unicode string
- `send_combination(keys)` — Send a key chord (e.g. Ctrl+C)

### `linux.py` — Linux Backend

- Detects `xdotool` or `ydotool` on `$PATH`
- Prefers `ydotool` on Wayland, `xdotool` on X11
- Uses `subprocess.Popen` for fire-and-forget key injection

### `windows.py` — Windows Backend

- Uses `ctypes.windll.user32.SendInput` directly
- Virtual-key mode for special keys and modifier combos
- Unicode mode (`KEYEVENTF_UNICODE`) for text characters
- Handles extended keys, surrogate pairs, UIAccess detection

---

## Key Synthesis: How Each Platform Works

### Linux: xdotool / ydotool

```
pressKey("a") in QML
    → KeyboardBridge._send_text("a")
        → LinuxKeySynthesizer.send_text("a")
            → subprocess.Popen(["xdotool", "type", "--clearmodifiers", "a"])
                → X11 server receives synthetic KeyPress event
                    → Focused application receives the keystroke
```

For modifier combos (Ctrl+C):
```
pressKey("c") with Ctrl active
    → KeyboardBridge._send_key("c")
        → LinuxKeySynthesizer.send_key("c", modifiers=["ctrl"])
            → subprocess.Popen(["xdotool", "key", "--clearmodifiers", "ctrl+c"])
```

### Windows: SendInput

```
pressKey("a") in QML
    → KeyboardBridge._send_text("a")
        → WindowsKeySynthesizer.send_text("a")
            → Build KEYBDINPUT with KEYEVENTF_UNICODE, wScan=ord('a')
            → SendInput(2, [key_down, key_up], sizeof(INPUT))
                → Windows input queue receives the event
                    → Focused application receives the keystroke
```

For modifier combos (Ctrl+C):
```
pressKey("c") with Ctrl active
    → KeyboardBridge._send_key("c")
        → WindowsKeySynthesizer.send_key("c", modifiers=["ctrl"])
            → Build array: [Ctrl_down, C_down, C_up, Ctrl_up]
            → SendInput(4, array, sizeof(INPUT))
```

**Key difference**: Linux fires a subprocess per keystroke (fast enough in
practice, ~1ms).  Windows calls `SendInput` directly in-process via ctypes
(~0.01ms).

---

## Window Management

### Shared (Qt Flags)

Applied in `keyboard_app.py` → `_apply_window_flags()`:

| Flag | Purpose |
|------|---------|
| `WindowStaysOnTopHint` | Keep above normal windows |
| `Tool` | Utility window (not in taskbar on Linux) |
| `FramelessWindowHint` | We draw our own title bar in QML |
| `WindowDoesNotAcceptFocus` | Qt-level focus prevention |

### Windows-Specific (Win32 API)

Applied in `keyboard_app.py` → `_apply_windows_extended_styles()`:

| Extended Style | Hex | Purpose |
|---------------|-----|---------|
| `WS_EX_NOACTIVATE` | `0x08000000` | **Critical**: Never activated on click |
| `WS_EX_TOOLWINDOW` | `0x00000080` | Hidden from Alt+Tab and taskbar |
| `WS_EX_TOPMOST` | `0x00000008` | Defence-in-depth topmost |

`WS_EX_NOACTIVATE` is the most important — without it, clicking any key
on the OSK would steal focus from the user's text editor, making the
keyboard useless.  Qt's `WindowDoesNotAcceptFocus` is a Qt-level hint that
doesn't always work on Windows; `WS_EX_NOACTIVATE` is the OS-level
enforcement.

---

## Configuration and Data Storage

### Config Directories

| Platform | Path | Example |
|----------|------|---------|
| Linux | `~/.config/alpha-osk/` | `/home/owen/.config/alpha-osk/` |
| Windows | `%APPDATA%\alpha-osk\` | `C:\Users\Owen\AppData\Roaming\alpha-osk\` |

### Model Storage

| Platform | Path |
|----------|------|
| Linux | `~/.config/alpha-osk/models/` |
| Windows | `%APPDATA%\alpha-osk\models\` |

Models stored:
- `ngram_model.json` — N-gram word frequency model
- `ppm_model.json` — PPM character-level model (Dasher algorithm)

### How It's Implemented

`src/platform/__init__.py` exports `get_config_dir()` and `get_model_dir()`
which return the correct `Path` for the current platform.  These are called
by `hybrid_predictor.py` when initialising the prediction engine.

---

## What's Shared vs. Platform-Specific

### 100% Shared (No Platform Logic)

| Component | Files |
|-----------|-------|
| QML UI | `qml/Main.qml`, `qml/components/*.qml` |
| Prediction engine | `src/prediction/*.py` |
| Bridge logic | `src/keyboard_bridge.py` (modifier state, predictions, context) |
| Data files | `data/*.txt` |
| Dashboard | `templates/*.html` |

### Platform-Aware (Conditional Logic)

| Component | File | What's Different |
|-----------|------|-----------------|
| Key synthesis | `src/platform/linux.py`, `src/platform/windows.py` | Entirely different implementations |
| Platform detection | `src/platform/__init__.py` | Factory + config paths |
| Window flags | `src/keyboard_app.py` | Win32 extended styles on Windows |
| Env setup | `src/keyboard_app.py` | `QT_QPA_PLATFORM=xcb` on Linux only |
| Launcher | `run.py` | Venv path (`bin` vs `Scripts`), dep checks |

---

## Adding a New Platform

To add support for a new platform (e.g. macOS):

### 1. Create the Backend

Create `src/platform/macos.py`:

```python
from .base import KeySynthesizerBase

class MacOSKeySynthesizer(KeySynthesizerBase):
    def is_available(self) -> bool: ...
    def backend_name(self) -> str: ...
    def send_key(self, key_name, modifiers=None) -> None: ...
    def send_text(self, text) -> None: ...
    def send_combination(self, keys) -> None: ...
```

### 2. Register in the Factory

Update `src/platform/__init__.py`:

```python
elif sys.platform == "darwin":
    CURRENT_PLATFORM = "macos"

# In create_key_synthesizer():
elif CURRENT_PLATFORM == "macos":
    from .macos import MacOSKeySynthesizer
    return MacOSKeySynthesizer()
```

### 3. Update Config Paths

Add macOS paths to `get_config_dir()`:

```python
elif CURRENT_PLATFORM == "macos":
    config_dir = Path.home() / "Library" / "Application Support" / "alpha-osk"
```

### 4. Update the Launcher

Add macOS venv path to `run.py`:

```python
def get_venv_python():
    if IS_MACOS:
        return SCRIPT_DIR / "venv" / "bin" / "python"
```

### 5. Document

- Add a `docs/MACOS.md` guide.
- Update this file.
- Update `README.md`.

---

## Testing Strategy

### Unit Testing the Platform Layer

Each backend can be tested independently:

```python
# Test Windows backend
from src.platform.windows import WindowsKeySynthesizer
synth = WindowsKeySynthesizer()
assert synth.is_available()
assert synth.backend_name() in ("SendInput", "SendInput+UIAccess")

# Test key resolution
assert synth._resolve_vk("BackSpace") == 0x08
assert synth._resolve_vk("a") == 0x41
assert synth._resolve_vk("F1") == 0x70
```

### Integration Testing

1. Launch Alpha-OSK.
2. Open a text editor (Notepad, gedit, etc.).
3. Click keys on Alpha-OSK.
4. Verify characters appear in the text editor.
5. Test modifier combos: Ctrl+A (select all), Ctrl+C (copy), Ctrl+V (paste).
6. Test special keys: Backspace, Enter, Tab, arrows.

### UIAccess Testing (Windows)

1. Build and sign with EV certificate.
2. Install to `C:\Program Files\Alpha-OSK\`.
3. Open an **elevated** Command Prompt.
4. Click keys on Alpha-OSK.
5. Verify characters appear in the elevated prompt.

---

## Decision Log

### Why ctypes Instead of pywin32?

**Decision**: Use `ctypes` to call `SendInput` directly.

**Rationale**:
- Zero additional dependencies (ctypes is stdlib).
- `SendInput` is a simple function with well-defined C structures.
- `pywin32` is a large package (~30MB) and we'd only use a tiny fraction.
- Reduces installation complexity for end users.

### Why Not pynput or keyboard Package?

**Decision**: Implement our own synthesizer using `SendInput`.

**Rationale**:
- `pynput` and `keyboard` are generic input libraries — we need
  OSK-specific behaviour (UIAccess, no-focus, modifier management).
- Neither supports UIAccess manifests.
- Both add unnecessary abstraction layers and dependencies.
- Our implementation is ~300 lines and does exactly what we need.

### Why Unicode Mode for Text?

**Decision**: Use `KEYEVENTF_UNICODE` for all printable characters.

**Rationale**:
- Layout-independent: works with any keyboard layout.
- Supports the full Unicode range (including emoji via surrogate pairs).
- Simpler than mapping characters → virtual key codes → scan codes.
- The only downside is that some legacy applications don't support
  Unicode input events, but this is extremely rare in modern Windows.

### Why WS_EX_NOACTIVATE via SetWindowLongW?

**Decision**: Apply `WS_EX_NOACTIVATE` via Win32 API after Qt window
creation.

**Rationale**:
- Qt's `WindowDoesNotAcceptFocus` doesn't reliably prevent activation on
  Windows (it's a hint, not enforcement).
- `WS_EX_NOACTIVATE` is the OS-level mechanism that guarantees the window
  is never activated, even on click.
- Must be applied post-creation because Qt creates the native window
  during `engine.load()`.

### Why Separate Platform Files Instead of if/else in Bridge?

**Decision**: Full platform abstraction with separate files and a factory.

**Rationale**:
- Clean separation of concerns.
- Easy to add new platforms without modifying existing code (Open/Closed
  Principle).
- Each platform file can be understood independently.
- Makes testing easier — you can instantiate a specific backend directly.
- Avoids `if sys.platform == ...` scattered throughout the codebase.

### Why PassThrough DPI Rounding Policy?

**Decision**: Call `QGuiApplication.setHighDpiScaleFactorRoundingPolicy(PassThrough)`
before creating `QGuiApplication` on all platforms.

**Rationale**:
- Qt 6's default `RoundPreferFloor` policy rounds each monitor's scale factor
  (e.g. 1.5 → 1.0).  When the window moves to a monitor whose rounded factor
  differs from the source monitor's rounded factor, Qt re-scales the logical
  window dimensions — making the keyboard grow or shrink unexpectedly.
- `PassThrough` uses the exact fractional DPI ratio from the OS, so no
  rounding discontinuity occurs across monitor transitions.
- A complementary `onScreenChanged` handler in `Main.qml` clamps `root.width`
  to `Screen.desktopAvailableWidth - 40` as a safety net for any remaining
  edge cases.

### Why NSIS Instead of WiX or Inno Setup?

**Decision**: Use NSIS for the Windows installer.

**Rationale**:
- Same installer technology used by gitconnect — proven patterns we can
  reuse (`installer.nsh` macros, shortcut creation, old-version cleanup).
- Free and open source.
- Widely supported — users trust NSIS installers.
- Easily automated from `build_windows.py` by generating `.nsi` scripts
  programmatically.

### Why a Python Signing Script Instead of Inline Commands?

**Decision**: Create `build/sign.py` as a dedicated signing tool with
retry logic (matching gitconnect's `build/sign.js`).

**Rationale**:
- Windows Defender frequently locks `.exe` files mid-build while scanning
  them, causing `signtool` to fail with "file being used by another
  process."  Retry logic with exponential backoff solves this reliably.
- Encapsulating certificate thumbprint, timestamp server, and signtool
  discovery in one file avoids duplication and mistakes.
- The same script works standalone (`python build/sign.py file.exe`) and
  as a library imported by `build_windows.py`.

---

## All Files (Complete Inventory)

### Platform Abstraction (Phase 7)

| File | Purpose |
|------|---------|
| `src/platform/__init__.py` | Platform detection, factory, config/model paths |
| `src/platform/base.py` | Abstract `KeySynthesizerBase` interface |
| `src/platform/linux.py` | Linux: xdotool / ydotool backend |
| `src/platform/windows.py` | Windows: SendInput backend + shortcut helpers |

### Modified for Cross-Platform (Phase 7)

| File | Change |
|------|--------|
| `src/keyboard_bridge.py` | Uses platform layer instead of direct xdotool |
| `src/keyboard_app.py` | Cross-platform env setup, Win32 extended styles |
| `src/prediction/hybrid_predictor.py` | Cross-platform model directory |
| `run.py` | Cross-platform venv paths and dep checks |

### Build Infrastructure (Phase 8)

| File | Purpose |
|------|---------|
| `build/alpha-osk.spec` | PyInstaller build specification |
| `build/alpha-osk.exe.manifest` | UIAccess manifest for EV signing |
| `build/alpha-osk.ico` | Application icon |
| `build/sign.py` | Code signing with retry logic (matches gitconnect) |
| `build/build_windows.py` | Full pipeline: PyInstaller → Sign → NSIS → Verify |
| `build/installer.nsh` | NSIS installer macros (shortcuts, cleanup) |

### Documentation

| File | Purpose |
|------|---------|
| `docs/WINDOWS.md` | Windows setup, signing, NSIS, UIAccess, troubleshooting |
| `docs/PLATFORM_ARCHITECTURE.md` | This file — design rationale and decisions |

---

*Last updated: March 2026*
