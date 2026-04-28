"""Tests for the fuzzy/spatial recognition engine."""

from __future__ import annotations

from src.prediction.fuzzy_recognizer import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_PREDICTION_WEIGHT,
    DEFAULT_SPATIAL_UNCERTAINTY,
    QWERTY_POSITIONS,
    FuzzyRecognizer,
    FuzzyWordGenerator,
    SpatialKeyModel,
)


class TestQWERTYLayout:
    """Verify the QWERTY position map is consistent."""

    def test_all_letters_present(self):
        import string
        for letter in string.ascii_lowercase:
            assert letter in QWERTY_POSITIONS, f"Missing key: {letter}"

    def test_positions_are_tuples(self):
        for key, pos in QWERTY_POSITIONS.items():
            assert isinstance(pos, tuple) and len(pos) == 2

    def test_rows_are_valid(self):
        for key, (row, col) in QWERTY_POSITIONS.items():
            assert 0 <= row <= 2, f"Key {key} has invalid row {row}"
            assert col >= 0, f"Key {key} has negative col {col}"


class TestDefaults:
    """The hardcoded constants that replaced the profile system."""

    def test_spatial_uncertainty_is_generous(self):
        # Larger than the original "Normal" profile's 1.0 — covers
        # diagonal neighbours so a near-miss surfaces the right word.
        assert DEFAULT_SPATIAL_UNCERTAINTY >= 1.2

    def test_confidence_threshold_in_sane_range(self):
        assert 0.5 <= DEFAULT_CONFIDENCE_THRESHOLD <= 0.9

    def test_prediction_weight_in_sane_range(self):
        assert 0.3 <= DEFAULT_PREDICTION_WEIGHT <= 0.9


class TestSpatialKeyModel:
    """Spatial probability model for key presses."""

    def test_clicked_key_has_highest_prob(self):
        model = SpatialKeyModel(uncertainty_radius=1.0)
        probs = model.get_key_probabilities("f")
        assert probs["f"] == max(probs.values())

    def test_probabilities_sum_to_one(self):
        model = SpatialKeyModel(uncertainty_radius=1.5)
        probs = model.get_key_probabilities("g")
        total = sum(probs.values())
        assert abs(total - 1.0) < 0.01

    def test_unknown_key_returns_certainty(self):
        model = SpatialKeyModel(uncertainty_radius=1.0)
        probs = model.get_key_probabilities("1")  # Not in QWERTY layout
        assert probs == {"1": 1.0}

    def test_nearby_keys_are_included(self):
        model = SpatialKeyModel(uncertainty_radius=1.5)
        probs = model.get_key_probabilities("f")
        # d, g, r, v are adjacent to f
        assert "d" in probs or "g" in probs

    def test_distant_keys_excluded(self):
        model = SpatialKeyModel(uncertainty_radius=0.5)
        probs = model.get_key_probabilities("a")
        # 'p' is far from 'a' — should not appear with small radius
        assert "p" not in probs

    def test_get_nearby_keys(self):
        model = SpatialKeyModel(uncertainty_radius=1.5)
        nearby = model.get_nearby_keys("f")
        assert "f" in nearby  # Key itself
        assert len(nearby) > 1  # Plus neighbors

    def test_set_uncertainty_rebuilds_cache(self):
        model = SpatialKeyModel(uncertainty_radius=0.5)
        small_neighbors = len(model.get_nearby_keys("f"))
        model.set_uncertainty_radius(2.5)
        large_neighbors = len(model.get_nearby_keys("f"))
        assert large_neighbors >= small_neighbors

    def test_zero_uncertainty_returns_only_self(self):
        model = SpatialKeyModel(uncertainty_radius=0.01)
        probs = model.get_key_probabilities("f")
        assert len(probs) == 1
        assert "f" in probs


