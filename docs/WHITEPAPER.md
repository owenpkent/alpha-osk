# Alpha-OSK: A Predictive On-Screen Keyboard for Motor-Impaired Users

**Version:** 1.1.0
**Date:** May 2026
**Audience:** Software engineers, accessibility researchers, assistive-technology practitioners

---

## Abstract

Alpha-OSK is an on-screen keyboard (OSK) for Windows and Linux designed for users whose primary input device is a mouse or pointer rather than a physical keyboard. It targets people with motor impairments (muscular dystrophy, ALS, spinal cord injury, severe arthritis, post-stroke hemiparesis) for whom every avoided keystroke is meaningful. The system pairs a Qt Quick (QML) UI with a Python bridge, synthesises keystrokes through OS-native APIs (`SendInput` on Windows, `xdotool`/`ydotool` on Linux), and runs a hybrid CPU-only prediction engine (n-gram + variable-order PPM + spatial fuzzy recognition) that learns the user's vocabulary on-device. There is no GPU, no cloud round-trip, and no LLM dependency. The system is **off-network by default**: predictions, learning, and analytics are local-only. As of 1.1.0, an opt-in usage-stats pipeline (a small weekly POST of lifetime counters; never any content) lets users contribute to a public community-impact aggregate; it is off by default and described in §5.6. This paper describes the architecture, the prediction stack, the accessibility-driven engineering trade-offs that shaped the system, the privacy and security model, and the open work.

---

## 1. Introduction and Motivation

### 1.1 The accessibility gap in OS-bundled keyboards

The on-screen keyboards bundled with Windows (`osk.exe`) and Linux desktops (GNOME On-Screen Keyboard, Onboard) provide a baseline accessibility surface, but they predate the prediction quality that mainstream mobile keyboards (Gboard, SwiftKey, iOS) have offered since the early 2010s. For an able-bodied user typing on glass, weak prediction is a minor annoyance; for a wheelchair user controlling a mouse with limited range of motion, weak prediction directly costs typing throughput, fatigue budget, and — over a working day — the ability to communicate at all.

Mobile-grade prediction has not transferred to the desktop OSK category for two structural reasons. First, mobile keyboards are tightly coupled to their operating systems' input method frameworks; the desktop equivalents (Windows TSF, Linux IBus/Fcitx) target IME use cases (CJK input) rather than augmentative communication. Second, the major mobile prediction stacks (LatinIME, SwiftKey's proprietary engine) are either Android-only or closed source, and the open-source desktop tools that approach their quality (Presage) have stalled.

### 1.2 Design goals

Alpha-OSK is shaped by five goals, in priority order:

1. **The OSK must never steal focus from the target application.** A focus loss to the keyboard is not just an annoyance — it can drop a modifier, abort a drag, or lose the user's place in a long composition.
2. **Predictions must be useful from the first keystroke.** A user who can only type 5–10 words per minute cannot afford a "warm-up" period where the engine learns their vocabulary before earning its keep.
3. **Local-first: no cloud round-trip on the prediction or learning path; no GPU; no LLM.** The system must run on the modest hardware motor-impaired users typically inherit (older laptops, low-power desktops). Keystrokes must never be exfiltrated — this is a category where data sensitivity is unusually high (passwords, medical communication, intimate correspondence). The community-impact pipeline added in 1.1.0 (§5.5) is the only optional egress and is gated on explicit user consent; even when enabled, it submits only the lifetime counters the user already sees on the in-app dashboard, never content.
4. **Spatial errors must be corrected without punishing deliberate typing.** A user with hand tremor will land off-centre on keys; a user typing "thru" deliberately must not be autocorrected to "throw".
5. **Every interaction must be reachable from the mouse.** Keyboard shortcuts, modal dialogs that require Enter, and physical-keyboard fallbacks are non-options.

### 1.3 What this paper covers

Section 2 lays out the runtime architecture and the two boundaries that dominate the design (QML↔Python and Python↔OS). Section 3 describes the prediction engine in depth. Section 4 walks through the accessibility-driven engineering decisions whose constraints rippled through the architecture. Section 5 covers privacy and security. Section 6 discusses performance. Section 7 covers distribution and updates. Section 8 enumerates the open work and the known gaps relative to commercial keyboards. The deeper algorithm-level design docs are cross-referenced inline rather than reproduced.

---

## 2. System Architecture

### 2.1 Process model

Alpha-OSK is a single user-mode process. There is no daemon, no background service, no IPC across process boundaries. The process owns:

- A Qt Quick UI thread rendering the keyboard surface (`qml/Main.qml` and the components under `qml/components/`).
- A Python "bridge" object (`src/keyboard_bridge.py`) that holds the prediction engine, the modifier state machine, the context buffer, and the per-platform key synthesiser.
- Two long-lived `QTimer` instances: a 200 ms password-field poller (Windows) and a 250 ms foreground-window poller for predicting context resets across app switches.

The process never elevates voluntarily. On Windows, the launcher (`run.py`) intentionally avoids `runas` and the build pipeline produces an installer that drops a UIAccess-marked executable into a Program Files subdirectory — UIAccess lets the OSK inject input into elevated target windows without itself running elevated, preserving the sandboxing properties of medium integrity level.

### 2.2 The QML ↔ Python bridge

QML drives all rendering and gesture detection. Python owns all state and side effects. The boundary is a single `QObject` subclass (`KeyboardBridge`) exposed to QML via `setContextProperty("keyboard", bridge)` at startup. Every interaction follows the same pattern:

1. QML invokes a `@Slot`-decorated method (`pressKey`, `pressSpecialKey`, `editPrediction`, `processSwipe`, …).
2. The slot mutates Python state, calls into the platform synthesiser, and may invoke the prediction engine.
3. The bridge emits a `Signal` (`predictionsChanged`, `capsLockActiveChanged`, `editKeyTyped`, …).
4. QML bindings react and re-render.

This is deliberately the *only* coupling between the layers. There are no shared Qt models, no `QQmlListProperty`, no QML access to Python attributes other than `@Property`-decorated ones. The reason is testability: the Python side has 450+ pytest tests that exercise prediction, capitalization, modifier semantics, and persistence without spinning up a Qt event loop.

### 2.3 Platform abstraction

The platform abstraction lives in `src/platform/` and consists of three concerns: key synthesis, password-field detection, and configuration paths.

`src/platform/base.py` defines the abstract `KeyboardSynthesizer` interface:

- `send_text(text)` — emit a stream of characters (used for prediction insertion, swipe results).
- `send_key(name, modifiers=None)` — emit one named key, optionally chorded with modifiers.
- `hold_modifier(name)` / `release_modifier(name)` — pin a modifier at the OS level. This is what enables Shift+drag in the target app, Ctrl+click on hyperlinks, and so on. Without it, sticky modifiers would only attach to the next synthesised key, not to the user's *physical* mouse interactions.
- `replace_text(prefix_length, replacement)` — used only when a clicked prediction's casing diverges from the typed prefix.
- `reset_modifier_state()` — called once at startup to release any modifier left held by a crashed prior instance.

`src/platform/windows.py` implements this via the Win32 `SendInput` API, called through `ctypes`. There is one important type subtlety documented in the codebase: `KEYBDINPUT.dwExtraInfo` is `ULONG_PTR`, an integer-sized field that the kernel does not dereference, but it must not be set to a Python pointer object whose lifetime ends before the `INPUT` struct is consumed. We alias it to `ctypes.c_size_t` and pass `0`.

The Windows backend dispatches to one of three `KEYBDINPUT` modes, chosen per character:

