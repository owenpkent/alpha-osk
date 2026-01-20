# Alpha-OSK Design Specification

## Layout Goals

This document defines the UX principles and layout specifications for Alpha-OSK.

---

## Design Philosophy

### 1. Accessibility First
Every feature must work for users with limited motor control. This means:
- **Large touch targets** — Minimum 48x48px, configurable up to 120px
- **Generous spacing** — Reduce accidental key presses
- **Dwell activation** — No click required
- **Scanning support** — Navigate with 1-2 switches

### 2. AI That Helps, Not Hinders
- Predictions should **reduce keystrokes**, not add cognitive load
- Voice input should be **seamless**, not a separate mode
- Learning should be **invisible** — it just gets better over time

### 3. Privacy by Design
- All learning happens **on-device** by default
- Federated learning is **opt-in**
- No raw text ever leaves the device

---

## Keyboard Layouts

### Primary: Adaptive QWERTY

```
┌─────────────────────────────────────────────────────────────┐
│  [Prediction 1]  [Prediction 2]  [Prediction 3]  [🎤 Voice] │
├─────────────────────────────────────────────────────────────┤
│  Q   W   E   R   T   Y   U   I   O   P   ⌫                  │
│  A   S   D   F   G   H   J   K   L   ⏎                      │
│  ⇧   Z   X   C   V   B   N   M   ,   .   ⇧                  │
│  123  🌐  ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣  ←  →                    │
└─────────────────────────────────────────────────────────────┘
```

**Key Features:**
- Top row: 3 AI predictions + voice toggle
- Enlarged spacebar for easier targeting
- Arrow keys for cursor navigation
- Shift keys on both sides

### Alternative: Frequency-Optimized

For users who benefit from less finger travel:

```
┌─────────────────────────────────────────────────────────────┐
│  [Prediction 1]  [Prediction 2]  [Prediction 3]  [🎤 Voice] │
├─────────────────────────────────────────────────────────────┤
│  E   T   A   O   I   N   S   R   H   L   ⌫                  │
│  D   C   U   M   W   F   G   Y   P   ⏎                      │
│  ⇧   B   V   K   J   X   Q   Z   ,   .   ⇧                  │
│  123  🌐  ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣ ␣  ←  →                    │
└─────────────────────────────────────────────────────────────┘
```

Most common letters in center, reducing travel distance.

### Compact: Reduced Layout

For limited screen space or one-handed use:

```
┌───────────────────────────┐
│ [Pred 1] [Pred 2] [Pred 3]│
├───────────────────────────┤
│  Q  W  E  R  T  Y  U  I  O│
│  A  S  D  F  G  H  J  K  L│
│  ⇧  Z  X  C  V  B  N  M  ⌫│
│  123  ␣ ␣ ␣ ␣ ␣ ␣ ␣   ⏎  │
└───────────────────────────┘
```

---

## Interaction Modes

### 1. Direct Touch/Click
- Standard tap-to-type
- Visual feedback on press
- Adjustable repeat delay

### 2. Dwell Click
- Hover over key for configurable duration (300ms–2000ms)
- Visual progress indicator (circular fill)
- Cancel by moving away

### 3. Scanning
- **Row-column scanning**: Highlight rows, then columns
- **Linear scanning**: One key at a time
- Configurable scan speed (500ms–3000ms per step)
- Works with 1 or 2 switches

### 4. Voice
- Push-to-talk or always-listening modes
- Visual waveform feedback
- Inline dictation with punctuation commands

---

## AI Prediction Behavior

### Prediction Display
- Show 3-5 predictions above keyboard
- First prediction can be selected with Space (optional)
- Predictions update after each keystroke

### Learning Modes

| Mode | Description | Privacy |
|------|-------------|---------|
| **Off** | Static dictionary only | Maximum |
| **Local** | Learns on-device, never shares | High |
| **Federated** | Contributes to shared model | Moderate |

