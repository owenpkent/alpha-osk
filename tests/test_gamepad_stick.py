"""The pick-up stick's arithmetic: anchor, confinement square, deflection."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from src.gamepad.stick import (
    HELD,
    IDLE,
    LATCHED,
    StickGesture,
    deflection,
    split_drive,
    travel_square,
)

CENTRE = (500, 300)


class TestTheTravelSquare:
    def test_it_reaches_travel_pixels_each_way_and_is_a_win32_rect(self):
        # Right and bottom are exclusive, so the far pixel itself is inside.
        assert travel_square((500, 300), 90) == (410, 210, 591, 391)

    def test_it_follows_the_anchor_onto_a_second_monitor(self):
        assert travel_square((-200, -1000), 10) == (-210, -1010, -189, -989)


class TestDeflection:
    def test_the_anchor_is_centre(self):
        assert deflection(CENTRE, CENTRE, 90) == (0.0, 0.0)

    def test_half_the_travel_is_half_deflection(self):
        assert deflection((545, 300), CENTRE, 90) == (0.5, 0.0)

    def test_up_on_screen_is_up_on_the_stick(self):
        assert deflection((500, 210), CENTRE, 90) == (0.0, 1.0)
        assert deflection((500, 390), CENTRE, 90) == (0.0, -1.0)

    def test_a_corner_of_the_square_is_still_a_unit_vector(self):
        x, y = deflection((590, 210), CENTRE, 90)
        assert math.hypot(x, y) == pytest.approx(1.0)
        assert x == pytest.approx(y)

    def test_inside_the_circle_nothing_is_rescaled(self):
        x, y = deflection((540, 270), CENTRE, 100)
        assert (x, y) == (0.4, 0.3)

    @given(st.integers(-4000, 4000), st.integers(-4000, 4000), st.integers(1, 400))
    def test_it_never_leaves_the_unit_circle(self, px, py, travel):
        x, y = deflection((px, py), CENTRE, travel)
        assert math.hypot(x, y) <= 1.0 + 1e-9


class TestOneStickDrives:
    def test_up_is_throttle_and_down_is_brake_and_never_both(self):
        assert split_drive(0.3, 0.8) == (0.3, 0.8, 0.0)
        assert split_drive(-1.0, -0.25) == (-1.0, 0.0, 0.25)

    def test_centred_is_neither(self):
        assert split_drive(0.0, 0.0) == (0.0, 0.0, 0.0)


class TestPickingUp:
    def test_recentring_anchors_on_the_stick_and_moves_the_pointer_there(self):
        gesture = StickGesture()
        pick = gesture.pick_up((530, 310), CENTRE, 90, recentre=True)
        assert pick.anchor == CENTRE
        assert pick.warp_to == CENTRE
        assert pick.clip == travel_square(CENTRE, 90)
        assert gesture.state == HELD
        assert gesture.position == (0.0, 0.0)

    def test_a_click_already_on_the_centre_needs_no_move(self):
        pick = StickGesture().pick_up(CENTRE, CENTRE, 90, recentre=True)
        assert pick.warp_to is None

    def test_without_recentring_the_click_is_the_anchor_and_nothing_moves(self):
        gesture = StickGesture()
        pick = gesture.pick_up((530, 310), CENTRE, 90, recentre=False)
        assert pick.anchor == (530, 310)
        assert pick.warp_to is None
        assert pick.clip == travel_square((530, 310), 90)
        # The click itself is centred: no jump on pick-up.
        assert gesture.move((530, 310)) == (0.0, 0.0)

    def test_a_travel_of_zero_cannot_divide(self):
        gesture = StickGesture()
        gesture.pick_up(CENTRE, CENTRE, 0, recentre=True)
        assert gesture.travel == 1
        assert gesture.move((501, 300)) == (1.0, 0.0)


class TestPuttingDown:
    def test_an_idle_stick_ignores_the_pointer(self):
        gesture = StickGesture()
        assert gesture.move((900, 900)) == (0.0, 0.0)
        assert gesture.state == IDLE

    def test_a_held_stick_follows_it(self):
        gesture = StickGesture()
        gesture.pick_up(CENTRE, CENTRE, 90, recentre=True)
        assert gesture.move((545, 300)) == (0.5, 0.0)

    def test_release_drops_it_back_to_centre(self):
        gesture = StickGesture()
        gesture.pick_up(CENTRE, CENTRE, 90, recentre=True)
        gesture.move((590, 300))
        gesture.release()
        assert gesture.state == IDLE
        assert gesture.position == (0.0, 0.0)

    def test_hold_leaves_it_where_it_is_and_the_pointer_no_longer_moves_it(self):
        gesture = StickGesture()
        gesture.pick_up(CENTRE, CENTRE, 90, recentre=True)
        gesture.move((545, 300))
        assert gesture.hold() == (0.5, 0.0)
        assert gesture.state == LATCHED
        assert gesture.move((410, 300)) == (0.5, 0.0)

    def test_hold_on_an_idle_stick_holds_nothing(self):
        gesture = StickGesture()
        assert gesture.hold() == (0.0, 0.0)
        assert gesture.state == IDLE

    def test_releasing_a_held_in_place_stick_centres_it(self):
        gesture = StickGesture()
        gesture.pick_up(CENTRE, CENTRE, 90, recentre=True)
        gesture.move((545, 300))
        gesture.hold()
        gesture.release()
        assert (gesture.state, gesture.position) == (IDLE, (0.0, 0.0))