1. **Virtual-key mode (`wVk = X`, `wScan = MapVirtualKeyW(X)`)**. Special keys (Backspace, arrows, F-keys), modifier holds, and chords. The OS dispatches a normal `WM_KEYDOWN(VK_X)` and `DefWindowProc` synthesises `WM_CHAR`. We populate `wScan` even though the OS keys off `wVk` locally, because remote-desktop clients (TeamViewer, RDP, VNC, AnyDesk) forward by scancode over the wire and silently drop events with `wScan = 0`.
2. **Scancode mode (`wVk = 0`, `wScan = scancode`, `KEYEVENTF_SCANCODE`)**. The default for ASCII text characters. Tells the OS "this is a physical key with this scancode"; the OS looks up the VK from the scancode using the active layout and dispatches `WM_KEYDOWN(VK_X)` plus `WM_CHAR`. Indistinguishable from a real keypress, which is why the Windows on-screen keyboard uses this mode. Character resolution: `VkKeyScanW(char)` for the VK and layout shift state, `MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)` for the scancode, `MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR)` with bit 31 to detect dead-key triggers. The OS Caps Lock state is folded into the shift wrap via `GetKeyState(VK_CAPITAL)`, so a clicked lowercase `a` types `a` even when the OS Caps Lock LED is on.
3. **Unicode mode (`wVk = 0`, `wScan = utf16_codepoint`, `KEYEVENTF_UNICODE`)**. Per-character fallback when scancode mode is unsafe: non-ASCII (≥ U+0080), unmappable on the active layout, AltGr- or Ctrl-required chord, dead-key trigger, or the corner case where Shift is physically held but the character does not need shift (we cannot safely release a key the user is holding). Unicode mode injects a `WM_KEYDOWN(VK_PACKET = 0xE7)` followed by `WM_CHAR`. It is layout-independent, covering the entire Unicode range including emoji and CJK.

The reason scancode is the default for ASCII rather than Unicode (which used to be the default everywhere except chords) is that `VK_PACKET` is invisible to applications that read raw scancodes or filter on real virtual-key codes. Confirmed cases that broke under pure-Unicode and now work: Blender (the GHOST input layer keys off the real VK and scancode for shortcuts and viewport ops), VirtualBox (the kernel-mode keyboard filter forwards by scancode to the guest VM), DirectInput-based games and DAWs, raw-input-based 3D and CAD tools. The general pattern: any application that wants `WM_KEYDOWN` rather than `WM_CHAR` was unreachable from Unicode mode.

`src/platform/linux.py` shells out to `xdotool` (X11) or `ydotool` (Wayland) via `subprocess.run`. Synchronous calls are mandatory: a chord like "shift down → key → shift up" issued through three `Popen` instances will race and leave shift stuck. Wayland-specific behaviours (no foreground-window query for unprivileged clients, the need to frame chords with `--key-down` / `--key-up` instead of letting the chord parser handle it) are encapsulated here so the bridge can remain platform-agnostic.

Modifier+punctuation chords need special handling on both platforms. The bridge never sends `Ctrl+-` as Unicode injection, because the target application's shortcut handler listens for virtual-key events (`WM_KEYDOWN` with `VK_OEM_MINUS`) or X keysyms (`minus`), not character events. On Windows, `send_key` uses `VkKeyScanW` to resolve any punctuation character to its layout-correct VK + shift state. On Linux, a `_CHAR_TO_KEYSYM` map rewrites `-` → `minus`, `=` → `equal`, etc. before chord assembly. Without these translations, "Ctrl+minus" zoom-out and similar chords silently fail.

### 2.4 Configuration paths and data locality

All persistent user data lives in OS-standard per-user locations:

