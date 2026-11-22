"""A held stick's confinement never reaches past the window that can end it.

The click that puts a stick down has to land on our own window. Without
recentring, the travel square is centred on the click, so a pick-up near the
window's edge used to confine the pointer partly over the game.
"""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from src.gamepad.stick_session import StickSession
from tests.test_gamepad_stick_session import FakeConfiner

# A 620 x 360 window whose top-left corner is at (1000, 1000), at 100 % scale.
WINDOW = (1000, 1000, 1620, 1360)
LEFT_STICK_CENTRE = (1110, 1134)
TRAVEL = 100


class _Sink:
    """Where each stick was last put."""

    def __init__(self) -> None:
        self.sticks: dict = {}

    def set_stick(self, stick: str, x: float, y: float) -> None:
        self.sticks[stick] = (x, y)

    def center_stick(self, stick: str) -> None:
        self.sticks[stick] = (0.0, 0.0)

    def set_trigger(self, trigger: str, value: float) -> None:
        pass

    def clear_trigger(self, trigger: str) -> None:
        pass


def _session() -> Tuple[StickSession, FakeConfiner, _Sink]:
    confiner, sink = FakeConfiner(), _Sink()
    return StickSession(confiner, sink), confiner, sink


def _confined_to(confiner: FakeConfiner) -> List[Any]:
    return [call[1] for call in confiner.calls if call[0] == "confine"]


def _inside(inner: Tuple[int, int, int, int], outer: Tuple[int, int, int, int]) -> bool:
    return (
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


class TestWithoutRecentring:
    def test_a_pick_up_at_the_windows_edge_is_cut_down_to_the_window(self) -> None:
        session, confiner, _ = _session()
        session.recentre = False
        confiner.pointer = (1012, 1134)  # 12 px in from the window's left edge

        assert session.pick_up("ls", LEFT_STICK_CENTRE, TRAVEL, WINDOW) == "held"

        (clip,) = _confined_to(confiner)
        # Uncut, this was (912, 1034, 1113, 1235): 88 px over the game.
        assert clip == (1000, 1034, 1113, 1235)
        assert _inside(clip, WINDOW), "a click in part of the confinement would reach the game"

    def test_the_pointer_is_not_moved_and_the_click_is_still_centre(self) -> None:
        session, confiner, sink = _session()
        session.recentre = False
        confiner.pointer = (1012, 1134)
        session.pick_up("ls", LEFT_STICK_CENTRE, TRAVEL, WINDOW)
        assert not [c for c in confiner.calls if c[0] == "warp"]
        assert session.tick() == "ok"
        assert sink.sticks["ls"] == (0.0, 0.0)

    def test_well_inside_the_window_nothing_is_cut(self) -> None:
        session, confiner, _ = _session()
        session.recentre = False
        confiner.pointer = (1300, 1180)
        session.pick_up("ls", LEFT_STICK_CENTRE, TRAVEL, WINDOW)
        (clip,) = _confined_to(confiner)
        assert clip == (1200, 1080, 1401, 1281)

    @pytest.mark.parametrize(
        "pointer", [(1003, 1004), (1616, 1003), (1004, 1356), (1617, 1357), (1310, 1001)]
    )
    def test_wherever_it_is_picked_up_every_confined_point_is_in_the_window(self, pointer) -> None:
        session, confiner, _ = _session()
        session.recentre = False
        confiner.pointer = pointer
        assert session.pick_up("ls", LEFT_STICK_CENTRE, TRAVEL, WINDOW) == "held"
        (clip,) = _confined_to(confiner)
        assert _inside(clip, WINDOW)
        assert clip[0] <= pointer[0] < clip[2] and clip[1] <= pointer[1] < clip[3]


class TestWithRecentring:
    def test_a_stick_inside_the_window_keeps_its_whole_square(self) -> None:
        session, confiner, _ = _session()
        confiner.pointer = (1012, 1134)
        assert session.pick_up("ls", LEFT_STICK_CENTRE, TRAVEL, WINDOW) == "held"
        (clip,) = _confined_to(confiner)
        assert clip == (1010, 1034, 1211, 1235)
        assert _inside(clip, WINDOW)

    def test_a_stick_too_close_to_the_edge_is_cut_down_too(self) -> None:
        session, confiner, _ = _session()
        confiner.pointer = (1040, 1134)
        assert session.pick_up("ls", (1050, 1134), TRAVEL, WINDOW) == "held"
        (clip,) = _confined_to(confiner)
        assert clip[0] == 1000 and _inside(clip, WINDOW)


class TestWhenTheWindowCannotHoldIt:
    def test_a_pointer_outside_the_window_is_never_confined_or_moved(self) -> None:
        session, confiner, _ = _session()
        session.recentre = False
        confiner.pointer = (900, 1134)  # not over our window at all
        assert session.pick_up("ls", LEFT_STICK_CENTRE, TRAVEL, WINDOW) == "held-unconfined"
        assert _confined_to(confiner) == []
        assert not [c for c in confiner.calls if c[0] == "warp"]

    def test_a_centre_outside_the_window_is_not_recentred_onto(self) -> None:
        session, confiner, _ = _session()
        confiner.pointer = (1012, 1134)
        assert session.pick_up("ls", (700, 1134), TRAVEL, WINDOW) == "held-unconfined"
        assert _confined_to(confiner) == []
        assert not [c for c in confiner.calls if c[0] == "warp"]


class TestWithNoBoundsGiven:
    def test_the_square_is_used_as_it_is(self) -> None:
        session, confiner, _ = _session()
        session.recentre = False
        confiner.pointer = (1012, 1134)
        assert session.pick_up("ls", LEFT_STICK_CENTRE, TRAVEL) == "held"
        (clip,) = _confined_to(confiner)
        assert clip == (912, 1034, 1113, 1235)


class TestLosingTheConfinementDropsTheStick:
    @pytest.mark.parametrize("verdict", ["clip-lost", "release-pending"])
    def test_these_results_centre_it(self, verdict: str) -> None:
        session, confiner, sink = _session()
        session.pick_up("ls", LEFT_STICK_CENTRE, TRAVEL, WINDOW)
        confiner.pointer = (1160, 1134)
        session.tick()
        assert sink.sticks["ls"] != (0.0, 0.0)
        confiner.tick_results = [verdict]
        assert session.tick() == verdict
        assert session.held is None and not session.confined
        assert sink.sticks["ls"] == (0.0, 0.0)

    def test_a_reapplied_clip_does_not(self) -> None:
        session, confiner, _ = _session()
        session.pick_up("ls", LEFT_STICK_CENTRE, TRAVEL, WINDOW)
        confiner.tick_results = ["reapplied"]
        assert session.tick() == "reapplied"
        assert session.held == "ls"
