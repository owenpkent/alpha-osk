"""The ways a clip could outlive what both sides believe.

Each case here was a real hole in the first cut, found in review: the pointer
stayed clipped while the confiner reported it free and the guard had nothing
left to act on. They are kept apart from ``test_pointer_clip.py`` so the
reason each exists stays visible.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

import pytest

from src.gamepad.stick_session import StickSession
from src.platform.pointer_clip import GUARD_SILENCE_S, POLL_S, STALE_S, run_guard
from tests.test_pointer_clip import RECT, FakeApi, Rig


class HookedApi(FakeApi):
    """A fake whose release can run a callback after the OS clip has gone."""

    def __init__(self) -> None:
        super().__init__()
        self.after_release: Optional[Callable[[], None]] = None

    def release(self) -> bool:
        ok = super().release()
        if ok and self.after_release is not None:
            hook, self.after_release = self.after_release, None
            hook()
        return ok


def _confined(rig: Rig) -> Any:
    assert rig.confiner.start()
    assert rig.confiner.confine(RECT)
    return rig.confiner


def _run_guard(rig: Rig, seconds: float) -> None:
    """The real guard loop over the rig's heartbeat and api, on the fake clock."""
    assert rig.guard_hb is not None
    run_guard(
        lambda: True,
        rig.guard_hb,
        rig.api,
        clock=rig.clock,
        sleep=rig.clock.sleep,
        max_iterations=int(seconds / POLL_S) + 1,
    )


def _active(rig: Rig) -> int:
    assert rig.guard_hb is not None
    return rig.guard_hb.read().active


class TestTheGuardReleasesWhileTheKeyboardResumes:
    """The guard clears the clip, then counts it: two steps, in another process."""

    def test_a_clip_reapplied_between_the_two_steps_is_still_released(self) -> None:
        rig = Rig(api=HookedApi())
        confiner = _confined(rig)
        seen: List[str] = []
        # The keyboard wakes exactly between the guard's release and its count.
        rig.api.after_release = lambda: seen.append(confiner.tick())

        _run_guard(rig, STALE_S + 0.3)

        assert seen == ["reapplied"], "the race did not happen; the test proves nothing"
        assert rig.api.clip == RECT
        # The next tick learns the guard released. It must not just forget.
        assert confiner.tick() == "guard-released"
        assert rig.api.clip is None, "the pointer is still clipped and nobody owns it"
        assert not confiner.confined and not confiner.release_pending
        assert _active(rig) == 0
        confiner.close()

    def test_without_the_race_a_guard_release_still_ends_confinement(self) -> None:
        rig = Rig()
        confiner = _confined(rig)
        _run_guard(rig, STALE_S + 0.3)
        assert rig.api.clip is None
        assert confiner.tick() == "guard-released"
        assert not confiner.confined
        assert _active(rig) == 0
        confiner.close()

    def test_confining_again_after_a_guard_release_starts_clean(self) -> None:
        rig = Rig()
        confiner = _confined(rig)
        _run_guard(rig, STALE_S + 0.3)
        confiner.tick()
        assert confiner.confine(RECT)
        assert confiner.tick() == "ok", "an old guard release was counted against the new clip"
        confiner.close()


class TestAReleaseTheSystemRefuses:
    @pytest.mark.parametrize("how", ["returns_false", "raises"])
    def test_it_stays_supervised_until_it_succeeds(self, how: str) -> None:
        rig = Rig()
        confiner = _confined(rig)
        if how == "raises":
            rig.api.release_raises = True
        else:
            rig.api.release_ok = False

        assert confiner.release() is False
        assert rig.api.clip == RECT
        assert confiner.release_pending
        assert not confiner.confined, "nothing may follow a pointer that is being let go"
        assert _active(rig) == 1, "the guard was told there is nothing to watch"

        # The system recovers. Nobody ticks (no stick is held), so the guard's
        # stale-heartbeat path is what finishes the job.
        rig.api.release_raises = False
        rig.api.release_ok = True
        _run_guard(rig, STALE_S + 0.3)
        assert rig.api.clip is None

        assert confiner.tick() == "idle"
        assert not confiner.release_pending
        assert _active(rig) == 0
        confiner.close()

    def test_the_guard_keeps_trying_while_the_system_keeps_refusing(self) -> None:
        rig = Rig()
        confiner = _confined(rig)
        rig.api.release_ok = False
        confiner.release()
        _run_guard(rig, STALE_S + 0.5)
        tries = rig.api.names().count("release")
        assert tries > 3, "the guard gave up on a clip that is still set"
        assert rig.guard_hb is not None and rig.guard_hb.read().guard_releases == 0
        rig.api.release_ok = True
        _run_guard(rig, 0.2)
        assert rig.api.clip is None
        confiner.close()

    def test_a_later_call_retries_it_before_doing_anything_else(self) -> None:
        rig = Rig()
        confiner = _confined(rig)
        rig.api.release_ok = False
        confiner.release()
        assert confiner.tick() == "release-pending"
        assert confiner.confine((1, 2, 3, 4)) is False, "a new clip over an unreleased one"
        rig.api.release_ok = True
        assert confiner.tick() == "idle"
        assert rig.api.clip is None
        assert confiner.confine((1, 2, 3, 4))
        confiner.close()

    @pytest.mark.parametrize("how", ["returns_false", "raises"])
    def test_close_does_not_kill_the_guard_while_a_release_is_outstanding(self, how: str) -> None:
        rig = Rig()
        confiner = _confined(rig)
        if how == "raises":
            rig.api.release_raises = True
        else:
            rig.api.release_ok = False

        confiner.close()

        assert rig.api.clip == RECT
        assert rig.proc.terminated == 0, "the only thing still retrying was terminated"
        assert _active(rig) == 1
        rig.api.release_raises = False
        rig.api.release_ok = True
        _run_guard(rig, STALE_S + 0.3)
        assert rig.api.clip is None

    def test_close_after_a_clean_release_does_end_the_guard(self) -> None:
        rig = Rig()
        confiner = _confined(rig)
        confiner.close()
        assert rig.api.clip is None
        assert rig.proc.terminated == 1

    def test_the_heartbeat_goes_inactive_only_after_the_system_confirms(self) -> None:
        rig = Rig()
        confiner = _confined(rig)
        during: List[int] = []
        rig.api.on_call = lambda call: during.append(_active(rig)) if call[0] == "release" else None
        assert confiner.release() is True
        assert during == [1], "inactive before the release: a stall there has no rescuer"
        assert _active(rig) == 0
        confiner.close()

    def test_releasing_with_nothing_held_touches_nothing(self) -> None:
        rig = Rig()
        assert rig.confiner.start()
        assert rig.confiner.release() is True
        assert "release" not in rig.api.names(), "a clip this object did not set was cleared"
        rig.confiner.close()


