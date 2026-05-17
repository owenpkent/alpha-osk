# Extra Buttons / Beyond-the-Keyboard Features

Design exploration. Brainstorm of features that go past a literal keyboard, prioritised through the lens of accessibility for a wheelchair user with limited motor control. Captured 2026-05-16 so we can come back to it.

**Owner:** Owen. **Status:** not implemented, options menu only.

## Framing

A "nice keyboard" has media keys, volume, calculator. That's the floor. Alpha-OSK is mouse-driven and accessibility-first, so the more interesting question is: what features compress the cost of typing for someone who can only use a mouse? Chord shortcuts collapse into one click. Phrases collapse into one click. Cursor navigation collapses into one click. The accessibility win is real-estate-per-keystroke, not the keys themselves.

## Tier 1: highest accessibility value

### Snippet / phrase system

Persistent tiles for things you type often.

- **Pin a phrase as a tile.** "Best, Owen", your email, current date, signature block.
- **Trigger expansions.** Type `/addr` and it expands to a stored address.
- **Multi-clipboard.** 4-5 slots, each a button. Copy to slot 1, paste from slot 3. Win+V already does some of this, but it's two clicks and a popup; tiles are one click.
- **Auto-surface.** Phrases the n-gram engine has seen often could graduate into tile candidates. The data is already there in `ngram_predictor.trigrams` and longer-context tracking.

