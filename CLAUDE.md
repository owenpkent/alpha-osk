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
- **Learned**: When a user types a word with non-trivial capitalization (e.g., "iPhone", "Owen") and completes it with space, the preferred form is saved via `learn_capitalization()`. **All-uppercase typings under Caps Lock are NOT learned** — those would poison the table with shouty forms of every word the user typed under caps lock. But all-caps typed *deliberately* (right-clicking each letter, or shifting each one — Caps Lock off the whole word) IS learned: the bridge tracks `_word_typed_under_caps_lock` for the duration of each word and passes `allow_uppercase=not _word_typed_under_caps_lock` into `learn_capitalization`. So typing "HVAC" via four right-clicks then space teaches the system, but typing "HELLO" with caps lock on does not. The flag is set whenever a char is appended to `_current_word` while `_caps_lock_active` is True, and reset at every word boundary (space, punctuation, return, backspace-to-empty, app switch, prediction click, edit, swipe, privacy mode, explicit context reset). Genuine acronyms (HBO, IBM, NASA) come from `data/proper_nouns.txt` via `_load_proper_nouns`, which writes directly into `self.capitalization` and bypasses this guard.
- **Learned via prediction click**: If the user typed any uppercase letter in the prefix (right-click → shifted variant, or sticky shift) and then accepts a prediction pill, `pressPrediction` calls `learn_capitalization(word)` on the chosen pill. The gate is `_current_word != _current_word.lower()` — *any* casing in the prefix counts, not just the first letter — so first-letter caps ("Hello") and mid-word caps ("eBay", "macBook") both record. Without this, accepting a pill would throw away the casing intent and the user would have to re-right-click every time they typed the same word.
- **User edits**: Right-click a prediction → Edit to correct capitalization. This calls `editPrediction()` which inserts the corrected word and saves the capitalization permanently.
- **Applied at output**: `hybrid_predictor._merge_predictions()` calls `ngram.get_capitalized(word, sentence_start)` on each result before returning to QML. `sentence_start` is true **only** when the (rstripped) context ends with `.!?`. Empty context is *not* treated as a sentence start — that produced annoying behaviour in terminals/REPLs where the user backspaces every typed char, leaving an empty context that isn't actually a fresh sentence. The fresh-document case (open Notepad, type the first letter) is handled by `_display_cased` mirroring the typed prefix's casing instead, so shift-typed "T" still surfaces "The".
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
- **OS-level hold**: `toggleShift` calls `_synth.hold_modifier("shift")` / `release_modifier("shift")` so the OS sees Shift physically held while the toggle is active. This is what makes Shift+click and Shift+drag in the target app extend the text selection — same as the Windows on-screen keyboard. Without it, Shift only attached to synthesised keystrokes as a chord modifier and a click between toggle and the next typed character would land without Shift held.
- **Auto-release**: Shift auto-releases after a single keypress; caps stays on until explicitly toggled. Auto-release paths also call `release_modifier("shift")` so the OS-held shift drops together with the Python state. Caps is unaffected by the auto-release path.
- **Visual highlight**: only the toggled key is highlighted — toggling caps does NOT also highlight the Shift key (it used to, that was a bug).

The shifted *glyph* on a key (e.g. `!` on the `1` key) follows shift only — caps lock uppercases letters but does not pick the shifted variant of symbol/number keys, matching standard keyboard behavior.

### Caps Lock and the prediction bar

When Caps Lock is on, the prediction pills also render uppercase. The pills must match what the user is typing *and* what the pill will insert when clicked — showing "hello" while the user has typed "HELL" and then inserting lowercase next to the uppercase prefix was the pre-fix bug. Implementation: `KeyboardBridge._display_cased()` uppercases the engine's output when `_caps_lock_active`, and every emit site (`_on_predictions_ready`, `_on_predictions_refined`, next-word-after-selection, `editPrediction`, swipe) routes through it. `toggleCapsLock` re-queries the engine so currently-visible pills flip case immediately — we can't just `.upper()` / `.lower()` the stored list in place because once "iPhone" becomes "IPHONE" the original casing is lost.

