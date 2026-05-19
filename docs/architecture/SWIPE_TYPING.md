# Swipe / Glide Typing — Design

Swipe typing lets the user drag the mouse across keys to type a whole word
in one continuous gesture, the same way Gboard / SwiftKey / iOS QuickPath
work on phones.  In Alpha-OSK this is mouse-driven: press, drag through
the letters in order, release.

The feature is **off by default**.  Toggle it under
*Settings → Smart Typing → Suggestions → Swipe Typing*.

## Files

| Path | Role |
|------|------|
| `src/prediction/swipe_recognizer.py` | `SwipeRecognizer` — shape-matching decoder |
| `src/keyboard_bridge.py` | `setSwipeEnabled`, `setSwipeLayout`, `processSwipe` slots |
| `qml/components/SwipeOverlay.qml` | Mouse-event interceptor + path canvas |
| `qml/Main.qml` | `charKeyRegistry`, `pushSwipeLayout()`, settings wiring |

## How a Gesture Flows

```
mouse press / drag / release on keyboard area
  → SwipeOverlay.MouseArea grabs the gesture (preventStealing: true)
    → records (x, y) points in overlay-local coords
    → if total movement > swipeThreshold (60px) → flagged as swipe
    → on release:
       • swipe → keyboard.processSwipe(points)
       • tap   → keyRegistry hit-test → KeyButton.keyPressed()
  → keyboard_bridge.processSwipe(points)
    → HybridPredictor.get_unigram_freqs() → dictionary + frequencies
    → SwipeRecognizer.decode(points, dictionary, freq) → top-N words
    → HybridPredictor.get_capitalized() for each candidate
    → sends top word + space via send_text
    → emits remaining candidates as predictions for re-pick
```

The bridge uses `HybridPredictor.get_unigram_freqs()` /
`get_capitalized()` rather than reaching through to `_ngram` directly.
See `HYBRID_MERGING.md` → "Public API for External Callers".

Tap fall-through is what lets a single key still work normally even when
swipe mode is on — short gestures hit the key under the release point.

## Algorithm — Simplified SHARK² / Shape Writer

(Kristensson & Zhai, UIST 2004 — the algorithm Gboard descends from.)

For each candidate dictionary word:

1. Build the **ideal trace** — a polyline through the centres of the
   keys for each letter.  Consecutive duplicate letters collapse to one
   vertex (you can't swipe to the same key twice meaningfully).
2. **Resample** both the user trace and the ideal trace to N=32 points
   uniformly spaced along arc length.  This makes shape comparison
   length-invariant.
3. **Normalize** — translate both traces to their centroid and scale so
   the largest extent is 1.  Now only *shape* matters, not size or
   absolute position.
4. **Score** = `log(freq + 1) − α · mean_euclidean_distance(user, ideal)`.
   The frequency prior breaks ties between shape-similar words —
   "the" beats "rge".

### Pre-filters (cut 20K → ~few hundred candidates)

- `len(word) >= min_word_len` (default 3) — taps for short words.
- First letter's key must be within `endpoint_tolerance` key-widths
  (default 1.5) of the trace's start point.
- Last letter's key must be within `endpoint_tolerance` of the end point.

### Coordinate system

The recogniser is unit-agnostic — it normalizes everything internally.
QML pushes both the trace points and the key-centre map in
**SwipeOverlay-local pixels**, so they share a frame.

### Performance

Pure Python, no numpy.  ~5–20 ms for a 20K-word dictionary on commodity
hardware after pre-filtering.  Resampling is O(N + path_length); shape
distance is O(N).  Total: O(K · N) where K is the post-filter candidate
count.

## Tunables (in `SwipeRecognizer.__init__`)

| Param | Default | What it controls |
|-------|---------|------------------|
| `sample_count` | 32 | Resample resolution.  Higher = more sensitive to shape, slower. |
| `min_word_len` | 3 | Below this, the user is expected to tap. |
| `endpoint_tolerance` | 1.5 | Key-widths a swipe end may be from the first/last letter's key. |
| `shape_weight` | 8.0 | Shape vs. frequency in scoring.  Higher = ignore frequency more. |

## Capitalization

The recogniser returns lowercase candidates (since the dictionary is
keyed on lowercase forms). The bridge runs each through
`NgramPredictor.get_capitalized` — same path predictions use — but
the current rule only auto-capitalises the `I` family (`I`, `I'm`,
`I'll`, `I'd`, `I've`). Everything else stays lowercase from the
recogniser. This matches the typed-prefix casing model used for
ordinary predictions: capitals come from the user pressing shift /
caps lock, not from a hidden proper-noun list. See `CLAUDE.md` →
"Auto-Capitalization & Proper Nouns" for the rationale and what
to do if you want to revive sentence-start or proper-noun cap.

## Known Limits / Future Work

- **No turning-point bias.**  Real Shape Writer also weights similarity
  at high-curvature points (where the swipe changes direction sharply).
  These typically fall on letter centres, so a turning-point match boost
  could disambiguate words with similar arc paths but different letter
  counts (e.g. "the" vs. "tee").
- **No bigram context.**  The score considers unigram frequency only;
  weighting `bigram(prev_word, candidate)` would catch obvious cases
  where the next word is heavily constrained by the previous one.
- **No swipe path visualization beyond the trail.**  A "fade-out" style
  trail or per-letter highlight as the swipe crosses keys would aid
  discoverability, especially for first-time users.
- **No partial-word swipes.**  All gestures are decoded as full words +
  space.  A modifier (e.g. holding Shift) could let users mid-word
  splice without an auto-space.
- **Visual hit-testing of taps walks the registry linearly.**  Fine for
  the ~50 char keys in the standard layout; if layouts grow large,
  switch to a spatial index (grid bucket).

## References

- Kristensson, P. O., & Zhai, S. (2004).  *SHARK²: A large vocabulary
  shorthand writing system for pen-based computers.*  UIST.
- Zhai, S., & Kristensson, P. O. (2003).  *Shorthand writing on stylus
  keyboard.*  CHI.
- Bi, X., Ouyang, T., & Zhai, S. (2014).  *Both complete and correct?
  Multi-objective optimization of touchscreen keyboard.*  CHI.
