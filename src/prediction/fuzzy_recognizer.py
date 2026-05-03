"""
Fuzzy / spatial recognition for typo-tolerant prediction.

Models spatial uncertainty around each key so a press near a key
boundary still surfaces the correct word, the way Gboard does.  No
configurable profiles — there's one default that's tuned to be
generous, because picking among six profiles was confusing and the
extra dials never carried their weight.
"""

from __future__ import annotations

import logging
import math
from typing import Dict, List, Mapping, Optional, Tuple

from .symspell import SymSpell

_logger = logging.getLogger("FuzzyRecognizer")


# QWERTY layout in (row, col) units.  Rows are offset by the standard
# staggered amount so diagonal distances reflect the physical keyboard.
QWERTY_POSITIONS: Dict[str, Tuple[float, float]] = {
    'q': (0, 0),    'w': (0, 1),    'e': (0, 2),    'r': (0, 3),    't': (0, 4),
    'y': (0, 5),    'u': (0, 6),    'i': (0, 7),    'o': (0, 8),    'p': (0, 9),
    'a': (1, 0.25), 's': (1, 1.25), 'd': (1, 2.25), 'f': (1, 3.25), 'g': (1, 4.25),
    'h': (1, 5.25), 'j': (1, 6.25), 'k': (1, 7.25), 'l': (1, 8.25),
    'z': (2, 0.75), 'x': (2, 1.75), 'c': (2, 2.75), 'v': (2, 3.75), 'b': (2, 4.75),
    'n': (2, 5.75), 'm': (2, 6.75),
}


# Tuned so a press one key off-center still has its true neighbours
# (cardinal + diagonal) in the candidate set.  Larger than the original
# "Normal" profile (1.0) but smaller than the "Mild Tremor" profile
# (1.5) — picks up the diagonals without dragging in second-row noise.
DEFAULT_SPATIAL_UNCERTAINTY = 1.4

# Minimum confidence for ``should_autocorrect`` to fire.
DEFAULT_CONFIDENCE_THRESHOLD = 0.65

# Weight the merger applies to fuzzy candidates against n-gram scores.
DEFAULT_PREDICTION_WEIGHT = 0.6

# Pruning threshold inside the candidate-sequence search.  Lower than
# the original 0.01 so a single-substitution path can survive across a
# 5+ character word — at 0.01 a substituted letter (~0.4 prob) dies
# after about 4 multiplications.
DEFAULT_MIN_PROB = 0.001

# Relative threshold for autocorrect: a correction's score must be at
# least this many times the typed word's hypothetical "rare-but-real"
# baseline before the correction fires.  Mirrors the LatinIME / Gboard
# 1.5x – 2x heuristic — keeps autocorrect from stomping on plausibly
# deliberate typings ("thru", "lol", short slang) while still letting
# obvious typos through.  Implausibly shaped inputs ("thx", "btw") get
# baseline 0, so the absolute confidence threshold alone gates them.
DEFAULT_AUTOCORRECT_MARGIN = 1.5


class SpatialKeyModel:
    """Probability distribution over intended keys for a given press."""

    def __init__(
        self,
        positions: Optional[Dict[str, Tuple[float, float]]] = None,
        uncertainty_radius: float = DEFAULT_SPATIAL_UNCERTAINTY,
    ):
        self.positions = positions or QWERTY_POSITIONS
        self.uncertainty_radius = uncertainty_radius

        self._neighbors: Dict[str, List[Tuple[str, float]]] = {}
        self._build_neighbor_cache()

    def _build_neighbor_cache(self) -> None:
        for key, pos in self.positions.items():
            neighbors = []
            for other_key, other_pos in self.positions.items():
                dist = self._distance(pos, other_pos)
                # Cache slightly more than the radius so we can change
                # the radius later without re-walking every key pair.
                if dist <= self.uncertainty_radius * 1.5:
                    neighbors.append((other_key, dist))
            neighbors.sort(key=lambda x: x[1])
            self._neighbors[key] = neighbors

    @staticmethod
    def _distance(pos1: Tuple[float, float], pos2: Tuple[float, float]) -> float:
        return math.sqrt((pos1[0] - pos2[0]) ** 2 + (pos1[1] - pos2[1]) ** 2)

    def get_key_probabilities(self, clicked_key: str) -> Dict[str, float]:
        """Probability distribution over keys the user might have meant."""
        clicked_key = clicked_key.lower()
        if clicked_key not in self.positions:
            return {clicked_key: 1.0}

        probabilities: Dict[str, float] = {}
        sigma = self.uncertainty_radius / 2  # ≈ 95 % within radius
        for key, distance in self._neighbors.get(clicked_key, [(clicked_key, 0.0)]):
            if distance <= self.uncertainty_radius:
                probabilities[key] = math.exp(-distance ** 2 / (2 * sigma ** 2))

        total = sum(probabilities.values())
        if total > 0:
            probabilities = {k: v / total for k, v in probabilities.items()}
        return probabilities

    def get_nearby_keys(self, key: str) -> List[str]:
        key = key.lower()
        if key not in self._neighbors:
            return [key]
        return [k for k, d in self._neighbors[key] if d <= self.uncertainty_radius]

    def set_uncertainty_radius(self, radius: float) -> None:
        self.uncertainty_radius = radius
        self._build_neighbor_cache()


