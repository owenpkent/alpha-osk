"""Tests for training data quality and loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.prediction.ngram_predictor import NgramPredictor

DATA_DIR = Path(__file__).parent.parent / "data"


class TestBaseDictionary:
    """Verify base_dictionary.txt format prevents fake bigrams."""

    def test_file_exists(self):
        assert (DATA_DIR / "base_dictionary.txt").exists()

    def test_no_multi_word_lines(self):
        """Each non-comment line is either a single word or ``word count``.

        The two-token ``word count`` form bypasses ``_learn_base`` (so
        no fake bigrams are created — see test_loading_does_not_create_bigrams)
        and is used for entries that need a frequency higher than the
        +1 the default loader path provides (e.g. contractions).
        """
        path = DATA_DIR / "base_dictionary.txt"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                # Allow either:  "word"  or  "word <integer>"
                if len(parts) == 2 and parts[1].isdigit():
                    continue
                assert len(parts) == 1, (
                    f"Line {i} has multiple words: {line!r}. "
                    "Use 'word' or 'word <count>' to avoid fake bigrams."
                )

    def test_loading_does_not_create_bigrams(self):
        """Loading the dictionary should only boost unigrams, not create bigrams."""
        predictor = NgramPredictor()
        # Clear any bigrams from the wordlist
        predictor.bigrams.clear()
        predictor.load_base_dictionary()
        # Single-word-per-line means no bigrams should be created
        assert sum(len(v) for v in predictor.bigrams.values()) == 0

    def test_common_words_are_present(self):
        predictor = NgramPredictor()
        predictor.load_base_dictionary()
        for word in ["the", "is", "you", "have", "want", "help", "computer"]:
            assert predictor.unigrams[word] > 0, f"Missing word: {word}"


class TestTrainingCorpus:
    """Verify training_corpus.txt quality."""

    def test_file_exists(self):
        assert (DATA_DIR / "training_corpus.txt").exists()

    def test_corpus_has_enough_lines(self):
        path = DATA_DIR / "training_corpus.txt"
        with open(path) as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        # Should have substantial training data
        assert len(lines) >= 400, f"Only {len(lines)} lines — need at least 400"

    def test_corpus_covers_key_topics(self):
        """Spot-check that corpus covers diverse domains."""
        path = DATA_DIR / "training_corpus.txt"
        text = path.read_text().lower()
        topics = {
            "greetings": "hello",
            "questions": "where",
            "work": "meeting",
            "technology": "wifi",
            "accessibility": "keyboard",
            "emotions": "happy",
            "casual": "lol",
        }
        for topic, keyword in topics.items():
            assert keyword in text, f"Corpus missing {topic} coverage (keyword: {keyword})"

    def test_corpus_builds_meaningful_bigrams(self):
        predictor = NgramPredictor()
        path = DATA_DIR / "training_corpus.txt"
        text = path.read_text()
        lines = [
            line.strip() for line in text.split("\n") if line.strip() and not line.startswith("#")
        ]
        for line in lines:
            predictor.learn(line)
        # "I want" -> "to" should be a strong bigram
        assert predictor.bigrams["i"]["want"] > 0
        assert predictor.bigrams["want"]["to"] > 0


class TestCommonBigrams:
    """Verify common_bigrams.txt."""

    def test_file_exists(self):
        assert (DATA_DIR / "common_bigrams.txt").exists()

    def test_loading_creates_bigrams(self):
        predictor = NgramPredictor()
        predictor.bigrams.clear()
        result = predictor.load_common_bigrams()
        assert result is True
        # Should have loaded many bigrams
        total = sum(len(v) for v in predictor.bigrams.values())
        assert total >= 100

    def test_key_bigrams_present(self):
        predictor = NgramPredictor()
        predictor.bigrams.clear()
        predictor.load_common_bigrams()
        assert predictor.bigrams["i"]["am"] > 0
        assert predictor.bigrams["thank"]["you"] > 0
        assert predictor.bigrams["how"]["are"] > 0


class TestCommonTrigrams:
    """Verify common_trigrams.txt."""

    def test_file_exists(self):
        assert (DATA_DIR / "common_trigrams.txt").exists()

    def test_loading_creates_trigrams(self):
        predictor = NgramPredictor()
        predictor.trigrams.clear()
        result = predictor.load_common_trigrams()
        assert result is True
        total = sum(len(v) for v in predictor.trigrams.values())
        assert total >= 50

    def test_key_trigrams_present(self):
        predictor = NgramPredictor()
        predictor.trigrams.clear()
        predictor.load_common_trigrams()
        assert predictor.trigrams["i want"]["to"] > 0
        assert predictor.trigrams["i need"]["to"] > 0
        assert predictor.trigrams["how are"]["you"] > 0

    def test_trigram_loading_also_reinforces_bigrams(self):
        predictor = NgramPredictor()
        predictor.bigrams.clear()
        predictor.load_common_trigrams()
        # "i want to" trigram should also create "i->want" and "want->to" bigrams
        assert predictor.bigrams["i"]["want"] > 0
        assert predictor.bigrams["want"]["to"] > 0

    def test_trigram_format_is_valid(self):
        """Each non-comment line should have at least 3 words."""
        path = DATA_DIR / "common_trigrams.txt"
        with open(path) as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                assert len(parts) >= 3, f"Line {i} has fewer than 3 words: {line!r}"

    def test_missing_file_returns_false(self, tmp_path: Path):
        predictor = NgramPredictor()
        result = predictor.load_common_trigrams(tmp_path / "nope.txt")
        assert result is False


class TestPredictionQuality:
    """End-to-end prediction quality with all training data loaded."""

    @pytest.fixture
    def trained_predictor(self) -> NgramPredictor:
        predictor = NgramPredictor()
        predictor.load_base_dictionary()
        predictor.load_common_bigrams()
        predictor.load_common_trigrams()
        # Also train on corpus
        path = DATA_DIR / "training_corpus.txt"
        if path.exists():
            text = path.read_text()
            lines = [ln.strip() for ln in text.split("\n") if ln.strip() and not ln.startswith("#")]
            for line in lines:
                predictor.learn(line)
        return predictor

    def test_next_word_after_i_want(self, trained_predictor: NgramPredictor):
        results = trained_predictor.predict("i want ", n=5)
        assert "to" in results

    def test_next_word_after_how_are(self, trained_predictor: NgramPredictor):
        results = trained_predictor.predict("how are ", n=5)
        assert "you" in results

    def test_next_word_after_thank(self, trained_predictor: NgramPredictor):
        results = trained_predictor.predict("thank ", n=5)
        assert "you" in results

    def test_completion_of_hel(self, trained_predictor: NgramPredictor):
        results = trained_predictor.predict("hel", n=5)
        assert "hello" in results or "help" in results

    def test_next_word_after_i_need_to(self, trained_predictor: NgramPredictor):
        results = trained_predictor.predict("i need to ", n=10)
        # Should predict common words after "need to"
        assert len(results) > 0
