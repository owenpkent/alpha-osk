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
| `vocabulary_pack.py` | Custom vocab pack import (no built-ins ship — see *Vocabulary Packs* section) |
| `transformer_predictor.py` | Optional LLM re-ranking (disabled by default) |

Deep-dive design docs for each algorithm: `docs/FUZZY_RECOGNITION.md` (spatial model + tunable constants), `docs/PPM.md` (variable-order character model + PPMD escape), `docs/HYBRID_MERGING.md` (merge weights + validation + capitalization), `docs/SWIPE_TYPING.md` (shape-matching swipe decoder).

## Auto-Capitalization & Proper Nouns

The pill-facing capitalization rule is intentionally minimal: **only the "I" family auto-capitalizes** (`"I"`, `"I'm"`, `"I'll"`, `"I'd"`, `"I've"` — hardcoded in `ngram_predictor._always_capitalize`). Anything else stays in the casing the user typed. The mental model is "shift / caps lock is the cap signal, full stop" — pills do not second-guess intent.

This used to be a three-tier Gboard-style system (Tier 1 "I" family, Tier 2 sentence-start for ambiguous names like `will` / `jack` / `may`, Tier 3 ~8 000 unambiguous proper nouns from `data/proper_nouns.txt` plus user-taught forms). Tiers 2 and 3 fired on too many common English words ("the hope is that", "a rose by", "will you", "may i", and the post-period word in any sentence), so pills came back capitalised when the user had typed lowercase. The user's stance is that those auto-caps were noise, not help.

### How it works now
- `NgramPredictor.get_capitalized(word, sentence_start)` returns the `_always_capitalize` form for the "I" family, otherwise returns `word` unchanged. The `sentence_start` argument is kept for API compatibility but ignored.
- `HybridPredictor._merge_predictions()` still calls `get_capitalized` on each pill (so the "I" family flows through the engine like any other word), and still computes `sentence_start = bool(ctx) and ctx[-1] in ".!?"` — the value just doesn't affect the result.
- **Pill-facing casing comes from `KeyboardBridge._display_cased`** — it mirrors *every* uppercase position from the typed prefix onto the pill. Type lowercase `monday` → pill shows `monday`. Type `Monday` (one-shot shift on the M) → pill shows `Monday`. Type `MON` (right-click each letter) → pill shows `MONday`. This is the only path that produces capitals in pills, and it's driven entirely by what the user typed.

### Data still being collected (currently inert in pills)
Two paths populate `NgramPredictor.capitalization` even though `get_capitalized` no longer reads from it:
- `_load_proper_nouns()` reads `data/proper_nouns.txt` at startup.
- `learn_capitalization(word, *, allow_uppercase=False)` is called from the bridge in three situations: (a) the user types a word with non-trivial casing and completes it with space; (b) the user has any uppercase letter in their typed prefix and accepts a pill (`pressPrediction` calls `learn_capitalization(word)` on the chosen pill); (c) the user right-click → Edits a prediction. The `allow_uppercase` guard is still meaningful: `_word_typed_under_caps_lock` flips to True whenever a char is appended while Caps Lock is on, and the bridge passes `allow_uppercase = not _word_typed_under_caps_lock` so all-caps under Caps Lock doesn't poison the table. Acronyms typed deliberately (right-clicking each letter, Caps Lock off) still land in the table.

The accumulated dict is persisted in `ngram_model.json`. Keeping the data lets a future opt-in switch (e.g. a "capitalize proper nouns" toggle) re-enable Tier 3 without re-teaching from scratch. **If you re-enable any tier, do it by editing `get_capitalized` to consult `self.capitalization` again — don't reintroduce the old three-tier behaviour as the default.**

### Adding to always-capitalize
Edit the `_always_capitalize` dict in `ngram_predictor.py`. Keep it tight — it's the one auto-cap that will fire mid-sentence regardless of what the user typed, so anything beyond the "I" family needs to be unambiguous in *every* mid-sentence context (which proper nouns aren't, which is why Tiers 2/3 are gone).

## Where User Data Lives

- **Settings** (layout, theme, toggles): Managed by Qt `Settings` in QML. Auto-saved on change. Stored in OS registry/config automatically by Qt.
- **Prediction model** (learned words/phrases): Saved to disk explicitly or via auto-save on exit.
  - Windows: `%APPDATA%/alpha-osk/models/`
  - Linux: `~/.config/alpha-osk/models/`
  - Files: `ngram_model.json`, `ppm_model.json`
  - **Load-time caps**: both loaders reject files over 50 MB. The n-gram loader also rejects files with more than 500 000 unigrams, 500 000 bigram prefixes, or 100 000 capitalisation entries — anything beyond these is assumed to be corrupt or hostile and is silently skipped (the in-memory base dictionary is kept).
- **Custom vocabulary packs**: Imported by the user. No built-in packs ship — see *Vocabulary Packs* section below for why.
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

