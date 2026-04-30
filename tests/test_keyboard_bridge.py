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


class TestForegroundWindow:
    """_get_foreground_window_id platform dispatch."""

    def test_wayland_returns_zero(self, bridge: KeyboardBridge, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        assert bridge._get_foreground_window_id() == 0

    def test_x11_parses_xdotool_output(self, bridge: KeyboardBridge, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "12345678\n"
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake_result)

        assert bridge._get_foreground_window_id() == 12345678

    def test_xdotool_missing_returns_zero(self, bridge: KeyboardBridge, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

        def raise_fnf(*a, **kw):
            raise FileNotFoundError("xdotool not found")
        monkeypatch.setattr("subprocess.run", raise_fnf)

        assert bridge._get_foreground_window_id() == 0

    def test_check_foreground_clears_context_on_switch(self, bridge: KeyboardBridge, monkeypatch):
        """When the window ID changes, predictions and buffers reset."""
        bridge._last_foreground_hwnd = 100
        bridge._current_word = "hel"
        bridge._context_buffer = "earlier "
        bridge._sentence_buffer = "earlier "
        bridge._predictions = ["hello"]

        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 200)
        bridge._check_foreground_window()

        assert bridge._current_word == ""
        assert bridge._context_buffer == ""
        assert bridge._sentence_buffer == ""
        assert bridge._predictions == []
        assert bridge._last_foreground_hwnd == 200

    def test_check_foreground_skips_first_poll(self, bridge: KeyboardBridge, monkeypatch):
        """First poll after startup only seeds _last_foreground_hwnd — no wipe."""
        bridge._last_foreground_hwnd = 0
        bridge._current_word = "hel"
        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 42)
        bridge._check_foreground_window()
        assert bridge._current_word == "hel"          # preserved
        assert bridge._last_foreground_hwnd == 42     # but seeded

    def test_check_foreground_noop_when_unavailable(self, bridge: KeyboardBridge, monkeypatch):
        """If we can't read the focused window, leave state alone."""
        bridge._last_foreground_hwnd = 100
        bridge._current_word = "hel"
        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 0)
        bridge._check_foreground_window()
        assert bridge._current_word == "hel"
        assert bridge._last_foreground_hwnd == 100    # unchanged


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
        # Caps Lock and Shift are independent — toggling caps does NOT
        # also flip shift's state (the visual highlight on the Shift key
        # used to come on with caps; that was a bug).
        assert not bridge._shift_active

    def test_caps_lock_off_does_not_touch_shift(self, bridge: KeyboardBridge):
        bridge.toggleShift()                   # Shift on independently
        bridge.toggleCapsLock()                # Caps on
        bridge.toggleCapsLock()                # Caps off
        assert not bridge._caps_lock_active
        assert bridge._shift_active            # Shift state preserved

    def test_caps_lock_uppercases_letters(self, bridge: KeyboardBridge):
        bridge.toggleCapsLock()
        # Layer reflects caps even though shift is off
        assert bridge._current_layer == "upper"

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

    def test_toggle_shift_holds_at_os_level(self, bridge: KeyboardBridge):
        """Shift+click in the target app only works if the OS sees
        Shift held — same model as Ctrl/Alt/Win. Without this the
        synthesised input has Shift attached as a chord modifier on
        each keystroke, but a mouse click between Shift-toggle and
        the next typed character lands without Shift held."""
        bridge.toggleShift()
        bridge._synth.hold_modifier.assert_called_with("shift")
        bridge.toggleShift()
        bridge._synth.release_modifier.assert_called_with("shift")

    def test_shift_auto_release_releases_os_modifier(self, bridge: KeyboardBridge):
        bridge.toggleShift()
        bridge._synth.release_modifier.reset_mock()
        bridge.pressKey("a")
        bridge._synth.release_modifier.assert_called_with("shift")

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

    def test_backspace_into_completed_word_rehydrates_current_word(
        self, bridge: KeyboardBridge,
    ):
        """Regression: backspacing past a trailing space pulls the
        partial word back into ``_current_word`` so prediction-clicks
        take the suffix-only branch instead of the next-word branch.

        Without this, clicking a prediction after backspacing into a
        committed word produced "backspacbackspaces"-style duplicates:
        ``_current_word`` was empty, so ``pressPrediction`` typed the
        full word alongside the on-screen partial.
        """
        for c in "backspaces":
            bridge.pressKey(c)
        bridge.pressSpecialKey("space")
        # State after space-completion: word committed to context.
        assert bridge._current_word == ""
        assert bridge._context_buffer.endswith("backspaces ")

        # First backspace pops the trailing space off context.  The
        # partial word "backspaces" is now the tail of context_buffer,
        # and the rehydrate hook should move it back into
        # _current_word.
        bridge.pressSpecialKey("backspace")
        assert bridge._current_word == "backspaces"
        assert not bridge._context_buffer.endswith("backspaces")

        # Subsequent backspaces shrink _current_word normally.
        bridge.pressSpecialKey("backspace")
        bridge.pressSpecialKey("backspace")
        assert bridge._current_word == "backspac"

    def test_backspace_into_word_without_preceding_text(
        self, bridge: KeyboardBridge,
    ):
        """Edge case: only one word typed, backspaced past the trailing
        space.  Rehydrate must work when there is no whitespace earlier
        in the buffer (entire context becomes _current_word, buffer
        becomes empty)."""
        for c in "hello":
            bridge.pressKey(c)
        bridge.pressSpecialKey("space")
        bridge.pressSpecialKey("backspace")
        assert bridge._current_word == "hello"
        assert bridge._context_buffer == ""

    def test_backspace_at_word_boundary_does_not_rehydrate(
        self, bridge: KeyboardBridge,
    ):
        """The rehydrate hook must no-op when the new tail is already
        whitespace — the user is between words, not editing one."""
        for c in "hi":
            bridge.pressKey(c)
        bridge.pressSpecialKey("space")
        for c in "x":
            bridge.pressKey(c)
        bridge.pressSpecialKey("space")
        # Context: "hi x ", _current_word: ""
        bridge.pressSpecialKey("backspace")  # pops trailing space
        # Now context "hi x" — single trailing word "x" should rehydrate.
        assert bridge._current_word == "x"
        # Backspace once more — _current_word goes empty.
        bridge.pressSpecialKey("backspace")
        assert bridge._current_word == ""
        # Backspace once more — pops the space between "hi" and what
        # used to be "x"; "hi" should rehydrate.
        bridge.pressSpecialKey("backspace")
        assert bridge._current_word == "hi"


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


