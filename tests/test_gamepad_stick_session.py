"""StickSession over a fake confiner and a fake sink. No pointer, no pad, no Qt."""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

import pytest

from src.gamepad.stick import HELD, IDLE, LATCHED
from src.gamepad.stick_session import StickSession

CENTRE = (500, 400)
TRAVEL = 100


class FakeConfiner:
    def __init__(self) -> None:
        self.calls: List[Tuple[Any, ...]] = []
        self.pointer: Optional[Tuple[int, int]] = (480, 380)
        self.confine_result = True
        self.tick_results: List[str] = []
        self.raise_on: set = set()

    def _maybe_raise(self, name: str) -> None:
        if name in self.raise_on:
            raise RuntimeError(name)

    def cursor_pos(self) -> Optional[Tuple[int, int]]:
        self._maybe_raise("cursor_pos")
        return self.pointer

    def confine(self, rect: Any) -> bool:
        self.calls.append(("confine", rect))
        return self.confine_result

    def set_cursor_pos(self, x: int, y: int) -> bool:
        self.calls.append(("warp", x, y))
        self.pointer = (x, y)
        return True

    def tick(self) -> str:
        self.calls.append(("tick",))
        self._maybe_raise("tick")
        return self.tick_results.pop(0) if self.tick_results else "ok"

    def release(self) -> None:
        self.calls.append(("release",))

    def names(self) -> List[str]:
        return [c[0] for c in self.calls]


class FakeSink:
    def __init__(self) -> None:
        self.calls: List[Tuple[Any, ...]] = []
        self.raise_on_set = False

    def set_stick(self, stick: str, x: float, y: float) -> None:
        if self.raise_on_set:
            raise RuntimeError("boom")
        self.calls.append(("stick", stick, x, y))

    def center_stick(self, stick: str) -> None:
        self.calls.append(("center", stick))

    def set_trigger(self, trigger: str, value: float) -> None:
        self.calls.append(("trigger", trigger, value))

    def clear_trigger(self, trigger: str) -> None:
        self.calls.append(("clear", trigger))


@pytest.fixture
def confiner() -> FakeConfiner:
    return FakeConfiner()


@pytest.fixture
def sink() -> FakeSink:
    return FakeSink()


@pytest.fixture
def session(confiner: FakeConfiner, sink: FakeSink) -> StickSession:
    return StickSession(confiner, sink)


