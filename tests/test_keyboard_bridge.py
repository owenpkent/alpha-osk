"""Tests for the KeyboardBridge (Python↔QML bridge).

These tests verify modifier state management, context tracking,
and prediction wiring. Actual key synthesis is mocked since we
don't want tests injecting real keystrokes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

PySide6 = pytest.importorskip("PySide6")

from src.keyboard_bridge import KeyboardBridge


@pytest.fixture
def bridge() -> KeyboardBridge:
    """Create a KeyboardBridge with mocked synthesizer."""
    with patch("src.keyboard_bridge.create_key_synthesizer") as mock_factory:
        mock_synth = MagicMock()
        mock_synth.is_available.return_value = True
        mock_synth.backend_name.return_value = "MockSynth"
        mock_factory.return_value = mock_synth
        b = KeyboardBridge()
        b._synth = mock_synth
        return b


class TestModifierState:
    """Modifier key state machine."""

    def test_initial_state(self, bridge: KeyboardBridge):
        assert not bridge._shift_active
        assert not bridge._caps_lock_active
        assert not bridge._ctrl_active
        assert not bridge._alt_active
        assert not bridge._win_active

    def test_toggle_shift(self, bridge: KeyboardBridge):
        bridge.toggleShift()
        assert bridge._shift_active
        bridge.toggleShift()
        assert not bridge._shift_active

    def test_toggle_caps_lock(self, bridge: KeyboardBridge):
        bridge.toggleCapsLock()
        assert bridge._caps_lock_active
        assert bridge._shift_active  # Caps activates shift

    def test_caps_lock_off_clears_shift(self, bridge: KeyboardBridge):
        bridge.toggleCapsLock()  # On
        bridge.toggleCapsLock()  # Off
        assert not bridge._caps_lock_active
        assert not bridge._shift_active

    def test_toggle_ctrl(self, bridge: KeyboardBridge):
        bridge.toggleCtrl()
        assert bridge._ctrl_active
        bridge.toggleCtrl()
        assert not bridge._ctrl_active

    def test_toggle_alt(self, bridge: KeyboardBridge):
        bridge.toggleAlt()
        assert bridge._alt_active

    def test_toggle_win(self, bridge: KeyboardBridge):
        bridge.toggleWin()
        assert bridge._win_active

    def test_shift_auto_releases_after_key(self, bridge: KeyboardBridge):
        bridge.toggleShift()
        bridge.pressKey("a")
        assert not bridge._shift_active  # Auto-released

    def test_caps_lock_persists_after_key(self, bridge: KeyboardBridge):
        bridge.toggleCapsLock()
        bridge.pressKey("a")
        assert bridge._caps_lock_active  # Still active

    def test_ctrl_auto_releases_after_key(self, bridge: KeyboardBridge):
        bridge.toggleCtrl()
        bridge.pressKey("c")
        assert not bridge._ctrl_active

    def test_alt_auto_releases_after_key(self, bridge: KeyboardBridge):
        bridge.toggleAlt()
        bridge.pressKey("f")
        assert not bridge._alt_active

    def test_ctrl_auto_releases_after_special_key(self, bridge: KeyboardBridge):
        bridge.toggleCtrl()
        bridge.pressSpecialKey("backspace")
        assert not bridge._ctrl_active


class TestLayerManagement:
    """Keyboard layer switching."""

    def test_initial_layer_is_lower(self, bridge: KeyboardBridge):
        assert bridge._current_layer == "lower"

    def test_shift_changes_to_upper(self, bridge: KeyboardBridge):
        bridge.toggleShift()
        assert bridge._current_layer == "upper"

    def test_shift_off_returns_to_lower(self, bridge: KeyboardBridge):
        bridge.toggleShift()
        bridge.toggleShift()
        assert bridge._current_layer == "lower"

    def test_switch_layer_explicit(self, bridge: KeyboardBridge):
        bridge.switchLayer("numbers")
        assert bridge._current_layer == "numbers"

    def test_shift_doesnt_change_numbers_layer(self, bridge: KeyboardBridge):
        bridge.switchLayer("numbers")
        bridge.toggleShift()
        assert bridge._current_layer == "numbers"  # Should not change


class TestKeyPress:
    """Key press handling."""

    def test_press_lowercase(self, bridge: KeyboardBridge):
        bridge.pressKey("a")
        bridge._synth.send_text.assert_called_with("a")

    def test_press_uppercase_with_shift(self, bridge: KeyboardBridge):
        bridge.toggleShift()
        bridge.pressKey("a")
        bridge._synth.send_text.assert_called_with("A")

    def test_press_with_ctrl_uses_send_key(self, bridge: KeyboardBridge):
        bridge.toggleCtrl()
        bridge.pressKey("c")
        bridge._synth.send_key.assert_called_once()

    def test_press_special_key(self, bridge: KeyboardBridge):
        bridge.pressSpecialKey("backspace")
        bridge._synth.send_key.assert_called_with("BackSpace", modifiers=None)

    def test_press_space(self, bridge: KeyboardBridge):
        bridge.pressSpecialKey("space")
        bridge._synth.send_key.assert_called()


class TestContextTracking:
    """Context buffer for prediction."""

    def test_initial_context_empty(self, bridge: KeyboardBridge):
        assert bridge._context_buffer == ""
        assert bridge._current_word == ""

    def test_pressing_key_builds_word(self, bridge: KeyboardBridge):
        bridge.pressKey("h")
        bridge.pressKey("i")
        assert bridge._current_word == "hi"

    def test_space_completes_word(self, bridge: KeyboardBridge):
        bridge.pressKey("h")
        bridge.pressKey("i")
        bridge.pressSpecialKey("space")
        assert bridge._current_word == ""
        assert "hi" in bridge._context_buffer

    def test_backspace_removes_char(self, bridge: KeyboardBridge):
        bridge.pressKey("h")
        bridge.pressKey("e")
        bridge.pressKey("l")
        bridge.pressSpecialKey("backspace")
        assert bridge._current_word == "he"

    def test_return_preserves_context(self, bridge: KeyboardBridge):
        bridge.pressKey("h")
        bridge.pressKey("i")
        bridge.pressSpecialKey("space")
        bridge.pressKey("t")
        bridge.pressSpecialKey("return")
        # Context preserved across lines, not wiped
        assert "hi" in bridge._context_buffer
        assert bridge._current_word == ""

    def test_return_resets_sentence_buffer(self, bridge: KeyboardBridge):
        bridge.pressKey("h")
        bridge.pressKey("i")
        bridge.pressSpecialKey("space")
        bridge.pressSpecialKey("return")
        assert bridge._sentence_buffer == ""

    def test_context_buffer_bounded(self, bridge: KeyboardBridge):
        # Simulate many words to exceed buffer limit (now 200 chars)
        for _ in range(50):
            for c in "hello":
                bridge.pressKey(c)
            bridge.pressSpecialKey("space")
        assert len(bridge._context_buffer) <= 200


class TestPredictionWiring:
    """Prediction integration."""

    def test_predictions_property_is_list(self, bridge: KeyboardBridge):
        assert isinstance(bridge._predictions, list)

    def test_llm_disabled_by_default(self, bridge: KeyboardBridge):
        # Bridge creates predictor with enable_llm=False
        assert not bridge._predictor.enable_llm

    def test_prediction_count_default(self, bridge: KeyboardBridge):
        assert bridge._prediction_count == 8

    def test_set_prediction_count(self, bridge: KeyboardBridge):
        bridge.setPredictionCount(3)
        assert bridge._prediction_count == 3

    def test_prediction_count_clamped(self, bridge: KeyboardBridge):
        bridge.setPredictionCount(100)
        assert bridge._prediction_count == 10
        bridge.setPredictionCount(0)
        assert bridge._prediction_count == 1


class TestDebugLog:
    """Debug logging."""

    def test_debug_mode_off_by_default(self, bridge: KeyboardBridge):
        assert not bridge._debug_mode

    def test_set_debug_mode(self, bridge: KeyboardBridge):
        bridge.setDebugMode(True)
        assert bridge._debug_mode

    def test_debug_log_entries(self, bridge: KeyboardBridge):
        bridge._add_debug_log("test message")
        log = bridge.getDebugLog()
        assert any("test message" in entry for entry in log)

    def test_debug_log_capped(self, bridge: KeyboardBridge):
        for i in range(200):
            bridge._add_debug_log(f"entry {i}")
        assert len(bridge._debug_log) <= 100

    def test_clear_debug_log(self, bridge: KeyboardBridge):
        bridge._add_debug_log("something")
        bridge.clearDebugLog()
        assert len(bridge._debug_log) == 0


class TestAccessibilityProfile:
    """Profile management via bridge."""

    def test_get_profiles(self, bridge: KeyboardBridge):
        profiles = bridge.getAccessibilityProfiles()
        assert "normal" in profiles

    def test_set_profile(self, bridge: KeyboardBridge):
        assert bridge.setAccessibilityProfile("mild_tremor")

    def test_set_invalid_profile(self, bridge: KeyboardBridge):
        assert not bridge.setAccessibilityProfile("bogus")

    def test_get_current_profile(self, bridge: KeyboardBridge):
        profile = bridge.getCurrentProfile()
        assert isinstance(profile, str)


class TestSentenceLearning:
    """Sentence-level learning from typing."""

    def test_space_builds_sentence_buffer(self, bridge: KeyboardBridge):
        for c in "hello":
            bridge.pressKey(c)
        bridge.pressSpecialKey("space")
        assert "hello" in bridge._sentence_buffer

    def test_space_learns_bigrams(self, bridge: KeyboardBridge):
        # Type "I want" — on second space, learn() is called with "I want"
        for c in "i":
            bridge.pressKey(c)
        bridge.pressSpecialKey("space")
        for c in "want":
            bridge.pressKey(c)
        bridge.pressSpecialKey("space")
        # The predictor should have learned the bigram "i -> want"
        assert bridge._predictor._ngram.bigrams["i"]["want"] > 0

    def test_period_triggers_sentence_learning(self, bridge: KeyboardBridge):
        for c in "hi":
            bridge.pressKey(c)
        bridge.pressSpecialKey("space")
        for c in "there":
            bridge.pressKey(c)
        bridge.pressKey(".")
        # Sentence buffer should be reset after period
        assert bridge._sentence_buffer == ""
        # Current word should be reset too
        assert bridge._current_word == ""

    def test_return_learns_sentence(self, bridge: KeyboardBridge):
        for c in "good":
            bridge.pressKey(c)
        bridge.pressSpecialKey("space")
        for c in "morning":
            bridge.pressKey(c)
        bridge.pressSpecialKey("return")
        # Should have learned bigram from the sentence
        assert bridge._predictor._ngram.bigrams["good"]["morning"] > 0
        # Sentence buffer reset
        assert bridge._sentence_buffer == ""

    def test_exclamation_triggers_learning(self, bridge: KeyboardBridge):
        for c in "wow":
            bridge.pressKey(c)
        bridge.pressKey("!")
        assert bridge._sentence_buffer == ""
        assert bridge._current_word == ""

    def test_question_triggers_learning(self, bridge: KeyboardBridge):
        for c in "why":
            bridge.pressKey(c)
        bridge.pressKey("?")
        assert bridge._sentence_buffer == ""
        assert bridge._current_word == ""


class TestPunctuationSpacing:
    """Smart punctuation spacing — removes space before punctuation."""

    def test_no_space_before_period(self, bridge: KeyboardBridge):
        bridge.pressKey("h")
        bridge.pressKey("i")
        bridge.pressSpecialKey("space")
        # Context buffer now has "hi ", pressing "." should remove the space
        bridge.pressKey(".")
        bridge._synth.send_key.assert_any_call("BackSpace", modifiers=None)
