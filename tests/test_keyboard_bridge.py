"""Tests for the KeyboardBridge (Python↔QML bridge).

These tests verify modifier state management, context tracking,
and prediction wiring. Actual key synthesis is mocked since we
don't want tests injecting real keystrokes.
"""

from __future__ import annotations

import time
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
        monkeypatch.setattr("src.keyboard_bridge.focused_element_token", lambda: None)
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
        monkeypatch.setattr("src.keyboard_bridge.focused_element_token", lambda: None)
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

    def test_focus_token_change_clears_same_window(self, bridge: KeyboardBridge, monkeypatch):
        """Same window, focus moved to a different control (e.g. another text
        box) → context resets even though the window handle is unchanged."""
        bridge._last_foreground_hwnd = 100
        bridge._last_focus_token = "A"
        bridge._current_word = "hel"
        bridge._context_buffer = "earlier "
        bridge._sentence_buffer = "earlier "
        bridge._predictions = ["hello"]

        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 100)
        monkeypatch.setattr("src.keyboard_bridge.focused_element_token", lambda: "B")
        bridge._check_foreground_window()

        assert bridge._current_word == ""
        assert bridge._context_buffer == ""
        assert bridge._sentence_buffer == ""
        assert bridge._predictions == []
        assert bridge._last_focus_token == "B"
        assert bridge._last_foreground_hwnd == 100    # window never changed

    def test_focus_token_same_preserves_context(self, bridge: KeyboardBridge, monkeypatch):
        """Caret staying in the same control (same token) must not wipe."""
        bridge._last_foreground_hwnd = 100
        bridge._last_focus_token = "A"
        bridge._current_word = "hel"
        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 100)
        monkeypatch.setattr("src.keyboard_bridge.focused_element_token", lambda: "A")
        bridge._check_foreground_window()
        assert bridge._current_word == "hel"
        assert bridge._last_focus_token == "A"

    def test_focus_token_first_sighting_seeds_only(self, bridge: KeyboardBridge, monkeypatch):
        """First time we read a token (baseline None) seeds it without wiping."""
        bridge._last_foreground_hwnd = 100
        bridge._last_focus_token = None
        bridge._current_word = "hel"
        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 100)
        monkeypatch.setattr("src.keyboard_bridge.focused_element_token", lambda: "A")
        bridge._check_foreground_window()
        assert bridge._current_word == "hel"          # preserved
        assert bridge._last_focus_token == "A"        # seeded

    def test_focus_token_unreadable_keeps_baseline(self, bridge: KeyboardBridge, monkeypatch):
        """A None token ('don't know') must not wipe or clobber the baseline."""
        bridge._last_foreground_hwnd = 100
        bridge._last_focus_token = "A"
        bridge._current_word = "hel"
        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 100)
        monkeypatch.setattr("src.keyboard_bridge.focused_element_token", lambda: None)
        bridge._check_foreground_window()
        assert bridge._current_word == "hel"
        assert bridge._last_focus_token == "A"        # unchanged


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

    def test_shift_auto_releases_after_special_key(self, bridge: KeyboardBridge):
        # Regression: Shift+Tab (and any sticky-shift + special key
        # chord) used to leave _shift_active=True and the OS-held shift
        # in place. Subsequent keys came out under Shift until the user
        # tapped Shift again. Match Windows on-screen keyboard, which
        # treats Shift as one-shot for chord and character keys alike.
        bridge.toggleShift()
        bridge._synth.release_modifier.reset_mock()
        bridge.pressSpecialKey("tab")
        assert not bridge._shift_active
        bridge._synth.release_modifier.assert_any_call("shift")

    def test_alt_auto_releases_after_special_key(self, bridge: KeyboardBridge):
        bridge.toggleAlt()
        bridge.pressSpecialKey("tab")
        assert not bridge._alt_active

    def test_win_auto_releases_after_special_key(self, bridge: KeyboardBridge):
        bridge.toggleWin()
        bridge.pressSpecialKey("left")
        assert not bridge._win_active

    def test_shift_persists_through_arrow_key(self, bridge: KeyboardBridge):
        # Regression: holding Shift and pressing an arrow (to extend a
        # selection) used to drop Shift after the first press, so the
        # second arrow no longer extended the selection — and an
        # auto-repeating held arrow lost Shift after its first tick. Nav
        # keys must keep Shift held across presses; the user taps Shift
        # again to release it.
        bridge.toggleShift()
        for key in ("left", "right", "up", "down", "home", "end",
                    "pageup", "pagedown"):
            bridge.pressSpecialKey(key)
            assert bridge._shift_active, f"shift dropped after {key}"

    def test_ctrl_persists_through_arrow_key(self, bridge: KeyboardBridge):
        # Ctrl+arrow (jump by word) and Ctrl+Shift+arrow (select by word)
        # need Ctrl held across presses for the same reason as Shift.
        bridge.toggleCtrl()
        bridge.pressSpecialKey("right")
        assert bridge._ctrl_active
        bridge.pressSpecialKey("right")
        assert bridge._ctrl_active

    def test_shift_still_releases_after_tab(self, bridge: KeyboardBridge):
        # Non-nav special keys keep the one-shot behaviour: Shift+Tab
        # drops Shift so the next click isn't under Shift.
        bridge.toggleShift()
        bridge.pressSpecialKey("tab")
        assert not bridge._shift_active

    def test_shift_special_key_emits_change_signal(self, bridge: KeyboardBridge):
        # QML binds the Shift key highlight to shiftActiveChanged; the
        # auto-release path has to emit the signal or the visual stays
        # latched after the chord even though the underlying state is
        # already False.
        bridge.toggleShift()
        emissions = []
        bridge.shiftActiveChanged.connect(emissions.append)
        bridge.pressSpecialKey("tab")
        assert emissions and emissions[-1] is False


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


