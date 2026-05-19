# Accessibility Ecosystem — Alpha-OSK + MacroVox + Octavium + Nimbus

## Overview

Four tools that together form a complete adaptive input platform:

| Tool | Input | Output | Purpose |
|------|-------|--------|---------|
| **Alpha-OSK** | Mouse clicks on virtual keys | Keystrokes (SendInput) | On-screen keyboard with AI prediction |
| **MacroVox** | Voice (microphone) | Text (clipboard → paste) | Voice dictation with AI cleanup |
| **Octavium** | Mouse clicks on virtual keys/pads | MIDI notes/CC | Virtual MIDI keyboard for music |
| **Nimbus** | Mouse drag/click on virtual sticks/buttons | Joystick (vJoy/ViGEm) | Virtual game controller |

All four are:
- Built by the same developer (Owen Kent / OK Studio Inc.)
- Designed for users with motor disabilities
- Windows-native with EV code signing
- Mouse-driven (the common input device for adaptive users)
- PySide6/Qt-based (Alpha-OSK, Octavium, Nimbus) or Tauri (MacroVox)

## The Input Problem for Adaptive Users

A person with muscular dystrophy, cerebral palsy, or a spinal cord injury may only be able to use a mouse (or trackball, head tracker, eye gaze). They need:

- **Text input** → Alpha-OSK (keyboard predictions reduce effort)
- **Voice input** → MacroVox (speak when typing is too tiring)
- **Game input** → Nimbus (mouse → joystick with sensitivity curves + tremor filtering)
- **Music input** → Octavium (mouse → MIDI with scale quantization + latch)

Today these run as separate apps. The vision: a unified platform where they share context, profiles, and coordinate automatically.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Physical Input                         │
│  Mouse · Trackball · Head tracker · Eye gaze · Switch    │
└──────────────────────────┬──────────────────────────────┘
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
     ┌──────────────┐ ┌────────┐ ┌──────────┐
     │  Alpha-OSK   │ │ Nimbus │ │ Octavium │
     │  (keyboard)  │ │(gamepad)│ │  (MIDI)  │
     └──────┬───────┘ └───┬────┘ └────┬─────┘
            │             │           │
            ▼             ▼           ▼
       SendInput      vJoy/ViGEm   MIDI Port
       (keystrokes)   (joystick)   (notes/CC)
            │             │           │
            └─────────────┼───────────┘
                          ▼
              ┌──────────────────────┐
              │   Target Application  │
              │  Game · DAW · Editor  │
              └──────────────────────┘

              ┌──────────────┐
              │   MacroVox   │
              │  (dictation) │
              └──────┬───────┘
                     ▼
              Clipboard → Paste
              (into any app)
```

## Integration Phases

### Phase 1 — Coexistence (current state)

All four apps run independently. The user manually switches between them.

**What works today:**
- All four can run simultaneously without conflict
- Alpha-OSK uses `WS_EX_NOACTIVATE` — doesn't steal focus from games
- Nimbus has Game Focus Mode — receives input without stealing game focus
- Octavium opens as separate windows — doesn't interfere with other apps

### Phase 2 — Launch & Trigger

Each app can launch and trigger the others.

| From | To | Trigger |
|------|----|---------|
| Alpha-OSK | MacroVox | Mic icon → Ctrl+Space toggle |
| Alpha-OSK | Nimbus | Gamepad icon → launch Nimbus with gaming profile |
| Alpha-OSK | Octavium | Music icon → launch Octavium with DAW profile |
| Nimbus | Alpha-OSK | Button mapped to "open keyboard" |

**Implementation**: Process detection + launch + hotkey synthesis. No IPC needed.

### Phase 3 — Profile Auto-Switch

Detect the foreground application and coordinate all tools:

```json
{
  "name": "Gaming Session",
  "rules": [
    {"process": "Terraria.exe"},
    {"process": "Skyrim.exe"},
    {"process": "NMS.exe"}
  ],
  "alpha_osk": {"layout": "gaming-fps", "visible": false},
  "nimbus": {"profile": "xbox-standard"},
  "octavium": {"visible": false},
  "macrovox": {"visible": false}
}
```

```json
{
  "name": "Music Production",
  "rules": [
    {"process": "Ableton Live.exe"},
    {"process": "REAPER.exe"},
    {"process": "FL64.exe"}
  ],
  "alpha_osk": {"layout": "daw-shortcuts"},
  "nimbus": {"profile": "daw-faders", "visible": true},
  "octavium": {"profile": "keyboard-88", "visible": true},
  "macrovox": {"visible": false}
}
```

**Implementation**: Shared config file or named pipe coordinator. Alpha-OSK already monitors the foreground window — extend it to broadcast to the other apps.

### Phase 4 — Shared Input Layer

A unified input pipeline where any physical device can route to any output:

```
Head tracker → Nimbus (sensitivity curves) → vJoy axis
             → Alpha-OSK (dwell click on keys) → keystrokes
             → Octavium (pitch control) → MIDI CC

