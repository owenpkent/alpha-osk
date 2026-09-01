"""Tests for the KeyboardBridge (Python↔QML bridge).

These tests verify modifier state management, context tracking,
and prediction wiring. Actual key synthesis is mocked since we
don't want tests injecting real keystrokes.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PySide6 = pytest.importorskip("PySide6")

from src import keyboard_bridge as kb
from src.keyboard_bridge import KeyboardBridge
from src.prediction.token_predictor import TokenPredictor
from src.snippets import SnippetStore


@pytest.fixture
def bridge(tmp_path: Path) -> KeyboardBridge:
    """Create a KeyboardBridge with mocked synthesizer.

    ``KeyboardBridge`` reads the developer's own config directory on
    construction, so two of its stores are unplugged here before any test
    touches them.  Both failures below are machine-dependent, which means
    they are invisible on CI and reproduce for one person.
    """
    with patch("src.keyboard_bridge.create_key_synthesizer") as mock_factory:
        mock_synth = MagicMock()
        mock_synth.is_available.return_value = True
        mock_synth.backend_name.return_value = "MockSynth"
        mock_factory.return_value = mock_synth
        b = KeyboardBridge()
        b._synth = mock_synth
        # **The snippet store is swapped before anything can write to it.**
        # SnippetStore saves synchronously on every mutation, and the
        # auto-detection tests below call acceptSnippetOffer() and
        # _snippets.set(), so without this the suite edits the developer's
        # own saved snippets: their email and phone number, replaced by
        # this file's example values, with no undo and no backup.
        b._snippets = SnippetStore(tmp_path / "snippets.json")
        b._snippets.load()
        # The real ngram_model.json carries a "tokens" key since the
        # structured-token work.  A token typed for real is prefix-matched
        # and count-ranked exactly like one a test teaches, so one address
        # in the developer's own model outranks the domain
        # TestEmailDomainSuggestions just learned.  The word model is
        # worked around per-test with synthetic zzq*/zephyrish tokens; the
        # token store has no such escape, because its whole matching rule
        # is "any prefix of anything typed", so it is emptied instead.
        b._predictor._ngram.tokens = TokenPredictor()
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
        assert bridge._current_word == "hel"  # preserved
        assert bridge._last_foreground_hwnd == 42  # but seeded

    def test_check_foreground_noop_when_unavailable(self, bridge: KeyboardBridge, monkeypatch):
        """If we can't read the focused window, leave state alone."""
        bridge._last_foreground_hwnd = 100
        bridge._current_word = "hel"
        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 0)
        bridge._check_foreground_window()
        assert bridge._current_word == "hel"
        assert bridge._last_foreground_hwnd == 100  # unchanged

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
        assert bridge._last_foreground_hwnd == 100  # window never changed

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
        assert bridge._current_word == "hel"  # preserved
        assert bridge._last_focus_token == "A"  # seeded

    def test_focus_token_unreadable_keeps_baseline(self, bridge: KeyboardBridge, monkeypatch):
        """A None token ('don't know') must not wipe or clobber the baseline."""
        bridge._last_foreground_hwnd = 100
        bridge._last_focus_token = "A"
        bridge._current_word = "hel"
        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 100)
        monkeypatch.setattr("src.keyboard_bridge.focused_element_token", lambda: None)
        bridge._check_foreground_window()
        assert bridge._current_word == "hel"
        assert bridge._last_focus_token == "A"  # unchanged


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
        bridge.toggleShift()  # Shift on independently
        bridge.toggleCapsLock()  # Caps on
        bridge.toggleCapsLock()  # Caps off
        assert not bridge._caps_lock_active
        assert bridge._shift_active  # Shift state preserved

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

    def test_release_shift_is_idempotent(self, bridge: KeyboardBridge):
        """The compact layer switch asks for a state, not a flip.

        It used to call `if (root.shiftOn) keyboard.toggleShift()`, which
        made the correctness of a *release* depend on QML's mirror of the
        bridge agreeing with the bridge. `root.shiftOn` starts as a binding
        but is imperatively reassigned by the shiftActiveChanged handler,
        which breaks the binding permanently, so from then on it is only as
        accurate as signal delivery. One missed emit and the toggle would
        turn Shift *on*, on a symbol page that has no Shift key to clear it
        and where the OS-held modifier makes "1" emit "!" under a keycap
        still reading "1". Calling releaseShift twice must be as safe as
        calling it once.
        """
        bridge.toggleShift()
        assert bridge._shift_active is True

        bridge.releaseShift()
        assert bridge._shift_active is False
        bridge._synth.release_modifier.assert_called_with("shift")

        bridge._synth.reset_mock()
        bridge.releaseShift()
        assert bridge._shift_active is False, "releaseShift must never turn Shift on"
        bridge._synth.hold_modifier.assert_not_called()

    def test_release_shift_clears_a_right_click_lock(self, bridge: KeyboardBridge):
        """A locked Shift mismatches the keycaps as loudly as a sticky one."""
        bridge.lockModifier("shift")
        assert bridge._shift_locked is True

        bridge.releaseShift()
        assert bridge._shift_active is False
        assert bridge._shift_locked is False
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

    def test_reset_modifiers_clears_all_held(self, bridge: KeyboardBridge):
        # Clean-slate on open: any sticky modifier left active (from a
        # prior run, a crash mid-chord, or an external grab) is dropped so
        # the user never starts a session with a stuck modifier.
        bridge.toggleShift()
        bridge.toggleCtrl()
        bridge.toggleAlt()
        bridge.toggleWin()
        bridge.resetModifiers()
        assert not bridge._shift_active
        assert not bridge._ctrl_active
        assert not bridge._alt_active
        assert not bridge._win_active

    def test_reset_modifiers_releases_os_state(self, bridge: KeyboardBridge):
        bridge._synth.reset_modifier_state.reset_mock()
        bridge.resetModifiers()
        bridge._synth.reset_modifier_state.assert_called_once()

    def test_reset_modifiers_preserves_caps_lock(self, bridge: KeyboardBridge):
        # Caps Lock holds nothing at the OS level (can't get stuck) and is
        # a deliberate persistent toggle — reset must not flip it off.
        bridge.toggleCapsLock()
        bridge.resetModifiers()
        assert bridge._caps_lock_active

    def test_shift_persists_through_arrow_key(self, bridge: KeyboardBridge):
        # Regression: holding Shift and pressing an arrow (to extend a
        # selection) used to drop Shift after the first press, so the
        # second arrow no longer extended the selection — and an
        # auto-repeating held arrow lost Shift after its first tick. Nav
        # keys must keep Shift held across presses; the user taps Shift
        # again to release it.
        bridge.toggleShift()
        for key in ("left", "right", "up", "down", "home", "end", "pageup", "pagedown"):
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