class TestGameKeyHold:
    """Game auto-compat: single keys are held down so polling games catch them.

    A zero-gap key-down+key-up injected in one SendInput batch can land
    entirely between two of a game's per-frame keyboard-state polls and be
    missed; holding the key down ~one frame fixes it.
    """

    def test_char_uses_send_text_when_not_a_game(self, bridge: KeyboardBridge):
        bridge._game_auto_active = False
        bridge.pressKey("q")
        bridge._synth.send_text.assert_called_with("q")

    def test_char_held_via_send_key_in_game_mode(self, bridge: KeyboardBridge):
        bridge._game_auto_active = True
        bridge.pressKey("q")
        # Routed through send_key with a positive hold, NOT the atomic send_text.
        bridge._synth.send_text.assert_not_called()
        _, kwargs = bridge._synth.send_key.call_args
        assert kwargs.get("hold_seconds", 0) > 0

    def test_special_key_held_in_game_mode(self, bridge: KeyboardBridge):
        bridge._game_auto_active = True
        bridge.pressSpecialKey("up")
        _, kwargs = bridge._synth.send_key.call_args
        assert kwargs.get("hold_seconds", 0) > 0

    def test_special_key_not_held_outside_game(self, bridge: KeyboardBridge):
        bridge._game_auto_active = False
        bridge.pressSpecialKey("up")
        # Original two-arg signature preserved when not holding.
        bridge._synth.send_key.assert_called_with("Up", modifiers=None)

    def test_key_hold_seconds_gate(self, bridge: KeyboardBridge):
        bridge._game_auto_active = False
        assert bridge._key_hold_seconds() == 0.0
        bridge._game_auto_active = True
        assert bridge._key_hold_seconds() > 0.0

    def test_window_is_game_false_off_windows(self, monkeypatch):
        import sys

        from src import keyboard_bridge
        monkeypatch.setattr(sys, "platform", "linux")
        assert keyboard_bridge._window_is_game(12345) is False

    def test_borderless_fullscreen_false_off_windows(self, monkeypatch):
        import sys

        from src import keyboard_bridge
        monkeypatch.setattr(sys, "platform", "linux")
        assert keyboard_bridge._window_is_borderless_fullscreen(12345) is False

    def test_game_match_takes_priority_over_compat(self, monkeypatch):
        # Exe in the game list wins; compat exclusion / fullscreen heuristic
        # are not consulted.
        import sys

        from src import keyboard_bridge
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(keyboard_bridge, "_owning_exe_name", lambda h: "aoe2de_s.exe")
        assert keyboard_bridge._window_is_game(999) is True

    def test_compat_exe_excluded_from_fullscreen_heuristic(self, monkeypatch):
        # A fullscreen IDE must NOT be treated as a game even if it's borderless
        # fullscreen, which would lag normal typing.
        import sys

        from src import keyboard_bridge
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(keyboard_bridge, "_owning_exe_name", lambda h: "code.exe")
        called = {"heuristic": False}

        def _flag(_h):
            called["heuristic"] = True
            return True

        monkeypatch.setattr(keyboard_bridge, "_window_is_borderless_fullscreen", _flag)
        assert keyboard_bridge._window_is_game(999) is False
        assert called["heuristic"] is False  # short-circuited before the heuristic

    def test_unlisted_fullscreen_window_is_game(self, monkeypatch):
        # Unknown exe + borderless fullscreen -> treated as a game.
        import sys

        from src import keyboard_bridge
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(keyboard_bridge, "_owning_exe_name", lambda h: "somegame.exe")
        monkeypatch.setattr(keyboard_bridge, "_window_is_borderless_fullscreen", lambda h: True)
        assert keyboard_bridge._window_is_game(999) is True


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

    def test_hyphen_resets_current_word(self, bridge: KeyboardBridge):
        """Regression: typing 'word1-word2' must not let _current_word
        accumulate as 'word1-word2'.  The hyphen is a word boundary for
        prediction purposes; the second token should be tracked alone.

        Without this, clicking a suggestion for the second token failed
        the prefix-match in pressPrediction (the suggestion didn't start
        with 'word1-word2'), fell back to replace_text(len, ...), and
        backspaced 'word1-' off the screen along with the partial word.
        """
        for c in "word1":
            bridge.pressKey(c)
        assert bridge._current_word == "word1"
        bridge.pressKey("-")
        # _current_word resets immediately on the boundary char; the
        # hyphen is in the OS-side text (sent via _send_text) but no
        # longer in our prediction state.
        assert bridge._current_word == ""
        assert bridge._context_buffer.endswith("word1-")
        for c in "wo":
            bridge.pressKey(c)
        # Subsequent typing builds the SECOND word alone.
        assert bridge._current_word == "wo"

    def test_hyphenated_prediction_click_uses_suffix_only_path(
        self, bridge: KeyboardBridge,
    ):
        """Regression for the user-reported bug: typing 'word1-wo' then
        clicking a prediction for 'world' must NOT send BackSpaces that
        eat 'word1-'.  Suffix-only insertion should fire as if 'wo'
        were the only typed prefix.
        """
        for c in "word1":
            bridge.pressKey(c)
        bridge.pressKey("-")
        for c in "wo":
            bridge.pressKey(c)
        bridge._synth.reset_mock()
        bridge.pressPrediction("world")
        # Suffix-only contract: send_text("rld "), no BackSpace, no
        # replace_text.
        backspace_calls = [
            c for c in bridge._synth.send_key.call_args_list
            if c.args and c.args[0] == "BackSpace"
        ]
        assert backspace_calls == [], (
            "BackSpace was sent — would have eaten 'word1-' off the screen"
        )
        bridge._synth.replace_text.assert_not_called()
        bridge._synth.send_text.assert_any_call("rld ")

    def test_other_word_boundary_chars_also_reset(self, bridge: KeyboardBridge):
        """Same fix applies to slash, backslash, opening brackets, and
        prefix punctuation (markdown *, mention @, hashtag #, sigils $,
        operators = + & % | ~ ^ etc.).  Spot-check a handful that
        trigger the same bug in real typing."""
        for c in "path":
            bridge.pressKey(c)
        bridge.pressKey("/")
        for c in "to":
            bridge.pressKey(c)
        assert bridge._current_word == "to"
        assert bridge._context_buffer.endswith("path/")

    def test_prefix_punctuation_does_not_pollute_current_word(
        self, bridge: KeyboardBridge,
    ):
        """Regression: typing '*hel' must leave _current_word = 'hel',
        not '*hel'.  Without the boundary, clicking a prediction for
        'hello' fell into the replace_text branch (since 'hello'
        doesn't start with '*hel') and Shift+Left-selected the
        asterisk along with the typed letters, deleting it."""
        boundary_chars = ["*", "@", "#", "$", "%", "&", "+", "=", "~", "^", "|", '"', "`"]
        for ch in boundary_chars:
            bridge._current_word = ""
            bridge._context_buffer = ""
            bridge.pressKey(ch)
            for c in "hel":
                bridge.pressKey(c)
            assert bridge._current_word == "hel", (
                f"{ch!r} should be a word boundary; _current_word was "
                f"{bridge._current_word!r}"
            )
            assert bridge._context_buffer.endswith(ch), (
                f"{ch!r} should remain in context_buffer"
            )

    def test_asterisk_prefix_prediction_click_keeps_asterisk(
        self, bridge: KeyboardBridge,
    ):
        """User-reported bug: typing '*hel' then clicking 'hello' must
        type ONLY 'lo ' as a suffix and leave the leading '*' alone.
        Pre-fix, the asterisk accumulated into _current_word so the
        click fell through to replace_text(len('*hel'), 'hello '),
        which Shift+Left-selected 4 chars and overwrote the asterisk.
        """
        bridge.pressKey("*")
        for c in "hel":
            bridge.pressKey(c)
        bridge._synth.reset_mock()
        bridge.pressPrediction("hello")
        backspace_calls = [
            c for c in bridge._synth.send_key.call_args_list
            if c.args and c.args[0] == "BackSpace"
        ]
        assert backspace_calls == [], (
            "BackSpace was sent — would have eaten the leading '*'"
        )
        bridge._synth.replace_text.assert_not_called()
        bridge._synth.send_text.assert_any_call("lo ")

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

    def test_backspace_into_typo_retracts_candidate_count(
        self, bridge: KeyboardBridge,
    ):
        """Backspace-as-negative-signal: when a word the user just
        completed is rehydrated by backspacing past its trailing space,
        the rehydrate hook retracts one sighting so a typo typed once
        and immediately corrected can't accumulate toward the candidate
        gate. We seed candidate_counts directly to keep the test
        deterministic — the on-disk user model in ``get_model_dir()``
        may already have arbitrary words promoted, which makes typing-
        based seeding flaky.
        """
        ngram = bridge._predictor._ngram
        # Seed: word is in candidate_counts and the bridge's context
        # buffer reflects "we already typed it and pressed space".
        ngram._candidate_counts["zephyrish"] = 1
        bridge._context_buffer = "zephyrish "
        # Backspace pops the trailing space → rehydrate fires.
        bridge.pressSpecialKey("backspace")
        assert bridge._current_word == "zephyrish"
        # Sighting retracted by the unlearn call inside _rehydrate.
        assert "zephyrish" not in ngram._candidate_counts

    def test_backspace_into_word_skips_unlearn_in_privacy_mode(
        self, bridge: KeyboardBridge,
    ):
        """Privacy mode suppresses learning AND unlearning. Symmetry
        matters: the original space-press never reached learn() in
        privacy mode, so retracting on backspace would push counts
        negative for whatever was actually in the table from past
        non-private typing.
        """
        ngram = bridge._predictor._ngram
        ngram._candidate_counts["zephyrish"] = 1
        bridge._context_buffer = "zephyrish "
        # setPrivacyMode pins _privacy_mode_manual so the per-keystroke
        # auto-detect doesn't reset the flag from system state during
        # the test.
        bridge.setPrivacyMode(True)
        # _enter_privacy_mode() scrubs context_buffer — re-seed after.
        bridge._context_buffer = "zephyrish "
        bridge.pressSpecialKey("backspace")
        # In privacy mode the backspace branch is bypassed entirely, so
        # _current_word and _context_buffer are untouched. The point of
        # the test is that whatever was in candidate_counts beforehand
        # stays put.
        assert ngram._candidate_counts.get("zephyrish", 0) == 1


