"""Tests for the fuzzy/spatial recognition engine."""

from __future__ import annotations

import json
import string
from pathlib import Path

from src.prediction.fuzzy_recognizer import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_PREDICTION_WEIGHT,
    DEFAULT_SPATIAL_UNCERTAINTY,
    QWERTY_POSITIONS,
    FuzzyRecognizer,
    FuzzyWordGenerator,
    SpatialKeyModel,
    positions_from_layout,
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
        # Row -1 is the digit row above qwerty (added so 5↔t-style
        # off-by-one-row mistypes are recoverable).
        for key, (row, col) in QWERTY_POSITIONS.items():
            assert -1 <= row <= 2, f"Key {key} has invalid row {row}"
            assert col >= 0, f"Key {key} has negative col {col}"

    def test_all_digits_present(self):
        # Number row was added so spatial fuzzy can recover digit↔letter
        # off-row presses ("h3llo" → "hello"). Without these entries,
        # SpatialKeyModel.get_key_probabilities returns {digit: 1.0} and
        # the candidate generator can't substitute anything for the
        # mistyped digit.
        for digit in "0123456789":
            assert digit in QWERTY_POSITIONS, f"Missing digit: {digit}"


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
        # Punctuation isn't mapped — fuzzy correction stays out of
        # punctuation entirely (different error mode, different fix).
        probs = model.get_key_probabilities("?")
        assert probs == {"?": 1.0}

    def test_digit_neighbours_letter_below(self):
        # The point of adding the number row: pressing "5" when you
        # meant "t" (or vice versa) should be recoverable. They're
        # vertically aligned (row -1 vs row 0, same column 4).
        model = SpatialKeyModel(uncertainty_radius=1.5)
        nearby_5 = model.get_nearby_keys("5")
        assert "t" in nearby_5, f"'5' should neighbour 't', got {nearby_5}"
        nearby_t = model.get_nearby_keys("t")
        assert "5" in nearby_t, f"'t' should neighbour '5', got {nearby_t}"

    def test_digit_neighbours_horizontal_digits(self):
        # Same-row digit neighbours come for free from the distance
        # metric (4 and 6 are adjacent to 5 in the digit row).
        model = SpatialKeyModel(uncertainty_radius=1.5)
        nearby_5 = model.get_nearby_keys("5")
        assert "4" in nearby_5 and "6" in nearby_5

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


class TestPositionsDerivedFromALayout:
    """``positions_from_layout`` against the shipped layout JSON.

    The bug these guard is that ``SpatialKeyModel`` fell back to
    ``QWERTY_POSITIONS`` unconditionally and nothing ever passed it
    anything else, so Dvorak and Colemak users were autocorrected
    against a keyboard they were not typing on.
    """

    @staticmethod
    def _rows(name: str) -> list:
        path = Path(__file__).parent.parent / "data" / "layouts" / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))["rows"]

    def test_qwerty_json_reproduces_the_hardcoded_table(self):
        # The whole reason the derivation is slot-based rather than
        # width-based: moving the QWERTY user onto this path has to be
        # a no-op for them, and "close enough" is not checkable.
        assert positions_from_layout(self._rows("qwerty")) == QWERTY_POSITIONS

    def test_dvorak_puts_p_in_the_slot_qwerty_gives_r(self):
        # Dvorak's top row is ' , . p y f g c r l, so p is the fourth
        # physical key.  Counting only the letters would put it at
        # column 0 and hand Dvorak users a model wrong in a new way.
        dvorak = positions_from_layout(self._rows("dvorak"))
        assert dvorak["p"] == QWERTY_POSITIONS["r"]

    def test_punctuation_holds_its_slot_without_entering_the_model(self):
        # The inverse half of the test above: the three punctuation
        # keys are what push p to column 3, and they must not
        # themselves become correctable keys (punctuation has a
        # different error mode, which is why QWERTY_POSITIONS omits it).
        dvorak = positions_from_layout(self._rows("dvorak"))
        assert "'" not in dvorak
        assert "," not in dvorak
        assert "." not in dvorak

    def test_dvorak_neighbours_are_dvorak_neighbours(self):
        # The behavioural point.  On Dvorak, e sits between u and o;
        # on QWERTY it sits between w and r.  A model that still
        # answered w/r here would pass every structural test above.
        model = SpatialKeyModel(positions=positions_from_layout(self._rows("dvorak")))
        nearby = set(model.get_nearby_keys("e"))
        assert {"u", "o"} <= nearby
        assert "w" not in nearby
        assert "r" not in nearby

    def test_the_digit_row_sits_directly_over_the_letters(self):
        # The number row carries a leading backtick, which on a
        # key-index rule would push every digit one column right of the
        # letter it is meant to sit above, so 5 would stop being over t.
        qwerty = positions_from_layout(self._rows("qwerty"))
        assert qwerty["1"] == (-1.0, 0.0)
        assert qwerty["5"][1] == qwerty["t"][1]
        assert "`" not in qwerty

    def test_a_layout_with_no_number_row_starts_at_the_letter_row(self):
        # Compact keeps its digits on the sym layer, so its first row
        # is the top letter row and must not be read as a digit row
        # shifted up to -1.
        compact = positions_from_layout(self._rows("qwerty-compact"))
        assert compact["q"] == (0.0, 0.0)
        assert compact["a"] == QWERTY_POSITIONS["a"]
        assert compact["z"] == QWERTY_POSITIONS["z"]

    def test_rows_on_another_layer_are_ignored(self):
        # Compact's digits are on sym, where they replace the letters
        # rather than sitting above them, so they are not spatially
        # adjacent to anything and must not enter the model.
        compact = positions_from_layout(self._rows("qwerty-compact"))
        assert not any(c.isdigit() for c in compact)

    def test_every_letter_survives_every_shipped_layout(self):
        for name in ("qwerty", "dvorak", "colemak", "qwerty-compact"):
            derived = positions_from_layout(self._rows(name))
            missing = set(string.ascii_lowercase) - set(derived)
            assert not missing, f"{name} lost {sorted(missing)}"

    def test_a_layout_with_no_character_keys_yields_nothing(self):
        assert positions_from_layout([{"keys": [{"type": "special", "action": "escape"}]}]) == {}


class TestSwitchingLayoutRebuildsTheSpatialModel:
    """``FuzzyRecognizer.set_key_positions``."""

    def test_the_word_generator_sees_the_new_grid(self):
        # The trap this exists for: FuzzyWordGenerator holds its own
        # reference to the model, so re-pointing only the recogniser
        # leaves candidate generation running on the old positions
        # while every accessor reports the new ones.
        recognizer = FuzzyRecognizer()
        recognizer.set_key_positions({"a": (0.0, 0.0), "b": (0.0, 1.0)})
        assert recognizer.word_generator.spatial_model is recognizer.spatial_model
        assert set(recognizer.word_generator.spatial_model.positions) == {"a", "b"}

    def test_an_empty_grid_is_ignored(self):
        # A layout with no alphanumeric keys is one the spatial model
        # has nothing to say about.  Blanking it would silently
        # disable autocorrect rather than leaving the previous layout
        # in place.
        recognizer = FuzzyRecognizer()
        before = dict(recognizer.spatial_model.positions)
        recognizer.set_key_positions({})
        assert recognizer.spatial_model.positions == before
