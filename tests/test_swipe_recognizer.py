"""Tests for the swipe/glide typing recogniser."""

from __future__ import annotations

from src.prediction.swipe_recognizer import SwipeRecognizer

# A stylised QWERTY-ish letter grid.  Units are arbitrary — the
# recogniser normalises internally.
_QWERTY = {
    "q": (0, 0),
    "w": (1, 0),
    "e": (2, 0),
    "r": (3, 0),
    "t": (4, 0),
    "y": (5, 0),
    "u": (6, 0),
    "i": (7, 0),
    "o": (8, 0),
    "p": (9, 0),
    "a": (0.3, 1),
    "s": (1.3, 1),
    "d": (2.3, 1),
    "f": (3.3, 1),
    "g": (4.3, 1),
    "h": (5.3, 1),
    "j": (6.3, 1),
    "k": (7.3, 1),
    "l": (8.3, 1),
    "z": (1.0, 2),
    "x": (2.0, 2),
    "c": (3.0, 2),
    "v": (4.0, 2),
    "b": (5.0, 2),
    "n": (6.0, 2),
    "m": (7.0, 2),
}


def _swipe_through(letters: str, points_per_segment: int = 6):
    """Build a synthetic trace that drags a straight line through each
    key centre in turn, with some interpolated points per segment."""
    anchors = [_QWERTY[c] for c in letters]
    trace = [anchors[0]]
    for i in range(1, len(anchors)):
        ax, ay = anchors[i - 1]
        bx, by = anchors[i]
        for step in range(1, points_per_segment + 1):
            t = step / points_per_segment
            trace.append((ax + t * (bx - ax), ay + t * (by - ay)))
    return trace


class TestSwipeRecognizer:
    def test_decodes_exact_trace_to_matching_word(self):
        """A trace straight through 'h-e-l-l-o' centres should rank 'hello' high."""
        recog = SwipeRecognizer()
        recog.set_layout(_QWERTY)

        candidates = ["hello", "heron", "world", "help", "the", "hero", "halo"]
        freq = {w: 10 for w in candidates}
        trace = _swipe_through("hello")

        results = recog.decode(trace, candidates, word_freq=freq, top_k=5)
        assert results, "expected at least one candidate"
        assert results[0] == "hello"

    def test_endpoint_filter_rejects_wrong_end_key(self):
        """Words whose last letter is far from the trace end get filtered."""
        recog = SwipeRecognizer()
        recog.set_layout(_QWERTY)

        trace = _swipe_through("the")  # ends on 'e'
        # 'top' ends on 'p' which is far from 'e' — should not pass the filter.
        candidates = ["the", "top"]
        results = recog.decode(trace, candidates, word_freq={"the": 5, "top": 5})
        assert "top" not in results
        assert "the" in results

    def test_ignores_words_shorter_than_min(self):
        recog = SwipeRecognizer(min_word_len=4)
        recog.set_layout(_QWERTY)
        trace = _swipe_through("the")
        results = recog.decode(trace, ["the"], word_freq={"the": 10})
        assert results == []

    def test_empty_layout_returns_nothing(self):
        recog = SwipeRecognizer()
        # no set_layout
        results = recog.decode(_swipe_through("the"), ["the"])
        assert results == []

    def test_degenerate_trace_returns_nothing(self):
        recog = SwipeRecognizer()
        recog.set_layout(_QWERTY)
        # Too few points to be a swipe
        results = recog.decode([(0, 0), (0.1, 0.1)], ["hello"])
        assert results == []
