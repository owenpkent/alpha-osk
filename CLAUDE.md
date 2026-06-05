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

## C++ / Qt6 Rewrite (`cpp-rewrite` branch)

There is a native **C++ / Qt6 port** of the backend on the `cpp-rewrite` branch.
It **reuses the QML UI (`qml/`) and data files (`data/`) unchanged** and rewrites
only the Python `src/` backend in C++ under `cpp/`. Motivation: drop the bundled
Python runtime (the PyInstaller build is ~85 MB and trips SmartScreen/AV), ship a
single native exe, native-speed prediction. The QML <-> bridge contract maps 1:1
onto a C++ `QObject` (`@Slot` -> `Q_INVOKABLE`, `Signal` -> Qt signal).

**Ported and verified:** app bootstrap + Win32 window flags, `SendInput`
synthesis, the full typing state machine, the n-gram + PPM + fuzzy prediction
stack (`rank` merge + autocorrect, reading the existing `ngram_model.json` /
`ppm_model.json`), swipe typing, key-click audio (Win32 `PlaySound`), and the
settings setter slots, snippets, vocabulary packs, password-field detection +
privacy auto-pause, and typing analytics. **Stubbed** (no-op so the reused QML
never calls a missing method): telemetry, auto-update, data backup, compat
auto-detect.

Build/run, toolchain, status table, and the C++ <- Python source map are in
[`docs/build/CPP_WINDOWS.md`](docs/build/CPP_WINDOWS.md); the in-tree quick
reference is [`cpp/README.md`](cpp/README.md). Build into `build-cpp/` (NOT
`build/`, which holds the committed Python packaging pipeline). When porting more
features, keep behaviour faithful to the Python source — the rest of this file is
the behavioural contract for both implementations.

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

Deep-dive design docs for each algorithm: `docs/architecture/FUZZY_RECOGNITION.md` (spatial model + tunable constants), `docs/architecture/PPM.md` (variable-order character model + PPMD escape), `docs/architecture/HYBRID_MERGING.md` (merge weights + validation + capitalization), `docs/architecture/SWIPE_TYPING.md` (shape-matching swipe decoder).

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

## Snippets (Quick-Insert Text)

User-defined quick-insert text: name, email, phone, address, signatures, canned replies. The user taps one to type it verbatim into the focused app, instead of typing it out and fighting prediction every time. Opened from a title-bar button (☰, next to Learning). Backend in `src/snippets.py`; UI is the floating `snippetsWindow` in `qml/Main.qml`.

### Data model and storage
Each entry is a `{label, value}` pair: `label` is the short text on the button (e.g. "Email"), `value` is the exact text typed when tapped. Persisted as `snippets.json` in the config dir (`%APPDATA%/alpha-osk/` Windows, `~/.config/alpha-osk/` Linux), saved synchronously on every mutation (atomic tempfile-then-rename), so there is no on-quit save path to wire up. On first launch the store seeds four pre-made empty labelled slots (Name / Email / Phone / Address); every field including the label is editable and deletable. Bounds: `MAX_SNIPPETS` 50, `MAX_LABEL_LEN` 40, `MAX_VALUE_LEN` 2000, file cap 1 MB. A corrupt, oversized, or empty file falls back to the seeded defaults rather than raising. Labels are collapsed to a single line; values keep newlines (a value may be a multi-line block like a mailing address).

