# CLAUDE.md — Alpha-OSK AI Onboarding

## About the Owner

Owen is a wheelchair user with muscular dystrophy. Typing is hard — be proactive, make decisions, don't ask for confirmation on small things. Offer A/B/C choices so he can type one letter instead of explaining. This is an accessibility tool he actually needs.

## What This Is

Alpha-OSK is an AI-powered on-screen keyboard for Windows and Linux. Users click keys in the UI to type into other applications. It uses a hybrid prediction engine (n-gram + PPM + fuzzy recognition) — no LLM/GPU required.

## How to Run

```bash
python run.py          # Creates venv, installs deps, launches keyboard
python -m pytest       # Run tests (269+ tests)
```

## Architecture Overview

```
User clicks key (QML)
  → KeyButton.qml sends signal
  → Main.qml calls keyboard.pressKey() / keyboard.pressSpecialKey()
  → keyboard_bridge.py (Python↔QML bridge)
    → platform/*.py synthesizes keystroke (xdotool on Linux, SendInput on Windows)
    → prediction engine updates suggestions
  → predictions emitted back to QML via Signal
```

## Key Directories

| Path | What |
|------|------|
| `src/keyboard_bridge.py` | Central bridge: key handling, modifiers, context tracking, predictions |
| `src/keyboard_app.py` | App launcher: QML engine, window flags, auto-save on exit |
| `src/platform/` | OS abstraction — `linux.py` (xdotool/ydotool), `windows.py` (SendInput), `password_detect.py` |
| `src/platform/__init__.py` | Platform detection, `get_config_dir()`, `get_model_dir()` |
| `src/prediction/` | Prediction engines (see below) |
| `qml/Main.qml` | Root UI — title bar, keyboard rows, prediction bar, resize handles |
| `qml/components/` | Reusable QML components (KeyButton, settings panels, etc.) |
| `data/` | Static data: dictionaries, training corpus, keyboard layouts, vocab packs |
| `build/` | Packaging pipelines — `build/windows/` (PyInstaller + NSIS + EV signing) and `build/linux/` (PyInstaller + optional AppImage). `build/launcher.py` is the shared frozen-mode entry point. |
| `tests/` | pytest suite |

## Prediction Engine

All in `src/prediction/`. Orchestrated by `hybrid_predictor.py`:

| File | Role |
|------|------|
| `ngram_predictor.py` | Word-frequency model: unigrams, bigrams, trigrams. Learns from typing. |
| `ppm_predictor.py` | Character-level PPM (Dasher algorithm). Predicts next characters. |
| `fuzzy_recognizer.py` | Spatial error correction. Considers nearby keys as candidates. Single tuned default (no profiles). |
| `hybrid_predictor.py` | Merges all predictors. Manages model save/load. Emits Qt signals. |
| `vocabulary_pack.py` | Domain vocab packs (medical, programming, etc.) + custom pack import |
| `transformer_predictor.py` | Optional LLM re-ranking (disabled by default) |

Deep-dive design docs for each algorithm: `docs/FUZZY_RECOGNITION.md` (spatial model + tunable constants), `docs/PPM.md` (variable-order character model + PPMD escape), `docs/HYBRID_MERGING.md` (merge weights + validation + capitalization), `docs/SWIPE_TYPING.md` (shape-matching swipe decoder).

## Auto-Capitalization & Proper Nouns

Capitalization uses a **three-tier context-aware system** (same model as Android/Gboard):

### Tier 1 — Always capitalize
Words that are always capitalized regardless of position. Hardcoded in `ngram_predictor._always_capitalize`:
- `"I"`, `"I'm"`, `"I'll"`, `"I'd"`, `"I've"`

### Tier 2 — Sentence-start only (ambiguous names)
Words that are both common English AND proper names (e.g., "will", "jack", "may", "mark"). Listed in `ngram_predictor._ambiguous_names`. These are **only capitalized** after sentence-ending punctuation (`.!?`) or at the start of input. Mid-sentence, they stay lowercase — "the jack was loose" stays lowercase, but "Jack went home." capitalizes.

### Tier 3 — Unambiguous proper nouns
Everything else in `data/proper_nouns.txt` (~8,000 entries) and user-taught capitalizations. These are always capitalized: "Monday", "Paris", "iPhone", "Owen".

### How it works
- **Built-in**: `data/proper_nouns.txt` loaded into `ngram_predictor.capitalization` on startup.
- **Learned**: When a user types a word with non-trivial capitalization (e.g., "iPhone", "Owen") and completes it with space, the preferred form is saved via `learn_capitalization()`.
- **User edits**: Right-click a prediction → Edit to correct capitalization. This calls `editPrediction()` which inserts the corrected word and saves the capitalization permanently.
- **Applied at output**: `hybrid_predictor._merge_predictions()` calls `ngram.get_capitalized(word, sentence_start)` on each result before returning to QML. The `sentence_start` flag is determined by checking if the context ends with `.!?` or is empty.
- **Persisted**: The `capitalization` dict is saved in `ngram_model.json`. User overrides merge with built-in proper nouns on load (user wins).

