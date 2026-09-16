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


def _oracle_distance(left: str, right: str) -> int:
    """Independent optimal-string-alignment distance for small test cases."""
    rows = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i in range(len(left) + 1):
        rows[i][0] = i
    for j in range(len(right) + 1):
        rows[0][j] = j

    for i in range(1, len(left) + 1):
        for j in range(1, len(right) + 1):
            substitution = 0 if left[i - 1] == right[j - 1] else 1
            rows[i][j] = min(
                rows[i - 1][j] + 1,
                rows[i][j - 1] + 1,
                rows[i - 1][j - 1] + substitution,
            )
            if i > 1 and j > 1 and left[i - 1] == right[j - 2] and left[i - 2] == right[j - 1]:
                rows[i][j] = min(rows[i][j], rows[i - 2][j - 2] + substitution)
    return rows[-1][-1]


def _expected_result_set(
    entries: list[tuple[str, int]], input_word: str, max_edit_distance: int
) -> set[tuple[str, int, int]]:
    """Apply public normalization and max-frequency rules to oracle distances."""
    frequencies: dict[str, int] = {}
    for word, frequency in entries:
        word = word.lower()
        if not word:
            continue
        frequencies[word] = max(frequency, frequencies.get(word, 0))

    input_word = input_word.lower()
    matches = set()
    for word, frequency in frequencies.items():
        distance = _oracle_distance(input_word, word)
        if distance <= max_edit_distance:
            matches.add((word, frequency, distance))
    return matches


def _single_edit_neighborhood(word: str) -> set[str]:
    """Return a compact exhaustive neighborhood using every supported edit kind."""
    probes = {word}
    probes.update(word[:i] + word[i + 1 :] for i in range(len(word)))
    probes.update(word[:i] + "x" + word[i + 1 :] for i in range(len(word)))
    probes.update(word[:i] + "x" + word[i:] for i in range(len(word) + 1))
    probes.update(
        word[:i] + word[i + 1] + word[i] + word[i + 2 :]
        for i in range(len(word) - 1)
        if word[i] != word[i + 1]
    )
    return probes


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


