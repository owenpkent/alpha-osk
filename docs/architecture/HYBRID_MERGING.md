# Hybrid Prediction — How the Predictors Combine

Alpha-OSK's public prediction surface is `HybridPredictor.predict`, but
under the hood four models vote on every suggestion and the final list
is a weighted, filtered, decorated merge.  This doc explains the
scoring rules in `HybridPredictor._merge_predictions` and how the
pieces fit.

Implementation: `src/prediction/hybrid_predictor.py`.

Design philosophy matches Presage and early Gboard/LatinIME: **fast
complementary predictors that each do one thing well, merged by linear
interpolation, with the word-level model dominating and the others
filling gaps**.

## The Four Predictors

| Predictor | File | Answers the question |
|-----------|------|----------------------|
| N-gram | `ngram_predictor.py` | "What word usually follows this word?" — unigram + bigram + trigram word counts. |
| PPM | `ppm_predictor.py` | "Given these characters, what character is likely next?"  Variable-order character model. |
| Fuzzy | `fuzzy_recognizer.py` | "Given the user probably misaimed, what word did they mean?"  Spatial error correction. |
| Transformer | `transformer_predictor.py` | "Re-rank the top candidates with an LLM."  Optional, async, off by default. |

Each runs on every keystroke (except the LLM, which is async and
opt-in).  `predict()` calls them in turn, then hands off to
`_merge_predictions`.

## The Core Merge

`_merge_predictions(ngram, ppm, fuzzy, n)`:

```
# 1. Score by source
is_next_word   = context ends with " "
ngram_weight   = 3.0  if is_next_word else 1.0
ppm_weight     = 0.3  if is_next_word else 0.8
fuzzy_weight   = FuzzyRecognizer.prediction_weight  # 0.6 default

for i, word in enumerate(ngram):
    scores[word] += ngram_weight / (i + 1)
for i, word in enumerate(ppm):
    scores[word] += ppm_weight   / (i + 1)
for i, word in enumerate(fuzzy):
    scores[word] += fuzzy_weight / (i + 1)

# 2. Penalise dispreferred words
for word in scores:
    dp = ngram.get_dispreference(word)
    if dp > 0:
        scores[word] /= (1 + dp · 0.5)

# 3. Sort, validate, capitalise, return top n
```

### Why those weights?

| Situation | N-gram | PPM | Why |
|-----------|--------|-----|-----|
| **Next-word** (space at end) | 3.0 | 0.3 | N-gram *is* the next-word authority; PPM produces character fragments that look like words but aren't ranked by word frequency.  Trusting PPM here produces noise. |
| **Mid-word** (completion) | 1.0 | 0.8 | N-gram still knows which dictionary words are common, but PPM is genuinely useful for partial prefixes the dictionary hasn't seen yet. |
| Fuzzy | 0.6 | 0.6 | Single tuned default — used to vary by accessibility profile, but the profile system was removed (see `FUZZY_RECOGNITION.md`).  0.6 is loud enough that legitimate spatial-correction candidates can beat n-gram on a clear typo, quiet enough that they don't drown out a clean partial-prefix completion. |

The `/ (i + 1)` is positional decay — rank-1 matters more than rank-5
from the same source.  It's linear, not exponential, so rank-10
still contributes roughly 10% of rank-1.

### Short-word filter

Two-letter words (`it`, `an`, `is`, `of`, …) are excluded from
**next-word** predictions to keep the pill bar populated with
higher-information content.  The literal `"i"` is whitelisted because
it's always grammatically plausible.  Mid-word completions bypass this
filter entirely — users should still be able to complete short words.

## Validation — `_is_valid_word`

Every candidate must:

1. Not be in `ngram.blacklist` (user explicitly suppressed it).
2. Be in `ngram.unigrams` (built-in dictionary + user vocabulary),
   **or** be one of ~30 hardcoded essential short words
   (`{"i","a","an","am","as","at","be",…}`).

This filter runs *during* the scoring loop, so blacklisted or
off-dictionary hallucinations from PPM never make it into `scores`.

## Dispreference Penalty

Users right-click a prediction and mark it "bad".  That increments
`ngram.dispreference[word]`.  At merge time:

```
scores[word] /= (1 + dispreference[word] · 0.5)
```

So one "bad" press halves the score; two presses cut it by 33% more;
it's monotonic and never hits zero.  Words can therefore recover if
the user starts typing them organically again (see auto-rehabilitation
below).

## Capitalisation at Output

Right before returning, each winning word goes through
`ngram.get_capitalized(word, sentence_start)`. The current rule is
deliberately minimal: only the **`I` family** (`I`, `I'm`, `I'll`,
`I'd`, `I've`) is auto-capitalised. Anything else is returned in the
casing it had on disk (lowercase for almost every dictionary entry).

The previous three-tier model (Tier 2 sentence-start for ambiguous
names like `will` / `jack` / `may` / `mark`, Tier 3 ~8 000 proper
nouns plus user-taught casings) was removed because it fired on too
many common English words ("the hope is", "a rose by", "may i", and
the post-period word in any sentence) and pills came back capitalised
when the user had typed lowercase. Pill-facing casing now comes
exclusively from `KeyboardBridge._display_cased`, which mirrors every
uppercase position from the typed prefix onto the pill — type
`monday` → `monday`, type `Monday` → `Monday`, type `MON` → `MONday`.

`sentence_start` is still computed (`bool(ctx) and ctx[-1] in ".!?"`)
and passed through but is currently ignored by `get_capitalized`.
`self.capitalization` is still populated by `_load_proper_nouns` and
`learn_capitalization` and persisted with the model — the data is
kept so a future opt-in toggle can revive proper-noun cap without
re-teaching, but it's intentionally unused at output today.

## The LLM Refinement Layer

If `enable_llm=True` and `TransformerPredictor` loaded successfully,
`predict_with_refinement` runs in two phases:

1. **Instant** — `predict()` returns the hybrid top-N right away.
   `predictionsReady` is emitted.  UI renders.
2. **Async** — `_refine_async` sends the top `n · 3` candidates plus
   context to the transformer in a background thread.  When it
   returns, `predictionsRefined` fires, but only if the user hasn't
   typed anything since (context match).

So the hybrid merge is always the primary experience; the LLM is a
*rerankier* that can quietly improve the order, never block, and
never produce hallucinations (it can only reorder the candidates
we already know are valid).

## Auto-Rehabilitation

If the user types a previously-blacklisted word 3 times in a row
(detected in `ngram.record_typed_word`), the word is automatically
un-blacklisted.  Applies to manual typing only, not prediction
selection — we assume if you keep typing it, you want it back.

## Learning Paths

User actions feed back into the models:

| Trigger | What gets learned |
|---------|-------------------|
| Word completed with space | n-gram unigrams/bigrams/trigrams, PPM trie, capitalisation (all-caps only learned if Caps Lock was off — see below) |
| Sentence ended (`.!?`) | Full sentence re-trains n-grams + PPM |
| Prediction selected | Word boosted (`learn_from_selection`), context→word association recorded |
| Prediction edited via right-click → "Edit" | Capitalisation recorded permanently (`set_capitalization`) |
| Word right-click → "Remove" | Blacklist entry added |
| Word right-click → "Bad suggestion" | Dispreference incremented |

All persisted to `ngram_model.json` + `ppm_model.json` on explicit
save or auto-save-on-exit.

### Capitalisation learning — Caps Lock vs. deliberate caps

`NgramPredictor.learn_capitalization` rejects all-uppercase typings
by default — those almost always come from Caps Lock being on, and
learning them would pollute the table with shouty forms of every
word the user typed under caps lock. But "all-caps typed
deliberately" is a real signal worth keeping ("HVAC", "ROFL", a
domain acronym).  The bridge tells the predictor which it is via
`learn_capitalization(word, allow_uppercase=...)`:

- `KeyboardBridge` carries a `_word_typed_under_caps_lock` flag,
  set whenever a character is appended to `_current_word` while
  `_caps_lock_active` is True. Reset at every word boundary.
- The space-handler and `pressPrediction` pass
  `allow_uppercase = not _word_typed_under_caps_lock`.  If the user
  right-clicked / shifted each letter (Caps Lock off the whole
  word), all-caps is allowed; if Caps Lock was on at any point,
  it's rejected.