### Adding to always-capitalize or ambiguous lists
- Always-capitalize: edit `_always_capitalize` dict in `ngram_predictor.py`
- Ambiguous names: edit `_ambiguous_names` set in `ngram_predictor.py`

## Where User Data Lives

- **Settings** (layout, theme, toggles): Managed by Qt `Settings` in QML. Auto-saved on change. Stored in OS registry/config automatically by Qt.
- **Prediction model** (learned words/phrases): Saved to disk explicitly or via auto-save on exit.
  - Windows: `%APPDATA%/alpha-osk/models/`
  - Linux: `~/.config/alpha-osk/models/`
  - Files: `ngram_model.json`, `ppm_model.json`
  - **Load-time caps**: both loaders reject files over 50 MB. The n-gram loader also rejects files with more than 500 000 unigrams, 500 000 bigram prefixes, or 100 000 capitalisation entries — anything beyond these is assumed to be corrupt or hostile and is silently skipped (the in-memory base dictionary is kept).
- **Custom vocabulary packs**: Imported by user, stored separately from built-in packs.
  - Built-in: `data/packs/` (in repo — medical, programming, academic, gaming, business)
  - User-imported: `%APPDATA%/alpha-osk/packs/` (Windows) or `~/.config/alpha-osk/packs/` (Linux)
  - Pack format: folder with `dictionary.txt` (required), optional `bigrams.txt`, `trigrams.txt`, `pack.json`
  - **Import hardening**: the source folder's name is sanitised to `[a-z0-9_-]{1,64}`; anything else (including `..`) is rejected. The resolved destination is verified to sit strictly under `user_packs_dir` before any `rmtree`/`copytree` runs, and symlinks inside the source tree are skipped rather than dereferenced. Don't loosen this without re-reading `PackManager.import_pack` and the regression tests in `tests/test_vocabulary_pack.py::TestImportPackSecurity`.

## QML ↔ Python Bridge Pattern

QML calls Python via `@Slot` methods on `KeyboardBridge`. Python emits `Signal`s back to QML. Example flow:

1. QML: `keyboard.pressKey("a")` → calls `KeyboardBridge.pressKey()`
2. Python: synthesizes keystroke, updates context, runs prediction
3. Python: `self.predictionsChanged.emit(predictions)` → Signal
4. QML: binds to `keyboard.predictions` property, updates UI

## Caps Lock vs. Shift

Caps Lock and Shift are **independent toggles**. Toggling caps no longer also flips shift. Both are surfaced separately to QML (`capsLockActive`, `shiftActive`).

- **Uppercase output** in `pressKey`: `key.upper()` if `_shift_active OR _caps_lock_active`.
- **Upper layer**: `_update_layer()` switches to `"upper"` if `_shift_active OR _caps_lock_active`. Same for the displayed glyph in `Main.qml`.
- **Auto-release**: Shift auto-releases after a single keypress; caps stays on until explicitly toggled. Caps is unaffected by the auto-release path.
- **Visual highlight**: only the toggled key is highlighted — toggling caps does NOT also highlight the Shift key (it used to, that was a bug).

The shifted *glyph* on a key (e.g. `!` on the `1` key) follows shift only — caps lock uppercases letters but does not pick the shifted variant of symbol/number keys, matching standard keyboard behavior.

### Caps Lock and the prediction bar

When Caps Lock is on, the prediction pills also render uppercase. The pills must match what the user is typing *and* what the pill will insert when clicked — showing "hello" while the user has typed "HELL" and then inserting lowercase next to the uppercase prefix was the pre-fix bug. Implementation: `KeyboardBridge._display_cased()` uppercases the engine's output when `_caps_lock_active`, and every emit site (`_on_predictions_ready`, `_on_predictions_refined`, next-word-after-selection, `editPrediction`, swipe) routes through it. `toggleCapsLock` re-queries the engine so currently-visible pills flip case immediately — we can't just `.upper()` / `.lower()` the stored list in place because once "iPhone" becomes "IPHONE" the original casing is lost. Shift is deliberately not mirrored — it's one-shot auto-releasing and sentence-start capitalisation is already handled upstream by `NgramPredictor.get_capitalized`.

## Editing a Prediction (OSK-friendly edit popup)

Right-click a prediction pill → Edit opens a small popup with the word pre-filled and selected, so users can correct it (e.g. `iphone` → `iPhone`) and save via `editPrediction(old, new)`. The popup is deliberately non-obvious in one way: OSK keystrokes must land in *our* TextField, but OSK key presses normally synthesize via `xdotool` / `SendInput` to the OS-focused app behind Alpha-OSK.

