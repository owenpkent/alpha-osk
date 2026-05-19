# Fuzzy / Spatial Recognition — Design

Fuzzy recognition is Alpha-OSK's **accessibility-first spelling / typing
error corrector**.  The idea is simple: when someone with tremor or
limited precision aims for `h`, they might land on `g`, `j`, or `y`
instead.  The recognizer models this spatial uncertainty and proposes
the word the user most likely *meant* to type.

Implementation: `src/prediction/fuzzy_recognizer.py`.

## Three Collaborating Classes

| Class | Responsibility |
|-------|----------------|
| `SpatialKeyModel` | Given a clicked key, returns P(intended = k) for nearby keys. |
| `FuzzyWordGenerator` | Expands a typed string into candidate strings using the spatial distribution, intersects with the dictionary. |
| `FuzzyRecognizer` | Public entry point. Wires the above together and decides whether to *auto*-correct. |

## Spatial Model — `SpatialKeyModel`

### Key layout

`QWERTY_POSITIONS` assigns each letter a `(row, col)` with the standard
stagger (home row offset +0.25, bottom row +0.75).  Units are
key-widths.

The number row sits at row -1 directly above the qwerty row, with no
horizontal stagger (5 above t, 6 above y, etc.).  Including digits in
the spatial model means an off-by-one-row mistype between letter and
digit is recoverable: typing "h3llo" surfaces "hello" because '3' is
now a near-neighbour of 'e' (distance 1.0).  Same-row digit-to-digit
nearness (4↔5↔6) falls out of the same Euclidean distance metric for
free, so no special-casing is needed.  Punctuation and the numpad
remain unmapped — punctuation has a different error mode (different
fix), and the numpad is spatially isolated from letters and has no
dictionary to correct against.

### P(intended | clicked)

For a clicked key, the neighbour cache stores every key within
`1.5 × uncertainty_radius`.  The probability another key was *intended*
is a Gaussian on Euclidean key-distance:

```
sigma   = uncertainty_radius / 2
P(key)  = exp( − distance² / (2 · sigma²) )     if distance ≤ radius
P(key)  = 0                                      otherwise
```

Divide-and-normalize so probabilities sum to 1.  The clicked key always
has distance 0 → highest probability, and probability falls off
smoothly with distance.  Sigma is half the radius so ~95% of the
Gaussian mass sits inside the configured uncertainty radius.

### Neighbour cache

Built once in `_build_neighbor_cache`: O(K²) in the number of keys
(~1296 pair checks for the 36 mapped keys — still cheap).  Rebuilt
only if `set_uncertainty_radius` is called at runtime; otherwise the cache
is permanent.

## Candidate Generation — `FuzzyWordGenerator`

Expands a typed string into weighted candidate strings, then
intersects with the dictionary.

```
_generate_fuzzy_sequences("hel"):
  for each char c in typed:
    multiply every (prefix, p) in beam by every (c', p(c'|c))
    prune: drop any combined probability < min_prob (default 0.001)
    keep top 2 · max_candidates (100) by probability
```

This is a **beam search over possible typing interpretations**, one
character per step.  For a 5-letter word with an average of 6 neighbours
per key, the unpruned search would be 7,776 paths; pruning keeps it at
a few hundred.

`generate_candidates` then filters the surviving sequences to those
that are in `self.dictionary`, returning `(word, probability)` sorted
by probability.  In parallel, `_edit_distance_candidates(typed)` runs
three single-edit transformations of the literal typed string —
**transposition** (swap each adjacent pair: "teh" → "the"),
**deletion** (drop each char: "thee" → "the"), and **insertion** (try
each `a-z'` letter at each position: "th" → "the", "im" → "i'm") —
and merges the dictionary hits into the same scored set.  The two
sources never duplicate because the spatial path can't express
length-changing edits.  Insertion is skipped for inputs over 12 chars
to keep the per-keystroke cost bounded.

The dictionary stores **frequencies**, not just membership: each
candidate's spatial / edit probability is multiplied by `log(freq + 1)`
before sorting.  Without this, "the" and "tha" tied on a 3-letter
spatial match.  Frequencies are sourced from `NgramPredictor.unigrams`
via `set_frequencies`, so the personal+base unigram counts feed
directly into fuzzy ranking.

### `get_correction(typed_word, context)`