class TestWordContextDrillDown:
    """getWordContext returns the structure the drill-down panel needs."""

    def test_returns_word_count_and_lists(self, bridge: KeyboardBridge):
        # Use synthetic tokens (zzq*) that won't collide with whatever
        # the user's on-disk ngram_model.json already holds — the bridge
        # fixture loads real user state on construction.
        ngram = bridge._predictor._ngram
        ngram.unigrams["zzqclaude"] = 12
        ngram.user_vocab["zzqclaude"] = 7
        ngram.bigrams["zzqasked"]["zzqclaude"] = 4
        ngram.bigrams["zzqtold"]["zzqclaude"] = 2
        ngram.bigrams["zzqclaude"]["zzqsaid"] = 5
        ngram.bigrams["zzqclaude"]["zzqwrote"] = 1
        ngram.trigrams["zzqi zzqasked"]["zzqclaude"] = 3
        ngram.trigrams["zzqasked zzqclaude"]["zzqyesterday"] = 2

        ctx = bridge.getWordContext("zzqclaude")

        assert ctx["word"] == "zzqclaude"
        assert ctx["count"] == 12
        assert ctx["userCount"] == 7
        # Successors sorted by count.
        assert ctx["successors"] == [
            {"word": "zzqsaid", "count": 5},
            {"word": "zzqwrote", "count": 1},
        ]
        # Predecessors sorted by count.
        assert ctx["predecessors"] == [
            {"word": "zzqasked", "count": 4},
            {"word": "zzqtold", "count": 2},
        ]
        # Trigrams: trailing window "i asked claude" and middle window
        # "asked claude yesterday".
        phrases = {(t["phrase"], t["position"], t["count"]) for t in ctx["trigrams"]}
        assert ("zzqi zzqasked zzqclaude", "trailing", 3) in phrases
        assert ("zzqasked zzqclaude zzqyesterday", "middle", 2) in phrases

    def test_empty_word_returns_empty(self, bridge: KeyboardBridge):
        ctx = bridge.getWordContext("")
        assert ctx["word"] == ""
        assert ctx["count"] == 0
        assert ctx["successors"] == []
        assert ctx["predecessors"] == []
        assert ctx["trigrams"] == []

    def test_unknown_word_returns_zeroed_lists(self, bridge: KeyboardBridge):
        ctx = bridge.getWordContext("notarealwordzzz")
        assert ctx["word"] == "notarealwordzzz"
        assert ctx["count"] == 0
        assert ctx["successors"] == []
        assert ctx["predecessors"] == []
        assert ctx["trigrams"] == []

    def test_lowercases_input(self, bridge: KeyboardBridge):
        ngram = bridge._predictor._ngram
        ngram.unigrams["claude"] = 5
        ctx = bridge.getWordContext("Claude")
        # Input lowercased before lookup so QML callers can pass user-
        # facing casing without worrying about the stored form.
        assert ctx["word"] == "claude"
        assert ctx["count"] == 5