class TestPredictionCapsDisplay:
    """Caps Lock mirrors into the prediction pill display case."""

    def test_display_cased_passthrough_when_caps_off(self, bridge: KeyboardBridge):
        assert bridge._display_cased(["hello", "world"]) == ["hello", "world"]

    def test_display_cased_uppercases_when_caps_on(self, bridge: KeyboardBridge):
        bridge._caps_lock_active = True
        assert bridge._display_cased(["hello", "iPhone"]) == ["HELLO", "IPHONE"]

    def test_display_cased_empty_list_is_safe(self, bridge: KeyboardBridge):
        bridge._caps_lock_active = True
        assert bridge._display_cased([]) == []

    def test_on_predictions_ready_uppercases_with_caps(self, bridge: KeyboardBridge):
        bridge._caps_lock_active = True
        bridge._on_predictions_ready(["hello", "help"])
        assert bridge._predictions == ["HELLO", "HELP"]

    def test_on_predictions_ready_preserves_case_without_caps(self, bridge: KeyboardBridge):
        bridge._on_predictions_ready(["iPhone", "iPad"])
        assert bridge._predictions == ["iPhone", "iPad"]

    def test_caps_toggle_triggers_prediction_refresh(self, bridge: KeyboardBridge):
        """Flipping caps while pills are visible should re-query the
        engine so the visible pills flip case to match the new mode."""
        bridge._predictions = ["HELLO"]  # something visible
        called = {"count": 0}
        bridge._update_predictions = lambda: called.__setitem__("count", called["count"] + 1)
        bridge.toggleCapsLock()
        assert called["count"] == 1

    def test_caps_toggle_skips_refresh_when_no_predictions(self, bridge: KeyboardBridge):
        bridge._predictions = []
        called = {"count": 0}
        bridge._update_predictions = lambda: called.__setitem__("count", called["count"] + 1)
        bridge.toggleCapsLock()
        assert called["count"] == 0

    def test_display_cased_matches_shift_typed_first_letter(self, bridge: KeyboardBridge):
        """One-shot Shift typed an uppercase H; pills must match so
        the suffix-only insert path's case-sensitive startswith fires
        and the user's capital isn't clobbered by a full replace."""
        bridge._current_word = "Hel"
        assert bridge._display_cased(["hello", "help"]) == ["Hello", "Help"]

    def test_display_cased_leaves_already_capitalised_alone(self, bridge: KeyboardBridge):
        """Proper-noun predictions (already title-cased) must not be
        touched — the upstream get_capitalized() already did the right
        thing, and re-casing would be a no-op at best."""
        bridge._current_word = "Mar"
        assert bridge._display_cased(["Mary", "march"]) == ["Mary", "March"]

    def test_display_cased_skips_predictions_not_matching_prefix(self, bridge: KeyboardBridge):
        """Defensive: predictions whose lowercase form doesn't start
        with the typed prefix shouldn't be re-cased."""
        bridge._current_word = "Hel"
        assert bridge._display_cased(["world"]) == ["world"]

    def test_display_cased_lowercase_prefix_unchanged(self, bridge: KeyboardBridge):
        """No shift means no case-matching — pure pass-through."""
        bridge._current_word = "hel"
        assert bridge._display_cased(["hello", "help"]) == ["hello", "help"]


