"""Tests for the n-gram prediction engine."""

from __future__ import annotations

from pathlib import Path

from src.prediction.ngram_predictor import NgramPredictor


class TestNgramPredictorBasic:
    """Basic prediction behavior."""

    def test_empty_context_returns_top_unigrams(self):
        predictor = NgramPredictor()
        results = predictor.predict("", n=5)
        assert len(results) <= 5
        assert all(isinstance(w, str) for w in results)

    def test_predict_returns_requested_count(self):
        predictor = NgramPredictor()
        for n in (1, 3, 5, 10):
            results = predictor.predict("", n=n)
            assert len(results) <= n

    def test_partial_word_completion(self):
        predictor = NgramPredictor()
        # "th" should match "the", "that", "this", etc.
        results = predictor.predict("th", n=10)
        assert any(w.startswith("th") for w in results)

    def test_partial_word_no_match_returns_empty(self):
        predictor = NgramPredictor()
        results = predictor.predict("zzzzqqqq", n=5)
        assert results == []

    def test_next_word_prediction_after_space(self):
        """Trailing space signals 'predict next word', not 'complete current'."""
        predictor = NgramPredictor()
        # Train heavily so bigram "want -> to" is strong
        for _ in range(10):
            predictor.learn("I want to go home")
        results = predictor.predict("I want ", n=5)
        # "to" should appear as a likely next word after "want"
        assert "to" in results

    def test_case_insensitive(self):
        predictor = NgramPredictor()
        predictor.learn("Hello World")
        results = predictor.predict("hel", n=5)
        assert "hello" in results


class TestNgramLearning:
    """Learning and vocabulary updates."""

    def test_learn_increases_frequency(self):
        predictor = NgramPredictor()
        initial_freq = predictor.unigrams.get("xylophone", 0)
        predictor.learn("xylophone xylophone xylophone")
        assert predictor.unigrams["xylophone"] > initial_freq

    def test_learn_builds_bigrams(self):
        predictor = NgramPredictor()
        predictor.learn("quick brown fox")
        assert "brown" in predictor.bigrams.get("quick", {})
        assert "fox" in predictor.bigrams.get("brown", {})

    def test_learn_builds_trigrams(self):
        predictor = NgramPredictor()
        predictor.learn("the quick brown fox")
        key = "the quick"
        assert "brown" in predictor.trigrams.get(key, {})

    def test_learn_word_boosts_frequency(self):
        predictor = NgramPredictor()
        before = predictor.unigrams.get("zephyr", 0)
        predictor.learn_word("zephyr")
        assert predictor.unigrams["zephyr"] == before + 5

    def test_learn_updates_user_vocab(self):
        predictor = NgramPredictor()
        predictor.learn("bespoke vocabulary")
        assert predictor.user_vocab["bespoke"] > 0
        assert predictor.user_vocab["vocabulary"] > 0

    def test_clear_user_data(self):
        predictor = NgramPredictor()
        predictor.learn("testing clear")
        predictor.clear_user_data()
        assert len(predictor.user_vocab) == 0

    def test_user_vocab_boosts_predictions(self):
        predictor = NgramPredictor()
        # Learn a word many times to boost it
        for _ in range(20):
            predictor.learn_word("zugzwang")
        results = predictor.predict("zugz", n=5)
        assert "zugzwang" in results


class TestNgramRecencyDecay:
    """Recency decay for user-learned frequencies."""

    def test_decay_reduces_user_vocab(self):
        predictor = NgramPredictor()
        predictor.learn("xylophone xylophone xylophone")
        before = predictor.user_vocab["xylophone"]
        predictor._apply_decay()
        after = predictor.user_vocab["xylophone"]
        assert after < before

    def test_decay_removes_low_frequency_words(self):
        predictor = NgramPredictor()
        predictor.user_vocab["rareword"] = 1
        predictor._apply_decay()
        # Frequency 1 * 0.95 = 0, should be removed
        assert "rareword" not in predictor.user_vocab

    def test_decay_triggered_after_interval(self):
        predictor = NgramPredictor()
        predictor._decay_interval = 5  # Small interval for testing
        predictor.learn_word("testword")
        initial = predictor.user_vocab["testword"]
        # Learn enough times to trigger decay
        for i in range(6):
            predictor.learn(f"word{i} sentence here")
        # Decay should have been applied
        assert predictor.user_vocab.get("testword", 0) < initial

    def test_recent_words_outweigh_old_after_decay(self):
        predictor = NgramPredictor()
        predictor._decay_interval = 3  # Trigger frequently
        # Learn "oldword" early
        predictor.learn("oldword oldword oldword")
        # Trigger several decay cycles
        for i in range(12):
            predictor.learn(f"filler{i} text here")
        # Now learn "newword"
        predictor.learn("newword newword newword")
        # newword should have higher user_vocab than oldword (decayed)
        assert predictor.user_vocab["newword"] > predictor.user_vocab.get("oldword", 0)

    def test_decay_preserves_bigrams(self):
        predictor = NgramPredictor()
        predictor.learn("alpha beta")
        predictor._apply_decay()
        # Bigram should still exist (decayed but ≥ 1)
        assert predictor.bigrams["alpha"]["beta"] >= 1


