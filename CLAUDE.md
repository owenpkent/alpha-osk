# CLAUDE.md: Alpha-OSK AI Onboarding

Alpha-OSK is an AI-assisted, mouse-driven on-screen keyboard for Windows and Linux (macOS in progress). Users click QML keys to type into whatever app currently holds OS focus; a hybrid n-gram + PPM + fuzzy engine predicts words locally, with no LLM, no GPU, and nothing leaving the machine. It is an accessibility tool the owner depends on daily. This file is both the AI-onboarding doc and the human codebase map; the detailed reference sections below are authoritative project knowledge, not background.

## About the Owner

Owen is a wheelchair user with muscular dystrophy. Typing is hard - be proactive, make decisions, don't ask for confirmation on small things. Offer A/B/C choices so he can type one letter instead of explaining. This is an accessibility tool he actually needs.

## Key rules (non-obvious, cross-cutting)

- The keyboard must NEVER steal OS focus: `WS_EX_NOACTIVATE` on Windows (`keyboard_app.py::_apply_window_flags` dispatches to `src/platform/windows_window.py::apply_extended_styles`), `WindowDoesNotAcceptFocus` elsewhere. Because our window cannot hold focus, route in-app text entry (prediction-edit popup, snippets editor, any future input slot) through `setEditMode(true)` plus the `editKeyTyped` / `editSpecialPressed` signals, never Qt focus. Set edit mode on open and clear it on close.
- Sticky-modifier auto-release lives in one place, `KeyboardBridge._release_sticky_modifiers(names=_MODIFIERS, *, keep=())`: every keystroke path (`_press_char`'s edit intercept, chord branch and char-path end; `_release_edit_chord_modifiers`; `pressSpecialKey`) calls it instead of hand-copying the block. `names` restricts which modifiers a call considers (the edit intercept passes only `("shift",)`); `keep` exempts specific active modifiers from an otherwise-eligible release (`pressSpecialKey` passes `keep=("shift", "ctrl")` on `_NAV_KEYS` so Shift/Ctrl survive arrow-key selection). A new keystroke path must call this rather than write its own copy.
- Linux `LinuxKeySynthesizer.hold_modifier()` MUST skip `win`/`super`: holding Super triggers a WM pointer grab that swallows every click, including clicks on the OSK itself. Do not "fix" it to hold Super. Windows still holds `VK_LWIN`.
- Pill-facing casing comes only from `KeyboardBridge._display_cased`, which mirrors every uppercase position of the typed prefix onto the pill, unconditionally (including fuzzy/autocorrect candidates). Auto-capitalisation is ONLY the "I" family, and it lives in the language profile (`language.ENGLISH.always_capitalize`) rather than in `ngram_predictor`; do NOT reintroduce the removed three-tier proper-noun auto-cap as a default. Every pill emit site must route through `_display_cased`.
- Verbatim inserts (prediction pill, snippet, glyph, token pill, dictated phrase) all open with `KeyboardBridge._begin_verbatim_insert(*, prose=True)`, which runs `_release_sticky_modifiers()`, settles a deferred auto-space (`_take_deferred_space(prose)`) and spends an armed auto-capital (`_consume_auto_cap()`), in that order, returning `(deferred_space, owes_capital)`. The two purely-literal inserts (`insertSnippet`, `insertGlyph`) go one step further through `_commit_verbatim_insert(text)`, which also sends the text inside `_without_held_modifiers()` and resets the typing-state fields both share. A modifier held at the OS level rewrites the whole string: `_make_char_scancode_events` only knows not to *add* a redundant Shift wrap, it cannot cancel a standing hold, so "Hello" typed with Shift down arrives as "HELLO" and with Ctrl down every character arrives as a chord. The context manager drops the holds for the duration and restores them, which keeps a right-click lock intact; the sticky release is separate and belongs to the caller. **It must wrap the whole insert, not just the text**: two of `pressPrediction`'s branches never reach `send_text` (the compat BackSpace loop, and `replace_text`, whose Shift+Left selection is itself a chord), and those are the destructive ones. `_send_literal_text` is a one-line convenience over the same context manager. The single-character path in `_press_char` deliberately does NOT route through it (there the held Shift is what makes the keystroke uppercase).
- Prediction insertion is suffix-only (type just the unseen tail), falling back to `replace_text()` on a prefix/casing mismatch. Compatibility Mode (`_in_compat_mode`, matched on IDE/RDP exe basenames in `_COMPAT_PROCESS_NAMES`, never window class) rewires this to BackSpace+retype. `_context_buffer` / `_current_word` must always mirror the on-screen text; backspace must trim and rehydrate a mid-word tail.
- Import paths are security-critical: `PackManager.import_pack`, `data_export.import_user_data`, and `inspect_export` sanitise names, cap sizes, and use allow-list (not deny-list) extraction against zip-slip. Do NOT loosen without re-reading the regression tests (`tests/test_vocabulary_pack.py::TestImportPackSecurity`, and the slip/absolute-path/oversize/future-schema/telemetry cases in `tests/test_data_export.py`).
- Imported snippets have every `\r`/`\n` in a value flattened to a space (`data_export.py::_flatten_imported_snippet_newlines`) before the write; locally authored snippets (typed in the snippet editor) keep their newlines. `xdotool type` turns a literal newline into a real Return keypress, and an imported archive is untrusted, so the flatten applies only on import.
- Privacy/password mode must suppress learning AND `activeContextChanged` so no password characters or password-field context leak into predictions, telemetry, or the live visualization. Detection is Windows UIA COM + Win32 fallback (`src/platform/password_detect.py`) and Linux AT-SPI2.
- `pressPrediction` and `editPrediction` call `_check_password_field_sync()` before anything else, then gate `record_prediction_selected`, `learn_from_selection`, `learn_capitalization` and `set_capitalization` behind `if not self._privacy_mode`. The insertion itself (BackSpace+retype, `_send_literal_text`, suffix insert, `replace_text`) is deliberately NOT gated: the user tapped the pill, so the word must still reach the target app regardless of privacy mode. Any new pill-click or prediction-edit path must mirror both halves.
- Telemetry is OFF by default and `DEFAULT_ENDPOINT` in `src/telemetry.py` ships empty (silent no-op). `TelemetryClient` is the source of truth for the consent flag; do NOT mirror it into `appSettings`. The Data Backup archive deliberately excludes `telemetry.json`.
- Adding a setting requires the full 8-step wiring (see "Settings Panel Structure"): `Settings{}` savedFoo + root prop in `Main.qml`, prop + `SettingsToggle` in the correct sub-view of `UnifiedSettingsPanel.qml`, pass-through, `onSettingChanged`, optional `@Slot` on `keyboard_bridge.py`, and load in `Component.onCompleted`.
- Releases: `src/__version__.py` is the single source of version truth; publish to the separate `okstudio1/alpha-osk-releases` repo with an explicit `--repo` (the updater API URL is hard-pinned there); the installer asset name must be exactly `Alpha-OSK-Setup-{version}.exe`.
- The install path is computed, never read from the registry: every silent install passes an explicit `/S /D=<dir>` from `updater.py::_install_target_dir()`. NSIS requires `/D=` last on the command line and unquoted even when the path has spaces, so don't reorder or requote the installer arguments (full reasoning under *Auto-Update*).
- `run.py::ensure_admin_windows()` runs after dependency installation, not as the first statement in `main()`, so `pip install` never executes with an admin token; `--dashboard` never elevates at all. The repo tree is still user-writable, so this narrows the blast radius rather than closing it.
- Load-bearing invariants: merge-strategy default MUST stay `"rank"`; `NgramPredictor._user_total == sum(user_vocab.values())`; `NgramPredictor.bigrams[p][w] >= round(_user_bigrams[p][w])` (the merged context tables never drop below the user's share, and only that share is ever persisted, see *Context tables*); window height is content-bound (never persist or assign it); every analytics metric needs both a session and an `_alltime_*` form; Windows subprocess calls need `CREATE_NO_WINDOW` when they suppress output *or* may run without a console to inherit (a git hook, a frozen GUI build).

## Stack & layout

- Python 3.10+ backend (CI runs 3.11, mypy targets 3.10), PySide6 (Qt6) + QML UI. No LLM/GPU. Key synthesis: ctypes SendInput scancode mode (Windows), `xdotool`/`ydotool` subprocess (Linux, NOT bundled), Quartz CGEvent (macOS, WIP). Dictation adds two Qt modules to the load-bearing set, `QtMultimedia` (capture) and `QtWebSockets` (transport), both already in the PySide6 wheel; see *Dictation*.
- `src/keyboard_bridge.py` (central QML<->Python bridge: keys, modifiers, context, predictions), `src/keyboard_app.py` (launcher, window flags dispatch, auto-save on exit), `src/platform/` (OS abstraction: key synthesis, window styling, password detect), `src/prediction/` (hybrid engine), `src/dictation/` (voice input), `qml/Main.qml` + `qml/components/`, `data/` (dictionaries/layouts/packs), `build/{windows,linux,macos}/`, `tests/` (pytest), `backend/cf-worker/` (Cloudflare telemetry worker).
- There is a second backend, a C++/Qt6 rewrite, and it exists **only on the `cpp-rewrite` branch** (`cpp/`; as of 2026-09-02 it is 64 commits ahead of and 27 behind `main`, last touched 2026-08-15). Nothing C++ is tracked on `main`, so every "mirrored in C++" note in this file describes that branch, not the checkout you are reading. `docs/architecture/BACKEND_PARITY.md` is the parity matrix. Its `tests/conformance/` harness diffs the two backends only when `ALPHA_OSK_CPP_BIN` names a built binary, which neither CI nor `check.py` sets, so today only the Python determinism self-check runs and parity is asserted rather than verified. **The branch is parked** (decided 2026-09-02, sequence item 7 of `docs/architecture/STRUCTURAL_REVIEW.md`): no Python change needs mirroring into it, its parity matrix is a snapshot as of its last commit, and the harness stays skipping. Reviving it means giving CI a built binary through `ALPHA_OSK_CPP_BIN`, which is the point at which "mirrored in C++" becomes a live claim again; until then treat every such note as history.

## Build, run, test

- Run: `python run.py` (creates venv, installs deps, launches the keyboard).
- Test: `python -m pytest` (around 1,900 tests; `python -m pytest --collect-only -q` prints the live count, so don't restate it elsewhere; also `-k fuzzy`, `-k property`, or a single file like `tests/test_keyboard_bridge.py`).
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
| `qml/components/` | Reusable QML components (KeyButton, settings panels, etc.) |
| `data/` | Static data: dictionaries, training corpus, keyboard layouts, vocab packs |
| `build/` | Packaging pipelines - `build/windows/` (PyInstaller + NSIS + EV signing) and `build/linux/` (PyInstaller + optional AppImage). `build/launcher.py` is the shared frozen-mode entry point. |
| `tests/` | pytest suite |

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

`NgramPredictor.bigrams` / `.trigrams` are **merged views**. The user's share lives in `_user_bigrams` / `_user_trigrams` (floats), and that share is the **only context that is persisted** (`user_bigrams` / `user_trigrams` keys in `ngram_model.json`). The base share (curated seeds at +50 per pair from `data/common_bigrams.txt` / `common_trigrams.txt`, the training corpus at +1, vocabulary packs) is rebuilt from the data files on every launch and never written. Invariant: `bigrams[p][w] >= round(_user_bigrams[p][w])`; base is whatever is left over.

Why it is split, all measured on 2026-09-02 (the write-up with the numbers is linked from the memory index):
- The single merged table was persisted **and** re-seeded on top of itself at every launch, so seeds inflated: a live model had `i -> want` at 1,037 against 63 on a fresh install, and the trigram `i want -> to` at 3,848 against 53, growing by 50 per launch with no ceiling.
- `_apply_decay` scaled **every** bigram to a floor of 1 (its comment claimed user-only) and never touched trigrams. On a fresh model the curated ordering was flat within ~2,500 learns; on a matured one 78% of edges sat permanently at 1, which is where the user's own pairs ended up.
- A personal phrase after a common word could not surface: `the bus` typed once was P = 0.0004 against 2,600 seed mass, needed 55 typings back to back, and at once a day never reached the pills; the same phrase after a word with no seeds was scored as a certainty (P = 1.0).

How it works now:
- **Scoring** (`_context_probs`) trusts the user's distribution for a prefix with weight `U / (U + _CONTEXT_PRIOR_FLOOR + _CONTEXT_BASE_TRUST * B)` (5 and 0.02; `U` user evidence, `B` base count for that prefix), blending it with the base distribution. With no user evidence it is the old normalised row **exactly**, so a fresh model scores byte-for-byte as before (keystroke savings 52.5% before and after), and the whole existing suite is the regression guard for that. With it, `the bus` reaches the pills after 2 typings and one typing after a new word gets 1/6, not 1.0. The sweep behind the two constants: the floor barely matters between 2 and 10; trust 0.01 surfaces a phrase after one typing, 0.05 needs five.
- **Writes**: every user context write goes through `_bump_user_context` (from `learn()` and `reinforce_context`), which keeps the merged view in step. Base writes go straight to the merged tables: `learn(text, corpus=True)` (what `load_corpus` calls), `learn_corpus_context`, `_learn_base`, the two seed loaders, and `PackManager.apply_to_predictor`. A direct write to `.bigrams` in a test is therefore a base write and still works.
- **Decay** (`_decay_user_context`) acts on the user share of **both** orders, subtracting exactly what it removes from the merged view, and drops a count below `_USER_CONTEXT_MIN` (0.1, about a week for a single typing). Seeds are untouched however long the session runs (`tests/test_ngram_context_split.py::TestSeedsSurviveTheSession`). The merged count rounds to zero at tick 14 while the user share lasts to tick 45, so for a prefix with no base evidence the merged row is gone for two thirds of the pair's life; `_context_probs` scores from the user row alone in that window rather than returning nothing (`TestScoringTrustsTheUserInProportion::test_a_lone_user_pair_keeps_scoring_until_it_is_forgotten`).
- **Legacy files** carrying `bigrams` / `trigrams` are adopted wholesale as user history (`_adopt_user_context`): the halves cannot be separated after the fact, adopting keeps every ranking exactly as it was, and decay retires the inherited seed mass over the following weeks while the base is re-seeded cleanly underneath. The next save writes the new keys; an older build reading a new file loses only user context, since it re-seeds base itself.
- **`HybridPredictor.reload_from_disk` must call `_reseed_context()` after `load()`.** It used to work by accident, because the persisted table already carried the (inflated) seeds. `clear_user_data` wipes both halves and the hybrid re-seeds, as before. The reseed re-links the corpus through `learn_corpus_context`, which applies the same known-or-third-sighting gate as `learn(corpus=True)` (reading the candidate pool, never writing it), so a reload rebuilds the base share a launch would; its first version linked every plausible word, and a reload grew edges for rare corpus words that a fresh start withholds (`TestTheMergedViewStaysHonest::test_reseeding_the_corpus_gates_rare_words_exactly_as_a_launch_does`).

Known follow-ups, deliberately not bundled: the training corpus's *unigrams* are still learned as user typing on every launch (pre-existing, bounded by decay, and why a fresh `user_vocab` is not empty); the seed weight of 50 now only matters relative to the corpus's +1 and could come down; the cross-order interpolation weights are fixed at 0.5/0.3/0.2 (renormalising when a table is silent would not reorder anything, since the bigram-to-unigram ratio is unchanged; evidence-weighted interpolation across orders is the change that would). Benchmarks: `scripts/bench/ksr.py` (keystroke savings, ablations, `--learn-half`) and `scripts/bench/fuzzy.py`; both run against a temporary model directory, never the live one.

## Prefix beam (mid-word fuzzy completion)

`src/prediction/prefix_beam.py`. `FuzzyRecognizer.get_fuzzy_predictions`, the fuzzy source the hybrid merges **mid-word**, completes the typed prefix through an error instead of correcting it as a finished word. The whole-word paths (`generate_candidates`, `get_correction`, `should_autocorrect`, SymSpell), which run on space, are unchanged; they measured well (75 to 99% top-1 by error type) and it was only their use mid-word that was wrong.

Why (measured 2026-09-02, write-up linked from the memory index): mid-word, the old fuzzy source put the intended word in its top five 0.0% of the time after a neighbour slip and 1.4% with no error at all, because its spatial beam only emitted sequences as long as the typed text and SymSpell reached two edits further, so 87% of its mid-word candidates were shorter than the prefix. The n-gram completer needs an exact prefix. Between them one mis-click cost +2.05 clicks per word (+69%).

How: a beam over the dictionary's **live prefixes** (`PrefixIndex`, about 24,000 for the shipped list, 0.01 s, rebuilt lazily after `load_dictionary` / `set_frequencies`, updated in place by `update_word` as the vocabulary changes (see *Fuzzy dictionary refresh*), and rebuilt whenever the spatial model object changes, which `set_key_positions` does on a layout switch) with an **unnormalised** Gaussian emission (`SpatialEmissions`: a hit costs 0, so a perfect 13-letter typing no longer prunes out and an edge key no longer outscores a central one for equal accuracy) and four transitions: substitution, omitted click, extra click, transposition. Completions are ranked by path score plus `0.55 * log1p(freq)`. Scores come back relative, in (0, 1], so `_normalise_source` sees positives. Below three typed characters it returns nothing (the `should_autocorrect` guard; the n-gram's exact match is the better source there).

Constants were set by sweep against the shipped list **with the n-gram's counts**, which is how the hybrid runs it: the bare wordlist carries no frequencies, so without `set_frequencies` the frequency term is a constant and rankings fall to insertion order (the first prototype's numbers were spatial-only for that reason; any test or bench of this path must inject the counts, see the fixture in `tests/test_fuzzy_prefix_beam.py`). With them, and drawing words from the 2,000 most frequent the way typing is distributed (a uniform draw over all 10,000 weights a word used once a year the same as "because" and reads 30% / 62% on the first two figures; the first bench did that): a clean 4-letter prefix completes to the intended word 92% of the time, a one-slip prefix 94.5%, a dropped click 71%, a doubled click 58%, a transposition 96%, and the top pick never overrides a typed prefix (100%). Omission and extra pull against each other (a cheaper omission explains a doubled click away as something else: at -2.0 it is 83% / 52%, at -3.0 58% / 66%); -2.5 leans toward omission, the dominant error for this kind of input. `scripts/bench/fuzzy.py --n 300` reproduces the shape (`--legacy` for the before column): clean prefixes 0.2% -> 89%, a slipped prefix 0 / 0 / 0.3 / 0.3% -> 56 / 92 / 98 / 99.7% by prefix length 3 to 6. End to end (`scripts/bench/ksr.py --conditions legacy-fuzzy,full --mis-click`): keystroke savings 52.5 -> 53.6% on clean typing (the source is no longer net-negative) and **34.6 -> 47.9%** with one uncorrected mis-click per word, at about 0.3 ms per keystroke.

**The emission takes a position.** `get_fuzzy_predictions(..., positions=[...])` carries one optional `(row, col)` in key units per character of the current word, defaulting to the key's centre. `mouse.x / mouse.y` from `KeyButton.qml` now reaches it as `offsets=`; see *Click position and the learned pointer bias* for what that turned out to be worth.

`FuzzyRecognizer.prefix_completion = False` restores the pre-beam path. It exists for the benchmark's before/after (`ksr.py`'s `legacy-fuzzy` condition, `fuzzy.py --legacy`) and nothing else: there is deliberately no user setting, since a user could not act on it and the merge weights are untouched. Tests: `tests/test_fuzzy_prefix_beam.py`.

