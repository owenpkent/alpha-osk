# CLAUDE.md: Alpha-OSK AI Onboarding

Alpha-OSK is an AI-assisted, mouse-driven on-screen keyboard for Windows and Linux (macOS in progress). Users click QML keys to type into whatever app currently holds OS focus; a hybrid n-gram + fuzzy engine predicts words locally (a PPM character model trains alongside but has been out of the merge since 2026-09-03), with no LLM, no GPU, and nothing leaving the machine. It is an accessibility tool the owner depends on daily. This file is both the AI-onboarding doc and the human codebase map; the detailed reference sections below are authoritative project knowledge, not background.

## About the Owner

Owen is a wheelchair user with muscular dystrophy. Typing is hard - be proactive, make decisions, don't ask for confirmation on small things. Offer A/B/C choices so he can type one letter instead of explaining. This is an accessibility tool he actually needs.

## Key rules (non-obvious, cross-cutting)

- The keyboard must NEVER steal OS focus: `WS_EX_NOACTIVATE` on Windows (`keyboard_app.py::_apply_window_flags` dispatches to `src/platform/windows_window.py::apply_extended_styles`), `WindowDoesNotAcceptFocus` elsewhere. Because our window cannot hold focus, route in-app text entry (prediction-edit popup, snippets editor, key-action editor, any future input slot) through `keyboard.beginEditSession(owner)` / `endEditSession(owner)` plus the `editKeyTyped` / `editSpecialPressed` signals, never Qt focus. `owner` is a name unique to the surface ("prediction" / "snippets" / "keyaction"); a surface's own `Connections` block only listens while `keyboard.editOwner === "<its name>"`, and it closes itself on `editOwnerChanged` when a different owner begins, rather than fight over one shared bool (see *Editing a Prediction*). Begin a session on open and end it, under your own name, on close.
- **No transparent window here rounds its own corners on Windows.** The keyboard, the Snippets and Symbols pickers and the Dashboard are all frameless and `color: "transparent"`, which makes them `WS_EX_LAYERED`, and the pixels a QML `radius` leaves outside the arc do not composite the desktop on a layered window: they come back white, as a bright notch in one corner. `Main.qml::selfRoundedCorners` squares their backgrounds off on Windows and `windows_window.py::_prefer_dwm_rounded_corners` hands the corner to DWM instead. A new floating window with a transparent background must bind to that property and be named in `keyboard_app.py::_wire_floating_windows`. See *Who rounds the window corners*.
- Sticky-modifier auto-release lives in one place, `KeyboardBridge._release_sticky_modifiers(names=_MODIFIERS, *, keep=())`: every keystroke path (`_press_char`'s edit intercept, chord branch and char-path end; `_release_edit_chord_modifiers`; `pressSpecialKey`) calls it instead of hand-copying the block. `names` restricts which modifiers a call considers (the edit intercept passes only `("shift",)`); `keep` exempts specific active modifiers from an otherwise-eligible release (`pressSpecialKey` passes `keep=("shift", "ctrl")` on `_NAV_KEYS` so Shift/Ctrl survive arrow-key selection). A new keystroke path must call this rather than write its own copy.
- Linux `LinuxKeySynthesizer.hold_modifier()` MUST skip `win`/`super`: holding Super triggers a WM pointer grab that swallows every click, including clicks on the OSK itself. Do not "fix" it to hold Super. Windows still holds `VK_LWIN`.
- Pill-facing casing comes only from `KeyboardBridge._display_cased`, which mirrors every uppercase position of the typed prefix onto the pill, unconditionally (including fuzzy/autocorrect candidates). Auto-capitalisation is the "I" family (in the language profile, `language.ENGLISH.always_capitalize`, rather than in `ngram_predictor`) plus taught acronyms (see *Taught acronyms*); do NOT reintroduce the removed three-tier proper-noun auto-cap as a default. Every pill emit site must route through `_display_cased`.
- Verbatim inserts (prediction pill, snippet, glyph, token pill, dictated phrase) all open with `KeyboardBridge._begin_verbatim_insert(*, prose=True)`, which runs `_release_sticky_modifiers()`, settles a deferred auto-space (`_take_deferred_space(prose)`) and spends an armed auto-capital (`_consume_auto_cap()`), in that order, returning `(deferred_space, owes_capital)`. The two purely-literal inserts (`insertSnippet`, `insertGlyph`) go one step further through `_commit_verbatim_insert(text)`, which also sends the text inside `_without_held_modifiers()` and resets the typing-state fields both share. A modifier held at the OS level rewrites the whole string: `_make_char_scancode_events` only knows not to *add* a redundant Shift wrap, it cannot cancel a standing hold, so "Hello" typed with Shift down arrives as "HELLO" and with Ctrl down every character arrives as a chord. The context manager drops the holds for the duration and restores them, which keeps a right-click lock intact; the sticky release is separate and belongs to the caller. **It must wrap the whole insert, not just the text**: two of `pressPrediction`'s branches never reach `send_text` (the compat BackSpace loop, and `replace_text`, whose Shift+Left selection is itself a chord), and those are the destructive ones. `_send_literal_text` is a one-line convenience over the same context manager. The single-character path in `_press_char` deliberately does NOT route through it (there the held Shift is what makes the keystroke uppercase).
- Prediction insertion is suffix-only (type just the unseen tail), falling back to `replace_text()` on a prefix/casing mismatch. Compatibility Mode (`_in_compat_mode`, matched on IDE/RDP exe basenames in `_COMPAT_PROCESS_NAMES`, never window class) rewires this to BackSpace+retype. `_context_buffer` / `_current_word` must always mirror the on-screen text; backspace must trim and rehydrate a mid-word tail.
- Import paths are security-critical: `PackManager.import_pack`, `data_export.import_user_data`, and `inspect_export` sanitise names, cap sizes, and use allow-list (not deny-list) extraction against zip-slip. Do NOT loosen without re-reading the regression tests (`tests/test_vocabulary_pack.py::TestImportPackSecurity`, and the slip/absolute-path/oversize/future-schema/telemetry cases in `tests/test_data_export.py`).
- Imported snippets have every `\r`/`\n` in a value flattened to a space (`data_export.py::_flatten_imported_snippet_newlines`) before the write; locally authored snippets (typed in the snippet editor) keep their newlines. `xdotool type` turns a literal newline into a real Return keypress, and an imported archive is untrusted, so the flatten applies only on import.
- Privacy/password mode must suppress learning AND `activeContextChanged` so no password characters or password-field context leak into predictions, telemetry, or the live visualization. Detection is Windows UIA COM + Win32 fallback (`src/platform/password_detect.py`) and Linux AT-SPI2.
- `pressPrediction` and `editPrediction` call `_check_password_field_sync()` before anything else, then gate `record_prediction_selected`, `learn_from_selection`, `learn_capitalization` and `set_capitalization` behind `if not self._privacy_mode`. The insertion itself (BackSpace+retype, `_send_literal_text`, suffix insert, `replace_text`) is deliberately NOT gated: the user tapped the pill, so the word must still reach the target app regardless of privacy mode. Any new pill-click or prediction-edit path must mirror both halves.
- Telemetry is OFF by default and `DEFAULT_ENDPOINT` in `src/telemetry.py` ships empty (silent no-op). `TelemetryClient` is the source of truth for the consent flag; do NOT mirror it into `appSettings`. The Data Backup archive deliberately excludes `telemetry.json`.
- Key colouring is a role -> colour table (`qml/palette.js`) that `Main.qml` hands to every keyboard surface, and `KeyButton` resolves; the default scheme hands down `null`, which every surface reads as "keep your own tint" (Monochrome is the shipped default). No hue is ever a literal: family hues are rotated off the active theme's accent in **OKLCh** (HSL swings perceived lightness), and every fill goes through the same wash that walks its strength down until the theme's `textColor` clears 4.5:1. See *Key Colours by role*.
- The grid, the nav cluster, the numpad and both separators all lay out to `Main.qml::sectionHeight`, which is the grid's own implicit height and nothing else: the panels fit it, absorbing the difference into their **key heights**, never their gaps, and their `implicitHeight` must stay derived from `keyH` rather than read off their own grid or it is a binding loop. A max over the three natural heights was tried and cannot work, because the grid cannot grow into a height its own implicit height defines. See *The three sections share one height*.
- Adding a setting requires the full 8-step wiring (see "Settings Panel Structure"): `Settings{}` savedFoo + root prop in `Main.qml`, prop + `SettingsToggle` in the correct sub-view of `UnifiedSettingsPanel.qml`, pass-through, `onSettingChanged`, optional `@Slot` on `keyboard_bridge.py`, and load in `Component.onCompleted`.
- Releases: `src/__version__.py` is the single source of version truth; publish to the separate `owenpkent/alpha-osk-releases` repo with an explicit `--repo` (the updater API URL is hard-pinned there); the installer asset name must be exactly `Alpha-OSK-Setup-{version}.exe`. The marketing site is a **third** repo, `owenpkent/alpha-osk-website`, and a release deliberately does not touch it: it reads the latest tag from the releases API at page load, so there is no version to bump there and no step to forget (see *The website*).
- The install path is computed, never read from the registry: every silent install passes an explicit `/S /D=<dir>` from `updater.py::_install_target_dir()`. NSIS requires `/D=` last on the command line and unquoted even when the path has spaces, so don't reorder or requote the installer arguments (full reasoning under *Auto-Update*).
- `run.py::ensure_admin_windows()` runs after dependency installation, not as the first statement in `main()`, so `pip install` never executes with an admin token; `--dashboard` never elevates at all. The repo tree is still user-writable, so this narrows the blast radius rather than closing it.
- Load-bearing invariants: merge-strategy default MUST stay `"rank"`; `NgramPredictor._user_total == sum(user_vocab.values())`; `NgramPredictor.bigrams[p][w] >= round(_user_bigrams[p][w])` (the merged context tables never drop below the user's share, and only that share is ever persisted, see *Context tables*); window height is content-bound (never persist or assign it); every full-size layout row must total exactly 15.5u and every compact row 13.0u, or that row is centred inside the grid and the keyboard's edges go ragged (see *Full-size rows are flush*); every `KeyButton` needs a share of the gap around it (`hitMarginH` / `hitMarginV`) or the strip between it and its neighbour is dead; every analytics metric needs both a session and an `_alltime_*` form; Windows subprocess calls need `CREATE_NO_WINDOW` when they suppress output *or* may run without a console to inherit (a git hook, a frozen GUI build).

## Stack & layout

- Python 3.10+ backend (CI runs 3.11, mypy targets 3.10), PySide6 (Qt6) + QML UI. No LLM/GPU. Key synthesis: ctypes SendInput scancode mode (Windows), `xdotool`/`ydotool` subprocess (Linux, NOT bundled), Quartz CGEvent (macOS, WIP). Dictation adds two Qt modules to the load-bearing set, `QtMultimedia` (capture) and `QtWebSockets` (transport), both already in the PySide6 wheel; see *Dictation*.
- `src/keyboard_bridge.py` (central QML<->Python bridge: keys, modifiers, context, predictions), `src/keyboard_app.py` (launcher, window flags dispatch, auto-save on exit), `src/platform/` (OS abstraction: key synthesis, window styling, password detect), `src/prediction/` (hybrid engine), `src/dictation/` (voice input), `qml/Main.qml` + `qml/components/`, `data/` (dictionaries/layouts/packs), `build/{windows,linux,macos}/`, `tests/` (pytest), `backend/cf-worker/` (Cloudflare telemetry worker).
- There is a second backend, a C++/Qt6 rewrite, and it exists **only on the `cpp-rewrite` branch** (`cpp/`; as of 2026-09-02 it is 64 commits ahead of and 27 behind `main`, last touched 2026-08-15). Nothing C++ is tracked on `main`, so every "mirrored in C++" note in this file describes that branch, not the checkout you are reading. `docs/architecture/BACKEND_PARITY.md` is the parity matrix. Its `tests/conformance/` harness diffs the two backends only when `ALPHA_OSK_CPP_BIN` names a built binary, which neither CI nor `check.py` sets, so today only the Python determinism self-check runs and parity is asserted rather than verified. **The branch is parked** (decided 2026-09-02, sequence item 7 of `docs/architecture/STRUCTURAL_REVIEW.md`): no Python change needs mirroring into it, its parity matrix is a snapshot as of its last commit, and the harness stays skipping. Reviving it means giving CI a built binary through `ALPHA_OSK_CPP_BIN`, which is the point at which "mirrored in C++" becomes a live claim again; until then treat every such note as history.

## Build, run, test

- Temporary files: use a scoped `tempfile.TemporaryDirectory` under the system temp directory for experiments and scratch models, with cleanup on success, errors and interruption. Do not create `.tmp-*` folders in the checkout. Clean up your own scratch files before finishing; never sweep unrelated folders or delete explicitly supplied model directories. Pytest removes its generated test directories after a passing run and keeps a failed test's for the post-mortem (`tmp_path_retention_policy = "failed"`); if supplying `--basetemp`, put it inside your own cleanup scope. The KSR benchmark cleans up its default model directory automatically and keeps `--model-dir`.
- Run: `python run.py` (creates venv, installs deps, launches the keyboard).
- Test: `python -m pytest` (around 2,250 tests; `python -m pytest --collect-only -q` prints the live count, so don't restate it elsewhere; also `-k fuzzy`, `-k property`, or a single file like `tests/test_keyboard_bridge.py`).
- Pre-push gate, the same checks as CI (`ruff check`, `ruff format --check`, `mypy` under **both** `--platform linux` and `--platform win32`, `pytest`): `python check.py` (~60s); `python check.py --full` adds the `--cov-fail-under=60` coverage gate (~110s, full CI parity). `python check.py --install-hook` wires it to `git push` so it runs automatically rather than by hand (`--no-verify` skips it once). CI additionally runs `osv-scanner` over the lockfiles. Formatting is gated separately from linting because `ruff check` ignores layout; fix a format failure with `ruff format src/ tests/`. The two mypy passes are both required and neither substitutes for the other: `linux` is what the runner uses (typeshed gates whole symbols on platform, so `ctypes.WinDLL` degrades to `Any` there and trips `warn_return_any`), and `win32` is the only thing that type-checks the `if sys.platform == "win32"` bodies at all, since mypy prunes them as unreachable under the other.

## Conventions

- Format/lint: `ruff check src/ tests/` + `ruff-format` (line length 100, rules E/F/W/I); types: `mypy src/`. Pre-commit runs ruff `--fix` + ruff-format.
- Conventional commits (`feat:` / `fix:` / `docs:` / `refactor:` / `chore:` / `test:`), subject under ~72 chars. Never add AI co-author trailers.
- NO em dashes anywhere (code, docs, commit messages, PR descriptions): use commas, colons, parentheses, or periods. Comment only the non-obvious "why". Tests required for behaviour changes.
- Accessibility first: any change to keystroke timing, repeat interval, or visual feedback must stay usable for slow, imprecise motor input.

## When to ask / flag in PR

- Call out in the PR description any change to the prediction engine, the build/signing pipeline, or telemetry.
- Get alignment before: changing the security-reporting flow or CoC contact (update `SECURITY.md` / `CONTRIBUTING.md` / `bug_report.yml` cross-references together); changing the data-export schema (`SCHEMA_VERSION` bump + back-compat import paths); changing the telemetry payload or consent model; loosening any import-hardening check; or disabling the OSV `fail-on-vuln` gate.
- If you cannot decide which Settings category a new toggle belongs in, that is a UX smell: push back on the requirement before adding the setting.

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
| `src/telemetry_bridge.py` | `TelemetryBridge` - QML-facing wrapper around opt-in telemetry, registered as its own context property (see *Opt-in Telemetry*) |
| `src/keyboard_app.py` | App launcher: QML engine, window flags, auto-save on exit |
| `src/platform/` | OS abstraction - `linux.py` (xdotool/ydotool), `windows.py` (SendInput), `password_detect.py`, `windows_window.py` (Win32 window styling), `macos_window.py` (pyobjc window/activation-policy styling), `x11_window.py` (X11 DOCK tuck) |
| `src/platform/__init__.py` | Platform detection, `get_config_dir()`, `get_model_dir()` |
| `src/prediction/` | Prediction engines (see below) |
| `src/glyphs.py` | Static symbol / emoji catalogue behind the Symbols & Emoji window |
| `src/dictation/` | Voice input - `config.py` (settings + API key), `audio.py` (mic capture), `providers.py` (Deepgram), `controller.py` (state machine) |
| `qml/Main.qml` | Root UI - title bar, keyboard rows, prediction bar, resize handles |
| `qml/palette.js` | The Key Colours engine: WCAG contrast, OKLab/OKLCh, and the six colour schemes. The single copy of the contrast maths (see *Key Colours by role*) |
| `qml/components/` | Reusable QML components (KeyButton, settings panels, etc.) |
| `data/` | Static data: dictionaries, training corpus, keyboard layouts, vocab packs |
| `build/` | Packaging pipelines - `build/windows/` (PyInstaller + NSIS + EV signing), `build/linux/` (PyInstaller + optional AppImage) and `build/macos/` (scaffolded). `build/launcher.py` is the shared frozen-mode entry point. |
| `tests/` | pytest suite |


## Deep-dive docs

Each section below that is marked *Full write-up* keeps only its load-bearing rules here; the reasoning, the measurements and the rejected alternatives live in the doc. Read the doc before changing that area, and put new reasoning there rather than growing this file.

| Area | Doc |
|------|-----|
| Prediction engine (context tables, corpus prior, prefix beam, fuzzy refresh, pointer bias, apostrophe, acronyms) | `docs/architecture/PREDICTION_NOTES.md` |
| Per-algorithm detail | `FUZZY_RECOGNITION.md`, `PPM.md`, `HYBRID_MERGING.md`, `NGRAM_SEEDS.md` |
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
| Window chrome (corners, title-bar menu, Move mode, snapping) | `docs/architecture/WINDOW_CHROME.md` |
| Switch scanning over UI Automation | `docs/architecture/UIA_TARGETS.md` |
| Dictation | `docs/architecture/DICTATION.md` |
| Telemetry, and the installer invitation | `docs/architecture/TELEMETRY.md` |
| Gotchas not repeated here | `docs/architecture/GOTCHAS.md` |
| Build and release | `docs/build/WINDOWS.md`, `RELEASE.md`, `AUTO_UPDATE.md`, `CI.md`, `LINUX.md`, `MACOS.md` |
| User study | `docs/research/STUDY_PROTOCOL.md`, `STUDY_CONSENT.md`, `STUDY_HARNESS.md` |

## Prediction Engine

All in `src/prediction/`. Orchestrated by `hybrid_predictor.py`:

| File | Role |
|------|------|
| `ngram_predictor.py` | Word-frequency model: unigrams, bigrams, trigrams. Learns from typing. |
| `ppm_predictor.py` | Character-level PPM (Dasher algorithm). Trained and persisted, but its word candidates are **out of the merge** since 2026-09-03; see *Fuzzy dictionary refresh, and PPM out of the merge*. |
| `fuzzy_recognizer.py` | Spatial error correction. Considers nearby keys as candidates. Single tuned default (no profiles). |
| `prefix_beam.py` | Mid-word fuzzy completion: a beam over live dictionary prefixes with substitution / omission / extra / transposition transitions and an unnormalised spatial emission. What `get_fuzzy_predictions` calls. See *Prefix beam*. |
| `hybrid_predictor.py` | Merges all predictors. Manages model save/load. Emits Qt signals. |
| `token_predictor.py` | Whole structured tokens the word model cannot hold: phone numbers, zips, house numbers, emails. Prefix-matched, no context, no fuzzy. See *Structured Tokens*. |
| `vocabulary_pack.py` | Custom vocab pack import (no built-ins ship - see *Vocabulary Packs* section) |
| `transformer_predictor.py` | Optional LLM re-ranking (disabled by default) |

Deep-dive design docs for each algorithm: `docs/architecture/FUZZY_RECOGNITION.md` (spatial model + tunable constants), `docs/architecture/PPM.md` (variable-order character model + PPMD escape), `docs/architecture/HYBRID_MERGING.md` (merge weights + validation + capitalization).

## Context tables: base and user halves

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `NgramPredictor.bigrams` / `.trigrams` are **merged views**. The user's share lives in `_user_bigrams` / `_user_trigrams` and is the **only context persisted**; the base share (curated seeds at +50, corpus at +1, packs) is rebuilt from the data files every launch and never written. Invariant: `bigrams[p][w] >= round(_user_bigrams[p][w])`.
- Every user context write goes through `_bump_user_context`. A direct write to `.bigrams` (tests, `learn(corpus=True)`, seed loaders, packs) is a base write.
- `_context_probs` trusts the user's distribution with weight `U / (U + 5 + 0.02 * B)`. With no user evidence it is the old normalised row **exactly**, so the whole existing suite is the regression guard for a fresh model.
- `_decay_user_context` acts on the user share of both orders only; seeds never decay. A lone user pair keeps scoring from the user row after the merged count rounds to zero.
- Legacy files carrying `bigrams` / `trigrams` are adopted wholesale as user history (`_adopt_user_context`).
- **`HybridPredictor.reload_from_disk` must call `_reseed_context()` after `load()`.**
- Benchmarks: `scripts/bench/ksr.py` and `scripts/bench/fuzzy.py`, always against a temporary model directory. **Quote the corpus with every keystroke-savings number**: 52.5 / 53.6 / 55.0% are on the hand-written `builtin` corpus; the held-out `aac-dev` / `aac-test` read 49.1 / 50.4%. Anything under 1.4 points is noise.

## Shipped corpus prior and faster personal learning

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- The shipped corpus's unigrams live in `NgramPredictor._corpus_unigrams` / `_corpus_total` **and nowhere else** (never in the persisted `unigrams`, or a dropped corpus word survives forever). Scorers read them through `_effective_typing_count` / `_effective_typing_total` at `_CORPUS_PRIOR_WEIGHT` (0.1).
- Membership and enumeration go through `NgramPredictor.in_vocabulary` / `vocabulary()`, which see both halves.
- Saving a prediction edit **whose spelling changed** calls `learn_from_selection(..., explicit=True)` (+5 per token, shape filter and blacklist applied at edit time). A Save that kept the spelling is one ordinary pill tap. Privacy mode and the learning freeze suppress both.
- Tests: `tests/test_corpus_prior.py`.

## Prefix beam (mid-word fuzzy completion)

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `FuzzyRecognizer.get_fuzzy_predictions` (the mid-word fuzzy source) is a beam over the dictionary's **live prefixes** (`src/prediction/prefix_beam.py`, `PrefixIndex`) with an unnormalised Gaussian emission and four transitions (substitution, omission, extra, transposition). The whole-word paths that run on space are unchanged.
- **Any test or bench of this path must inject the n-gram's counts** (`set_frequencies`); the bare wordlist has no frequencies and rankings fall to insertion order.
- Below three typed characters it returns nothing, unless the run is a dead prefix (`MIN_TYPED_DEAD_PREFIX` = 2). `HybridPredictor.predict` opts into `allow_short_prefix` when fewer than `SHORT_PREFIX_RESCUE_FLOOR` (5) valid n-gram candidates exist, **independent of the max-suggestions setting**, because pill position is muscle memory. One character never triggers it.
- Constants (`LOG_OMIT` -2.5, the 0.55 frequency weight) were set by sweep. `prefix_completion = False` exists for the benchmark's before/after only; there is deliberately no user setting.
- Tests: `tests/test_fuzzy_prefix_beam.py`, `tests/test_hybrid_predictor.py::TestTwoLettersAlwaysFillTheBar`.

## Fuzzy dictionary refresh, and PPM out of the merge

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- **The fuzzy dictionary follows the vocabulary.** Every learning path pushes changed words through `HybridPredictor._refresh_fuzzy_frequencies(words)` -> `FuzzyRecognizer.update_word` (in place, safe on the keystroke path). The events that *shrink* the vocabulary (`clear_user_data`, `reload_from_disk`, `unprefer`) go through `_rebuild_fuzzy_dictionary`. `unlearn_word` is deliberately not followed.
- **Every injection into the fuzzy dictionary goes through `HybridPredictor._fuzzy_frequency(word)`** (the n-gram's belief on the base count's scale, never the raw merged count). `PrefixBeam._protect_exact_completions` (`FREQUENCY_MAY_BUY` -1.5) keeps frequency from buying a multi-slip candidate past exact completions; a hard exact-first tier was tried and reversed (`teh` must still offer `the`).
- **PPM contributes no word candidates** (`HybridPredictor._ppm_in_merge = False`, since 2026-09-03). It still trains, loads and saves. Text describing the merge as "n-gram + PPM + fuzzy" predates this.
- SymSpell and `PrefixIndex` are packed immutable indexes (`packed_deletes.py`, `packed_prefixes.py`) with mutable overlays for personal words; base postings merge before overlay postings to keep tie order. Hybrid startup and rebuilds call `prepare_prefix_index()`. Base vocabulary is 83,386 words (`LanguageProfile.extra_vocabulary` owns the extra list; see `docs/research/VOCABULARY_MEMORY.md`). Equal n-gram scores sort lexically.
- Tests: `tests/test_fuzzy_refresh.py`, `tests/test_ngram_candidate_index.py`.

## Click position and the learned pointer bias

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `KeyButton.qml` publishes `pressDx` / `pressDy` (-0.5 to 0.5); `Main.qml` passes them on all three char paths. The bridge slots are overloaded (`@Slot(str)` and `@Slot(str, float, float)`), so existing callers mean "key centre".
- `_word_offsets` entries carry the character they were recorded under and are only handed on when they spell the word (`_offsets_spell` / `_offsets_for_word`). **Do not maintain the list at every site that rewrites `_current_word`**; a mismatch degrades to key centres by design.
- `src/prediction/pointer_model.py` learns a per **physical slot** bias (`PRIOR = 10`), owned by `NgramPredictor.pointer` (`pointer` key in `ngram_model.json`), mutated in place and never rebound. Observed only outside privacy mode.
- `SpatialEmissions.KEY_SIGMA` 0.85, `POSITION_SIGMA` 0.55, by sweep; sharper *hurt*. The measured gain is a few tenths of a point, so read the numbers before extending it.
- Tests: `tests/test_pointer_model.py`, `tests/test_click_position.py`, `tests/test_qml_click_position.py`.

## The apostrophe is optional in a typed prefix

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `ill` offers `I'll`, `im` offers `I'm`: one clause in `NgramPredictor._matches_partial`, applied **only when the user has typed no apostrophe themselves**. It belongs in the n-gram's exact match, not the fuzzy source (cheapening the beam's apostrophe omission was tried and is the wrong lever).
- `_continues_a_word` gates the re-query in `_press_char`: letters always, plus `'` when letters precede it, so typing the apostrophe does not blank the bar. **The underscore is deliberately not in the gate** (the tokenizer drops it, and a pill tap would `replace_text` the underscore away).
- Tests: `TestTheApostropheIsOptionalInATypedPrefix`, `TestASkippedApostropheStillFindsTheWord`, `TestTypingTheApostropheKeepsTheBar`.

## Short words in next-word predictions

`HybridPredictor._short_word_allowed` gates one- and two-letter words out of *next-word* predictions (the filter does not apply once the user has started typing a word, where the prefix already constrains things). It used to be a blanket `len(word) <= 2` with `"i"` as the single exception, which discarded exactly the words next-word prediction is best at: after "I want", the useful pills are "to", "it", "my", "us"; after "one", they are "of" and "or". Those are also the highest-frequency words in English, so the bar was withholding its strongest guesses and offering the fourth-best instead.

It is now an **allow-list of real short words**, not a relaxed length rule, and that distinction is load-bearing: the engine learns whatever the user types, so stray two-character fragments ("th", "ap", "sm") from a typo or an interrupted word accumulate in the model, and a bare length change would let every one of them compete for a pill. Words, not lengths. (The one thing besides that list which can satisfy the gate is a taught acronym, so `pr` can be offered after `opened a`; see *Taught acronyms*. Both merge sites go through `HybridPredictor._next_word_allowed` rather than calling `_short_word_allowed` directly.) The list is the active language profile's `short_words` (`language.ENGLISH.short_words`), reused rather than restated: it is already the project's answer to "real word or keyboard slip" (it gates the dictionary-load fragment filter), and a private copy in `hybrid_predictor` would be one more thing to keep in sync. Extend that set to extend this filter. Guarded by `tests/test_hybrid_predictor.py::TestShortWordsAreOfferedAsNextWords`, whose negative half is what stops a future "just drop the filter" from passing.

## Taught acronyms (why "PR" would never learn)

Full write-up: `docs/architecture/PREDICTION_NOTES.md` (section of the same name). Read it before changing this area.

- `NgramPredictor.is_taught_acronym(word)` is true when `capitalization[word]` carries **two or more capitals** (one is what every sentence-start fragment carries). It exempts the word from `_is_plausible_word`'s shape filter.
- **`load()` merges `capitalization` before the fragment strip**, or every learned acronym is deleted on the way back in.
- The next-word gate consults it too (`HybridPredictor._next_word_allowed`, which both merge sites call).
- `get_capitalized` returns the taught form only when the word is not in `_base_unigrams` and the form is acronym-shaped (all caps but for a plural `s`). This is not the removed Tier 3.
- Limitation: it must be taught with per-letter shift or right-click; typed under Caps Lock it teaches nothing.
- Tests: `TestTaughtAcronymsAreLearnable`, `TestTaughtAcronymsReachTheBar`.

## Auto-Capitalization & Proper Nouns

The pill-facing capitalization rule is intentionally minimal: **only the "I" family auto-capitalizes** (`"I"`, `"I'm"`, `"I'll"`, `"I'd"`, `"I've"` - the whole of `language.ENGLISH.always_capitalize`). Anything else stays in the casing the user typed. The mental model is "shift / caps lock is the cap signal, full stop" - pills do not second-guess intent.

This used to be a three-tier Gboard-style system (Tier 1 "I" family, Tier 2 sentence-start for ambiguous names like `will` / `jack` / `may`, Tier 3 ~8 000 unambiguous proper nouns from `data/proper_nouns.txt` plus user-taught forms). Tiers 2 and 3 fired on too many common English words ("the hope is that", "a rose by", "will you", "may i", and the post-period word in any sentence), so pills came back capitalised when the user had typed lowercase. The user's stance is that those auto-caps were noise, not help.

### How it works now
- `NgramPredictor.get_capitalized(word, sentence_start)` returns the `_always_capitalize` form for the "I" family, the taught form for an acronym-shaped non-word the user taught with per-letter capitals (see *Taught acronyms*, which carries the two guards that keep this from being Tier 3), otherwise returns `word` unchanged. The `sentence_start` argument is kept for API compatibility but ignored.
- `HybridPredictor._merge_predictions()` still calls `get_capitalized` on each pill (so the "I" family flows through the engine like any other word), and still computes `sentence_start = bool(ctx) and ctx[-1] in ".!?"` - the value just doesn't affect the result.
- **Pill-facing casing comes from `KeyboardBridge._display_cased`** - it mirrors *every* uppercase position from the typed prefix onto the pill. Type lowercase `monday` -> pill shows `monday`. Type `Monday` (one-shot shift on the M) -> pill shows `Monday`. Type `MON` (right-click each letter) -> pill shows `MONday`. This is the only path that produces capitals in pills, and it's driven entirely by what the user typed.

### Data still being collected (currently inert in pills)
Two paths populate `NgramPredictor.capitalization` even though `get_capitalized` no longer reads from it:
- `_load_proper_nouns()` reads `data/proper_nouns.txt` at startup.
- `learn_capitalization(word, *, allow_uppercase=False)` is called from the bridge in three situations: (a) the user types a word with non-trivial casing and completes it with space; (b) the user has any uppercase letter in their typed prefix and accepts a pill (`pressPrediction` calls `learn_capitalization(word)` on the chosen pill); (c) the user right-click -> Edits a prediction. The `allow_uppercase` guard is still meaningful: `_word_typed_under_caps_lock` flips to True whenever a char is appended while Caps Lock is on, and the bridge passes `allow_uppercase = not _word_typed_under_caps_lock` so all-caps under Caps Lock doesn't poison the table. Acronyms typed deliberately (right-clicking each letter, Caps Lock off) still land in the table.

The accumulated dict is persisted in `ngram_model.json`. Keeping the data lets a future opt-in switch (e.g. a "capitalize proper nouns" toggle) re-enable Tier 3 without re-teaching from scratch. **If you re-enable any tier, do it by editing `get_capitalized` to consult `self.capitalization` again - don't reintroduce the old three-tier behaviour as the default.**

### Adding to always-capitalize
Edit `always_capitalize` on the language profile in `src/prediction/language.py`. Keep it tight - it's the one auto-cap that will fire mid-sentence regardless of what the user typed, so anything beyond the "I" family needs to be unambiguous in *every* mid-sentence context (which proper nouns aren't, which is why Tiers 2/3 are gone).

## Where User Data Lives

Full write-up: `docs/architecture/USER_DATA.md`. Read it before changing this area.

- **Every store writes through `src/atomic_write.py`** (`atomic_write_text` / `atomic_write_json`); never a bare `open()` / `write_text`.
- **Settings**: Qt `Settings` in QML, on Windows `HKCU\Software\alpha-osk` (the **organisation** name from `setOrganizationName`). Registry keys are case-insensitive, and the installer runs the previous version's uninstaller on every upgrade and auto-update, so:
  - the uninstaller deletes that key only in `installer.nsh::customUnInstall`, inside the `IfSilent` guard, spelled from the `APP_ORG` define;
  - every invocation of a previous uninstaller goes through `upgrade_settings.nsh::RunPreviousUninstaller` (back up the tree, run, restore). **Backup and restore failures stop setup; the old uninstaller's exit code does not.**
  - the Add/Remove Programs entry lives in HKLM, and `removePreviousInstallAt` executes nothing it reads from the registry (reads only `InstallLocation`, normalises it, requires our own exes, defaults to No when silent).
  - Guards: `tests/test_windows_installer.py`, `tests/test_upgrade_settings.py`.
- **Prediction model**: `%APPDATA%/alpha-osk/models/` (Windows), `~/.config/alpha-osk/models/` (Linux); `ngram_model.json`, `ppm_model.json`. Loaders reject files over 50 MB, and the n-gram loader rejects more than 500 000 unigrams or bigram prefixes, or 100 000 capitalisation entries.
- **Packs**: `<config>/packs/`, import-only (see *Vocabulary Packs*).
- **Analytics**: `analytics.json` in the config root, 5 MB cap checked by `stat()` before opening, frequency tables capped at 5000; every scalar goes through `_as_count` / `_as_minutes`; `save()` builds its payload inside its own `try`.
- **Dictation**: `dictation.json` holds the API key (DPAPI on Windows, 0600 elsewhere, `key_protected` records which), 64 KB cap. **Excluded from the Data Backup archive**, like `telemetry.json`.
- **Diagnostic log**: `alpha-osk.log` in the config dir (`LOG_FILENAME` / `get_log_path()` in `src/platform/__init__.py`), 2 MB x 3, excluded from the archive.
  - **It must never contain typed content.** No record at INFO or above may interpolate a word, `_current_word`, `_context_buffer`, `_sentence_buffer` or a prediction list; content-bearing debug goes at DEBUG *and* behind `if not self._privacy_mode:`. The platform layer is inside the rule: `linux.py::_run` logs by `_describe` / `_error_name`, never `cmd` or `str(exc)`.
  - Uncaught tracebacks reach it only through `keyboard_app.py::_install_exception_hooks` (both `sys` and `threading` hooks, chained, idempotent); `build/launcher.py` logs its own crash.
  - *Data & Privacy -> Diagnostics* opens the **folder**, not the file (`getLogPath` / `openLogFolder` / `copyLogPath`).
  - `_purge_pre_fix_logs` deletes pre-fix logs once per fix generation (sentinels `.log-privacy-purge`, `.log-privacy-purge-2`; the second is Linux and macOS only). It runs before the handler opens the file, never raises, and a generation is never retired.

## Snippets (Quick-Insert Text)

Full write-up: `docs/architecture/SNIPPETS.md` (section of the same name). Read it before changing this area.

User-defined quick-insert text, opened from the bookmark button in the suggestion bar (`snippetsBarButton`, with a title-bar twin visible only when `suggestionsEnabled` is false). Backend `src/snippets.py`, UI `qml/components/SnippetsWindow.qml`.

- **A tile tap copies to the clipboard (`copySnippet(index) -> bool`) and does nothing else.** A False return must show `snippetProblemToast`; an empty snippet or bad index never touches the clipboard. `QGuiApplication` is imported **inside the slot**. The toasts live on the keyboard window (this window hides on the same tap) and stay `Popup.NoAutoClose`.
- `insertSnippet` has no QML caller; it is kept as the reference verbatim insert (`_commit_verbatim_insert`).
- `snippets.json`: saved synchronously and atomically on every mutation; `MAX_SNIPPETS` 50, label 40, value 2000, file 1 MB; a corrupt file falls back to seeded defaults. **Colour tags are stored as a name from `SNIPPET_COLORS`, never a hex** (`_clean_color`); `""` is the grey default. Nothing reads `SCHEMA_VERSION` on load, deliberately.
- The window is a top-level `Window` (not a `Popup`), named in `keyboard_app.py::_wire_floating_windows`; its whole surface with `Main.qml` is `required` properties plus `copied` / `problem` / `saved` signals. Position persists and is clamped to the whole virtual desktop (`root.clampedWindowPos`), not the primary screen.
- Exactly one of three views shows (grid, actions sheet, editor). The grid pages 2 x 3 with the Repeater model equal to the page size. **The header's Manage toggle is the left-click-only route to the sheet**; press-and-hold never opens it. Button dispatch lives in `tileClicked(idx, button)`. Delete always confirms; Add goes inert at the cap (`getSnippetLimit()`).
- The sheet and editor track a snippet's **identity** (label + value, not colour), not its index, because an import replaces the list underneath them.
- Edit session `"snippets"` is held **only while the editor is showing**. A `MouseArea` over an input must set `mouse.accepted = false`; Tab calls `focusOtherField()`; Shift + arrow selects through `moveCaret()`. `setSnippet` returns a bool and leaves the colour alone.
- Icons are `StrokeIcon` path data, never glyphs; every colour is theme-derived except the tag inks.
- In the Data Backup archive; **import flattens newlines** in values.
- Tests: `tests/test_qml_snippets.py`, `tests/test_data_export.py::TestSnippetNewlineFlattening`.

## Intelligent Spacing & Snippet Auto-Detection

Full write-up: `docs/architecture/TEXT_PATTERNS.md` (section of the same name). Read it before changing this area.

Both live in `src/text_patterns.py` (linear-time, length-capped, never logs: every argument is typed content).

- **`_raw_token`**, not `_current_word`, is the run before the cursor (the latter resets at `@` and every dot). Capped at 128, maintained only outside privacy mode.
- A suppressed auto-space skips three things together: the sent space, the space in `_context_buffer`, and the auto-capitalize. The `@` and path rules fire for `.` and `:` only. **A bare digit run suppresses provisionally**: the space is held in `_deferred_auto_space` and settled by the next character; it only ever *adds* a space late, never takes one back.
- `_closes_a_quotation` decides per keystroke on `"` parity; `"` must never join `_NO_SPACE_BEFORE`.
- **Auto-capitalize is not a held Shift**: `_auto_capitalize_after_punctuation` arms `_pending_auto_cap`, never `_shift_active` (which would poison every following chord). Verbatim inserts spend it (`_consume_auto_cap`); the edit-mode branch of `_press_char` ignores it; the end-of-keystroke spend is guarded on `consumed_auto_cap and not rearmed_auto_cap`; the quotes and brackets in `_CARRIES_AUTO_CAP` pass it along. Test with even-length punctuation runs.
- **Do not reintroduce the "first-token rescue" or guess-from-the-next-character rules.** A bare `example.com` gets a space after its first dot; that is a pinned known limitation.
- Snippet auto-detection (*Data & Privacy -> Privacy*, default ON): six guards; `_offered_snippet_values` is written when the user **answers** an offer, never when one is raised; `_withdraw_snippet_offer` emits `snippetOfferWithdrawn`; `acceptSnippetOffer` returns a bool QML must honour. Phone detection is the digit **grouping** (`_PHONE_GROUPINGS`, which excludes SSNs, dates, cards, IPs); an address needs a capitalised street-name word. Pair every positive test with a hostile near-miss.
- The offer toast is parked at the bottom, its buttons arm after 400 ms, it stays `Popup.NoAutoClose`, and its 8 s timeout calls `dismissSnippetOffer()`.

## Structured Tokens (numbers, phone numbers, email domains)

Full write-up: `docs/architecture/TEXT_PATTERNS.md` (section of the same name). Read it before changing this area.

`src/prediction/token_predictor.py`: a flat count-weighted store of whole tokens (phones, zips, house numbers, emails) matched by prefix. **No context model, no fuzzy matching, no capitalisation logic**, and not merged into the word ranking: the two bars are mutually exclusive.

- `KeyboardBridge._in_token_context()` reads `_raw_token`: a digit anywhere, or an `@` that is not the first character. Two characters minimum.
- **Every path that repopulates the bar routes through `_refresh_prediction_bar()`.** `_recase_visible_predictions` returns early on a token bar.
- `_token_pill_words` (a set) plus `_token_pill_typed`; `pressPrediction` dispatches on membership **and** on the pill being in `self._predictions` right now. Every pill is strictly longer than the run it continues.
- `_insert_token_pill`: suffix path only when the pill case-sensitively continues the run, otherwise select and overwrite; bypasses `_display_cased`; no trailing space after an email or phone; opens with `_begin_verbatim_insert(prose=False)`; Compatibility Mode rewires it; the `_context_buffer` update is arithmetic on `_context_buffer + _current_word`, never the buffer alone.
- A tapped pill is one sighting (`_learned_raw_token`). The pill context menu is suppressed on token pills (`isTokenPill`, `_is_live_token_pill`).
- `text_patterns.is_learnable_token`: digit runs over `_MAX_LEARNABLE_DIGITS` (8) rejected, phones admitted by `is_phone`, `_NEVER_LEARNED_GROUPINGS` re-blocks the SSN shape, plain words rejected, **Tab never learns**. Entries are re-validated on load, stripping before validating.
- Selections go through `record_token_prediction_selected`, never `record_prediction_selected`. Nothing logs token content. Stored under the `tokens` key of `ngram_model.json` (`MAX_TOKENS` 2000) and cleared by `clear_user_data()`.
- *Dashboard -> Saved Numbers & Addresses* (`getLearnedTokens` / `forgetToken`, which deliberately does not log) is the other half of the admission rule: the shape test will eventually admit a password, so a learned token must be visible and removable.
- Tests: `tests/test_token_predictor.py`, `tests/test_text_patterns.py::TestLearnableToken`, the token classes in `tests/test_keyboard_bridge.py`.

## Dictation (voice input)

Full write-up: `docs/architecture/DICTATION.md` (section of the same name). Read it before changing this area.

Mic at the left end of the suggestion bar; off by default, inert without a Deepgram key. Code in `src/dictation/`, `KeyboardBridge._insert_dictated_text`, the Dictation settings category.

- **Built entirely on Qt** (`QAudioSource` resamples to 16 kHz mono, `QWebSocket` on the event loop): zero new Python dependencies. Don't swap in `sounddevice`.
- `predBar.micReserve` must stay 0 whenever the mic is not on screen. There is a title-bar mirror (`dictationTitleBarButton`) for when suggestions are off. The busy pulse animates a `pulse` property, **never a bound `opacity`** (an animation takes ownership of what it animates).
- Insertion opens with `_begin_verbatim_insert()` and sends via `_send_literal_text`; never gated on privacy mode. It mirrors into `_context_buffer` / `_sentence_buffer` (not the deferred space again, and not in privacy mode) and does **not** teach the prediction model.
- **Privacy mode calls `cancel()`, not `stop()`**, and `cancel()` disconnects the controller's handlers before aborting the stream.
- `dictation.json` holds the API key: DPAPI-wrapped on Windows, never logged, never returned to QML in the clear, never in the websocket URL, and **never in the Data Backup archive**.
- Toggle rather than push-to-talk, two automatic stops, four states (`idle` / `connecting` / `listening` / `finishing`), **no automatic reconnect**, no transcript content in logs, custom vocabulary via `keyterm`.

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
- Extraction is allow-list, not deny-list. Only members matching the exact expected paths (`models/ngram_model.json`, `models/ppm_model.json`, `analytics.json`, `packs/<sanitised-id>/<allowed-filename>`) are written to disk. A hand-edited archive that snuck `telemetry.json` or `../../boot.ini` in past the manifest check is silently ignored at extraction time. Pack ids are re-matched against `PACK_ID_RE` on import, which also rejects reserved Windows device names (`con`, `prn`, `aux`, `nul`, `com1`-`9`, `lpt1`-`9`, case-insensitive, extension stripped) so an archive can't name a pack something unopenable on Windows; each per-pack file write is wrapped in `try`/`except OSError` so one bad entry is skipped instead of aborting the rest of the import. The pattern and the reserved-name check both come from `src/prediction/pack_ids.py`, the single place this rule is defined; `PackManager.import_pack` imports the same module, so the two callers can't drift the way they once did.
- Schema-version forward-compatibility: if the manifest's `schema_version` exceeds `SCHEMA_VERSION`, import is refused with a "upgrade Alpha-OSK first" message rather than half-applied.

Regression coverage: `tests/test_data_export.py::TestInspect::test_zip_slip_rejected`, `test_absolute_path_rejected`, `test_future_schema_rejected`, `test_oversize_entry_rejected`, plus `TestImport::test_telemetry_not_restored` (a hand-crafted archive cannot smuggle telemetry.json past the extractor), `TestBoundedCopy` (streaming caps + `BadZipFile` translation), and `TestReservedPackNames` (device-name rejection).

### Bridge slots
- `getDefaultExportDir() -> str` - Documents folder via QStandardPaths, falls back to home.
- `getSuggestedExportName() -> str` - `Alpha-OSK-Export-<YYYY-MM-DD-HHMMSS>.zip`.
- `exportUserData(dest_path) -> str` - empty string on success, error message otherwise. Calls `_predictor.save()` + `_analytics.save()` first so the export reflects the running session.
- `inspectUserExport(src_path) -> dict` - `{ok: True, files, pack_ids, app_version, exported_at, bytes, schema_version}` or `{ok: False, error}`. QML uses this to show a preview before the user commits.
- `importUserData(src_path) -> str` - empty string on success, error message otherwise. Calls `reload_from_disk` on the predictor + analytics, clears `_current_word` / `_context_buffer` / `_sentence_buffer`, emits empty predictions.

## QML <-> Python Bridge Pattern

QML calls Python via `@Slot` methods on `KeyboardBridge`; Python emits `Signal`s back. The keystroke round trip is the one diagrammed under *Architecture Overview*, and it ends with `self.predictionsChanged.emit(predictions)`, which QML picks up as a binding on the `keyboard.predictions` property rather than as a callback.

There are now **two** QML context properties registered in `keyboard_app.py`: `keyboard` (`KeyboardBridge`, everything above) and `telemetry` (`TelemetryBridge`, see *Opt-in Telemetry*) - the first feature surface pulled off the bridge per `docs/architecture/STRUCTURAL_REVIEW.md` section 3.1. A future split follows the same shape: its own `QObject` wrapper, its own context property name, registered next to `keyboard` in `keyboard_app.py` and in `tests/qml_context.py::install_context_properties` for the headless QML test fixtures.

## Caps Lock vs. Shift

Full write-up: `docs/architecture/MODIFIERS_AND_CASING.md` (section of the same name). Read it before changing this area.

- Caps Lock and Shift are **independent toggles**, surfaced separately (`capsLockActive`, `shiftActive`). Uppercase output and the `"upper"` layer follow `_shift_active OR _caps_lock_active`; the shifted *glyph* on a symbol key follows Shift only. Only the toggled key highlights.
- `toggleShift` holds Shift at the OS level (`hold_modifier`), which is what makes Shift+click and Shift+drag select in the target app. Shift auto-releases after one keypress; caps stays.
- **`_display_cased`**, three cases in priority order: (1) Caps Lock on, all upper; (2) any uppercase in the typed prefix, mirror **every** uppercase position, unconditionally (fuzzy candidates included); (3) Shift held, or an armed `_pending_auto_cap`, with nothing uppercase typed, capitalize the first letter. Every pill emit site routes through it.
- `toggleCapsLock`, `toggleShift`, `releaseShift` and `lockModifier("shift")` re-query the engine (`_recase_visible_predictions`) rather than re-casing the stored list, because `self._predictions` holds the displayed form. It no-ops on an empty bar.
- A pill tap releases sticky modifiers **before** the insert, or "Hello" arrives as "HELLO".
- **Prediction pill widths: the bar drops low-ranked pills rather than eliding any.** `predRow.computeFit(...)` in `Main.qml` returns `{words, widths}`. **`predBar.predTextInset` is the single source of truth for horizontal padding**, read by both the delegate and the fitter; never inline it at either site. Leftover space is handed back max-min fair. `predBar.clearCtxReserve` (plus `micReserve`) is subtracted and the row is positioned by explicit `x`, not `centerIn`.
- Testing that bar: `findChildren` cannot see Repeater delegates (use the `_pill_texts` helper and assert it is non-empty); never assert `contentWidth <= width` (`Text.truncated` is the only honest signal); the failure is a knife-edge, so sweep widths (`test_every_pill_has_room_for_its_own_text`).

## Editing a Prediction (OSK-friendly edit popup)

Right-click a prediction pill -> Edit opens a small popup with the word pre-filled and selected, so users can correct it (e.g. `iphone` -> `iPhone`) and save via `editPrediction(old, new)`. The popup is deliberately non-obvious in one way: OSK keystrokes must land in *our* TextField, but OSK key presses normally synthesize via `xdotool` / `SendInput` to the OS-focused app behind Alpha-OSK.

- **No modal overlay**: `predEditPopup.modal = false`. A modal popup would install an overlay that swallows MouseArea clicks on the keyboard below, so no OSK key would fire.
- **No press-outside close**: `closePolicy: Popup.CloseOnEscape` only - every OSK key click is a "press outside" and would otherwise slam the popup shut on the first keystroke. Escape and the X cancel button are the visible ways out.
- **Edit-mode intercept, with an owner**: on open the popup calls `keyboard.beginEditSession("prediction")`, on close `keyboard.endEditSession("prediction")`. While `_edit_mode_active` is set (by *any* session, not just this one), `pressKey` and `pressSpecialKey` short-circuit the synthesizer and emit `editKeyTyped(char)` / `editSpecialPressed(name)` instead. A `Connections { target: keyboard }` block inside the popup wires those to TextField ops - insert at cursor, backspace, delete, left/right/home/end cursor motion, space, return-to-accept, escape-to-cancel - but it only *listens* while `predEditPopup.opened && keyboard.editOwner === "prediction"`. Before this, all three edit surfaces (this popup, the snippets editor, the key-action editor) called one shared `setEditMode(bool)` with no notion of who had turned it on: opening the Snippets window while this popup was still up called an unconditional `setEditMode(false)`, silently cutting the popup's routing out from under it, and opening two editors the other way round handed `editKeyTyped` to both `Connections` blocks at once. `beginEditSession(owner)` / `endEditSession(owner)` fix that - `endEditSession` is a no-op unless `owner` matches the session's current holder, so a surface that has already lost a takeover can't clear the new owner's session by closing itself, and a **separate**, unconditionally-enabled `Connections` block listens for `editOwnerChanged` and closes this popup the instant another owner begins (kept separate from the ownership-gated block above because that block's own `enabled` is itself bound to `editOwner`, so folding the close-on-takeover handler into it would race the very signal it listens for). `setEditMode(bool)` still exists, mapped onto owner `"legacy"`, purely so existing Python callers/tests keep working; no QML surface calls it any more. `KeyboardBridge.beginEditSession` carries the full reasoning. Guarded by `tests/test_qml_edit_session.py` and `tests/test_keyboard_bridge.py::TestEditSessionsHaveAnOwner`.
- **Modifier handling in edit mode**: shift/caps still apply to letter case, and Shift auto-releases after one keypress the same way it does outside edit mode. **A Ctrl/Alt/Win chord acts on the field or does nothing at all; it never reaches the app behind us.** `_EDIT_CHORDS` maps Ctrl+a/c/v/x/z/y onto `editSpecialPressed("selectall"/"copy"/"paste"/"cut"/"undo"/"redo")`, which both edit surfaces handle; every other chord is swallowed. This used to be described as "ctrl/alt/win are ignored", and only half of that was true: the modifier was skipped and the **letter inserted**, so Ctrl+A in the snippets editor typed `a` and Ctrl+B typed `b`. The clipboard four are what make this worth wiring rather than merely swallowing, and the reason is the whole premise of the app: every character of a long address costs a click, so pasting one in from elsewhere is the difference between a snippet being worth making and not. The chord path calls `_release_edit_chord_modifiers()`, the chord-path entry point into the shared `_release_sticky_modifiers(("ctrl", "alt", "win"))` call (see *Sticky Modifiers*); a right-click lock still outranks it.
- **"Saved" confirmation toast**: a small green popup at the top of the window flashes "Saved" (with a checkmark) for 1.4 s after a successful save. Triggered from all three save paths (checkmark button click, Return-key in edit mode, TextField `onAccepted`). The save itself was always synchronous - `set_capitalization` updates the dict immediately and `aboutToQuit` writes it to `ngram_model.json` - but with no UI feedback the user couldn't tell it stuck without quitting and relaunching. Any new save path must also call `editSavedToast.flash()` or the user will think their edit was lost.

If you add a new input source (e.g. a voice-dictation slot, another popup with its own TextField), the pattern is: pick a name for it, call `keyboard.beginEditSession("<name>")` on open and `keyboard.endEditSession("<name>")` on close, gate your `Connections` block's `enabled` on `keyboard.editOwner === "<name>"` (combined with your own `opened`/`visible` condition), and add a **separate**, unconditionally-enabled `Connections { target: keyboard }` with an `onEditOwnerChanged(owner)` handler that closes or backs your surface out when `owner !== "" && owner !== "<name>"` - don't fold that into the ownership-gated block, since its `enabled` binding depends on the very property `editOwnerChanged` carries and there's no guarantee it disables before or after your handler runs off the same signal. Don't try to route through Qt focus - `WS_EX_NOACTIVATE` / `WindowDoesNotAcceptFocus` prevent our window from holding OS focus, so physical keyboard input and synthesized input both go to whatever app was focused before we opened.

## Removed: Swipe / Glide Typing

Swipe typing (drag across letters to type a word in one gesture) was removed; issue #39 carries the reasoning. It is not a candidate for a quiet reintroduction.

What went with it: `src/prediction/swipe_recognizer.py`, `qml/components/SwipeOverlay.qml`, `KeyboardBridge.setSwipeEnabled` / `setSwipeLayout` / `processSwipe`, the `charKeyRegistry` / `tappableKeyRegistry` pair plus `registerCharKey` / `pushSwipeLayout` in `Main.qml`, the `registerFn` plumbing in `NumberRow.qml` / `FunctionRow.qml`, `KeyButton.externalPress` / `externalRelease` / `externalHoldPress`, the settings toggle, and `docs/architecture/SWIPE_TYPING.md`. All of it is recoverable in full from the commit before the removal.

Two things worth knowing before any future gesture feature, because both were learned expensively:

- **The overlay was the cause of issue #15, not merely where it surfaced.** It covered the keyboard rows and took *every* press inside its bounds, then resolved it against a registry, so any key missing from that registry was a dead tap. That bill was paid three separate times (the main grid, the Number Row's Esc, every F-key), and every new control near the grid had to remember it. An interceptor that owns every press is the design flaw; the registry was the patch.
- **The two registries were not duplication.** `charKeyRegistry` held the recogniser's key centres, single characters only, because a "Backspace" centre is a phantom letter in every shape match; `tappableKeyRegistry` held every key for hit testing. Collapsing them into one list is what caused #15, and widening the char filter to fix the taps corrupts decoding instead. Any replacement meets the same fork.

The premise to re-examine first is the one this feature skipped: a sustained, precise drag is the single gesture a mouse-driven user with imprecise motor control cannot reliably make, which is why every other input path here is a click.

## Sticky Modifiers (Shift, Ctrl, Alt, Win)

Full write-up: `docs/architecture/MODIFIERS_AND_CASING.md` (section of the same name). Read it before changing this area.

- Modifiers are **sticky** (tap on, tap off) and held at the OS level via `hold_modifier()` / `release_modifier()`, so modifier+click works. After any key press they auto-release through the one shared `_release_sticky_modifiers` (see *Key rules*). `shutdown()` releases anything still held.
- **Linux never holds `win` / `super`** (`LinuxKeySynthesizer.hold_modifier` early-returns; a held Super makes the WM swallow every click). Super+key chords still work atomically through `send_key`. Windows still holds `VK_LWIN`. Test: `TestLinuxSuperNeverHeld`.
- `resetModifiers()` runs from `Main.qml`'s `Component.onCompleted` so a session never starts with a stuck modifier. Caps Lock is deliberately not reset.
- **Right-click locks** Shift / Ctrl / Alt / Win (`lockModifier(name)`, `_*_locked`; **locked always implies active**), exempt from auto-release through the single `_*_locked` guard in `_release_sticky_modifiers`. A left-tap clears the lock (`_clear_lock`). Caps Lock is not lockable, and the gesture is independent of "Right-Click for Shifted Character".
- The lock cue is a 3 px `lockBar` inked with `KeyButton._onFillColor`. **Never an emoji or icon-sized glyph on a keycap**: Windows renders it through Segoe UI Emoji in colour and ignores `color`. The clear-context ring is drawn from Feather's `rotate-ccw` path data through `ctx.path` on a Canvas (no `QtQuick.Shapes` / `QtSvg`), centred by ink (`inkOffsetX`); read `TestTheClearButtonIcon` before adding an assertion there.
- **Synth invariant: `WindowsKeySynthesizer.send_key` and `replace_text` skip wrapping any modifier that is already physically held** (`_modifier_already_held`), or the trailing key-up drops a lock after one keystroke. Any new synth path that wraps a modifier needs the same guard.
- Tests: `TestModifierLock`, `TestWindowsSendKeyPunctuationChord`, `TestWindowsReplaceText`.

## Function Keys F13-F24 and Programmable Keys

Full write-up: `docs/architecture/FUNCTION_KEYS.md` (section of the same name). Read it before changing this area.

- F13-F24 are real keys (`VK_F13`-`VK_F24`, X11 keysyms). **macOS stops at F20**; never invent a keycode. Their panel toggle is independent of F1-F12 and the row renders above it.
- **`src/key_actions.py` owns the action vocabulary; the bridge switches on nothing.** `KeyActionStore.execute` dispatches through a registry of `KeyActionType` records against a two-method `ActionExecutor`; `execute` returning a bool ("did I handle this tap") is the whole interface. Types: `key` (label only, returns False), `hotkey`, `text`.
- The dispatch sits inside `pressSpecialKey` **at the point the keystroke would be sent, not as an early return**, so sticky auto-release still runs. A chord merges the user's held modifiers (`_send_key(..., extra_modifiers=...)`). A text action *is* `_commit_verbatim_insert`. The name map is class-level (`_SPECIAL_KEY_NAMES`).
- `key_actions.json`: atomic save per mutation, 256 KB cap, bad entries dropped individually, **allow-list sanitisation** (modifiers from `MODIFIERS`, action key from `CHORD_SPECIAL_KEYS` or one printable ASCII char; a hotkey with no action key is refused). `setKeyAction` returns "did my save stick", so re-saving an unchanged action is success. **Not in the Data Backup archive.**
- Editor (`KeyActionEditor.qml`): a `Popup` with `modal: false` and `closePolicy: Popup.CloseOnEscape` only, edit session `"keyaction"`, parked at the top, with the same three text-box behaviours the snippets editor supplies. Chord capture is a mode.
- **The only route into the editor is *Settings -> Function Keys*.** Right-click on an F-key does nothing (removed 2026-09-13; don't add it back), and there is no Edit toggle on the row. Tapping a row hides the settings window and `root.settingsReturnView` brings it back on the same page.
- Geometry: the twelve keys fill the grid width (`FunctionRow._fillKeyW`) and the group gap is a fixed `keySpacing * 4`. Don't change it without rendering it next to the number row. Both function rows need `hitMarginH` / `hitMarginV` bindings.
- Test traps: the editor is a `Popup`, so search with `findChild(QObject, ...)`; a QML `var` arrives as a `QJSValue`, call `.toVariant()`; `tests/conftest.py` patches `src.key_actions.get_config_dir` by name.

## Settings Panel Structure

`UnifiedSettingsPanel.qml` is a drill-down menu, not a long scrolling list. The home view shows six category cards; clicking a card swaps the body to that category's sub-view. The header swaps in a back arrow (<) and the category title; the close X stays put.

State is held in a single string property: `currentView` is one of {`"home"`, `"appearance"`, `"typing"`, `"fkeys"`, `"dictation"`, `"model"`, `"data"`}. The Flickable contains seven sibling `ColumnLayout`s, each with `visible: unifiedSettings.currentView === "<id>"`; only one renders at a time. Scroll position is reset to the top on every view change (a `Connections` block on `currentView`) so a drilled-in view never opens mid-section.

The parent (`Main.qml`'s settings popup window) calls `settingsPanel.resetToHome()` in `onVisibleChanged` so re-opening Settings always lands on the home grid, not whatever sub-page the user last visited. Don't break that - landing on a deep page reads as "the menu changed."

**Where it opens is `root.safePanelPos(w, h)`, shared with Help and the Dashboard.** All three used to open at `Screen.width / 2 - width / 2`, which gets three things wrong at once: it centres on the **primary** screen whatever screen the keyboard is on (a monitor to the left has negative coordinates a primary-screen centre cannot even reach, the same bug the snippets restore documents one window over); it can land on top of the keyboard, which is what the user types into these windows with; and none of the three has an OS title bar to drag it back by, while Settings cannot take focus either, so a window that opens somewhere unreachable stays unreachable. `safePanelPos` puts the panel above the keyboard where there is room, below it where there is not, centred on the keyboard's own screen when it fits neither, and clamped on that screen in every case; `screenBoundsAt(px, py)` is the screen lookup, walking `Qt.application.screens` because `Screen` inside a Window is not knowable before the window is placed and `Screen.width` is a size rather than a position. Guarded by `tests/test_qml_panel_placement.py`, which cannot exercise the multi-monitor half (the offscreen plugin gives one screen) and so pins the half a single screen can prove: that the position is derived from the keyboard's geometry rather than the screen's centre, which is exactly the property the old code lacked.

**The one exception is `root.settingsReturnView`, and it is a return rather than a re-open.** Tapping a key in *Function Keys* hides the settings window and opens the key editor, which lives on the **keyboard** window because it is typed into with the OSK's own keys and the settings window cannot hold OS focus (the Deepgram key field carries the same note). Leaving a 360x540 window parked mid-screen would cover the editor, the letter grid it is typed with, or both. `settingsWindow.onVisibleChanged` consumes `settingsReturnView` when it is set and calls `resetToHome()` otherwise, so only that hand-off lands deep; coming back to the home grid there would lose the user's place in a list of twenty-four. Guarded by `tests/test_qml_function_row.py::TestTheSettingsListIsTheLeftClickRoute`, whose inverse half asserts an editor opened any other way does **not** pop the settings window open behind it.

### Where each section lives

| Top-level | Section | What's inside |
|-----------|---------|---------------|
| **Appearance** | Panels | Compact View / Navigation / Numpad toggles. The two function-row toggles are deliberately **not** here: they moved to the Function Keys category, which owns the whole feature (showing a row and deciding what is on it are one job). Compact View leads the section because it gates the two below it: it forces Navigation + Numpad off (restoring them on exit) and renders their toggles disabled. There is no Number Row toggle - `Main.qml::showNumberRow` derives from whether the active layout JSON already carries a `number` row, so the standalone panel appears exactly on the compact layouts, which lack one. |
| | Keyboard Layout | qwerty / dvorak / colemak picker (compact variants are filtered out - see *Compact View*) |
| | Theme | 9-theme color picker |
| | Key Colours | Six-scheme picker, **Monochrome by default** (Default / Monochrome / Two-Tone / Function / Ink / Signal). Directly under Theme because every colour it offers is derived from the theme |
| | Sound & Opacity | Key click sound, opacity slider |
| **Smart Typing** | Suggestions | Show suggestions, auto-space, intelligent spacing, auto-cap, max count, filter explicit words |
| | Suggestion Engine | Merge strategy 4-card picker (rank / rrf / linear / loglinear) |
| | Input | Right-click shift, key preview popup, Compatibility Mode picker, repeat delay & interval |
| **Function Keys** | Show | Function Keys (F1-F12) and Extra Function Keys (F13-F24) row toggles, moved here from Appearance -> Panels |
| | F13-F24 | One row per key: the label on its cap, a one-line description of what tapping it does, and a tap to program it. Listed **before** F1-F12 because these are the keys the feature is for, and scrolling past twelve keys nobody should reassign on every visit is the wrong default |
| | F1-F12 | The same list for the standard keys, under a note that reassigning F5 costs the user refresh in every app |
| **Dictation** | Voice Input | Enable Dictation, Type As You Speak |
| | Transcription Service | Deepgram API key, model, language |
| | Microphone | Input device picker |
| | Stop Listening | Silence timeout, maximum run length |
| | Custom Words | Boosted vocabulary (Deepgram `keyterm`) |
| **Your Language Model** | (top button) | Open Dashboard -> opens ModelVisualization |
| | Vocabulary Packs | Toggles for any imported packs + Import Custom Pack (no built-ins ship) |
| | Prediction Model | Auto-save toggle, Save Now, Clear Learned Data |
| **Data & Privacy** | (top button) | Help & Shortcuts |
| | Privacy | Snippet auto-detection opt-out, telemetry opt-in + Delete contributed data |
| | Updates | Installed version, auto-check toggle, Check Now |
| | Developer | Debug Mode |

Old labels and their new homes (for backwards-compat references in code comments / docs you might see): the standalone "Layout" section was renamed to "Panels" (the parent category is "Appearance", reusing the name was confusing); the standalone "Appearance" section was renamed to "Sound & Opacity" for the same reason; the old "Tools" section was split - its **Help & Shortcuts** button is now a standalone tile at the top of Data & Privacy, and its **Your Language Model** button moved to be the top-of-page tile in the Your Language Model view.

### Adding a New Setting

**Step 0: does it hold a secret or a device identity?** If so it does not belong in `Settings {}` at all. Qt's settings layer is a registry key on Windows, it is not covered by the size caps and validation every other loader here has, and it is the wrong place for a credential. Dictation is the worked example: only `savedDictationEnabled` lives in `appSettings`, because QML has to decide whether to reserve the suggestion bar's left edge before it has asked the bridge anything, and everything else (key, model, language, device, timeouts, custom words) lives in `dictation.json` behind `DictationConfig`, read back through `Main.qml`'s `refreshDictation()`. Splitting a record across two stores is how the halves drift, so split only on that boundary and say why. The eight steps below are for an ordinary setting.

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

A component whose root is a `Window` (a floating popup rather than an in-line
panel) must still be instantiated as a child of the keyboard window's own
tree, or `transientParent()` cannot resolve back to it. `SnippetsWindow.qml`
and `SymbolsWindow.qml` are the examples: both are declared once, as
`Comp.SnippetsWindow { ... }` / `Comp.SymbolsWindow { ... }`, inside
`background` in `Main.qml`.

## Fuzzy Recognition Defaults

Hardcoded in `src/prediction/fuzzy_recognizer.py` as `DEFAULT_*` / `_*_PROB` constants. There used to be six named "accessibility profiles" but they were confusing - the profile UI is gone and there's now one generous, Gboard-leaning default. Knobs:
- **`spatial_uncertainty` (1.4)**: how far off-center a press still counts as the intended key, in key-widths. It governs the whole-word paths only (`generate_candidates`, `get_correction`, `should_autocorrect`); the mid-word prefix beam scores with its own emission width, `SpatialEmissions.sigma`, which was set by sweep and deliberately does not follow this knob (see *Prefix beam*).
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

### Autouse guards in `tests/conftest.py`

Two fixtures apply to every test, both stating a property that has to hold
for tests nobody has written yet rather than being patched in case by case:

- **`_unplug_the_live_desktop`** stubs `is_password_field()` and
  `external_click_detected()`. `KeyboardBridge` is constructed for real, and
  both read the developer's actual desktop, so a password field on screen
  flipped the bridge into privacy mode mid-test.
- **`_no_real_update_relauncher`** stubs `updater._spawn_relauncher`. Several
  `download_and_install` tests stub only `_launch_installer` and reached the
  real spawn, which launches a *detached* process by design; it outlives the
  pytest worker and never exits, so every run of `tests/test_updater.py`
  stranded four of them, each holding a console window.

Both fail the same way when absent: something outside the test survives it.
A test wanting the real behaviour patches the same name and wins, since its
own monkeypatch applies after the fixture's.

Determinism is load-bearing: the `alpha-osk` profile in `tests/conftest.py`
sets `database=None` and `derandomize=True`, so these cannot pass locally
and fail on CI from a stale `.hypothesis` corpus. Use
`--hypothesis-profile=alpha-osk-fast` (25 examples) while iterating. **Don't
add `assume()` to filter a generated value down to a narrow case**: it
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

`python check.py` runs the same gates CI does (lint, format, mypy under both
platforms, pytest) in ~60 s; `--full` adds the `--cov-fail-under=60` coverage
gate (~110 s, full CI parity). `python check.py --install-hook` wires it to
`git push` so it happens instead of being remembered; `git push --no-verify`
is the escape hatch. Hooks are not version controlled, so a fresh clone runs
that once.

The suite is sharded on two different axes: `pytest-xdist` (`-n auto`) across
one machine's cores, and `--shard-id` / `--shard-count` across CI machines
(the matrix is `os x shard[0..3]`, eight jobs, each still `-n auto`). That is
what makes the gate a minute rather than twenty-five, and CI fourteen minutes
rather than twenty-six. Measurements, the per-choice reasoning, and the bugs
each one fixed: `docs/build/CI.md`. The rules that outlive the reasoning:

- **Any test touching QSettings, the registry, a fixed temp path, or any other
  machine-global resource has to key it per xdist worker**, through
  `tests/qt_settings_scope.py` (suffixed with `PYTEST_XDIST_WORKER`). Workers
  sharing one scope call `.clear()` on each other mid-test, and it surfaces as
  exactly the persistence bug those tests exist to catch, not as an obvious
  crash.
- **The shard hash cannot be `hash()`.** CPython salts string hashing per
  process, so workers would disagree about which tests are theirs, which does
  not fail loudly: it runs some tests twice and others never. It is
  `crc32(nodeid) % count`, in `tests/conftest.py::pytest_collection_modifyitems`.
- **Dependabot patch and minor updates merge themselves once the required
  checks pass** (`.github/workflows/dependabot-auto-merge.yml`); majors wait
  for a person. Needs the repository's "Allow auto-merge" setting on. The job
  runs only when Dependabot opened the PR *and* caused the event, and never
  checks out PR code. To take a Dependabot PR over, `gh pr merge <n>
  --disable-auto` before pushing to it. A merge made with `GITHUB_TOKEN` does
  not trigger CI's push run on main, and protection does not require branches
  to be up to date, so a break from an auto-merged update surfaces on the next
  PR's checks rather than on main. **Dependabot is switched on in three
  places and needs all three**: the version schedule in
  `.github/dependabot.yml`, the repository's *Dependabot security updates*
  setting (a different trigger, "an advisory now names your pinned version",
  enabled 2026-09-20 after running without it), and *Allow auto-merge*. A
  security update is an ordinary Dependabot PR, so the auto-merge job covers
  it with no new workflow. See `docs/build/CI.md`.
- **Branch protection requires the `Tests` job, not the shards.** Required
  checks are configured by name in the repo settings, so naming shards there
  means reconfiguring protection on every change to the shard count, and a
  stale entry blocks every PR for ever on a check that can never report.
- **One approving review is required to merge, and administrators are
  exempt.** GitHub will not let an author approve their own PR, so the
  maintainer merges with `gh pr merge --admin`; the rule gates any future
  collaborator or token instead. The Dependabot workflow approves the patch
  and minor updates it queues, which needs the repository's "Allow GitHub
  Actions to create and approve pull requests" setting on. Decided
  2026-09-15 from the security audit; see `docs/build/CI.md`.
- **`--cov-fail-under` belongs to the separate `coverage` job**, not the test
  jobs: a shard measures roughly a quarter of the lines. Its artifact upload
  needs `include-hidden-files: true`, or every upload is empty and the gate
  silently stops gating.
- **There is deliberately no fast-subset tier.** The static steps cost ~5 s
  between them, so the gate is pytest, and pytest is dominated by per-test
  setup rather than test count. If the gate creeps back up, measure before
  tiering: coverage on the one gate that runs before code leaves the machine
  is the last thing to trade away.
- **The `test` and `coverage` jobs carry `timeout-minutes: 20`.** A shard
  finishes in about six minutes, so a job that reaches twenty has hung, not
  slowed; without it the default is six hours, and a flaky hang on a windows
  shard held a PR for half an hour until it was cancelled by hand. If the
  shards ever grow toward it, measure what grew before raising the number.

**A test module that constructs a Qt application must build a
`QGuiApplication` under that same scope, never a bare
`QCoreApplication`.** There is one application per *process*, it cannot
be upgraded after construction, and `-n auto` puts unrelated modules in
the same worker. `tests/test_dictation.py` shipped briefly with a fixture
doing `QCoreApplication([])` with no organisation name, and the cost was
not subtle: every headless QML test that happened to run after it in that
worker failed at *setup*, because `QQmlApplicationEngine` needs a GUI
application it could no longer get. The unnamed organisation is the worse
half, since a QML `Settings` element resolves to a process-external store
and an unnamed app points it at the **real user's** settings. That is why
the QML modules assert their organisation in the `qapp` fixture rather
than merely setting it; the assertion is what caught this, and any new
`qapp` fixture should carry it too.

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

Full write-up: `docs/architecture/CONTEXT_RESET.md` (section of the same name). Read it before changing this area.

Six signals reset the typing context, each catching what the others cannot: (1) foreground window, (2) focused element (UIA RuntimeId), (3) caret position, all polled at 4 Hz by `_check_foreground_window`; (4) a click outside our own process (`src/platform/pointer.py`, its own 50 ms poll); (5) Tab; (6) the `_NAV_KEYS`. **Stale `_current_word` / `_context_buffer` is not a bad suggestion, it is a pill tap that eats text elsewhere.**

- `None` from a token means "don't know" and leaves state untouched.
- **The outside click (4) resets mid-word; `_check_caret_moved` keeps its only-between-words guard. Don't re-unify them.** The old `_external_click_pending` deferral is deleted.
- Where a caret is published, a click is settled over `_CLICK_SETTLE_MS` (200 ms) against `_caret_before_click` (the previous tick's reading, never `_last_caret_token`); an unreadable caret resets. `_note_own_keystroke` sets both `_keystroke_since_poll` and `_keystroke_since_click_poll`, and it is set in the synth wrappers (`_send_key` / `_send_text` / `_replace_text`), not the keystroke entry points. `_reset_typing_context` clears a pending settle. A scrollbar click still resets.
- All three within-window resets pass `keep_snippet_offer=True`; an app switch and privacy mode withdraw the offer.
- **The tail of an interrupted word is never learned** (`_word_prefix_lost`, `_take_lost_prefix`). Tests for it must type the word three times.
- Own-window clicks are filtered by process id, not geometry. It polls rather than hooking, and reads **only the high bit** of `GetAsyncKeyState` (the low bit is system-wide and the read clears it for other assistive tools).
- Tab does not learn the word in progress (it is the accept-completion key). Delete and Escape are excluded from the nav-key reset. A held arrow resets once.
- `resetContext` (the clear-context ring) delegates to `_reset_typing_context`.
- Tests: `TestTabClearsContext`, `TestCursorMotionClearsContext`, `TestOutsideClickClearsContext`, `TestAnOutsideClickThatMovesNoCaretIsLeftAlone`, `TestCaretMoveClearsContext`, `TestAnInterruptedWordIsNotLearned`, `tests/test_pointer.py`.

### Key files
- `src/platform/password_detect.py` - platform-specific detection (UIA COM via ctypes), plus `focused_element_token` / `caret_position_token`
- `src/platform/pointer.py` - `external_click_detected()`, the outside-click probe behind signal (4) above (Windows only, no-op elsewhere)
- `src/keyboard_bridge.py` - `_privacy_mode` flag, `_check_password_field()` timer, `_check_password_field_sync()` per-keystroke, `setPrivacyMode()` slot, `passwordDetectionAvailable` read-only property

### Linux
Auto-detection uses AT-SPI 2 via `gi.repository.Atspi`. A daemon thread owns a GLib event loop and listens for `object:state-changed:focused`; whenever focus lands on an accessible whose state set contains `STATE_PASSWORD_TEXT`, the shared `_is_password` flag flips on. Works for GTK (`GtkEntry` with `visibility=false`), Qt (`QLineEdit` in Password echo mode), and browsers that expose accessibility metadata. Requires `gir1.2-atspi-2.0` + a working at-spi bus on the host. If `gi` fails to import or `Atspi.init()` fails, falls back to the null detector and now logs a WARNING once at startup instead of failing silently (`detection_available()` surfaces this to the UI, see *How it works* above) - users can still toggle privacy mode manually.

## Themes

Defined in `themeData` in `Main.qml`. Each theme has: `name`, `background`, `keyColor`, `keyPressed`, `textColor`, `accent`, `border`.

**9 themes**: Dark, Light, Ocean, Forest, Amethyst, Vaporwave, Blackboard, Typewriter, Spaceship.

Theme colors flow to all components: main keyboard keys, prediction pills, nav panel, numpad, title bar icons, and active key states (NumLock, Shift, etc.). `KeyButton.qml` auto-computes text contrast on active/pressed states using luminance.

Theme picker in settings shows labeled color swatches with mini key previews.

## Vocabulary

- **Base**: Google 10K wordlist (`data/google-10000-english-usa-no-swears.txt`) + 10K supplement (`data/google-20000-supplement.txt`, filtered for explicit content) + `data/english-expanded.txt`, 64,443 words from SCOWL size 60 by way of the ESDB bundle (permissively licensed, see `data/licenses/ESDB.txt`, pinned by sha256 in `data/english-expanded.manifest`). ~83K total. The SCOWL half enters at **one base count each**, so it supplies coverage without competing with conversational frequencies or reading as personal history. It is a speller's list, which is why it carries explicit content that the curated lists do not: see *Explicit content is filtered from suggestions*.
- **Packs**: No built-ins ship. The system is import-only - see *Vocabulary Packs* section. Imported packs appear as toggles in Settings -> Your Language Model -> Vocabulary Packs.
- **Numpad**: Toggles between numbers and navigation keys (Home/End/PgUp/PgDn/arrows/Ins/Del) via NumLock. Key 5 is blank in nav mode. Layout mirrors a physical numpad: rows `7 8 9 /`, `4 5 6 *`, `1 2 3 -`, `0(span 2) . +`, `Enter(span 3) NumLock`. NumLock sits at the bottom-right (active highlight uses the theme accent), Enter is the wide bottom-row key. Earlier builds put NumLock on the top row and stretched `+` / Enter as 2-row spans on the right column. The flat 5-row layout was the user's request to match a physical 10-key.

## Explicit content is filtered from suggestions, not from the vocabulary

*Settings -> Smart Typing -> Suggestions -> Filter Explicit Words*, **default
ON**. The whole design is in the distinction the title makes: the words stay
in the dictionary, stay typable character by character, and stay learnable.
The setting decides only what the prediction bar **volunteers**.

That is deliberate and was the owner's call (2026-09-16): a keyboard that
cannot swear is a dignity problem for an AAC user, so the answer is not to
remove the words but to let the user decide whether the bar offers them. The
shipped wordlist is therefore unfiltered, including slurs, and the filter is
the control over it. Do not re-litigate the content question; do keep the
filter honest.

- **`data/explicit_words.txt` is generated, not hand-edited**
  (`scripts/gen_explicit_words.py`). It is a list of **exact words**, so the
  runtime is a set lookup with no suffix logic to get wrong.
- **Matching is stem-plus-closed-suffix-set, never substring.** That rule and
  its suffix list come from `data/explicit_stems.txt`, which already
  documented it. Substring matching is the Scunthorpe problem and it flags
  `class`, `assess`, `cocktail`, `peacock`, `dictionary`, `analysis` and
  `shiitake`. A filter that visibly swallows ordinary words is one the user
  switches off and leaves off, which is the same outcome as not having it.
  `"spook"` is deliberately **not** a stem for exactly this reason: it would
  take `spooky` and `spooked` with it.
- **Nothing is filtered at generation time any more, and that is the fix for
  a bug worth remembering.** `explicit_stems.txt` (named
  `explicit_exclusions.txt` until 2026-09-16) used to be applied by
  `gen_vocabulary.py`, so the words it named were absent from the shipped
  list and no setting could bring them back. It had been written to cover
  swearing and it missed slurs, so the shipped vocabulary ended up carrying
  **slurs but no common profanity**, which is the exact inverse of what
  anyone wanted: you could not predict `fuck` at all, while the slurs were
  one keystroke from a pill. The stems now only *seed* the suggestion
  filter, the wordlist ships unfiltered, and the file was renamed because a
  file called "exclusions" that excludes nothing is how the two jobs got
  confused in the first place.
- **The filter is applied in exactly one place**, `_finalize_scores`, beside
  the short-word gate, because every suggestion from every strategy passes
  through there. A second copy at another emit site is the parallel-blocks
  failure this file warns about for sticky-modifier release.
- **Personal vocabulary outranks the filter.** A flagged word in
  `user_vocab` is offered normally, because at that point the keyboard has
  direct evidence of the user's own register. **One typing is enough**, not
  three: the three-sighting candidate gate applies to words the model does
  not already know, and these are all in the shipped dictionary, so `learn`
  takes the known-word branch. Worth knowing because the neighbouring gate
  makes three the number a reader expects.
- **It fails open.** A missing or unreadable list leaves the filter inert
  rather than stopping construction, the same trade `_load_extra_vocabulary`
  makes, and `explicit_filter_available` is what lets the UI say so rather
  than showing a toggle that governs nothing.

Guarded by `tests/test_explicit_filter.py`, where every case is paired with
its inverse: the suppression test is paired with turning the filter off (a
filter that suggested nothing at all would satisfy the first alone), and the
flag list is checked against the shipped **no-swears** frequency list as
ground truth, so a false positive is caught by construction rather than by
anyone's judgement.

## Vocabulary Packs

Import-only. **No built-in packs ship.** Earlier releases shipped six (medical / programming / academic / gaming / business / nsfw) but each was 200-400 words - too thin to compete with personal learning, which bumps a word's score by +5 every time the user accepts it as a pill. After typing "physical therapy" three times, the user's own model already knows it, and the seed list saves nothing. Sourcing a real domain vocabulary (SNOMED-grade for medical, full API surface for programming) is its own project and runs into licensing rabbit holes; curated 300-word lists were strictly worse than no shipped packs at all. They were also drifting in maintenance (NSFW had a different `pack.json` schema and no n-grams) and there was an open correctness bug (see *Known limitations* below).

### What the system still does
- `src/prediction/vocabulary_pack.py` (`VocabularyPack`, `PackManager`) discovers packs from `data/packs/` (now absent) and from the user dir (`%APPDATA%/alpha-osk/packs/` Windows, `~/.config/alpha-osk/packs/` Linux). The user dir is created on first launch.
- Pack format: a folder containing `dictionary.txt` (required, one word per line, `#` comments allowed), optional `bigrams.txt` (whitespace-separated word pairs), `trigrams.txt` (word triples), and `pack.json` (`{name, description, version}` - generated automatically if missing on import).
- Settings -> Your Language Model -> Vocabulary Packs shows one toggle per imported pack (driven by `keyboard.getAvailablePacks()` returning the rich `{id, name, description, version, words, bigrams, trigrams}` list - the `id` field is the directory name and `VocabularyPack.get_info()` includes it explicitly so the QML side can call enable/disable). A pack's `name`/`description` are attacker-controlled strings read from an imported `pack.json`, so they pass through `_clean_meta_text` on load: collapsed to a single line, bounded to `_MAX_PACK_META_FIELD_LEN` (200), and replaced by the directory name / empty string if they are not strings at all. That's for the *log*, which this module writes the pack name into on every cap trip and which users attach to bug reports, so an embedded newline would let a pack name forge whole log lines. Separately, the `Text` elements that render them set `textFormat: Text.PlainText` so an `<img>` tag in a pack name can't make Qt fire an outbound request just from being displayed (see *Things to Watch Out For*).
- Empty state: just the "Import Custom Pack..." button + a one-line note about the format. The hardcoded `[{id: "medical", label: "Medical"}, ...]` Repeater that drove the old UI is gone - adding a new pack only requires importing it (or, in a future release, dropping a folder under `data/packs/`); no QML edit needed.
- Import hardening (security-critical, **don't loosen**): folder name sanitised to `[a-z0-9_-]{1,64}` and rejected outright if it collides with a Windows reserved device name (`con`, `prn`, `aux`, `nul`, `com1`-`9`, `lpt1`-`9`, checked case-insensitively on the base name before any extension). Those names pass the id regex but would fail `mkdir` on Windows. Resolved destination verified to sit strictly under `user_packs_dir` before any `rmtree`/`copytree`, symlinks inside the source tree are skipped rather than dereferenced. Built-in packs (if any) cannot be overwritten via import. The id rule itself (the pattern plus the reserved-name check) lives once in `src/prediction/pack_ids.py`; both this loader and `src/data_export.py` import it, so the two can't drift the way they once did. See `tests/test_vocabulary_pack.py::TestImportPackSecurity` for the regression coverage.
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

`top_pick_count` is still computed and persisted, incremented inside `record_prediction_selected` only when `rank == 1`, and surfaced as `alltimeTopPickRate` for the Model Visualization Dashboard. It was briefly the subtext on the Predictions Used tile but reads "0%" for any user upgrading from a prior build (the counter didn't exist then), which masked real usage. The bridge already passes a 1-based rank in `pressPrediction`, so a new prediction surface needs no caller-side change: it just has to call `record_prediction_selected` with the right rank.

## Prediction & Autocorrect - Architecture Notes

Full notes in **`docs/architecture/PREDICTION_NOTES.md`** (the "unified system" framing, fragment filter + repetition gate, autocorrect thresholds, reinforcement-on-click, backspace-as-negative-signal, the prioritized future-work gaps, and reference implementations). Per-algorithm deep dives: `FUZZY_RECOGNITION.md`, `PPM.md`, `HYBRID_MERGING.md`.

Load-bearing defaults to keep in mind: **space-time autocorrect is OFF by default** (`KeyboardBridge._autocorrect_enabled = False` - corrections surface as pills, never silent overwrites); the autocorrect gate skips typings under 3 chars and runs an absolute + relative threshold so deliberate typings ("thru", "lol") survive; n-gram scoring is linear interpolation in probability space (lambda = 0.5/0.3/0.2); unknown words promote into `user_vocab` only after 3 sightings (pill clicks gated the same way).

## Compact View

Full write-up: `docs/architecture/COMPACT_VIEW.md` (section of the same name). Read it before changing this area.

A denser 13x4 keyboard, off by default (*Appearance -> Panels -> Compact View*).

- **Every row in a compact layout totals the same unit count** (13.0 for `qwerty-compact`).
- **Layers are a QML-side view concept; the backend never sees them.** A `"type": "layer"` key sets `activeLayer` and must not call `keyboard.setLayout()`. Every layer switch calls the idempotent `keyboard.releaseShift()`. The symbol pages carry no Shift key (its slot is the second page), so a glyph cannot appear twice on one screen.
- `totalKeyUnits` is derived (`_widestRow`), never a constant. `resolveLayoutId()` combines `currentLayout` with `compactView`; a layout with no compact variant falls back to full size.
- **No panel that lines up with the grid may use `QtQuick.Layouts`** (it rounds children to whole pixels).
- The accent fill on the editing keys is `root.accentWashFor()`, walked down until `textColor` clears 4.5:1; never the raw accent.
- Del is on the base layer and Esc on `?123`; the Number Row panel's leading Esc is a deliberate duplicate.
- `NumberRow.qml` shows whenever `Main.qml::showNumberRow` is true, which is **derived** from the layout carrying no `number` row. It is declared **below** both function rows, so the stack reads F13-F24, F1-F12, digits, letters. The nav column reads Home / PgUp / PgDn / End.
- `tests/test_qml_compact_view.py` and `tests/test_qml_prediction_bar.py` load the real `Main.qml` headlessly and fail on QML warnings: the only guard against a binding error shipping as a blank keyboard.

## Dead space between keys

Full write-up: `docs/architecture/LAYOUT_GEOMETRY.md` (section of the same name). Read it before changing this area.

- Every `KeyButton`'s MouseArea reaches half a gap past its slot (`hitMarginH` / `hitMarginV`), so neighbours meet mid-gap and no strip is a silent miss. **Half each, never more** (an overlap resolves to the later-declared key, not the nearer one); the vertical share carries an extra half pixel on purpose.
- The press handler subtracts the margin back out before the ripple origin and `pressDx` / `pressDy`.
- **A new key or panel must be passed both margins**; the default is 0 and there is no cascade. `Main.qml` owns `keyHitMarginH` / `keyHitMarginV` / `panelHitMarginV`.
- Rejected: growing the keycaps, and one MouseArea over the whole grid (the swipe overlay's flaw). `FunctionRow`'s group gap deliberately stays dead.
- Test: `tests/test_qml_compact_view.py::TestNoDeadStripBetweenKeys`.

## Removed: the full-size symbol layer

Full write-up: `docs/architecture/LAYOUT_GEOMETRY.md` (section of the same name). Read it before changing this area.

- The full-size `Sym` page was removed on 2026-09-05 (every glyph is in the Symbols & Emoji window) and its width went to the space bar. The full-size layout files declare no layers.
- **The space bar's centre stays at 8.25u**: Win stays 1.0u and the four Ctrl / Alt keys stay equal to each other (`tests/test_layouts.py::TestTheFullSizeSpaceRow`).
- Del stays off the full-size grid and Enter's width is what puts Q over A. Compact's `?123` / `=\<` pages are **not** removable by the same argument.

## Full-size rows are flush (every row is 15.5u)

Full write-up: `docs/architecture/LAYOUT_GEOMETRY.md` (section of the same name). Read it before changing this area.

- Every full-size row totals exactly 15.5u (compact 13.0u), or `Main.qml` centres it and the edges go ragged. Widths derive from each row's middle-key budget: Tab = `\` = Caps = 1.75u, Enter = both Shifts = 2.75u, space 9.5u.
- **Equal units are not equal pixels**: each row absorbs its own gap shortfall into its keys (`rowKeyW` in the `Row` delegate), so gaps stay identical and the widest row is unchanged. Widths match exactly; origins match to within a pixel (the positioner's rounding).
- W over S now reduces to **Tab and Caps being the same width**. Del stays off the grid for that reason.
- Measure a row from first key edge to last key edge, not the `Row`'s bounding box (the Repeater adds a phantom pixel).
- Tests: `TestEveryFullSizeRowIsFlush`, `TestEveryGridRowIsPixelFlush`, `TestTheLetterColumnsLineUp`.

## The three sections share one height

Full write-up: `docs/architecture/LAYOUT_GEOMETRY.md` (section of the same name). Read it before changing this area.

- The grid, nav cluster, numpad and both separators all lay out to `Main.qml::sectionHeight`, which is **the grid's implicit height and nothing else** (a `Math.max` over the three was tried and cannot work). The panels fit it by growing their **key heights**, never their gaps.
- A panel's `implicitHeight` is computed from `keyH` (`Math.ceil(keyH)` is deliberate), never read off its own grid, or it is a binding loop.
- The nav cluster keeps its arrow gutter and the numpad has none, so their rows do not align; that was tried and reversed. Separators take `Layout.preferredHeight: root.sectionHeight`, not `fillHeight`.
- **A headless test that toggles a row must force a frame before measuring** (`_relayout`, which calls `grabWindow`); `processEvents` alone measures the old layout.
- Test: `tests/test_qml_key_colors.py::TestTheSectionsShareOneHeight`.

## Key Colours by role

Full write-up: `docs/architecture/KEY_COLOURS.md` (section of the same name). Read it before changing this area.

*Appearance -> Key Colours*: six schemes (`mono` **default**, `twotone`, `bands`, `ink`, `signal`, `off`). Engine `qml/palette.js`, wiring `Main.qml::keyRoles` / `keyRoleFor`, resolution in `KeyButton` (`_roleFill` / `_roleInk` / `_roleBar`), never at the call sites.

- **`off` hands down a null table and every surface reads null as "keep your own tint".** No per-surface `off` branch. An unknown scheme id reads as `off`.
- **No hue is a literal**: family hues are placed by farthest-point dispersion off the theme's accent in **OKLCh** (not HSL, not a tetradic harmony), lightness taken from the key colour clamped to [0.38, 0.84]. Anchored roles (`kill`, `commit`, `mod`) are pulled at most 22 degrees toward the accent. `fromOklch` reduces chroma to fit gamut rather than clamping channels.
- **Every fill goes through `washFor`**, which walks tint strength down until `textColor` clears 4.5:1. `Main.qml::accentWashFor` delegates to `palette.js` (the single copy of the WCAG maths). Dimmed ink (`_dimmedInk`) and the hover lift (`hoverFill`) are guarded too; `legibleInk` picks its pole at `INK_POLE_LUMINANCE` (0.179).
- A modifier's theme colour is its **click** colour; `mono` is neutral for every role but `toggle`.
- **A scheme colours the prediction pills' ring (`pill.bar`), never their fill**, and may not soften the ring. Only `bands` has a ring colour of its own.
- `roleForKey` reads the layout JSON `type` plus the key. The numpad's roles follow NumLock (`NumpadPanel.numRole`); `NumberRow` reads roles inline; compact's embedded nav keys are `nav`. The role stripe hides while a key is pressed, active or locked.
- Tests: `tests/test_qml_key_colors.py` (a 540-combination contrast sweep, paired with "bands stay tellable apart").

## Symbols & Emoji window

Full write-up: `docs/architecture/SYMBOLS_WINDOW.md` (section of the same name). Read it before changing this area.

The only route to a glyph outside a physical keyboard's printing, on every layout. Smile button in the suggestion bar (title-bar twin when suggestions are off). Catalogue `src/glyphs.py`, UI `qml/components/SymbolsWindow.qml`.

- **A tap types, it does not copy** (unlike Snippets): `insertGlyph(str) -> bool` calls the same `_commit_verbatim_insert` `insertSnippet` does, and is not gated on privacy mode. QML honours the bool: a refused tap flashes the problem toast and is not written to Recent.
- Recent lives in `appSettings.savedRecentGlyphs` (cap from `getRecentGlyphLimit()`), not a file; a malformed value is dropped silently. The window opens on Recent once it has content.
- Same shell as the Snippets window (flags, manual drag, desktop-wide clamp, named in `_wire_floating_windows`, whose per-window handler binds its target as a default argument). Grid pages 8 x 4 with the model equal to the page size; category tabs are a wrapping `Flow`, not a scroll strip.
- Chrome icons are `StrokeIcon`; **glyph cells name no font family** so Qt's fallback reaches the host emoji font.
- `predBar.predButtonCount` derives the suggestion bar's button reserve; new buttons go first in the right-anchored Row.
- Tests: `tests/test_qml_symbols.py`, `tests/test_glyphs.py`, `TestTypingAGlyphFromThePicker`.

## Switch-scanning targets (external scanners over UI Automation)

Every visible key and pill is published as a UI Automation `Button` so an external scanner (Switchify PC, issue #106) can enumerate and Invoke them. Normative contract: `docs/architecture/UIA_TARGETS.md`. Guard: `tests/test_qml_scan_targets.py`.

- **UIA, not an IPC server**: the app runs with `uiAccess="true"`, so a user-validated pipe would make it a confused deputy. Don't "upgrade" to a socket.
- Ids are `aosk.v1.<section>.<row>.<index>`; the format lives once in `Main.qml::scanTargetId`. Pills are `aosk.v1.pred.<index>.g<generation>`.
- **Presence means activatable, and the check runs at activation too** (`KeyButton._scanActivatable`). A `KeyButton` without a `targetId` is silently unreachable.
- `Accessible.name` is a speakable label (`_scanName`), not the keycap; the numpad names both NumLock states. Only real toggles report a toggle state (a programmed F-key is not one). The lock rides in `Accessible.description`, which is UIA `FullDescription`, not HelpText.
- Everything that can change the target set feeds `Main.qml::scanRevision` (including NumLock and `root.predictionsArePresent`), exposed as the `aosk.v1.revision` beacon, a real 1x1 visible item. Compare, never parse.
- **An Invoke carries no click position** (`pressFromPointer` false): it resolves to the key centre and teaches the pointer model nothing. Invoke is a one-shot (`activateFromAssistiveClient`), never `_activate()`, and flashes the key.
- WindowPattern is safe because restoring never takes the foreground (`windows_window.QuietRestoreFilter`), a close that is not a quit minimizes (`_KeyboardApplication` sets `quitting`), and a minimized keyboard offers no targets.
- **Stale pills**: every pill-model entry carries the generation, and `invokeScanPrediction(word, generation)` refuses a dead one. Keep both halves.
- The window is found by AutomationId `alphaOsk.alphaOskKeyboard` (`_name_for_ui_automation`).
- PySide cannot read attached `Accessible.*` properties: keep every value in a named property and **never inline an expression into the `Accessible` block**.

## Modular Layouts

Design doc at `docs/architecture/MODULAR_LAYOUTS.md`. Inspired by Octavium's (`C:\Users\Owen\dev\Octavium`) Layout/KeyDef data model. Four levels of modularity: (1) Built-in JSON layout packs (video editing, gaming, streaming). (2) User-created layouts via editor. (3) Panel composition - snap independent panels (QWERTY, numpad, macros) into a grid. (4) App-aware auto-switching based on foreground window.

Action types: `char`, `special`, `hotkey`, `text`, `macro`, `launch`, `layout`, `midi`. Profiles bundle layout + theme + window position + auto-switch rules.

## Auto-Update

Implemented in `src/updater.py`. Flow walkthrough, threat model + defences table, and the per-defence rationale all live in `docs/build/AUTO_UPDATE.md`. Release checklist is in `docs/build/WINDOWS.md`.

> **Releases live in a separate public repo** - `owenpkent/alpha-osk-releases`. The updater's API URL is hard-pinned to that repo, so `gh release create` must always pass `--repo owenpkent/alpha-osk-releases`. (Historical note: the source repo `owenpkent/alpha-osk` was private until 2026-05-16; the split was originally a private/public boundary, and is now preserved because the pinned updater URL relies on the releases repo being its own canonical source-of-truth.) **The repo moved from the `okstudio1` org to `owenpkent` on 2026-08-17, and every build through 1.2.2 is pinned to the old URL.** They keep updating on GitHub's transfer redirect (the old REST path 301s to `api.github.com/repositories/<id>/...`, which `urlopen` follows), so **a repo named `alpha-osk-releases` must never exist under `okstudio1` again**: reclaiming the name kills the redirect and strands every pre-1.2.3 install. The same redirect is why `_is_safe_download_url` must stay host-scoped and never grow a path check.

Version source of truth is `src/__version__.py`. The release-asset filename **must** match `Alpha-OSK-Setup-{version}.exe` exactly - the updater rejects anything else. User-facing toggle: *Settings -> Data & Privacy -> Updates -> "Check for updates on startup"* (persisted as `appSettings.savedAutoCheckUpdates`).

**Install path is pinned, not read from the registry.** The generated NSIS installer no longer declares `InstallDirRegKey HKCU`: nothing in the build ever wrote that key, so it was a dangling read of a user-writable registry value, and the silent `/S` auto-update path honoured it anyway. `updater.py::_install_target_dir()` computes the directory itself (the currently-running frozen exe's own parent directory, or the `%ProgramFiles%\Alpha-OSK` default when not running frozen) and every silent install launches with an explicit `/S /D=<dir>`. NSIS's `/D=` has strict syntax: it must be the last parameter on the command line and unquoted even when the path contains spaces. Don't reorder the installer arguments or add quotes.

**Signature verification also pins the version.** `_verify_signature(exe_path, expected_version)` still checks the Authenticode chain (status `Valid`, cert thumbprint, signer CN) but now also reads the downloaded exe's embedded `FileVersion` (via a PowerShell `Get-Item ... .VersionInfo.FileVersion` call, comparing only the first three components since `FileVersion` is often four-part) and requires it to equal `expected_version`. This matters because the release asset filename (`Alpha-OSK-Setup-{version}.exe`) is a selector, not a trust boundary: someone who could rename or re-upload a release asset, without being able to forge a signature, could otherwise re-attach an older, genuinely-signed installer under a newer version's name and roll every user back onto a build with known-fixed bugs. Relatedly, `_ps_single_quote_escape` fixes a real bug: a Windows username containing an apostrophe (`%TEMP%` paths embed it) broke the single-quoted PowerShell literal built from the exe path. That failed closed, so it wasn't exploitable, but it silently and permanently disabled auto-update for that user.

### Update progress UI

Full walkthrough (the four pieces from "user clicks install" to "new keyboard appears", plus the v1.0.19 file list) is in `docs/build/AUTO_UPDATE.md`. The non-obvious bits to remember: **never expose the download URL to QML** (the bridge only emits primitive ints); the pre-install toast sleeps `_PRE_INSTALL_TOAST_DWELL_S` (1.8 s) in the worker so it paints before the installer's taskkill; the relauncher splash is a `QTimer` state machine with an indeterminate `QProgressBar` (NSIS silent install has no real percentage); `_run_headless` is preserved as the test target and no-display fallback; and `_is_dev_target()` routes `python`/`pythonw` straight to headless so dev runs don't hang waiting for an exe mtime that never changes.

**`_spawn_relauncher` must pass `CREATE_NO_WINDOW` *instead of* `DETACHED_PROCESS`, not alongside it.** Windows documents the two as mutually exclusive ("CREATE_NO_WINDOW ... is ignored if it is used with either CREATE_NEW_CONSOLE or DETACHED_PROCESS"), so OR-ing them, which this did first, leaves `DETACHED_PROCESS` winning and the console suppression inert. The console has to be *suppressed* rather than absent because the flags do not propagate: in dev mode the command starts `venv\Scripts\python.exe`, that interpreter re-execs as the base interpreter, and the re-exec is a fresh `CreateProcess` carrying none of them. Under `DETACHED_PROCESS` there is no console for it to inherit so it allocates one (an empty terminal per relauncher, titled with the working directory); under `CREATE_NO_WINDOW` it inherits a console that is merely invisible. Detachment is not lost: Windows has no parent-death signal, so the child already outlives us, and `CREATE_NEW_PROCESS_GROUP` keeps it clear of the installer's taskkill. Relatedly, **no test may reach the real `_spawn_relauncher`** (an autouse guard in `tests/conftest.py` enforces it): several `download_and_install` tests stub only `_launch_installer`, and each real spawn is a detached process that outlives the pytest worker and never exits, because the helper has no branch for a parent PID that is already gone. That last part is still open, see `TODO.md`. Full write-up in `docs/build/AUTO_UPDATE.md`.

## The website (alphaosk.com)

Source in `owenpkent/alpha-osk-website`, cloned alongside this repo as `alpha-osk-website`. One
static page, no build step, no dependencies, deployed from the repo root on Netlify. It is the
same shape as the author's other site repos (`reflex-website`, `okstudio-website`): plain HTML
plus one stylesheet and one script, with `netlify.toml` carrying the security headers, cache
rules and the apex redirect.

**A release never edits it, and that is the point.** `scripts/alphaosk.js` reads the latest tag
from `api.github.com/repos/owenpkent/alpha-osk-releases/releases/latest` on page load and writes
it into the download buttons, so there is no version string in that repo to go stale. If the
request fails (rate limit, offline, a blocked origin) the buttons keep the static text the markup
already carried, which is why none of them say a version number on their own. Two consequences:
the CSP in `netlify.toml` must keep `https://api.github.com` in `connect-src`, and a release that
does not appear on the site is a releases-API question, never a site deploy question.

**Its content is derived from this repo, so this repo is the authority.** The copy comes from
`README.md` and `docs/PRIVACY.md`. When the two disagree the site is stale, and the claims most
likely to go stale are named in that repo's own README: the platform table (macOS is listed as
not yet released), the note that the telemetry endpoint is not deployed, and the test count.
Changing any of those three here means changing them there in the same sitting.

**The screenshots and icons are generated, not hand-made.** `images/screenshots/` is copied from
this repo's `assets/screenshots/`, which `scripts/capture_screenshots.py` produces against a
sandboxed config directory and a demo vocabulary, so nothing on the public site is anybody's real
typing. The favicons and the Open Graph card come from this repo's logo through that repo's
`tools/gen_assets.py` (PySide6, no new dependency), so the site's icon and the application's icon
cannot drift apart.

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

## Multilingual Input (French, Pinyin, Kanji)

### The language profile

`src/prediction/language.py` holds `LanguageProfile`, and **English is a profile like any other** (`language.ENGLISH`). Every field on it was a module-level literal in the prediction engine, written when English was the only language: the tokenizer's `[a-zA-Z']+`, the fragment filter's vowel sets, the short-word allow-list, the "I" family, and the paths to the two English wordlists. None of them were wrong; they are answers to questions with a different answer in French.

`NgramPredictor(model_path, profile)` and `HybridPredictor(..., profile=...)` take one and default to `ENGLISH`, so no caller had to change. The profile is **passed down, never read from a module-level "current language"**: a global would be one more copy to keep in sync with the layout, the model file and the settings layer, and this codebase already documents what parallel copies of one fact cost. It is a frozen dataclass because it is a description rather than state, and the engine reads it on every keystroke.

**The acceptance criterion for the refactor was that the rest of the suite passes unchanged**, which it does. That is also why `tests/test_language_profile.py` drives deliberately un-English profiles: a test that only exercised `ENGLISH` would pass just as happily against the literals, so it would not catch the engine having kept a private copy of a constant it was supposed to give up.

Two things are deliberately **not** on the profile:

- **Key positions.** Dvorak-English is a real combination, so the fuzzy spatial model belongs to the *layout*. See the section below.
- **The `text_patterns` locale data** (the NANP phone groupings, the US street-suffix matcher, which punctuation auto-spaces, the `3.14` / `1,000` number shapes). All of it is English-and-American and all of it belongs in a profile eventually, but those are free functions with about twenty call sites across the bridge and the token predictor, and **a field nothing reads is worse than a field that does not exist yet**. That slice lands with French, which is what gives it a second value to hold.


Research notes at `docs/roadmap/MULTILINGUAL_INPUT.md`. Not implemented. One finding worth knowing even if none of it ships: Unicode output already works end to end on all three platforms (`send_text` falls through to `KEYEVENTF_UNICODE` above `U+0080`, macOS uses `CGEventKeyboardSetUnicodeString`), so no key-synthesis work is needed for any language, and French needs no dead keys because we can put the precomposed `é` on a key.

### The fuzzy spatial model is layout-derived

`FuzzyRecognizer` used to fall back to its hardcoded `QWERTY_POSITIONS` unconditionally, and nothing anywhere passed it anything else, so **Dvorak and Colemak users were autocorrected against a keyboard they were not typing on**: `hwllo` resolved to `hello` on the strength of `w` neighbouring `e`, which on their layout it does not. That was a plain bug rather than a multilingual one; AZERTY is what made it worth finding.

`fuzzy_recognizer.positions_from_layout(rows)` derives the grid from the layout JSON, and `KeyboardBridge._apply_layout_key_positions` pushes it on construction and on every `setLayout`. Three things about the derivation are load-bearing:

- **Slots are assigned by a key's index within its row, not by accumulating `width` fields.** Dvorak, Colemak and AZERTY are letter remappings of the same physical board, so the physical slot is what matters, and the index rule is the only formulation that reproduces `QWERTY_POSITIONS` byte-for-byte, which is what makes moving the QWERTY user onto this path a provable no-op (`test_qwerty_json_reproduces_the_hardcoded_table`). The rendered widths would not: Tab is 1.3u here, not the ANSI 1.5u, so a geometric walk puts the home row at +0.3 rather than +0.25.
- **Punctuation is dropped from the result but still consumes a slot.** Dvorak's top row is `' , . p y f g c r l`, so `p` is the fourth key and belongs in the slot QWERTY gives `r`. Counting only the letters would put it at column 0, which is wrong in a new way rather than fixed.
- **The digit row is the one exception and is aligned by digit index**, preserving the deliberate "no horizontal stagger, `5` sits above `t`" rule `QWERTY_POSITIONS` already encoded. The leading backtick would otherwise push every digit one column right of the letter it sits above.

Rows carrying a `layer` other than the one being read are skipped, so compact's `sym` digits (which *replace* the letters rather than sitting above them) correctly contribute nothing. Known gap: the Number Row panel puts digits above the compact grid in the rendered UI, and that panel is a QML component rather than layout JSON, so those digits have no spatial position.

`FuzzyRecognizer.set_key_positions` rebuilds the whole `SpatialKeyModel` and **must re-point `word_generator.spatial_model` in the same breath** (the generator holds its own reference, so re-pointing only the recogniser leaves candidate generation on the old grid while every accessor reports the new one). An empty mapping is ignored rather than applied: blanking the grid would silently disable autocorrect instead of leaving the previous layout in place.

## Opt-in Telemetry

Design: `docs/architecture/TELEMETRY.md`. User-facing privacy: `docs/PRIVACY.md`. Backend: `backend/cf-worker/` (Cloudflare Worker + D1).

**Off by default.** When enabled (Settings -> Data & Privacy -> Privacy -> "Share anonymous usage stats"), the client sends a weekly POST containing ten fields: `anon_id`, `app_version`, `os`, `keystrokes`, `words`, `predictions`, `keystrokes_saved`, `minutes`, `sessions`, `prediction_offers`. These are exactly the lifetime counters already shown on the Analytics dashboard. **Never sent**: content, word frequencies, key frequencies, IP, hostname, or any per-session breakdown.

**Reached from QML as its own context property, not through the bridge.** `src/telemetry_bridge.py::TelemetryBridge` owns the `TelemetryClient` and the hourly submit timer, and is registered in `keyboard_app.py` as `telemetry` (alongside `keyboard`, the `KeyboardBridge`) - see *QML <-> Python Bridge Pattern*. `UnifiedSettingsPanel.qml` calls `telemetry.getEnabled()` / `telemetry.setEnabled(c)` / `telemetry.forgetData()`; `KeyboardBridge` no longer references telemetry at all. The headless QML test fixtures register both context properties through one helper, `tests/qml_context.py::install_context_properties`, rather than each hand-rolling `setContextProperty` calls.

Files, endpoint config, anon_id lifecycle, submit cadence, and the worker schema are all detailed in `docs/architecture/TELEMETRY.md`. Load-bearing facts:
- **`DEFAULT_ENDPOINT` in `src/telemetry.py` is the empty string** - while empty the client silently no-ops every submit (consent toggle still works, no data leaves the machine). Set it per-build before shipping a telemetry-enabled release; the Windows checklist (`docs/build/WINDOWS.md` step 2a) gates on this.
- **anon_id is cleared on opt-out**, so re-opt-in gets a fresh UUID4 and prior contributions can't be linked. "Delete my contributed data" POSTs to `/v1/forget`. (This is why the Data Backup archive deliberately excludes `telemetry.json`.)
- **`TelemetryClient` is the source of truth for the consent flag** - `UnifiedSettingsPanel.qml` queries `telemetry` on mount; **don't** mirror it into `appSettings`.
- **Cadence**: weekly `QTimer` (1-hour tick, 7-day window check) plus `submit_on_quit()` from `TelemetryBridge.shutdown()` (60 s anti-spam guard), called from `keyboard_app.py`'s quit sequence before `bridge.shutdown()` - the same relative order the two had when both lived inside the bridge's own `shutdown()`. All paths gated on `enabled AND endpoint AND anon_id`; failures retry `[5s, 30s, 120s]` then drop silently.
- **Privacy mode needs no special handling** - it already suppresses learning/tracking upstream, so password activity never enters the counters telemetry forwards.
- **Worker-side rate limiting** (`backend/cf-worker/src/worker.ts`): two layers, both keyed on `anon_id` and never on a request header. An edge `RATE_LIMITER` binding gates by `submit:<anon_id>`, and a `SUBMIT_COOLDOWN_SECONDS` (3600s) cooldown is enforced inside the D1 upserts themselves. The cooldown `WHERE` clause is on **both** statements in `handleSubmit` (`users` and `submissions_latest`); gating only the second left `users` taking a write per request, so the cooldown bounded half the write path. The clause gates `DO UPDATE` only, so a first-ever submission for an id still lands immediately. Every reject path, rate-limited, cooled-down, or a `/v1/forget` for an id that never existed, returns the same 204 a success would, so a response can never be used as an existence oracle for a given `anon_id`. `app_version` and `os` are validated against a semver regex and a platform enum before being written. Neither layer stops an attacker cycling through many fake `anon_id`s; that needs IP-based throttling, out of scope here.
- **Not telemetry**: auto-update version checks (GitHub Releases requests) and the planned federated-learning feature (its own opt-in + DP-noise design). Keep them conceptually separate.

## The user study

Design `docs/research/STUDY_PROTOCOL.md`, consent form `docs/research/STUDY_CONSENT.md` (both published before enrolment; their git history stands in for preregistration, so **don't edit them casually**), harness `src/study/`. Harness notes: `docs/research/STUDY_HARNESS.md`.

- The condition list is data (`session.DESIGNS`: A, B, C).
- **"Predictions off" must NOT be the `suggestionsEnabled` setting** (it collapses the bar and moves every key). The off condition keeps the bar reserved and empty.
- **Learning is frozen for the whole session** via `HybridPredictor.frozen_learning()`. `tests/test_learning_freeze.py` classifies **every** public method of `HybridPredictor` and fails on an unclassified one. Six explicit user actions are deliberately unfrozen. Privacy mode is not a substitute. `StudyBridge` holds the freeze in an `ExitStack`; `shutdown()` and `withdraw()` both thaw.
- A trial types into a `RecordingSynthesizer` swapped in at `KeyboardBridge.begin_study_capture`, **not through edit mode** (which skips prediction). The recorder keeps a caret; compat mode is forced off; action kind is inferred from what arrived.
- A pill tap is one input action (`metrics.input_actions`), so KSPC goes below 1.0. Counterbalancing is a Williams design assigned by enrolment position. Phrases are held out from the seed data (`phrases.contamination_report`).
- `src/study/config.py` resolves `get_config_dir` inside the function, never at module scope.
- Nothing is transmitted until the participant reviews and submits. The study and telemetry are separate channels with separate consent.

## Telemetry: the installer invitation, and going live

Full notes, including how to measure the installer page on a real dialog: `docs/architecture/TELEMETRY.md`.

- The installer's participation page (`build/windows/build.py`, `StudyInvitePage`) **ships its checkbox ticked (since 2026-09-08)**. That default is defensible only because the page states the purpose, shows the exact payload, and declines in one click; `TestTheCheckboxDefaultsToChecked` asserts all of them. The ePrivacy / Planet49 argument against was checked against primary sources and is recorded in the doc; don't re-derive it from memory.
- The checkbox does not enrol anyone in the typing study.
- The page shows the real payload field names (`TestThePageShowsTheMessageItSends`); `app_version` is `${APP_VERSION}`. **The 140u layout budget is nearly spent**; a control past 140u is clipped and never draws (`TestTheStudyPageFitsItsDialog`).
- To check the page for real, build a harness from the **generated** script (`_generate_nsi_script(...)`), with `RequestExecutionLevel user`, read live geometry with `EnumChildWindows`, and ship a BEFORE copy alongside. Never screen-capture.
- **The seed key is `HKLM\Software\alpha-osk-setup`, NOT `alpha-osk`** (the Qt settings organisation key). Each user's first run consumes it once through `TelemetryClient.apply_install_invite()`. A silent install never runs the page.
- **Going live is four coupled steps**: deploy the worker, set `DEFAULT_ENDPOINT`, set the `TELEMETRY_ENDPOINT` repository variable, ship a build with the invitation. Checklist in `backend/cf-worker/README.md`.

## Building & Signing a Release (Windows)

Full step-by-step release checklist, signing details, troubleshooting table, and bundle-size notes are in `docs/build/WINDOWS.md` (sections "Building a Standalone Executable", "Code Signing", "Release Checklist"). Asset/icon regeneration in `docs/build/BRANDING.md`. Quick mental model:

1. Bump `src/__version__.py` (single source of truth - `build/windows/build.py` reads from it).
2. Update `CHANGELOG.md`, commit.
3. Build + sign from a **non-elevated shell** with the eToken plugged in: `python build/windows/build.py`.
4. Test the installer in `release/`, including UIAccess against an elevated shell.
5. `git tag vX.Y.Z && git push origin main && git push origin vX.Y.Z`.
6. **Public releases repo**: `gh release create vX.Y.Z release/Alpha-OSK-Setup-X.Y.Z.exe release/Alpha-OSK-Setup-X.Y.Z-requirements.lock.txt release/Alpha-OSK-Setup-X.Y.Z-sbom.cyclonedx.json --repo owenpkent/alpha-osk-releases ...`. The `--repo` flag is mandatory because the auto-updater hard-pins the API URL to that repo (see `src/updater.py::GITHUB_API_URL`). Upload the lockfile **and** the CycloneDX SBOM as release assets alongside the installer (see *Dependency Lockfile & SBOM* below).
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
NSWindow tuning in `src/platform/macos_window.py::apply_window_flags`
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

### Cleaning up merged branches

`python scripts/clean_branches.py` deletes local branches whose pull
request has been merged; `--dry-run` lists them instead, and
`--install-hook` wires it to `git pull` so it happens on its own.

**It cannot use `git branch -d`, and that is the whole reason it
exists.** This repo squash-merges, so a merge rewrites the branch's
commits into one new commit and the branch tip is never an ancestor of
`main`: `-d` refuses every merged branch alike as "not fully merged".
What is left is `-D`, which refuses nothing and will throw away
unlanded work just as happily. So the question the script asks is not
git's "is it merged" but "does this branch have a pull request, and was
it merged", which is the one a squash-merging repo can actually answer.
A branch whose PR is open, closed unmerged, or missing is kept, and the
run says which. `git diff main <branch>` is **not** a usable check
either: main moves on after the merge, so that diff reports main's own
later commits as deletions on the branch side and every merged branch
looks like it still has work.

Three things fail closed, each because the cost of being wrong is
asymmetric (a kept branch is clutter, a deleted one is lost work): a
branch with no upstream at all is never a candidate, since never-pushed
work exists nowhere else and renders the same empty tracking field an
up-to-date branch does; `gh` being missing or unauthenticated keeps
every branch rather than deleting them all; and `main` and the
checked-out branch are refused by name.

**"Automatic" here means `git pull`, because there is no local event for
a merge.** The merge happens on GitHub and nothing on this machine is
told, so `--install-hook` hangs a `post-merge` hook off the pull that
brings the squashed commit down, and sets `fetch.prune` for the repo,
which is what makes the "upstream is gone" reading accurate at the
moment the hook runs. The hook ends in `|| true`: a branch left behind
is clutter, and clutter is not worth making `git pull` report a
failure. Hooks are not version controlled, so a fresh clone runs
`--install-hook` once, the same as `check.py --install-hook`.

Guarded by `tests/test_clean_branches.py`, where every case that could
delete something is paired with the near-miss it must keep.

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
easiest to reintroduce. Focus / window flags, sticky-modifier release,
suffix-only insertion and the buffer invariants are not repeated here: they
are in *Key rules* at the top of this file.

- **Windows uses scancode mode** for both `send_text` (ASCII) and chords/`hold_modifier` (UNICODE/`wVk`-mode only as a fallback) - required for Blender/VirtualBox/games and for Ctrl+V over TeamViewer/RDP.
- **Linux `xdotool`/`ydotool` calls that carry arbitrary typed text must precede it with a literal `--`.** Both tools' `type` subcommand parses flags with getopt, so typed text that happens to look like an option (`--help`) would otherwise be silently parsed as one and dropped instead of typed. The four call sites carrying arbitrary text do this; the other sixteen `xdotool`/`ydotool` call sites pass internally-built key names, not user text, so they don't need it. `platform/linux.py::_run()` is also bounded by a 2.0 s `_SUBPROCESS_TIMEOUT_S`: it runs synchronously on the Qt UI thread on every keystroke, so a wedged binary (dead X server, unresponsive display) used to freeze the whole keyboard; `TimeoutExpired` is now caught and logged, not left to hang.
- **Games need a held key, not a zero-gap tap.** Games read the keyboard by *polling* state once per render frame (DirectInput / Raw Input / `GetAsyncKeyState`), so a key-down+key-up injected in one `SendInput` batch can land entirely between two polls and be missed: the keystroke does nothing in-game even though it works everywhere else. Auto game-compat fixes this: when `_window_is_game(hwnd)` is true, `_game_auto_active` flips on (set in the same 250 ms foreground poll as compat auto-detect) and single keys are sent with `hold_seconds = _GAME_KEY_HOLD_SECONDS` (50 ms). `WindowsKeySynthesizer.send_key` then splits the injection into a down-batch, a real `time.sleep`, and an up-batch (modifiers wrap the held key). Non-game keystrokes keep the zero-latency atomic path. `_window_is_game` uses three signals (`keyboard_bridge.py`): (1) the owning-process exe is in `_GAME_PROCESS_NAMES` (seeded with the Age of Empires family plus `unrealeditor.exe`; extend like `_COMPAT_PROCESS_NAMES`), which catches games even in windowed mode; (2) the exe ends with one of `_GAME_EXE_SUFFIXES`, which matches a whole **engine family** rather than a title: every packaged Unreal game ships a `<Project>-Win64-Shipping.exe`, so one suffix covers any UE title, windowed included, without anyone having to add it to a list; (3) a **borderless-fullscreen heuristic** (`_window_is_borderless_fullscreen`: window rect covers the whole monitor *and* the window has no `WS_CAPTION`) as a zero-config catch-all for unlisted games. The heuristic is deliberately skipped for exes in `_COMPAT_PROCESS_NAMES` (IDEs / remote-desktop clients), which are sometimes run fullscreen and must not get the typing-lag hold. Requiring "no caption" excludes normal maximized windows (which keep their title bar); the remaining false positives (fullscreen video players, slideshows) are harmless because a 50 ms hold doesn't hurt there. **The Unreal Editor is listed deliberately, editor and all.** Play-in-editor polls input exactly like a shipped game and the editor runs windowed, so neither of the other two signals reaches it. The cost is that the editor's own text fields (asset rename, content-browser search) take the 50 ms hold too, which is latency rather than lost input, while without it viewport and PIE keys are dropped entirely. Any future entry that is an authoring tool rather than a game owes the same trade in the same direction, and it is only worth taking where the tool polls input. Coverage for both new signals is in `tests/test_keyboard_bridge.py::TestGameKeyHold` (`test_unreal_editor_windowed_is_game`, `test_unreal_shipping_suffix_is_game`), each with the fullscreen heuristic stubbed **off** so it cannot be what makes them pass. This is unrelated to UIAccess: a signed Program-Files install still hit it because the keystrokes *reach* the game, they're just too brief to be polled.
- **`pressKey` lowercases its input** - use `pressKeyLiteral` when QML already resolved the final character (right-click shifted variant, etc.).
- **QML `Text` defaults to `AutoText`, which sniffs the string for HTML and can trigger an outbound request just from being displayed.** Any `Text` rendering a value that ultimately came from imported or otherwise untrusted data (a vocabulary pack's `name`/`description`, anything read from a file the user picked) must set `textFormat: Text.PlainText` explicitly, or an `<img src=...>` planted in that string makes Qt fetch it the moment the Settings page renders. 23 `Text` elements across 7 QML files (`Main.qml`, `UnifiedSettingsPanel.qml`, `DebugPanel.qml`, `AnalyticsDashboard.qml`, `ModelVisualization.qml`, `KeyButton.qml`, `SettingsToggle.qml`) now set it explicitly; new `Text` elements displaying untrusted strings must too. **Known gap**: the attached-property `ToolTip.text` idiom has no `textFormat` to set, so a tooltip built from untrusted text is not covered.

## Who rounds the window corners

Full write-up: `docs/architecture/WINDOW_CHROME.md` (section of the same name). Read it before changing this area.

- The keyboard, Snippets, Symbols and Dashboard windows are transparent, hence `WS_EX_LAYERED` on Windows, where pixels a QML `radius` leaves unpainted come back **white**. `Main.qml::selfRoundedCorners` is false on Windows (radius 0 for background, title bar, shadow) and `windows_window.py::_prefer_dwm_rounded_corners` asks DWM for `DWMWCP_ROUND`. Turning DWM rounding off fixes nothing.
- The title bar's radius follows the background's. The floating windows take `selfRoundedCorners` as a required property and are named in `_wire_floating_windows`; the Dashboard gets only the DWM call (it may take focus, so no `WS_EX_NOACTIVATE`).
- The DWM call runs before the style writes and is best-effort (Windows 11 only); it must never fail startup.
- `scripts/capture_screenshots.py` sets `selfRoundedCorners` back to true, which is why it is not `readonly`.
- The offscreen render was correct while the bug was live, so `tests/test_window_corners.py` pins only the checkable half.

## Title-bar window menu, and click-free Move

Full write-up: `docs/architecture/WINDOW_CHROME.md` (section of the same name). Read it before changing this area.

Right-clicking the title bar opens Move / Minimize / Tuck away (X11 only) / Close. Guard: `tests/test_qml_window_menu.py`.

- **`titleBarMenuArea` is declared before every other input-taking child of `titleBar` and accepts only `Qt.RightButton`**; on top it would kill dragging.
- Rows come from `windowMenu.actions`, word-only. Close carries a rule and a gap above it.
- **Move mode** is two taps with a free hand between: the window follows by `(current - anchor)` in the overlay's coordinates (self-correcting), the anchor is dropped on `onExited`, `windowMoveOverlay` is `enabled: root.moveMode` and swallows the ending click, left puts it down and right puts it back (`_moveReturnX/Y`); there is no Escape.
- Tests drive the pointer in desktop coordinates; a closed `Popup`'s rows all report `visible: false`.

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

Left-click previews use `keyBtn.displayText` (which already reflects shift/caps casing, so it matches what `pressKey` sends); right-click previews use the resolved `rch`. The same press also publishes `keyBtn.pressDx` / `pressDy`, the click's position inside the key, which the three `pressKey*` calls pass to the bridge (see *Click position and the learned pointer bias*). Both call sites are gated on `root.keyPreviewEnabled`. Modifier and special keys do not preview (a bubble over Shift or Backspace isn't "what it typed").
