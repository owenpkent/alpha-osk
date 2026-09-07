# Alpha-OSK: A Predictive On-Screen Keyboard for Motor-Impaired Users

**Document revision:** 2.0 (September 2026), describing Alpha-OSK 1.3.0
**Audience:** Software engineers, accessibility researchers, assistive-technology practitioners

> *App version references inline (e.g. "1.1.0+" in §5.6) denote the first release in which a feature ships. The single source of truth for the current installed version is `src/__version__.py`.*

---

## Abstract

Alpha-OSK is an on-screen keyboard (OSK) for Windows and Linux designed for users whose primary input device is a mouse or pointer rather than a physical keyboard. It targets people with motor impairments (muscular dystrophy, ALS, spinal cord injury, severe arthritis, post-stroke hemiparesis) for whom every avoided keystroke is meaningful. The system pairs a Qt Quick (QML) UI with a Python bridge, synthesises keystrokes through OS-native APIs (`SendInput` on Windows, `xdotool`/`ydotool` on Linux), and runs a hybrid CPU-only prediction engine (word n-gram plus a spatial model that completes a typed prefix through a mis-clicked key) that learns the user's vocabulary on-device. There is no GPU, no cloud round-trip, and no LLM dependency. The system is **off-network by default**: predictions, learning, and analytics are local-only. The one optional egress is an off-by-default usage-stats client that would submit ten lifetime counters weekly and never any content; its endpoint is undeployed, so it currently no-ops every submit (§5.6).

On held-out data from a crowdsourced corpus of AAC-like communications, the engine avoids 49.1% and 50.4% of keystrokes on the two splits, offering the correct next word before any of its letters are typed about three times in ten, at a median 2.9 ms per keystroke. Two of our results are negative and are reported as such: on clean input the spatial layer is worth nothing, and the character model we inherited from the Dasher lineage contributed nothing to word prediction while costing five times the latency. The spatial layer earns its place only under pointer error, where it is worth eight to nine points of keystroke savings, which is the condition this population actually types in. This paper describes the architecture, the prediction stack, the accessibility-driven engineering trade-offs that shaped the system, the privacy and security model, the evaluation, and its substantial limitations: there is no user study, and the system has one long-term user.

---

## 1. Introduction and Motivation

### 1.1 The accessibility gap in OS-bundled keyboards

The on-screen keyboards bundled with Windows (`osk.exe`) and Linux desktops (GNOME On-Screen Keyboard, Onboard) provide a baseline accessibility surface, but they predate the prediction quality that mainstream mobile keyboards (Gboard, SwiftKey, iOS) have offered since the early 2010s. For an able-bodied user typing on glass, weak prediction is a minor annoyance; for a wheelchair user controlling a mouse with limited range of motion, weak prediction directly costs typing throughput, fatigue budget, and (over a working day) the ability to communicate at all.

Mobile-grade prediction has not transferred to the desktop OSK category for two structural reasons. First, mobile keyboards are tightly coupled to their operating systems' input method frameworks; the desktop equivalents (Windows TSF, Linux IBus/Fcitx) target IME use cases (CJK input) rather than augmentative communication. Second, the major mobile prediction stacks (LatinIME, SwiftKey's proprietary engine) are either Android-only or closed source, and the open-source desktop tools that approach their quality (Presage) have stalled.

### 1.2 Design goals

Alpha-OSK is shaped by five goals, in priority order:

1. **The OSK must never steal focus from the target application.** A focus loss to the keyboard is not just an annoyance. It can drop a modifier, abort a drag, or lose the user's place in a long composition.
2. **Predictions must be useful from the first keystroke.** A user who can only type 5–10 words per minute cannot afford a "warm-up" period where the engine learns their vocabulary before earning its keep.
3. **Local-first: no cloud round-trip on the prediction or learning path; no GPU; no LLM.** The system must run on the modest hardware motor-impaired users typically inherit (older laptops, low-power desktops). Keystrokes must never be exfiltrated. This is a category where data sensitivity is unusually high (passwords, medical communication, intimate correspondence). The community-impact pipeline added in 1.1.0 (§5.6) is the only optional egress, is gated on explicit user consent, and is currently a no-op because the submission endpoint is not yet deployed; when the endpoint is configured in a future release, opting in would submit only the lifetime counters the user already sees on the in-app dashboard, never content.
4. **Spatial errors must be corrected without punishing deliberate typing.** A user with hand tremor will land off-centre on keys; a user typing "thru" deliberately must not be autocorrected to "throw".
5. **Every interaction must be reachable from the mouse.** Keyboard shortcuts, modal dialogs that require Enter, and physical-keyboard fallbacks are non-options.

### 1.3 Related work

Four bodies of work bear directly on this system, and the gaps between them are where it sits.

**Rate enhancement in AAC.** Word prediction as an access technology, rather than as a convenience, comes out of the augmentative and alternative communication literature (Higginbotham et al., 2007). That literature is also the source of the field's central methodological caution: keystroke savings measured by simulation is an upper bound on benefit, not a measure of it, because selecting a prediction has a cognitive and visual-search cost that typing the next letter does not (Trnka and McCoy, 2008). We adopt their keystroke-savings rate as our primary metric and inherit that caveat explicitly; section 9 states what our numbers do and do not claim.

**Statistical decoding of noisy input.** Modern mobile keyboards do not treat a touch as a keypress; they treat it as evidence, and decode a whole word or sentence against a joint spatial and language model (Kristensson and Zhai, 2004; Vertanen et al., 2015). Alpha-OSK's mid-word prefix beam (section 3.1) is a small member of that family: a beam over live dictionary prefixes with a Gaussian emission over key positions. The difference is the input device. VelociTap decodes a finger on glass, where the noise is large, roughly isotropic and drawn from a population of users. Our noise comes from one pointer, driven by one person's motor system, and is therefore small, systematically biased, and learnable per user. Section 8.4 measures what that last property is worth, and the answer is less than one might expect.

**Character-level models for accessible input.** Dasher (Ward et al., 2000) established the variable-order character model as the workhorse of accessible text entry, using the PPM construction of Cleary and Witten (1984). Alpha-OSK trains the same class of model, and section 8.3 reports the result of taking its word candidates out of the merge, which is one of the few places where our measurements disagree with an inherited assumption.

**Production keyboard stacks.** Presage (Vescovi) supplies the layered framing this engine follows: several redundant predictors, merged. AOSP's LatinIME supplies the reference treatment of weighted edit distance and of letting the literal typed word compete against corrections. SymSpell (Garbe, 2012) supplies the deletion-index lookup used in the whole-word correction path.