class TestEditModeIntercept:
    """pressKey / pressSpecialKey route to QML when edit mode is active."""

    def _collect(self, signal):
        calls: list = []
        signal.connect(lambda *args: calls.append(args))
        return calls

    def test_press_key_emits_editKeyTyped_when_edit_mode_active(self, bridge: KeyboardBridge):
        typed = self._collect(bridge.editKeyTyped)
        bridge.setEditMode(True)
        bridge.pressKey("a")
        assert typed == [("a",)]
        # And nothing leaked to the synth
        bridge._synth.send_text.assert_not_called()
        bridge._synth.send_key.assert_not_called()

    def test_press_key_respects_shift_in_edit_mode(self, bridge: KeyboardBridge):
        typed = self._collect(bridge.editKeyTyped)
        bridge.setEditMode(True)
        bridge.toggleShift()
        bridge.pressKey("a")
        assert typed == [("A",)]
        assert not bridge._shift_active  # auto-released after one keypress

    def test_press_key_respects_caps_in_edit_mode(self, bridge: KeyboardBridge):
        typed = self._collect(bridge.editKeyTyped)
        bridge.setEditMode(True)
        bridge._caps_lock_active = True
        bridge.pressKey("a")
        bridge.pressKey("b")
        assert typed == [("A",), ("B",)]

    def test_press_special_emits_editSpecialPressed_in_edit_mode(self, bridge: KeyboardBridge):
        specials = self._collect(bridge.editSpecialPressed)
        bridge.setEditMode(True)
        bridge.pressSpecialKey("backspace")
        bridge.pressSpecialKey("left")
        bridge.pressSpecialKey("return")
        assert specials == [("backspace",), ("left",), ("return",)]
        bridge._synth.send_key.assert_not_called()

    def test_edit_mode_off_falls_through_to_synth(self, bridge: KeyboardBridge):
        typed = self._collect(bridge.editKeyTyped)
        bridge.setEditMode(False)
        bridge.pressKey("a")
        assert typed == []               # no signal
        bridge._synth.send_text.assert_called()   # synth path taken

    def test_set_edit_mode_toggles_flag(self, bridge: KeyboardBridge):
        assert bridge._edit_mode_active is False
        bridge.setEditMode(True)
        assert bridge._edit_mode_active is True
        bridge.setEditMode(False)
        assert bridge._edit_mode_active is False


