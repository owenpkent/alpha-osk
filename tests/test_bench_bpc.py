"""The bits-per-character benchmark scores what it claims to score."""

from __future__ import annotations

import importlib
import logging
import math

import pytest

pytest.importorskip("PySide6")

from src.prediction.ppm_predictor import PPMPredictor  # noqa: E402


@pytest.fixture(scope="module")
def bpc():  # type: ignore[no-untyped-def]
    disabled = logging.root.manager.disable
    try:
        return importlib.import_module("scripts.bench.bpc")
    finally:
        logging.disable(disabled)


@pytest.fixture(scope="module")
def abab_model() -> PPMPredictor:
    model = PPMPredictor(max_order=4, alphabet="ab ")
    model.train("ab" * 200)
    return model


def _bpc(bpc, model: PPMPredictor, text: str) -> float:  # type: ignore[no-untyped-def]
    bits, scored, _, _ = bpc.score_text(model, [text])
    assert scored > 0
    return bits / scored


def test_matched_text_scores_far_below_uniform(bpc, abab_model) -> None:  # type: ignore[no-untyped-def]
    uniform = math.log2(len(abab_model.alphabet))
    assert _bpc(bpc, abab_model, "abababab") < uniform / 2


def test_scrambled_text_scores_worse_than_matched(bpc, abab_model) -> None:  # type: ignore[no-untyped-def]
    matched = _bpc(bpc, abab_model, "abababab")
    scrambled = _bpc(bpc, abab_model, "aabbbaab")
    assert scrambled > matched * 2


def test_unknown_characters_are_skipped_and_counted(bpc, abab_model) -> None:  # type: ignore[no-untyped-def]
    _, scored, unknown, zeros = bpc.score_text(abab_model, ["ab#ab"])
    assert (scored, unknown, zeros) == (4, 1, 0)


def test_zero_probability_is_floored_and_counted(bpc) -> None:  # type: ignore[no-untyped-def]
    class Zero:
        alphabet = {"a"}

        def get_probabilities(self, context: str) -> dict[str, float]:
            return {"a": 0.0}

    bits, scored, _, zeros = bpc.score_text(Zero(), ["a"], floor=0.5)  # type: ignore[arg-type]
    assert (scored, zeros) == (1, 1)
    assert bits == pytest.approx(1.0)


def test_sentences_seen_in_training_are_excluded(bpc, abab_model) -> None:  # type: ignore[no-untyped-def]
    train = "ab ab\nba ba"
    sents = ["ab ab", "ab ba"]
    assert bpc.seen_in_training(sents, train) == {"ab ab"}
    excluded = bpc.evaluate(abab_model, "x", sents, train)
    kept = bpc.evaluate(abab_model, "x", sents, train, keep_seen=True)
    assert (excluded.sentences, kept.sentences) == (1, 2)
    assert excluded.sentences_seen_in_training == 1