class TestAClipThatWillNotGoBack:
    def test_a_failed_reapplication_ends_confinement(self) -> None:
        rig = Rig()
        confiner = _confined(rig)
        rig.api.clip = None  # a game, or a foreground change, cleared it
        rig.api.set_ok = False

        assert confiner.tick() == "clip-lost"
        assert not confiner.confined
        assert confiner.reapplied == 0, "a reapplication that failed was counted"
        assert _active(rig) == 0
        confiner.close()

    def test_a_successful_one_is_counted_and_keeps_it(self) -> None:
        rig = Rig()
        confiner = _confined(rig)
        rig.api.clip = None
        assert confiner.tick() == "reapplied"
        assert confiner.confined and confiner.reapplied == 1 and rig.api.clip == RECT
        confiner.close()

    def test_the_session_drops_the_stick_when_it_is_lost(self) -> None:
        rig = Rig()
        assert rig.confiner.start()
        sink = _Sink()
        session = StickSession(rig.confiner, sink)
        assert session.pick_up("ls", (5, 6), 50) == "held"
        rig.api.clip = None
        rig.api.set_ok = False
        assert session.tick() == "clip-lost"
        assert session.held is None and not session.confined
        assert sink.sticks["ls"] == (0.0, 0.0)
        rig.confiner.close()


class _Sink:
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


class _MovingApi(FakeApi):
    def __init__(self) -> None:
        super().__init__()
        self.pointer: Tuple[int, int] = (5, 6)

    def cursor_pos(self) -> Optional[Tuple[int, int]]:
        self._log("cursor_pos")
        return self.pointer


class TestNoGuardStillLeavesAStick:
    def test_the_pointer_can_be_read_after_the_guard_fails_to_start(self) -> None:
        rig = Rig()
        rig.spawn_raises = True
        assert rig.confiner.start() is False
        assert rig.confiner.cursor_pos() == (5, 6)
        assert rig.confiner.confine(RECT) is False
        assert "set_clip" not in rig.api.names()

    def test_an_unconfined_stick_is_picked_up_and_follows_the_pointer(self) -> None:
        rig = Rig(api=_MovingApi())
        rig.spawn_raises = True
        assert rig.confiner.start() is False
        sink = _Sink()
        session = StickSession(rig.confiner, sink)
        session.confine = False

        assert session.pick_up("ls", (500, 500), 50) == "held-unconfined"
        rig.api.pointer = (30, 6)
        assert session.tick() == "ok"
        assert sink.sticks["ls"] == (0.5, 0.0)
        assert "set_clip" not in rig.api.names()
        assert "set_cursor_pos" not in rig.api.names(), "a pointer that is not walled in was moved"

    def test_with_confinement_wanted_but_no_guard_it_falls_back_and_moves_nothing(self) -> None:
        rig = Rig(api=_MovingApi())
        rig.spawn_raises = True
        rig.confiner.start()
        session = StickSession(rig.confiner, _Sink())
        assert session.pick_up("ls", (500, 500), 50) == "held-unconfined"
        assert "set_cursor_pos" not in rig.api.names()

    def test_the_pointer_is_never_moved_unless_it_is_confined(self) -> None:
        rig = Rig()
        assert rig.confiner.start()
        assert rig.confiner.set_cursor_pos(1, 2) is False
        assert rig.confiner.confine(RECT)
        assert rig.confiner.set_cursor_pos(1, 2) is True
        rig.confiner.close()


def test_the_guards_limits_leave_room_for_one_another() -> None:
    # The keyboard decides the guard is gone after GUARD_SILENCE_S; the guard
    # decides the keyboard is hung after STALE_S. Each must outlast a poll.
    assert STALE_S > 2 * POLL_S
    assert GUARD_SILENCE_S > 2 * POLL_S
