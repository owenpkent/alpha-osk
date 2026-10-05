# Ideas

A scratchpad for features, experiments, and directions worth exploring. Nothing here is committed to — it's a place to capture thoughts before they get lost.

To add an idea, drop it under the right heading with a short description. If it matures into real work, move it to `TODO.md` or `ROADMAP.md`.

---

## Word Prediction Board (Drum Pad Mode)

A dedicated word-focused input surface — less keyboard, more instrument. Think a grid of large, tappable word tiles (like a drum pad or Launchpad) that updates dynamically based on context.

**Core concept:**
- Grid of word buttons (e.g., 4x4 or configurable) instead of letter keys
- Each tile shows a predicted word, sized or colored by probability
- Tapping a word inserts it and refreshes the entire grid with new predictions
- Optimizes for word-level input speed — fewer taps per sentence than letter-by-letter typing

**Open questions:**
- How does the user start a new word that isn't predicted? Fall back to the keyboard, or have a "spell it out" tile?
- Should tiles animate/reflow as predictions update, or snap?
- Could tile position stay stable for frequent words (muscle memory) while less common words rotate?
- Does this replace the prediction bar, or is it a separate mode/view?

---

## Modern UI Overhaul

Bring the look and feel up to current design standards — the keyboard should feel like a polished, native app, not a utility.

**Audio feedback:**
- Key click sounds on press (subtle, not annoying)
- Different tones for special keys (backspace, enter, modifiers)
- Sound for prediction selection (distinct from regular key press)
- Volume control or mute toggle in settings
- Consider haptic-style audio cues for accessibility (confirmation sounds, error sounds)

**Window transparency:**
- Adjustable window opacity so the keyboard doesn't fully obscure what's behind it
- Slider in settings (e.g., 50%–100% opacity)
- Could also apply blur-behind-window (frosted glass effect) on supported platforms
- Transparency level might need to differ per theme (dark themes can go more transparent)

**Other visual polish ideas:**
- Smoother animations and transitions
- Subtle glow or ripple on key press
- Rounded, softer key shapes
- Better typography for prediction tiles
- Micro-interactions (prediction bar slide-in, settings panel transitions)

---

## Future Ideas (Unsorted)

Drop new ideas here. One line is fine — expand later if it gains traction.

- Emoji panel with search and recently-used
- Clipboard history panel (last N copied items, one-tap paste)
- Phrase shortcuts / text expansion (e.g., "addr" expands to full address)
- Customizable keyboard layouts (Dvorak, Colemak, regional)
- Floating mini-mode (just the prediction bar, no keyboard)
- Theming engine (user-created themes, import/export)
- Integration with system accessibility APIs (AT-SPI on Linux, UI Automation on Windows)
- Touch gesture support (swipe to delete word, swipe down to dismiss)
- Multi-language switching with per-language prediction models
- Usage analytics dashboard (words per minute, most used words, prediction hit rate)
