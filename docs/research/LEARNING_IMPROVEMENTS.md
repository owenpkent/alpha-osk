# Learning & Prediction — Beyond the Rules-Based Approach

## Why this doc exists

Today's prediction stack works but is unmistakably *rules-based*: a stack
of hand-tuned constants, hardcoded lists, and per-condition carve-outs
that grew organically as bugs surfaced. This doc inventories what's
rules-based now, then lays out four directions for moving past it. None
of these are committed — they're options to choose from when there's
appetite for the work.

This is not a comparison of prediction *engines* (that's
`PREDICTION_OPTIONS.md` from earlier in the project). This is about
making the engine we already have less brittle.

---

## Inventory of the current rules

### Capitalization (`I` family only at output; collection paths still active)

`src/prediction/ngram_predictor.py`

- `_always_capitalize` (5 entries: I, I'm, I'll, I'd, I've) — the only
  rule `get_capitalized` consults today.
- `_ambiguous_names` (~130 entries: will, jack, may, mark, …) — kept in
  the source but unused; previously gated Tier 2 sentence-start cap,
  which has been removed.
- `data/proper_nouns.txt` (~8 000 entries) — still loaded into
  `self.capitalization` at startup, but `get_capitalized` no longer
  reads from that dict, so the data is currently inert in pills.
- `learn_capitalization` — still active. Records user-taught casings
  (right-click → Edit, prediction-click after typing a capital, word
  completion with non-trivial casing) into `self.capitalization`. The
  Caps Lock guard (`allow_uppercase = not _word_typed_under_caps_lock`)
  still applies. Persisted with the model.

The collection paths are kept so a future opt-in toggle can revive
proper-noun cap without re-teaching from scratch. Pills today get all
their non-`I`-family casing from the typed-prefix mirror in
`KeyboardBridge._display_cased`.

### Fragment / plausibility filter

- `_is_plausible_word` — vowel-and-consonant rule + a 41-entry
  `_SHORT_WORD_WHITELIST` for length ≤ 2 words. Applied in three places
  (learn, base wordlist load, saved-model load).
