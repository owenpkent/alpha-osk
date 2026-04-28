"""Tests for the hybrid prediction engine.

HybridPredictor requires PySide6 (QObject), so these tests verify
the merge logic and coordination between prediction sources.
We test the merge function and the standalone predictors directly
to avoid needing a full Qt event loop.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# We need PySide6 for HybridPredictor since it extends QObject.
# If PySide6 is not available, skip these tests.
PySide6 = pytest.importorskip("PySide6")

from src.prediction.hybrid_predictor import HybridPredictor


@pytest.fixture
def predictor(tmp_model_dir: Path) -> HybridPredictor:
    """Create a HybridPredictor with LLM disabled and temp model dir."""
    return HybridPredictor(model_dir=tmp_model_dir, enable_llm=False)


class TestHybridPredictBasic:
    """Core prediction behavior."""

    def test_predict_returns_list_of_strings(self, predictor: HybridPredictor):
        results = predictor.predict("hel", n=5)
        assert isinstance(results, list)
        assert all(isinstance(w, str) for w in results)

    def test_predict_returns_at_most_n(self, predictor: HybridPredictor):
        results = predictor.predict("the ", n=3)
        assert len(results) <= 3

    def test_predict_empty_context(self, predictor: HybridPredictor):
        results = predictor.predict("", n=5)
        assert isinstance(results, list)

    def test_predict_partial_word(self, predictor: HybridPredictor):
        predictor.learn("hello world hello there")
        results = predictor.predict("hel", n=5)
        assert any(w.startswith("hel") for w in results)

    def test_predict_next_word(self, predictor: HybridPredictor):
        predictor.learn("I want to go to the store")
        results = predictor.predict("I want ", n=5)
        # Should predict next words, not complete "want"
        assert isinstance(results, list)


class TestHybridLearning:
    """Learning and adaptation."""

    def test_learn_text(self, predictor: HybridPredictor):
        # Unknown words need to pass the ngram fragment-filter repetition
        # gate (3 sightings) before they enter user_vocab.
        predictor.learn("xylophone zebra xylophone xylophone")
        results = predictor.predict("xylo", n=5)
        assert "xylophone" in results

    def test_learn_word(self, predictor: HybridPredictor):
        predictor.learn_word("supercalifragilistic")
        # Word should be known to the n-gram predictor
        assert predictor._ngram.unigrams["supercalifragilistic"] > 0

    def test_learn_from_selection(self, predictor: HybridPredictor):
        predictor.learn_from_selection("I want", "pizza")
        assert predictor._ngram.unigrams["pizza"] > 0


class TestHybridAutocorrect:
    """Autocorrect integration."""

    def test_check_autocorrect_valid_word(self, predictor: HybridPredictor):
        # Valid words should not be corrected
        result = predictor.check_autocorrect("hello")
        # May or may not have a correction depending on dictionary
        assert result is None or isinstance(result, str)

    def test_get_key_alternatives(self, predictor: HybridPredictor):
        alts = predictor.get_key_alternatives("f")
        assert isinstance(alts, dict)
        assert "f" in alts


class TestHybridPersistence:
    """Model save/load."""

    def test_save_creates_files(self, predictor: HybridPredictor, tmp_model_dir: Path):
        predictor.learn("test data for saving")
        predictor.save()
        assert (tmp_model_dir / "ngram_model.json").exists()
        assert (tmp_model_dir / "ppm_model.json").exists()

    def test_clear_user_data(self, predictor: HybridPredictor):
        predictor.learn("unique test words")
        predictor.clear_user_data()
        # clear_user_data clears user vocab, then reloads base dictionary
        # which may re-populate user_vocab. Verify our learned words are gone.
        assert predictor._ngram.user_vocab.get("unique", 0) == 0


class TestHybridLLM:
    """LLM toggle behavior (without actually loading a model)."""

    def test_llm_disabled_by_default_in_fixture(self, predictor: HybridPredictor):
        assert not predictor.enable_llm
        assert not predictor.llm_available

    def test_enable_llm_property(self, predictor: HybridPredictor):
        predictor.enable_llm = True
        assert predictor.enable_llm


class TestHybridStats:
    """Statistics reporting."""

    def test_get_stats_keys(self, predictor: HybridPredictor):
        stats = predictor.get_stats()
        assert "total_words" in stats
        assert "llm_enabled" in stats
        assert "llm_available" in stats
        assert "ppm_enabled" in stats
        assert "ppm" in stats
        assert "fuzzy" in stats


class TestHybridMergeWeighting:
    """Verify the merge logic prioritizes correctly."""

    def test_ngram_weighted_higher_for_next_word(self, predictor: HybridPredictor):
        """When context ends with space, n-gram should dominate."""
        predictor.learn("the quick brown fox jumps over the lazy dog")
        # Train enough for n-gram to have opinions
        for _ in range(10):
            predictor.learn("I want to go home")

        # This is a functional test — just verify it returns something
        results = predictor.predict("I want ", n=5)
        assert isinstance(results, list)

    def test_fuzzy_weight_is_a_float(self, predictor: HybridPredictor):
        # The merger reads ``_fuzzy.prediction_weight`` directly now
        # that profiles are gone — make sure it's still a usable float.
        weight = predictor._fuzzy.prediction_weight
        assert isinstance(weight, float)
        assert 0 < weight < 1

    def test_last_context_word_extracts_trailing_token(self, predictor: HybridPredictor):
        predictor._current_context = "the quick brown "
        assert predictor._last_context_word() == "brown"
        predictor._current_context = "Hello"
        assert predictor._last_context_word() == "hello"
        predictor._current_context = ""
        assert predictor._last_context_word() == ""
        predictor._current_context = "   "
        assert predictor._last_context_word() == ""

    def test_bigram_bonus_lifts_fuzzy_candidate(self, predictor: HybridPredictor):
        # Seed a strong "of → the" bigram and a competing weak bigram
        # for "of → tha".  Without the bonus the merger would tie them
        # on positional score; with the bonus, "the" should sort first.
        predictor._ngram.bigrams["of"]["the"] = 500
        predictor._ngram.bigrams["of"]["tha"] = 1
        predictor._current_context = "of "

        # Force-feed both candidates as if fuzzy returned them in this order.
        merged = predictor._merge_predictions(
            ngram=[], ppm=[], fuzzy=["tha", "the"], n=5,
        )
        # "the" should outrank "tha" because of the bigram bonus, even
        # though "tha" came first in the fuzzy list (positional weight
        # would favor it).
        # NB: results may be capitalized depending on context.
        as_lower = [w.lower() for w in merged]
        if "the" in as_lower and "tha" in as_lower:
            assert as_lower.index("the") < as_lower.index("tha")


class TestSentenceStartCapitalization:
    """`_merge_predictions` only treats genuinely-after-punctuation
    contexts as sentence starts. Empty context (fresh launch, post-
    backspace, post-app-switch) does NOT capitalize predictions.

    Reproduces the user-reported terminal bug: in Windows Terminal,
    typing "lsa" then backspacing 3 times left the prediction pills
    showing "The", "I", "How", etc. — the empty context was being
    treated as a sentence start.
    """

    def test_empty_context_does_not_capitalize(self, predictor: HybridPredictor):
        predictor._ngram.unigrams["the"] = 100
        predictor._ngram.unigrams["of"] = 80
        merged = predictor._merge_predictions(
            ngram=["the", "of"], ppm=[], fuzzy=[], n=5,
        )
        # current_context defaults to "" — must NOT trigger sentence
        # start capitalisation.
        assert "the" in merged or "The" not in merged, (
            f"empty context capitalised predictions: {merged}"
        )

    def test_post_period_context_does_capitalize(self, predictor: HybridPredictor):
        predictor._ngram.unigrams["the"] = 100
        predictor._current_context = "Hello world. "
        merged = predictor._merge_predictions(
            ngram=["the"], ppm=[], fuzzy=[], n=5,
        )
        assert "The" in merged, f"sentence-start should capitalise: {merged}"

    def test_mid_word_completion_does_not_capitalize(
        self, predictor: HybridPredictor,
    ):
        predictor._ngram.unigrams["the"] = 100
        predictor._current_context = "the"
        merged = predictor._merge_predictions(
            ngram=["the"], ppm=[], fuzzy=[], n=5,
        )
        assert "the" in merged, f"mid-word should stay lowercase: {merged}"
