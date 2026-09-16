"""Regression coverage for ``NgramPredictor.load()`` value hardening.

A Data Backup archive is a zip the user picked off disk, and the model
file inside it is untrusted input despite living in the config
directory: ``load()`` used to validate only a count table's *keys*
(``_is_plausible_word``) and store whatever value sat next to them, so a
crafted ``{"unigrams": {"hello": "boom"}}`` loaded without complaint and
then blew up the first arithmetic that touched it -- ``learn()``'s
``self.unigrams[word] += 1``, ``predict()``'s unigram sort -- well after
the app looked like it had started cleanly. These tests drive that
crafted file through both ``NgramPredictor`` and ``HybridPredictor`` and
assert the poison is dropped on load rather than merely deferred to the
next keystroke.

Note on ``HybridPredictor``: what reproduces the reported crash is
``HybridPredictor.__init__`` -> ``load_base_dictionary`` /
``_load_training_corpus`` -> ``NgramPredictor.load_corpus_prior`` and
``learn_corpus_context``, which is the path a poisoned
``unigrams["hello"]`` used to take down (each wrapped in its own
try/except, so construction "succeeded" while quietly logging an ERROR
and leaving the corpus half-trained). That is the path exercised below.
The corpus prior never writes ``unigrams`` (it lives in
``_corpus_unigrams``, see CLAUDE.md *Shipped corpus prior*), so the
proof that it ran to completion is the prior itself.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.prediction.ngram_predictor import NgramPredictor

# Written as raw text rather than json.dumps(...): the payload needs a
# bare NaN and a JSON `true`, the same extension-grammar gap
# (json.load accepts NaN/Infinity by default) a hand-crafted archive
# would lean on. Every table load() reads from the file gets at least
# one poisoned entry paired with a valid one, so a fix that drops a
# whole table instead of one entry would still fail these.
_CRAFTED_MODEL_JSON = """
{
  "unigrams": {
    "hello": "boom",
    "world": true,
    "friend": NaN,
    "enemy": -3,
    "valid": 42,
    "float_valid": 7.5
  },
  "user_vocab": {
    "mine": 10,
    "poisoned": "boom",
    "negative": -1
  },
  "total_words": "boom",
  "dispreference": {"bad": "boom", "good": 2},
  "preferred": {"bad": [1, 2], "good": 3},
  "blacklist_type_count": {"bad": NaN, "good": 1},
  "candidate_counts": {"bad": true, "good": 4},
  "blacklist": ["spam", 5, null, "junk"]
}
"""

# Words a bare arithmetic bug would poison quietly: "hello" is a real
# word ("hello" -> "boom") so it takes the *known-word* branch inside
# learn(), which is what makes it able to trip `self.unigrams[word] += 1`
# immediately rather than sitting in the unknown-word candidate pool.
_POISONED_WORDS = ("hello", "world", "friend", "enemy", "poisoned", "negative", "bad")


def _write_crafted_model(directory: Path) -> Path:
    model_path = directory / "ngram_model.json"
    model_path.write_text(_CRAFTED_MODEL_JSON, encoding="utf-8")
    return model_path


class TestPoisonedCountsAreDroppedNotStored:
    """Case 1: a bad value costs its own entry, a good one in the same
    table survives -- across every count table load() populates."""

    def test_unigrams_drops_each_bad_shape_and_keeps_the_good_ones(self, tmp_path: Path) -> None:
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        assert "hello" not in predictor.unigrams  # string count
        assert "world" not in predictor.unigrams  # bool count
        assert "friend" not in predictor.unigrams  # NaN count
        assert "enemy" not in predictor.unigrams  # negative count
        assert predictor.unigrams["valid"] == 42  # plain valid int survives
        assert predictor.unigrams["float_valid"] == 7  # valid float survives too

    def test_user_vocab_drops_bad_values_and_keeps_the_good_one(self, tmp_path: Path) -> None:
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        assert "poisoned" not in predictor.user_vocab
        assert "negative" not in predictor.user_vocab
        assert predictor.user_vocab["mine"] == 10

    def test_the_other_count_tables_drop_only_their_bad_entries(self, tmp_path: Path) -> None:
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        assert "bad" not in predictor.dispreference
        assert predictor.dispreference["good"] == 2
        assert "bad" not in predictor.preferred
        assert predictor.preferred["good"] == 3
        assert "bad" not in predictor._blacklist_type_count
        assert predictor._blacklist_type_count["good"] == 1
        assert "bad" not in predictor._candidate_counts
        assert predictor._candidate_counts["good"] == 4

    def test_blacklist_keeps_only_the_string_entries(self, tmp_path: Path) -> None:
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        assert predictor.blacklist == {"spam", "junk"}

    def test_total_words_falls_back_to_the_cleaned_unigram_mass(self, tmp_path: Path) -> None:
        # "total_words" itself is the string "boom" in the crafted file;
        # the only entries left in unigrams after cleaning are
        # valid=42 and float_valid=int(7.5)=7.
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        assert predictor.total_words == 49


class TestThePoisonedWordBehavesLikeAnUnknownWord:
    """Case 2: the engine stays usable on the very next keystroke.

    Pre-fix, ``predictor.learn("hello ")`` raised
    ``TypeError: can only concatenate str (not "int") to str`` at
    ``self.unigrams[word] += 1``, and ``predictor.predict("", n=5)``
    raised ``TypeError: bad operand type for unary -: 'str'`` sorting
    unigrams by frequency -- both reproduced against the unfixed loader
    before this test was written.
    """

    def test_learning_the_poisoned_word_does_not_raise(self, tmp_path: Path) -> None:
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        predictor.learn("hello ")
        # "hello" is a known base-dictionary word (Google 10K), so it
        # takes the immediate-learn branch rather than the 3-sighting
        # gate: its count was dropped on load, so this starts over at 1
        # instead of touching a poisoned value.
        assert predictor.unigrams["hello"] == 1

    def test_predicting_with_no_context_does_not_raise(self, tmp_path: Path) -> None:
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        results = predictor.predict("", n=5)
        assert isinstance(results, list)

    def test_predicting_a_partial_word_does_not_raise(self, tmp_path: Path) -> None:
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        results = predictor.predict("hel", n=5)
        assert isinstance(results, list)


class TestHybridPredictorLoadsOverAPoisonedModel:
    """Case 3: HybridPredictor construction survives a poisoned model file
    and the corpus / base dictionary loads it triggers do not silently
    fail. See the module docstring for why this checks
    ``load_base_dictionary`` / ``_load_training_corpus`` rather than a
    "corpus prior" attribute that does not exist in this checkout.
    """

    def test_construction_logs_no_error_and_still_trains(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        pytest.importorskip("PySide6")
        from src.prediction.hybrid_predictor import HybridPredictor

        model_dir = tmp_path / "models"
        model_dir.mkdir()
        _write_crafted_model(model_dir)

        with caplog.at_level(logging.WARNING):
            hybrid = HybridPredictor(model_dir=model_dir, enable_llm=False)

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert not error_records, [r.getMessage() for r in error_records]

        # "hello" appears in data/training_corpus.txt and is a known
        # Google-10K word, so if load_base_dictionary /
        # _load_training_corpus ran to completion (instead of dying on
        # the poisoned entry load() used to hand them), the corpus prior
        # holds it with a positive count and the model knows the word,
        # rather than the prior staying empty.
        assert hybrid._ngram._corpus_unigrams.get("hello", 0) > 0
        assert hybrid._ngram.in_vocabulary("hello")
        assert hybrid._ngram.total_words > 0


class TestUserTotalInvariantSurvivesAMixedFile:
    """Case 4: ``_user_total == sum(user_vocab.values())`` after loading a
    file whose ``user_vocab`` mixes valid and invalid values."""

    def test_user_total_matches_the_cleaned_vocab(self, tmp_path: Path) -> None:
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        assert predictor._user_total == sum(predictor.user_vocab.values())

    def test_the_invariant_still_holds_after_further_learning(self, tmp_path: Path) -> None:
        predictor = NgramPredictor(_write_crafted_model(tmp_path))
        predictor.learn("mine mine another")
        assert predictor._user_total == sum(predictor.user_vocab.values())


class TestTheWarningNamesACountNotAWord:
    """Case 5: the dropped-entry log record carries a number, never a key.

    Typed content (which a learned word is, once it has been through
    the user's own keystrokes) must never reach the log at WARNING or
    above -- see CLAUDE.md's "diagnostic log" rules.
    """

    def test_the_warning_reports_a_count_and_never_a_dropped_word(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            NgramPredictor(_write_crafted_model(tmp_path))

        dropped_count_records = [
            r.getMessage() for r in caplog.records if "invalid count" in r.getMessage()
        ]
        assert dropped_count_records, "expected at least one dropped-count warning"
        for message in dropped_count_records:
            assert any(ch.isdigit() for ch in message), message
            for word in _POISONED_WORDS:
                assert word not in message, message

    def test_a_clean_file_logs_no_dropped_count_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        clean_path = tmp_path / "clean.json"
        clean_path.write_text(json.dumps({"unigrams": {"tidy": 3}}), encoding="utf-8")
        with caplog.at_level(logging.WARNING):
            NgramPredictor(clean_path)
        assert not [r for r in caplog.records if "invalid count" in r.getMessage()]