### Insertion path
`KeyboardBridge.insertSnippet(index)` routes the value straight through `_send_text` (the same verbatim path swipe / predictions use). Snippets are full literal inserts, so unlike prediction pills there is **no** prefix matching, no autocorrect, and **no compat-mode BackSpace+retype** (that dance exists to replace a typed prefix, which a fresh insert doesn't have). Insertion is **not** blocked by privacy mode: privacy is about not *learning* from typing, and the user may need to drop their address into a sensitive form. After inserting, `_current_word` / predictions are cleared so the verbatim text (which may carry punctuation or newlines) can't corrupt the next prediction's prefix matching. `insertSnippet` is a no-op while edit mode is active (`_edit_mode_active`) so it can't fire while the user is editing a snippet field.

### Floating window (NOT a Popup)
`snippetsWindow` is a **separate top-level `Window`**, not a QML `Popup`. A `Popup` is clipped to its parent window's overlay, so it could never be dragged off the keyboard; a standalone `Window` floats anywhere on the desktop. It carries the same OSK flags as the main window (`Qt.Window | FramelessWindowHint | WindowStaysOnTopHint | WindowDoesNotAcceptFocus`). On Windows that Qt flag alone doesn't stop click-activation, so `keyboard_app.py::_wire_snippets_window` finds the window by `objectName: "snippetsWindow"` and re-applies `WS_EX_NOACTIVATE` (via the shared `_apply_windows_extended_styles`) on every `visibleChanged` (the native handle only exists once shown). Non-Windows is a no-op (X11/Wayland respect the Qt flag; macOS uses the app-wide Accessory policy). The header is a drag handle that moves the whole window freely with no clamp. First open centers it just above the keyboard; the dragged position persists **for the session only** (not across restarts yet — unlike the main window, which persists `savedWindowX/Y`).

### Editor UX (reuses the edit-mode plumbing)
The window has two views (an `editingIndex` switch: -1 list, >= 0 editor): the list (default) and a per-snippet editor with Label + Text fields. **Edit mode is only turned on while the editor is showing**, not for the whole window. This is critical: if it set `setEditMode(true)` on open, tapping a snippet in the list would be swallowed by edit-mode routing instead of inserting to the OS. In the editor, OSK keystrokes flow through the same `editKeyTyped` / `editSpecialPressed` signals the prediction-edit popup uses; an `editTarget` property ("label" / "value", set by tapping a field) picks which `TextField` receives them. Saving calls `setSnippet` and flashes the shared `editSavedToast`. Empty slots are never dead taps: tapping a slot with no value opens the editor directly instead of inserting.

### Bridge slots and signal
`getSnippets() -> QVariantList`, `insertSnippet(int)`, `setSnippet(int, str, str)`, `addSnippet()` (appends a blank "New" slot for the user to fill), `deleteSnippet(int)`, `moveSnippet(int, int)` (direction -1 up / +1 down). The `snippetsChanged(list)` signal is emitted after every mutation so the window re-queries and rebuilds its rows.

### Backup
`snippets.json` is in the Data Backup archive (`_MODEL_FILES` in `src/data_export.py`), replace-on-import like the model files. After an import, `KeyboardBridge.importUserData` calls `SnippetStore.reload_from_disk()` + emits `snippetsChanged` so the running session picks up the imported snippets without a restart. It is *not* encrypted: it's local user data on the user's own machine, same trust model as the prediction model.

## Data Backup (Export / Import)

User-facing "back up my data" feature so a user can move their model between machines. Lives in `src/data_export.py`; UI is *Settings → Data & Privacy → Data Backup* (above the Privacy section).

### What's in the archive
A normal `.zip` with `manifest.json` (schema version, app version, ISO-8601 UTC timestamp, file list, pack id list) plus `models/ngram_model.json`, `models/ppm_model.json`, `analytics.json`, `snippets.json` (user quick-insert snippets, see *Snippets* section), and `packs/<id>/...` for each imported pack. **`telemetry.json` is deliberately excluded** — copying the anon_id across machines would link contributions, which `docs/PRIVACY.md` and the telemetry consent docs explicitly promise not to do. A fresh anon_id is generated on the new machine when telemetry is re-enabled.

Settings (theme, layout, toggles, window size) are **not** in the archive. They live in the Qt settings layer (Windows registry / Linux config) and are quick to reconfigure manually; the irreplaceable bit is the prediction model. If a future release adds settings to the export, schema_version must be bumped and old-version import paths must still apply correctly.

### Import is *replace*, not *merge*
Imported files overwrite the corresponding files in the config dir; packs not in the archive are removed (the imported state is "the user's full snapshot at export time"). Before any overwrite, the current state is written to a timestamped rescue archive in `<config_dir>/exports/rescue-<ts>.zip` so the user can roll back by importing that file. Rescue export failures are logged but do not abort the import.

Model files are replaced via tempfile-then-rename so a partial write can't corrupt the existing file. After files are replaced, `HybridPredictor.reload_from_disk()` re-reads `ngram_model.json` / `ppm_model.json` and re-discovers packs; `TypingAnalytics.reload_from_disk()` re-reads lifetime counters. The user does not need to restart Alpha-OSK. Enabled-pack state is reset (packs come back disabled and the user re-enables what they want — this matches what would happen if they imported each pack one at a time on the new machine).

### Security hardening (don't loosen without re-reading the tests)
Both `inspect_export` and `import_user_data` validate every archive entry:
- Reject names with `..` components, absolute paths, drive prefixes (`C:`), or backslashes (zip-slip defence — Python's `Path` handles `..` natively but the explicit check is defence in depth and matches how `PackManager.import_pack` validates pack ids).
- Per-file uncompressed size cap (`_MAX_FILE_BYTES`, 75 MB), total uncompressed cap (`_MAX_TOTAL_UNCOMPRESSED`, 500 MB), archive-on-disk cap (`_MAX_ARCHIVE_BYTES`, 200 MB). Caps trip on file-size metadata before any bytes are extracted, so a forged 50 GB entry is refused without OOM.
- Extraction is allow-list, not deny-list. Only members matching the exact expected paths (`models/ngram_model.json`, `models/ppm_model.json`, `analytics.json`, `packs/<sanitised-id>/<allowed-filename>`) are written to disk. A hand-edited archive that snuck `telemetry.json` or `../../boot.ini` in past the manifest check is silently ignored at extraction time. Pack ids are re-matched against `_PACK_ID_RE` on import.
- Schema-version forward-compatibility: if the manifest's `schema_version` exceeds `SCHEMA_VERSION`, import is refused with a "upgrade Alpha-OSK first" message rather than half-applied.

Regression coverage: `tests/test_data_export.py::TestInspect::test_zip_slip_rejected`, `test_absolute_path_rejected`, `test_future_schema_rejected`, `test_oversize_entry_rejected`, plus `TestImport::test_telemetry_not_restored` (a hand-crafted archive cannot smuggle telemetry.json past the extractor).

### Bridge slots
- `getDefaultExportDir() -> str` — Documents folder via QStandardPaths, falls back to home.
- `getSuggestedExportName() -> str` — `Alpha-OSK-Export-<YYYY-MM-DD-HHMMSS>.zip`.
- `exportUserData(dest_path) -> str` — empty string on success, error message otherwise. Calls `_predictor.save()` + `_analytics.save()` first so the export reflects the running session.
- `inspectUserExport(src_path) -> dict` — `{ok: True, files, pack_ids, app_version, exported_at, bytes, schema_version}` or `{ok: False, error}`. QML uses this to show a preview before the user commits.
- `importUserData(src_path) -> str` — empty string on success, error message otherwise. Calls `reload_from_disk` on the predictor + analytics, clears `_current_word` / `_context_buffer` / `_sentence_buffer`, emits empty predictions.

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

Drag the mouse across letters to type a whole word in one gesture, like Gboard. Off by default; toggle in *Settings → Smart Typing → Suggestions → Swipe Typing*. Design doc: `docs/architecture/SWIPE_TYPING.md`.

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
| | Input | Right-click shift, key preview popup, Compatibility Mode picker, repeat delay & interval |
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

Full notes in **`docs/architecture/PREDICTION_NOTES.md`** (the "unified system" framing, fragment filter + repetition gate, autocorrect thresholds, reinforcement-on-click, backspace-as-negative-signal, the prioritized future-work gaps, and reference implementations). Per-algorithm deep dives: `FUZZY_RECOGNITION.md`, `PPM.md`, `HYBRID_MERGING.md`, `SWIPE_TYPING.md`.

Load-bearing defaults to keep in mind: **space-time autocorrect is OFF by default** (`KeyboardBridge._autocorrect_enabled = False` — corrections surface as pills, never silent overwrites); the autocorrect gate skips typings under 3 chars and runs an absolute + relative threshold so deliberate typings ("thru", "lol") survive; n-gram scoring is linear interpolation in probability space (λ = 0.5/0.3/0.2); unknown words promote into `user_vocab` only after 3 sightings (pill clicks gated the same way).

## Modular Layouts

Design doc at `docs/architecture/MODULAR_LAYOUTS.md`. Inspired by Octavium's (`C:\Users\Owen\dev\Octavium`) Layout/KeyDef data model. Four levels of modularity: (1) Built-in JSON layout packs (video editing, gaming, streaming). (2) User-created layouts via editor. (3) Panel composition — snap independent panels (QWERTY, numpad, macros) into a grid. (4) App-aware auto-switching based on foreground window.

Action types: `char`, `special`, `hotkey`, `text`, `macro`, `launch`, `layout`, `midi`. Profiles bundle layout + theme + window position + auto-switch rules.

## Auto-Update

Implemented in `src/updater.py`. Flow walkthrough, threat model + defences table, and the per-defence rationale all live in `docs/build/AUTO_UPDATE.md`. Release checklist is in `docs/build/WINDOWS.md`.

> ⚠️ **Releases live in a separate public repo** — `okstudio1/alpha-osk-releases`. The updater's API URL is hard-pinned to that repo, so `gh release create` must always pass `--repo okstudio1/alpha-osk-releases`. (Historical note: the source repo `owenpkent/alpha-osk` was private until 2026-05-16; the split was originally a private/public boundary, and is now preserved because the pinned updater URL relies on the releases repo being its own canonical source-of-truth.)

Version source of truth is `src/__version__.py`. The release-asset filename **must** match `Alpha-OSK-Setup-{version}.exe` exactly — the updater rejects anything else. User-facing toggle: *Settings → Data & Privacy → Updates → "Check for updates on startup"* (persisted as `appSettings.savedAutoCheckUpdates`).

### Update progress UI

Full walkthrough (the four pieces from "user clicks install" to "new keyboard appears", plus the v1.0.19 file list) is in `docs/build/AUTO_UPDATE.md`. The non-obvious bits to remember: **never expose the download URL to QML** (the bridge only emits primitive ints); the pre-install toast sleeps `_PRE_INSTALL_TOAST_DWELL_S` (1.8 s) in the worker so it paints before the installer's taskkill; the relauncher splash is a `QTimer` state machine with an indeterminate `QProgressBar` (NSIS silent install has no real percentage); `_run_headless` is preserved as the test target and no-display fallback; and `_is_dev_target()` routes `python`/`pythonw` straight to headless so dev runs don't hang waiting for an exe mtime that never changes.

## Accessibility Ecosystem

Design doc at `docs/roadmap/ECOSYSTEM.md`. Alpha-OSK is part of a four-tool adaptive input platform:

| Tool | Repo | Output |
|------|------|--------|
| **Alpha-OSK** | `C:\Users\Owen\dev\alpha-osk` | Keystrokes (SendInput) |
| **MacroVox** | `C:\Users\Owen\dev\MacroVox` | Text (Deepgram STT → clipboard) |
| **Octavium** | `C:\Users\Owen\dev\Octavium` | MIDI (virtual piano/pads) |
| **Nimbus** | `C:\Users\Owen\dev\Nimbus-Adaptive-Controller` | Joystick (vJoy/ViGEm) |

All four: same developer, same EV cert, PySide6/Qt (except MacroVox: Tauri), mouse-driven, accessibility-first. Integration phases: coexistence → launch/trigger → profile auto-switch → shared input layer → unified UI.

See also: `docs/roadmap/MACROVOX_INTEGRATION.md` (voice dictation), `docs/architecture/MODULAR_LAYOUTS.md` (custom layouts inspired by Octavium/Nimbus).

## Federated Learning

Design doc at `docs/roadmap/FEDERATED_LEARNING.md`. Not yet implemented — Phase 1 (local delta computation) is the next step.

## Opt-in Telemetry

Design: `docs/architecture/TELEMETRY.md`. User-facing privacy: `docs/PRIVACY.md`. Backend: `backend/cf-worker/` (Cloudflare Worker + D1).

**Off by default.** When enabled (Settings → Data & Privacy → Privacy → "Share anonymous usage stats"), the client sends a weekly POST containing nine integers: `anon_id`, `app_version`, `os`, `keystrokes`, `words`, `predictions`, `keystrokes_saved`, `minutes`, `sessions`, `prediction_offers`. These are exactly the lifetime counters already shown on the Analytics dashboard. **Never sent**: content, word frequencies, key frequencies, IP, hostname, or any per-session breakdown.

Files, endpoint config, anon_id lifecycle, submit cadence, and the worker schema are all detailed in `docs/architecture/TELEMETRY.md`. Load-bearing facts:
- **`DEFAULT_ENDPOINT` in `src/telemetry.py` is the empty string** — while empty the client silently no-ops every submit (consent toggle still works, no data leaves the machine). Set it per-build before shipping a telemetry-enabled release; the Windows checklist (`docs/build/WINDOWS.md` step 2a) gates on this.
- **anon_id is cleared on opt-out**, so re-opt-in gets a fresh UUID4 and prior contributions can't be linked. "Delete my contributed data" POSTs to `/v1/forget`. (This is why the Data Backup archive deliberately excludes `telemetry.json`.)
- **`TelemetryClient` is the source of truth for the consent flag** — `UnifiedSettingsPanel.qml` queries the bridge on mount; **don't** mirror it into `appSettings`.
- **Cadence**: weekly `QTimer` (1-hour tick, 7-day window check) plus `submit_on_quit()` from `shutdown()` (60 s anti-spam guard). All paths gated on `enabled AND endpoint AND anon_id`; failures retry `[5s, 30s, 120s]` then drop silently.
- **Privacy mode needs no special handling** — it already suppresses learning/tracking upstream, so password activity never enters the counters telemetry forwards.
- **Not telemetry**: auto-update version checks (GitHub Releases requests) and the planned federated-learning feature (its own opt-in + DP-noise design). Keep them conceptually separate.

## Building & Signing a Release (Windows)

Full step-by-step release checklist, signing details, troubleshooting table, and bundle-size notes are in `docs/build/WINDOWS.md` (sections "Building a Standalone Executable", "Code Signing", "Release Checklist"). Asset/icon regeneration in `docs/build/BRANDING.md`. Quick mental model:

1. Bump `src/__version__.py` (single source of truth — `build/windows/build.py` reads from it).
2. Update `CHANGELOG.md`, commit.
3. Build + sign from a **non-elevated shell** with the eToken plugged in: `python build/windows/build.py`.
4. Test the installer in `release/`, including UIAccess against an elevated shell.
5. `git tag vX.Y.Z && git push origin main && git push origin vX.Y.Z`.
6. **Public releases repo**: `gh release create vX.Y.Z release/Alpha-OSK-Setup-X.Y.Z.exe release/Alpha-OSK-Setup-X.Y.Z-requirements.lock.txt release/Alpha-OSK-Setup-X.Y.Z-sbom.cyclonedx.json --repo okstudio1/alpha-osk-releases ...`. The `--repo` flag is mandatory because the auto-updater hard-pins the API URL to that repo (see `src/updater.py::GITHUB_API_URL`). Upload the lockfile **and** the CycloneDX SBOM as release assets alongside the installer (see *Dependency Lockfile & SBOM* below).
7. **Track downloads**: `python scripts/downloads.py` prints per-release and total download counts via `gh api`. Includes auto-updater fetches, so it's a directional number rather than unique-install count.

The eToken-non-elevated requirement is the single most common build trap: SafeNet exposes the cert to the user session only, so elevated shells get "Cannot find certificate."

### Release artefacts (EULA, lockfile, SBOM, CVE scanning)

Reference detail moved to **`docs/build/RELEASE.md`**. The essentials:
- **Clickwrap EULA**: the NSIS installer shows a `MUI_PAGE_LICENSE` page (checkbox-gated) backed by `build/windows/LICENSE.rtf`; keep that RTF and the repo-root plaintext `LICENSE` in sync. Silent install (`/S`, auto-updater) bypasses it, so it only blocks the first interactive install.
- **Lockfile + SBOM**: every build emits a `pip freeze` lockfile *and* a CycloneDX 1.6 SBOM into `release/` (filenames encode the version), even on `--skip-build`. Upload both as release assets alongside the installer.
- **CI CVE scanning**: `.github/workflows/ci.yml` runs `osv-scanner` over both lockfiles with `fail-on-vuln: true`. A new advisory blocks every PR — fix the dep or quarantine with a time-boxed `osv-scanner.toml` entry; never flip `fail-on-vuln` off globally.

## macOS build (in progress)

Phase-1 platform support lives in `src/platform/macos.py`
(`MacOSKeySynthesizer` via `Quartz.CGEventCreateKeyboardEvent`) +
NSWindow tuning in `keyboard_app.py::_apply_macos_window_flags`
(float level, all-Spaces collection behavior, `hidesOnDeactivate=NO`).
`"win"` modifier maps to ⌘ Command. Config dir is
`~/Library/Application Support/alpha-osk/`. Build pipeline scaffolded
at `build/macos/` (PyInstaller `BUNDLE()` → `Alpha-OSK.app`, optional
`hdiutil` `.dmg`) but not yet exercised end-to-end. Code signing,
notarization, auto-update, and AXUIElement password detection are
explicit follow-up phases. **First-run gotcha:** macOS requires an
Accessibility TCC grant (System Settings → Privacy & Security →
Accessibility) before `CGEventPost` reaches other apps — without it
the OSK UI works but keystrokes silently no-op. Full plan + phase
breakdown + troubleshooting in `docs/build/MACOS.md`.

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

See `docs/build/LINUX.md` for deeper coverage (troubleshooting, AppImage
internals, spec customization).

## Git Conventions

Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`

## Community Files

The repo ships the standard GitHub community health files at the top level and under `.github/`:

- `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1. Reports go to owenpkent@gmail.com with subject `CONDUCT: alpha-osk`.
- `CONTRIBUTING.md` — dev setup, `check.py` pre-push gate, conventions, PR flow. Points new contributors at this file as the architecture map.
- `SECURITY.md` — private vulnerability reporting via the releases repo's GHSA form, email fallback.
- `.github/ISSUE_TEMPLATE/bug_report.yml` and `feature_request.yml` — form templates. `config.yml` disables blank issues and links to the security advisory + Discussions.
- `.github/pull_request_template.md` — summary, type, test plan, accessibility check.

If you change the security reporting flow, the CoC contact email, or the contribution gates, update both the relevant file and the cross-references in `CONTRIBUTING.md` / `bug_report.yml`.

## Known Issues

(IDE prediction-pill duplication is now handled by auto-compat. `_COMPAT_PROCESS_NAMES` covers VS Code + Monaco forks (`code.exe`, `code - insiders.exe`, `cursor.exe`, `windsurf.exe`, `codium.exe`, `code-oss.exe`, `positron.exe`, `trae.exe`) and the JetBrains family (`idea64.exe`, `pycharm64.exe`, `webstorm64.exe`, `phpstorm64.exe`, `clion64.exe`, `goland64.exe`, `rider64.exe`, `rubymine64.exe`, `datagrip64.exe`, `dataspell64.exe`, `studio64.exe`, `studio.exe`). Both groups intercept keystrokes for completion/snippets/multi-caret in ways that break suffix-only insertion. Match on exe basename, **not** window class — `Chrome_WidgetWin_1` (Electron) and `SunAwtFrame` (JetBrains) are shared with too many unrelated apps. Visual Studio (`devenv.exe`), Sublime, and Eclipse were considered but left out: their interception is opt-in / popup-style rather than always-on, and the BackSpace-flicker path running unnecessarily isn't free. Add them if reports come in.)

## Things to Watch Out For

The full list of implementation gotchas and invariants lives in
**`docs/architecture/GOTCHAS.md`** — read it before touching keystroke
synthesis, the prediction context buffers, window flags, or the build
pipeline. The highest-frequency traps, kept inline because they're the
easiest to reintroduce:

- **Window flags / focus**: the keyboard must never steal focus. `WS_EX_NOACTIVATE` is set via Win32 API on Windows (`_apply_window_flags()` in `keyboard_app.py`); `WindowDoesNotAcceptFocus` elsewhere.
- **Sticky modifier auto-release lives in two parallel blocks — keep them in sync.** `_press_char` and `pressSpecialKey` each end with their own Shift/Ctrl/Alt/Win release sequence (state flip + `release_modifier()` + change-signal emit, plus `_update_layer()` for Shift). New keystroke paths that branch off (autocorrect retype, pill insertion, edit-mode, macros) must mirror it. **Exception:** `pressSpecialKey` keeps Shift/Ctrl held on `_NAV_KEYS` (arrows/home/end/pageup/pagedown) so Shift+arrow selection and Ctrl+arrow word-jump persist across presses; Alt/Win still release.
- **Prediction insertion is suffix-only** (type just the unseen tail), falling back to `replace_text()` only on a prefix mismatch (casing). Compatibility Mode (`_in_compat_mode()`) rewires this to BackSpace + retype for remote-desktop clients and IDEs where suffix-only is unsafe.
- **`_context_buffer` / `_current_word` mirror the on-screen text.** Backspace must trim the buffer and rehydrate a mid-word tail back into `_current_word`; prefix punctuation must be in the word-boundary set or pill clicks eat it.
- **Windows uses scancode mode** for both `send_text` (ASCII) and chords/`hold_modifier` (UNICODE/`wVk`-mode only as a fallback) — required for Blender/VirtualBox/games and for Ctrl+V over TeamViewer/RDP.
- **`pressKey` lowercases its input** — use `pressKeyLiteral` when QML already resolved the final character (right-click shifted variant, etc.).
- **Invariants**: `NgramPredictor._user_total == sum(user_vocab.values())`; merge strategy default MUST stay `"rank"`; window height is content-bound (never persist/assign it); analytics metrics need both session and `_alltime_*` forms; Windows subprocess calls suppressing output need `CREATE_NO_WINDOW`.

## Right-Click for Shifted Character

Right-click on a char key types its shifted variant without flipping the sticky shift state — `1` → `!`, `,` → `<`, `a` → `A`. Modifier and special keys are deliberate no-ops. Toggle in *Settings → Smart Typing → Input → "Right-Click for Shifted Character"* (default ON; left-click is unaffected whether on or off). Implementation:
- `KeyButton.qml` exposes a `keyRightPressed` signal. The `MouseArea` accepts both buttons; the right-button branch in `onPressed` returns *before* the auto-repeat timer starts so right-click is always a one-shot. Press visuals + ripple still fire — same tactile feedback as a left-click.
- `Main.qml` per-key `onKeyRightPressed` resolves the output: prefer `kd.shifted` from the layout JSON (covers `1`→`!`, `,`→`<`); fall back to `kd.key.toUpperCase()` for letters; otherwise no-op.
- The handler routes through `keyboard.pressKeyLiteral(rch)`, **not** `pressKey` — the latter would lowercase the chosen `'A'` back to `'a'` (see the `pressKey` watch-out above).

The companion long-press → accents feature is **not** implemented — see `docs/architecture/LONG_PRESS_ALTERNATES.md` for the design and the reason it's deferred (press-on-release timing change is hostile to slow-motor users until we have a way to scope the latency to keys with alternates).

## Key Preview Bubble

A small bubble floats just above a key showing the character that was actually typed, the same "key preview" pattern phone keyboards use. It fires on **both** left- and right-click. The motivating case is right-click (it sends the shifted variant, and that glyph isn't always the one drawn on the key, so the preview confirms what reached the OS), but left-click previews every typed character too. Toggle in *Settings → Smart Typing → Input → "Show Key Preview Popup"* (default ON). It's a pure visual: there is no Python bridge, the setting is `appSettings.savedKeyPreview` mirrored into `root.keyPreviewEnabled` and restored on launch like any other Qt setting.

### Phone-style press/release timing
The bubble shows on press and hides on release, so during normal typing it's visible only for the tap duration (down to a floor), exactly like Gboard/iOS. It is **not** a fixed-dwell toast. The mechanics:
- `KeyButton.qml` emits a new `keyReleased()` signal from all three "press ended" paths: `onReleased`, `onCanceled`, and `onContainsMouseChanged` when the cursor drags off while pressed (the drag-off case is sometimes the only release signal we get under `WS_EX_NOACTIVATE`). The release emit in `onContainsMouseChanged` is guarded on `_visualPressed` so a pure hover-out doesn't fire it.
- `Main.qml` `showKeyPreview(item, ch)` maps the key's top-center into the overlay and calls `keyPreviewBubble.show()`; the per-key `onKeyReleased: root.hideKeyPreview()` dismisses it.
- The bubble (`keyPreviewBubble`, a `Popup` parented to `Overlay.overlay`, fixed 40x40 so the first show centers before content is measured) has two guard timers: `keyPreviewMinTimer` (110 ms visibility floor so a lightning-fast click still flashes long enough to register instead of opening and closing in the same frame) and `keyPreviewSafetyTimer` (1500 ms force-close in case a release event is dropped and `keyReleased` never arrives). `hide()` defers the close to the min timer when the press was shorter than the floor (via the `pendingHide` flag); otherwise it closes immediately.

Left-click previews use `keyBtn.displayText` (which already reflects shift/caps casing, so it matches what `pressKey` sends); right-click previews use the resolved `rch`. Both call sites are gated on `root.keyPreviewEnabled`. Modifier and special keys do not preview (a bubble over Shift or Backspace isn't "what it typed").
