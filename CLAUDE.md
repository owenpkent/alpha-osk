# CLAUDE.md: Alpha-OSK AI Onboarding

Alpha-OSK is an AI-assisted, mouse-driven on-screen keyboard for Windows and Linux (macOS in progress). Users click QML keys to type into whatever app holds OS focus; a hybrid n-gram + fuzzy engine predicts words locally (a PPM model trains alongside but has been out of the merge since 2026-09-03), with no LLM, no GPU, and nothing leaving the machine. It is an accessibility tool the owner depends on daily. This file is both the AI-onboarding doc and the human codebase map; the sections below are authoritative project knowledge, not background.

## About the Owner

Owen is a wheelchair user with muscular dystrophy. Typing is hard - be proactive, make decisions, don't ask for confirmation on small things. Offer A/B/C choices so he can type one letter instead of explaining. This is an accessibility tool he actually needs.

## Key rules (non-obvious, cross-cutting)

- The keyboard must NEVER steal OS focus: `WS_EX_NOACTIVATE` on Windows (`keyboard_app.py::_apply_window_flags` -> `windows_window.py::apply_extended_styles`), `WindowDoesNotAcceptFocus` elsewhere. In-app text entry (prediction popup, snippets editor, key-action editor, any new slot) uses `keyboard.beginEditSession(owner)` / `endEditSession(owner)` plus `editKeyTyped` / `editSpecialPressed`, never Qt focus; `owner` is unique per surface and its `Connections` listens only while `keyboard.editOwner` is its name. See *Editing a Prediction*.
- **No transparent window here rounds its own corners on Windows** (layered windows paint `radius` leftovers white): `Main.qml::selfRoundedCorners` squares them off and `windows_window.py::_prefer_dwm_rounded_corners` asks DWM. A new transparent floating window must bind to it and be named in `keyboard_app.py::_wire_floating_windows`. See *Who rounds the window corners*.
- Sticky-modifier auto-release lives only in `KeyboardBridge._release_sticky_modifiers(names=_MODIFIERS, *, keep=())`; every keystroke path calls it, never a copy. See *Sticky Modifiers*.
- Linux `LinuxKeySynthesizer.hold_modifier()` MUST skip `win`/`super` (a held Super makes the WM swallow every click, including on the OSK). Windows still holds `VK_LWIN`.
- Pill casing comes only from `KeyboardBridge._display_cased`; every pill emit site routes through it. Auto-cap is only the "I" family (`language.ENGLISH.always_capitalize`) plus taught acronyms; do NOT reintroduce the removed three-tier proper-noun auto-cap.
- Verbatim inserts (pill, snippet, glyph, token pill, dictation) open with `KeyboardBridge._begin_verbatim_insert(*, prose=True)` (sticky release, `_take_deferred_space`, `_consume_auto_cap`). `insertSnippet` / `insertGlyph` go on through `_commit_verbatim_insert`, which sends inside `_without_held_modifiers()` (a held OS modifier rewrites the string). **That context manager must wrap the whole insert**, including `pressPrediction`'s compat BackSpace loop and `replace_text`. The single-char path in `_press_char` deliberately does not use it. See `docs/architecture/MODIFIERS_AND_CASING.md`.
- Prediction insertion is suffix-only, falling back to `replace_text()` on a prefix/casing mismatch; Compatibility Mode (`_in_compat_mode`, by exe basename in `_COMPAT_PROCESS_NAMES`, never window class) uses BackSpace+retype. `_context_buffer` / `_current_word` must always mirror the screen; backspace must trim and rehydrate a mid-word tail.
- Import paths are security-critical: `PackManager.import_pack`, `data_export.import_user_data`, `inspect_export` sanitise names, cap sizes and extract by allow-list. Do NOT loosen without re-reading `tests/test_vocabulary_pack.py::TestImportPackSecurity` and the slip/absolute-path/oversize/future-schema/telemetry cases in `tests/test_data_export.py`.
- Imported snippets get `\r`/`\n` flattened to spaces (`data_export.py::_flatten_imported_snippet_newlines`); locally authored ones keep newlines.
- Privacy/password mode must suppress learning AND `activeContextChanged` (no password content into predictions, telemetry or the visualization). Detection: `src/platform/password_detect.py`.
- `pressPrediction` and `editPrediction` call `_check_password_field_sync()` first, then gate `record_prediction_selected`, `learn_from_selection`, `learn_capitalization`, `set_capitalization` behind `if not self._privacy_mode`; the insertion itself is deliberately NOT gated. Any new pill-click or edit path mirrors both halves.
- Telemetry is OFF by default; `DEFAULT_ENDPOINT` (`src/telemetry.py`) is the production worker (empty = silent no-op). `TelemetryClient` owns the consent flag; do NOT mirror it into `appSettings`. The Data Backup archive excludes `telemetry.json`.
- Key colouring is a role -> colour table (`qml/palette.js`) handed down by `Main.qml`, resolved in `KeyButton`; the default hands down `null` ("keep your own tint"). No hue is a literal (OKLCh off the theme accent) and every fill goes through the wash that keeps `textColor` at 4.5:1. See *Key Colours by role*.
- Grid, nav cluster, numpad and separators lay out to `Main.qml::sectionHeight` (the grid's implicit height only); panels absorb differences into **key heights**, never gaps, and their `implicitHeight` derives from `keyH`, never their own grid. See *The three sections share one height*.
- Adding a setting requires the full 8-step wiring in *Settings Panel Structure*.
- Releases: `src/__version__.py` is the version source; publish to `owenpkent/alpha-osk-releases` with an explicit `--repo`; the asset must be exactly `Alpha-OSK-Setup-{version}.exe`. A release never touches the website repo (*The website*).
- **UIAccess comes from `uac_uiaccess=True` in `build/windows/alpha-osk.spec`, not the manifest file.** `build.py::verify_exe_requests_uiaccess` fails the build unless the built manifest says `"true"`; the update helper is the mirror (`uac_uiaccess=False`, `verify_helper_does_not_request_uiaccess`). Guard: `tests/test_windows_uiaccess_manifest.py`. See *Auto-Update*.
- The install path is computed, never read from the registry: silent installs pass `/S /D=<dir>` from `updater.py::_install_target_dir()`, `/D=` last and unquoted. Don't reorder or requote.
- `run.py::ensure_admin_windows()` runs after dependency installation; `--dashboard` never elevates.
- Until the engine loads, `KeyboardBridge._predictor` is a `NullPredictor`, never None; refuse through `_engine_loaded()` (see *Startup*).
- Load-bearing invariants: merge-strategy default MUST stay `"rank"`; `NgramPredictor._user_total == sum(user_vocab.values())`; `bigrams[p][w] >= round(_user_bigrams[p][w])`; window height is content-bound (never persist or assign it); full-size rows total exactly 15.5u, compact 13.0u; every `KeyButton` gets `hitMarginH` / `hitMarginV`; every analytics metric needs a session and an `_alltime_*` form; Windows subprocess calls need `CREATE_NO_WINDOW` when they suppress output *or* may run without a console (git hook, frozen GUI build).

## Stack & layout

- Python 3.10+ backend (CI runs 3.11, mypy targets 3.10), PySide6 (Qt6) + QML UI. No LLM/GPU. Key synthesis: ctypes SendInput scancode mode (Windows), `xdotool`/`ydotool` subprocess (Linux, NOT bundled), Quartz CGEvent (macOS, WIP). Dictation adds `QtMultimedia` and `QtWebSockets` to the load-bearing Qt modules (both in the PySide6 wheel; see *Dictation*).
- `src/keyboard_bridge.py` (QML<->Python bridge), `src/keyboard_app.py` (launcher, window flags, auto-save), `src/platform/` (OS abstraction), `src/prediction/` (hybrid engine), `src/dictation/`, `qml/Main.qml` + `qml/components/`, `data/`, `build/{windows,linux,macos}/`, `tests/`, `backend/cf-worker/` (telemetry worker).
- A C++/Qt6 rewrite exists **only on the `cpp-rewrite` branch** (`cpp/`, nothing C++ on `main`), and it is **parked** (decided 2026-09-02, `docs/architecture/STRUCTURAL_REVIEW.md` item 7): no Python change needs mirroring into it, so every "mirrored in C++" note in this file is history. Its parity matrix `docs/architecture/BACKEND_PARITY.md` is a snapshot, and the `tests/conformance/` cross-backend diff runs only when `ALPHA_OSK_CPP_BIN` names a built binary (neither CI nor `check.py` sets it), so parity is asserted, not verified.

## Build, run, test

- Temporary files: use a scoped `tempfile.TemporaryDirectory` under the system temp directory (cleanup on success, errors and interruption). No `.tmp-*` folders in the checkout. Clean up your own scratch files; never sweep unrelated folders or delete explicitly supplied model directories. Pytest keeps only a failed test's dir (`tmp_path_retention_policy = "failed"`); a `--basetemp` goes inside your own cleanup scope. The KSR benchmark cleans its default model directory and keeps `--model-dir`.
- Run: `python run.py` (creates venv, installs deps, launches the keyboard).
- Test: `python -m pytest` (~3,400 tests; `--collect-only -q` prints the live count, so don't restate it; also `-k fuzzy`, `-k property`, or a single file).
- Pre-push gate, same checks as CI (`ruff check`, `ruff format --check`, `mypy` under **both** `--platform linux` and `--platform win32`, `pytest`): `python check.py` (~60s); `--full` adds the `--cov-fail-under=60` gate (~110s, full CI parity); `--install-hook` wires it to `git push` (`--no-verify` skips once). CI also runs `osv-scanner`. Fix a format failure with `ruff format src/ tests/` (`ruff check` ignores layout). Neither mypy pass substitutes for the other: `linux` is the runner (typeshed gates symbols by platform, so `ctypes.WinDLL` is `Any` and trips `warn_return_any`); `win32` is the only pass that checks `if sys.platform == "win32"` bodies.

## Conventions

- Format/lint: `ruff check src/ tests/` + `ruff-format` (line length 100, rules E/F/W/I); types: `mypy src/`. Pre-commit runs ruff `--fix` + ruff-format.
- Conventional commits (`feat:` / `fix:` / `docs:` / `refactor:` / `chore:` / `test:`), subject under ~72 chars. Never add AI co-author trailers.
- NO em dashes anywhere (code, docs, commits, PRs): use commas, colons, parentheses, periods. Comment only the non-obvious "why". Tests required for behaviour changes.
- Accessibility first: any change to keystroke timing, repeat interval, or visual feedback must stay usable for slow, imprecise motor input.

## When to ask / flag in PR

- Call out in the PR description any change to the prediction engine, the build/signing pipeline, or telemetry.
- Get alignment before: changing the security-reporting flow or CoC contact (update `SECURITY.md` / `CONTRIBUTING.md` / `bug_report.yml` together); the data-export schema (`SCHEMA_VERSION` bump + back-compat import); the telemetry payload or consent model; loosening any import-hardening check; or disabling the OSV `fail-on-vuln` gate.
- Can't decide which Settings category a new toggle belongs in? That is a UX smell: push back on the requirement first.

## Architecture Overview

```
User clicks key (QML)
  -> KeyButton.qml signal
  -> Main.qml calls keyboard.pressKey() / keyboard.pressSpecialKey()
  -> keyboard_bridge.py
    -> platform/*.py synthesizes keystroke (xdotool / SendInput)
    -> prediction engine updates suggestions
  -> predictions emitted back to QML via Signal
```

## Key Directories

| Path | What |
|------|------|
| `src/keyboard_bridge.py` | Central bridge: keys, modifiers, context tracking, predictions |
| `src/telemetry_bridge.py` | `TelemetryBridge`: QML-facing opt-in telemetry wrapper, its own context property (see *Opt-in Telemetry*) |
| `src/keyboard_app.py` | Launcher: QML engine, window flags, auto-save on exit |
| `src/platform/` | `linux.py` (xdotool/ydotool), `windows.py` (SendInput), `password_detect.py`, `windows_window.py`, `macos_window.py`, `x11_window.py` (DOCK tuck); `__init__.py` has platform detection, `get_config_dir()`, `get_model_dir()` |
| `src/prediction/` | Prediction engines (see below) |
| `src/glyphs.py` | Symbol / emoji catalogue for the Symbols & Emoji window |
| `src/dictation/` | Voice input: `config.py`, `audio.py`, `providers.py` (Deepgram), `controller.py` |
| `qml/Main.qml` | Root UI: title bar, keyboard rows, prediction bar, resize handles |
| `qml/palette.js` | Key Colours engine; the single copy of the contrast maths (see *Key Colours by role*) |
| `qml/components/` | Reusable QML components |
| `data/` | Dictionaries, training corpus, layouts, vocab packs |
| `build/` | Packaging: `windows/` (PyInstaller + NSIS + EV signing), `linux/` (PyInstaller + AppImage), `macos/` (scaffolded); `build/launcher.py` is the shared frozen-mode entry point |
| `tests/` | pytest suite |

## Deep-dive docs

Each section below that is marked *Full write-up* keeps only its load-bearing rules here; the reasoning, the measurements and the rejected alternatives live in the doc. Read the doc before changing that area, and put new reasoning there rather than growing this file.

| Area | Doc |
|------|-----|
| Prediction engine (startup loader, context tables, corpus prior, prefix beam, fuzzy refresh and shared caches, pointer bias, apostrophe, acronyms, slur removal) | `docs/architecture/PREDICTION_NOTES.md` |
| Per-algorithm detail | `docs/architecture/FUZZY_RECOGNITION.md`, `docs/architecture/PPM.md`, `docs/architecture/HYBRID_MERGING.md`, `docs/architecture/NGRAM_SEEDS.md` |
| Where user data lives, the log, the installer's registry handling | `docs/architecture/USER_DATA.md` |
| Modifiers, casing, pill widths | `docs/architecture/MODIFIERS_AND_CASING.md` |
| Spacing, snippet detection, structured tokens | `docs/architecture/TEXT_PATTERNS.md` |
| Snippets | `docs/architecture/SNIPPETS.md` |
| Symbols & Emoji window | `docs/architecture/SYMBOLS_WINDOW.md` |
| Function keys and programmable actions | `docs/architecture/FUNCTION_KEYS.md` |
| Clearing stale typing context | `docs/architecture/CONTEXT_RESET.md` |
| Key colours | `docs/architecture/KEY_COLOURS.md` |
| Layout geometry (flush rows, section height, dead space) | `docs/architecture/LAYOUT_GEOMETRY.md` |
| Compact view | `docs/architecture/COMPACT_VIEW.md` |
| Window chrome (corners, taskbar button, title-bar menu, Move mode, snapping, stepping aside for notifications) | `docs/architecture/WINDOW_CHROME.md` |
| Switch scanning over UI Automation | `docs/architecture/UIA_TARGETS.md` |
| Dictation | `docs/architecture/DICTATION.md` |
| Telemetry, and the installer invitation | `docs/architecture/TELEMETRY.md` |
| Vocabulary, explicit filter, packs | `docs/architecture/VOCABULARY.md` |
| Language profile, layout-derived key positions | `docs/architecture/LANGUAGE_PROFILE.md` |
| Show more / less / remove | `docs/architecture/WORD_ADJUSTMENTS.md` |
| Privacy mode and password detection | `docs/architecture/PRIVACY_MODE.md` |
| Edit sessions (in-app text entry) | `docs/architecture/EDIT_SESSIONS.md` |
| Settings panel, and where each setting lives | `docs/architecture/SETTINGS_PANEL.md` |
| Analytics and the model dashboard | `docs/architecture/ANALYTICS.md` |
| Right-click shifted character, key preview bubble | `docs/architecture/KEY_FEEDBACK.md` |
| Gotchas not repeated here | `docs/architecture/GOTCHAS.md` |
| Build and release | `docs/build/WINDOWS.md`, `RELEASE.md`, `AUTO_UPDATE.md`, `CI.md`, `LINUX.md`, `MACOS.md`, `TESTING.md`, `GIT.md`, `WEBSITE.md` |
| User study | `docs/research/STUDY_PROTOCOL.md`, `STUDY_CONSENT.md`, `STUDY_HARNESS.md` |

## Startup

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `keyboard_app.main()` builds `KeyboardBridge(defer_predictions=True)`; `prediction/loader.py` builds the `HybridPredictor` on a daemon thread while keys, modifiers, snippets and privacy detection already work. `predictionStatus` is the single loading/ready/error state; a failed load offers Retry without disabling typing. Direct construction stays synchronous.
- Every exit of the loader's `_build` marks the job done (`finally`); a cancel stops the CPU work too (`abort_check` at `_checkpoint()`, then `shutdown()` joins).
- **Until the engine loads, `_predictor` is a `NullPredictor`, never None.** Refuse through `_engine_loaded()` / `null_predictor.is_loaded`, never `is None`.
- Tests: `tests/test_prediction_startup.py`, `tests/test_null_predictor.py`.

## Prediction Engine

All in `src/prediction/`, orchestrated by `hybrid_predictor.py`. Deep dives: `docs/architecture/FUZZY_RECOGNITION.md`, `PPM.md`, `HYBRID_MERGING.md`, `PREDICTION_NOTES.md`.

| File | Role |
|------|------|
| `ngram_predictor.py` | Word-frequency model (uni/bi/trigrams); learns from typing |
| `ppm_predictor.py` | Character-level PPM; trained and persisted, **out of the merge** since 2026-09-03 (see *Fuzzy dictionary refresh*) |
| `fuzzy_recognizer.py` | Spatial error correction; one tuned default, no profiles |
| `prefix_beam.py` | Mid-word fuzzy completion; what `get_fuzzy_predictions` calls (see *Prefix beam*) |
| `hybrid_predictor.py` | Merges predictors, model save/load, Qt signals |
| `token_predictor.py` | Whole structured tokens: phones, zips, emails (see *Structured Tokens*) |
| `vocabulary_pack.py` | Custom vocab pack import, none built in (see *Vocabulary Packs*) |
| `transformer_predictor.py` | Optional LLM re-ranking, disabled by default |

## Context tables: base and user halves

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `NgramPredictor.bigrams` / `.trigrams` are **merged views**. The user's share (`_user_bigrams` / `_user_trigrams`) is the **only context persisted**; the base share (seeds, corpus, packs) is rebuilt every launch. Invariant: `bigrams[p][w] >= round(_user_bigrams[p][w])`.
- Every user context write goes through `_bump_user_context`; a direct write to `.bigrams` (tests, `learn(corpus=True)`, seed loaders, packs) is a base write.
- `_context_probs` weights the user row `U / (U + 5 + 0.02 * B)`; with no user evidence it equals the old normalised row **exactly**.
- `_decay_user_context` acts on the user share only; seeds never decay. Legacy files with `bigrams` / `trigrams` are adopted as user history (`_adopt_user_context`).
- **`HybridPredictor.reload_from_disk` must call `_reseed_context()` after `load()`.**
- Benchmarks (`scripts/bench/ksr.py`, `fuzzy.py`): always a temporary model directory, and **quote the corpus with every keystroke-savings number**. Under 1.4 points is noise.

## Shipped corpus prior and faster personal learning

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- Corpus unigrams live in `NgramPredictor._corpus_unigrams` / `_corpus_total` **and nowhere else** (never in persisted `unigrams`); scorers read them via `_effective_typing_count` / `_effective_typing_total` at `_CORPUS_PRIOR_WEIGHT`. Membership goes through `in_vocabulary` / `vocabulary()`.
- Saving a prediction edit **whose spelling changed** calls `learn_from_selection(..., explicit=True)`; a Save that kept the spelling is one ordinary pill tap. Privacy mode and the learning freeze suppress both.
- Tests: `tests/test_corpus_prior.py`.

## Prefix beam (mid-word fuzzy completion)

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `FuzzyRecognizer.get_fuzzy_predictions` is a beam over the dictionary's **live prefixes** (`prefix_beam.py`, `PrefixIndex`); the whole-word paths that run on space are unchanged.
- **Any test or bench of this path must inject the n-gram's counts** (`set_frequencies`), or rankings fall to insertion order.
- Below three typed characters it returns nothing, unless the run is a dead prefix (`MIN_TYPED_DEAD_PREFIX`). `HybridPredictor.predict` opts into `allow_short_prefix` under `SHORT_PREFIX_RESCUE_FLOOR` candidates, **independent of the max-suggestions setting**. One character never triggers it.
- Constants were set by sweep; `prefix_completion = False` is for the benchmark only, and there is deliberately no user setting.
- Tests: `tests/test_fuzzy_prefix_beam.py`, `tests/test_hybrid_predictor.py::TestTwoLettersAlwaysFillTheBar`.

## Fuzzy dictionary refresh, and PPM out of the merge

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- **The fuzzy dictionary follows the vocabulary.** Every learning path pushes changed words through `HybridPredictor._refresh_fuzzy_frequencies(words)` -> `FuzzyRecognizer.update_word`. Events that *shrink* the vocabulary (`clear_user_data`, `reload_from_disk`, `unprefer`) go through `_rebuild_fuzzy_dictionary`. `unlearn_word` is deliberately not followed.
- **Every injection into the fuzzy dictionary goes through `HybridPredictor._fuzzy_frequency(word)`**, never the raw merged count. `PrefixBeam._protect_exact_completions` (`FREQUENCY_MAY_BUY`) stays; a hard exact-first tier was tried and reversed (`teh` must still offer `the`).
- **PPM contributes no word candidates** (`_ppm_in_merge = False`, since 2026-09-03) but still trains, loads and saves. Text calling the merge "n-gram + PPM + fuzzy" predates this.
- SymSpell and `PrefixIndex` are packed immutable indexes (`packed_deletes.py`, `packed_prefixes.py`) with mutable overlays; base postings merge before overlay postings; rebuilds call `prepare_prefix_index()`. Slurs are excluded from base vocabulary (`LanguageProfile.extra_vocabulary`; `docs/research/VOCABULARY_MEMORY.md`). Equal n-gram scores sort lexically.
- **The packed structures and the validated wordlist are built once per process and shared** (`packed_cache.py`, BLAKE2b-keyed; `NgramPredictor._validated_extra_words`), so everything that changes as the user types goes to the overlay. The taught-acronym bypass guards on `taught_capitalization`, not `capitalization`.
- Tests: `tests/test_fuzzy_refresh.py`, `tests/test_ngram_candidate_index.py`.

## Click position and the learned pointer bias

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `KeyButton.qml` publishes `pressDx` / `pressDy` (-0.5 to 0.5); `Main.qml` passes them on all three char paths. Bridge slots are overloaded (`@Slot(str)`, `@Slot(str, float, float)`); the old form means "key centre".
- `_word_offsets` entries are handed on only when they spell the word (`_offsets_spell` / `_offsets_for_word`). **Do not maintain the list at every site that rewrites `_current_word`**; a mismatch degrades to key centres by design.
- `pointer_model.py` learns a per **physical slot** bias, owned by `NgramPredictor.pointer` (`pointer` key in `ngram_model.json`), mutated in place, never rebound; observed only outside privacy mode.
- `SpatialEmissions.KEY_SIGMA` / `POSITION_SIGMA` were set by sweep (sharper *hurt*); the gain is a few tenths of a point.
- Tests: `tests/test_pointer_model.py`, `test_click_position.py`, `test_qml_click_position.py`.

## The apostrophe is optional in a typed prefix

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `ill` offers `I'll`, `im` offers `I'm`: one clause in `NgramPredictor._matches_partial`, **only when the user typed no apostrophe**. It belongs in the n-gram's exact match, not the fuzzy source.
- `_continues_a_word` gates the re-query in `_press_char`: letters, plus `'` after letters. **The underscore is deliberately not in the gate.**
- Tests: `TestTheApostropheIsOptionalInATypedPrefix`, `TestASkippedApostropheStillFindsTheWord`, `TestTypingTheApostropheKeepsTheBar`.

## Short words in next-word predictions

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `HybridPredictor._short_word_allowed` gates 1-2 letter words out of *next-word* predictions only.
- It is an **allow-list of real words (language profile `short_words`), not a length rule**: stray fragments would otherwise compete for pills.
- **Both merge sites call `_next_word_allowed`** (also admits taught acronyms), never `_short_word_allowed`.
- Test: `tests/test_hybrid_predictor.py::TestShortWordsAreOfferedAsNextWords` (its negative half matters).

## Taught acronyms (why "PR" would never learn)

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `NgramPredictor.is_taught_acronym(word)`: `capitalization[word]` carries **two or more capitals**. It exempts the word from `_is_plausible_word`'s shape filter and the next-word gate (`HybridPredictor._next_word_allowed`, called by both merge sites).
- **`load()` merges `capitalization` before the fragment strip**, or learned acronyms are deleted on reload.
- `get_capitalized` returns the taught form only for a word not in `_base_unigrams` whose form is acronym-shaped. This is not the removed Tier 3.
- Limitation: teach with per-letter shift or right-click; Caps Lock teaches nothing.
- Tests: `TestTaughtAcronymsAreLearnable`, `TestTaughtAcronymsReachTheBar`.

## Auto-Capitalization & Proper Nouns

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- **Only the "I" family auto-capitalizes** (`language.ENGLISH.always_capitalize`). Shift / caps lock is the cap signal. Keep that set tight.
- `NgramPredictor.get_capitalized` returns the "I" form, a taught acronym (see *Taught acronyms*), else the word unchanged; `sentence_start` is ignored.
- **Pill casing comes only from `KeyboardBridge._display_cased`.**
- `NgramPredictor.capitalization` is still collected (`learn_capitalization(word, *, allow_uppercase=False)`) but inert in pills. The bridge passes `allow_uppercase = not _word_typed_under_caps_lock`.
- **Never reintroduce the old three-tier system as a default**; re-enable only by editing `get_capitalized`, opt-in.

## Where User Data Lives

Full write-up: `docs/architecture/USER_DATA.md`. Read it before changing this area.

- **Every store writes through `src/atomic_write.py`** (`atomic_write_text` / `atomic_write_json`); never a bare `open()` / `write_text`.
- **Settings**: Qt `Settings`, on Windows `HKCU\Software\alpha-osk` (the **organisation** name; keys are case-insensitive). Only `installer.nsh::customUnInstall` deletes it (inside `IfSilent`, spelled from `APP_ORG`); every previous-uninstaller call goes through `upgrade_settings.nsh::RunPreviousUninstaller` (**backup/restore failures stop setup, the old uninstaller's exit code does not**); `removePreviousInstallAt` executes nothing read from the registry. Guards: `tests/test_windows_installer.py`, `tests/test_upgrade_settings.py`.
- **Loader caps**: models (`%APPDATA%/alpha-osk/models/`) reject over 50 MB, 500 000 unigrams or bigram prefixes, or 100 000 capitalisation entries; `analytics.json` 5 MB via `stat()` first, tables 5000, scalars via `_as_count` / `_as_minutes`; `dictation.json` 64 KB (holds the API key, **excluded from the Data Backup archive** like `telemetry.json` and the log).
- **`alpha-osk.log` must never contain typed content.** No record at INFO or above may interpolate a word, `_current_word`, `_context_buffer`, `_sentence_buffer` or a prediction list; content-bearing debug goes at DEBUG *and* behind `if not self._privacy_mode:`. The platform layer is inside the rule (`linux.py::_run` logs `_describe` / `_error_name`, never `cmd` or `str(exc)`).
- Tracebacks reach the log via `keyboard_app.py::_install_exception_hooks`; Diagnostics opens the **folder**; `_purge_pre_fix_logs` runs once per generation (sentinels `.log-privacy-purge`, `-2`), before the handler opens the file, never raises.

## Snippets (Quick-Insert Text)

Full write-up: `docs/architecture/SNIPPETS.md` (section of the same name). Read it before changing this area.

Backend `src/snippets.py`, UI `qml/components/SnippetsWindow.qml`, opened from `snippetsBarButton` (title-bar twin when `suggestionsEnabled` is false).

- **A tile tap copies to the clipboard (`copySnippet(index) -> bool`) and does nothing else.** False must show `snippetProblemToast`; `QGuiApplication` is imported **inside the slot**; toasts live on the keyboard window and stay `Popup.NoAutoClose`. `insertSnippet` has no QML caller (reference verbatim insert via `_commit_verbatim_insert`).
- `snippets.json`: atomic save on every mutation; caps `MAX_SNIPPETS` 50, label 40, value 2000, file 1 MB; corrupt file falls back to seeded defaults. **Colour tags are stored as a `SNIPPET_COLORS` name, never a hex** (`_clean_color`). Nothing reads `SCHEMA_VERSION` on load, deliberately.
- Top-level `Window` (not a `Popup`), named in `_wire_floating_windows`; its surface with `Main.qml` is `required` properties plus `copied` / `problem` / `saved`. Position clamped to the whole virtual desktop (`root.clampedWindowPos`).
- One of three views shows (grid, actions sheet, editor). **The header's Manage toggle is the left-click-only route to the sheet**; dispatch is `tileClicked(idx, button)`. The sheet and editor track a snippet's **identity** (label + value), not its index (import replaces the list).
- Edit session `"snippets"` is held **only while the editor shows**. A `MouseArea` over an input sets `mouse.accepted = false`; Tab calls `focusOtherField()`; Shift + arrow uses `moveCaret()`. Icons are `StrokeIcon`, never glyphs.
- In the Data Backup archive; **import flattens newlines**.
- Tests: `tests/test_qml_snippets.py`, `tests/test_data_export.py::TestSnippetNewlineFlattening`.

## Intelligent Spacing & Snippet Auto-Detection

Full write-up: `docs/architecture/TEXT_PATTERNS.md` (section of the same name). Read it before changing this area.

Both live in `src/text_patterns.py` (linear-time, length-capped, never logs: every argument is typed content).

- **`_raw_token`**, not `_current_word`, is the run before the cursor (capped at 128, maintained only outside privacy mode).
- A suppressed auto-space skips the sent space, the `_context_buffer` space, and the auto-capitalize together. **A bare digit run suppresses provisionally** (`_deferred_auto_space`, settled by the next character; it only ever *adds* a space late).
- `_closes_a_quotation` decides on `"` parity; `"` must never join `_NO_SPACE_BEFORE`.
- **Auto-capitalize is not a held Shift**: it arms `_pending_auto_cap`, never `_shift_active`. Verbatim inserts spend it (`_consume_auto_cap`); the edit-mode branch ignores it; the end-of-keystroke spend is guarded on `consumed_auto_cap and not rearmed_auto_cap`; `_CARRIES_AUTO_CAP` characters pass it along. Test with even-length punctuation runs.
- **Do not reintroduce the "first-token rescue" or guess-from-the-next-character rules.** A bare `example.com` getting a space after its first dot is a pinned limitation.
- Snippet auto-detection (default ON): `_offered_snippet_values` is written when the user **answers** an offer; `_withdraw_snippet_offer` emits `snippetOfferWithdrawn`; `acceptSnippetOffer` returns a bool QML must honour. Phone detection is digit **grouping** (`_PHONE_GROUPINGS`); an address needs a capitalised street-name word. Pair every positive test with a hostile near-miss.
- The offer toast is parked at the bottom, arms its buttons after 400 ms, stays `Popup.NoAutoClose`, and its 8 s timeout calls `dismissSnippetOffer()`.

## Structured Tokens (numbers, phone numbers, email domains)

Full write-up: `docs/architecture/TEXT_PATTERNS.md` (section of the same name). Read it before changing this area.

`src/prediction/token_predictor.py`: count-weighted whole tokens (phones, zips, house numbers, emails) matched by prefix. **No context model, no fuzzy, no capitalisation**, not merged into word ranking (the two bars are mutually exclusive).

- `_in_token_context()` reads `_raw_token`: a digit anywhere, or a non-leading `@`; two characters minimum. **Every path that repopulates the bar routes through `_refresh_prediction_bar()`**; `_recase_visible_predictions` returns early on a token bar.
- `pressPrediction` dispatches on `_token_pill_words` membership **and** the pill being in `self._predictions` now. `_insert_token_pill`: suffix only when the pill case-sensitively continues the run, else select and overwrite; bypasses `_display_cased`; opens with `_begin_verbatim_insert(prose=False)`; no trailing space after email/phone; Compat Mode rewires it; the `_context_buffer` update is arithmetic on `_context_buffer + _current_word`.
- A tapped pill is one sighting (`_learned_raw_token`). The pill context menu is suppressed on token pills (`isTokenPill`).
- `is_learnable_token`: over `_MAX_LEARNABLE_DIGITS` rejected, phones via `is_phone`, `_NEVER_LEARNED_GROUPINGS` re-blocks the SSN shape, plain words rejected, **Tab never learns**; entries are re-validated on load.
- Use `record_token_prediction_selected`, never `record_prediction_selected`. Nothing logs token content. Stored under `tokens` in `ngram_model.json` (`MAX_TOKENS`), cleared by `clear_user_data()`.
- *Dashboard -> Saved Numbers & Addresses* (`getLearnedTokens` / `forgetToken`, which does not log) is the other half of the admission rule: a learned token must be visible and removable.
- Tests: `tests/test_token_predictor.py`, `tests/test_text_patterns.py::TestLearnableToken`, token classes in `tests/test_keyboard_bridge.py`.

## Dictation (voice input)

Full write-up: `docs/architecture/DICTATION.md` (section of the same name). Read it before changing this area.

Mic in the suggestion bar; off by default, inert without a Deepgram key. Code in `src/dictation/`, `KeyboardBridge._insert_dictated_text`.

- **Built entirely on Qt** (`QAudioSource`, `QWebSocket`): zero new Python dependencies. Don't swap in `sounddevice`.
- `predBar.micReserve` must be 0 whenever the mic is off screen (title-bar mirror `dictationTitleBarButton` when suggestions are off). The busy pulse animates a `pulse` property, **never a bound `opacity`**.
- Insertion opens with `_begin_verbatim_insert()`, sends via `_send_literal_text`, is never gated on privacy mode, mirrors into `_context_buffer` / `_sentence_buffer` (not the deferred space, not in privacy mode), and does **not** teach the model.
- **Privacy mode calls `cancel()`, not `stop()`** (`cancel()` disconnects handlers before aborting).
- `dictation.json` holds the API key: DPAPI on Windows, never logged, never returned to QML in the clear, never in the websocket URL, **never in the Data Backup archive**.
- Toggle, not push-to-talk; four states; **no automatic reconnect**; no transcript content in logs; custom vocabulary via `keyterm`.

## Data Backup (Export / Import)

Full write-up: `docs/architecture/USER_DATA.md` (section of the same name). Read it before changing this area.

`src/data_export.py`: a `.zip` of `manifest.json`, both model files, `analytics.json`, `snippets.json`, `packs/<id>/...`. Settings are not in it.

- **`telemetry.json` and `dictation.json` are excluded** (anon_id would link contributions; a credential must not travel).
- **Import is replace, not merge**: a rescue archive (`<config>/exports/rescue-<ts>.zip`) is written first, files swap via tempfile-then-rename, then `reload_from_disk()`; packs come back disabled.
- **Hardening, do not loosen**: reject `..`, absolute, drive-prefixed and backslash names (zip-slip); extraction is **allow-list** (exact expected paths only); caps `_MAX_FILE_BYTES` 75 MB, `_MAX_TOTAL_UNCOMPRESSED` 500 MB, `_MAX_ARCHIVE_BYTES` 200 MB, re-enforced while writing by `_bounded_copy`, which **translates `zipfile.BadZipFile` to `DataExportError`**; pack ids re-match `PACK_ID_RE` in `src/prediction/pack_ids.py` (reserved device names); a `schema_version` above `SCHEMA_VERSION` is refused. **Any schema change needs a `SCHEMA_VERSION` bump with old import paths kept.**
- Slots: `getDefaultExportDir`, `getSuggestedExportName`, `exportUserData`, `inspectUserExport`, `importUserData` (empty string = success).
- Tests: `tests/test_data_export.py` (`TestInspect::test_zip_slip_rejected`, `test_absolute_path_rejected`, `test_future_schema_rejected`, `test_oversize_entry_rejected`, `TestImport::test_telemetry_not_restored`, `TestBoundedCopy`, `TestReservedPackNames`).

## QML <-> Python Bridge Pattern

QML calls Python via `@Slot` methods on `KeyboardBridge`; Python emits `Signal`s back (the round trip is under *Architecture Overview*; predictions reach QML as a binding on `keyboard.predictions`, not a callback).

Two QML context properties are registered in `keyboard_app.py`: `keyboard` (`KeyboardBridge`) and `telemetry` (`TelemetryBridge`, see *Opt-in Telemetry*), the first surface split off per `docs/architecture/STRUCTURAL_REVIEW.md` 3.1. A further split follows the same shape: its own `QObject` wrapper and context property, registered in `keyboard_app.py` and `tests/qml_context.py::install_context_properties`.

## Caps Lock vs. Shift

Full write-up: `docs/architecture/MODIFIERS_AND_CASING.md` (section of the same name). Read it before changing this area.

- Caps Lock and Shift are **independent toggles** (`capsLockActive`, `shiftActive`). Uppercase output and the `"upper"` layer follow `_shift_active OR _caps_lock_active`; the shifted *glyph* on a symbol key follows Shift only.
- `toggleShift` holds Shift at the OS level (`hold_modifier`) so Shift+click selects in the target app; it auto-releases after one keypress, caps stays. A pill tap releases sticky modifiers **before** the insert.
- **`_display_cased`**, in priority order: (1) Caps Lock on, all upper; (2) any uppercase in the typed prefix, mirror **every** position (fuzzy candidates included); (3) Shift held or `_pending_auto_cap` armed with nothing typed uppercase, capitalise the first letter. Every pill emit site routes through it.
- `toggleCapsLock`, `toggleShift`, `releaseShift`, `lockModifier("shift")` re-query the engine (`_recase_visible_predictions`) rather than re-casing the stored list (it holds the displayed form).
- **The bar drops low-ranked pills rather than eliding any**: `predRow.computeFit(...)` returns `{words, widths}`; **`predBar.predTextInset` is the single source of truth for horizontal padding** (never inline it); `clearCtxReserve` and `micReserve` are subtracted; the row is placed by explicit `x`, not `centerIn`.
- Testing that bar: `findChildren` cannot see Repeater delegates (use `_pill_texts`, assert non-empty); never assert `contentWidth <= width` (`Text.truncated` is the honest signal); sweep widths (`test_every_pill_has_room_for_its_own_text`).

## Editing a Prediction (OSK-friendly edit popup)

Full write-up: `docs/architecture/EDIT_SESSIONS.md` (section of the same name). Read it before changing this area.

- While `_edit_mode_active`, keys emit `editKeyTyped` / `editSpecialPressed` instead of synthesizing; never use Qt focus (our window cannot hold it).
- **`predEditPopup.modal = false` and `closePolicy: Popup.CloseOnEscape` only**: a modal overlay swallows OSK clicks, and every OSK key click is a "press outside".
- **New input surface recipe**: pick a unique name; `keyboard.beginEditSession("<name>")` on open, `endEditSession("<name>")` on close (a no-op unless you hold the session); gate your `Connections { target: keyboard }` `enabled` on `opened && keyboard.editOwner === "<name>"`; add a **separate, unconditionally enabled** `Connections` whose `onEditOwnerChanged(owner)` closes the surface when `owner !== "" && owner !== "<name>"`. `setEditMode(bool)` survives only for Python callers/tests (owner `"legacy"`); no QML calls it.
- **A Ctrl/Alt/Win chord acts on the field or does nothing; it never reaches the app behind us.** `_EDIT_CHORDS` maps Ctrl+a/c/v/x/z/y to `editSpecialPressed("selectall"/"copy"/"paste"/"cut"/"undo"/"redo")`; every other chord is swallowed.
- **Every save path must call `editSavedToast.flash()`** (checkmark click, Return in edit mode, TextField `onAccepted`), or the user thinks the edit was lost.
- Tests: `tests/test_qml_edit_session.py`, `tests/test_keyboard_bridge.py::TestEditSessionsHaveAnOwner`.

## Removed: Swipe / Glide Typing

Swipe typing was removed; issue #39 carries the reasoning and the commit before the removal has the code. It is not a candidate for quiet reintroduction.

- **The overlay caused issue #15**: it took every press in its bounds and resolved it against a registry, so any key missing from the registry was a dead tap. An interceptor that owns every press is the flaw.
- The two registries (decode centres vs hit-test keys) were not duplication; collapsing them caused #15, widening the char filter corrupts decoding. A sustained precise drag is the one gesture an imprecise-motor user cannot make.

## Sticky Modifiers (Shift, Ctrl, Alt, Win)

Full write-up: `docs/architecture/MODIFIERS_AND_CASING.md` (section of the same name). Read it before changing this area.

- Modifiers are **sticky** and held at the OS level (`hold_modifier()` / `release_modifier()`); auto-release goes through the one `_release_sticky_modifiers` (see *Key rules*). `shutdown()` releases anything held.
- **Linux never holds `win` / `super`** (`TestLinuxSuperNeverHeld`); Super chords go through atomic `send_key`. Windows holds `VK_LWIN`.
- `resetModifiers()` runs from `Main.qml`'s `Component.onCompleted`; Caps Lock is deliberately not reset.
- **Right-click locks** Shift / Ctrl / Alt / Win (`lockModifier(name)`, `_*_locked`; **locked implies active**), exempt via the single `_*_locked` guard in `_release_sticky_modifiers`; a left-tap clears it (`_clear_lock`). Caps Lock is not lockable.
- The lock cue is a 3 px `lockBar`. **Never an emoji or icon-sized glyph on a keycap** (Windows renders it in colour via Segoe UI Emoji). The clear-context ring is Feather `rotate-ccw` path data on a Canvas (no `QtQuick.Shapes` / `QtSvg`), centred by `inkOffsetX`; read `TestTheClearButtonIcon` first.
- **Synth invariant: `WindowsKeySynthesizer.send_key` and `replace_text` skip wrapping any modifier already physically held** (`_modifier_already_held`), or the key-up drops a lock. Any new modifier-wrapping synth path needs the same guard.
- Tests: `TestModifierLock`, `TestWindowsSendKeyPunctuationChord`, `TestWindowsReplaceText`.

## Function Keys F13-F24 and Programmable Keys

Full write-up: `docs/architecture/FUNCTION_KEYS.md` (section of the same name). Read it before changing this area.

- F13-F24 are real keys (`VK_F13`-`VK_F24`, X11 keysyms). **macOS stops at F20**; never invent a keycode.
- **With both toggles on, one function row shows at a time** (`Main.qml::functionRowPage`), with a 13th swap key (4-4-4-1) that is QML-only and must **not** release sticky modifiers. The page is remembered state (`savedFunctionRowPage`, not a setting) and feeds `scanRevision`. Both rows are full key height (`root.keyH`). Guard: `tests/test_qml_function_row.py::TestOneFunctionRowAtATime`.
- **`src/key_actions.py` owns the action vocabulary; the bridge switches on nothing.** `KeyActionStore.execute` dispatches through `KeyActionType` records against a two-method `ActionExecutor` and returns a bool. Types: `key` (False), `hotkey`, `text`.
- Dispatch sits inside `pressSpecialKey` **where the keystroke would be sent, not as an early return**, so sticky release still runs. Chords merge held modifiers (`extra_modifiers`); a text action *is* `_commit_verbatim_insert`.
- `key_actions.json`: atomic save, 256 KB cap, bad entries dropped individually, **allow-list sanitisation** (`MODIFIERS`, `CHORD_SPECIAL_KEYS` or one printable ASCII char; a hotkey with no action key is refused). `setKeyAction` returns "did my save stick". **Not in the Data Backup archive.**
- Editor (`KeyActionEditor.qml`): `Popup`, `modal: false`, `closePolicy: Popup.CloseOnEscape` only, edit session `"keyaction"`, parked at top.
- **The only route into the editor is *Settings -> Function Keys*.** Right-click on an F-key does nothing (removed 2026-09-13; don't add it back); `root.settingsReturnView` brings Settings back on the same page.
- Geometry: keys fill the grid width (`FunctionRow._fillKeyW`), group gap `keySpacing * 4`; both rows need `hitMarginH` / `hitMarginV`.
- Test traps: search the editor with `findChild(QObject, ...)`; a QML `var` is a `QJSValue` (`.toVariant()`); `tests/conftest.py` patches `src.key_actions.get_config_dir` by name.

## Settings Panel Structure

Full write-up: `docs/architecture/SETTINGS_PANEL.md` (section of the same name). Read it before changing this area, and pick a new setting's sub-view from the "Where each section lives" table in the doc.

`UnifiedSettingsPanel.qml` is a drill-down: a home grid of six category cards (Appearance, Smart Typing, Function Keys, Dictation, Your Language Model, Data & Privacy), each swapping in a sub-view. `currentView` is one of `"home"`, `"appearance"`, `"typing"`, `"fkeys"`, `"dictation"`, `"model"`, `"data"`; the Flickable holds seven sibling `ColumnLayout`s with `visible: currentView === "<id>"`.

- `settingsPanel.resetToHome()` runs in `onVisibleChanged`: re-opening always lands on home. **The one exception is `root.settingsReturnView`** (Function Keys hands off to the key editor on the keyboard window and returns to the same page); `settingsWindow.onVisibleChanged` consumes it.
- Placement is `root.safePanelPos(w, h)` (shared with Help and the Dashboard), never a primary-screen centre. Guard: `tests/test_qml_panel_placement.py`.
- **Step 0: a secret or device identity never goes in `Settings {}`** (registry key, no size caps). Dictation's key/model/language/device live in `dictation.json` behind `DictationConfig`; only `savedDictationEnabled` is in `appSettings`.
- Adding a setting, 8 steps: (1) `savedFoo` in `Settings{}` in `Main.qml`; (2) root `foo: appSettings.savedFoo`; (3) prop in `UnifiedSettingsPanel.qml`; (4) `SettingsToggle` in the right sub-view's `SettingsSection`; (5) pass-through `foo: root.foo`; (6) `onSettingChanged`: update root, save, call bridge; (7) optional `@Slot` on `keyboard_bridge.py`; (8) load in `Component.onCompleted` if the bridge needs it.
- If you cannot decide which category a toggle belongs in, push back on the requirement before adding it.

## Adding a New QML Component

1. Create `qml/components/MyComponent.qml`
2. It's auto-discovered - the `components/` directory is imported as `"components" as Comp` in Main.qml
3. Use as `Comp.MyComponent {}` in Main.qml

A component whose root is a `Window` (a floating popup rather than an in-line
panel) must still be instantiated as a child of the keyboard window's own
tree, or `transientParent()` cannot resolve back to it. `SnippetsWindow.qml`
and `SymbolsWindow.qml` are the examples: both are declared once, as
`Comp.SnippetsWindow { ... }` / `Comp.SymbolsWindow { ... }`, inside
`background` in `Main.qml`.

## Fuzzy Recognition Defaults

Full write-up: `docs/architecture/FUZZY_RECOGNITION.md` (section of the same name). Read it before changing this area.

One default in `src/prediction/fuzzy_recognizer.py` (no profiles, no UI); tune by overriding class attributes.

- `spatial_uncertainty` 1.4 (whole-word paths only), `confidence_threshold` 0.65, `autocorrect_margin` 1.5, `prediction_weight` 0.6, `min_prob` 0.001.
- `_TRANSPOSITION_PROB` 0.30, `_DELETION_PROB` 0.20, `_INSERTION_PROB` 0.15, `_APOSTROPHE_INSERTION_PROB` 0.50.
- **`QWERTY_POSITIONS` is a-z plus 0-9 only**; punctuation and numpad unmapped. New layouts: letters + digit row only.

## Testing

Full write-up: `docs/build/TESTING.md` (section of the same name). Read it before changing tests or CI.

- Run: `python -m pytest` (`-k fuzzy`, `-k property`, or one file). Gate: `python check.py` (`--full` adds coverage).
- **Property tests**: a new keystroke path or modifier slot gets a rule in `tests/test_property_keystroke_state.py`; do not weaken `tests/fake_os_keyboard.py` to make a failure go away. A new store's loader belongs in `tests/test_property_loader_fuzz.py`. Don't filter with `assume()`; build the strategy.
- An intended engine change fails `tests/test_model_fingerprint.py` by design: regenerate with `ALPHA_OSK_UPDATE_SNAPSHOT=1`. New sorts over scores need a string tie-break.
- Autouse guards in `tests/conftest.py`: `_unplug_the_live_desktop` stubs password/outside-click probes; `_no_real_update_relauncher` stubs `updater._spawn_relauncher`.
- Set privacy mode via `setPrivacyMode()`, never `_privacy_mode`.
- **QSettings, registry or fixed temp paths must be keyed per xdist worker** via `tests/qt_settings_scope.py`.
- **The shard hash is `crc32(nodeid)`, never `hash()`.**
- **A Qt test fixture builds a `QGuiApplication` (never `QCoreApplication`) and asserts the organisation name** in `qapp`.
- Branch protection requires the `Tests` job, not the shards. **Merge with `python scripts/merge_pr.py <n>`, never a bare `gh pr merge --admin`.**

## Word Suppression and Boosting

Full write-up: `docs/architecture/WORD_ADJUSTMENTS.md` (section of the same name). Read it before changing this area.

- Right-click a pill: **Show more** (+5 to `unigrams` / `user_vocab`, recorded in `preferred`), **Show less** (`dispreference`, weight `1 / (1 + count * 0.5)`), **Remove** (`blacklist`). Persisted in `ngram_model.json`.
- `unprefer(word)` rolls back the cumulative boost, **capped at the current `user_vocab` count** so organic learning survives.
- **Auto-rehabilitation**: typing a blacklisted word 3 times (space-completed) restores it (`_blacklist_type_count`).
- Bridge slots: `markGoodSuggestion`, `markBadSuggestion`, `blacklistWord`, `unprefer`, `unblacklistWord`, `undisprefer`.

## Model Visualization

Full write-up: `docs/architecture/ANALYTICS.md` (section of the same name). Read it before changing this area.

- Settings -> Your Language Model -> Open Dashboard: Word Cloud, Word Flow and Dashboard tabs, fed by `keyboard_bridge.getVisualizationData()` -> `ModelVisualization.qml`; drill-down via `keyboard.getWordContext(word)`.
- **`activeContextChanged(prev_word, current_partial)` is suppressed in privacy mode**: it must not leak password characters or field context.

## Privacy Mode & Password Detection

Full write-up: `docs/architecture/PRIVACY_MODE.md` (section of the same name). Read it before changing this area.

- **Detection**: a 200 ms `QTimer` (`_check_password_field`) plus a ~50 ms rate-limited `_check_password_field_sync()` on **every** keystroke. Windows: UIA, then Win32 `EM_GETPASSWORDCHAR`; Linux: AT-SPI 2; code in `src/platform/password_detect.py`. The title-bar **Learning** switch overrides it.
- **Fails open**: with no working backend (`detection_available()` / `KeyboardBridge.passwordDetectionAvailable`) a WARNING is logged and the UI says so; the manual toggle is then the only protection.
- **When active**: keys still reach the OS, but `_current_word`, predictions, learning and `activeContextChanged` are suppressed.

### Clearing stale context when focus or the caret moves

Full write-up: `docs/architecture/CONTEXT_RESET.md` (section of the same name). Read it before changing this area.

Six signals reset context: foreground window, focused element, caret position (4 Hz in `_check_foreground_window`), a click outside our process (`src/platform/pointer.py`), Tab, `_NAV_KEYS`. **Stale `_current_word` / `_context_buffer` is a pill tap that eats text elsewhere.**

- A `None` token means unknown; state is untouched.
- **The outside click resets mid-word; `_check_caret_moved` keeps its only-between-words guard. Do not re-unify them.**
- Clicks settle over `_CLICK_SETTLE_MS` against `_caret_before_click`; `_note_own_keystroke` is set in the synth wrappers. Within-window resets pass `keep_snippet_offer=True`.
- **The tail of an interrupted word is never learned** (`_word_prefix_lost`); tests must type the word three times.
- Only the **high bit** of `GetAsyncKeyState` is read. Tab does not learn; Delete and Escape skip the nav reset.
- Tests: `TestTabClearsContext`, `TestCursorMotionClearsContext`, `TestOutsideClickClearsContext`, `TestAnOutsideClickThatMovesNoCaretIsLeftAlone`, `TestCaretMoveClearsContext`, `TestAnInterruptedWordIsNotLearned`, `tests/test_pointer.py`.

## Themes

Defined in `themeData` in `Main.qml`. Each theme has: `name`, `background`, `keyColor`, `keyPressed`, `textColor`, `accent`, `border`.

**9 themes**: Dark, Light, Ocean, Forest, Amethyst, Vaporwave, Blackboard, Typewriter, Spaceship.

Theme colors flow to all components: main keyboard keys, prediction pills, nav panel, numpad, title bar icons, and active key states (NumLock, Shift, etc.). `KeyButton.qml` auto-computes text contrast on active/pressed states using luminance.

Theme picker in settings shows labeled color swatches with mini key previews.

## Vocabulary

Full write-up: `docs/architecture/VOCABULARY.md` (section of the same name). Read it before changing this area.

- Base: Google 10K + 10K supplement + `data/english-expanded.txt` (SCOWL, sha256-pinned in `data/english-expanded.manifest`), ~83K words; SCOWL enters at **one base count each**. Slurs removed at generation.
- Packs: none ship, import-only (see *Vocabulary Packs*).
- Numpad: NumLock toggles numbers and navigation; mirrors a physical 10-key; key 5 blank in nav mode.

## Explicit content is filtered from suggestions, not from the vocabulary

Full write-up: `docs/architecture/VOCABULARY.md` (section of the same name). Read it before changing this area.

*Smart Typing -> Suggestions -> Filter Explicit Words*, **default ON**. Words stay in the dictionary, typable and learnable; the setting decides only what the bar volunteers. **Owner's decisions: profanity ships and is filtered (2026-09-16), slurs are removed outright (2026-09-23). Do not re-litigate.**

- `data/explicit_words.txt` is **generated** (`scripts/gen_explicit_words.py`), exact words, set lookup.
- Matching is **stem plus closed suffix set, never substring** (Scunthorpe); `"spook"` is not a stem. Stems (`data/explicit_stems.txt`) only seed the filter; **nothing is filtered at generation time**.
- **Applied only in `_finalize_scores`**; never a second copy elsewhere.
- **Personal vocabulary outranks it**: one typing is enough (known-word branch).
- **Fails open**: missing list leaves it inert (`explicit_filter_available`).
- Test: `tests/test_explicit_filter.py`, each case paired with its inverse.

### Slurs are removed, not filtered

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `data/slurs.txt` lists **exact words, never stems**; excluded at generation, stripped from saved `unigrams` on load unless in `user_vocab`. No setting restores them; they stay typable and learnable. Ordinary-sense words are filtered instead (`FILTER_ONLY_WORDS`).
- Tests: `tests/test_slurs.py` (removals paired with near-misses that must ship).

## Vocabulary Packs

Full write-up: `docs/architecture/VOCABULARY.md` (section of the same name). Read it before changing this area.

- Import-only, **no built-in packs ship**. `src/prediction/vocabulary_pack.py` (`VocabularyPack`, `PackManager`) discovers packs in the user dir (`<config>/packs/`). A pack is a folder with `dictionary.txt` (required), optional `bigrams.txt`, `trigrams.txt`, `pack.json`.
- **Import hardening is security-critical, don't loosen**: ids sanitised to `[a-z0-9_-]{1,64}`, Windows reserved device names rejected, destination verified under `user_packs_dir`, symlinks skipped. The id rule lives once in `src/prediction/pack_ids.py`, shared with `data_export.py`. Tests: `tests/test_vocabulary_pack.py::TestImportPackSecurity`, `::TestPackInputCaps` (byte caps reject whole files, entry caps truncate).
- A pack's `name`/`description` are attacker-controlled: `_clean_meta_text` on load (single line, 200 chars), and every `Text` rendering them sets `textFormat: Text.PlainText`.
- `apply_to_predictor` uses `max()` for bigrams/trigrams, not addition (additive would compound per enable cycle).
- **Known limitation: disabling a pack does not undo its predictor injection** until restart.

## Analytics

Full write-up: `docs/architecture/ANALYTICS.md` (section of the same name). Read it before changing this area.

- `src/analytics.py`; all-time stats in `<config_dir>/analytics.json`. **Every metric needs both a session and an `_alltime_*` form** (surfaced as `<metric>` and `alltime<Metric>` in `get_session_stats()`).
- Word and key frequency tables are capped at 5000 (`_WORD_FREQ_CAP` / `_KEY_FREQ_CAP`) on load and save. `_load_alltime` `stat()`s against `_MAX_STATS_FILE_BYTES` (5 MB) before reading and parses into locals before assigning to `self`.
- **Do not reintroduce the composite Prediction Quality Score** as a primary surface.
- `StatBox` grows its background from `contentCol.implicitHeight + 14`; keep the inner ColumnLayout anchored horizontally + verticalCenter only (never `anchors.fill: parent`).
- A new prediction surface just calls `record_prediction_selected` with a 1-based rank; `top_pick_count` increments on rank 1.

## Prediction & Autocorrect - Architecture Notes

Full notes in **`docs/architecture/PREDICTION_NOTES.md`** (the "unified system" framing, fragment filter + repetition gate, autocorrect thresholds, reinforcement-on-click, backspace-as-negative-signal, the prioritized future-work gaps, and reference implementations). Per-algorithm deep dives: `FUZZY_RECOGNITION.md`, `PPM.md`, `HYBRID_MERGING.md`.

Load-bearing defaults to keep in mind: **space-time autocorrect is OFF by default** (`KeyboardBridge._autocorrect_enabled = False` - corrections surface as pills, never silent overwrites); the autocorrect gate skips typings under 3 chars and runs an absolute + relative threshold so deliberate typings ("thru", "lol") survive; n-gram scoring is linear interpolation in probability space (lambda = 0.5/0.3/0.2); unknown words promote into `user_vocab` only after 3 sightings (pill clicks gated the same way).

## Compact View

Full write-up: `docs/architecture/COMPACT_VIEW.md` (section of the same name). Read it before changing this area.

A denser 13x4 keyboard, off by default (*Appearance -> Panels -> Compact View*).

- **Every compact row totals the same unit count** (13.0 for `qwerty-compact`); `totalKeyUnits` is derived (`_widestRow`), never a constant. `resolveLayoutId()` falls back to full size when no compact variant exists.
- **Layers are a QML-side concept; the backend never sees them.** A `"type": "layer"` key sets `activeLayer` and must not call `keyboard.setLayout()`; every layer switch calls the idempotent `keyboard.releaseShift()`. The `?123` page carries no Shift key and offers only glyphs that have a key of their own (which caps it at 29).
- **No panel that lines up with the grid may use `QtQuick.Layouts`** (it rounds to whole pixels).
- The accent fill on editing keys is `root.accentWashFor()`, never the raw accent; **Enter wears the same wash and no hue of its own**. **No key takes an accent-coloured border, on either view** (removed 2026-10-05). Guard: `TestNoKeyTakesAnAccentRing`.
- Del is on the base layer, Esc on `?123`; the Number Row panel's leading Esc is a deliberate duplicate.
- **Both letter rows open with a 1u key, Tab over Caps, so `w` sits above `s`**; Tab and Caps lead rows 1 and 2 on `?123` too. A 13u row has no spare unit, so **compact's Backspace is 1u** (Enter keeps 2u). Guards: `TestCompactLayout::test_w_sits_above_s`, `::test_the_left_column_is_the_same_on_both_layers`.
- `NumberRow.qml` shows whenever the **derived** `Main.qml::showNumberRow` is true, declared **below** both function rows. **It is on screen on every layer, so no compact layer may draw digits of its own**; hiding it on the symbol page is the wrong fix. Guard: `tests/test_layouts.py::test_no_digit_appears_on_a_symbol_layer`.
- `tests/test_qml_compact_view.py` and `tests/test_qml_prediction_bar.py` load the real `Main.qml` headlessly and fail on QML warnings: the only guard against a blank keyboard from a binding error.

## Dead space between keys

Full write-up: `docs/architecture/LAYOUT_GEOMETRY.md` (section of the same name). Read it before changing this area.

- Every `KeyButton`'s MouseArea reaches half a gap past its slot (`hitMarginH` / `hitMarginV`). **Half each, never more** (an overlap goes to the later-declared key); the vertical share carries an extra half pixel on purpose. The press handler subtracts the margin before the ripple origin and `pressDx` / `pressDy`.
- **A new key or panel must be passed both margins** (default 0, no cascade); `Main.qml` owns `keyHitMarginH` / `keyHitMarginV` / `panelHitMarginV`.
- Rejected: growing keycaps, and one MouseArea over the whole grid. `FunctionRow`'s group gap stays dead.
- Test: `tests/test_qml_compact_view.py::TestNoDeadStripBetweenKeys`.

## Removed: the full-size symbol layer

Full write-up: `docs/architecture/LAYOUT_GEOMETRY.md` (section of the same name). Read it before changing this area.

- The full-size `Sym` page was removed 2026-09-05 (glyphs are in the Symbols & Emoji window); the full-size layout files declare no layers.
- **The space bar's centre stays at 8.25u** (Win 1.0u, four Ctrl / Alt keys equal): `tests/test_layouts.py::TestTheFullSizeSpaceRow`.
- Del stays off the full-size grid; Enter's width puts Q over A. Compact's `?123` is not removable the same way; its second page went 2026-09-21 (see *Compact View*).

## Full-size rows are flush (every row is 15.5u)

Full write-up: `docs/architecture/LAYOUT_GEOMETRY.md` (section of the same name). Read it before changing this area.

- Every full-size row totals exactly 15.5u (compact 13.0u), or `Main.qml` centres it and the edges go ragged. Tab = `\` = Caps = 1.75u, Enter = both Shifts = 2.75u, space 9.5u.
- **Equal units are not equal pixels**: each row absorbs its gap shortfall into its key widths (`rowKeyW`); widths match exactly, origins within a pixel. W over S reduces to Tab and Caps being the same width.
- Measure first key edge to last key edge, not the `Row`'s bounding box (phantom pixel).
- Tests: `TestEveryFullSizeRowIsFlush`, `TestEveryGridRowIsPixelFlush`, `TestTheLetterColumnsLineUp`.

## The three sections share one height

Full write-up: `docs/architecture/LAYOUT_GEOMETRY.md` (section of the same name). Read it before changing this area.

- Grid, nav cluster, numpad and both separators lay out to `Main.qml::sectionHeight`, **the grid's implicit height and nothing else** (a `Math.max` over the three cannot work). Panels grow their **key heights**, never their gaps.
- A panel's `implicitHeight` derives from `keyH` (`Math.ceil(keyH)`), never its own grid (binding loop). Separators use `Layout.preferredHeight: root.sectionHeight`, not `fillHeight`.
- **A headless test that toggles a row must force a frame before measuring** (`_relayout` calls `grabWindow`).
- Test: `tests/test_qml_key_colors.py::TestTheSectionsShareOneHeight`.

## Key Colours by role

Full write-up: `docs/architecture/KEY_COLOURS.md` (section of the same name). Read it before changing this area.

Six schemes (`mono` **default**, `twotone`, `bands`, `ink`, `signal`, `off`). Engine `qml/palette.js`, wiring `Main.qml::keyRoles` / `keyRoleFor`, resolution in `KeyButton` (`_roleFill` / `_roleInk` / `_roleBar`), never at call sites.

- **`off` hands down a null table, read everywhere as "keep your own tint".** No per-surface `off` branch; an unknown id reads as `off`.
- **No hue is a literal**: placed off the theme accent in **OKLCh** (not HSL); anchored roles (`kill`, `commit`, `mod`) are pulled at most 22 degrees toward the accent; `fromOklch` reduces chroma rather than clamping channels.
- **Every fill goes through `washFor`** (walks strength down until `textColor` clears 4.5:1); `Main.qml::accentWashFor` delegates to `palette.js`, the single copy of the WCAG maths. `_dimmedInk` and `hoverFill` are guarded too.
- A modifier's theme colour is its **click** colour; `mono` is neutral for every role but `toggle`.
- **A scheme colours the pill ring (`pill.bar`), never the fill**, and may not soften it; only `bands` has its own.
- `roleForKey` reads layout JSON `type` plus the key; numpad roles follow NumLock (`NumpadPanel.numRole`); compact's embedded nav keys are `nav`. The stripe hides while pressed, active or locked.
- Tests: `tests/test_qml_key_colors.py` (540-combination contrast sweep, paired with "bands stay tellable apart").

## Symbols & Emoji window

Full write-up: `docs/architecture/SYMBOLS_WINDOW.md` (section of the same name). Read it before changing this area.

Alpha button in the suggestion bar (title-bar twin when suggestions are off). Catalogue `src/glyphs.py`, UI `qml/components/SymbolsWindow.qml`.

- **A tap types, it does not copy**: `insertGlyph(str) -> bool` calls `_commit_verbatim_insert`, not gated on privacy mode. QML honours the bool: a refused tap flashes the problem toast and is not written to Recent.
- Recent lives in `appSettings.savedRecentGlyphs` (cap `getRecentGlyphLimit()`); malformed values are dropped silently.
- Same shell as Snippets (flags, drag, desktop-wide clamp, named in `_wire_floating_windows`). Grid model equals the page size; category tabs are a wrapping `Flow`.
- Chrome icons are `StrokeIcon`; **glyph cells name no font family**. `predBar.predButtonCount` derives the button reserve; new buttons go first in the right-anchored Row.
- Tests: `tests/test_qml_symbols.py`, `tests/test_glyphs.py`, `TestTypingAGlyphFromThePicker`.

## Switch-scanning targets (external scanners over UI Automation)

Every visible key and pill is a UI Automation `Button` for external scanners (Switchify PC, issue #106). Contract: `docs/architecture/UIA_TARGETS.md`. Guard: `tests/test_qml_scan_targets.py`.

- **UIA, not an IPC server** (`uiAccess="true"` makes a pipe a confused deputy). Don't "upgrade" to a socket.
- Ids `aosk.v1.<section>.<row>.<index>` from `Main.qml::scanTargetId`; pills `aosk.v1.pred.<index>.g<generation>`.
- **Presence means activatable, rechecked at activation** (`KeyButton._scanActivatable`); a `KeyButton` without `targetId` is unreachable.
- `Accessible.name` is a speakable label (`_scanName`); only real toggles report toggle state; the lock rides in `Accessible.description`.
- Anything that can change the target set feeds `Main.qml::scanRevision` (the `aosk.v1.revision` beacon). Compare, never parse.
- **An Invoke carries no click position** (`pressFromPointer` false) and is a one-shot (`activateFromAssistiveClient`), never `_activate()`.
- WindowPattern is safe because restore never takes the foreground (`QuietRestoreFilter`), a non-quit close minimizes while a shell close quits (`ShellCloseFilter`), and a minimized keyboard offers no targets. `surface_existing_instance` uses `SW_SHOWNOACTIVATE`, never `SetForegroundWindow`.
- **Stale pills**: `invokeScanPrediction(word, generation)` refuses a dead generation; keep both halves.
- Window AutomationId `alphaOsk.alphaOskKeyboard` (`_name_for_ui_automation`).
- PySide cannot read attached `Accessible.*`: keep values in named properties, **never inline an expression into the `Accessible` block**.

## Modular Layouts

Design doc: `docs/architecture/MODULAR_LAYOUTS.md` (four levels of modularity, action types, profiles). Inspired by Octavium's Layout/KeyDef model.

## Auto-Update

Full write-up: `docs/build/AUTO_UPDATE.md`. Release checklist: `docs/build/WINDOWS.md`. Code: `src/updater.py`.

- **Releases live in the separate public repo `owenpkent/alpha-osk-releases`; `gh release create` must always pass `--repo owenpkent/alpha-osk-releases`** (the updater's API URL is hard-pinned there). Builds through 1.2.2 are pinned to the old `okstudio1` path and survive on GitHub's transfer redirect, so **a repo named `alpha-osk-releases` must never exist under `okstudio1` again**, and `_is_safe_download_url` must stay host-scoped, never growing a path check.
- Version source of truth is `src/__version__.py`; the asset name must be exactly `Alpha-OSK-Setup-{version}.exe`.
- **Install path is pinned, not read from the registry**: `_install_target_dir()` feeds every silent install an explicit `/S /D=<dir>`. `/D=` must be last and unquoted; don't reorder or requote.
- **`_verify_signature(exe_path, expected_version)` also pins the version** (embedded `FileVersion`, first three components), because the asset filename is a selector, not a trust boundary.
- Never expose the download URL to QML (the bridge emits primitive ints only). The update screen is `UpdateFlow`; `_run_headless` stays as the test target.
- **The update helper is its own exe, `alpha-osk-relauncher.exe`, built WITHOUT UIAccess (`uac_uiaccess=False`), run from a staged copy in `%TEMP%`; all three are load-bearing.** `build.py::verify_helper_does_not_request_uiaccess` and `verify_helper_stage_runs` guard it. Never launch a frozen bundle on the interactive desktop (use `hidden_desktop.py`). End its loop with `app.exit()`, never `quit()`.
- **No test may reach the real `_spawn_relauncher`** (autouse guard in `tests/conftest.py`).

## The website (alphaosk.com)

Full write-up: `docs/build/WEBSITE.md` (section of the same name). Read it before changing this area.

- Source is the separate repo `owenpkent/alpha-osk-website` (static, no build, Netlify). **A release never edits it**: `scripts/alphaosk.js` reads the latest tag from the releases API at page load. Its CSP must keep `https://api.github.com` in `connect-src`.
- **This repo is the authority for its content** (`README.md`, `docs/PRIVACY.md`). Changing the platform table, the telemetry-endpoint note or the test count here means changing them there in the same sitting.
- Screenshots and icons are generated (`scripts/capture_screenshots.py`, that repo's `tools/gen_assets.py`), never hand-made or real typing.

## Accessibility Ecosystem

Design doc: `docs/roadmap/ECOSYSTEM.md`. Alpha-OSK is one of four sibling tools (MacroVox text, Octavium MIDI, Nimbus joystick; all under `C:\Users\owenp\dev\`, same developer and EV cert). See also `docs/roadmap/MACROVOX_INTEGRATION.md`.

## Federated Learning

Design doc: `docs/roadmap/FEDERATED_LEARNING.md`. Not yet implemented; Phase 1 (local delta computation) is next.

## Multilingual Input (French, Pinyin, Kanji)

Full write-up: `docs/architecture/LANGUAGE_PROFILE.md` (section of the same name). Read it before changing this area. Research (not implemented): `docs/roadmap/MULTILINGUAL_INPUT.md`.

- `language.py::LanguageProfile` is a frozen dataclass; **English is a profile** (`language.ENGLISH`). `NgramPredictor(model_path, profile)` / `HybridPredictor(..., profile=...)` take one, **passed down, never a module-level "current language"**. `tests/test_language_profile.py` uses un-English profiles.
- Not on the profile: key positions (layout's) and `text_patterns` locale data (**a field nothing reads is worse than none**).
- **Fuzzy key positions are layout-derived**: `fuzzy_recognizer.positions_from_layout(rows)`, pushed by `KeyboardBridge._apply_layout_key_positions` on construction and `setLayout`. Slot = **key index within its row, not summed widths**; punctuation is dropped but consumes a slot; digit row aligns by digit index.
- `FuzzyRecognizer.set_key_positions` **must re-point `word_generator.spatial_model`**; an empty mapping is ignored.
- Test: `test_qwerty_json_reproduces_the_hardcoded_table`.

## Opt-in Telemetry

Full write-up: `docs/architecture/TELEMETRY.md` (section of the same name). Privacy: `docs/PRIVACY.md`. Backend: `backend/cf-worker/`.

- **Off by default.** A weekly POST carries exactly ten fields: `anon_id`, `app_version`, `os`, `keystrokes`, `words`, `predictions`, `keystrokes_saved`, `minutes`, `sessions`, `prediction_offers`. Never content, frequencies, IP or per-session data.
- **Own QML context property**: `TelemetryBridge` (`src/telemetry_bridge.py`) registered as `telemetry`; `KeyboardBridge` does not reference it. Fixtures: `tests/qml_context.py::install_context_properties`.
- **`TelemetryClient` is the source of truth for consent; do NOT mirror it into `appSettings`.**
- **`DEFAULT_ENDPOINT` is the production worker**, never staging or localhost (empty = silent no-op).
- **anon_id is cleared on opt-out**; "Delete my contributed data" POSTs `/v1/forget`.
- Submits are gated on `enabled AND endpoint AND anon_id`; privacy mode needs no special handling.
- **Worker limits are keyed on `anon_id`, never a header**: `RATE_LIMITER` plus a `SUBMIT_COOLDOWN_SECONDS` cooldown in **both** upserts. **Every reject path returns the same 204 as success** (no existence oracle).

## The user study

Full write-up: `docs/research/STUDY_HARNESS.md`. Protocol `STUDY_PROTOCOL.md` and consent `STUDY_CONSENT.md` were published before enrolment (git history stands in for preregistration): **don't edit them casually**. Harness: `src/study/`.

- **"Predictions off" must NOT be `suggestionsEnabled`**; the off condition keeps the bar reserved and empty.
- **Learning is frozen via `HybridPredictor.frozen_learning()`**; `tests/test_learning_freeze.py` classifies every public method. Privacy mode is not a substitute.
- Trials type into a `RecordingSynthesizer` (`begin_study_capture`), **not through edit mode**.
- Conditions are data (`session.DESIGNS`); nothing is sent until the participant submits; study and telemetry have separate consent.

## Telemetry: the installer invitation, and going live

Full write-up: `docs/architecture/TELEMETRY.md` (section of the same name).

- The installer page (`StudyInvitePage`) **ships its checkbox ticked**, defensible only because it states the purpose, shows the exact payload and declines in one click (`TestTheCheckboxDefaultsToChecked`). It does not enrol anyone in the study.
- **The 140u layout budget is nearly spent**; a control past 140u never draws (`TestTheStudyPageFitsItsDialog`).
- **The seed key is `HKLM\Software\alpha-osk-setup`, NOT `alpha-osk`**; consumed once by `TelemetryClient.apply_install_invite()`.
- Check the page via a harness from `_generate_nsi_script(...)` output; never screen-capture.
- **Going live is four coupled steps**: deploy the worker, set `DEFAULT_ENDPOINT`, set the `TELEMETRY_ENDPOINT` repo variable, ship a build with the invitation.

## Building & Signing a Release (Windows)

Full write-up: `docs/build/WINDOWS.md` (checklist, signing, version resource) and `docs/build/RELEASE.md` (EULA, lockfile, SBOM, CVE scanning). Assets/icons: `docs/build/BRANDING.md`.

1. Bump `src/__version__.py`; update `CHANGELOG.md`, commit.
2. Build + sign from a **non-elevated shell** with the eToken plugged in: `python build/windows/build.py` (elevated shells get "Cannot find certificate").
3. Test the installer in `release/`, including UIAccess against an elevated shell.
4. `git tag vX.Y.Z && git push origin main && git push origin vX.Y.Z`.
5. `gh release create vX.Y.Z <installer> <lockfile> <sbom> --repo owenpkent/alpha-osk-releases`. `--repo` is mandatory (the updater pins that repo). Upload the lockfile and CycloneDX SBOM too.
6. `python scripts/downloads.py` counts downloads (directional).

- The exe carries a generated version resource (`build/windows/version_resource.py`); its publisher must equal `APP_PUBLISHER` in `build.py`. It does not rename an existing taskbar pin (unpin and re-pin).
- NSIS shows a checkbox-gated EULA page from `build/windows/LICENSE.rtf`; keep it in sync with the root `LICENSE`.
- Dependencies are exact-pinned (`==`; `pyobjc-framework-*` excepted).
- **osv-scanner: never flip `fail-on-vuln` off**; quarantine with a time-boxed `osv-scanner.toml` entry in the scanned lockfile's own directory. The PR job name `OSV Scanner (deps CVE check) / osv-scan` is required by branch protection.

## macOS build (in progress)

Full write-up: `docs/build/MACOS.md` (section "macOS build status"). Code: `src/platform/macos.py`, `macos_window.py`; build scaffold `build/macos/` (not yet exercised end to end).

- `"win"` maps to Command. Password detection is done: `_MacOSAXDetector` goes through the frontmost *application*, **never `AXUIElementCreateSystemWide()`**.
- Keystrokes need an Accessibility TCC grant; without it they silently no-op and password detection fails open.

## Linux build

Full write-up: `docs/build/LINUX.md` (section "Linux build"). Build: `python build/linux/build.py [--appimage --fetch-appimagetool]`.

- `appimagetool` is pinned to tag `1.9.1` and verified against `APPIMAGETOOL_SHA256`: **bump the tag and the hash together**, never the mutable `continuous` tag.
- `xdotool` / `ydotool` are not bundled; without them key synthesis silently no-ops.

## Git Conventions

Full write-up: `docs/build/GIT.md` (section of the same name). Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`.

- `python scripts/clean_branches.py [--dry-run | --install-hook]` deletes local branches whose PR merged. **It must not use `git branch -d`** (squash merges make every merged branch look unmerged) **nor `git diff main <branch>`**; it asks the PR API instead.
- It fails closed: no upstream, no `gh`, `main`, the checked-out branch, and branches checked out in another worktree are all kept. Guard: `tests/test_clean_branches.py`.

## Community Files

The repo ships the standard GitHub community health files at the top level and under `.github/`:

- `CODE_OF_CONDUCT.md` - Contributor Covenant 2.1. Reports go to owenpkent@gmail.com with subject `CONDUCT: alpha-osk`.
- `CONTRIBUTING.md` - dev setup, `check.py` pre-push gate, conventions, PR flow. Points new contributors at this file as the architecture map.
- `SECURITY.md` - private vulnerability reporting via the releases repo's GHSA form, email fallback.
- `.github/ISSUE_TEMPLATE/bug_report.yml` and `feature_request.yml` - form templates. `config.yml` disables blank issues and links to the security advisory + Discussions.
- `.github/pull_request_template.md` - summary, type, test plan, accessibility check.

If you change the security reporting flow, the CoC contact email, or the contribution gates, update both the relevant file and the cross-references in `CONTRIBUTING.md` / `bug_report.yml`.

## Known Issues

None open here. IDE pill duplication is handled by auto-compat (`_COMPAT_PROCESS_NAMES`, matched on exe basename, **not** window class); see `docs/architecture/GOTCHAS.md`.

## Things to Watch Out For

Full list: `docs/architecture/GOTCHAS.md`. Read it before touching keystroke synthesis, the prediction context buffers, window flags, or the build pipeline. Focus flags, sticky-modifier release, suffix-only insertion and buffer invariants are in *Key rules*.

- **Windows uses scancode mode** for `send_text` (ASCII) and chords/`hold_modifier` (UNICODE only as fallback): needed for Blender/VirtualBox/games and Ctrl+V over TeamViewer/RDP.
- **`xdotool`/`ydotool` calls carrying arbitrary typed text must precede it with a literal `--`** (getopt would eat `--help`). `platform/linux.py::_run()` is bounded by a 2.0 s `_SUBPROCESS_TIMEOUT_S` because it runs on the UI thread.
- **Games need a held key, not a zero-gap tap**: when `_window_is_game(hwnd)` is true (exe in `_GAME_PROCESS_NAMES`, exe ends with a `_GAME_EXE_SUFFIXES` engine suffix, or the borderless-fullscreen heuristic, skipped for `_COMPAT_PROCESS_NAMES`), single keys are sent with a 50 ms hold. Tests: `TestGameKeyHold`.
- **`pressKey` lowercases its input**: use `pressKeyLiteral` when QML already resolved the final character.
- **Any QML `Text` showing imported or untrusted data must set `textFormat: Text.PlainText`** (AutoText can fetch an `<img>`). Known gap: `ToolTip.text` has no `textFormat`.
- Compatibility Mode matches IDE/RDP **exe basenames** in `_COMPAT_PROCESS_NAMES`, never window class.

## Who rounds the window corners

Full write-up: `docs/architecture/WINDOW_CHROME.md` (section of the same name). Read it before changing this area.

- Keyboard, Snippets, Symbols and Dashboard are transparent (`WS_EX_LAYERED`), so QML `radius` leftovers render **white** on Windows. `Main.qml::selfRoundedCorners` is false there and `windows_window.py::_prefer_dwm_rounded_corners` asks DWM for `DWMWCP_ROUND`. Turning DWM rounding off fixes nothing.
- The title bar's radius follows the background's. Floating windows take `selfRoundedCorners` as a required property and are named in `_wire_floating_windows`; the Dashboard gets only the DWM call (no `WS_EX_NOACTIVATE`).
- The DWM call runs before the style writes, is best-effort (Windows 11), and must never fail startup.
- `scripts/capture_screenshots.py` sets `selfRoundedCorners` true, so it is not `readonly`. `tests/test_window_corners.py` pins only the checkable half.

## The taskbar button appears on launch (hide, restyle, re-show)

Full write-up: `docs/architecture/WINDOW_CHROME.md` (section of the same name). Read it before changing this area.

- On the `taskbar_button` path `apply_extended_styles` hides the keyboard, writes the styles and re-shows it, in a `finally`, with `SW_SHOWNOACTIVATE`, never `SW_SHOW` / `SW_SHOWNORMAL`. A non-visible window is left alone; floating windows (`taskbar_button=False`) are never blinked.
- Tests: `tests/test_windows_window.py::TestTheTaskbarButtonAppearsOnLaunch`.

## Title-bar window menu, and click-free Move

Full write-up: `docs/architecture/WINDOW_CHROME.md` (section of the same name). Read it before changing this area.

Right-clicking the title bar opens Move / Minimize / Tuck away (X11 only) / Close. Guard: `tests/test_qml_window_menu.py`.

- **`titleBarMenuArea` is declared before every other input-taking child of `titleBar` and accepts only `Qt.RightButton`**; on top it would kill dragging.
- **`dragArea` reserves `titleButtons.width`, never a constant** (`TestTheWholeStripDrags`).
- **Move mode** is two taps: window follows `(current - anchor)`, `windowMoveOverlay` is `enabled: root.moveMode` and swallows the ending click, left puts it down, right puts it back (`_moveReturnX/Y`); no Escape.
- **Always on Top** (default ON) changes the native Z-band only (`window_band.set_keyboard_topmost`), **never the Qt flags**. The pickers and Settings are owned windows that follow the keyboard's band, so every path that demotes the keyboard ends in `_restore_floating_bands`. See the doc's *Always on Top* section.
- **Magnetic edges** (default ON): both move paths go through `Main.qml::snapWindowPos`, `snapThreshold` 24 px. **The snapped value is never written back into what the caller accumulates** (`freeX` / `freeY`); Move mode re-anchors by how far the window actually went, or an edge becomes a trap.
- Tests drive the pointer in desktop coordinates; `_park` turns snapping off.

## Notifications and taskbar previews draw over the keyboard

Full write-up: `docs/architecture/WINDOW_CHROME.md` (section of the same name). Read it before changing this area.

- A UIAccess always-on-top window is in `ZBID_UIACCESS`, above toasts and previews. **The band follows topmost-ness both ways**; don't try to "just stay in `ZBID_DESKTOP`".
- `windows_window.ShellPopupYielder` (`install_shell_popup_yield`, held alive in `main()` as it owns the ctypes callback) drops our visible always-on-top windows to `HWND_NOTOPMOST` while a toast or preview is up, **both passes keyboard first** (`keyboard_app._always_on_top_windows`).
- A toast is `Windows.UI.Core.CoreWindow` **in the notification band**; previews are `XamlExplorerHostIslandWindow`. Never match on the (localised) title.
- **The yield is held**: `HWND_NOTOPMOST` is a one-shot, so every `EVENT_SYSTEM_FOREGROUND` and poll tick re-raises our windows with `HWND_TOP` (no activation), keyboard first.
- **A picker opened mid-yield is demoted too**: the yielder tracks `_demoted`, and `_wire_floating_windows(root, on_shown=...)` feeds `ShellPopupYielder.window_shown`. Install the yielder **before** wiring the floating windows.
- **A hook that took is unhooked if a later one fails** (the shared ctypes callback; a leftover hook calls freed memory).
- Tests: `tests/test_shell_popup_yield.py`.

## Right-Click for Shifted Character

Full write-up: `docs/architecture/KEY_FEEDBACK.md` (section of the same name). Read it before changing this area.

- Right-click on a char key types its shifted variant without touching sticky shift (setting default ON; left-click unaffected). Modifier and special keys are no-ops.
- **It routes through `keyboard.pressKeyLiteral(rch)`, never `pressKey`** (which lowercases `'A'` back to `'a'`).
- **Right-click is one-shot**: the `onPressed` right-button branch returns before the auto-repeat timer starts. `Main.qml` resolves `kd.shifted`, falling back to `kd.key.toUpperCase()`.
- Long-press accents are not implemented: `docs/architecture/LONG_PRESS_ALTERNATES.md`.

## Key Preview Bubble

Full write-up: `docs/architecture/KEY_FEEDBACK.md` (section of the same name). Read it before changing this area.

- Pure visual (`root.keyPreviewEnabled`, default ON), no bridge; modifier and special keys do not preview.
- Shown on press, hidden on release. **`KeyButton.keyReleased()` must fire from all three press-ended paths**: `onReleased`, `onCanceled`, and `onContainsMouseChanged` drag-off (guarded on `_visualPressed`).
- `keyPreviewBubble` has a 110 ms visibility floor (`keyPreviewMinTimer`, `pendingHide`) and a 1500 ms safety close (`keyPreviewSafetyTimer`) for a dropped release.