class TestUpdateHandoffConsumption:
    """consumeUpdateHandoff drives the post-update toast on first launch."""

    def test_returns_empty_when_no_handoff_file(
        self, bridge: KeyboardBridge, tmp_path, monkeypatch,
    ):
        monkeypatch.setattr("src.platform.get_config_dir", lambda: tmp_path)
        result = bridge.consumeUpdateHandoff()
        assert result == {}

    def test_reads_and_returns_payload(self, bridge: KeyboardBridge, tmp_path, monkeypatch):
        import json as _json
        import time as _time
        monkeypatch.setattr("src.platform.get_config_dir", lambda: tmp_path)
        (tmp_path / "update_handoff.json").write_text(_json.dumps({
            "version": "1.0.16",
            "previous_version": "1.0.15",
            "completed_at": _time.time(),
        }))
        result = bridge.consumeUpdateHandoff()
        assert result == {"version": "1.0.16", "previousVersion": "1.0.15"}

    def test_deletes_file_after_read(self, bridge: KeyboardBridge, tmp_path, monkeypatch):
        import json as _json
        import time as _time
        monkeypatch.setattr("src.platform.get_config_dir", lambda: tmp_path)
        path = tmp_path / "update_handoff.json"
        path.write_text(_json.dumps({
            "version": "1.0.16",
            "previous_version": "1.0.15",
            "completed_at": _time.time(),
        }))
        bridge.consumeUpdateHandoff()
        # Single-use breadcrumb — the next launch must not re-toast.
        assert not path.exists()
        # Subsequent calls return empty.
        assert bridge.consumeUpdateHandoff() == {}

    def test_stale_handoff_is_discarded(self, bridge: KeyboardBridge, tmp_path, monkeypatch):
        import json as _json
        monkeypatch.setattr("src.platform.get_config_dir", lambda: tmp_path)
        # 10 minutes ago — older than the 5-min freshness window.
        (tmp_path / "update_handoff.json").write_text(_json.dumps({
            "version": "1.0.16",
            "previous_version": "1.0.15",
            "completed_at": time.time() - 600,
        }))
        result = bridge.consumeUpdateHandoff()
        assert result == {}
        # File is also deleted so it doesn't sit around forever.
        assert not (tmp_path / "update_handoff.json").exists()

    def test_malformed_json_is_handled(self, bridge: KeyboardBridge, tmp_path, monkeypatch):
        monkeypatch.setattr("src.platform.get_config_dir", lambda: tmp_path)
        (tmp_path / "update_handoff.json").write_text("{not json")
        result = bridge.consumeUpdateHandoff()
        assert result == {}
        # Garbage file is purged — wouldn't want it to keep showing up.
        assert not (tmp_path / "update_handoff.json").exists()