**What is not covered by any of them.** All four assume the software may hold input focus. A desktop on-screen keyboard cannot: it is a window on the same desktop as the application being typed into, and taking focus to accept a click would take it away from the text field the keystroke is destined for. Nearly every non-obvious decision in section 4 descends from that one constraint, and we are not aware of a published treatment of it. The desktop OSK category itself is thinly covered: the systems users actually have (`osk.exe`, GNOME's on-screen keyboard, Onboard) predate the prediction quality described above, and the open-source tools that approach it have stalled.

### 1.4 What this paper covers

Section 2 lays out the runtime architecture and the two boundaries that dominate the design (QML to Python, and Python to the OS). Section 3 describes the prediction engine in depth. Section 4 walks through the accessibility-driven engineering decisions, which is the material least represented in the literature. Section 5 covers privacy and security, section 6 the resource envelope, and section 7 distribution. Section 8 is the evaluation, section 9 states its limitations, and section 10 enumerates the open work. The deeper algorithm-level design documents are cross-referenced inline rather than reproduced.

<p align="center">
  <img src="../assets/screenshots/dark-theme-keyboard.png" alt="Alpha-OSK full keyboard in the Dark theme, showing function row, QWERTY block, navigation cluster, and numpad." width="900" />
  <br /><em>Figure 1. Full keyboard surface. Left to right: function row (F1–F12), QWERTY block with sticky modifier keys, navigation cluster (PrtSc / ScrLk / Pause / Ins / Home / PgUp / Del / End / PgDn / arrows), and numpad. Title bar carries (right side) the update indicator, the Learning privacy switch, settings, minimize, and close; the clear-context button (⟲) sits at the right end of the suggestion bar. Width is user-resizable from either edge; height auto-fits content. This figure is from 1.1.0 and is retained because the surface it shows is unchanged in layout; 1.3.0 adds a Delete key to the QWERTY row, a switch-style Learning control, and microphone and picker buttons at the ends of the suggestion bar.</em>
</p>

---

## 2. System Architecture

### 2.1 Process model

Alpha-OSK is a single user-mode process. There is no daemon, no background service, no IPC across process boundaries. The process owns:

- A Qt Quick UI thread rendering the keyboard surface (`qml/Main.qml` and the components under `qml/components/`).
- A Python "bridge" object (`src/keyboard_bridge.py`) that holds the prediction engine, the modifier state machine, the context buffer, and the per-platform key synthesiser.
- Three long-lived `QTimer` instances: a 200 ms password-field poller (Windows), a 250 ms foreground-window poller driving context resets across app and focus changes, and a 50 ms outside-click poller (Windows, `src/platform/pointer.py`) that catches the caret moving inside a single window where the accessibility signals report nothing.

The process never elevates voluntarily. On Windows, the launcher (`run.py`) intentionally avoids `runas` and the build pipeline produces an installer that drops a UIAccess-marked executable into a Program Files subdirectory. UIAccess lets the OSK inject input into elevated target windows without itself running elevated, preserving the sandboxing properties of medium integrity level.

### 2.2 The QML ↔ Python bridge

QML drives all rendering and gesture detection. Python owns all state and side effects. The boundary is a single `QObject` subclass (`KeyboardBridge`) exposed to QML via `setContextProperty("keyboard", bridge)` at startup. Every interaction follows the same pattern:

1. QML invokes a `@Slot`-decorated method (`pressKey`, `pressSpecialKey`, `editPrediction`, …).
2. The slot mutates Python state, calls into the platform synthesiser, and may invoke the prediction engine.
3. The bridge emits a `Signal` (`predictionsChanged`, `capsLockActiveChanged`, `editKeyTyped`, …).
4. QML bindings react and re-render.

This is deliberately the *only* coupling between the layers. There are no shared Qt models, no `QQmlListProperty`, no QML access to Python attributes other than `@Property`-decorated ones. The reason is testability: the Python side has around 2,250 pytest tests that exercise prediction, capitalization, modifier semantics, and persistence without spinning up a Qt event loop.

### 2.3 Platform abstraction

The platform abstraction lives in `src/platform/` and consists of three concerns: key synthesis, password-field detection, and configuration paths.

`src/platform/base.py` defines the abstract `KeyboardSynthesizer` interface:

- `send_text(text)`: emit a stream of characters (used for prediction insertion, snippets).
- `send_key(name, modifiers=None)`: emit one named key, optionally chorded with modifiers.
- `hold_modifier(name)` / `release_modifier(name)`: pin a modifier at the OS level. This is what enables Shift+drag in the target app, Ctrl+click on hyperlinks, and so on. Without it, sticky modifiers would only attach to the next synthesised key, not to the user's *physical* mouse interactions.
- `replace_text(prefix_length, replacement)`: used only when a clicked prediction's casing diverges from the typed prefix.
- `reset_modifier_state()`: called once at startup to release any modifier left held by a crashed prior instance.

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

The directories hold `models/ngram_model.json` (learned vocabulary, capitalisation, suppression flags, structured tokens), `models/ppm_model.json` (character-level n-gram trie), `analytics.json` (lifetime stats. See §5), `snippets.json` (user quick-insert text), and `dictation.json` (dictation settings and, where the platform offers it, an OS-encrypted recogniser API key. See §4.10). The last of those is deliberately excluded from the Data Backup archive (§5.5) for the same reason the telemetry identifier is: an archive is carried between machines and handed around, which is the last place a credential belongs. User-imported vocabulary packs go to `packs/` under the same root. The application ships with **no built-in packs**. Earlier releases shipped six (medical, programming, academic, gaming, business, NSFW) but each was 200–400 words, ~30× smaller than the base wordlist, and personal learning caught up within minutes. The current system is import-only; the rationale and pack format are documented in §3.1.2.

The application *never* writes to its own install directory at runtime. This invariant matters for Program Files installs (where writes would silently redirect to VirtualStore) and for AppImage packaging (where the bundle is read-only).

---

## 3. The Prediction Engine

The prediction engine is the most novel component of the system and the most salient to the user. It lives in `src/prediction/`. The design follows the layered approach pioneered by Presage (Vescovi) (multiple predictors with different strengths, merged by linear interpolation) extended with a spatial recognition layer borrowed in spirit from LatinIME.

### 3.1 Component overview

| Component | Role |
|-----------|------|
| `ngram_predictor.py` | Word-level unigram/bigram/trigram model. The dominant signal source. |
| `ppm_predictor.py` | Character-level variable-order Markov model (PPMD escape). Predicts next characters within a partial word. Trained and persisted, but its word candidates have been out of the merge since 2026-09-03 (see `architecture/PPM.md`). |
| `fuzzy_recognizer.py` | Spatial error correction. Considers nearby keys as substitution candidates and applies edit-distance penalties. Mid-word it completes the typed prefix through a beam over live dictionary prefixes (`prefix_beam.py`). |
| `hybrid_predictor.py` | The merge layer. Runs the n-gram and fuzzy predictors in parallel (PPM's word list is empty unless `_ppm_in_merge` is switched on), normalises scores, applies suppression and capitalisation, returns up to N suggestions. |
| `token_predictor.py` | Verbatim completion for the strings the word model cannot represent at all: phone numbers, zips, house numbers, email addresses. See §3.1.2. |
| `vocabulary_pack.py` | User-imported domain vocabulary packs that contribute weighted unigram/bigram/trigram counts into the n-gram tables. No built-in packs ship; the system is import-only (see CLAUDE.md *Vocabulary Packs* for the rationale). |
| `symspell.py` | Precomputed-deletion edit-distance index used by the fuzzy recogniser. Built eagerly at startup; per-query lookup runs in well under 1 ms on a 10 K dictionary. See §3.6. |
| `transformer_predictor.py` | Optional LLM re-ranking pass. Disabled by default; not on the hot path. |

For deep design treatments of each, see `architecture/PPM.md`, `architecture/FUZZY_RECOGNITION.md`, and `architecture/HYBRID_MERGING.md`.

#### 3.1.1 Why layered

Each predictor in isolation has a complementary failure mode, and the merge layer is what makes the whole stack stronger than any of its parts. A pure n-gram model is excellent at "the user just typed *I want*. They probably want *to*", but it has no notion of partial words, so it cannot complete *th* into *the*. A pure PPM character model is excellent at completing partial words, but it sees the world as a stream of characters, not words, so it readily produces suffixes that match no real word ("th" → "throu" rather than "through"). A pure fuzzy/spatial recogniser corrects mis-taps but is context-blind: after typing "of ", it cannot tell *the* from *thy*. Both are spatially plausible neighbours of whatever the user meant. Merge them and each layer's strength covers the others' blind spots: the n-gram supplies context, PPM supplies prefix-completion, fuzzy supplies typo-tolerance.

This layered approach mirrors Presage's "natural language as a combination of redundant information sources" framing and the merge model used in LatinIME and Gboard. The Alpha-OSK contribution is not the layering itself but the specific weights, the cold-start strategy (§3.3), the fragment filter (§3.4), and the spatial-bigram cross-talk in the merge (§3.6). Each tuned for the OSK use case where a single predictor's wrong answer costs the user real fatigue.

#### 3.1.2 Per-component internals

**`NgramPredictor` (`ngram_predictor.py`).** Holds three plain dicts: `unigrams: Dict[str, int]`, `bigrams: Dict[str, Dict[str, int]]`, and `trigrams: Dict[str, Dict[str, int]]` keyed by `"w₁ w₂"`. It also owns the capitalisation table (`Dict[str, str]`), the blacklist set, the dispreference counter, and a `_user_total` invariant that must equal `sum(user_vocab.values())` after every mutation (§3.4 detail). `predict(context, n)` performs the linear-interpolated rank described in §3.2; `learn(word)` updates frequencies through the fragment filter and repetition gate; `get_capitalized(word, sentence_start)` is the export hook used by the merge layer to apply the three-tier capitalisation model. This file is the largest by volume in the prediction package because every persistent piece of user data lives here.

**`PPMPredictor` and `PPMWordPredictor` (`ppm_predictor.py`).** Two cooperating classes. `PPMPredictor` is the raw character model. A variable-order Markov chain with PPMD escape, following Cleary & Witten's original construction. It learns from a stream of characters and predicts a probability distribution over the next character given the last N (where N is the model order, default 5). `PPMWordPredictor` wraps it: it walks the character distribution into the most likely full-word completions of the partial prefix the user has typed. The wrapping matters because the merge layer takes word predictions, not character distributions.

**`FuzzyRecognizer` (`fuzzy_recognizer.py`).** Owns a `SpatialKeyModel` (Gaussian distribution over neighbouring keys keyed by Euclidean distance on the QWERTY layout, σ = `spatial_uncertainty / 2`, default σ = 0.7) and a `FuzzyWordGenerator` that combines the spatial beam search with a SymSpell-backed edit-distance lookup (`src/prediction/symspell.py`). The per-edit penalties quoted in §3.6 are the recogniser's own (`fuzzy_recognizer.py`), not the index's. Two query paths: `get_fuzzy_predictions(context, n)` returns ranked candidates for the merge, and `should_autocorrect(typed, candidate)` runs the two-tier threshold (§3.7) when the system has to decide whether to *commit* a correction (e.g. on space) versus merely *suggest* it. The recogniser is the most numerically tuned component. Its constants are spelled out in §3.6.

**`HybridPredictor` (`hybrid_predictor.py`).** The orchestrator. Holds references to one `NgramPredictor`, one `PPMWordPredictor`, one `FuzzyRecognizer`, one `PackManager`, and (optionally) one `TransformerPredictor`. The `predict()` entry point runs the n-gram and fuzzy predictors in sequence (PPM's word list has been empty by default since 2026-09-03; `_ppm_in_merge` restores it), and it runs them (not in threads. Python's GIL would defeat parallelism here, and each predictor is fast enough that single-threaded sequential is simpler and still meets the <30 ms latency budget). It then dispatches to a strategy-specific scorer (`_score_rank` / `_score_rrf` / `_score_linear` / `_score_loglinear`) keyed on the user's selection in *Settings → Smart Typing → Suggestion Engine*; default is `rank`. Source weights are shared across every strategy (`_source_weights` returns 3.0/0.3/0.6 for next-word, 1.0/0.8/0.6 for mid-word completion). The default rank strategy scores each candidate as the sum of `weight / (rank + 1)` from every source it appeared in. Consensus boost (RRF) substitutes `weight / (60 + rank + 1)` so the rank-1 vs rank-2 gap shrinks and consensus across sources matters more. Confidence-weighted (linear) normalises each source's raw scores into a sum-to-1 distribution and combines `Σ w_i · P_i(w)`. Multiplicative (log-linear) does the same per-source normalisation but combines `Σ w_i · log P_i(w)` with a 1e-6 floor for words missing from a source. Equivalent to `Π P_i(w)^w_i`. Klakow (1998) showed log-linear beats linear interpolation by ~20% relative perplexity on n-gram smoothing. Across every strategy, the spatial-bigram cross-talk (`1 + log1p(bigram_count) / 2`) re-weights fuzzy candidates against the previous word's bigram table. The only context signal fuzzy has access to. Dispreference penalties divide the score by `(1 + count · 0.5)` and capitalisation is applied last (`_finalize_scores`), so all internal scoring is case-insensitive. Full strategy trade-offs and migration history live in `architecture/HYBRID_MERGING.md`.

**`VocabularyPack` and `PackManager` (`vocabulary_pack.py`).** A pack is a folder of plain-text files: `dictionary.txt` (one word per line), optional `bigrams.txt` (whitespace-separated pairs), `trigrams.txt`, and a `pack.json` metadata stub. `PackManager` enumerates packs from the user's import directory (`%APPDATA%/alpha-osk/packs/` on Windows, `~/.config/alpha-osk/packs/` on Linux), tracks which are enabled, and on enable calls `apply_to_predictor(ngram)` to write the pack's contents *into* the n-gram model's tables. All three tables are merged with `max(existing, pack_weight)` (`PACK_UNIGRAM_WEIGHT = 3`, `PACK_BIGRAM_WEIGHT = PACK_TRIGRAM_WEIGHT = 30`) so a pack never flattens an organically-learned high-frequency word. The pack establishes a floor, the user's own typing can climb above it. The architectural point: packs are not an independent predictor at merge time. They are a *modifier* of the n-gram tables, applied at enable-time. This is why the merge layer sees three predictors (n-gram, PPM, fuzzy), not four.

The system is **import-only as of 1.1.0+**. Earlier releases shipped six built-in packs (medical, programming, academic, gaming, business, NSFW) but each was 200–400 words. Too thin to compete with the engine's organic learning, which bumps a word's score by +5 every time the user accepts it as a pill. Sourcing real domain vocabularies (SNOMED-grade for medical, full API surface for programming) is its own project and runs into licensing rabbit holes; curated 300-word lists were strictly worse than no shipped packs at all. The import path is preserved so power users with a real domain wordlist (a nurse with her unit's drug list, a researcher with citation keywords) can still drop a folder in. A known limitation, documented in `CLAUDE.md`: disabling a pack does not currently undo its predictor injection. `apply_to_predictor` uses `max()`, `disable_pack` only clears the pack's own in-memory copy, so injected words persist until the next process restart. Mostly invisible with no built-ins shipping; the fix (track per-pack contributions, revert on disable, guard against clobbering organic learning that piled on after enable) is open work.

**`TokenPredictor` (`token_predictor.py`).** Outside the merge, and for a structural reason rather than a stylistic one. `NgramPredictor._tokenize` splits on `[a-zA-Z']+`, so every digit and symbol is discarded before a word reaches the vocabulary. That is the correct choice for a word model, and it means the stack described above cannot represent a phone number, a postcode, a house number or an email address at all. Those are among the strings a user retypes most often, and among the most expensive to produce with a pointer, so the omission was a blind spot rather than a simplification. `TokenPredictor` is a flat, count-weighted store of whole tokens matched by prefix, persisted under a `tokens` key inside `ngram_model.json`.

Two omissions are load-bearing. It carries **no context model**: the signal that matters ("this is the number I always type") is already expressed by the count, and a bigram over tokens would be estimating a distribution from a handful of observations. And it does **no fuzzy matching**, which is the more interesting one, because the rest of §3 is largely about tolerating mis-taps. Correcting a mistyped *letter* is a favour to the user; "correcting" a *digit* silently substitutes a different number, and a phone number one digit out is strictly worse than no suggestion, because the error is invisible at the point it is made. Error tolerance is not a universally desirable property. It is desirable exactly where the space of valid outputs is dense and the cost of a wrong one is low, and neither holds here.

Nor is it merged into the ranked candidate list. Mid-token (`owen@gm`, `555-123-`) no English word is a plausible continuation, so the two are mutually exclusive rather than competing, and `KeyboardBridge._in_token_context()` selects a bar rather than blending sources. Admission is gated by `text_patterns.is_learnable_token`, which shares a module with the intelligent-spacing and snippet-detection matchers so that "what does an email address look like" has a single answer in the codebase. That gate is deliberately asymmetric: a rejected token costs one suggestion the user never sees, while an accepted one is offered back in the UI and carried in the Data Backup archive, so any digit run longer than eight digits that is not shaped like a phone number is refused wholesale rather than by enumerating which classes of identifier are sensitive. See §5 for the privacy treatment.

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

$$\mathrm{score}(w) = \lambda_3 \, P(w \mid w_{-2}, w_{-1})
+ \lambda_2 \, P(w \mid w_{-1})
+ \lambda_1 \, P_{\mathrm{uni}}(w)$$

with λ = (0.5, 0.3, 0.2). All three terms live in probability space, so a strong bigram signal can override a higher-frequency unigram. After typing "I want", "to" beats "the" because `P(to | want)` ≫ `P(the)`. This is the textbook Jelinek–Mercer interpolation applied at three orders.

When there is no preceding word (start of input, or after a context reset), the trigram and bigram terms collapse to zero and `P_uni` is taken at full weight, so partial-prefix completion is not attenuated.

**The context tables are two tables, not one.** Each of `bigrams` and `trigrams` is a merged view over a *base* share, rebuilt from the shipped data files at every launch, and a *user* share, which is the only part persisted. The split exists because the single merged table was both persisted and re-seeded on top of itself at each launch, so seed counts grew without bound: a live model held `i -> want` at 1,037 against 63 on a fresh install. Worse, a personal phrase could not compete with the seeds at all. `the bus`, typed once, scored 0.0004 against 2,600 of seed mass and would have needed 55 consecutive typings to surface, which at once a day meant never. Scoring now trusts the user's distribution for a prefix in proportion to its evidence, blending it with the base distribution at weight `U / (U + 5 + 0.02 B)` for user evidence `U` and base count `B`. With no user evidence this is the plain interpolated row above, byte for byte, which is what makes a fresh install score identically to the previous design; with it, a phrase reaches the suggestion bar after two typings. Decay retires only the user share, so the curated seeds survive however long a session runs, and a model file written by an older build is adopted wholesale as user history rather than being split after the fact, which keeps every existing ranking intact across the upgrade. An earlier implementation added bigram and unigram counts in raw frequency space (`freq·2` for bigram, `p·100_000` for unigram), which made unigram dominate by three orders of magnitude. Bigram evidence was effectively dead weight. The fix to a proper interpolated formula is one of the larger quality wins in the engine.

### 3.3 Cold-start signal: seeded context tables

Linear interpolation only helps when the higher-order tables have data, so the base share of §3.2 is built from two sources at every launch.

A small curated layer, hand-audited, covers conversational English skeletons: `data/common_bigrams.txt` (~750 pairs at weight 50) and `data/common_trigrams.txt` (~740 triples at weight 50, plus 10 reinforcement on each of the two internal bigrams `w₁→w₂` and `w₂→w₃`).

A generated layer, added in 1.3.0, supplies the bulk: `data/seed_bigrams.txt` covers 13,437 contexts over 65,694 edges, and `data/seed_trigrams.txt` a further 20,000 contexts, 125,302 edges in total. Both are pruned from the forum language model published alongside this paper's evaluation corpus (Vertanen and Kristensson, 2011, CC BY 4.0), restricted to the words this keyboard ships, then to the most frequent contexts, then to the top continuations of each: five for bigrams, three for trigrams, on the reasoning that a five-pill bar cannot show a longer tail. That is 2.56 MB on disk, 17 MB resident, and 414 ms of the startup budget. §8.3 measures what they are worth: +1.8 and +1.7 points of keystroke savings on the two held-out splits, and +5.8 and +5.2 points of next-word hit rate, which is the largest single quality win in the engine's history.

Neither layer is ever written back to disk. Both are rebuilt from the data files at launch, which is the invariant that makes the base and user shares separable in the first place.

### 3.4 Fragment filter on learning

A naive learning policy ("any sequence of letters terminated by space is a word") makes the engine rapidly unusable. Real OSK input includes false starts, mistakes the user backspaced past but reformulated, accidental letter sequences from drags, and miss-tapped key fragments. The engine therefore applies `_is_plausible_word` at three points:

1. **At learn time** (`NgramPredictor.learn`): fragments are rejected before entering the candidate pool.
2. **At seed-corpus load time**: the Google 10k and 20k supplement wordlists are scraped from web search corpora and contain every isolated letter of the alphabet plus several hundred two-letter abbreviations and state codes at high frequency. Without filtering, typing a single-letter prefix would surface a flood of one- and two-letter "words".
3. **At model load**: existing users' `ngram_model.json` files are scrubbed on first launch after the filter shipped, so historical fragment pollution gets cleaned up rather than persisted forever.

The filter rules: words of length ≤ 2 must be on a short whitelist (legitimate two-letter words like "is", "in", "to"); words of length ≥ 3 must contain both a vowel and a non-`aeiou` letter, with `y` counting as both (so "eye" and "cry" pass, but "aaaa" and "xqz" do not).

Surviving unknown words from `learn` go through a repetition gate: counted in `_candidate_counts` until 3 sightings, then promoted into `user_vocab`. Known base-dictionary words and explicit `learn_word()` calls bypass the gate. Candidate counts decay alongside user vocab and persist across save/load.

### 3.5 Capitalisation model

Pills only auto-capitalise the **`I` family** (`I`, `I'm`, `I'll`, `I'd`, `I've`). Every other word surfaces in the casing the user typed: type `monday` and the pill is `monday`; type `Monday` (one-shot shift) and the pill is `Monday`; type `MON` (right-click each letter) and the pill is `MONday`. `KeyboardBridge._display_cased` mirrors every uppercase position from the typed prefix onto the pill, and that's the only path that produces capitals in pills.

Earlier builds shipped a three-tier Gboard-style system (Tier 1 = `I` family; Tier 2 = sentence-start auto-cap for ~130 ambiguous names like `will`, `jack`, `may`, `mark`; Tier 3 = ~8 000 unambiguous proper nouns from `data/proper_nouns.txt` plus user-taught forms). Tiers 2 and 3 were dropped because they fired on common English words ("the hope is", "a rose by", "may I", and the post-period word in every sentence) and pills came back capitalised when the user had typed lowercase. The user-facing rule is now "shift / caps lock is the cap signal", with the `I` family kept as the one mid-sentence exception.

`NgramPredictor.capitalization` is still populated. `_load_proper_nouns` reads `data/proper_nouns.txt` at startup, and `learn_capitalization` records user-taught forms (right-click → Edit, prediction-click after typing a capital, word completion with non-trivial casing). All-uppercase typings are still rejected by default because the dominant cause of all-uppercase input is Caps Lock being on; deliberate all-caps (right-clicking each letter, Caps Lock off the whole word. The bridge tracks this with a `_word_typed_under_caps_lock` flag and passes `allow_uppercase = not _flag`) is allowed through. The accumulated dict is persisted to `ngram_model.json`. `get_capitalized` does not consult it today, but keeping the data lets a future opt-in toggle revive proper-noun cap without re-teaching from scratch.

### 3.6 Spatial fuzzy recognition

`fuzzy_recognizer.py` accepts a typed string and a layout (key positions in pixel space) and produces ranked correction candidates. Candidates come from two parallel paths:

1. **Spatial beam search** over the typed positions, considering each key plus its neighbours weighted by inverse distance. Beam width is bounded by `min_prob = 0.001`.
2. **SymSpell-backed edit-distance candidates** at edit distance ≤ 2 (`src/prediction/symspell.py`). For every dictionary word, deletion variants up to two deletions are precomputed and indexed; at query time the input's deletion variants are looked up against the same index, and a Damerau-Levenshtein post-filter confirms the actual edit distance. Each surviving candidate is scored with a per-edit-type penalty when the distance is 1 (`_TRANSPOSITION_PROB = 0.30`, `_DELETION_PROB = 0.20`, `_INSERTION_PROB = 0.15`, `_SUBSTITUTION_PROB = 0.18`, `_APOSTROPHE_INSERTION_PROB = 0.50`) and a flat `_DOUBLE_EDIT_PROB = 0.05` at distance 2. Apostrophe insertion is bumped to `0.50` because missing apostrophes ("im" → "I'm", "dont" → "don't") are by far the dominant insertion error in real OSK typing. The index is built eagerly at the end of `set_frequencies` so the ~200 ms one-time cost lands in startup latency rather than the user's first keystroke; per-query lookup runs in well under 1 ms on a 10K-word dictionary.

The two paths produce overlapping candidates, which are deduplicated by string and rescored against the unigram frequency table. The spatial path is best at near-key mis-taps (Gaussian neighbour probability picks them up at the candidate-generation level, scoring them by spatial proximity); the SymSpell path is best at non-spatial typos (transpositions, missed letters, double edits). Together they cover the full failure space. A property the prior pure-edit-distance-1 path did not have, since substitutions outside the spatial neighbour radius were not enumerated at all.

### 3.7 Two-tier autocorrect threshold

Spatial recognition produces a list of correction candidates. Whether to *commit* a correction (e.g. on space) (as opposed to merely surfacing it as a suggestion) is gated by two thresholds in `should_autocorrect`:

- **Absolute confidence:** the correction's score must exceed `confidence_threshold = 0.65`.
- **Relative margin:** the correction must clear `_typed_baseline(typed_word) × 1.5`, where `_typed_baseline` returns `log1p(1) ≈ 0.69` for plausibly-shaped typings (vowel + consonant) and `0` for implausible slop ("xqz", "thx").

This is the LatinIME / Gboard pattern in miniature: the literal typed word competes against corrections in the same scoring frame. Plausible deliberate typings ("thru", "lol", "btw") are protected by the relative gate; implausible inputs fall back to the absolute gate alone. The goal is to commit a correction when the user clearly mis-tapped, and to leave the typing alone when the user typed exactly what they meant.

A fully unified scoring model (where the literal typed word has an explicit probability and competes against alternatives in a single ranked list) is the proper long-term fix and is described as Known Gap #1 in §8.

### 3.8 Word suppression and rehabilitation

Users can right-click a prediction pill to *remove from vocabulary* (adds to a blacklist) or mark as *bad suggestion* (increments a dispreference counter that downweights the word by `1 / (1 + count · 0.5)`). Both lists persist in `ngram_model.json` and apply at merge time in `hybrid_predictor._merge_predictions`.

Auto-rehabilitation handles the case where the user changes their mind: typing a blacklisted word three times (each completed with space) restores it. The counter is tracked in `_blacklist_type_count` and persisted alongside the blacklist itself. Users can also restore words manually from the Model Visualization dashboard.

---

### 3.9 The click's position, and a learned pointer bias

Every component above treats a keystroke as the identity of a key. The pointer supplies more than that: it lands somewhere *inside* the key, and where it lands is not uniform. A user whose hand drifts down and right will land low and right on most keys, consistently, and that bias is a property of the person rather than of the letter.

Two mechanisms follow. Each press reports its offset from the key's centre as a fraction of the key's width and height, and the prefix beam of §3.1 scores that continuous position against its Gaussian emission rather than assuming the key's centre. Separately, `pointer_model.py` accumulates per *physical slot* (the key's row and column, so the bias belongs to the pointer and survives a Dvorak or Colemak remap) the count and sum of observed offsets, and estimates each slot's bias as its own mean shrunk toward the global mean with ten pseudo-observations. That estimate is subtracted before scoring.

The emission carries two widths, and the relationship between them is the part worth keeping. When only the key is known the uncertainty is 0.85 key-widths; when the position inside it is known it is 0.55. The second was set by sweep, and sharper values measured *worse*: at 0.3 the engine lost 1.6 points of keystroke savings and at 0.22 it lost 6, because scatter then places the intended key on the expensive side of the observed click more often than the extra precision helps.

The table is owned by the n-gram predictor, so it lives in the one file that already holds everything the user taught the engine, with the load caps, the backup archive and the Clear Learned Data path already built around it. Learning is suppressed in privacy mode like every other learning path (§5.2), offsets are clamped to one key, and nothing is logged. §8.4 measures the whole feature at 0.9 points of keystroke savings for a systematically biased pointer, of which 0.4 comes from the learned bias, which is smaller than the per-key simulation predicted and is reported in §8.4 as such.

---

## 4. Accessibility-Driven Engineering Decisions

A recurring theme in Alpha-OSK is that constraints from the user population pushed back *into* the architecture in ways that would not arise in a mainstream keyboard. This section walks through the most consequential.

### 4.1 The non-focus invariant

The OSK must never take focus from the user's target application. On Windows this is enforced by:

- `Qt::WindowDoesNotAcceptFocus` and `Qt::Tool` on the QML window.
- `WS_EX_NOACTIVATE` set via `SetWindowLong` after window creation. Qt does not expose this flag and will not set it on its own.
- `WS_EX_TOOLWINDOW` is actively *cleared* rather than applied (Qt adds it on its own once these flags reach an already-shown window), and `WS_EX_APPWINDOW` is set, so the keyboard keeps a normal taskbar entry for its minimize button. The accepted trade-off is that the OSK also appears in Alt-Tab; `WS_EX_NOACTIVATE` is what actually matters for the non-focus invariant, and neither taskbar nor Alt-Tab visibility affects it.

On Linux, the equivalent is `Qt::Tool` plus `_NET_WM_STATE_ABOVE` and `_NET_WM_WINDOW_TYPE_UTILITY`.

Two consequences ripple out:

1. **Qt's built-in `onActiveChanged` does not fire reliably**, because `WS_EX_NOACTIVATE` keeps the window from becoming the active window even when the user is interacting with it. The bridge therefore polls the OS directly (`GetForegroundWindow()` on Windows, `xdotool getactivewindow` on X11) on a 250 ms timer to detect app switches and reset the prediction context. Wayland does not expose the foreground window to unprivileged clients, so this poll is a no-op there. Context resets only happen on explicit cues (e.g. a clicked prediction).
2. **Physical keyboard input never lands in the OSK**, because the OSK never holds focus. This makes the in-app text edit popup (§4.5) non-trivial to build.

### 4.2 Sticky modifiers

Modifier keys are *sticky*: tap once to activate, tap again to deactivate, auto-release after one keypress (Shift) or remain held until explicit toggle (Caps Lock). This is the mainstream OSK model.

The non-obvious part is that activating a sticky modifier holds it at the *OS level* via `hold_modifier(name)`, not just in Python state. Without OS-level holding, sticky Shift would only attach to synthesised keystrokes. Shift+click and Shift+drag in the target app would not extend selections. With OS-level holding, the OSK behaves identically to the user pressing-and-holding a physical Shift key, which is the model users expect. `release_modifier(name)` is called on auto-release and on app shutdown so a phantom modifier is not left held against the X server, the Wayland compositor, or the Windows kernel after the OSK quits.

Caps Lock and Shift are independent toggles; toggling caps no longer flips shift. Both are surfaced separately to QML.

### 4.3 Suffix-only insertion for predictions

When the user has typed "hel" and clicks the "hello" prediction, the OSK sends `lo `. Only the suffix and a trailing space. Earlier versions sent Backspace×3 followed by "hello ", which was correct in plain text fields but failed in two important cases:

- **Slack and similar chat composers** treat Backspace at the start of an empty input as "discard draft and close composer" or "go to previous channel". A full Backspace-then-replace was destroying user state.
- **Terminals and REPLs** disable shell-style Shift+Left text selection, making any "select then overwrite" approach impossible.

Suffix-only insertion sidesteps both. The fall-back to `replace_text(prefix_length, replacement)` only fires when the prediction's casing differs from the typed prefix (e.g. typed "iph", clicked "iPhone"), where suffix-only would produce "iphPhone".

### 4.4 Right-click for shifted variant

A right-click on a character key types its shifted variant *without* flipping the sticky-shift state: `1` → `!`, `,` → `<`, `a` → `A`. This is a one-shot, modifier-free way to type a single shifted character. A common operation that would otherwise cost two clicks (Shift, then key).

The QML side resolves the output (preferring `kd.shifted` from the layout JSON, falling back to `kd.key.toUpperCase()` for letters), then routes through `keyboard.pressKeyLiteral(rch)` rather than `pressKey(rch)`. The distinction matters: `pressKey` applies the current shift/caps state and would lowercase the chosen `'A'` back to `'a'` if shift is off, defeating the feature. `pressKeyLiteral` types the character verbatim. Any future input source where the QML side has already chosen the final character (e.g. a long-press alternates picker) should use `pressKeyLiteral`.

### 4.5 The edit-popup pattern

Users frequently need to correct learned capitalisations: the engine learned "iphone" before the proper-noun list shipped, and the user wants to teach it "iPhone". The edit popup, opened from the right-click menu on any prediction pill, presents the word in a small in-window `TextField` that the user can edit using the OSK itself.

Because the OSK never holds OS focus, OSK keystrokes normally synthesise to whatever app is *behind* the OSK. To make keystrokes land in *our* `TextField`, the popup uses an "edit-mode intercept" pattern:

- `predEditPopup.modal = false`: a modal overlay would swallow MouseAreas on the keyboard below.
- `closePolicy: Popup.CloseOnEscape`: every OSK key click is a "press outside" relative to the popup; the default close-on-press-outside policy would slam the popup shut on the first keystroke.
- On open, the popup calls `keyboard.setEditMode(true)`. While active, `pressKey` and `pressSpecialKey` short-circuit the synthesiser and emit `editKeyTyped(char)` / `editSpecialPressed(name)` instead. A `Connections` block inside the popup wires those to TextField operations: insert at cursor, backspace, cursor motion, etc.

This pattern generalises: any future input source that needs OSK keystrokes to land in-app rather than out-of-app (a voice-dictation review field, a snippet editor) should follow the same shape.

### 4.6 Window-height invariant

The OSK window height is *bound* to the keyboard content's implicit height: `height: outerLayout.implicitHeight + 80`. There is no vertical resize handle (both edges are `SizeHorCursor`), and only the window *width* is persisted across launches.

An earlier version also persisted height. The first time `Component.onCompleted` ran `root.height = savedWindowHeight`, the binding broke (Qt binds are one-shot and any imperative assignment severs them). After that, any width change scaled the keyboard but the height was frozen. The user got either clipped bottom rows or empty bands above and below the keys, with no way to fix it. The fix was to delete the height-persistence path entirely. If height persistence is ever reintroduced, it must use `Qt.binding(...)` or a re-clamp in `onHeightChanged` to keep the binding live.

This is a small example of a pattern that recurs: accessibility constraints (in this case, no vertical resize handle because users with limited motor precision struggle with edge resizes) push *into* code that would be trivially correct in a normal desktop app.

<p align="center">
  <img src="../assets/screenshots/settings-appearance.png" alt="Appearance settings panel: panel-visibility toggles for function row, navigation, and numpad; layout picker for QWERTY / Dvorak / Colemak; nine-theme picker; sound and opacity controls." width="420" />
  <br /><em>Figure 2. Appearance panel from the drill-down settings menu. Panel-visibility toggles let users strip the keyboard to the minimum surface their precision can hit reliably. The layout picker covers QWERTY, Dvorak, and Colemak. Nine themes ship, including a high-contrast Blackboard option. Opacity is a continuous slider (default 100%) for users who need to see through the keyboard at the cost of contrast. (Screenshot predates the Number Row and Compact View toggles described in §4.7.)</em>
</p>

### 4.7 Compact view: geometry for a pointer, not ten fingers

The default layout is a faithful replica of a 104-key physical keyboard, and most of what makes such a keyboard wide exists to serve ten fingers resting on a home row: a 6-unit space bar reachable by either thumb, duplicated right-hand Shift / Ctrl / Alt, a dedicated number row. None of that serves a single pointer. Because key size is derived from the widest row and every narrower row is centred, the surplus width did not compress, it became symmetric dead space: 26% of the keyboard's width on the space row alone, scaling proportionally so it was no better on a small screen.

Before rearranging anything, mean pointer travel was measured across four candidate arrangements, weighting every character-pair transition by its frequency in English:

| Arrangement | Footprint | Mean travel |
|---|--:|--:|
| Desktop mirror (default) | 15.5u x 5 | 195 px |
| Compact 10x4 | 10u x 4 | 189 px |
| Compact 13x4 (shipped) | 13u x 4 | 186 px |
| Square 7x6 (QWERTY wrapped) | 7u x 6 | 179 px |

**Rearranging the letters buys nothing.** Every variant lands within 8%, because QWERTY adjacency dominates the distribution and all of them preserve it. Even the radical square wrap saves 8% while destroying the visual scan, and QWERTY *is* the visual index for a user who reads the keyboard rather than touch-types it. So the letters stay exactly where they are and the win comes entirely from deleting what a pointer cannot use: net 33% less area, 16% narrower, 20% shorter, or equivalently +20% key size at a fixed window width.

The implementation constraint is one line: **every row totals exactly 13.0 units**. Equal rows leave nothing to centre, so the gutters vanish by construction and no stretching or justification logic exists anywhere in the QML. It also means the layout has no spare capacity, which is a real design cost, not a footnote: adding forward-delete to the base layer required moving Escape to the symbol layer, because there was no fourteenth column to put it in. Digits are recoverable without leaving compact via a separate 13-unit Number Row panel.

Layers are a QML-side view concept. Rows may carry a `layer` field and the view filters on it; rows without one always render, which is why the full-size layouts were untouched. Consequently the entire feature is data plus QML, and neither the Python backend nor the C++ rewrite needed a single change.

### 4.8 Suggestion pills: drop, do not truncate

Prediction pills are sized by max-min fair allocation rather than an equal split, so a long word is not clipped while "I" and "the" sit in half-empty pills beside it. That handles one long word among short ones. It does not handle *several* long words competing, and the original even-split fallback for that case produced the worst possible outcome: eight `documentation`-family candidates in a 940 px window all rendered as `docu...`, eight identical buttons with nothing to choose between them.

The resolution is a priority order rather than a better fit function. Padding compresses first; if the set still does not fit, candidates are dropped from the tail, which is the lowest-ranked end, until the survivors fit at full text width; leftover space is then handed back as padding, max-min fair. **A pill the user cannot read is not a suggestion**, so five whole words strictly dominate eight elided ones. The only remaining elide is a single word wider than the whole bar, where there is nothing left to drop.

The visible consequence is that raising the maximum-suggestion count past what the window can hold no longer adds pills. That is the correct behaviour under the priority order, and it makes window width, not a count setting, the real lever on how many suggestions a user sees.

### 4.9 Knowing when the context went stale

`_context_buffer` and `_current_word` are a mirror of text the process does not own: they model what sits beside the caret in another application's window. Nothing notifies us when that caret moves. §4.1's non-focus invariant is the reason, and it is not incidental. A window that never activates receives no focus events, and the events we would want belong to a foreign process regardless. The mirror is therefore maintained by polling, and the design question is what to poll.

A stale mirror is not a cosmetic problem. Suffix-only insertion (§4.3) types only the unseen tail of the chosen word, so a context describing a field the user has left inserts a fragment into the field they are now in. The failure is silent, lands in the user's text, and is most likely exactly when the user is moving between fields on a form, which is when an on-screen keyboard is under the most load.

Four signals are polled, ordered from cheapest and most portable to most specific:

1. **Foreground window** (`GetForegroundWindow`, or `xdotool getactivewindow` on X11), at 4 Hz. Catches application switches. Wayland exposes no equivalent to unprivileged clients, so this degrades to a no-op there.
2. **Focused element identity** (UIA `RuntimeId`), Windows only, on the same poll. Catches the caret moving between two controls inside one window. The RuntimeId is the right key precisely because it is *not* the element's screen rectangle: it survives scrolling and window moves, so neither produces a false reset.
3. **Caret position** (`GetGUIThreadInfo`, the caret's owning window plus its top-left), Windows only, on the same poll. Catches a move *within* one control, which (2) structurally cannot see.
4. **A click outside the process** (`GetAsyncKeyState(VK_LBUTTON)` plus `WindowFromPoint` resolved to an owning pid, `src/platform/pointer.py`), Windows only, at 20 Hz.

Signals 2 and 3 both treat an unreadable answer as "do not know" and leave the mirror untouched, which is the correct failure direction for each in isolation: an accessibility hiccup must not discard the user's context. Composed, they produce a gap. A browser commonly exposes a single UIA element for an entire document and publishes no caret, so both fall silent *simultaneously* on the most common move a user makes, clicking from one field to the next on a form. Signal 4 exists for that intersection, and it is deliberately of a different kind: it asks the input system rather than the accessibility layer, so it needs no cooperation from the target application.

Two properties make signal 4 tractable. Ownership is resolved by process id rather than by geometry, which classifies the keyboard's own keys, pills, title bar, popups and detached snippets window in a single comparison, and without which every keystroke would clear the context it just contributed to. And the poll interval is part of the correctness argument rather than a tuning parameter: the button state is paired with the pointer's *current* position, so the interval bounds how far the pointer can travel between the press and the reading, and therefore how often a click in the target application is misattributed to the keyboard. 20 Hz costs two syscalls per tick against a pointer that must physically cross the screen.

Signal 4 is coarser than the three it backs up: a click on a toolbar button or a scrollbar moves no caret, and resetting there costs a next-word suggestion. This is an asymmetric trade taken deliberately. A missed reset corrupts text; a spurious reset costs one suggestion. It deliberately does not inherit the guard from (3) against firing mid-word. That guard was shared originally, and it withheld the reset precisely where it was most needed: a partial word is when the suggestion row is full of completions for the field the caret has just left, so a suggestion tapped after such a click inserted the abandoned word's tail into a field that never held its head. The desync the guard prevents (clearing the mirror while its characters remain on screen) is a hazard only for a click that moves no caret, which on a keyboard whose own clicks are filtered by process id is the rarer of the two. Signal 3 keeps the guard, because scroll drags the caret rectangle without moving the caret and lands mid-word constantly.

Dropping the guard has a second consequence, and it is the one that outlives the session. A reset landing mid-word leaves that word's opening on screen with nothing tracking it, so everything typed from there to the next boundary is a tail, and the word-completion path would learn it as a word: an interrupted "documentation" deposits "mentation" in the learned vocabulary, in the typing statistics, and at the top of the suggestion row for every subsequent "ment", from where it is written to disk and carried in the user's backup archive. The mirror desync the trade above weighs is transient by comparison. The reset therefore marks the word as beheaded, and the three sites that learn from it skip exactly one word: the tail still reaches the on-screen mirror, because that has to match what was typed, and reaches nothing that persists.

Two further signals are not polled at all, because the keyboard is the thing causing the move and therefore already knows. **Tab** and **the cursor-motion keys** (the arrows, Home, End, PageUp, PageDown) each reset the mirror directly when the user presses them. These are the only two signals in the set that behave identically on every platform, since they require nothing from the host beyond the keystroke the user just asked for, and they cover the case the polled signals are weakest at: a caret move inside a single control, in an application exposing no accessibility information, performed without touching the mouse. Both clear the suggestion row in the same operation, which is what distinguishes them from signal 3's deliberate refusal to fire mid-word: that refusal exists because clearing the buffers while leaving a populated row on screen invites an insertion beside its own prefix, and clearing both together cannot produce that state. Delete is deliberately excluded, since it removes the character following the caret and leaves the run preceding it, which is the only thing the mirror describes, unchanged.

### 4.10 Dictation: why a toggle, and why the transcript sits in the suggestion bar

Voice input is the obvious complement to a keyboard driven one click at a time, and the interesting decisions are not about recognition quality.

**It is a toggle, not push-to-talk.** Every consumer implementation holds a key or a button for the duration of the utterance, and that is precisely the gesture this user cannot make: a sustained, precise hold is what §4.7's geometry argument and the removal of swipe typing were both about. Click to start, click to stop. The cost of a toggle is that a missed "off" click leaves a microphone open and, on a metered recogniser, a session billing, so two automatic stops sit behind it. A silence timeout ends a run a few seconds after speech stops, and is the one the user perceives. A wall-clock ceiling backs up the silence detector itself, for the room noisy enough that nothing ever reads as silence. Both are adjustable, because a fixed "few seconds" is a guess about someone else's speech rhythm.

**Capture format is fixed at the boundary rather than negotiated.** The audio layer asks Qt for 16 kHz mono signed-16-bit PCM and lets Qt resample whatever the device natively runs at, so the recogniser's parameters are constants rather than variables and an unusual interface is not an untested path. This also costs nothing: 16 kHz is what speech models are trained at, so the alternative would be triple the bandwidth for no accuracy. The whole feature is built on Qt's own multimedia and websocket modules, which the bundle already carries, so it adds no dependency for the packaging and signing pipeline (§7) to absorb, and no worker thread: audio arrives on the UI thread and is written straight to a socket on the same thread, at 32 kB/s.

**The transcript renders in the suggestion bar, and only the uncommitted part.** A finalised phrase leaves the bar and is typed into the target application through the same verbatim-insert path snippets use (§4.3's insertion invariants apply unchanged, including releasing sticky modifiers before the send so a held Shift does not deliver the sentence in capitals). What remains on screen is therefore exactly what has *not* yet been typed, which keeps it short by construction rather than by truncation, and gives the user a live view of what the recogniser is still revising. It elides from the left, the opposite of every other elide in the UI, because while speaking the tail is what is being checked.

**Placement follows the same rule as every other control here.** The microphone sits at the left end of the suggestion bar, the one edge with nothing on it, because the clear-context ring at the right end is pressed from muscle memory and a new control must not move it. The space it reserves collapses to zero when the feature is off, so the pill-fitting geometry of §4.8 is bit-for-bit unchanged for a user who never enables it.

---

### 4.11 One route is not a route: redundancy in reachable controls

A recurring failure in this system's own history is a control reachable by exactly one gesture, where that gesture is one some users do not have. Three instances, all fixed the same way, are worth stating as a single principle because the principle is what generalises.

**Right-click cannot be the only way in.** Dwell-click software, switch access, head and eye trackers, and single-button adaptive mice all produce a left click and nothing else. Alpha-OSK uses right-click for three genuinely useful shortcuts: the shifted variant of a character key (§4.4), locking a modifier held (§4.2), and opening the editor for a programmable function key. Each of the three has a left-click route as well, and the third is the instructive one: the function-key editor is reachable from a Settings page listing all twenty-four keys, which turned out to be strictly better than the right-click it backs up, because its rows are far larger targets than a 36 px keycap and it is the only surface that shows an assignment the user has forgotten making.

**A setting must not be able to remove the only route to an unrelated feature.** Turning suggestions off collapses the suggestion bar to zero height, and the bar carries the buttons for dictation, the symbol picker, snippets and clearing context. Each of those has a title-bar mirror that appears exactly when the bar is hidden. The general form of the bug is a container whose visibility is controlled by one feature's setting while holding another feature's only entry point.

**Colour may encode meaning only if it survives every theme.** Nine themes ship, several with pale accents and one light throughout, so any fixed colour is illegible on roughly half of them. The key-colouring scheme added in 1.3.0 therefore derives every hue by rotation from the active theme's own accent in a perceptually uniform space (OKLCh, because rotating hue in HSL holds the lightness *number* constant while perceived lightness swings), and then walks each fill's strength down until the theme's own text colour clears a 4.5:1 contrast ratio against it. A scheme that would bury a legend on some theme yields instead. The property is enforced by a test sweeping every scheme against every theme against every role, 540 combinations at rest and hovered, paired with an inverse assertion that the schemes remain distinguishable from one another, since a scheme that collapsed every key to one colour would satisfy a contrast sweep perfectly.

---

## 5. Privacy and Security

### 5.1 Off-network by default

Alpha-OSK does not connect to the network at runtime except for the auto-update check (§7) and two features that are off until the user turns them on: the usage-stats pipeline (§5.6) and dictation (§4.10). There is no crash reporter, no model-improvement upload path, and no implicit telemetry.

Dictation is the one feature that sends *content* rather than counters, and it is worth stating plainly rather than filing under "opt-in". Speech recognition of the quality this needs does not run locally without shipping a model, so while the microphone is open the audio is streamed to a third-party recogniser under the user's own API key. Three things bound it. It is inert until the user both enables it and supplies a key, so the default install is unchanged. Audio flows only between the click that starts a run and the one that ends it, and nothing is written to disk at any point. And it is cancelled outright, mid-utterance, the moment password-field detection (§5.2) fires, because the failure mode being prevented is a spoken password reaching a third party, which is worse than any of the model-poisoning cases §5.2 exists for. Lifetime statistics persist to a local `analytics.json` and are visible to the user through the in-app dashboard; **the dashboard is always local-only**, regardless of the §5.6 toggle. The toggle controls a separate, narrower pipeline that submits only the same lifetime counters the dashboard already shows the user, and only when explicitly enabled.

Off-network is not the same as off-disk. A separate local-only diagnostic log, `alpha-osk.log` (rotating, 2 MB x 3 generations, in the config directory), records operational events. Until an August 2026 hardening pass, nine call sites in `keyboard_bridge.py` wrote the user's actual typed text into this log, including while privacy mode was active; those sites now log lengths and booleans only, and the standing invariant is that no record at INFO level or above may carry typed content. The log is never uploaded and is not part of the Data Backup archive. See `PRIVACY.md` for the full disclosure, including what to do with a pre-fix log file still on disk.

Having no crash reporter does not mean crashes go unrecorded, and the distinction is where the decision to share sits rather than whether the evidence exists. Uncaught exceptions on the main thread and on worker threads are routed into that same local file (`keyboard_app.py::_install_exception_hooks`), which matters because the shipped build is windowed and therefore has no stderr at all: before this, the traceback for a genuine crash was written to a stream that is `None` and discarded. The file is surfaced to the user in *Settings > Data & Privacy > Diagnostics*, so the transfer of a crash report is an explicit act by the user, attaching a file they can read first, rather than an automatic upload they would have to discover and opt out of.

<p align="center">
  <img src="../assets/screenshots/settings-data-privacy.png" alt="Data and Privacy settings: data-backup export and import buttons, anonymous-usage-stats toggle (off), installed version, and check-for-updates-on-startup toggle." width="420" />
  <br /><em>Figure 3. Data & Privacy panel. Data Backup writes a single .zip containing the user's model, lifetime stats, and imported vocabulary packs; importing on a new machine restores everything in place. The telemetry contributor ID is deliberately excluded from exports so contributions never link across machines. The anonymous-usage-stats toggle (off by default) controls the §5.6 pipeline. Auto-update is opt-out and shown alongside the installed version.</em>
</p>

### 5.2 Privacy mode and password-field detection

A typed password should never enter the prediction model. Two paths enforce this on Windows:

1. **Background polling.** A 200 ms `QTimer` calls `is_password_field()` from `src/platform/password_detect.py`, which uses Windows UI Automation (`IUIAutomation::GetFocusedElement` → `UIA_IsPasswordPropertyId`). UIA covers native applications and modern browsers that expose accessibility metadata. A Win32 fallback (`EM_GETPASSWORDCHAR`) catches older Win32-only apps.
2. **Per-keystroke synchronous check.** `pressKey` and `pressSpecialKey` call `_check_password_field_sync()` rate-limited to ~50 ms. This closes the race window where the user types the first characters of a password between timer ticks. Without the synchronous check, those characters would reach the prediction cache before the timer fires.

When privacy mode is active (auto-detected, or set manually with the "Learning" switch in the title bar), keystrokes still reach the OS, but `_current_word`, predictions, and learning are all suppressed. The prediction bar shows "Learning paused".

On Linux, the equivalent uses AT-SPI 2 (`gi.repository.Atspi`). A daemon thread owns a GLib event loop and listens for `object:state-changed:focused`; whenever focus lands on an accessible whose state set contains `STATE_PASSWORD_TEXT`, the privacy flag flips on. Coverage spans GTK (`GtkEntry` with `visibility=false`), Qt (`QLineEdit` in Password echo mode), and browsers that expose accessibility metadata. If `gi` fails to import or AT-SPI is not running, the detector falls back silently to the null detector and the user can still toggle privacy mode manually.

The COM lifecycle on Windows is worth noting: `_WindowsUIADetector` tracks `_owns_com` so `CoUninitialize` only fires if our code called `CoInitializeEx` and got `S_OK`. If `CoInitializeEx` returned `S_FALSE` (some other component already initialised the apartment), we skip the uninit. Calling it would tear down the other caller's COM environment.

### 5.3 Pack import hardening

The vocabulary system is import-only (§3.1.2 covers why no built-ins ship). Users import third-party packs from arbitrary filesystem paths, which makes import the dominant security surface for the prediction stack. `PackManager.import_pack` enforces:

- The source folder's name is sanitised against `^[a-z0-9][a-z0-9_-]{0,63}$`: total length 1–64 characters, the first character must be alphanumeric (so a leading `_` or `-` is rejected, blocking dotfile-style or argument-style escapes), and only lowercase alphanumerics, underscore, and hyphen are accepted. Anything else (including `..`, slashes, spaces, uppercase) is rejected.
- The resolved destination path is verified to sit strictly under `user_packs_dir` before any `rmtree` or `copytree` runs. This blocks symlink traversal that resolves outside the packs root.
- Symlinks inside the source tree are *skipped*, not dereferenced. A pack that contains a symlink to `/etc/passwd` will import without that file.
- Windows reserved device names (`con`, `nul`, `com1`, and similar) pass the character-class regex above, since they're valid lowercase alphanumerics, but collide with the OS regardless of extension and previously crashed the import loop when the OS refused to create the folder. These are now rejected explicitly, and a single bad pack entry no longer aborts the rest of the per-pack write loop.
- Import is bounded: 64 KB for `pack.json` metadata, 20 MB per file, 50 MB total per import, and 200,000 entries each for words, bigrams, and trigrams. A pack with no size limits was an unbounded-resource-consumption path for a user importing an unvetted third-party pack.

The regression tests for these properties are in `tests/test_vocabulary_pack.py::TestImportPackSecurity`.

### 5.4 Model load caps

Both the n-gram and PPM model loaders reject files over 50 MB. The n-gram loader additionally rejects models with more than 500,000 unigrams, 500,000 bigram prefixes, or 100,000 capitalisation entries. Anything beyond these is assumed to be corrupt or hostile and is silently skipped (the in-memory base dictionary is kept). These limits are intentionally well above what real long-term users produce.

### 5.5 Data backup (export / import)

Lifetime stats and learned vocabulary are the part of a user's setup that takes the longest to rebuild from scratch. Settings (theme, layout, panel toggles) live in the Qt settings layer and are quick to reconfigure manually; the irreplaceable artefact is the prediction model that has accumulated over months of typing. *Settings → Data & Privacy → Data Backup* writes the model, lifetime stats, and imported vocabulary packs into a single `.zip` the user controls. The implementation lives in `src/data_export.py`.

**What's in the archive.** A `manifest.json` (schema version, app version, ISO-8601 UTC timestamp, file list, pack id list) plus `models/ngram_model.json`, `models/ppm_model.json`, `analytics.json`, and `packs/<id>/...` for each imported pack. `telemetry.json` is **deliberately excluded** so the contributor `anon_id` does not link contributions across machines (the user's data-policy promise in §5.6 and `PRIVACY.md` would otherwise be silently broken). A fresh `anon_id` is generated on the new machine when telemetry is re-enabled.

**Import is replace, not merge.** The imported state is "the user's full snapshot at export time", so imported files overwrite the corresponding files in the config directory and packs not in the archive are removed. Before any overwrite, the current state is written to a timestamped rescue archive at `<config_dir>/exports/rescue-<ts>.zip`, so a regrettable import is one click away from a rollback. Rescue-write failures are logged but do not abort the import; correctness of the import path takes priority over the safety net. Model files are replaced via tempfile-then-rename so a partial write cannot corrupt the existing file. After files are replaced, `HybridPredictor.reload_from_disk()` re-reads the models and re-discovers packs; `TypingAnalytics.reload_from_disk()` re-reads lifetime counters. The user does not need to restart the application. Enabled-pack state is intentionally reset (imported packs come back disabled and the user re-enables what they want, matching what would happen if they imported each pack one at a time on the new machine).

**Archive validation.** Both the inspect path (preview before commit) and the import path enforce the same allow-list extraction policy. Reject any archive entry whose path contains `..` components, is absolute, has a drive prefix, or contains backslashes (zip-slip defence; `Path` handles `..` natively, but the explicit check is defence in depth and matches the pack-id validation in §5.3). Enforce a 75 MB per-entry cap, a 500 MB total-uncompressed cap, and a 200 MB archive-on-disk cap. Caps trip on file-size metadata before any bytes are extracted, so a forged 50 GB entry is refused without OOM; `_bounded_copy` additionally re-checks both caps against the actual bytes copied as each file streams out, not just the declared metadata (defence in depth, since CPython's own `ZipExtFile` already truncates reads to the declared size, so a forged size was never itself an exploitable cap bypass). Extraction is allow-list, not deny-list: only members matching the exact expected paths (`models/ngram_model.json`, `models/ppm_model.json`, `analytics.json`, `packs/<sanitised-id>/<allowed-filename>`) are written to disk. A hand-edited archive that snuck a `telemetry.json` or a `../../boot.ini` past the manifest check is silently ignored at extraction time. Pack ids are re-matched against the §5.3 regex on import. If the manifest's `schema_version` exceeds the current schema version, import is refused with an "upgrade the application first" message rather than half-applied. A malformed or corrupted archive (`zipfile.BadZipFile`) is caught and surfaced as `DataExportError` rather than raising after some files have already been replaced mid-import, which previously could leave the config directory in a partially-imported state.

Regression coverage for the validation properties is in `tests/test_data_export.py`. A hand-crafted archive cannot smuggle `telemetry.json` past the extractor, a zip-slip path is rejected at inspect time, an absolute path is rejected, an oversize entry is rejected, and a future-`schema_version` archive is rejected.

### 5.6 Opt-in usage telemetry (1.1.0+)

Alpha-OSK has a community-impact pipeline designed to let users contribute to a shared "X million keystrokes saved" aggregate. **It is off by default, and currently a no-op even when on**, because `DEFAULT_ENDPOINT` is the empty string in `src/telemetry.py` and the backend Cloudflare Worker is not yet deployed to a production URL. The client, the consent toggle, and the dashboard surface are present in 1.1.0 so the wiring can be validated against the agreed schema before the endpoint goes live; the client begins actually submitting only when `DEFAULT_ENDPOINT` is set in a future release. Both the user-facing data policy (`PRIVACY.md`) and the design (`architecture/TELEMETRY.md`) are versioned in the repo.

The toggle lives in *Settings → Data & Privacy → Privacy → "Share anonymous usage stats"*. When on, a weekly POST sends ten fields, of which seven are the lifetime counters and three identify the build: a randomly-generated `anon_id` (UUID4), `app_version`, `os` (`windows` / `linux`), and the seven lifetime counters that already render on the in-app dashboard (`keystrokes`, `words`, `predictions`, `keystrokes_saved`, `minutes`, `sessions`, `prediction_offers`). **Nothing else.** The pipeline never sends content, word frequencies, key frequencies, IP, hostname, machine identifiers, or per-session breakdowns. The privacy-mode interaction is implicit: privacy mode (§5.2) suppresses learning and counter increments at the analytics layer, so password-field activity never enters the lifetime totals in the first place. The telemetry layer just forwards what the dashboard would show.

The `anon_id` is generated on first opt-in and **cleared on opt-out**, so opt-in/opt-out cycles produce unlinkable contributions. A user who wants their already-submitted row removed can use the "Delete my contributed data" button in the same Settings section, which POSTs to `/v1/forget` (the server returns 204 regardless of whether the id existed, so request-pattern probing yields no information). Reinstallation or deletion of the per-user config directory also produces a fresh id; the old row becomes orphaned and is garbage-collected by the daily cron after 365 days of inactivity.

The submission cadence is enforced by an hourly QTimer owned by `TelemetryBridge`, which is registered to QML as its own context property rather than through `KeyboardBridge` that calls `maybe_submit()`; the function short-circuits unless the consent flag is on, the endpoint is configured, an `anon_id` exists, and at least seven days have elapsed since the last successful submission. A second hook (`submit_on_quit` from `KeyboardBridge.shutdown`) covers the case where the user runs the app for less than a week between sessions; it bypasses the weekly window with a 60 s anti-spam guard. Failures (HTTP 5xx, 429, network error) retry with backoff `[5 s, 30 s, 120 s]` and then drop until the next cycle. There are no user-visible error toasts: a network failure is not the user's problem.

The backend is a Cloudflare Worker (`backend/cf-worker/`) backed by D1. Two tables: `users(anon_id PK, first_seen, last_seen, app_version, os)` and `submissions_latest(anon_id PK, ts, …counters…)`. The latest submission overwrites the previous because lifetime counters are monotonic. Three routes: `POST /v1/submit` validates each counter against a sanity ceiling (e.g. 10⁹ keystrokes) and upserts both tables; `GET /v1/aggregate` returns sums across `submissions_latest`, cached at the edge for five minutes; `POST /v1/forget` deletes the user's row. A daily cron prunes users whose `last_seen` is older than 365 days, with `ON DELETE CASCADE` cleaning up the child row.

The `DEFAULT_ENDPOINT` constant in `src/telemetry.py` is the kill switch. While it is the empty string, the client treats the endpoint as not configured and silently no-ops every submit even when the toggle is on; setting it to a deployed worker URL activates the pipeline. This decoupling lets the client and the toggle UI ship in a release that has the backend not yet deployed (or for a release where telemetry is intentionally disabled across the board, e.g. a regression-investigation build).

#### 5.6.1 Threat model for the telemetry pipeline

- **An operator with full backend access** sees `anon_id`s, app-version distribution, OS distribution, and lifetime counters per user. Cannot see content, individual sessions, words used, or anything that would identify a user.
- **A passive network observer** sees that the user POSTed to the telemetry endpoint, plus the payload size (~200 bytes). TLS hides the payload contents.
- **A compromised backend** could backfill submissions to fake the public aggregate. Sanity ceilings on each counter limit the blast radius. Volume is limited by two `anon_id`-keyed layers, a Cloudflare rate-limit binding on both POST routes and a `SUBMIT_COOLDOWN_SECONDS` window enforced in the D1 upsert, neither of which reads a request header (so this is not per-IP rate limiting). Both reject by no-opping and returning the same 204 as success, so neither is an oracle for whether an id exists. A flood from many distinct fabricated ids is not covered in code and needs an out-of-band WAF rule.
- **An adversary trying to deanonymize a user** has very little to work with: the `anon_id` is opaque, no IP is stored, no User-Agent is stored, no submission history is retained (only `latest`), and the aggregate endpoint never exposes individual rows.

### 5.7 Auto-update threat model

Auto-update fetches from the release repository `owenpkent/alpha-osk-releases`, which is separate from the source repo `owenpkent/alpha-osk`. Both are public; the split is preserved because the updater's API URL is hard-pinned to the releases repo. The threat model and per-defence rationale are in `build/AUTO_UPDATE.md`; the short version is:

- The update endpoint is `https://api.github.com/repos/owenpkent/alpha-osk-releases/releases/latest` over HTTPS with system-trusted CA roots.
- The downloaded asset filename must match `Alpha-OSK-Setup-{version}.exe` exactly. Anything else is rejected before execution.
- The installer is EV-code-signed; Windows SmartScreen and the OS verify the signature before the user is prompted.
- The NSIS installer's auto-relaunch path (silent installs only) launches the new executable through `explorer.exe` rather than directly. This drops the new process to medium integrity level instead of inheriting the installer's high-IL token, which is what the OSK needs (UIAccess injects medium-IL → high-IL, not the reverse, and learned vocabulary should land in the user's `%APPDATA%`, not the admin profile).

---

## 6. Performance and Resource Envelope

Alpha-OSK is intended to run unobtrusively on hardware that motor-impaired users typically have: older laptops, low-power desktops, sometimes tablet hybrids. The performance envelope reflects this.

| Metric | Budget | Measured |
|--------|--------|----------|
| Per-keystroke prediction latency | < 30 ms typical, < 100 ms worst-case | **2.9 ms p50, 11.4 ms p95** (§8.6) |
| Cold start | < 2 s on a 2018-era laptop | not systematically measured |
| Resident set size | < 200 MB after warm-up | not systematically measured |
| Installer size (Windows) | as small as Qt allows | 93 MB |
| GPU | not used | none required |
| Network | update check on startup only (opt-out) | as budgeted |

**Only the latency row is a measurement.** It comes from every `predict()` call in the benchmark runs of §8, on one development machine. The rest of the table is a design budget that the system is believed to meet and that no instrumentation currently verifies, and it is presented as such because the hardware target of the paragraph above is exactly the hardware on which nobody has measured it. §9 lists this among the limitations.

The prediction engine is pure Python with no native extensions. The hot paths (n-gram lookup, fuzzy candidate generation) operate on plain dicts and lists rather than NumPy or compiled tries. This is deliberate: the working-set size is small enough (tens of thousands of words) that Python dict performance is adequate, and a native dependency would complicate cross-platform builds.

Idle cost is dominated by three timers rather than by the engine, which does nothing between keystrokes. Two run at 4-5 Hz (the password-field detector's UIA call, the foreground/focus/caret poll). The third, the outside-click probe of §4.9, runs at 20 Hz but is two syscalls per tick, `GetAsyncKeyState` plus a cached comparison, and only resolves a window and its owning process on the ticks where a button actually went down. The asymmetry is intentional: the expensive checks are the ones that can afford to be slow, because the state they watch changes on human timescales, while the cheap one is the one whose accuracy degrades with interval length.

The single-instance lock uses `QSharedMemory`. The lock-holder reference is module-level in `keyboard_app.py`. A function-local would be destroyed before the application started, releasing the lock prematurely.

---

## 7. Distribution and Updates

### 7.1 Windows

Windows builds are produced by `build/windows/build.py`, which drives PyInstaller, NSIS (for the installer), and SignTool (for EV signing). The signing step is the single most common build trap: SafeNet Authentication Client exposes the eToken-resident certificate to the *user session only*, so the build must run from a non-elevated shell with the eToken plugged in. An elevated shell will fail with "Cannot find certificate."

The release-asset filename must match `Alpha-OSK-Setup-{version}.exe` exactly. The auto-updater rejects anything else. Version is sourced from `src/__version__.py` and read by both the updater (to compare against the latest GitHub release) and the build script (to name the installer and stamp the registry entry).

UIAccess is granted at install time via the `uiAccess="true"` manifest entry and the Program Files install location. UIAccess lets the OSK inject input into elevated target windows (UAC consent dialog, Task Manager, regedit) without the OSK itself running elevated.

### 7.2 Linux

Linux builds use a parallel pipeline in `build/linux/build.py` that produces a PyInstaller bundle and, optionally, an AppImage (`--appimage --fetch-appimagetool`). Signing is not part of the Linux flow. AppImage is unsigned by design, and EV signing is Windows-specific. The AppImage entry script (`build/linux/AppRun`) points `QT_PLUGIN_PATH` and `QML2_IMPORT_PATH` at the bundled Qt and defaults `QT_QPA_PLATFORM=xcb`.

`xdotool` and `ydotool` are *not* bundled. They are OS-level tools that must be installed on the host. The bundle starts without them but key synthesis silently no-ops, which is a known limitation discussed in `build/LINUX.md`.

### 7.3 Update flow

The auto-update flow is documented end-to-end in `build/AUTO_UPDATE.md`. The user-facing toggle is *Settings → Data & Privacy → Updates → Check for updates on startup* (persisted in QML settings). The Windows path uses NSIS silent install with a taskkill of the running OSK in `customInit` (so the new executable can be written) and an auto-relaunch through `explorer.exe` in `customInstall` (so the relaunched process runs at the user's medium IL, not the installer's high IL).

### 7.4 Dependency lockfiles, SBOMs, and CVE scanning

Every release ships two dependency artefacts alongside the installer, plus a CI-time scan job. They form a small but standards-aligned supply-chain hygiene story.

**Lockfiles.** Both build pipelines (`build/windows/build.py::freeze_lockfile` and `build/linux/build.py::freeze_lockfile`) run `pip freeze --all` against the build venv and write `release/Alpha-OSK-Setup-{version}-requirements.lock.txt` (Windows) or `release/Alpha-OSK-{version}-linux-requirements.lock.txt` (Linux). Pip-installable record of every Python package + exact version; `pip install -r <lockfile>` into a fresh venv recreates the build env. Until 1.1.0 there was no lockfile and "what version of urllib3 shipped in 1.0.16?" had no definitive answer.

**CycloneDX SBOMs.** Both pipelines additionally emit a CycloneDX 1.6 SBOM (`build/{windows,linux}/build.py::emit_sbom`, via `python -m cyclonedx_py environment`) to `release/Alpha-OSK-Setup-{version}-sbom.cyclonedx.json` (Windows) or `release/Alpha-OSK-{version}-linux-sbom.cyclonedx.json` (Linux). The SBOM is the machine-readable counterpart: per-component PURL (`pkg:pypi/<name>@<version>`), license expression where the package's metadata declares one, integrity hashes, and dependency graph. CycloneDX is OWASP-stewarded, ECMA-424 standardised, and the input format most security scanners (Trivy, Grype, OSV-Scanner, Dependency-Track) expect. `--output-reproducible` strips time/random fields so two builds of the same env produce byte-identical SBOMs. ~100 KB / 80 components at the current Python dep set. Soft-fails (warning, no abort) if `cyclonedx-bom` isn't installed; production builds pull it in via `requirements-dev.txt`.

**Why both.** The lockfile is the human/pip-friendly view, the SBOM is the machine/compliance view. The plaintext lockfile is more discoverable for a developer reading the release page and recreating the env; the SBOM is what a procurement reviewer drops into Dependency-Track or what a CI scanner consumes. They're the same packages from two angles, ~100 KB combined relative to the 85 MB installer. No reason not to ship both.

**Worker side.** `backend/cf-worker/package-lock.json` is checked in alongside `package.json` so Wrangler / TypeScript / `@cloudflare/workers-types` versions are deterministic between local and CI. A second SBOM (`cf-worker-sbom.cyclonedx.json`, ~470 KB / 209 components. Npm dep trees are deeper than pip's) is generated by `npm run sbom` (which calls `@cyclonedx/cyclonedx-npm`) and auto-fires before every `npm run deploy` via the `predeploy` script. The SBOM file is in `.gitignore` since it's regenerable from the lockfile any time.

**CI-time CVE scanning.** `.github/workflows/ci.yml` has an `osv-scan` job pinned to `google/osv-scanner-action@9a498708959aeaef5ef730655706c5a1df1edbc2` (v2.3.8) that reads both lockfiles and queries the OSV database on every push and pull request. Merges are gated (`fail-on-vuln: true`): any CVE in either lockfile fails CI, so unknown advisories gate merges by default. The earlier known noise (six dev-only Wrangler-3.x findings: one moderate `esbuild`, five medium-to-high `undici`) was resolved by upgrading the worker to Wrangler 4.x, which ships clean `esbuild` and `miniflare` 4. The Python side had one transitive `lxml` advisory flowing through `cyclonedx-bom`, pinned away via `lxml>=6.1.0` in `requirements-dev.txt`. SARIF upload to the GitHub Security tab is disabled (`upload-sarif: false`) by default; findings surface in the job's annotations / summary instead. Originally disabled because the source repo was private and GitHub Advanced Security was off; the repo went public on 2026-05-16, so SARIF upload could be re-enabled, but it stays off for now because the job summary already surfaces the same findings and publishing them to the public Security tab is a separate disclosure decision. If a future advisory genuinely cannot be fixed before the next push, quarantine it with an `osv-scanner.toml` ignore entry rather than reverting the global gate.

**Compliance posture.** This setup meets the structured-SBOM requirement of US Executive Order 14028 and the form expected by hospital / pharma / defence procurement reviews. The EU Cyber Resilience Act (in force 2027) will require similar documentation for any networked product sold in the EU. Alpha-OSK now has the artefacts ready before the requesters appear.

### 7.5 macOS (in progress)

A third platform port is scaffolded but not yet shipped. The platform abstraction (`src/platform/macos.py`) implements `MacOSKeySynthesizer` on top of `Quartz.CGEventCreateKeyboardEvent`; the `"win"` modifier maps to ⌘ Command. Window flags (`src/platform/macos_window.py::apply_window_flags`) set float level, all-Spaces collection behaviour, and `hidesOnDeactivate=NO` so the OSK floats above target apps without joining Mission Control's window list. Config and model directories live under `~/Library/Application Support/alpha-osk/`. The build pipeline at `build/macos/` produces an `Alpha-OSK.app` bundle via PyInstaller `BUNDLE()` with an optional `hdiutil` step for a `.dmg`, but is not yet exercised end-to-end.

The hard part is **input delivery**. Naïve `CGEventPost(kCGHIDEventTap, ev)` posts to whatever app is currently frontmost, and on macOS clicking the OSK window activates the OSK as the foreground app (even with `NSApplicationActivationPolicyAccessory`, `Qt.WindowDoesNotAcceptFocus`, `Qt.Tool`, and `hidesOnDeactivate=NO`, all of which help on other surfaces but do not prevent click-activation of a plain NSWindow in Qt 6). The synthesised keystroke then lands back in Alpha-OSK rather than the editor the user was typing into. The working pattern installs an `NSWorkspaceDidActivateApplicationNotification` observer at synth init, records the pid of every non-self app that activates, and posts each event via `CGEventPostToPid(target_pid, ev)`. Pid-targeted delivery is frontmost-independent, so the event lands in the editor whether or not Alpha-OSK is the foreground app at the instant of dispatch. A cold-start window before any target has been recorded falls back to `CGEventPost`; in practice users tab into a target editor before clicking the OSK, so the fallback is rare.

**First-run gotcha.** macOS requires an Accessibility TCC grant (System Settings → Privacy & Security → Accessibility) before `CGEventPostToPid` reaches other apps. Without it, the UI works but keystrokes silently no-op. The same grant gates password-field detection, so without it that protection is unavailable too, and it fails open. Explicit follow-up phases include code signing with a Developer ID certificate, notarisation, and auto-update parity with the Windows path. `AXUIElement`-based password-field detection (the macOS analogue of UI Automation on Windows and AT-SPI 2 on Linux) is **implemented**: `_MacOSAXDetector` resolves the frontmost application's pid, walks to `kAXFocusedUIElementAttribute`, and matches the `AXSecureTextField` subrole reported by Cocoa, WebKit and Chromium alike. Full plan and phase breakdown are in `build/MACOS.md`.

---

## 8. Evaluation

### 8.1 Method

We measure **keystroke savings rate** (KSR), the standard word-prediction metric (Trnka and McCoy, 2008), with the harness in `scripts/bench/ksr.py`.

**Protocol.** For each word in a held-out sentence, the simulated user asks the engine for its top five predictions at every prefix length, starting from the empty prefix, and accepts the intended word the first time it appears. A word accepted after *i* typed letters costs *i* + 1 clicks: the letters, plus one click on the suggestion. A word never predicted costs its full length plus one for the following space, which is also the baseline every word is scored against. KSR is then

$$\mathrm{KSR} = 1 - \frac{\text{clicks with prediction}}{\text{clicks without prediction}}$$

Alongside it we report **next-word hit rate** (the share of words already present in the bar before any letter of them is typed, which is the metric the context tables directly address), **never predicted** (the share of words the engine never offers at any prefix), and per-keystroke latency as p50 and p95 over every `predict()` call in the run.

**Corpus.** The evaluation sets are the development and test splits of *A Crowdsourced Corpus of AAC-like Communications* (Vertanen and Kristensson, 2011), used under CC BY 4.0: 557 and 566 sentences, 2,956 and 2,730 words. The corpus was collected by asking crowd workers to invent communications as if using a scanning interface, which makes it the closest public proxy for this system's population. **The splits are by worker rather than by sentence**, so no author's writing appears in more than one split. The corresponding training split is deliberately absent from the repository: a training set sitting beside the test set is a standing invitation to seed the model from it and report against the match.

**Model state.** Every run builds a cold-start engine in a fresh temporary directory, so the developer's own learned model is never involved: 18,990 unigrams, 13,440 bigram prefixes over 67,697 edges, and 20,947 trigram prefixes, all of it from the shipped data files. Section 8.5 is the one exception, and says so.

**What this measures, and what it does not.** KSR is a property of the engine under an idealised user who notices every suggestion the instant it appears and never mis-clicks the pill. Real users scan the bar, sometimes miss an offer, and pay a visual-search cost that typing the next letter does not carry. The number is therefore an upper bound on benefit rather than an estimate of it, and the gap between the two is exactly what a user study would measure. Section 9 returns to this.

### 8.2 Main results

| Split | Sentences | Words | KSR | Next-word hit | Never predicted |
|---|---|---|---|---|---|
| `aac-dev` | 557 | 2,956 | 49.1% | 28.7% | 13.6% |
| `aac-test` | 566 | 2,730 | 50.4% | 30.4% | 13.0% |

Roughly half of the clicks a user would spend typing these sentences are avoidable, and the engine offers the correct next word before a single letter of it is typed about three times in ten. The two splits sit 1.3 points apart with nothing else changed, which sets the noise floor for every comparison below: **we treat differences under about 1.4 points as not meaningful.**

For calibration, the same harness scores 55.0% on the 30 hand-written sentences that were this project's original benchmark. That set is easier than either AAC split because it was written in this repository and sits close to the curated seeds and the training corpus. It is reported here only to make earlier internal figures comparable; every number in this section is on held-out data.

### 8.3 What each source contributes

Each row removes or restores one component of the merge. All other settings are held at their shipped defaults.

| Condition | dev KSR | test KSR | test p50 |
|---|---|---|---|
| **Full system (shipped)** | **49.1%** | **50.4%** | **2.9 ms** |
| Fuzzy source removed | 49.4% | 50.5% | 2.3 ms |
| Pre-1.3.0 whole-word fuzzy | 47.4% | 49.2% | 3.4 ms |
| PPM word candidates restored | 48.5% | 49.9% | 15.9 ms |

Three results, and two of them are negative.

**The mid-word prefix beam is worth about two points.** Replacing it with the whole-word fuzzy source that preceded it costs 1.7 points on dev and 1.2 on test. That source could only emit candidates as long as the letters typed so far, so mid-word it could not complete a word at all once a click had slipped; the beam completes the typed prefix *through* the error. Section 8.4 is where this component earns its keep properly.

**On clean input the fuzzy source is not worth anything.** Removing it entirely scores 0.3 points *above* the full system on dev and 0.1 above on test. Both differences are inside the noise floor, so the honest statement is that spatial correction is free rather than beneficial when every click lands where it was aimed. It is insurance, and section 8.4 prices the premium.

**Taking PPM's word candidates out of the merge cost nothing and bought a great deal of latency.** Restoring them moves KSR by −0.6 and −0.5 points, both inside the noise floor, while median per-keystroke latency rises from 2.9 ms to 15.9 ms, a factor of five and a half. The character model was constructed without a dictionary, so its word path emitted fragments rather than words. It still trains and persists, because a fusion inside the prefix beam is the obvious next step and would need it, but it no longer votes.

**Seeded context tables are the largest single win in the engine's history.** Measured in the same harness while the seeds were being built (`architecture/NGRAM_SEEDS.md`), each row adding to the one above it:

| | dev KSR | test KSR | dev next-word | test next-word |
|---|---|---|---|---|
| Curated seeds only (~750 bigrams, ~740 trigrams) | 47.3% | 48.7% | 22.9% | 25.2% |
| + generated seed bigrams (~79,000) | 48.4% | 49.8% | 26.3% | 28.4% |
| **+ generated seed trigrams (~80,000, shipped)** | **49.1%** | **50.4%** | **28.7%** | **30.4%** |

+1.8 and +1.7 points of KSR, and +5.8 and +5.2 points of next-word hit rate. The next-word figure is the one to watch: it is what these tables directly address, and it moved more than three times as far as KSR, which averages it in with mid-word completions the prefix beam was already handling. The seeds are pruned from the forum language model published with the evaluation corpus (Vertanen and Kristensson, 2011); §3.3 gives the pruning. They cost 2.56 MB in the installed bundle and 414 ms at startup.

One caution about this row, which we state rather than leave for a reader to find: the seeds and the evaluation splits derive from the same research programme. The language model is built from web forum text and the evaluation sets from crowd-written AAC-like sentences, so they are different corpora collected for different purposes, and the splits remain held out from our engine in the sense that matters here, namely that nothing in them was used to fit anything. But a seed table drawn from the same authors' modelling of conversational English is closer to this test material than an arbitrary external corpus would be, and the +1.7 points should be read with that in mind.

**The merge strategy barely matters.** The four user-selectable scorers on `aac-test` score 50.4% (rank, the default), 50.5% (reciprocal rank fusion), 49.9% (linear), and 50.5% (log-linear). The whole spread is 0.6 points, inside the noise floor. This is a negative result about a user-facing setting, and it is worth stating plainly: the setting exists, it is defensible on the grounds that different strategies fail differently on individual queries, but we cannot demonstrate that any choice beats the default on aggregate.

### 8.4 Robustness to pointer error

The population this system targets does not click where it aims. `--mis-click` models the simplest version of that: one click per word, on the second character of every word of four letters or more, lands on a physically adjacent key and is never corrected by the user. The question is what the suggestion bar does about it.

| Condition | dev clean | dev + slip | test clean | test + slip |
|---|---|---|---|---|
| **Full system** | 49.1% | **44.7%** | 50.4% | **46.3%** |
| Pre-1.3.0 whole-word fuzzy | 47.4% | 35.7% | 49.2% | 38.1% |

One uncorrected slip per word costs the shipped engine 4.4 points on dev and 4.1 on test. It cost the previous engine 11.7 and 11.1. **The prefix beam is worth 9.0 and 8.2 points under pointer error**, against nothing at all on clean input, which is the clearest single argument in this evaluation for building a spatial model into a keyboard for this population. The share of words never predicted at any prefix tells the same story more starkly: it roughly doubles under slip with the old source, from 14.6% to 29.8% on dev, and moves from 13.6% to 16.2% with the beam.

**Learning the pointer's bias helps, but much less than the per-key simulation suggested.** `--pointer` simulates a pointer end to end: where each click lands, which key gets reported, and the presses the bias model learns from. For a systematically biased pointer (0.35 and 0.25 key-widths of offset, 0.15 of Gaussian scatter, so roughly a fifth of clicks land on the wrong key) on `aac-test`:

| | KSR | Never predicted |
|---|---|---|
| Reported keys only | 46.1% | 18.1% |
| + the click's position inside the key | 46.6% | 17.4% |
| + a learned per-position bias | 47.0% | 16.8% |

0.9 points from the whole feature, of which 0.4 comes from the learned bias. An earlier per-key simulation had measured intended-key recovery rising from 75% to 86.5% with a learned bias, which did not translate, because the prefix beam and the dictionary between them already recover most single-key errors from the reported key alone. We report it because it is the honest size of the effect and because the direction is consistent, not because it is large.

### 8.5 Personalisation

Every result above is cold start. `--learn-half` measures what the user's own typing is worth by training on the first half of a split and testing on the second, on `aac-dev` (n = 279 held-out sentences):

| | KSR | Next-word hit |
|---|---|---|
| Before learning | 49.3% | 29.4% |
| After learning the training half | 51.5% | 34.1% |
| Oracle: the test half already learned | 67.6% | 65.7% |

Learning in-domain material is worth 2.2 points of KSR and 4.7 points of next-word hit rate, which is a real effect and above the noise floor. The oracle row is the more interesting one. An engine that had already seen the exact sentences it is asked to predict reaches 67.6%, so **roughly 16 points of headroom separate the shipped engine from a perfect model of this user**. That number bounds what any amount of further personalisation can buy on this corpus, and it is the strongest argument in this paper for the federated-learning direction of section 10.2: the gap is large, and it is a vocabulary gap rather than an algorithmic one.

### 8.6 Latency

Median per-keystroke latency is 2.9 to 4.1 ms across conditions, with p95 between 10 and 16 ms, measured over every `predict()` call in a run on the author's development machine. The budget in section 7 is 30 ms typical, so the engine sits roughly an order of magnitude inside it. The only condition that approaches the budget is the restored PPM merge, at 15.9 ms median on test and 24.5 ms on dev with a p95 of 44.3 ms, which is what made removing it an easy decision once its contribution measured as zero.

These figures are a single machine and should be read as an order of magnitude rather than a specification. What they establish is the shape of the claim: the engine is not the reason the interface would feel slow, and there is no cloud round-trip on the path.

### 8.7 Deployment instrumentation

The offline benchmark measures the engine. The application separately instruments itself, which is what produces the only longitudinal evidence the project has, and section 9 is explicit about the weight that evidence can carry.

`src/analytics.py` maintains session and lifetime counters in `<config_dir>/analytics.json`: keystrokes, words, predictions offered and accepted, keystrokes saved, backspaces, sessions, minutes, the rank of each accepted suggestion, and capped word and key frequency tables. Nothing leaves the machine (section 5.1). The dashboard presents four framings of the same engine output, because in use they land differently on different days: keystrokes saved as an absolute count, the same figure converted to wall-clock at the user's own measured seconds per keystroke, the same figure as a share of total effort, and acceptance rate, which is the one genuinely separate signal because it asks how often an offered suggestion was worth taking rather than how much typing was avoided.

An earlier build surfaced a composite 0 to 100 "prediction quality" score, weighting savings, hit rate, rank-1 accuracy and backspace rate. It was removed because it was not actionable: a user can act on "you have saved four hours" and cannot act on "73 out of 100", which hides which term moved. The components are still tracked and exposed; only the composite was retired.

**These are one installation's numbers.** The lifetime figures shown below come from the author's own machine, computed by the software under evaluation, and they are reported here as evidence that the system has been in sustained real use rather than as a measurement of its benefit. Section 9 states the case against reading them as a result.

<p align="center">
  <img src="../assets/screenshots/analytics-dashboard.png" alt="Lifetime analytics dashboard showing 16.8k keystrokes saved, 153.1 hours saved, 50% effort saved, 28% acceptance, top words bar chart, and vocabulary / bigram / trigram counts." width="720" />
  <br /><em>Figure 4. Lifetime view of the in-app analytics dashboard, from the author's own installation (n = 1, self-reported, unblinded; see section 9). The four impact tiles (Keystrokes Saved, Time Saved, Effort Saved, Acceptance) are surfaced together because each lands differently with different mindsets: absolute count, wall-clock, percentage of effort, and engine-quality. The Lifetime / This Session toggle pivots every tile and chart from `_alltime_*` counters to in-session counters. The five framed cards below (Vocabulary, Bigrams, Trigrams, Top Pick, Saved) make the user's personal language model legible as raw counts rather than abstract scores.</em>
</p>

<p align="center">
  <img src="../assets/screenshots/language-model-word-cloud.png" alt="Word Cloud tab of the Your Language Model panel, showing learned vocabulary as circle-packed bubbles sized by frequency." width="480" />
  <br /><em>Figure 5. Word Cloud tab of the Your Language Model panel. Each bubble is a learned word, sized by unigram frequency. The visualization is built from `getVisualizationData()` over the live n-gram tables; clicking any bubble drills into that word's bigram predecessors, successors, and trigram windows. The Word Flow tab presents the same data as a network graph of bigram edges. Both surfaces pulse the matching node and edge when the user is actively typing, providing a live view of which part of their personal model is firing.</em>
</p>

---

## 9. Limitations

The evidence in section 8 is entirely of one kind: simulation against held-out text. That supports claims about the prediction engine and supports nothing about the system as an interface. The distinction matters enough to enumerate.

**There is no user study.** No participants, no task battery, no measured entry rate, no comparison against `osk.exe` or a commercial alternative with real users. Everything reported here about the interface, and section 4 is almost entirely about the interface, rests on design argument and on one person's daily use rather than on measurement. A study with motor-impaired participants is the single most valuable piece of missing work, and it is missing for the ordinary reasons: recruitment in this population is slow, and the project has no institutional affiliation to run it through.

**Keystroke savings is an upper bound, not a benefit.** The simulated user accepts a suggestion the instant it appears. A real user has to notice it, read it, decide, and land a pointer on it, and each of those has a cost that typing the next letter does not. Trnka and McCoy's finding that measured savings and observed rate improvement diverge substantially applies directly to every number in section 8. Our figures should be read as an ordering over engine configurations, which is what we use them for, rather than as a prediction of how much faster anyone types.

**The system has one long-term user, who is also its author.** He has muscular dystrophy and uses it daily as his primary text input, which is genuinely unusual as a design constraint and is why section 4 exists. It is also a sample of one, with all the selection effects that implies: the interface has been shaped, over years, to one motor profile, one set of habits, and one language. Where this paper says a decision was reversed after use, that is one person's experience reported honestly, not a finding.

**The longitudinal counters are self-reported and unblinded.** The lifetime figures in the analytics dashboard come from that same user's installation. They are the system's own accounting of its own behaviour, computed by the code under evaluation, from a single participant who knows what the numbers mean and wrote them. They are reported in section 8.7 as an existence proof of sustained real use, and should be read as nothing more.

**The evaluation corpus is a proxy.** The AAC corpus is crowd-written by workers imagining a scanning interface, not collected from people using this or any other keyboard. It is the closest public match available and its split-by-worker construction is sound, but the register of invented AAC-like sentences is not guaranteed to match the register of the correspondence, code, and medical communication this system is used for.

**Scope.** English only. Every language-specific rule now sits behind a profile object (section 3), but no second profile exists, and the tokeniser, the short-word list, the capitalisation rules, the phone and address matchers and the spatial model are all English and mostly American. Windows and Linux only, with macOS in progress. Password-field detection fails open by design, so on any platform or application where the accessibility APIs do not report a secure field, the user's manual control is the only protection.

**Single-machine performance figures.** Section 8.6 and the resource envelope in section 7 are measured or budgeted on one developer machine. No systematic measurement across the older, lower-power hardware the paper claims as its target has been done.

---

## 10. Known Gaps and Future Work

### 10.1 Known gaps relative to commercial keyboards

In rough priority order:

1. **Unified prediction-and-correction scoring.** LatinIME and Gboard score the literal typed word and all corrections in a single ranked list with a shared probability scale. The two-tier autocorrect threshold (§3.7) is a partial proxy; full unification is the proper fix and would clean up several classes of edge cases (deliberate-typing protection, low-confidence corrections that currently surface as suggestions but should not).
2. **Spatial edit costs in final ranking.** Key-distance weights from `fuzzy_recognizer` currently feed candidate *generation* but not the final rank. Folding them into the merge layer would let the engine prefer "the" over "rhe" in ambiguous contexts based on the fact that `r` is far from `t` in QWERTY.
3. **Katz / stupid backoff for sparse contexts.** Linear interpolation gives `λ₃·P_tri = 0` weight to the trigram term when the trigram has never been seen, which is correct but pessimistic. Katz backoff would discount seen events and redistribute mass to lower-order fallbacks. Larger-lift change (~100 lines) with a measurable quality gain on rare contexts.
4. **Seeding the context tables from a larger corpus.** Closed in 1.3.0 by the generated seed layer of §3.3, worth +1.8 and +1.7 points of keystroke savings and +5.8 and +5.2 of next-word hit rate (§8.3). What remains open is narrower: trigram seeding stops at the 20,000 most frequent contexts, and going to 50,000 moved the test split a further 0.3 points, inside the noise floor, for 2.5 times the file size and 315 ms more startup. The frequency-ranked prune works, in other words, and the tail really is tail.
5. **Vocabulary-pack disable undoes injection.** The current `PackManager.disable_pack` only clears the pack's own in-memory copy; it does not revert the per-word entries the pack pushed into the predictor's `unigrams` / `bigrams` / `trigrams` (which were merged with `max()`, see §3.1.2). Toggling a pack off therefore leaves its words ranking until the process restarts. Effectively invisible today because no built-in packs ship and very few users import their own; becomes a real correctness issue if built-ins return or imports become common. The clean fix is to track per-pack `(word, prior_value)` tuples at apply time and revert on disable, guarded so words that organic learning piled on top of (current value > pack's contribution) are not clobbered.

#### Closed gaps

- **Structured CycloneDX SBOM at release time.** Implemented (§7.4). `build/{windows,linux}/build.py::emit_sbom` writes a CycloneDX 1.6 SBOM alongside the plaintext lockfile on every build via `python -m cyclonedx_py environment --output-reproducible`. The worker side uses `@cyclonedx/cyclonedx-npm` via `npm run sbom`, chained before `wrangler deploy` by a `predeploy` script. A CI `osv-scan` job pinned to `google/osv-scanner-action` v2.3.8 reads both lockfiles and queries OSV on every push/PR, gating merges (`fail-on-vuln: true`) once the previously known Wrangler-3.x dev CVEs and the transitive `lxml` advisory were resolved (Wrangler 4 upgrade + `lxml>=6.1.0` pin). Meets the structured-SBOM bar that US Executive Order 14028 and most hospital / pharma / defence procurement asks for.
- **SymSpell for fuzzy matching.** Implemented. `src/prediction/symspell.py` provides a precomputed-deletion index with Damerau-Levenshtein post-filter. The previous candidate generator was capped at edit distance 1 and did not enumerate substitutions; the new path defaults to edit distance 2 and adds substitution coverage, so two-edit corrections (e.g. "becouase" → "because") and non-adjacent substitutions (e.g. "rxample" → "example") now surface. Lookup latency dropped from ~30 ms to ~0.75 ms on the 10K-word base dictionary; one-time index build is ~200 ms, paid eagerly during `FuzzyWordGenerator.set_frequencies` so the cost lands in startup instead of the user's first keystroke. See §3.6 for the integration; `tests/test_symspell.py` for the algorithm-level tests.

### 10.2 Federated learning

Federated learning would let users contribute to a shared model without sending raw keystrokes anywhere. The design is in `roadmap/FEDERATED_LEARNING.md`. Phase 1 (local delta computation. The user's machine produces a "diff" against the base model that summarises learned vocabulary) is the next step. Phases 2 and 3 (secure aggregation, differential privacy budgets) follow.

The motivation is strongest for the disability-community vocabulary case: users with rare conditions, specific medical equipment, or specialised AAC needs benefit disproportionately from shared vocabulary, but the same users have the strongest privacy concerns about raw keystroke data. Federated learning is the standard answer.

### 10.3 Ecosystem integration

Alpha-OSK is one of four tools in an adaptive-input platform (see `roadmap/ECOSYSTEM.md`):

| Tool | Output |
|------|--------|
| Alpha-OSK | Keystrokes (SendInput / xdotool) |
| MacroVox | Text (Deepgram speech-to-text → clipboard) |
| Octavium | MIDI (virtual piano / pads) |
| Nimbus | Joystick (vJoy / ViGEm) |

All four target the same mouse-driven, accessibility-first user. Integration phases progress from coexistence (today) → cross-launch and trigger → profile auto-switch → shared input layer → unified UI. The `roadmap/MACROVOX_INTEGRATION.md` and `architecture/MODULAR_LAYOUTS.md` documents cover specific integration paths.

---

## 11. Conclusion

Alpha-OSK is what happens when an accessibility-first OSK is built from scratch with a hard requirement that prediction quality match modern mobile keyboards on commodity hardware without a cloud round-trip. The architecture (a Qt Quick UI on top of a Python bridge, with a word n-gram merged with a spatial prefix decoder) is conventional in its parts, but the constraints from the user population (no focus stealing, no lost modifiers, no destructive prediction insertion, no GPU, off-network by default with the only optional egress being the explicitly opted-in usage-stats pipeline) shape the implementation in ways that diverge consistently from how a mainstream keyboard would be built.

The prediction stack is honest about its limits. It does not match Gboard's quality on rare contexts, it does not yet have unified scoring that lets the literal typed word compete against corrections in a single ranked frame, and the seed corpus is small. Each of these has a documented path forward and a rough cost estimate. None of them require fundamentally rethinking the architecture.

The accessibility-driven engineering decisions (the non-focus invariant, sticky modifiers held at the OS level, suffix-only prediction insertion, the right-click shifted variant, the edit-popup pattern) are the part of the work that does not appear in textbooks. They are also the part most likely to transfer to other accessibility tools building on the same hardware target.

---

## References

### Works cited

Cleary, J. G. and Witten, I. H. (1984). Data Compression Using Adaptive Coding and Partial String Matching. *IEEE Transactions on Communications*, 32(4), 396-402. The PPM construction used by `ppm_predictor.py`.

Garbe, W. (2012). *SymSpell: 1000x faster spelling correction*. github.com/wolfgarbe/SymSpell. The symmetric-delete index in `symspell.py` (§3.6).

Higginbotham, D. J., Shane, H., Russell, S. and Caves, K. (2007). Access to AAC: Present, past, and future. *Augmentative and Alternative Communication*, 23(3), 243-257. Rate enhancement as an access technology (§1.3).

Klakow, D. (1998). Log-linear interpolation of language models. *ICSLP*. The basis for the log-linear merge strategy (§3.1.2).

Kristensson, P. O. and Zhai, S. (2004). SHARK²: A Large Vocabulary Shorthand Writing System for Pen-based Computers. *UIST*, 43-52. Shape writing, and why it does not transfer to this population (§1.3, §4).

MacKenzie, I. S. and Soukoreff, R. W. (2002). Text Entry for Mobile Computing: Models and Methods, Theory and Practice. *Human-Computer Interaction*, 17(2-3), 147-198. Evaluation methodology for text entry (§8.1).

Trnka, K. and McCoy, K. F. (2008). Evaluating Word Prediction: Framing Keystroke Savings. *Proceedings of ACL-08: HLT, Short Papers*, 261-264. The keystroke-savings metric and the gap between simulated savings and observed benefit (§8.1, §9).

Vertanen, K. and Kristensson, P. O. (2011). The Imagination of Crowds: Conversational AAC Language Modeling using Crowdsourcing and Large Data Sources. *EMNLP*, 700-711. The evaluation corpus (§8.1) and the forum language model the shipped context seeds are pruned from (§3.3), both used under CC BY 4.0.

Vertanen, K., Memmi, H., Emge, J., Reyal, S. and Kristensson, P. O. (2015). VelociTap: Investigating Fast Mobile Text Entry using Sentence-Based Decoding of Touchscreen Keyboard Input. *CHI*, 659-668. Joint spatial and language decoding (§1.3).

Vescovi, M. *Presage: An intelligent predictive text entry platform*. presage.sourceforge.io. The layered-predictor framing this engine follows (§3.1.1).

Ward, D. J., Blackwell, A. F. and MacKay, D. J. C. (2000). Dasher: A Data Entry Interface Using Continuous Gestures and Language Models. *UIST*, 129-137. The character model as an accessible-input technique (§1.3).

AOSP LatinIME source. Reference implementation for trie-based dictionaries, weighted edit distance, and letting the literal typed word compete against corrections (§3.7).

Microsoft. *UI Automation overview*. learn.microsoft.com/en-us/windows/win32/winauto/. Password-field detection on Windows (§5.2).

AT-SPI 2. *Accessibility Toolkit Service Provider Interface*. Password-field detection on Linux (§5.2).

### Implementation documentation

The repository carries per-component design documents that this paper cross-references rather than reproduces: `architecture/PPM.md`, `FUZZY_RECOGNITION.md`, `HYBRID_MERGING.md`, `NGRAM_SEEDS.md`, `DICTATION.md`, `TELEMETRY.md`, `PLATFORM_ARCHITECTURE.md`, `COMPACT_VIEW.md`; `build/AUTO_UPDATE.md`, `WINDOWS.md`, `LINUX.md`; `research/SECURITY_AUDIT.md`; `roadmap/FEDERATED_LEARNING.md`, `ECOSYSTEM.md`; and `PRIVACY.md`. The benchmark harness is `scripts/bench/ksr.py` and `scripts/bench/fuzzy.py`, and the evaluation corpus and its provenance are described in `scripts/bench/data/README.md`.

---

*Alpha-OSK is developed by Owen Kent. Source repository: github.com/owenpkent/alpha-osk. Public releases: github.com/owenpkent/alpha-osk-releases.*