- **No modal overlay**: `predEditPopup.modal = false`. A modal popup would install an overlay that swallows MouseArea clicks on the keyboard below, so no OSK key would fire.
- **No press-outside close**: `closePolicy: Popup.CloseOnEscape` only — every OSK key click is a "press outside" and would otherwise slam the popup shut on the first keystroke. Escape and the ✕ cancel button are the visible ways out.
- **Edit-mode intercept**: on open/close the popup calls `keyboard.setEditMode(true/false)`. While active, `pressKey` and `pressSpecialKey` short-circuit the synthesizer and emit `editKeyTyped(char)` / `editSpecialPressed(name)` instead. A `Connections { target: keyboard }` block inside the popup wires those to TextField ops — insert at cursor, backspace, delete, left/right/home/end cursor motion, space, return-to-accept, escape-to-cancel.
- **Modifier handling in edit mode**: shift/caps still apply to letter case; ctrl/alt/win are ignored inside the field so stray chords can't leak to the app behind us. Shift auto-releases after one keypress the same way it does outside edit mode.

If you add a new input source (e.g. a voice-dictation slot, another popup with its own TextField), the pattern is: set edit mode on open, listen to `editKeyTyped` / `editSpecialPressed`, clear edit mode on close. Don't try to route through Qt focus — `WS_EX_NOACTIVATE` / `WindowDoesNotAcceptFocus` prevent our window from holding OS focus, so physical keyboard input and synthesized input both go to whatever app was focused before we opened.

## Swipe / Glide Typing

Drag the mouse across letters to type a whole word in one gesture, like Gboard. Off by default; toggle in *Settings → Suggestions → Swipe Typing*. Design doc: `docs/SWIPE_TYPING.md`.

| File | Role |
|------|------|
| `src/prediction/swipe_recognizer.py` | `SwipeRecognizer` — simplified SHARK² shape matching + frequency prior |
| `src/keyboard_bridge.py` | `setSwipeEnabled`, `setSwipeLayout`, `processSwipe` slots |
| `qml/components/SwipeOverlay.qml` | Mouse interceptor + path canvas, hidden when off |
| `qml/Main.qml` | `charKeyRegistry`, `pushSwipeLayout()` (overlay-local key centres) |

When the toggle is on, a transparent overlay covers the keyboard rows and intercepts all gestures. Press → drag past 60 px → swipe; press → release on a key → tap fall-through (the overlay hit-tests the registry and forwards to the underlying `KeyButton.keyPressed`). The recogniser pre-filters by start/end key, then scores remaining candidates with `log(freq+1) − 8 · mean_normalized_distance`. Top result is typed via `send_text` + space; alternates appear in the prediction bar so the user can repick.

## Sticky Modifiers (Ctrl, Alt, Win)

Modifier keys are **sticky** — tap once to activate, tap again to deactivate. While active, the modifier is held at the OS level via `hold_modifier()` / `release_modifier()` on the platform synthesizer. This means:

- **Modifier+click works**: e.g., Ctrl+click to open hyperlinks in terminals/browsers.
- **Modifier+key combos work**: e.g., tap Ctrl, then tap C → sends Ctrl+C.
- **Auto-release**: After any key press (character or special), active modifiers are released at the OS level and deactivated.

### Implementation
- `keyboard_bridge.py`: `toggleCtrl()` / `toggleAlt()` / `toggleWin()` call `_synth.hold_modifier()` on activate and `_synth.release_modifier()` on deactivate. All auto-release paths in `pressKey()` and `pressSpecialKey()` also call `release_modifier()`.
- `platform/base.py`: `hold_modifier()` and `release_modifier()` — default no-op.
- `platform/windows.py`: Sends `VK_CONTROL` / `VK_MENU` / `VK_LWIN` key-down or key-up via `SendInput`.
- `platform/linux.py`: Uses `xdotool keydown/keyup` or `ydotool key --key-down/--key-up`.

## Adding a New Setting

1. Add `property bool savedFoo: defaultValue` to `Settings {}` in `Main.qml`
2. Add `property bool foo: appSettings.savedFoo` to root in `Main.qml`
3. Add `property bool foo: defaultValue` to `UnifiedSettingsPanel.qml`
4. Add `SettingsToggle` in the appropriate section of `UnifiedSettingsPanel.qml`
5. Pass property through: `foo: root.foo` in the `Comp.UnifiedSettingsPanel {}` block
6. Handle in `onSettingChanged`: update root, save to appSettings, call bridge if needed
7. If Python needs it: add `@Slot(bool) def setFoo()` to `keyboard_bridge.py`
8. Load on startup in `Component.onCompleted` if it needs to be sent to the bridge

## Adding a New QML Component

1. Create `qml/components/MyComponent.qml`
2. It's auto-discovered — the `components/` directory is imported as `"components" as Comp` in Main.qml
3. Use as `Comp.MyComponent {}` in Main.qml

