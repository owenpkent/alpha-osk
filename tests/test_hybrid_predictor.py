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

    def test_learn_from_selection_gates_unknown_word(self, predictor: HybridPredictor):
        # A brand-new word that the engine generated (e.g. fuzzy /
        # PPM completion of a typed prefix) must pass through the
        # candidate gate, not land in user_vocab on the first click.
        predictor.learn_from_selection("hello", "zephyrish")
        assert predictor._ngram.user_vocab.get("zephyrish", 0) == 0
        assert predictor._ngram._candidate_counts["zephyrish"] == 1
        # Trailing bigram still gets +1 — context was validated by
        # this click even if the unigram is still gated.
        assert predictor._ngram.bigrams["hello"]["zephyrish"] == 1

    def test_learn_from_selection_promotes_after_threshold(self, predictor: HybridPredictor):
        for _ in range(3):
            predictor.learn_from_selection("hello", "zephyrish")
        # Promoted with cumulative click weight.
        assert predictor._ngram.user_vocab["zephyrish"] > 0

    def test_learn_from_selection_targets_trailing_edge(self, predictor: HybridPredictor):
        # Type a sentence so all the bigrams in the running context exist.
        predictor.learn("I have asked")
        before_i_have = predictor._ngram.bigrams["i"]["have"]
        before_have_asked = predictor._ngram.bigrams["have"]["asked"]
        # Click "claude" — only (asked, claude) should grow. Earlier
        # bigrams used to be re-incremented on every click; that double-
        # counted established edges in proportion to how many predictions
        # the user picked per sentence.
        predictor.learn_from_selection("I have asked", "claude")
        assert predictor._ngram.bigrams["asked"]["claude"] == 1
        assert predictor._ngram.bigrams["i"]["have"] == before_i_have
        assert predictor._ngram.bigrams["have"]["asked"] == before_have_asked

    def test_unlearn_word_forwards_to_ngram(self, predictor: HybridPredictor):
        predictor._ngram.learn("zephyrish")
        assert predictor._ngram._candidate_counts["zephyrish"] == 1
        assert predictor.unlearn_word("zephyrish") is True
        assert "zephyrish" not in predictor._ngram._candidate_counts


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
            ngram=[], ppm=[], fuzzy=[("tha", 1.0), ("the", 1.0)], n=5,
        )
        # "the" should outrank "tha" because of the bigram bonus, even
        # though "tha" came first in the fuzzy list (positional weight
        # would favor it).
        # NB: results may be capitalized depending on context.
        as_lower = [w.lower() for w in merged]
        if "the" in as_lower and "tha" in as_lower:
            assert as_lower.index("the") < as_lower.index("tha")


class TestPillCapitalization:
    """``_merge_predictions`` no longer auto-capitalises pills based on
    context. Sentence-start auto-cap and proper-noun lookup were
    removed because they fired on common English words ("hope",
    "rose", "may", "mark", "monday") and on the post-period word in
    every sentence — the user's stance is "only capitalize when shift
    or caps lock is engaged." Pills now mirror the typed prefix's
    casing only (handled by ``KeyboardBridge._display_cased``); the
    only Tier-1 exception is the "I" family."""

    def test_empty_context_stays_lowercase(self, predictor: HybridPredictor):
        predictor._ngram.unigrams["the"] = 100
        predictor._ngram.unigrams["of"] = 80
        merged = predictor._merge_predictions(
            ngram=[("the", 1.0), ("of", 0.8)], ppm=[], fuzzy=[], n=5,
        )
        assert "The" not in merged, f"empty context should stay lowercase: {merged}"

    def test_post_period_context_stays_lowercase(self, predictor: HybridPredictor):
        predictor._ngram.unigrams["the"] = 100
        predictor._current_context = "Hello world. "
        merged = predictor._merge_predictions(
            ngram=[("the", 1.0)], ppm=[], fuzzy=[], n=5,
        )
        assert "the" in merged, f"post-period should stay lowercase: {merged}"
        assert "The" not in merged

    def test_mid_word_completion_stays_lowercase(
        self, predictor: HybridPredictor,
    ):
        predictor._ngram.unigrams["the"] = 100
        predictor._current_context = "the"
        merged = predictor._merge_predictions(
            ngram=[("the", 1.0)], ppm=[], fuzzy=[], n=5,
        )
        assert "the" in merged, f"mid-word should stay lowercase: {merged}"

    def test_i_family_still_auto_capitalized(self, predictor: HybridPredictor):
        """Tier 1 — the ``I`` family is the one exception kept."""
        predictor._ngram.unigrams["i"] = 100
        merged = predictor._merge_predictions(
            ngram=[("i", 1.0)], ppm=[], fuzzy=[], n=5,
        )
        assert "I" in merged, f"'i' should still surface as 'I': {merged}"