Storage: `<config_dir>/snippets.json`. Editor lives in Settings → Your Language Model (it's data the user maintains, so it fits the category).

**Effort:** medium. The output path is just `send_text`. The UX to add, edit, and reorder snippets is the real work.

### Cursor and selection ninja

Compresses chord shortcuts that already exist into single clicks.

- **Select word.** `Ctrl + Shift + Right`, or double-click in the target app if we route through mouse.
- **Select line.** `Home`, then `Shift + End`.
- **Select paragraph.** Editor-specific; falls back to triple-click.
- **Move line up / down.** `Alt + Up/Down` in VS Code, JetBrains, most editors.
- **Duplicate line.** `Ctrl + Shift + D` in VS Code, `Ctrl + D` in JetBrains.
- **Delete word forward / back.** `Ctrl + Delete` / `Ctrl + Backspace`.
- **Jump to start / end of line with selection.** `Shift + Home/End`.

**Effort:** low. All of these are chord sends through the existing `send_key` path. Wire them as preset chord buttons.

Open question: editor-specific chords like move-line and duplicate-line work in some apps and not others. Detection isn't free. Option: ship the buttons assuming VS Code and JetBrains conventions, then let users remap per-profile later.

### Mouse assist

For users who can't precisely click-and-hold or use a scroll wheel.

- **Dwell-click toggle.** Hovering for N ms counts as a click. Pairs with eye-tracking or limited-precision pointing.
- **Click-and-hold lock.** Press once to "start hold", drag freely, press to release. Replaces holding a button down (hard with reduced grip strength).
- **Scroll wheel buttons.** Scroll up / down / page up / page down without a wheel. Routes through `mouse_event` on Windows or `xdotool click 4/5` on Linux.
- **Middle-click button.** Single tile that synthesises a middle click at the current pointer position.
- **Right-click button.** Same for right click. Some users find the right mouse button physically harder to reach.

**Effort:** medium. Mouse synthesis is a new path, not currently in `platform/*.py`. Need a new `_synth.click(button)` and a global pointer-manipulation primitive. Dwell-click is a `QTimer` on hover events.

## Tier 2: novel and high-impact

### AI text helpers

Buttons that send selected or recent text through a model.

- **Rephrase.** Take the selected text, send to a local or cloud model, offer formal / casual / shorter / longer / proofread variants in the prediction bar.
- **Expand.** Type a brief like "thx mtg tmrw 3pm" and the AI expands it to "Thanks, see you at the meeting tomorrow at 3pm."
- **Spellcheck last sentence.** Clean up the most recent sentence.
- **Sentence completion.** Invoke a small local LLM to suggest a full sentence given the current paragraph. `transformer_predictor.py` is scaffolded for this; currently disabled.

**Effort:** high. Model choice matters. Cloud calls have privacy implications (off by default, opt-in like telemetry); local models have a size, latency, and RAM cost. Easiest first step: ship "Rephrase" backed by a small instruct model running on the GPU when available, fall back to a no-op when not.

**Privacy:** any text leaving the device is a category of risk Alpha-OSK doesn't currently take. Password-field detection already exists; the button must respect it. The opt-in toggle in Data & Privacy is the right home for the consent flow.

### Speech and multimodal

Ties to MacroVox and the broader ecosystem.

- **TTS readback of pills.** Keyboard reads each prediction tile aloud on hover, so you can confirm without looking down. Global toggle.
- **Read selection aloud.** Select text, fire TTS. Useful for proof-reading by ear.
- **Listen now.** Handoff to MacroVox to dictate the next sentence. A mode-switch tile.
- **Voice tag.** Brief verbal note that an AI helper expands into prose.

**Effort:** medium for TTS (Windows SAPI, Linux `espeak` or `festival`), high for full voice-mode integration with MacroVox (touches the ecosystem-handoff problem in `docs/ECOSYSTEM.md`).

### Smart contextual bar

A small row whose buttons change based on what the foreground app is, what's selected, or what's been typed.

- **URL detected in selection.** "Open in browser" / "Copy as link" / "Shorten" buttons appear.
- **Number selected.** Quick-math buttons appear (×2, ÷2, %).
- **Code editor focused.** Format and toggle-comment buttons appear.
- **Email composer detected.** Signature insert and send-with-formatting appear.
- **Markdown editor.** Bold, italic, link buttons appear.

**Effort:** high. Context detection is the hard part. Foreground exe is already polled, but inspecting selection requires platform-specific hooks (UIA on Windows, AT-SPI on Linux, AX on macOS). Worth considering as a flagship feature for the ecosystem story rather than a Phase 1 button.

## Tier 3: nice but conventional

### Standard "nice keyboard" buttons

- **Media.** Play/Pause, Next, Prev, Vol Up/Down, Mute. `VK_MEDIA_*` on Windows, `XF86AudioPlay` etc. on Linux.
- **Browser.** Back, Forward, Refresh. `VK_BROWSER_*`.
- **Launch.** Calculator, Mail (mostly outdated, skip).
- **Common chords.** Cut, Copy, Paste, Undo, Redo, Select All, Find, Save. One-click versions of the Ctrl-key chords.
- **OS shortcuts.** Win+D (show desktop), Win+L (lock), Win+. (emoji picker), Win+Shift+S (snipping tool), Win+V (clipboard history), Alt+Tab.

**Effort:** low. Each is a single VK or keysym, or a fixed chord, dispatched through the existing send path. Could ship as a "Media Panel" toggle in Settings → Appearance → Panels.

### Window and mode controls

- **Corner snap.** Move the OSK to TL / TR / BL / BR with one click.
- **Follow active window.** Keyboard automatically repositions next to whichever app is foreground.
- **Always-on-top temporary toggle.** Already always on top, but a one-shot "hide when I'm not typing" mode.
- **Lock keyboard.** Disable all click handling so a cat walking on the screen doesn't fire keys. Re-enable with a long-hold or a global hotkey.

**Effort:** low for snap and lock. Follow-active-window reuses the same hwnd polling already used for compat-mode detection; add a position-tracker on top.

## Tier 4: longer roadmap

### Profile / mode switching

Already designed in `docs/MODULAR_LAYOUTS.md`.

- One-click swap: Coding / Email / Chat / Gaming.
- Each profile = layout + theme + window position + active panels + enabled extras.
- Auto-switch by foreground app.

**Effort:** medium-high. Layout JSON exists; profile bundling and an editor UI are the missing pieces.

### Ecosystem handoff

The four-tool accessibility stack from `docs/ECOSYSTEM.md`: Alpha-OSK, MacroVox, Octavium, Nimbus.

- **Switch to Octavium.** Launch or focus Octavium, hide self.
- **Switch to Nimbus.** Same for joystick mode.
- **Switch to MacroVox.** Same for voice dictation.
- **Unified launcher** tile in the title bar.

**Effort:** medium. Each is a process launch plus window-state coordination. The deeper integration (shared input layer, unified UI) is a multi-phase project tracked in `docs/ECOSYSTEM.md`.

## Cross-cutting design questions

These apply regardless of which tier(s) we ship.

### Placement

Three plausible homes for new buttons:

1. **New toggleable panel** next to Navigation / Numpad. Discoverable via Settings → Appearance → Panels. Costs horizontal real estate when on.
2. **Mixed into existing panels.** Media keys into the function row, chord shortcuts into the navigation panel. No new toggle.
3. **Compact icon row above the title bar.** Thin, always visible. Doesn't grow the footprint much, but harder to read at small sizes.

Lean toward (1) for any category with more than 4-5 buttons, (3) for status-style controls (lock keyboard, follow-window, always-on-top), and (2) only when the addition fits naturally (media keys in the F-row makes physical sense; cursor-ninja buttons in the nav panel do too).

### Customisation

Hard-coded buttons age badly. Long-term, the right shape is a tile manifest:

```json
{
  "id": "copy",
  "label": "Copy",
  "action": { "type": "hotkey", "keys": ["ctrl", "c"] },
  "panel": "shortcuts",
  "position": [0, 0]
}
```

This mirrors the action model in `docs/MODULAR_LAYOUTS.md` (`char`, `special`, `hotkey`, `text`, `macro`, `launch`, `layout`, `midi`). Ship the first batch hard-coded, but use the same action types so the manifest path is a future refactor, not a rewrite.

### Settings real estate

A "Smart Typing" sub-page is already busy. Likely add a new top-level category, **Tiles** or **Shortcuts**, under Settings, with sub-pages for snippets, chord buttons, media keys, and mouse-assist toggles. Keep existing categories from bloating.

### Profile boundary

Custom buttons should be per-profile when profiles ship (Tier 4), but work as a single global set in the meantime. Don't design the storage in a way that's hostile to per-profile later. Suggest `<config_dir>/tiles.json` with an optional `profile` field defaulting to `"default"`.

## Recommended path

If we wanted to ship something soon that lands the biggest accessibility win for the smallest scope:

1. **Snippets system** (Tier 1). Roughly a week. Storage + editor + tile rendering + tap handler. Reuses `send_text`.
2. **Cursor ninja buttons** (Tier 1). A couple of days as preset chord tiles inside the navigation panel.
3. **Mouse assist toggles** (Tier 1). About a week, mostly new platform primitives plus a settings page.

Together (~2-3 weeks) these cover the "compress chords into clicks" and "compress repetitive phrases into clicks" axes, which are the two highest-leverage accessibility wins. Everything else can follow.

## Cross-references

- `docs/MODULAR_LAYOUTS.md`: action types and the broader customisation story.
- `docs/ECOSYSTEM.md`: MacroVox / Octavium / Nimbus integration surface.
- `docs/MACROVOX_INTEGRATION.md`: voice-dictation specifics.
- `docs/LONG_PRESS_ALTERNATES.md`: why we don't already have a long-press → accents UI, and the constraints that apply to any press-on-release feature.
