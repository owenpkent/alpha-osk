"""The update window can be moved, by a drag and by a carry (pure logic).

A user with imprecise motor control cannot hold a precise drag, so the window
has the keyboard's click-free Move mode as well: pick it up, move with no
button held, click to put it down, right click to put it back.  These tests
drive the state machine with plain tuples; the window half (a button, an
overlay, the Win32 calls) is covered in ``test_update_relauncher.py``.
"""

from __future__ import annotations

import pytest

from src.update_window_move import (
    MoveState,
    WindowMover,
    clamp_to_desktop,
    desktop_edges,
)

SIZE = (400, 200)
ONE_SCREEN = [(0, 0, 1920, 1080)]
# A second monitor to the LEFT of the primary: negative coordinates.
TWO_SCREENS = [(-1920, 0, 0, 1080), (0, 0, 1920, 1080)]
# An L: the top-right corner of the bounding box belongs to no monitor.
L_SHAPE = [(0, 0, 1000, 1000), (1000, 500, 2000, 1000)]


def mover(monitors=ONE_SCREEN) -> WindowMover:
    return WindowMover(lambda: monitors)


class TestTheClamp:
    def test_a_window_on_the_desktop_is_not_moved(self):
        assert clamp_to_desktop((100, 100), SIZE, ONE_SCREEN) == (100, 100)

    def test_it_is_kept_inside_every_edge(self):
        assert clamp_to_desktop((-50, -50), SIZE, ONE_SCREEN) == (0, 0)
        assert clamp_to_desktop((5000, 5000), SIZE, ONE_SCREEN) == (1920 - 400, 1080 - 200)

    def test_it_clamps_to_the_whole_desktop_not_the_primary_screen(self):
        # The bug a primary-screen clamp has: a window on the left monitor
        # collapses onto the primary one.
        assert clamp_to_desktop((-1500, 300), SIZE, TWO_SCREENS) == (-1500, 300)
        assert clamp_to_desktop((-5000, 300), SIZE, TWO_SCREENS) == (-1920, 300)
        assert clamp_to_desktop((5000, 300), SIZE, TWO_SCREENS) == (1920 - 400, 300)

    def test_a_window_may_straddle_two_monitors(self):
        assert clamp_to_desktop((-200, 300), SIZE, TWO_SCREENS) == (-200, 300)

    def test_a_window_in_a_gap_of_the_bounding_box_is_pulled_onto_a_monitor(self):
        # Top-right of the L's bounding box is no monitor at all.
        x, y = clamp_to_desktop((1500, 0), SIZE, L_SHAPE)
        assert y >= 500, "pulled down onto the monitor that exists there"
        assert 1000 <= x and x + 400 <= 2000

    def test_with_no_monitor_list_the_position_is_returned_as_it_came(self):
        assert clamp_to_desktop((-9999, 9999), SIZE, []) == (-9999, 9999)

    def test_a_window_bigger_than_the_desktop_is_pinned_top_left(self):
        assert clamp_to_desktop((300, 300), (5000, 5000), ONE_SCREEN) == (0, 0)

    def test_the_bounding_box(self):
        assert desktop_edges(TWO_SCREENS) == (-1920, 0, 1920, 1080)
        assert desktop_edges([]) is None


class TestTheDrag:
    def test_a_press_move_release_moves_the_window_by_the_pointers_travel(self):
        m = mover()
        assert m.press((500, 400), (300, 250)) is True
        assert m.state is MoveState.DRAGGING
        assert m.follow((530, 380), SIZE) == (330, 230)
        m.release()
        assert m.state is MoveState.IDLE
        assert m.moved is True

    def test_idle_follows_nothing(self):
        assert mover().follow((1, 1), SIZE) is None

    def test_a_press_while_carrying_is_ignored(self):
        m = mover()
        m.pick_up((10, 10), (0, 0))
        assert m.press((10, 10), (0, 0)) is False
        assert m.state is MoveState.CARRYING

    def test_release_does_not_end_a_carry(self):
        m = mover()
        m.pick_up((10, 10), (0, 0))
        m.release()
        assert m.state is MoveState.CARRYING

    def test_a_press_that_never_moves_does_not_count_as_moved(self):
        m = mover()
        m.press((500, 400), (300, 250))
        assert m.follow((500, 400), SIZE) == (300, 250)
        m.release()
        assert m.moved is False