class TestModifierLock:
    """Right-click 'lock' (held-down) modifiers.

    A locked modifier is held at the OS level and exempt from the
    per-keystroke auto-release, so the user can fire several combos or
    hold Shift across many keys without re-tapping.
    """

    def test_lock_ctrl_holds_at_os_level(self, bridge: KeyboardBridge):
        bridge.lockModifier("ctrl")
        assert bridge._ctrl_locked
        assert bridge._ctrl_active
        bridge._synth.hold_modifier.assert_called_with("ctrl")

    def test_locked_ctrl_survives_chord(self, bridge: KeyboardBridge):
        # The whole point: Ctrl+C then Ctrl+V without re-tapping Ctrl.
        bridge.lockModifier("ctrl")
        bridge.pressKey("c")
        assert bridge._ctrl_active, "locked Ctrl released after a chord"
        bridge.pressKey("v")
        assert bridge._ctrl_active

    def test_locked_shift_survives_character(self, bridge: KeyboardBridge):
        # Locked Shift keeps producing uppercase across many letters.
        bridge.lockModifier("shift")
        bridge.pressKey("a")
        bridge._synth.send_text.assert_called_with("A")
        bridge.pressKey("b")
        assert bridge._shift_active
        bridge._synth.send_text.assert_called_with("B")

    def test_locked_alt_survives_special_key(self, bridge: KeyboardBridge):
        bridge.lockModifier("alt")
        bridge.pressSpecialKey("tab")
        assert bridge._alt_active  # Alt+Tab held for repeat cycling

    def test_locked_ctrl_survives_non_nav_special(self, bridge: KeyboardBridge):
        # A sticky Ctrl drops after Backspace; a locked one must not.
        bridge.lockModifier("ctrl")
        bridge.pressSpecialKey("backspace")
        assert bridge._ctrl_active

    def test_second_lock_releases(self, bridge: KeyboardBridge):
        bridge.lockModifier("ctrl")
        bridge._synth.release_modifier.reset_mock()
        bridge.lockModifier("ctrl")
        assert not bridge._ctrl_locked
        assert not bridge._ctrl_active
        bridge._synth.release_modifier.assert_called_with("ctrl")

    def test_tap_clears_lock(self, bridge: KeyboardBridge):
        # A plain left-tap on a locked modifier releases it (easy way out).
        bridge.lockModifier("ctrl")
        bridge.toggleCtrl()
        assert not bridge._ctrl_locked
        assert not bridge._ctrl_active

    def test_lock_over_sticky_active_no_redundant_hold(self, bridge: KeyboardBridge):
        # Right-clicking an already-sticky-active modifier should lock it
        # without re-sending a key-down (it's already held).
        bridge.toggleCtrl()  # sticky active
        bridge._synth.hold_modifier.reset_mock()
        bridge.lockModifier("ctrl")
        assert bridge._ctrl_locked
        bridge._synth.hold_modifier.assert_not_called()

    def test_lock_emits_locked_signal(self, bridge: KeyboardBridge):
        emissions = []
        bridge.ctrlLockedChanged.connect(emissions.append)
        bridge.lockModifier("ctrl")
        assert emissions and emissions[-1] is True
        bridge.lockModifier("ctrl")
        assert emissions[-1] is False

    def test_lock_shift_switches_to_upper_layer(self, bridge: KeyboardBridge):
        bridge.lockModifier("shift")
        assert bridge._current_layer == "upper"

    def test_lock_unknown_modifier_is_noop(self, bridge: KeyboardBridge):
        bridge.lockModifier("caps")  # caps is already persistent
        assert not bridge._shift_locked
        assert not bridge._ctrl_locked

    def test_shutdown_releases_locked_modifier(self, bridge: KeyboardBridge):
        bridge.lockModifier("ctrl")
        bridge._synth.release_modifier.reset_mock()
        bridge.shutdown()
        bridge._synth.release_modifier.assert_any_call("ctrl")
        assert not bridge._ctrl_locked


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

    def test_unreal_editor_windowed_is_game(self, monkeypatch):
        # The windowed Unreal Editor must hit the held path (PIE polls input);
        # the fullscreen heuristic must not be needed.
        import sys

        from src import keyboard_bridge

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(keyboard_bridge, "_owning_exe_name", lambda h: "unrealeditor.exe")
        monkeypatch.setattr(keyboard_bridge, "_window_is_borderless_fullscreen", lambda h: False)
        assert keyboard_bridge._window_is_game(999) is True

    def test_unreal_shipping_suffix_is_game(self, monkeypatch):
        # Any packaged UE title matches by the -Win64-Shipping.exe suffix,
        # even windowed.
        import sys

        from src import keyboard_bridge

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            keyboard_bridge, "_owning_exe_name", lambda h: "ramms-win64-shipping.exe"
        )
        monkeypatch.setattr(keyboard_bridge, "_window_is_borderless_fullscreen", lambda h: False)
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
        self,
        bridge: KeyboardBridge,
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
            c for c in bridge._synth.send_key.call_args_list if c.args and c.args[0] == "BackSpace"
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
        self,
        bridge: KeyboardBridge,
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
                f"{ch!r} should be a word boundary; _current_word was {bridge._current_word!r}"
            )
            assert bridge._context_buffer.endswith(ch), f"{ch!r} should remain in context_buffer"

    def test_every_glyph_a_layout_can_type_is_a_word_boundary(
        self,
        bridge: KeyboardBridge,
    ):
        """Stated over the layout files, not over a hand-written list.

        The boundary check used to be a literal tuple of separators, so it
        failed open: the second symbol page added 18 glyphs (maths, currency,
        legal marks, the bullet) and every one of them was appended to
        _current_word instead, making "cost€" the prediction prefix and the
        learned token. Deriving the case list from data/layouts means the next
        page of glyphs is covered without anyone remembering to come back
        here.
        """
        import json
        from pathlib import Path

        glyphs = set()
        for path in Path("data/layouts").glob("*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            for row in data["rows"]:
                for key in row["keys"]:
                    if key.get("type") == "char" and key.get("key"):
                        glyphs.add(key["key"])
                    if key.get("shifted"):
                        glyphs.add(key["shifted"])
        # Word characters, deliberately: letters and digits are word content,
        # the apostrophe keeps contractions whole, the underscore keeps
        # snake_case identifiers whole.
        separators = sorted(g for g in glyphs if not (g.isalnum() or g in ("'", "_")))
        assert len(separators) > 30, "layout scrape found suspiciously few glyphs"

        for ch in separators:
            bridge._current_word = ""
            bridge._context_buffer = ""
            bridge._sentence_buffer = ""
            bridge.pressKeyLiteral(ch)
            for c in "hel":
                bridge.pressKeyLiteral(c)
            assert bridge._current_word == "hel", (
                f"{ch!r} is typeable from a shipped layout but is not a word "
                f"boundary; _current_word was {bridge._current_word!r}"
            )

    def test_asterisk_prefix_prediction_click_keeps_asterisk(
        self,
        bridge: KeyboardBridge,
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
            c for c in bridge._synth.send_key.call_args_list if c.args and c.args[0] == "BackSpace"
        ]
        assert backspace_calls == [], "BackSpace was sent — would have eaten the leading '*'"
        bridge._synth.replace_text.assert_not_called()
        bridge._synth.send_text.assert_any_call("lo ")

    def test_backspace_into_completed_word_rehydrates_current_word(
        self,
        bridge: KeyboardBridge,
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
        self,
        bridge: KeyboardBridge,
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
        self,
        bridge: KeyboardBridge,
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
        self,
        bridge: KeyboardBridge,
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
        self,
        bridge: KeyboardBridge,
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
        self,
        bridge: KeyboardBridge,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.setattr("src.platform.get_config_dir", lambda: tmp_path)
        result = bridge.consumeUpdateHandoff()
        assert result == {}

    def test_reads_and_returns_payload(self, bridge: KeyboardBridge, tmp_path, monkeypatch):
        import json as _json
        import time as _time

        monkeypatch.setattr("src.platform.get_config_dir", lambda: tmp_path)
        (tmp_path / "update_handoff.json").write_text(
            _json.dumps(
                {
                    "version": "1.0.16",
                    "previous_version": "1.0.15",
                    "completed_at": _time.time(),
                }
            )
        )
        result = bridge.consumeUpdateHandoff()
        assert result == {"version": "1.0.16", "previousVersion": "1.0.15"}

    def test_deletes_file_after_read(self, bridge: KeyboardBridge, tmp_path, monkeypatch):
        import json as _json
        import time as _time

        monkeypatch.setattr("src.platform.get_config_dir", lambda: tmp_path)
        path = tmp_path / "update_handoff.json"
        path.write_text(
            _json.dumps(
                {
                    "version": "1.0.16",
                    "previous_version": "1.0.15",
                    "completed_at": _time.time(),
                }
            )
        )
        bridge.consumeUpdateHandoff()
        # Single-use breadcrumb — the next launch must not re-toast.
        assert not path.exists()
        # Subsequent calls return empty.
        assert bridge.consumeUpdateHandoff() == {}

    def test_stale_handoff_is_discarded(self, bridge: KeyboardBridge, tmp_path, monkeypatch):
        import json as _json

        monkeypatch.setattr("src.platform.get_config_dir", lambda: tmp_path)
        # 10 minutes ago — older than the 5-min freshness window.
        (tmp_path / "update_handoff.json").write_text(
            _json.dumps(
                {
                    "version": "1.0.16",
                    "previous_version": "1.0.15",
                    "completed_at": time.time() - 600,
                }
            )
        )
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
        self,
        bridge: KeyboardBridge,
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
            c for c in bridge._synth.send_key.call_args_list if c.args and c.args[0] == "BackSpace"
        ]
        assert len(backspace_calls) == 3
        bridge._synth.send_text.assert_any_call("hello ")
        bridge._synth.replace_text.assert_not_called()

    def test_press_prediction_keeps_suffix_only_when_compat_mode_off(
        self,
        bridge: KeyboardBridge,
    ):
        # Default (compat mode off) — must keep the existing suffix-only
        # path so chat composers (Slack/Teams/Discord) keep working.
        for c in "hel":
            bridge.pressKey(c)
        bridge._synth.reset_mock()
        bridge.pressPrediction("hello")
        # No BackSpaces should have been sent — only the suffix.
        backspace_calls = [
            c for c in bridge._synth.send_key.call_args_list if c.args and c.args[0] == "BackSpace"
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
        self,
        bridge: KeyboardBridge,
    ):
        # Right-click flow: pressKeyLiteral types the capital verbatim
        # (no shift state involved) — same _current_word state as a
        # manual shift would produce.
        bridge._predictor.learn_capitalization = MagicMock(return_value=True)
        bridge.pressKeyLiteral("O")
        bridge.pressKey("w")
        bridge.pressKey("e")
        bridge.pressPrediction("Owen")
        bridge._predictor.learn_capitalization.assert_called_with("Owen", allow_uppercase=True)

    def test_lowercase_prefix_then_pill_click_does_not_teach_casing(
        self,
        bridge: KeyboardBridge,
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
        self,
        bridge: KeyboardBridge,
    ):
        # Next-word prediction (nothing typed) — no signal of intent.
        bridge._predictor.learn_capitalization = MagicMock(return_value=True)
        bridge.pressPrediction("Hello")
        bridge._predictor.learn_capitalization.assert_not_called()

    def test_midword_capital_prefix_then_pill_click_teaches_casing(
        self,
        bridge: KeyboardBridge,
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
        bridge._predictor.learn_capitalization.assert_called_with("eBay", allow_uppercase=True)


class TestAllCapsLearningGate:
    """Caps Lock must NOT teach the system that a word is canonically
    all-caps — that pollutes the table with shouty forms of every word
    the user typed under caps lock.  But right-clicking each letter to
    deliberately type all-caps IS a strong signal and should be learned."""

    def test_word_typed_all_caps_via_rightclick_learns_uppercase(
        self,
        bridge: KeyboardBridge,
    ):
        """User right-clicks each letter — Caps Lock is off the whole
        word.  At space, the all-caps form should be learned."""
        bridge._predictor.learn_capitalization = MagicMock(return_value=True)
        for ch in "HVAC":
            bridge.pressKeyLiteral(ch)
        assert bridge._word_typed_under_caps_lock is False
        bridge.pressSpecialKey("space")
        bridge._predictor.learn_capitalization.assert_called_with("HVAC", allow_uppercase=True)

    def test_word_typed_all_caps_under_caps_lock_does_not_learn_uppercase(
        self,
        bridge: KeyboardBridge,
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
        bridge._predictor.learn_capitalization.assert_called_with("HELLO", allow_uppercase=False)

    def test_caps_lock_flag_resets_at_word_boundary(
        self,
        bridge: KeyboardBridge,
    ):
        """After space, the next word starts with a clean slate — caps
        lock used on the previous word does not taint the next one."""
        bridge.toggleCapsLock()
        bridge.pressKey("h")
        assert bridge._word_typed_under_caps_lock is True
        bridge.pressSpecialKey("space")
        assert bridge._word_typed_under_caps_lock is False

    def test_caps_lock_flag_resets_when_backspaced_to_empty(
        self,
        bridge: KeyboardBridge,
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
        assert typed == []  # no signal
        bridge._synth.send_text.assert_called()  # synth path taken

    def test_set_edit_mode_toggles_flag(self, bridge: KeyboardBridge):
        assert bridge._edit_mode_active is False
        bridge.setEditMode(True)
        assert bridge._edit_mode_active is True
        bridge.setEditMode(False)
        assert bridge._edit_mode_active is False


class TestEditModeChords:
    """Ctrl chords in an edit field act on the field, and never leak.

    Ctrl/Alt/Win were documented as "ignored inside the field so stray
    chords cannot leak to the app behind us". The leak half held; the
    other half did not. The modifier was simply skipped and the *letter*
    inserted, so Ctrl+A in the snippets editor typed "a" and Ctrl+B typed
    "b".

    The clipboard set is the reason this is worth wiring rather than
    merely swallowing: every character of a long address costs a click on
    this keyboard, so pasting one in from somewhere else is the
    difference between a snippet being worth making and not.
    """

    @staticmethod
    def _collect(signal) -> list:
        seen: list = []
        signal.connect(lambda *args: seen.append(args))
        return seen

    @pytest.mark.parametrize(
        ("letter", "action"),
        [("a", "selectall"), ("c", "copy"), ("v", "paste"), ("x", "cut"), ("z", "undo")],
    )
    def test_a_ctrl_chord_becomes_an_edit_action(
        self, bridge: KeyboardBridge, letter: str, action: str
    ):
        specials = self._collect(bridge.editSpecialPressed)
        typed = self._collect(bridge.editKeyTyped)
        bridge.setEditMode(True)
        bridge.toggleCtrl()

        bridge.pressKey(letter)

        assert specials == [(action,)]
        assert typed == [], "the chord was typed as a letter as well"

    def test_an_unknown_ctrl_chord_types_nothing(self, bridge: KeyboardBridge):
        """Ctrl+B used to insert "b"."""
        typed = self._collect(bridge.editKeyTyped)
        specials = self._collect(bridge.editSpecialPressed)
        bridge.setEditMode(True)
        bridge.toggleCtrl()

        bridge.pressKey("b")

        assert typed == []
        assert specials == []

    def test_a_chord_never_reaches_the_app_behind_us(self, bridge: KeyboardBridge):
        bridge.setEditMode(True)
        bridge.toggleCtrl()
        bridge._synth.send_text.reset_mock()
        bridge._synth.send_key.reset_mock()

        bridge.pressKey("v")

        bridge._synth.send_text.assert_not_called()
        bridge._synth.send_key.assert_not_called()

    def test_the_modifier_is_released_after_the_chord(self, bridge: KeyboardBridge):
        """Same one-keystroke release every other path does, or the next
        plain letter in the field would be eaten as another chord."""
        bridge.setEditMode(True)
        bridge.toggleCtrl()
        assert bridge._ctrl_active is True

        bridge.pressKey("a")

        assert bridge._ctrl_active is False
        bridge._synth.release_modifier.assert_any_call("ctrl")

    def test_a_locked_modifier_survives_the_chord(self, bridge: KeyboardBridge):
        """Right-click lock outranks the auto-release here as everywhere."""
        bridge.setEditMode(True)
        bridge.lockModifier("ctrl")

        bridge.pressKey("c")

        assert bridge._ctrl_active is True
        assert bridge._ctrl_locked is True

    def test_a_plain_letter_is_unaffected(self, bridge: KeyboardBridge):
        typed = self._collect(bridge.editKeyTyped)
        bridge.setEditMode(True)

        bridge.pressKey("a")

        assert typed == [("a",)]


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
            c for c in bridge._synth.send_key.call_args_list if c[0][0] == "BackSpace"
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
            c for c in bridge._synth.send_key.call_args_list if c[0][0] == "BackSpace"
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


class TestTheClosingQuoteRemovesTheAutoSpace:
    """`"` is the one mark that both opens and closes, so it earns the
    space removal only when parity says it is closing something.  Every
    case is paired with the near-miss it has to leave alone.
    """

    def test_a_closing_quote_after_a_sentence_removes_the_auto_space(self, bridge: KeyboardBridge):
        """`"hi." ` is not a thing anyone types."""
        bridge._auto_space_after_punctuation = True
        for ch in '"hi':
            bridge.pressKey(ch)
        bridge.pressKey(".")
        bridge._synth.send_key.reset_mock()
        bridge.pressKey('"')
        bridge._synth.send_key.assert_any_call("BackSpace", modifiers=None)

    def test_an_opening_quote_keeps_the_auto_space(self, bridge: KeyboardBridge):
        """The inverse, and the reason `"` cannot just join
        _NO_SPACE_BEFORE: `he said, "hi` wants that space.
        """
        bridge._auto_space_after_punctuation = True
        for ch in "he":
            bridge.pressKey(ch)
        bridge.pressKey(",")
        bridge._synth.send_key.reset_mock()
        bridge.pressKey('"')
        backspaces = [c for c in bridge._synth.send_key.call_args_list if c[0][0] == "BackSpace"]
        assert backspaces == []

    def test_a_closing_quote_after_a_prediction_removes_the_auto_space(
        self, bridge: KeyboardBridge
    ):
        """The common case: the pill's own trailing space, then the quote
        that closes the quotation it was typed inside.
        """
        bridge.pressKey('"')
        bridge.pressKey("h")
        bridge.pressPrediction("hello")
        bridge._synth.send_key.reset_mock()
        bridge.pressKey('"')
        bridge._synth.send_key.assert_any_call("BackSpace", modifiers=None)

    def test_a_user_typed_space_before_a_closing_quote_is_preserved(self, bridge: KeyboardBridge):
        """Only our own auto-space is ever undone, same rule the other
        marks follow.
        """
        bridge.pressKey('"')
        for ch in "hi":
            bridge.pressKey(ch)
        bridge.pressSpecialKey("space")
        bridge._synth.send_key.reset_mock()
        bridge.pressKey('"')
        backspaces = [c for c in bridge._synth.send_key.call_args_list if c[0][0] == "BackSpace"]
        assert backspaces == []


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
        assert KeyboardBridge._sanitize_edit(42) == ""  # type: ignore[arg-type]

    def test_normal_input_preserved(self):
        from src.keyboard_bridge import KeyboardBridge

        assert KeyboardBridge._sanitize_edit("iPhone") == "iPhone"
        assert KeyboardBridge._sanitize_edit("  hello  ") == "hello"


class TestNoTypedContentInLogs:
    """No log record at INFO or above may contain what the user typed: a
    word, _current_word, _context_buffer, or a prediction list.

    The app logs to a plaintext RotatingFileHandler (see
    keyboard_app.py::_configure_logging) that is not gated by privacy
    mode, so anything logged here is a leak regardless of whether the
    user was in a password field at the time. Security-audit
    remediation: see CLAUDE.md "Privacy Mode & Password Detection".
    """

    # Distinctive enough that no unrelated log boilerplate could ever
    # contain it as a substring, so "not in caplog.text" is unambiguous.
    _SENTINEL = "zzqxplorbnium"

    def test_press_prediction_does_not_log_the_word(self, bridge: KeyboardBridge, caplog):
        with caplog.at_level(logging.INFO, logger="KeyboardBridge"):
            bridge.pressPrediction(self._SENTINEL)
        assert self._SENTINEL not in caplog.text

    def test_edit_prediction_does_not_log_the_word(self, bridge: KeyboardBridge, caplog):
        with caplog.at_level(logging.INFO, logger="KeyboardBridge"):
            bridge.editPrediction("typo", self._SENTINEL)
        assert self._SENTINEL not in caplog.text

    def test_word_completion_does_not_log_the_word(self, bridge: KeyboardBridge, caplog):
        with caplog.at_level(logging.INFO, logger="KeyboardBridge"):
            for c in self._SENTINEL:
                bridge.pressKey(c)
            bridge.pressSpecialKey("space")
        assert self._SENTINEL not in caplog.text

    def test_sentence_end_does_not_log_the_word(self, bridge: KeyboardBridge, caplog):
        with caplog.at_level(logging.INFO, logger="KeyboardBridge"):
            for c in self._SENTINEL:
                bridge.pressKey(c)
            bridge.pressKey(".")
        assert self._SENTINEL not in caplog.text

    def test_return_key_does_not_log_the_word(self, bridge: KeyboardBridge, caplog):
        with caplog.at_level(logging.INFO, logger="KeyboardBridge"):
            for c in self._SENTINEL:
                bridge.pressKey(c)
            bridge.pressSpecialKey("return")
        assert self._SENTINEL not in caplog.text

    def test_autocorrect_does_not_log_typed_or_corrected_word(self, bridge: KeyboardBridge, caplog):
        bridge.setAutocorrectEnabled(True)
        bridge._predictor.check_autocorrect = MagicMock(return_value=self._SENTINEL)
        with caplog.at_level(logging.INFO, logger="KeyboardBridge"):
            for c in "zyxw":  # the "typo" being corrected
                bridge.pressKey(c)
            bridge.pressSpecialKey("space")
        assert self._SENTINEL not in caplog.text
        assert "zyxw" not in caplog.text

    def test_next_word_prediction_context_not_logged(self, bridge: KeyboardBridge, caplog):
        bridge._context_buffer = self._SENTINEL + " "
        with caplog.at_level(logging.INFO, logger="KeyboardBridge"):
            bridge.pressPrediction("hello")
        assert self._SENTINEL not in caplog.text


class TestPressPredictionPrivacyMode:
    """pressPrediction must still type the tapped word into the target
    app while in privacy mode (the user tapped a visible pill, so the
    text must reach the app), but must not persist the selection into
    analytics or the model. Mirrors the guards _press_char already has.
    See CLAUDE.md "Privacy Mode & Password Detection".
    """

    def test_privacy_mode_still_inserts_the_word(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._synth.reset_mock()
        bridge.pressPrediction("hello")
        bridge._synth.send_text.assert_any_call("hello ")

    def test_privacy_mode_respects_typed_prefix_for_insertion(self, bridge: KeyboardBridge):
        """Even under privacy mode, a non-empty prefix still gets
        suffix-only insertion rather than the full word duplicated."""
        bridge.setPrivacyMode(True)
        bridge._current_word = "hel"
        bridge._synth.reset_mock()
        bridge.pressPrediction("hello")
        bridge._synth.send_text.assert_any_call("lo ")

    def test_privacy_mode_suppresses_analytics(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._analytics.record_prediction_selected = MagicMock()
        bridge.pressPrediction("hello")
        bridge._analytics.record_prediction_selected.assert_not_called()

    def test_privacy_mode_suppresses_learn_from_selection(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._predictor.learn_from_selection = MagicMock()
        bridge.pressPrediction("hello")
        bridge._predictor.learn_from_selection.assert_not_called()

    def test_privacy_mode_suppresses_capitalization_learning(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._current_word = "Hel"
        bridge._predictor.learn_capitalization = MagicMock(return_value=True)
        bridge.pressPrediction("Hello")
        bridge._predictor.learn_capitalization.assert_not_called()

    def test_privacy_mode_does_not_grow_context_buffer(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._context_buffer = ""
        bridge.pressPrediction("hello")
        # Must not linger: it would otherwise feed learn_from_selection() /
        # predict() on the next call, even after privacy mode ends.
        assert bridge._context_buffer == ""

    def test_privacy_mode_still_clears_current_word(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._current_word = "hel"
        bridge.pressPrediction("hello")
        assert bridge._current_word == ""

    def test_privacy_mode_still_refreshes_predictions(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        emitted: list = []
        bridge.predictionsChanged.connect(lambda preds: emitted.append(list(preds)))
        bridge.pressPrediction("hello")
        # Privacy mode must not freeze the pill bar mid-tap.
        assert emitted, "predictionsChanged never fired"

    def test_normal_mode_still_persists_everything(self, bridge: KeyboardBridge):
        """Sanity check: the new guards are privacy-specific, not a
        general regression that always skips persistence."""
        bridge._current_word = "hel"
        bridge._analytics.record_prediction_selected = MagicMock()
        bridge._predictor.learn_from_selection = MagicMock()
        bridge.pressPrediction("hello")
        bridge._analytics.record_prediction_selected.assert_called_once()
        bridge._predictor.learn_from_selection.assert_called_once()


class TestEditPredictionPrivacyMode:
    """editPrediction must still insert the user's typed correction while
    in privacy mode, but must not persist it into the model."""

    def test_privacy_mode_still_inserts(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._synth.reset_mock()
        bridge.editPrediction("helo", "hello")
        bridge._synth.replace_text.assert_called_once()

    def test_privacy_mode_suppresses_set_capitalization(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._predictor.set_capitalization = MagicMock()
        bridge.editPrediction("iphone", "iPhone")
        bridge._predictor.set_capitalization.assert_not_called()

    def test_privacy_mode_does_not_grow_context_buffer(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._context_buffer = ""
        bridge.editPrediction("helo", "hello")
        assert bridge._context_buffer == ""

    def test_privacy_mode_still_clears_current_word(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        bridge._current_word = "helo"
        bridge.editPrediction("helo", "hello")
        assert bridge._current_word == ""

    def test_privacy_mode_still_refreshes_predictions(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        emitted: list = []
        bridge.predictionsChanged.connect(lambda preds: emitted.append(list(preds)))
        bridge.editPrediction("helo", "hello")
        assert emitted, "predictionsChanged never fired"

    def test_normal_mode_still_persists(self, bridge: KeyboardBridge):
        bridge._predictor.set_capitalization = MagicMock()
        bridge.editPrediction("iphone", "iPhone")
        bridge._predictor.set_capitalization.assert_called_once_with("iPhone", "iPhone")


class TestPasswordDetectionAvailableProperty:
    """KeyboardBridge.passwordDetectionAvailable surfaces whether the
    platform's auto-detect backend is real or the null fallback, so the
    UI can tell the user the manual Learning toggle is their only
    protection this session. See
    src/platform/password_detect.py::detection_available.
    """

    def _make_bridge(self, monkeypatch, available: bool) -> KeyboardBridge:
        monkeypatch.setattr("src.keyboard_bridge.detection_available", lambda: available)
        with patch("src.keyboard_bridge.create_key_synthesizer") as mock_factory:
            mock_synth = MagicMock()
            mock_synth.is_available.return_value = True
            mock_synth.backend_name.return_value = "MockSynth"
            mock_factory.return_value = mock_synth
            return KeyboardBridge()

    def test_true_when_backend_available(self, monkeypatch):
        b = self._make_bridge(monkeypatch, True)
        assert b.passwordDetectionAvailable is True

    def test_false_when_backend_unavailable(self, monkeypatch):
        b = self._make_bridge(monkeypatch, False)
        assert b.passwordDetectionAvailable is False


def _typed(bridge: KeyboardBridge) -> list[str]:
    """Every string the bridge sent to the synthesizer, in order."""
    return [c[0][0] for c in bridge._synth.send_text.call_args_list]


def _type(bridge: KeyboardBridge, text: str) -> None:
    """Drive the bridge through *text* one keystroke at a time.

    Space goes through pressSpecialKey because that is the path QML uses
    and the one that completes a word; everything else is a char key.
    """
    for ch in text:
        if ch == " ":
            bridge.pressSpecialKey("space")
        else:
            bridge.pressKeyLiteral(ch)


def _type_cased(bridge: KeyboardBridge, text: str) -> None:
    """Drive the bridge through *text* via ``pressKey``, the casing path.

    ``_type`` uses ``pressKeyLiteral``, which is what QML calls once it
    has already resolved the character (a right-clicked shifted
    variant), so Shift and Caps Lock do not touch it.  A test about what
    Caps Lock does to the typed run therefore has to come in through
    this entry point instead, or it is quietly asserting against
    lowercase input and passes whether or not the code under test is
    correct.
    """
    for ch in text:
        if ch == " ":
            bridge.pressSpecialKey("space")
        else:
            bridge.pressKey(ch)


class TestShiftCapitalizesSuggestions:
    """Shift capitalises the suggestion pills, the way Caps Lock already did.

    Shift means "the next character is a capital", and a tapped pill *is*
    the next thing typed.  Without this the only way to capitalise a
    suggested word was to type its first letter shifted and wait for the
    typed-prefix mirror to catch up, which defeats the point of tapping
    a pill at all.
    """

    def test_shift_capitalizes_the_first_letter(self, bridge: KeyboardBridge):
        bridge._shift_active = True
        assert bridge._display_cased(["hello", "help"]) == ["Hello", "Help"]

    def test_without_shift_nothing_changes(self, bridge: KeyboardBridge):
        """The inverse: "always capitalise" would pass the test above."""
        bridge._shift_active = False
        assert bridge._display_cased(["hello", "help"]) == ["hello", "help"]

    def test_only_the_first_letter(self, bridge: KeyboardBridge):
        """Shift is one character's worth of capital, not a mode."""
        bridge._shift_active = True
        assert bridge._display_cased(["hello"]) == ["Hello"]

    def test_caps_lock_still_wins(self, bridge: KeyboardBridge):
        bridge._caps_lock_active = True
        bridge._shift_active = True
        assert bridge._display_cased(["hello"]) == ["HELLO"]

    def test_a_typed_uppercase_prefix_still_wins(self, bridge: KeyboardBridge):
        """An uppercase already in the prefix says more about the word's
        shape (mid-word caps like "iP" -> "iPhone") than a pending Shift.
        """
        bridge._shift_active = True
        bridge._current_word = "iP"
        assert bridge._display_cased(["iphone"]) == ["iPhone"]

    def test_empty_predictions_are_left_alone(self, bridge: KeyboardBridge):
        bridge._shift_active = True
        assert bridge._display_cased([]) == []

    def test_toggling_shift_redraws_the_visible_pills(self, bridge: KeyboardBridge):
        """Otherwise the bar contradicts the keycaps until the next keystroke."""
        bridge._predictions = ["hello"]
        with patch.object(bridge, "_update_predictions") as refresh:
            bridge.toggleShift()
        refresh.assert_called_once()

    def test_releasing_shift_redraws_them_too(self, bridge: KeyboardBridge):
        bridge._shift_active = True
        bridge._predictions = ["Hello"]
        with patch.object(bridge, "_update_predictions") as refresh:
            bridge.releaseShift()
        refresh.assert_called_once()

    def test_locking_shift_redraws_them_too(self, bridge: KeyboardBridge):
        """Only the held state matters, not how it came to be held."""
        bridge._predictions = ["hello"]
        with patch.object(bridge, "_update_predictions") as refresh:
            bridge.lockModifier("shift")
        refresh.assert_called_once()

    def test_an_empty_bar_costs_no_prediction_round_trip(self, bridge: KeyboardBridge):
        bridge._predictions = []
        with patch.object(bridge, "_update_predictions") as refresh:
            bridge.toggleShift()
        refresh.assert_not_called()


class TestVerbatimInsertsSurviveAHeldModifier:
    """A modifier held at the OS level rewrites a whole verbatim insert.

    ``_make_char_scancode_events`` only knows not to *add* a redundant
    Shift wrap; it cannot cancel a standing hold, so "Hello" typed with
    Shift down arrives as "HELLO", and with Ctrl down every character
    arrives as a chord instead of as text.  Sticky and right-click-locked
    modifiers both survive a pill tap, so this is reachable in ordinary
    use rather than only mid-chord.
    """

    def test_a_pill_tap_drops_a_sticky_shift_before_typing(self, bridge: KeyboardBridge):
        bridge._shift_active = True
        bridge._current_word = ""
        bridge.pressPrediction("Hello")
        assert bridge._shift_active is False
        bridge._synth.release_modifier.assert_any_call("shift")
        assert _typed(bridge)[-1] == "Hello "

    def test_a_locked_shift_survives_the_tap_but_not_the_text(self, bridge: KeyboardBridge):
        """The lock is the user's decision; corrupting the word is not.

        The hold is dropped for the duration of the insert and put back
        after, so the word lands as written and the keycap still agrees
        with the OS about Shift being down.
        """
        bridge.lockModifier("shift")
        bridge._synth.reset_mock()
        bridge._current_word = ""
        bridge.pressPrediction("Hello")
        assert bridge._shift_locked is True
        assert bridge._shift_active is True
        bridge._synth.release_modifier.assert_any_call("shift")
        bridge._synth.hold_modifier.assert_any_call("shift")
        assert _typed(bridge)[-1] == "Hello "

    def test_a_snippet_insert_drops_a_sticky_shift(self, bridge: KeyboardBridge):
        """Otherwise the whole address arrives in capitals."""
        bridge._snippets.set(1, "Email", "owen@example.com")
        bridge._shift_active = True
        bridge.insertSnippet(1)
        assert bridge._shift_active is False
        assert _typed(bridge)[-1] == "owen@example.com"

    def test_ctrl_is_dropped_too(self, bridge: KeyboardBridge):
        """With Ctrl held every character arrives as a chord, not as text."""
        bridge._ctrl_active = True
        bridge._current_word = ""
        bridge.pressPrediction("hello")
        assert bridge._ctrl_active is False
        bridge._synth.release_modifier.assert_any_call("ctrl")

    def test_nothing_held_means_no_modifier_traffic(self, bridge: KeyboardBridge):
        """The ordinary path must not pay for a release/re-hold round trip."""
        bridge._current_word = ""
        bridge._synth.reset_mock()
        bridge.pressPrediction("hello")
        bridge._synth.release_modifier.assert_not_called()
        bridge._synth.hold_modifier.assert_not_called()


class TestIntelligentSpacing:
    """The punctuation auto-space is skipped inside a structured token.

    The shapes themselves are covered in tests/test_text_patterns.py;
    these are about the bridge handing it the right token.  That is the
    part with a trap in it: ``_current_word`` resets at "@" and at every
    dot, so an email is only visible through ``_raw_token``.
    """

    def test_a_decimal_point_gets_no_space(self, bridge: KeyboardBridge):
        _type(bridge, "pi is 3")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(".")
        assert _typed(bridge) == ["."]

    def test_a_sentence_end_still_gets_its_space(self, bridge: KeyboardBridge):
        """The inverse, and the half that matters: never eat a prose space."""
        _type(bridge, "i went home")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(".")
        assert _typed(bridge) == [".", " "]

    def test_an_email_domain_gets_no_space(self, bridge: KeyboardBridge):
        """_current_word is "gmail" here; only _raw_token still has the "@"."""
        _type(bridge, "mail me at owen@gmail")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(".")
        assert _typed(bridge) == ["."]

    def test_a_thousands_separator_gets_no_space(self, bridge: KeyboardBridge):
        _type(bridge, "it cost 1")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(",")
        assert _typed(bridge) == [","]

    def test_a_prose_comma_still_gets_its_space(self, bridge: KeyboardBridge):
        _type(bridge, "well hello")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(",")
        assert _typed(bridge) == [",", " "]

    def test_a_dotted_run_stays_joined_once_the_first_dot_was_suppressed(
        self, bridge: KeyboardBridge
    ):
        """Evidence at the first dot carries the whole run.

        "@" proves the rest is a domain, so no space is inserted, so the
        run before the cursor is still intact when the next dot lands and
        the "already contains a dot" rule sees it.  This used to be
        demonstrated with a bare "example.co" typed into an empty field,
        via a first-token rescue that has since been removed for eating
        the first sentence of every newly focused field -- see
        TestIntelligentSpacingInAFreshField.
        """
        _type(bridge, "mail owen@example.co")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(".")
        assert _typed(bridge) == ["."]

    def test_a_mid_sentence_bare_domain_loses_a_space_per_dot(self, bridge: KeyboardBridge):
        """Documenting the known limitation, not endorsing it.

        Mid-sentence there is no evidence at the first dot ("example" is
        an ordinary word), so a space goes in.  That space ends the run,
        which means the *second* dot has no evidence either and the miss
        repeats: "example.co.uk" arrives as "example. co. uk".

        Guessing from the following character was tried and rejected --
        it turns "i went home. then i left" into "home.then", and
        corrupting prose is far worse than a space the user can delete.
        Closing this properly needs lookahead (buffering keystrokes until
        a known TLD resolves), which costs the immediate visual feedback
        that a mouse-driven keyboard depends on.  If you improve it,
        change this test rather than deleting it.
        """
        _type(bridge, "go to example.co")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(".")
        assert _typed(bridge) == [".", " "]

    def test_turning_it_off_restores_the_plain_auto_space(self, bridge: KeyboardBridge):
        bridge.setIntelligentSpacing(False)
        _type(bridge, "pi is 3")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(".")
        assert _typed(bridge) == [".", " "]

    def test_a_suppressed_space_is_not_recorded_in_the_context_buffer(self, bridge: KeyboardBridge):
        """The buffer mirrors the screen; a phantom space breaks pill inserts."""
        _type(bridge, "pi is 3")
        bridge.pressKeyLiteral(".")
        assert not bridge._context_buffer.endswith(". ")

    def test_no_auto_capital_mid_token(self, bridge: KeyboardBridge):
        """Otherwise "example.com" comes out "example.Com"."""
        bridge._auto_capitalize_after_punctuation = True
        _type(bridge, "visit example.co")
        bridge.pressKeyLiteral(".")
        assert bridge._shift_active is False

    def test_a_sentence_end_still_auto_capitalizes(self, bridge: KeyboardBridge):
        """Arms _pending_auto_cap, deliberately *not* _shift_active.

        See TestAutoCapitalizeIsNotAHeldShift for why the difference
        matters: the Shift version leaked into every chord.
        """
        bridge._auto_capitalize_after_punctuation = True
        _type(bridge, "i went home")
        bridge.pressKeyLiteral(".")
        assert bridge._pending_auto_cap is True
        assert bridge._shift_active is False

    def test_backspace_walks_the_token_back(self, bridge: KeyboardBridge):
        _type(bridge, "3")
        bridge.pressSpecialKey("backspace")
        assert bridge._raw_token == ""

    def test_a_cursor_move_abandons_the_token(self, bridge: KeyboardBridge):
        """We can no longer say what sits before the caret, so we say nothing."""
        _type(bridge, "example")
        bridge.pressSpecialKey("left")
        assert bridge._raw_token == ""

    def test_privacy_mode_tracks_nothing(self, bridge: KeyboardBridge):
        """_raw_token holds typed characters, so it is scrubbed like the rest."""
        bridge.setPrivacyMode(True)
        _type(bridge, "hunter2")
        assert bridge._raw_token == ""


class TestSnippetOffers:
    """Detecting a just-typed email / phone / address and offering to save it."""

    @pytest.fixture(autouse=True)
    def _isolate_snippets(self, bridge: KeyboardBridge, tmp_path):
        """Never touch the real snippets.json — these tests write to it."""
        from src.snippets import SnippetStore

        bridge._snippets = SnippetStore(tmp_path / "snippets.json")
        bridge._snippets.load()

    def _offers(self, bridge: KeyboardBridge) -> list:
        seen: list = []
        bridge.snippetOffered.connect(lambda k, lbl, v: seen.append((k, lbl, v)))
        return seen

    def test_an_email_is_offered(self, bridge: KeyboardBridge):
        seen = self._offers(bridge)
        _type(bridge, "write to owen@example.com ")
        assert seen == [("email", "Email", "owen@example.com")]

    def test_a_phone_is_offered(self, bridge: KeyboardBridge):
        seen = self._offers(bridge)
        _type(bridge, "call 555-123-4567 ")
        assert seen == [("phone", "Phone", "555-123-4567")]

    def test_ordinary_typing_offers_nothing(self, bridge: KeyboardBridge):
        """The inverse: an over-eager detector trains the user to dismiss."""
        seen = self._offers(bridge)
        _type(bridge, "the quick brown fox jumped over 500 people ")
        assert seen == []

    def test_accepting_fills_the_empty_matching_slot(self, bridge: KeyboardBridge):
        _type(bridge, "write to owen@example.com ")
        bridge.acceptSnippetOffer()
        saved = {s["label"]: s["value"] for s in bridge._snippets.get_all()}
        assert saved["Email"] == "owen@example.com"

    def test_a_second_email_becomes_its_own_slot(self, bridge: KeyboardBridge):
        """A curated value is never overwritten; work and personal both matter."""
        bridge._snippets.set(1, "Email", "first@example.com")
        _type(bridge, "write to second@example.com ")
        bridge.acceptSnippetOffer()
        saved = {s["label"]: s["value"] for s in bridge._snippets.get_all()}
        assert saved["Email"] == "first@example.com"
        assert saved["Email 2"] == "second@example.com"

    def test_dismissing_saves_nothing(self, bridge: KeyboardBridge):
        _type(bridge, "write to owen@example.com ")
        bridge.dismissSnippetOffer()
        saved = {s["label"]: s["value"] for s in bridge._snippets.get_all()}
        assert saved["Email"] == ""

    def test_a_dismissed_value_is_not_offered_again(self, bridge: KeyboardBridge):
        """Otherwise the next word boundary re-detects it from the buffer."""
        _type(bridge, "write to owen@example.com ")
        bridge.dismissSnippetOffer()
        seen = self._offers(bridge)
        _type(bridge, "again ")
        assert seen == []

    def test_a_value_already_saved_is_never_offered(self, bridge: KeyboardBridge):
        bridge._snippets.set(1, "Email", "owen@example.com")
        seen = self._offers(bridge)
        _type(bridge, "write to owen@example.com ")
        assert seen == []

    def test_only_one_offer_at_a_time(self, bridge: KeyboardBridge):
        """A second candidate must not replace the one being decided on."""
        seen = self._offers(bridge)
        _type(bridge, "owen@example.com and 555-123-4567 ")
        assert len(seen) == 1

    def test_privacy_mode_offers_nothing(self, bridge: KeyboardBridge):
        """A password field must not produce an offer, let alone a saved copy."""
        bridge.setPrivacyMode(True)
        seen = self._offers(bridge)
        _type(bridge, "write to owen@example.com ")
        assert seen == []

    def test_turning_detection_off_offers_nothing(self, bridge: KeyboardBridge):
        bridge.setSnippetDetection(False)
        seen = self._offers(bridge)
        _type(bridge, "write to owen@example.com ")
        assert seen == []

    def test_accepting_with_no_offer_pending_is_a_no_op(self, bridge: KeyboardBridge):
        before = bridge._snippets.get_all()
        bridge.acceptSnippetOffer()
        assert bridge._snippets.get_all() == before

    def test_turning_detection_off_withdraws_a_live_offer(self, bridge: KeyboardBridge):
        """Not merely drops it -- the toast is a QML window on its own timer.

        Clearing only the Python side left a Save button on screen that
        did nothing, and worse than nothing: ``acceptSnippetOffer``
        returns False for "no offer" and for "snippet list is full"
        alike, so the user was told their email could not be saved
        because they were out of slots.
        """
        withdrawn: list[bool] = []
        bridge.snippetOfferWithdrawn.connect(lambda: withdrawn.append(True))
        _type(bridge, "write to owen@example.com ")
        assert bridge._pending_snippet_offer is not None
        bridge.setSnippetDetection(False)
        assert withdrawn == [True]
        assert bridge._pending_snippet_offer is None


def _press(bridge: KeyboardBridge, text: str) -> None:
    """Type *text* through the ordinary (non-literal) character path.

    Distinct from ``_type`` above, which uses ``pressKeyLiteral`` and so
    bypasses the shift / caps / auto-capital case computation entirely.
    Anything asserting on *casing* has to come through here.
    """
    for ch in text:
        if ch == " ":
            bridge.pressSpecialKey("space")
        else:
            bridge.pressKey(ch)


class TestAutoCapitalizeIsNotAHeldShift:
    """Auto-capitalize must capitalize the next letter and nothing else.

    The first attempt at making this setting work expressed it as
    ``_shift_active = True``, which looked equivalent and was not.
    ``_send_key`` builds its chord modifiers straight from that flag, so
    a sentence-ending period left a Shift that the OS never had but the
    bridge believed in: Enter became Shift+Enter (a newline in Slack
    rather than send), Ctrl+C became Ctrl+Shift+C, and arrows started
    extending a selection instead of moving. It was also the one site in
    the file that set an ``_*_active`` flag with no paired
    ``hold_modifier``, which is the invariant ``_without_held_modifiers``
    relies on to decide what to restore.
    """

    @pytest.fixture
    def capping(self, bridge: KeyboardBridge) -> KeyboardBridge:
        bridge._auto_capitalize_after_punctuation = True
        return bridge

    def test_the_next_letter_is_capitalized(self, capping: KeyboardBridge):
        _press(capping, "i went home. world")
        assert "".join(_typed(capping)).endswith(". World")

    def test_no_shift_is_claimed(self, capping: KeyboardBridge):
        """The state the whole bug came from."""
        _press(capping, "i went home.")
        assert capping._shift_active is False
        assert capping._pending_auto_cap is True

    def test_no_shift_is_held_at_the_os(self, capping: KeyboardBridge):
        """Belief and reality have to agree, or the restore path pins it."""
        _press(capping, "i went home.")
        capping._synth.hold_modifier.assert_not_called()

    def test_enter_is_not_shift_enter(self, capping: KeyboardBridge):
        """Shift+Enter inserts a newline in Slack / Teams / Discord."""
        _press(capping, "i went home.")
        capping._synth.send_key.reset_mock()
        capping.pressSpecialKey("return")
        sent = [c for c in capping._synth.send_key.call_args_list if c[0][0] == "Return"]
        assert sent, "Return was never sent"
        assert not sent[0][1].get("modifiers")

    def test_ctrl_c_is_not_ctrl_shift_c(self, capping: KeyboardBridge):
        _press(capping, "i went home.")
        capping._synth.send_key.reset_mock()
        capping.toggleCtrl()
        capping.pressKey("c")
        sent = [c for c in capping._synth.send_key.call_args_list if c[0][0] == "c"]
        assert sent, "the chord was never sent"
        assert sent[0][1].get("modifiers") == ["ctrl"]

    def test_the_keycaps_follow_it(self, capping: KeyboardBridge):
        _press(capping, "i went home.")
        assert capping._current_layer == "upper"
        _press(capping, "w")
        assert capping._current_layer == "lower"

    def test_it_is_spent_on_one_letter(self, capping: KeyboardBridge):
        """Only the first letter, not everything after the full stop.

        Typed spaces go out through ``send_key``, so the joined
        ``send_text`` stream has none of them in it -- only the auto-space
        the period inserted.  "Worldhere" is the expected shape: W
        capitalised, the following word untouched.
        """
        _press(capping, "i went home. world here")
        assert "".join(_typed(capping)).endswith(". Worldhere")

    def test_a_caret_move_drops_it(self, capping: KeyboardBridge):
        """The capital belonged to the word after the full stop."""
        _press(capping, "done. ")
        capping.pressSpecialKey("left")
        _press(capping, "x")
        assert _typed(capping)[-1] == "x"

    def test_a_context_reset_drops_it(self, capping: KeyboardBridge):
        _press(capping, "done. ")
        capping._reset_typing_context()
        _press(capping, "x")
        assert _typed(capping)[-1] == "x"

    def test_off_by_default_changes_nothing(self, bridge: KeyboardBridge):
        """The inverse: capitalising unconditionally would pass the rest."""
        _press(bridge, "done. x")
        assert _typed(bridge)[-1] == "x"
        assert bridge._pending_auto_cap is False

    @pytest.mark.parametrize("run", ["!?", "?!", "!!"])
    def test_a_run_of_punctuation_still_arms_it(self, capping: KeyboardBridge, run: str):
        """ "Wait!?" must still capitalise the next sentence.

        The spend was guarded on "was one owed when this keystroke
        started", which is not the same question as "did this keystroke
        arm one".  The "?" of "Wait!?" does both: it spends the capital
        the "!" armed and arms another, and clearing on the spend threw
        the new one away.

        The runs here are all of *even* length on purpose.  Each mark
        alternately armed and cleared the flag, so a three-dot ellipsis
        came out capitalised by parity alone and passes against the bug
        — the obvious example to reach for is the one that proves
        nothing.
        """
        _press(capping, f"wait{run}")
        assert capping._pending_auto_cap is True
        _press(capping, "then")
        assert "".join(_typed(capping)).endswith("Then")

    def test_an_opening_quote_passes_it_along(self, capping: KeyboardBridge):
        """`hi. "hello"` capitalises the h, not the quote.

        A `"` cannot carry a capital, so spending one on it threw it
        away and the quoted sentence started lowercase.
        """
        _press(capping, 'i went home. "world')
        assert "".join(_typed(capping)).endswith('. "World')

    def test_a_closing_quote_passes_it_along(self, capping: KeyboardBridge):
        """The same from the other side: `"hi." Then`.

        The screen reads `." Then`; the joined ``send_text`` stream reads
        `. "Then` because the auto-space is undone by a BackSpace (a
        ``send_key``) and the user's own space is a ``send_key`` too.
        The capital on the T is what this asserts.
        """
        _press(capping, '"i went home." then')
        assert "".join(_typed(capping)).endswith('"Then')

    def test_an_opening_bracket_passes_it_along(self, capping: KeyboardBridge):
        _press(capping, "i went home. (world")
        assert "".join(_typed(capping)).endswith(". (World")

    def test_a_letter_still_spends_it(self, capping: KeyboardBridge):
        """The inverse: carrying it through everything would pass the
        quote cases and capitalise the whole sentence.
        """
        _press(capping, "i went home. world here")
        assert "".join(_typed(capping)).endswith(". Worldhere")

    def test_a_digit_still_spends_it(self, capping: KeyboardBridge):
        """`hi. 5 apples` starts its sentence with the digit, so there is
        no letter left owed a capital.
        """
        _press(capping, "i went home. 5 apples")
        assert "".join(_typed(capping)).endswith("5apples")
        assert capping._pending_auto_cap is False

    def test_the_edit_popup_does_not_eat_it(self, capping: KeyboardBridge):
        """Nor capitalise everything typed into the popup.

        The edit-mode branch applied the pending capital and returned
        before the spend, so after a full stop *every* character entered
        in the prediction-edit popup or the snippets editor came out
        uppercase.  The capital belongs to the app behind us -- nothing
        typed in edit mode can arm one -- so it is neither applied nor
        spent here.
        """
        _press(capping, "i went home.")
        typed: list[str] = []
        capping.editKeyTyped.connect(typed.append)
        capping.setEditMode(True)
        _press(capping, "abc")
        capping.setEditMode(False)
        assert typed == ["a", "b", "c"]
        assert capping._pending_auto_cap is True

    def test_a_tapped_pill_spends_it(self, capping: KeyboardBridge):
        """The pill is the next thing typed, so it takes the capital.

        Both halves matter: the pill has to *show* the capital (it is
        what will be inserted), and the flag has to be cleared, or the
        same capital lands again on a later, unrelated character.
        """
        _press(capping, "i went home. ")
        assert capping._display_cased(["then"]) == ["Then"]
        capping.pressPrediction("Then")
        assert capping._pending_auto_cap is False
        capping._synth.send_text.reset_mock()
        _press(capping, "x")
        assert _typed(capping) == ["x"]

    def test_a_snippet_spends_it(self, capping: KeyboardBridge, tmp_path):
        """A snippet is verbatim, so it takes the capital without wearing it."""
        from src.snippets import SnippetStore

        capping._snippets = SnippetStore(tmp_path / "snippets.json")
        capping._snippets.load()
        capping._snippets.set(0, "Email", "owen@example.com")
        _press(capping, "i went home. ")
        capping.insertSnippet(0)
        assert capping._pending_auto_cap is False
        capping._synth.send_text.reset_mock()
        _press(capping, "x")
        assert _typed(capping) == ["x"]


class TestTheDeferredSpaceAfterABareNumber:
    """ "3" and "42" are the same token until the next character lands.

    ``is_numeric_run`` cannot tell a decimal point from a full stop, so
    suppressing on it outright broke prose ("the total is 42. Then" came
    out "42.Then", auto-capital skipped too) and not suppressing broke
    "3.14".  The next character settles it, and settles it *additively*:
    a digit confirms the decimal and there was never a space to send,
    while a letter proves prose and the withheld space is simply typed
    then.  Nothing is ever deleted, so a wrong guess costs a late space
    rather than mangled text -- which is why this is allowed where the
    rejected "guess from the following character" rule was not.
    """

    @pytest.fixture
    def capping(self, bridge: KeyboardBridge) -> KeyboardBridge:
        bridge._auto_capitalize_after_punctuation = True
        return bridge

    def test_a_decimal_keeps_its_dot(self, bridge: KeyboardBridge):
        _press(bridge, "pi is 3")
        bridge._synth.send_text.reset_mock()
        _press(bridge, ".14")
        assert "".join(_typed(bridge)) == ".14"

    def test_a_sentence_ending_number_gets_its_space_back(self, bridge: KeyboardBridge):
        _press(bridge, "the total is 42")
        bridge._synth.send_text.reset_mock()
        _press(bridge, ".then")
        assert "".join(_typed(bridge)) == ". then"

    def test_and_the_capital_it_owed(self, capping: KeyboardBridge):
        """The auto-capital was skipped along with the space, so it is owed too."""
        _press(capping, "the total is 42")
        capping._synth.send_text.reset_mock()
        _press(capping, ".then")
        assert "".join(_typed(capping)) == ". Then"

    def test_a_thousands_separator_survives(self, bridge: KeyboardBridge):
        _press(bridge, "it cost 1")
        bridge._synth.send_text.reset_mock()
        _press(bridge, ",000")
        assert "".join(_typed(bridge)) == ",000"

    def test_a_prose_comma_after_a_number_gets_its_space_back(self, bridge: KeyboardBridge):
        _press(bridge, "i bought 5")
        bridge._synth.send_text.reset_mock()
        _press(bridge, ",then")
        assert "".join(_typed(bridge)) == ", then"

    def test_a_user_typed_space_is_not_doubled(self, bridge: KeyboardBridge):
        """They supplied the space themselves; ours would be a second one."""
        _press(bridge, "the total is 42.")
        bridge._synth.send_text.reset_mock()
        _press(bridge, " then")
        assert "".join(_typed(bridge)) == "then"

    def test_backspace_drops_it(self, bridge: KeyboardBridge):
        _press(bridge, "the total is 42.")
        bridge.pressSpecialKey("backspace")
        bridge._synth.send_text.reset_mock()
        _press(bridge, "x")
        assert "".join(_typed(bridge)) == "x"

    def test_a_context_reset_drops_it(self, bridge: KeyboardBridge):
        """The punctuation that withheld the space is no longer at the caret."""
        _press(bridge, "the total is 42.")
        bridge._reset_typing_context()
        bridge._synth.send_text.reset_mock()
        _press(bridge, "x")
        assert "".join(_typed(bridge)) == "x"

    def test_a_tapped_pill_settles_it(self, bridge: KeyboardBridge):
        """A pill is a word, so the full stop ended a sentence after all."""
        _press(bridge, "the total is 42.")
        bridge._synth.send_text.reset_mock()
        bridge.pressPrediction("then")
        assert "".join(_typed(bridge)) == " then "

    def test_an_evidenced_suppression_is_not_provisional(self, bridge: KeyboardBridge):
        """ "192.168.1" carries a separator already, so no space is owed."""
        _press(bridge, "ping 192.168.1")
        bridge._synth.send_text.reset_mock()
        _press(bridge, ".1")
        assert "".join(_typed(bridge)) == ".1"

    def test_the_setting_off_still_spaces_everything(self, bridge: KeyboardBridge):
        """The auto-space-off path stays byte-identical, deferral included."""
        bridge.setIntelligentSpacing(False)
        _press(bridge, "pi is 3")
        bridge._synth.send_text.reset_mock()
        _press(bridge, ".14")
        assert "".join(_typed(bridge)) == ". 14"
        assert bridge._deferred_auto_space == ""


class TestIntelligentSpacingInAFreshField:
    """The rescue rule that ate the first sentence of every focused field.

    ``suppresses_auto_space`` used to take a "nothing with a space has
    been typed yet" hint, meant to catch a bare domain typed into a URL
    bar.  The hint was derived from ``_context_buffer``, which is emptied
    on every app switch *and* every focused-element change, so it was
    true at the start of every newly focused text box.  Clicking into a
    field and typing "hello." lost its space, and then kept losing it,
    because the buffer still contained none.

    The rule is gone rather than repaired: at the instant the dot lands,
    "example" and "hello" are the same string, and only text that has not
    been typed yet tells them apart.
    """

    def test_the_first_sentence_in_a_fresh_field_keeps_its_space(self, bridge: KeyboardBridge):
        bridge._reset_typing_context()  # what an app / element switch does
        _type(bridge, "hello")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(".")
        assert _typed(bridge) == [".", " "]

    def test_and_the_one_after_it(self, bridge: KeyboardBridge):
        """The original failure repeated, so assert past the first."""
        bridge._reset_typing_context()
        _type(bridge, "hello")
        bridge.pressKeyLiteral(".")
        _type(bridge, "world")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(".")
        assert _typed(bridge) == [".", " "]

    def test_a_lowercase_first_word_is_not_treated_as_a_domain(self, bridge: KeyboardBridge):
        bridge._reset_typing_context()
        _type(bridge, "ok")
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(".")
        assert _typed(bridge) == [".", " "]

    @pytest.mark.parametrize(
        "typed,punct",
        [("mail owen@gmail", "."), ("pi is 3", "."), ("go to www", "."), ("open https", ":")],
    )
    def test_evidence_based_suppression_still_works(self, bridge: KeyboardBridge, typed, punct):
        """The inverse: deleting every rule would pass the tests above."""
        _type(bridge, typed)
        bridge._synth.send_text.reset_mock()
        bridge.pressKeyLiteral(punct)
        assert _typed(bridge) == [punct]


class TestLockedModifiersCannotCorruptAnInsert:
    """A right-click-locked modifier survives a pill tap by design.

    ``_release_sticky_modifiers`` deliberately spares it, so the *whole*
    insert has to run inside ``_without_held_modifiers`` -- not just the
    ``send_text`` half.  Two of ``pressPrediction``'s four branches never
    reach ``send_text`` at all, and they are the destructive ones.
    """

    def test_compat_backspaces_do_not_run_under_a_locked_alt(self, bridge: KeyboardBridge):
        """Alt+BackSpace is undo; N of them undo N times."""
        bridge._current_word = "iph"
        bridge.lockModifier("alt")
        with patch.object(bridge, "_in_compat_mode", return_value=True):
            bridge._synth.reset_mock()
            bridge.pressPrediction("iPhone")
        order = [c[0][0] for c in bridge._synth.method_calls if c[0]]
        released = [
            i for i, c in enumerate(bridge._synth.method_calls) if c[0] == "release_modifier"
        ]
        backspaces = [i for i, c in enumerate(bridge._synth.method_calls) if c[0] == "send_key"]
        assert released and backspaces, f"expected both, got {order}"
        assert released[0] < backspaces[0], "Alt was still held over the BackSpaces"
        bridge._synth.hold_modifier.assert_any_call("alt")
        assert bridge._alt_locked is True

    def test_replace_text_does_not_run_under_a_locked_ctrl(self, bridge: KeyboardBridge):
        """Ctrl+Shift+Left selects whole words, which the insert overwrites."""
        bridge._current_word = "iph"
        bridge.lockModifier("ctrl")
        bridge._synth.reset_mock()
        bridge.pressPrediction("iPhone")  # casing mismatch -> replace_text branch
        calls = [c[0] for c in bridge._synth.method_calls]
        assert "replace_text" in calls
        assert calls.index("release_modifier") < calls.index("replace_text")
        bridge._synth.hold_modifier.assert_any_call("ctrl")
        assert bridge._ctrl_locked is True

    def test_caps_lock_still_pins_a_sticky_shift(self, bridge: KeyboardBridge):
        """The exception both inline auto-release copies carry.

        With Caps Lock on, a tapped Shift is meant to persist (it is how
        a Shift+drag selection is set up). The shared helper dropped it.
        """
        bridge._caps_lock_active = True
        bridge._shift_active = True
        bridge._current_word = ""
        bridge.pressPrediction("hello")
        assert bridge._shift_active is True

    def test_without_caps_lock_it_is_still_released(self, bridge: KeyboardBridge):
        """The inverse, so "never release" cannot pass as the fix."""
        bridge._caps_lock_active = False
        bridge._shift_active = True
        bridge._current_word = ""
        bridge.pressPrediction("hello")
        assert bridge._shift_active is False


class TestOutsideClickClearsContext:
    """The backstop for two fields inside one window.

    ``GetForegroundWindow`` only sees whole-app switches, a web document
    commonly exposes one UIA element for every field on it, and most
    browsers and Electron apps publish no caret rectangle at all, so the
    other three signals all fail closed on the single most common way
    context goes stale: clicking from one field into another.  A click
    is observable in all of them.
    """

    def _click(self, bridge: KeyboardBridge, outside: bool) -> None:
        with patch("src.keyboard_bridge.external_click_detected", return_value=outside):
            bridge._check_external_click()

    def test_a_click_outside_clears_the_context(self, bridge: KeyboardBridge):
        bridge._context_buffer = "dear bob "
        bridge._sentence_buffer = "dear bob "
        self._click(bridge, outside=True)
        assert bridge._context_buffer == ""
        assert bridge._sentence_buffer == ""

    def test_a_click_on_the_keyboard_itself_changes_nothing(self, bridge: KeyboardBridge):
        """The inverse, so "always reset" cannot pass as the fix: every key
        tap is a click, and clearing on those would leave the engine with
        no context to predict from at all."""
        bridge._context_buffer = "dear bob "
        self._click(bridge, outside=False)
        assert bridge._context_buffer == "dear bob "

    def test_a_mid_word_click_clears_immediately(self, bridge: KeyboardBridge):
        """The reversal, and the case the old guard withheld.

        ``_check_caret_moved``'s "only between words" rule used to apply
        here too, which meant the reset was suppressed at the moment it
        was most needed: a partial word is exactly when the bar is full
        of completions for the field the caret has just left.  Deferring
        to the next word boundary did not help either, because that
        boundary only arrives after the user finishes the word.

        The bar is driven by typing rather than assigned, because
        ``_predictions`` starts out ``[]``: asserting it is empty after a
        reset that was never given anything to clear is the unfalsifiable
        shape this file's own notes warn about, and passes with the emit
        deleted.
        """
        _type(bridge, "hello wor")
        assert bridge._predictions, "the bar has to be full for the clear to mean anything"
        self._click(bridge, outside=True)
        assert bridge._context_buffer == ""
        assert bridge._current_word == ""
        assert bridge._predictions == []

    def test_a_pill_after_a_mid_word_click_inserts_no_suffix(self, bridge: KeyboardBridge):
        """The measured harm the old guard left in place.

        With "hel" still in ``_current_word`` and the caret in another
        field, the suffix-only insert path typed "lo " into that field:
        the abandoned word's tail, in a box that never held its head.
        Clearing on the click empties the bar, and a tap that arrives
        anyway (QML is free to drift) can only be a whole word.
        """
        _type(bridge, "hel")
        self._click(bridge, outside=True)
        assert bridge._predictions == []
        bridge._synth.reset_mock()
        bridge.pressPrediction("hello")
        sent = [c[1][0] for c in bridge._synth.method_calls if c[0] == "send_text"]
        assert sent == ["hello "], f"expected the whole word, got {sent}"

    def test_a_live_snippet_offer_survives(self, bridge: KeyboardBridge, tmp_path):
        """Clicking the next field of a form must not close the Save button.

        The offer is about a value the user typed, not about where the
        caret is, and typing an email then clicking the next field is the
        single most likely thing to happen right after one is raised.
        """
        from src.snippets import SnippetStore

        bridge._snippets = SnippetStore(tmp_path / "snippets.json")
        bridge._snippets.load()
        withdrawn: list[bool] = []
        bridge.snippetOfferWithdrawn.connect(lambda: withdrawn.append(True))
        _type(bridge, "write to owen@example.com ")
        assert bridge._pending_snippet_offer is not None
        self._click(bridge, outside=True)
        assert bridge._context_buffer == ""
        assert bridge._pending_snippet_offer is not None
        assert withdrawn == []

    def test_an_app_switch_still_withdraws_the_offer(self, bridge: KeyboardBridge, tmp_path):
        """The inverse: keeping it *everywhere* would be the wrong fix.

        Privacy mode routes through the same reset, and there "stop doing
        this" has to include the offer already on screen.
        """
        from src.snippets import SnippetStore

        bridge._snippets = SnippetStore(tmp_path / "snippets.json")
        bridge._snippets.load()
        _type(bridge, "write to owen@example.com ")
        bridge._reset_typing_context()
        assert bridge._pending_snippet_offer is None

    def test_the_offer_survives_the_caret_poll_that_follows(self, bridge: KeyboardBridge, tmp_path):
        """The click keeps the offer; the poll 250 ms later has to as well.

        Four signals watch for a moved caret and each runs on its own
        timer, so driving one in isolation says nothing about what the
        user ends up seeing.  Clearing ``_current_word`` on the click is
        precisely what unlocks ``_check_caret_moved``'s "only between
        words" guard, so the very next caret poll reset again with the
        default ``keep_snippet_offer=False`` and closed the Save button
        the click path had just gone out of its way to keep open.
        """
        from src.snippets import SnippetStore

        bridge._snippets = SnippetStore(tmp_path / "snippets.json")
        bridge._snippets.load()
        withdrawn: list[bool] = []
        bridge.snippetOfferWithdrawn.connect(lambda: withdrawn.append(True))
        _type(bridge, "write to owen@example.com ")
        _type(bridge, "than")  # the click lands mid-word, as it usually does
        assert bridge._pending_snippet_offer is not None
        with patch("src.keyboard_bridge.caret_position_token", return_value="A,10,20"):
            bridge._check_caret_moved(False)  # seed the baseline
        self._click(bridge, outside=True)
        with patch("src.keyboard_bridge.caret_position_token", return_value="A,400,90"):
            bridge._check_caret_moved(False)
        assert bridge._pending_snippet_offer is not None
        assert withdrawn == []

    def test_the_offer_survives_a_focused_element_change(
        self, bridge: KeyboardBridge, tmp_path, monkeypatch
    ):
        """The Chrome case, and the same rule: one window, one form.

        The element token moves from the email box to the next input
        while ``GetForegroundWindow`` never changes, which is the single
        most likely thing to happen right after an address is typed.
        """
        from src.snippets import SnippetStore

        bridge._snippets = SnippetStore(tmp_path / "snippets.json")
        bridge._snippets.load()
        _type(bridge, "write to owen@example.com ")
        assert bridge._pending_snippet_offer is not None
        bridge._last_foreground_hwnd = 100
        bridge._last_focus_token = "A"
        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 100)
        monkeypatch.setattr("src.keyboard_bridge.focused_element_token", lambda: "B")
        bridge._check_foreground_window()
        assert bridge._context_buffer == ""  # the reset did run
        assert bridge._pending_snippet_offer is not None

    def test_a_withdrawn_offer_can_be_raised_again(self, bridge: KeyboardBridge, tmp_path):
        """A withdrawal is not an answer, so it must not burn the value.

        ``_offered_snippet_values`` is the don't-nag ledger and it used
        to be written the moment the toast went up.  Anything that took
        the toast away before the user could reach it -- a poll, an app
        switch, privacy mode -- therefore made that address unofferable
        for the rest of the session.
        """
        from src.snippets import SnippetStore

        bridge._snippets = SnippetStore(tmp_path / "snippets.json")
        bridge._snippets.load()
        _type(bridge, "write to owen@example.com ")
        assert bridge._pending_snippet_offer is not None
        bridge._withdraw_snippet_offer()
        _type(bridge, "again owen@example.com ")
        assert bridge._pending_snippet_offer is not None

    def test_a_dismissed_offer_stays_dismissed(self, bridge: KeyboardBridge, tmp_path):
        """The inverse, so "never remember" cannot pass as the fix.

        Dismissing is an answer, and the toast's own 8 s timeout comes
        through the same slot, so without the ledger the next word
        boundary would put the same toast straight back.
        """
        from src.snippets import SnippetStore

        bridge._snippets = SnippetStore(tmp_path / "snippets.json")
        bridge._snippets.load()
        _type(bridge, "write to owen@example.com ")
        bridge.dismissSnippetOffer()
        _type(bridge, "again owen@example.com ")
        assert bridge._pending_snippet_offer is None


class TestAnOutsideClickThatMovesNoCaretIsLeftAlone:
    """Telling a caret-moving click from a caret-neutral one.

    The click signal is deliberately coarse: it fires on any press
    outside the keyboard, so a scrollbar or a toolbar button clears the
    context alongside a click into another field.  Where Windows will
    say plainly whether the caret moved, it is now asked.

    **The timing is the whole difficulty.**  The poll sees the press up
    to 50 ms late and the target app may not have handled it yet, so a
    caret read taken right then reports the old position for a click
    that is about to move the caret.  Deciding on that read would
    suppress the very reset this signal exists for, which is why the
    decision is settled over a window against a baseline from the
    *previous* poll.

    Every test drives the poll tick by tick, because a version that
    decides immediately passes any test that only looks at the end
    state.
    """

    def _tick(self, bridge: KeyboardBridge, caret, clicked: bool = False) -> None:
        """One 50 ms poll: *caret* is what the desktop reports this tick."""
        with (
            patch("src.keyboard_bridge.caret_position_token", return_value=caret),
            patch("src.keyboard_bridge.external_click_detected", return_value=clicked),
        ):
            bridge._check_external_click()

    def _hold_open(self, bridge: KeyboardBridge) -> None:
        """Push the settle's deadline out of reach for the rest of a test.

        Every test that ticks more than once *inside* the window has to
        do this, and the reason is not tidiness.  The window is a real
        200 ms of wall clock, and a keystroke here is not free: `_type`
        drives the whole prediction path per character.  On a loaded
        4-core CI runner the window closed between two ticks, which does
        not fail loudly -- the settle clears itself and every later tick
        becomes a no-op, so an assertion that a *later* move still
        resets failed outright (it did, on both runners) and one that a
        keystroke is ignored passed while measuring nothing at all.

        The window's actual length is asserted in exactly one place,
        ``test_the_window_lasts_as_long_as_it_says_it_does``, which is
        the only test here that should depend on the clock.
        """
        assert bridge._click_settle is not None
        _, baseline = bridge._click_settle
        bridge._click_settle = (time.monotonic() + 3600, baseline)

    def _expire(self, bridge: KeyboardBridge, caret) -> None:
        """Run the settle window out without the caret ever moving.

        Writes a past deadline rather than sleeping, which is why the
        length of the window is asserted separately in
        ``test_the_window_lasts_as_long_as_it_says_it_does`` -- on this
        helper alone, ``_CLICK_SETTLE_MS`` could be any number at all
        and every test here would still pass.
        """
        bridge._click_settle = (time.monotonic() - 1, caret)
        self._tick(bridge, caret)

    def test_a_click_that_moves_the_caret_still_clears(self, bridge: KeyboardBridge):
        """The behaviour the whole signal exists for."""
        self._tick(bridge, "A,10,20")  # seed the baseline
        _type(bridge, "hello wor")
        self._tick(bridge, "A,400,90", clicked=True)  # app was quick
        assert bridge._context_buffer == ""
        assert bridge._current_word == ""

    def test_a_click_the_app_has_not_processed_yet_still_clears(self, bridge: KeyboardBridge):
        """The race, and the reason for the window.

        The app reports the old caret on the tick the press is seen, and
        moves it a tick later.  Deciding on that first read would keep a
        context describing a field the caret has left, which is the bug
        the click signal was added to fix.
        """
        self._tick(bridge, "A,10,20")
        _type(bridge, "hello wor")
        self._tick(bridge, "A,10,20", clicked=True)  # not moved *yet*
        assert bridge._context_buffer != "", "decided too early"
        self._hold_open(bridge)
        self._tick(bridge, "A,400,90")  # the app catches up
        assert bridge._context_buffer == ""
        assert bridge._current_word == ""

    def test_a_click_that_moves_no_caret_keeps_the_context(self, bridge: KeyboardBridge):
        """A scrollbar or a toolbar button: the case being rescued."""
        self._tick(bridge, "A,10,20")
        _type(bridge, "hello wor")
        self._tick(bridge, "A,10,20", clicked=True)
        self._expire(bridge, "A,10,20")
        assert bridge._context_buffer == "hello "
        assert bridge._current_word == "wor"

    def test_no_caret_published_clears_exactly_as_before(self, bridge: KeyboardBridge):
        """Browsers and Electron, which is where this signal earns its keep.

        They publish no caret at all, so there is nothing to reason
        about and the behaviour has to stay byte-identical to resetting
        on every press.  If this ever stops holding, the original bug is
        back in the one place it was actually reported.
        """
        self._tick(bridge, None)
        _type(bridge, "hello wor")
        self._tick(bridge, None, clicked=True)
        assert bridge._context_buffer == ""
        assert bridge._current_word == ""

    def test_a_caret_that_becomes_unreadable_mid_window_clears(self, bridge: KeyboardBridge):
        """Fail closed on the way out too, not just on the way in.

        Focus landing somewhere with no caret is a move, and treating an
        unreadable token as "unchanged" would hold the context open on
        the strength of not knowing.
        """
        self._tick(bridge, "A,10,20")
        _type(bridge, "hello wor")
        self._tick(bridge, "A,10,20", clicked=True)
        self._hold_open(bridge)
        self._tick(bridge, None)
        assert bridge._context_buffer == ""

    def test_the_window_does_not_outlive_its_click(self, bridge: KeyboardBridge):
        """A settled click leaves nothing pending.

        Left behind, the next unrelated tick would be judged against a
        stale baseline and reset a context nobody clicked away from.
        """
        self._tick(bridge, "A,10,20")
        self._tick(bridge, "A,10,20", clicked=True)
        assert bridge._click_settle is not None
        self._expire(bridge, "A,10,20")
        assert bridge._click_settle is None

    def test_typing_inside_the_window_is_not_a_caret_move(self, bridge: KeyboardBridge):
        """Our own inserts move the caret, and this poll runs between them.

        The sibling path carries exactly this guard (see
        ``TestCaretMoveClearsContext::test_typing_does_not_trigger_it``)
        and treats it as load-bearing.  Without it, a keystroke landing
        inside the window reads as the user clicking away and tears down
        the context -- and the next-word pills -- that the keystroke had
        just produced.  A settle only opens on a click that did *not*
        move the caret, so a keystroke inside it is evidence about our
        own insert and none at all about the click.
        """
        self._tick(bridge, "A,10,20")
        _type(bridge, "hello wor")
        self._tick(bridge, "A,10,20", clicked=True)  # caret-neutral so far
        self._hold_open(bridge)
        _type(bridge, "l")  # our own insert moves the caret
        self._tick(bridge, "A,70,20")
        # A second, quiet tick at the same place, because ignoring the
        # keystroke is not enough on its own: leaving the pre-typing
        # baseline in place only postpones the reset by one tick, and
        # the next tick reads the caret our own insert moved as the
        # user clicking away.
        self._tick(bridge, "A,70,20")
        assert bridge._current_word == "worl"
        assert bridge._context_buffer == "hello "

    def test_a_late_move_is_still_caught_after_we_typed(self, bridge: KeyboardBridge):
        """The inverse: re-baselining must not blind the rest of the window.

        Ignoring our own keystroke cannot mean abandoning the decision,
        or an app slow to process the click would keep a context that
        belongs to the field the caret has left -- the failure the whole
        signal exists to prevent.
        """
        self._tick(bridge, "A,10,20")
        _type(bridge, "hello wor")
        self._tick(bridge, "A,10,20", clicked=True)
        self._hold_open(bridge)
        _type(bridge, "l")
        self._tick(bridge, "A,70,20")  # ours
        self._tick(bridge, "A,400,90")  # the app finally catches up
        assert bridge._context_buffer == ""
        assert bridge._current_word == ""

    def test_the_window_lasts_as_long_as_it_says_it_does(self, bridge: KeyboardBridge):
        """``_CLICK_SETTLE_MS`` is otherwise pinned by nothing.

        Every other test here expires the window by writing a past
        deadline straight into ``_click_settle``, which skips the
        arithmetic entirely: set the constant to 200 *seconds* and they
        all stay green while a stale prediction bar survives more than
        three minutes after a caret-neutral click.  The argument for the
        window is that it is short enough to be unreachable, so its
        length is part of the behaviour.
        """
        assert kb._CLICK_SETTLE_MS <= 1000, (
            "the case for holding the context through a settle is that the "
            "window is too short to reach; a longer one needs its own argument"
        )
        before = time.monotonic()
        self._tick(bridge, "A,10,20")
        self._tick(bridge, "A,10,20", clicked=True)
        assert bridge._click_settle is not None
        deadline, _ = bridge._click_settle
        expected = kb._CLICK_SETTLE_MS / 1000.0
        assert expected - 0.05 <= deadline - before <= expected + 0.5

    def test_a_settle_does_not_outlive_the_context_it_was_reasoning_about(
        self, bridge: KeyboardBridge
    ):
        """An app switch resolves nothing against the app it left.

        The settle holds one app's caret token.  Left standing across a
        reset, the next tick compares the *new* app's caret against it,
        calls the difference a move, and resets again through
        ``_reset_after_outside_click`` -- which passes
        ``keep_snippet_offer=True`` and so quietly re-opens a decision
        the app switch had already made the other way.
        """
        self._tick(bridge, "A,10,20")
        self._tick(bridge, "A,10,20", clicked=True)
        assert bridge._click_settle is not None
        bridge._reset_typing_context()  # what an app switch calls
        assert bridge._click_settle is None
        assert bridge._caret_before_click is None

    def test_the_baseline_is_the_previous_tick_not_the_foreground_poll(
        self, bridge: KeyboardBridge
    ):
        """`_last_caret_token` cannot serve as the baseline.

        It is maintained by the 4 Hz foreground poll, which lands between
        a press and its detection often enough to matter, and when it
        does it already holds the *post*-click value.  Comparing against
        it would read a genuine move as "unchanged".  Here it is set to
        the post-click position before the click is even seen, and the
        reset must still happen.
        """
        self._tick(bridge, "A,10,20")  # pre-click baseline, from this poll
        _type(bridge, "hello wor")
        bridge._last_caret_token = "A,400,90"  # foreground poll got there first
        self._tick(bridge, "A,400,90", clicked=True)
        assert bridge._context_buffer == "", "compared against the wrong baseline"


class TestAnInterruptedWordIsNotLearned:
    """A reset mid-word makes the rest of that word a tail, not a word.

    The outside-click reset accepts a false positive: a click that does
    not move the caret (a scrollbar, a toolbar button) clears
    ``_current_word`` while its characters are still on screen.  The
    documented cost of that is a desynced mirror, which is transient.
    Learning the tail is not: it reaches ``user_vocab``, the analytics
    word table, the dashboard's Top Words and the Data Backup archive,
    and prefix-matches to the top of the pill bar for ever after.

    Every case here types its word **three** times, because an unknown
    word only promotes into ``user_vocab`` on its third sighting: a
    one-shot version of these assertions passes whether or not the gate
    exists, which is the unfalsifiable shape this file's own notes warn
    about.
    """

    SIGHTINGS = 3

    def _interrupt(self, bridge: KeyboardBridge) -> None:
        with patch("src.keyboard_bridge.external_click_detected", return_value=True):
            bridge._check_external_click()

    def _interrupted(self, bridge: KeyboardBridge, head: str, tail: str) -> None:
        """Type *head*, take a caret-neutral outside click, finish *tail*."""
        for _ in range(self.SIGHTINGS):
            _type(bridge, head)
            self._interrupt(bridge)
            _type(bridge, tail)

    def test_the_tail_after_a_space_is_not_learned(self, bridge: KeyboardBridge):
        self._interrupted(bridge, "docu", "mentation ")
        ngram = bridge._predictor._ngram
        assert "mentation" not in ngram.user_vocab
        assert ngram.unigrams.get("mentation", 0) == 0
        assert bridge._analytics._word_freq.get("mentation", 0) == 0

    def test_the_tail_is_not_offered_as_a_pill(self, bridge: KeyboardBridge):
        """What the user actually sees when a fragment gets in.

        The measured symptom was "mentation" arriving at rank 1 for
        every later "ment", which is the point at which model pollution
        stops being an abstraction.
        """
        self._interrupted(bridge, "docu", "mentation ")
        _type(bridge, "ment")
        assert "mentation" not in bridge._predictions

    def test_the_tail_after_a_full_stop_is_not_learned(self, bridge: KeyboardBridge):
        """The second learning boundary: "." hands the sentence to learn()."""
        self._interrupted(bridge, "docu", "mentation.")
        assert "mentation" not in bridge._predictor._ngram.user_vocab

    def test_the_tail_before_return_is_not_learned(self, bridge: KeyboardBridge):
        """The third: Return learns the sentence buffer the same way."""
        for _ in range(self.SIGHTINGS):
            _type(bridge, "docu")
            self._interrupt(bridge)
            _type(bridge, "mentation")
            bridge.pressSpecialKey("return")
        assert "mentation" not in bridge._predictor._ngram.user_vocab

    def test_a_comma_does_not_smuggle_the_tail_into_the_sentence(self, bridge: KeyboardBridge):
        """A comma ends the word without learning, but banks it for later.

        ``_sentence_buffer`` is what the *next* boundary hands to
        ``learn()``, so a fragment parked there arrives one keystroke
        late rather than not at all.
        """
        self._interrupted(bridge, "docu", "mentation, yes ")
        assert "mentation" not in bridge._predictor._ngram.user_vocab

    def test_a_hyphen_does_not_smuggle_the_tail_into_the_sentence(self, bridge: KeyboardBridge):
        """Same for the word-internal boundaries, which bank it too."""
        self._interrupted(bridge, "docu", "mentation-heavy ")
        assert "mentation" not in bridge._predictor._ngram.user_vocab

    def test_the_tail_still_reaches_the_context_buffer(self, bridge: KeyboardBridge):
        """It is suppressed from the model, not from the screen mirror.

        ``_context_buffer`` has to match what was typed or backspace pops
        characters that are not there and a pill inserts against a prefix
        that is not on screen.
        """
        _type(bridge, "docu")
        self._interrupt(bridge)
        _type(bridge, "mentation ")
        assert bridge._context_buffer == "mentation "

    def test_the_same_tail_typed_whole_is_learned(self, bridge: KeyboardBridge):
        """The inverse that makes the cases above falsifiable.

        "mentation" is an ordinary unknown-but-plausible word: typed
        without the interruption it promotes on its third sighting, so
        every assertion above is testing the gate rather than the
        promotion rule.
        """
        for _ in range(self.SIGHTINGS):
            _type(bridge, "mentation ")
        assert "mentation" in bridge._predictor._ngram.user_vocab

    def test_the_next_word_is_learned_normally(self, bridge: KeyboardBridge):
        """The suppression covers exactly one word.

        Left armed it would quietly stop the model learning anything
        after the first outside click of the session.
        """
        for _ in range(self.SIGHTINGS):
            _type(bridge, "docu")
            self._interrupt(bridge)
            _type(bridge, "mentation flurgen ")
        assert "flurgen" in bridge._predictor._ngram.user_vocab

    def test_a_word_typed_after_a_between_words_click_is_learned(self, bridge: KeyboardBridge):
        """A click that lands *between* words interrupts nothing.

        The flag is armed off ``_current_word`` being non-empty, so the
        common case -- a click while no word is in progress -- must not
        cost the next word its learning.
        """
        for _ in range(self.SIGHTINGS):
            self._interrupt(bridge)
            _type(bridge, "flurgen ")
        assert "flurgen" in bridge._predictor._ngram.user_vocab


class TestCursorMotionClearsContext:
    """Home, End, the arrows and the page keys move the caret somewhere we
    did not watch it go, so everything describing the old position goes.

    The branch that handles these keys already reasoned this way about
    `_raw_token` and stopped there, which left the word half of the same
    state describing text the caret has left.

    It matters more than a wrong suggestion. `_current_word` and
    `_context_buffer` are what the insert path measures against: a pill
    types only the tail it believes is unseen, and otherwise selects
    `len(_current_word)` characters backwards and overwrites them. Both
    are arithmetic on a prefix that is no longer where the caret is.
    """

    @pytest.mark.parametrize(
        "key", ["home", "end", "left", "right", "up", "down", "pageup", "pagedown"]
    )
    def test_every_cursor_key_clears_the_context(self, bridge: KeyboardBridge, key: str):
        _type(bridge, "dear bob hel")
        assert bridge._current_word == "hel"

        bridge.pressSpecialKey(key)

        assert bridge._current_word == ""
        assert bridge._context_buffer == ""
        assert bridge._sentence_buffer == ""

    def test_the_pills_go_too(self, bridge: KeyboardBridge):
        """Otherwise the bar is left offering a word for a prefix the
        caret has left, and tapping it inserts into the new position."""
        emitted: list = []
        bridge.predictionsChanged.connect(emitted.append)
        _type(bridge, "dear bob hel")

        bridge.pressSpecialKey("home")

        assert bridge._predictions == []
        assert emitted[-1] == []

    def test_the_keystroke_still_reaches_the_app(self, bridge: KeyboardBridge):
        """The reset must not swallow the key itself."""
        _type(bridge, "hel")
        bridge._synth.send_key.reset_mock()
        bridge.pressSpecialKey("home")
        sent = [c[0][0] for c in bridge._synth.send_key.call_args_list]
        assert "Home" in sent

    def test_a_held_key_does_not_reset_per_repeat(self, bridge: KeyboardBridge):
        """These auto-repeat. The guard is self-limiting rather than a
        rate limit: after the first reset every field is already empty."""
        _type(bridge, "dear bob hel")
        emitted: list = []
        bridge.predictionsChanged.connect(emitted.append)

        for _ in range(8):
            bridge.pressSpecialKey("left")

        assert len(emitted) == 1, f"emitted {len(emitted)} times for one held key"

    def test_delete_keeps_the_context(self, bridge: KeyboardBridge):
        """The inverse, and the reason the set is the cursor keys rather
        than every key in `_TOKEN_BREAKING_KEYS`. Delete removes the
        character *after* the caret, so the run before it is untouched."""
        _type(bridge, "dear bob hel")
        bridge.pressSpecialKey("delete")
        assert bridge._current_word == "hel"
        assert bridge._context_buffer != ""

    def test_a_snippet_offer_survives(self, bridge: KeyboardBridge):
        """Same rule as Tab: moving the caret around a form you are
        filling in must not close the Save button on the address you
        just typed."""
        offers: list = []
        bridge.snippetOffered.connect(lambda *a: offers.append(a))
        _type(bridge, "write to owen@example.com ")
        assert offers, "no offer was raised, so this proves nothing"

        bridge.pressSpecialKey("end")

        assert bridge._pending_snippet_offer is not None


class TestTabClearsContext:
    """Tab moves to the next field, so the buffers describing this one go.

    Same failure the outside-click signal exists for, arriving through the
    keyboard instead: left alone, a pill tapped after tabbing inserts the
    tail of a word whose head is in the field the user just left.
    """

    def test_the_context_is_cleared(self, bridge: KeyboardBridge):
        _type(bridge, "dear bob hel")
        assert bridge._current_word == "hel"
        bridge.pressSpecialKey("tab")
        assert bridge._current_word == ""
        assert bridge._context_buffer == ""
        assert bridge._sentence_buffer == ""

    def test_the_keystroke_still_reaches_the_app(self, bridge: KeyboardBridge):
        """The reset must not swallow the Tab itself."""
        _type(bridge, "hel")
        bridge._synth.send_key.reset_mock()
        bridge.pressSpecialKey("tab")
        sent = [c[0][0] for c in bridge._synth.send_key.call_args_list]
        assert "Tab" in sent

    def test_an_ordinary_key_does_not_clear(self, bridge: KeyboardBridge):
        """The inverse, so "reset on every special key" cannot pass as this.

        Delete rather than Left, which used to play this role: the cursor
        keys clear now too (see TestCursorMotionClearsContext), and Delete
        is the sharper example anyway. It removes the character *after*
        the caret, so the run before it, which is the only thing these
        buffers describe, is untouched.
        """
        _type(bridge, "dear bob hel")
        bridge.pressSpecialKey("delete")
        assert bridge._context_buffer != ""
        assert bridge._current_word == "hel"

    def test_the_word_in_progress_is_not_learned(self, bridge: KeyboardBridge):
        """Tab is the accept-completion key in every IDE and shell.

        There `_current_word` is a *prefix* the app is about to finish, so
        learning it would feed the model "hel" every time the user
        completed "hello".
        """
        before = bridge._predictor.get_unigram_freqs().get("hel", 0)
        _type(bridge, "hel")
        bridge.pressSpecialKey("tab")
        assert bridge._predictor.get_unigram_freqs().get("hel", 0) == before

    def test_a_live_snippet_offer_survives(self, bridge: KeyboardBridge, tmp_path):
        """Tabbing to the next field of a form must not close the Save button.

        Exactly the outside-click case reached by keyboard: typing an
        email and then moving to the next field is the single most likely
        thing to happen while the offer is on screen.
        """
        from src.snippets import SnippetStore

        bridge._snippets = SnippetStore(tmp_path / "snippets.json")
        bridge._snippets.load()
        withdrawn: list[bool] = []
        bridge.snippetOfferWithdrawn.connect(lambda: withdrawn.append(True))
        _type(bridge, "write to owen@example.com ")
        assert bridge._pending_snippet_offer is not None
        bridge.pressSpecialKey("tab")
        assert bridge._pending_snippet_offer is not None
        assert withdrawn == []


class TestCaretMoveClearsContext:
    """Clearing stale context when the user moves the caret themselves.

    ``focused_element_token`` answers "different control?" and cannot
    answer "different *place* in the same control?".  Clicking from one
    paragraph to another inside a single text box keeps the element, and
    a web page commonly exposes one UIA element for the whole document,
    so two fields on it share a RuntimeId.  Either way the prediction
    context ends up describing text that is no longer beside the caret.
    """

    def _poll(self, bridge: KeyboardBridge, token, switched: bool = False) -> None:
        with patch("src.keyboard_bridge.caret_position_token", return_value=token):
            bridge._check_caret_moved(switched)

    def test_a_caret_jump_between_words_clears_the_context(self, bridge: KeyboardBridge):
        bridge._context_buffer = "hello world "
        self._poll(bridge, "A,10,20")  # seed the baseline
        self._poll(bridge, "A,400,90")  # user clicked somewhere else
        assert bridge._context_buffer == ""

    def test_an_unchanged_caret_changes_nothing(self, bridge: KeyboardBridge):
        bridge._context_buffer = "hello world "
        self._poll(bridge, "A,10,20")
        self._poll(bridge, "A,10,20")
        assert bridge._context_buffer == "hello world "

    def test_typing_does_not_trigger_it(self, bridge: KeyboardBridge):
        """Our own keystrokes move the caret every time."""
        bridge._context_buffer = "hello "
        self._poll(bridge, "A,10,20")
        bridge._keystroke_since_poll = True
        self._poll(bridge, "A,30,20")
        assert bridge._context_buffer == "hello "

    def test_never_mid_word(self, bridge: KeyboardBridge):
        """The dangerous direction, and the one scrolling would hit.

        A reset mid-word clears ``_current_word`` while the partial word
        is still on screen, so the next pill tap inserts the whole word
        beside it -- the "backspacbackspaces" duplication.  Scrolling
        drags the caret rectangle without the caret moving in the text,
        which is exactly the false positive most likely to land mid-word.
        """
        bridge._context_buffer = "hello "
        bridge._current_word = "wor"
        self._poll(bridge, "A,10,20")
        self._poll(bridge, "A,10,400")
        assert bridge._context_buffer == "hello "
        assert bridge._current_word == "wor"

    def test_no_caret_published_is_a_no_op(self, bridge: KeyboardBridge):
        """Most browsers and Electron apps publish no caret at all.

        None means "don't know", and the whole feature has to fail closed
        there -- the same contract ``focused_element_token`` uses for a
        transient UIA failure.
        """
        bridge._context_buffer = "hello "
        self._poll(bridge, None)
        self._poll(bridge, None)
        assert bridge._context_buffer == "hello "

    def test_an_app_switch_is_left_to_the_window_check(self, bridge: KeyboardBridge):
        """Not a second reset for the same event."""
        bridge._context_buffer = "hello "
        self._poll(bridge, "A,10,20")
        self._poll(bridge, "B,10,20", switched=True)
        assert bridge._context_buffer == "hello "

    def test_the_first_reading_only_seeds(self, bridge: KeyboardBridge):
        """With no baseline there is nothing to compare against."""
        bridge._context_buffer = "hello "
        self._poll(bridge, "A,10,20")
        assert bridge._context_buffer == "hello "

    @pytest.mark.parametrize(
        "insert",
        [
            pytest.param(lambda b: b.pressPrediction("world"), id="pill"),
            pytest.param(lambda b: b.insertSnippet(0), id="snippet"),
        ],
    )
    def test_our_own_inserts_do_not_trigger_it(self, bridge: KeyboardBridge, tmp_path, insert):
        """A pill tap and a snippet move the caret exactly as typing does.

        ``_keystroke_since_poll`` was set only by ``_press_char`` and
        ``pressSpecialKey``, so every path that types without going
        through them -- a tapped pill, a snippet, the autocorrect
        retype -- read to this poll as the user clicking
        somewhere else.  The context and the freshly emitted next-word
        pills were then torn down within 250 ms of the insert that
        produced them.  It is set in the synthesizer wrappers now, so a
        future insert path is covered by construction.
        """
        from src.snippets import SnippetStore

        bridge._snippets = SnippetStore(tmp_path / "snippets.json")
        bridge._snippets.load()
        bridge._snippets.set(0, "Email", "owen@example.com")
        bridge._context_buffer = "hello "
        self._poll(bridge, "A,10,20")
        insert(bridge)
        self._poll(bridge, "A,400,90")
        assert bridge._context_buffer != ""


class TestStructuredTokenPredictions:
    """Numbers, phone numbers and email domains reach the prediction bar.

    Before this, ``_press_char`` ended with ``if char.isalpha()`` and
    blanked the bar on every other character, so a digit produced no
    suggestion at all and the engine had no way to learn one: the n-gram
    tokeniser is ``[a-zA-Z']+``, which discards digits and symbols before
    a word reaches the vocabulary.  The strings this covers -- a phone
    number, a zip, a house number, an email address -- are the ones a
    mouse-driven keyboard makes most expensive to type.
    """

    def test_a_digit_no_longer_blanks_the_bar(self, bridge: KeyboardBridge):
        """The regression this feature is: a number offered nothing."""
        bridge._predictor.learn_token("1247")
        _type(bridge, "12")
        assert bridge._predictions == ["1247"]

    def test_a_typed_phone_number_is_learned(self, bridge: KeyboardBridge):
        _type(bridge, "555-123-4567 ")
        assert bridge._predictor.predict_tokens("555") == ["555-123-4567"]

    def test_a_typed_social_security_number_is_not(self, bridge: KeyboardBridge):
        """The paired hostile case, end to end through the keystroke path."""
        _type(bridge, "123-45-6789 ")
        assert bridge._predictor.predict_tokens("123") == []

    def test_a_return_also_completes_the_token(self, bridge: KeyboardBridge):
        _type(bridge, "02134")
        bridge.pressSpecialKey("return")
        assert bridge._predictor.predict_tokens("021") == ["02134"]

    def test_privacy_mode_learns_nothing(self, bridge: KeyboardBridge):
        bridge.setPrivacyMode(True)
        _type(bridge, "555-123-4567 ")
        assert bridge._predictor.predict_tokens("555") == []

    def test_tapping_a_number_pill_types_only_the_unseen_tail(self, bridge: KeyboardBridge):
        bridge._predictor.learn_token("1247")
        _type(bridge, "12")
        bridge.pressPrediction("1247")
        assert "".join(_typed(bridge)) == "1247 "

    def test_a_number_pill_appends_a_space(self, bridge: KeyboardBridge):
        """A house number sits mid-sentence: "1247 Main Street"."""
        bridge._predictor.learn_token("1247")
        _type(bridge, "12")
        bridge.pressPrediction("1247")
        assert "".join(_typed(bridge)).endswith(" ")


class TestEmailDomainSuggestions:
    """Typing "@" offers the domain, which is the expensive half."""

    def test_the_at_sign_offers_common_domains(self, bridge: KeyboardBridge):
        _type(bridge, "owen@")
        assert "gmail.com" in bridge._predictions

    def test_the_domain_narrows_as_it_is_typed(self, bridge: KeyboardBridge):
        _type(bridge, "owen@out")
        assert bridge._predictions == ["outlook.com"]

    def test_a_pill_shows_the_domain_not_the_whole_address(self, bridge: KeyboardBridge):
        """20-character pills would push the row down to two suggestions."""
        _type(bridge, "owen@")
        assert all("@" not in pill for pill in bridge._predictions)

    def test_tapping_one_completes_the_address(self, bridge: KeyboardBridge):
        _type(bridge, "owen@")
        bridge.pressPrediction("gmail.com")
        assert "".join(_typed(bridge)) == "owen@gmail.com"

    def test_an_address_gets_no_trailing_space(self, bridge: KeyboardBridge):
        """A login field that does not trim rejects "owen@gmail.com "."""
        _type(bridge, "owen@")
        bridge.pressPrediction("gmail.com")
        assert not "".join(_typed(bridge)).endswith(" ")

    def test_a_completed_address_is_learned(self, bridge: KeyboardBridge):
        _type(bridge, "owen@")
        bridge.pressPrediction("gmail.com")
        assert bridge._predictor.predict_tokens("owen@") == ["owen@gmail.com"]

    def test_a_learned_domain_outranks_the_built_ins(self, bridge: KeyboardBridge):
        _type(bridge, "owen@alphaosk.dev ")
        _type(bridge, "sam@")
        assert bridge._predictions[0] == "alphaosk.dev"

    def test_a_mention_still_gets_word_predictions(self, bridge: KeyboardBridge):
        """ "@owen" is a handle, and the word model can complete a name."""
        _type(bridge, "@ow")
        assert bridge._token_pill_words == set()

    def test_the_pill_map_is_dropped_when_words_come_back(self, bridge: KeyboardBridge):
        """A pill can only ever be inserted the way it was emitted."""
        _type(bridge, "owen@")
        assert bridge._token_pill_words
        _type(bridge, "gmail.com ")
        assert bridge._token_pill_words == set()

    def test_backspacing_inside_an_address_keeps_the_domain_bar(self, bridge: KeyboardBridge):
        """`_current_word` is "out" here, which reads as an ordinary word."""
        _type(bridge, "owen@outl")
        bridge.pressSpecialKey("backspace")
        assert bridge._predictions == ["outlook.com"]


class TestTheTokenBarSurvivesEveryWayTheBarIsRepopulated:
    """One property, five paths, and two of them used to break it.

    The word bar and the token bar are mutually exclusive, so *every*
    path that repopulates suggestions has to make the same choice.
    Written out inline at each site it was missed twice, which is why
    they all now route through ``_refresh_prediction_bar``.  These tests
    name the paths rather than the helper, so they still describe the
    property if the helper is ever restructured.
    """

    def test_backspacing_over_a_separator_keeps_the_token_bar(self, bridge: KeyboardBridge):
        """The branch the sibling test above could not reach.

        After any "-", ".", "/", "(" or "@" the current word is empty, so
        "555-" then Backspace lands in the `elif self._context_buffer`
        branch, not the one with the guard.  That branch called
        `_update_predictions` unconditionally.
        """
        _type(bridge, "555-123-4567 ")
        _type(bridge, "555-")
        assert bridge._predictions == ["555-123-4567"]
        bridge.pressSpecialKey("backspace")
        assert bridge._predictions == ["555-123-4567"]
        assert bridge._token_pill_words

    def test_tapping_shift_does_not_throw_the_token_bar_away(self, bridge: KeyboardBridge):
        """Reaching for a shifted symbol mid-number must not cost the pill."""
        _type(bridge, "555-123-4567 ")
        _type(bridge, "555-")
        bridge.toggleShift()
        assert bridge._predictions == ["555-123-4567"]
        assert bridge._token_pill_words

    def test_caps_lock_does_not_either(self, bridge: KeyboardBridge):
        _type(bridge, "owen@")
        assert "gmail.com" in bridge._predictions
        bridge.toggleCapsLock()
        assert "gmail.com" in bridge._predictions

    def test_shift_does_not_double_count_the_offer(self, bridge: KeyboardBridge):
        """Re-emitting an unchanged bar would inflate the acceptance rate."""
        _type(bridge, "owen@")
        offers = bridge._analytics._prediction_offers
        bridge.toggleShift()
        assert bridge._analytics._prediction_offers == offers

    def test_a_caret_move_takes_the_pills_with_it(self, bridge: KeyboardBridge):
        """A pill continues `_raw_token`; an arrow key invalidates it.

        Left alone, the pill stayed on the bar *and* in the insert map,
        so tapping it typed a whole domain wherever the caret had gone.
        The "is it on the bar right now" check in `pressPrediction`
        cannot catch this: the bar was never touched.
        """
        _type(bridge, "owen@")
        assert bridge._token_pill_words
        bridge.pressSpecialKey("right")
        assert bridge._token_pill_words == set()
        assert "gmail.com" not in bridge._predictions

    def test_a_stale_pill_is_no_longer_a_continuation_of_the_abandoned_run(
        self, bridge: KeyboardBridge
    ) -> None:
        """The consequence, stated separately from the mechanism.

        Deliberately *not* asserting on what was typed.  Once the pill
        leaves the token map, `pressPrediction` treats it as an ordinary
        word, and typing a word the caller named is that path's job
        rather than a defect -- QML only ever offers what is on the bar,
        which the sibling test above covers.  The synthesizer mock has no
        caret either, so both paths produce the same string here and an
        output assertion could not tell them apart.

        What must not survive is the *token* path's stale-prefix
        arithmetic: it rebuilds `_raw_token` as the abandoned prefix plus
        the pill, and that reconstruction is the thing that used to land
        a whole address wherever the caret had gone.
        """
        _type(bridge, "owen@")
        bridge.pressSpecialKey("right")
        bridge.pressPrediction("gmail.com")
        assert bridge._raw_token != "owen@gmail.com"

    def test_a_held_arrow_does_not_fire_a_query_per_repeat(self, bridge: KeyboardBridge):
        """The refresh is guarded on there being pills to invalidate.

        Auto-repeat is how a slow-motor user moves the caret, so an
        unconditional refresh here would run a prediction round trip per
        repeat.
        """
        _type(bridge, "hel")
        bridge._predictions = []
        for _ in range(5):
            bridge.pressSpecialKey("right")
        assert bridge._predictions == []


class TestLearnedTokensCanBeSeenAndRemoved:
    """The store's only window onto itself.

    The admission rule is a shape test rather than a judgement about
    sensitivity, so it accepts any short run with a digit in it -- which
    is also the shape of a password typed into a field where detection
    failed open.  Keeping it permissive is a deliberate call for recall,
    and it rests entirely on the user being able to see what was kept and
    drop entries one at a time.  Clear Learned Data is not that: it
    throws the whole vocabulary away too.
    """

    def test_a_learned_token_is_listed(self, bridge: KeyboardBridge):
        _type(bridge, "555-123-4567 ")
        assert [t["token"] for t in bridge.getLearnedTokens()] == ["555-123-4567"]

    def test_the_count_comes_with_it(self, bridge: KeyboardBridge):
        _type(bridge, "02134 ")
        _type(bridge, "02134 ")
        assert bridge.getLearnedTokens()[0]["count"] == 2

    def test_the_most_typed_is_listed_first(self, bridge: KeyboardBridge):
        _type(bridge, "1247 ")
        for _ in range(3):
            _type(bridge, "90210 ")
        assert bridge.getLearnedTokens()[0]["token"] == "90210"

    def test_forgetting_one_removes_it(self, bridge: KeyboardBridge):
        _type(bridge, "555-123-4567 ")
        assert bridge.forgetToken("555-123-4567") is True
        assert bridge.getLearnedTokens() == []

    def test_a_forgotten_token_stops_being_offered(self, bridge: KeyboardBridge):
        """Removing it from the list has to remove it from the bar."""
        _type(bridge, "555-123-4567 ")
        bridge.forgetToken("555-123-4567")
        _type(bridge, "555-")
        assert bridge._predictions == []

    def test_forgetting_something_absent_is_not_an_error(self, bridge: KeyboardBridge):
        assert bridge.forgetToken("nothing-here") is False

    def test_an_empty_store_lists_nothing(self, bridge: KeyboardBridge):
        assert bridge.getLearnedTokens() == []


class TestTokenPillsInsertWhatTheyDisplay:
    """The pill is a promise about what will be on screen afterwards.

    The store matches case-insensitively, so the pill and the typed
    characters can disagree; a suffix-only insert then leaves a third
    string that is neither.
    """

    def test_caps_lock_does_not_corrupt_the_completed_address(self, bridge: KeyboardBridge):
        """`OWEN@GM` + `gmail.com` used to land as `OWEN@GMail.com`."""
        bridge.toggleCapsLock()
        _type_cased(bridge, "owen@gm")
        # The point of the test: the run really is uppercase, so the pill
        # cannot case-sensitively continue it and the replace path is the
        # one under test.  Driven through pressKeyLiteral this read
        # "owen@gm" and the suffix path answered, which made both
        # assertions below pass against the pre-fix code.
        assert bridge._raw_token == "OWEN@GM"
        assert "gmail.com" in bridge._predictions
        bridge.pressPrediction("gmail.com")
        bridge._synth.replace_text.assert_called_once()
        assert bridge._raw_token.endswith("gmail.com")
        assert "GMail" not in bridge._raw_token

    def test_the_replace_path_leaves_the_buffer_mirroring_the_screen(
        self, bridge: KeyboardBridge
    ) -> None:
        """The replace path used to chop real characters out of the buffer.

        It subtracted ``len(typed)`` from ``_context_buffer`` alone, but
        the typed run straddles the buffer and ``_current_word``, so
        "OWEN@" lost two characters it never had to give.
        """
        bridge.toggleCapsLock()
        _type_cased(bridge, "owen@gm")
        bridge.pressPrediction("gmail.com")
        assert bridge._context_buffer == "OWEN@gmail.com"

    def test_the_corrupted_form_is_never_learned(self, bridge: KeyboardBridge):
        bridge.toggleCapsLock()
        _type_cased(bridge, "owen@gm")
        assert bridge._raw_token == "OWEN@GM"
        bridge.pressPrediction("gmail.com")
        assert not any("GMail" in t for t in bridge._predictor._ngram.tokens.tokens)

    def test_a_matching_prefix_still_takes_the_cheap_suffix_path(
        self, bridge: KeyboardBridge
    ) -> None:
        """The replace path is the fallback, not the new default."""
        _type(bridge, "owen@gm")
        bridge.pressPrediction("gmail.com")
        assert "".join(_typed(bridge)) == "owen@gmail.com"
        bridge._synth.replace_text.assert_not_called()

    def test_the_suffix_path_leaves_the_buffer_mirroring_the_screen(
        self, bridge: KeyboardBridge
    ) -> None:
        """The buffer used to record only the part the pill added.

        The rest of the run was sitting in ``_current_word``, which is
        never committed, so "555-12" + the "555-123-4567" pill recorded
        "555-3-4567" for a screen reading "555-123-4567".
        """
        _type(bridge, "555-123-4567 ")
        _type(bridge, "555-12")
        assert "555-123-4567" in bridge._predictions
        bridge.pressPrediction("555-123-4567")
        assert bridge._context_buffer == "555-123-4567 555-123-4567"

    def test_a_backspace_afterwards_does_not_delete_real_text(self, bridge: KeyboardBridge) -> None:
        """The cascade the buffer corruption caused, stated directly.

        A corrupted tail is rehydrated into ``_current_word`` by the next
        Backspace, and the tap after that replaces however many
        characters that tail claims to be worth.
        """
        _type(bridge, "555-123-4567 ")
        _type(bridge, "555-12")
        bridge.pressPrediction("555-123-4567")
        bridge.pressSpecialKey("backspace")
        # What is on screen after the backspace, and so what a following
        # replace may consume, is exactly the run minus one character.
        assert bridge._current_word == "555-123-456"


class TestTokenPillsInCompatibilityMode:
    """Compat mode rewires the insert, and the token path had missed it.

    Suffix-only insertion and replace_text's Shift+Left selection are
    both unsafe inside an IDE or a remote-desktop client -- the whole
    reason ``pressPrediction`` branches here.
    """

    def test_the_typed_run_is_backspaced_and_the_pill_retyped(
        self, bridge: KeyboardBridge, monkeypatch
    ) -> None:
        _type(bridge, "owen@gm")
        monkeypatch.setattr(bridge, "_in_compat_mode", lambda: True)
        bridge.pressPrediction("gmail.com")
        backspaces = [
            call for call in bridge._synth.send_key.call_args_list if call[0][0] == "BackSpace"
        ]
        assert len(backspaces) == 2
        assert "".join(_typed(bridge)).endswith("gmail.com")
        bridge._synth.replace_text.assert_not_called()

    def test_the_buffer_still_mirrors_the_screen(self, bridge: KeyboardBridge, monkeypatch) -> None:
        _type(bridge, "owen@gm")
        monkeypatch.setattr(bridge, "_in_compat_mode", lambda: True)
        bridge.pressPrediction("gmail.com")
        assert bridge._context_buffer == "owen@gmail.com"


class TestATappedTokenPillIsOneSighting:
    """One user action must not count as two.

    Emails and phone numbers deliberately withhold the trailing space,
    so ``_raw_token`` is still holding the completed token when the
    user's own space retires it again.  Count is the sort key in the
    store, so a doubled one puts pill-accepted tokens (including a
    built-in domain nobody typed) ahead of hand-typed ones.
    """

    def _tokens(self, bridge: KeyboardBridge) -> dict:
        return bridge._predictor._ngram.tokens.tokens

    def test_the_space_after_a_tapped_address_does_not_relearn_it(
        self, bridge: KeyboardBridge
    ) -> None:
        _type(bridge, "owen@")
        assert "gmail.com" in bridge._predictions
        bridge.pressPrediction("gmail.com")
        assert self._tokens(bridge).get("owen@gmail.com") == 1
        bridge.pressSpecialKey("space")
        assert self._tokens(bridge).get("owen@gmail.com") == 1

    def test_typing_the_same_address_again_still_counts(self, bridge: KeyboardBridge) -> None:
        """The inverse: the guard must not suppress a genuine sighting."""
        _type(bridge, "owen@")
        bridge.pressPrediction("gmail.com")
        bridge.pressSpecialKey("space")
        _type(bridge, "owen@gmail.com ")
        assert self._tokens(bridge).get("owen@gmail.com") == 2


class TestTokenPillsAreNotWordModelObjects:
    """The pill context menu's four actions are all word-model writes.

    ``unigrams`` and ``preferred`` feed the word cloud, Top Words and
    the dashboard's boosted tags; ``capitalization`` is persisted; all
    of them travel in the Data Backup archive.  A phone number belongs
    in none of them, which is the same rule
    ``record_token_prediction_selected`` exists to keep.
    """

    def _live_phone_pill(self, bridge: KeyboardBridge) -> str:
        _type(bridge, "555-123-4567 ")
        _type(bridge, "555-12")
        assert "555-123-4567" in bridge._predictions
        return "555-123-4567"

    def test_a_token_pill_is_reported_as_one(self, bridge: KeyboardBridge) -> None:
        _type(bridge, "owen@")
        assert bridge.isTokenPill("gmail.com")

    def test_a_word_pill_is_not(self, bridge: KeyboardBridge) -> None:
        """The inverse, so "always True" cannot pass as a fix."""
        _type(bridge, "hel")
        assert bridge._predictions
        assert not bridge.isTokenPill(bridge._predictions[0])

    def test_show_more_cannot_boost_a_phone_number(self, bridge: KeyboardBridge) -> None:
        word = self._live_phone_pill(bridge)
        bridge.markGoodSuggestion(word)
        ngram = bridge._predictor._ngram
        assert word not in ngram.preferred
        assert word not in ngram.unigrams

    def test_show_less_cannot_dispreference_one(self, bridge: KeyboardBridge) -> None:
        word = self._live_phone_pill(bridge)
        bridge.markBadSuggestion(word)
        assert word not in bridge._predictor._ngram.dispreference

    def test_remove_cannot_blacklist_one(self, bridge: KeyboardBridge) -> None:
        word = self._live_phone_pill(bridge)
        bridge.blacklistWord(word)
        assert word not in bridge._predictor._ngram.blacklist

    def test_edit_cannot_persist_one_into_the_capitalization_table(
        self, bridge: KeyboardBridge
    ) -> None:
        word = self._live_phone_pill(bridge)
        bridge.editPrediction(word, "555-123-9999")
        assert "555-123-9999" not in bridge._predictor._ngram.capitalization

    def test_a_real_word_can_still_be_boosted(self, bridge: KeyboardBridge) -> None:
        """The inverse again: the guard must not disarm the word menu."""
        _type(bridge, "hel")
        word = bridge._predictions[0]
        bridge.markGoodSuggestion(word)
        assert word.lower() in bridge._predictor._ngram.preferred

    def test_reset_context_drops_the_token_pill_map(self, bridge: KeyboardBridge) -> None:
        """Every other reset path clears it; this one used not to."""
        _type(bridge, "owen@")
        assert bridge._token_pill_words
        bridge.resetContext()
        assert bridge._token_pill_words == set()
        assert bridge._token_pill_typed == ""


class TestTheClearContextRingClearsEverything:
    """``resetContext`` and ``_reset_typing_context`` are one block now.

    They were two hand-written copies of the same field list, and they
    had drifted in both directions: the ring cleared
    ``_learned_raw_token`` and the shared reset did not, while the shared
    reset cleared ``_pending_auto_cap`` and the ring did not.  That is
    the parallel-blocks failure this codebase documents for sticky
    modifiers, so the fix is the one it prescribes: one method, every
    caller through it.
    """

    def test_the_ring_drops_a_pending_capital(self, bridge: KeyboardBridge) -> None:
        """The user-visible half of the drift.

        Type "hello." with auto-capitalize on and a capital is owed to
        the next character.  Tapping the ring says "forget where I am",
        and the capital used to survive it: the next letter came out
        uppercase in a context the user had just cleared.
        """
        bridge.setAutoCapitalizeAfterPunctuation(True)
        _type(bridge, "hello.")
        assert bridge._pending_auto_cap is True
        bridge.resetContext()
        assert bridge._pending_auto_cap is False

    def test_the_shared_reset_drops_the_learned_token_marker(self, bridge: KeyboardBridge) -> None:
        """The other half, in the other direction.

        ``_learned_raw_token`` names the run last handed to the token
        store so one user action cannot count as two sightings.  It goes
        with ``_raw_token``, which this reset already cleared, and the
        reset now fires on every outside click.
        """
        _type(bridge, "555-1234 ")
        assert bridge._learned_raw_token == "555-1234"
        bridge._reset_typing_context()
        assert bridge._learned_raw_token == ""

    def test_the_ring_still_clears_what_it_always_did(self, bridge: KeyboardBridge) -> None:
        """The inverse: delegating must not quietly drop a field."""
        _type(bridge, "hello wor")
        bridge.resetContext()
        assert bridge._current_word == ""
        assert bridge._context_buffer == ""
        assert bridge._sentence_buffer == ""
        assert bridge._raw_token == ""
        assert bridge._learned_raw_token == ""
        assert bridge._predictions == []