Returns the top candidate **only if the typed word is not itself in the
dictionary**.  (If the user typed a valid word, we don't "correct" it.)

## Tunable Parameters

There used to be six pre-built "accessibility profiles" (Precise,
Normal, Mild/Moderate/Severe Tremor, Limited Mobility) that swapped
parameter sets at runtime.  They were confusing — most users picked
"Normal" and never looked at the others, and the parameters that
actually mattered (`spatial_uncertainty`, `confidence_threshold`,
`prediction_weight`) are different shades of the same dial.  Now the
recognizer uses one set of generous, Gboard-leaning constants:

| Constant | Value | What it controls |
|----------|-------|------------------|
| `DEFAULT_SPATIAL_UNCERTAINTY` | 1.4 | Radius (in key-widths) the Gaussian covers.  Larger than the old "Normal" 1.0 — picks up diagonal neighbours so a near-miss still surfaces the right word. |
| `DEFAULT_CONFIDENCE_THRESHOLD` | 0.65 | Auto-correct only if the top candidate's probability clears this.  Lower than the old "Normal" 0.8 — more willing to fix obvious typos. |
| `DEFAULT_PREDICTION_WEIGHT` | 0.6 | How heavily `HybridPredictor._source_weights` trusts fuzzy candidates vs. n-gram.  Shared across every merge strategy (rank / RRF / linear / log-linear). |
| `DEFAULT_MIN_PROB` | 0.001 | Beam-search pruning threshold inside `_generate_fuzzy_sequences`.  Lower than the old 0.01 so a single-substitution path can survive across a 5+ character word. |
| `_TRANSPOSITION_PROB` | 0.30 | Per-edit probability for `_edit_distance_candidates` when the typed string can be turned into a dictionary word by swapping two adjacent characters ("teh" → "the"). |
| `_DELETION_PROB` | 0.20 | Same, for the "typed has an extra letter" path — drop each char and look up. |
| `_INSERTION_PROB` | 0.15 | Same, for "typed is missing a letter" — try each `a-z` insertion at each position. |
| `_APOSTROPHE_INSERTION_PROB` | 0.50 | Special case of insertion: when the inserted character is `'`. Bumped well above the generic letter-insertion penalty because missing apostrophes ("im" → "I'm", "dont" → "don't") are by far the dominant insertion error in real typing, especially for users who struggle with the apostrophe key on a low-precision OSK. |

If you need to tune behaviour for a specific user, override these on
the `FuzzyRecognizer` instance — they're class attributes, so a
subclass or instance assignment is enough.  The profile UI in settings
is gone.

## How it Plugs into the Hybrid Engine

`HybridPredictor.predict` pulls fuzzy predictions for the current
partial word via `get_fuzzy_predictions`, which returns
`List[Tuple[str, float]]` with raw spatial scores.  Those candidates
are merged with n-gram and PPM suggestions using
`FuzzyRecognizer.prediction_weight` as the per-source weight; the
formula that combines them depends on the active merge strategy
(Default / Consensus boost / Confidence-weighted / Multiplicative).
See `HYBRID_MERGING.md`.

The bigram bonus on fuzzy candidates (`_bigram_bonus`,
`1 + log1p(count) / 2`) applies in every strategy — fuzzy is the only
predictor that's context-blind by default, so we add the previous
word's bigram support as the primary context signal.  In rank/RRF
the bonus multiplies the positional score; in linear/log-linear it
multiplies the score *before* per-source normalisation, so the
context signal flows through the resulting probability distribution.

`HybridPredictor.check_autocorrect` calls `should_autocorrect`, which
returns a corrected word only if (1) the typed word is at least 3
characters long, (2) the top candidate's probability ≥
`confidence_threshold`, and (3) the candidate's score clears the
relative `autocorrect_margin` over the typed word's hypothetical
"rare real word" baseline. The 3-char gate is a hard cutoff: 1- and
2-char fragments carry too little signal — without it, "v" → "is",
"vs" → "is", "th" → "to" all fired on inputs the user typed
deliberately. Genuine 2-char misspellings ("im" → "I'm") are handled
by the `check_autocorrect` fast-path misspellings table, which sits
*above* `should_autocorrect` and bypasses this guard.

The space-time autocorrect path in `KeyboardBridge` (which calls
`check_autocorrect` and overwrites the typed word via `replace_text`)
is **off by default** (`_autocorrect_enabled = False`) — corrections
surface as clickable suggestion pills, never silent on-space
overwrites. `setAutocorrectEnabled(True)` re-enables the overwrite
path; the fuzzy recogniser itself runs unconditionally as part of
the prediction merge.

## Known Gaps / Future Work

The list matches `CLAUDE.md`'s "Prediction & Autocorrect — Architecture
Notes" section:

1. **Edit-distance generation is O(branches · length)** — a five-letter
   word with six-neighbour keys is fine, but longer words and bigger
   alphabets get expensive fast.  Replacing the beam search with
   **SymSpell** (Garbe 2012 — precomputed deletion variants, O(1)
   hash lookup) would be ~1000× faster on a 20K dictionary.
2. **No direct edit-distance scoring** — we handle spatial *substitution*
   but not insertion, deletion, or transposition.  Real autocorrectors
   (LatinIME, Hunspell) use Damerau–Levenshtein with key-distance
   weights on substitution.  Alpha-OSK effectively caps at
   substitutions-only with Gaussian weighting.
3. **Autocorrect doesn't compete with the literal word** — commercial
   keyboards only auto-replace when the correction scores **1.5–2×
   higher** than what the user actually typed.  We use a flat
   confidence threshold, which over-corrects near the boundary.
4. **No n-gram prior in fuzzy ranking** — context (`the ___` is almost
   certainly "is/was/one/…") doesn't influence which fuzzy candidate
   wins.  Passing `context` through to `FuzzyWordGenerator` and
   re-scoring with `NgramPredictor.bigrams` would help.

## References

- Goodman, J., Venolia, G., Steury, K., & Parker, C. (2002).
  *Language modeling for soft keyboards.*  IUI.  (Key-distance weighted
  edit distance for soft keyboards — the LatinIME ancestor.)
- Kernighan, M. D., Church, K. W., & Gale, W. A. (1990).
  *A spelling correction program based on a noisy channel model.*
  COLING.  (Classical substitution/insertion/deletion/transposition
  model — still the textbook reference for edit-distance scoring.)
- Garbe, W. (2012).  *1000× faster spelling correction algorithm.*
  (SymSpell — precomputed-deletions approach.)
- Damerau, F. J. (1964).  *A technique for computer detection and
  correction of spelling errors.*  CACM.
