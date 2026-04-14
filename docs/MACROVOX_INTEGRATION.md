# MacroVox + Alpha-OSK Integration Plan

## Overview

**MacroVox** (`C:\Users\Owen\dev\MacroVox`) is a managed Windows voice dictation app. **Alpha-OSK** is an AI-powered on-screen keyboard. Both are accessibility tools for motor-impaired users built by the same developer. Together they form a complete text input suite: type when you can, dictate when you can't.

## What MacroVox Does

- Voice-to-text via Deepgram nova-3 (WebSocket streaming or batch)
- AI transcript cleanup via Claude Haiku (Pro subscribers)
- Agentic writing — speak a request, get polished output via Claude Sonnet
- Global hotkey (`Ctrl+Space`) triggers dictation from any app
- Auto-paste into the focused window via native `SendInput` (enigo crate)
- Tauri 2 app (Rust backend + React/Vite frontend)
- Always-on-top dictation window (380x360px)

## Why Integrate

| Scenario | Without integration | With integration |
|----------|-------------------|-----------------|
| User wants to type a quick word | Use Alpha-OSK keyboard | Same |
| User wants to dictate a sentence | Alt-tab to MacroVox, Ctrl+Space, speak, wait, paste | Click mic icon on Alpha-OSK, speak, text appears |
| Dictated text needs correction | Retype with Alpha-OSK | Alpha-OSK predictions learn from dictated text, corrections are faster |
| User switches between typing and voice | Two separate apps, no shared context | Unified context — dictation feeds into prediction model |

## Integration Phases

### Phase 1 — Launch & Trigger (low effort)

Alpha-OSK gets a microphone button in the title bar or prediction bar. Clicking it:

1. Checks if MacroVox is running (look for the process or a named mutex)
2. If not running, launches it (`start "" "C:\Program Files\MacroVox\MacroVox.exe"`)
3. Sends a trigger to start/stop dictation

**Implementation:**
- Alpha-OSK side: new title bar icon (mic), `Slot` in `keyboard_bridge.py` that launches MacroVox
- Communication: use the existing `Ctrl+Space` global hotkey — Alpha-OSK can synthesize it via `SendInput` to toggle MacroVox recording
- No changes to MacroVox needed

**Files to modify:**
- `qml/Main.qml` — add mic icon to title bar
- `src/keyboard_bridge.py` — add `toggleDictation()` slot
- `src/platform/windows.py` — send `Ctrl+Space` combo

### Phase 2 — Clipboard Bridge (medium effort)

After MacroVox pastes dictated text, Alpha-OSK picks it up and updates its prediction context.

**How it works:**
- MacroVox writes to clipboard, then sends `Ctrl+V` to paste
- Alpha-OSK monitors the clipboard for changes (or listens for a custom Windows message)
- When dictated text arrives, Alpha-OSK updates `_context_buffer` and `_sentence_buffer` so predictions are informed by what was just dictated
- The prediction engine learns from dictated text (same as typed text)

**Implementation:**
- Alpha-OSK side: `QClipboard` monitoring in `keyboard_app.py`
- Filter: only update context when MacroVox is the clipboard source (check window title or use a clipboard format marker)
- Feed clipboard text into `_predictor.learn()` and `_context_buffer`

**Files to modify:**
- `src/keyboard_app.py` — clipboard monitoring
- `src/keyboard_bridge.py` — `updateContextFromDictation(text)` slot

### Phase 3 — IPC Channel (higher effort, richer integration)

Direct communication between the two apps via named pipe or localhost WebSocket.

**Capabilities:**
- Alpha-OSK sends current context to MacroVox for Deepgram keyword boosting (better recognition of words the user has been typing)
- MacroVox sends transcript directly to Alpha-OSK (bypasses clipboard)
- MacroVox sends "dictation active" / "dictation ended" signals so Alpha-OSK can show status
- Shared settings sync (theme, accessibility profile)

**Protocol (simple JSON over named pipe):**
```json
// MacroVox → Alpha-OSK
{"type": "transcript", "text": "hello world", "final": true}
{"type": "status", "recording": true}

// Alpha-OSK → MacroVox  
{"type": "context", "recent_words": ["the", "quick", "brown"]}
{"type": "boost_keywords", "words": ["accessibility", "keyboard"]}
```

**Implementation:**
- Named pipe: `\\.\pipe\alpha-osk-macrovox`
- Alpha-OSK side: Python `asyncio` pipe server in `keyboard_bridge.py`
- MacroVox side: Rust named pipe client in `commands.rs`
- Fallback: if pipe not available, fall back to clipboard bridge (Phase 2)

### Phase 4 — Unified Suite (future)

- Single installer that installs both Alpha-OSK and MacroVox
- Shared auth (MacroVox Pro subscription unlocks dictation in Alpha-OSK)
- Shared analytics dashboard
- Combined settings panel
- Single system tray icon with both tools

## Architecture Diagram

```
┌─────────────────────────────────────────────┐
│                  User                        │
│                                              │
│   Typing (motor OK)    Speaking (motor tired)│
│        │                      │              │
│        ▼                      ▼              │
│  ┌──────────┐          ┌───────────┐         │
│  │ Alpha-OSK│◄────────►│ MacroVox  │         │
│  │ (Python) │  IPC/    │ (Rust)    │         │
│  │          │  Clipboard│          │         │
│  └────┬─────┘          └─────┬─────┘         │
│       │                      │               │
│       ▼                      ▼               │
│  SendInput (keys)      Deepgram (STT)        │
│       │                      │               │
│       ▼                      ▼               │
│  ┌──────────────────────────────────┐        │
│  │        Target Application        │        │
│  │     (Slack, Word, browser...)    │        │
│  └──────────────────────────────────┘        │
└─────────────────────────────────────────────┘
```

## Shared Technical Details

| Aspect | Alpha-OSK | MacroVox |
|--------|-----------|----------|
| Input injection | `SendInput` via ctypes | `SendInput` via enigo |
| Window behavior | `WS_EX_NOACTIVATE`, always-on-top | Always-on-top, minimize-to-tray |
| Config directory | `%APPDATA%\alpha-osk\` | `%LOCALAPPDATA%\MacroVox\` |
| Build system | PyInstaller + NSIS | Tauri 2 (built-in bundler) |
| Signing | EV cert (OK Studio Inc.) | Same EV cert |
| Platform | Windows (Linux planned) | Windows (macOS planned) |

## Key Risks

- **Input conflict**: Both apps use `SendInput`. If both try to inject simultaneously, keystrokes could interleave. Mitigation: Alpha-OSK pauses prediction/injection while MacroVox is actively dictating (Phase 3 status signal).
- **Focus fighting**: Both are always-on-top. They need to not overlap. Mitigation: Alpha-OSK docks to bottom, MacroVox floats top-right (or integrates into Alpha-OSK's UI in Phase 4).
- **Double-paste**: MacroVox auto-pastes via `Ctrl+V`. If Alpha-OSK intercepts modifier keys, it could interfere. Mitigation: Alpha-OSK should not intercept `Ctrl+V` from other processes (it only tracks keys it sends itself).

## Getting Started (Phase 1)

Phase 1 requires no changes to MacroVox. Steps:

1. Add mic icon to Alpha-OSK title bar (Canvas-drawn, like the privacy icon)
2. On click: check if MacroVox is running, launch if not
3. Send `Ctrl+Space` via `SendInput` to toggle dictation
4. Optionally show "Dictating..." in the prediction bar while MacroVox is active
