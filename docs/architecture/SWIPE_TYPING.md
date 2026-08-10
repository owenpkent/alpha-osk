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
swipe mode is on. The overlay's MouseArea fills the whole main-keyboard block
and takes every press (`preventStealing: true`), so it has to resolve the key
itself and drive it.

### Two registries

`Main.qml`'s `registerCharKey` populates **two** lists from the same call:

| List | Contents | Consumer |
|------|----------|----------|
| `charKeyRegistry` | single-character `char` keys only | `pushSwipeLayout()`, i.e. the recogniser's key-centre map |
| `tappableKeyRegistry` | every key under the overlay | the overlay's hit testing |

They are separate because the two consumers want different things. A
`backspace` entry in the key-centre map is a phantom letter in every shape
match, so that filter must stay exactly as strict as it is. But hit testing
needs the opposite: it must see every key it covers.

**This used to be one list, and it was a real bug** (issue #15). The tap
fall-through resolved through the char-only list, so Backspace, Delete, Tab,
Enter, the arrows, the modifiers, the `?123` layer key and the Number Row's
Esc all hit-tested against a list that structurally could not contain them,
and were silently swallowed. Enabling swipe typing took away the one key an
imprecise typist needs most, with nothing on screen to say why. **Do not
"fix" a future variant of this by admitting specials into `charKeyRegistry`;
split the consumers instead.**

Hit testing also skips items whose `visible` is false. A `KeyButton` inside a
hidden panel is still constructed and still registers, so the Number Row's
keys sit in the registry with stale geometry whenever that panel is off.

### Specials press, characters tap

The two key classes activate at different moments, on purpose:

- **Character keys activate on release.** Until the gesture ends it is
  genuinely ambiguous whether it is a tap or the start of a swipe, so nothing
  may be typed on press.
- **Special keys activate on press, and stay held.** A gesture starting on a
  non-character key can never become a legitimate swipe, since the recogniser
  pre-filters candidates by start key and only characters are ever swipe
  starts. Activating on press is what preserves **auto-repeat**: holding
  Backspace to delete a word is most of what Backspace is for on a
  mouse-driven OSK, and a release-time tap cannot express it. Dragging off a
  held key aborts it, matching what `KeyButton` does on its own.

The overlay drives the key through `KeyButton.externalPress()` /
`externalRelease()`, which run the same debounce, press visual, ripple,
activation and repeat-timer code the button's own MouseArea runs. A key must
not behave differently depending on whether swipe typing happens to be on.

Covered by `tests/test_qml_swipe_overlay.py`, which taps real keys through
the real overlay and asserts the keystroke reached the synthesizer. Note the
two traps recorded there: `findChildren` cannot see a Repeater's delegates,
and a test that only checks "no exception was raised" passes against a dead
tap, because a dead tap is silent.

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
- **Only one gesture at a time.**  The overlay tracks a single held key and
  a single point list, so it has no notion of a second simultaneous touch.
  Fine for a mouse; would need rethinking for a touchscreen.

## References

- Kristensson, P. O., & Zhai, S. (2004).  *SHARK²: A large vocabulary
  shorthand writing system for pen-based computers.*  UIST.
- Zhai, S., & Kristensson, P. O. (2003).  *Shorthand writing on stylus
  keyboard.*  CHI.
- Bi, X., Ouyang, T., & Zhai, S. (2014).  *Both complete and correct?
  Multi-objective optimization of touchscreen keyboard.*  CHI.
