# CLAUDE.md: Alpha-OSK AI Onboarding

Alpha-OSK is an AI-assisted, mouse-driven on-screen keyboard for Windows and Linux (macOS in progress). Users click QML keys to type into whatever app currently holds OS focus; a hybrid n-gram + PPM + fuzzy engine predicts words locally, with no LLM, no GPU, and nothing leaving the machine. It is an accessibility tool the owner depends on daily. This file is both the AI-onboarding doc and the human codebase map; the detailed reference sections below are authoritative project knowledge, not background.

## Key rules (non-obvious, cross-cutting)

- The keyboard must NEVER steal OS focus: `WS_EX_NOACTIVATE` on Windows (`keyboard_app.py::_apply_window_flags`), `WindowDoesNotAcceptFocus` elsewhere. Because our window cannot hold focus, route in-app text entry (prediction-edit popup, snippets editor, any future input slot) through `setEditMode(true)` plus the `editKeyTyped` / `editSpecialPressed` signals, never Qt focus. Set edit mode on open and clear it on close.
- Sticky-modifier auto-release logic is duplicated in `_press_char` and `pressSpecialKey` (state flip + `release_modifier()` + change-signal emit, plus `_update_layer()` for Shift). Keep both blocks in sync; new keystroke paths (autocorrect retype, pill insert, macros) must mirror it. `pressSpecialKey` deliberately keeps Shift/Ctrl held on `_NAV_KEYS` (arrows/home/end/pageup/pagedown).
- Linux `LinuxKeySynthesizer.hold_modifier()` MUST skip `win`/`super`: holding Super triggers a WM pointer grab that swallows every click, including clicks on the OSK itself. Do not "fix" it to hold Super. Windows still holds `VK_LWIN`.
- Pill-facing casing comes only from `KeyboardBridge._display_cased`, which mirrors every uppercase position of the typed prefix onto the pill, unconditionally (including fuzzy/autocorrect candidates). Auto-capitalisation is ONLY the "I" family in `ngram_predictor._always_capitalize`; do NOT reintroduce the removed three-tier proper-noun auto-cap as a default. Every pill emit site must route through `_display_cased`.
- Verbatim inserts (prediction pill, snippet, swipe word, autocorrect retype) go through `_send_literal_text`, never `_send_text` directly, and call `_release_sticky_modifiers()` first. A modifier held at the OS level rewrites the whole string: `_make_char_scancode_events` only knows not to *add* a redundant Shift wrap, it cannot cancel a standing hold, so "Hello" typed with Shift down arrives as "HELLO" and with Ctrl down every character arrives as a chord. `_send_literal_text` drops the holds for the duration and restores them, which is what keeps a right-click lock intact; the sticky release is separate and belongs to the caller. The single-character path in `_press_char` deliberately does NOT route through it (there the held Shift is what makes the keystroke uppercase).
- Prediction insertion is suffix-only (type just the unseen tail), falling back to `replace_text()` on a prefix/casing mismatch. Compatibility Mode (`_in_compat_mode`, matched on IDE/RDP exe basenames in `_COMPAT_PROCESS_NAMES`, never window class) rewires this to BackSpace+retype. `_context_buffer` / `_current_word` must always mirror the on-screen text; backspace must trim and rehydrate a mid-word tail.
- Import paths are security-critical: `PackManager.import_pack`, `data_export.import_user_data`, and `inspect_export` sanitise names, cap sizes, and use allow-list (not deny-list) extraction against zip-slip. Do NOT loosen without re-reading the regression tests (`tests/test_vocabulary_pack.py::TestImportPackSecurity`, and the slip/absolute-path/oversize/future-schema/telemetry cases in `tests/test_data_export.py`).
- Imported snippets have every `\r`/`\n` in a value flattened to a space (`data_export.py::_flatten_imported_snippet_newlines`) before the write; locally authored snippets (typed in the snippet editor) keep their newlines. `xdotool type` turns a literal newline into a real Return keypress, and an imported archive is untrusted, so the flatten applies only on import.
- Privacy/password mode must suppress learning AND `activeContextChanged` so no password characters or password-field context leak into predictions, telemetry, or the live visualization. Detection is Windows UIA COM + Win32 fallback (`src/platform/password_detect.py`) and Linux AT-SPI2.
- `pressPrediction` and `editPrediction` call `_check_password_field_sync()` before anything else, then gate `record_prediction_selected`, `learn_from_selection`, `learn_capitalization` and `set_capitalization` behind `if not self._privacy_mode`. The insertion itself (BackSpace+retype, `_send_literal_text`, suffix insert, `replace_text`) is deliberately NOT gated: the user tapped the pill, so the word must still reach the target app regardless of privacy mode. Any new pill-click or prediction-edit path must mirror both halves.
- Telemetry is OFF by default and `DEFAULT_ENDPOINT` in `src/telemetry.py` ships empty (silent no-op). `TelemetryClient` is the source of truth for the consent flag; do NOT mirror it into `appSettings`. The Data Backup archive deliberately excludes `telemetry.json`.
- Adding a setting requires the full 8-step wiring (see "Settings Panel Structure"): `Settings{}` savedFoo + root prop in `Main.qml`, prop + `SettingsToggle` in the correct sub-view of `UnifiedSettingsPanel.qml`, pass-through, `onSettingChanged`, optional `@Slot` on `keyboard_bridge.py`, and load in `Component.onCompleted`.
- Releases: `src/__version__.py` is the single source of version truth; publish to the separate `okstudio1/alpha-osk-releases` repo with an explicit `--repo` (the updater API URL is hard-pinned there); the installer asset name must be exactly `Alpha-OSK-Setup-{version}.exe`.
- The generated NSIS installer no longer relies on `InstallDirRegKey HKCU` (a dangling read of a user-writable registry value nothing in the build ever wrote, which the silent `/S` auto-update path honoured anyway); `updater.py::_install_target_dir()` computes the directory instead and every silent install passes an explicit `/S /D=<dir>`. NSIS requires `/D=` to be the last parameter on the command line and unquoted even when the path has spaces. Don't reorder or requote it.
- `run.py::ensure_admin_windows()` runs after dependency installation, not as the first statement in `main()`, so `pip install` never executes with an admin token; `--dashboard` never elevates at all. The repo tree is still user-writable, so this narrows the blast radius rather than closing it.
- Load-bearing invariants: merge-strategy default MUST stay `"rank"`; `NgramPredictor._user_total == sum(user_vocab.values())`; window height is content-bound (never persist or assign it); every analytics metric needs both a session and an `_alltime_*` form; Windows subprocess calls that suppress output need `CREATE_NO_WINDOW`.

## Stack & layout

- Python 3.10+ backend (CI runs 3.11, mypy targets 3.10), PySide6 (Qt6) + QML UI. No LLM/GPU. Key synthesis: ctypes SendInput scancode mode (Windows), `xdotool`/`ydotool` subprocess (Linux, NOT bundled), Quartz CGEvent (macOS, WIP).
- `src/keyboard_bridge.py` (central QML<->Python bridge: keys, modifiers, context, predictions), `src/keyboard_app.py` (launcher, window flags, auto-save on exit), `src/platform/` (OS abstraction + password detect), `src/prediction/` (hybrid engine), `qml/Main.qml` + `qml/components/`, `data/` (dictionaries/layouts/packs), `build/{windows,linux,macos}/`, `tests/` (pytest), `backend/cf-worker/` (Cloudflare telemetry worker).

## Build, run, test

- Run: `python run.py` (creates venv, installs deps, launches the keyboard).
- Test: `python -m pytest` (also `-k fuzzy`, or a single file like `tests/test_keyboard_bridge.py`).
- Pre-push gate, same four checks as CI (`ruff check`, `ruff format --check`, `mypy`, `pytest`): `python check.py` (fast, ~85s); `python check.py --full` adds the `--cov-fail-under=60` coverage gate (~3min, full CI parity). CI additionally runs `osv-scanner` over the lockfiles. Formatting is gated separately from linting because `ruff check` ignores layout; fix a format failure with `ruff format src/ tests/`.

## Conventions

- Format/lint: `ruff check src/ tests/` + `ruff-format` (line length 100, rules E/F/W/I); types: `mypy src/`. Pre-commit runs ruff `--fix` + ruff-format.
- Conventional commits (`feat:` / `fix:` / `docs:` / `refactor:` / `chore:` / `test:`), subject under ~72 chars. Never add AI co-author trailers.
- NO em dashes anywhere (code, docs, commit messages, PR descriptions): use commas, colons, parentheses, or periods. Comment only the non-obvious "why". Tests required for behaviour changes.
- Accessibility first: any change to keystroke timing, repeat interval, or visual feedback must stay usable for slow, imprecise motor input.

## When to ask / flag in PR

- Call out in the PR description any change to the prediction engine, the build/signing pipeline, or telemetry.
- Get alignment before: changing the security-reporting flow or CoC contact (update `SECURITY.md` / `CONTRIBUTING.md` / `bug_report.yml` cross-references together); changing the data-export schema (`SCHEMA_VERSION` bump + back-compat import paths); changing the telemetry payload or consent model; loosening any import-hardening check; or disabling the OSV `fail-on-vuln` gate.
- If you cannot decide which Settings category a new toggle belongs in, that is a UX smell: push back on the requirement before adding the setting.

---

## About the Owner

Owen is a wheelchair user with muscular dystrophy. Typing is hard - be proactive, make decisions, don't ask for confirmation on small things. Offer A/B/C choices so he can type one letter instead of explaining. This is an accessibility tool he actually needs.

## What This Is

Alpha-OSK is an AI-powered on-screen keyboard for Windows and Linux. Users click keys in the UI to type into other applications. It uses a hybrid prediction engine (n-gram + PPM + fuzzy recognition) - no LLM/GPU required.

## How to Run

```bash
python run.py          # Creates venv, installs deps, launches keyboard
python -m pytest       # Run tests (1009 tests)
```

## Architecture Overview

```
User clicks key (QML)
  -> KeyButton.qml sends signal
  -> Main.qml calls keyboard.pressKey() / keyboard.pressSpecialKey()
  -> keyboard_bridge.py (Python<->QML bridge)
    -> platform/*.py synthesizes keystroke (xdotool on Linux, SendInput on Windows)
    -> prediction engine updates suggestions
  -> predictions emitted back to QML via Signal
```

## Key Directories

| Path | What |
|------|------|
| `src/keyboard_bridge.py` | Central bridge: key handling, modifiers, context tracking, predictions |
| `src/keyboard_app.py` | App launcher: QML engine, window flags, auto-save on exit |
| `src/platform/` | OS abstraction - `linux.py` (xdotool/ydotool), `windows.py` (SendInput), `password_detect.py` |
| `src/platform/__init__.py` | Platform detection, `get_config_dir()`, `get_model_dir()` |
| `src/prediction/` | Prediction engines (see below) |
| `qml/Main.qml` | Root UI - title bar, keyboard rows, prediction bar, resize handles |
| `qml/components/` | Reusable QML components (KeyButton, settings panels, etc.) |
| `data/` | Static data: dictionaries, training corpus, keyboard layouts, vocab packs |
| `build/` | Packaging pipelines - `build/windows/` (PyInstaller + NSIS + EV signing) and `build/linux/` (PyInstaller + optional AppImage). `build/launcher.py` is the shared frozen-mode entry point. |
| `tests/` | pytest suite |

## Prediction Engine

All in `src/prediction/`. Orchestrated by `hybrid_predictor.py`:

| File | Role |
|------|------|
| `ngram_predictor.py` | Word-frequency model: unigrams, bigrams, trigrams. Learns from typing. |
| `ppm_predictor.py` | Character-level PPM (Dasher algorithm). Predicts next characters. |
| `fuzzy_recognizer.py` | Spatial error correction. Considers nearby keys as candidates. Single tuned default (no profiles). |
| `hybrid_predictor.py` | Merges all predictors. Manages model save/load. Emits Qt signals. |
| `vocabulary_pack.py` | Custom vocab pack import (no built-ins ship - see *Vocabulary Packs* section) |
| `transformer_predictor.py` | Optional LLM re-ranking (disabled by default) |

Deep-dive design docs for each algorithm: `docs/architecture/FUZZY_RECOGNITION.md` (spatial model + tunable constants), `docs/architecture/PPM.md` (variable-order character model + PPMD escape), `docs/architecture/HYBRID_MERGING.md` (merge weights + validation + capitalization), `docs/architecture/SWIPE_TYPING.md` (shape-matching swipe decoder).

## Short words in next-word predictions

`HybridPredictor._short_word_allowed` gates one- and two-letter words out of *next-word* predictions (the filter does not apply once the user has started typing a word, where the prefix already constrains things). It used to be a blanket `len(word) <= 2` with `"i"` as the single exception, which discarded exactly the words next-word prediction is best at: after "I want", the useful pills are "to", "it", "my", "us"; after "one", they are "of" and "or". Those are also the highest-frequency words in English, so the bar was withholding its strongest guesses and offering the fourth-best instead.

It is now an **allow-list of real short words** (`_SHORT_WORDS_ALLOWED`), not a relaxed length rule, and that distinction is load-bearing: the engine learns whatever the user types, so stray two-character fragments ("th", "ap", "sm") from a typo or an interrupted word accumulate in the model, and a bare length change would let every one of them compete for a pill. Words, not lengths. Adding to the set is the supported way to extend it. Guarded by `tests/test_hybrid_predictor.py::TestShortWordsAreOfferedAsNextWords`, whose negative half is what stops a future "just drop the filter" from passing.

## Auto-Capitalization & Proper Nouns

The pill-facing capitalization rule is intentionally minimal: **only the "I" family auto-capitalizes** (`"I"`, `"I'm"`, `"I'll"`, `"I'd"`, `"I've"` - hardcoded in `ngram_predictor._always_capitalize`). Anything else stays in the casing the user typed. The mental model is "shift / caps lock is the cap signal, full stop" - pills do not second-guess intent.

This used to be a three-tier Gboard-style system (Tier 1 "I" family, Tier 2 sentence-start for ambiguous names like `will` / `jack` / `may`, Tier 3 ~8 000 unambiguous proper nouns from `data/proper_nouns.txt` plus user-taught forms). Tiers 2 and 3 fired on too many common English words ("the hope is that", "a rose by", "will you", "may i", and the post-period word in any sentence), so pills came back capitalised when the user had typed lowercase. The user's stance is that those auto-caps were noise, not help.

### How it works now
- `NgramPredictor.get_capitalized(word, sentence_start)` returns the `_always_capitalize` form for the "I" family, otherwise returns `word` unchanged. The `sentence_start` argument is kept for API compatibility but ignored.
- `HybridPredictor._merge_predictions()` still calls `get_capitalized` on each pill (so the "I" family flows through the engine like any other word), and still computes `sentence_start = bool(ctx) and ctx[-1] in ".!?"` - the value just doesn't affect the result.
- **Pill-facing casing comes from `KeyboardBridge._display_cased`** - it mirrors *every* uppercase position from the typed prefix onto the pill. Type lowercase `monday` -> pill shows `monday`. Type `Monday` (one-shot shift on the M) -> pill shows `Monday`. Type `MON` (right-click each letter) -> pill shows `MONday`. This is the only path that produces capitals in pills, and it's driven entirely by what the user typed.

### Data still being collected (currently inert in pills)
Two paths populate `NgramPredictor.capitalization` even though `get_capitalized` no longer reads from it:
- `_load_proper_nouns()` reads `data/proper_nouns.txt` at startup.
- `learn_capitalization(word, *, allow_uppercase=False)` is called from the bridge in three situations: (a) the user types a word with non-trivial casing and completes it with space; (b) the user has any uppercase letter in their typed prefix and accepts a pill (`pressPrediction` calls `learn_capitalization(word)` on the chosen pill); (c) the user right-click -> Edits a prediction. The `allow_uppercase` guard is still meaningful: `_word_typed_under_caps_lock` flips to True whenever a char is appended while Caps Lock is on, and the bridge passes `allow_uppercase = not _word_typed_under_caps_lock` so all-caps under Caps Lock doesn't poison the table. Acronyms typed deliberately (right-clicking each letter, Caps Lock off) still land in the table.

