# Modular Keyboard Layouts

## Vision

Alpha-OSK currently has one layout: a standard QWERTY keyboard with prediction bar. But different tasks need different input surfaces:

| Use Case | What's Needed |
|----------|--------------|
| **General typing** | QWERTY + prediction bar (current) |
| **Video editing** | Transport controls (JKL), timeline shortcuts (Ctrl+Z, Ctrl+S), marker keys |
| **Gaming** | WASD cluster, number bar, function keys, macro buttons |
| **Streaming** | Scene switches (OBS hotkeys), chat macros, media controls |
| **Music production** | MIDI keys (via Octavium bridge), transport, mixer controls |
| **Data entry** | Numpad-focused, tab/enter prominent, field navigation |
| **AAC (communication)** | Large phrase buttons, category tabs, symbol boards |

The goal: let users switch between purpose-built layouts, and let power users create their own.

## Prior Art: Octavium

Owen's MIDI keyboard project (`C:\Users\Owen\dev\Octavium`) solves a similar problem for music. Key patterns to reuse:

**Layout data model** (`app/models.py`):
```python
class Layout(BaseModel):
    name: str
    rows: List[RowDef]
    columns: int
    gap: int
    base_octave: int
```

Each `KeyDef` specifies position, size, label, and action. Layouts are defined as data, not code — easy to serialize to JSON.

**Multi-window launcher**: Octavium opens each layout type (piano, pads, faders) as its own window with shared MIDI output. Alpha-OSK could do the same — a launcher that opens different layout windows, all sharing the same `SendInput` synthesizer.

**Modular panels**: Chord Pad, Faders, XY Pad are independent widgets. Alpha-OSK could treat prediction bar, key rows, numpad, macro grid as independent panels that snap together.

## Architecture

### Layout Definition Format

A layout is a JSON file that defines rows of keys with actions:

```json
{
  "name": "Video Editing",
  "author": "Alpha-OSK",
  "description": "DaVinci Resolve / Premiere Pro shortcuts",
  "version": "1.0",
  "rows": [
    {
      "keys": [
        {"label": "⏮", "action": {"type": "hotkey", "keys": ["Home"]}},
        {"label": "⏪", "action": {"type": "hotkey", "keys": ["J"]}},
        {"label": "⏸", "action": {"type": "hotkey", "keys": ["K"]}},
        {"label": "⏩", "action": {"type": "hotkey", "keys": ["L"]}},
        {"label": "⏭", "action": {"type": "hotkey", "keys": ["End"]}},
        {"label": "Cut", "action": {"type": "hotkey", "keys": ["Ctrl", "X"]}, "width": 1.5}
      ]
    },
    {
      "keys": [
        {"label": "Undo", "action": {"type": "hotkey", "keys": ["Ctrl", "Z"]}},
        {"label": "Redo", "action": {"type": "hotkey", "keys": ["Ctrl", "Shift", "Z"]}},
        {"label": "Save", "action": {"type": "hotkey", "keys": ["Ctrl", "S"]}},
        {"label": "Marker", "action": {"type": "hotkey", "keys": ["M"]}},
        {"label": "Razor", "action": {"type": "hotkey", "keys": ["B"]}}
      ]
    }
  ],
  "settings": {
    "showPredictionBar": false,
    "theme": "dark",
    "keySize": "large"
  }
}
```

### Action Types

| Type | Description | Example |
|------|------------|---------|
| `char` | Type a character (current behavior) | `{"type": "char", "key": "a"}` |
| `special` | Special key (current behavior) | `{"type": "special", "key": "backspace"}` |
| `hotkey` | Key combination via SendInput | `{"type": "hotkey", "keys": ["Ctrl", "C"]}` |
| `text` | Type a string of text | `{"type": "text", "text": "Hello world"}` |
| `macro` | Sequence of actions with delays | `{"type": "macro", "steps": [...]}` |
| `launch` | Run a program | `{"type": "launch", "path": "notepad.exe"}` |
| `layout` | Switch to another layout | `{"type": "layout", "name": "Gaming"}` |
| `midi` | Send MIDI (via Octavium bridge) | `{"type": "midi", "note": 60, "velocity": 100}` |

### Key Properties

```json
{
  "label": "Cut",
  "sublabel": "Ctrl+X",
  "action": {"type": "hotkey", "keys": ["Ctrl", "X"]},
  "width": 1.5,
  "height": 1.0,
  "color": "#e74c3c",
  "icon": "scissors",
  "holdAction": {"type": "hotkey", "keys": ["Ctrl", "Shift", "X"]},
  "repeatOnHold": false
}
```

## Levels of Modularity

### Level 1 — Built-in Layout Packs (low effort)

Ship pre-made layouts as JSON files in `data/layouts/`:

```
data/layouts/
├── qwerty.json          (default — current keyboard)
├── video-editing.json   (DaVinci/Premiere shortcuts)
├── gaming-fps.json      (WASD + number bar + function keys)
├── gaming-moba.json     (QWER abilities + item slots)
├── streaming-obs.json   (scene switches + audio controls)
├── data-entry.json      (numpad-focused + tab/enter)
├── aac-phrases.json     (large phrase buttons for communication)
└── emoji.json           (emoji picker grid)
```

Users switch layouts via a dropdown in the title bar or settings.

**Implementation**: Extend `keyboard_bridge.getLayoutRows()` to read from JSON files instead of hardcoded Python dicts. The QML already renders whatever `getLayoutRows()` returns.

### Level 2 — User-Created Layouts (medium effort)

A layout editor in settings where users can:

1. Start from a template (duplicate an existing layout)
2. Add/remove/rearrange keys
3. Assign actions to keys (dropdown: char, hotkey, text, macro)
4. Set key size, color, label
5. Save to `%APPDATA%/alpha-osk/layouts/`

**Implementation**: A QML-based grid editor. Each key is a draggable, resizable rectangle. Right-click to edit properties. Save as JSON.

### Level 3 — Panel Composition (higher effort, Octavium-inspired)

Instead of one monolithic layout, the keyboard is composed of snappable panels:

```
┌─────────────────────────────────────────────┐
│ [Prediction Bar                           ] │
├──────────────────────┬──────────────────────┤
│                      │  [Macro Grid]        │
│  [QWERTY Keys]       │  [Cut] [Copy] [Paste]│
│                      │  [Undo] [Redo] [Save]│
│                      │  [Scene1] [Scene2]   │
├──────────────────────┴──────────────────────┤
│ [Numpad]  [Arrow Keys]  [Media Controls]    │
└─────────────────────────────────────────────┘
```

Users drag panels into a grid. Each panel is an independent widget:
- **QWERTY Panel**: standard keyboard rows
- **Prediction Panel**: suggestion bar
- **Numpad Panel**: number grid
- **Macro Panel**: user-defined button grid
- **Media Panel**: play/pause/volume/mute
- **Navigation Panel**: arrows, home/end, page up/down
- **Custom Panel**: user-defined from layout JSON

**Implementation**: A QML `GridLayout` where each cell can hold a panel. Panels are registered as plugins. Drag-and-drop to rearrange. Save arrangement to user profile.

### Level 4 — App-Aware Profiles (future)

The keyboard detects which application is in the foreground (via `GetForegroundWindow` + `GetWindowText`) and automatically switches layouts:

| Foreground App | Layout |
|---------------|--------|
| DaVinci Resolve | Video Editing |
| Premiere Pro | Video Editing |
| OBS Studio | Streaming |
| Any game (fullscreen) | Gaming |
| Default | QWERTY |

**Implementation**: Extend the existing foreground window monitor in `keyboard_bridge.py`. Map window titles/process names to layout names in a config file.

## Profile System

A profile bundles layout + settings + theme:

```json
{
  "name": "Video Editing Session",
  "layout": "video-editing",
  "theme": "dark",
  "windowSize": [800, 300],
  "windowPosition": [100, 700],
  "opacity": 0.85,
  "predictionEnabled": false,
  "autoSwitchRules": [
    {"process": "resolve.exe", "activate": true},
    {"process": "premiere.exe", "activate": true}
  ]
}
```

Profiles stored in `%APPDATA%/alpha-osk/profiles/`. Quick-switch via tray icon menu or keyboard shortcut.

## Octavium Bridge (Music Production)

For music use cases, Alpha-OSK could launch Octavium panels:

1. Alpha-OSK detects a DAW in foreground (Ableton, FL Studio, Reaper)
2. Switches to a "Music Production" profile
3. Profile includes a MIDI panel that communicates with Octavium via shared MIDI port
4. Or embeds Octavium's `KeyboardWidget` directly (both are PySide6/Qt)

Since both apps use PySide6 and share the same signing cert, embedding is technically feasible — import Octavium's widget classes directly.

## Implementation Roadmap

| Phase | What | Effort |
|-------|------|--------|
| **1** | JSON layout format + 3-4 built-in packs | Low — extend `getLayoutRows()` |
| **2** | Layout switcher in title bar / tray menu | Low — dropdown + reload |
| **3** | `hotkey` and `text` action types in bridge | Medium — extend `pressKey` |
| **4** | User layout editor (QML grid editor) | Medium-High |
| **5** | Panel composition system | High |
| **6** | App-aware auto-switching | Medium — extend foreground monitor |
| **7** | Octavium bridge for MIDI panels | Medium — IPC or direct import |

## File Structure (after Phase 1-2)

```
data/layouts/
├── qwerty.json
├── video-editing.json
├── gaming-fps.json
├── streaming-obs.json
└── ...

%APPDATA%/alpha-osk/
├── layouts/           ← user-created layouts
│   └── my-custom.json
├── profiles/          ← layout + settings bundles
│   └── video-session.json
└── models/            ← prediction models (existing)
```