class TestPickUp:
    def test_only_two_sticks_exist(self, session: StickSession, confiner: FakeConfiner) -> None:
        assert session.pick_up("xs", CENTRE, TRAVEL) == "refused"
        assert confiner.calls == []
        assert session.pick_up("ls", CENTRE, TRAVEL) == "held"

    def test_no_pointer_is_refused_and_nothing_is_held(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        confiner.pointer = None
        assert session.pick_up("ls", CENTRE, TRAVEL) == "refused"
        assert session.held is None
        assert "confine" not in confiner.names()
        assert sink.calls == []

    def test_confined_pick_up_sets_the_clip_before_moving_the_pointer(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        assert session.pick_up("ls", CENTRE, TRAVEL) == "held"
        assert confiner.names() == ["confine", "warp"]
        assert confiner.calls[0][1] == (400, 300, 601, 501)
        assert confiner.calls[1][1:] == CENTRE
        assert session.held == "ls" and session.confined is True
        assert session.state("ls") == HELD
        assert sink.calls == [("stick", "ls", 0.0, 0.0)]

    def test_pointer_already_on_the_centre_is_not_moved(
        self, session: StickSession, confiner: FakeConfiner
    ) -> None:
        confiner.pointer = CENTRE
        session.pick_up("ls", CENTRE, TRAVEL)
        assert confiner.names() == ["confine"]

    def test_without_recentre_the_pointer_is_the_anchor_and_stays_put(
        self, session: StickSession, confiner: FakeConfiner
    ) -> None:
        session.recentre = False
        assert session.pick_up("ls", CENTRE, TRAVEL) == "held"
        assert confiner.names() == ["confine"]
        assert confiner.calls[0][1] == (380, 280, 581, 481)

    def test_a_refused_clip_falls_back_to_an_unconfined_hold_without_moving(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        confiner.confine_result = False
        assert session.pick_up("ls", CENTRE, TRAVEL) == "held-unconfined"
        assert "warp" not in confiner.names()
        assert session.confined is False and session.held == "ls"
        # Anchored at the pointer, so the stick starts centred.
        assert sink.calls == [("stick", "ls", 0.0, 0.0)]
        confiner.pointer = (580, 380)
        session.tick()
        assert sink.calls[-1] == ("stick", "ls", 1.0, 0.0)

    def test_confine_off_never_asks_for_a_clip(
        self, session: StickSession, confiner: FakeConfiner
    ) -> None:
        session.confine = False
        assert session.pick_up("ls", CENTRE, TRAVEL) == "held-unconfined"
        assert confiner.names() == []

    def test_clicking_a_latched_stick_releases_it_and_does_not_pick_it_up(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("rs", CENTRE, TRAVEL)
        session.hold()
        assert session.state("rs") == LATCHED
        sink.calls.clear()
        assert session.pick_up("rs", CENTRE, TRAVEL) == "released"
        assert session.state("rs") == IDLE
        assert session.held is None
        assert sink.calls == [("center", "rs")]

    def test_a_second_stick_latches_the_first(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.pointer = (550, 400)
        session.tick()
        confiner.calls.clear()
        assert session.pick_up("rs", (900, 400), TRAVEL) == "held"
        assert session.state("ls") == LATCHED
        assert session.position("ls") == (0.5, 0.0)
        assert session.held == "rs"
        # The first stick's clip was let go before the second was set.
        assert confiner.names()[:2] == ["release", "confine"]
        assert ("center", "ls") not in sink.calls

    def test_picking_up_the_same_stick_again_does_not_latch_anything(
        self, session: StickSession
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        assert session.pick_up("ls", CENTRE, TRAVEL) == "held"
        assert session.state("rs") == IDLE and session.state("ls") == HELD

    def test_an_error_while_picking_up_frees_the_pointer(
        self, session: StickSession, confiner: FakeConfiner
    ) -> None:
        confiner.raise_on.add("cursor_pos")
        assert session.pick_up("ls", CENTRE, TRAVEL) == "refused"
        assert "release" in confiner.names()
        assert session.held is None


class TestTick:
    def test_idle_when_nothing_is_held(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        assert session.tick() == "idle"
        assert session.last_tick == "idle"
        assert confiner.calls == [] and sink.calls == []

    def test_follows_the_pointer_and_reports_the_confiner_status(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.pointer = (600, 300)
        confiner.tick_results = ["reapplied"]
        assert session.tick() == "reapplied"
        assert session.last_tick == "reapplied"
        assert sink.calls[-1][:2] == ("stick", "ls")
        assert session.position("ls") == pytest.approx((0.7071, 0.7071), abs=1e-3)
        assert session.tick() == "ok"

    @pytest.mark.parametrize("verdict", ["guard-released", "guard-lost"])
    def test_a_lost_guard_drops_the_stick_and_centres_the_sink(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink, verdict: str
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.tick_results = [verdict]
        sink.calls.clear()
        assert session.tick() == verdict
        assert session.last_tick == verdict
        assert session.held is None and session.confined is False
        assert session.state("ls") == IDLE
        assert sink.calls == [("center", "ls")]
        assert session.tick() == "idle"

    def test_unconfined_never_asks_the_confiner_to_tick(
        self, session: StickSession, confiner: FakeConfiner
    ) -> None:
        confiner.confine_result = False
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.tick_results = ["guard-lost"]
        assert session.tick() == "ok"
        assert "tick" not in confiner.names()
        assert session.held == "ls"

    def test_no_pointer_sends_nothing(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        sink.calls.clear()
        confiner.pointer = None
        assert session.tick() == "no-pointer"
        assert sink.calls == []
        assert session.held == "ls"

    def test_a_raising_tick_frees_the_pointer_and_centres(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.raise_on.add("tick")
        confiner.calls.clear()
        assert session.tick() == "error"
        assert "release" in confiner.names()
        assert session.held is None
        assert ("center", "ls") in sink.calls

    def test_a_raising_sink_frees_the_pointer(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        sink.raise_on_set = True
        confiner.calls.clear()
        assert session.tick() == "error"
        assert "release" in confiner.names()
        assert session.held is None


class TestDropHoldRelease:
    def test_drop_releases_the_clip_and_centres(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.calls.clear()
        session.drop()
        assert confiner.names() == ["release"]
        assert sink.calls[-1] == ("center", "ls")
        assert session.held is None and session.state("ls") == IDLE

    def test_drop_with_nothing_held_touches_nothing(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.drop()
        assert confiner.calls == [] and sink.calls == []

    def test_hold_keeps_the_position_and_frees_the_pointer(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.pointer = (550, 400)
        session.tick()
        confiner.calls.clear()
        sink.calls.clear()
        session.hold()
        assert confiner.names() == ["release"]
        assert sink.calls == []
        assert session.state("ls") == LATCHED
        assert session.position("ls") == (0.5, 0.0)
        assert session.held is None and session.confined is False
        confiner.pointer = (590, 400)
        assert session.tick() == "idle"
        assert session.position("ls") == (0.5, 0.0)

    def test_hold_with_nothing_held_is_quiet(
        self, session: StickSession, confiner: FakeConfiner
    ) -> None:
        session.hold()
        assert confiner.calls == []

    def test_release_centres_a_latched_stick_without_touching_the_clip_of_another(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        session.pick_up("rs", (900, 400), TRAVEL)  # ls is latched, rs is held
        confiner.calls.clear()
        session.release("ls")
        assert confiner.calls == []
        assert session.held == "rs" and session.confined is True
        assert sink.calls[-1] == ("center", "ls")

    def test_release_of_the_held_stick_frees_the_clip(
        self, session: StickSession, confiner: FakeConfiner
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.calls.clear()
        session.release("ls")
        assert confiner.names() == ["release"]

    def test_release_all_centres_both_and_frees_the_clip(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        session.pick_up("rs", (900, 400), TRAVEL)
        confiner.calls.clear()
        sink.calls.clear()
        session.release_all()
        assert "release" in confiner.names()
        assert ("center", "ls") in sink.calls and ("center", "rs") in sink.calls
        assert session.held is None and session.confined is False

    def test_release_all_frees_the_clip_even_when_the_sink_raises(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)

        def boom(stick: str) -> None:
            raise RuntimeError("boom")

        sink.center_stick = boom  # type: ignore[method-assign]
        confiner.calls.clear()
        session.release_all()
        assert confiner.names()[0] == "release"
        assert session.held is None

    def test_a_confiner_that_raises_on_release_does_not_escape(
        self, session: StickSession, confiner: FakeConfiner
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)

        def boom() -> None:
            raise RuntimeError("boom")

        confiner.release = boom  # type: ignore[method-assign]
        session.drop()
        session.release_all()
        assert session.held is None and session.state("ls") == IDLE


class TestDriveMode:
    def test_off_by_default_the_left_stick_is_a_plain_stick(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.pointer = (500, 350)
        session.tick()
        assert sink.calls[-1] == ("stick", "ls", 0.0, 0.5)
        assert not any(c[0] == "trigger" for c in sink.calls)

    def test_on_the_left_stick_steers_and_its_y_becomes_the_triggers(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.drive = True
        session.pick_up("ls", CENTRE, TRAVEL)
        confiner.pointer = (550, 350)
        sink.calls.clear()
        session.tick()
        assert sink.calls == [
            ("stick", "ls", 0.5, 0.0),
            ("trigger", "rt", 0.5),
            ("trigger", "lt", 0.0),
        ]
        confiner.pointer = (500, 450)
        sink.calls.clear()
        session.tick()
        assert ("trigger", "lt", 0.5) in sink.calls and ("trigger", "rt", 0.0) in sink.calls

    def test_on_the_right_stick_is_still_a_plain_stick(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.drive = True
        session.pick_up("rs", CENTRE, TRAVEL)
        confiner.pointer = (500, 350)
        sink.calls.clear()
        session.tick()
        assert sink.calls == [("stick", "rs", 0.0, 0.5)]

    def test_releasing_the_left_stick_clears_both_triggers(
        self, session: StickSession, sink: FakeSink
    ) -> None:
        session.drive = True
        session.pick_up("ls", CENTRE, TRAVEL)
        sink.calls.clear()
        session.drop()
        assert sink.calls == [("center", "ls"), ("clear", "rt"), ("clear", "lt")]

    def test_off_releasing_the_left_stick_leaves_triggers_alone(
        self, session: StickSession, sink: FakeSink
    ) -> None:
        session.pick_up("ls", CENTRE, TRAVEL)
        sink.calls.clear()
        session.drop()
        assert sink.calls == [("center", "ls")]

    def test_switching_drive_off_while_deflected_leaves_no_trigger_held(
        self, session: StickSession, confiner: FakeConfiner, sink: FakeSink
    ) -> None:
        session.drive = True
        session.pick_up("ls", CENTRE, TRAVEL)
        session.hold()
        sink.calls.clear()
        session.drive = False
        assert sink.calls == [("center", "ls"), ("clear", "rt"), ("clear", "lt")]
        assert session.state("ls") == IDLE

    def test_switching_drive_with_an_idle_stick_touches_nothing(
        self, session: StickSession, sink: FakeSink
    ) -> None:
        session.drive = True
        session.drive = True
        session.drive = False
        assert sink.calls == []