### Specialized Vocabularies
- Medical terminology
- Assistive technology terms
- User-defined abbreviations
- Imported word lists

---

## Voice Dictation UX

### Activation
- **Toggle button** on keyboard
- **Hotkey** (configurable, e.g., F12)
- **Voice command** ("Hey Alpha, start dictation")

### Feedback
- Pulsing microphone icon when listening
- Real-time transcription preview
- Confidence highlighting (uncertain words dimmed)

### Commands
| Voice Command | Action |
|---------------|--------|
| "Delete" | Remove last word |
| "Delete all" | Clear text |
| "New line" | Insert line break |
| "Period" / "Comma" | Insert punctuation |
| "Capital [word]" | Capitalize next word |
| "Stop listening" | Deactivate voice |

---

## Accessibility Features

### Visual
- **High contrast mode** — Black/white/yellow themes
- **Large text** — Scalable key labels
- **Focus indicators** — Clear current key highlight
- **Reduced motion** — Disable animations

### Motor
- **Adjustable key size** — 48px to 120px
- **Key spacing** — 0px to 24px gaps
- **Sticky keys** — Modifiers stay active
- **Tremor filtering** — Ignore rapid repeated presses

### Cognitive
- **Simple mode** — Fewer keys, larger targets
- **Consistent layout** — No dynamic key rearrangement
- **Clear icons** — Text labels optional

---

## Federated Learning Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Alpha-OSK Device                        │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │ User Input  │→ │ Local Model │→ │ Personalized Pred.  │  │
│  └─────────────┘  └──────┬──────┘  └─────────────────────┘  │
│                          │                                   │
│                    (if opted in)                             │
│                          ↓                                   │
│              ┌───────────────────────┐                       │
│              │ Model Update (grads)  │                       │
│              └───────────┬───────────┘                       │
└──────────────────────────┼──────────────────────────────────┘
                           ↓
                 ┌─────────────────────┐
                 │  Aggregation Server │  (Flower)
                 │  (no raw text ever) │
                 └─────────────────────┘
                           ↓
              ┌────────────────────────────┐
              │ Improved Global Model      │
              │ (distributed back to users)│
              └────────────────────────────┘
```

**Privacy Guarantees:**
- Only model gradients shared, never keystrokes
- Differential privacy noise added
- Minimum participation threshold before aggregation

---

## Collaboration Features

### Shared Vocabularies
- Export personal dictionary as JSON
- Import community word lists
- Version-controlled abbreviation packs

### Accessibility Profiles
- Pre-configured layouts for common conditions
- One-click application
- Community-contributed profiles

### Example Profile: "Minimal Motor Control"
```json
{
  "name": "Minimal Motor Control",
  "key_size": 100,
  "key_spacing": 16,
  "dwell_time": 800,
  "scanning_enabled": true,
  "scan_speed": 1500,
  "predictions": 5,
  "voice_enabled": true
}
```

---

## Technical Requirements

### Performance
- **Keystroke latency**: < 50ms
- **Prediction update**: < 100ms
- **Voice transcription**: < 500ms (streaming)

### Compatibility
- Windows 10/11
- Works with all standard applications
- Compatible with other AT software (screen readers, switch interfaces)

### Resource Usage
- Idle: < 50MB RAM
- Active: < 200MB RAM
- GPU optional (for faster voice transcription)

---

## Inspiration: What We're Improving On

### GNOME On-Board (Linux)
✅ Great customization, dwell click, scanning  
❌ No AI prediction, no voice, Linux-only

### Windows OSK
✅ Built-in, always available  
❌ Dated UI, no learning, limited customization

### Gboard/SwiftKey (Mobile)
✅ Excellent prediction, learns vocabulary  
❌ Not designed for accessibility, mobile-only

**Alpha-OSK Goal:** Combine the accessibility focus of On-Board with the AI smarts of modern mobile keyboards.
