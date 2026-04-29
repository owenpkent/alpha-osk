"""
SymSpell: precomputed-deletion spelling correction.

Wolf Garbe's symmetric-delete algorithm (2012).  For every dictionary
word, precompute all "deletion variants" up to ``max_edit_distance``
deletions and index them in a hash table.  At query time, generate
deletion variants of the input the same way; any dictionary word that
shares a deletion variant with the input is a candidate, and a final
Damerau-Levenshtein check filters to the configured edit distance.

This catches insertions, deletions, substitutions, transpositions, and
mixed errors at the configured edit distance with O(1) hash lookups
per input variant.  The prior beam-search/per-letter edit-distance
approach in ``fuzzy_recognizer`` was capped at edit distance 1 and did
not catch substitutions at all; this module raises the default to
edit distance 2 and adds substitution coverage.

Reference: github.com/wolfgarbe/SymSpell
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

_logger = logging.getLogger("SymSpell")


def damerau_levenshtein(a: str, b: str, max_dist: int = 2) -> int:
    """Damerau-Levenshtein distance with early termination.

    Returns ``max_dist + 1`` if the true distance exceeds ``max_dist``
    (the precise value above the threshold is not meaningful — the
    early-termination prunes once any row's minimum exceeds it).
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > max_dist:
        return max_dist + 1

    la, lb = len(a), len(b)
    # Two prior rows tracked: prev2 for transposition (i-2 row), prev for i-1.
    prev2 = [0] * (lb + 1)
    prev = list(range(lb + 1))

    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        min_in_row = i
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            curr[j] = min(
                curr[j - 1] + 1,        # insertion
                prev[j] + 1,            # deletion
                prev[j - 1] + cost,     # substitution
            )
            if (
                i > 1
                and j > 1
                and ai == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                curr[j] = min(curr[j], prev2[j - 2] + cost)
            if curr[j] < min_in_row:
                min_in_row = curr[j]
        if min_in_row > max_dist:
            return max_dist + 1
        prev2 = prev
        prev = curr

    return prev[lb]


class SymSpell:
    """Precomputed-deletion spelling correction index.

    Build via ``add_word`` / ``add_dictionary``, then query with
    ``lookup``.  The deletion index is built lazily on the first
    ``lookup`` call after any mutation, so bulk-loading does not pay
    rebuild cost per insert.
    """

    def __init__(self, max_edit_distance: int = 2, prefix_length: int = 7):
        """
        Args:
            max_edit_distance: Max edit distance for lookups.  Higher
                means more recall but more memory — the deletion-variant
                count grows superlinearly with this.  Default 2 catches
                most real typos including double-edits.
            prefix_length: For words longer than this, only index
                deletion variants of the first ``prefix_length``
                characters.  Bounds the per-word index size for rare
                long words; typos in the first few letters of any word
                are still caught.  Standard SymSpell trade-off.
        """
        if max_edit_distance < 0:
            raise ValueError("max_edit_distance must be non-negative")
        if prefix_length < max_edit_distance + 1:
            raise ValueError(
                "prefix_length must be at least max_edit_distance + 1"
            )

        self.max_edit_distance = max_edit_distance
        self.prefix_length = prefix_length

        self._words: Dict[str, int] = {}
        self._deletes: Dict[str, List[str]] = defaultdict(list)
        self._built = False

    def add_word(self, word: str, freq: int = 1) -> None:
        """Add or update a word's frequency.  Higher freq always wins."""
        word = word.lower()
        if not word:
            return
        existing = self._words.get(word, 0)
        if freq > existing:
            self._words[word] = freq
        elif word not in self._words:
            self._words[word] = freq
        self._built = False

    def add_dictionary(self, entries: Iterable[Tuple[str, int]]) -> None:
        """Bulk-add ``(word, freq)`` pairs."""
        for word, freq in entries:
            self.add_word(word, freq)

    def _deletion_variants(self, word: str, max_deletes: int) -> Set[str]:
        """All distinct deletion variants of ``word`` up to ``max_deletes``."""
        if max_deletes <= 0 or len(word) <= 1:
            return set()
        # BFS through deletion levels — each level deletes one char from
        # each string in the previous level.
        result: Set[str] = set()
        frontier: Set[str] = {word}
        for _ in range(max_deletes):
            next_frontier: Set[str] = set()
            for w in frontier:
                if len(w) <= 1:
                    continue
                for i in range(len(w)):
                    variant = w[:i] + w[i + 1:]
                    if variant and variant not in result:
                        result.add(variant)
                        next_frontier.add(variant)
            frontier = next_frontier
            if not frontier:
                break
        return result

    def prepare(self) -> None:
        """Force the deletion-variant index to build now.

        Useful when the caller wants to pay the build cost during
        startup (or any other known idle window) instead of on the
        first ``lookup`` call.  Idempotent — does nothing if the index
        is already built and no mutations have occurred since.
        """
        self._build_index()

    def _build_index(self) -> None:
        if self._built:
            return
        self._deletes.clear()
        for word in self._words:
            indexed = (
                word[:self.prefix_length]
                if len(word) > self.prefix_length
                else word
            )
            # The indexed prefix is itself a "zero-deletion variant".
            self._deletes[indexed].append(word)
            for variant in self._deletion_variants(
                indexed, self.max_edit_distance
            ):
                self._deletes[variant].append(word)
        self._built = True
        _logger.debug(
            "SymSpell index: %d words, %d deletion variants",
            len(self._words),
            len(self._deletes),
        )

    def lookup(
        self,
        input_word: str,
        max_edit_distance: Optional[int] = None,
    ) -> List[Tuple[str, int, int]]:
        """Find dictionary words within ``max_edit_distance`` of input.

        Returns ``(word, frequency, edit_distance)`` tuples sorted by
        edit distance ascending, then frequency descending.
        """
        if not input_word:
            return []
        if not self._built:
            self._build_index()

        if max_edit_distance is None:
            max_edit_distance = self.max_edit_distance
        if max_edit_distance > self.max_edit_distance:
            max_edit_distance = self.max_edit_distance
        if max_edit_distance < 0:
            return []

        input_word = input_word.lower()

        candidates: Dict[str, int] = {}
        if input_word in self._words:
            candidates[input_word] = 0
            if max_edit_distance == 0:
                return [(input_word, self._words[input_word], 0)]

        indexed_input = (
            input_word[:self.prefix_length]
            if len(input_word) > self.prefix_length
            else input_word
        )
        input_variants: Set[str] = {indexed_input}
        input_variants.update(
            self._deletion_variants(indexed_input, max_edit_distance)
        )

        for variant in input_variants:
            sources = self._deletes.get(variant)
            if not sources:
                continue
            for source in sources:
                if source in candidates:
                    continue
                dist = damerau_levenshtein(
                    input_word, source, max_edit_distance
                )
                if dist <= max_edit_distance:
                    candidates[source] = dist

        return sorted(
            (
                (w, self._words[w], d)
                for w, d in candidates.items()
            ),
            key=lambda x: (x[2], -x[1]),
        )

    def __len__(self) -> int:
        return len(self._words)

    def __contains__(self, word: str) -> bool:
        return word.lower() in self._words
