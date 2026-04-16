# Changelog

All notable changes to Alpha-OSK are documented in this file.

## [Unreleased]

### Security
- **Path traversal in `PackManager.import_pack` (CRITICAL)** — a source folder whose pathlib `.name` resolves to `..` would have made `dest_dir` the parent of the user packs directory, and the existing `shutil.rmtree(dest_dir)` call would then have wiped the app config root (learned models, settings). Now the pack id is sanitised with a strict `[a-z0-9_-]{1,64}` pattern and the resolved destination is verified to sit under `user_packs_dir`; symlinks in the source tree are skipped rather than dereferenced.
- **Background QTimers no longer fire during shutdown (HIGH)** — `_password_timer` and `_foreground_timer` are now stopped from `KeyboardBridge.shutdown()` via `QApplication.aboutToQuit`, so a final `timeout` cannot run against half-collected bridge state.
- **JSON model load capped (HIGH)** — `NgramPredictor.load` / `PPMPredictor.load` refuse to open files larger than 50 MB, and the n-gram loader also rejects files with more than 500k unigrams, 500k bigram prefixes, or 100k capitalisation entries. A hostile or corrupted model file in `%APPDATA%/alpha-osk/models/` can no longer OOM the app at startup.
- **Password-field detection race closed (HIGH)** — `pressKey` / `pressSpecialKey` now call `is_password_field()` synchronously (rate-limited to ~50 ms) before touching prediction state, instead of relying on the 200 ms background timer.
- **`editPrediction` input sanitised (MEDIUM)** — `_sanitize_edit` strips control characters, caps length to 64, and rejects empty-after-strip so malformed QML input can't persist junk into the capitalisation table.

### Changed
- **Encapsulation** — `keyboard_bridge.processSwipe` now calls `HybridPredictor.get_unigram_freqs()` / `get_capitalized()` instead of reaching through `_predictor._ngram`.
- **`NgramPredictor._user_total` is tracked incrementally** — `learn` / `learn_word` / `_apply_decay` / `clear_user_data` / `load` all keep the running total in sync, removing an O(N) `sum()` from every keystroke's `predict()` call.

### Added
- **Swipe / glide typing** — drag the mouse across letters to type a whole word in one gesture (Gboard-style). Uses simplified SHARK² shape matching against the dictionary, with a frequency prior. Off by default; toggle in *Settings → Suggestions → Swipe Typing*. Design doc: `docs/SWIPE_TYPING.md`.
- **Deep-dive algorithm docs** — `docs/FUZZY_RECOGNITION.md` (spatial model + accessibility profiles), `docs/PPM.md` (variable-order character model + PPMD escape), `docs/HYBRID_MERGING.md` (merge weights + validation + capitalization).

### Changed
- **Personal vocabulary now outranks dictionary words in predictions** — the n-gram unigram scoring now blends a separate base-dictionary table with the user's personal typing counts in probability space (`P = α·P_user + (1−α)·P_base`, α = 0.7 by default). Previously, a word typed 10 times scored ~10 while a common dictionary word scored ~5,000; now a few uses is enough for a personal word to rise to the top for its prefix. Tunable via `NgramPredictor.personal_weight`. See `docs/HYBRID_MERGING.md` → "Personal vs. Base Vocabulary".

### Fixed
- **Predictions now honour capitalization** — picking a prediction like "iPhone" after typing "iph" no longer outputs "iphone". Suffix-only typing now requires a case-sensitive prefix match; mismatched casing falls back to select-and-replace.
- **Caps Lock no longer also turns Shift on** — caps and shift are independent toggles. Letter keys still display uppercase under either, but the Shift key is no longer forcibly highlighted while caps is active.
- **Held key auto-releases on drag-off** — moving the cursor off a key while held now stops the key-repeat timer immediately, instead of continuing to fire until the mouse button is released.
- **Base dictionary no longer pollutes personal vocabulary** — `load_base_dictionary` previously routed through `learn()`, which added every dictionary word to `user_vocab`, making recency decay eat real personal typing alongside base words. Now routes through `_learn_base()` which updates only the base table.

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