## Fuzzy Recognition Defaults

Hardcoded in `src/prediction/fuzzy_recognizer.py` as `DEFAULT_*` / `_*_PROB` constants. Used to be six "accessibility profiles" (Precise / Normal / Mild Tremor / etc.) but they were confusing — the profile UI is gone and there's now one generous, Gboard-leaning default. Knobs:
- **`spatial_uncertainty` (1.4)**: how far off-center a press still counts as the intended key, in key-widths.
- **`confidence_threshold` (0.65)**: minimum score for `should_autocorrect` to fire.
- **`prediction_weight` (0.6)**: weight applied to fuzzy candidates in the hybrid merge.
- **`min_prob` (0.001)**: beam-search pruning threshold inside candidate generation — low enough that a single substitution survives across a 5+ char word.
- **`_TRANSPOSITION_PROB` (0.30) / `_DELETION_PROB` (0.20) / `_INSERTION_PROB` (0.15)**: per-edit penalties for the edit-distance candidate path (alongside the spatial beam search), so "teh" → "the", "thee" → "the", "th" → "the" all surface.
- **`_APOSTROPHE_INSERTION_PROB` (0.50)**: insertion of `'` specifically, bumped well above the generic letter-insertion penalty because missing apostrophes ("im" → "I'm", "dont" → "don't") are by far the dominant insertion error in real typing on a low-precision OSK.

To tune, override the class attributes on `FuzzyRecognizer`. There's no UI for it.

## Testing

```bash
python -m pytest                    # All tests
python -m pytest tests/test_keyboard_bridge.py  # Bridge tests
python -m pytest -k "fuzzy"         # Fuzzy recognizer tests
```

Linting: `ruff check src/`, type checking: `mypy src/`

## Word Suppression

Users can right-click prediction pills to:
- **Remove from vocabulary** — adds to `ngram_predictor.blacklist` (word never appears again)
- **Bad suggestion** — increments `ngram_predictor.dispreference` (word is downweighted by `1 / (1 + count * 0.5)`)

Both are persisted in `ngram_model.json` and applied in `hybrid_predictor._merge_predictions()`.

### Restoring Suppressed Words
In the Model Visualization dashboard (Settings → Tools → Language Model Visualization → Dashboard tab → Suppressed Words), blacklisted and dispreferred words display as clickable tags. Click a tag to restore it.

Bridge slots: `keyboard.unblacklistWord(word)`, `keyboard.undisprefer(word)`.

### Auto-Rehabilitation
If a user manually types a blacklisted word 3 times (completing it with space), the word is automatically restored to predictions. Tracked via `ngram_predictor._blacklist_type_count`, persisted in `ngram_model.json`.

## Model Visualization

Accessed via Settings → Tools → Language Model Visualization. Three tabs:
- **Word Cloud** — circle-packed bubble chart of top words, sized by frequency
- **Word Flow** — network graph of bigram word→word connections
- **Dashboard** — stats cards, top words bar chart, interactive suppressed words, top word pairs

Data provided by `keyboard_bridge.getVisualizationData()` → `ModelVisualization.qml`.

## Privacy Mode & Password Detection

Protects sensitive input (passwords, PINs) from leaking into the prediction model.

### How it works
- **Auto-detection** (Windows): Two complementary paths call `is_password_field()` from `src/platform/password_detect.py`:
  1. A background `QTimer` polls every 200ms (`_check_password_field`). Catches focus changes that happen between keystrokes.
  2. **Every keystroke** (`pressKey`/`pressSpecialKey`) also calls `_check_password_field_sync()`, rate-limited to ~50ms via `_last_sync_password_check`. Closes the race window where the first characters after focus lands on a password field would otherwise reach the prediction cache before the timer fires.
- Detection uses Windows UI Automation COM (`IUIAutomation::GetFocusedElement` → `UIA_IsPasswordPropertyId`) in native apps and browsers. Falls back to Win32 `EM_GETPASSWORDCHAR` if UIA fails.
- **Manual toggle**: Play/pause icon in the title bar (Canvas-drawn). Overrides auto-detection.
- **When active**: Keystrokes still reach the OS, but `_current_word`, predictions, and learning are all suppressed. The prediction bar shows "Learning paused".

### Key files
- `src/platform/password_detect.py` — platform-specific detection (UIA COM via ctypes)
- `src/keyboard_bridge.py` — `_privacy_mode` flag, `_check_password_field()` timer, `_check_password_field_sync()` per-keystroke, `setPrivacyMode()` slot

