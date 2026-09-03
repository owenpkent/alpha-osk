"""The click's position inside the key reaches the fuzzy beam, and the bias is learned.

``KeyButton.qml`` has always had ``mouse.x / mouse.y`` at the press and
used it for the ripple only.  It now hands the bridge the click's offset
within the key (fractions of the key's width and height from its centre)
with the character; the bridge keeps those parallel to the word it is
typing and passes them, when they line up, to the prefix beam, which
turns them into continuous positions with the learned per-slot bias taken
out.  Each test here drives one hop of that path, and the recognizer
tests are paired with the near-miss the rule must leave alone.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.keyboard_bridge import KeyboardBridge
from src.prediction.fuzzy_recognizer import QWERTY_POSITIONS, FuzzyRecognizer
from src.prediction.ngram_predictor import NgramPredictor
from src.prediction.pointer_model import slot_id
from src.prediction.token_predictor import TokenPredictor
from src.snippets import SnippetStore

DATA = Path(__file__).resolve().parents[1] / "data" / "google-10000-english-usa-no-swears.txt"


@pytest.fixture(scope="module")
def fr() -> FuzzyRecognizer:
    recognizer = FuzzyRecognizer()
    assert recognizer.load_dictionary(DATA)
    ngram = NgramPredictor()
    ngram.load_base_dictionary()
    recognizer.set_frequencies(ngram.unigrams)
    return recognizer


def top(fr: FuzzyRecognizer, typed: str, **kw) -> list[str]:
    return [w for w, _ in fr.get_fuzzy_predictions(typed, 5, **kw)]


@pytest.fixture
def bridge(tmp_path: Path) -> KeyboardBridge:
    """A bridge with a mocked synthesizer and its writable stores unplugged.

    The same shape as the fixture in ``test_keyboard_bridge.py``, for the
    same reasons given there: the bridge reads the developer's own config
    directory on construction and must not write to it.
    """
    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        b = KeyboardBridge()
        b._synth = synth
        b._snippets = SnippetStore(tmp_path / "snippets.json")
        b._snippets.load()
        b._predictor._ngram.tokens = TokenPredictor()
        return b


class TestTheRecognizerResolvesOffsets:
    def test_a_click_on_the_edge_of_h_toward_g_turns_hood_into_good(self, fr):
        # h and g are one key apart; a click on h's left edge is equidistant,
        # and the commoner word wins the tie.
        assert top(fr, "hood")[0] == "hood"
        assert top(fr, "hood", offsets=[(-0.5, 0.0), None, None, None])[0] == "good"

    def test_a_click_dead_centre_changes_nothing(self, fr):
        assert top(fr, "hood", offsets=[(0.0, 0.0)] * 4) == top(fr, "hood")

    def test_positions_for_resolves_against_the_spatial_model(self, fr):
        positions = fr.positions_for("ha!", [(0.25, -0.25), None, (0.1, 0.1)])
        h = QWERTY_POSITIONS["h"]
        assert positions[0] == pytest.approx((h[0] - 0.25, h[1] + 0.25))
        assert positions[1] is None  # no offset supplied
        assert positions[2] is None  # "!" has no slot

    def test_the_learned_bias_is_taken_out_before_scoring(self):
        # A controlled dictionary, so the outcome is geometry rather than
        # whatever the shipped counts happen to be.  A press 0.1 right of h's
        # centre stays "hood".  Once the model has learned that this pointer
        # lands 0.7 of a key right of where it aims, the same press corrects
        # to 0.6 left of h's centre, past the midpoint to g, where spatial
        # evidence and frequency both favour "good" at any emission width.
        def fresh() -> FuzzyRecognizer:
            return FuzzyRecognizer(dictionary={"hood": 3000.0, "good": 9000.0, "hoods": 100.0})

        press = [(0.1, 0.0), None, None, None]
        assert top(fresh(), "hood", offsets=press)[0] == "hood"
        biased = fresh()
        for _ in range(50):
            biased.observe_press("h", 0.7, 0.0)
        assert top(biased, "hood", offsets=press)[0] == "good"

    def test_unmapped_characters_are_not_observed(self):
        recognizer = FuzzyRecognizer()
        recognizer.observe_press("!", 0.3, 0.3)
        recognizer.observe_press(" ", 0.3, 0.3)
        assert len(recognizer.pointer) == 0
        recognizer.observe_press("H", 0.3, 0.3)  # case is not a different key
        assert len(recognizer.pointer) == 1
        assert recognizer.pointer.bias(slot_id(QWERTY_POSITIONS["h"]))[0] > 0


class TestTheBridgeKeepsOffsetsParallelToTheWord:
    def test_a_press_records_its_offset(self, bridge: KeyboardBridge):
        bridge.pressKey("h", 0.25, -0.1)
        bridge.pressKey("o")
        assert bridge._current_word == "ho"
        assert bridge._word_offsets == [("h", 0.25, -0.1), ("o", 0.0, 0.0)]

    def test_backspace_pops_and_a_reset_clears(self, bridge: KeyboardBridge):
        bridge.pressKey("h", 0.25, -0.1)
        bridge.pressKey("o", 0.1, 0.1)
        bridge.pressSpecialKey("backspace")
        assert bridge._word_offsets == [("h", 0.25, -0.1)]
        bridge._reset_typing_context()
        assert bridge._word_offsets == []

    def test_offsets_only_travel_when_they_line_up_with_the_word(
        self, bridge: KeyboardBridge, monkeypatch
    ):
        seen: list = []
        monkeypatch.setattr(
            bridge._predictor,
            "predict_with_refinement",
            lambda context, n=5, offsets=None: seen.append(offsets) or [],
        )
        bridge.pressKey("h", 0.25, -0.1)
        assert seen[-1] == [(0.25, -0.1)]
        # a path that rewrites the word without touching the list
        bridge._current_word = "hello"
        bridge._update_predictions()
        assert seen[-1] is None
        # and the next press re-syncs rather than staying broken
        bridge.pressKey("w", 0.1, 0.0)
        assert seen[-1] == [(0.0, 0.0)] * 5 + [(0.1, 0.0)]

    def test_a_rewritten_word_of_the_same_length_does_not_inherit_them(
        self, bridge: KeyboardBridge, monkeypatch
    ):
        # Typed `hwllo`, tapped the `hello` pill, backspaced into it: five
        # letters, five offsets, four of them under the wrong letter.  A
        # length check let that through; autocorrect has the same shape.
        seen: list = []
        monkeypatch.setattr(
            bridge._predictor,
            "predict_with_refinement",
            lambda context, n=5, offsets=None: seen.append(offsets) or [],
        )
        typed = [(0.1, 0.0), (0.2, 0.0), (0.3, 0.0), (0.4, 0.0), (0.5, 0.0)]
        for ch, (dx, dy) in zip("hwllo", typed):
            bridge.pressKey(ch, dx, dy)
        assert seen[-1] == typed
        bridge._predictions = ["hello"]
        bridge.pressPrediction("hello")
        bridge.pressSpecialKey("backspace")
        assert bridge._current_word == "hello"
        bridge._update_predictions()
        assert seen[-1] is None

    def test_backspacing_into_the_word_as_it_was_typed_keeps_them(
        self, bridge: KeyboardBridge, monkeypatch
    ):
        # The inverse: the same letters under the same clicks are still
        # the user's own presses, so they travel.
        seen: list = []
        monkeypatch.setattr(
            bridge._predictor,
            "predict_with_refinement",
            lambda context, n=5, offsets=None: seen.append(offsets) or [],
        )
        typed = [(0.1, 0.0), (0.2, 0.0), (0.3, 0.0), (0.4, 0.0), (0.5, 0.0)]
        for ch, (dx, dy) in zip("hello", typed):
            bridge.pressKey(ch, dx, dy)
        bridge.pressSpecialKey("space")
        bridge.pressSpecialKey("backspace")
        assert bridge._current_word == "hello"
        bridge._update_predictions()
        assert seen[-1] == typed

    def test_the_literal_path_carries_the_offset_too(self, bridge: KeyboardBridge):
        bridge.pressKeyLiteral("A", 0.3, 0.2)
        assert bridge._word_offsets == [("A", 0.3, 0.2)]

    def test_a_press_is_observed_for_the_bias_outside_privacy_mode_only(
        self, bridge: KeyboardBridge
    ):
        pointer = bridge._predictor._fuzzy.pointer
        before = len(pointer)
        bridge.pressKey("h", 0.3, 0.0)
        assert len(pointer) == before + 1
        bridge.setPrivacyMode(True)
        bridge.pressKey("h", 0.3, 0.0)
        assert len(pointer) == before + 1

    def test_the_bias_table_is_the_one_the_ngram_persists(self, bridge: KeyboardBridge):
        assert bridge._predictor._fuzzy.pointer is bridge._predictor._ngram.pointer


class TestPersistence:
    def test_the_table_rides_in_the_model_file(self, tmp_path):
        p = NgramPredictor()
        p.pointer.observe("1,5.25", 0.2, 0.1)
        p.save(tmp_path / "m.json")
        q = NgramPredictor()
        q.load(tmp_path / "m.json")
        assert q.pointer.bias("1,5.25") == pytest.approx(p.pointer.bias("1,5.25"))

    def test_clear_learned_data_forgets_it(self):
        p = NgramPredictor()
        pointer = p.pointer
        pointer.observe("1,5.25", 0.2, 0.1)
        p.clear_user_data()
        assert len(p.pointer) == 0
        assert p.pointer is pointer  # mutated in place, never rebound