So `HVAC` typed via four right-clicks then space is learned, but
`HELLO` typed under Caps Lock is not — even though both produce an
identical `_current_word`. The pill display uses the same
`_word_typed_under_caps_lock` distinction implicitly: pills are
case-mirrored from the typed prefix, and Caps Lock simply
uppercases everything.

Right-click → Edit goes through `set_capitalization`, which writes
directly to `self.capitalization` and bypasses `learn_capitalization`
entirely. Explicit user edits always win.

## Personal vs. Base Vocabulary (split-table scoring)

The n-gram unigram score inside `NgramPredictor.predict` blends two
separate tables in probability space:

| Table | Source | Updated by |
|-------|--------|-----------|
| `_base_unigrams` | Google 10K + 20K supplement + `data/base_dictionary.txt` + fallback common words | Loaded at startup / `_learn_base`.  Does not change during use. |
| `user_vocab` | The user's actual typing | `learn()` / `learn_word()`.  Recency-decayed. |

Scoring for a partial-prefix candidate:

```
alpha   = personal_weight   (default 0.7)
P_user  = user_vocab[w]    / _user_total
P_base  = _base_unigrams[w] / _base_total
score   = SCALE · [ alpha · P_user + (1 − alpha) · P_base ]
```

`SCALE = 100,000` brings the interpolated probability into the same
magnitude as the bigram/trigram scores added earlier in `predict`, so
context bonuses still move the needle.

**`_user_total` is tracked incrementally** — `learn`, `learn_word`,
`_apply_decay`, `clear_user_data`, and `load` all keep it equal to
`sum(user_vocab.values())`.  Don't recompute the sum in `predict()`;
the invariant is covered by
`tests/test_ngram_predictor.py::TestUserTotalIncremental`.

### Why the split matters

The old merged scheme stored base-dictionary frequencies and personal
typing counts in the **same** `unigrams` dict.  The Google 10K seeds
top words at ~10,000 while a personal word typed 10 times sat at 10.
The multiplicative user boost `(1 + count · 0.1)` couldn't close that
gap — a word like "Claude" typed 10 times scored ~10, while "can"
scored ~5,000.  Personal vocabulary effectively never surfaced.

Under split-table scoring, `P_user(claude) = 10 / user_total` is
~0.01 after a few hundred words of typing; at alpha = 0.7 that gives
7 "units" of score, which beats the top dictionary word's
`0.3 · 0.002 · 100000 = 60` by an order of magnitude once enough
personal use accumulates.  The knob to tune this balance is
`NgramPredictor.personal_weight`.

### Known limits

- **Early-typing dominance.**  With only a handful of user_vocab
  entries, any one word has `P_user ≈ 1` and dominates regardless of
  alpha.  Works itself out after ~100 words of typing; could be
  smoothed with a `max(user_total, N)` floor if it bites.
- **Bigrams/trigrams are still merged.**  `bigrams` and `trigrams`
  hold both base-loaded and user-learned counts together, so the
  same "base drowns personal" problem exists for context predictions
  (`hi ___`).  Worth splitting next if the unigram fix helps.

## Recency Decay

Every `_decay_interval` learn calls, `ngram._apply_decay` multiplies
all user-vocab and user-learned bigram counts by `_decay_factor` (0.95
by default).  This prevents a flurry of typing on one topic from
dominating predictions months later.  PPM does **not** currently decay
— see `PPM.md` "Known Limits".

## Trade-offs Baked into the Weights

- **N-gram >> PPM for next word** — Deliberately harsh.  If you want
  PPM to contribute more to next-word, raise `ppm_weight` in
  `_merge_predictions`; the cost is noisier predictions when the
  context is clear.
- **Fuzzy weight is fixed at 0.6** — Used to be profile-driven (0.3–0.8
  across six "accessibility profiles") but the profile UI was confusing
  and the per-user dials never carried their weight.  Now there's one
  generous default tuned to surface diagonal-neighbour mistypes the way
  Gboard does.  See `FUZZY_RECOGNITION.md`.
- **Positional decay is linear** — Rank-based, not probability-based.
  Fine for short lists; if `n` grew to 20+, an exponential decay
  would better reflect how users actually scan pill bars.