### Linux
Auto-detection uses AT-SPI 2 via `gi.repository.Atspi`. A daemon thread owns a GLib event loop and listens for `object:state-changed:focused`; whenever focus lands on an accessible whose state set contains `STATE_PASSWORD_TEXT`, the shared `_is_password` flag flips on. Works for GTK (`GtkEntry` with `visibility=false`), Qt (`QLineEdit` in Password echo mode), and browsers that expose accessibility metadata. Requires `gir1.2-atspi-2.0` + a working at-spi bus on the host. If `gi` fails to import or `Atspi.init()` fails, falls back silently to the null detector — users can still toggle privacy mode manually.

## Themes

Defined in `themeData` in `Main.qml`. Each theme has: `name`, `background`, `keyColor`, `keyPressed`, `textColor`, `accent`, `border`, `animation`.

**9 themes**: Dark, Light, Ocean, Forest, Amethyst, Vaporwave, Blackboard, Typewriter, Spaceship.

Theme colors flow to all components: main keyboard keys, prediction pills, nav panel, numpad, title bar icons, and active key states (NumLock, Shift, etc.). `KeyButton.qml` auto-computes text contrast on active/pressed states using luminance.

**Animations** (optional per theme): Canvas overlay at 15% opacity. Vaporwave has gradient shift, Spaceship has twinkling stars.

Theme picker in settings shows labeled color swatches with mini key previews.

## Vocabulary

- **Base**: Google 10K wordlist (`data/google-10000-english-usa-no-swears.txt`) + 10K supplement (`data/google-20000-supplement.txt`, filtered for explicit content). ~20K total regular words.
- **Packs**: Medical, Programming, Academic, Gaming, Business, NSFW. Toggled in Settings → Vocabulary Packs. NSFW is off by default.
- **Numpad**: Toggles between numbers and navigation keys (Home/End/PgUp/PgDn/arrows/Ins/Del) via NumLock. Key 5 is blank in nav mode.

## Analytics & Quality Scoring

`src/analytics.py` tracks session and all-time stats. All-time stats persist to `<config_dir>/analytics.json`.

Every session counter has an `_alltime_*` mirror that's loaded on launch, merged with the session at exit, and surfaced in `get_session_stats()` as both `<metric>` (session) and `alltime<Metric>` (lifetime). The dashboard's Lifetime / Session toggle (`AnalyticsDashboard.qml`) drives every tile off these paired keys. Persisted fields include: keystrokes, words, predictions (hits), keystrokes_saved, sessions, minutes, **backspaces, prediction_offers, prediction_rank_sum/count, word_freq, key_freq**. Word frequencies are capped at 5000 unique entries on save (top-N by count) so `analytics.json` stays bounded over years of typing.

**Prediction Quality Score** (0-100) is a weighted combination:
- Keystroke savings rate (40%) — how much effort predictions save
- Prediction hit rate (25%) — how often predictions are used
- Rank accuracy (20%) — how often users pick the #1 suggestion
- Low correction rate (15%) — inverse of backspace rate

`_compute_quality_score` takes kwargs so the same logic computes either the session score or a lifetime score from the persisted aggregates. The dashboard's "Prediction Quality" bar shows the lifetime score because session quality is noisy until the user has typed for a while.

## Prediction & Autocorrect — Architecture Notes

Commercial keyboards (Gboard/LatinIME, Presage) treat prediction and spell-check as **one unified system**, not two. During a single dictionary trie traversal, they generate both completions and corrections scored together. The literal typed word competes against alternatives — autocorrect only fires if a correction scores 1.5–2x higher.

