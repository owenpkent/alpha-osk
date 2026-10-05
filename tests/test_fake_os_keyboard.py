"""Sanity tests for the fake OS itself (PowerToys' ``MockedInputSanityTests``).

``tests/test_property_keystroke_state.py`` trusts ``FakeOSKeyboard`` to be a
truthful operating system.  A fake that was quietly lenient (a held Shift that
did not shift, a held Ctrl that still typed) would let the bridge's
modifier-guard bugs pass, so the fake is tested first, and every case here is
paired with its inverse: the behaviour appears when its cause is present and
is absent when it is not.
"""

from __future__ import annotations

import pytest

from tests.fake_os_keyboard import FakeOSError, FakeOSKeyboard


@pytest.fixture
def os_() -> FakeOSKeyboard:
    return FakeOSKeyboard()


class TestHeldModifiers:
    def test_hold_and_release_edit_the_state_table(self, os_: FakeOSKeyboard) -> None:
        os_.hold_modifier("shift")
        os_.hold_modifier("win")
        assert os_.held == {"shift", "win"}
        os_.release_modifier("shift")
        assert os_.held == {"win"}

    def test_unknown_names_are_not_holdable(self, os_: FakeOSKeyboard) -> None:
        os_.hold_modifier("caps")
        assert os_.held == set()

    def test_reset_releases_everything(self, os_: FakeOSKeyboard) -> None:
        for name in ("shift", "ctrl", "alt", "win"):
            os_.hold_modifier(name)
        os_.reset_modifier_state()
        assert os_.held == set()


class TestSendText:
    def test_no_hold_leaves_text_alone(self, os_: FakeOSKeyboard) -> None:
        os_.send_text("Hello, 1!")
        assert os_.text == "Hello, 1!"
        assert os_.chords() == []

    def test_a_held_shift_uppercases_letters(self, os_: FakeOSKeyboard) -> None:
        os_.hold_modifier("shift")
        os_.send_text("Hello")
        assert os_.text == "HELLO"

    def test_a_held_shift_shifts_digits_and_punctuation(self, os_: FakeOSKeyboard) -> None:
        os_.hold_modifier("shift")
        os_.send_text("1,")
        assert os_.text == "!<"

    def test_shift_does_not_reach_the_unicode_route(self, os_: FakeOSKeyboard) -> None:
        os_.hold_modifier("shift")
        os_.send_text("é")
        assert os_.text == "é"

    @pytest.mark.parametrize("name", ["ctrl", "alt", "win"])
    def test_a_chord_modifier_turns_text_into_chords(self, os_: FakeOSKeyboard, name: str) -> None:
        os_.hold_modifier(name)
        os_.send_text("ab")
        assert os_.text == ""
        assert [e.detail for e in os_.chords()] == ["a", "b"]

    def test_releasing_the_chord_modifier_restores_typing(self, os_: FakeOSKeyboard) -> None:
        os_.hold_modifier("ctrl")
        os_.release_modifier("ctrl")
        os_.send_text("ab")
        assert os_.text == "ab"
        assert os_.chords() == []


class TestSendKey:
    def test_modifiers_wrap_the_key_without_being_left_held(self, os_: FakeOSKeyboard) -> None:
        os_.send_key("c", modifiers=["ctrl"])
        assert os_.held == set()
        assert os_.text == ""
        assert os_.chords()[0].modifiers == ("ctrl",)

    def test_an_already_held_modifier_stays_held(self, os_: FakeOSKeyboard) -> None:
        os_.hold_modifier("ctrl")
        os_.send_key("c", modifiers=["ctrl"])
        assert os_.held == {"ctrl"}

    def test_a_plain_key_types(self, os_: FakeOSKeyboard) -> None:
        os_.send_key("a")
        os_.send_key("space")
        assert os_.text == "a "

    def test_backspace_edits_and_a_held_ctrl_makes_it_a_chord(self, os_: FakeOSKeyboard) -> None:
        os_.send_text("abc")
        os_.send_key("BackSpace")
        assert os_.text == "ab"
        os_.hold_modifier("alt")
        os_.send_key("BackSpace")
        assert os_.text == "ab"
        assert os_.chords()[-1].detail == "BackSpace"

    def test_shift_does_not_stop_an_editing_key_working(self, os_: FakeOSKeyboard) -> None:
        os_.send_text("ab")
        os_.hold_modifier("shift")
        os_.send_key("BackSpace")
        assert os_.text == "a"

    def test_caret_keys_move_the_caret(self, os_: FakeOSKeyboard) -> None:
        os_.send_text("ac")
        os_.send_key("Left")
        os_.send_text("b")
        assert os_.text == "abc"
        os_.send_key("End")
        assert os_.caret == 3


class TestReplaceText:
    def test_replaces_the_tail_when_nothing_is_held(self, os_: FakeOSKeyboard) -> None:
        os_.send_text("say iph")
        os_.replace_text(3, "iPhone ")
        assert os_.text == "say iPhone "

    def test_a_held_shift_still_uppercases_the_replacement(self, os_: FakeOSKeyboard) -> None:
        os_.send_text("say iph")
        os_.hold_modifier("shift")
        os_.replace_text(3, "iPhone ")
        assert os_.text == "say IPHONE "

    def test_a_held_ctrl_deletes_nothing_and_types_nothing(self, os_: FakeOSKeyboard) -> None:
        os_.send_text("say iph")
        os_.hold_modifier("ctrl")
        os_.replace_text(3, "iPhone ")
        assert os_.text == "say iph"
        assert os_.chords()


class TestFailureInjection:
    def test_no_hook_never_fails(self, os_: FakeOSKeyboard) -> None:
        for _ in range(20):
            os_.send_text("a")
        assert os_.text == "a" * 20

    def test_the_nth_call_raises_once_and_has_no_effect(self, os_: FakeOSKeyboard) -> None:
        os_.fail_after(2)
        os_.send_text("a")
        with pytest.raises(FakeOSError):
            os_.send_text("b")
        os_.send_text("c")
        assert os_.text == "ac"

    def test_a_failed_hold_leaves_the_key_up(self, os_: FakeOSKeyboard) -> None:
        os_.fail_after(1, methods=["hold_modifier"])
        with pytest.raises(FakeOSError):
            os_.hold_modifier("shift")
        assert os_.held == set()

    def test_the_method_filter_ignores_other_calls(self, os_: FakeOSKeyboard) -> None:
        os_.fail_after(1, methods=["send_key"])
        os_.send_text("a")
        os_.hold_modifier("shift")
        with pytest.raises(FakeOSError):
            os_.send_key("b")