| Platform | Config / model directory |
|----------|--------------------------|
| Windows  | `%APPDATA%\alpha-osk\` |
| Linux    | `~/.config/alpha-osk/` |

The directories hold three artefacts: `models/ngram_model.json` (learned vocabulary, capitalisation, suppression flags), `models/ppm_model.json` (character-level n-gram trie), and `analytics.json` (lifetime stats — see §5). User-imported vocabulary packs go to `packs/` under the same root. The application ships with **no built-in packs** — earlier releases shipped six (medical, programming, academic, gaming, business, NSFW) but each was 200–400 words, ~30× smaller than the base wordlist, and personal learning caught up within minutes. The current system is import-only; the rationale and pack format are documented in §3.1.2.

The application *never* writes to its own install directory at runtime. This invariant matters for Program Files installs (where writes would silently redirect to VirtualStore) and for AppImage packaging (where the bundle is read-only).

---

## 3. The Prediction Engine

The prediction engine is the most novel component of the system and the most salient to the user. It lives in `src/prediction/`. The design follows the layered approach pioneered by Presage (Vescovi) — multiple predictors with different strengths, merged by linear interpolation — extended with a spatial recognition layer borrowed in spirit from LatinIME.

### 3.1 Component overview

| Component | Role |
|-----------|------|
| `ngram_predictor.py` | Word-level unigram/bigram/trigram model. The dominant signal source. |
| `ppm_predictor.py` | Character-level variable-order Markov model (PPMD escape). Predicts next characters within a partial word. |
| `fuzzy_recognizer.py` | Spatial error correction. Considers nearby keys as substitution candidates and applies edit-distance penalties. |
| `hybrid_predictor.py` | The merge layer. Runs all three predictors in parallel, normalises scores, applies suppression and capitalisation, returns up to N suggestions. |
| `vocabulary_pack.py` | User-imported domain vocabulary packs that contribute weighted unigram/bigram/trigram counts into the n-gram tables. No built-in packs ship; the system is import-only (see CLAUDE.md *Vocabulary Packs* for the rationale). |
| `symspell.py` | Precomputed-deletion edit-distance index used by the fuzzy recogniser. Built eagerly at startup; per-query lookup runs in well under 1 ms on a 10 K dictionary. See §3.6. |
| `swipe_recognizer.py` | Shape-matching gesture decoder for glide typing. |
| `transformer_predictor.py` | Optional LLM re-ranking pass. Disabled by default; not on the hot path. |

For deep design treatments of each, see `docs/PPM.md`, `docs/FUZZY_RECOGNITION.md`, `docs/HYBRID_MERGING.md`, and `docs/SWIPE_TYPING.md`.

#### 3.1.1 Why layered

Each predictor in isolation has a complementary failure mode, and the merge layer is what makes the whole stack stronger than any of its parts. A pure n-gram model is excellent at "the user just typed *I want* — they probably want *to*", but it has no notion of partial words, so it cannot complete *th* into *the*. A pure PPM character model is excellent at completing partial words, but it sees the world as a stream of characters, not words, so it readily produces suffixes that match no real word ("th" → "throu" rather than "through"). A pure fuzzy/spatial recogniser corrects mis-taps but is context-blind: after typing "of ", it cannot tell *the* from *thy* — both are spatially plausible neighbours of whatever the user meant. Merge them and each layer's strength covers the others' blind spots: the n-gram supplies context, PPM supplies prefix-completion, fuzzy supplies typo-tolerance.

This layered approach mirrors Presage's "natural language as a combination of redundant information sources" framing and the merge model used in LatinIME and Gboard. The Alpha-OSK contribution is not the layering itself but the specific weights, the cold-start strategy (§3.3), the fragment filter (§3.4), and the spatial-bigram cross-talk in the merge (§3.6) — each tuned for the OSK use case where a single predictor's wrong answer costs the user real fatigue.

#### 3.1.2 Per-component internals

**`NgramPredictor` (`ngram_predictor.py`).** Holds three plain dicts: `unigrams: Dict[str, int]`, `bigrams: Dict[str, Dict[str, int]]`, and `trigrams: Dict[str, Dict[str, int]]` keyed by `"w₁ w₂"`. It also owns the capitalisation table (`Dict[str, str]`), the blacklist set, the dispreference counter, and a `_user_total` invariant that must equal `sum(user_vocab.values())` after every mutation (§3.4 detail). `predict(context, n)` performs the linear-interpolated rank described in §3.2; `learn(word)` updates frequencies through the fragment filter and repetition gate; `get_capitalized(word, sentence_start)` is the export hook used by the merge layer to apply the three-tier capitalisation model. This file is the largest by volume in the prediction package because every persistent piece of user data lives here.

**`PPMPredictor` and `PPMWordPredictor` (`ppm_predictor.py`).** Two cooperating classes. `PPMPredictor` is the raw character model — a variable-order Markov chain with PPMD escape, following Cleary & Witten's original construction. It learns from a stream of characters and predicts a probability distribution over the next character given the last N (where N is the model order, default 5). `PPMWordPredictor` wraps it: it walks the character distribution into the most likely full-word completions of the partial prefix the user has typed. The wrapping matters because the merge layer takes word predictions, not character distributions.

**`FuzzyRecognizer` (`fuzzy_recognizer.py`).** Owns a `SpatialKeyModel` (Gaussian distribution over neighbouring keys keyed by Euclidean distance on the QWERTY layout, σ = `spatial_uncertainty / 2`, default σ = 0.7) and a `FuzzyWordGenerator` that combines the spatial beam search with a SymSpell-backed edit-distance lookup (`src/prediction/symspell.py`). Two query paths: `get_fuzzy_predictions(context, n)` returns ranked candidates for the merge, and `should_autocorrect(typed, candidate)` runs the two-tier threshold (§3.7) when the system has to decide whether to *commit* a correction (e.g. on space) versus merely *suggest* it. The recogniser is the most numerically tuned component — its constants are spelled out in §3.6.

**`HybridPredictor` (`hybrid_predictor.py`).** The orchestrator. Holds references to one `NgramPredictor`, one `PPMWordPredictor`, one `FuzzyRecognizer`, one `PackManager`, and (optionally) one `TransformerPredictor`. The `predict()` entry point runs the three primary predictors in sequence (not in threads — Python's GIL would defeat parallelism here, and each predictor is fast enough that single-threaded sequential is simpler and still meets the <30 ms latency budget). It then dispatches to a strategy-specific scorer (`_score_rank` / `_score_rrf` / `_score_linear` / `_score_loglinear`) keyed on the user's selection in *Settings → Smart Typing → Suggestion Engine*; default is `rank`. Source weights are shared across every strategy (`_source_weights` returns 3.0/0.3/0.6 for next-word, 1.0/0.8/0.6 for mid-word completion). The default rank strategy scores each candidate as the sum of `weight / (rank + 1)` from every source it appeared in. Consensus boost (RRF) substitutes `weight / (60 + rank + 1)` so the rank-1 vs rank-2 gap shrinks and consensus across sources matters more. Confidence-weighted (linear) normalises each source's raw scores into a sum-to-1 distribution and combines `Σ w_i · P_i(w)`. Multiplicative (log-linear) does the same per-source normalisation but combines `Σ w_i · log P_i(w)` with a 1e-6 floor for words missing from a source — equivalent to `Π P_i(w)^w_i`. Klakow (1998) showed log-linear beats linear interpolation by ~20% relative perplexity on n-gram smoothing. Across every strategy, the spatial-bigram cross-talk (`1 + log1p(bigram_count) / 2`) re-weights fuzzy candidates against the previous word's bigram table — the only context signal fuzzy has access to. Dispreference penalties divide the score by `(1 + count · 0.5)` and capitalisation is applied last (`_finalize_scores`), so all internal scoring is case-insensitive. Full strategy trade-offs and migration history live in `docs/HYBRID_MERGING.md`.

**`VocabularyPack` and `PackManager` (`vocabulary_pack.py`).** A pack is a folder of plain-text files: `dictionary.txt` (one word per line), optional `bigrams.txt` (whitespace-separated pairs), `trigrams.txt`, and a `pack.json` metadata stub. `PackManager` enumerates packs from the user's import directory (`%APPDATA%/alpha-osk/packs/` on Windows, `~/.config/alpha-osk/packs/` on Linux), tracks which are enabled, and on enable calls `apply_to_predictor(ngram)` to write the pack's contents *into* the n-gram model's tables. All three tables are merged with `max(existing, pack_weight)` (`PACK_UNIGRAM_WEIGHT = 3`, `PACK_BIGRAM_WEIGHT = PACK_TRIGRAM_WEIGHT = 30`) so a pack never flattens an organically-learned high-frequency word — the pack establishes a floor, the user's own typing can climb above it. The architectural point: packs are not an independent predictor at merge time — they are a *modifier* of the n-gram tables, applied at enable-time. This is why the merge layer sees three predictors (n-gram, PPM, fuzzy), not four.

The system is **import-only as of 1.1.0+**. Earlier releases shipped six built-in packs (medical, programming, academic, gaming, business, NSFW) but each was 200–400 words — too thin to compete with the engine's organic learning, which bumps a word's score by +5 every time the user accepts it as a pill. Sourcing real domain vocabularies (SNOMED-grade for medical, full API surface for programming) is its own project and runs into licensing rabbit holes; curated 300-word lists were strictly worse than no shipped packs at all. The import path is preserved so power users with a real domain wordlist (a nurse with her unit's drug list, a researcher with citation keywords) can still drop a folder in. A known limitation, documented in `CLAUDE.md`: disabling a pack does not currently undo its predictor injection — `apply_to_predictor` uses `max()`, `disable_pack` only clears the pack's own in-memory copy, so injected words persist until the next process restart. Mostly invisible with no built-ins shipping; the fix (track per-pack contributions, revert on disable, guard against clobbering organic learning that piled on after enable) is open work.

**`SwipeRecognizer` (`swipe_recognizer.py`).** Outside the normal predict path. The QML side intercepts a swipe gesture, forwards the raw point trace through `KeyboardBridge.processSwipe`, and the recogniser returns a ranked list of words by SHARK²-style shape matching. The top result is typed verbatim; the rest replace the prediction bar so the user can repick. The recogniser uses its own dictionary (the same unigrams the n-gram model has) but bypasses the merge layer entirely — swipe is its own input modality, not a refinement of typed input.

**`TransformerPredictor` (`transformer_predictor.py`).** Optional, disabled by default. When enabled, it operates as an asynchronous re-ranking pass over the merged candidate list (`predict_with_refinement` is the entry point, not `predict`). The async design is deliberate: the synchronous `predict` path must return in tens of milliseconds, but a small transformer pass can take 100–300 ms even on CPU. The path emits a second, refined `predictionsRefined` signal so the QML side can update the bar a beat later if the refinement produced a different ranking. In practice this is rarely on; the layered classical stack is good enough for the hardware target, and the transformer dependency drags in PyTorch which would inflate the bundle by ~600 MB.

#### 3.1.3 Per-keystroke data flow

A single character keystroke from the user produces the following sequence inside `KeyboardBridge.pressKey`:

1. `_press_char` resolves the case (shift / caps state) and synthesises the keystroke to the OS through the platform synthesiser.
2. `_current_word` and `_context_buffer` are updated to mirror the on-screen text.
3. If the character is a word terminator (space, punctuation), `NgramPredictor.learn(_current_word)` runs through the fragment filter and repetition gate, and `_current_word` is reset.
4. `HybridPredictor.predict(context, n=8)` is called. Inside:
   a. `NgramPredictor.predict()` produces up to 16 ranked candidates (`n × 2`) by linear-interpolated trigram/bigram/unigram scoring.
   b. If PPM is enabled, `PPMWordPredictor.predict()` produces up to 16 character-walked completions.
   c. `FuzzyRecognizer.get_fuzzy_predictions()` produces spatial candidates (typically 5–20).
   d. `_merge_predictions()` dispatches to the active strategy scorer (`_score_rank` by default, or `_score_rrf` / `_score_linear` / `_score_loglinear` if the user has switched).  Each scorer applies the bigram cross-talk for fuzzy candidates inline; `_finalize_scores` then applies dispreference penalties, sorts, and trims to 8.
   e. `NgramPredictor.get_capitalized()` is called on each surviving candidate with the sentence-start flag derived from `context.rstrip()[-1] in ".!?"`.
5. The bridge applies one final case mirror in `_display_cased` (caps lock active → uppercase the result; typed prefix shift-mirroring) and emits `predictionsChanged` to QML.

Total wall-clock budget for steps 4–5 on a 2018 laptop is comfortably under 30 ms with vocabularies up to ~100k unigrams. The fuzzy spatial beam search is the dominant remaining cost; the previously-dominant edit-distance candidate generation was replaced by a SymSpell-backed lookup in `src/prediction/symspell.py` and now runs in well under 1 ms.

### 3.2 N-gram scoring with linear interpolation

`NgramPredictor.predict()` ranks candidate words by

```
score(w) = λ₃·P(w | w₋₂, w₋₁)  +  λ₂·P(w | w₋₁)  +  λ₁·P_uni(w)
```

with λ = (0.5, 0.3, 0.2). All three terms live in probability space, so a strong bigram signal can override a higher-frequency unigram — after typing "I want", "to" beats "the" because `P(to | want)` ≫ `P(the)`. This is the textbook Jelinek–Mercer interpolation applied at three orders.

When there is no preceding word (start of input, or after a context reset), the trigram and bigram terms collapse to zero and `P_uni` is taken at full weight, so partial-prefix completion is not attenuated. An earlier implementation added bigram and unigram counts in raw frequency space (`freq·2` for bigram, `p·100_000` for unigram), which made unigram dominate by three orders of magnitude — bigram evidence was effectively dead weight. The fix to a proper interpolated formula is one of the larger quality wins in the engine.

### 3.3 Cold-start signal: curated bigram / trigram corpus

Linear interpolation only helps when the higher-order tables have data. To avoid a cold-start period where the engine learns from scratch, the n-gram loader seeds two curated corpora at first launch:

- `data/common_bigrams.txt` — ~750 hand-picked English bigrams with weight 50 each.
- `data/common_trigrams.txt` — ~740 trigrams with weight 50 each, plus 10 reinforcement on each of the two internal bigrams (`w₁→w₂` and `w₂→w₃`).

The seed corpora cover conversational English (subject–verb–object skeletons, common discourse markers, frequent prepositions). They are deliberately small enough to be hand-audited; the next planned scale-up is COCA top-100k bigrams or Google n-gram exports (see §8).

### 3.4 Fragment filter on learning

A naive learning policy ("any sequence of letters terminated by space is a word") makes the engine rapidly unusable. Real OSK input includes false starts, mistakes the user backspaced past but reformulated, accidental letter sequences from drags, and miss-tapped key fragments. The engine therefore applies `_is_plausible_word` at three points:

1. **At learn time** (`NgramPredictor.learn`) — fragments are rejected before entering the candidate pool.
2. **At seed-corpus load time** — the Google 10k and 20k supplement wordlists are scraped from web search corpora and contain every isolated letter of the alphabet plus several hundred two-letter abbreviations and state codes at high frequency. Without filtering, typing a single-letter prefix would surface a flood of one- and two-letter "words".
3. **At model load** — existing users' `ngram_model.json` files are scrubbed on first launch after the filter shipped, so historical fragment pollution gets cleaned up rather than persisted forever.

The filter rules: words of length ≤ 2 must be on a short whitelist (legitimate two-letter words like "is", "in", "to"); words of length ≥ 3 must contain both a vowel and a non-`aeiou` letter, with `y` counting as both (so "eye" and "cry" pass, but "aaaa" and "xqz" do not).

Surviving unknown words from `learn` go through a repetition gate: counted in `_candidate_counts` until 3 sightings, then promoted into `user_vocab`. Known base-dictionary words and explicit `learn_word()` calls bypass the gate. Candidate counts decay alongside user vocab and persist across save/load.

### 3.5 Capitalisation model

Pills only auto-capitalise the **`I` family** (`I`, `I'm`, `I'll`, `I'd`, `I've`). Every other word surfaces in the casing the user typed: type `monday` and the pill is `monday`; type `Monday` (one-shot shift) and the pill is `Monday`; type `MON` (right-click each letter) and the pill is `MONday`. `KeyboardBridge._display_cased` mirrors every uppercase position from the typed prefix onto the pill, and that's the only path that produces capitals in pills.

Earlier builds shipped a three-tier Gboard-style system (Tier 1 = `I` family; Tier 2 = sentence-start auto-cap for ~130 ambiguous names like `will`, `jack`, `may`, `mark`; Tier 3 = ~8 000 unambiguous proper nouns from `data/proper_nouns.txt` plus user-taught forms). Tiers 2 and 3 were dropped because they fired on common English words ("the hope is", "a rose by", "may I", and the post-period word in every sentence) and pills came back capitalised when the user had typed lowercase. The user-facing rule is now "shift / caps lock is the cap signal", with the `I` family kept as the one mid-sentence exception.

`NgramPredictor.capitalization` is still populated — `_load_proper_nouns` reads `data/proper_nouns.txt` at startup, and `learn_capitalization` records user-taught forms (right-click → Edit, prediction-click after typing a capital, word completion with non-trivial casing). All-uppercase typings are still rejected by default because the dominant cause of all-uppercase input is Caps Lock being on; deliberate all-caps (right-clicking each letter, Caps Lock off the whole word — the bridge tracks this with a `_word_typed_under_caps_lock` flag and passes `allow_uppercase = not _flag`) is allowed through. The accumulated dict is persisted to `ngram_model.json`. `get_capitalized` does not consult it today, but keeping the data lets a future opt-in toggle revive proper-noun cap without re-teaching from scratch.

### 3.6 Spatial fuzzy recognition

`fuzzy_recognizer.py` accepts a typed string and a layout (key positions in pixel space) and produces ranked correction candidates. Candidates come from two parallel paths:

1. **Spatial beam search** over the typed positions, considering each key plus its neighbours weighted by inverse distance. Beam width is bounded by `min_prob = 0.001`.
2. **SymSpell-backed edit-distance candidates** at edit distance ≤ 2 (`src/prediction/symspell.py`). For every dictionary word, deletion variants up to two deletions are precomputed and indexed; at query time the input's deletion variants are looked up against the same index, and a Damerau-Levenshtein post-filter confirms the actual edit distance. Each surviving candidate is scored with a per-edit-type penalty when the distance is 1 (`_TRANSPOSITION_PROB = 0.30`, `_DELETION_PROB = 0.20`, `_INSERTION_PROB = 0.15`, `_SUBSTITUTION_PROB = 0.18`, `_APOSTROPHE_INSERTION_PROB = 0.50`) and a flat `_DOUBLE_EDIT_PROB = 0.05` at distance 2. Apostrophe insertion is bumped to `0.50` because missing apostrophes ("im" → "I'm", "dont" → "don't") are by far the dominant insertion error in real OSK typing. The index is built eagerly at the end of `set_frequencies` so the ~200 ms one-time cost lands in startup latency rather than the user's first keystroke; per-query lookup runs in well under 1 ms on a 10K-word dictionary.

The two paths produce overlapping candidates, which are deduplicated by string and rescored against the unigram frequency table. The spatial path is best at near-key mis-taps (Gaussian neighbour probability picks them up at the candidate-generation level, scoring them by spatial proximity); the SymSpell path is best at non-spatial typos (transpositions, missed letters, double edits). Together they cover the full failure space — a property the prior pure-edit-distance-1 path did not have, since substitutions outside the spatial neighbour radius were not enumerated at all.

### 3.7 Two-tier autocorrect threshold

Spatial recognition produces a list of correction candidates. Whether to *commit* a correction (e.g. on space) — as opposed to merely surfacing it as a suggestion — is gated by two thresholds in `should_autocorrect`:

- **Absolute confidence:** the correction's score must exceed `confidence_threshold = 0.65`.
- **Relative margin:** the correction must clear `_typed_baseline(typed_word) × 1.5`, where `_typed_baseline` returns `log1p(1) ≈ 0.69` for plausibly-shaped typings (vowel + consonant) and `0` for implausible slop ("xqz", "thx").

This is the LatinIME / Gboard pattern in miniature: the literal typed word competes against corrections in the same scoring frame. Plausible deliberate typings ("thru", "lol", "btw") are protected by the relative gate; implausible inputs fall back to the absolute gate alone. The goal is to commit a correction when the user clearly mis-tapped, and to leave the typing alone when the user typed exactly what they meant.

A fully unified scoring model — where the literal typed word has an explicit probability and competes against alternatives in a single ranked list — is the proper long-term fix and is described as Known Gap #1 in §8.

### 3.8 Swipe / glide typing

Swipe typing is off by default and toggled in *Settings → Smart Typing → Suggestions → Swipe Typing*. When on, a transparent overlay (`qml/components/SwipeOverlay.qml`) covers the keyboard rows and intercepts pointer gestures. A press → drag past 60 pixels → release is recognised as a swipe; press → release on a key falls through to a normal tap.

The recogniser (`SwipeRecognizer`) is a simplified SHARK² shape-matcher. It pre-filters candidates by start-key and end-key, then scores remaining words by

```
score(word) = log(freq + 1) − 8 · mean_normalized_distance
```

where `mean_normalized_distance` is the average distance between the user's path and the ideal path through the word's key centres, normalised by key width. The top-ranked word is typed via `send_text` followed by a space; alternates appear in the prediction bar so the user can re-pick if the top result is wrong. Design rationale and the trade-offs against a full HMM-based gesture decoder are in `docs/SWIPE_TYPING.md`.

### 3.9 Word suppression and rehabilitation

Users can right-click a prediction pill to *remove from vocabulary* (adds to a blacklist) or mark as *bad suggestion* (increments a dispreference counter that downweights the word by `1 / (1 + count · 0.5)`). Both lists persist in `ngram_model.json` and apply at merge time in `hybrid_predictor._merge_predictions`.

Auto-rehabilitation handles the case where the user changes their mind: typing a blacklisted word three times (each completed with space) restores it. The counter is tracked in `_blacklist_type_count` and persisted alongside the blacklist itself. Users can also restore words manually from the Model Visualization dashboard.

---

## 4. Accessibility-Driven Engineering Decisions

A recurring theme in Alpha-OSK is that constraints from the user population pushed back *into* the architecture in ways that would not arise in a mainstream keyboard. This section walks through the most consequential.

### 4.1 The non-focus invariant

The OSK must never take focus from the user's target application. On Windows this is enforced by:

- `Qt::WindowDoesNotAcceptFocus` and `Qt::Tool` on the QML window.
- `WS_EX_NOACTIVATE` set via `SetWindowLong` after window creation. Qt does not expose this flag and will not set it on its own.
- `WS_EX_TOOLWINDOW` to keep the window out of Alt-Tab and the taskbar.

On Linux, the equivalent is `Qt::Tool` plus `_NET_WM_STATE_ABOVE` and `_NET_WM_WINDOW_TYPE_UTILITY`.

Two consequences ripple out:

1. **Qt's built-in `onActiveChanged` does not fire reliably**, because `WS_EX_NOACTIVATE` keeps the window from becoming the active window even when the user is interacting with it. The bridge therefore polls the OS directly (`GetForegroundWindow()` on Windows, `xdotool getactivewindow` on X11) on a 250 ms timer to detect app switches and reset the prediction context. Wayland does not expose the foreground window to unprivileged clients, so this poll is a no-op there — context resets only happen on explicit cues (e.g. a clicked prediction).
2. **Physical keyboard input never lands in the OSK**, because the OSK never holds focus. This makes the in-app text edit popup (§4.5) non-trivial to build.

### 4.2 Sticky modifiers

Modifier keys are *sticky*: tap once to activate, tap again to deactivate, auto-release after one keypress (Shift) or remain held until explicit toggle (Caps Lock). This is the mainstream OSK model.

The non-obvious part is that activating a sticky modifier holds it at the *OS level* via `hold_modifier(name)`, not just in Python state. Without OS-level holding, sticky Shift would only attach to synthesised keystrokes — Shift+click and Shift+drag in the target app would not extend selections. With OS-level holding, the OSK behaves identically to the user pressing-and-holding a physical Shift key, which is the model users expect. `release_modifier(name)` is called on auto-release and on app shutdown so a phantom modifier is not left held against the X server, the Wayland compositor, or the Windows kernel after the OSK quits.

Caps Lock and Shift are independent toggles; toggling caps no longer flips shift. Both are surfaced separately to QML.

### 4.3 Suffix-only insertion for predictions

When the user has typed "hel" and clicks the "hello" prediction, the OSK sends `lo ` — only the suffix and a trailing space. Earlier versions sent Backspace×3 followed by "hello ", which was correct in plain text fields but failed in two important cases:

- **Slack and similar chat composers** treat Backspace at the start of an empty input as "discard draft and close composer" or "go to previous channel". A full Backspace-then-replace was destroying user state.
- **Terminals and REPLs** disable shell-style Shift+Left text selection, making any "select then overwrite" approach impossible.

Suffix-only insertion sidesteps both. The fall-back to `replace_text(prefix_length, replacement)` only fires when the prediction's casing differs from the typed prefix (e.g. typed "iph", clicked "iPhone"), where suffix-only would produce "iphPhone".

### 4.4 Right-click for shifted variant

A right-click on a character key types its shifted variant *without* flipping the sticky-shift state: `1` → `!`, `,` → `<`, `a` → `A`. This is a one-shot, modifier-free way to type a single shifted character — a common operation that would otherwise cost two clicks (Shift, then key).

The QML side resolves the output (preferring `kd.shifted` from the layout JSON, falling back to `kd.key.toUpperCase()` for letters), then routes through `keyboard.pressKeyLiteral(rch)` rather than `pressKey(rch)`. The distinction matters: `pressKey` applies the current shift/caps state and would lowercase the chosen `'A'` back to `'a'` if shift is off, defeating the feature. `pressKeyLiteral` types the character verbatim. Any future input source where the QML side has already chosen the final character (e.g. a long-press alternates picker) should use `pressKeyLiteral`.

### 4.5 The edit-popup pattern

Users frequently need to correct learned capitalisations: the engine learned "iphone" before the proper-noun list shipped, and the user wants to teach it "iPhone". The edit popup, opened from the right-click menu on any prediction pill, presents the word in a small in-window `TextField` that the user can edit using the OSK itself.

Because the OSK never holds OS focus, OSK keystrokes normally synthesise to whatever app is *behind* the OSK. To make keystrokes land in *our* `TextField`, the popup uses an "edit-mode intercept" pattern:

- `predEditPopup.modal = false` — a modal overlay would swallow MouseAreas on the keyboard below.
- `closePolicy: Popup.CloseOnEscape` — every OSK key click is a "press outside" relative to the popup; the default close-on-press-outside policy would slam the popup shut on the first keystroke.
- On open, the popup calls `keyboard.setEditMode(true)`. While active, `pressKey` and `pressSpecialKey` short-circuit the synthesiser and emit `editKeyTyped(char)` / `editSpecialPressed(name)` instead. A `Connections` block inside the popup wires those to TextField operations: insert at cursor, backspace, cursor motion, etc.

This pattern generalises: any future input source that needs OSK keystrokes to land in-app rather than out-of-app (a voice-dictation review field, a snippet editor) should follow the same shape.

### 4.6 Window-height invariant

The OSK window height is *bound* to the keyboard content's implicit height: `height: outerLayout.implicitHeight + 60`. There is no vertical resize handle (both edges are `SizeHorCursor`), and only the window *width* is persisted across launches.

An earlier version also persisted height. The first time `Component.onCompleted` ran `root.height = savedWindowHeight`, the binding broke (Qt binds are one-shot and any imperative assignment severs them). After that, any width change scaled the keyboard but the height was frozen — the user got either clipped bottom rows or empty bands above and below the keys, with no way to fix it. The fix was to delete the height-persistence path entirely. If height persistence is ever reintroduced, it must use `Qt.binding(...)` or a re-clamp in `onHeightChanged` to keep the binding live.

This is a small example of a pattern that recurs: accessibility constraints (in this case, no vertical resize handle because users with limited motor precision struggle with edge resizes) push *into* code that would be trivially correct in a normal desktop app.

---

## 5. Privacy and Security

### 5.1 Off-network by default

Alpha-OSK does not connect to the network at runtime except for the auto-update check (§7) and, only if the user explicitly opts in, the usage-stats pipeline (§5.6). There is no crash reporter, no model-improvement upload path, and no implicit telemetry. Lifetime statistics persist to a local `analytics.json` and are visible to the user through the in-app dashboard; **the dashboard is always local-only**, regardless of the §5.6 toggle. The toggle controls a separate, narrower pipeline that submits only the same lifetime counters the dashboard already shows the user, and only when explicitly enabled.

### 5.2 Privacy mode and password-field detection

A typed password should never enter the prediction model. Two paths enforce this on Windows:

1. **Background polling.** A 200 ms `QTimer` calls `is_password_field()` from `src/platform/password_detect.py`, which uses Windows UI Automation (`IUIAutomation::GetFocusedElement` → `UIA_IsPasswordPropertyId`). UIA covers native applications and modern browsers that expose accessibility metadata. A Win32 fallback (`EM_GETPASSWORDCHAR`) catches older Win32-only apps.
2. **Per-keystroke synchronous check.** `pressKey` and `pressSpecialKey` call `_check_password_field_sync()` rate-limited to ~50 ms. This closes the race window where the user types the first characters of a password between timer ticks — without the synchronous check, those characters would reach the prediction cache before the timer fires.

When privacy mode is active (auto-detected or manually toggled by the "Learning" / "Paused" button in the title bar), keystrokes still reach the OS, but `_current_word`, predictions, and learning are all suppressed. The prediction bar shows "Learning paused".

On Linux, the equivalent uses AT-SPI 2 (`gi.repository.Atspi`). A daemon thread owns a GLib event loop and listens for `object:state-changed:focused`; whenever focus lands on an accessible whose state set contains `STATE_PASSWORD_TEXT`, the privacy flag flips on. Coverage spans GTK (`GtkEntry` with `visibility=false`), Qt (`QLineEdit` in Password echo mode), and browsers that expose accessibility metadata. If `gi` fails to import or AT-SPI is not running, the detector falls back silently to the null detector and the user can still toggle privacy mode manually.

The COM lifecycle on Windows is worth noting: `_WindowsUIADetector` tracks `_owns_com` so `CoUninitialize` only fires if our code called `CoInitializeEx` and got `S_OK`. If `CoInitializeEx` returned `S_FALSE` (some other component already initialised the apartment), we skip the uninit — calling it would tear down the other caller's COM environment.

### 5.3 Pack import hardening

The vocabulary system is import-only (§3.1.2 covers why no built-ins ship). Users import third-party packs from arbitrary filesystem paths, which makes import the dominant security surface for the prediction stack. `PackManager.import_pack` enforces:

- The source folder's name is sanitised against `^[a-z0-9][a-z0-9_-]{0,63}$` — total length 1–64 characters, the first character must be alphanumeric (so a leading `_` or `-` is rejected, blocking dotfile-style or argument-style escapes), and only lowercase alphanumerics, underscore, and hyphen are accepted. Anything else (including `..`, slashes, spaces, uppercase) is rejected.
- The resolved destination path is verified to sit strictly under `user_packs_dir` before any `rmtree` or `copytree` runs. This blocks symlink traversal that resolves outside the packs root.
- Symlinks inside the source tree are *skipped*, not dereferenced. A pack that contains a symlink to `/etc/passwd` will import without that file.

The regression tests for these properties are in `tests/test_vocabulary_pack.py::TestImportPackSecurity`.

### 5.4 Model load caps

Both the n-gram and PPM model loaders reject files over 50 MB. The n-gram loader additionally rejects models with more than 500,000 unigrams, 500,000 bigram prefixes, or 100,000 capitalisation entries — anything beyond these is assumed to be corrupt or hostile and is silently skipped (the in-memory base dictionary is kept). These limits are intentionally well above what real long-term users produce.

### 5.5 Opt-in usage telemetry (1.1.0+)

Alpha-OSK has a community-impact pipeline that lets users contribute to a shared "X million keystrokes saved" aggregate. **It is off by default.** Both the user-facing data policy (`docs/PRIVACY.md`) and the design (`docs/TELEMETRY.md`) are versioned in the repo.

The toggle lives in *Settings → Data & Privacy → Privacy → "Share anonymous usage stats"*. When on, a weekly POST sends nine integer fields: a randomly-generated `anon_id` (UUID4), `app_version`, `os` (`windows` / `linux`), and the seven lifetime counters that already render on the in-app dashboard (`keystrokes`, `words`, `predictions`, `keystrokes_saved`, `minutes`, `sessions`, `prediction_offers`). **Nothing else.** The pipeline never sends content, word frequencies, key frequencies, IP, hostname, machine identifiers, or per-session breakdowns. The privacy-mode interaction is implicit: privacy mode (§5.2) suppresses learning and counter increments at the analytics layer, so password-field activity never enters the lifetime totals in the first place. The telemetry layer just forwards what the dashboard would show.

The `anon_id` is generated on first opt-in and **cleared on opt-out**, so opt-in/opt-out cycles produce unlinkable contributions. A user who wants their already-submitted row removed can use the "Delete my contributed data" button in the same Settings section, which POSTs to `/v1/forget` (the server returns 204 regardless of whether the id existed, so request-pattern probing yields no information). Reinstallation or deletion of the per-user config directory also produces a fresh id; the old row becomes orphaned and is garbage-collected by the daily cron after 365 days of inactivity.

The submission cadence is enforced by an hourly QTimer in the bridge that calls `maybe_submit()`; the function short-circuits unless the consent flag is on, the endpoint is configured, an `anon_id` exists, and at least seven days have elapsed since the last successful submission. A second hook (`submit_on_quit` from `KeyboardBridge.shutdown`) covers the case where the user runs the app for less than a week between sessions; it bypasses the weekly window with a 60 s anti-spam guard. Failures (HTTP 5xx, 429, network error) retry with backoff `[5 s, 30 s, 120 s]` and then drop until the next cycle. There are no user-visible error toasts: a network failure is not the user's problem.

The backend is a Cloudflare Worker (`backend/cf-worker/`) backed by D1. Two tables: `users(anon_id PK, first_seen, last_seen, app_version, os)` and `submissions_latest(anon_id PK, ts, …counters…)`. The latest submission overwrites the previous because lifetime counters are monotonic. Three routes: `POST /v1/submit` validates each counter against a sanity ceiling (e.g. 10⁹ keystrokes) and upserts both tables; `GET /v1/aggregate` returns sums across `submissions_latest`, cached at the edge for five minutes; `POST /v1/forget` deletes the user's row. A daily cron prunes users whose `last_seen` is older than 365 days, with `ON DELETE CASCADE` cleaning up the child row.

The `DEFAULT_ENDPOINT` constant in `src/telemetry.py` is the kill switch. While it is the empty string, the client treats the endpoint as not configured and silently no-ops every submit even when the toggle is on; setting it to a deployed worker URL activates the pipeline. This decoupling lets the client and the toggle UI ship in a release that has the backend not yet deployed (or for a release where telemetry is intentionally disabled across the board, e.g. a regression-investigation build).

#### 5.5.1 Threat model for the telemetry pipeline

- **An operator with full backend access** sees `anon_id`s, app-version distribution, OS distribution, and lifetime counters per user. Cannot see content, individual sessions, words used, or anything that would identify a user.
- **A passive network observer** sees that the user POSTed to the telemetry endpoint, plus the payload size (~200 bytes). TLS hides the payload contents.
- **A compromised backend** could backfill submissions to fake the public aggregate. Sanity ceilings on each counter limit the blast radius; per-IP rate limiting at the Cloudflare edge limits volume.
- **An adversary trying to deanonymize a user** has very little to work with: the `anon_id` is opaque, no IP is stored, no User-Agent is stored, no submission history is retained (only `latest`), and the aggregate endpoint never exposes individual rows.

### 5.6 Auto-update threat model

Auto-update fetches from the public release repository `okstudio1/alpha-osk-releases` (the source repo is private). The threat model and per-defence rationale are in `docs/AUTO_UPDATE.md`; the short version is:

- The update endpoint is `https://api.github.com/repos/okstudio1/alpha-osk-releases/releases/latest` over HTTPS with system-trusted CA roots.
- The downloaded asset filename must match `Alpha-OSK-Setup-{version}.exe` exactly. Anything else is rejected before execution.
- The installer is EV-code-signed; Windows SmartScreen and the OS verify the signature before the user is prompted.
- The NSIS installer's auto-relaunch path (silent installs only) launches the new executable through `explorer.exe` rather than directly. This drops the new process to medium integrity level instead of inheriting the installer's high-IL token, which is what the OSK needs (UIAccess injects medium-IL → high-IL, not the reverse, and learned vocabulary should land in the user's `%APPDATA%`, not the admin profile).

---

## 6. Performance and Resource Envelope

Alpha-OSK is intended to run unobtrusively on hardware that motor-impaired users typically have: older laptops, low-power desktops, sometimes tablet hybrids. The performance envelope reflects this.

| Metric | Target |
|--------|--------|
| Cold start | < 2 s on a 2018-era laptop |
| Per-keystroke prediction latency | < 30 ms typical, < 100 ms worst-case |
| Resident set size | < 200 MB after warm-up |
| Disk footprint (installed) | ~120 MB Windows / ~150 MB Linux (PyInstaller bundle) |
| GPU | Not used |
| Network | Update check on startup only (opt-out) |

The prediction engine is pure Python with no native extensions. The hot paths (n-gram lookup, fuzzy candidate generation) operate on plain dicts and lists rather than NumPy or compiled tries. This is deliberate: the working-set size is small enough (tens of thousands of words) that Python dict performance is adequate, and a native dependency would complicate cross-platform builds.

The single-instance lock uses `QSharedMemory`. The lock-holder reference is module-level in `keyboard_app.py` — a function-local would be destroyed before the application started, releasing the lock prematurely.

---

## 7. Distribution and Updates

### 7.1 Windows

Windows builds are produced by `build/windows/build.py`, which drives PyInstaller, NSIS (for the installer), and SignTool (for EV signing). The signing step is the single most common build trap: SafeNet Authentication Client exposes the eToken-resident certificate to the *user session only*, so the build must run from a non-elevated shell with the eToken plugged in. An elevated shell will fail with "Cannot find certificate."

The release-asset filename must match `Alpha-OSK-Setup-{version}.exe` exactly — the auto-updater rejects anything else. Version is sourced from `src/__version__.py` and read by both the updater (to compare against the latest GitHub release) and the build script (to name the installer and stamp the registry entry).

UIAccess is granted at install time via the `uiAccess="true"` manifest entry and the Program Files install location. UIAccess lets the OSK inject input into elevated target windows (UAC consent dialog, Task Manager, regedit) without the OSK itself running elevated.

### 7.2 Linux

Linux builds use a parallel pipeline in `build/linux/build.py` that produces a PyInstaller bundle and, optionally, an AppImage (`--appimage --fetch-appimagetool`). Signing is not part of the Linux flow — AppImage is unsigned by design, and EV signing is Windows-specific. The AppImage entry script (`build/linux/AppRun`) points `QT_PLUGIN_PATH` and `QML2_IMPORT_PATH` at the bundled Qt and defaults `QT_QPA_PLATFORM=xcb`.

`xdotool` and `ydotool` are *not* bundled. They are OS-level tools that must be installed on the host. The bundle starts without them but key synthesis silently no-ops, which is a known limitation discussed in `docs/LINUX.md`.

### 7.3 Update flow

The auto-update flow is documented end-to-end in `docs/AUTO_UPDATE.md`. The user-facing toggle is *Settings → Data & Privacy → Updates → Check for updates on startup* (persisted in QML settings). The Windows path uses NSIS silent install with a taskkill of the running OSK in `customInit` (so the new executable can be written) and an auto-relaunch through `explorer.exe` in `customInstall` (so the relaunched process runs at the user's medium IL, not the installer's high IL).

### 7.4 Dependency lockfiles, SBOMs, and CVE scanning

Every release ships two dependency artefacts alongside the installer, plus a CI-time scan job. They form a small but standards-aligned supply-chain hygiene story.

**Lockfiles.** Both build pipelines (`build/windows/build.py::freeze_lockfile` and `build/linux/build.py::freeze_lockfile`) run `pip freeze --all` against the build venv and write `release/Alpha-OSK-Setup-{version}-requirements.lock.txt` (Windows) or `release/Alpha-OSK-{version}-linux-requirements.lock.txt` (Linux). Pip-installable record of every Python package + exact version; `pip install -r <lockfile>` into a fresh venv recreates the build env. Until 1.1.0 there was no lockfile and "what version of urllib3 shipped in 1.0.16?" had no definitive answer.

**CycloneDX SBOMs.** Both pipelines additionally emit a CycloneDX 1.6 SBOM (`build/{windows,linux}/build.py::emit_sbom`, via `python -m cyclonedx_py environment`) to `release/Alpha-OSK-Setup-{version}-sbom.cyclonedx.json` (Windows) or `release/Alpha-OSK-{version}-linux-sbom.cyclonedx.json` (Linux). The SBOM is the machine-readable counterpart: per-component PURL (`pkg:pypi/<name>@<version>`), license expression where the package's metadata declares one, integrity hashes, and dependency graph. CycloneDX is OWASP-stewarded, ECMA-424 standardised, and the input format most security scanners (Trivy, Grype, OSV-Scanner, Dependency-Track) expect. `--output-reproducible` strips time/random fields so two builds of the same env produce byte-identical SBOMs. ~100 KB / 80 components at the current Python dep set. Soft-fails (warning, no abort) if `cyclonedx-bom` isn't installed; production builds pull it in via `requirements-dev.txt`.

**Why both.** The lockfile is the human/pip-friendly view, the SBOM is the machine/compliance view. The plaintext lockfile is more discoverable for a developer reading the release page and recreating the env; the SBOM is what a procurement reviewer drops into Dependency-Track or what a CI scanner consumes. They're the same packages from two angles, ~100 KB combined relative to the 85 MB installer — no reason not to ship both.

**Worker side.** `backend/cf-worker/package-lock.json` is checked in alongside `package.json` so Wrangler / TypeScript / `@cloudflare/workers-types` versions are deterministic between local and CI. A second SBOM (`cf-worker-sbom.cyclonedx.json`, ~470 KB / 209 components — npm dep trees are deeper than pip's) is generated by `npm run sbom` (which calls `@cyclonedx/cyclonedx-npm`) and auto-fires before every `npm run deploy` via the `predeploy` script. The SBOM file is in `.gitignore` since it's regenerable from the lockfile any time.

**CI-time CVE scanning.** `.github/workflows/ci.yml` has an `osv-scan` job pinned to `google/osv-scanner-action@9a498708959aeaef5ef730655706c5a1df1edbc2` (v2.3.8) that reads both lockfiles and queries the OSV database on every push and pull request. Findings upload to the GitHub Security tab via SARIF. Currently advisory-only (`fail-on-vuln: false`) because four dev-only CVEs flow through Wrangler 3.x (three moderate `esbuild`, one high `undici` — they affect only the local `wrangler dev --local` dev server, not the deployed Cloudflare-edge worker, but a Wrangler 4.x upgrade is a breaking change and is tracked separately). Flipping the flag to `true` once the known noise is resolved makes unknown new CVEs gate merges.

**Compliance posture.** This setup meets the structured-SBOM requirement of US Executive Order 14028 and the form expected by hospital / pharma / defence procurement reviews. The EU Cyber Resilience Act (in force 2027) will require similar documentation for any networked product sold in the EU. Alpha-OSK now has the artefacts ready before the requesters appear.

---

## 8. Evaluation, Known Gaps, and Future Work

### 8.1 In-app analytics dashboard

`src/analytics.py` records the lifetime counters that drive the in-app analytics dashboard and the §5.5 telemetry payload. Counters are session and `_alltime_*` paired; persisted to `<config_dir>/analytics.json` on shutdown and on explicit save; loaded at launch and incremented in-place. Persisted fields: keystrokes, words, predictions, keystrokes_saved, sessions, minutes, backspaces, prediction_offers, prediction_rank_sum, prediction_rank_count, top_pick_count, plus capped word_freq and key_freq Counters (top 5 000 retained on save so the file stays bounded over years of typing).

The dashboard (`qml/components/AnalyticsDashboard.qml`) presents four impact tiles in a single 2×2 grid with a Lifetime / This Session toggle:

- **Keystrokes Saved** — absolute count, the headline number ("keys you didn't have to press").
- **Time Saved** — `keystrokes_saved × user's own seconds per keystroke` (`alltime_minutes × 60 / alltime_keystrokes`, fallback 0.5 s/key for new installs). Using the user's own pace makes the number honest: a slow OSK user genuinely saves more wall-clock time per avoided keystroke than a fast one.
- **Effort Saved** — savings as a percentage of total typing effort (`keystrokes_saved / (keystrokes + keystrokes_saved)`), the percentage view of the same engine value as Time Saved.
- **Acceptance** — `prediction_hits / prediction_offers`, asking "when the keyboard offered a suggestion, how often was it useful enough to take". Distinct from the keystroke-share metric Effort Saved measures.

Earlier builds also surfaced a composite 0–100 "Prediction Quality" score (weighted: 40% savings, 25% hit rate, 20% rank-1 accuracy, 15% low backspace rate). It was removed in 1.1.0 because the number wasn't actionable: a user can act on "you've saved 4.2 hours" or "67% of your picks were the first suggestion" but a "73/100" composite hides which lever moved. Per-component metrics are still tracked and exposed in `getAnalytics()` (`predictionHitRate`, `topPickRate`, `backspaceRate`, etc.) for the Model Visualization panel and downstream callers; only the composite was retired. Don't reintroduce the composite as a primary surface; if a single internal scoring number is needed for ranking-strategy comparisons, compute it ad-hoc in tests rather than baking it back into `get_session_stats`.

These metrics track regressions and improvements over time but are not a substitute for benchmark comparisons against other keyboards. We do not yet have a published benchmark; building one is open work.

### 8.2 Known gaps relative to commercial keyboards

In rough priority order:

1. **Unified prediction-and-correction scoring.** LatinIME and Gboard score the literal typed word and all corrections in a single ranked list with a shared probability scale. The two-tier autocorrect threshold (§3.7) is a partial proxy; full unification is the proper fix and would clean up several classes of edge cases (deliberate-typing protection, low-confidence corrections that currently surface as suggestions but should not).
2. **Spatial edit costs in final ranking.** Key-distance weights from `fuzzy_recognizer` currently feed candidate *generation* but not the final rank. Folding them into the merge layer would let the engine prefer "the" over "rhe" in ambiguous contexts based on the fact that `r` is far from `t` in QWERTY.
3. **Katz / stupid backoff for sparse contexts.** Linear interpolation gives `λ₃·P_tri = 0` weight to the trigram term when the trigram has never been seen, which is correct but pessimistic — Katz backoff would discount seen events and redistribute mass to lower-order fallbacks. Larger-lift change (~100 lines) with a measurable quality gain on rare contexts.
4. **Larger seed corpus.** The ~750 / ~740 curated bigram/trigram lists are hand-audited but small. Seeding from COCA top-100k bigrams or Google n-gram exports would dwarf them. Easy win, no algorithm changes — just more data.
5. **Vocabulary-pack disable undoes injection.** The current `PackManager.disable_pack` only clears the pack's own in-memory copy; it does not revert the per-word entries the pack pushed into the predictor's `unigrams` / `bigrams` / `trigrams` (which were merged with `max()`, see §3.1.2). Toggling a pack off therefore leaves its words ranking until the process restarts. Effectively invisible today because no built-in packs ship and very few users import their own; becomes a real correctness issue if built-ins return or imports become common. The clean fix is to track per-pack `(word, prior_value)` tuples at apply time and revert on disable, guarded so words that organic learning piled on top of (current value > pack's contribution) are not clobbered.

#### Closed gaps

- ~~**Structured CycloneDX SBOM at release time.**~~ Implemented (§7.4). `build/{windows,linux}/build.py::emit_sbom` writes a CycloneDX 1.6 SBOM alongside the plaintext lockfile on every build via `python -m cyclonedx_py environment --output-reproducible`. The worker side uses `@cyclonedx/cyclonedx-npm` via `npm run sbom`, chained before `wrangler deploy` by a `predeploy` script. A CI `osv-scan` job pinned to `google/osv-scanner-action` v2.3.8 reads both lockfiles and queries OSV on every push/PR (advisory-only initially while known Wrangler-3.x dev CVEs are quarantined; will flip to gating once resolved). Meets the structured-SBOM bar that US Executive Order 14028 and most hospital / pharma / defence procurement asks for.
- ~~**SymSpell for fuzzy matching.**~~ Implemented. `src/prediction/symspell.py` provides a precomputed-deletion index with Damerau-Levenshtein post-filter. The previous candidate generator was capped at edit distance 1 and did not enumerate substitutions; the new path defaults to edit distance 2 and adds substitution coverage, so two-edit corrections (e.g. "becouase" → "because") and non-adjacent substitutions (e.g. "rxample" → "example") now surface. Lookup latency dropped from ~30 ms to ~0.75 ms on the 10K-word base dictionary; one-time index build is ~216 ms, paid eagerly during `FuzzyWordGenerator.set_frequencies` so the cost lands in startup instead of the user's first keystroke. See §3.6 for the integration; `tests/test_symspell.py` for the algorithm-level tests.

### 8.3 Federated learning

Federated learning would let users contribute to a shared model without sending raw keystrokes anywhere. The design is in `docs/FEDERATED_LEARNING.md`. Phase 1 (local delta computation — the user's machine produces a "diff" against the base model that summarises learned vocabulary) is the next step. Phases 2 and 3 (secure aggregation, differential privacy budgets) follow.

The motivation is strongest for the disability-community vocabulary case: users with rare conditions, specific medical equipment, or specialised AAC needs benefit disproportionately from shared vocabulary, but the same users have the strongest privacy concerns about raw keystroke data. Federated learning is the standard answer.

### 8.4 Ecosystem integration

Alpha-OSK is one of four tools in an adaptive-input platform (see `docs/ECOSYSTEM.md`):

| Tool | Output |
|------|--------|
| Alpha-OSK | Keystrokes (SendInput / xdotool) |
| MacroVox | Text (Deepgram speech-to-text → clipboard) |
| Octavium | MIDI (virtual piano / pads) |
| Nimbus | Joystick (vJoy / ViGEm) |

All four target the same mouse-driven, accessibility-first user. Integration phases progress from coexistence (today) → cross-launch and trigger → profile auto-switch → shared input layer → unified UI. The `docs/MACROVOX_INTEGRATION.md` and `docs/MODULAR_LAYOUTS.md` documents cover specific integration paths.

---

## 9. Conclusion

Alpha-OSK is what happens when an accessibility-first OSK is built from scratch with a hard requirement that prediction quality match modern mobile keyboards on commodity hardware without a cloud round-trip. The architecture (a Qt Quick UI on top of a Python bridge, with a hybrid n-gram + PPM + fuzzy prediction stack) is conventional in its parts, but the constraints from the user population (no focus stealing, no lost modifiers, no destructive prediction insertion, no GPU, off-network by default with the only optional egress being the explicitly opted-in usage-stats pipeline) shape the implementation in ways that diverge consistently from how a mainstream keyboard would be built.

The prediction stack is honest about its limits. It does not match Gboard's quality on rare contexts, it does not yet have unified scoring that lets the literal typed word compete against corrections in a single ranked frame, and the seed corpus is small. Each of these has a documented path forward and a rough cost estimate. None of them require fundamentally rethinking the architecture.

The accessibility-driven engineering decisions — the non-focus invariant, sticky modifiers held at the OS level, suffix-only prediction insertion, the right-click shifted variant, the edit-popup pattern — are the part of the work that does not appear in textbooks. They are also the part most likely to transfer to other accessibility tools building on the same hardware target.

---

## References and Further Reading

### Internal design docs

- `docs/PPM.md` — Variable-order character model with PPMD escape.
- `docs/FUZZY_RECOGNITION.md` — Spatial model and tunable constants.
- `docs/HYBRID_MERGING.md` — Merge weights, validation, capitalisation pipeline.
- `docs/SWIPE_TYPING.md` — Shape-matching swipe decoder.
- `docs/AUTO_UPDATE.md` — Update flow, threat model, defences.
- `docs/TELEMETRY.md` — Opt-in usage stats: payload schema, anon_id lifecycle, backend, deployment workflow.
- `docs/PRIVACY.md` — User-facing data policy.
- `docs/PLATFORM_ARCHITECTURE.md` — Cross-platform abstraction details.
- `docs/FEDERATED_LEARNING.md` — Federated-learning roadmap (separate from §5.5 telemetry; not yet implemented).
- `docs/ECOSYSTEM.md` — Four-tool adaptive-input platform.
- `docs/SECURITY_AUDIT.md` — Pack-import hardening, model load caps.
- `docs/LINUX.md` / `docs/WINDOWS.md` — Platform-specific build and packaging.

### External references

- Vescovi, M. *Presage: An intelligent predictive text entry platform*. presage.sourceforge.io. Open-source predictor library; influence on the layered hybrid approach (multiple predictors merged by linear interpolation).
- Cleary, J. G., Witten, I. H. (1984). *Data Compression Using Adaptive Coding and Partial String Matching*. IEEE Transactions on Communications, 32(4), 396–402. Original PPM paper.
- Garbe, W. (2012). *1000× faster spelling correction algorithm*. github.com/wolfgarbe/SymSpell. Symmetric Delete approach; future-work reference.
- AOSP LatinIME source. Reference implementation for trie-based dictionary, weighted edit distance, n-gram LM scoring.
- Kristensson, P. O., Zhai, S. (2004). *SHARK²: A large vocabulary shorthand writing system for pen-based computers*. Proceedings of UIST '04, ACM. Inspired the swipe decoder.
- Microsoft. *UI Automation overview*. learn.microsoft.com/en-us/windows/win32/winauto/. Used for password-field detection.
- AT-SPI 2. *Accessibility Toolkit Service Provider Interface*. Used for Linux password-field detection.

---

*Alpha-OSK is developed by Owen Kent. Source repository: github.com/okstudio1/alpha-osk. Public releases: github.com/okstudio1/alpha-osk-releases.*
