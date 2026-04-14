# Changelog

All notable changes to Alpha-OSK are documented in this file.

## [1.0.1] — 2026-04-14

### Fixed
- **Modifier+click now works** — Ctrl, Alt, and Win keys are held at the OS level via SendInput (Windows) / xdotool (Linux), so Ctrl+click to open hyperlinks and similar modifier+mouse combos work correctly.
- **Prediction selection no longer outputs fragments** — backspace + replacement text is now sent as a single atomic SendInput call, preventing race conditions that produced output like "ose" instead of "choose".
- **Typed fragments no longer pollute the model** — selecting a prediction no longer learns the partial word that was being replaced.
- **Key repeat disabled on character keys** — only navigational keys (backspace, arrows, etc.) repeat on hold, preventing accidental repeated characters.
- **Clear User Data fully resets the model** — now flushes unigrams, bigrams, trigrams, and PPM state, not just user_vocab.
- **Model visualization shows only user-typed words**, not pretrained dictionary data.
- Removed quotes from no-space-before punctuation set.

### Chores
- Added PyInstaller output and `.coverage` to `.gitignore`.

## [1.0.0] — 2026-04-12

Initial public release.
