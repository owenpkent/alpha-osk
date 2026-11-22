"""Analog positions in the controller's state: a stick anywhere, a trigger part-way."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.gamepad.state import NEUTRAL, PadState

_FLOATS = st.floats(min_value=-5.0, max_value=5.0, allow_nan=False, allow_infinity=False)


class TestAnAnalogStick:
    def test_it_is_reported_where_it_is_put(self):
        state = PadState()
        assert state.set_stick("ls", 0.25, -0.5) is True
        report = state.report()
        assert (report.left_x, report.left_y) == (0.25, -0.5)
        assert (report.right_x, report.right_y) == (0.0, 0.0)

    def test_setting_the_same_place_again_is_no_change(self):
        state = PadState()
        state.set_stick("rs", 0.25, 0.0)
        assert state.set_stick("rs", 0.25, 0.0) is False

    def test_past_the_edge_it_is_pulled_back_onto_the_circle(self):
        state = PadState()
        state.set_stick("ls", 3.0, 4.0)
        report = state.report()
        assert (report.left_x, report.left_y) == pytest.approx((0.6, 0.8))

    def test_it_stays_until_it_is_cleared(self):
        state = PadState()
        state.set_stick("ls", 0.5, 0.0)
        state.press("a")
        state.release("a")
        assert state.report().left_x == 0.5
        assert state.clear_stick("ls") is True
        assert state.report() == NEUTRAL

    def test_clearing_a_stick_that_has_no_position_is_nothing(self):
        assert PadState().clear_stick("ls") is False

    def test_while_it_has_a_position_the_direction_ids_do_not_show(self):
        state = PadState()
        state.toggle_latch("ls_e")
        state.set_stick("ls", 0.0, 0.25)
        assert (state.report().left_x, state.report().left_y) == (0.0, 0.25)
        # Centred analog is still a position: the stick is centred, not east.
        state.set_stick("ls", 0.0, 0.0)
        assert (state.report().left_x, state.report().left_y) == (0.0, 0.0)

    def test_once_cleared_the_latched_direction_shows_through_again(self):
        state = PadState()
        state.toggle_latch("ls_e")
        state.set_stick("ls", 0.0, 0.25)
        state.clear_stick("ls")
        assert (state.report().left_x, state.report().left_y) == (1.0, 0.0)

    def test_the_other_stick_keeps_its_direction_ids(self):
        state = PadState()
        state.toggle_latch("rs_n")
        state.set_stick("ls", 0.5, 0.0)
        assert (state.report().right_x, state.report().right_y) == (0.0, 1.0)

    @pytest.mark.parametrize("stick", ["", "LS", "left", "ls_n", None, 0])
    def test_an_unknown_stick_is_refused(self, stick):
        state = PadState()
        assert state.set_stick(stick, 1.0, 0.0) is False
        assert state.report() == NEUTRAL

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), "0.5", None, True])
    def test_a_value_that_is_not_a_finite_number_is_refused(self, bad):
        state = PadState()
        assert state.set_stick("ls", bad, 0.0) is False
        assert state.set_stick("ls", 0.0, bad) is False
        assert state.set_trigger("rt", bad) is False
        assert state.report() == NEUTRAL

    @given(_FLOATS, _FLOATS)
    def test_it_never_leaves_the_unit_circle(self, x, y):
        state = PadState()
        state.set_stick("ls", x, y)
        report = state.report()
        assert math.hypot(report.left_x, report.left_y) <= 1.0 + 1e-9


class TestAnAnalogTrigger:
    def test_it_is_reported_part_way(self):
        state = PadState()
        assert state.set_trigger("rt", 0.4) is True
        assert (state.report().left_trigger, state.report().right_trigger) == (0.0, 0.4)

    def test_it_is_clamped_to_its_travel(self):
        state = PadState()
        state.set_trigger("lt", 7.0)
        assert state.report().left_trigger == 1.0
        state.set_trigger("lt", -3.0)
        assert state.report().left_trigger == 0.0

    def test_the_trigger_id_and_the_analog_value_report_whichever_is_further_down(self):
        state = PadState()
        state.set_trigger("rt", 0.4)
        state.toggle_latch("rt")
        assert state.report().right_trigger == 1.0
        state.toggle_latch("rt")
        assert state.report().right_trigger == 0.4

    def test_clearing_it_lets_go(self):
        state = PadState()
        state.set_trigger("rt", 0.4)
        assert state.clear_trigger("rt") is True
        assert state.clear_trigger("rt") is False
        assert state.report() == NEUTRAL

    def test_a_button_id_is_not_a_trigger(self):
        assert PadState().set_trigger("a", 1.0) is False


class TestReleaseAllCoversAnalog:
    def test_it_centres_sticks_and_lets_triggers_up(self):
        state = PadState()
        state.set_stick("ls", 0.5, 0.5)
        state.set_trigger("rt", 0.7)
        assert state.release_all() is True
        assert state.report() == NEUTRAL
        assert state.analog_stick("ls") is None
        assert state.release_all() is False