class TestActiveContextSignal:
    """activeContextChanged drives the live pulse in the visualization."""

    def test_emits_prev_and_current_on_typing(self, bridge: KeyboardBridge):
        emitted: list[tuple[str, str]] = []
        bridge.activeContextChanged.connect(
            lambda prev, current: emitted.append((prev, current)),
        )
        bridge._context_buffer = "I have asked "
        bridge._current_word = ""
        bridge.pressKey("c")
        # At least one emit with prev='asked', current='c'.
        assert ("asked", "c") in emitted

    def test_empty_prev_when_no_context(self, bridge: KeyboardBridge):
        emitted: list[tuple[str, str]] = []
        bridge.activeContextChanged.connect(
            lambda prev, current: emitted.append((prev, current)),
        )
        bridge._context_buffer = ""
        bridge._current_word = ""
        bridge.pressKey("h")
        assert ("", "h") in emitted

    def test_suppressed_in_privacy_mode(self, bridge: KeyboardBridge):
        emitted: list[tuple[str, str]] = []
        bridge.activeContextChanged.connect(
            lambda prev, current: emitted.append((prev, current)),
        )
        bridge.setPrivacyMode(True)
        bridge.pressKey("h")
        # Privacy mode must not leak the typed character into the viz.
        assert emitted == []