`_display_cased` *also* mirrors **every** uppercase position from the typed prefix onto the displayed pill, not just the first letter. If the user typed "Hel" the pills show "Hello"/"Help"; if they right-clicked each letter to type "HEL", the pills show "HELlo"/"HELp"; if they typed "iP" (mid-word cap via right-click), the pill shows "iPhone". The gate is `any(c.isupper() for c in cw)` and the body iterates each prediction position, force-uppercasing it when the corresponding `cw[i]` is uppercase. Two reasons: (1) the displayed pill must match what the user typed so they can tell which pill matches their prefix, and (2) the suffix-only insert path uses a case-sensitive `startswith`, so "hello".startswith("HEL") is False and the click would fall through to a full replace, clobbering the user's capitals. Sentence-start and proper-noun capitalisation still flow through `NgramPredictor.get_capitalized` upstream; this layer only mirrors the *typed* prefix back into the displayed form.

## Editing a Prediction (OSK-friendly edit popup)

Right-click a prediction pill → Edit opens a small popup with the word pre-filled and selected, so users can correct it (e.g. `iphone` → `iPhone`) and save via `editPrediction(old, new)`. The popup is deliberately non-obvious in one way: OSK keystrokes must land in *our* TextField, but OSK key presses normally synthesize via `xdotool` / `SendInput` to the OS-focused app behind Alpha-OSK.

- **No modal overlay**: `predEditPopup.modal = false`. A modal popup would install an overlay that swallows MouseArea clicks on the keyboard below, so no OSK key would fire.
- **No press-outside close**: `closePolicy: Popup.CloseOnEscape` only — every OSK key click is a "press outside" and would otherwise slam the popup shut on the first keystroke. Escape and the ✕ cancel button are the visible ways out.
- **Edit-mode intercept**: on open/close the popup calls `keyboard.setEditMode(true/false)`. While active, `pressKey` and `pressSpecialKey` short-circuit the synthesizer and emit `editKeyTyped(char)` / `editSpecialPressed(name)` instead. A `Connections { target: keyboard }` block inside the popup wires those to TextField ops — insert at cursor, backspace, delete, left/right/home/end cursor motion, space, return-to-accept, escape-to-cancel.
- **Modifier handling in edit mode**: shift/caps still apply to letter case; ctrl/alt/win are ignored inside the field so stray chords can't leak to the app behind us. Shift auto-releases after one keypress the same way it does outside edit mode.
- **"Saved" confirmation toast**: a small green popup at the top of the window flashes "✓ Saved" for 1.4 s after a successful save. Triggered from all three save paths (✓ button click, Return-key in edit mode, TextField `onAccepted`). The save itself was always synchronous — `set_capitalization` updates the dict immediately and `aboutToQuit` writes it to `ngram_model.json` — but with no UI feedback the user couldn't tell it stuck without quitting and relaunching. Any new save path must also call `editSavedToast.flash()` or the user will think their edit was lost.

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

## Sticky Modifiers (Shift, Ctrl, Alt, Win)

Modifier keys are **sticky** — tap once to activate, tap again to deactivate. While active, the modifier is held at the OS level via `hold_modifier()` / `release_modifier()` on the platform synthesizer. This means:

- **Modifier+click works**: e.g., Ctrl+click to open hyperlinks, Shift+click and Shift+drag to extend text selection in the target app — same model as the Windows on-screen keyboard.
- **Modifier+key combos work**: e.g., tap Ctrl, then tap C → sends Ctrl+C.
- **Auto-release**: After any key press (character or special), active modifiers are released at the OS level and deactivated. Shift specifically auto-releases after one keypress (caps lock pins it on instead) — Ctrl/Alt/Win behave the same way.

### Implementation
- `keyboard_bridge.py`: `toggleShift()` / `toggleCtrl()` / `toggleAlt()` / `toggleWin()` call `_synth.hold_modifier()` on activate and `_synth.release_modifier()` on deactivate. All auto-release paths in `pressKey()` and `pressSpecialKey()` also call `release_modifier()`. `shutdown()` releases any still-held modifiers so quitting with one "active" doesn't pin it at the X server / Wayland compositor / Windows kernel.
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
- **`confidence_threshold` (0.65)**: minimum *absolute* score for `should_autocorrect` to fire — the first gate.
- **`autocorrect_margin` (1.5)**: *relative* gate. The correction's score must clear `typed_baseline * autocorrect_margin`, where `typed_baseline = log1p(1) ≈ 0.69` for plausibly-shaped typings (vowel + consonant) and `0` for implausible slop. This is the LatinIME / Gboard "the literal typed word competes against corrections" pattern — keeps autocorrect from stomping on deliberate typings like "thru", "lol", "btw" while still letting obvious typos through. Implausible inputs ("xqz", "thx") fall back to the absolute threshold alone since their baseline is 0.
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