- The whitelist is hand-curated. "tv", "pc", "id", state codes — all
  rejected. `pm` is in (it's morning/evening, common in chat). The
  cut line is judgment, not data.

### Repetition gate

- New unknown words must be sighted `_candidate_threshold = 3` times
  before promotion to `user_vocab`. Magic number.

### Linear-interpolation n-gram weights

- `_LAMBDA_TRI / _LAMBDA_BI / _LAMBDA_UNI = 0.5 / 0.3 / 0.2` — hand-set,
  not derived from the user's data.
- Falls back to unigram-at-full-weight when no preceding context.

### Hybrid merge weights

`src/prediction/hybrid_predictor.py::_source_weights` (shared) and
the per-strategy scorers (`_score_rank` / `_score_rrf` /
`_score_linear` / `_score_loglinear`).

- N-gram weight: `3.0` for next-word, `1.0` for completion.
- PPM weight: `0.3` for next-word, `0.8` for completion.
- Fuzzy weight: `0.6` (from `DEFAULT_PREDICTION_WEIGHT`).
- Bigram bonus on fuzzy candidates: `1 + log1p(count)/2`.
- Personal-vs-base mix in unigrams: `personal_weight = 0.5`.
- RRF smoothing constant: `k = 60` (IR-standard, only used by the
  Consensus boost strategy).
- Log-linear floor: `1e-6` (only used by the Multiplicative strategy
  to prevent `log(0)` when a word is missing from a source).
- All hand-tuned.  The weights are shared across every merge
  strategy; the formula varies, the relative trust between predictors
  does not.  See `../architecture/HYBRID_MERGING.md` for strategy trade-offs.

### Autocorrect thresholds

`src/prediction/fuzzy_recognizer.py`

- Absolute confidence floor: `confidence_threshold = 0.65`.
- Relative margin (added recently): `autocorrect_margin = 1.5`.
- Per-edit penalties: `_TRANSPOSITION_PROB = 0.30`,
  `_DELETION_PROB = 0.20`, `_INSERTION_PROB = 0.15`,
  `_APOSTROPHE_INSERTION_PROB = 0.50`.
- Spatial uncertainty: `1.4` key-widths.

### Sentence-start detection

- Empty context = NOT sentence start (recent fix).
- Context ending in `.!?` = sentence start.
- That's it. (Uncertain whether Enter should be added.)

### Decay, blacklist, rehabilitation

- User vocab decay: periodic, fractional.
- Auto-rehabilitation of blacklisted words: `_rehabilitate_threshold = 3`.
- Dispreference downweighting: `score / (1 + count * 0.5)`.

**Total magic constants in the prediction path: ~25.** Each was the
right call when added; the cumulative weight is what feels unwieldy.

---

## Option A — Track and self-tune merge weights

### What it does

Log every prediction event with: which words were offered, in what
ranks, from which sources (`ngram` / `ppm` / `fuzzy` / `bigram-bonus`),
and which one (if any) the user picked. Periodically — say nightly, or
after every N picks — solve for the merge weights that would have made
the user's actual picks rank higher.

The hand-tuned constants `(3.0 / 1.0 / 0.3 / 0.8 / 0.6 / 0.5 / 0.5 …)`
become learned values, derived from the user's own typing.

### What changes

- New module: `src/prediction/weight_tuner.py`.
- New file on disk: `weights.json` next to `ngram_model.json`.
- Hybrid predictor consults this on init, falls back to defaults if missing.
- Analytics already records most of what's needed (rank, source) — minor
  extension to capture the full event.

### What stays

- All the rules / lists / filters above. The model is the same; only
  the weights move.

### Risks

- Cold start: a new user has no picks yet, so the defaults still need
  to be reasonable. Fine — that's where they are now.
- Sparse data: a user who types rarely won't get useful tuning for
  weeks. Fine — defaults still apply.
- Weight drift on bad picks: if the user accepts a wrong-but-close
  prediction (because the right one wasn't offered), the tuner could
  reinforce the wrong source. Mitigate with a regularisation term.

### Effort

~2–3 days. Pure Python, no new dependencies. Testable offline.

### Why pick this

It's the smallest disruption that converts the largest chunk of magic
numbers into observed values. Doesn't change the engine's shape, just
its parameters. Highest impact-per-line of any option here.

### Why skip it

If you want a *qualitatively* better predictor (richer next-word
suggestions, better paraphrasing, idiom awareness), a learned-weights
n-gram still has the same n-gram ceiling. Tuning won't break that
ceiling.

---

## Option B — Drop the capitalization tiers (SHIPPED, variant)

A variant of this option shipped: `get_capitalized` was gutted to the
`I`-family only (Tier 1). Tiers 2 and 3 — sentence-start auto-cap for
`_ambiguous_names`, and the proper-noun / user-taught lookup — were
removed from the output path. Pills now mirror the typed prefix's
casing via `KeyboardBridge._display_cased`; "shift / caps lock is the
cap signal" is the user-facing rule.

### Why this variant rather than the originally-described one

The original sketch deleted `_ambiguous_names`, the
`data/proper_nouns.txt` loader, and the persisted `capitalization`
dict, leaving only the runtime `learn_capitalization` map. The
shipped change is more conservative:

- `_load_proper_nouns` still runs at startup.
- `learn_capitalization` still records user-taught forms (right-click
  → Edit, prediction-click after typing a capital, word completion
  with non-trivial casing). The Caps Lock guard still applies.
- `self.capitalization` is still persisted in `ngram_model.json`.
- `_ambiguous_names` is still in the source, just unreferenced.

`get_capitalized` simply doesn't read any of it. Keeping the
collection paths means a future opt-in toggle (e.g. *Settings →
Suggestions → Capitalize proper nouns*) can flip the behaviour back
without re-teaching from scratch and without a data-format migration.

### Cold-start regression — actual outcome

The original "Risks" section flagged that a fresh user typing
`monday` would see `monday` back (not `Monday`). That's exactly what
happens now, and it's the *intended* behaviour: the user's stance
is that auto-cap on common-name proper nouns ("hope", "rose", "may",
"mark", "monday") was firing on too many ordinary English words and
producing more wrong-case pills than it fixed. Pressing shift on the
first letter remains the way to get a capital.

### What's left to consider

- A future opt-in toggle that flips `get_capitalized` back to
  Tier 1 + Tier 3 (skipping Tier 2, which had the worst false-positive
  rate). Driven by `self.capitalization`, no data migration needed.
- Removing `_ambiguous_names` and the unused inventory entries if a
  follow-up code-shrink pass is wanted. Currently inert but harmless.

---

## Option C — Switch on the transformer

### What it does

`src/prediction/transformer_predictor.py` already exists, scaffolded
behind `enable_llm=False`. Flipping it on gives the hybrid predictor a
fourth source: a small language model (e.g. distilgpt2) that re-ranks
the n-gram candidates against actual learned linguistic context.

Magic constants don't go away — but they matter less, because a real
LM contributes most of the signal, and the n-gram path becomes a
fast first-pass rather than the primary engine.

### What changes

- `HybridPredictor(enable_llm=True)` in `keyboard_bridge.py`.
- `transformer_predictor.py` actually runs (it's been dormant).
- Bundle gains ~80–100 MB (the model weights).
- Per-prediction latency: +30–50 ms (CPU inference; GPU optional).
- First-launch latency: +500 ms one-time model load.

### What stays

- N-gram, PPM, fuzzy — all of it. The transformer re-ranks the merged
  candidate list rather than replacing it.
- All the rules. They become input features, not the final answer.

### Risks

- **Bundle size.** A 100 MB jump for an installer that's currently
  ~40 MB. AppImage / NSIS install times grow proportionally.
- **CPU on slow machines.** A user on an old laptop might feel the
  30–50 ms latency as a typing lag. Profile before shipping default-on.
- **Model behaviour.** GPT-2 era models hallucinate proper nouns and
  can produce stylistically off suggestions in a writing context.
- **Privacy story.** No external calls — runs locally — but bundling
  a generative model raises the bar for the privacy disclosure.

### Effort

Probably ~1 day to wire and test, but several days of *evaluation* to
decide if the quality bump justifies the bundle/latency cost.

### Why pick this

This is the only option here that breaks the n-gram ceiling on
prediction quality. If the user's complaint is fundamentally "the
suggestions feel dumb," nothing in A/B/D can fix that — only a real LM
can.

### Why skip it

Bundle size matters for an accessibility tool that needs to install
quickly and run on whatever the user has. And the n-gram model isn't
*bad* once Option A's tuning runs — it's quite good for casual chat
typing, which is most of what an OSK does.

---

## Option D — Just simplify what's there

### What it does

Audit every constant, dead branch, and overlapping rule. Consolidate.
Document trade-offs inline. Move tunable values to a single
`prediction_config.py` so they're discoverable. No behaviour change.

### What changes

- New file `src/prediction/config.py` with all the constants in one
  place, documented.
- A handful of dead code paths removed.
- Comments explaining *why* each constant is set the way it is.

### What stays

- Behaviour. Identical.

### Risks

- None to user-facing behaviour.
- Risk of stalling on bikeshedding: "what's the right place for this?"

### Effort

Half a day. Cosmetic.

### Why pick this

Pre-work for any of A / B / C. If the rules are unwieldy to read, fix
the readability first before changing the rules.

### Why skip it

Doesn't make the predictor smarter. The unwieldy feeling will persist
under tidier organisation if the underlying carve-outs remain.

---

## Combinations

- **A + D** — clean up and learn weights on the cleaned-up structure.
  Pragmatic. Preserves cold-start quality, makes runtime adaptive,
  keeps the engine shape. Probably the right "next quarter" plan.
- **B + A** — drop tiers AND learn merge weights. Maximally observed,
  minimally rules-based. Cold-start regression is the price.
- **A + C** — learned weights *and* a real LM. The Cadillac. ~4 days.
- **C alone** — fastest path to qualitatively better suggestions, at
  bundle/latency cost.

---

## Decision criteria

Pick based on what's actually bothering you:

| Pain point | Best fit |
|---|---|
| "The constants feel arbitrary" | **A** (track-and-tune) |
| "The capitalization tiers are confusing" | **B** with seeding |
| "The suggestions themselves are dumb" | **C** (transformer) |
| "I just want to read the code without three open tabs" | **D** (simplify) |
| "All of the above" | **A + D**, then evaluate **C** |

If we don't pick anything, the system stays correct and works. The
rules-based feeling is a maintenance smell, not a user-facing bug.