class TestDebugLog:
    """Debug logging."""

    def test_debug_mode_off_by_default(self, bridge: KeyboardBridge):
        assert not bridge._debug_mode

    def test_set_debug_mode(self, bridge: KeyboardBridge):
        bridge.setDebugMode(True)
        assert bridge._debug_mode

    def test_debug_log_entries(self, bridge: KeyboardBridge):
        bridge._debug_mode = True
        bridge._add_debug_log("test message")
        log = bridge.getDebugLog()
        assert any("test message" in entry for entry in log)

    def test_debug_log_skipped_when_disabled(self, bridge: KeyboardBridge):
        bridge._debug_mode = False
        bridge._add_debug_log("secret")
        assert len(bridge._debug_log) == 0

    def test_debug_log_capped(self, bridge: KeyboardBridge):
        bridge._debug_mode = True
        for i in range(200):
            bridge._add_debug_log(f"entry {i}")
        assert len(bridge._debug_log) <= 100

    def test_clear_debug_log(self, bridge: KeyboardBridge):
        bridge._debug_mode = True
        bridge._add_debug_log("something")
        bridge.clearDebugLog()
        assert len(bridge._debug_log) == 0


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

    def test_user_typed_space_before_period_is_preserved(self, bridge: KeyboardBridge):
        """A space the user typed manually must NOT be backspaced when
        they then type punctuation — the visible flicker is surprising
        and in some apps undoes selection / breaks undo history. Only
        our own auto-space (after a prediction or punctuation) gets
        cleaned up; see test_space_removed_before_comma_after_prediction.
        """
        bridge.pressKey("h")
        bridge.pressKey("i")
        bridge.pressSpecialKey("space")
        bridge._synth.send_key.reset_mock()
        bridge.pressKey(".")
        backspace_calls = [
            c for c in bridge._synth.send_key.call_args_list
            if c[0][0] == "BackSpace"
        ]
        assert len(backspace_calls) == 0

    def test_auto_space_after_comma(self, bridge: KeyboardBridge):
        bridge._auto_space_after_punctuation = True
        bridge.pressKey("h")
        bridge.pressKey("i")
        bridge.pressKey(",")
        # Comma should be followed by an auto-space
        calls = [c[0][0] for c in bridge._synth.send_text.call_args_list]
        assert calls[-2:] == [",", " "]

    def test_auto_space_after_semicolon(self, bridge: KeyboardBridge):
        bridge._auto_space_after_punctuation = True
        bridge.pressKey("h")
        bridge.pressKey("i")
        bridge.pressKey(";")
        calls = [c[0][0] for c in bridge._synth.send_text.call_args_list]
        assert calls[-2:] == [";", " "]

    def test_comma_does_not_trigger_capitalize(self, bridge: KeyboardBridge):
        bridge._auto_space_after_punctuation = True
        bridge._auto_capitalize_after_punctuation = True
        bridge.pressKey("h")
        bridge.pressKey("i")
        bridge.pressKey(",")
        # Comma should NOT activate shift (only sentence-enders do)
        assert not bridge._shift_active

    def test_no_backspace_before_comma_mid_word(self, bridge: KeyboardBridge):
        """Typing 'hello,' should NOT backspace — there's no space to remove."""
        bridge.pressKey("h")
        bridge.pressKey("e")
        bridge.pressKey("l")
        bridge.pressKey("l")
        bridge.pressKey("o")
        bridge._synth.send_key.reset_mock()
        bridge.pressKey(",")
        # No BackSpace should have been sent — the comma comes right after letters
        backspace_calls = [
            c for c in bridge._synth.send_key.call_args_list
            if c[0][0] == "BackSpace"
        ]
        assert len(backspace_calls) == 0

    def test_space_removed_before_comma_after_prediction(self, bridge: KeyboardBridge):
        """Selecting prediction 'hello ' then typing ',' should remove the space."""
        bridge._auto_space_after_punctuation = True
        bridge.pressKey("h")
        bridge.pressPrediction("hello")
        # After prediction: _current_word = "", context_buffer ends with "hello "
        bridge._synth.send_key.reset_mock()
        bridge.pressKey(",")
        # Should have sent a BackSpace to remove the trailing space
        bridge._synth.send_key.assert_any_call("BackSpace", modifiers=None)

    def test_clear_predictions_preserves_context(self, bridge: KeyboardBridge):
        """clearPredictions should NOT wipe typing state."""
        bridge.pressKey("h")
        bridge.pressKey("e")
        bridge.pressKey("l")
        assert bridge._current_word == "hel"
        bridge.clearPredictions()
        # Predictions cleared but typing state preserved
        assert bridge._predictions == []
        assert bridge._current_word == "hel"
        assert bridge._context_buffer != "" or bridge._current_word == "hel"


