"""
Swipe / Glide Typing Recognizer.

Decodes a continuous mouse trace across the keyboard into a word, the
same way Gboard / SwiftKey decode finger swipes on phones.

Algorithm (simplified SHARK² / Shape Writer — Kristensson & Zhai, 2004):

1. The user's trace is resampled to N points uniformly spaced along
   arc length.
2. For each candidate word, an "ideal trace" is constructed by joining
   the centres of the keys in the word's letter sequence, then
   resampled to the same N points.
3. Both traces are translated and scaled into a common unit box so
   shape is compared independently of size and position.
4. Score = ``log(freq + 1) − α · mean_euclidean_distance``.  The
   word frequency prior breaks ties between shape-similar words
   (e.g. "the" beats "rge").
5. Pre-filters cut the candidate set from ~20K words to a few hundred:
   - Only words of length ``min_word_len`` or more.
   - First letter's key must be near the trace's start point.
   - Last letter's key must be near the trace's end point.

The recognizer is **layout-aware** — call :meth:`set_layout` with a
``{key_char: (x, y)}`` map of key-centre coordinates (any unit) before
decoding.  All scaling/normalization is internal.

References
----------
- Kristensson, P. O., & Zhai, S. (2004). SHARK²: A large vocabulary
  shorthand writing system for pen-based computers. UIST '04.
- Zhai, S., & Kristensson, P. O. (2003). Shorthand writing on stylus
  keyboard. CHI '03.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

_logger = logging.getLogger("SwipeRecognizer")

Point = Tuple[float, float]


class SwipeRecognizer:
    """
    Shape-matching swipe-typing decoder.

    Lightweight pure-Python — no numpy dependency.  Decodes a 32-point
    resampled trace against the dictionary in ~5–20 ms for a 20K word
    vocabulary on commodity hardware.
    """

    def __init__(
        self,
        sample_count: int = 32,
        min_word_len: int = 3,
        endpoint_tolerance: float = 1.5,
        shape_weight: float = 8.0,
    ) -> None:
        """
        Args:
            sample_count: Resample resolution for both user and ideal traces.
            min_word_len: Don't decode swipes shorter than this; users tap
                          short words instead of swiping.
            endpoint_tolerance: How many key-widths the start/end of a swipe
                                may be from the first/last letter's key
                                centre.  Looser = more candidates, slower.
            shape_weight: Weight on shape distance vs. word frequency in
                          the final score.  Higher = shape matters more.
        """
        self.sample_count = sample_count
        self.min_word_len = min_word_len
        self.endpoint_tolerance = endpoint_tolerance
        self.shape_weight = shape_weight

        # Layout: {char_lowercase: (x, y)} — key centre coordinates.
        # Units are arbitrary; only ratios matter.
        self._layout: Dict[str, Point] = {}
        self._key_size: float = 1.0  # average distance between adjacent keys

    # ------------------------------------------------------------------ #
    #  Public API
    # ------------------------------------------------------------------ #

    def set_layout(self, key_centers: Dict[str, Point]) -> None:
        """
        Provide the keyboard layout.

        Args:
            key_centers: ``{letter: (x, y)}`` map.  Letters should be
                         lowercase a-z; non-letter keys are ignored.
        """
        self._layout = {
            k.lower(): (float(x), float(y))
            for k, (x, y) in key_centers.items()
            if len(k) == 1 and k.isalpha()
        }
        self._key_size = self._estimate_key_size()
        _logger.info(
            "Layout set: %d keys, key_size≈%.3f",
            len(self._layout),
            self._key_size,
        )

    def decode(
        self,
        trace: Sequence[Point],
        candidates: Iterable[str],
        word_freq: Optional[Dict[str, int]] = None,
        top_k: int = 8,
    ) -> List[str]:
        """
        Decode a user trace into ranked word candidates.

        Args:
            trace: List of (x, y) points sampled along the user's swipe.
                   Coordinates must be in the same units as ``set_layout``.
            candidates: Iterable of dictionary words to consider.
            word_freq: Optional ``{word: frequency}`` for scoring.  If
                       omitted, all words score equally on frequency.
            top_k: Maximum number of results to return.

        Returns:
            Top-``k`` candidate words sorted best-first.  Empty list if
            the layout isn't set or the trace is too short.
        """
        if not self._layout or len(trace) < 4:
            return []

        word_freq = word_freq or {}
        user = self._resample(list(trace), self.sample_count)
        if user is None:
            return []
        user_norm = self._normalize(user)

        start = trace[0]
        end = trace[-1]
        max_endpoint_dist = self.endpoint_tolerance * self._key_size

        scored: List[Tuple[float, str]] = []
        for word in candidates:
            w = word.lower()
            if len(w) < self.min_word_len:
                continue
            first = self._layout.get(w[0])
            last = self._layout.get(w[-1])
            if first is None or last is None:
                continue
            # Endpoint pre-filter — cheap rejection of obviously wrong words.
            if _dist(first, start) > max_endpoint_dist:
                continue
            if _dist(last, end) > max_endpoint_dist:
                continue

            ideal = self._ideal_trace(w)
            if ideal is None:
                continue
            ideal_resampled = self._resample(ideal, self.sample_count)
            if ideal_resampled is None:
                continue
            ideal_norm = self._normalize(ideal_resampled)

            distance = _mean_distance(user_norm, ideal_norm)
            freq = word_freq.get(w, 0)
            # Higher score = better.
            score = math.log1p(freq) - self.shape_weight * distance
            scored.append((score, word))

        scored.sort(key=lambda x: -x[0])
        return [w for _, w in scored[:top_k]]

    # ------------------------------------------------------------------ #
    #  Internals
    # ------------------------------------------------------------------ #

    def _ideal_trace(self, word: str) -> Optional[List[Point]]:
        """Polyline through the key centres of each letter in ``word``.

        Consecutive duplicate letters collapse to a single vertex (the
        user can't swipe to the same key twice in a meaningful way).
        """
        pts: List[Point] = []
        for ch in word:
            c = self._layout.get(ch)
            if c is None:
                return None
            if not pts or pts[-1] != c:
                pts.append(c)
        if len(pts) < 2:
            # Single-key "swipes" are ambiguous — let tap handling decode them.
            return None
        return pts

    def _estimate_key_size(self) -> float:
        """Mean nearest-neighbour distance between key centres."""
        keys = list(self._layout.values())
        if len(keys) < 2:
            return 1.0
        total = 0.0
        for i, k in enumerate(keys):
            best = math.inf
            for j, other in enumerate(keys):
                if i == j:
                    continue
                d = _dist(k, other)
                if d < best:
                    best = d
            total += best
        return total / len(keys)

    @staticmethod
    def _resample(points: List[Point], n: int) -> Optional[List[Point]]:
        """Resample a polyline to exactly ``n`` points uniformly spaced
        along arc length.  Returns None if the path has zero length."""
        if len(points) < 2 or n < 2:
            return None
        # Cumulative arc length
        cum = [0.0]
        for i in range(1, len(points)):
            cum.append(cum[-1] + _dist(points[i - 1], points[i]))
        total = cum[-1]
        if total <= 0:
            return None
        step = total / (n - 1)
        out: List[Point] = [points[0]]
        target = step
        j = 1
        for _ in range(1, n - 1):
            while j < len(points) and cum[j] < target:
                j += 1
            if j >= len(points):
                out.append(points[-1])
                target += step
                continue
            # Interpolate between points[j-1] and points[j]
            seg_len = cum[j] - cum[j - 1]
            t = (target - cum[j - 1]) / seg_len if seg_len > 0 else 0.0
            x = points[j - 1][0] + t * (points[j][0] - points[j - 1][0])
            y = points[j - 1][1] + t * (points[j][1] - points[j - 1][1])
            out.append((x, y))
            target += step
        out.append(points[-1])
        return out

    @staticmethod
    def _normalize(points: List[Point]) -> List[Point]:
        """Translate to centroid and scale so the largest extent is 1.

        This makes the comparison invariant to where on the keyboard the
        user started swiping and to their swipe size, leaving only
        *shape* to drive the distance metric.
        """
        if not points:
            return points
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        translated = [(p[0] - cx, p[1] - cy) for p in points]
        max_extent = max(
            (max(abs(p[0]) for p in translated), max(abs(p[1]) for p in translated)),
        )
        if max_extent <= 0:
            return translated
        return [(p[0] / max_extent, p[1] / max_extent) for p in translated]


def _dist(a: Point, b: Point) -> float:
    dx = a[0] - b[0]
    dy = a[1] - b[1]
    return math.sqrt(dx * dx + dy * dy)


def _mean_distance(a: List[Point], b: List[Point]) -> float:
    """Mean Euclidean distance between two equal-length point sequences."""
    n = min(len(a), len(b))
    if n == 0:
        return math.inf
    total = 0.0
    for i in range(n):
        total += _dist(a[i], b[i])
    return total / n