class FuzzyWordGenerator:
    """Expands a typed sequence into spatial-neighbour interpretations.

    The dictionary is a mapping ``{word: frequency}`` so candidate
    ranking can multiply spatial probability by ``log(freq + 1)``.
    Without that, "the" and "tha" tied on a 3-letter spatial match;
    with it, common words win the way Gboard ranks them.
    """

    def __init__(
        self,
        spatial_model: Optional[SpatialKeyModel] = None,
        dictionary: Optional[Dict[str, float]] = None,
        max_candidates: int = 50,
        max_edit_distance: int = 2,
    ):
        self.spatial_model = spatial_model or SpatialKeyModel()
        self.dictionary: Dict[str, float] = dict(dictionary) if dictionary else {}
        self.max_candidates = max_candidates
        self.max_edit_distance = max_edit_distance
        self._symspell = SymSpell(max_edit_distance=max_edit_distance)
        if self.dictionary:
            for word, freq in self.dictionary.items():
                self._symspell.add_word(word, int(max(1, freq)))

    def generate_candidates(
        self,
        typed_sequence: str,
        min_prob: float = DEFAULT_MIN_PROB,
    ) -> List[Tuple[str, float]]:
        typed_sequence = typed_sequence.lower()
        if not typed_sequence:
            return []

        # Two complementary candidate sources:
        # 1. Spatial-substitution beam search — covers near-key mistypes
        #    ("g" instead of "h") through Gaussian neighbour probability.
        # 2. Single-edit transformations — covers transpositions ("teh"
        #    → "the"), deletions (typed has an extra char), and
        #    insertions (typed missing a char), which the spatial path
        #    can't express.
        scored: Dict[str, float] = {}

        for seq, prob in self._generate_fuzzy_sequences(typed_sequence, min_prob):
            freq = self.dictionary.get(seq)
            if freq is None:
                continue
            # Multiply spatial probability by log-frequency so a
            # high-frequency word with a slightly worse spatial match
            # still beats a rare word with a perfect spatial match.
            score = prob * math.log1p(freq)
            if score > scored.get(seq, 0.0):
                scored[seq] = score

        for word, edit_prob in self._edit_distance_candidates(typed_sequence):
            score = edit_prob * math.log1p(self.dictionary.get(word, 0.0))
            if score > scored.get(word, 0.0):
                scored[word] = score

        return sorted(scored.items(), key=lambda x: -x[1])[:self.max_candidates]

    # Per-edit penalties.  Tuned so a single-edit dictionary hit on a
    # short word lands in the same score range as a perfect spatial
    # match — high enough to surface as a real correction, low enough
    # that they don't drown out a clean exact match when the user
    # actually typed a word correctly.
    _TRANSPOSITION_PROB = 0.30
    _DELETION_PROB = 0.20
    _INSERTION_PROB = 0.15
    # Apostrophe insertion gets a much higher probability than the
    # generic letter-insertion penalty — missing apostrophes (typing
    # "im" for "I'm", "dont" for "don't", "youre" for "you're") is the
    # dominant insertion pattern in real use, especially for users who
    # struggle with the apostrophe key on a low-precision OSK.  At 0.5
    # an apostrophe-insertion candidate competes with a perfect spatial
    # match instead of getting buried at rank 9.
    _APOSTROPHE_INSERTION_PROB = 0.50
    # Substitution penalty.  Substitutions ("rxample" → "example") were
    # not enumerated by the prior edit-distance-1 path at all — that
    # path only covered transposition / deletion / insertion.  The
    # spatial beam search catches *near-key* substitutions via Gaussian
    # neighbour probability, but non-adjacent substitutions slipped
    # through entirely.  SymSpell now surfaces them; this constant
    # scores them slightly below insertion since the spatial path
    # already covers the near-key common case (avoids double-counting).
    _SUBSTITUTION_PROB = 0.18
    # Distance-2 candidates (any combination of two edits).  Much lower
    # than single-edit penalties because two-edit corrections are far
    # noisier than one-edit ones.  Still positive enough that real
    # double-typo hits ("becuase" → "because" is distance-1, but
    # "becouase" → "because" is distance-2) can surface as suggestions.
    _DOUBLE_EDIT_PROB = 0.05

    def _edit_distance_candidates(self, typed: str) -> List[Tuple[str, float]]:
        """Return dictionary hits within ``max_edit_distance`` of ``typed``.

        Backed by SymSpell (Garbe, 2012) — precomputed-deletion index
        with Damerau-Levenshtein post-filter.  Replaces the previous
        per-letter brute-force candidate generator, which was capped
        at edit distance 1 and did not cover substitutions.

        Each candidate is scored with the edit-type-specific penalty
        (transposition / deletion / insertion / apostrophe-insertion /
        substitution) when the edit distance is 1, or a flat
        ``_DOUBLE_EDIT_PROB`` for distance-2 matches.  The penalty is
        normalised by ``1/n`` (input length) so longer words are not
        unfairly penalised.
        """
        n = len(typed)
        if n < 2:
            return []

        scored: Dict[str, float] = {}
        for word, _freq, dist in self._symspell.lookup(typed):
            if dist == 0:
                # The user typed an exact dictionary word; skip — the
                # caller (fuzzy candidate path) is for *corrections*,
                # and an exact match is handled upstream.
                continue
            if dist == 1:
                prob = self._classify_edit_prob(typed, word)
            else:
                prob = self._DOUBLE_EDIT_PROB
            score = prob / n
            if score > scored.get(word, 0.0):
                scored[word] = score

        return list(scored.items())

    def _classify_edit_prob(self, typed: str, candidate: str) -> float:
        """Return the per-edit-type probability for a distance-1 pair.

        The precomputed-deletion index gives us the candidate set; this
        function inspects the actual transformation to pick the right
        penalty (transposition vs. substitution vs. deletion vs.
        apostrophe-insertion vs. plain insertion).
        """
        lt, lc = len(typed), len(candidate)
        if lt == lc:
            # Same length — either substitution or transposition.
            diff_positions = [
                i for i in range(lt) if typed[i] != candidate[i]
            ]
            if (
                len(diff_positions) == 2
                and diff_positions[1] == diff_positions[0] + 1
                and typed[diff_positions[0]] == candidate[diff_positions[1]]
                and typed[diff_positions[1]] == candidate[diff_positions[0]]
            ):
                return self._TRANSPOSITION_PROB
            return self._SUBSTITUTION_PROB
        if lc < lt:
            # Candidate is shorter — user typed an extra char (deletion
            # gets us from typed to candidate).
            return self._DELETION_PROB
        # Candidate is longer — user missed a char (insertion gets us
        # from typed to candidate).  Apostrophe insertion is the
        # dominant real-world case ("im" → "i'm", "dont" → "don't") and
        # gets a much higher penalty than generic insertion.
        if "'" in candidate and "'" not in typed:
            return self._APOSTROPHE_INSERTION_PROB
        return self._INSERTION_PROB

    def _generate_fuzzy_sequences(
        self,
        typed: str,
        min_prob: float,
    ) -> List[Tuple[str, float]]:
        current: List[Tuple[str, float]] = [("", 1.0)]
        for char in typed:
            char_probs = self.spatial_model.get_key_probabilities(char)
            new_sequences: List[Tuple[str, float]] = []
            for prefix, prefix_prob in current:
                for possible_char, char_prob in char_probs.items():
                    combined_prob = prefix_prob * char_prob
                    if combined_prob >= min_prob:
                        new_sequences.append((prefix + possible_char, combined_prob))
            new_sequences.sort(key=lambda x: -x[1])
            current = new_sequences[:self.max_candidates * 2]
        return current

    def get_correction(
        self,
        typed_word: str,
        context: str = "",
    ) -> Optional[Tuple[str, float]]:
        if typed_word.lower() in self.dictionary:
            return None
        candidates = self.generate_candidates(typed_word)
        return candidates[0] if candidates else None

    def load_dictionary(self, path) -> bool:
        """Load a flat dictionary file (one word per line, optional count).

        The fallback frequency for entries without a count is 1.0 — the
        n-gram unigram counts get folded in separately by the hybrid
        predictor (see ``set_frequencies``).
        """
        from pathlib import Path

        path = Path(path)
        if not path.exists():
            return False
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip().lower()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    word = parts[0]
                    freq = float(parts[1]) if len(parts) > 1 else 1.0
                    # Don't overwrite a higher freq we already loaded.
                    if freq > self.dictionary.get(word, 0.0):
                        self.dictionary[word] = freq
                    self._symspell.add_word(word, int(max(1, freq)))
            _logger.info("Fuzzy dictionary loaded: %d words", len(self.dictionary))
            return True
        except OSError as e:
            _logger.error("Failed to load dictionary: %s", e)
            return False

    def set_frequencies(self, freqs: Mapping[str, float]) -> None:
        """Merge the given word→frequency map into the dictionary.

        Used to inject n-gram unigram counts so candidate ranking
        prefers common words over rare ones.  Words already in the
        dictionary keep the larger of the two frequencies; new words
        are added.

        Accepts ``Mapping`` (covariant value type) so a
        ``dict[str, int]`` from the n-gram model can be passed
        directly without a coercion at every call site.
        """
        for word, freq in freqs.items():
            word = word.lower()
            if freq > self.dictionary.get(word, 0.0):
                self.dictionary[word] = float(freq)
            self._symspell.add_word(word, int(max(1, freq)))
        # Eager-build so the cost lands in startup latency, not on the
        # user's first keystroke.  Lazy-build is fine for incremental
        # additions later (vocab pack toggles, learned words).
        self._symspell.prepare()