### Pre-push check

Run `python check.py` before `git push` to catch lint / type / test
failures locally instead of waiting for CI's red X (the same three
gates GitHub Actions runs).  Default mode skips coverage tracking
(~85 s); add `--full` to include the `--cov-fail-under=60` gate
(~3 min, matches CI exactly).

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
- **Fragment filter on learning AND on dictionary load**: `_is_plausible_word` (length ≤ 2 must be in a short whitelist; length ≥ 3 needs both a vowel and a non-`aeiou` letter — `y` counts as both so "eye" and "cry" pass but "aaaa" and "xqz" don't) is applied in three places: (1) `NgramPredictor.learn()` rejects obvious keyboard-slip fragments before they enter the candidate pool, (2) `_load_frequency_wordlist` filters the Google 10K + 20K supplement dumps on first load — those wordlists are scraped from web search corpora and contain every letter of the alphabet plus ~370 two-letter abbreviations / state codes / fragments at high frequency, which would otherwise flood the pills when typing a one-letter prefix, and (3) `load()` strips fragments out of saved `unigrams` and `user_vocab` so existing users' models get cleaned up on the first launch after the filter shipped. Surviving unknown words from `learn()` go through a repetition gate: counted in `_candidate_counts` until 3 sightings, then promoted into `user_vocab`. Known base-dict words and `learn_word()` bypass the gate. Candidate counts decay with the rest of user vocab and persist across save/load.
- **Space-time autocorrect is OFF by default.** `KeyboardBridge._autocorrect_enabled = False`. The on-space path that overwrites the typed word with a correction (`check_autocorrect` → `replace_text`) was clobbering deliberate input — "vs" → "is", and a hyphenated word followed by another word reportedly wiped both. The user wants corrections to surface as suggestion **pills only**, never silent overwrites. The fuzzy recogniser still contributes to the prediction merge so corrections appear as clickable pills (`HybridPredictor` includes fuzzy in its merge sources). `setAutocorrectEnabled(True)` re-enables the space path; tests that exercise it call this slot first.
- **Two-tier autocorrect threshold + short-typing guard** (still applies when autocorrect is on): `FuzzyRecognizer.should_autocorrect` first skips any typing under 3 chars — single-char and 2-char fragments carry too little signal ("v" → "is", "vs" → "is", "th" → "to" all fired before the guard, none of which the user asked for). Then it runs an *absolute* confidence gate (`confidence_threshold`, 0.65) and a *relative* margin gate (`autocorrect_margin`, 1.5×). The relative gate compares the correction's score against `_typed_baseline(typed_word) * 1.5`, where `_typed_baseline` returns `log1p(1) ≈ 0.69` for plausibly-shaped typings (vowel + consonant) and 0 for implausible slop. Plausible deliberate typings ("thru", "lol") are protected; implausible inputs ("xqz", "thx") fall back to the absolute threshold alone. This is the LatinIME / Gboard pattern — the literal typed word effectively competes with corrections — without the full unified-scoring rewrite. Genuine 2-char misspellings that need autocorrect ("im" → "I'm") go through the upstream `check_autocorrect` fast-path table and bypass the length guard.
- **Curated bigram / trigram seed corpus**: `data/common_bigrams.txt` (~700 pairs) and `data/common_trigrams.txt` (~700 sequences) are loaded with high weight (50 per bigram, 50 per trigram + 10 reinforcement on each internal bigram) so cold-start prediction has signal before the user's personal typing builds up. Edit those files to expand coverage; the n-gram loaders skip comment / blank lines and tokenise on whitespace.

### Known gaps (future work, priority order)
1. **SymSpell for fuzzy matching** — Replace Levenshtein edit-distance in `fuzzy_recognizer.py` with SymSpell's precomputed-deletion approach. ~1000x faster, O(1) lookup, ~30MB RAM. (Garbe, 2012)
2. **Unified scoring** — Make the literal typed word compete against corrections in the same ranked list with an explicit score, so the system knows when NOT to correct. The two-tier autocorrect threshold above is a partial proxy; full unified scoring is the proper fix.
3. **Spatial edit costs in ranking** — Key-distance weights from fuzzy_recognizer should feed into final prediction ranking, not just candidate generation.
4. **Katz / Stupid Backoff for sparse contexts** — The linear-interpolation formula above gives λ₃·P_tri even when the trigram table has never seen this 2-word prefix (P_tri = 0). Katz backoff discounts seen events and redistributes the mass to the bigram/unigram fallback. Better behaviour on rare contexts. Larger lift (~100 lines).
5. **Even larger n-gram corpus from a real public source** — the ~700/700 curated lists cover a lot of conversational English, but seeding from COCA top-100k bigrams or Google n-gram exports would dwarf that. Easy win, doesn't require algorithm changes — just more data.

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
7. **Track downloads**: `python scripts/downloads.py` prints per-release and total download counts via `gh api`. Includes auto-updater fetches, so it's a directional number rather than unique-install count.

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

## Known Issues

- **Prediction pills are broken in VS Code.** Clicking a pill produces wrong / duplicated text in the editor. Almost certainly the same root cause as the Remote Desktop Mode watch-out below: VS Code's Monaco editor has its own keystroke interception (IntelliSense, snippet expansion, multi-cursor) that breaks the suffix-only insertion path's "the typed prefix is already on screen, just append the rest" assumption. Workaround for now: turn on Remote Desktop Mode manually in *Settings → Input* when typing into VS Code — that switches to the BackSpace-then-retype path which is robust against keystroke interception. Proper fix is to add VS Code's window class (`Chrome_WidgetWin_1` is too broad — Code uses an Electron host class) or process name (`Code.exe`, `Code - Insiders.exe`) to one of the detection sets in `_is_remote_desktop_window`. Care needed because Electron classes overlap with other apps; process-name match is the safer lever.

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
- **Merge strategy default MUST stay `"rank"`.** `HybridPredictor._merge_strategy` defaults to `"rank"` and `appSettings.savedMergeStrategy` likewise. Four strategies are live (`rank` / `rrf` / `linear` / `loglinear`); the alternatives are confidence-aware and re-rank predictions noticeably differently from the historical behaviour. Changing the default would silently shift every existing user's pill ranking on the next launch — there's no migration prompt and no way for the user to know what changed. If a future strategy beats `rank` on held-out data, ship it as a new option and prompt; do not flip the default. See `docs/HYBRID_MERGING.md`.
- **Each predictor must produce both `predict()` and `predict_with_scores()`.** The merge strategies in `HybridPredictor._merge_predictions` consume score tuples; the LLM rerank path and external callers (e.g. swipe) consume word lists. Today both are wrappers over the same internal sorted list — `predict()` strips scores, `predict_with_scores()` returns them. If you add a new predictor, mirror this contract; if you change one, change the other. The shared post-processing (`_finalize_scores`) and per-source helpers (`_normalise_source`, `_bigram_bonus`, `_source_weights`) are the seams to extend before adding a new strategy.
- **`NgramPredictor._user_total` is an invariant** — every mutation to `user_vocab` (in `learn`, `learn_word`, `_apply_decay`, `clear_user_data`, `load`) must keep it equal to `sum(user_vocab.values())`. `predict()` reads it every keystroke; the consistency tests in `tests/test_ngram_predictor.py::TestUserTotalIncremental` will catch a missed site.
- **Window height is bound to content — do NOT persist it or assign it imperatively.** `Main.qml` declares `height: outerLayout.implicitHeight + 60`, and only `savedWindowWidth` is restored at startup. An earlier version also persisted height, which broke the binding the moment `Component.onCompleted` did `root.height = savedWindowHeight` — once the binding was dead, any width change made the keyboard either clip the bottom row or grow empty bands above/below the keys. The user has no vertical resize handle (both edges are `SizeHorCursor`), so width is the only knob; height auto-follows. If you ever need to add height persistence, you also need a re-binding strategy (`Qt.binding(...)`, or an `onHeightChanged` clamp to expected) — don't just assign and walk away.
- **`KeyButton.qml` drives press visuals off `_visualPressed`, not `mouseArea.pressed`.** With `WS_EX_NOACTIVATE`, Qt occasionally drops the release event when the user drags off the OSK onto another window. Binding visuals straight to `pressed` left the key visually latched. `_visualPressed` is set true on press, cleared on release / cancel / drag-off / a 5 s safety timer — four independent paths back to neutral. If you add a new visual that should follow press state, bind it to `keyRoot._visualPressed`.
- **Single-instance lock holder must outlive `QApplication`.** `keyboard_app.py::_SINGLETON_LOCK` is a module-level `QSharedMemory` reference. `QSharedMemory`'s segment is freed when the holding object is destroyed, so a function-local would release the lock before the app even started. If you refactor `_acquire_singleton_or_surface`, keep that module-level reference alive (or move ownership to the `QApplication` instance).
- **`KEYBDINPUT.dwExtraInfo` is `ULONG_PTR` (an integer), not a real pointer.** MSDN types it that way; we alias `ULONG_PTR = ctypes.c_size_t` in `src/platform/windows.py`. Set it to `0` for our synthesized input — *never* allocate a Python `c_ulong` and pass `ctypes.pointer()` to it. The kernel doesn't dereference the field, but the Python object would be reaped while the INPUT struct still references its address (a real UB hazard if the field is ever read by another consumer).
- **Lifetime analytics: persist BOTH session and lifetime.** Any new metric added to `analytics.py` needs (1) a session counter, (2) an `_alltime_*` mirror, (3) load + save in `_load_alltime` / `save`, and (4) a lifetime field in `get_session_stats`. The dashboard's Lifetime / Session toggle expects every visible metric to have both forms; surfacing only session keys regresses the toggle for that tile. `_compute_quality_score` accepts kwargs so the same logic computes session and lifetime scores.
- **Password detector COM lifecycle.** `password_detect._WindowsUIADetector` tracks `_owns_com` so `CoUninitialize` only fires if *we* called `CoInitializeEx` (S_OK). On S_FALSE (1) we skip the uninit — another caller already owned the apartment and tearing it down would break them. `KeyboardBridge.shutdown` calls `password_detect.shutdown()` to release the IUIAutomation interface and pair the uninit; don't add another path that calls `CoInitializeEx` without matching that pattern.
- **NSIS auto-relaunch only fires on silent install (`/S`) and goes through `explorer.exe`.** The auto-update path leaves the user with no keyboard if the installer doesn't restart the app — `customInit` taskkills the running `alpha-osk.exe` so the new exe can be written. `installer.nsh::customInstall` ends with `IfSilent 0 +2 / Exec '"$WINDIR\explorer.exe" "$INSTDIR\alpha-osk.exe"'`. Two non-obvious things: (1) the `IfSilent` guard means interactive installs don't auto-launch — that's deliberate, the user can pick from the Start Menu. (2) `Exec`-ing `alpha-osk.exe` directly would inherit the installer's high IL (admin) token; spawning via `explorer.exe` drops to the user's medium IL, which is what the OSK needs (UIAccess is designed for medium-IL injecting *into* high-IL, not the other way around, and learned vocabulary should land in the user's `%APPDATA%`, not the admin profile). If you ever swap the launching mechanism, preserve both properties.
- **Modifier+punctuation chords need VK/keysym translation, not Unicode injection.** Ctrl+-, Ctrl+=, Ctrl+/, etc. go through `_send_key` (not `_send_text`) so the chord wraps a real virtual-key event. On Windows `_resolve_vk` only maps A-Z/0-9 directly; for punctuation `send_key` falls back to `VkKeyScanW` to find the layout-correct VK + shift state (so `+` on US layouts becomes Shift+VK_OEM_PLUS without hardcoding OEM codes). On Linux, `_CHAR_TO_KEYSYM` rewrites `-` → `minus`, `=` → `equal`, etc. before chord assembly so xdotool sees the canonical `ctrl+minus` instead of the malformed `ctrl+-` (xdotool's chord parser uses `+` as the separator and expects keysym names). Without these translations the chord goes via Unicode injection (Windows) or breaks the chord parser (Linux), and the target app's shortcut handler — which listens for `WM_KEYDOWN(VK_OEM_*)` or X keysyms, not `WM_CHAR` — never fires. So zoom, comment toggle, settings shortcuts silently don't work. New chord paths must route through `send_key`, not `send_text`.
- **`pressKey` lowercases its input — use `pressKeyLiteral` to type a verbatim character.** `pressKey(key)` applies shift / caps-lock case normalization (`key.upper()` if shift/caps, else `key.lower()`), so passing `'A'` from QML when shift is off gets you `'a'`. The right-click → shifted-variant feature hit this exact bug — the QML handler had already chosen `'A'` and pressKey turned it back. The fix is `pressKeyLiteral(char)`, which types the character as-is. Both slots delegate to `_press_char(key, literal)`; if you add another path where QML has already resolved the final character (e.g. the future long-press alternates picker), use `pressKeyLiteral` and don't second-guess case from Python.
- **`_auto_space_pending` gates the punctuation-spacing cleanup.** When the user types `, . ; : ! ? ) ] }` after a trailing space, we only BackSpace away the space if *we* auto-inserted it (after a prediction click or after `. , ; : ! ?` with auto-space-after-punctuation on). The flag is set at every auto-space site and cleared on every other keystroke (any non-punctuation char, any special key including manual space). User-typed spaces are preserved verbatim — undoing them produced a visible BackSpace flicker after the user's own keystroke and in some apps (rich-text editors, web fields) clobbered selection state and undo history. New auto-space sites must set the flag; new `pressKey` / `pressSpecialKey` paths inherit the clear-on-every-keystroke contract by going through `_press_char` / `pressSpecialKey`.
- **`_context_buffer` mirrors the on-screen text — Backspace must trim it.** `pressSpecialKey("backspace")` always sends one BackSpace to the OS. When `_current_word` is non-empty, we also pop one char off `_current_word`. When `_current_word` is empty, we pop one char off `_context_buffer` instead (added in this commit). Without that second branch, a stale `.` from an earlier sentence stayed in the buffer after the user wiped the screen, and the next prediction call computed `sentence_start = trimmed.endswith(".")` on `"Hello."` and surfaced capitalized candidates on what looked like an empty document. The sentence-start formula itself is correct (`bool(ctx) and ctx[-1] in ".!?"`); the bug was the stale buffer. If you add another keystroke path that mutates the OS-side text without going through `pressSpecialKey`, mirror the same trim, or `_context_buffer` will drift out of sync again.
- **Remote Desktop Mode rewires prediction insertion to BackSpace + retype.** When effective remote-compat is on, `pressPrediction` and the autocorrect-on-space branch in `pressSpecialKey` both stop using suffix-only `_send_text` and `replace_text` (Shift+Left+type). Instead they emit `BackSpace × len(_current_word)` followed by `_send_text(word + " ")`. Reason: TeamViewer / RDP / VNC remote-forwarding pipelines drop, duplicate, and reorder keystrokes between the local OSK and the remote app, so suffix-only's "the typed prefix is already on screen" assumption breaks — the user reported "suggestions create chaos over team viewer" with both scrambled words and "helhello"-style duplicates. The independent-single-event keystroke sequence survives per-event glitches because no event depends on a prior event's outcome. The effective-state gate is computed by `_in_remote_compat_mode()` — `manual OR (auto_enabled AND auto_active)`. Three flags: `_remote_compat_manual` (Settings → Input → "Remote Desktop Mode (always on)" — force-on override, default off), `_remote_compat_auto_enabled` (Settings → Input → "Auto-Detect Remote Desktop Sessions" — default ON), and `_remote_compat_auto_active` (driven by `_check_foreground_window` calling `_is_remote_desktop_window(hwnd)` on every 250 ms poll). Auto-detect default ON is deliberate — the user reported the bug *before* finding the manual toggle, so the keyboard should just do the right thing without requiring a discovery step. If you add a third path that depends on locally-tracked state matching remote state, gate it on `_in_remote_compat_mode()` the same way.
- **Remote-desktop window detection is whitelist-based and platform-specific.** `_is_remote_desktop_window(hwnd)` lives module-level in `keyboard_bridge.py`. Detection runs only on Windows; non-win32 platforms return False unconditionally (the auto-active flag stays False, but the manual toggle still works). Two-pass: first match the window class (`GetClassNameW`) against `_REMOTE_DESKTOP_WINDOW_CLASSES`, then fall back to the owning process exe basename (`QueryFullProcessImageNameW`) against `_REMOTE_DESKTOP_PROCESS_NAMES`. Both are exact-match frozensets so unrelated apps cannot spuriously trigger compat mode (a fail-positive would cost the user the chat-composer-friendly suffix-only path; a fail-negative just means the manual toggle is still available). Adding new tools means appending to one or both sets — keep entries narrow (avoid catch-all class names like `Chrome_RenderWidgetHostHWND` or generic exe names that other apps use). The whole function is wrapped in `try / except (OSError, AttributeError)` so any ctypes failure bails to False rather than throwing on the 250 ms timer thread.
- **Backspace into a completed word must rehydrate `_current_word`.** After the `_context_buffer` trim above, the bridge checks whether the new tail is mid-word (no trailing whitespace) and, if so, calls `_rehydrate_current_word_from_context()` to move the trailing partial word back into `_current_word`. Invariant: "the word currently being edited lives in `_current_word`, the rest lives in `_context_buffer`." Without rehydrate, the partial word stranded in `_context_buffer` while `_current_word` was empty broke `pressPrediction`'s suffix-only insertion path: the case `not self._current_word` fired even though the user *was* mid-edit, so clicking a prediction emitted the FULL word alongside the on-screen partial — "backspac" + clicked "backspaces" = "backspacbackspaces". Any future Backspace-adjacent code (cursor-move special keys that effectively delete a char, popups that drain a partial word back to the OS) must preserve this invariant.
- **`KeyButton.qml` repeat timer needs a warm-up tick.** The `repeatTimer` fires twice per hold cycle: once at `repeatDelay` (warm-up — sets the `warmedUp` flag and rescales `interval` to `repeatInterval`, but does *not* emit `keyPressed`), and again every `repeatInterval` thereafter (each emits `keyPressed`). Without the warm-up, a press held to exactly `repeatDelay` produced two keystrokes — the on-press emit plus the timer's first emit — which slow-motor users hit systematically as "Backspace sends 2". The warm-up swallows the boundary so any press shorter than `repeatDelay + repeatInterval` produces exactly one keystroke. The `warmedUp` flag must be reset wherever the timer is stopped (`onReleased`, `onCanceled`, `onContainsMouseChanged`); otherwise a subsequent press would skip the warm-up tick and resume mid-cycle.
- **Hold-to-repeat timing is user-tunable via Settings → Input.** `appSettings.savedRepeatDelay` and `savedRepeatInterval` (defaults 500 / 120 ms) flow through `root.repeatDelay` / `root.repeatInterval` to every `KeyButton` with `enableRepeat: true` — the Backspace key in `Main.qml` and all seven repeat-enabled keys in `NavigationPanel.qml`. If you add a new repeat-enabled key, pass `repeatDelay: root.repeatDelay` and `repeatInterval: root.repeatInterval` (or the equivalent from a wrapping component's properties) so the user's setting takes effect there too — hardcoded values defeat the entire point of the setting.

## Right-Click for Shifted Character

Right-click on a char key types its shifted variant without flipping the sticky shift state — `1` → `!`, `,` → `<`, `a` → `A`. Modifier and special keys are deliberate no-ops. Toggle in *Settings → Input → "Right-Click for Shifted Character"* (default ON; left-click is unaffected whether on or off). Implementation:
- `KeyButton.qml` exposes a `keyRightPressed` signal. The `MouseArea` accepts both buttons; the right-button branch in `onPressed` returns *before* the auto-repeat timer starts so right-click is always a one-shot. Press visuals + ripple still fire — same tactile feedback as a left-click.
- `Main.qml` per-key `onKeyRightPressed` resolves the output: prefer `kd.shifted` from the layout JSON (covers `1`→`!`, `,`→`<`); fall back to `kd.key.toUpperCase()` for letters; otherwise no-op.
- The handler routes through `keyboard.pressKeyLiteral(rch)`, **not** `pressKey` — the latter would lowercase the chosen `'A'` back to `'a'` (see the `pressKey` watch-out above).

The companion long-press → accents feature is **not** implemented — see `docs/LONG_PRESS_ALTERNATES.md` for the design and the reason it's deferred (press-on-release timing change is hostile to slow-motor users until we have a way to scope the latency to keys with alternates).