class TestCompatMode:
    """Compatibility mode — race fix for remote-desktop sessions and
    IDEs with always-on keystroke interception (VS Code + Monaco
    forks, JetBrains family).

    When enabled, prediction-click insertion and autocorrect-on-space
    must use BackSpace × N + type-full-word instead of suffix-only or
    Shift+Left selection.  Independent single-event keystrokes survive
    the keystroke drops/duplications that remote-forwarding pipelines
    introduce, and the keystroke reordering inside intercepting
    editors.
    """

    def test_default_state(self, bridge: KeyboardBridge):
        # Manual off, auto on (covers TeamViewer/RDP/VS Code without
        # user toggle), not currently active because no relevant
        # window detected.
        assert bridge._compat_manual is False
        assert bridge._compat_auto_enabled is True
        assert bridge._compat_auto_active is False
        assert bridge._in_compat_mode() is False

    def test_set_compat_mode(self, bridge: KeyboardBridge):
        bridge.setCompatMode(True)
        assert bridge._compat_manual is True
        assert bridge._in_compat_mode() is True
        bridge.setCompatMode(False)
        assert bridge._compat_manual is False

    def test_set_compat_auto_detect(self, bridge: KeyboardBridge):
        bridge.setCompatAutoDetect(False)
        assert bridge._compat_auto_enabled is False
        assert bridge._compat_auto_active is False
        bridge.setCompatAutoDetect(True)
        assert bridge._compat_auto_enabled is True

    def test_in_compat_mode_or_semantics(self, bridge: KeyboardBridge):
        # manual OR (auto_enabled AND auto_active)
        bridge._compat_manual = False
        bridge._compat_auto_enabled = False
        bridge._compat_auto_active = False
        assert bridge._in_compat_mode() is False
        # Manual on alone wins.
        bridge._compat_manual = True
        assert bridge._in_compat_mode() is True
        bridge._compat_manual = False
        # Auto enabled + active wins.
        bridge._compat_auto_enabled = True
        bridge._compat_auto_active = True
        assert bridge._in_compat_mode() is True
        # Auto enabled but not active — off.
        bridge._compat_auto_active = False
        assert bridge._in_compat_mode() is False
        # Active but auto disabled — off (auto must be enabled).
        bridge._compat_auto_enabled = False
        bridge._compat_auto_active = True
        assert bridge._in_compat_mode() is False

    def test_disabling_auto_clears_active(self, bridge: KeyboardBridge):
        bridge._compat_auto_enabled = True
        bridge._compat_auto_active = True
        bridge.setCompatAutoDetect(False)
        assert bridge._compat_auto_active is False

    def test_press_prediction_uses_backspace_plus_word_in_compat_mode(
        self, bridge: KeyboardBridge,
    ):
        bridge.setCompatMode(True)
        for c in "hel":
            bridge.pressKey(c)
        bridge._synth.reset_mock()
        bridge.pressPrediction("hello")
        # Compat-mode contract: 3 BackSpaces (one per char of typed
        # prefix), then send_text("hello ").  No replace_text, no
        # suffix-only send_text.
        backspace_calls = [
            c for c in bridge._synth.send_key.call_args_list
            if c.args and c.args[0] == "BackSpace"
        ]
        assert len(backspace_calls) == 3
        bridge._synth.send_text.assert_any_call("hello ")
        bridge._synth.replace_text.assert_not_called()

    def test_press_prediction_keeps_suffix_only_when_compat_mode_off(
        self, bridge: KeyboardBridge,
    ):
        # Default (compat mode off) — must keep the existing suffix-only
        # path so chat composers (Slack/Teams/Discord) keep working.
        for c in "hel":
            bridge.pressKey(c)
        bridge._synth.reset_mock()
        bridge.pressPrediction("hello")
        # No BackSpaces should have been sent — only the suffix.
        backspace_calls = [
            c for c in bridge._synth.send_key.call_args_list
            if c.args and c.args[0] == "BackSpace"
        ]
        assert backspace_calls == []
        bridge._synth.send_text.assert_any_call("lo ")


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