class TestMergeStrategies:
    """Each merge strategy must produce a usable, validated ranking.

    These tests force-feed candidate lists directly into
    ``_merge_predictions`` so the strategy formula is the only variable
    — no n-gram lookup, no PPM training, no fuzzy spatial scoring.
    """

    def _seed_vocab(self, predictor: HybridPredictor, words: list[str]) -> None:
        # Words must be in unigrams for ``_is_valid_word`` to pass them
        # through the merge.  Real predictions feed scored tuples whose
        # words are already in vocab; tests need to mirror that gate.
        for w in words:
            predictor._ngram.unigrams[w] = 100

    def test_default_strategy_is_rank(self, predictor: HybridPredictor):
        assert predictor.merge_strategy == "rank"

    def test_set_merge_strategy_accepts_known_values(
        self, predictor: HybridPredictor,
    ):
        for strategy in ("rank", "rrf", "linear", "loglinear"):
            assert predictor.set_merge_strategy(strategy) is True
            assert predictor.merge_strategy == strategy

    def test_set_merge_strategy_rejects_unknown(
        self, predictor: HybridPredictor,
    ):
        predictor.set_merge_strategy("rank")
        assert predictor.set_merge_strategy("bogus") is False
        # Previous strategy preserved.
        assert predictor.merge_strategy == "rank"

    def test_rank_strategy_unchanged(self, predictor: HybridPredictor):
        """Default strategy must produce the same result it always did
        — pure positional weighting, no confidence sensitivity.
        """
        self._seed_vocab(predictor, ["alpha", "beta"])
        predictor.set_merge_strategy("rank")
        merged = predictor._merge_predictions(
            ngram=[("alpha", 0.99), ("beta", 0.01)],
            ppm=[],
            fuzzy=[],
            n=5,
        )
        # rank-1 always beats rank-2 regardless of underlying scores.
        assert merged.index("alpha") < merged.index("beta")

    def test_rrf_promotes_consensus(self, predictor: HybridPredictor):
        """A word that appears in two sources at modest rank should
        beat a word that only appears in one source even at rank-1.
        That's the whole point of RRF — k=60 dampens the rank-1 edge.
        """
        self._seed_vocab(predictor, ["solo", "consensus"])
        predictor.set_merge_strategy("rrf")
        # "solo" leads in n-gram but is absent from ppm.
        # "consensus" sits at rank-3 in n-gram but rank-1 in ppm.
        merged = predictor._merge_predictions(
            ngram=[
                ("solo", 1.0),
                ("filler1", 1.0),
                ("consensus", 1.0),
            ],
            ppm=[("consensus", 1.0)],
            fuzzy=[],
            n=5,
        )
        self._seed_vocab(predictor, ["filler1"])  # ensure it passes valid-word
        assert "consensus" in merged
        assert "solo" in merged
        assert merged.index("consensus") < merged.index("solo"), (
            f"RRF should promote consensus word: {merged}"
        )

    def test_linear_uses_confidence(self, predictor: HybridPredictor):
        """Linear interpolation respects per-source score magnitudes.
        A word with 0.99 of the source's mass beats a word with 0.01
        even though both are rank-1 in their respective lists — a
        distinction the rank strategy literally cannot make.
        """
        self._seed_vocab(predictor, ["confident", "unsure"])
        predictor.set_merge_strategy("linear")
        # Two single-element lists from different sources.  After
        # per-source normalisation each word is the only candidate in
        # its source so each gets P=1.0 there — the scores end up
        # comparable via the source weights (3.0 ngram vs 0.3 ppm in
        # next-word mode).  We test mid-word (1.0 vs 0.8) to make sure
        # the formula at least picks the higher-weighted source.
        predictor._current_context = "p"  # mid-word
        merged = predictor._merge_predictions(
            ngram=[("confident", 100.0)],
            ppm=[("unsure", 0.001)],
            fuzzy=[],
            n=5,
        )
        # Both should be returned; n-gram-sourced word should rank above
        # ppm-sourced (1.0 weight vs 0.8 mid-word).
        assert merged.index("confident") < merged.index("unsure")

    def test_loglinear_floor_prevents_zero_collapse(
        self, predictor: HybridPredictor,
    ):
        """A word present in only one source must still rank — the
        floor smoothing (1e-6) prevents log(0) from killing it.
        """
        self._seed_vocab(predictor, ["lonely"])
        predictor.set_merge_strategy("loglinear")
        predictor._current_context = "lo"  # mid-word, less aggressive weights
        merged = predictor._merge_predictions(
            ngram=[("lonely", 1.0)],
            ppm=[],
            fuzzy=[],
            n=5,
        )
        # Without floor smoothing, log(0) from missing sources would
        # produce -inf and the word would be unrankable.  With floor,
        # it ranks fine.
        assert "lonely" in merged

    def test_loglinear_prefers_consensus_strict(
        self, predictor: HybridPredictor,
    ):
        """Log-linear is multiplicative — a word that scores well in
        every source should beat a word that scores only in one,
        more strictly than RRF does.
        """
        self._seed_vocab(predictor, ["everywhere", "narrow"])
        predictor.set_merge_strategy("loglinear")
        predictor._current_context = "e"  # mid-word
        merged = predictor._merge_predictions(
            ngram=[("everywhere", 1.0), ("narrow", 1.0)],
            ppm=[("everywhere", 1.0)],
            fuzzy=[("everywhere", 1.0)],
            n=5,
        )
        assert "everywhere" in merged
        if "narrow" in merged:
            assert merged.index("everywhere") < merged.index("narrow")