The accumulated dict is persisted in `ngram_model.json`. Keeping the data lets a future opt-in switch (e.g. a "capitalize proper nouns" toggle) re-enable Tier 3 without re-teaching from scratch. **If you re-enable any tier, do it by editing `get_capitalized` to consult `self.capitalization` again - don't reintroduce the old three-tier behaviour as the default.**

### Adding to always-capitalize
Edit the `_always_capitalize` dict in `ngram_predictor.py`. Keep it tight - it's the one auto-cap that will fire mid-sentence regardless of what the user typed, so anything beyond the "I" family needs to be unambiguous in *every* mid-sentence context (which proper nouns aren't, which is why Tiers 2/3 are gone).

## Where User Data Lives

- **Settings** (layout, theme, toggles): Managed by Qt `Settings` in QML. Auto-saved on change. Stored in OS registry/config automatically by Qt.
- **Prediction model** (learned words/phrases): Saved to disk explicitly or via auto-save on exit.
  - Windows: `%APPDATA%/alpha-osk/models/`
  - Linux: `~/.config/alpha-osk/models/`
  - Files: `ngram_model.json`, `ppm_model.json`
  - **Load-time caps**: both loaders reject files over 50 MB. The n-gram loader also rejects files with more than 500 000 unigrams, 500 000 bigram prefixes, or 100 000 capitalisation entries - anything beyond these is assumed to be corrupt or hostile and is silently skipped (the in-memory base dictionary is kept).
- **Custom vocabulary packs**: Imported by the user. No built-in packs ship - see *Vocabulary Packs* section below for why.
  - User-imported: `%APPDATA%/alpha-osk/packs/` (Windows) or `~/.config/alpha-osk/packs/` (Linux)
  - Pack format: folder with `dictionary.txt` (required), optional `bigrams.txt`, `trigrams.txt`, `pack.json`
  - **Import hardening**: the source folder's name is sanitised to `[a-z0-9_-]{1,64}`; anything else (including `..`) is rejected. The resolved destination is verified to sit strictly under `user_packs_dir` before any `rmtree`/`copytree` runs, and symlinks inside the source tree are skipped rather than dereferenced. Don't loosen this without re-reading `PackManager.import_pack` and the regression tests in `tests/test_vocabulary_pack.py::TestImportPackSecurity`.
- **Analytics** (lifetime typing stats): `analytics.json` sits directly in the config dir root (not under `models/`), same Windows/Linux paths as above. **Load-time cap**: rejected outright over `_MAX_STATS_FILE_BYTES` (5 MB, checked via `stat()` before the file is opened, the same before-you-open-it pattern as the n-gram/snippets loaders); word and key frequency tables are separately capped at 5000 entries each (top-N by count) on both load and save. Every **scalar** counter goes through `_as_count` / `_as_minutes` on load, which reject a non-number (including `bool`, since `True` is an `int`), reject NaN/inf, and clamp negatives to 0: the file is replaced wholesale by a Data Backup import, and a wrongly-typed field would not fail on load at all, it would fail later inside `save()`, where every value is fed into an addition. `save()` therefore builds its whole payload inside its own `try` too. See *Analytics* section below.
- **Diagnostic log**: `alpha-osk.log` in the config dir (`%APPDATA%/alpha-osk/` Windows, `~/.config/alpha-osk/` Linux), wired up in `keyboard_app.py::_configure_logging` as a `RotatingFileHandler` at 2 MB x 3 backups. The frozen build has no console, so this file is the only place updater errors and crash tracebacks land, which also makes it the file users attach to bug reports.
  - **It must never contain typed content.** No log record at INFO or above may interpolate a word, `_current_word`, `_context_buffer`, `_sentence_buffer`, or a prediction list. Log lengths and booleans instead. Anything that genuinely needs the content for local debugging goes at DEBUG *and* behind `if not self._privacy_mode:`. This is not a style preference: `_context_buffer` mirrors the on-screen text up to 200 chars, so a single careless `%s` turns the log into a plaintext transcript of the user's typing, and privacy mode does not gate the logging layer for free. Regression coverage lives with the prediction-path tests.
  - Deliberately **excluded** from the Data Backup archive (`_MODEL_FILES` in `src/data_export.py`), so the leak cannot compound through an export.
  - **Pre-fix logs are purged once on upgrade.** `keyboard_app.py::_purge_pre_fix_logs` deletes `alpha-osk.log*` on first launch of a fixed build, guarded by a `.log-privacy-purge` sentinel in the config dir. Fixing the logging sites only stops *new* leakage; an upgrading user still had up to four rotated files holding a transcript of what they typed, so removing them is part of the fix rather than housekeeping. It must run **before** the `RotatingFileHandler` opens the file, because Windows will not unlink a log the handler holds, and it must never raise: logging setup is not allowed to be the reason the keyboard fails to start. The sentinel is why it runs exactly once, so a user who wants logs kept across restarts is not fighting us. Covered by `tests/test_keyboard_app.py::TestPurgePreFixLogs`.

## Snippets (Quick-Insert Text)

User-defined quick-insert text: name, email, phone, address, signatures, canned replies. The user taps one to type it verbatim into the focused app, instead of typing it out and fighting prediction every time. Opened from a title-bar button (the hamburger menu icon, next to Learning). Backend in `src/snippets.py`; UI is the floating `snippetsWindow` in `qml/Main.qml`.

### Data model and storage
Each entry is a `{label, value}` pair: `label` is the short text on the button (e.g. "Email"), `value` is the exact text typed when tapped. Persisted as `snippets.json` in the config dir (`%APPDATA%/alpha-osk/` Windows, `~/.config/alpha-osk/` Linux), saved synchronously on every mutation (atomic tempfile-then-rename), so there is no on-quit save path to wire up. On first launch the store seeds four pre-made empty labelled slots (Name / Email / Phone / Address); every field including the label is editable and deletable. Bounds: `MAX_SNIPPETS` 50, `MAX_LABEL_LEN` 40, `MAX_VALUE_LEN` 2000, file cap 1 MB. A corrupt, oversized, or empty file falls back to the seeded defaults rather than raising. Labels are collapsed to a single line; values keep newlines (a value may be a multi-line block like a mailing address), though `_clean_value` strips every other C0 control character plus DEL (0x7F, which sorts above the C0 range, so an `ord(ch) >= 0x20` filter alone would miss it). That's for locally authored snippets only: an *imported* value has its newlines flattened to spaces instead, see *Backup* below.