class TestPredictionCapitalizationLearning:
    """Selecting a prediction after typing a capital should teach
    the system that this word is canonically capitalized.  Without
    this, a right-click → uppercase or shift → uppercase choice is
    forgotten the moment the user accepts a prediction, and they
    have to redo it every time."""

    def test_capital_prefix_then_pill_click_teaches_casing(
        self, bridge: KeyboardBridge,
    ):
        # Right-click flow: pressKeyLiteral types the capital verbatim
        # (no shift state involved) — same _current_word state as a
        # manual shift would produce.
        bridge._predictor.learn_capitalization = MagicMock(return_value=True)
        bridge.pressKeyLiteral("O")
        bridge.pressKey("w")
        bridge.pressKey("e")
        bridge.pressPrediction("Owen")
        bridge._predictor.learn_capitalization.assert_called_with(
            "Owen", allow_uppercase=True
        )

    def test_lowercase_prefix_then_pill_click_does_not_teach_casing(
        self, bridge: KeyboardBridge,
    ):
        # Lowercase prefix → user did not signal capitalization intent.
        # The capital that may appear on the pill came from sentence-
        # start auto-cap or proper-noun lookup, not from the user.
        bridge._predictor.learn_capitalization = MagicMock(return_value=True)
        for c in "ow":
            bridge.pressKey(c)
        bridge.pressPrediction("owen")
        bridge._predictor.learn_capitalization.assert_not_called()

    def test_pill_click_with_no_typed_prefix_does_not_teach_casing(
        self, bridge: KeyboardBridge,
    ):
        # Next-word prediction (nothing typed) — no signal of intent.
        bridge._predictor.learn_capitalization = MagicMock(return_value=True)
        bridge.pressPrediction("Hello")
        bridge._predictor.learn_capitalization.assert_not_called()

    def test_midword_capital_prefix_then_pill_click_teaches_casing(
        self, bridge: KeyboardBridge,
    ):
        # Mid-word right-click ("e" then right-click "B") is an even
        # stronger casing signal than a first-letter cap — there is no
        # sentence-start ambiguity, mid-word capitals only ever come from
        # brand / PascalCase intent.  The first-letter-only check missed
        # this entirely; the prefix's first char was lowercase 'e', so
        # learn_capitalization was never called and "ebay" stayed
        # uncased forever.
        bridge._predictor.learn_capitalization = MagicMock(return_value=True)
        bridge.pressKey("e")
        bridge.pressKeyLiteral("B")
        bridge.pressPrediction("eBay")
        bridge._predictor.learn_capitalization.assert_called_with(
            "eBay", allow_uppercase=True
        )