class TestFuzzyWordGenerator:
    """Fuzzy word candidate generation."""

    def test_empty_input_returns_empty(self, small_dictionary: set):
        gen = FuzzyWordGenerator(dictionary=small_dictionary)
        assert gen.generate_candidates("") == []

    def test_exact_match_returns_word(self, small_dictionary: set):
        gen = FuzzyWordGenerator(
            spatial_model=SpatialKeyModel(uncertainty_radius=1.0),
            dictionary=small_dictionary,
        )
        candidates = gen.generate_candidates("the")
        words = [w for w, _ in candidates]
        assert "the" in words

    def test_nearby_typo_generates_correction(self, small_dictionary: set):
        gen = FuzzyWordGenerator(
            spatial_model=SpatialKeyModel(uncertainty_radius=1.5),
            dictionary=small_dictionary,
        )
        # 'r' is next to 't', so "rhe" might correct to "the"
        candidates = gen.generate_candidates("rhe")
        words = [w for w, _ in candidates]
        assert "the" in words

    def test_candidates_are_sorted_by_probability(self, small_dictionary: set):
        gen = FuzzyWordGenerator(
            spatial_model=SpatialKeyModel(uncertainty_radius=1.5),
            dictionary=small_dictionary,
        )
        candidates = gen.generate_candidates("the")
        probs = [p for _, p in candidates]
        assert probs == sorted(probs, reverse=True)

    def test_max_candidates_respected(self, small_dictionary: set):
        gen = FuzzyWordGenerator(
            spatial_model=SpatialKeyModel(uncertainty_radius=2.0),
            dictionary=small_dictionary,
            max_candidates=3,
        )
        candidates = gen.generate_candidates("the")
        assert len(candidates) <= 3

    def test_get_correction_returns_none_for_valid_word(self, small_dictionary: set):
        gen = FuzzyWordGenerator(dictionary=small_dictionary)
        assert gen.get_correction("the") is None

    def test_get_correction_returns_candidate_for_typo(self, small_dictionary: set):
        gen = FuzzyWordGenerator(
            spatial_model=SpatialKeyModel(uncertainty_radius=1.5),
            dictionary=small_dictionary,
        )
        result = gen.get_correction("rhe")
        if result is not None:
            word, prob = result
            assert isinstance(word, str)
            assert prob > 0


class TestEditDistanceCandidates:
    """Edit-distance variants (transposition / deletion / insertion)."""

    def test_transposition_finds_word(self, small_dictionary: dict):
        gen = FuzzyWordGenerator(dictionary=small_dictionary)
        # "teh" → swap positions 1 and 2 → "the"
        words = [w for w, _ in gen.generate_candidates("teh")]
        assert "the" in words

    def test_deletion_finds_word(self, small_dictionary: dict):
        gen = FuzzyWordGenerator(dictionary=small_dictionary)
        # "thee" → drop final 'e' → "the"
        words = [w for w, _ in gen.generate_candidates("thee")]
        assert "the" in words

    def test_insertion_finds_word(self, small_dictionary: dict):
        gen = FuzzyWordGenerator(dictionary=small_dictionary)
        # "th" → insert 'e' at end → "the"
        words = [w for w, _ in gen.generate_candidates("th")]
        assert "the" in words

    def test_transposition_skips_no_op_swap(self, small_dictionary: dict):
        # "hheelp" has duplicate adjacent chars — swapping shouldn't
        # claim "hheelp" as a candidate.
        gen = FuzzyWordGenerator(dictionary=small_dictionary)
        # Doesn't matter what's returned, just that it doesn't crash.
        gen.generate_candidates("hheellp")

    def test_no_edits_for_single_char(self, small_dictionary: dict):
        gen = FuzzyWordGenerator(dictionary=small_dictionary)
        # Length-1 input → edit-distance path returns nothing.
        assert gen._edit_distance_candidates("h") == []

    def test_insertion_skipped_for_long_input(self, small_dictionary: dict):
        # Inputs over 12 chars skip the 26 × N insertion enumeration
        # to keep per-keystroke cost bounded.
        gen = FuzzyWordGenerator(dictionary=small_dictionary)
        results = gen._edit_distance_candidates("a" * 13)
        # No insertions should appear (all candidates would be length 14).
        for word, _ in results:
            assert len(word) <= 13

    def test_common_word_beats_rare_word_via_frequency(self):
        # "teh" → transposition could map to "the" or to a rarer word;
        # frequency multiplier ensures the common word wins.
        dictionary = {"the": 1000.0, "tha": 1.0}
        gen = FuzzyWordGenerator(dictionary=dictionary)
        candidates = gen.generate_candidates("teh")
        assert candidates[0][0] == "the"

    def test_apostrophe_insertion_finds_contraction(self):
        # "im" → insert ' at position 1 → "i'm".  Apostrophe insertion
        # gets a higher per-edit probability than the generic letter
        # insertion path because missing apostrophes are by far the
        # most common insertion error in real typing.
        dictionary = {"i'm": 8000.0, "him": 100.0, "aim": 50.0}
        gen = FuzzyWordGenerator(dictionary=dictionary)
        candidates = gen.generate_candidates("im")
        words = [w for w, _ in candidates]
        assert "i'm" in words
        # Should rank competitively (top 3) thanks to the boosted
        # apostrophe-insertion prob + high frequency.
        assert words.index("i'm") < 3

    def test_apostrophe_insertion_beats_letter_insertion(self):
        # Same input, two equally-frequent candidates: "i'm" via
        # apostrophe insertion vs "him" via letter insertion. The
        # apostrophe path should win because of the higher edit prob.
        dictionary = {"i'm": 1000.0, "him": 1000.0}
        gen = FuzzyWordGenerator(dictionary=dictionary)
        candidates = gen.generate_candidates("im")
        assert candidates[0][0] == "i'm"