class TestNgramPersistence:
    """Save and load round-trips."""

    def test_save_and_load_preserves_unigrams(self, tmp_model_dir: Path):
        path = tmp_model_dir / "ngram.json"
        predictor = NgramPredictor()
        predictor.learn("alpha beta gamma")
        predictor.save(path)

        loaded = NgramPredictor()
        loaded.load(path)
        assert loaded.unigrams["alpha"] == predictor.unigrams["alpha"]
        assert loaded.unigrams["beta"] == predictor.unigrams["beta"]

    def test_save_and_load_preserves_bigrams(self, tmp_model_dir: Path):
        path = tmp_model_dir / "ngram.json"
        predictor = NgramPredictor()
        predictor.learn("alpha beta gamma")
        predictor.save(path)

        loaded = NgramPredictor()
        loaded.load(path)
        assert loaded.bigrams["alpha"]["beta"] == predictor.bigrams["alpha"]["beta"]

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "nested" / "dir" / "model.json"
        predictor = NgramPredictor()
        predictor.save(path)
        assert path.exists()

    def test_load_nonexistent_file_is_safe(self, tmp_path: Path):
        predictor = NgramPredictor()
        predictor.load(tmp_path / "nope.json")
        # Should not crash, model still usable
        assert predictor.predict("", n=3) is not None

    def test_load_corrupt_file_is_safe(self, tmp_model_dir: Path):
        path = tmp_model_dir / "corrupt.json"
        path.write_text("not valid json {{{{")
        predictor = NgramPredictor()
        predictor.load(path)
        # Should not crash
        assert predictor.predict("", n=3) is not None

    def test_save_load_round_trip_predictions_match(self, tmp_model_dir: Path):
        path = tmp_model_dir / "ngram.json"
        predictor = NgramPredictor()
        predictor.learn("the quick brown fox jumps over the lazy dog")
        original_preds = predictor.predict("the ", n=5)
        predictor.save(path)

        loaded = NgramPredictor()
        loaded.load(path)
        loaded_preds = loaded.predict("the ", n=5)
        assert original_preds == loaded_preds


class TestNgramTokenization:
    """Tokenization edge cases."""

    def test_tokenize_strips_punctuation(self):
        predictor = NgramPredictor()
        tokens = predictor._tokenize("hello, world! it's fine.")
        assert "hello" in tokens
        assert "world" in tokens
        assert "it's" in tokens  # apostrophe preserved

    def test_tokenize_handles_empty(self):
        predictor = NgramPredictor()
        assert predictor._tokenize("") == []

    def test_tokenize_handles_only_punctuation(self):
        predictor = NgramPredictor()
        assert predictor._tokenize("...!!!") == []


class TestNgramStats:
    """Statistics reporting."""

    def test_get_stats_keys(self):
        predictor = NgramPredictor()
        stats = predictor.get_stats()
        assert "total_words" in stats
        assert "unique_words" in stats
        assert "bigrams" in stats
        assert "trigrams" in stats
        assert "user_words" in stats

    def test_stats_update_after_learning(self):
        predictor = NgramPredictor()
        before = predictor.get_stats()["user_words"]
        predictor.learn("unique test words here")
        after = predictor.get_stats()["user_words"]
        assert after > before


class TestCapitalization:
    """Context-aware capitalization (Android/Gboard model)."""

    def test_always_capitalize_i(self):
        predictor = NgramPredictor()
        assert predictor.get_capitalized("i", sentence_start=False) == "I"
        assert predictor.get_capitalized("i", sentence_start=True) == "I"

    def test_always_capitalize_contractions(self):
        predictor = NgramPredictor()
        assert predictor.get_capitalized("i'm", sentence_start=False) == "I'm"
        assert predictor.get_capitalized("i'll", sentence_start=False) == "I'll"
        assert predictor.get_capitalized("i'd", sentence_start=False) == "I'd"
        assert predictor.get_capitalized("i've", sentence_start=False) == "I've"

    def test_ambiguous_name_mid_sentence(self):
        """'will', 'jack', etc. should NOT capitalize mid-sentence."""
        predictor = NgramPredictor()
        assert predictor.get_capitalized("will", sentence_start=False) == "will"
        assert predictor.get_capitalized("jack", sentence_start=False) == "jack"
        assert predictor.get_capitalized("may", sentence_start=False) == "may"
        assert predictor.get_capitalized("mark", sentence_start=False) == "mark"

    def test_ambiguous_name_sentence_start(self):
        """'will', 'jack', etc. SHOULD capitalize at sentence start."""
        predictor = NgramPredictor()
        assert predictor.get_capitalized("will", sentence_start=True) == "Will"
        assert predictor.get_capitalized("jack", sentence_start=True) == "Jack"

    def test_unambiguous_proper_noun_always(self):
        """'Monday', 'Paris' should always capitalize."""
        predictor = NgramPredictor()
        assert predictor.get_capitalized("monday", sentence_start=False) == "Monday"
        assert predictor.get_capitalized("monday", sentence_start=True) == "Monday"

    def test_unknown_word_sentence_start(self):
        """Unknown words capitalize at sentence start only."""
        predictor = NgramPredictor()
        assert predictor.get_capitalized("hello", sentence_start=False) == "hello"
        assert predictor.get_capitalized("hello", sentence_start=True) == "Hello"

    def test_learned_capitalization(self):
        """User-taught capitalization persists."""
        predictor = NgramPredictor()
        predictor.learn_capitalization("iPhone")
        assert predictor.get_capitalized("iphone", sentence_start=False) == "iPhone"