### What Alpha-OSK does now
- **Hybrid prediction**: n-gram + PPM + fuzzy (same layered approach as Presage)
- **Spatial error correction**: `fuzzy_recognizer.py` considers nearby keys (same concept as LatinIME's key-distance weighting)
- **Three-tier capitalization**: always-capitalize ("I"), sentence-start-only (ambiguous names), always (proper nouns)
- **Linear-interpolation n-gram scoring**: `NgramPredictor.predict()` ranks candidates with `score(w) = λ₃·P(w|w₋₂,w₋₁) + λ₂·P(w|w₋₁) + λ₁·P_uni(w)` (λ = 0.5 / 0.3 / 0.2). Trigram / bigram / unigram all live in probability space, so bigram evidence can actually beat the global unigram favourite after a trained context (e.g. "I want " → "to", not "the"). When there's no preceding word, the formula collapses to `P_uni` at full weight so partial-prefix completion isn't flattened. (Pre-fix bug: bigram added `freq·2`, unigram added `p·100_000` — unigram dominated by 1000×.)
- **Fragment filter on learning**: `NgramPredictor.learn()` rejects obvious keyboard-slip fragments (`_is_plausible_word`: length ≤ 2 must be in a short whitelist; length ≥ 3 needs both a vowel and a non-`aeiou` letter — `y` counts as both so "eye" and "cry" pass but "aaaa" and "xqz" don't). Surviving unknown words go through a repetition gate: counted in `_candidate_counts` until 3 sightings, then promoted into `user_vocab`. Known base-dict words and `learn_word()` bypass the gate. Candidate counts decay with the rest of user vocab and persist across save/load.

### Known gaps (future work, priority order)
1. **SymSpell for fuzzy matching** — Replace Levenshtein edit-distance in `fuzzy_recognizer.py` with SymSpell's precomputed-deletion approach. ~1000x faster, O(1) lookup, ~30MB RAM. (Garbe, 2012)
2. **Autocorrect confidence threshold** — Only auto-replace if the correction scores 1.5–2x higher than the literal typed word. Prevents over-correction. (LatinIME uses ~1.8x)
3. **Unified scoring** — Make the literal typed word compete against corrections in the same ranked list with an explicit score, so the system knows when NOT to correct.
4. **Spatial edit costs in ranking** — Key-distance weights from fuzzy_recognizer should feed into final prediction ranking, not just candidate generation.
5. **Katz / Stupid Backoff for sparse contexts** — The linear-interpolation formula above gives λ₃·P_tri even when the trigram table has never seen this 2-word prefix (P_tri = 0). Katz backoff discounts seen events and redistributes the mass to the bigram/unigram fallback. Better behaviour on rare contexts. Larger lift (~100 lines).
6. **Larger curated bigram/trigram corpus** — `data/common_bigrams.txt` and `common_trigrams.txt` are modest. Seeding from a big public corpus (e.g. COCA top 100k bigrams, Google n-gram exports) would help cold-start users before their personal typing builds up. Easy win, doesn't require algorithm changes.

### Reference implementations
- **LatinIME (AOSP)**: trie-based dictionary with weighted edit distance, n-gram LM scoring. Open source.
- **Presage**: pluggable predictors (smoothed n-gram + Katz backoff, recency, trie completion). Linear interpolation merge. Similar to our hybrid approach.
- **Dasher**: PPM-C character-level prediction. Our PPM predictor is based on this.
- **SymSpell**: precompute all deletion variants within edit distance N at index time. Query = generate deletions of input + hash lookup. (github.com/wolfgarbe/SymSpell)
- **Hunspell**: affix-based dictionary + phonetic matching. Slower but handles morphology.

## Modular Layouts

Design doc at `docs/MODULAR_LAYOUTS.md`. Inspired by Octavium's (`C:\Users\Owen\dev\Octavium`) Layout/KeyDef data model. Four levels of modularity: (1) Built-in JSON layout packs (video editing, gaming, streaming). (2) User-created layouts via editor. (3) Panel composition — snap independent panels (QWERTY, numpad, macros) into a grid. (4) App-aware auto-switching based on foreground window.

Action types: `char`, `special`, `hotkey`, `text`, `macro`, `launch`, `layout`, `midi`. Profiles bundle layout + theme + window position + auto-switch rules.

## Auto-Update

Implemented in `src/updater.py`. Flow walkthrough, threat model + defences table, and the per-defence rationale all live in `docs/AUTO_UPDATE.md`. Release checklist is in `docs/WINDOWS.md`.

> ⚠️ **Releases live in a separate public repo** — `okstudio1/alpha-osk-releases`. The source repo is private (returns 404 on `/releases/latest` to unauthenticated update clients). Always pass `--repo okstudio1/alpha-osk-releases` to `gh release create`.

Version source of truth is `src/__version__.py`. The release-asset filename **must** match `Alpha-OSK-Setup-{version}.exe` exactly — the updater rejects anything else. User-facing toggle: *Settings → Updates → "Check for updates on startup"* (persisted as `appSettings.savedAutoCheckUpdates`).

## Accessibility Ecosystem

Design doc at `docs/ECOSYSTEM.md`. Alpha-OSK is part of a four-tool adaptive input platform:

| Tool | Repo | Output |
|------|------|--------|
| **Alpha-OSK** | `C:\Users\Owen\dev\alpha-osk` | Keystrokes (SendInput) |
| **MacroVox** | `C:\Users\Owen\dev\MacroVox` | Text (Deepgram STT → clipboard) |
| **Octavium** | `C:\Users\Owen\dev\Octavium` | MIDI (virtual piano/pads) |
| **Nimbus** | `C:\Users\Owen\dev\Nimbus-Adaptive-Controller` | Joystick (vJoy/ViGEm) |

All four: same developer, same EV cert, PySide6/Qt (except MacroVox: Tauri), mouse-driven, accessibility-first. Integration phases: coexistence → launch/trigger → profile auto-switch → shared input layer → unified UI.

See also: `docs/MACROVOX_INTEGRATION.md` (voice dictation), `docs/MODULAR_LAYOUTS.md` (custom layouts inspired by Octavium/Nimbus).

## Federated Learning

Design doc at `docs/FEDERATED_LEARNING.md`. Not yet implemented — Phase 1 (local delta computation) is the next step.

## Building & Signing a Release (Windows)

Full step-by-step release checklist, signing details, troubleshooting table, and bundle-size notes are in `docs/WINDOWS.md` (sections "Building a Standalone Executable", "Code Signing", "Release Checklist"). Asset/icon regeneration in `docs/BRANDING.md`. Quick mental model:

1. Bump `src/__version__.py` (single source of truth — `build/windows/build.py` reads from it).
2. Update `CHANGELOG.md`, commit.
3. Build + sign from a **non-elevated shell** with the eToken plugged in: `python build/windows/build.py`.
4. Test the installer in `release/`, including UIAccess against an elevated shell.
5. `git tag vX.Y.Z && git push origin main && git push origin vX.Y.Z`.
6. **Public repo for binaries**: `gh release create vX.Y.Z release/Alpha-OSK-Setup-X.Y.Z.exe --repo okstudio1/alpha-osk-releases ...`. The `--repo` flag is mandatory — source repo is private and the auto-updater can't see private releases.

The eToken-non-elevated requirement is the single most common build trap: SafeNet exposes the cert to the user session only, so elevated shells get "Cannot find certificate."

## Linux build

Linux has its own pipeline in `build/linux/` that mirrors the Windows
one but skips the NSIS/signing legs (AppImage is unsigned by design,
and EV signing is Windows-specific).

```bash
venv/bin/pip install pyinstaller          # one-time

python build/linux/build.py               # PyInstaller bundle → dist/alpha-osk/
python build/linux/build.py --appimage --fetch-appimagetool
                                          # + AppImage → release/Alpha-OSK-<ver>-x86_64.AppImage
```

Key files:
- `build/linux/alpha-osk.spec` — PyInstaller spec (same exclusions as
  the Windows spec: torch, transformers, QtWebEngine, etc.).
- `build/linux/build.py` — driver; optionally downloads `appimagetool`
  to `~/.cache/alpha-osk-build/` on first `--appimage` run.
- `build/linux/AppRun` — AppImage entry script that points `QT_PLUGIN_PATH`
  / `QML2_IMPORT_PATH` at the bundled Qt and defaults
  `QT_QPA_PLATFORM=xcb`.
- `build/linux/alpha-osk.desktop` — `Categories=Utility;Accessibility;`
  so the app surfaces in accessibility menus once the AppImage is
  integrated.

`xdotool` / `ydotool` are **not** bundled — they're OS-level tools that
must be installed on the host. The bundle will start without them but
key synthesis will silently no-op.

See `docs/LINUX.md` for deeper coverage (troubleshooting, AppImage
internals, spec customization).

## Git Conventions

Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`

## Things to Watch Out For

- `Main.qml` is large (~1300 lines). The keyboard rows are data-driven from `keyboard.getLayoutRows()`.
- `keyboard_bridge.py` is the biggest Python file (~1000 lines). It handles everything: keys, modifiers, context, predictions, settings, privacy mode.
- Window flags are critical — the keyboard must never steal focus from the user's app. See `_apply_window_flags()` in `keyboard_app.py`.
- On Windows, `WS_EX_NOACTIVATE` is set via Win32 API (not just Qt flags).
- Key spacing and sizing are calculated dynamically from window width — see `keyW`, `keyH`, `keySpacing`, `layoutFixedPixels` properties in Main.qml.
- The title bar has play/pause (privacy), ⚙ (settings), minimize, and close. Help and visualization are in Settings → Tools.
- Predictions clear when the user switches apps — monitored via a 250 ms poll in `keyboard_bridge.py::_get_foreground_window_id`. Windows uses `GetForegroundWindow()` via ctypes; X11 shells out to `xdotool getactivewindow`. Wayland is a no-op (compositors don't expose focused window to unprivileged clients). QML `onActiveChanged` doesn't fire reliably with `WS_EX_NOACTIVATE` on Windows, so Python handles it on both platforms. Full context reset on app switch (`_current_word`, `_context_buffer`, `_sentence_buffer`).
- Prediction selection uses **suffix-only typing** — if the user typed "hel" and picks "hello", only "lo " is sent. No Backspace (empties Slack compose), no Shift+Left (doesn't work in terminals). Falls back to `replace_text()` only when the prediction doesn't match the typed prefix (e.g. casing diff "iph"→"iPhone"). `replace_text()` is implemented on both platforms: Windows sends the whole Shift+Left-then-type sequence in one `SendInput`; Linux chains N `shift+Left` chords into a single `xdotool key` invocation then a separate `xdotool type`, or frames `ydotool key --key-down shift` / `--key-up shift` around N Left presses on Wayland.
- **Shutdown ordering matters** — `keyboard_app.py` wires `aboutToQuit` to run `savePredictionModel`, `saveAnalytics`, then `bridge.shutdown()` in that order. `shutdown()` stops `_password_timer` and `_foreground_timer` so a final `timeout` can't run against a half-torn-down predictor, **and** releases any sticky Ctrl/Alt/Win that was still "active" so the OS doesn't see a phantom-held modifier after quit. Any new long-lived QTimer in `KeyboardBridge` should also be stopped there.
- **Linux key synthesis is synchronous** — `src/platform/linux.py` wraps every `xdotool`/`ydotool` invocation in `subprocess.run` (not `Popen`). Ordering between `keydown` / chord / `keyup` must be preserved; non-blocking subprocesses race and leave modifiers stuck. If you add a new send path, use the module-level `_run()` helper. Windows (`SendInput` via ctypes) has no analogous concern — events are atomic.
- **Modifier reset only runs at startup** — `KeyboardBridge.__init__` calls `synth.reset_modifier_state()` to clear any Ctrl/Alt/Shift/Super pinned by a crashed prior instance. Do **not** call this on a timer or in response to user events: it would release a modifier the user is *physically* holding (Alt-codes, Ctrl-scroll, etc.). Any future reconciliation during a live session must query the server first (`XQueryKeymap` via ctypes or python-xlib) to distinguish "we held this" from "the user is holding this."
- **External callers reach `NgramPredictor` via `HybridPredictor` forwarders** — don't access `keyboard._predictor._ngram` from `keyboard_bridge.py` or new code. Use `get_unigram_freqs()` / `get_capitalized()` or add a new forwarder. The swipe path is the canonical example (see `processSwipe`).
- **`NgramPredictor._user_total` is an invariant** — every mutation to `user_vocab` (in `learn`, `learn_word`, `_apply_decay`, `clear_user_data`, `load`) must keep it equal to `sum(user_vocab.values())`. `predict()` reads it every keystroke; the consistency tests in `tests/test_ngram_predictor.py::TestUserTotalIncremental` will catch a missed site.
- **Window height is bound to content — do NOT persist it or assign it imperatively.** `Main.qml` declares `height: outerLayout.implicitHeight + 60`, and only `savedWindowWidth` is restored at startup. An earlier version also persisted height, which broke the binding the moment `Component.onCompleted` did `root.height = savedWindowHeight` — once the binding was dead, any width change made the keyboard either clip the bottom row or grow empty bands above/below the keys. The user has no vertical resize handle (both edges are `SizeHorCursor`), so width is the only knob; height auto-follows. If you ever need to add height persistence, you also need a re-binding strategy (`Qt.binding(...)`, or an `onHeightChanged` clamp to expected) — don't just assign and walk away.
- **`KeyButton.qml` drives press visuals off `_visualPressed`, not `mouseArea.pressed`.** With `WS_EX_NOACTIVATE`, Qt occasionally drops the release event when the user drags off the OSK onto another window. Binding visuals straight to `pressed` left the key visually latched. `_visualPressed` is set true on press, cleared on release / cancel / drag-off / a 5 s safety timer — four independent paths back to neutral. If you add a new visual that should follow press state, bind it to `keyRoot._visualPressed`.
- **Single-instance lock holder must outlive `QApplication`.** `keyboard_app.py::_SINGLETON_LOCK` is a module-level `QSharedMemory` reference. `QSharedMemory`'s segment is freed when the holding object is destroyed, so a function-local would release the lock before the app even started. If you refactor `_acquire_singleton_or_surface`, keep that module-level reference alive (or move ownership to the `QApplication` instance).
- **`KEYBDINPUT.dwExtraInfo` is `ULONG_PTR` (an integer), not a real pointer.** MSDN types it that way; we alias `ULONG_PTR = ctypes.c_size_t` in `src/platform/windows.py`. Set it to `0` for our synthesized input — *never* allocate a Python `c_ulong` and pass `ctypes.pointer()` to it. The kernel doesn't dereference the field, but the Python object would be reaped while the INPUT struct still references its address (a real UB hazard if the field is ever read by another consumer).
- **Lifetime analytics: persist BOTH session and lifetime.** Any new metric added to `analytics.py` needs (1) a session counter, (2) an `_alltime_*` mirror, (3) load + save in `_load_alltime` / `save`, and (4) a lifetime field in `get_session_stats`. The dashboard's Lifetime / Session toggle expects every visible metric to have both forms; surfacing only session keys regresses the toggle for that tile. `_compute_quality_score` accepts kwargs so the same logic computes session and lifetime scores.
- **Password detector COM lifecycle.** `password_detect._WindowsUIADetector` tracks `_owns_com` so `CoUninitialize` only fires if *we* called `CoInitializeEx` (S_OK). On S_FALSE (1) we skip the uninit — another caller already owned the apartment and tearing it down would break them. `KeyboardBridge.shutdown` calls `password_detect.shutdown()` to release the IUIAutomation interface and pair the uninit; don't add another path that calls `CoInitializeEx` without matching that pattern.