## Fuzzy dictionary refresh, and PPM out of the merge

Two more of the 2026-09-02 findings, fixed together on 2026-09-03.

**The fuzzy dictionary follows the vocabulary.** It used to be loaded once at startup (`load_dictionary` plus one `set_frequencies(ngram.unigrams)`) and never touched again: the constructor comment named a `_refresh_fuzzy_frequencies` that did not exist, and `enable_vocabulary_pack` wrote pack words into the n-gram only, so a word learned this session was not fuzzy-matchable (nor reachable by the prefix beam) until a restart, and a pack's words never were. Now every learning path pushes the changed words through `HybridPredictor._refresh_fuzzy_frequencies(words)` (`learn`, `learn_word`, `learn_from_selection`, `mark_good_suggestion`), which calls `FuzzyRecognizer.update_word(word, count)`: the dictionary entry is raised, **SymSpell indexes a new word in place** (`SymSpell.add_word` on a built index no longer invalidates it, since the rebuild is about 0.5 s and this runs on the keystroke path) and `PrefixIndex.update_word` adjusts the word's own prefixes. A pack enable merges the whole table (`_refresh_fuzzy_frequencies()` with no words). Both only add or raise, the `max` rule `set_frequencies` always had, so the three events that *shrink* the vocabulary, Clear Learned Data, a Data Backup import and a boost rollback from the dashboard (`clear_user_data`, `reload_from_disk`, `unprefer`), go through `_rebuild_fuzzy_dictionary`: `reset_dictionary`, the profile's wordlist, then the counts, about half a second at a moment the user asked for. `unprefer` was missed at first, and since the boost itself had reached the fuzzy dictionary through the refresh, a rolled-back word kept winning mid-word completions until the next restart. The one lowering deliberately *not* followed is `unlearn_word`, the backspace negative signal: it moves a count by one on the keystroke path, where the rebuild has no place, so the fuzzy count can sit a sighting or two above the n-gram's until the next rebuild. Tests: `tests/test_fuzzy_refresh.py`, one vocabulary event each, asking the fuzzy source on the same instance.

**Fuzzy frequencies are on the n-gram's scale, not the raw unigram count.** Plumbing the refresh through was not enough on its own: the fuzzy dictionary took the merged unigram count, which puts a base word at its rank-derived count (up to 9,885) and a personal word typed three times at 3, and on that scale the beam's frequency term buys four slips' worth of spatial cost, so a typed `zorb` ranked `spent` (z to s, o to p, r to e, b to n) above the `zorblat` the user had just taught it. The n-gram never had this problem, because `P(w) = 0.7 * P_user + 0.3 * P_base` makes a personal word far likelier than a base word. `HybridPredictor._fuzzy_frequency(word)` maps that same belief onto the base count's scale (a word the user has never typed keeps exactly its base count, so a fresh model ranks as before; a typed one is lifted by the n-gram's personal weight) and every injection into the fuzzy dictionary, at startup, on refresh and on rebuild, goes through it. It is the unigram cousin of the context-table split above: the same base-versus-user scale mismatch, one layer over. The beam carries one rule on top (`PrefixBeam._protect_exact_completions`): when the typed prefix is itself live, a candidate reached only by paths costing more than one cheap edit (`FREQUENCY_MAY_BUY`, -1.5: an adjacent slip is -0.69, a swap -1.0) is moved just below the exact prefix's own completions rather than bought past them by frequency, which is what lets a pack word at weight 3 surface against `question` two slips away, while `teh` still offers `the` first because one swap competes on frequency as before. A hard exact-first tier was tried and reversed: `teh` is a live prefix of a rare word in the shipped list, and the tier buried `the`.

**PPM no longer contributes word candidates.** `PPMWordPredictor` was constructed without a dictionary, so its dictionary-completion path was dead code and it ran as a bare character beam that emitted fragments (`ing` held a pill at every sentence start). Measured with the prefix beam in place (`scripts/bench/ksr.py --conditions ppm-merge,full --mis-click`): taking it out of the merge raised keystroke savings 53.6 -> 55.0% on clean typing and 47.9 -> 49.9% with a mis-click, next-word hits 30.7 -> 32.6%, and cut per-keystroke latency from 21 ms to 3 ms. It did not help in-domain learning (learn-half 53.8% with it against 54.4% without), and the one thing a character model could add, surfacing a word before its third sighting, never reached the bar, because `_is_valid_word` gates on the unigram table either way. `HybridPredictor._ppm_in_merge` (default `False`) is the switch. The model still trains on every `learn`, still loads and saves `ppm_model.json`, and `enable_ppm` still governs that, so a later fusion inside the prefix beam (the VelociTap shape) has a trained model to use. No user setting, for the reason the prefix beam has none. Any text elsewhere that describes the merge as "n-gram + PPM + fuzzy" describes it before this date; `docs/architecture/PPM.md` and `HYBRID_MERGING.md` carry a note.

## Click position and the learned pointer bias

The last of the 2026-09-02 recommendations, landed 2026-09-03, and the one whose measured gain is smallest; read the numbers before extending it.

**What travels.** `KeyButton.qml` always had `mouse.x / mouse.y` at the press and used it for the ripple. It now publishes `pressDx` / `pressDy`, the press as a fraction of the key's width and height from its centre (-0.5 to 0.5), and `Main.qml` hands them to the bridge with the character on all three char paths (`pressKey`, `pressKeyLiteral`, the right-click variant); auto-repeat re-reads the same values. The bridge slots are overloaded (`@Slot(str)` and `@Slot(str, float, float)` on one method with Python defaults), so every existing caller, tests included, still means "key centre". `_press_char` keeps the offsets in `_word_offsets`, parallel to `_current_word`, **each entry carrying the character it was recorded under**, and **does not try to maintain that list at every site that resets the word**: it re-syncs at the append (padding with centres when the recorded characters are not the word so far) and `_update_predictions` only hands the list on when the recorded characters spell the word (`_offsets_spell` / `_offsets_for_word`), so any path that rewrites `_current_word` without knowing about it degrades to today's key-centre behaviour rather than mis-scoring. The first version compared lengths, and a rewrite of the same length slipped through it: `hwllo` typed, the `hello` pill tapped, a backspace into it, and five offsets sat under four wrong letters (autocorrect has the same shape). Matching on the characters closes that without touching any of the sites that rewrite the word, the same argument `_token_pill_words` makes; backspacing into a word exactly as it was typed still carries its offsets, since those are the user's own presses. `HybridPredictor.predict` / `predict_with_refinement` take `offsets=` and pass them to `FuzzyRecognizer.get_fuzzy_predictions`, whose `positions_for` resolves each offset against the reported key's centre in the current spatial model (so it follows the layout) with the learned bias for that slot taken out, and the prefix beam scores the continuous position. Characters the model does not place (punctuation, the space bar) resolve to `None` and fall back to the key centre.

**What is learned.** `src/prediction/pointer_model.py` keeps, per **physical slot** (`slot_id` of the key's row and column, so the bias belongs to the pointer rather than the letter and survives a Dvorak or Colemak remap with no layout reset), the count and sum of offsets, and estimates each slot's bias as its own mean shrunk toward the global mean with `PRIOR = 10` pseudo-observations, Gboard's clustering idea in its simplest form. The bridge observes a press inside the not-privacy branch of `_press_char`, beside every other learning; unmapped characters are ignored; offsets are clamped to one key; nothing is logged. The table is owned by `NgramPredictor.pointer` for the reasons the token store is (one file for everything the user taught the engine, with the load caps, the backup archive and Clear Learned Data already in place; `pointer` key in `ngram_model.json`, malformed rows skipped one at a time) and the hybrid binds `FuzzyRecognizer.pointer` to that same object, which is therefore mutated in place and never rebound.

**Two emission widths.** `SpatialEmissions.KEY_SIGMA` (0.85) is the uncertainty when only the key is known: somewhere inside it, plus scatter. `POSITION_SIGMA` (0.55) is the uncertainty when the position inside the key is known. It was set by sweep and the sweep is the part to remember: sharper than 0.55 *hurt* both simulated pointers (0.3 lost 1.6 points of keystroke savings, 0.22 lost 6), because scatter then puts the intended key on the expensive side of the click more often than the extra precision helps.

**The gain is modest, and that is the finding.** `scripts/bench/ksr.py --pointer BIAS_X,BIAS_Y,NOISE` simulates a pointer end to end (reported key, offset, and the presses the bias is learned from) and reports each condition three ways. A pointer whose misses are mostly random (bias 0.2, 0.15; noise 0.3; about a quarter of clicks on the wrong key), the robustness check: keys only 50.5%, plus offsets 50.8%, plus learned bias 50.9%. One whose misses are mostly systematic (0.35, 0.25; 0.15; 22% wrong), the case this feature is for: 51.1%, 51.5%, **51.8%**. The earlier per-key simulation (75% to 86.5% intended-key recovery with a learned bias) was real but did not translate, because the prefix beam plus the dictionary already recover most single-key errors from the reported key alone; what a click position adds on top is a few tenths of a point, more for a systematic pointer than a scattered one. It ships because it never regresses at 0.55, costs nothing at runtime, is privacy-gated like every other learning, and because the persisted table is the first measurement of this user's actual pointer bias, which no simulation can supply. Tests: `tests/test_pointer_model.py`, `tests/test_click_position.py` (recognizer, bridge and persistence hops, each paired with the near-miss it must leave alone) and `tests/test_qml_click_position.py`, which loads `KeyButton.qml` on its own in a `QQuickView` (one item, not a Repeater delegate, so the scene point is reliable under the offscreen plugin) and presses at a known point, and which also calls the overloaded `pressKey` / `pressKeyLiteral` slots with three arguments and with one from a QML function against a real bridge: the Python tests call the slots directly and never touch Qt's overload resolution, and a three-argument call that failed to bind from QML would degrade every press to the key centre with no warning anyone would see.

## Short words in next-word predictions

`HybridPredictor._short_word_allowed` gates one- and two-letter words out of *next-word* predictions (the filter does not apply once the user has started typing a word, where the prefix already constrains things). It used to be a blanket `len(word) <= 2` with `"i"` as the single exception, which discarded exactly the words next-word prediction is best at: after "I want", the useful pills are "to", "it", "my", "us"; after "one", they are "of" and "or". Those are also the highest-frequency words in English, so the bar was withholding its strongest guesses and offering the fourth-best instead.

It is now an **allow-list of real short words**, not a relaxed length rule, and that distinction is load-bearing: the engine learns whatever the user types, so stray two-character fragments ("th", "ap", "sm") from a typo or an interrupted word accumulate in the model, and a bare length change would let every one of them compete for a pill. Words, not lengths. The list is the active language profile's `short_words` (`language.ENGLISH.short_words`), reused rather than restated: it is already the project's answer to "real word or keyboard slip" (it gates the dictionary-load fragment filter), and a private copy in `hybrid_predictor` would be one more thing to keep in sync. Extend that set to extend this filter. Guarded by `tests/test_hybrid_predictor.py::TestShortWordsAreOfferedAsNextWords`, whose negative half is what stops a future "just drop the filter" from passing.

## Auto-Capitalization & Proper Nouns

The pill-facing capitalization rule is intentionally minimal: **only the "I" family auto-capitalizes** (`"I"`, `"I'm"`, `"I'll"`, `"I'd"`, `"I've"` - the whole of `language.ENGLISH.always_capitalize`). Anything else stays in the casing the user typed. The mental model is "shift / caps lock is the cap signal, full stop" - pills do not second-guess intent.

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
Edit `always_capitalize` on the language profile in `src/prediction/language.py`. Keep it tight - it's the one auto-cap that will fire mid-sentence regardless of what the user typed, so anything beyond the "I" family needs to be unambiguous in *every* mid-sentence context (which proper nouns aren't, which is why Tiers 2/3 are gone).

## Where User Data Lives

- **Every store writes through `src/atomic_write.py`** (tempfile created in the same directory, flushed and fsynced, then renamed into place), so a crash or power loss mid-write can never leave a truncated file where a good one used to be. A new persistent store must not write with a bare `open()`/`write_text`; route it through `atomic_write_text` or `atomic_write_json` instead.
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
- **Dictation settings**: `dictation.json` in the config dir root (same paths as above). Holds the enable flag, model, language, input device, timeouts, custom words, **and the recogniser API key**, which makes it the only file here carrying a credential. Written atomically on every mutation, so there is no on-quit save path. Load-time caps mirror the sibling loaders: rejected outright over 64 KB (checked via `stat()` before the file is opened), every scalar clamped, an unknown model or language falling back to the default rather than being stored.
  - **The key is wrapped with DPAPI on Windows** (`CryptProtectData` via ctypes, no new dependency), which ties the ciphertext to the user account, so a copied file is inert elsewhere. Plaintext at mode 0600 on Linux/macOS, with `key_protected` recording which form is on disk so a file written on either platform still loads on the other. Never logged, never returned to QML in the clear, and never in the websocket URL (it rides in an `Authorization` header).
  - **Deliberately excluded from the Data Backup archive** (do NOT add it to `_MODEL_FILES` in `src/data_export.py`): an archive is carried between machines and handed around, which is the last place a credential belongs. Same class of reason as `telemetry.json`. Asserted by `tests/test_dictation.py::TestTheKeyNeverLeavesTheMachine`, so re-adding it fails loudly.
- **Diagnostic log**: `alpha-osk.log` in the config dir (`%APPDATA%/alpha-osk/` Windows, `~/.config/alpha-osk/` Linux), wired up in `keyboard_app.py::_configure_logging` as a `RotatingFileHandler` at 2 MB x 3 backups. The frozen build has no console, so this file is the only place updater errors and crash tracebacks land, which also makes it the file users attach to bug reports.
  - **It must never contain typed content.** No log record at INFO or above may interpolate a word, `_current_word`, `_context_buffer`, `_sentence_buffer`, or a prediction list. Log lengths and booleans instead. Anything that genuinely needs the content for local debugging goes at DEBUG *and* behind `if not self._privacy_mode:`. This is not a style preference: `_context_buffer` mirrors the on-screen text up to 200 chars, so a single careless `%s` turns the log into a plaintext transcript of the user's typing, and privacy mode does not gate the logging layer for free. Regression coverage lives with the prediction-path tests.
  - Deliberately **excluded** from the Data Backup archive (`_MODEL_FILES` in `src/data_export.py`), so the leak cannot compound through an export.
  - **Uncaught tracebacks only reach it because `keyboard_app.py::_install_exception_hooks` puts them there.** Without it the file holds just the failures somebody remembered to wrap in `try` / `except` + `_logger.exception`; everything else goes to `sys.excepthook`, which writes to stderr, and a windowed PyInstaller build has no stderr (`sys.stderr` is `None`), so the traceback for an actual crash was discarded at the moment it was worth the most. Both `sys.excepthook` and `threading.excepthook` are wired (the dictation capture, the updater's download worker and Linux's AT-SPI listener all run off the main thread, and a crash there never reaches `sys.excepthook` at all), both **chain** to the previous hook so a dev run keeps its stderr traceback, both wrap the log call because a hook that raises replaces the crash it is reporting, and `KeyboardInterrupt` is passed through unlogged. A module-level flag makes the install idempotent. **`build/launcher.py` is the one crash class the hook cannot catch** (it wraps `app_main()` in its own `except`), so it logs the traceback itself before showing its dialog; before that the dialog carried one line of `str(e)` and the traceback went nowhere. Covered by `tests/test_keyboard_app.py::TestUncaughtTracebacksReachTheLog`.
  - **The user can find it without being told where to look.** *Settings -> Data & Privacy -> Diagnostics* shows the path with **Open Log Folder** and **Copy Path**, backed by `getLogPath` / `openLogFolder` / `copyLogPath` on the bridge; Help & Shortcuts has a *Reporting a Problem* section and `bug_report.yml` names the file per platform. The path was previously announced only *inside* the log itself at startup, and the issue template asked for console output a frozen build cannot produce. **The folder, not the file**: a `.log` has no registered handler on a stock Windows install, so opening the file is a "how do you want to open this?" dialog, and the rotations live in the folder anyway. The filename lives once, as `LOG_FILENAME` / `get_log_path()` in `src/platform/__init__.py`, because the handler that writes it, the purge that globs for it and the panel that points at it must not drift. Covered by `tests/test_keyboard_bridge.py::TestTheLogIsReachableFromTheUI`.
  - **Pre-fix logs are purged once on upgrade.** `keyboard_app.py::_purge_pre_fix_logs` deletes `alpha-osk.log*` on first launch of a fixed build, guarded by a `.log-privacy-purge` sentinel in the config dir. Fixing the logging sites only stops *new* leakage; an upgrading user still had up to four rotated files holding a transcript of what they typed, so removing them is part of the fix rather than housekeeping. It must run **before** the `RotatingFileHandler` opens the file, because Windows will not unlink a log the handler holds, and it must never raise: logging setup is not allowed to be the reason the keyboard fails to start. The sentinel is why it runs exactly once, so a user who wants logs kept across restarts is not fighting us. Covered by `tests/test_keyboard_app.py::TestPurgePreFixLogs`.

## Snippets (Quick-Insert Text)

User-defined quick-insert text: name, email, phone, address, signatures, canned replies. The user taps one to copy it to the clipboard, instead of typing it out and fighting prediction every time. Opened from a **bookmark button in the suggestion bar, immediately left of the clear-context ring** (`snippetsBarButton`). It sits there rather than in the title bar because 45 px beats 28 px for an imprecise pointer, and left of the ring because the ring is pressed from muscle memory and should not move. **A title-bar copy survives as `snippetsTitleBarButton`, visible only when `suggestionsEnabled` is false**: the suggestion bar collapses to zero height with that setting, taking everything in it, and an unrelated setting must not be able to remove the only way into a feature (the clear-context button does have that hole, and did not get to spread it). Both icons are Feather's `bookmark` drawn through `StrokeIcon`, never a glyph, for the reason given under *Things to Watch Out For*. Backend in `src/snippets.py`; UI is the floating `snippetsWindow` in `qml/components/SnippetsWindow.qml`, instantiated from `Main.qml` (see *Floating window (NOT a Popup)* below for the component's boundary with the keyboard window).