- **LLM is a rerankier, not a generator** — By design, it can only
  reorder the hybrid candidates, preserving vocabulary/blacklist
  guarantees.

## Is the Weighted-Rank Approach Best? — Critique and Alternatives

The current merge is honest about what it is: **rank-based fusion with
hand-tuned weights**.  Each source ranks its top candidates, then the
merge collapses those rankings via `weight / (rank + 1)`.  Three
substantive criticisms:

1. **Confidence is thrown away.**  N-gram's `predict` returns
   `(word, probability)` internally and strips the probability before
   returning (`ngram_predictor.py:423`).  Fuzzy's `get_fuzzy_predictions`
   returns `List[Tuple[str, float]]` — the score is dropped at
   `hybrid_predictor.py:183`.  A 0.99-confident rank-1 and a 0.51-confident
   rank-1 contribute identically.  The merge is blind to the gap between
   "I'm sure" and "best of a bad bunch."
2. **Weights are hand-picked, not tuned.**  `3.0 / 0.3 / 0.6` are
   reasonable guesses, but no offline evaluation set or held-out
   perplexity check validates them.  Drift in any one predictor (e.g.
   PPM growing more confident as the user types more) doesn't update the
   weights.
3. **Source-scale mismatch is hidden, not solved.**  Linear positional
   decay across heterogeneous rankers only works because rank
   *magnitudes* happen to be similar.  If one source returned 50
   candidates and another 5, the longer list would dominate purely
   through rank coverage.  The `n * 2` cap in `predict()` papers over
   this without addressing it.

The current scheme's redeeming features: it's fast, deterministic,
trivially debuggable, and never produces a numerical surprise.  Every
alternative below trades some of that for better ranking quality.

### Alternative A — Probability-space linear interpolation (Presage-style)

Each predictor returns a normalised probability over its candidate set,
combined as:

```
P(w | context) = λ_ng · P_ng(w) + λ_ppm · P_ppm(w) + λ_fz · P_fz(w),    Σλ = 1
```

This is the textbook language-model mixture and what Presage actually
does internally.  `NgramPredictor.predict` already computes calibrated
probabilities (linear interpolation of trigram/bigram/unigram in
probability space — see "Personal vs. Base Vocabulary" above) and
throws them away; this alternative simply stops throwing them away.

- **Pros**: Confidence is preserved.  λ can be EM-tuned on a held-out
  corpus.  Composes cleanly with the LLM rerank step (LLM probabilities
  live in the same space).
- **Cons**: Each source must produce a *comparable* probability.  PPM
  word probability is well-defined but expensive (sum over all
  completions of a prefix); fuzzy emits a spatial score that needs
  softmax normalisation to behave like a probability.  Sources with
  empty output (fuzzy on a fresh word) need explicit zero-handling so
  they don't divide-by-zero the mixture.
- **Cost**: ~50 lines.  Each predictor's `predict_with_scores` would
  return `List[Tuple[str, float]]` (some already do); the merge replaces
  positional decay with score-weighted addition.
- **When it helps most**: cases where one source is *very* confident
  and the others are noise — e.g. mid-word with a clean prefix the
  n-gram dictionary nails, or a clear typo fuzzy is sure about.

### Alternative B — Reciprocal Rank Fusion (RRF)

The IR-standard rank-merge formula:

```
score(w) = Σ_i  1 / (k + rank_i(w)),    k = 60
```

Identical structure to our current code; the only change is the damping
constant `k`.  With `k = 0` (effectively today), rank-1 dominates rank-2
by 2× and rank-3 by 3×.  With `k = 60`, the rank-1/rank-2 ratio is
~1.02 — agreement across sources matters far more than which source
ranked something first.

