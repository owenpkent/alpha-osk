"""The prefix-aware fuzzy beam: the live bar's fuzzy source completes through an error.

Before this, ``FuzzyRecognizer.get_fuzzy_predictions`` returned whole-word
corrections of the typed prefix, so mid-word it offered words the same
length as, or shorter than, what had been typed and never the word being
typed (0.0% after a slip, 1.4% with no error, measured).  These tests
drive the same entry point the hybrid merge calls, against the shipped
dictionary, and every positive case is paired with the near-miss or the
legacy path it has to beat.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from src.prediction.fuzzy_recognizer import QWERTY_POSITIONS, FuzzyRecognizer, SpatialKeyModel
from src.prediction.ngram_predictor import NgramPredictor
from src.prediction.prefix_beam import PrefixBeam, PrefixIndex, SpatialEmissions

DATA = Path(__file__).resolve().parents[1] / "data" / "google-10000-english-usa-no-swears.txt"


@pytest.fixture(scope="module")
def fr() -> FuzzyRecognizer:
    """The recognizer as the hybrid builds it: wordlist plus the n-gram's counts.

    The shipped wordlist carries no frequencies, so ``load_dictionary`` alone
    gives every word 1.0 and the beam's frequency term goes inert; the hybrid
    follows it with ``set_frequencies(ngram.unigrams)``, and so does this.
    """
    recognizer = FuzzyRecognizer()
    assert recognizer.load_dictionary(DATA)
    ngram = NgramPredictor()
    ngram.load_base_dictionary()
    recognizer.set_frequencies(ngram.unigrams)
    return recognizer


def top(fr: FuzzyRecognizer, typed: str, n: int = 5, **kw) -> list[str]:
    return [w for w, _ in fr.get_fuzzy_predictions(typed, n, **kw)]


class TestCompletesThroughAnError:
    def test_a_clean_prefix_is_completed(self, fr):
        assert all(w.startswith("docu") for w in top(fr, "docu"))
        assert sum(w.startswith("hel") for w in top(fr, "hel")) >= 4

    def test_a_neighbour_slip_still_reaches_the_word(self, fr):
        assert top(fr, "hwllo")[0] == "hello"
        assert any(w.startswith("soci") for w in top(fr, "socu", 3))

    def test_an_omitted_click_is_recovered(self, fr):
        assert "hello" in top(fr, "helo")
        assert "information" in top(fr, "informtion")

    def test_an_extra_click_is_recovered(self, fr):
        assert "hello" in top(fr, "helllo")
        assert "document" in top(fr, "documennt")

    def test_a_transposition_is_recovered(self, fr):
        assert "the" in top(fr, "teh", 3)
        assert "hello" in top(fr, "hlelo")

    def test_a_correctly_typed_word_ranks_first_however_long(self, fr):
        # The old beam pruned a perfect 9-letter typing below its floor and
        # returned nothing at all for ``documentation``.
        for word in ("hello", "because", "different", "information", "documentation"):
            assert top(fr, word)[0] == word

    def test_too_short_a_prefix_is_left_to_the_exact_completer(self, fr):
        assert top(fr, "he") == []
        assert top(fr, "h") == []

    def test_scores_are_relative_positive_and_descending(self, fr):
        scored = fr.get_fuzzy_predictions("docu", 5)
        values = [s for _, s in scored]
        assert values[0] == pytest.approx(1.0)
        assert all(0 < v <= 1.0 for v in values)
        assert values == sorted(values, reverse=True)


def _sample(fr: FuzzyRecognizer, min_len: int, count: int) -> list[str]:
    words = sorted(
        (w for w in fr.word_generator.dictionary if w.isalpha() and len(w) >= min_len),
        key=lambda w: -fr.word_generator.dictionary[w],
    )[:2000]
    step = max(1, len(words) // count)
    return words[::step][:count]


class TestMeasuredRecall:
    """Floors set well under the measured figures, so they guard direction, not noise."""

    def test_clean_prefixes_complete_to_the_intended_word(self, fr):
        words = _sample(fr, 6, 200)
        hits = sum(w in top(fr, w[:4]) for w in words)
        assert hits / len(words) >= 0.80  # measured 92%
        # and the top pick never overrides what was actually typed
        assert all(top(fr, w[:4])[0].startswith(w[:4]) for w in words)

    def test_a_slip_inside_the_prefix_is_survived_and_the_legacy_path_did_not(self, fr):
        rng = random.Random(11)
        model = SpatialKeyModel()

        def neighbours(c: str) -> list[str]:
            n = [k for k in model.get_nearby_keys(c) if k != c and k.isalpha()]
            return n or [c]

        words = _sample(fr, 7, 200)
        cases = []
        for w in words:
            prefix = w[:5]
            i = rng.randrange(5)
            typed = prefix[:i] + rng.choice(neighbours(prefix[i])) + prefix[i + 1 :]
            if typed != prefix:
                cases.append((w, typed))
        beam_hits = sum(w in top(fr, typed) for w, typed in cases)
        legacy_hits = sum(
            w in [c for c, _ in fr.word_generator.generate_candidates(typed)][:5]
            for w, typed in cases
        )
        assert beam_hits / len(cases) >= 0.80  # measured 94.5%
        assert legacy_hits / len(cases) < 0.05  # measured 0.0%


class TestTheEmissionTakesAPosition:
    def test_key_centres_reproduce_the_default(self, fr):
        centres = [QWERTY_POSITIONS[c] for c in "hood"]
        assert top(fr, "hood", positions=centres) == top(fr, "hood")

    def test_a_click_reported_on_one_key_but_landing_on_another_follows_the_landing(self, fr):
        assert top(fr, "hood")[0] == "hood"
        on_g = [QWERTY_POSITIONS["g"], None, None, None]
        assert top(fr, "hood", positions=on_g)[0] == "good"

    def test_a_perfect_hit_costs_nothing_and_a_miss_costs_distance(self):
        emissions = SpatialEmissions(QWERTY_POSITIONS)
        row = emissions.for_key("h")
        assert row is not None
        assert row["h"] == 0.0
        assert row["g"] < 0.0
        assert row["b"] < row["g"]  # diagonal is further than adjacent


class TestTheBeamFollowsTheLayout:
    def test_moving_a_key_changes_which_slips_are_recoverable(self):
        base = FuzzyRecognizer()
        assert base.load_dictionary(DATA)
        assert "problem" not in top(base, "qroblem")
        moved = dict(QWERTY_POSITIONS)
        moved["q"] = (0.0, 9.5)  # put q beside p
        base.set_key_positions(moved)
        assert "problem" in top(base, "qroblem")


class TestTheIndexFollowsTheDictionary:
    def test_new_words_become_reachable_without_a_restart(self):
        recognizer = FuzzyRecognizer(dictionary={"zebra": 10.0, "zebras": 5.0})
        assert top(recognizer, "zeb") == ["zebra", "zebras"]
        recognizer.set_frequencies({"zebrafish": 50})
        assert "zebrafish" in top(recognizer, "zeb")

    def test_index_shape(self):
        index = PrefixIndex({"cat": 3, "cats": 2, "car": 1}, precompute_len=2)
        assert index.is_live("ca") and index.is_live("cats") and not index.is_live("cab")
        assert index.children("ca") == "rt"
        assert [w for _, w in index.completions("ca")] == ["cat", "cats", "car"]
        assert [w for _, w in index.completions("cat")] == ["cat", "cats"]  # bisect path
        assert index.completions("dog") == []


class TestTheLegacyPathIsStillThere:
    def test_the_switch_restores_whole_word_correction_for_the_bar(self, fr, monkeypatch):
        monkeypatch.setattr(fr, "prefix_completion", False)
        legacy = [w for w, _ in fr.word_generator.generate_candidates("hel")][:5]
        assert top(fr, "hel") == legacy

    def test_whole_word_correction_is_untouched(self, fr):
        correction = fr.word_generator.get_correction("hwllo")
        assert correction is not None and correction[0] == "hello"
        assert fr.word_generator.get_correction("hello") is None


class TestBeamUnit:
    def test_an_empty_dictionary_yields_nothing(self):
        beam = PrefixBeam(PrefixIndex({}), SpatialEmissions(QWERTY_POSITIONS))
        assert beam.complete("hello") == []

    def test_n_bounds_the_result(self):
        index = PrefixIndex({f"help{c}": 1.0 for c in "abcdefgh"})
        beam = PrefixBeam(index, SpatialEmissions(QWERTY_POSITIONS))
        assert len(beam.complete("help", 3)) == 3


class TestWhatWasTypedIsEvidence:
    def test_a_rare_word_typed_exactly_beats_a_common_word_a_few_slips_away(self):
        # On frequency alone "spent" (four slips: z->s, o->p, r->e, b->n) buys
        # its way past a word typed three times; the exact prefix must win.
        index = PrefixIndex({"zorblat": 3.0, "spent": 9000.0, "spend": 8000.0})
        beam = PrefixBeam(index, SpatialEmissions(QWERTY_POSITIONS))
        assert [w for w, _ in beam.complete("zorb", 3)][0] == "zorblat"

    def test_but_a_prefix_that_is_not_live_still_goes_to_the_slips(self):
        index = PrefixIndex({"spent": 9000.0, "spend": 8000.0})
        beam = PrefixBeam(index, SpatialEmissions(QWERTY_POSITIONS))
        assert [w for w, _ in beam.complete("zorb", 3)] == ["spent", "spend"]

    def test_within_the_exact_completions_frequency_still_orders(self, fr):
        assert top(fr, "docu")[0] == "document"

    def test_one_cheap_edit_still_competes_on_frequency(self, fr):
        # "teh" is a live prefix in the shipped list, and "the" is one swap away;
        # a hard exact-first tier buried it behind the rare word.
        first_two = top(fr, "teh", 2)
        assert "the" in first_two
        assert not first_two[0].startswith("teh")

    def test_an_expensive_path_is_only_moved_below_the_exact_completions_not_dropped(self):
        index = PrefixIndex({"zorblat": 3.0, "spent": 9000.0, "spend": 8000.0})
        beam = PrefixBeam(index, SpatialEmissions(QWERTY_POSITIONS))
        assert [w for w, _ in beam.complete("zorb", 3)] == ["zorblat", "spent", "spend"]