class TestSymSpellPreparedAndIncrementalBehavior:
    ENTRIES = [
        ("cat", 10),
        ("bat", 10),
        ("can't", 7),
        ("café", 9),
        ("hat", 10),
        ("cart", 6),
        ("cant", 4),
        ("cafe", 5),
    ]

    def test_prepared_and_grown_indexes_match_an_independent_oracle(self):
        prepared = SymSpell(max_edit_distance=2)
        prepared.add_dictionary(self.ENTRIES)
        prepared.prepare()

        grown = SymSpell(max_edit_distance=2)
        grown.add_dictionary(self.ENTRIES[:4])
        grown.prepare()
        grown.add_dictionary(self.ENTRIES[4:])

        probes = {"dat", "ct", "cta", "caxt", "cafx", "cant", "can't"}
        for word, _ in self.ENTRIES:
            probes.update(_single_edit_neighborhood(word))

        for max_distance in (0, 1, 2):
            for probe in probes:
                expected = _expected_result_set(self.ENTRIES, probe, max_distance)
                prepared_results = prepared.lookup(probe, max_distance)
                grown_results = grown.lookup(probe, max_distance)

                assert set(prepared_results) == expected
                assert prepared_results == grown_results
                ranking = [(distance, -frequency) for _, frequency, distance in prepared_results]
                assert ranking == sorted(ranking)

    def test_base_and_added_word_share_a_deletion_bucket(self):
        index = SymSpell(max_edit_distance=2)
        index.add_word("cat", 10)
        index.prepare()
        index.add_word("bat", 10)

        assert index.lookup("dat", 1) == [("cat", 10, 1), ("bat", 10, 1)]

    def test_added_word_with_new_deletion_buckets_is_immediately_findable(self):
        index = SymSpell(max_edit_distance=2)
        index.add_word("cat", 10)
        index.prepare()
        index.add_word("zebra", 3)

        assert index.lookup("zebar") == [("zebra", 3, 1)]

    def test_repeated_prepare_does_not_regenerate_the_base(self, monkeypatch):
        index = SymSpell(max_edit_distance=2)
        index.add_dictionary([("hello", 10), ("world", 8)])

        calls = 0
        original = index._deletion_variants

        def counted(word, max_deletes):
            nonlocal calls
            calls += 1
            return original(word, max_deletes)

        monkeypatch.setattr(index, "_deletion_variants", counted)
        index.prepare()
        first_prepare_calls = calls
        index.prepare()

        assert first_prepare_calls > 0
        assert calls == first_prepare_calls

    def test_frequency_updates_use_the_max_for_base_and_added_words(self):
        index = SymSpell(max_edit_distance=2)
        index.add_word("cat", 10)
        index.prepare()
        index.add_word("bat", 20)

        index.add_word("cat", 4)
        index.add_word("bat", 3)
        index.add_word("cat", 40)
        index.add_word("bat", 30)

        assert index.lookup("dat") == [("cat", 40, 1), ("bat", 30, 1)]
        assert index.lookup("cat", 0) == [("cat", 40, 0)]
        assert index.lookup("bat", 0) == [("bat", 30, 0)]

    def test_unicode_and_apostrophes_work_in_both_layers(self):
        index = SymSpell(max_edit_distance=2)
        index.add_dictionary([("naïve", 12), ("i'm", 11)])
        index.prepare()
        index.add_dictionary([("café", 10), ("we're", 9)])

        assert index.lookup("naive", 1) == [("naïve", 12, 1)]
        assert index.lookup("im", 1) == [("i'm", 11, 1)]
        assert index.lookup("cafe", 1) == [("café", 10, 1)]
        assert index.lookup("were", 1) == [("we're", 9, 1)]

    def test_lone_surrogate_round_trips_through_the_prepared_index(self):
        encoded_word = b"a\xed\xa0\x80b"
        encoded_probe = b"a\xed\xa0\x80c"
        word = encoded_word.decode("utf-8", "surrogatepass")
        probe = encoded_probe.decode("utf-8", "surrogatepass")
        index = SymSpell(max_edit_distance=2)
        index.add_word(word, 8)
        index.prepare()

        results = index.lookup(probe, 1)
        safe_results = [
            (candidate.encode("utf-8", "surrogatepass"), frequency, distance)
            for candidate, frequency, distance in results
        ]
        assert safe_results == [(encoded_word, 8, 1)]

    def test_hash_collisions_preserve_base_overlay_and_missing_key_lookups(self, monkeypatch):
        from src.prediction import packed_deletes

        monkeypatch.setattr(packed_deletes, "hash", lambda _key: 0, raising=False)
        index = SymSpell(max_edit_distance=2)
        index.add_dictionary([("cat", 10), ("dog", 8), ("café", 6)])
        index.prepare()
        index.add_word("bat", 20)

        assert index.lookup("dat", 1) == [("bat", 20, 1), ("cat", 10, 1)]
        assert index.lookup("dgo", 1) == [("dog", 8, 1)]
        assert index.lookup("cafe", 1) == [("café", 6, 1)]
        assert index.lookup("zzzz") == []

    def test_empty_prepared_base_accepts_later_words(self):
        index = SymSpell(max_edit_distance=2)
        index.prepare()
        index.add_word("later", 5)

        assert index.lookup("ltaer") == [("later", 5, 1)]

    def test_lookup_distance_zero_negative_and_clamped(self):
        index = SymSpell(max_edit_distance=2)
        index.add_word("because", 100)
        index.prepare()

        assert index.lookup("because", 0) == [("because", 100, 0)]
        assert index.lookup("becuase", 0) == []
        assert index.lookup("because", -1) == []
        assert index.lookup("becouase", 99) == [("because", 100, 2)]

    def test_prefix_boundary_and_distance_parameter_behavior(self):
        index = SymSpell(max_edit_distance=2, prefix_length=4)
        index.add_word("abcdefgh", 10)
        index.prepare()

        one_edit_probes = [
            "abxdefgh",
            "abcdxfgh",
            "abcdegh",
            "abxcdefgh",
        ]
        for probe in one_edit_probes:
            assert index.lookup(probe, 1) == [("abcdefgh", 10, 1)]

        two_edit_probe = "abxdxfgh"
        assert index.lookup(two_edit_probe, 1) == []
        assert index.lookup(two_edit_probe, 2) == [("abcdefgh", 10, 2)]


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