class TestTheCarry:
    def test_it_follows_with_no_button_held(self):
        m = mover()
        assert m.pick_up((500, 400), (300, 250)) is True
        assert m.state is MoveState.CARRYING
        assert m.follow((600, 450), SIZE) == (400, 300)
        assert m.follow((100, 100), SIZE) == (0, 0), "clamped at the top-left"

    def test_a_left_click_puts_it_down_where_it_is(self):
        m = mover()
        m.pick_up((500, 400), (300, 250))
        assert m.follow((600, 450), SIZE) == (400, 300)
        m.put_down()
        assert m.state is MoveState.IDLE
        assert m.moved is True
        assert m.follow((0, 0), SIZE) is None, "put down means it stops following"

    def test_a_right_click_returns_it_to_where_it_was(self):
        m = mover()
        m.pick_up((500, 400), (300, 250))
        m.follow((900, 900), SIZE)
        assert m.put_back() == (300, 250)
        assert m.state is MoveState.IDLE
        assert m.moved is False, "a window put back was never moved"

    def test_a_put_back_keeps_an_earlier_move(self):
        m = mover()
        m.press((10, 10), (100, 100))
        m.follow((60, 60), SIZE)
        m.release()
        assert m.moved is True
        m.pick_up((10, 10), (150, 150))
        m.follow((500, 500), SIZE)
        assert m.put_back() == (150, 150)
        assert m.moved is True

    def test_put_back_and_put_down_do_nothing_when_not_carrying(self):
        m = mover()
        assert m.put_back() is None
        m.put_down()
        assert m.state is MoveState.IDLE

    def test_a_second_pick_up_is_ignored(self):
        m = mover()
        assert m.pick_up((0, 0), (10, 10)) is True
        assert m.pick_up((5, 5), (99, 99)) is False
        assert m.put_back() == (10, 10), "the first pick-up's return spot stands"


class TestTheClampIsNeverFedBack:
    """The rule the keyboard's Move mode states about its snapped value."""

    def test_pushing_against_an_edge_does_not_swallow_the_travel_back(self):
        m = mover()
        m.pick_up((1000, 500), (1000, 500))
        # Push 800 px right: the window pins at the edge, 1520.
        assert m.follow((1800, 500), SIZE) == (1920 - 400, 500)
        # Coming back 200 px is still past the edge in the unclamped
        # position (1600 > 1520), so the window stays pinned, exactly as
        # the pointer says.
        assert m.follow((1600, 500), SIZE) == (1920 - 400, 500)
        # And the travel back to the grab point lands the window on its
        # origin: nothing drifted while it was pinned.
        assert m.follow((1000, 500), SIZE) == (1000, 500)

    def test_the_position_depends_only_on_the_pointer_not_the_path(self):
        a, b = mover(), mover()
        a.pick_up((500, 500), (300, 300))
        b.pick_up((500, 500), (300, 300))
        for point in [(2500, 2500), (-900, -900), (640, 520)]:
            a.follow(point, SIZE)
        assert a.follow((700, 650), SIZE) == b.follow((700, 650), SIZE)

    def test_it_works_across_a_left_hand_monitor(self):
        m = mover(TWO_SCREENS)
        m.pick_up((100, 300), (50, 250))
        assert m.follow((-1000, 300), SIZE) == (-1050, 250)


class TestKeepInView:
    def test_a_window_that_grew_off_the_bottom_is_pulled_back(self):
        m = mover()
        assert m.keep_in_view((100, 1000), (400, 400)) == (100, 680)

    def test_a_visible_window_is_left_alone(self):
        assert mover().keep_in_view((100, 100), SIZE) == (100, 100)


@pytest.mark.parametrize("monitors", [ONE_SCREEN, TWO_SCREENS, L_SHAPE])
def test_whatever_the_pointer_does_the_window_ends_on_a_monitor(monitors):
    m = mover(monitors)
    m.pick_up((500, 500), (300, 300))
    for x in range(-4000, 4000, 400):
        for y in range(-3000, 3000, 500):
            wx, wy = m.follow((x, y), SIZE)
            cx, cy = wx + SIZE[0] // 2, wy + SIZE[1] // 2
            assert any(r[0] <= cx < r[2] and r[1] <= cy < r[3] for r in monitors)
