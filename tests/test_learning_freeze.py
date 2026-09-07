"""Tests for the learning freeze on src/prediction/hybrid_predictor.py.

See docs/research/STUDY_PROTOCOL.md section 5.2: without a freeze, the
prediction-on block trains the model that same block is scored on, which
inflates that condition specifically and does so more the later the block
runs. The freeze has to cover the n-gram tables, the pointer-bias model,
the capitalisation table and the token store, and it has to restore
cleanly even when the study harness raises partway through, because a
participant's own keyboard must never come out of a session permanently
unable to learn.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# HybridPredictor extends QObject; skip this whole module where PySide6 is
# unavailable, the same guard tests/test_hybrid_predictor.py uses.
PySide6 = pytest.importorskip("PySide6")

from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402


@pytest.fixture
def predictor(tmp_model_dir: Path) -> HybridPredictor:
    """A real HybridPredictor over a temp model dir, LLM disabled.

    Mirrors the fixture in tests/test_hybrid_predictor.py rather than
    stubbing anything: the freeze has to be checked against the real
    n-gram tables, not a mock that would pass no matter what the gate did.
    """
    return HybridPredictor(model_dir=tmp_model_dir, enable_llm=False)


def _promote_to_known(predictor: HybridPredictor, word: str) -> None:
    """Push *word* past the 3-sighting candidate gate.

    Once a word is in ``user_vocab`` every later ``learn()`` call for it
    takes the deterministic "known word" branch (an unconditional +1),
    rather than the candidate-gate branch, which only promotes every
    third sighting. Priming the word this way keeps the state-change
    assertions below independent of whether the shipped base dictionary
    happens to already contain the word.
    """
    for _ in range(3):
        predictor.learn(word)


class TestLearningFrozenDefaultsToFalse:
    def test_a_fresh_predictor_is_not_frozen(self, predictor: HybridPredictor) -> None:
        assert predictor.learning_frozen is False


class TestLearnIsGatedByTheFreeze:
    def test_frozen_learn_returns_no_new_words_and_moves_nothing(
        self, predictor: HybridPredictor
    ) -> None:
        _promote_to_known(predictor, "zorblatt")
        before = predictor._ngram.user_vocab.get("zorblatt", 0)

        with predictor.frozen_learning():
            result = predictor.learn("zorblatt")

        assert result == []
        assert predictor._ngram.user_vocab.get("zorblatt", 0) == before

    def test_thawed_learn_still_moves_the_vocabulary(self, predictor: HybridPredictor) -> None:
        """The paired inverse: without this half, a freeze implemented as
        "break everything" (e.g. always returning early) would pass the
        frozen-case test above too.
        """
        _promote_to_known(predictor, "zorblatt")
        before = predictor._ngram.user_vocab.get("zorblatt", 0)

        predictor.learn("zorblatt")

        assert predictor._ngram.user_vocab.get("zorblatt", 0) == before + 1


class TestLearnWordIsGatedByTheFreeze:
    def test_frozen_learn_word_moves_nothing(self, predictor: HybridPredictor) -> None:
        before = predictor._ngram.user_vocab.get("gizmoword", 0)

        with predictor.frozen_learning():
            result = predictor.learn_word("gizmoword")

        assert result is None
        assert predictor._ngram.user_vocab.get("gizmoword", 0) == before

    def test_thawed_learn_word_still_boosts_the_word(self, predictor: HybridPredictor) -> None:
        predictor.learn_word("gizmoword")
        assert predictor._ngram.user_vocab.get("gizmoword", 0) == 5


class TestUnlearnWordIsGatedByTheFreeze:
    def test_frozen_unlearn_word_returns_false_and_moves_nothing(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.learn_word("gadgetword")  # seed it into user_vocab, unfrozen
        before = predictor._ngram.user_vocab.get("gadgetword", 0)

        with predictor.frozen_learning():
            result = predictor.unlearn_word("gadgetword")

        assert result is False
        assert predictor._ngram.user_vocab.get("gadgetword", 0) == before

    def test_thawed_unlearn_word_still_retracts_the_sighting(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.learn_word("gadgetword")
        before = predictor._ngram.user_vocab.get("gadgetword", 0)

        result = predictor.unlearn_word("gadgetword")

        assert result is True
        assert predictor._ngram.user_vocab.get("gadgetword", 0) == before - 1


class TestLearnTokenIsGatedByTheFreeze:
    def test_frozen_learn_token_returns_false_and_moves_nothing(
        self, predictor: HybridPredictor
    ) -> None:
        before = predictor._ngram.tokens.tokens.get("90210", 0)

        with predictor.frozen_learning():
            result = predictor.learn_token("90210")

        assert result is False
        assert predictor._ngram.tokens.tokens.get("90210", 0) == before

    def test_thawed_learn_token_still_records_the_token(self, predictor: HybridPredictor) -> None:
        result = predictor.learn_token("90210")
        assert result is True
        assert predictor._ngram.tokens.tokens.get("90210", 0) == 1


class TestLearnFromSelectionIsGatedByTheFreeze:
    def test_frozen_call_touches_neither_the_word_nor_the_context_edge(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.learn_word("selectedword")  # known word: deterministic reinforce branch
        before_vocab = predictor._ngram.user_vocab.get("selectedword", 0)
        before_bigram = predictor._ngram.bigrams.get("typing", {}).get("selectedword", 0)

        with predictor.frozen_learning():
            result = predictor.learn_from_selection("typing", "selectedword")

        assert result is None
        assert predictor._ngram.user_vocab.get("selectedword", 0) == before_vocab
        assert predictor._ngram.bigrams.get("typing", {}).get("selectedword", 0) == before_bigram

    def test_thawed_call_still_reinforces_the_word_and_the_context_edge(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.learn_word("selectedword")
        before_vocab = predictor._ngram.user_vocab.get("selectedword", 0)
        before_bigram = predictor._ngram.bigrams.get("typing", {}).get("selectedword", 0)

        predictor.learn_from_selection("typing", "selectedword")

        assert predictor._ngram.user_vocab.get("selectedword", 0) > before_vocab
        assert predictor._ngram.bigrams.get("typing", {}).get("selectedword", 0) > before_bigram


class TestMarkGoodSuggestionIsGatedByTheFreeze:
    def test_frozen_call_boosts_nothing(self, predictor: HybridPredictor) -> None:
        before_vocab = predictor._ngram.user_vocab.get("boostedword", 0)
        before_pref = predictor._ngram.preferred.get("boostedword", 0)

        with predictor.frozen_learning():
            result = predictor.mark_good_suggestion("boostedword")

        assert result is None
        assert predictor._ngram.user_vocab.get("boostedword", 0) == before_vocab
        assert predictor._ngram.preferred.get("boostedword", 0) == before_pref

    def test_thawed_call_still_boosts_the_word(self, predictor: HybridPredictor) -> None:
        predictor.mark_good_suggestion("boostedword")
        assert predictor._ngram.user_vocab.get("boostedword", 0) == 5
        assert predictor._ngram.preferred.get("boostedword", 0) == 5


class TestLearnCapitalizationIsGatedByTheFreeze:
    def test_frozen_call_returns_false_and_learns_nothing(self, predictor: HybridPredictor) -> None:
        with predictor.frozen_learning():
            result = predictor.learn_capitalization("myWord")

        assert result is False
        assert "myword" not in predictor._ngram.capitalization

    def test_thawed_call_still_learns_the_casing(self, predictor: HybridPredictor) -> None:
        result = predictor.learn_capitalization("myWord")
        assert result is True
        assert predictor._ngram.capitalization["myword"] == "myWord"


class TestSetCapitalizationIsGatedByTheFreeze:
    def test_frozen_call_writes_nothing(self, predictor: HybridPredictor) -> None:
        with predictor.frozen_learning():
            result = predictor.set_capitalization("ignored", "Zibble")

        assert result is None
        assert "zibble" not in predictor._ngram.capitalization

    def test_thawed_call_still_writes_the_preferred_casing(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.set_capitalization("ignored", "Zibble")
        assert predictor._ngram.capitalization["zibble"] == "Zibble"


class TestFrozenLearningRestoresOnExit:
    def test_the_previous_value_is_restored_after_the_block(
        self, predictor: HybridPredictor
    ) -> None:
        assert predictor.learning_frozen is False
        with predictor.frozen_learning():
            assert predictor.learning_frozen is True
        assert predictor.learning_frozen is False

    def test_the_previous_value_is_restored_even_when_the_body_raises(
        self, predictor: HybridPredictor
    ) -> None:
        """A study session that throws part way through must not leave the
        participant's own keyboard permanently unable to learn, with
        nothing on screen to say so. contextmanager's try/finally is what
        this guards.
        """
        with pytest.raises(RuntimeError):
            with predictor.frozen_learning():
                assert predictor.learning_frozen is True
                raise RuntimeError("study harness blew up")

        assert predictor.learning_frozen is False
        # and the gate is really thawed, not just the flag report:
        before = predictor._ngram.user_vocab.get("afterthecrash", 0)
        predictor.learn_word("afterthecrash")
        assert predictor._ngram.user_vocab.get("afterthecrash", 0) == before + 5


class TestFrozenLearningNests:
    def test_an_inner_exit_does_not_thaw_the_outer_block(self, predictor: HybridPredictor) -> None:
        assert predictor.learning_frozen is False
        with predictor.frozen_learning():
            assert predictor.learning_frozen is True
            with predictor.frozen_learning():
                assert predictor.learning_frozen is True
            # the inner block exited; the outer freeze must still hold
            assert predictor.learning_frozen is True
        assert predictor.learning_frozen is False


class TestPredictionStillWorksWhileFrozen:
    def test_predict_still_returns_suggestions_while_frozen(
        self, predictor: HybridPredictor
    ) -> None:
        """The freeze is about mutation only. This is the test that would
        catch someone implementing it by disabling the engine instead of
        gating the writes: none of the state-comparison tests above ever
        call predict(), so a freeze that silently blanked predictions
        would pass every one of them.
        """
        predictor.learn("the cat sat on the mat")  # unfrozen: seed a known word

        with predictor.frozen_learning():
            results = predictor.predict("th", n=5)

        assert isinstance(results, list)
        assert any(w.startswith("th") for w in results)
