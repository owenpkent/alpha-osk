"""Tests for the SymSpell precomputed-deletion spelling correction index.

Covers the algorithm-level guarantees (exact match, single-edit
recall across all four edit types, distance-2 recall, beyond-distance
rejection) and a couple of integration-shaped checks against the
fuzzy recognizer (distance-2 corrections that the prior edit-distance-1
path could not reach now surface).
"""

from __future__ import annotations

import pytest

from src.prediction.symspell import SymSpell, damerau_levenshtein


class TestDamerauLevenshtein:
    def test_identity(self):
        assert damerau_levenshtein("the", "the") == 0

    def test_single_substitution(self):
        assert damerau_levenshtein("cat", "bat") == 1

    def test_single_insertion(self):
        assert damerau_levenshtein("th", "the") == 1

    def test_single_deletion(self):
        assert damerau_levenshtein("thee", "the") == 1

    def test_transposition_is_distance_one(self):
        assert damerau_levenshtein("teh", "the") == 1
        assert damerau_levenshtein("becuase", "because") == 1

    def test_two_edits(self):
        assert damerau_levenshtein("becouase", "because") == 2

    def test_early_termination_returns_above_max(self):
        # "kitten" → "sitting" is distance 3 (substitute k/s, substitute
        # e/i, insert g).  Asking for max_dist=2 should return >2 fast.
        assert damerau_levenshtein("kitten", "sitting", max_dist=2) > 2

    def test_length_difference_short_circuit(self):
        assert damerau_levenshtein("a", "abcdef", max_dist=2) > 2


class TestSymSpellBasic:
    def test_empty_lookup_returns_empty(self):
        ss = SymSpell()
        assert ss.lookup("") == []

    def test_exact_match_distance_zero(self):
        ss = SymSpell()
        ss.add_word("the", 100)
        results = ss.lookup("the")
        assert results == [("the", 100, 0)]

    def test_unknown_word_no_neighbours(self):
        ss = SymSpell()
        ss.add_word("the", 100)
        # "xyz" is far enough from "the" to exceed max_edit_distance=2
        results = ss.lookup("xyz")
        assert results == []

    def test_substitution(self):
        ss = SymSpell()
        ss.add_word("example", 100)
        # "rxample" — substitution of 'r' for 'e' at position 0
        results = ss.lookup("rxample")
        assert any(w == "example" and d == 1 for w, _, d in results)

    def test_insertion_user_missed_a_letter(self):
        ss = SymSpell()
        ss.add_word("the", 100)
        results = ss.lookup("th")
        assert any(w == "the" and d == 1 for w, _, d in results)

    def test_deletion_user_typed_extra_letter(self):
        ss = SymSpell()
        ss.add_word("the", 100)
        results = ss.lookup("thee")
        assert any(w == "the" and d == 1 for w, _, d in results)

    def test_transposition_counted_as_distance_one(self):
        ss = SymSpell()
        ss.add_word("the", 100)
        results = ss.lookup("teh")
        assert any(w == "the" and d == 1 for w, _, d in results)

    def test_distance_two_double_edit(self):
        ss = SymSpell(max_edit_distance=2)
        ss.add_word("because", 100)
        # "becouase" — transposition 'u'/'a' plus 'o' insertion at index 3
        # i.e. two edits from "because".
        results = ss.lookup("becouase")
        assert any(w == "because" and d == 2 for w, _, d in results)

    def test_distance_one_index_rejects_distance_two(self):
        ss = SymSpell(max_edit_distance=1)
        ss.add_word("because", 100)
        # Distance 2 — should not surface with max_edit_distance=1.
        results = ss.lookup("becouase")
        assert all(w != "because" for w, _, _ in results)

    def test_apostrophe_word_findable_from_bare_form(self):
        # "i'm" indexed with apostrophe; querying "im" (apostrophe
        # missing) should find it via deletion-variant overlap.
        ss = SymSpell()
        ss.add_word("i'm", 50)
        results = ss.lookup("im")
        assert any(w == "i'm" for w, _, _ in results)

    def test_results_sorted_by_distance_then_frequency(self):
        ss = SymSpell()
        ss.add_word("the", 1000)
        ss.add_word("she", 500)
        ss.add_word("hen", 50)
        # All three are within distance ≤ 2 of "the".  Distance-0 ("the")
        # comes first; remaining ties broken by frequency descending.
        results = ss.lookup("the")
        assert results[0][0] == "the"
        # Among distance-1 results, frequency ordering wins.
        d1 = [w for w, _, d in results if d == 1]
        if "she" in d1 and "hen" in d1:
            assert d1.index("she") < d1.index("hen")

    def test_add_word_higher_frequency_wins(self):
        ss = SymSpell()
        ss.add_word("the", 10)
        ss.add_word("the", 100)
        results = ss.lookup("the")
        assert results == [("the", 100, 0)]

    def test_index_rebuild_after_add(self):
        ss = SymSpell()
        ss.add_word("the", 100)
        # Trigger first build.
        _ = ss.lookup("the")
        # Add another word and lookup — index should rebuild
        # transparently and find the new word.
        ss.add_word("she", 50)
        results = ss.lookup("she")
        assert any(w == "she" for w, _, _ in results)

    def test_contains(self):
        ss = SymSpell()
        ss.add_word("the", 100)
        assert "the" in ss
        assert "THE" in ss  # case-insensitive
        assert "xyz" not in ss

    def test_len(self):
        ss = SymSpell()
        ss.add_word("the", 100)
        ss.add_word("she", 50)
        assert len(ss) == 2