### Data model and storage
Each entry is a `{label, value, color}` record: `label` is the short text on the tile (e.g. "Email"), `value` is the exact text typed when tapped, `color` is an optional tag name used to tint the tile. Persisted as `snippets.json` in the config dir (`%APPDATA%/alpha-osk/` Windows, `~/.config/alpha-osk/` Linux), saved synchronously on every mutation (atomic tempfile-then-rename), so there is no on-quit save path to wire up. On first launch the store seeds four pre-made empty labelled slots (Name / Email / Phone / Address); every field including the label is editable and deletable. Bounds: `MAX_SNIPPETS` 50, `MAX_LABEL_LEN` 40, `MAX_VALUE_LEN` 2000, file cap 1 MB. A corrupt, oversized, or empty file falls back to the seeded defaults rather than raising. Labels are collapsed to a single line; values keep newlines (a value may be a multi-line block like a mailing address), though `_clean_value` strips every other C0 control character plus DEL (0x7F, which sorts above the C0 range, so an `ord(ch) >= 0x20` filter alone would miss it). That's for locally authored snippets only: an *imported* value has its newlines flattened to spaces instead, see *Backup* below.

**Colour tags are stored as a *name* from `SNIPPET_COLORS`, never as the hex the UI draws** (`""`, `red`, `amber`, `green`, `blue`, `purple`). Two reasons, both load-bearing. `snippets.json` is replace-on-import from an archive the user picked, and the stored string ends up in a QML `color` property, so an arbitrary value from an untrusted file must never reach it verbatim; `_clean_color` normalises case and whitespace and drops anything off the list to untagged, per entry rather than rejecting the file (a bad tag is not a reason to lose someone else's snippets). And the hexes have to stay legible on nine themes, which the store has no way to know, so QML owns them (`snippetsWindow.tagInks` in `SnippetsWindow.qml`) while the store owns which names exist. `getSnippetColors()` hands the list to QML so a swatch can never offer a tag the store would silently drop, and `tests/test_qml_snippets.py::TestColourTags` asserts every name has an ink, which is what catches the two halves drifting apart.

**`""` is the grey default, not a missing value.** An untagged snippet renders in the theme's own key colour and the first swatch is a plain grey circle. That is also why there is no grey *in* the list, and why the blue-grey "slate" that was briefly there is gone: a tag that reads as the default is a tag that cannot be seen.

`SCHEMA_VERSION` is 2 (colour). Nothing reads the version on load, deliberately: an entry with no `color` reads as untagged and one carrying an unknown name is retagged to untagged, so a file from either side of the bump loads correctly on its own merits, and a version gate would refuse files this loader can in fact read.

### Tapping a snippet copies it to the clipboard
`KeyboardBridge.copySnippet(index) -> bool` puts the value on the system clipboard via `QGuiApplication.clipboard()`, and QML flashes the `snippetCopiedToast`. **This is the only thing a tile tap does.** There is no "type it" control anywhere in the UI.

Copy beat typing for the reason the *Insertion path* below spends four sentences on: a synthetic insert is one click against a paste's two, but it only lands correctly when the caret is already in the right field and the app does not intercept synthetic keystrokes (the entire reason Compatibility Mode exists), a long address arrives one character at a time, and **when it misses it misses silently**, into whichever window happened to be focused. A clipboard write has no focus race.

Three things follow from the clipboard being invisible:
- **The toast is the only feedback**, so it is not optional garnish. It **names the snippet** ("Copied Email"), because with colour-tagged near-duplicates on screen a confirmation that does not say which one was taken is worth very little, and it dwells 2 s rather than the 1.4 s of the sibling confirmations because it is read rather than merely noticed.
- **The toast lives on the keyboard window, not in the snippets window**, which hides itself on the same tap and would take a toast parented there down with it. Its `closePolicy` must stay `Popup.NoAutoClose` for the usual reason: every OSK key click is a press-outside.
- **An empty snippet and an out-of-range index return False without touching the clipboard.** Copying "nothing" would silently wipe what the user had already put there, which is the one way this feature can destroy something. Guarded by `tests/test_qml_snippets.py::TestTappingATileCopies`.
- **A False return has to say so on screen** (`snippetProblemToast`, shared with the editor's failed save). The whole argument for the toast is that a clipboard write is invisible, which makes the failure branch the one that needs it most: doing nothing there is indistinguishable from a tap that did not register, so the user taps again. `copySnippet` also imports `QGuiApplication` **inside the slot**, not at module scope: QtGui dlopens the host's libEGL/libGL on first import, and `keyboard_bridge` is imported by most of the Python suite, so an unconditional import turns every one of those files into a pytest *collection error* on a host without them rather than a skip. Its `isinstance(QGuiApplication.instance(), QGuiApplication)` guard is deliberate too, since `instance()` is inherited from `QCoreApplication` and hands back a plain `QCoreApplication` in a non-GUI process, so a null check does not answer the question it appears to.

Not gated on privacy mode, same rule as the insert path: privacy is about not *learning* from typing, and the user may need their own address in a sensitive form. Nothing in the copy path learns or logs, and the value is never written to the diagnostic log.

### Insertion path (Python only, no UI path today)
`KeyboardBridge.insertSnippet(index)` types a snippet verbatim into the focused app. **Nothing in QML calls it** since the tile tap became a copy; it is kept because it works, is covered by tests, and is the reference for how a verbatim snippet insert has to behave. Read it before wiring any new path that types stored text.

`KeyboardBridge.insertSnippet(index)` routes the value through `_commit_verbatim_insert(value)`, the same call `insertGlyph` makes: prologue (`_release_sticky_modifiers()`, settle a deferred auto-space, spend an armed auto-capital), then `_send_literal_text` (a held Shift would otherwise deliver the whole address in capitals), then the shared typing-state reset. Snippets are full literal inserts, so unlike prediction pills there is **no** prefix matching, no autocorrect, and **no compat-mode BackSpace+retype** (that dance exists to replace a typed prefix, which a fresh insert doesn't have). Insertion is **not** blocked by privacy mode: privacy is about not *learning* from typing, and the user may need to drop their address into a sensitive form. After inserting, `_current_word` / predictions are cleared so the verbatim text (which may carry punctuation or newlines) can't corrupt the next prediction's prefix matching. `insertSnippet` is a no-op while edit mode is active (`_edit_mode_active`) so it can't fire while the user is editing a snippet field.

### Floating window (NOT a Popup)
`snippetsWindow` is a **separate top-level `Window`**, not a QML `Popup`, and it lives in its own file, `qml/components/SnippetsWindow.qml`, instantiated from `Main.qml` as `Comp.SnippetsWindow { id: snippetsWindow ... }`. A `Popup` is clipped to its parent window's overlay, so it could never be dragged off the keyboard; a standalone `Window` floats anywhere on the desktop. It carries the same OSK flags as the main window (`Qt.Window | FramelessWindowHint | WindowStaysOnTopHint | WindowDoesNotAcceptFocus`). On Windows that Qt flag alone doesn't stop click-activation, so `keyboard_app.py::_wire_floating_windows` finds the window by `objectName: "snippetsWindow"` and re-applies `WS_EX_NOACTIVATE` (via the shared `src/platform/windows_window.py::apply_extended_styles`) on every `visibleChanged` (the native handle only exists once shown). Non-Windows is a no-op (X11/Wayland respect the Qt flag; macOS uses the app-wide Accessory policy). The header is a drag handle that moves the whole window freely with no clamp. The dragged position **persists across restarts** (`appSettings.savedSnippetsX/Y`, the same -1000000 sentinel and on-screen clamp as the main window's `savedWindowX/Y`, written once on drag release rather than on every motion event); first open on a fresh install centers it just above the keyboard. It used to reset every launch, which undid the one adjustment anyone makes to this window: dragging it clear of the field they are filling in.

**The component's whole surface with the keyboard window is `required` properties plus signals, declared at the top of the file.** `SnippetsWindow.qml` cannot see `root`, `appSettings` or any id declared in `Main.qml`, so every theme colour, helper function (`clampedWindowPos`, `inkOn`, `luminance`, `applyEditChord`) and the settings object it reads/writes are `required property` declarations that `Main.qml` binds when it instantiates the component; there is no fallback default, so a missing binding fails loudly at load rather than producing a blank or mis-themed window. The three confirmation toasts (`snippetCopiedToast`, `snippetProblemToast`, `editSavedToast`) stay behind on the keyboard window rather than moving into the component, because this window hides itself on the same tap that would trigger one and would take a toast parented here down with it; `SnippetsWindow.qml` instead declares `copied` / `problem` / `saved` signals that `Main.qml` connects to the real toasts.

**The restore clamps to the whole virtual desktop, not to the primary screen** (`root.clampedWindowPos` over `root.desktopBounds`, the union of `Qt.application.screens`). `Screen` is the screen the *item* is on and `Screen.virtualX/Y` describe that screen's origin rather than the desktop's, so a `Screen.width` clamp dragged a window saved at x=2400 on a second monitor back to the primary one on every launch, and collapsed a left-hand monitor's negative coordinates to 0. That is worse than not persisting at all: the window lands nowhere near the keyboard it belongs to. The main window's own restore block still has the primary-screen version; it is the same bug and was left alone deliberately, as it is out of scope for the snippets change and touches the window the user depends on daily. The multi-monitor case cannot be exercised headlessly (the offscreen plugin gives one screen), so `TestTheRestoredPositionIsClampedToTheWholeDesktop` pins that the bounds come from the screen *list* and says so in its docstring rather than implying more coverage than it has.

### Three views: tile grid, actions sheet, editor
The window shows exactly one of three views, gated on two indices in priority order: the **editor** (`editingIndex >= 0`), the **actions sheet** (`menuIndex >= 0`), otherwise the **tile grid**. `tests/test_qml_snippets.py::TestTheWindowLoads::test_only_one_view_is_ever_showing` asserts that, because the three are siblings gated on the same two properties and a botched condition shows two at once rather than failing loudly.

**The grid pages, 2 columns x 3 rows, and that is not a scroll.** This window floats over whatever the user is typing into, so a list that grew downward would eventually cover the target app, and at the 50-snippet cap it would run off the screen. A page keeps its full six cells as soon as there is more than one page (the Repeater's model is `pageSize`, not the remaining count), so a short last page cannot pull the pager and the Add button up the window, under a pointer already travelling toward one of them. `page` is a plain int rather than a binding, so `clampPage()` runs from both `refresh()` and the `onSnippetsChanged` handler: deleting the last snippet on the last page used to strand the grid on a page that no longer existed, showing six empty cells and a pager counting "Page 3 of 2".

**There is a left-click-only route to the sheet: the header's Manage toggle.** Right-click opens it and press-and-hold deliberately does not, which between them made every management action unreachable to a pointer that can only left-click: dwell-click, switch access, a head or eye tracker, a single-button adaptive mouse. Such a user could copy a snippet and nothing else, never editing, recolouring, reordering or deleting one, and never freeing a slot at the 50 cap. The list this grid replaced at least put a pencil and a cross on every row, so it was a reachability regression rather than a relocation. It is a **mode**, not a per-tile control, for the reason the grid exists: a second target on a 165x58 tile sits a few pixels from the one pressed every day, which is exactly the arrangement the rework removed. In manage mode the whole tile is the target and copy is unreachable, so the worst a mis-tap does is open a sheet. Tiles take an accent border while it is on (a border rather than a fill, same contrast argument as the compact accent keys), the toggle is hidden inside the sheet and the editor, and `openList()` clears it, since copying is what the window is for and managing is an errand. The button is sized to the wider of its two labels: driven by the live text it shrank 24 px on flipping to "Done" and slid the close ✕ along the header under a pointer already moving toward it. Guarded by `TestTheTileDispatchesOnMouseButton`.

**Right-click a tile opens the actions sheet; left-click types the snippet.** The sheet is a *view*, not a floating menu, and that is deliberate: a popup anchored to a 165 px tile inside a 360 px window has to be clamped away from two edges, and it puts every management action on a target smaller than the tile it came from. Taking over the window instead gives each action the full width. It carries Copy to clipboard / Edit label and text / the colour swatches / Move earlier / Move later / Delete. **Press-and-hold deliberately does not open it**: a click held a beat too long is ordinary on a keyboard built for slow motor input, and it must never turn typing a snippet into opening a menu.

**Moving follows the snippet, not the slot** (`moveSnippet` updates `menuIndex` and the page it landed on). The sheet is about one snippet, and staying on the index would silently retarget it at whichever one swapped in. It points at the destination *before* calling the bridge, because the mutation emits `snippetsChanged` synchronously and the handler reads `menuIndex`.

**The sheet and the editor track a snippet's identity, not its index** (`menuIdentity` / `editIdentity`, label + value, compared in `onSnippetsChanged`). A Data Backup import replaces the whole list underneath an open sheet, and an index is not an identity: Delete, Edit and the colour swatches went on acting on whatever the import had put at that index, and an index past the new end opened a blank editor whose save was a silent no-op behind a green "Saved". Identity deliberately excludes the colour, or recolouring from the sheet, which is meant to leave it open, would close it on every swatch tap. Guarded by `TestTheSheetTracksItsOwnSnippet`, whose inverse half is the colour and move cases.

**Which mouse button did what lives in `tileClicked(idx, button)`, not in the delegate.** A synthetic click cannot be delivered to a Repeater delegate reliably in the headless tests (the offscreen window's layout has not settled, so every tile maps to the same scene point), so the whole test suite drove `openMenu()` / `primaryTap()` directly and the actual dispatch had no coverage at all: swapping the two branches left everything green. The delegate is now a one-line pass-through and the branch is a named function the tests call. `acceptedButtons` is asserted separately, since dropping `Qt.RightButton` is the half a pass-through cannot state.

**Delete always confirms**, in place, with Keep first and wider than Delete. A snippet has no undo behind it and this window is operated with an imprecise pointer. `closeMenu()` clears `confirmingDelete` so a half-answered prompt is never waiting when the sheet reopens.

**Add goes inert at the cap** rather than trying and failing. `SnippetStore.add` refuses past `MAX_SNIPPETS` by returning False and the list simply does not grow, which the old button could not tell from success: it opened the editor on "the last snippet" either way, which at the cap is an existing snippet the user never asked to edit. QML reads the cap from `getSnippetLimit()` rather than hardcoding 50.

**Nothing in this window typesets an icon as a font glyph.** `qml/components/StrokeIcon.qml` draws them from SVG path data on a Canvas, the same approach and the same reason as the clear-context button: on Windows the geometric-shape and dingbat ranges commonly resolve through Segoe UI Emoji, which renders in colour and ignores the `color` property outright. The old pencil and cross were exactly that. `qml/components/SheetRow.qml` is the sheet's action row, deliberately word-only for the same reason.

**Every colour is theme-derived.** The window used to be hardcoded blues, reds and greens, which read as foreign on the dark themes and broke outright on Typewriter (a light theme with near-black text). The tag inks are the one exception, because they are user data rather than chrome; `danger` is picked per theme luminance (a dark red is illegible on Typewriter's cream, a bright one glares on Spaceship's near-black) and text on an accent fill goes through the shared `root.inkOn()`.

### Editor UX (reuses the edit-mode plumbing)
The editor is a per-snippet form with Label + Text fields. **Edit mode is only turned on while the editor is showing**, not for the whole window. This is critical: if it set `setEditMode(true)` on open, tapping a snippet in the list would be swallowed by edit-mode routing instead of inserting to the OS. In the editor, OSK keystrokes flow through the same `editKeyTyped` / `editSpecialPressed` signals the prediction-edit popup uses; an `editTarget` property ("label" / "value", set by tapping a field) picks which `TextField` receives them. Saving calls `setSnippet` and flashes the shared `editSavedToast` **only when it returns True**; `SnippetStore.set` refuses an out-of-range index, and this editor is reachable from the actions sheet, whose index an import can invalidate, so the flash was capable of confirming a write that never happened. That is the same failure `acceptSnippetOffer` was given a bool return for, and `setSnippet` was the last mutation slot reporting nothing. Empty slots are never dead taps: tapping a tile with no value opens the editor directly instead of inserting. The editor edits label and value only, so `setSnippet` leaves the colour tag alone (`SnippetStore.set` takes `color=None` meaning "keep"); a save that replaced the whole record would silently clear a tag set from the sheet.

**The editor has to provide the text-box behaviour the window's own flags take away, and it did not.** Three things were missing, all reported at once. (1) **Each field carried a `MouseArea` filling it**, there to record which box the OSK types into, and a MouseArea's entire job is to consume the press: caret placement, double-click-for-a-word, triple-click and drag-select were all dead behind it. Nothing else was wrong, which is the part worth remembering: `selectByMouse` was true throughout and `selectWord()` worked when invoked directly, so the fix is `mouse.accepted = false` in `onPressed`, keeping the bookkeeping and passing the event down. Any future overlay on an input has to do the same. (2) **Tab did nothing**, and since this window cannot hold OS focus there was no other key-driven way to change field, only landing a click on the other box; it now calls `focusOtherField()`, which switches and `selectAll()`s, so replacing a value is one gesture. (3) **Shift with an arrow moved the caret instead of selecting**; the arrow/Home/End branches now go through `moveCaret()`, which reads `root.shiftOn` and calls `moveCursorSelection`. `shiftOn` is still true at that point because the bridge's edit-mode intercept emits and returns *before* the auto-release block. Guarded by `tests/test_qml_snippets.py::TestTheEditorSupportsOrdinaryTextEditing`, which drives real `QTest` mouse events rather than the fields' QML API, because driving the API is exactly what let the swallowed clicks go unnoticed.

### Bridge slots and signal
`getSnippets() -> QVariantList`, `insertSnippet(int)`, `setSnippet(int, str, str)`, `addSnippet()` (appends a blank "New" slot for the user to fill), `deleteSnippet(int)`, `moveSnippet(int, int)` (direction -1 up / +1 down), `copySnippet(int) -> bool`, `setSnippetColor(int, str)`, `getSnippetColors() -> QStringList`, `getSnippetLimit() -> int`. The `snippetsChanged(list)` signal is emitted after every mutation so the window re-queries and rebuilds its tiles; `setSnippetColor` deliberately does **not** emit when the tag is unchanged, since QML rebuilds the whole grid on it.

### Backup
`snippets.json` is in the Data Backup archive (`_MODEL_FILES` in `src/data_export.py`), replace-on-import like the model files. **Import flattens newlines**: every `\r`/`\n` in an imported snippet's value is replaced with a space (`data_export.py::_flatten_imported_snippet_newlines`) right after extraction, because `xdotool type` turns a literal newline into a real Return keypress and an imported archive is untrusted content; a snippet authored locally in the editor keeps its newlines (see *Data model and storage* above). Best-effort by design: a flatten failure is logged and the file is left as extracted rather than aborting an otherwise-successful import. After an import, `KeyboardBridge.importUserData` calls `SnippetStore.reload_from_disk()` + emits `snippetsChanged` so the running session picks up the imported snippets without a restart. It is *not* encrypted: it's local user data on the user's own machine, same trust model as the prediction model. Guarded by `tests/test_data_export.py::TestSnippetNewlineFlattening`.

## Intelligent Spacing & Snippet Auto-Detection

Two features that look unrelated but ask the same question ("does this text have a recognisable shape"), so they share one module: **`src/text_patterns.py`**. Answering it in one place keeps them from drifting into disagreeing about what an email address looks like. Everything there is linear-time with explicit length caps, because it runs on the keystroke path and the input is whatever the user is typing; and nothing there logs, because every argument is typed content.

### `_raw_token` (the run before the cursor)

Both features need the unbroken run of characters immediately before the cursor, punctuation included. **This cannot be `_current_word`**: that is the prediction engine's notion of a word and it resets at `@` and at every dot, so by the time the `.` in `owen@gmail.com` arrives, `_current_word` is `gmail` and the `@` that proves this is an email is already gone. `_raw_token` is maintained in `_press_char` (append), `pressSpecialKey` (cleared on space/return and on `_TOKEN_BREAKING_KEYS`, popped by backspace), and cleared by every verbatim insert and context reset. Capped at `_MAX_RAW_TOKEN_LEN` (128). It is only maintained **outside privacy mode**, same as `_current_word`, because it holds typed characters.

### Intelligent spacing

*Settings → Smart Typing → Suggestions → Intelligent Spacing*, default ON, disabled and greyed when Auto-Space After Punctuation is off (it modifies that behaviour and does nothing without it). Skips the punctuation auto-space when it would break a structured token: `3.14`, `1,000`, `12:30`, `owen@gmail.com`, `www.example.com`, `https://…`, `192.168.1.1`, `C:/Users/…`, and every dot after the first in a dotted run.

When suppressed, three things are skipped together: the sent space, the space in `_context_buffer` (it mirrors the screen, and a phantom space breaks pill inserts), and the auto-capitalize (or `example.com` comes out `example.Com`). The auto-space-**off** path is deliberately byte-identical to before.

**The other half of the auto-space is taking one back, and `"` cannot join the list that does it.** `_NO_SPACE_BEFORE` (`? ! . , ; : ) ] }`) is the set whose members backspace over an auto-space we inserted, and every one of them is a closer or a terminator. A quote is both: `he said, "hi."` wants the space kept before the first `"` and removed before the second, so adding it to the set would jam every opening quote against the word in front of it. `_closes_a_quotation` decides per keystroke on the parity of `"` in `_context_buffer`; odd means we are inside a quotation and this one closes it. A buffer truncated at 200 chars or cleared by an app switch reads as even and keeps the space, which is the safe way to be wrong: a stray space the user deletes beats one we deleted for them. Guarded by `tests/test_keyboard_bridge.py::TestTheClosingQuoteRemovesTheAutoSpace`, where each removal case is paired with the near-miss it must leave alone.

**Structural rules are gated on structural punctuation.** The `@` and path rules fire for `.` and `:` only. A domain contains dots and colons and never a comma or a semicolon, so one typed after an address belongs to the sentence around it; firing for all four auto-spaced marks turned `owen@gmail.com, thanks` into `owen@gmail.com,thanks`. Guarded by `tests/test_text_patterns.py::TestAutoSpaceSurvivesProse::test_a_comma_after_an_address_is_prose`, paired with the inverse that the structural marks are still suppressed.

**A bare digit run suppresses *provisionally*.** `3` and `42` are the same token, so `3` + `.` (a decimal) and `42` + `.` (a full stop) cannot be told apart when the dot lands. Suppressing outright corrupted prose (`the total is 42. Then` → `42.Then`, capital skipped too); not suppressing broke `3.14`. So the space is withheld and settled by the **next** character: a digit confirms the decimal, a letter proves prose and the withheld space (plus any capital it owed) is typed then, one keystroke late. `text_patterns.suppression_is_provisional` decides which suppressions qualify (a bare digit run and nothing else: `192.168.1` and `1,000` already carry a separator, and `@`/path/scheme rules stand on their own evidence); the bridge holds the owed punctuation in `_deferred_auto_space` and settles it in `_flush_deferred_space`.

This is *not* a reintroduction of the rejected "guess from the following character" rule below, and the difference is the direction: that rule *withheld* a space on a guess, this one only ever *adds* one. Nothing typed is taken back, so the worst case is a space arriving a keystroke late rather than mangled text. The deferral is dropped, never delivered, by any special key (the user typed their own space, backspaced, or moved the caret) and by a context reset (the punctuation is no longer at the caret); the verbatim insert paths flush it, because a pill and a snippet are both prose. Guarded by `tests/test_keyboard_bridge.py::TestTheDeferredSpaceAfterABareNumber`.

### Auto-capitalize is not a held Shift

`_auto_capitalize_after_punctuation` arms **`_pending_auto_cap`**, never `_shift_active`. The distinction is load-bearing and was learned the hard way. Expressing it as a Shift looks equivalent (both uppercase the next letter) but `_send_key` builds its chord modifiers straight from `_shift_active`, so a sentence-ending period left every following chord poisoned: Enter became Shift+Enter (a newline in Slack rather than send), Ctrl+C became Ctrl+Shift+C, and arrows started extending a selection. It was also the one site in the file that set an `_*_active` flag with **no paired `hold_modifier()`**, so the bridge believed in a hold the OS never had, which is exactly the invariant `_without_held_modifiers` relies on to decide what to restore. `_pending_auto_cap` feeds the case computation in `_press_char`, `_update_layer` (so the keycaps show it) and `_display_cased` case 3 (so a next-word pill shows the capital it will insert, exactly as a held Shift does); it is spent by the next character, and dropped by a caret move or a context reset. Space deliberately does *not* clear it, because the word after the auto-space is the one the capital was meant for. Guarded by `tests/test_keyboard_bridge.py::TestAutoCapitalizeIsNotAHeldShift`.

**Three things must spend it, and each was missed once.** (1) The **verbatim inserts** (`pressPrediction`, `insertSnippet`) each call `_consume_auto_cap`: a tapped pill is the next thing typed, so it takes the capital, and leaving the flag armed handed the same capital to a later unrelated character. (2) The **edit-mode branch** of `_press_char` deliberately does *not* consult it at all: the capital is owed to the app behind us (nothing typed in edit mode can arm one, the branch returns before the punctuation handling), and applying it there spent nothing, so after `hello.` every character typed in the prediction-edit popup or the snippets editor came out uppercase. (3) The spend at the end of `_press_char` is guarded on `consumed_auto_cap and not rearmed_auto_cap`. "Was one owed when this keystroke started" is not "did this keystroke arm one", and the `?` of `Wait!?` does both; on the snapshot alone it threw the new capital away. Note the parity trap when testing this: each mark alternately armed and cleared, so a three-dot ellipsis came out right by accident and proves nothing. Use even-length runs.

**And one class of character must *not* spend it: the quotes and brackets in `_CARRIES_AUTO_CAP` (`" ' ( ) [ ] { }`).** They can stand between a sentence boundary and the first letter of the sentence, and none of them can carry a capital, so spending one on them threw it away: `hi. "hello"` and `hi. (see below)` both came out lowercase, as did `he said "hi." Then`, where the closer sits on the other side of the same gap. Everything else still spends it, a **digit included**, since `hi. 5 apples` starts its sentence with the digit and there is no letter left owed a capital. Keep the set tight: a mark that only ever appears mid-word (`-`, `/`, `=`) would leave the flag armed on a keystroke that genuinely ended the case for it. Guarded by the three `passes_it_along` cases in `TestAutoCapitalizeIsNotAHeldShift`, each paired with a spend case (a letter, a digit) that fails if the carry is widened to everything.

(Before this, the feature was inert: it set `_shift_active` and the auto-release block at the end of the same keystroke cleared it, so the toggle had no observable effect at all.)

**Every rule requires positive evidence in the token itself.** There was briefly a "first-token rescue" that suppressed the dot in a lowercase single word when nothing containing whitespace had been typed yet, meant to catch `example.com` in a URL bar. **Do not reintroduce it.** The proxy for "start of the field" was `_context_buffer` being space-free, and that buffer is emptied on every app switch *and* every focused-element change (the 250 ms UIA poll), so the rule fired at the start of every newly focused text box: click into a field, type `hello.` and the space vanished, then kept vanishing because the buffer still held none. It also cannot be repaired with a better proxy, because at the instant the dot lands `example` and `hello` are the same string.

**Known limitation, now unconditional.** A bare `example.com` always gets a space after its *first* dot, and since that space ends the run, `example.co.uk` loses one per dot. Guessing from the following character was tried and rejected too: it turns `i went home. then i left` into `home.then`, and corrupting prose is far worse than a space the user can delete. Closing it properly needs lookahead (buffering keystrokes until a known TLD resolves), which costs the immediate visual feedback a mouse-driven keyboard depends on. Pinned by `tests/test_keyboard_bridge.py::TestIntelligentSpacingInAFreshField` and `TestIntelligentSpacing::test_a_mid_sentence_bare_domain_loses_a_space_per_dot`. Change those if you improve it, don't delete them.

### Snippet auto-detection

*Settings → Data & Privacy → Privacy*, default ON. At each word boundary, `_maybe_offer_snippet()` looks for an email / phone / address and emits `snippetOffered(kind, label, value)`; QML shows the `snippetOfferToast` with a Save button and a dismiss ✕, and calls `acceptSnippetOffer()` / `dismissSnippetOffer()`.

It lives under **Privacy** rather than Smart Typing because the question it raises is "may the keyboard notice personal details you type", not "how should typing behave".

Six guards, all of which exist because the failure mode is offering to store the user's personal data when they didn't ask: never in privacy mode; never twice for the same value; never for a value already in a snippet; never on top of a live offer; nothing is written until Save is tapped; and a live offer is **withdrawn** when the context it belonged to goes away.

`_offered_snippet_values` is the don't-nag ledger. It is a dict used as an ordered set so `_remember_offered` can bound it at `_MAX_REMEMBERED_OFFERS` (64): it holds emails, phone numbers and addresses the user typed, so it is not allowed to accumulate for a whole session. Overflowing costs at most a re-offer of something dismissed long ago.

**It is written when the user *answers* an offer (`acceptSnippetOffer` / `dismissSnippetOffer`), never when one is raised.** Recording it at raise time made every withdrawal permanent: a caret poll, an app switch or privacy mode took the toast away and the address became unofferable for the rest of the session, so retyping it never brought the Save button back. A withdrawal is not an answer. The toast's own 8 s timeout does come through `dismissSnippetOffer`, because ignoring an offer is one. Guarded by the pair `test_a_withdrawn_offer_can_be_raised_again` / `test_a_dismissed_offer_stays_dismissed`.

`_withdraw_snippet_offer` fires from `_reset_typing_context` whenever `keep_snippet_offer` is not set, so an app switch and entering privacy mode drop a pending offer (the three within-window signals deliberately do not; see *Clearing stale context* above). It never writes the don't-nag ledger, because the user did not answer. It is also what `setSnippetDetection(False)` calls: turning the feature off while a toast is up must take the toast with it, and clearing only the Python side left a Save button that reported **"Snippets are full"** when tapped, because `acceptSnippetOffer` returns False for "no offer pending" and for the cap alike. It **emits `snippetOfferWithdrawn`** rather than only clearing the Python side, because the toast lives in QML on its own timer and would otherwise sit there with a Save button that silently does nothing. This is also what stops an offer raised just before focus landed on a password field from remaining savable: privacy mode has to mean "stop doing this", not "stop starting new ones".

**`acceptSnippetOffer` returns a bool and QML must honour it.** `SnippetStore.add` refuses past `MAX_SNIPPETS` (50) and reports it by returning False, so at the cap the save used to do nothing at all while the UI flashed "Saved" and the user walked away believing their email was stored. QML now flashes the confirmation only on True, and shows `snippetsFullToast` otherwise.

Two scan windows, both needed. `_raw_token` is the only place a single-token shape survives intact (see above). The last `_SNIPPET_SCAN_TAIL` (120) chars of `_context_buffer` then cover shapes that span whitespace: an address is several words, a phone is often `(555) 123 4567`. The tail is bounded so an address typed a paragraph ago doesn't resurface at an unrelated word boundary.

**Accepting fills the empty matching slot, or appends a numbered one.** The seeded Name / Email / Phone / Address slots exist to be filled, so an empty one takes the value. A slot that already holds a *different* value is never overwritten: a second email becomes `Email 2`. Work and personal addresses are both worth keeping, and silently replacing a value the user had curated is the worse failure.

**Detection is conservative on purpose, and both matchers had to be tightened once.** An offer the user has to dismiss twice is an offer they learn to ignore, and a value that reaches Snippets goes to disk, travels in the Data Backup archive, and is one tap from being typed into whatever app has focus. The first version of each rule sounded specific and was not:

- **Phone** started as "7-15 digits with at least one separator", which accepts a US Social Security number (`123-45-6789`), the leading digits of a card number, an ISO date (`2026-08-13`), an IP address, and `1.234-5.678`. Offering to store an SSN typed into a tax form is the worst thing this feature could do. The rule is now the digit **grouping**, matched against `_PHONE_GROUPINGS` (`(10,)`, `(3,3,4)`, `(3,4)`, `(1,3,3,4)`), with a leading `+` accepted on its own because international grouping varies too much to enumerate and nobody writes `+` in front of a date. `(3,2,4)` is an SSN, `(4,2,2)` a date, `(4,4,4,4)` a card, `(3,3,1,1)` an IP: all absent by construction.
- **Address** started as "a house number plus a street-type suffix", which matched `it took 2 hours to drive`, `we walked 3 miles down the road` and `there were 10 people in the square`, because the suffix list is full of ordinary English words and any digit run reads as a house number. It now needs a **third anchor: a capitalised street-name word** between the two. People write `123 Main Street`; the prose cases are lowercase throughout. The lookbehind also excludes `.` and `,` so a decimal cannot supply the number (`total 12.50 in the close` matched with `50`). The cost is a miss on an address typed entirely in lowercase, which is real on a keyboard where capitals take a click, and still the right trade: a missed offer is one the user never sees, a false one puts a slice of a private message on screen and one tap from disk.

Every positive case in `tests/test_text_patterns.py` is paired with the near-miss it must reject. **Pick hostile examples.** The original pairs (`500 people showed up`, `walking down Baker Street`) each fail an anchor outright, so they passed happily against matchers that were wide open; the ones that actually bite are the SSN family and ordinary prose containing a number.

**The toast is parked at the bottom of the window and its buttons arm after 400 ms.** Both are deliberate, and neither is polish. The offer is raised by the *same keystroke* that repopulates the suggestion pills, so the pill row (y 52 to ~95, see `outerLayout.anchors.topMargin`) is the one region on screen that changes at the instant the banner appears, which makes it exactly the wrong place for a button that persists personal data: a click already travelling toward a pill would be caught by a control that did not exist when the user began the movement. The bottom row is static by comparison, and a mis-click landing on a key merely types a character. The `armed` flag closes the same race in time rather than in space, and 400 ms is chosen for imprecise motor input, where a click can land well after the intent formed.

The toast is interactive, which is why its `closePolicy` **must** stay `Popup.NoAutoClose`: every OSK key click is a press-outside, so `CloseOnPressOutside` would slam it shut on the first keystroke (the same trap the prediction-edit popup documents). Its dwell is 8 s rather than the 1.4 s of the sibling confirmation toasts because the user has to read it, decide, and land a click with an imprecise pointer, and timing out calls `dismissSnippetOffer()`, because ignoring an offer is a decision too and without telling the bridge the value would stay "pending" and block the next one.

## Structured Tokens (numbers, phone numbers, email domains)

`NgramPredictor._tokenize` is `re.findall(r"[a-zA-Z']+", text.lower())`. Every digit and every symbol is discarded before a word reaches the vocabulary, which is right for a word model and leaves the engine structurally unable to learn a phone number, a zip, a house number or an email address. Those are among the strings the user retypes most, and each is expensive here: ten digits is ten clicks, and on the compact layouts the digit row is a layer hop away. `src/prediction/token_predictor.py` is that gap and nothing else.

`_press_char` used to end with `if char.isalpha(): _update_predictions() else: <blank the bar>`, so a digit produced no suggestion at all. It now picks between two bars.

### What it is not
A flat count-weighted store of whole tokens, matched by prefix. **No context model** (the useful signal, "this is the number I always type", is already the count), **no fuzzy matching** (correcting a mistyped letter is a favour; "correcting" a digit silently changes a number, and a phone number one digit out is worse than no suggestion), **no capitalisation logic** (irrelevant for digits and domains, already carried in the stored form otherwise). It is deliberately **not merged into the pill ranking**: while the user is part-way through `owen@gm` or `555-123-`, no English word is a plausible suggestion, so the two bars are mutually exclusive rather than ranked, and the bridge picks.

### The two bars
`KeyboardBridge._in_token_context()` decides, off `_raw_token` (not `_current_word`, which resets at `@` and at every dot: see the `_raw_token` note under *Intelligent Spacing*). Two signals, and the asymmetry is deliberate:
- **a digit anywhere**: already outside what the word engine can complete;
- **an `@` that is not the first character**: `owen@` is an address whose only useful continuation is a domain, while `@owen` is a *mention*, which the word model can genuinely complete from a learned name. `tok.find("@") > 0`, not `"@" in tok`.

Two characters minimum (`TokenPredictor.MIN_PREFIX_LEN`): on one character the prefix matches most of the store, so the bar would fill with unrelated numbers on the first digit of anything.

**Every path that repopulates the bar routes through `_refresh_prediction_bar()`**, and that is structural rather than a convention: written out inline at each site the choice was missed twice. The backspace branch was the first, and only half-fixed: mid-address `_current_word` holds only the letters since the last `@` or dot, so it looks like an ordinary partial word, but after any `-`, `.`, `/`, `(` or `@` it is *empty*, so `555-` + Backspace lands in the `elif self._context_buffer` branch, which had no guard at all. `_recase_visible_predictions` was the second: tapping Shift or Caps Lock mid-token threw the whole token bar away. Both are the parallel-blocks failure this file warns about for sticky-modifier release, so the fix is the one prescribed there: one method, every emit through it.

Two related invariants live with it. `_recase_visible_predictions` **returns early on a token bar** rather than routing through the helper: token pills bypass `_display_cased` so there is nothing to recase, and re-emitting would double-count `record_prediction_offered`. And the `_TOKEN_BREAKING_KEYS` branch **refreshes the bar when it clears `_raw_token`**, guarded on `_token_pill_inserts` being non-empty: a pill continues that run, so an arrow key leaves a pill promising a prefix the caret has left (the "on the bar right now" check cannot catch it, because that branch never touched the bar), while an unconditional refresh would fire a prediction query per auto-repeat.

### Which pills are tokens is a set, and the insert text is recomputed
`_token_pill_words` is the **set of pills currently on the bar that insert as tokens**, paired with `_token_pill_typed`, the run they continue. A domain pill reads `gmail.com` while the run before the cursor is `owen@`, so neither `word` nor `_current_word` tells `pressPrediction` what to insert; it dispatches on membership in that set, so a pill can only ever be inserted the way it was emitted, and `_insert_token_pill` derives the text to type from the pill and the typed run. It was briefly a dict of displayed text -> text to type, which read as the authority on what a tap inserts and was not: every read site tested membership only, and the insert path recomputed the value itself because it alone applies the case-sensitivity rule. A stored value nothing validates is a value a later caller will trust. **The dispatch requires the pill to be in `self._predictions` too, and that half is the safety property.** There are a dozen sites in `keyboard_bridge.py` that emit a pill row, so "clear the set at each one" would be one more set of parallel blocks to keep in sync (the failure mode this file warns about for sticky-modifier release), and missing one would leave a stale entry tappable. Requiring the pill to be *on the bar right now* makes that impossible without touching any of them. `_on_predictions_ready` / `_on_predictions_refined` clear the set as well, but only to keep it small; correctness does not rest on them.

**Every pill is strictly longer than the run it continues**, enforced in `_token_suggestions` on both branches (and by `TokenPredictor.predict` for its own half). `_insert_token_pill` relies on it: a pill equal to what is already typed would insert nothing, or select the run and replace it with itself.

**Domain pills show the domain, full learned tokens show the whole token.** Showing `owen@gmail.com` would match how word pills render and would be worse: at 20-odd characters the fitter drops the row to two suggestions (it drops rather than elides, see *Prediction pill widths*), and the local part is already on screen a centimetre away. Within any one row the semantics are uniform, because after an `@` every pill is a domain.

**A tap inserts what the pill displayed, which is not always a suffix.** The store matches case-insensitively, so the pill and the characters on screen can disagree: with Caps Lock on and `OWEN@GM` typed, the pill reads `gmail.com` and a suffix-only insert of `ail.com` leaves `OWEN@GMail.com`, which is neither what the pill promised nor what the user typed, and is then learned back in that corrupted form. `Apt4B` matched from a typed `apt` fails the same way. So `_insert_token_pill` takes the suffix path only when the pill **case-sensitively** continues the typed run, and otherwise selects that run and overwrites it, exactly as `pressPrediction` falls back to `replace_text`. The run being continued is carried in `_token_pill_typed` (the tail after the `@` for a domain, the whole run otherwise), because it cannot be recovered from the pair. `keystrokes_saved` counts the pill minus what was typed, not the length of the retype.

**Token pills bypass `_display_cased`.** It mirrors the typed prefix's capitals onto the pill, which is right for a word and wrong here: under Caps Lock, case 1 would render `gmail.com` as `GMAIL.COM` and then insert it that way.

**A trailing space is appended except after an email or a phone number.** A house number sits mid-sentence (`1247 Main Street`) and a free space is a click saved. An email or a phone is a field *value*: a login form that does not trim rejects `owen@gmail.com ` with a validation error the user then has to notice, diagnose and backspace out of, which costs far more than the one click a wanted space costs. Nothing follows either shape in the same field anyway.

Insert-path invariants mirror `pressPrediction` exactly, and the three that were missed are worth naming because each is a way the mirror can be *nearly* right. `_insert_token_pill` opens with `_begin_verbatim_insert(prose=False)`, the same prologue call every other verbatim insert makes, with `prose=False` because the tap continues the token: the returned deferred space is always empty for `prose=False`, so there is nothing to fold into the insert (unlike `pressPrediction`, which folds an owed capital into the pill text). The `_send_text` runs inside `_without_held_modifiers()`, and the insert itself is never gated on privacy mode (the user tapped it) while everything that persists is. Settling the deferred space with `prose=False` matters because the tap continues the token, so the punctuation was structural and delivering the space would put it inside the number. **Compatibility Mode rewires this too** (BackSpace x len(typed) + full retype): suffix-only insertion and `replace_text`'s Shift+Left selection are both unsafe inside an IDE or an RDP client, which is the whole reason that branch exists in `pressPrediction`.

**The `_context_buffer` update is arithmetic on the join, not on the buffer.** The typed run straddles the two halves of the on-screen mirror: part of it sits in `_context_buffer` and part in `_current_word`, which is never committed. Computing `screen = _context_buffer + _current_word` and replacing the last `len(typed)` characters of *that* is the only formulation that works, and it works for all three send branches at once, because all three leave the same text on screen. Written against the buffer alone it recorded `555-3-4567` for a screen reading `555-123-4567`, and the replace branch additionally chopped `len(typed)` real characters off the front. It then cascaded: the next Backspace rehydrated the corrupted tail into `_current_word`, so the tap after that called `replace_text` with a length that ate real text. Guarded by `TestTokenPillsInsertWhatTheyDisplay`.

**A tapped pill is one sighting, not two.** `_learn_raw_token` is called at the tap, because a domain accepted from the built-in list would otherwise never be learned at all; but emails and phones deliberately withhold the trailing space, so `_raw_token` still holds the completed token when the user's own space retires it again. `_learned_raw_token` records what was last handed to the store and suppresses the repeat; typing or backspacing clears it, so re-typing the same number later still counts. Count is the sort key in `TokenPredictor.predict`, so an unguarded double put pill-accepted tokens (including a built-in domain nobody typed) ahead of hand-typed ones.

**The prediction-pill context menu is suppressed on a token pill** (`isTokenPill`, gating the right-click in `Main.qml`, with `_is_live_token_pill` guarding the four bridge slots as well because QML is free to drift). All four actions are word-model writes: "Show more" pushed a phone number into `unigrams` and `preferred`, which is the word cloud, Top Words, the dashboard's green boosted tags and the backup archive, i.e. exactly what `record_token_prediction_selected` exists to prevent. The other three do not even work in the other direction, since `TokenPredictor.predict` never consults `dispreference` or `blacklist`, and `editPrediction` is word-shaped throughout (it replaces `len(_current_word)`, which is only part of the run, always appends the withheld space, and persists the result into `capitalization`). Forgetting a token is `forgetToken`, in the dashboard's *Saved Numbers & Addresses*.

### What may be learned
`text_patterns.is_learnable_token` lives in that module, beside the other shape rules, because it is the same question ("does this text have a recognisable shape") and two copies of "what does an email look like" is how they start disagreeing. Learning happens at every point `_raw_token` is retired by something meaning *the user finished typing that*: space, Return, and the two punctuation branches **on their non-suppressed path only** (suppressed means intelligent spacing judged the mark to be part of the token, so it is still being typed). **Tab is deliberately excluded**, for the same reason it does not learn `_current_word`: it is the accept-completion key in every IDE and shell, so what precedes it is a prefix the app is about to finish.

**The bar is asymmetric on purpose and was set against hostile examples, not tidy ones.** A missed token costs one suggestion the user never sees; a wrongly-learned one is prefix-matched into the suggestion bar, written to `ngram_model.json`, and carried in the Data Backup archive. So:
- **Long digit runs are rejected wholesale** (`_MAX_LEARNABLE_DIGITS` = 8) rather than by trying to enumerate which identifiers are sensitive. Eight sits *below* a bare nine-digit US Social Security number while clearing every shape worth learning: zip (5), house number (1-6), year (4), IP (8), ISO date (8). Card numbers (16) and account numbers fall out by construction.
- **Real phone numbers run longer than that** and are admitted separately by `is_phone`, which vets the digit *grouping* first (see the `_PHONE_GROUPINGS` comment: `(3,2,4)` is an SSN, `(4,2,2)` a date, `(4,4,4,4)` a card, `(3,3,1,1)` an IP). `_NEVER_LEARNED_GROUPINGS` re-blocks `(3,2,4)` because the generic "has a digit" path below `is_phone` would otherwise let it through.
- **Plain alphabetic words are rejected**: they are the n-gram model's job, and a second, dumber vocabulary in front of the one that does context is a regression.

**Entries are re-validated against the current rule on every load** (`from_dict`), so tightening the rule retroactively cleans an existing store: a file written by an older build, hand-edited, or arriving in an imported archive is not a reason to start offering an SSN. Malformed entries are dropped individually rather than rejecting the file: this rides in `ngram_model.json` alongside the vocabulary, and one bad token is not a reason to lose someone's learned words. **Load strips before it validates, exactly as `learn` does**, and the order is the point: validating the stripped form while storing the raw one let the store keep `555-1234.`, admitted on the strength of `555-1234` and then offered as a pill that types a stray full stop into the number. Counts merge rather than overwrite, so a file carrying both written forms keeps both sightings instead of whichever iterated last.

### Analytics and the log
Selections go through `TypingAnalytics.record_token_prediction_selected(rank, keystrokes_saved)`, **not** `record_prediction_selected`, which feeds its argument into `word_freq`. That table is persisted, surfaced as the dashboard's "Top Words" and carried in the backup archive; a phone number belongs in none of the three, least of all on a dashboard the user might screen-share. The word counter is skipped for the same reason it should be: a zip code is not a word, and counting it would inflate WPM. **Nothing in this path logs token content**, the same rule as the rest of the keystroke path, and every argument here is typed content by construction.

### Storage
Persisted in `ngram_model.json` under a `tokens` key, not a file of its own. That file is already the "everything the user taught us" store (capitalisation, blacklist, boosts), already in the Data Backup archive, and already has load-time size caps; a separate file would have needed all three re-established **and** a `_MODEL_FILES` change to the export. Absent from every model saved before this existed, which `from_dict` reads as an empty store. `clear_user_data()` clears it too: "clear my learned data" has to mean all of it. Bounded at `TokenPredictor.MAX_TOKENS` (2 000), evicting least-seen first with ties breaking toward the *longer* token (it cost more clicks to type, so it is worth more as a completion).

### Seeing and forgetting what was learned

*Dashboard -> **Saved Numbers & Addresses***, one removable tag per learned token, driven by `getLearnedTokens()` and `forgetToken(token)`.

This is not a nicety, it is the other half of the admission rule. That rule is a **shape** test, not a judgement about sensitivity: it accepts any short run carrying a digit, which is also the shape of `hunter2`, `Tr0ub4dor`, `AB1234567` (a passport) and `sk-abc123def` (an API key). Password auto-detection **fails open** by design (no AT-SPI on Linux, no TCC grant on macOS, any field UIA does not mark), so the store must be assumed to see a password eventually. Rejecting every letters-plus-digits token would close that, and was tried and backed out: it also drops `Apt4B` and `v1.2`, and no rule keeps one while dropping the other because they are the same shape. **The project's position is that the recall is worth more, on the condition that a wrongly-learned token is visible and individually removable.** Delete this section and that trade stops being defensible.

It is also the only window the store has onto itself. Learned *words* surface in the word cloud, the flow graph and Top Words; a phone number or an email deliberately reaches none of the three (see the analytics note above), so before this the only answer to "what has it remembered" was Clear Learned Data, which throws the vocabulary away too. `TokenPredictor.forget` had existed the whole time with nothing in QML calling it.

`forgetToken` deliberately does **not** log, unlike its `unblacklistWord` siblings: those take a dictionary word, this takes whatever the user typed, and the diagnostic log is what gets attached to bug reports.

Guarded by `tests/test_token_predictor.py`, `tests/test_text_patterns.py::TestLearnableToken`, and `tests/test_keyboard_bridge.py::TestStructuredTokenPredictions` / `TestEmailDomainSuggestions` / `TestLearnedTokensCanBeSeenAndRemoved` / `TestTheTokenBarSurvivesEveryWayTheBarIsRepopulated` / `TestTokenPillsInsertWhatTheyDisplay`. Every positive case is paired with the near-miss it must reject, and the pairs that bite are the SSN family, not the tidy ones.

## Dictation (voice input)

Click the mic at the **left end of the suggestion bar**, speak, click again. The live transcript renders in the bar itself; each finalised phrase is typed into the focused app through the verbatim-insert path. Off by default and inert without a Deepgram API key. Full write-up: `docs/architecture/DICTATION.md`. Code: `src/dictation/` (`config` / `audio` / `providers` / `controller`), `KeyboardBridge._insert_dictated_text` + the `setDictation*` slots, `qml/Main.qml` (mic button, transcript banner, `dictationErrorToast`), the Dictation category in `UnifiedSettingsPanel.qml`.

- **Built entirely on Qt, and that is a constraint rather than a coincidence: zero new Python dependencies.** `QAudioSource` (QtMultimedia) captures and **resamples for us**, so the recogniser always sees 16 kHz mono signed-16-bit PCM whatever the device natively runs at, and `QWebSocket` (QtWebSockets) speaks to the provider on the Qt event loop, so there is no worker thread, no asyncio, and no queue between the audio callback and the socket. Both already ship in the PySide6 wheel the build bundles. `sounddevice` would drag PortAudio in as a binary for PyInstaller to carry and the EV cert to sign; MacroVox sends the device's native rate and declares it, which works but makes every recogniser parameter a variable. Don't "simplify" either back.
- **`predBar.micReserve` must stay 0 whenever the mic is not on screen.** `predRow.x` centres the pills in `width - micReserve - clearCtxReserve`, so a zero reserve collapses that expression back to exactly the single-reserve one it grew from, and a user who never enables dictation gets byte-identical bar geometry. `computeFit` is passed `clearCtxReserve + micReserve` as its existing single `reserve` argument rather than gaining a tenth parameter: the fitter has no notion of which *side* a reserve sits on, only how much of the bar the pills may not have.
- **There is a title-bar mirror** (`dictationTitleBarButton`), visible only when `suggestionsEnabled` is false, for the reason `snippetsTitleBarButton` exists: the bar collapses to zero height with that setting, and an unrelated setting must not remove the only way into a feature. Both mic icons are Feather's `mic` through `StrokeIcon`, never a glyph.
- **The mic's busy pulse drives a `pulse` property, never `opacity` directly.** `SequentialAnimation on <property>` takes ownership of what it animates and never hands it back, so animating `opacity` destroyed the `ready ? 1.0 : 0.45` binding on the first connect and left the button parked wherever the loop was, which for half of each cycle is nearly transparent. A property with no binding of its own is what an animation can own safely; `opacity` multiplies it in and `onRunningChanged` resets it. Applies to any future animation on a bound property.
- **Insertion opens with `_begin_verbatim_insert()`, the same verbatim-insert prologue every other insert path calls**: `_release_sticky_modifiers()` **before** the send, a deferred auto-space settled with `prose=True`, `_consume_auto_cap()` (a dictated phrase is the next thing typed, so it takes the capital), then the send itself inside `_without_held_modifiers()` via `_send_literal_text` (so a right-click lock survives), and **the insert itself never gated on privacy mode**. It does not go the extra step through `_commit_verbatim_insert` (as `insertSnippet` / `insertGlyph` do), because its tail differs: it mirrors the insert into `_context_buffer` / `_sentence_buffer` and decides a leading space, both dictation-specific. One space is prepended between phrases, decided from what is on screen (`_context_buffer + _current_word`) rather than from a "have I inserted yet" flag, because Deepgram returns phrases with no surrounding whitespace.
- `_context_buffer` / `_sentence_buffer` are appended to so dictation's own contribution is reflected: they are what the insert path *measures against*, so a buffer disagreeing with the screen is how a later pill tap eats real text. **The deferred auto-space is not re-added there** (`_take_deferred_space` already mirrored it, and appending it again put two spaces in the buffer where the screen has one), and the append is **suppressed in privacy mode**, exactly as `pressPrediction`'s own append is. The prediction model is deliberately **not** taught from dictated words (they came from Deepgram's vocabulary, not the user's typing).
- **Privacy mode calls `cancel()`, not `stop()`**, and the difference is the point: `cancel` does not wait for the provider to flush, so a run under way when the caret landed on a password field cannot deliver one more sentence. **`cancel()` disconnects the controller's own handlers before aborting the stream, and the disconnect is the load-bearing half**: clearing `self._stream` only stops us *sending*, not the provider *telling us* things, and a websocket can have a `textMessageReceived` already queued at teardown. Without it a cancelled run typed one more phrase into whatever field the user had moved to. Found by `tests/test_dictation.py::TestTheRunLifecycle::test_cancelling_drops_what_is_in_flight`.
- **`dictation.json` is deliberately absent from the Data Backup archive** (do NOT add it to `_MODEL_FILES`), because it holds an API key and the archive exists to be carried between machines. The key is DPAPI-wrapped on Windows via ctypes, plaintext at 0600 elsewhere, never logged, never returned to QML in the clear (`getDictationSettings` gives `hasKey` plus a masked preview), and never in the websocket URL (it rides in an `Authorization` header, so no proxy log captures it). `TestTheKeyNeverLeavesTheMachine` asserts the export exclusion.
- **Toggle, not push-to-talk**, same argument as the swipe-typing removal: a sustained precise hold is the one gesture this user cannot reliably make. Two automatic stops sit behind it (a silence timeout the user notices, and a wall-clock ceiling that is the backstop for the silence detector itself failing in a noisy room), because a missed "off" click leaves the mic live and, on a metered API, billing.
- **Four states, not a boolean** (`idle`/`connecting`/`listening`/`finishing`): `connecting` and `finishing` both have to look busy **without looking like recording**, or the user clicks again and starts a second run. `finishing` is not cosmetic: `stop()` closes the mic immediately but keeps the socket open for the `CloseStream` flush, because the final fragment for the last thing said arrives *after* that message.
- **No automatic reconnect, on purpose.** Audio spoken during a gap is gone, so a silent reconnect yields a transcript with an invisible hole: words the user said, believes they said, and cannot see. A drop ends the run and says so. Errors are mapped to sentences naming a next step (`_friendly_error`); nobody can act on `QAbstractSocket::RemoteHostClosedError`.
- **Nothing in `src/dictation/` logs transcript content**, same rule as the rest of the keystroke path.
- Deepgram parameters were taken from MacroVox's production set: `model`/`punctuate`/`smart_format`/`interim_results`, with **`keyterm`** for custom vocabulary (nova-3 rejects the legacy `keywords`).

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

Caps Lock and Shift are **independent toggles**. Toggling caps no longer also flips shift. Both are surfaced separately to QML (`capsLockActive`, `shiftActive`).

- **Uppercase output** in `pressKey`: `key.upper()` if `_shift_active OR _caps_lock_active`.
- **Upper layer**: `_update_layer()` switches to `"upper"` if `_shift_active OR _caps_lock_active`. Same for the displayed glyph in `Main.qml`.
- **OS-level hold**: `toggleShift` calls `_synth.hold_modifier("shift")` / `release_modifier("shift")` so the OS sees Shift physically held while the toggle is active. This is what makes Shift+click and Shift+drag in the target app extend the text selection - same as the Windows on-screen keyboard. Without it, Shift only attached to synthesised keystrokes as a chord modifier and a click between toggle and the next typed character would land without Shift held.
- **Auto-release**: Shift auto-releases after a single keypress; caps stays on until explicitly toggled. Auto-release paths also call `release_modifier("shift")` so the OS-held shift drops together with the Python state. Caps is unaffected by the auto-release path.
- **Visual highlight**: only the toggled key is highlighted - toggling caps does NOT also highlight the Shift key (it used to, that was a bug).

The shifted *glyph* on a key (e.g. `!` on the `1` key) follows shift only - caps lock uppercases letters but does not pick the shifted variant of symbol/number keys, matching standard keyboard behavior.

### Caps Lock and the prediction bar

When Caps Lock is on, the prediction pills also render uppercase. The pills must match what the user is typing *and* what the pill will insert when clicked - showing "hello" while the user has typed "HELL" and then inserting lowercase next to the uppercase prefix was the pre-fix bug. Implementation: `KeyboardBridge._display_cased()` uppercases the engine's output when `_caps_lock_active`, and every emit site (`_on_predictions_ready`, `_on_predictions_refined`, next-word-after-selection, `editPrediction`) routes through it. `toggleCapsLock` re-queries the engine so currently-visible pills flip case immediately - we can't just `.upper()` / `.lower()` the stored list in place because once "iPhone" becomes "IPHONE" the original casing is lost.

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
- **Modifier handling in edit mode**: shift/caps still apply to letter case, and Shift auto-releases after one keypress the same way it does outside edit mode. **A Ctrl/Alt/Win chord acts on the field or does nothing at all; it never reaches the app behind us.** `_EDIT_CHORDS` maps Ctrl+a/c/v/x/z/y onto `editSpecialPressed("selectall"/"copy"/"paste"/"cut"/"undo"/"redo")`, which both edit surfaces handle; every other chord is swallowed. This used to be described as "ctrl/alt/win are ignored", and only half of that was true: the modifier was skipped and the **letter inserted**, so Ctrl+A in the snippets editor typed `a` and Ctrl+B typed `b`. The clipboard four are what make this worth wiring rather than merely swallowing, and the reason is the whole premise of the app: every character of a long address costs a click, so pasting one in from elsewhere is the difference between a snippet being worth making and not. The chord path calls `_release_edit_chord_modifiers()`, the chord-path entry point into the shared `_release_sticky_modifiers(("ctrl", "alt", "win"))` call (see *Sticky Modifiers*); a right-click lock still outranks it.
- **"Saved" confirmation toast**: a small green popup at the top of the window flashes "Saved" (with a checkmark) for 1.4 s after a successful save. Triggered from all three save paths (checkmark button click, Return-key in edit mode, TextField `onAccepted`). The save itself was always synchronous - `set_capitalization` updates the dict immediately and `aboutToQuit` writes it to `ngram_model.json` - but with no UI feedback the user couldn't tell it stuck without quitting and relaunching. Any new save path must also call `editSavedToast.flash()` or the user will think their edit was lost.

If you add a new input source (e.g. a voice-dictation slot, another popup with its own TextField), the pattern is: set edit mode on open, listen to `editKeyTyped` / `editSpecialPressed`, clear edit mode on close. Don't try to route through Qt focus - `WS_EX_NOACTIVATE` / `WindowDoesNotAcceptFocus` prevent our window from holding OS focus, so physical keyboard input and synthesized input both go to whatever app was focused before we opened.

## Removed: Swipe / Glide Typing

Swipe typing (drag across letters to type a word in one gesture) was removed; issue #39 carries the reasoning. It is not a candidate for a quiet reintroduction.

What went with it: `src/prediction/swipe_recognizer.py`, `qml/components/SwipeOverlay.qml`, `KeyboardBridge.setSwipeEnabled` / `setSwipeLayout` / `processSwipe`, the `charKeyRegistry` / `tappableKeyRegistry` pair plus `registerCharKey` / `pushSwipeLayout` in `Main.qml`, the `registerFn` plumbing in `NumberRow.qml` / `FunctionRow.qml`, `KeyButton.externalPress` / `externalRelease` / `externalHoldPress`, the settings toggle, and `docs/architecture/SWIPE_TYPING.md`. All of it is recoverable in full from the commit before the removal.

Two things worth knowing before any future gesture feature, because both were learned expensively:

- **The overlay was the cause of issue #15, not merely where it surfaced.** It covered the keyboard rows and took *every* press inside its bounds, then resolved it against a registry, so any key missing from that registry was a dead tap. That bill was paid three separate times (the main grid, the Number Row's Esc, every F-key), and every new control near the grid had to remember it. An interceptor that owns every press is the design flaw; the registry was the patch.
- **The two registries were not duplication.** `charKeyRegistry` held the recogniser's key centres, single characters only, because a "Backspace" centre is a phantom letter in every shape match; `tappableKeyRegistry` held every key for hit testing. Collapsing them into one list is what caused #15, and widening the char filter to fix the taps corrupts decoding instead. Any replacement meets the same fork.

The premise to re-examine first is the one this feature skipped: a sustained, precise drag is the single gesture a mouse-driven user with imprecise motor control cannot reliably make, which is why every other input path here is a click.

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

**Right-clicking** Shift / Ctrl / Alt / Win **locks** it held down: the modifier stays held at the OS level and is **exempt from the per-keystroke auto-release**, so the user can fire several combos (Ctrl+C then Ctrl+V) or hold Shift across a whole selection without re-tapping. This is the accessibility answer to "hold the key down" for a mouse-driven OSK. Right-click again, or plain left-tap, to release. **Caps Lock is not lockable** (it's already a persistent toggle). Right-click-to-lock on a modifier is **independent of the "Right-Click for Shifted Character" setting** (a modifier has no shifted variant, and the whole point of the gesture is holding it).

State model: each modifier keeps its existing `_*_active` (held-at-OS, drives the highlight + chord logic) plus a new `_*_locked` flag. **Locked always implies active.** `lockModifier(name)` (bridge slot, called from QML `onKeyRightPressed` for `type === "modifier"` keys) toggles the lock: locking sets active+locked and holds at OS (only if not already held, so locking an already-sticky-active modifier doesn't re-send a key-down); unlocking clears both and releases. The sticky `toggleX()` paths call `_clear_lock(name)` when they turn a modifier off, so a left-tap on a locked key also clears the lock (easy way out). The lock check lives in one place now, `_release_sticky_modifiers`'s `getattr(self, f"_{name}_locked")` guard, and every keystroke path (the edit-mode intercept, the Ctrl/Alt/Win chord branch, the char-path end, `_release_edit_chord_modifiers`, and `pressSpecialKey`, the last two alongside the nav-key `keep` exception) calls through it rather than each re-checking the flag inline. Miss the call and a locked modifier would silently drop after a keystroke. `shutdown()` releases locked modifiers too (and clears the flags) so quitting never pins one desktop-wide.

QML surfaces the lock via `shiftLocked` / `ctrlLocked` / `altLocked` / `winLocked` bridge properties (+ `*LockedChanged` signals), bound in `Main.qml` and mapped per `kd.stateKey` onto `KeyButton.isLocked`. Because locked implies active, a locked key already carries the accent fill a sticky one-shot has; the lock adds a **solid 3 px bar along the bottom edge** (`lockBar` in `KeyButton.qml`) on top of it, so the two states differ by one unmissable mark rather than by a whole second colour scheme. The bar is inked with `KeyButton._onFillColor`, the shared luminance rule that also picks the key label's colour on an active/pressed fill: dark on a bright accent, white on a dark one. Nine themes ship, several with a pale accent (Blackboard, Spaceship) and one light outright (Typewriter), so any fixed colour is unreadable on roughly half of them. It is inset horizontally past the keycap's corner radius because `clip: true` clips to the bounding rect, not the rounded shape, so a full-bleed bar pokes out past the curve.

This replaced a hardcoded gold 2 px ring plus a 15x15 gold badge holding a 9 px 🔒. **Don't reintroduce an emoji on a keycap**: at that size the padlock is a smudge, and Windows renders it through Segoe UI Emoji as a *colour* glyph, which ignores the `color` property outright, so what shipped was a yellow blob. Any glyph small enough to fit on a keycap is at the mercy of the host emoji font.

**The same applies to any icon-sized glyph, and the clear-context (circle-arrow) button is the second case of it.** That button used to render U+27F2 in a `Text` with `anchors.centerIn`, which centres the text *item* while the ink inside it sits wherever the font puts it: the ring the eye reads sat down-and-right of the circle it lives on, with the glyph's tail hanging out to the left. It now draws **Feather's `rotate-ccw`** (MIT, see `THIRD_PARTY_NOTICES.md`) from its published path data.

Three things about that are load-bearing. **It goes through `ctx.path` on the `Canvas` that was already there**, because QML's Canvas takes SVG path data directly: `QtQuick.Shapes` and `QtSvg` would each render it just as well and would each add a QML module the frozen build has to carry, and a missing QML module does not degrade, it fails `Main.qml` and ships as a blank keyboard. **The path data is kept verbatim** (the source's `polyline` written as the equivalent `M1 4 L1 10 L7 10`) so the icon can be diffed against upstream. And **the icon is centred by ink, not by viewBox**: Feather puts the corner arrow outside the ring, which leaves the composition's ink one unit left of the 24-unit box's centre, so `inkOffsetX` corrects it. That offset was measured from a render, not derived by eye.

Guarded by `tests/test_qml_prediction_bar.py::TestTheClearButtonIcon`, which is worth reading before adding an assertion here, because **two successive metrics were wrong**. The ink bounding box is centred in the *glyph* version too, to within half a pixel, since the tail hanging off one side cancels the ring being pushed to the other, so a bbox assertion alone would have passed against the bug it was written for. A radial-spread metric replaced it and did separate them (glyph 0.29, hand-drawn arc 0.09, bar 0.15) but had to go as well: Feather's arrow sits outside the ring by design and scores 0.147, and a test passing by three thousandths is not a test. What is left is the pair that is honest about what the code guarantees: the icon is *drawn* rather than typeset (this is what catches the glyph), and it is grossly centred within 3 px (this catches a dropped transform, verified at -8 px). The one-unit optical correction is deliberately not pinned: asserting it across renderers buys a flake, not a guard.

**Synth invariant (load-bearing: the lock is worthless without it).** Keeping the bridge state "held" isn't enough; the OS modifier must physically stay down. `hold_modifier` puts it down, but `send_key`/`replace_text` used to *wrap* the action key with a modifier down+up, and that trailing key-up silently released a held modifier after the first keystroke (mouse Ctrl+click / Shift+drag / Alt+Tab then broke even though the key still showed it held). Fix: **`WindowsKeySynthesizer.send_key` and `replace_text` skip wrapping any modifier that is already physically held** (`_modifier_already_held` → `GetAsyncKeyState`), relying on the standing hold instead. This mirrors the pre-existing `shift_already_held` guard in `_make_char_scancode_events`. Any new synth path that wraps a modifier around a keystroke must apply the same guard, or a locked (or sticky) modifier will drop. Mirrored on the `cpp-rewrite` branch (`WindowsKeySynthesizer::modifierAlreadyHeld`, used by `sendKey` + `replaceText`); no C++ is tracked on `main`. Covered by `tests/test_platform.py::TestWindowsSendKeyPunctuationChord::test_already_held_modifier_is_not_wrapped` and `TestWindowsReplaceText::test_held_shift_not_wrapped_in_selection`.

**Parity**: mirrored 1:1 on the `cpp-rewrite` branch, not on `main` (`KeyboardBridge::lockModifier` / `clearLock`, the `m_*Locked` members, the guarded `releaseStickyAll()` + `pressSpecialKey` + edit-mode blocks, and the `*Locked` Q_PROPERTY/signals). Bridge behaviour is covered by `tests/test_keyboard_bridge.py::TestModifierLock` on the Python side.

## Settings Panel Structure

`UnifiedSettingsPanel.qml` is a drill-down menu, not a long scrolling list. The home view shows five category cards; clicking a card swaps the body to that category's sub-view. The header swaps in a back arrow (<) and the category title; the close X stays put.

State is held in a single string property: `currentView` is one of {`"home"`, `"appearance"`, `"typing"`, `"dictation"`, `"model"`, `"data"`}. The Flickable contains six sibling `ColumnLayout`s, each with `visible: unifiedSettings.currentView === "<id>"`; only one renders at a time. Scroll position is reset to the top on every view change (a `Connections` block on `currentView`) so a drilled-in view never opens mid-section.

The parent (`Main.qml`'s settings popup window) calls `settingsPanel.resetToHome()` in `onVisibleChanged` so re-opening Settings always lands on the home grid, not whatever sub-page the user last visited. Don't break that - landing on a deep page reads as "the menu changed."

### Where each section lives

| Top-level | Section | What's inside |
|-----------|---------|---------------|
| **Appearance** | Panels | Compact View / Function row / Navigation / Numpad toggles. Compact View leads the section because it gates the two below it: it forces Navigation + Numpad off (restoring them on exit) and renders their toggles disabled. There is no Number Row toggle - `Main.qml::showNumberRow` derives from whether the active layout JSON already carries a `number` row, so the standalone panel appears exactly on the compact layouts, which lack one. |
| | Keyboard Layout | qwerty / dvorak / colemak picker (compact variants are filtered out - see *Compact View*) |
| | Theme | 9-theme color picker |
| | Sound & Opacity | Key click sound, opacity slider |
| **Smart Typing** | Suggestions | Show suggestions, auto-space, intelligent spacing, auto-cap, max count |
| | Suggestion Engine | Merge strategy 4-card picker (rank / rrf / linear / loglinear) |
| | Input | Right-click shift, key preview popup, Compatibility Mode picker, repeat delay & interval |
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
- **Branch protection requires the `Tests` job, not the shards.** Required
  checks are configured by name in the repo settings, so naming shards there
  means reconfiguring protection on every change to the shard count, and a
  stale entry blocks every PR for ever on a check that can never report.
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

Six independent signals, each catching what the ones before it cannot. The first three are polled by `_check_foreground_window` at 4 Hz:

1. **The foreground window** (`GetForegroundWindow` / `xdotool getactivewindow`). Catches an app switch.
2. **The focused element** (`focused_element_token`, UIA RuntimeId, Windows only). Catches the caret moving between two controls *inside* one window, e.g. two text boxes on a web page.
3. **The caret position** (`caret_position_token`, `GetGUIThreadInfo` on the foreground thread, Windows only). Catches what (2) structurally cannot: a move *within the same control*, such as clicking from one paragraph to another in a single text box, and the common case where UIA reports one element for a whole web document so two fields share a RuntimeId.
4. **A click outside our own window** (`external_click_detected` in `src/platform/pointer.py`, polled by `_check_external_click` on its own 50 ms timer, Windows only).
5. **Tab** (`pressSpecialKey`). Not polled and not platform-specific: the user told us they are moving to the next field, so `_reset_typing_context(keep_snippet_offer=True)` runs directly. It is the same failure as (4) arriving through the keyboard instead of the mouse, and the only one of the five that works identically on every platform. The word in progress is deliberately **not** learned first: Tab is the accept-completion key in every IDE and shell, where `_current_word` is a *prefix* the app is about to finish, so learning it would feed the model `hel` every time the user completed `hello`. Guarded by `tests/test_keyboard_bridge.py::TestTabClearsContext`, whose inverse half asserts the Tab keystroke still reaches the app (the reset must not swallow it).

6. **The cursor-motion keys** (`_NAV_KEYS`: arrows, Home, End, PageUp, PageDown, in `pressSpecialKey`). Same reasoning as (5) and the same call, `_reset_typing_context(keep_snippet_offer=True)`. This branch already cleared `_raw_token` on these keys and stopped there, which applied the reasoning to half the state and left `_current_word` / `_context_buffer` describing text the caret has left. **The cost of being wrong here is not a bad suggestion.** Those two buffers are what the insert path measures against: a pill types only the tail it believes is unseen, and otherwise selects `len(_current_word)` characters backwards and overwrites them, so a stale context can eat text the user typed elsewhere. Clearing the bar in the same breath is what separates this from the mid-word reset that `_check_caret_moved` deliberately avoids: that one leaves a live bar and a partial word on screen, which is how a tap inserts a whole word beside its own prefix. **Delete and Escape are deliberately excluded** even though they are in `_TOKEN_BREAKING_KEYS`: Delete removes the character *after* the caret, so the run before it is untouched, and Escape does not move the caret at all. Guarded by `TestCursorMotionClearsContext`, including the auto-repeat case (a held arrow must reset once, not per repeat) and the Delete inverse.

Both (2) and (3) treat `None` as "don't know" and leave state untouched, so a transient failure never wipes context. That is correct in isolation and adds up to a hole: browsers and Electron apps expose one UIA element for a whole document *and* publish no caret, so both fail closed at once and clicking from one field to another in a single window had no signal at all. That is exactly the case (4) exists for, and it is why the fallback is a click rather than a better token: a click is observable in every app, no accessibility cooperation required.

(4) is deliberately coarser than the signals it backs up. A click on a toolbar button or a scrollbar does not move the caret in the text, and resetting there costs the next-word prediction the user would have got. That trade is worth taking because the failure it replaces is worse: context describing a field the caret has left produces pills that insert the wrong text into the field it is now in. It deliberately does **not** carry `_check_caret_moved`'s **only between words** guard; see the paragraph below for why that was reversed.

**Where a caret is published, the two kinds of click are now told apart** (`_begin_click_settle` / `_continue_click_settle`), and the interesting part is the timing rather than the comparison. The obvious implementation reads `caret_position_token()` when the press is seen and skips the reset if it has not moved; that silently undoes the whole signal, because the poll is up to `_CLICK_POLL_MS` behind the press and the target app may not have handled it yet, so the read reports the old caret for a click that is about to move it. The decision is therefore settled over `_CLICK_SETTLE_MS` (200 ms, four ticks) and any change inside that window counts as a move. **The baseline is `_caret_before_click`, the previous tick's reading**, which is the only one that reliably predates the press, since the press is itself detected as a transition against that tick. `_last_caret_token` cannot serve: it belongs to the 4 Hz foreground poll, which lands between a press and its detection in a 50 ms window out of every 250 ms, and when it does it already holds the *post*-click value, so a genuine move reads as "unchanged". **An unreadable caret resets, on the way in and on the way out**, which is what keeps browsers and Electron (the case this signal was invented for) byte-identical to resetting on every press. The 200 ms window is not reachable as a stale bar: taking it would mean moving the pointer off whatever was just clicked, back onto a pill, and clicking again. Guarded by `TestAnOutsideClickThatMovesNoCaretIsLeftAlone`, which drives the poll tick by tick because a version that decides immediately passes any test that only inspects the end state.

**Three things the window has to be told about, and each is a way to get it nearly right.** (1) **Our own inserts move the caret**, so this poll carries the same guard `_check_caret_moved` does, in a flag of its own: `_note_own_keystroke` sets `_keystroke_since_poll` *and* `_keystroke_since_click_poll`, because each poll consumes the flag it reads and a shared one would mean whichever fired first ate the evidence the other needed. Inside the window a keystroke **re-baselines rather than concluding** (a settle only opens on a click that did *not* move the caret, so a keystroke landing in it is evidence about our own insert and none about the click); at the *entry* comparison it deliberately does not apply, since there the caret has already changed by the time the click is seen and believing our own keystroke did it would mean keeping a context that may belong to the field the click just left. (2) **A settle is cleared by `_reset_typing_context`**, or an app switch would leave the next tick comparing the new app's caret against the old app's baseline, calling it a move and quietly re-opening (with `keep_snippet_offer=True`) a decision the switch had already made the other way. (3) **A scrollbar is not among the clicks this rescues.** `rcCaret` is client-relative, so scrolling drags it while the caret stays exactly where it was in the text and the click reads as a move; that is the same false positive `_check_caret_moved` carries its only-between-words guard for, and taking that guard here was tried and reversed for a stronger reason, so a scroll keeps the coarse behaviour and resets. Closing it needs a caret identity that survives a scroll, which Windows does not publish.

**(4) fires mid-word, and that is a reversal of the original design.** It used to carry `_check_caret_moved`'s only-between-words guard, and mid-word clicks were held in an `_external_click_pending` flag to be acted on at the next word boundary. Both are gone. The guard suppressed the reset at the moment it was most needed, because a partial word is exactly when the bar is full of completions for the field the caret has just left: with `hel` typed and the caret clicked into another field, tapping the `hello` pill sent `lo ` into that field (measured, not theorised). Deferring to the word boundary did not rescue it either, since that boundary only arrives once the user finishes the word, by which point the wrong text is in. The flag is deleted rather than left inert: a reset that always happens immediately has nothing to hold.

What the guard protected is real but rare **here specifically**: a click that does *not* move the caret desyncs `_current_word` from the screen, so a later pill completes against a prefix that is only part of what is there (`hel`, then a typed `lo`, then a tapped `look`, gives `hellook`). Reaching that costs leaving the keyboard, clicking something caret-neutral in another app, and returning mid-word, because mid-word the pointer is on the keyboard and own-window clicks are filtered by process id. Clicking into another field mid-word is ordinary. Both directions corrupt text; this one corrupts it far less often. **`_check_caret_moved` keeps its own guard** (see below), because scrolling drags the caret rectangle without moving the caret in the text and that false positive lands mid-word constantly.

**A live snippet offer survives every *within-window* reset.** `keep_snippet_offer=True` is passed by all three of the outside click, the focused-element change and the caret move; the app switch and privacy mode take the default and still withdraw. The offer describes a value the user typed rather than a caret position, and clicking the next field of the same form is the single most likely thing to happen right after an email address is typed, so withdrawing there closed the Save button before the user could travel to it.

**It has to be all three, and that follows from the reversal above rather than being an independent choice.** Clearing `_current_word` on the click is exactly what unlocks `_check_caret_moved`'s only-between-words guard, so a click that kept the offer was followed within 250 ms by a caret poll that reset again on the default and withdrew it anyway; the focused-element branch had no `_current_word` guard at all, which is the Chrome case. Each of these polls is testable in isolation and proves nothing that way, so `TestOutsideClickClearsContext::test_the_offer_survives_the_caret_poll_that_follows` drives two signals in sequence, which is the shape any future test here needs.

**The tail of an interrupted word is never learned** (`_word_prefix_lost`, taken by `_take_lost_prefix`). This is the second cost of dropping the guard and the one that lasts. A reset landing mid-word leaves the word's opening on screen with nothing tracking it, so `docu` plus a caret-neutral click plus `mentation ` handed `mentation` to `_predictor.learn` as a whole word: measured after three such cycles it sat in `user_vocab`, in the analytics word table, and at rank 1 for every later `ment`, and from there it travels into `ngram_model.json`, the dashboard's Top Words and the Data Backup archive. The desync the paragraph above weighs is transient; this is not. The flag travels with `_word_typed_under_caps_lock` (same per-word lifetime, cleared at the same boundaries) and is consumed at the three sites that learn from `_current_word`: space, sentence punctuation and Return. The two mid-word punctuation branches gate the `_sentence_buffer` append instead, because that buffer is what the *next* boundary hands to the learner, so a fragment parked there arrives one keystroke late rather than not at all. The tail still reaches `_context_buffer`, which has to mirror the screen. Guarded by `tests/test_keyboard_bridge.py::TestAnInterruptedWordIsNotLearned`, where every case types its word **three** times, because an unknown word only promotes into `user_vocab` on its third sighting and a one-shot version of those assertions passes whether or not the gate exists.

Two implementation notes on (4), both load-bearing. **Clicks on our own window are filtered by process id**, not by geometry: `WindowFromPoint` -> `GetWindowThreadProcessId` compared against `os.getpid()`, which covers the keyboard, the snippets window and every popup in one check, and is what stops the keyboard clearing its own context on every key tap. And **it polls rather than hooking**: `WS_EX_NOACTIVATE` keeps our window off the focus path so Qt never sees the event, and a `WH_MOUSE_LL` hook would put this process on the input path of every mouse event on the desktop, which is a latency and antivirus-heuristic cost out of proportion to a signal this coarse. Polling reads the pointer's *current* position, so the 50 ms interval is part of the correctness argument, not a tuning knob: the longer the gap, the more chance the pointer has already travelled back onto a key and reads as ours. **Only `GetAsyncKeyState`'s high bit is read** (`_left_button_pressed_since_last_call`), as a transition against the previous poll. The low bit ("pressed since the last call") would catch a click shorter than one poll interval, which the high bit structurally misses, and reading it is still the wrong trade: that bit is system-wide and **the read clears it**, so polling 20x a second silently steals every press from anything else watching the same way. Dwell-click and switch-access utilities are exactly the software an on-screen keyboard user runs alongside this one, and degrading another assistive tool to sharpen a signal this coarse is not worth it. A missed click costs one next-word suggestion, and the next click resets anyway. `tests/test_pointer.py::TestPressDetection::test_the_pressed_since_last_call_bit_is_ignored` asserts the *absence* of that detection, so restoring the bit fails loudly.

`_check_caret_moved` carries two guards, and both matter:

- **Typing moves the caret too.** `_keystroke_since_poll` is cleared on each poll, so a move we caused is never mistaken for one the user made. It is set in the bridge's synthesizer wrappers (`_send_key` / `_send_text` / `_replace_text`), **not** at the keystroke entry points: a tapped pill and a snippet both type without going through `_press_char`, so keying it off the entry points made this poll read our own insert as the user clicking elsewhere and tear down the context, and the freshly emitted next-word pills, within 250 ms of producing them. Setting it at the synth layer covers any future insert path by construction. Guarded by `TestCaretMoveClearsContext::test_our_own_inserts_do_not_trigger_it`.
- **Only between words** (`_current_word` must be empty). A reset mid-word is the dangerous direction: it clears `_current_word` while the partial word is still on screen, so the next pill tap inserts the whole word beside it, which is the "backspacbackspaces" duplication the rehydrate logic exists to prevent. Scrolling also drags the caret rectangle across the screen without the caret moving in the text, and that is the false positive most likely to land mid-word. Waiting for a word boundary costs the mid-word case, where a stale context matters least because the user is about to finish the word anyway. **The outside-click signal (4) no longer shares this guard**: there the mid-word case is the common one and the caret-neutral false positive is rare, so the trade lands the other way round. Don't re-unify them.

**`resetContext` (the clear-context ring) delegates to `_reset_typing_context` rather than clearing the same fields itself.** They were two hand-written copies of one field list and had drifted in *both* directions: the ring cleared `_learned_raw_token` and the shared reset did not, while the shared reset cleared `_pending_auto_cap` and the ring did not, so typing `hello.` and tapping the ring left a capital owed and the next character came out uppercase in a context the user had just told the keyboard to forget. That is the parallel-blocks failure this file documents for sticky-modifier release, and the fix is the one prescribed there: one method, every caller through it. Guarded by `TestTheClearContextRingClearsEverything`.

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

A denser 13x4 keyboard for small screens. Off by default; toggle in *Settings ->
Appearance -> Panels -> Compact View*. The design, the measurements behind it,
and the full rationale for every rule below live in
`docs/architecture/COMPACT_VIEW.md`.

Load-bearing rules:

- **Every row in a compact layout must total the same unit count** (13.0 for
  `qwerty-compact`). `Main.qml` centres any narrower row, so an unequal row
  brings back the exact side gutters this view exists to remove. Enforced by
  `tests/test_layouts.py::TestCompactLayout::test_every_row_is_exactly_13_units`.
- **Layers are a QML-side view concept and the backends never see them.** Rows
  carry an optional `"layer"` field and rows without one always render, which is
  what keeps the full-size layouts working; a `"type": "layer"` key sets
  `activeLayer` and deliberately does **not** call `keyboard.setLayout()` (that
  would persist as the user's layout preference). `activeLayer` resets to
  `"base"` on every layout change. Because the whole feature is data + QML, it
  needed zero backend work on either backend (Python on `main`, C++ on
  `cpp-rewrite`): don't "port" it.
- **`totalKeyUnits` is derived, not hardcoded** (`_widestRow` in `Main.qml`
  computes the widest visible row's units + gap count, and full-size layouts
  resolve to exactly the historical 15.5u / 14 gaps). Don't reintroduce the
  constant.
- **Compact is orthogonal to letter arrangement.** `resolveLayoutId()` combines
  `currentLayout` with the `compactView` bool into `<layout>-compact`, and a
  layout with no compact variant falls back to full size, so the toggle is always
  safe. Adding compact Dvorak is dropping `data/layouts/dvorak-compact.json` in
  place, no code change.
- **No panel that has to line up with the keyboard grid may use
  `QtQuick.Layouts`.** It rounds every child up to a whole pixel, so 13 keys of
  69.23 px each became 13 of 70, and the panel rendered 10 px wider than the grid
  it sits flush with, overhanging the window and clipping its last key. Number
  Row and Function Row are plain `Row`s, Navigation a plain `Grid`, Numpad a
  `Column` of `Row`s. Guarded by
  `tests/test_qml_compact_view.py::TestPanelsSitFlushWithTheGrid`.
- **The accent fill on the editing keys (Esc, Tab, Shift, Backspace, Del) is a
  derived wash over the theme's key colour, never the raw accent and never a
  constant.** `root.accentWashFor()` walks the alpha down from 0.35 until the
  theme's own `textColor` clears 4.5:1; a flat 35% dropped five of the nine
  themes below WCAG AA, on exactly the keys the style exists to make findable.
  The accent-coloured border carries the cue where the wash has to back off.
  Full-size layouts are deliberately untouched.
- **Del sits on the base layer, Esc on `?123`.** A 13u row has no spare unit, so
  the two traded places. The Number Row panel puts a second Esc back at the
  top-left and that duplicate is deliberate, so `?123` stays the fallback for a
  future layout that shows the compact grid without the panel. Don't swap them
  back without reading the rationale in the design doc.
- **The symbol pages carry no Shift key**; Shift's slot switches to a second page
  (`=\<`), the phone convention, which makes a glyph appearing twice on one
  screen *structurally impossible* rather than merely absent. The bottom row and
  the right-hand nav column are byte-identical on every layer, and the tests that
  guard that derive the layer list from the file rather than naming base/sym.
  `Main.qml`'s layer branch calls the idempotent `keyboard.releaseShift()` on
  every switch (never `if (shiftOn) toggleShift()`), because the modifier is held
  at the OS level and a Shift carried in from the letters page makes `1` emit `!`
  with the keycap still reading `1`. Guarded by
  `tests/test_layouts.py::TestNoDuplicateGlyphsWithinALayer` and
  `tests/test_qml_compact_view.py::TestSecondSymbolPage`.
- **Digits come back via a panel, not a fifth row, and not via a toggle.**
  `qml/components/NumberRow.qml` (13 x 1u, flush with the compact grid) renders
  above the keyboard whenever `Main.qml::showNumberRow` is true, which is
  derived: true exactly when the active layout JSON carries no `number` row of
  its own. That is the compact variants and nothing else, so digits are always
  on screen in both views and a full-size layout can never end up with a
  second, narrower number row stacked on the one built into its JSON. Keying it
  off the layout rather than `compactView` matters because a letter arrangement
  with no compact variant silently falls back to full size. Its leading key
  is **Esc, not `` ` ``** (backtick lives on `?123` row 2).
- **The nav column reads Home / PgUp / PgDn / End top to bottom** (a scroll
  ladder: top, page up, page down, bottom; Owen asked for Home above PgUp).
  Pinned by
  `test_layouts.py::TestCompactLayout::test_nav_column_reads_top_to_bottom`.
- QML-only behaviour can't be covered by the Python suite, so
  `tests/test_qml_compact_view.py` and `tests/test_qml_prediction_bar.py` load the
  real `Main.qml` headlessly (`QT_QPA_PLATFORM=offscreen`) and fail on QML
  warnings. That's the only guard against a binding error shipping as a blank
  keyboard.

## Symbol Layer (full-size layouts)

`qwerty` / `dvorak` / `colemak` carry one symbol page, reached from a `Sym`
key at each end of the space row. Compact View had `?123` and `=\<` from the
start and the full-size layouts had nothing, so every glyph outside a
physical keyboard's printing (`° × ÷ ± € £ © ™ … → ¿`) was reachable in one
view and not the other. Data plus QML only, like Compact View: the backends
never see a layer.

**One page, not two.** Compact needs two because a 13u row cannot hold the
ASCII symbols *and* the extended ones. Full size already has every ASCII
symbol on the base layer, printed on a key or as a shifted variant that both
Shift and right-click reach, so the page is only worth a hop for glyphs that
have nowhere else to come from. That is 34 slots, and the long tail
(accented letters, `∞ √ π † ★`, emoji) belongs in the Symbols & Emoji window,
which has categories and a Recent page. A second page here would be duplicating that
window's job in layout JSON. `TestFullSizeSymbolLayer::test_no_symbol_repeats_what_the_base_layer_already_types`
is the rule stated as a property: it is the same thing
`TestNoDuplicateGlyphsWithinALayer` asserts within one page, applied across
the hop.

The 34: **`sym-top`** dashes, ellipsis, curly quotes, arrows, inverted marks;
**`sym-home`** currency, section, pilcrow, bullet, copyright, registered,
trademark, degree; **`sym-bottom`** the maths set. Everything on it is Latin-1
Supplement, General Punctuation, Arrows or Math Operators, all text
presentation. **Keep it that way**: the geometric-shape and dingbat ranges
(`✓ ✗ ★`) resolve through Segoe UI Emoji on Windows, which renders in colour
and ignores the `color` property outright, which is the same reason the lock
badge and the clear-context ring are not glyphs (see *Right-Click to Lock*).

### Why nothing moves

**Only the three letter rows swap.** `number` and `space` carry no `layer`
field, so they render on every page: digits stay one tap away instead of
going behind the hop the way Compact View has to put them, and the space bar
never leaves the screen. Each `sym-*` row matches the row it replaces both in
unit total and in key count, so `keyW`, the window width and every column
position are identical across the hop. Tab, Caps and Enter keep their
exact slots, which is the payoff for full size having room compact does not:
a comma typed on the symbol page does not cost a hop back to reach Enter.

**Del is not on the full-size grid at all.** It sits above the arrows on the
Navigation panel (shown by default), which is where a physical keyboard puts
it. The top row used to carry it past the backslash, which made that row 0.9u
wider than the home row and, because rows are centred individually, pushed
the whole letter block left until W sat between A and S. Removing it and
growing Enter to 2.3u is what puts Q over A. The space row cannot take it
either: with a `Sym` at each end a third key there is 15.6u, past the number
row, and the window widens to fit. Guarded by
`tests/test_layouts.py::TestTheLetterColumnsLineUp`.

**Two `Sym` keys, not one, and that is arithmetic rather than taste.** Rows
are centred individually, so adding equal width to *both* ends of a centred
row leaves every key already in it exactly where it was. A single key
appended to either end would have slid Ctrl, Win, Alt and the space bar
sideways by half a key width on the row the user clicks most. The space row
goes 11.6u to 14.6u and stays under the number row's 15.5u, so the window
width is untouched.

**The `sym-*` rows must sit before the `space` row in the JSON array.**
`visibleRows` filters in array order, so with them appended at the end the
symbol page rendered `number, space, sym-top, sym-home, sym-bottom` and the
space bar jumped three rows up the keyboard. Guarded by
`TestTheFullSizeSymbolPage::test_the_space_bar_does_not_move`, which measures
from the top-left corner of the key grid rather than in scene coordinates:
the first tap on any non-char key settles the chrome above the keyboard by
one pixel (Caps does it too, and did before this feature existed), so a
scene-y assertion fails by 1 px for a reason that has nothing to do with the
grid.

### Why the keys are `literal`

Every char key on the page sets `"literal": true`, which routes it through
`pressKeyLiteral` instead of `pressKey`. `pressKey` applies shift / caps-lock
case normalisation, a layer switch deliberately leaves Caps Lock alone (it
only affects letters, and this page has none), and Python's `str.upper()` is
not the identity on every non-ASCII character: Caps Lock plus the micro sign
typed a Greek capital Mu, so the key emitted one glyph while the cap
displayed another. That is the same disagreement the symbol pages carry no
Shift key in order to avoid, arriving through the other toggle.

The page therefore carries **no Shift key** either, per the existing rule; the
two Shift slots on `sym-bottom` hold `ABC` keys instead, which is the phone
convention and puts a wide exit target where a hand reaching for Shift out of
habit already is.

### The `Sym` key is both the way in and the way out

It sits on the space row, which renders on every page, so it cannot be a
one-way door the way compact's layer keys are. `Main.qml` therefore sends a
layer key whose target is **already showing** back to `base`. Every other
layer key in the project targets something it is not on, so that branch is
dead for them and their behaviour is unchanged. `stateKey: "symLayer"` lights
the key while the page is up, which is the only thing on screen that says
which page the letters were swapped for.

Guarded by `tests/test_layouts.py::TestFullSizeSymbolLayer` (the data) and
`tests/test_qml_compact_view.py::TestTheFullSizeSymbolPage` (the live QML).
`TestNoDuplicateGlyphsWithinALayer`'s helpers now fold a row with no `layer`
into **every** layer rather than into `base` alone: that was correct while
full size had a single layer, and one layer too few the moment it had two.

## Symbols & Emoji window

The long tail behind the symbol layer above. That layer carries the 34 glyphs
worth a single click; this window carries the rest, because categories, a
Recent page and several hundred glyphs do not fit on a key grid at a size an
imprecise pointer can hit. Opened from a smile button in the suggestion bar,
immediately left of the Snippets bookmark, with a title-bar twin
(`symbolsTitleBarButton`) visible only when `suggestionsEnabled` is false, for
the reason the Snippets pair documents: the suggestion bar collapses to zero
height with that setting and an unrelated toggle must not be the only thing
between the user and a feature.

Catalogue in `src/glyphs.py`, bridge slots `getGlyphCategories()`,
`insertGlyph(str) -> bool` and `getRecentGlyphLimit()`, UI is the floating
`symbolsWindow` in `qml/components/SymbolsWindow.qml`, instantiated from
`Main.qml` the same way as `SnippetsWindow.qml`: required properties for
theme colours and the helper functions it needs, and a `problem` signal so
the `snippetProblemToast` it fires on a failed insert can stay on the
keyboard window.

### A tap types, it does not copy

This is the one place the window deliberately diverges from Snippets, which
copies to the clipboard and offers no way to type at all. The argument there
is that a long address is expensive to retype, only lands when the caret is
already in the right field, and **misses silently** when it does not. A glyph
is a keystroke. Every key on the keyboard has exactly the same focus
exposure, so paying two clicks and a clipboard round trip to type one
character would make the window worse than the keys beside it.

`insertGlyph` calls the **same `_commit_verbatim_insert` method `insertSnippet`
calls**, rather than merely mirroring its shape, because a pair of
nearly-identical blocks drifting apart is this codebase's recurring failure:
one call runs the verbatim-insert prologue (`_release_sticky_modifiers()`, a
deferred auto-space settled as prose, `_consume_auto_cap()`), sends through
`_send_literal_text` so a held modifier cannot rewrite it, then clears
`_current_word` / `_raw_token` / the pill row so the glyph cannot corrupt the
next prefix match. Not gated on privacy mode, same rule as every other
insert: the user tapped it, so it has to reach the app.

**It returns a bool and QML honours it.** The bridge refuses while an edit
field owns the keystrokes (the prediction-edit popup, the snippets editor).
The window is somewhere else on the desktop, so a tap that silently did
nothing is indistinguishable from one that missed and the user taps again;
a refused tap flashes the problem toast and is **not** written to Recent,
which would otherwise fill with glyphs that never reached anything.

### Recent lives in the settings layer, not in a file

It is a convenience the user rebuilds by tapping four glyphs. A file would
have needed a loader, a size cap and a Data Backup decision for something
worth none of the three, so it is `appSettings.savedRecentGlyphs`, a JSON
array of strings, capped at `glyphs.MAX_RECENT` and deduplicated most-recent
first. The cap lives in Python and QML asks for it, the same split
`getSnippetLimit` uses: QML owns the storage, the bridge owns the bound.

A malformed value is dropped rather than reported. The catalogue underneath
it is intact and the user is one tap from refilling the list, so a dialog
would cost more than the thing it is about.

The window **opens on Recent once there is something in it**, and on the
first catalogue tab before that: opening on an empty page teaches the user
the window is empty.

### The shell is the Snippets window's

Same flags, same manual drag (never `startSystemMove()`), same
desktop-wide `clampedWindowPos` restore, same write-position-on-release. It
is found by `objectName` from `keyboard_app.py::_wire_floating_windows`,
which now loops over both windows and re-applies `WS_EX_NOACTIVATE` on every
`visibleChanged`; the per-window handler binds its target as a **default
argument** rather than closing over the loop variable, which would otherwise
leave both handlers styling whichever window was found last.

The grid pages 8 x 4 and the Repeater's model is the **page size, not the
glyphs remaining**, so a short last page keeps its empty cells and the pager
underneath cannot walk up the window into a pointer already travelling toward
it. An empty cell's MouseArea is disabled, so it is not a tap that silently
does nothing. Switching category resets to page 0, or a tab switch lands on
page 3 of a category with two.

Category tabs are a **wrapping `Flow`, not a scrolling strip**: a scroll
strip would put half the categories behind a drag gesture, which is the input
this keyboard's users can least rely on. Three rows of chips is the cost.

### Two font rules, and they pull in opposite directions

The **chrome** obeys the project's usual rule: the smile and the close cross
are `StrokeIcon` path data, never typeset, because Segoe UI Emoji renders a
glyph in colour and ignores the ink it is given.

The **cells** do the opposite and name no font family at all. Colour is the
content here rather than chrome that has to obey an ink colour, and Qt's own
fallback is what reaches the host emoji font. `font.families` (a list) does
not exist on this Qt's grouped font property, and naming a single family
would pin one platform's font and lose the glyph on the other two.

`tests/test_qml_symbols.py::TestTheEntryButtonIcon` asserts the smile paints
ink at all, which is the property its one hand-converted path can break:
invalid path data paints **nothing** through `ctx.path` (measured: zero lit
pixels), so a bad conversion ships as a blank circle on the suggestion bar
rather than as an error.

### The suggestion bar's button reserve is derived

`predBar.predButtonCount` is now the number the reserve is computed from
(`predPillHeight * count + predButtonGap * (count - 1) + 16`). It went from
one button to two to three and each time the formula was re-derived by hand;
the reserve is what stops a prediction pill rendering underneath a control,
so a reserve that is merely near the truth is the bug the property exists to
prevent. The new button is the **first** child of the right-anchored Row, so
Snippets and the clear-context ring do not move.

Guarded by `tests/test_qml_symbols.py`, `tests/test_glyphs.py`, and
`tests/test_keyboard_bridge.py::TestTypingAGlyphFromThePicker`.

## Modular Layouts

Design doc at `docs/architecture/MODULAR_LAYOUTS.md`. Inspired by Octavium's (`C:\Users\owenp\dev\Octavium`) Layout/KeyDef data model. Four levels of modularity: (1) Built-in JSON layout packs (video editing, gaming, streaming). (2) User-created layouts via editor. (3) Panel composition - snap independent panels (QWERTY, numpad, macros) into a grid. (4) App-aware auto-switching based on foreground window.

Action types: `char`, `special`, `hotkey`, `text`, `macro`, `launch`, `layout`, `midi`. Profiles bundle layout + theme + window position + auto-switch rules.

## Auto-Update

Implemented in `src/updater.py`. Flow walkthrough, threat model + defences table, and the per-defence rationale all live in `docs/build/AUTO_UPDATE.md`. Release checklist is in `docs/build/WINDOWS.md`.

> **Releases live in a separate public repo** - `okstudio1/alpha-osk-releases`. The updater's API URL is hard-pinned to that repo, so `gh release create` must always pass `--repo okstudio1/alpha-osk-releases`. (Historical note: the source repo `owenpkent/alpha-osk` was private until 2026-05-16; the split was originally a private/public boundary, and is now preserved because the pinned updater URL relies on the releases repo being its own canonical source-of-truth.)

Version source of truth is `src/__version__.py`. The release-asset filename **must** match `Alpha-OSK-Setup-{version}.exe` exactly - the updater rejects anything else. User-facing toggle: *Settings -> Data & Privacy -> Updates -> "Check for updates on startup"* (persisted as `appSettings.savedAutoCheckUpdates`).

**Install path is pinned, not read from the registry.** The generated NSIS installer no longer declares `InstallDirRegKey HKCU`: nothing in the build ever wrote that key, so it was a dangling read of a user-writable registry value, and the silent `/S` auto-update path honoured it anyway. `updater.py::_install_target_dir()` computes the directory itself (the currently-running frozen exe's own parent directory, or the `%ProgramFiles%\Alpha-OSK` default when not running frozen) and every silent install launches with an explicit `/S /D=<dir>`. NSIS's `/D=` has strict syntax: it must be the last parameter on the command line and unquoted even when the path contains spaces. Don't reorder the installer arguments or add quotes.

**Signature verification also pins the version.** `_verify_signature(exe_path, expected_version)` still checks the Authenticode chain (status `Valid`, cert thumbprint, signer CN) but now also reads the downloaded exe's embedded `FileVersion` (via a PowerShell `Get-Item ... .VersionInfo.FileVersion` call, comparing only the first three components since `FileVersion` is often four-part) and requires it to equal `expected_version`. This matters because the release asset filename (`Alpha-OSK-Setup-{version}.exe`) is a selector, not a trust boundary: someone who could rename or re-upload a release asset, without being able to forge a signature, could otherwise re-attach an older, genuinely-signed installer under a newer version's name and roll every user back onto a build with known-fixed bugs. Relatedly, `_ps_single_quote_escape` fixes a real bug: a Windows username containing an apostrophe (`%TEMP%` paths embed it) broke the single-quoted PowerShell literal built from the exe path. That failed closed, so it wasn't exploitable, but it silently and permanently disabled auto-update for that user.

### Update progress UI

Full walkthrough (the four pieces from "user clicks install" to "new keyboard appears", plus the v1.0.19 file list) is in `docs/build/AUTO_UPDATE.md`. The non-obvious bits to remember: **never expose the download URL to QML** (the bridge only emits primitive ints); the pre-install toast sleeps `_PRE_INSTALL_TOAST_DWELL_S` (1.8 s) in the worker so it paints before the installer's taskkill; the relauncher splash is a `QTimer` state machine with an indeterminate `QProgressBar` (NSIS silent install has no real percentage); `_run_headless` is preserved as the test target and no-display fallback; and `_is_dev_target()` routes `python`/`pythonw` straight to headless so dev runs don't hang waiting for an exe mtime that never changes.

**`_spawn_relauncher` must pass `CREATE_NO_WINDOW` *instead of* `DETACHED_PROCESS`, not alongside it.** Windows documents the two as mutually exclusive ("CREATE_NO_WINDOW ... is ignored if it is used with either CREATE_NEW_CONSOLE or DETACHED_PROCESS"), so OR-ing them, which this did first, leaves `DETACHED_PROCESS` winning and the console suppression inert. The console has to be *suppressed* rather than absent because the flags do not propagate: in dev mode the command starts `venv\Scripts\python.exe`, that interpreter re-execs as the base interpreter, and the re-exec is a fresh `CreateProcess` carrying none of them. Under `DETACHED_PROCESS` there is no console for it to inherit so it allocates one (an empty terminal per relauncher, titled with the working directory); under `CREATE_NO_WINDOW` it inherits a console that is merely invisible. Detachment is not lost: Windows has no parent-death signal, so the child already outlives us, and `CREATE_NEW_PROCESS_GROUP` keeps it clear of the installer's taskkill. Relatedly, **no test may reach the real `_spawn_relauncher`** (an autouse guard in `tests/conftest.py` enforces it): several `download_and_install` tests stub only `_launch_installer`, and each real spawn is a detached process that outlives the pytest worker and never exits, because the helper has no branch for a parent PID that is already gone. That last part is still open, see `TODO.md`. Full write-up in `docs/build/AUTO_UPDATE.md`.

## Accessibility Ecosystem

Design doc at `docs/roadmap/ECOSYSTEM.md`. Alpha-OSK is part of a four-tool adaptive input platform:

| Tool | Repo | Output |
|------|------|--------|
| **Alpha-OSK** | `C:\Users\owenp\dev\alpha-osk` | Keystrokes (SendInput) |
| **MacroVox** | `C:\Users\owenp\dev\MacroVox` | Text (Deepgram STT -> clipboard) |
| **Octavium** | `C:\Users\owenp\dev\Octavium` | MIDI (virtual piano/pads) |
| **Nimbus** | `C:\Users\owenp\dev\Nimbus-Adaptive-Controller` | Joystick (vJoy/ViGEm) |

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

**Off by default.** When enabled (Settings -> Data & Privacy -> Privacy -> "Share anonymous usage stats"), the client sends a weekly POST containing nine integers: `anon_id`, `app_version`, `os`, `keystrokes`, `words`, `predictions`, `keystrokes_saved`, `minutes`, `sessions`, `prediction_offers`. These are exactly the lifetime counters already shown on the Analytics dashboard. **Never sent**: content, word frequencies, key frequencies, IP, hostname, or any per-session breakdown.

**Reached from QML as its own context property, not through the bridge.** `src/telemetry_bridge.py::TelemetryBridge` owns the `TelemetryClient` and the hourly submit timer, and is registered in `keyboard_app.py` as `telemetry` (alongside `keyboard`, the `KeyboardBridge`) - see *QML <-> Python Bridge Pattern*. `UnifiedSettingsPanel.qml` calls `telemetry.getEnabled()` / `telemetry.setEnabled(c)` / `telemetry.forgetData()`; `KeyboardBridge` no longer references telemetry at all. The headless QML test fixtures register both context properties through one helper, `tests/qml_context.py::install_context_properties`, rather than each hand-rolling `setContextProperty` calls.

Files, endpoint config, anon_id lifecycle, submit cadence, and the worker schema are all detailed in `docs/architecture/TELEMETRY.md`. Load-bearing facts:
- **`DEFAULT_ENDPOINT` in `src/telemetry.py` is the empty string** - while empty the client silently no-ops every submit (consent toggle still works, no data leaves the machine). Set it per-build before shipping a telemetry-enabled release; the Windows checklist (`docs/build/WINDOWS.md` step 2a) gates on this.
- **anon_id is cleared on opt-out**, so re-opt-in gets a fresh UUID4 and prior contributions can't be linked. "Delete my contributed data" POSTs to `/v1/forget`. (This is why the Data Backup archive deliberately excludes `telemetry.json`.)
- **`TelemetryClient` is the source of truth for the consent flag** - `UnifiedSettingsPanel.qml` queries `telemetry` on mount; **don't** mirror it into `appSettings`.
- **Cadence**: weekly `QTimer` (1-hour tick, 7-day window check) plus `submit_on_quit()` from `TelemetryBridge.shutdown()` (60 s anti-spam guard), called from `keyboard_app.py`'s quit sequence before `bridge.shutdown()` - the same relative order the two had when both lived inside the bridge's own `shutdown()`. All paths gated on `enabled AND endpoint AND anon_id`; failures retry `[5s, 30s, 120s]` then drop silently.
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