class TestAllCapsLearningGate:
    """Caps Lock must NOT teach the system that a word is canonically
    all-caps — that pollutes the table with shouty forms of every word
    the user typed under caps lock.  But right-clicking each letter to
    deliberately type all-caps IS a strong signal and should be learned."""

    def test_word_typed_all_caps_via_rightclick_learns_uppercase(
        self, bridge: KeyboardBridge,
    ):
        """User right-clicks each letter — Caps Lock is off the whole
        word.  At space, the all-caps form should be learned."""
        bridge._predictor.learn_capitalization = MagicMock(return_value=True)
        for ch in "HVAC":
            bridge.pressKeyLiteral(ch)
        assert bridge._word_typed_under_caps_lock is False
        bridge.pressSpecialKey("space")
        bridge._predictor.learn_capitalization.assert_called_with(
            "HVAC", allow_uppercase=True
        )

    def test_word_typed_all_caps_under_caps_lock_does_not_learn_uppercase(
        self, bridge: KeyboardBridge,
    ):
        """User toggles Caps Lock on, types HELLO — that's incidental
        all-caps, not a deliberate signal.  The bridge must pass
        ``allow_uppercase=False`` so the predictor's guard rejects it."""
        bridge._predictor.learn_capitalization = MagicMock(return_value=False)
        bridge.toggleCapsLock()
        for ch in "HELLO":
            bridge.pressKey(ch)
        assert bridge._word_typed_under_caps_lock is True
        bridge.pressSpecialKey("space")
        bridge._predictor.learn_capitalization.assert_called_with(
            "HELLO", allow_uppercase=False
        )

    def test_caps_lock_flag_resets_at_word_boundary(
        self, bridge: KeyboardBridge,
    ):
        """After space, the next word starts with a clean slate — caps
        lock used on the previous word does not taint the next one."""
        bridge.toggleCapsLock()
        bridge.pressKey("h")
        assert bridge._word_typed_under_caps_lock is True
        bridge.pressSpecialKey("space")
        assert bridge._word_typed_under_caps_lock is False

    def test_caps_lock_flag_resets_when_backspaced_to_empty(
        self, bridge: KeyboardBridge,
    ):
        """Backspacing all the way through the word resets the flag —
        the user is starting over."""
        bridge.toggleCapsLock()
        bridge.pressKey("h")
        assert bridge._word_typed_under_caps_lock is True
        bridge.pressSpecialKey("backspace")
        assert bridge._word_typed_under_caps_lock is False


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

    def test_display_cased_mirrors_caps_onto_non_prefix_predictions(self, bridge: KeyboardBridge):
        """Fuzzy / autocorrect pills don't always strict-prefix-match
        the typed letters.  Typing 'Hwl' (typo for 'Hel') still surfaces
        'hello' as a fuzzy candidate. The capital must be mirrored onto
        the displayed pill anyway; the prior strict-prefix gate dropped
        the cap on every fuzzy correction, so capitalised typings only
        looked right when no typo got involved."""
        bridge._current_word = "Hwl"
        assert bridge._display_cased(["hello", "help"]) == ["Hello", "Help"]

    def test_display_cased_mirrors_caps_onto_extra_letter_typo(self, bridge: KeyboardBridge):
        """Insertion typos ('Heilo' for 'Hello') produce a fuzzy
        correction that's *shorter* than the typed prefix.  Still mirror
        every uppercase position that lines up with a pill char."""
        bridge._current_word = "Heilo"
        assert bridge._display_cased(["hello"]) == ["Hello"]

    def test_display_cased_lowercase_prefix_unchanged(self, bridge: KeyboardBridge):
        """No shift means no case-matching — pure pass-through."""
        bridge._current_word = "hel"
        assert bridge._display_cased(["hello", "help"]) == ["hello", "help"]

    def test_display_cased_mirrors_all_caps_in_prefix(self, bridge: KeyboardBridge):
        """Right-clicking each of H, E, L produces 'HEL'. The pills must
        mirror every typed capital so the suffix-only insert path's
        case-sensitive startswith fires and the user sees what they
        typed reflected back."""
        bridge._current_word = "HEL"
        assert bridge._display_cased(["hello", "help"]) == ["HELlo", "HELp"]

    def test_display_cased_mirrors_partial_caps_in_prefix(self, bridge: KeyboardBridge):
        """Mixed casing 'HEl' (two right-clicks then a normal tap)
        mirrors only the typed uppercase positions."""
        bridge._current_word = "HEl"
        assert bridge._display_cased(["hello", "help"]) == ["HEllo", "HElp"]

    def test_display_cased_mirrors_midword_cap(self, bridge: KeyboardBridge):
        """A mid-word cap from a right-click — typed 'iP' — must be
        preserved on the pill, not lost. The first-letter-only gate
        used to skip this case because cw[0] was lowercase."""
        bridge._current_word = "iP"
        assert bridge._display_cased(["iphone", "ipad"]) == ["iPhone", "iPad"]

    def test_display_cased_mirrors_caps_onto_already_capped_prediction(
        self, bridge: KeyboardBridge
    ):
        """Pred already has a mid-word cap (e.g. 'iPhone' from proper
        nouns). User typed 'IP' — the mirror should add the I cap and
        keep the P cap."""
        bridge._current_word = "IP"
        assert bridge._display_cased(["iPhone"]) == ["IPhone"]


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
    """pressSpecialKey('space') runs misspelling/fuzzy autocorrect.

    Space-time autocorrect is OFF by default (a silent on-space
    replacement clobbered deliberate input — see
    `feedback_conservative_autotransform`).  Tests that exercise the
    autocorrect path turn it back on explicitly via
    ``setAutocorrectEnabled(True)``.
    """

    def test_default_is_off(self, bridge: KeyboardBridge):
        """Space-time autocorrect must NOT fire by default — even on
        a known misspelling, the literal typed word survives."""
        bridge._current_word = "recieve"
        bridge.pressSpecialKey("space")
        assert "recieve" in bridge._sentence_buffer
        assert "receive" not in bridge._sentence_buffer

    def test_known_misspelling_replaced_on_space(self, bridge: KeyboardBridge):
        bridge.setAutocorrectEnabled(True)
        bridge._current_word = "recieve"
        bridge.pressSpecialKey("space")
        # _current_word should have been corrected before clearing.
        # Check the post-space state — corrected word should land in
        # the sentence buffer, not the misspelling.
        assert "receive" in bridge._sentence_buffer
        assert "recieve" not in bridge._sentence_buffer

    def test_misspelling_casing_matches_typed_word(self, bridge: KeyboardBridge):
        bridge.setAutocorrectEnabled(True)
        bridge._current_word = "Recieve"
        bridge.pressSpecialKey("space")
        # Title-cased typed → title-cased correction.
        assert "Receive" in bridge._sentence_buffer

    def test_valid_word_not_corrected(self, bridge: KeyboardBridge):
        bridge.setAutocorrectEnabled(True)
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
        bridge.setAutocorrectEnabled(True)
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