class TestSymSpellPrefixLength:
    def test_long_word_indexed_at_prefix(self):
        ss = SymSpell(max_edit_distance=2, prefix_length=7)
        ss.add_word("internationalization", 10)
        # Typo in the first 7 chars should be caught.
        results = ss.lookup("internatonalization")  # missing 'i' at index 7
        # The typo is past the prefix — but the indexed prefix matches
        # the input's prefix exactly, so the candidate surfaces and
        # final D-L confirms.
        assert any(w == "internationalization" for w, _, _ in results)


class TestSymSpellEdgeCases:
    def test_invalid_max_edit_distance(self):
        with pytest.raises(ValueError):
            SymSpell(max_edit_distance=-1)

    def test_invalid_prefix_length(self):
        with pytest.raises(ValueError):
            # prefix_length too short for max_edit_distance
            SymSpell(max_edit_distance=2, prefix_length=2)

    def test_empty_word_ignored(self):
        ss = SymSpell()
        ss.add_word("", 100)
        assert len(ss) == 0

    def test_lookup_max_distance_capped_to_index_max(self):
        ss = SymSpell(max_edit_distance=1)
        ss.add_word("because", 100)
        # Caller asks for distance 5; index built for 1.  Should not
        # crash, just return distance-1 results.
        results = ss.lookup("becuase", max_edit_distance=5)
        assert all(d <= 1 for _, _, d in results)


class TestFuzzyRecognizerIntegration:
    """Regression cases for SymSpell-backed candidate generation in
    FuzzyWordGenerator.  These cases were not reachable by the prior
    edit-distance-1 path (or only via the spatial neighbour route, which
    only catches near-key substitutions)."""

    def _make_generator(self, words):
        from src.prediction.fuzzy_recognizer import FuzzyWordGenerator
        gen = FuzzyWordGenerator(dictionary={w: 100 for w in words})
        return gen

    def test_distance_two_correction_now_reachable(self):
        gen = self._make_generator(["because", "the", "and"])
        # Two-edit input that the old edit-distance-1 path could not
        # reach.  "becouase" = "because" with 'o' inserted and 'u'/'a'
        # transposed (distance 2).
        candidates = gen.generate_candidates("becouase")
        assert any(w == "because" for w, _ in candidates)

    def test_non_adjacent_substitution_now_reachable(self):
        # "rxample" → "example" is a single substitution but 'r' and 'e'
        # are not spatial neighbours on QWERTY (different rows).  The
        # prior edit-distance-1 path did not enumerate substitutions at
        # all; only the spatial path did, which fails here.
        gen = self._make_generator(["example", "sample", "ample"])
        candidates = gen.generate_candidates("rxample")
        assert any(w == "example" for w, _ in candidates)

    def test_existing_distance_one_transposition_still_works(self):
        gen = self._make_generator(["the", "and", "for"])
        candidates = gen.generate_candidates("teh")
        assert any(w == "the" for w, _ in candidates)

    def test_apostrophe_insertion_still_works(self):
        gen = self._make_generator(["i'm", "im", "it's", "the"])
        # Typed "im" without the apostrophe — should surface "i'm" as
        # an insertion candidate.  "im" is also in the dict (distance 0)
        # which surfaces too, but "i'm" should be present.
        candidates = gen.generate_candidates("im")
        assert any(w == "i'm" for w, _ in candidates)