- **Pros**: One-line code change.  Provably robust to score-scale
  differences across sources (the original RRF paper's whole point).
  No tuning needed; `k = 60` is the default that ships in elasticsearch
  and Vespa.  Predictably promotes consensus picks.
- **Cons**: Still discards confidence — same critique as today, just
  with a saner formula.  Per-source weights still hand-picked.
- **Cost**: ~5 lines.  Replace `weight / (i + 1)` with
  `weight / (k + i + 1)`.
- **When it helps most**: when sources frequently *almost* agree
  (e.g. n-gram and PPM both have "the" in the top 3 but at different
  positions) — RRF promotes consensus much more aggressively than the
  current formula.

### Alternative C — Log-linear / Naive-Bayes-style mixture

Treat sources as conditionally independent evidence:

```
log P(w) = Σ_i  λ_i · log P_i(w | context)
```

Multiplicative in probability space, additive in log space.  Common in
ASR and search.  The independence assumption is wrong (n-gram and PPM
both see the user's typing and will correlate) but in practice this
works surprisingly well when sources cover *different* aspects (lexical
vs spatial vs character-level).

- **Pros**: Naturally penalises words only one source likes (one zero
  → log(0) → −∞).  The `λ_i` become per-source temperatures, easier
  to interpret than additive weights.
- **Cons**: A single source returning 0 wipes out the candidate
  entirely — too aggressive when one predictor is just blind to the
  word (PPM hasn't seen it, doesn't mean it's wrong).  Needs floor
  smoothing (`max(P_i, ε)`) which reintroduces a magic number.
- **Cost**: same as Alternative A.
- **When it helps most**: precision-critical autocorrect contexts
  where you'd rather show fewer, surer candidates.

### Alternative D — Confidence-weighted merge (minimal-change variant)

Keep the current architecture but replace `1 / (rank + 1)` with the
*actual* normalised score from each source:

```
P_i(w) = score_i(w) / sum_v score_i(v)        # softmax per source
combined(w) = Σ_i  λ_i · P_i(w)
```

Architecturally indistinguishable from Alternative A; ergonomically the
smallest delta because no predictor's public API has to change shape.
We can normalise inside `_merge_predictions` from whatever score field
each source happens to expose.

- **Pros**: Smallest behavioural change consistent with addressing the
  "confidence is thrown away" critique.  Backwards-compatible with the
  rest of the merge pipeline (validation, dispreference, capitalisation
  all unchanged).
- **Cons**: Per-source softmax temperature is yet another knob.  Inherits
  the "λ are hand-picked" critique.
- **Cost**: ~30 lines.
- **When it helps most**: same as A, slightly less principled but ships
  in an afternoon.

### Alternative E — Mixture-of-experts gating

Stop using fixed weights; pick the *primary* source dynamically based
on context features.  Hand-coded gate sketch:

| Context | Primary | Secondaries |
|---------|---------|-------------|
| Empty / sentence start | n-gram unigram | — |
| `prev_word + " "` (next-word) | n-gram bigram/trigram | LLM rerank |
| Mid-word, prefix matches dict | n-gram completion | PPM |
| Mid-word, prefix is messy | fuzzy | n-gram |
| Long context, low n-gram confidence | LLM | n-gram fallback |

The current code already has a crude version of this (the `is_next_word`
branch flips ngram_weight 1.0 ↔ 3.0).  A full version would have ~5–8
explicit cases.

- **Pros**: Each predictor contributes only when it's the right tool
  for the job.  Predictions become more interpretable ("we showed you
  these because fuzzy was driving").  Closer to how Gboard's internal
  scorer actually behaves.
- **Cons**: A pile of `if/elif` that's harder to test and easier to
  drift.  Each new context type is another branch.  Requires defining
  "low confidence" numerically — you're back to thresholds.
- **Cost**: ~150 lines plus tests for each branch.
- **When it helps most**: when the right answer to "which predictor?"
  varies a lot by context (which it does — n-gram is useless on
  pre-typing, fuzzy is useless on next-word).

### Alternative F — Learning-to-rank from user selections

Treat the merge as a supervised ranking problem.  Features: per-source
score, per-source rank, source identity, recency, word length, bigram
presence, fuzzy spatial cost, etc.  Label: did the user click this
prediction?  Train a lightweight model (LambdaMART, RankNet) on the
user's own session data.

- **Pros**: Self-tuning to the *individual user's* typing patterns.
  Captures interactions the hand-tuned weights can't (e.g. "this user
  trusts fuzzy on long words but not short ones").  Recovery from
  weight drift is automatic.
- **Cons**: Heavyweight.  Needs a labelled stream (we already log
  prediction selections via `learn_from_selection`, but not negatives).
  Cold start is brutal until ~hundreds of selections accumulate.  Adds
  a model-versioning concern (do we ship a starter model, or train
  per-user from zero?).  Personalised ranking models are also a
  privacy surface — they encode typing style.
- **Cost**: ~500 lines plus a feature-extraction layer plus model
  serialization.  Realistically a multi-week project.
- **When it helps most**: late-stage optimisation, after the fixed
  scheme has been pushed as far as it goes.

### What Real Keyboards Actually Do

Industry datapoints, in order of how directly they speak to our setup:

- **Presage's `MeritocracyCombiner`** (the closest architectural analog
  to Alpha-OSK — multiple pluggable predictors, on-device, classical
  ML).  Each suggestion's *probability* is its merit; final ranking is
  by sum/max of probabilities across predictors.  This is exactly
  **Alternative A** above.  Presage's own documentation flags the
  calibration risk verbatim: *"this combination strategy might
  introduce some imbalance between different predictive methods that
  calculate probabilities substantially differently."*  Real systems
  hit the same trap and ship anyway.
- **Klakow (1998)** benchmarked log-linear vs linear interpolation
  on n-gram smoothing and reported a ~20% relative perplexity
  improvement for log-linear.  The same multiplicative form
  (`score(w) = P_unigram(w) · P_ngram(w | context)`, optionally with
  per-source `P^α` power weights) shows up in shipped commercial
  combiners — multiplicative consensus, not weighted-rank fusion, is
  the dominant pattern in production.  This is **Alternative C**
  (log-linear).
- **Gboard 2024** ([Neural Search Space, EMNLP 2024]) replaces the
  n-gram FST entirely with a neural-LM-derived FST composed with the
  spatial decoder.  Different paradigm — they don't merge ranked
  lists, they compose probabilistic transducers.  Out of scope for
  Alpha-OSK without a major architecture shift, but the philosophy
  ("structure beats post-hoc weighting") tracks with our
  mixture-of-experts (Alternative E) intuition.
- **SwiftKey** ships a neural network *blended with* the legacy n-gram
  engine.  Neither the formula nor the weights are public, but the
  architecture is explicitly hybrid — they didn't replace n-gram, they
  added a second source.
- **Apple QuickType** (iOS) uses on-device bi-LSTMs with personal
  adaptation.  Combination details proprietary.
- **Klakow 1998** ("Log-linear interpolation of language models")
  benchmarked log-linear vs linear on n-gram smoothing and reported
  ~20% relative perplexity improvement for log-linear.  This is the
  empirical case for **Alternative C over A** when both are on the
  table.

Two patterns recur across the industry sources: (1) score-space, not
rank-space — every cited system uses actual probabilities, not
positional decay; and (2) the weights are tuned by empirical
evaluation, not chosen by gut.

### What AAC research says about the user-side ceiling

The merge formula isn't the only thing affecting felt prediction
quality.  Three findings worth holding next to any ranking change:

- **Keystroke-savings ceiling is ~50–60%** for traditional word
  prediction, regardless of model sophistication, once the user
  actually has to scan and select.  ([Trnka et al., AAC keystroke
  savings limit])  Pushing the ranking from "good" to "very good"
  matters less than it looks like in offline metrics, because the
  human-side scan/select cost dominates.
- **Prediction *utilisation* tracks ranking quality more than
  keystroke savings does**: in a comparison study, the better
  algorithm got 93.6% utilisation vs 78.2% — users picked predictions
  more often when they trusted them.  That's the felt-quality lever a
  rank-fusion change could move.
- **LLM-based abbreviation expansion** (a category beyond what we
  do — type the consonants, get the full sentence) can push effective
  keystroke savings to ~77%.  Outside the scope of merge formula, but
  a useful ceiling marker for "how much further can we go without
  changing the input model."

### Status — all four strategies now ship behind a setting

As of the merge-strategy-picker work (Settings → Smart Typing →
Suggestion Engine), all four merge formulae are implemented and
user-selectable at runtime:

| Setting value | UI label | Implementation |
|--------------|----------|----------------|
| `rank` (default) | Default | `HybridPredictor._score_rank` |
| `rrf` | Consensus boost | `HybridPredictor._score_rrf`, k = 60 |
| `linear` | Confidence-weighted | `HybridPredictor._score_linear` |
| `loglinear` | Multiplicative | `HybridPredictor._score_loglinear`, floor = 1e-6 |

Plumbing changes that landed alongside:

- `NgramPredictor.predict_with_scores` and
  `PPMWordPredictor.predict_with_scores` return the raw per-source
  scores `predict()` used to throw away.  `predict()` is preserved as
  a thin word-only wrapper for back-compat.
- `_merge_predictions` is now a strategy dispatcher; shared
  post-processing (dispreference, sentence-start capitalisation,
  short-word filter, capping) lives in `_finalize_scores`.
- `HybridPredictor.set_merge_strategy(name)` is the public API.  QML
  flips it via `keyboard.setMergeStrategy(...)` from
  `KeyboardBridge`, with the choice persisted in
  `appSettings.savedMergeStrategy`.
- The `is_next_word` weight switch (3.0/0.3/0.6 → 1.0/0.8/0.6) is
  shared across every strategy via `_source_weights`.  The bigram
  bonus on fuzzy candidates (`_bigram_bonus`) likewise applies in
  every strategy — the formula varies, the context signal does not.

The default **must** stay `rank` — every existing user's pill ranking
depends on it, and there's no migration prompt.  A change of default
would silently re-rank predictions for every installed copy on the
next launch.  Don't.

### When each strategy is most useful

Three candidates now plausible, in order of evidence-backed expected
impact:

- **Log-linear / multiplicative mixture (Alternative C)** is the form
  used by shipped commercial combiners for systems like ours, and
  Klakow's empirical result favours it over linear interpolation by
  ~20% relative perplexity.  The "single zero wipes out the candidate"
  failure mode is real but solvable with `max(P_i, ε)` floor smoothing
  — a one-line fix that every shipping log-linear system has.  This is
  the technically-best-supported direction.
- **Probability-space linear interpolation (Alternative A)** is what
  Presage actually ships, with full awareness of the calibration
  risk.  Lower risk than C (no zero-collapse failure mode), still
  honest about confidence, and the closer match to the existing code
  shape.  Easier sell as a first migration step.
- **Reciprocal Rank Fusion (Alternative B)** remains the cheapest
  patch — five lines — and is what you'd ship if the goal is "stop
  tying everything to exact rank position with no other change."  Use
  RRF as a stopgap if the score-extraction work in A or C is too big
  to take on right now.

Migration progress:

1. ~~Plumb scores through.~~  **Done** — `predict_with_scores` exposes
   raw per-source scores end-to-end.  Each source still uses its own
   scale; the strategies normalise per source before combining.
2. ~~Ship A and C behind a flag.~~  **Done** — both are
   user-selectable in Settings → Smart Typing → Suggestion Engine, alongside RRF.
   The default stays `rank` so existing users see no change.
3. **Tune `λ` empirically.**  Still open.  Grid-search on a held-out
   corpus of the user's own typing for held-out perplexity.  The
   currently-shared 3.0/0.3/0.6 weights skip this entirely.  Now
   feasible per-strategy since the score plumbing is in place.

Mixture-of-experts (D) and learning-to-rank (F) sit on top of any of
A / B / C and only make sense once the underlying score plumbing is
honest — no point gating between predictors whose scores aren't
comparable.

## Known Gaps / Future Work

1. **Unified scoring with the literal typed word.**  Commercial
   keyboards score the literal typed characters as one of the
   candidates, so autocorrect only fires when an alternative scores
   1.5–2× higher.  We don't — autocorrect uses a flat confidence
   threshold.  (Also called out in `FUZZY_RECOGNITION.md`.)
2. **Key-distance weights in the final ranking.**  Fuzzy's spatial
   model feeds candidate generation but not final merge scores.  A
   nearby-key match should count more than a far-key match even
   after dictionary filtering.
3. **Confidence-aware merge.**  The current rank-based fusion discards
   each predictor's score.  See "Is the Weighted-Rank Approach Best? —
   Critique and Alternatives" below for the trade-offs and concrete
   replacement options.
4. **LLM could suggest new candidates, not just reorder.**  Current
   implementation is defensive; a more integrated path would let the
   LLM propose words outside the candidate list (with sandboxing).

## Public API for External Callers

Code outside `src/prediction/` should go through `HybridPredictor`, not
reach into `_ngram` / `_ppm` / `_fuzzy`.  The bridge and anything else
that needs raw data should use these forwarders:

| Method | Returns | Used by |
|--------|---------|---------|
| `get_unigram_freqs()` | merged `unigrams` dict (base + user) | `keyboard_bridge.processSwipe` (candidate set for the swipe decoder) |
| `get_capitalized(word, sentence_start)` | `str` | same, to render "iPhone" / "Owen" correctly on decoded swipes |
| `learn`, `learn_word`, `learn_from_selection`, `predict`, `predict_with_refinement` | — | all normal prediction paths |
| `blacklist_word`, `unblacklist_word`, `mark_bad_suggestion`, `remove_dispreference` | — | right-click word suppression |
| `enable_vocabulary_pack`, `disable_vocabulary_pack`, `import_vocabulary_pack` | — | vocabulary-pack UI |

If you need data that isn't exposed, add a new forwarder here rather
than reaching through private attributes.  Private access from the
bridge or UI was removed during the security review; don't re-introduce
it.  CLAUDE.md "Things to Watch Out For" calls this out.

## References

### Architectural priors
- **Presage** — https://presage.sourceforge.io/ (pluggable predictor
  architecture that inspired the hybrid merge design).
  `MeritocracyCombiner` (probability-as-merit ranking) docs:
  https://presage.sourceforge.io/documentation/presage/doc/html/classMeritocracyCombiner.html
- **LatinIME (AOSP)** — trie-based dictionary with weighted edit
  distance and n-gram LM scoring.
- **Dasher** — `PPM.md` for full references.

### Foundational research
- **Goodman, Venolia, Steury, & Parker (2002)** — *Language modeling
  for soft keyboards.* IUI.  Unified spatial + LM probability model;
  reduced word error rate from 38.4% → 5.7% on a soft keyboard.
  https://www.microsoft.com/en-us/research/publication/language-modeling-for-soft-keyboards/
- **Klakow (1998)** — *Log-linear interpolation of language models.*
  ICSLP.  Empirical comparison of log-linear vs linear interpolation
  on n-gram smoothing — log-linear wins by ~20% relative perplexity.
  https://www.isca-archive.org/icslp_1998/klakow98_icslp.html

### Industry datapoints
- **Zhang et al. (EMNLP 2024)** — *Neural Search Space in Gboard
  Decoder.*  Neural-LM-derived FST replaces the n-gram FST in
  Gboard's decoder; reduces Words Modified Ratio by 0.26%–1.19%.
  https://aclanthology.org/2024.emnlp-industry.93/
- **Hard et al. (2018)** — *Federated Learning for Mobile Keyboard
  Prediction.*  CIFG architecture, 1.4M params, FedAvg training.
  https://arxiv.org/abs/1811.03604
- **Xu et al. (2023)** — *Federated Learning of Gboard Language
  Models with Differential Privacy.*  All current Gboard NN-LMs
  ship with DP guarantees.  https://arxiv.org/abs/2305.18465

### AAC / accessibility evaluation
- **Trnka & McCoy (2008)** — *Word prediction and communication rate
  in AAC.*  Quantifies utilisation gap between basic and advanced
  prediction algorithms (78.2% → 93.6%) and 50–60% keystroke-savings
  ceiling.
  https://www.eecis.udel.edu/~mccoy/publications/2008/trnka08at.pdf
- **Cai et al. (2025)** — *Adapting Large Language Models for
  Character-based AAC.*  LLM-based character prediction outperforms
  n-gram baselines for letter-by-letter input.
  https://arxiv.org/abs/2501.10582

### Rank fusion (IR literature)
- **Cormack, Clarke, & Buettcher (2009)** — *Reciprocal Rank Fusion
  outperforms Condorcet and individual rank learning methods.*
  SIGIR.  Origin of the `1/(k+rank)` formula and `k=60` default.
  Now the standard in elasticsearch, Vespa, Azure AI Search.