class TestMatchCase:
    """KeyboardBridge._match_case casing rules for autocorrect output."""

    def test_all_uppercase_typed_returns_upper(self):
        from src.keyboard_bridge import KeyboardBridge
        assert KeyboardBridge._match_case("RECIEVE", "receive") == "RECEIVE"

    def test_title_case_typed_returns_title(self):
        from src.keyboard_bridge import KeyboardBridge
        assert KeyboardBridge._match_case("Recieve", "receive") == "Receive"

    def test_lowercase_typed_returns_replacement_as_is(self):
        from src.keyboard_bridge import KeyboardBridge
        # Replacement keeps its own casing (e.g. "iPhone" out of the
        # misspellings table) even if typed was all-lowercase.
        assert KeyboardBridge._match_case("iphone", "iPhone") == "iPhone"

    def test_mixed_case_typed_passes_through(self):
        from src.keyboard_bridge import KeyboardBridge
        # Not title, not all-upper → don't try to second-guess.
        assert KeyboardBridge._match_case("RecIEVE", "receive") == "receive"

    def test_empty_typed_returns_replacement(self):
        from src.keyboard_bridge import KeyboardBridge
        assert KeyboardBridge._match_case("", "x") == "x"


class TestSpaceTimeAutocorrect:
    """pressSpecialKey('space') runs misspelling/fuzzy autocorrect."""

    def test_known_misspelling_replaced_on_space(self, bridge: KeyboardBridge):
        bridge._current_word = "recieve"
        bridge.pressSpecialKey("space")
        # _current_word should have been corrected before clearing.
        # Check the post-space state — corrected word should land in
        # the sentence buffer, not the misspelling.
        assert "receive" in bridge._sentence_buffer
        assert "recieve" not in bridge._sentence_buffer

    def test_misspelling_casing_matches_typed_word(self, bridge: KeyboardBridge):
        bridge._current_word = "Recieve"
        bridge.pressSpecialKey("space")
        # Title-cased typed → title-cased correction.
        assert "Receive" in bridge._sentence_buffer

    def test_valid_word_not_corrected(self, bridge: KeyboardBridge):
        bridge._current_word = "the"
        bridge.pressSpecialKey("space")
        assert "the" in bridge._sentence_buffer

    def test_disabling_autocorrect_lets_misspelling_through(self, bridge: KeyboardBridge):
        bridge.setAutocorrectEnabled(False)
        bridge._current_word = "recieve"
        bridge.pressSpecialKey("space")
        # With autocorrect off, the literal typed word survives.
        assert "recieve" in bridge._sentence_buffer

    def test_privacy_mode_skips_autocorrect(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._current_word = "recieve"
        bridge.pressSpecialKey("space")
        # Privacy mode shouldn't learn or autocorrect.
        assert "recieve" not in bridge._sentence_buffer
        assert "receive" not in bridge._sentence_buffer


class TestEditPredictionSanitize:
    """_sanitize_edit scrubs untrusted QML input before it reaches the model."""

    def test_strips_control_chars(self):
        from src.keyboard_bridge import KeyboardBridge
        assert KeyboardBridge._sanitize_edit("hello\x00world") == "helloworld"
        assert KeyboardBridge._sanitize_edit("foo\nbar") == "foobar"
        assert KeyboardBridge._sanitize_edit("a\x01b\x1fc") == "abc"

    def test_caps_length(self):
        from src.keyboard_bridge import KeyboardBridge
        long = "a" * 200
        assert len(KeyboardBridge._sanitize_edit(long)) == KeyboardBridge._MAX_EDIT_LEN

    def test_empty_after_strip_is_empty(self):
        from src.keyboard_bridge import KeyboardBridge
        assert KeyboardBridge._sanitize_edit("   ") == ""
        assert KeyboardBridge._sanitize_edit("\x00\x01\n") == ""

    def test_non_string_input(self):
        from src.keyboard_bridge import KeyboardBridge
        assert KeyboardBridge._sanitize_edit(None) == ""  # type: ignore[arg-type]
        assert KeyboardBridge._sanitize_edit(42) == ""    # type: ignore[arg-type]

    def test_normal_input_preserved(self):
        from src.keyboard_bridge import KeyboardBridge
        assert KeyboardBridge._sanitize_edit("iPhone") == "iPhone"
        assert KeyboardBridge._sanitize_edit("  hello  ") == "hello"