Eye gaze    → Alpha-OSK (gaze typing) → keystrokes
            → Nimbus (gaze-directed aim) → joystick

Switch      → Alpha-OSK (scanning) → keystrokes
            → Nimbus (button press) → gamepad button
            → Octavium (note trigger) → MIDI note
```

Nimbus becomes the **input hardware abstraction layer** — it reads any physical device, applies accessibility transforms (sensitivity curves, tremor filtering, dwell), and routes the processed input to whichever output tool is appropriate.

### Phase 5 — Unified UI

A single window that hosts panels from all four tools:

```
┌──────────────────────────────────────────────────────┐
│ [Alpha-OSK: Prediction Bar                         ] │
├────────────────────────┬─────────────────────────────┤
│                        │  [Nimbus: Left Stick]       │
│  [Alpha-OSK: QWERTY]  │  [Nimbus: Buttons A B X Y]  │
│                        │  [Nimbus: Right Stick]      │
├────────────────────────┼─────────────────────────────┤
│ [Octavium: Piano Keys] │  [MacroVox: Dictate Button] │
│                        │  [Octavium: Chord Pad]      │
└────────────────────────┴─────────────────────────────┘
```

Since Alpha-OSK, Octavium, and Nimbus are all PySide6/Qt, their widgets can be embedded in a single QML layout. MacroVox (Tauri) would communicate via IPC.

## Shared Technical Patterns

| Pattern | Alpha-OSK | Nimbus | Octavium | MacroVox |
|---------|-----------|--------|----------|----------|
| Framework | PySide6/QML | PySide6/QML | PySide6/Widgets | Tauri 2 (Rust) |
| Input injection | SendInput (ctypes) | vJoy/ViGEm | MIDI (mido) | enigo (Rust) |
| Window flags | WS_EX_NOACTIVATE | Game Focus Mode | Standard | Always-on-top |
| Config format | Qt Settings + JSON | JSON profiles | In-memory | localStorage |
| Build | PyInstaller + NSIS | PyInstaller + NSIS | PyInstaller + InnoSetup | Tauri bundler |
| Signing | EV cert (OK Studio) | EV cert (OK Studio) | EV cert (OK Studio) | EV cert (OK Studio) |
| Platform | Windows (Linux planned) | Windows | Windows | Windows |

## Nimbus Features Relevant to Alpha-OSK

| Nimbus Feature | Alpha-OSK Application |
|---------------|----------------------|
| **Drag-and-drop widget canvas** | Layout editor for custom keyboard panels |
| **Sensitivity curves** | Could apply to key repeat rate or prediction confidence |
| **Toggle mode buttons** | Already have sticky modifiers — same concept |
| **Tremor filtering** | Could filter rapid unintended key presses |
| **Profile JSON format** | Reference for Alpha-OSK's layout JSON format |
| **Game Focus Mode** | Alpha-OSK already has WS_EX_NOACTIVATE |
| **Auto-updater** | Reference implementation for Alpha-OSK's planned updater |
| **Cloud sync** | Future: shared profiles across devices |

## Nimbus Features Relevant to Octavium

| Nimbus Feature | Octavium Application |
|---------------|---------------------|
| **Joystick → button macro mode** | Joystick → MIDI note zone mapping |
| **Sensitivity curves** | Velocity curves for MIDI (already exists in Octavium) |
| **Hardware wrapping** | Read physical MIDI controller → apply Nimbus transforms → re-emit |
| **Profile system** | Reference for Octavium's planned profile persistence |

## Recommended Next Steps

1. **Document the ecosystem** (this file) ✅
2. **Alpha-OSK Phase 2**: Mic icon (MacroVox), gamepad icon (Nimbus) in title bar
3. **Shared foreground monitor**: Alpha-OSK broadcasts foreground window changes via named pipe; Nimbus and Octavium subscribe
4. **Nimbus keyboard output**: When Nimbus adds keyboard output mode, it becomes a direct complement to Alpha-OSK (joystick for movement, keyboard for text)
5. **Unified profile format**: JSON schema that all four tools can read for auto-switch rules
6. **Long term**: Embed Qt widgets from all three PySide6 tools into one window