`_display_cased` *also* mirrors **every** uppercase position from the typed prefix onto the displayed pill, not just the first letter. If the user typed "Hel" the pills show "Hello"/"Help"; if they right-clicked each letter to type "HEL", the pills show "HELlo"/"HELp"; if they typed "iP" (mid-word cap via right-click), the pill shows "iPhone". The gate is `any(c.isupper() for c in cw)` and the body iterates each prediction position, force-uppercasing it when the corresponding `cw[i]` is uppercase. The mirror runs **regardless of whether the pill strict-prefix-matches the typed letters**, which is the difference from the original implementation. The earlier version short-circuited to pass-through whenever `w.lower().startswith(cw.lower())` was False, which silently dropped the cap on every fuzzy / autocorrect candidate (typing "Hwl" for "Hel" → fuzzy returns "hello" → "hello" doesn't strict-prefix "hwl" → cap lost). Mirroring unconditionally fixes that. Two reasons capitalised pills still matter even when the prefix matches: (1) the displayed pill must reflect what the user typed so they can tell which pill matches their prefix, and (2) the suffix-only insert path uses a case-sensitive `startswith`, so "hello".startswith("HEL") is False and the click would fall through to a full replace, clobbering the user's capitals. Sentence-start and proper-noun capitalisation still flow through `NgramPredictor.get_capitalized` upstream; this layer only mirrors the *typed* prefix back into the displayed form.

## Editing a Prediction (OSK-friendly edit popup)

Right-click a prediction pill → Edit opens a small popup with the word pre-filled and selected, so users can correct it (e.g. `iphone` → `iPhone`) and save via `editPrediction(old, new)`. The popup is deliberately non-obvious in one way: OSK keystrokes must land in *our* TextField, but OSK key presses normally synthesize via `xdotool` / `SendInput` to the OS-focused app behind Alpha-OSK.

- **No modal overlay**: `predEditPopup.modal = false`. A modal popup would install an overlay that swallows MouseArea clicks on the keyboard below, so no OSK key would fire.
- **No press-outside close**: `closePolicy: Popup.CloseOnEscape` only — every OSK key click is a "press outside" and would otherwise slam the popup shut on the first keystroke. Escape and the ✕ cancel button are the visible ways out.
- **Edit-mode intercept**: on open/close the popup calls `keyboard.setEditMode(true/false)`. While active, `pressKey` and `pressSpecialKey` short-circuit the synthesizer and emit `editKeyTyped(char)` / `editSpecialPressed(name)` instead. A `Connections { target: keyboard }` block inside the popup wires those to TextField ops — insert at cursor, backspace, delete, left/right/home/end cursor motion, space, return-to-accept, escape-to-cancel.
- **Modifier handling in edit mode**: shift/caps still apply to letter case; ctrl/alt/win are ignored inside the field so stray chords can't leak to the app behind us. Shift auto-releases after one keypress the same way it does outside edit mode.
- **"Saved" confirmation toast**: a small green popup at the top of the window flashes "✓ Saved" for 1.4 s after a successful save. Triggered from all three save paths (✓ button click, Return-key in edit mode, TextField `onAccepted`). The save itself was always synchronous — `set_capitalization` updates the dict immediately and `aboutToQuit` writes it to `ngram_model.json` — but with no UI feedback the user couldn't tell it stuck without quitting and relaunching. Any new save path must also call `editSavedToast.flash()` or the user will think their edit was lost.

If you add a new input source (e.g. a voice-dictation slot, another popup with its own TextField), the pattern is: set edit mode on open, listen to `editKeyTyped` / `editSpecialPressed`, clear edit mode on close. Don't try to route through Qt focus — `WS_EX_NOACTIVATE` / `WindowDoesNotAcceptFocus` prevent our window from holding OS focus, so physical keyboard input and synthesized input both go to whatever app was focused before we opened.

## Swipe / Glide Typing

Drag the mouse across letters to type a whole word in one gesture, like Gboard. Off by default; toggle in *Settings → Smart Typing → Suggestions → Swipe Typing*. Design doc: `docs/SWIPE_TYPING.md`.

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

## Settings Panel Structure

`UnifiedSettingsPanel.qml` is a drill-down menu, not a long scrolling list. The home view shows four category cards; clicking a card swaps the body to that category's sub-view. The header swaps in a back arrow (‹) and the category title; the close ✕ stays put.

State is held in a single string property: `currentView` ∈ {`"home"`, `"appearance"`, `"typing"`, `"model"`, `"data"`}. The Flickable contains five sibling `ColumnLayout`s, each with `visible: unifiedSettings.currentView === "<id>"`; only one renders at a time. Scroll position is reset to the top on every view change (a `Connections` block on `currentView`) so a drilled-in view never opens mid-section.

The parent (`Main.qml`'s settings popup window) calls `settingsPanel.resetToHome()` in `onVisibleChanged` so re-opening Settings always lands on the home grid, not whatever sub-page the user last visited. Don't break that — landing on a deep page reads as "the menu changed."

### Where each section lives

| Top-level | Section | What's inside |
|-----------|---------|---------------|
| **Appearance** | Panels | Function row / Navigation / Numpad toggles |
| | Keyboard Layout | qwerty / dvorak / colemak picker |
| | Theme | 9-theme color picker |
| | Sound & Opacity | Key click sound, opacity slider |
| **Smart Typing** | Suggestions | Show suggestions, auto-space, auto-cap, swipe, max count |
| | Suggestion Engine | Merge strategy 4-card picker (rank / rrf / linear / loglinear) |
| | Input | Right-click shift, Compatibility Mode picker, repeat delay & interval |
| **Your Language Model** | (top button) | Open Dashboard → opens ModelVisualization |
| | Vocabulary Packs | Toggles for any imported packs + Import Custom Pack (no built-ins ship) |
| | Prediction Model | Auto-save toggle, Save Now, Clear Learned Data |
| **Data & Privacy** | (top button) | Help & Shortcuts |
| | Privacy | Telemetry opt-in + Delete contributed data |
| | Updates | Installed version, auto-check toggle, Check Now |
| | Developer | Debug Mode |

Old labels and their new homes (for backwards-compat references in code comments / docs you might see): the standalone "Layout" section was renamed to "Panels" (the parent category is "Appearance", reusing the name was confusing); the standalone "Appearance" section was renamed to "Sound & Opacity" for the same reason; the old "Tools" section was split — its **Help & Shortcuts** button is now a standalone tile at the top of Data & Privacy, and its **Your Language Model** button moved to be the top-of-page tile in the Your Language Model view.

### Adding a New Setting

1. Add `property bool savedFoo: defaultValue` to `Settings {}` in `Main.qml`
2. Add `property bool foo: appSettings.savedFoo` to root in `Main.qml`
3. Add `property bool foo: defaultValue` to `UnifiedSettingsPanel.qml`
4. Add `SettingsToggle` to the **right sub-view** in `UnifiedSettingsPanel.qml` — pick the category from the table above. Toggles go inside an existing `SettingsSection` block; if no section fits, add a new `SettingsSection { title: "..." }` to that view.
5. Pass property through: `foo: root.foo` in the `Comp.UnifiedSettingsPanel {}` block
6. Handle in `onSettingChanged`: update root, save to appSettings, call bridge if needed
7. If Python needs it: add `@Slot(bool) def setFoo()` to `keyboard_bridge.py`
8. Load on startup in `Component.onCompleted` if it needs to be sent to the bridge

If you can't decide which category a new setting belongs to, that's a sign the UX is fuzzy — push back on the requirement before adding the setting.

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

The spatial layout (`QWERTY_POSITIONS`) covers a-z plus 0-9 — the digit row sits at row -1 directly above qwerty (5 above t, 6 above y, etc.) so an off-by-one-row mistype between letter and digit ("h3llo" → "hello") is recoverable. Punctuation and the numpad are deliberately unmapped: punctuation has a different error mode, and the numpad is spatially isolated from letters and has no dictionary to correct against. If you add a new layout (Dvorak, Colemak), mirror this — letters + digit row only.

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

## Word Suppression and Boosting

Users can right-click prediction pills to:
- **Show more** — clears any prior dispreference and bumps `ngram_predictor.unigrams` / `user_vocab` by +5 (same magnitude as the prediction-click reinforcement), then records the boost in `ngram_predictor.preferred` so the dashboard can surface it and the user can roll it back.
- **Show less** — increments `ngram_predictor.dispreference` (word is downweighted by `1 / (1 + count * 0.5)`)
- **Remove** — adds to `ngram_predictor.blacklist` (word never appears again)

All three are persisted in `ngram_model.json`. Suppression is applied in `hybrid_predictor._merge_predictions()`; boosting is implicit in the bumped unigram counts (no separate multiplier — the engine treats a boosted word the same as a heavily-typed word).

### Boost rollback math
`unprefer(word)` decrements `unigrams` / `user_vocab` / `_user_total` / `total_words` by the cumulative boost amount, capped at the current `user_vocab` count so a word that was also organically learned keeps its organic count after the boost is removed. The `preferred` entry is then dropped. Boosts are never applied to bigrams or trigrams.

### Restoring Suppressed and Boosted Words
In the Model Visualization dashboard (Settings → Your Language Model → Open Dashboard → Dashboard tab), three sections surface user-adjusted words as clickable tags:
- **Boosted Words** — green tags labelled `word (+N)` where N is the cumulative boost. Click to call `keyboard.unprefer(word)` which rolls back the boost (see math above).
- **Suppressed Words → Blocked** — red tags for blacklisted words. Click to call `keyboard.unblacklistWord(word)`.
- **Suppressed Words → Downweighted** — yellow tags for dispreferred words. Click to call `keyboard.undisprefer(word)`.

Each section is hidden when the corresponding list is empty (`preferredCount > 0`, `blacklistCount > 0`, `dispreferenceCount > 0`).

Bridge slots: `keyboard.markGoodSuggestion(word)`, `keyboard.markBadSuggestion(word)`, `keyboard.blacklistWord(word)`, `keyboard.unprefer(word)`, `keyboard.unblacklistWord(word)`, `keyboard.undisprefer(word)`.

### Auto-Rehabilitation
If a user manually types a blacklisted word 3 times (completing it with space), the word is automatically restored to predictions. Tracked via `ngram_predictor._blacklist_type_count`, persisted in `ngram_model.json`.

## Model Visualization

Accessed via Settings → Your Language Model → Open Dashboard. Three tabs:
- **Word Cloud** — circle-packed bubble chart of top words, sized by frequency
- **Word Flow** — network graph of bigram word→word connections
- **Dashboard** — embedded AnalyticsDashboard (lifetime/session typing stats) at top, then stats cards, top words bar chart, interactive boosted words, interactive suppressed words, top word pairs. The AnalyticsDashboard was previously a separate section at the top of the Settings panel; it was moved here because lifetime savings is user-typing data and belongs with the rest of the user's model.

Data provided by `keyboard_bridge.getVisualizationData()` → `ModelVisualization.qml`.

### Click-to-drill-down

Clicking a circle in the Word Cloud or a node in the Word Flow opens a side panel with that word's top successors (bigram `word → next`), top predecessors (`prev → word`), and trigram windows (`X word Y` middle position + `X Y word` trailing position). Predecessor / successor entries are themselves clickable — click "asked" under "claude"'s predecessors to drill into "asked". Data comes from `keyboard.getWordContext(word)` (bridge slot) which reads `ngram.bigrams` / `ngram.trigrams` directly — no extra tracking. Hit-testing is canvas-side: a `MouseArea` over each canvas walks `circles[]` / `nodes[]` and matches against squared-distance-to-center, so the click target is the visible circle. Selected node is outlined white; the drill-down panel slides in from the right at z=5 over Cloud and Flow tabs (hidden on Dashboard since it has its own Suppressed Words drill-in).

### Live pulse on the active edge

While the visualization window is open, typing in the foreground app pulses the matching node and edge. Driven by `KeyboardBridge.activeContextChanged(prev_word, current_partial)`, emitted from `_update_predictions` on every keystroke (suppressed in privacy mode — must not leak password chars or password-field context). The viz holds `activePrevWord` / `activeCurrentWord` properties and the canvases compare `n.word === activePrevWord || n.word === activeCurrentWord` per node and `from.word === activePrevWord && to.word === activeCurrentWord` per edge. Active node gets a warm gold glow + `#ffd84d` border; active edge draws gold with thicker stroke. The signal is intentionally cheap — raw lowercased tokens, no formatting — and the viz drives a short pulse off the property rebinding, no Timer per canvas.

## Privacy Mode & Password Detection

Protects sensitive input (passwords, PINs) from leaking into the prediction model.

### How it works
- **Auto-detection** (Windows): Two complementary paths call `is_password_field()` from `src/platform/password_detect.py`:
  1. A background `QTimer` polls every 200ms (`_check_password_field`). Catches focus changes that happen between keystrokes.
  2. **Every keystroke** (`pressKey`/`pressSpecialKey`) also calls `_check_password_field_sync()`, rate-limited to ~50ms via `_last_sync_password_check`. Closes the race window where the first characters after focus lands on a password field would otherwise reach the prediction cache before the timer fires.
- Detection uses Windows UI Automation COM (`IUIAutomation::GetFocusedElement` → `UIA_IsPasswordPropertyId`) in native apps and browsers. Falls back to Win32 `EM_GETPASSWORDCHAR` if UIA fails.
- **Manual toggle**: "Learning" / "Paused" text button in the title bar. Overrides auto-detection. Used to be a play/pause Canvas icon but the media-player metaphor read as "is something playing" rather than "is the keyboard learning"; see the title-bar bullet in *Things to Watch Out For* for the full rationale.
- **When active**: Keystrokes still reach the OS, but `_current_word`, predictions, and learning are all suppressed. The prediction bar shows "Learning paused".

### Key files
- `src/platform/password_detect.py` — platform-specific detection (UIA COM via ctypes)
- `src/keyboard_bridge.py` — `_privacy_mode` flag, `_check_password_field()` timer, `_check_password_field_sync()` per-keystroke, `setPrivacyMode()` slot

### Linux
Auto-detection uses AT-SPI 2 via `gi.repository.Atspi`. A daemon thread owns a GLib event loop and listens for `object:state-changed:focused`; whenever focus lands on an accessible whose state set contains `STATE_PASSWORD_TEXT`, the shared `_is_password` flag flips on. Works for GTK (`GtkEntry` with `visibility=false`), Qt (`QLineEdit` in Password echo mode), and browsers that expose accessibility metadata. Requires `gir1.2-atspi-2.0` + a working at-spi bus on the host. If `gi` fails to import or `Atspi.init()` fails, falls back silently to the null detector — users can still toggle privacy mode manually.

## Themes

Defined in `themeData` in `Main.qml`. Each theme has: `name`, `background`, `keyColor`, `keyPressed`, `textColor`, `accent`, `border`.

**9 themes**: Dark, Light, Ocean, Forest, Amethyst, Vaporwave, Blackboard, Typewriter, Spaceship.

Theme colors flow to all components: main keyboard keys, prediction pills, nav panel, numpad, title bar icons, and active key states (NumLock, Shift, etc.). `KeyButton.qml` auto-computes text contrast on active/pressed states using luminance.

Theme picker in settings shows labeled color swatches with mini key previews.

## Vocabulary

- **Base**: Google 10K wordlist (`data/google-10000-english-usa-no-swears.txt`) + 10K supplement (`data/google-20000-supplement.txt`, filtered for explicit content). ~20K total regular words.
- **Packs**: No built-ins ship. The system is import-only — see *Vocabulary Packs* section. Imported packs appear as toggles in Settings → Your Language Model → Vocabulary Packs.
- **Numpad**: Toggles between numbers and navigation keys (Home/End/PgUp/PgDn/arrows/Ins/Del) via NumLock. Key 5 is blank in nav mode. Layout mirrors a physical numpad: rows `7 8 9 /`, `4 5 6 *`, `1 2 3 -`, `0(span 2) . +`, `Enter(span 3) NumLock`. NumLock sits at the bottom-right (active highlight uses the theme accent), Enter is the wide bottom-row key. Earlier builds put NumLock on the top row and stretched `+` / Enter as 2-row spans on the right column. The flat 5-row layout was the user's request to match a physical 10-key.

## Vocabulary Packs

Import-only. **No built-in packs ship.** Earlier releases shipped six (medical / programming / academic / gaming / business / nsfw) but each was 200-400 words — too thin to compete with personal learning, which bumps a word's score by +5 every time the user accepts it as a pill. After typing "physical therapy" three times, the user's own model already knows it, and the seed list saves nothing. Sourcing a real domain vocabulary (SNOMED-grade for medical, full API surface for programming) is its own project and runs into licensing rabbit holes; curated 300-word lists were strictly worse than no shipped packs at all. They were also drifting in maintenance (NSFW had a different `pack.json` schema and no n-grams) and there was an open correctness bug (see *Known limitations* below).

### What the system still does
- `src/prediction/vocabulary_pack.py` (`VocabularyPack`, `PackManager`) discovers packs from `data/packs/` (now absent) and from the user dir (`%APPDATA%/alpha-osk/packs/` Windows, `~/.config/alpha-osk/packs/` Linux). The user dir is created on first launch.
- Pack format: a folder containing `dictionary.txt` (required, one word per line, `#` comments allowed), optional `bigrams.txt` (whitespace-separated word pairs), `trigrams.txt` (word triples), and `pack.json` (`{name, description, version}` — generated automatically if missing on import).
- Settings → Your Language Model → Vocabulary Packs shows one toggle per imported pack (driven by `keyboard.getAvailablePacks()` returning the rich `{id, name, description, version, words, bigrams, trigrams}` list — the `id` field is the directory name and `VocabularyPack.get_info()` includes it explicitly so the QML side can call enable/disable).
- Empty state: just the "Import Custom Pack…" button + a one-line note about the format. The hardcoded `[{id: "medical", label: "Medical"}, ...]` Repeater that drove the old UI is gone — adding a new pack only requires importing it (or, in a future release, dropping a folder under `data/packs/`); no QML edit needed.
- Import hardening (security-critical, **don't loosen**): folder name sanitised to `[a-z0-9_-]{1,64}`, resolved destination verified to sit strictly under `user_packs_dir` before any `rmtree`/`copytree`, symlinks inside the source tree are skipped rather than dereferenced. Built-in packs (if any) cannot be overwritten via import. See `tests/test_vocabulary_pack.py::TestImportPackSecurity` for the regression coverage.

### Known limitations
- **Disabling a pack does not undo its predictor injection.** `apply_to_predictor` writes pack words into `predictor.unigrams / .bigrams / .trigrams` with `max()`. `disable_pack` calls `pack.unload()` which clears the *pack's own* in-memory copy, but the entries it pushed into the predictor stay there until the next process restart. Mostly invisible now that no built-ins ship (only users who imported a pack and then disabled it without restarting hit this), but worth fixing if we ever ship built-ins again. The clean fix is to track per-pack `(word, prior_value)` tuples at apply time and revert on disable, with a guard that only reverts when the predictor's current value still equals the pack's contribution (so words that piled on organic learning after enable aren't clobbered).
- **`apply_to_predictor` uses `max()` for bigrams/trigrams, not addition.** Earlier comments in this file claimed bigrams/trigrams were "additive with weight 30" — that was the doc, not the code. Code is correct: additive would compound on every enable cycle. The doc is now consistent.

### Re-introducing a built-in pack
If a future release ships a built-in pack, mirror it back into `data/packs/<id>/` with the four files described above. PackManager's `_discover_packs` will pick it up automatically (it iterates both built-in and user dirs). Add a parametrised structural test back to `tests/test_vocabulary_pack.py` modelled on the deleted `TestRealPacks` class — the `sample_pack_dir` fixture in that file shows the expected shape.

## Analytics

`src/analytics.py` tracks session and all-time stats. All-time stats persist to `<config_dir>/analytics.json`.

Every session counter has an `_alltime_*` mirror that's loaded on launch, merged with the session at exit, and surfaced in `get_session_stats()` as both `<metric>` (session) and `alltime<Metric>` (lifetime). The dashboard's Lifetime / Session toggle (`AnalyticsDashboard.qml`) drives every tile off these paired keys. Persisted fields include: keystrokes, words, predictions (hits), keystrokes_saved, sessions, minutes, **backspaces, prediction_offers, prediction_rank_sum/count, top_pick_count, word_freq, key_freq**. Word frequencies are capped at 5000 unique entries on save (top-N by count) so `analytics.json` stays bounded over years of typing.

The dashboard is now a **single section**: scope toggle (Lifetime / This Session) + 2x2 tile grid + sparkline + top words. Earlier versions layered a separate hero card ("10.3k keystrokes saved" with green border), an all-time stats pill row (words / sessions / hours), and a horizontal divider above the tile grid; the user reported it read as 4 disconnected sections rather than one analytics view. Promoting Keystrokes Saved into the tile grid carries the headline number, and the words/sessions/hours pills were dropped (sessions and hours weren't load-bearing; words is implicit from the prediction-related tiles).

The four tiles are **Keystrokes Saved** (formatted count, subtext "keys you didn't have to press"), **Time Saved** (formatted hours/min from `keystrokes_saved × user's own seconds per keystroke`, falling back to 0.5s/key for new installs; subtext "avoided by predictions"), **Effort Saved** (`savingsPercent`, subtext "of total keystrokes"), and **Acceptance** (`acceptanceRate` = `prediction_hits / prediction_offers`, subtext "of offered suggestions accepted"). Keystrokes Saved + Time Saved + Effort Saved are three framings of the same underlying engine output: absolute count, wall-clock, and percentage respectively. They're shown together because each lands differently with different mindsets (a daily-saving thinker, a wall-clock thinker, a relative-effort thinker). Acceptance is **distinct from** the others: it asks "when the keyboard offered a suggestion, how often was it useful enough to take" (an engine quality signal), independent of how many keystrokes the user typed total. All four subtexts are deliberately verbose ("of total keystrokes" not "of typing effort") to make the denominator unambiguous; the user iterated on terser variants and found them ambiguous.

Earlier iterations also had **Typing Effort** (total keystrokes typed) and **Predictions Used** (hit rate %) and **Corrections** (backspace count) tiles. All three metrics are still tracked and exposed in `getAnalytics()` (`alltimeKeystrokes`, `predictionHitRate`, `alltimeBackspaces`, etc.) because the Model Visualization Dashboard and other callers may use them; only the AnalyticsDashboard surface dropped them. WPM lived on the first tile briefly but was unusable on cold sessions (a fresh "0.5 avg wpm" reading next to a lifetime "103 hrs saved" hero card visually contradicted itself).

The `StatBox` component grows its background Rectangle from `contentCol.implicitHeight + 14` rather than using a fixed `implicitHeight: 50`. The fixed height was ~10 px shorter than the three text elements need, so subtext rendered past the rounded gray background. If you add a fourth Text element to StatBox, this binding still works as long as the inner ColumnLayout is anchored only horizontally + verticalCenter (don't switch to `anchors.fill: parent`, which would break the implicit-height computation by yoking layout size to rectangle size).

The earlier composite Prediction Quality Score (0-100, weighted savings + hit rate + rank + low-correction) was removed because the number wasn't actionable: a user can act on "you've saved 4.2 hours" but a "73/100" composite hides which lever moved. Don't reintroduce the composite as a primary surface; if you need a single internal scoring number for ranking strategy comparisons, compute it ad-hoc in tests rather than baking it back into `get_session_stats`.

`top_pick_count` is still computed and persisted (incremented inside `record_prediction_selected` only when `rank == 1`) and surfaced as `alltimeTopPickRate` for the Model Visualization Dashboard. It was briefly the subtext on the Predictions Used tile but reads "0%" for any user upgrading from a prior build (the counter didn't exist then), which masked real usage.

`top_pick_count` is incremented inside `record_prediction_selected` only when `rank == 1`. The bridge already passes a 1-based rank in `pressPrediction`, so no caller-side change is needed when adding new prediction surfaces. They just need to call `record_prediction_selected` with the right rank.

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
- **Targeted reinforcement on prediction click**: `HybridPredictor.learn_from_selection(context, selected_word)` boosts the selected word's unigram via `learn_word(+5)` and adds **only** the trailing `(prev_word, selected_word)` bigram and `(prev2, prev1, selected_word)` trigram via `NgramPredictor.reinforce_context`. Earlier this passed `context + selected_word` to `learn()`, which re-incremented every bigram in the running buffer on each prediction click — inflating the leading edges in proportion to how many predictions the user picked per sentence. The fix is a clean +1 to the edge that was actually validated by the click.
- **Backspace as negative signal**: `NgramPredictor.unlearn_word(word)` retracts one sighting from `_candidate_counts` (most common — typo never made it into `user_vocab` yet) or, if already promoted, decrements `user_vocab` / `unigrams` / `_user_total` / `total_words` together. Bigrams/trigrams are intentionally untouched (one backspace shouldn't crater multi-word context history). Wired into `KeyboardBridge._rehydrate_current_word_from_context`: when backspace pops a trailing space and rehydrates a word back into `_current_word`, the rehydrated word is unlearned (gated on `not _privacy_mode`). Net effect: typing `teh ` then immediately backspacing past the space removes the candidate sighting; if the user re-completes the word with the same spelling, `learn()` counts it again. A word that has been typed many times and is already deep in `user_vocab` can't be unlearned in one keystroke — the decrement is per-sighting, not per-word.

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

Version source of truth is `src/__version__.py`. The release-asset filename **must** match `Alpha-OSK-Setup-{version}.exe` exactly — the updater rejects anything else. User-facing toggle: *Settings → Data & Privacy → Updates → "Check for updates on startup"* (persisted as `appSettings.savedAutoCheckUpdates`).

### Update progress UI (T40)

Three pieces cover the gap between "user clicks install" and "new keyboard appears":

1. **Pre-install toast in the live OSK** (`updateInstallHandoffPending` signal → `updateStartingToast` in `Main.qml`). Fired by `updater.download_and_install` via the `on_installer_launching` callback, immediately before `_launch_installer`. The bridge callback emits the signal then sleeps `_PRE_INSTALL_TOAST_DWELL_S` (1.8 s) in the worker thread so the toast paints before the installer's taskkill arrives. Without this dwell, the toast and the keyboard would both vanish before the user could read it.
2. **Relauncher splash** (`_run_with_splash` in `_update_relauncher.py`). A frameless `WindowStaysOnTopHint` widget owned by the detached relauncher process. The polling logic was refactored from blocking sleep loops into a `QTimer` state machine (`_poll_parent` → `_poll_new_exe` → `_launch`) so the splash stays painted between checks. New `_new_exe_ready` is the single-shot mirror of `_wait_for_new_exe`. Splash colours match the in-app toast. Has a ✕ dismiss button that *hides* the splash but keeps polling running — the user is dismissing the visual, not the work.
3. **Post-update ✓ toast** (already shipped in 1.0.17, `updateAppliedToast` driven by `consumeUpdateHandoff`). Confirms the install completed.

**`_run_headless` is preserved** as the legacy code path. Tests target it (so they don't have to stand up `QApplication`) and it's the fallback when the splash fails to start (PySide6 import error, no display server). Production always passes `--show-splash` so the splash path runs.

**Dev-mode short-circuit**: `_spawn_relauncher` passes `--target-exe sys.executable` in dev mode; `_new_exe_ready` would then wait for `python.exe`'s mtime to advance past parent-death, which never happens, leaving the splash stuck at "Installing files…" for the full `_NEW_EXE_TIMEOUT_S`. `_is_dev_target()` detects `python` / `pythonw` basenames and routes those straight to headless. Production installs always point at `alpha-osk.exe`, so this guard never trips for real users.

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

## Opt-in Telemetry

Design: `docs/TELEMETRY.md`. User-facing privacy: `docs/PRIVACY.md`. Backend: `backend/cf-worker/` (Cloudflare Worker + D1).

**Off by default.** When enabled (Settings → Data & Privacy → Privacy → "Share anonymous usage stats"), the client sends a weekly POST containing nine integers: `anon_id`, `app_version`, `os`, `keystrokes`, `words`, `predictions`, `keystrokes_saved`, `minutes`, `sessions`, `prediction_offers`. These are exactly the lifetime counters already shown on the Analytics dashboard. **Never sent**: content, word frequencies, key frequencies, IP, hostname, or any per-session breakdown.

### Files
- `src/telemetry.py` — `TelemetryClient`. Owns the consent flag, the UUID4 `anon_id`, and the weekly cadence. Persists to `<config_dir>/telemetry.json` (separate from `analytics.json` to avoid two-writer contention; `TypingAnalytics` owns analytics.json on the on-quit path).
- `src/keyboard_bridge.py` — instantiates the client in `__init__`, wires an hourly `QTimer` to `maybe_submit()` (which short-circuits unless the 7-day window has elapsed), and calls `submit_on_quit()` from `shutdown()`. Three new slots: `getTelemetryEnabled()`, `setTelemetryEnabled(bool)`, `forgetTelemetryData()`.
- `qml/components/UnifiedSettingsPanel.qml` — Privacy section in the **Data & Privacy** sub-view, with the toggle and a two-step "Delete my contributed data" button. Toggle initial state is queried from the bridge on mount; **don't** mirror it into `appSettings`, the `TelemetryClient` is the source of truth.
- `backend/cf-worker/` — Cloudflare Worker (TypeScript) exposing `POST /v1/submit`, `GET /v1/aggregate`, `POST /v1/forget`, plus a daily cron that prunes users with `last_seen` older than 365 days.

### Endpoint configuration
`DEFAULT_ENDPOINT` in `src/telemetry.py` is the empty string. While empty, the client treats the endpoint as "not configured" and silently no-ops every submit (consent toggle still works, just no data leaves the machine). Set this constant per-build before shipping a release that has telemetry enabled. Plan is to flip it on after the worker is deployed and the schema is verified against real submissions. Full deployment + dev-validation workflow is in `docs/TELEMETRY.md` § "Deployment & release"; the Windows release checklist (`docs/WINDOWS.md` step 2a) gates on this.

### anon_id lifecycle
UUID4 generated on first opt-in. **Cleared on opt-out**, so re-opt-in gets a new id and prior contributions cannot be linked. If the user wants their already-submitted row deleted, the "Delete my contributed data" button POSTs to `/v1/forget` (returns 204 regardless of whether the id existed). Reinstall or `~/.config/alpha-osk/` deletion also resets the id.

### Submit cadence
- **Weekly QTimer** in the bridge (1-hour tick, internal 7-day window check). First submit lands ~7 days after opt-in, not immediately on toggle.
- **`submit_on_quit`** from `shutdown()`. Bypasses the weekly window (with a 60 s anti-spam guard) so a user who quits soon after a long session doesn't lose that delta.

Both paths are gated on `enabled AND endpoint AND anon_id`. Failures are silent (3 retries with exponential backoff `[5s, 30s, 120s]`, then drop until next week — no user-visible error toasts).

### Privacy mode interaction
None needed. Privacy mode (password-field detection) suppresses `_current_word`, prediction tracking, and learning at the bridge level, so password-field activity never enters the analytics counters in the first place. Telemetry just forwards what the analytics dashboard would show.

### What is NOT telemetry
- Auto-update version checks: those are GitHub Releases requests, unrelated.
- Federated learning: a separate (planned, unimplemented) feature with its own opt-in toggle and its own design (n-gram deltas + DP noise). Keep them conceptually separate even when explaining to users.

## Building & Signing a Release (Windows)

Full step-by-step release checklist, signing details, troubleshooting table, and bundle-size notes are in `docs/WINDOWS.md` (sections "Building a Standalone Executable", "Code Signing", "Release Checklist"). Asset/icon regeneration in `docs/BRANDING.md`. Quick mental model:

1. Bump `src/__version__.py` (single source of truth — `build/windows/build.py` reads from it).
2. Update `CHANGELOG.md`, commit.
3. Build + sign from a **non-elevated shell** with the eToken plugged in: `python build/windows/build.py`.
4. Test the installer in `release/`, including UIAccess against an elevated shell.
5. `git tag vX.Y.Z && git push origin main && git push origin vX.Y.Z`.
6. **Public repo for binaries**: `gh release create vX.Y.Z release/Alpha-OSK-Setup-X.Y.Z.exe release/Alpha-OSK-Setup-X.Y.Z-requirements.lock.txt release/Alpha-OSK-Setup-X.Y.Z-sbom.cyclonedx.json --repo okstudio1/alpha-osk-releases ...`. The `--repo` flag is mandatory — source repo is private and the auto-updater can't see private releases. Upload the lockfile **and** the CycloneDX SBOM as release assets alongside the installer (see *Dependency Lockfile & SBOM* below).
7. **Track downloads**: `python scripts/downloads.py` prints per-release and total download counts via `gh api`. Includes auto-updater fetches, so it's a directional number rather than unique-install count.

The eToken-non-elevated requirement is the single most common build trap: SafeNet exposes the cert to the user session only, so elevated shells get "Cannot find certificate."

### Dependency Lockfile & SBOM (release-time)

Every release ships **two** dependency artefacts alongside the installer: a plaintext `pip freeze` lockfile (human/pip-friendly, reproducible install) and a CycloneDX 1.6 SBOM (machine/scanner-friendly, supply-chain compliance). Both are emitted unconditionally on every build (Windows and Linux) — even on `--skip-build`, since bumping the version is what `--skip-build` is for and the filenames encode the version.

**The lockfile** (`build/{windows,linux}/build.py::freeze_lockfile`). Runs `pip freeze --all` against the build venv and writes `release/Alpha-OSK-Setup-{version}-requirements.lock.txt` (Windows) or `release/Alpha-OSK-{version}-linux-requirements.lock.txt` (Linux). Pip-installable record of every Python package + exact version. `pip install -r <lockfile>` into a fresh venv recreates the build environment. Honest answer to "what shipped in version X.Y.Z?" — no tooling required to read it.

**The SBOM** (`build/{windows,linux}/build.py::emit_sbom`). Runs `python -m cyclonedx_py environment --of JSON --sv 1.6 --output-reproducible -o ...` and writes `release/Alpha-OSK-Setup-{version}-sbom.cyclonedx.json` (Windows) or `release/Alpha-OSK-{version}-linux-sbom.cyclonedx.json` (Linux). CycloneDX 1.6 JSON containing per-component name, version, PURL (`pkg:pypi/<name>@<version>` — globally unique identifier the whole ecosystem agrees on), license expression where the package's metadata declares one, and integrity hashes. `--output-reproducible` strips time/random fields so two builds of the same env produce byte-identical SBOMs. Soft-fails (returns None + warning) if `cyclonedx-bom` isn't installed in the venv — dev builds without the package still produce a working installer, they just skip the SBOM. Production release builds pull it in via `requirements-dev.txt`. About 100 KB at the current dep set (80 components for the Python side).

**Why ship both.** The lockfile is for the human reading a release page; the SBOM is for the security scanner / procurement tool / compliance system. Same packages, different surface. Both are ~100 KB combined — trivial relative to the 85 MB installer, no reason not to.

**Worker side.** `backend/cf-worker/package-lock.json` is committed alongside `package.json` so Wrangler / TypeScript / `@cloudflare/workers-types` versions are deterministic between local and CI. `node_modules/` is in `.gitignore` — the lockfile is the source of truth, run `npm install` (or `npm ci` in CI) to materialise it. A second SBOM (`cf-worker-sbom.cyclonedx.json`) is generated by `npm run sbom` (which calls `@cyclonedx/cyclonedx-npm`) and **auto-fires before every `npm run deploy`** via the `predeploy` script — so every worker deploy has a fresh SBOM next to it. The SBOM file itself is in `.gitignore` (regenerable from the lockfile any time); commit a copy with the deploy if you want a permanent audit trail. The worker SBOM is about 470 KB (209 components — npm dep trees are deep).

**CI-time CVE scanning.** `.github/workflows/ci.yml` has an `osv-scan` job pinned to `google/osv-scanner-action@9a498708959aeaef5ef730655706c5a1df1edbc2` (v2.3.8) that reads both lockfiles (`requirements-dev.txt` + `backend/cf-worker/package-lock.json`) and queries the OSV database on every push/PR. **Merges are gated** (`fail-on-vuln: true`): any CVE in either lockfile fails CI. The earlier known noise (six Wrangler-3.x findings: one moderate esbuild, five medium-to-high undici) was resolved by upgrading the worker to Wrangler 4.x, which ships clean esbuild and miniflare 4. The Python side had one transitive lxml CVE (GHSA-vfmq-68hx-4jfw, fixed in 6.1.0) coming through cyclonedx-bom; it's pinned away via `lxml>=6.1.0` in `requirements-dev.txt`. If a new advisory lands that we genuinely cannot fix before the next push, quarantine it with an `osv-scanner.toml` ignore entry rather than flipping `fail-on-vuln` back to false globally. Findings surface in the job's annotations / summary; SARIF upload to the Security tab is **disabled** (`upload-sarif: false`) because the source repo is private and GitHub Advanced Security (required for code scanning on private repos) is not enabled. Flip `upload-sarif: true` if/when GHAS is enabled or the repo goes public.

**When to bump the toolchain.** `cyclonedx-bom>=7.0.0` is pinned in `requirements-dev.txt`. `@cyclonedx/cyclonedx-npm` follows the worker's `^` range. The action's pinned SHA in `ci.yml` needs an occasional bump — Dependabot doesn't yet auto-bump reusable-workflow refs reliably, so it's a manual `gh api repos/google/osv-scanner-action/releases/latest --jq .tag_name` + update the pinned SHA. Both action and tool spec-version should stay aligned (we pin CycloneDX spec to 1.6 in both Python and npm calls).

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

(IDE prediction-pill duplication is now handled by auto-compat. `_COMPAT_PROCESS_NAMES` covers VS Code + Monaco forks (`code.exe`, `code - insiders.exe`, `cursor.exe`, `windsurf.exe`, `codium.exe`, `code-oss.exe`, `positron.exe`, `trae.exe`) and the JetBrains family (`idea64.exe`, `pycharm64.exe`, `webstorm64.exe`, `phpstorm64.exe`, `clion64.exe`, `goland64.exe`, `rider64.exe`, `rubymine64.exe`, `datagrip64.exe`, `dataspell64.exe`, `studio64.exe`, `studio.exe`). Both groups intercept keystrokes for completion/snippets/multi-caret in ways that break suffix-only insertion. Match on exe basename, **not** window class — `Chrome_WidgetWin_1` (Electron) and `SunAwtFrame` (JetBrains) are shared with too many unrelated apps. Visual Studio (`devenv.exe`), Sublime, and Eclipse were considered but left out: their interception is opt-in / popup-style rather than always-on, and the BackSpace-flicker path running unnecessarily isn't free. Add them if reports come in.)

## Things to Watch Out For

- `Main.qml` is large (~1300 lines). The keyboard rows are data-driven from `keyboard.getLayoutRows()`.
- `keyboard_bridge.py` is the biggest Python file (~1000 lines). It handles everything: keys, modifiers, context, predictions, settings, privacy mode.
- Window flags are critical — the keyboard must never steal focus from the user's app. See `_apply_window_flags()` in `keyboard_app.py`.
- On Windows, `WS_EX_NOACTIVATE` is set via Win32 API (not just Qt flags).
- Key spacing and sizing are calculated dynamically from window width — see `keyW`, `keyH`, `keySpacing`, `layoutFixedPixels` properties in Main.qml.
- The title bar has six buttons (left to right): update (↓, only when an update is pending), **Learning / Paused** (privacy mode — text label that shows the current state, with red border + dark red bg in the Paused state; used to be a play/pause icon but the media-player metaphor read as "is something playing" rather than "is the keyboard learning". Both labels are -ing/-ed state words — not imperatives — so the button reads as "this is what's happening", and the hover tooltip says what clicking will *do*. Button is sized for the longer label so toggling doesn't reflow the title bar), ⟲ (clear suggestion context — calls `keyboard.resetContext()` to wipe `_current_word` / `_sentence_buffer` / `_context_buffer` and re-emit empty predictions, with a 1.4 s "Context cleared" toast for feedback; the auto app-switch reset misses tab changes inside a single hwnd so this is the manual escape hatch), ⚙ (settings), minimize, close. Each one shows a tooltip on hover (400 ms delay, matches the prediction-pill tooltip in the same file); the privacy tooltip says what *clicking* will do ("Pause learning" / "Resume learning") so the label-vs-action ambiguity is resolved before the click. Help is now Settings → Data & Privacy → Help & Shortcuts; the language-model dashboard is Settings → Your Language Model → Open Dashboard.
- Predictions clear when the user switches apps — monitored via a 250 ms poll in `keyboard_bridge.py::_get_foreground_window_id`. Windows uses `GetForegroundWindow()` via ctypes; X11 shells out to `xdotool getactivewindow`. Wayland is a no-op (compositors don't expose focused window to unprivileged clients). QML `onActiveChanged` doesn't fire reliably with `WS_EX_NOACTIVATE` on Windows, so Python handles it on both platforms. Full context reset on app switch (`_current_word`, `_context_buffer`, `_sentence_buffer`).
- Prediction selection uses **suffix-only typing** — if the user typed "hel" and picks "hello", only "lo " is sent. No Backspace (empties Slack compose), no Shift+Left (doesn't work in terminals). Falls back to `replace_text()` only when the prediction doesn't match the typed prefix (e.g. casing diff "iph"→"iPhone"). `replace_text()` is implemented on both platforms: Windows sends the whole Shift+Left-then-type sequence in one `SendInput`; Linux chains N `shift+Left` chords into a single `xdotool key` invocation then a separate `xdotool type`, or frames `ydotool key --key-down shift` / `--key-up shift` around N Left presses on Wayland.
- **Shutdown ordering matters** — `keyboard_app.py` wires `aboutToQuit` to run `savePredictionModel`, `saveAnalytics`, then `bridge.shutdown()` in that order. `shutdown()` stops `_password_timer` and `_foreground_timer` so a final `timeout` can't run against a half-torn-down predictor, **and** releases any sticky Ctrl/Alt/Win that was still "active" so the OS doesn't see a phantom-held modifier after quit. Any new long-lived QTimer in `KeyboardBridge` should also be stopped there.
- **Linux key synthesis is synchronous** — `src/platform/linux.py` wraps every `xdotool`/`ydotool` invocation in `subprocess.run` (not `Popen`). Ordering between `keydown` / chord / `keyup` must be preserved; non-blocking subprocesses race and leave modifiers stuck. If you add a new send path, use the module-level `_run()` helper. Windows (`SendInput` via ctypes) has no analogous concern — events are atomic.
- **Windows subprocess calls must pass `CREATE_NO_WINDOW`.** Any `subprocess.run` / `subprocess.Popen` that invokes a console-subsystem child (powershell.exe, cmd.exe, console-mode python.exe, signtool.exe, certutil.exe, etc.) without `creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)` pops a console window. From a GUI parent with no attached console (frozen Qt build, the live OSK, Cursor / VS Code task runner, post-elevation re-spawn), that window can be visible and on some hosts persists until the user closes it manually. **The rule of thumb**: if you pass `capture_output=True` (or otherwise suppress the child's stdout) and the child is a console-mode binary, you also need `CREATE_NO_WINDOW`. Visible-output sites (`build_pyinstaller`, `build_nsis_installer`, `sign.py::sign_file` with `capture_output=False`, `check.py`'s actual ruff/mypy/pytest runs, `run.py::run_keyboard`, the pip install path) are deliberately untouched so their output still streams to the terminal. **Known-fixed sites** (look here before adding a new one): `updater.py::_verify_signature` (PowerShell sig check on every auto-update), `platform/windows.py::create_shortcut` (PowerShell COM call to make Start Menu / Desktop / Startup `.lnk` files), `check.py::_have_module` (capability probe with DEVNULL), `run.py::check_dependencies` (PySide6 import check with `capture_output=True`), `build/windows/build.py::check_pyinstaller` (`python -m PyInstaller --version`), `build/windows/build.py::check_certificate` (`certutil -store`), `build/windows/build.py::freeze_lockfile` (`pip freeze`), `build/windows/build.py::emit_sbom` (`python -m cyclonedx_py environment`), and `build/windows/sign.py::verify_file` (`signtool verify` -- this one ran once per signed artefact, so each release build produced two blank windows at the end before the fix). Detached spawns (`_spawn_relauncher`, `_launch_new_osk`, non-Windows `_launch_installer`) already OR `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`, which suffices on its own. Linux is unaffected. New Windows subprocess paths: pass `creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)` (portable: 0 on POSIX, 0x08000000 on Windows). For detached spawns: OR in `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`.
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
- **Modifier+punctuation chords need VK/keysym translation, not Unicode injection.** Ctrl+-, Ctrl+=, Ctrl+/, etc. go through `_send_key` (not `_send_text`) so the chord wraps a real virtual-key event. On Windows `_resolve_vk` only maps A-Z/0-9 directly; for punctuation `send_key` falls back to `VkKeyScanW` to find the layout-correct VK + shift state (so `+` on US layouts becomes Shift+VK_OEM_PLUS without hardcoding OEM codes). On Linux, `_CHAR_TO_KEYSYM` rewrites `-` → `minus`, `=` → `equal`, etc. before chord assembly so xdotool sees the canonical `ctrl+minus` instead of the malformed `ctrl+-` (xdotool's chord parser uses `+` as the separator and expects keysym names). Without these translations the chord goes via Unicode injection (Windows) or breaks the chord parser (Linux), and the target app's shortcut handler (which listens for `WM_KEYDOWN(VK_OEM_*)` or X keysyms, not `WM_CHAR`) never fires. So zoom, comment toggle, settings shortcuts silently don't work. New chord paths must route through `send_key`, not `send_text`.
- **Windows `send_text` uses scancode mode for ASCII, UNICODE only as a fallback.** `KEYEVENTF_UNICODE` synthesises `WM_KEYDOWN(VK_PACKET=0xE7)` followed by `WM_CHAR`. Apps that filter `VK_PACKET` or read raw scancodes never see clicked letters: confirmed broken in Blender (GHOST keyhandler keys off the real VK / scancode for shortcuts and viewport ops), VirtualBox (kernel-mode keyboard filter forwards by scancode), and most DirectInput games. The Windows on-screen keyboard works in those apps because it uses `KEYEVENTF_SCANCODE`, which produces a normal `WM_KEYDOWN(VK_X)` derived from the scancode just like a physical keypress. So `send_text` (and the typed portion of `replace_text`) now tries `_make_char_scancode_events` first per char; if it returns `None`, the char falls through to `_make_unicode_events`. Resolution path: `VkKeyScanW(char)` for VK + layout shift state, `MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)` for scancode, `MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR)` with bit 31 to detect dead keys. Caps Lock state is folded into `needs_shift` for letters via `GetKeyState(VK_CAPITAL)`, so a clicked lowercase `a` types `a` even when the OS Caps Lock LED is on. The fall-back conditions (each silently routed to UNICODE for that one char) are: non-ASCII (>= U+0080), unmappable on the active layout, AltGr / Ctrl-required chord, dead-key trigger, and the corner case where Shift is *physically* held but the char doesn't need shift (we can't safely release a key the user is holding). `_make_char_scancode_events` itself wraps with a synthetic Shift press/release when needed, but skips the wrap when Shift is already held by the user or by sticky-modifier state, otherwise the trailing release would unbalance the user's held key. Don't "simplify" this back to pure UNICODE for character input. Blender / VirtualBox / Resolve / etc. silently break again. Coverage in `tests/test_platform.py::TestWindowsScancodeDispatch`.
- **`pressKey` lowercases its input — use `pressKeyLiteral` to type a verbatim character.** `pressKey(key)` applies shift / caps-lock case normalization (`key.upper()` if shift/caps, else `key.lower()`), so passing `'A'` from QML when shift is off gets you `'a'`. The right-click → shifted-variant feature hit this exact bug — the QML handler had already chosen `'A'` and pressKey turned it back. The fix is `pressKeyLiteral(char)`, which types the character as-is. Both slots delegate to `_press_char(key, literal)`; if you add another path where QML has already resolved the final character (e.g. the future long-press alternates picker), use `pressKeyLiteral` and don't second-guess case from Python.
- **`_auto_space_pending` gates the punctuation-spacing cleanup.** When the user types `, . ; : ! ? ) ] }` after a trailing space, we only BackSpace away the space if *we* auto-inserted it (after a prediction click or after `. , ; : ! ?` with auto-space-after-punctuation on). The flag is set at every auto-space site and cleared on every other keystroke (any non-punctuation char, any special key including manual space). User-typed spaces are preserved verbatim — undoing them produced a visible BackSpace flicker after the user's own keystroke and in some apps (rich-text editors, web fields) clobbered selection state and undo history. New auto-space sites must set the flag; new `pressKey` / `pressSpecialKey` paths inherit the clear-on-every-keystroke contract by going through `_press_char` / `pressSpecialKey`.
- **`_context_buffer` mirrors the on-screen text — Backspace must trim it.** `pressSpecialKey("backspace")` always sends one BackSpace to the OS. When `_current_word` is non-empty, we also pop one char off `_current_word`. When `_current_word` is empty, we pop one char off `_context_buffer` instead (added in this commit). Without that second branch, a stale `.` from an earlier sentence stayed in the buffer after the user wiped the screen, and the next prediction call computed `sentence_start = trimmed.endswith(".")` on `"Hello."` and surfaced capitalized candidates on what looked like an empty document. The sentence-start formula itself is correct (`bool(ctx) and ctx[-1] in ".!?"`); the bug was the stale buffer. If you add another keystroke path that mutates the OS-side text without going through `pressSpecialKey`, mirror the same trim, or `_context_buffer` will drift out of sync again.
- **Prefix punctuation must be in the word-boundary set, or prediction clicks eat it.** `_press_char` treats `-`, `/`, `\`, `(`, `[`, `{`, `<`, `*`, `@`, `#`, `$`, `%`, `&`, `+`, `=`, `~`, `^`, `|`, `"`, `` ` `` as word boundaries: they go to the OS via `_send_text` (so they appear on screen), then `_current_word` resets to empty and the char is appended to `_context_buffer` *without* a trailing space. Without the boundary, the char accumulates into `_current_word` and breaks the prefix match in `pressPrediction`: typing `*hel` then clicking "hello" did `"hello".startswith("*hel") == False`, fell into the `replace_text` branch, and Shift+Left-selected 4 chars (including the `*`), overwriting the asterisk. Same bug for `@user`, `#tag`, `$var`, markdown `**bold**`, quoted `"hello`, backtick `` `code ``, and operator-prefix tokens like `key=value` (click pill for "value"). **Excluded deliberately**: apostrophe (`don't` is one token, see `learn_capitalization`) and underscore (snake_case identifiers in code). If you add a char here, it must also be one the user would *never* want as part of a learned token. `_` and `'` fail that test, but the rest of the set is unambiguous. Coverage in `tests/test_keyboard_bridge.py::test_prefix_punctuation_does_not_pollute_current_word` and `test_asterisk_prefix_prediction_click_keeps_asterisk`.
- **Compatibility Mode rewires prediction insertion to BackSpace + retype.** When effective compat mode is on, `pressPrediction` and the autocorrect-on-space branch in `pressSpecialKey` both stop using suffix-only `_send_text` and `replace_text` (Shift+Left+type). Instead they emit `BackSpace × len(_current_word)` followed by `_send_text(word + " ")`. The mode covers two categories of foreground app where suffix-only is unsafe: (a) remote-desktop clients (TeamViewer / RDP / VNC / AnyDesk / ...) whose forwarding pipelines drop, duplicate, and reorder keystrokes between the local OSK and the remote app — reported as "suggestions create chaos over team viewer" with scrambled words and "helhello"-style duplicates; and (b) IDEs with always-on keystroke interception (VS Code + Monaco forks, JetBrains family) whose IntelliSense / snippet expansion / multi-caret eats or reorders keystrokes inside the editor — reported as duplicated text in VS Code. In both cases the OSK's "the typed prefix is already on screen" assumption breaks. The independent-single-event keystroke sequence survives per-event glitches because no event depends on a prior event's outcome. The effective-state gate is `_in_compat_mode()` — `manual OR (auto_enabled AND auto_active)`. Three flags: `_compat_manual` (set by Settings → Smart Typing → Input → Compatibility Mode = "Always On" — force-on override, default off), `_compat_auto_enabled` (set by the same picker = "Auto" — default ON), and `_compat_auto_active` (driven by `_check_foreground_window` calling `_window_needs_compat_mode(hwnd)` on every 250 ms poll). Auto-detect default ON is deliberate — the user reported the bugs *before* finding the manual toggle, so the keyboard should just do the right thing without requiring a discovery step. If you add a third path that depends on locally-tracked state matching what the foreground app actually sees, gate it on `_in_compat_mode()` the same way. **Don't shrink `_COMPAT_PROCESS_NAMES` because scancode-mode `send_text` now reaches more apps.** Scancode mode fixed the *character-typing* path for raw-input apps (Blender, VirtualBox, DirectInput games). It does not fix the *prediction-pill insertion* path that compat mode covers. The IDE category in particular is unchanged: VS Code / JetBrains were already receiving keystrokes under UNICODE injection (the duplication symptom proved it), and the underlying issue is IntelliSense / snippet / multi-caret reacting to incoming keystrokes inside the editor, which scancode does not change (and arguably makes more reliable for the IDE handler, which is the opposite of what we want). The retype path inside compat mode does pick up scancode dispatch automatically since it goes through `send_text`, which is a small reliability win for remote-desktop compat mode but not a reason to remove anything from the list. **Legacy setting keys** `savedRemoteCompatMode` / `savedRemoteCompatAuto` from earlier releases are migrated once at startup by `_migrate_legacy_compat_settings()` in `keyboard_app.py`; the bridge slot names changed too (`setRemoteCompatMode` → `setCompatMode`, `setRemoteCompatAuto` → `setCompatAutoDetect`).
- **Compat-mode window detection is whitelist-based and platform-specific.** `_window_needs_compat_mode(hwnd)` lives module-level in `keyboard_bridge.py`. Detection runs only on Windows; non-win32 platforms return False unconditionally (the auto-active flag stays False, but the manual toggle still works). Two-pass: first match the window class (`GetClassNameW`) against `_COMPAT_WINDOW_CLASSES` (used for remote-desktop clients which expose distinctive class names), then fall back to the owning process exe basename (`QueryFullProcessImageNameW`) against `_COMPAT_PROCESS_NAMES` (used for IDEs — Electron's `Chrome_WidgetWin_1` and JetBrains' `SunAwtFrame` are too broad to match by class — and as a safety net for remote tools). Both are exact-match frozensets so unrelated apps cannot spuriously trigger compat mode (a fail-positive would cost the user the chat-composer-friendly suffix-only path; a fail-negative just means the manual toggle is still available). Adding new tools means appending to one or both sets — keep entries narrow (avoid catch-all class names like `Chrome_RenderWidgetHostHWND` or generic exe names that other apps use). The whole function is wrapped in `try / except (OSError, AttributeError)` so any ctypes failure bails to False rather than throwing on the 250 ms timer thread.
- **Backspace into a completed word must rehydrate `_current_word`.** After the `_context_buffer` trim above, the bridge checks whether the new tail is mid-word (no trailing whitespace) and, if so, calls `_rehydrate_current_word_from_context()` to move the trailing partial word back into `_current_word`. Invariant: "the word currently being edited lives in `_current_word`, the rest lives in `_context_buffer`." Without rehydrate, the partial word stranded in `_context_buffer` while `_current_word` was empty broke `pressPrediction`'s suffix-only insertion path: the case `not self._current_word` fired even though the user *was* mid-edit, so clicking a prediction emitted the FULL word alongside the on-screen partial — "backspac" + clicked "backspaces" = "backspacbackspaces". Any future Backspace-adjacent code (cursor-move special keys that effectively delete a char, popups that drain a partial word back to the OS) must preserve this invariant.
- **`KeyButton.qml` repeat timer runs a three-phase state machine.** The `repeatTimer` walks through `phase 0` (pre-warmup, scheduled at `repeatDelay`) → `phase 1` (grace, scheduled at `warmUpGrace`) → `phase 2` (repeating, scheduled at `repeatInterval` with `repeat=true`). Phase 0 fires at `repeatDelay` and only transitions; it does *not* emit a keystroke. Phase 1 fires at `warmUpGrace` (300 ms default), emits the first auto-repeat keystroke, and transitions to phase 2. Phase 2 emits each tick at `repeatInterval` cadence. Without the grace phase, the boundary between "one tap" and "tap that fires twice" was `repeatDelay + repeatInterval` (~620 ms), and slow-motor users systematically tipped past it on backspace and felt it as "Backspace sometimes sends 2". The grace widens the boundary to `repeatDelay + warmUpGrace` (~800 ms) without slowing down bulk-delete once auto-repeat is engaged. Any press shorter than `repeatDelay + warmUpGrace` produces exactly one keystroke. The `phase` field must be reset to `0` wherever the timer is stopped (`onReleased`, `onCanceled`, `onContainsMouseChanged`); otherwise a subsequent press would skip a phase and resume mid-cycle.
- **Hold-to-repeat timing is user-tunable via Settings → Smart Typing → Input.** `appSettings.savedRepeatDelay` and `savedRepeatInterval` (defaults 500 / 120 ms) flow through `root.repeatDelay` / `root.repeatInterval` to every `KeyButton` with `enableRepeat: true` — the Backspace key in `Main.qml` and all seven repeat-enabled keys in `NavigationPanel.qml`. If you add a new repeat-enabled key, pass `repeatDelay: root.repeatDelay` and `repeatInterval: root.repeatInterval` (or the equivalent from a wrapping component's properties) so the user's setting takes effect there too — hardcoded values defeat the entire point of the setting.

## Right-Click for Shifted Character

Right-click on a char key types its shifted variant without flipping the sticky shift state — `1` → `!`, `,` → `<`, `a` → `A`. Modifier and special keys are deliberate no-ops. Toggle in *Settings → Smart Typing → Input → "Right-Click for Shifted Character"* (default ON; left-click is unaffected whether on or off). Implementation:
- `KeyButton.qml` exposes a `keyRightPressed` signal. The `MouseArea` accepts both buttons; the right-button branch in `onPressed` returns *before* the auto-repeat timer starts so right-click is always a one-shot. Press visuals + ripple still fire — same tactile feedback as a left-click.
- `Main.qml` per-key `onKeyRightPressed` resolves the output: prefer `kd.shifted` from the layout JSON (covers `1`→`!`, `,`→`<`); fall back to `kd.key.toUpperCase()` for letters; otherwise no-op.
- The handler routes through `keyboard.pressKeyLiteral(rch)`, **not** `pressKey` — the latter would lowercase the chosen `'A'` back to `'a'` (see the `pressKey` watch-out above).

The companion long-press → accents feature is **not** implemented — see `docs/LONG_PRESS_ALTERNATES.md` for the design and the reason it's deferred (press-on-release timing change is hostile to slow-motor users until we have a way to scope the latency to keys with alternates).