class TestFuzzyRecognizer:
    """Main fuzzy recognizer interface."""

    def test_uses_default_constants(self):
        rec = FuzzyRecognizer()
        assert rec.spatial_uncertainty == DEFAULT_SPATIAL_UNCERTAINTY
        assert rec.confidence_threshold == DEFAULT_CONFIDENCE_THRESHOLD
        assert rec.prediction_weight == DEFAULT_PREDICTION_WEIGHT

    def test_spatial_model_uses_default_radius(self):
        rec = FuzzyRecognizer()
        assert rec.spatial_model.uncertainty_radius == DEFAULT_SPATIAL_UNCERTAINTY

    def test_get_key_alternatives(self):
        rec = FuzzyRecognizer()
        alts = rec.get_key_alternatives("f")
        assert isinstance(alts, dict)
        assert "f" in alts

    def test_autocorrect_returns_none_for_valid_word(self, small_dictionary: set):
        rec = FuzzyRecognizer(dictionary=small_dictionary)
        # "the" is valid → no correction.
        assert rec.should_autocorrect("the") is None

    def test_typed_baseline_implausible_returns_zero(self):
        """Implausible shapes (no vowel, or no consonant, or empty)
        get baseline 0 so the relative-margin gate doesn't apply —
        only the absolute confidence threshold guards them."""
        assert FuzzyRecognizer._typed_baseline("") == 0.0
        assert FuzzyRecognizer._typed_baseline("xqz") == 0.0
        assert FuzzyRecognizer._typed_baseline("aaa") == 0.0
        assert FuzzyRecognizer._typed_baseline("thx") == 0.0

    def test_typed_baseline_plausible_returns_log1p_one(self):
        import math
        # Plausible shape (vowel + consonant) → rare-real-word
        # baseline used by the relative-margin check.
        assert FuzzyRecognizer._typed_baseline("hello") == math.log1p(1)
        assert FuzzyRecognizer._typed_baseline("thru") == math.log1p(1)
        # 'y' counts as both vowel and consonant.
        assert FuzzyRecognizer._typed_baseline("cry") == math.log1p(1)

    def test_should_autocorrect_relative_margin_blocks_borderline(self):
        """A correction that clears the absolute threshold but only
        marginally beats the typed word's baseline should NOT fire —
        the user might have meant the typed letters."""
        rec = FuzzyRecognizer()
        # Stub get_correction to return a controlled (word, score).
        rec.word_generator.get_correction = (  # type: ignore[method-assign]
            lambda word, ctx="": ("the", 1.0)
        )
        # "thru" is plausible → baseline ≈ 0.69 → threshold ≈ 1.04.
        # confidence 1.0 < 1.04 → blocked.
        assert rec.should_autocorrect("thru") is None

    def test_should_autocorrect_relative_margin_passes_clear_winner(self):
        rec = FuzzyRecognizer()
        rec.word_generator.get_correction = (  # type: ignore[method-assign]
            lambda word, ctx="": ("the", 5.0)
        )
        # 5.0 > 0.69 * 1.5 = 1.04 → fires.
        assert rec.should_autocorrect("thru") == "the"

    def test_should_autocorrect_implausible_typed_skips_relative_gate(self):
        """Random-letter inputs (no vowel/consonant balance) get
        baseline 0, so only the absolute confidence threshold gates
        them — corrections of obvious slop ('xqz' → 'the') still
        fire as long as they clear the absolute bar."""
        rec = FuzzyRecognizer()
        rec.word_generator.get_correction = (  # type: ignore[method-assign]
            lambda word, ctx="": ("the", 0.8)
        )
        # 0.8 ≥ 0.65 absolute, baseline 0 → relative gate trivially
        # passes.
        assert rec.should_autocorrect("xqz") == "the"

    def test_get_fuzzy_predictions_empty_text(self):
        rec = FuzzyRecognizer()
        assert rec.get_fuzzy_predictions("") == []

    def test_get_fuzzy_predictions_after_space(self, small_dictionary: set):
        rec = FuzzyRecognizer(dictionary=small_dictionary)
        # Trailing space = no current word
        assert rec.get_fuzzy_predictions("hello ") == []

    def test_get_stats(self):
        rec = FuzzyRecognizer()
        stats = rec.get_stats()
        assert "spatial_uncertainty" in stats
        assert "confidence_threshold" in stats
        assert "prediction_weight" in stats
        assert "dictionary_size" in stats
