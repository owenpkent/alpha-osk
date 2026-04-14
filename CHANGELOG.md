# Changelog

All notable changes to Alpha-OSK are documented in this file.

## [1.0.1] — 2026-04-14

### Added
- **System tray icon** — Alpha-OSK now appears in the notification area with the app logo. Right-click for Show/Hide and Quit. Double-click to toggle keyboard visibility.
- **Branded installer** — NSIS installer now shows the Alpha-OSK logo on welcome/finish sidebar, header image on all pages, and custom welcome text with feature highlights.
- **New app icon** — custom "A" logo (Midjourney-generated) embedded in the exe, shortcuts, installer, and system tray. Multi-resolution ICO (16–256px).
- **Auto-space after comma, semicolon, colon** — mid-sentence punctuation now inserts a trailing space (same as sentence-ending punctuation), without triggering auto-capitalize.
- **Build & release documentation** — comprehensive build/sign/release checklist added to CLAUDE.md with prerequisites, troubleshooting, and installer upgrade behavior.
- **Branding guide** — Midjourney prompts, asset specs, color palette, and icon generation workflow in docs/BRANDING.md.

### Fixed
- **Modifier+click now works** — Ctrl, Alt, and Win keys are held at the OS level via SendInput (Windows) / xdotool (Linux), so Ctrl+click to open hyperlinks and similar modifier+mouse combos work correctly.
- **Prediction selection no longer outputs fragments** — backspace + replacement text is now sent as a single atomic SendInput call, preventing race conditions that produced output like "ose" instead of "choose".
- **Typed fragments no longer pollute the model** — selecting a prediction no longer learns the partial word that was being replaced.
- **Key repeat disabled on character keys** — only navigational keys (backspace, arrows, etc.) repeat on hold, preventing accidental repeated characters.
- **Clear User Data actually clears everything** — now flushes unigrams, bigrams, trigrams, PPM state, and the capitalization dict. Saves to disk immediately so stale model files don't resurrect weird learned words on restart.
- **Installer removes previous versions on upgrade** — same-directory upgrades now silently run the old uninstaller before extracting new files, removing orphaned files from prior versions. User's learned vocabulary is preserved during upgrades.
- **Model visualization shows only user-typed words**, not pretrained dictionary data.
- Removed quotes from no-space-before punctuation set.

### Chores
- Added PyInstaller output and `.coverage` to `.gitignore`.
- Source logos stored in `assets/`.

## [1.0.0] — 2026-04-12

Initial public release.