### Insertion path
`KeyboardBridge.insertSnippet(index)` routes the value through `_release_sticky_modifiers()` then `_send_literal_text` (the same verbatim path swipe / predictions use; a held Shift would otherwise deliver the whole address in capitals). Snippets are full literal inserts, so unlike prediction pills there is **no** prefix matching, no autocorrect, and **no compat-mode BackSpace+retype** (that dance exists to replace a typed prefix, which a fresh insert doesn't have). Insertion is **not** blocked by privacy mode: privacy is about not *learning* from typing, and the user may need to drop their address into a sensitive form. After inserting, `_current_word` / predictions are cleared so the verbatim text (which may carry punctuation or newlines) can't corrupt the next prediction's prefix matching. `insertSnippet` is a no-op while edit mode is active (`_edit_mode_active`) so it can't fire while the user is editing a snippet field.

### Floating window (NOT a Popup)
`snippetsWindow` is a **separate top-level `Window`**, not a QML `Popup`. A `Popup` is clipped to its parent window's overlay, so it could never be dragged off the keyboard; a standalone `Window` floats anywhere on the desktop. It carries the same OSK flags as the main window (`Qt.Window | FramelessWindowHint | WindowStaysOnTopHint | WindowDoesNotAcceptFocus`). On Windows that Qt flag alone doesn't stop click-activation, so `keyboard_app.py::_wire_snippets_window` finds the window by `objectName: "snippetsWindow"` and re-applies `WS_EX_NOACTIVATE` (via the shared `_apply_windows_extended_styles`) on every `visibleChanged` (the native handle only exists once shown). Non-Windows is a no-op (X11/Wayland respect the Qt flag; macOS uses the app-wide Accessory policy). The header is a drag handle that moves the whole window freely with no clamp. First open centers it just above the keyboard; the dragged position persists **for the session only** (not across restarts yet - unlike the main window, which persists `savedWindowX/Y`).

### Editor UX (reuses the edit-mode plumbing)
The window has two views (an `editingIndex` switch: -1 list, >= 0 editor): the list (default) and a per-snippet editor with Label + Text fields. **Edit mode is only turned on while the editor is showing**, not for the whole window. This is critical: if it set `setEditMode(true)` on open, tapping a snippet in the list would be swallowed by edit-mode routing instead of inserting to the OS. In the editor, OSK keystrokes flow through the same `editKeyTyped` / `editSpecialPressed` signals the prediction-edit popup uses; an `editTarget` property ("label" / "value", set by tapping a field) picks which `TextField` receives them. Saving calls `setSnippet` and flashes the shared `editSavedToast`. Empty slots are never dead taps: tapping a slot with no value opens the editor directly instead of inserting.

### Bridge slots and signal
`getSnippets() -> QVariantList`, `insertSnippet(int)`, `setSnippet(int, str, str)`, `addSnippet()` (appends a blank "New" slot for the user to fill), `deleteSnippet(int)`, `moveSnippet(int, int)` (direction -1 up / +1 down). The `snippetsChanged(list)` signal is emitted after every mutation so the window re-queries and rebuilds its rows.

### Backup
`snippets.json` is in the Data Backup archive (`_MODEL_FILES` in `src/data_export.py`), replace-on-import like the model files. **Import flattens newlines**: every `\r`/`\n` in an imported snippet's value is replaced with a space (`data_export.py::_flatten_imported_snippet_newlines`) right after extraction, because `xdotool type` turns a literal newline into a real Return keypress and an imported archive is untrusted content; a snippet authored locally in the editor keeps its newlines (see *Data model and storage* above). Best-effort by design: a flatten failure is logged and the file is left as extracted rather than aborting an otherwise-successful import. After an import, `KeyboardBridge.importUserData` calls `SnippetStore.reload_from_disk()` + emits `snippetsChanged` so the running session picks up the imported snippets without a restart. It is *not* encrypted: it's local user data on the user's own machine, same trust model as the prediction model. Guarded by `tests/test_data_export.py::TestSnippetNewlineFlattening`.

## Intelligent Spacing & Snippet Auto-Detection

Two features that look unrelated but ask the same question ("does this text have a recognisable shape"), so they share one module: **`src/text_patterns.py`**. Answering it in one place keeps them from drifting into disagreeing about what an email address looks like. Everything there is linear-time with explicit length caps, because it runs on the keystroke path and the input is whatever the user is typing; and nothing there logs, because every argument is typed content.

### `_raw_token` (the run before the cursor)

Both features need the unbroken run of characters immediately before the cursor, punctuation included. **This cannot be `_current_word`**: that is the prediction engine's notion of a word and it resets at `@` and at every dot, so by the time the `.` in `owen@gmail.com` arrives, `_current_word` is `gmail` and the `@` that proves this is an email is already gone. `_raw_token` is maintained in `_press_char` (append), `pressSpecialKey` (cleared on space/return and on `_TOKEN_BREAKING_KEYS`, popped by backspace), and cleared by every verbatim insert and context reset. Capped at `_MAX_RAW_TOKEN_LEN` (128). It is only maintained **outside privacy mode**, same as `_current_word`, because it holds typed characters.

### Intelligent spacing

*Settings → Smart Typing → Suggestions → Intelligent Spacing*, default ON, disabled and greyed when Auto-Space After Punctuation is off (it modifies that behaviour and does nothing without it). Skips the punctuation auto-space when it would break a structured token: `3.14`, `1,000`, `12:30`, `owen@gmail.com`, `www.example.com`, `https://…`, `192.168.1.1`, `C:/Users/…`, and every dot after the first in a dotted run.

When suppressed, three things are skipped together: the sent space, the space in `_context_buffer` (it mirrors the screen, and a phantom space breaks pill inserts), and the auto-capitalize (or `example.com` comes out `example.Com`). The auto-space-**off** path is deliberately byte-identical to before.

### Auto-capitalize is not a held Shift

`_auto_capitalize_after_punctuation` arms **`_pending_auto_cap`**, never `_shift_active`. The distinction is load-bearing and was learned the hard way. Expressing it as a Shift looks equivalent (both uppercase the next letter) but `_send_key` builds its chord modifiers straight from `_shift_active`, so a sentence-ending period left every following chord poisoned: Enter became Shift+Enter (a newline in Slack rather than send), Ctrl+C became Ctrl+Shift+C, and arrows started extending a selection. It was also the one site in the file that set an `_*_active` flag with **no paired `hold_modifier()`**, so the bridge believed in a hold the OS never had, which is exactly the invariant `_without_held_modifiers` relies on to decide what to restore. `_pending_auto_cap` feeds the case computation in `_press_char`, `_update_layer` (so the keycaps show it) and nothing else; it is spent by the next character, and dropped by a caret move or a context reset. Space deliberately does *not* clear it, because the word after the auto-space is the one the capital was meant for. Guarded by `tests/test_keyboard_bridge.py::TestAutoCapitalizeIsNotAHeldShift`.

(Before this, the feature was inert: it set `_shift_active` and the auto-release block at the end of the same keystroke cleared it, so the toggle had no observable effect at all.)

**Every rule requires positive evidence in the token itself.** There was briefly a "first-token rescue" that suppressed the dot in a lowercase single word when nothing containing whitespace had been typed yet, meant to catch `example.com` in a URL bar. **Do not reintroduce it.** The proxy for "start of the field" was `_context_buffer` being space-free, and that buffer is emptied on every app switch *and* every focused-element change (the 250 ms UIA poll), so the rule fired at the start of every newly focused text box: click into a field, type `hello.` and the space vanished, then kept vanishing because the buffer still held none. It also cannot be repaired with a better proxy, because at the instant the dot lands `example` and `hello` are the same string.

**Known limitation, now unconditional.** A bare `example.com` always gets a space after its *first* dot, and since that space ends the run, `example.co.uk` loses one per dot. Guessing from the following character was tried and rejected too: it turns `i went home. then i left` into `home.then`, and corrupting prose is far worse than a space the user can delete. Closing it properly needs lookahead (buffering keystrokes until a known TLD resolves), which costs the immediate visual feedback a mouse-driven keyboard depends on. Pinned by `tests/test_keyboard_bridge.py::TestIntelligentSpacingInAFreshField` and `TestIntelligentSpacing::test_a_mid_sentence_bare_domain_loses_a_space_per_dot`. Change those if you improve it, don't delete them.

### Snippet auto-detection

*Settings → Data & Privacy → Privacy*, default ON. At each word boundary, `_maybe_offer_snippet()` looks for an email / phone / address and emits `snippetOffered(kind, label, value)`; QML shows the `snippetOfferToast` with a Save button and a dismiss ✕, and calls `acceptSnippetOffer()` / `dismissSnippetOffer()`.

It lives under **Privacy** rather than Smart Typing because the question it raises is "may the keyboard notice personal details you type", not "how should typing behave".

Five guards, all of which exist because the failure mode is offering to store the user's personal data when they didn't ask: never in privacy mode; never twice for the same value (`_offered_snippet_values`, session-scoped, because it is a nag guard rather than a preference); never for a value already in a snippet; never on top of a live offer; nothing is written until Save is tapped.

Two scan windows, both needed. `_raw_token` is the only place a single-token shape survives intact (see above). The last `_SNIPPET_SCAN_TAIL` (120) chars of `_context_buffer` then cover shapes that span whitespace: an address is several words, a phone is often `(555) 123 4567`. The tail is bounded so an address typed a paragraph ago doesn't resurface at an unrelated word boundary.

**Accepting fills the empty matching slot, or appends a numbered one.** The seeded Name / Email / Phone / Address slots exist to be filled, so an empty one takes the value. A slot that already holds a *different* value is never overwritten: a second email becomes `Email 2`. Work and personal addresses are both worth keeping, and silently replacing a value the user had curated is the worse failure.

**Detection is conservative on purpose.** An offer the user has to dismiss twice is an offer they learn to ignore. A phone needs 7-15 digits *and* either a separator or a leading `+` (a bare digit run is only accepted at exactly 10, the unambiguous NANP form), so an order number doesn't match. An address needs **both** a leading house number and a recognised street-type suffix, so `500 people showed up` and `walking down Baker Street` both correctly fail. Every positive case in `tests/test_text_patterns.py` is paired with the near-miss it must reject; the inverse assertions are the point, since a detector that matched every number would satisfy the positive half completely.

The toast is interactive, which is why its `closePolicy` **must** stay `Popup.NoAutoClose`: every OSK key click is a press-outside, so `CloseOnPressOutside` would slam it shut on the first keystroke (the same trap the prediction-edit popup documents). Its dwell is 8 s rather than the 1.4 s of the sibling confirmation toasts because the user has to read it, decide, and land a click with an imprecise pointer, and timing out calls `dismissSnippetOffer()`, because ignoring an offer is a decision too and without telling the bridge the value would stay "pending" and block the next one.

## Data Backup (Export / Import)

User-facing "back up my data" feature so a user can move their model between machines. Lives in `src/data_export.py`; UI is *Settings -> Data & Privacy -> Data Backup* (above the Privacy section).

### What's in the archive
A normal `.zip` with `manifest.json` (schema version, app version, ISO-8601 UTC timestamp, file list, pack id list) plus `models/ngram_model.json`, `models/ppm_model.json`, `analytics.json`, `snippets.json` (user quick-insert snippets, see *Snippets* section), and `packs/<id>/...` for each imported pack. **`telemetry.json` is deliberately excluded** - copying the anon_id across machines would link contributions, which `docs/PRIVACY.md` and the telemetry consent docs explicitly promise not to do. A fresh anon_id is generated on the new machine when telemetry is re-enabled.

Settings (theme, layout, toggles, window size) are **not** in the archive. They live in the Qt settings layer (Windows registry / Linux config) and are quick to reconfigure manually; the irreplaceable bit is the prediction model. If a future release adds settings to the export, schema_version must be bumped and old-version import paths must still apply correctly.

### Import is *replace*, not *merge*
Imported files overwrite the corresponding files in the config dir; packs not in the archive are removed (the imported state is "the user's full snapshot at export time"). Before any overwrite, the current state is written to a timestamped rescue archive in `<config_dir>/exports/rescue-<ts>.zip` so the user can roll back by importing that file. Rescue export failures are logged but do not abort the import.

Model files are replaced via tempfile-then-rename so a partial write can't corrupt the existing file. After files are replaced, `HybridPredictor.reload_from_disk()` re-reads `ngram_model.json` / `ppm_model.json` and re-discovers packs; `TypingAnalytics.reload_from_disk()` re-reads lifetime counters. The user does not need to restart Alpha-OSK. Enabled-pack state is reset (packs come back disabled and the user re-enables what they want - this matches what would happen if they imported each pack one at a time on the new machine).

### Security hardening (don't loosen without re-reading the tests)
Both `inspect_export` and `import_user_data` validate every archive entry:
- Reject names with `..` components, absolute paths, drive prefixes (`C:`), or backslashes (zip-slip defence - Python's `Path` handles `..` natively but the explicit check is defence in depth and matches how `PackManager.import_pack` validates pack ids).
- Per-file uncompressed size cap (`_MAX_FILE_BYTES`, 75 MB), cross-entry running-total cap (`_MAX_TOTAL_UNCOMPRESSED`, 500 MB), archive-on-disk cap (`_MAX_ARCHIVE_BYTES`, 200 MB). `inspect_export` (which `import_user_data` always calls first) pre-checks every entry's declared `file_size`; `_bounded_copy` (replacing a bare `shutil.copyfileobj`) then re-enforces both uncompressed caps against bytes actually read, chunk by chunk, while writing. Don't read that streaming check as closing a metadata bypass. There wasn't one: CPython's `ZipExtFile` already truncates a read to the declared `file_size`, so a forged small size can't yield more bytes than it claims, it just fails the CRC check instead and raises `zipfile.BadZipFile`. That exception was the real defect: uncaught, it used to blow up `import_user_data` after model files, analytics and snippets had already been overwritten. `_bounded_copy` catches it and translates it into a `DataExportError` like every other validation failure here.
- Extraction is allow-list, not deny-list. Only members matching the exact expected paths (`models/ngram_model.json`, `models/ppm_model.json`, `analytics.json`, `packs/<sanitised-id>/<allowed-filename>`) are written to disk. A hand-edited archive that snuck `telemetry.json` or `../../boot.ini` in past the manifest check is silently ignored at extraction time. Pack ids are re-matched against `_PACK_ID_RE` on import, which also rejects reserved Windows device names (`con`, `prn`, `aux`, `nul`, `com1`-`9`, `lpt1`-`9`, case-insensitive, extension stripped) so an archive can't name a pack something unopenable on Windows; each per-pack file write is wrapped in `try`/`except OSError` so one bad entry is skipped instead of aborting the rest of the import.
- Schema-version forward-compatibility: if the manifest's `schema_version` exceeds `SCHEMA_VERSION`, import is refused with a "upgrade Alpha-OSK first" message rather than half-applied.

Regression coverage: `tests/test_data_export.py::TestInspect::test_zip_slip_rejected`, `test_absolute_path_rejected`, `test_future_schema_rejected`, `test_oversize_entry_rejected`, plus `TestImport::test_telemetry_not_restored` (a hand-crafted archive cannot smuggle telemetry.json past the extractor), `TestBoundedCopy` (streaming caps + `BadZipFile` translation), and `TestReservedPackNames` (device-name rejection).

### Bridge slots
- `getDefaultExportDir() -> str` - Documents folder via QStandardPaths, falls back to home.
- `getSuggestedExportName() -> str` - `Alpha-OSK-Export-<YYYY-MM-DD-HHMMSS>.zip`.
- `exportUserData(dest_path) -> str` - empty string on success, error message otherwise. Calls `_predictor.save()` + `_analytics.save()` first so the export reflects the running session.
- `inspectUserExport(src_path) -> dict` - `{ok: True, files, pack_ids, app_version, exported_at, bytes, schema_version}` or `{ok: False, error}`. QML uses this to show a preview before the user commits.
- `importUserData(src_path) -> str` - empty string on success, error message otherwise. Calls `reload_from_disk` on the predictor + analytics, clears `_current_word` / `_context_buffer` / `_sentence_buffer`, emits empty predictions.

## QML <-> Python Bridge Pattern

QML calls Python via `@Slot` methods on `KeyboardBridge`. Python emits `Signal`s back to QML. Example flow:

1. QML: `keyboard.pressKey("a")` -> calls `KeyboardBridge.pressKey()`
2. Python: synthesizes keystroke, updates context, runs prediction
3. Python: `self.predictionsChanged.emit(predictions)` -> Signal
4. QML: binds to `keyboard.predictions` property, updates UI

## Caps Lock vs. Shift

Caps Lock and Shift are **independent toggles**. Toggling caps no longer also flips shift. Both are surfaced separately to QML (`capsLockActive`, `shiftActive`).

- **Uppercase output** in `pressKey`: `key.upper()` if `_shift_active OR _caps_lock_active`.
- **Upper layer**: `_update_layer()` switches to `"upper"` if `_shift_active OR _caps_lock_active`. Same for the displayed glyph in `Main.qml`.
- **OS-level hold**: `toggleShift` calls `_synth.hold_modifier("shift")` / `release_modifier("shift")` so the OS sees Shift physically held while the toggle is active. This is what makes Shift+click and Shift+drag in the target app extend the text selection - same as the Windows on-screen keyboard. Without it, Shift only attached to synthesised keystrokes as a chord modifier and a click between toggle and the next typed character would land without Shift held.
- **Auto-release**: Shift auto-releases after a single keypress; caps stays on until explicitly toggled. Auto-release paths also call `release_modifier("shift")` so the OS-held shift drops together with the Python state. Caps is unaffected by the auto-release path.
- **Visual highlight**: only the toggled key is highlighted - toggling caps does NOT also highlight the Shift key (it used to, that was a bug).

The shifted *glyph* on a key (e.g. `!` on the `1` key) follows shift only - caps lock uppercases letters but does not pick the shifted variant of symbol/number keys, matching standard keyboard behavior.

### Caps Lock and the prediction bar

When Caps Lock is on, the prediction pills also render uppercase. The pills must match what the user is typing *and* what the pill will insert when clicked - showing "hello" while the user has typed "HELL" and then inserting lowercase next to the uppercase prefix was the pre-fix bug. Implementation: `KeyboardBridge._display_cased()` uppercases the engine's output when `_caps_lock_active`, and every emit site (`_on_predictions_ready`, `_on_predictions_refined`, next-word-after-selection, `editPrediction`, swipe) routes through it. `toggleCapsLock` re-queries the engine so currently-visible pills flip case immediately - we can't just `.upper()` / `.lower()` the stored list in place because once "iPhone" becomes "IPHONE" the original casing is lost.

### Shift and the prediction bar

Shift capitalizes the pills' first letter, the same courtesy Caps Lock gets. `_display_cased` has three cases in priority order: **(1)** Caps Lock on → all upper; **(2)** any uppercase in the typed prefix → mirror each uppercase position (the pre-existing rule, described below); **(3)** Shift held with nothing uppercase typed yet → capitalize the first letter only. Case 2 outranks case 3 because an uppercase already in the prefix says something more specific about the word's shape (mid-word caps like `iP` → `iPhone`) than a pending Shift does.

`toggleShift`, `releaseShift` and `lockModifier("shift")` all call `_recase_visible_predictions()` so pills already on screen flip immediately, exactly as `toggleCapsLock` does; it re-queries the engine rather than re-casing the stored list, because `self._predictions` holds the *displayed* form and once "iPhone" has been shown as "IPHONE" the original casing is gone. It no-ops on an empty bar so a Shift tap during ordinary typing costs no prediction round trip.

Tapping a pill with Shift held consumes it like any keystroke (`_release_sticky_modifiers()`, which runs **before** the insert). The ordering is load-bearing, not cosmetic: the capital is already baked into the word by `_display_cased`, so a Shift still held at the OS level would uppercase the insert on top of that and "Hello" would arrive as "HELLO". Releasing first also leaves nothing for `_send_literal_text` to drop and restore.

One interaction worth knowing: `_auto_capitalize_after_punctuation` sets `_shift_active` after a sentence-ending period, so with that setting on the next-word pills render capitalized. That is correct (the next word *is* capitalized) and is not a reintroduction of the removed Tier-2 sentence-start auto-cap: it reflects a Shift the user's own setting turned on, and it never reaches `learn_capitalization`, which still keys on the typed prefix.

`_display_cased` *also* mirrors **every** uppercase position from the typed prefix onto the displayed pill, not just the first letter. If the user typed "Hel" the pills show "Hello"/"Help"; if they right-clicked each letter to type "HEL", the pills show "HELlo"/"HELp"; if they typed "iP" (mid-word cap via right-click), the pill shows "iPhone". The gate is `any(c.isupper() for c in cw)` and the body iterates each prediction position, force-uppercasing it when the corresponding `cw[i]` is uppercase. The mirror runs **regardless of whether the pill strict-prefix-matches the typed letters**, which is the difference from the original implementation. The earlier version short-circuited to pass-through whenever `w.lower().startswith(cw.lower())` was False, which silently dropped the cap on every fuzzy / autocorrect candidate (typing "Hwl" for "Hel" -> fuzzy returns "hello" -> "hello" doesn't strict-prefix "hwl" -> cap lost). Mirroring unconditionally fixes that. Two reasons capitalised pills still matter even when the prefix matches: (1) the displayed pill must reflect what the user typed so they can tell which pill matches their prefix, and (2) the suffix-only insert path uses a case-sensitive `startswith`, so "hello".startswith("HEL") is False and the click would fall through to a full replace, clobbering the user's capitals. Sentence-start and proper-noun capitalisation still flow through `NgramPredictor.get_capitalized` upstream; this layer only mirrors the *typed* prefix back into the displayed form.

### Prediction pill widths (never "..." truncation)

**The bar drops low-ranked pills rather than eliding any of them.** Eight `documentation`-family candidates in a 940 px window rendered as eight identical `docu...` pills, which is unusable - every pill looks the same, so there is nothing to choose between. Showing five readable words beats showing eight unreadable ones. The whole computation lives in `predRow.computeFit(...)` in `qml/Main.qml`, which returns `{words, widths}`; the Repeater's model is `predRow.fit.words` (a **prefix** of `root.predictions`, so anything dropped is the lowest-ranked) and each delegate takes `predRow.fit.widths[index]`. `predRow.pillWidthList` is a read-only alias kept for the tests.

Three rules, in priority order:

1. **No elide.** Each word's *tight* width is `ceil(FontMetrics.advanceWidth(word)) + minPad` (floored at `predMinWidth`), where **`minPad = max(14, 2 * predBar.predTextInset)`**. Padding compresses to that first; if the set still doesn't fit, `count` decrements until the survivors fit at tight width. A dedicated `FontMetrics { id: predMetrics }` measures in the *same* font the pills render (pixelSize / weight / family), so the parent sizes every pill centrally instead of each delegate publishing its own `implicitWidth` back up.

   **`predBar.predTextInset` is the single source of truth for horizontal padding, and that is load-bearing.** It is what the delegate's `Text` sets `anchors.leftMargin` / `rightMargin` to, *and* what `computeFit` reserves. Deriving the two from different numbers makes "tight" a width the word provably cannot render in, and the no-elide guarantee silently becomes false. That is exactly what happened: the fitter floored padding at `predHorizontalPad * 0.45` while the delegate ate `2 * predHorizontalPad * 0.28` = `0.56` of it, so for any `predHorizontalPad` above ~26 (every window wider than ~700 px, and all of Compact View) text-driven pills were born 1-5 px too narrow. Rule 2's water-fill usually topped them back up, which is why it looked fine; when the row packed tightly enough that slack ran out, they elided. **Never inline the inset at either site.** Widths are also `ceil`'d because `Text` elides on a sub-pixel overflow and the width handed back is a float.
2. **Leftover space is handed back as padding, max-min fair**: a pill wanting less than the current fair share settles at what it wants and releases the rest, raising the share for the others (<= count passes); still-hungry pills split what remains. This is what stops "I"/"the" sitting in half-empty pills beside a cramped long word.
3. **`predBar.clearCtxReserve` is subtracted from the available width** so the row can never reach the ⟲ button (see the invariant below).

Only one case can still elide: a single word wider than the whole bar, where there is nothing left to drop. It's clamped to the available width and the hover `ToolTip` (gated on `predText.truncated`) reveals it. Consequence to be aware of: raising *Settings -> Smart Typing -> Suggestions -> max count* past what the window can hold no longer shows more pills, it just gets clipped by the fitter - the lever for more visible suggestions is a wider window.

The `fit` binding reads `root.predictions`, `root.width` and the `predBar.pred*` geometry props directly so it re-evaluates whenever predictions, window width or pill sizing change. Headless-verified against real Qt `FontMetrics` + the QML binding engine in `tests/test_qml_prediction_bar.py`, which asserts on `Text.truncated` (the same flag the ToolTip is gated on, so the test can't disagree with what the user sees).

**Two traps in testing this bar, both of which already produced a test that could not fail.** Read these before adding an assertion here:
- **`root.findChildren(QObject, "predictionPillText")` returns an empty list.** A `Repeater`'s delegates are re-parented as *visual* children; their QObject parent is the delegate model, not the item tree. Every truncation assertion in the file went through `findChildren` and so ran against zero pills for its whole life, which is how a real eliding regression shipped underneath a class named `TestNoPillIsEverTruncated`. Use the `_pill_texts` helper (walks `childItems()`) and assert the result is non-empty at the call site. Reading `root.contentItem` also needs `from PySide6.QtQuick import QQuickItem` somewhere in the module or PySide raises `Can't find converter for 'QQuickItem*'`.
- **Never assert `contentWidth <= width`.** Once a `Text` elides, `contentWidth` measures the *shortened* string, so it fits by construction and the comparison can never fail. It reads like arithmetic proof and is unfalsifiable. `Text.truncated` is the only honest signal.

Also: the failure mode here is a **knife-edge**, so spot-checking a few round window widths proves nothing. `test_every_pill_has_room_for_its_own_text` sweeps 260 configurations (both view modes x 720-1240 px) because the deficit only bites where the row packs tightly enough that the water-fill cannot cover it. With the bug present that sweep failed 130 of 260; at 940 px non-compact, the obvious width to check by hand, it did not fail at all.
- **`predBar.clearCtxReserve` is load-bearing, not decorative.** The clear-context (⟲) button owns a strip at the right edge; that width is subtracted inside `computeFit` *and* the row is positioned with an explicit `x` (not `anchors.centerIn`, which centres on the full bar) so pills are centred in what's left. It was declared but never used at first, and the right-hand pill rendered underneath the button. Reserved on the right only: taking the same bite from the left would re-centre the row in the window at twice the width cost and make long words elide sooner. Guarded by `tests/test_qml_prediction_bar.py::TestClearButtonNeverCoversPills`.

## Editing a Prediction (OSK-friendly edit popup)

Right-click a prediction pill -> Edit opens a small popup with the word pre-filled and selected, so users can correct it (e.g. `iphone` -> `iPhone`) and save via `editPrediction(old, new)`. The popup is deliberately non-obvious in one way: OSK keystrokes must land in *our* TextField, but OSK key presses normally synthesize via `xdotool` / `SendInput` to the OS-focused app behind Alpha-OSK.

- **No modal overlay**: `predEditPopup.modal = false`. A modal popup would install an overlay that swallows MouseArea clicks on the keyboard below, so no OSK key would fire.
- **No press-outside close**: `closePolicy: Popup.CloseOnEscape` only - every OSK key click is a "press outside" and would otherwise slam the popup shut on the first keystroke. Escape and the X cancel button are the visible ways out.
- **Edit-mode intercept**: on open/close the popup calls `keyboard.setEditMode(true/false)`. While active, `pressKey` and `pressSpecialKey` short-circuit the synthesizer and emit `editKeyTyped(char)` / `editSpecialPressed(name)` instead. A `Connections { target: keyboard }` block inside the popup wires those to TextField ops - insert at cursor, backspace, delete, left/right/home/end cursor motion, space, return-to-accept, escape-to-cancel.
- **Modifier handling in edit mode**: shift/caps still apply to letter case; ctrl/alt/win are ignored inside the field so stray chords can't leak to the app behind us. Shift auto-releases after one keypress the same way it does outside edit mode.
- **"Saved" confirmation toast**: a small green popup at the top of the window flashes "Saved" (with a checkmark) for 1.4 s after a successful save. Triggered from all three save paths (checkmark button click, Return-key in edit mode, TextField `onAccepted`). The save itself was always synchronous - `set_capitalization` updates the dict immediately and `aboutToQuit` writes it to `ngram_model.json` - but with no UI feedback the user couldn't tell it stuck without quitting and relaunching. Any new save path must also call `editSavedToast.flash()` or the user will think their edit was lost.

If you add a new input source (e.g. a voice-dictation slot, another popup with its own TextField), the pattern is: set edit mode on open, listen to `editKeyTyped` / `editSpecialPressed`, clear edit mode on close. Don't try to route through Qt focus - `WS_EX_NOACTIVATE` / `WindowDoesNotAcceptFocus` prevent our window from holding OS focus, so physical keyboard input and synthesized input both go to whatever app was focused before we opened.

## Swipe / Glide Typing

Drag the mouse across letters to type a whole word in one gesture, like Gboard. Off by default; toggle in *Settings -> Smart Typing -> Suggestions -> Swipe Typing*. Design doc: `docs/architecture/SWIPE_TYPING.md`.

| File | Role |
|------|------|
| `src/prediction/swipe_recognizer.py` | `SwipeRecognizer` - simplified SHARK^2 shape matching + frequency prior |
| `src/keyboard_bridge.py` | `setSwipeEnabled`, `setSwipeLayout`, `processSwipe` slots |
| `qml/components/SwipeOverlay.qml` | Mouse interceptor + path canvas, hidden when off |
| `qml/Main.qml` | `charKeyRegistry` + `tappableKeyRegistry`, `pushSwipeLayout()` (overlay-local key centres) |

When the toggle is on, a transparent overlay covers the keyboard rows and intercepts all gestures. Press -> drag past 60 px -> swipe; press -> release on a key -> tap fall-through. The recogniser pre-filters by start/end key, then scores remaining candidates with `log(freq+1) - 8 * mean_normalized_distance`. Top result is typed via `send_text` + space; alternates appear in the prediction bar so the user can repick.

**Two registries, and the split is load-bearing.** `registerCharKey` fills both: `charKeyRegistry` (single-character keys only) is the recogniser's key-centre map, and `tappableKeyRegistry` (every key under the overlay) is hit testing. One list served both until swipe typing was found to make Backspace, Delete, Tab, Enter, the arrows, the modifiers, `?123` and the Number Row's Esc **dead taps** (issue #15): the overlay took every press, then resolved it against a list that structurally could not contain them. Widening the char filter fixes the taps and corrupts swipe decoding, so **never** do that; give each consumer its own list. Hit testing also skips `visible: false` items, because a KeyButton in a hidden panel still registers and carries stale geometry.

**Specials activate on press and hold; characters activate on release.** A gesture starting on a non-character key can never be a swipe, so activating immediately is safe and is what keeps **auto-repeat** working (holding Backspace to delete a word). Characters must wait for release because until the gesture ends it is genuinely ambiguous. The overlay drives keys through `KeyButton.externalPress()` / `externalRelease()`, which share the debounce / visual / activation / repeat code with the button's own MouseArea, so a key behaves identically whether or not swipe is on. Guarded by `tests/test_qml_swipe_overlay.py`.

## Sticky Modifiers (Shift, Ctrl, Alt, Win)

Modifier keys are **sticky** - tap once to activate, tap again to deactivate. While active, the modifier is held at the OS level via `hold_modifier()` / `release_modifier()` on the platform synthesizer. This means:

- **Modifier+click works**: e.g., Ctrl+click to open hyperlinks, Shift+click and Shift+drag to extend text selection in the target app - same model as the Windows on-screen keyboard.
- **Modifier+key combos work**: e.g., tap Ctrl, then tap C -> sends Ctrl+C.
- **Auto-release**: After any key press (character or special), active modifiers are released at the OS level and deactivated. Shift specifically auto-releases after one keypress (caps lock pins it on instead) - Ctrl/Alt/Win behave the same way.

### Super/Meta is never held on Linux (`win` modifier)

The one exception to "held at the OS level": on Linux, `LinuxKeySynthesizer.hold_modifier()` **skips `win`/`super` entirely** (early-return, no `xdotool keydown super`). Holding Super is a window-manager gesture trigger - while it's down, Mutter/KWin grab the pointer for window move/resize (Super+drag = move, Super+right-button = resize), so *every* mouse click (including clicks on the OSK's own keys) is swallowed as a WM gesture instead of reaching the keyboard. The user then can't tap Win again to release it and is stuck (the reported "stuck in a right-click scenario" bug). `toggleWin` in the bridge is unchanged and cross-platform-uniform - it still calls `hold_modifier("win")`; the platform layer is where the no-op lives, because the WM-grab is a Linux/X11/Wayland quirk. **Super+`<key>` combos still work** (Win+D, Win+L, Win+arrow) because `send_key()` emits them as an atomic `xdotool key super+<key>` chord that presses and releases Super in one shot. Holding Super buys nothing for an OSK anyway - you can't Super+drag with the same mouse you click keys with. `release_modifier("win")` is left functional (a `keyup super` when Super isn't down is a harmless no-op and clears any externally-stuck Super). Windows still holds `VK_LWIN` - the WM-grab problem is Linux-specific. See `tests/test_platform.py::TestLinuxSuperNeverHeld`.

### Clean state on open (`resetModifiers`)

`KeyboardBridge.resetModifiers()` (`@Slot`) drops every held modifier (Shift/Ctrl/Alt/Win) - releasing the OS-level state via `reset_modifier_state()` and clearing the bridge flags + their key highlights - so a session never starts with a modifier stuck from a prior run, a crash mid-chord, or an external grab. Called from `Main.qml`'s `Component.onCompleted`. Caps Lock is intentionally **not** reset (it holds nothing at the OS level so it can't get stuck, and it's a deliberate persistent toggle). This complements the OS-only `reset_modifier_state()` already called in the bridge `__init__`.

### Implementation
- `keyboard_bridge.py`: `toggleShift()` / `toggleCtrl()` / `toggleAlt()` / `toggleWin()` call `_synth.hold_modifier()` on activate and `_synth.release_modifier()` on deactivate. All auto-release paths in `pressKey()` and `pressSpecialKey()` also call `release_modifier()`. `shutdown()` releases any still-held modifiers so quitting with one "active" doesn't pin it at the X server / Wayland compositor / Windows kernel.
- `platform/base.py`: `hold_modifier()` and `release_modifier()` - default no-op.
- `platform/windows.py`: Sends `VK_CONTROL` / `VK_MENU` / `VK_LWIN` key-down or key-up via `SendInput`.
- `platform/linux.py`: Uses `xdotool keydown/keyup` or `ydotool key --key-down/--key-up`. **`hold_modifier` skips `win`/`super`** - see the Super/Meta note above.

### Right-Click to Lock (persistent hold)

**Right-clicking** Shift / Ctrl / Alt / Win **locks** it held down: the modifier stays held at the OS level and is **exempt from the per-keystroke auto-release**, so the user can fire several combos (Ctrl+C then Ctrl+V) or hold Shift across a whole selection without re-tapping. This is the accessibility answer to "hold the key down" for a mouse-driven OSK. Right-click again — or plain left-tap — to release. **Caps Lock is not lockable** (it's already a persistent toggle). Right-click-to-lock on a modifier is **independent of the "Right-Click for Shifted Character" setting** (a modifier has no shifted variant, and the whole point of the gesture is holding it).

State model: each modifier keeps its existing `_*_active` (held-at-OS, drives the highlight + chord logic) plus a new `_*_locked` flag. **Locked always implies active.** `lockModifier(name)` (bridge slot, called from QML `onKeyRightPressed` for `type === "modifier"` keys) toggles the lock: locking sets active+locked and holds at OS (only if not already held, so locking an already-sticky-active modifier doesn't re-send a key-down); unlocking clears both and releases. The sticky `toggleX()` paths call `_clear_lock(name)` when they turn a modifier off, so a left-tap on a locked key also clears the lock (easy way out). **Every** auto-release site is guarded with `and not self._*_locked` — there are four: the edit-mode intercept, the Ctrl/Alt/Win chord branch, the char-path end (`_press_char`), and the special-key end (`pressSpecialKey`, alongside the existing nav-key `keep_selection_modifiers` exception). Miss one and a locked modifier would silently drop after a keystroke. `shutdown()` releases locked modifiers too (and clears the flags) so quitting never pins one desktop-wide.

QML surfaces the lock via `shiftLocked` / `ctrlLocked` / `altLocked` / `winLocked` bridge properties (+ `*LockedChanged` signals), bound in `Main.qml` and mapped per `kd.stateKey` onto `KeyButton.isLocked`. Because locked implies active, a locked key already carries the accent fill a sticky one-shot has; the lock adds a **solid 3 px bar along the bottom edge** (`lockBar` in `KeyButton.qml`) on top of it, so the two states differ by one unmissable mark rather than by a whole second colour scheme. The bar is inked with `KeyButton._onFillColor`, the shared luminance rule that also picks the key label's colour on an active/pressed fill: dark on a bright accent, white on a dark one. Nine themes ship, several with a pale accent (Blackboard, Spaceship) and one light outright (Typewriter), so any fixed colour is unreadable on roughly half of them. It is inset horizontally past the keycap's corner radius because `clip: true` clips to the bounding rect, not the rounded shape, so a full-bleed bar pokes out past the curve.

This replaced a hardcoded gold 2 px ring plus a 15x15 gold badge holding a 9 px 🔒. **Don't reintroduce an emoji on a keycap**: at that size the padlock is a smudge, and Windows renders it through Segoe UI Emoji as a *colour* glyph, which ignores the `color` property outright, so what shipped was a yellow blob. Any glyph small enough to fit on a keycap is at the mercy of the host emoji font.

**Synth invariant (load-bearing — the lock is worthless without it).** Keeping the bridge state "held" isn't enough; the OS modifier must physically stay down. `hold_modifier` puts it down, but `send_key`/`replace_text` used to *wrap* the action key with a modifier down+up, and that trailing key-up silently released a held modifier after the first keystroke (mouse Ctrl+click / Shift+drag / Alt+Tab then broke even though the key still showed it held). Fix: **`WindowsKeySynthesizer.send_key` and `replace_text` skip wrapping any modifier that is already physically held** (`_modifier_already_held` → `GetAsyncKeyState`), relying on the standing hold instead. This mirrors the pre-existing `shift_already_held` guard in `_make_char_scancode_events`. Any new synth path that wraps a modifier around a keystroke must apply the same guard, or a locked (or sticky) modifier will drop. Mirrored in C++ (`WindowsKeySynthesizer::modifierAlreadyHeld`, used by `sendKey` + `replaceText`). Covered by `tests/test_platform.py::TestWindowsSendKeyPunctuationChord::test_already_held_modifier_is_not_wrapped` and `TestWindowsReplaceText::test_held_shift_not_wrapped_in_selection`.

**Parity**: mirrored 1:1 in C++ (`KeyboardBridge::lockModifier` / `clearLock`, the `m_*Locked` members, the guarded `releaseStickyAll()` + `pressSpecialKey` + edit-mode blocks, and the `*Locked` Q_PROPERTY/signals). Bridge behaviour is covered by `tests/test_keyboard_bridge.py::TestModifierLock` on the Python side.

## Settings Panel Structure

`UnifiedSettingsPanel.qml` is a drill-down menu, not a long scrolling list. The home view shows four category cards; clicking a card swaps the body to that category's sub-view. The header swaps in a back arrow (<) and the category title; the close X stays put.

State is held in a single string property: `currentView` is one of {`"home"`, `"appearance"`, `"typing"`, `"model"`, `"data"`}. The Flickable contains five sibling `ColumnLayout`s, each with `visible: unifiedSettings.currentView === "<id>"`; only one renders at a time. Scroll position is reset to the top on every view change (a `Connections` block on `currentView`) so a drilled-in view never opens mid-section.

The parent (`Main.qml`'s settings popup window) calls `settingsPanel.resetToHome()` in `onVisibleChanged` so re-opening Settings always lands on the home grid, not whatever sub-page the user last visited. Don't break that - landing on a deep page reads as "the menu changed."

### Where each section lives

| Top-level | Section | What's inside |
|-----------|---------|---------------|
| **Appearance** | Panels | Compact View / Function row / Navigation / Numpad toggles. Compact View leads the section because it gates the two below it: it forces Navigation + Numpad off (restoring them on exit) and renders their toggles disabled. There is no Number Row toggle - `Main.qml::showNumberRow` derives from whether the active layout JSON already carries a `number` row, so the standalone panel appears exactly on the compact layouts, which lack one. |
| | Keyboard Layout | qwerty / dvorak / colemak picker (compact variants are filtered out - see *Compact View*) |
| | Theme | 9-theme color picker |
| | Sound & Opacity | Key click sound, opacity slider |
| **Smart Typing** | Suggestions | Show suggestions, auto-space, intelligent spacing, auto-cap, swipe, max count |
| | Suggestion Engine | Merge strategy 4-card picker (rank / rrf / linear / loglinear) |
| | Input | Right-click shift, key preview popup, Compatibility Mode picker, repeat delay & interval |
| **Your Language Model** | (top button) | Open Dashboard -> opens ModelVisualization |
| | Vocabulary Packs | Toggles for any imported packs + Import Custom Pack (no built-ins ship) |
| | Prediction Model | Auto-save toggle, Save Now, Clear Learned Data |
| **Data & Privacy** | (top button) | Help & Shortcuts |
| | Privacy | Snippet auto-detection opt-out, telemetry opt-in + Delete contributed data |
| | Updates | Installed version, auto-check toggle, Check Now |
| | Developer | Debug Mode |

Old labels and their new homes (for backwards-compat references in code comments / docs you might see): the standalone "Layout" section was renamed to "Panels" (the parent category is "Appearance", reusing the name was confusing); the standalone "Appearance" section was renamed to "Sound & Opacity" for the same reason; the old "Tools" section was split - its **Help & Shortcuts** button is now a standalone tile at the top of Data & Privacy, and its **Your Language Model** button moved to be the top-of-page tile in the Your Language Model view.

### Adding a New Setting

1. Add `property bool savedFoo: defaultValue` to `Settings {}` in `Main.qml`
2. Add `property bool foo: appSettings.savedFoo` to root in `Main.qml`
3. Add `property bool foo: defaultValue` to `UnifiedSettingsPanel.qml`
4. Add `SettingsToggle` to the **right sub-view** in `UnifiedSettingsPanel.qml` - pick the category from the table above. Toggles go inside an existing `SettingsSection` block; if no section fits, add a new `SettingsSection { title: "..." }` to that view.
5. Pass property through: `foo: root.foo` in the `Comp.UnifiedSettingsPanel {}` block
6. Handle in `onSettingChanged`: update root, save to appSettings, call bridge if needed
7. If Python needs it: add `@Slot(bool) def setFoo()` to `keyboard_bridge.py`
8. Load on startup in `Component.onCompleted` if it needs to be sent to the bridge

If you can't decide which category a new setting belongs to, that's a sign the UX is fuzzy - push back on the requirement before adding the setting.

## Adding a New QML Component

1. Create `qml/components/MyComponent.qml`
2. It's auto-discovered - the `components/` directory is imported as `"components" as Comp` in Main.qml
3. Use as `Comp.MyComponent {}` in Main.qml

## Fuzzy Recognition Defaults

Hardcoded in `src/prediction/fuzzy_recognizer.py` as `DEFAULT_*` / `_*_PROB` constants. Used to be six "accessibility profiles" (Precise / Normal / Mild Tremor / etc.) but they were confusing - the profile UI is gone and there's now one generous, Gboard-leaning default. Knobs:
- **`spatial_uncertainty` (1.4)**: how far off-center a press still counts as the intended key, in key-widths.
- **`confidence_threshold` (0.65)**: minimum *absolute* score for `should_autocorrect` to fire - the first gate.
- **`autocorrect_margin` (1.5)**: *relative* gate. The correction's score must clear `typed_baseline * autocorrect_margin`, where `typed_baseline = log1p(1) approx 0.69` for plausibly-shaped typings (vowel + consonant) and `0` for implausible slop. This is the LatinIME / Gboard "the literal typed word competes against corrections" pattern - keeps autocorrect from stomping on deliberate typings like "thru", "lol", "btw" while still letting obvious typos through. Implausible inputs ("xqz", "thx") fall back to the absolute threshold alone since their baseline is 0.
- **`prediction_weight` (0.6)**: weight applied to fuzzy candidates in the hybrid merge.
- **`min_prob` (0.001)**: beam-search pruning threshold inside candidate generation - low enough that a single substitution survives across a 5+ char word.
- **`_TRANSPOSITION_PROB` (0.30) / `_DELETION_PROB` (0.20) / `_INSERTION_PROB` (0.15)**: per-edit penalties for the edit-distance candidate path (alongside the spatial beam search), so "teh" -> "the", "thee" -> "the", "th" -> "the" all surface.
- **`_APOSTROPHE_INSERTION_PROB` (0.50)**: insertion of `'` specifically, bumped well above the generic letter-insertion penalty because missing apostrophes ("im" -> "I'm", "dont" -> "don't") are by far the dominant insertion error in real typing on a low-precision OSK.

To tune, override the class attributes on `FuzzyRecognizer`. There's no UI for it.

The spatial layout (`QWERTY_POSITIONS`) covers a-z plus 0-9 - the digit row sits at row -1 directly above qwerty (5 above t, 6 above y, etc.) so an off-by-one-row mistype between letter and digit ("h3llo" -> "hello") is recoverable. Punctuation and the numpad are deliberately unmapped: punctuation has a different error mode, and the numpad is spatially isolated from letters and has no dictionary to correct against. If you add a new layout (Dvorak, Colemak), mirror this - letters + digit row only.

## Testing

```bash
python -m pytest                    # All tests
python -m pytest tests/test_keyboard_bridge.py  # Bridge tests
python -m pytest -k "fuzzy"         # Fuzzy recognizer tests
python -m pytest -k "property"      # Property-based suites only
```

Linting: `ruff check src/`, type checking: `mypy src/`

### Property-based tests

`tests/test_property_import_hardening.py` and
`tests/test_property_prediction_invariants.py` use Hypothesis. They exist
because the things they cover are the ones where hand-picked examples are
weakest: adversarial *inputs* (an archive member or pack folder can be
named anything) and adversarial *orderings* (an incrementally maintained
counter breaks on the sequence nobody thought to write down).

- **Import hardening**: the property is "nothing outside the destination
  directory is ever created, modified or removed", asserted end-to-end
  against a sandbox holding a canary tree, plus the allow-list invariants.
  Note the deliberate inverse test (`test_the_legitimate_layout_is_still_accepted`):
  an allow-list that rejected everything would satisfy every containment
  property while silently turning import into a no-op.
- **Engine invariants**: `_user_total == sum(user_vocab.values())` checked
  after *every individual* mutation across generated operation sequences,
  so a failure names the operation rather than the sequence. Plus the
  spatial model's normalisation and the `_context_buffer` / `_current_word`
  accounting.

Determinism is load-bearing: the `alpha-osk` profile in `tests/conftest.py`
sets `database=None` and `derandomize=True`, so these cannot pass locally
and fail on CI from a stale `.hypothesis` corpus. Use
`--hypothesis-profile=alpha-osk-fast` (25 examples) while iterating. **Don't
add `assume()` to filter a generated value down to a narrow case** — it
throws away most examples and trips the `filter_too_much` health check;
build a strategy that generates the interesting shape directly, and branch
on the predicate instead of discarding.

**Two traps these tests already hit**, worth knowing before writing more:
- The bridge's buffer accounting is a per-keystroke *delta*, not a mirror
  of the screen. Space with no word in progress deliberately commits
  nothing, so an equality model flags a non-defect.
- Privacy mode must be set via `setPrivacyMode()`, not by poking
  `_privacy_mode`. Every keystroke calls `_check_password_field_sync()` to
  close the 200 ms polling race, and that overwrites a hand-set flag on the
  first press; only `_privacy_mode_manual` makes it stand down.

### Pre-push check

Run `python check.py` before `git push` to catch lint / format / type /
test failures locally instead of waiting for CI's red X (the same four
gates GitHub Actions runs).  Default mode skips coverage tracking
(~85 s); add `--full` to include the `--cov-fail-under=60` gate
(~3 min, matches CI exactly).

The `format` step is `ruff format --check src/ tests/`, and it is a
separate gate from `ruff` because `ruff check` does not look at layout.
Fix a failure with `ruff format src/ tests/` rather than by hand.  The
`ruff-format` hook in `.pre-commit-config.yaml` only helps contributors
who ran `pre-commit install`, which is why the tree had drifted in 46 of
57 files before the gate existed. The hook is pinned to a commit SHA
matching CI's `ruff==0.16.2`, so a contributor's local `--fix` pass and
CI never disagree on a rule version.

## Word Suppression and Boosting

Users can right-click prediction pills to:
- **Show more** - clears any prior dispreference and bumps `ngram_predictor.unigrams` / `user_vocab` by +5 (same magnitude as the prediction-click reinforcement), then records the boost in `ngram_predictor.preferred` so the dashboard can surface it and the user can roll it back.
- **Show less** - increments `ngram_predictor.dispreference` (word is downweighted by `1 / (1 + count * 0.5)`)
- **Remove** - adds to `ngram_predictor.blacklist` (word never appears again)

All three are persisted in `ngram_model.json`. Suppression is applied in `hybrid_predictor._merge_predictions()`; boosting is implicit in the bumped unigram counts (no separate multiplier - the engine treats a boosted word the same as a heavily-typed word).

### Boost rollback math
`unprefer(word)` decrements `unigrams` / `user_vocab` / `_user_total` / `total_words` by the cumulative boost amount, capped at the current `user_vocab` count so a word that was also organically learned keeps its organic count after the boost is removed. The `preferred` entry is then dropped. Boosts are never applied to bigrams or trigrams.

### Restoring Suppressed and Boosted Words
In the Model Visualization dashboard (Settings -> Your Language Model -> Open Dashboard -> Dashboard tab), three sections surface user-adjusted words as clickable tags:
- **Boosted Words** - green tags labelled `word (+N)` where N is the cumulative boost. Click to call `keyboard.unprefer(word)` which rolls back the boost (see math above).
- **Suppressed Words -> Blocked** - red tags for blacklisted words. Click to call `keyboard.unblacklistWord(word)`.
- **Suppressed Words -> Downweighted** - yellow tags for dispreferred words. Click to call `keyboard.undisprefer(word)`.

Each section is hidden when the corresponding list is empty (`preferredCount > 0`, `blacklistCount > 0`, `dispreferenceCount > 0`).

Bridge slots: `keyboard.markGoodSuggestion(word)`, `keyboard.markBadSuggestion(word)`, `keyboard.blacklistWord(word)`, `keyboard.unprefer(word)`, `keyboard.unblacklistWord(word)`, `keyboard.undisprefer(word)`.

### Auto-Rehabilitation
If a user manually types a blacklisted word 3 times (completing it with space), the word is automatically restored to predictions. Tracked via `ngram_predictor._blacklist_type_count`, persisted in `ngram_model.json`.

## Model Visualization

Accessed via Settings -> Your Language Model -> Open Dashboard. Three tabs:
- **Word Cloud** - circle-packed bubble chart of top words, sized by frequency
- **Word Flow** - network graph of bigram word->word connections
- **Dashboard** - embedded AnalyticsDashboard (lifetime/session typing stats) at top, then stats cards, top words bar chart, interactive boosted words, interactive suppressed words, top word pairs. The AnalyticsDashboard was previously a separate section at the top of the Settings panel; it was moved here because lifetime savings is user-typing data and belongs with the rest of the user's model.

Data provided by `keyboard_bridge.getVisualizationData()` -> `ModelVisualization.qml`.

### Click-to-drill-down

Clicking a circle in the Word Cloud or a node in the Word Flow opens a side panel with that word's top successors (bigram `word -> next`), top predecessors (`prev -> word`), and trigram windows (`X word Y` middle position + `X Y word` trailing position). Predecessor / successor entries are themselves clickable - click "asked" under "claude"'s predecessors to drill into "asked". Data comes from `keyboard.getWordContext(word)` (bridge slot) which reads `ngram.bigrams` / `ngram.trigrams` directly - no extra tracking. Hit-testing is canvas-side: a `MouseArea` over each canvas walks `circles[]` / `nodes[]` and matches against squared-distance-to-center, so the click target is the visible circle. Selected node is outlined white; the drill-down panel slides in from the right at z=5 over Cloud and Flow tabs (hidden on Dashboard since it has its own Suppressed Words drill-in).

### Live pulse on the active edge

While the visualization window is open, typing in the foreground app pulses the matching node and edge. Driven by `KeyboardBridge.activeContextChanged(prev_word, current_partial)`, emitted from `_update_predictions` on every keystroke (suppressed in privacy mode - must not leak password chars or password-field context). The viz holds `activePrevWord` / `activeCurrentWord` properties and the canvases compare `n.word === activePrevWord || n.word === activeCurrentWord` per node and `from.word === activePrevWord && to.word === activeCurrentWord` per edge. Active node gets a warm gold glow + `#ffd84d` border; active edge draws gold with thicker stroke. The signal is intentionally cheap - raw lowercased tokens, no formatting - and the viz drives a short pulse off the property rebinding, no Timer per canvas.

## Privacy Mode & Password Detection

Protects sensitive input (passwords, PINs) from leaking into the prediction model.

### How it works
- **Auto-detection** (Windows): Two complementary paths call `is_password_field()` from `src/platform/password_detect.py`:
  1. A background `QTimer` polls every 200ms (`_check_password_field`). Catches focus changes that happen between keystrokes.
  2. **Every keystroke** (`pressKey`/`pressSpecialKey`) also calls `_check_password_field_sync()`, rate-limited to ~50ms via `_last_sync_password_check`. Closes the race window where the first characters after focus lands on a password field would otherwise reach the prediction cache before the timer fires.
- Detection uses Windows UI Automation COM (`IUIAutomation::GetFocusedElement` -> `UIA_IsPasswordPropertyId`) in native apps and browsers. Falls back to Win32 `EM_GETPASSWORDCHAR` if UIA fails.
- **Manual toggle**: the **Learning** switch in the title bar (static label + a sliding knob; accent when on, red when paused). Overrides auto-detection. Two earlier designs (a play/pause Canvas icon, then a text label that swapped between "Learning" and "Paused") both had to be read and interpreted; see the title-bar bullet in *Things to Watch Out For* for the full rationale.
- **When auto-detection has no working backend this session** (`detection_available()` in `password_detect.py`, surfaced read-only as `KeyboardBridge.passwordDetectionAvailable`): the null-detector fallback now logs a WARNING at startup instead of failing silently, and the UI says so in two low-key places rather than a dialog to dismiss: the Learning switch's tooltip appends "(auto-detect unavailable this session: this is your only protection)", and *Settings -> Data & Privacy -> Privacy* shows an amber note pointing at the switch. Detection fails open by design, so a backend that can never turn on still lets typing proceed; the manual toggle is the only thing between a password field and the model until it's fixed.
- **When active**: Keystrokes still reach the OS, but `_current_word`, predictions, and learning are all suppressed. The prediction bar shows "Learning paused".

### Clearing stale context when focus or the caret moves

Three independent signals, all polled by `_check_foreground_window` at 4 Hz, each catching what the one before it cannot:

1. **The foreground window** (`GetForegroundWindow` / `xdotool getactivewindow`). Catches an app switch.
2. **The focused element** (`focused_element_token`, UIA RuntimeId, Windows only). Catches the caret moving between two controls *inside* one window, e.g. two text boxes on a web page.
3. **The caret position** (`caret_position_token`, `GetGUIThreadInfo` on the foreground thread, Windows only). Catches what (2) structurally cannot: a move *within the same control*, such as clicking from one paragraph to another in a single text box, and the common case where UIA reports one element for a whole web document so two fields share a RuntimeId.

All three treat `None` as "don't know" and leave state untouched, so a transient failure never wipes context. Most browsers and Electron apps publish no caret at all, which makes (3) a silent no-op there rather than a source of false resets.

`_check_caret_moved` carries two guards, and both matter:

- **Typing moves the caret too.** `_keystroke_since_poll` is set by `_press_char` and `pressSpecialKey` and cleared on each poll, so a move we caused is never mistaken for one the user made.
- **Only between words** (`_current_word` must be empty). A reset mid-word is the dangerous direction: it clears `_current_word` while the partial word is still on screen, so the next pill tap inserts the whole word beside it, which is the "backspacbackspaces" duplication the rehydrate logic exists to prevent. Scrolling also drags the caret rectangle across the screen without the caret moving in the text, and that is the false positive most likely to land mid-word. Waiting for a word boundary costs the mid-word case, where a stale context matters least because the user is about to finish the word anyway.

### Key files
- `src/platform/password_detect.py` - platform-specific detection (UIA COM via ctypes), plus `focused_element_token` / `caret_position_token`
- `src/keyboard_bridge.py` - `_privacy_mode` flag, `_check_password_field()` timer, `_check_password_field_sync()` per-keystroke, `setPrivacyMode()` slot, `passwordDetectionAvailable` read-only property

### Linux
Auto-detection uses AT-SPI 2 via `gi.repository.Atspi`. A daemon thread owns a GLib event loop and listens for `object:state-changed:focused`; whenever focus lands on an accessible whose state set contains `STATE_PASSWORD_TEXT`, the shared `_is_password` flag flips on. Works for GTK (`GtkEntry` with `visibility=false`), Qt (`QLineEdit` in Password echo mode), and browsers that expose accessibility metadata. Requires `gir1.2-atspi-2.0` + a working at-spi bus on the host. If `gi` fails to import or `Atspi.init()` fails, falls back to the null detector and now logs a WARNING once at startup instead of failing silently (`detection_available()` surfaces this to the UI, see *How it works* above) - users can still toggle privacy mode manually.

## Themes

Defined in `themeData` in `Main.qml`. Each theme has: `name`, `background`, `keyColor`, `keyPressed`, `textColor`, `accent`, `border`.

**9 themes**: Dark, Light, Ocean, Forest, Amethyst, Vaporwave, Blackboard, Typewriter, Spaceship.

Theme colors flow to all components: main keyboard keys, prediction pills, nav panel, numpad, title bar icons, and active key states (NumLock, Shift, etc.). `KeyButton.qml` auto-computes text contrast on active/pressed states using luminance.

Theme picker in settings shows labeled color swatches with mini key previews.

## Vocabulary

- **Base**: Google 10K wordlist (`data/google-10000-english-usa-no-swears.txt`) + 10K supplement (`data/google-20000-supplement.txt`, filtered for explicit content). ~20K total regular words.
- **Packs**: No built-ins ship. The system is import-only - see *Vocabulary Packs* section. Imported packs appear as toggles in Settings -> Your Language Model -> Vocabulary Packs.
- **Numpad**: Toggles between numbers and navigation keys (Home/End/PgUp/PgDn/arrows/Ins/Del) via NumLock. Key 5 is blank in nav mode. Layout mirrors a physical numpad: rows `7 8 9 /`, `4 5 6 *`, `1 2 3 -`, `0(span 2) . +`, `Enter(span 3) NumLock`. NumLock sits at the bottom-right (active highlight uses the theme accent), Enter is the wide bottom-row key. Earlier builds put NumLock on the top row and stretched `+` / Enter as 2-row spans on the right column. The flat 5-row layout was the user's request to match a physical 10-key.

## Vocabulary Packs

Import-only. **No built-in packs ship.** Earlier releases shipped six (medical / programming / academic / gaming / business / nsfw) but each was 200-400 words - too thin to compete with personal learning, which bumps a word's score by +5 every time the user accepts it as a pill. After typing "physical therapy" three times, the user's own model already knows it, and the seed list saves nothing. Sourcing a real domain vocabulary (SNOMED-grade for medical, full API surface for programming) is its own project and runs into licensing rabbit holes; curated 300-word lists were strictly worse than no shipped packs at all. They were also drifting in maintenance (NSFW had a different `pack.json` schema and no n-grams) and there was an open correctness bug (see *Known limitations* below).

### What the system still does
- `src/prediction/vocabulary_pack.py` (`VocabularyPack`, `PackManager`) discovers packs from `data/packs/` (now absent) and from the user dir (`%APPDATA%/alpha-osk/packs/` Windows, `~/.config/alpha-osk/packs/` Linux). The user dir is created on first launch.
- Pack format: a folder containing `dictionary.txt` (required, one word per line, `#` comments allowed), optional `bigrams.txt` (whitespace-separated word pairs), `trigrams.txt` (word triples), and `pack.json` (`{name, description, version}` - generated automatically if missing on import).
- Settings -> Your Language Model -> Vocabulary Packs shows one toggle per imported pack (driven by `keyboard.getAvailablePacks()` returning the rich `{id, name, description, version, words, bigrams, trigrams}` list - the `id` field is the directory name and `VocabularyPack.get_info()` includes it explicitly so the QML side can call enable/disable). A pack's `name`/`description` are attacker-controlled strings read from an imported `pack.json`, so they pass through `_clean_meta_text` on load: collapsed to a single line, bounded to `_MAX_PACK_META_FIELD_LEN` (200), and replaced by the directory name / empty string if they are not strings at all. That's for the *log*, which this module writes the pack name into on every cap trip and which users attach to bug reports, so an embedded newline would let a pack name forge whole log lines. Separately, the `Text` elements that render them set `textFormat: Text.PlainText` so an `<img>` tag in a pack name can't make Qt fire an outbound request just from being displayed (see *Things to Watch Out For*).
- Empty state: just the "Import Custom Pack..." button + a one-line note about the format. The hardcoded `[{id: "medical", label: "Medical"}, ...]` Repeater that drove the old UI is gone - adding a new pack only requires importing it (or, in a future release, dropping a folder under `data/packs/`); no QML edit needed.
- Import hardening (security-critical, **don't loosen**): folder name sanitised to `[a-z0-9_-]{1,64}` and rejected outright if it collides with a Windows reserved device name (`con`, `prn`, `aux`, `nul`, `com1`-`9`, `lpt1`-`9`, checked case-insensitively on the base name before any extension). Those names pass the id regex but would fail `mkdir` on Windows. Resolved destination verified to sit strictly under `user_packs_dir` before any `rmtree`/`copytree`, symlinks inside the source tree are skipped rather than dereferenced. Built-in packs (if any) cannot be overwritten via import. See `tests/test_vocabulary_pack.py::TestImportPackSecurity` for the regression coverage.
- Load and import size caps (packs previously had none; every sibling loader in the codebase already capped its input): `pack.json` metadata capped at `_MAX_PACK_META_BYTES` (64 KB), each of `dictionary.txt`/`bigrams.txt`/`trigrams.txt` capped at `_MAX_PACK_FILE_BYTES` (20 MB), and the whole source folder capped at `_MAX_PACK_IMPORT_TOTAL_BYTES` (50 MB, walked and checked before `import_pack`'s `copytree` starts). These are whole-file rejections. Separately, `load()` caps entries at `_MAX_PACK_WORDS` / `_MAX_PACK_BIGRAM_ENTRIES` / `_MAX_PACK_TRIGRAM_ENTRIES` (200 000 each); unlike the byte caps this is discovered mid-iteration, so a file under the byte cap but with millions of short lines is truncated and kept rather than rejected outright. See `tests/test_vocabulary_pack.py::TestPackInputCaps`.

### Known limitations
- **Disabling a pack does not undo its predictor injection.** `apply_to_predictor` writes pack words into `predictor.unigrams / .bigrams / .trigrams` with `max()`. `disable_pack` calls `pack.unload()` which clears the *pack's own* in-memory copy, but the entries it pushed into the predictor stay there until the next process restart. Mostly invisible now that no built-ins ship (only users who imported a pack and then disabled it without restarting hit this), but worth fixing if we ever ship built-ins again. The clean fix is to track per-pack `(word, prior_value)` tuples at apply time and revert on disable, with a guard that only reverts when the predictor's current value still equals the pack's contribution (so words that piled on organic learning after enable aren't clobbered).
- **`apply_to_predictor` uses `max()` for bigrams/trigrams, not addition.** Earlier comments in this file claimed bigrams/trigrams were "additive with weight 30" - that was the doc, not the code. Code is correct: additive would compound on every enable cycle. The doc is now consistent.

### Re-introducing a built-in pack
If a future release ships a built-in pack, mirror it back into `data/packs/<id>/` with the four files described above. PackManager's `_discover_packs` will pick it up automatically (it iterates both built-in and user dirs). Add a parametrised structural test back to `tests/test_vocabulary_pack.py` modelled on the deleted `TestRealPacks` class - the `sample_pack_dir` fixture in that file shows the expected shape.

## Analytics

`src/analytics.py` tracks session and all-time stats. All-time stats persist to `<config_dir>/analytics.json`.

Every session counter has an `_alltime_*` mirror that's loaded on launch, merged with the session at exit, and surfaced in `get_session_stats()` as both `<metric>` (session) and `alltime<Metric>` (lifetime). The dashboard's Lifetime / Session toggle (`AnalyticsDashboard.qml`) drives every tile off these paired keys. Persisted fields include: keystrokes, words, predictions (hits), keystrokes_saved, sessions, minutes, **backspaces, prediction_offers, prediction_rank_sum/count, top_pick_count, word_freq, key_freq**. Word and key frequencies are each capped at 5000 unique entries (`_WORD_FREQ_CAP` / `_KEY_FREQ_CAP`), applied on both load and save (top-N by count), so `analytics.json` stays bounded over years of typing. `_load_alltime` also `stat()`s the file against `_MAX_STATS_FILE_BYTES` (5 MB) before reading it at all, and parses into local variables first, only assigning to `self` once every field has parsed successfully, so a bad type or a cap trip partway through a load can never leave the in-memory lifetime counters half-updated.

The dashboard is now a **single section**: scope toggle (Lifetime / This Session) + 2x2 tile grid + sparkline + top words. Earlier versions layered a separate hero card ("10.3k keystrokes saved" with green border), an all-time stats pill row (words / sessions / hours), and a horizontal divider above the tile grid; the user reported it read as 4 disconnected sections rather than one analytics view. Promoting Keystrokes Saved into the tile grid carries the headline number, and the words/sessions/hours pills were dropped (sessions and hours weren't load-bearing; words is implicit from the prediction-related tiles).

The four tiles are **Keystrokes Saved** (formatted count, subtext "keys you didn't have to press"), **Time Saved** (formatted hours/min from `keystrokes_saved x user's own seconds per keystroke`, falling back to 0.5s/key for new installs; subtext "avoided by predictions"), **Effort Saved** (`savingsPercent`, subtext "of total keystrokes"), and **Acceptance** (`acceptanceRate` = `prediction_hits / prediction_offers`, subtext "of offered suggestions accepted"). Keystrokes Saved + Time Saved + Effort Saved are three framings of the same underlying engine output: absolute count, wall-clock, and percentage respectively. They're shown together because each lands differently with different mindsets (a daily-saving thinker, a wall-clock thinker, a relative-effort thinker). Acceptance is **distinct from** the others: it asks "when the keyboard offered a suggestion, how often was it useful enough to take" (an engine quality signal), independent of how many keystrokes the user typed total. All four subtexts are deliberately verbose ("of total keystrokes" not "of typing effort") to make the denominator unambiguous; the user iterated on terser variants and found them ambiguous.

Earlier iterations also had **Typing Effort** (total keystrokes typed) and **Predictions Used** (hit rate %) and **Corrections** (backspace count) tiles. All three metrics are still tracked and exposed in `getAnalytics()` (`alltimeKeystrokes`, `predictionHitRate`, `alltimeBackspaces`, etc.) because the Model Visualization Dashboard and other callers may use them; only the AnalyticsDashboard surface dropped them. WPM lived on the first tile briefly but was unusable on cold sessions (a fresh "0.5 avg wpm" reading next to a lifetime "103 hrs saved" hero card visually contradicted itself).

The `StatBox` component grows its background Rectangle from `contentCol.implicitHeight + 14` rather than using a fixed `implicitHeight: 50`. The fixed height was ~10 px shorter than the three text elements need, so subtext rendered past the rounded gray background. If you add a fourth Text element to StatBox, this binding still works as long as the inner ColumnLayout is anchored only horizontally + verticalCenter (don't switch to `anchors.fill: parent`, which would break the implicit-height computation by yoking layout size to rectangle size).

The earlier composite Prediction Quality Score (0-100, weighted savings + hit rate + rank + low-correction) was removed because the number wasn't actionable: a user can act on "you've saved 4.2 hours" but a "73/100" composite hides which lever moved. Don't reintroduce the composite as a primary surface; if you need a single internal scoring number for ranking strategy comparisons, compute it ad-hoc in tests rather than baking it back into `get_session_stats`.

`top_pick_count` is still computed and persisted (incremented inside `record_prediction_selected` only when `rank == 1`) and surfaced as `alltimeTopPickRate` for the Model Visualization Dashboard. It was briefly the subtext on the Predictions Used tile but reads "0%" for any user upgrading from a prior build (the counter didn't exist then), which masked real usage.

`top_pick_count` is incremented inside `record_prediction_selected` only when `rank == 1`. The bridge already passes a 1-based rank in `pressPrediction`, so no caller-side change is needed when adding new prediction surfaces. They just need to call `record_prediction_selected` with the right rank.

## Prediction & Autocorrect - Architecture Notes

Full notes in **`docs/architecture/PREDICTION_NOTES.md`** (the "unified system" framing, fragment filter + repetition gate, autocorrect thresholds, reinforcement-on-click, backspace-as-negative-signal, the prioritized future-work gaps, and reference implementations). Per-algorithm deep dives: `FUZZY_RECOGNITION.md`, `PPM.md`, `HYBRID_MERGING.md`, `SWIPE_TYPING.md`.

Load-bearing defaults to keep in mind: **space-time autocorrect is OFF by default** (`KeyboardBridge._autocorrect_enabled = False` - corrections surface as pills, never silent overwrites); the autocorrect gate skips typings under 3 chars and runs an absolute + relative threshold so deliberate typings ("thru", "lol") survive; n-gram scoring is linear interpolation in probability space (lambda = 0.5/0.3/0.2); unknown words promote into `user_vocab` only after 3 sightings (pill clicks gated the same way).

## Compact View

A denser 13x4 keyboard for small screens. Off by default; toggle in *Settings ->
Appearance -> Panels -> Compact View*. Design doc + measurements:
`docs/architecture/COMPACT_VIEW.md`.

Load-bearing facts:

- **Every row in a compact layout must total the same unit count** (13.0 for
  `qwerty-compact`). `Main.qml` centres any narrower row, so an unequal row
  brings back the exact side gutters this view exists to remove. Enforced by
  `tests/test_layouts.py::TestCompactLayout::test_every_row_is_exactly_13_units`.
- **Layers are a QML-side view concept - the backends never see them.** Rows may
  carry a `"layer"` field; `Main.qml` filters `layoutRows` into `visibleRows` by
  `root.activeLayer`. Rows *without* a `layer` field always render, which is what
  keeps the full-size layouts working. A `"type": "layer"` key sets
  `activeLayer`; it deliberately does **not** call `keyboard.setLayout()` (that
  would persist as the user's layout preference and make `getCurrentLayout()`
  report the symbol layer). `activeLayer` resets to `"base"` on every layout
  change - stranding the user on a `sym` layer the next layout doesn't define
  would render an empty keyboard.
- **Because it's data + QML only, it needed zero backend work on either Python
  or C++.** Don't "port" it; both backends get it from the shared `qml/` +
  `data/` contract.
- **`totalKeyUnits` is now derived, not hardcoded.** `_widestRow` in `Main.qml`
  computes the widest visible row's units + gap count. Full-size layouts resolve
  to exactly the historical 15.5u / 14 gaps, so the default 940 px window is
  unchanged. Don't reintroduce the constant.
- **Compact is orthogonal to letter arrangement.** `currentLayout` stays
  qwerty/dvorak/colemak; `resolveLayoutId()` combines it with the `compactView`
  bool into `<layout>-compact`. A layout with no compact variant falls back to
  full size, so the toggle is always safe. Adding compact Dvorak = drop
  `data/layouts/dvorak-compact.json` in place, no code change.
- **The nav column reads Home / PgUp / PgDn / End top to bottom** (a scroll
  ladder: top, page up, page down, bottom). Owen asked for Home above PgUp.
  Pinned by `test_layouts.py::TestCompactLayout::test_nav_column_reads_top_to_bottom`.
- **Del sits on the base layer, Esc on `?123`.** A 13u row has no spare unit, so
  the two traded places; Esc was the only base-layer key not in the protected
  "never behind a hop" set. Don't swap them back without reading the rationale
  in `docs/architecture/COMPACT_VIEW.md`. The Number Row panel (below) puts a
  second Esc back at the top-left; that duplicate is deliberate, so `?123`
  stays the fallback for any future layout that shows the compact grid without
  the panel.
- **`:` has its own key on `?123` row 3**, in the slot `^` used to hold. Row 2
  already carried `;`→`:`, but a shifted variant is invisible (the keycap reads
  `;`), so the layer read as having no colon.
- **The symbol pages carry no Shift key; Shift's slot switches to a second
  page (`=\<`), the phone convention.** Shift on `?123` used to re-render row 1
  as `! @ # $ % ^ & * ( )` while row 3 already showed `! @ # $ % : & ( )`
  permanently: nine keys on screen saying the same thing as another key on
  screen. Making the shift-position key a page switch means every glyph Shift
  used to reach has a key of its own, so the overlap is *structurally
  impossible* rather than merely absent. `sym2` holds what `?123` lacks:
  `~ ^ * _ + { } | < >`, then maths (`° × ÷ ± ≈ ≠ ≤ ≥`), then currency and
  legal (`€ £ ¥ ¢ § • © ® ™` - the bullet sits in the pilcrow's slot; it was
  briefly on the bottom row, where it displaced the period every other layer
  has there). All three layers are 13.0u with matching key counts
  (12/13/12/11), so hopping pages never resizes a key. **The bottom row and
  the right-hand nav column are byte-identical on every layer**, and the tests
  that guard that derive the layer list from the file rather than naming
  base/sym: written against a hardcoded pair they were blind to `sym2`, which
  is how the bullet shipped green.
  **The `shifted` fields stay on the symbol keys** even though no Shift key can
  reach them: right-click still types the shifted variant, and that is a
  bonus rather than a duplicate, because right-click output is never
  displayed. **`Main.qml`'s layer branch calls the idempotent
  `keyboard.releaseShift()` on every switch** (never
  `if (shiftOn) toggleShift()` - `root.shiftOn` is a mirror kept alive by
  signal delivery, not a live binding, so a flip could turn Shift *on* here)
  and that is load-bearing: the modifier is held at the OS level, so a Shift
  carried in from the letters page would make `1` emit `!` while the keycap
  still read `1`, and the pages have no Shift key to clear it from. Caps is
  left alone (it only affects letters). Guarded by
  `tests/test_layouts.py::TestNoDuplicateGlyphsWithinALayer` (in particular
  `test_shifted_variants_never_duplicate_a_visible_key`, which states the
  property rather than the fix, so it catches an equivalent overlap on any
  layer that keeps a Shift key) and
  `tests/test_qml_compact_view.py::TestSecondSymbolPage`.
- **Esc, Tab, Shift, Backspace and Del are accent-filled on the compact layouts**
  (`"style": "accent"` in the layout JSON, resolved by `root.accentKeyColor` in
  `Main.qml`). The compact grid is uniform, so unlike the full-size layouts
  there are no size cues to tell the editing keys apart from the letters, and
  they have to be findable by colour. The fill is a **wash of the accent over
  the theme's key colour, not the raw accent**: three themes have a pale accent
  (Blackboard `#ffffaa`, Spaceship `#00ff9f`) and Typewriter is a light theme
  with near-black text, so a saturated fill would destroy the label contrast.
  Same reason Enter is a muted `#2a5a2a`. **The wash strength is derived, not
  fixed**: a flat 35% was measured against all nine themes and dropped the
  label below WCAG AA on five of them (Blackboard 6.19:1 -> 2.66:1, Vaporwave
  6.17 -> 2.97, Forest 7.53 -> 3.33, Spaceship 10.37 -> 3.85, Ocean 6.96 ->
  4.44), which is the worst place to lose contrast because these are the keys
  the style exists to make findable, and Forest could not be rescued by
  swapping the label to black or white either (best case 4.37). So
  `root.accentWashFor()` walks the alpha down from 0.35 until the theme's own
  `textColor` clears 4.5:1. **Don't reintroduce a constant here.** Accent keys
  also take an accent-coloured border, which carries the cue on the themes
  where the wash has to back off to 0.12-0.21; a border sits beside the label
  rather than behind it, so it costs no contrast. Full-size layouts are
  deliberately untouched. Pinned by
  `tests/test_layouts.py::TestCompactEditingKeysAreAccented` (which keys) and
  `tests/test_qml_compact_view.py::TestAccentKeysStayReadable` (the contrast
  floor, plus the inverse test that the wash is still visible, so "stop
  tinting" cannot pass as a fix).
- **No panel that has to line up with the keyboard grid may use
  `QtQuick.Layouts`.** Number Row and Function Row are plain `Row`s,
  Navigation is a plain `Grid`, Numpad is a `Column` of `Row`s. `Main.qml`
  reserves an exact float unit budget for each panel when it derives
  `minimumWidth`, so a rounding positioner costs pixels the window was never
  given. `QtQuick.Layouts` rounds every child up to a whole pixel, so
  13 keys of 69.23 px each became 13 of 70 and the panel rendered 10 px wider
  than the keyboard grid it is supposed to sit flush with, overhanging the
  window and clipping its last key. The keyboard rows are plain `Row`
  positioners, which keep `keyW` as the float it is; a panel that sizes itself
  any other way cannot line up with the keys underneath it. Guarded by
  `tests/test_qml_compact_view.py::TestPanelsSitFlushWithTheGrid`, which
  asserts the panel width equals the widest keyboard row rather than merely
  fitting the window, because "fits" was already true of the broken version at
  some widths.
- **Digits come back via a panel, not a fifth row, and not via a toggle.**
  `qml/components/NumberRow.qml` (13 x 1u, flush with the compact grid) renders
  above the keyboard whenever `Main.qml::showNumberRow` is true, which is
  derived: true exactly when the active layout JSON carries no `number` row of
  its own. That is the compact variants and nothing else, so digits are always
  on screen in both views and a full-size layout can never end up with a
  second, narrower number row stacked on the one built into its JSON. Keying it
  off the layout rather than `compactView` matters because a letter arrangement
  with no compact variant silently falls back to full size. Every key must register through
  `registerFn` or the swipe overlay swallows every tap on it. Its leading key
  is **Esc, not `` ` ``** (backtick lives on `?123` row 2), and Esc registers
  as a `special`, not a `char`: that keeps it hit-testable while keeping a
  phantom "Esc" centre out of the swipe shape match. See the two-registry
  note under *Swipe / Glide Typing*.
- QML-only behaviour can't be covered by the Python suite, so
  `tests/test_qml_compact_view.py` and `tests/test_qml_prediction_bar.py` load
  the real `Main.qml` headlessly (`QT_QPA_PLATFORM=offscreen`) and fail on QML
  warnings. That's the only guard against a binding error shipping as a blank
  keyboard.

## Modular Layouts

Design doc at `docs/architecture/MODULAR_LAYOUTS.md`. Inspired by Octavium's (`C:\Users\Owen\dev\Octavium`) Layout/KeyDef data model. Four levels of modularity: (1) Built-in JSON layout packs (video editing, gaming, streaming). (2) User-created layouts via editor. (3) Panel composition - snap independent panels (QWERTY, numpad, macros) into a grid. (4) App-aware auto-switching based on foreground window.

Action types: `char`, `special`, `hotkey`, `text`, `macro`, `launch`, `layout`, `midi`. Profiles bundle layout + theme + window position + auto-switch rules.

## Auto-Update

Implemented in `src/updater.py`. Flow walkthrough, threat model + defences table, and the per-defence rationale all live in `docs/build/AUTO_UPDATE.md`. Release checklist is in `docs/build/WINDOWS.md`.

> **Releases live in a separate public repo** - `okstudio1/alpha-osk-releases`. The updater's API URL is hard-pinned to that repo, so `gh release create` must always pass `--repo okstudio1/alpha-osk-releases`. (Historical note: the source repo `owenpkent/alpha-osk` was private until 2026-05-16; the split was originally a private/public boundary, and is now preserved because the pinned updater URL relies on the releases repo being its own canonical source-of-truth.)

Version source of truth is `src/__version__.py`. The release-asset filename **must** match `Alpha-OSK-Setup-{version}.exe` exactly - the updater rejects anything else. User-facing toggle: *Settings -> Data & Privacy -> Updates -> "Check for updates on startup"* (persisted as `appSettings.savedAutoCheckUpdates`).

**Install path is pinned, not read from the registry.** The generated NSIS installer no longer declares `InstallDirRegKey HKCU`: nothing in the build ever wrote that key, so it was a dangling read of a user-writable registry value, and the silent `/S` auto-update path honoured it anyway. `updater.py::_install_target_dir()` computes the directory itself (the currently-running frozen exe's own parent directory, or the `%ProgramFiles%\Alpha-OSK` default when not running frozen) and every silent install launches with an explicit `/S /D=<dir>`. NSIS's `/D=` has strict syntax: it must be the last parameter on the command line and unquoted even when the path contains spaces. Don't reorder the installer arguments or add quotes.

**Signature verification also pins the version.** `_verify_signature(exe_path, expected_version)` still checks the Authenticode chain (status `Valid`, cert thumbprint, signer CN) but now also reads the downloaded exe's embedded `FileVersion` (via a PowerShell `Get-Item ... .VersionInfo.FileVersion` call, comparing only the first three components since `FileVersion` is often four-part) and requires it to equal `expected_version`. This matters because the release asset filename (`Alpha-OSK-Setup-{version}.exe`) is a selector, not a trust boundary: someone who could rename or re-upload a release asset, without being able to forge a signature, could otherwise re-attach an older, genuinely-signed installer under a newer version's name and roll every user back onto a build with known-fixed bugs. Relatedly, `_ps_single_quote_escape` fixes a real bug: a Windows username containing an apostrophe (`%TEMP%` paths embed it) broke the single-quoted PowerShell literal built from the exe path. That failed closed, so it wasn't exploitable, but it silently and permanently disabled auto-update for that user.

### Update progress UI

Full walkthrough (the four pieces from "user clicks install" to "new keyboard appears", plus the v1.0.19 file list) is in `docs/build/AUTO_UPDATE.md`. The non-obvious bits to remember: **never expose the download URL to QML** (the bridge only emits primitive ints); the pre-install toast sleeps `_PRE_INSTALL_TOAST_DWELL_S` (1.8 s) in the worker so it paints before the installer's taskkill; the relauncher splash is a `QTimer` state machine with an indeterminate `QProgressBar` (NSIS silent install has no real percentage); `_run_headless` is preserved as the test target and no-display fallback; and `_is_dev_target()` routes `python`/`pythonw` straight to headless so dev runs don't hang waiting for an exe mtime that never changes.

## Accessibility Ecosystem

Design doc at `docs/roadmap/ECOSYSTEM.md`. Alpha-OSK is part of a four-tool adaptive input platform:

| Tool | Repo | Output |
|------|------|--------|
| **Alpha-OSK** | `C:\Users\Owen\dev\alpha-osk` | Keystrokes (SendInput) |
| **MacroVox** | `C:\Users\Owen\dev\MacroVox` | Text (Deepgram STT -> clipboard) |
| **Octavium** | `C:\Users\Owen\dev\Octavium` | MIDI (virtual piano/pads) |
| **Nimbus** | `C:\Users\Owen\dev\Nimbus-Adaptive-Controller` | Joystick (vJoy/ViGEm) |

All four: same developer, same EV cert, PySide6/Qt (except MacroVox: Tauri), mouse-driven, accessibility-first. Integration phases: coexistence -> launch/trigger -> profile auto-switch -> shared input layer -> unified UI.

See also: `docs/roadmap/MACROVOX_INTEGRATION.md` (voice dictation), `docs/architecture/MODULAR_LAYOUTS.md` (custom layouts inspired by Octavium/Nimbus).

## Federated Learning

Design doc at `docs/roadmap/FEDERATED_LEARNING.md`. Not yet implemented - Phase 1 (local delta computation) is the next step.

## Opt-in Telemetry

Design: `docs/architecture/TELEMETRY.md`. User-facing privacy: `docs/PRIVACY.md`. Backend: `backend/cf-worker/` (Cloudflare Worker + D1).

**Off by default.** When enabled (Settings -> Data & Privacy -> Privacy -> "Share anonymous usage stats"), the client sends a weekly POST containing nine integers: `anon_id`, `app_version`, `os`, `keystrokes`, `words`, `predictions`, `keystrokes_saved`, `minutes`, `sessions`, `prediction_offers`. These are exactly the lifetime counters already shown on the Analytics dashboard. **Never sent**: content, word frequencies, key frequencies, IP, hostname, or any per-session breakdown.

Files, endpoint config, anon_id lifecycle, submit cadence, and the worker schema are all detailed in `docs/architecture/TELEMETRY.md`. Load-bearing facts:
- **`DEFAULT_ENDPOINT` in `src/telemetry.py` is the empty string** - while empty the client silently no-ops every submit (consent toggle still works, no data leaves the machine). Set it per-build before shipping a telemetry-enabled release; the Windows checklist (`docs/build/WINDOWS.md` step 2a) gates on this.
- **anon_id is cleared on opt-out**, so re-opt-in gets a fresh UUID4 and prior contributions can't be linked. "Delete my contributed data" POSTs to `/v1/forget`. (This is why the Data Backup archive deliberately excludes `telemetry.json`.)
- **`TelemetryClient` is the source of truth for the consent flag** - `UnifiedSettingsPanel.qml` queries the bridge on mount; **don't** mirror it into `appSettings`.
- **Cadence**: weekly `QTimer` (1-hour tick, 7-day window check) plus `submit_on_quit()` from `shutdown()` (60 s anti-spam guard). All paths gated on `enabled AND endpoint AND anon_id`; failures retry `[5s, 30s, 120s]` then drop silently.
- **Privacy mode needs no special handling** - it already suppresses learning/tracking upstream, so password activity never enters the counters telemetry forwards.
- **Worker-side rate limiting** (`backend/cf-worker/src/worker.ts`): two layers, both keyed on `anon_id` and never on a request header. An edge `RATE_LIMITER` binding gates by `submit:<anon_id>`, and a `SUBMIT_COOLDOWN_SECONDS` (3600s) cooldown is enforced inside the D1 upserts themselves. The cooldown `WHERE` clause is on **both** statements in `handleSubmit` (`users` and `submissions_latest`); gating only the second left `users` taking a write per request, so the cooldown bounded half the write path. The clause gates `DO UPDATE` only, so a first-ever submission for an id still lands immediately. Every reject path, rate-limited, cooled-down, or a `/v1/forget` for an id that never existed, returns the same 204 a success would, so a response can never be used as an existence oracle for a given `anon_id`. `app_version` and `os` are validated against a semver regex and a platform enum before being written. Neither layer stops an attacker cycling through many fake `anon_id`s; that needs IP-based throttling, out of scope here.
- **Not telemetry**: auto-update version checks (GitHub Releases requests) and the planned federated-learning feature (its own opt-in + DP-noise design). Keep them conceptually separate.

## Building & Signing a Release (Windows)

Full step-by-step release checklist, signing details, troubleshooting table, and bundle-size notes are in `docs/build/WINDOWS.md` (sections "Building a Standalone Executable", "Code Signing", "Release Checklist"). Asset/icon regeneration in `docs/build/BRANDING.md`. Quick mental model:

1. Bump `src/__version__.py` (single source of truth - `build/windows/build.py` reads from it).
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
- **Exact-pinned dependencies**: `requirements.txt` and `requirements-dev.txt` pin every dependency to an exact `==` version (most were `>=` floors before), so a fresh install is reproducible and an `osv-scanner` hit names a version you can actually go look up. The macOS-only `pyobjc-framework-*` entries are the deliberate exception and stay on `>=` floors. Hash pinning (`--require-hashes`) is a known follow-up, not done yet.
- **CI CVE scanning**: `.github/workflows/ci.yml` runs `osv-scanner` over both lockfiles with `fail-on-vuln: true`. A new advisory blocks every PR - fix the dep or quarantine with a time-boxed `osv-scanner.toml` entry; never flip `fail-on-vuln` off globally.

## macOS build (in progress)

Phase-1 platform support lives in `src/platform/macos.py`
(`MacOSKeySynthesizer` via `Quartz.CGEventCreateKeyboardEvent`) +
NSWindow tuning in `keyboard_app.py::_apply_macos_window_flags`
(float level, all-Spaces collection behavior, `hidesOnDeactivate=NO`).
`"win"` modifier maps to Command (the Cmd key). Config dir is
`~/Library/Application Support/alpha-osk/`. Build pipeline scaffolded
at `build/macos/` (PyInstaller `BUNDLE()` -> `Alpha-OSK.app`, optional
`hdiutil` `.dmg`) but not yet exercised end-to-end. Code signing,
notarization, and auto-update are the explicit follow-up phases.
**Password-field auto-detection is done**, not pending: `_MacOSAXDetector`
in `password_detect.py` resolves the frontmost app's pid ->
`AXUIElementCreateApplication` -> `kAXFocusedUIElementAttribute` and
matches the `AXSecureTextField` subrole, which Cocoa, WebKit and
Chromium all report. It deliberately goes through the frontmost
*application* rather than `AXUIElementCreateSystemWide()`, because the
system-wide element returns `kAXErrorCannotComplete` in practice; don't
"simplify" it back. **First-run gotcha:** macOS requires an
Accessibility TCC grant (System Settings -> Privacy & Security ->
Accessibility) before `CGEventPost` reaches other apps - without it
the OSK UI works but keystrokes silently no-op. The same grant gates
the AX detector, so a missing TCC grant costs you password detection
too, and it fails open (see the fail-open note under *Privacy Mode &
Password Detection*). Full plan + phase breakdown + troubleshooting in
`docs/build/MACOS.md`.

## Linux build

Linux has its own pipeline in `build/linux/` that mirrors the Windows
one but skips the NSIS/signing legs (AppImage is unsigned by design,
and EV signing is Windows-specific).

```bash
venv/bin/pip install pyinstaller          # one-time

python build/linux/build.py               # PyInstaller bundle -> dist/alpha-osk/
python build/linux/build.py --appimage --fetch-appimagetool
                                          # + AppImage -> release/Alpha-OSK-<ver>-x86_64.AppImage
```

Key files:
- `build/linux/alpha-osk.spec` - PyInstaller spec (same exclusions as
  the Windows spec: torch, transformers, QtWebEngine, etc.).
- `build/linux/build.py` - driver; optionally downloads `appimagetool`
  to `~/.cache/alpha-osk-build/` on first `--appimage` run. Pinned to a
  specific tagged release (`1.9.1`, not the mutable `continuous` tag)
  and verified against `APPIMAGETOOL_SHA256` before it is ever executed;
  bump the tag and the hash together, never one without the other. This
  is trust-on-first-use, not independent verification (appimagetool
  ships no signed checksum manifest), but it still catches the actual
  risk: the release asset being swapped, the tag re-pointed, or a
  corrupted/intercepted download, all of which the old `continuous`-tag
  fetch would have silently executed.
- `build/linux/AppRun` - AppImage entry script that points `QT_PLUGIN_PATH`
  / `QML2_IMPORT_PATH` at the bundled Qt and defaults
  `QT_QPA_PLATFORM=xcb`.
- `build/linux/alpha-osk.desktop` - `Categories=Utility;Accessibility;`
  so the app surfaces in accessibility menus once the AppImage is
  integrated.

`xdotool` / `ydotool` are **not** bundled - they're OS-level tools that
must be installed on the host. The bundle will start without them but
key synthesis will silently no-op.

See `docs/build/LINUX.md` for deeper coverage (troubleshooting, AppImage
internals, spec customization).

## Git Conventions

Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`

## Community Files

The repo ships the standard GitHub community health files at the top level and under `.github/`:

- `CODE_OF_CONDUCT.md` - Contributor Covenant 2.1. Reports go to owenpkent@gmail.com with subject `CONDUCT: alpha-osk`.
- `CONTRIBUTING.md` - dev setup, `check.py` pre-push gate, conventions, PR flow. Points new contributors at this file as the architecture map.
- `SECURITY.md` - private vulnerability reporting via the releases repo's GHSA form, email fallback.
- `.github/ISSUE_TEMPLATE/bug_report.yml` and `feature_request.yml` - form templates. `config.yml` disables blank issues and links to the security advisory + Discussions.
- `.github/pull_request_template.md` - summary, type, test plan, accessibility check.

If you change the security reporting flow, the CoC contact email, or the contribution gates, update both the relevant file and the cross-references in `CONTRIBUTING.md` / `bug_report.yml`.

## Known Issues

(IDE prediction-pill duplication is now handled by auto-compat. `_COMPAT_PROCESS_NAMES` covers VS Code + Monaco forks (`code.exe`, `code - insiders.exe`, `cursor.exe`, `windsurf.exe`, `codium.exe`, `code-oss.exe`, `positron.exe`, `trae.exe`) and the JetBrains family (`idea64.exe`, `pycharm64.exe`, `webstorm64.exe`, `phpstorm64.exe`, `clion64.exe`, `goland64.exe`, `rider64.exe`, `rubymine64.exe`, `datagrip64.exe`, `dataspell64.exe`, `studio64.exe`, `studio.exe`). Both groups intercept keystrokes for completion/snippets/multi-caret in ways that break suffix-only insertion. Match on exe basename, **not** window class - `Chrome_WidgetWin_1` (Electron) and `SunAwtFrame` (JetBrains) are shared with too many unrelated apps. Visual Studio (`devenv.exe`), Sublime, and Eclipse were considered but left out: their interception is opt-in / popup-style rather than always-on, and the BackSpace-flicker path running unnecessarily isn't free. Add them if reports come in.)

## Things to Watch Out For

The full list of implementation gotchas and invariants lives in
**`docs/architecture/GOTCHAS.md`** - read it before touching keystroke
synthesis, the prediction context buffers, window flags, or the build
pipeline. The highest-frequency traps, kept inline because they're the
easiest to reintroduce:

- **Window flags / focus**: the keyboard must never steal focus. `WS_EX_NOACTIVATE` is set via Win32 API on Windows (`_apply_window_flags()` in `keyboard_app.py`); `WindowDoesNotAcceptFocus` elsewhere.
- **Sticky modifier auto-release lives in two parallel blocks — keep them in sync.** `_press_char` and `pressSpecialKey` each end with their own Shift/Ctrl/Alt/Win release sequence (state flip + `release_modifier()` + change-signal emit, plus `_update_layer()` for Shift). New keystroke paths that branch off (autocorrect retype, pill insertion, edit-mode, macros) must mirror it. **Two exceptions, both of which skip the release:** (1) `pressSpecialKey` keeps Shift/Ctrl held on `_NAV_KEYS` (arrows/home/end/pageup/pagedown) so Shift+arrow selection and Ctrl+arrow word-jump persist across presses; Alt/Win still release. (2) a **right-click-locked** modifier (`_*_locked`, see *Sticky Modifiers → Right-Click to Lock*) is skipped in every release block. There are actually **four** guarded blocks, not two — also the edit-mode intercept and the Ctrl/Alt/Win chord branch in `_press_char`. A new keystroke path must add the `and not self._*_locked` guard or a held modifier will silently drop.
- **Prediction insertion is suffix-only** (type just the unseen tail), falling back to `replace_text()` only on a prefix mismatch (casing). Compatibility Mode (`_in_compat_mode()`) rewires this to BackSpace + retype for remote-desktop clients and IDEs where suffix-only is unsafe.
- **`_context_buffer` / `_current_word` mirror the on-screen text.** Backspace must trim the buffer and rehydrate a mid-word tail back into `_current_word`; prefix punctuation must be treated as a word boundary or pill clicks eat it. That check is an **allow-list of word characters** (alphanumeric plus `'` and `_`), not a list of separators: as a separator list it failed open when the second symbol page added 18 glyphs, none of which were on it, so `cost€` became the prediction prefix and the learned token. Don't convert it back.
- **Windows uses scancode mode** for both `send_text` (ASCII) and chords/`hold_modifier` (UNICODE/`wVk`-mode only as a fallback) - required for Blender/VirtualBox/games and for Ctrl+V over TeamViewer/RDP.
- **Linux `xdotool`/`ydotool` calls that carry arbitrary typed text must precede it with a literal `--`.** Both tools' `type` subcommand parses flags with getopt, so typed text that happens to look like an option (`--help`) would otherwise be silently parsed as one and dropped instead of typed. The four call sites carrying arbitrary text do this; the other sixteen `xdotool`/`ydotool` call sites pass internally-built key names, not user text, so they don't need it. `platform/linux.py::_run()` is also bounded by a 2.0 s `_SUBPROCESS_TIMEOUT_S`: it runs synchronously on the Qt UI thread on every keystroke, so a wedged binary (dead X server, unresponsive display) used to freeze the whole keyboard; `TimeoutExpired` is now caught and logged, not left to hang.
- **Games need a held key, not a zero-gap tap.** Games read the keyboard by *polling* state once per render frame (DirectInput / Raw Input / `GetAsyncKeyState`), so a key-down+key-up injected in one `SendInput` batch can land entirely between two polls and be missed: the keystroke does nothing in-game even though it works everywhere else. Auto game-compat fixes this: when `_window_is_game(hwnd)` is true, `_game_auto_active` flips on (set in the same 250 ms foreground poll as compat auto-detect) and single keys are sent with `hold_seconds = _GAME_KEY_HOLD_SECONDS` (50 ms). `WindowsKeySynthesizer.send_key` then splits the injection into a down-batch, a real `time.sleep`, and an up-batch (modifiers wrap the held key). Non-game keystrokes keep the zero-latency atomic path. `_window_is_game` uses two signals (`keyboard_bridge.py`): (1) the owning-process exe is in `_GAME_PROCESS_NAMES` (seeded with Age of Empires; extend like `_COMPAT_PROCESS_NAMES`), which catches games even in windowed mode; (2) a **borderless-fullscreen heuristic** (`_window_is_borderless_fullscreen`: window rect covers the whole monitor *and* the window has no `WS_CAPTION`) as a zero-config catch-all for unlisted games. The heuristic is deliberately skipped for exes in `_COMPAT_PROCESS_NAMES` (IDEs / remote-desktop clients), which are sometimes run fullscreen and must not get the typing-lag hold. Requiring "no caption" excludes normal maximized windows (which keep their title bar); the remaining false positives (fullscreen video players, slideshows) are harmless because a 50 ms hold doesn't hurt there. This is unrelated to UIAccess: a signed Program-Files install still hit it because the keystrokes *reach* the game, they're just too brief to be polled.
- **`pressKey` lowercases its input** - use `pressKeyLiteral` when QML already resolved the final character (right-click shifted variant, etc.).
- **QML `Text` defaults to `AutoText`, which sniffs the string for HTML and can trigger an outbound request just from being displayed.** Any `Text` rendering a value that ultimately came from imported or otherwise untrusted data (a vocabulary pack's `name`/`description`, anything read from a file the user picked) must set `textFormat: Text.PlainText` explicitly, or an `<img src=...>` planted in that string makes Qt fetch it the moment the Settings page renders. 23 `Text` elements across 7 QML files (`Main.qml`, `UnifiedSettingsPanel.qml`, `DebugPanel.qml`, `AnalyticsDashboard.qml`, `ModelVisualization.qml`, `KeyButton.qml`, `SettingsToggle.qml`) now set it explicitly; new `Text` elements displaying untrusted strings must too. **Known gap**: the attached-property `ToolTip.text` idiom has no `textFormat` to set, so a tooltip built from untrusted text is not covered.
- **Invariants**: `NgramPredictor._user_total == sum(user_vocab.values())`; merge strategy default MUST stay `"rank"`; window height is content-bound (never persist/assign it); analytics metrics need both session and `_alltime_*` forms; Windows subprocess calls suppressing output need `CREATE_NO_WINDOW`.

## Right-Click for Shifted Character

Right-click on a char key types its shifted variant without flipping the sticky shift state - `1` -> `!`, `,` -> `<`, `a` -> `A`. Modifier and special keys are deliberate no-ops. Toggle in *Settings -> Smart Typing -> Input -> "Right-Click for Shifted Character"* (default ON; left-click is unaffected whether on or off). Implementation:
- `KeyButton.qml` exposes a `keyRightPressed` signal. The `MouseArea` accepts both buttons; the right-button branch in `onPressed` returns *before* the auto-repeat timer starts so right-click is always a one-shot. Press visuals + ripple still fire - same tactile feedback as a left-click.
- `Main.qml` per-key `onKeyRightPressed` resolves the output: prefer `kd.shifted` from the layout JSON (covers `1`->`!`, `,`->`<`); fall back to `kd.key.toUpperCase()` for letters; otherwise no-op.
- The handler routes through `keyboard.pressKeyLiteral(rch)`, **not** `pressKey` - the latter would lowercase the chosen `'A'` back to `'a'` (see the `pressKey` watch-out above).

The companion long-press -> accents feature is **not** implemented - see `docs/architecture/LONG_PRESS_ALTERNATES.md` for the design and the reason it's deferred (press-on-release timing change is hostile to slow-motor users until we have a way to scope the latency to keys with alternates).

## Key Preview Bubble

A small bubble floats just above a key showing the character that was actually typed, the same "key preview" pattern phone keyboards use. It fires on **both** left- and right-click. The motivating case is right-click (it sends the shifted variant, and that glyph isn't always the one drawn on the key, so the preview confirms what reached the OS), but left-click previews every typed character too. Toggle in *Settings -> Smart Typing -> Input -> "Show Key Preview Popup"* (default ON). It's a pure visual: there is no Python bridge, the setting is `appSettings.savedKeyPreview` mirrored into `root.keyPreviewEnabled` and restored on launch like any other Qt setting.

### Phone-style press/release timing
The bubble shows on press and hides on release, so during normal typing it's visible only for the tap duration (down to a floor), exactly like Gboard/iOS. It is **not** a fixed-dwell toast. The mechanics:
- `KeyButton.qml` emits a new `keyReleased()` signal from all three "press ended" paths: `onReleased`, `onCanceled`, and `onContainsMouseChanged` when the cursor drags off while pressed (the drag-off case is sometimes the only release signal we get under `WS_EX_NOACTIVATE`). The release emit in `onContainsMouseChanged` is guarded on `_visualPressed` so a pure hover-out doesn't fire it.
- `Main.qml` `showKeyPreview(item, ch)` maps the key's top-center into the overlay and calls `keyPreviewBubble.show()`; the per-key `onKeyReleased: root.hideKeyPreview()` dismisses it.
- The bubble (`keyPreviewBubble`, a `Popup` parented to `Overlay.overlay`, fixed 40x40 so the first show centers before content is measured) has two guard timers: `keyPreviewMinTimer` (110 ms visibility floor so a lightning-fast click still flashes long enough to register instead of opening and closing in the same frame) and `keyPreviewSafetyTimer` (1500 ms force-close in case a release event is dropped and `keyReleased` never arrives). `hide()` defers the close to the min timer when the press was shorter than the floor (via the `pendingHide` flag); otherwise it closes immediately.

Left-click previews use `keyBtn.displayText` (which already reflects shift/caps casing, so it matches what `pressKey` sends); right-click previews use the resolved `rch`. Both call sites are gated on `root.keyPreviewEnabled`. Modifier and special keys do not preview (a bubble over Shift or Backspace isn't "what it typed").