class FuzzyRecognizer:
    """Top-level interface used by the hybrid predictor."""

    spatial_uncertainty: float = DEFAULT_SPATIAL_UNCERTAINTY
    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    prediction_weight: float = DEFAULT_PREDICTION_WEIGHT
    autocorrect_margin: float = DEFAULT_AUTOCORRECT_MARGIN

    def __init__(self, dictionary: Optional[Dict[str, float]] = None):
        self.spatial_model = SpatialKeyModel(
            uncertainty_radius=self.spatial_uncertainty,
        )
        self.word_generator = FuzzyWordGenerator(
            spatial_model=self.spatial_model,
            dictionary=dictionary,
        )

    def get_fuzzy_predictions(
        self,
        typed_text: str,
        n: int = 5,
    ) -> List[Tuple[str, float]]:
        words = typed_text.split()
        current_word = words[-1] if words and not typed_text.endswith(" ") else ""
        if not current_word:
            return []
        return self.word_generator.generate_candidates(current_word)[:n]

    def should_autocorrect(
        self,
        typed_word: str,
        context: str = "",
    ) -> Optional[str]:
        # Skip very short typings.  Single chars and 2-char fragments
        # carry too little signal — "v" → "is", "vs" → "is", "th" →
        # "to" are all corrections the user did not ask for.  Common
        # 2-char abbreviations the user types deliberately ("vs",
        # "ok", "th" as a fragment of "the" they're still typing)
        # would be clobbered.  Genuine 2-char misspellings that need
        # autocorrect (e.g. "im" → "I'm") are handled by the upstream
        # ``check_autocorrect`` fast-path table, which is unaffected
        # by this guard.
        if len(typed_word) < 3:
            return None
        correction = self.word_generator.get_correction(typed_word, context)
        if correction is None:
            return None
        word, confidence = correction
        if confidence < self.confidence_threshold:
            return None
        # Relative threshold: the correction must outscore what the
        # typed word would score if it were a rare real dictionary
        # entry. Without this, "thru" → "the", "lol" → "log",
        # "wtf" → "wtf" with one extra letter, etc. would all fire
        # because the absolute confidence threshold is permissive
        # by design (it has to admit corrections of obvious typos
        # like "teh" → "the"). Implausibly shaped inputs get
        # baseline 0 so we still autocorrect random letter slop.
        typed_baseline = self._typed_baseline(typed_word)
        if confidence < typed_baseline * self.autocorrect_margin:
            return None
        return word

    @staticmethod
    def _typed_baseline(typed_word: str) -> float:
        """Hypothetical "rare real word" score for the typed input.

        Returns ``log1p(1) ≈ 0.69`` if the input has the shape of a
        plausible word (at least one vowel and one consonant — same
        rule the n-gram fragment filter uses), else ``0.0``. The
        ``should_autocorrect`` relative-margin check multiplies this
        by ``autocorrect_margin`` to derive the score a correction
        must clear before it fires.
        """
        if not typed_word:
            return 0.0
        has_vowel = False
        has_consonant = False
        for c in typed_word.lower():
            if c in "aeiou":
                has_vowel = True
            elif c == "y":
                has_vowel = True
                has_consonant = True
            elif c.isalpha():
                has_consonant = True
        if not (has_vowel and has_consonant):
            return 0.0
        return math.log1p(1)

    def get_key_alternatives(self, key: str) -> Dict[str, float]:
        return self.spatial_model.get_key_probabilities(key)

    def load_dictionary(self, path) -> bool:
        return self.word_generator.load_dictionary(path)

    def set_frequencies(self, freqs: Mapping[str, float]) -> None:
        """Merge n-gram-style frequency counts into the fuzzy dictionary."""
        self.word_generator.set_frequencies(freqs)

    def get_stats(self) -> dict:
        return {
            "spatial_uncertainty": self.spatial_uncertainty,
            "confidence_threshold": self.confidence_threshold,
            "prediction_weight": self.prediction_weight,
            "dictionary_size": len(self.word_generator.dictionary),
        }
