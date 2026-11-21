"""The controller's state model: presses, latches, and the report they make.

Every rule here is paired with the case that must NOT trip it, because a
stuck stick or a button that will not come up is worse than one that never
went down.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.gamepad.state import (
    BUTTON_MASKS,
    CONTROL_IDS,
    NEUTRAL,
    STICK_VECTORS,
    TRIGGERS,
    PadReport,
    PadState,
    is_control,
)
from src.platform.padbus_client import XUSB_BUTTON


class TestTheVocabularyIsClosed:
    def test_every_xinput_button_has_exactly_one_id(self):
        assert sorted(BUTTON_MASKS.values()) == sorted(int(b) for b in XUSB_BUTTON)

    def test_the_ids_are_buttons_triggers_and_sixteen_stick_directions(self):
        assert len(STICK_VECTORS) == 16
        assert CONTROL_IDS == set(BUTTON_MASKS) | set(TRIGGERS) | set(STICK_VECTORS)

    def test_every_stick_direction_is_full_deflection(self):
        for control_id, (x, y) in STICK_VECTORS.items():
            assert math.hypot(x, y) == pytest.approx(1.0), control_id

    def test_north_is_up_and_east_is_right(self):
        assert STICK_VECTORS["ls_n"] == (0.0, 1.0)
        assert STICK_VECTORS["rs_e"] == (1.0, 0.0)

    @pytest.mark.parametrize(
        "bad", ["", "A", "ls", "ls_up", "dpad", "a ", "__class__", None, 3, ["a"]]
    )
    def test_anything_else_is_refused(self, bad):
        assert not is_control(bad)

    def test_a_refused_id_changes_nothing(self):
        state = PadState()
        assert state.press("fire") is False
        assert state.toggle_latch("fire") is None
        assert state.release("fire") is False
        assert state.report() == NEUTRAL


class TestAPress:
    def test_a_fresh_pad_is_neutral(self):
        assert PadState().report() == NEUTRAL
        assert PadState().report().is_neutral

    def test_a_button_goes_down_and_comes_up(self):
        state = PadState()
        assert state.press("a") is True
        assert state.report().buttons == BUTTON_MASKS["a"]
        assert state.release("a") is True
        assert state.report() == NEUTRAL

    def test_two_buttons_are_both_in_the_report(self):
        state = PadState()
        state.press("a")
        state.press("lb")
        assert state.report().buttons == BUTTON_MASKS["a"] | BUTTON_MASKS["lb"]

    def test_pressing_twice_is_one_press(self):
        state = PadState()
        state.press("x")
        assert state.press("x") is False
        assert state.release("x") is True
        assert state.report() == NEUTRAL

    def test_releasing_what_was_never_pressed_is_nothing(self):
        state = PadState()
        state.press("a")
        assert state.release("b") is False
        assert state.report().buttons == BUTTON_MASKS["a"]

    def test_a_trigger_is_all_the_way_down_and_only_that_one(self):
        state = PadState()
        state.press("rt")
        report = state.report()
        assert (report.left_trigger, report.right_trigger) == (0.0, 1.0)
        assert report.buttons == 0

    def test_a_stick_direction_moves_that_stick_only(self):
        state = PadState()
        state.press("ls_e")
        report = state.report()
        assert (report.left_x, report.left_y) == (1.0, 0.0)
        assert (report.right_x, report.right_y) == (0.0, 0.0)

    def test_a_diagonal_is_a_unit_vector_not_a_corner(self):
        state = PadState()
        state.press("rs_sw")
        report = state.report()
        assert report.right_x < 0 and report.right_y < 0
        assert math.hypot(report.right_x, report.right_y) == pytest.approx(1.0)


class TestALatch:
    def test_a_latch_stays_down_with_nothing_pressed(self):
        state = PadState()
        assert state.toggle_latch("rt") is True
        assert state.report().right_trigger == 1.0
        assert state.latched == {"rt"}

    def test_toggling_again_lets_go(self):
        state = PadState()
        state.toggle_latch("rt")
        assert state.toggle_latch("rt") is False
        assert state.report() == NEUTRAL

    def test_pressing_a_latched_control_releases_it_and_presses_nothing(self):
        state = PadState()
        state.toggle_latch("a")
        assert state.press("a") is True
        assert state.report() == NEUTRAL
        assert state.pressed == frozenset()
        # The release that follows the click is then a no-op, not a second change.
        assert state.release("a") is False

    def test_pressing_another_control_leaves_the_latch_alone(self):
        state = PadState()
        state.toggle_latch("a")
        state.press("b")
        state.release("b")
        assert state.latched == {"a"}
        assert state.report().buttons == BUTTON_MASKS["a"]

    def test_releasing_a_press_does_not_release_a_latch_on_the_same_control(self):
        state = PadState()
        state.press("lb")
        state.toggle_latch("lb")
        state.release("lb")
        assert state.report().buttons == BUTTON_MASKS["lb"]

    def test_release_all_drops_presses_and_latches(self):
        state = PadState()
        state.press("a")
        state.toggle_latch("ls_n")
        state.toggle_latch("rt")
        assert state.release_all() is True
        assert state.report() == NEUTRAL
        assert state.latched == frozenset()
        assert state.release_all() is False


class TestAStickPointsOneWay:
    def test_latching_a_second_direction_replaces_the_first(self):
        state = PadState()
        state.toggle_latch("ls_n")
        state.toggle_latch("ls_e")
        assert state.latched == {"ls_e"}
        assert (state.report().left_x, state.report().left_y) == (1.0, 0.0)

    def test_the_other_stick_keeps_its_own_latch(self):
        state = PadState()
        state.toggle_latch("ls_n")
        state.toggle_latch("rs_w")
        report = state.report()
        assert (report.left_x, report.left_y) == (0.0, 1.0)
        assert (report.right_x, report.right_y) == (-1.0, 0.0)

    def test_a_press_overrides_the_latched_direction_only_while_it_lasts(self):
        state = PadState()
        state.toggle_latch("ls_n")
        state.press("ls_s")
        assert (state.report().left_x, state.report().left_y) == (0.0, -1.0)
        state.release("ls_s")
        assert (state.report().left_x, state.report().left_y) == (0.0, 1.0)

    def test_the_most_recent_of_two_presses_wins_and_the_older_shows_through_after(self):
        state = PadState()
        state.press("ls_w")
        state.press("ls_e")
        assert state.report().left_x == 1.0
        state.release("ls_e")
        assert state.report().left_x == -1.0

    def test_the_dpad_cannot_point_up_and_down_at_once(self):
        state = PadState()
        state.toggle_latch("dpad_up")
        state.toggle_latch("dpad_down")
        assert state.report().buttons == BUTTON_MASKS["dpad_down"]

    def test_the_dpad_can_point_up_and_left_at_once(self):
        state = PadState()
        state.toggle_latch("dpad_up")
        state.toggle_latch("dpad_left")
        assert state.report().buttons == BUTTON_MASKS["dpad_up"] | BUTTON_MASKS["dpad_left"]

    def test_face_buttons_are_not_a_group(self):
        state = PadState()
        state.toggle_latch("a")
        state.toggle_latch("b")
        assert state.latched == {"a", "b"}


_OPS = st.lists(
    st.tuples(st.sampled_from(["press", "release", "latch"]), st.sampled_from(sorted(CONTROL_IDS))),
    max_size=40,
)


def _apply(state: PadState, ops) -> None:
    for op, control_id in ops:
        if op == "press":
            state.press(control_id)
        elif op == "release":
            state.release(control_id)
        else:
            state.toggle_latch(control_id)


class TestWhateverTheOrder:
    @given(_OPS)
    def test_a_stick_is_centred_or_at_full_deflection(self, ops):
        state = PadState()
        _apply(state, ops)
        report = state.report()
        for x, y in ((report.left_x, report.left_y), (report.right_x, report.right_y)):
            assert math.hypot(x, y) == pytest.approx(1.0) or (x, y) == (0.0, 0.0)

    @given(_OPS)
    def test_opposite_dpad_directions_are_never_both_down(self, ops):
        state = PadState()
        _apply(state, ops)
        buttons = state.report().buttons
        assert not (buttons & BUTTON_MASKS["dpad_up"] and buttons & BUTTON_MASKS["dpad_down"])
        assert not (buttons & BUTTON_MASKS["dpad_left"] and buttons & BUTTON_MASKS["dpad_right"])

    @given(_OPS)
    def test_release_all_always_ends_neutral(self, ops):
        state = PadState()
        _apply(state, ops)
        state.release_all()
        assert state.report() == NEUTRAL

    @given(_OPS)
    def test_press_and_release_report_exactly_whether_the_report_changed(self, ops):
        state = PadState()
        for op, control_id in ops:
            before = state.report()
            if op == "press":
                changed = state.press(control_id)
            elif op == "release":
                changed = state.release(control_id)
            else:
                state.toggle_latch(control_id)
                continue
            assert changed == (state.report() != before)

    @given(_OPS)
    def test_the_report_is_a_value(self, ops):
        state = PadState()
        _apply(state, ops)
        assert isinstance(state.report(), PadReport)
        assert state.report() == state.report()
