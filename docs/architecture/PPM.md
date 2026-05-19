# PPM — Character-Level Prediction

PPM (**Prediction by Partial Matching**) is Alpha-OSK's character-level
language model.  It complements the word-level n-gram predictor by
handling the case where the user is *mid-word* with a partial prefix
the dictionary hasn't seen yet — PPM will gracefully back off to
shorter and shorter contexts until it finds one that has.

PPM is the same algorithm [Dasher](https://www.inference.org.uk/dasher/)
has used for 25 years for assistive text entry.  Cleary & Witten
published it in 1984; the PPMD escape variant implemented here is from
Howard (1993).

Implementation: `src/prediction/ppm_predictor.py`.

## Two Classes

| Class | Responsibility |
|-------|----------------|
| `PPMPredictor` | Character trie + probability blending + beam search over characters. |
| `PPMWordPredictor` | Dictionary-constrained wrapper.  Re-ranks dictionary words by the PPM character model. |

## Data Structure — The PPM Trie

Every prefix of length ≤ `max_order` (default 8) that has been
observed in training has a `PPMNode` in the trie.  Each node stores:

```
class PPMNode:
    count:    int                   # how often this string was seen
    children: Dict[char, PPMNode]   # each next-char observation
```

`add_child(c)` creates the node if needed and returns it.  Updates walk
down the trie, bumping counts.

### `_update(context, char)` — training

For every suffix of the current context (from empty up to
`max_order`), navigate to that suffix's node and add/increment a
child for `char`.  So one character observation updates up to
`max_order + 1` nodes — one for each context length.

This is what makes PPM a *variable-order* Markov model: it
simultaneously maintains unigram, bigram, trigram, …, 9-gram statistics
over characters.

## Probability Estimation — The Escape Mechanism

`get_probabilities(context)` returns a distribution over the next
character, blended across all context lengths.  This is the heart of
PPM.

### The problem

A pure 8-gram character model would give zero probability to any
9-character string never seen before — useless for an adaptive
system that learns as it goes.

### PPMD's solution

1. **Start at the longest matching context.**  Look up what characters
   have followed this 8-gram.
2. **Emit each character with probability** `count / (total + unique)`.
   The `+ unique` in the denominator is the PPMD escape reservation.
3. **Reserve some probability mass for "something I haven't seen at
   this order"** — the **escape probability**
   `unique / (total + unique)`.
4. **Shorten the context by one character and repeat**, but skip any
   character already assigned probability at a higher order (tracked
   via the `excluded` set).  Weight this level's contributions by the
   product of all escape probabilities from higher orders.
5. Stop at the zero-order (unconditional) model.
6. Blend in a tiny uniform distribution over the alphabet as a final
   smoothing step so literally-unseen characters still have
   non-zero probability.

```
# Pseudocode — see _blend_probabilities()
excluded       = {}
escape_weight  = 1.0
for order in [len(context), …, 1, 0]:
    node = navigate to context[-order:]
    if not found: continue
    total   = sum of child counts
    unique  = number of children
    escape  = unique / (total + unique)
    for char, child in node.children:
        if char in excluded: continue   # already counted at higher order
        prob[char] += escape_weight · (child.count / (total + unique))
        excluded.add(char)
    escape_weight *= escape
```

Long contexts dominate when they exist; as they fail, shorter contexts
pick up the escape mass.  The excluded-set trick (Moffat 1990)
prevents double-counting a character that already got probability at a
higher order.

### Final smoothing

`get_probabilities` further mixes in a uniform distribution over
`self.alphabet` with weights `0.1 · uniform + 0.9 · ppm`.  This means
every alphabet character has at least ~0.1 / |alphabet| probability —
useful for robustness to out-of-distribution text.

## Word Completion — `predict_word` / `_beam_search_words`

Beam search builds words character-by-character:

```
beam = [(partial, 1.0, context + partial)]
for step in range(max_length - len(partial)):
    for word, prob, ctx in beam:
        for char, p_char in get_probabilities(ctx):
            if char == " ":
                completed.append((word, prob · p_char))
            else:
                new_beam.append((word + char, prob · p_char, ctx + char))
    beam = top beam_width of new_beam
```

Width defaults to `n · 3` — enough to keep alternative branches alive
while staying fast.  Space is the terminator; deduplication happens at
the end.

## Dictionary Constraint — `PPMWordPredictor`

Raw PPM beam search can produce non-words.  `PPMWordPredictor` fixes
this:

1. Find every dictionary word that starts with the user's `partial`.
2. Score each word by computing the PPM probability of the remaining
   suffix, character by character, feeding each char back into the
   context.
3. Sort descending by PPM probability.
4. If fewer than `n` candidates survive, top up with free-form beam
   search (unconstrained), still deduped.

There's a 1000-entry LRU-style cache keyed on
`(last-20-chars-of-context)|partial` to avoid re-scoring after every
keystroke.

## Training & Persistence

- `train(text)` runs the update loop over every character.
- `learn_text` / `learn_word` are thin wrappers used by
  `HybridPredictor.learn`.
- `save(path)` serialises the whole trie to JSON (recursive
  `node_to_dict`).  `load(path)` rebuilds it.
- `get_context_entropy(context)` returns Shannon entropy of the next-
  character distribution — useful for debugging "how confident is the
  model here?"

## Parameters

| Param | Default | What it controls |
|-------|---------|------------------|
| `max_order` | 8 | Longest context in the trie.  Higher = more memory, better long-range prediction, but slower updates.  Dasher uses order 5–7. |
| `alphabet` | lowercase + `' .,!?-` | Characters the model cares about.  Anything else is normalised to space. |
| `escape_method` | `"ppmd"` | PPMD escape is the industry default; PPMC is an alternative. |
| Beam width | `n · 3` | Word search breadth.  Larger = catches more alternatives, slower. |

## Role in the Hybrid Engine

`HybridPredictor._source_weights` weights PPM suggestions **lower**
than n-gram for next-word prediction (0.3 vs 3.0) and **near equal**
for mid-word completion (0.8 vs 1.0).  These weights are shared
across every merge strategy (Default / Consensus boost /
Confidence-weighted / Multiplicative) — the formula varies by
strategy, the relative trust between predictors does not.  The
rationale: n-gram *is* the word-level authority; PPM shines when the
word is partial or absent from the dictionary.  See
`HYBRID_MERGING.md` for the full scoring rules and strategy
trade-offs.

PPM emits scores via `predict_with_scores()` — raw chained-character
probabilities for dictionary completions, beam-search probabilities
for novel completions.  These live on different scales and are
normalised per source by the linear / log-linear strategies before
combining; the rank and RRF strategies ignore the scores and use
positional rank only.

## Known Limits / Future Work

- **Memory grows with training.**  A full trie over the training
  corpus plus user typing sits in RAM.  Capping the trie size (LRU
  eviction, Moffat's "update exclusions") would bound growth.
- **Fixed `max_order`.**  Adaptive order (varikn-style) could be more
  efficient for short inputs.
- **No forgetting.**  Counts never decay, unlike the n-gram
  predictor's periodic recency decay.  Typing habits from a year ago
  still weigh equally with yesterday's.

## References

- Cleary, J. G., & Witten, I. H. (1984).  *Data compression using
  adaptive coding and partial string matching.*  IEEE Transactions on
  Communications.  (The original PPM paper.)
- Howard, P. G. (1993).  *The design and analysis of efficient lossless
  data compression systems.*  PhD thesis.  (PPMD escape.)
- Moffat, A. (1990).  *Implementing the PPM data compression scheme.*
  IEEE Transactions on Communications.  (Exclusions trick.)
- Ward, D. J., Blackwell, A. F., & MacKay, D. J. C. (2000).  *Dasher
  — a data entry interface using continuous gestures and language
  models.*  UIST.  (PPM for assistive text input.)
