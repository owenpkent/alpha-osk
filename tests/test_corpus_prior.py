"""Regression coverage for the shipped corpus's in-memory unigram prior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PySide6 = pytest.importorskip("PySide6")

from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402
from src.prediction.ngram_predictor import NgramPredictor  # noqa: E402


def test_corpus_prior_does_not_enter_user_history():
    predictor = NgramPredictor()
    predictor.load_corpus_prior("zzqprior zzqprior zzqprior")

    assert predictor._corpus_unigrams["zzqprior"] == 3
    assert predictor.user_vocab.get("zzqprior", 0) == 0
    assert predictor._candidate_counts == {}
    assert predictor._learn_count == 0


def test_corpus_prior_unknown_word_keeps_three_sighting_gate():
    predictor = NgramPredictor()
    predictor.load_corpus_prior("zzqprior zzqprior")

    assert "zzqprior" not in predictor._corpus_unigrams
    assert "zzqprior" not in predictor.unigrams
    assert predictor._candidate_counts == {}


def test_accepted_corpus_word_is_known_to_typing_and_pill_paths():
    predictor = NgramPredictor()
    predictor.load_corpus_prior("zzqaccepted zzqaccepted zzqaccepted zzqrejected")

    predictor.learn("zzqaccepted zzqrejected")
    predictor.learn_from_pill_click("zzqaccepted")
    predictor.learn_from_pill_click("zzqrejected")

    assert predictor.user_vocab["zzqaccepted"] == 6
    assert predictor._candidate_counts["zzqrejected"] == 2
    assert predictor.user_vocab.get("zzqrejected", 0) == 0


def test_corpus_prior_words_are_prediction_candidates():
    predictor = NgramPredictor()
    predictor.load_corpus_prior("zzqprior zzqprior zzqprior")

    assert "zzqprior" in predictor.predict("zzq", n=5)
    count = predictor._effective_typing_count("zzqprior")
    total = predictor._effective_typing_total()
    assert count == pytest.approx(0.3)
    assert total == pytest.approx(0.3)


def test_hybrid_startup_keeps_corpus_out_of_user_history(tmp_path: Path):
    predictor = HybridPredictor(model_dir=tmp_path / "model", enable_llm=False)

    assert predictor._ngram._corpus_total > 0
    assert predictor._ngram.user_vocab == {}
    assert predictor._ngram._user_total == 0
    assert predictor._ngram._candidate_counts == {}


def test_separating_the_prior_keeps_initial_predictions(tmp_path: Path):
    predictor = HybridPredictor(model_dir=tmp_path / "model", enable_llm=False)
    contexts = ("", "w", "ww", "the ", "i want ", "can we ", "docu", "hwllo")
    expected = {context: predictor.predict(context) for context in contexts}
    frequencies = {word: predictor._fuzzy_frequency(word) for word in ("we", "bamboo", "hello")}

    # Recreate the old startup state: the corpus was counted as user typing.
    ngram = predictor._ngram
    ngram.user_vocab.update(ngram._corpus_unigrams)
    ngram._user_total = ngram._corpus_total
    ngram._corpus_unigrams.clear()
    ngram._corpus_total = 0

    assert {context: predictor.predict(context) for context in contexts} == expected
    assert {word: predictor._fuzzy_frequency(word) for word in frequencies} == pytest.approx(
        frequencies
    )


def test_fuzzy_frequency_includes_corpus_prior_without_user_count(tmp_path: Path, monkeypatch):
    corpus = "zzqfuzzy zzqfuzzy zzqfuzzy"
    monkeypatch.setattr(
        HybridPredictor,
        "_read_training_corpus",
        staticmethod(lambda: corpus),
    )

    predictor = HybridPredictor(model_dir=tmp_path / "model", enable_llm=False)

    assert predictor._ngram.user_vocab.get("zzqfuzzy", 0) == 0
    assert predictor._fuzzy_frequency("zzqfuzzy") > 0


def test_one_selection_can_outrank_repeated_shipped_examples():
    predictor = NgramPredictor()
    predictor.load_corpus_prior("can " * 30 + "could " * 20 + "call " * 10)
    assert predictor.predict("c")[0] == "can"

    predictor.learn_from_pill_click("cello")

    assert predictor.predict("c")[0] == "cello"
    assert predictor.user_vocab == {"cello": 5}


def test_hybrid_save_restart_does_not_duplicate_user_counts(tmp_path: Path):
    model_dir = tmp_path / "model"
    first = HybridPredictor(model_dir=model_dir, enable_llm=False)
    first.learn_word("zzqpersonal")
    before = dict(first._ngram.user_vocab)
    before_total = first._ngram._user_total
    first.save()

    second = HybridPredictor(model_dir=model_dir, enable_llm=False)

    assert dict(second._ngram.user_vocab) == before
    assert second._ngram._user_total == before_total
    assert second._ngram._user_total == sum(second._ngram.user_vocab.values())


def test_reload_restores_missing_corpus_candidate_once(tmp_path: Path, monkeypatch):
    corpus = "zzqrestore zzqrestore zzqrestore"
    monkeypatch.setattr(
        HybridPredictor,
        "_read_training_corpus",
        staticmethod(lambda: corpus),
    )
    model_dir = tmp_path / "model"
    predictor = HybridPredictor(model_dir=model_dir, enable_llm=False)
    total = predictor._ngram._corpus_total
    predictor._ngram.unigrams.pop("zzqrestore", None)
    predictor.save()

    predictor.reload_from_disk()

    assert predictor._ngram._corpus_total == total
    assert predictor._ngram.unigrams["zzqrestore"] == 3


def test_clear_rebuilds_corpus_prior_with_ppm_disabled(tmp_path: Path, monkeypatch):
    corpus = "zzqclear zzqclear zzqclear"
    monkeypatch.setattr(
        HybridPredictor,
        "_read_training_corpus",
        staticmethod(lambda: corpus),
    )
    predictor = HybridPredictor(model_dir=tmp_path / "model", enable_llm=False)
    predictor._enable_ppm = False
    predictor.learn_word("zzqpersonal")

    predictor.clear_user_data()

    assert predictor._ngram._corpus_unigrams["zzqclear"] == 3
    assert predictor._ngram.unigrams["zzqclear"] == 3
    assert predictor._ngram.user_vocab.get("zzqpersonal", 0) == 0


def test_reload_rebuilds_prior_from_replacement_user_history(tmp_path: Path, monkeypatch):
    corpus = "hello hello zzqformer"
    monkeypatch.setattr(
        HybridPredictor,
        "_read_training_corpus",
        staticmethod(lambda: corpus),
    )
    clean = HybridPredictor(model_dir=tmp_path / "clean", enable_llm=False)
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    model_path = model_dir / "ngram_model.json"
    learned = NgramPredictor()
    learned.learn_word("zzqformer")
    learned.save(model_path)
    predictor = HybridPredictor(model_dir=model_dir, enable_llm=False)
    assert predictor._ngram._corpus_unigrams["zzqformer"] == 1
    assert "zzqformer" in predictor._fuzzy.word_generator.dictionary

    # Backup import replaces the snapshot before the live model reloads it.
    clean._ngram.save(model_path)
    predictor.reload_from_disk()

    assert predictor._ngram.user_vocab == {}
    assert predictor._ngram._corpus_unigrams == clean._ngram._corpus_unigrams
    assert predictor._ngram._corpus_total == clean._ngram._corpus_total
    assert "zzqformer" not in predictor._ngram.unigrams
    assert "zzqformer" not in predictor._fuzzy.word_generator.dictionary
    assert "zzqformer" not in predictor.predict("zzqform")


def test_legacy_user_count_is_not_reinterpreted(tmp_path: Path, monkeypatch):
    corpus = "zzqlegacy zzqlegacy zzqlegacy"
    monkeypatch.setattr(
        HybridPredictor,
        "_read_training_corpus",
        staticmethod(lambda: corpus),
    )
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    (model_dir / "ngram_model.json").write_text(
        json.dumps(
            {
                "unigrams": {"zzqlegacy": 7},
                "user_vocab": {"zzqlegacy": 7},
                "total_words": 7,
            }
        )
    )

    predictor = HybridPredictor(model_dir=model_dir, enable_llm=False)

    assert predictor._ngram.user_vocab["zzqlegacy"] == 7
    assert predictor._ngram._corpus_unigrams["zzqlegacy"] == 3
