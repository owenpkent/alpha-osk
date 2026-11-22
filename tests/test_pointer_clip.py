"""The pointer-clip guard and confiner, tested over fakes.

Nothing here touches the real pointer: the api is a recording fake, the
guard is either stepped in-process or a real child in dry-run mode.
"""

from __future__ import annotations

import itertools
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

import pytest

from src.platform import pointer_clip
from src.platform.pointer_clip import (
    GUARD_SILENCE_S,
    Heartbeat,
    PointerConfiner,
    Rect,
    run_guard,
)

_names = itertools.count()


def _unique_name() -> str:
    return f"alpha-osk-test-{os.getpid()}-{next(_names)}-{time.monotonic_ns()}"


class FakeApi:
    def __init__(self) -> None:
        self.calls: List[Tuple[Any, ...]] = []
        self.clip: Optional[Rect] = None
        self.set_ok = True
        self.release_ok = True
        self.release_raises = False
        self.on_call: Any = None

    def _log(self, *call: Any) -> None:
        self.calls.append(call)
        if self.on_call is not None:
            self.on_call(call)

    def get_clip(self) -> Optional[Rect]:
        self._log("get_clip")
        return self.clip

    def set_clip(self, rect: Rect) -> bool:
        self._log("set_clip", rect)
        if self.set_ok:
            self.clip = rect
        return self.set_ok

    def release(self) -> bool:
        self._log("release")
        if self.release_raises:
            raise OSError("boom")
        if self.release_ok:
            self.clip = None
        return self.release_ok

    def cursor_pos(self) -> Optional[Tuple[int, int]]:
        self._log("cursor_pos")
        return (5, 6)

    def set_cursor_pos(self, x: int, y: int) -> bool:
        self._log("set_cursor_pos", x, y)
        return True

    def names(self) -> List[str]:
        return [c[0] for c in self.calls]


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


class FakeProc:
    def __init__(self) -> None:
        self.running = True
        self.terminated = 0

    def poll(self) -> Optional[int]:
        return None if self.running else 0

    def terminate(self) -> None:
        self.terminated += 1
        self.running = False

    def wait(self, timeout: Optional[float] = None) -> int:
        return 0

    def kill(self) -> None:
        self.running = False


class Rig:
    """A confiner whose 'guard' is the test: it ticks guard_beat by hand."""

    def __init__(self, *, guard_beats_on_sleep: bool = True, api: Optional[FakeApi] = None):
        self.api = api if api is not None else FakeApi()
        self.clock = FakeClock()
        self.proc = FakeProc()
        self.spawned = 0
        self.spawn_raises = False
        self.guard_hb: Optional[Heartbeat] = None
        self.guard_beats_on_sleep = guard_beats_on_sleep

        def spawn(name: str) -> FakeProc:
            self.spawned += 1
            if self.spawn_raises:
                raise OSError("no spawn")
            self.guard_hb = Heartbeat.open(name)
            return self.proc

        def sleep(s: float) -> None:
            self.clock.sleep(s)
            if self.guard_beats_on_sleep and self.guard_hb is not None:
                self.guard_hb.guard_tick()

        self.confiner = PointerConfiner(
            api_factory=lambda: self.api, spawn_guard=spawn, clock=self.clock, sleep=sleep
        )

    def tick_guard(self) -> None:
        assert self.guard_hb is not None
        self.guard_hb.guard_tick()


@pytest.fixture
def rig() -> Rig:
    r = Rig()
    yield r
    r.confiner.close()


RECT: Rect = (10, 20, 110, 120)


class TestHeartbeat:
    def test_a_created_block_starts_zeroed_and_an_opened_one_sees_it(self) -> None:
        name = _unique_name()
        a = Heartbeat.create(name)
        b = Heartbeat.open(name)
        try:
            assert a.read() == pointer_clip.HeartbeatState(0, 0, 0, 0)
            a.set_active(True)
            a.beat()
            assert b.read().active == 1 and b.read().beat == 1
        finally:
            a.close()
            b.close()

    def test_each_handle_sees_the_others_writes(self) -> None:
        name = _unique_name()
        a = Heartbeat.create(name)
        b = Heartbeat.open(name)
        try:
            b.guard_tick()
            b.guard_tick()
            b.note_guard_release()
            s = a.read()
            assert (s.guard_beat, s.guard_releases) == (2, 1)
            assert (s.beat, s.active) == (0, 0)
        finally:
            a.close()
            b.close()

    def test_counters_wrap_at_two_to_the_32(self) -> None:
        a = Heartbeat.create(_unique_name())
        try:
            a._put(pointer_clip._OFF_BEAT, 2**32 - 1)
            a.beat()
            assert a.read().beat == 0
            a._put(pointer_clip._OFF_GUARD_BEAT, 2**32 - 1)
            a.guard_tick()
            assert a.read().guard_beat == 0
            a.beat()
            assert a.read().beat == 1
        finally:
            a.close()

    def test_active_can_be_cleared_again(self) -> None:
        a = Heartbeat.create(_unique_name())
        try:
            a.set_active(True)
            a.set_active(False)
            assert a.read().active == 0
        finally:
            a.close()


class _GuardRig:
    def __init__(self) -> None:
        self.hb = Heartbeat.create(_unique_name())
        self.api = FakeApi()
        self.clock = FakeClock()
        self.alive = True

    def run(self, n: int, **kw: Any) -> str:
        return run_guard(
            lambda: self.alive,
            self.hb,
            self.api,
            clock=self.clock,
            sleep=self.clock.sleep,
            max_iterations=n,
            **kw,
        )


@pytest.fixture
def guard() -> _GuardRig:
    g = _GuardRig()
    yield g
    g.hb.close()


class TestRunGuard:
    def test_it_releases_once_and_returns_when_the_parent_is_gone(self, guard) -> None:
        guard.alive = False
        assert guard.run(5) == "parent-gone"
        assert guard.api.names() == ["release"]

    def test_a_living_parent_with_no_clip_active_is_left_alone(self, guard) -> None:
        assert guard.run(50) == "max-iterations"
        assert guard.api.calls == []

    def test_it_releases_exactly_once_per_stall_and_counts_it(self, guard) -> None:
        guard.hb.set_active(True)
        guard.hb.beat()
        guard.run(40)  # 2 s of silence, far over the 0.4 s limit
        assert guard.api.names() == ["release"]
        assert guard.hb.read().guard_releases == 1

    def test_it_does_not_release_while_beats_keep_arriving(self, guard) -> None:
        guard.hb.set_active(True)
        for _ in range(60):
            guard.hb.beat()
            guard.run(1)
        assert guard.api.calls == []
        assert guard.hb.read().guard_releases == 0

    def test_it_does_not_release_when_inactive_however_stale(self, guard) -> None:
        guard.hb.set_active(False)
        guard.run(200)
        assert guard.api.calls == []

    def test_it_releases_again_on_a_second_stall_after_beats_resumed(self, guard) -> None:
        guard.hb.set_active(True)
        guard.hb.beat()
        guard.run(30)
        assert guard.hb.read().guard_releases == 1
        for _ in range(5):
            guard.hb.beat()
            guard.run(1)
        assert guard.hb.read().guard_releases == 1
        guard.run(30)
        assert guard.hb.read().guard_releases == 2
        assert guard.api.names() == ["release", "release"]

    def test_the_staleness_clock_starts_when_the_clip_goes_active(self, guard) -> None:
        guard.run(100)  # long idle, inactive
        guard.hb.set_active(True)
        guard.run(2)  # 0.1 s into activity: not stale yet
        assert guard.api.calls == []

    def test_a_parent_probe_that_raises_counts_as_the_parent_gone(self, guard) -> None:
        def boom() -> bool:
            raise OSError("probe")

        result = run_guard(
            boom,
            guard.hb,
            guard.api,
            clock=guard.clock,
            sleep=guard.clock.sleep,
            max_iterations=3,
        )
        assert result == "parent-gone"
        assert guard.api.names() == ["release"]

    def test_a_raising_api_does_not_kill_the_loop_and_is_retried(self, guard) -> None:
        guard.hb.set_active(True)
        guard.hb.beat()
        guard.api.release_raises = True
        assert guard.run(30) == "max-iterations"
        assert guard.hb.read().guard_releases == 0
        guard.api.release_raises = False
        guard.run(30)  # a fresh run restarts its staleness clock
        assert guard.hb.read().guard_releases == 1

    def test_a_broken_heartbeat_does_not_kill_the_loop(self, guard) -> None:
        guard.hb.close()  # every access now fails its assertion
        assert guard.run(5) == "max-iterations"


class TestConfinerConstruction:
    def test_construction_calls_nothing_and_spawns_nothing(self) -> None:
        r = Rig()
        assert r.api.calls == [] and r.spawned == 0
        assert not r.confiner.confined and not r.confiner.guard_alive


class TestStart:
    def test_start_succeeds_once_the_guard_has_beaten_twice(self, rig) -> None:
        assert rig.confiner.start() is True
        assert rig.spawned == 1
        assert rig.confiner.start() is True  # idempotent
        assert rig.spawned == 1

    def test_start_is_false_when_there_is_no_api(self) -> None:
        r = Rig()
        c = PointerConfiner(api_factory=lambda: None, spawn_guard=lambda n: r.proc)
        assert c.start() is False

    def test_start_is_false_and_torn_down_when_the_guard_never_beats(self) -> None:
        r = Rig(guard_beats_on_sleep=False)
        assert r.confiner.start() is False
        assert r.proc.terminated == 1
        assert r.confiner.confine(RECT) is False
        assert r.api.calls == []

    def test_start_is_false_when_the_guard_beats_only_once(self) -> None:
        r = Rig(guard_beats_on_sleep=False)
        orig_sleep = r.clock.sleep
        beats = iter([True])

        def one_beat(s: float) -> None:
            orig_sleep(s)
            if next(beats, False) and r.guard_hb is not None:
                r.guard_hb.guard_tick()

        r.confiner._sleep = one_beat
        assert r.confiner.start() is False

    def test_start_is_false_when_the_guard_exits_early(self) -> None:
        r = Rig(guard_beats_on_sleep=False)
        r.proc.running = False
        assert r.confiner.start() is False

    def test_start_is_false_when_spawning_raises(self) -> None:
        r = Rig()
        r.spawn_raises = True
        assert r.confiner.start() is False
        assert r.confiner.confine(RECT) is False


class TestConfine:
    def test_confine_is_refused_before_start(self) -> None:
        r = Rig()
        assert r.confiner.confine(RECT) is False
        assert r.api.calls == []

    def test_confine_is_refused_when_the_guard_has_gone_silent(self, rig) -> None:
        assert rig.confiner.start()
        rig.clock.t += GUARD_SILENCE_S + 0.1
        assert rig.confiner.confine(RECT) is False
        assert "set_clip" not in rig.api.names()

    def test_confine_is_refused_when_the_guard_process_has_exited(self, rig) -> None:
        assert rig.confiner.start()
        rig.proc.running = False
        assert rig.confiner.confine(RECT) is False
        assert "set_clip" not in rig.api.names()

    def test_confine_is_allowed_while_the_guard_keeps_beating(self, rig) -> None:
        assert rig.confiner.start()
        rig.clock.t += GUARD_SILENCE_S - 0.1
        rig.tick_guard()
        rig.clock.t += GUARD_SILENCE_S - 0.1
        assert rig.confiner.confine(RECT) is True

    def test_the_heartbeat_is_active_before_set_clip_is_called(self, rig) -> None:
        assert rig.confiner.start()
        seen: List[int] = []

        def on_call(call: Tuple[Any, ...]) -> None:
            if call[0] == "set_clip":
                assert rig.guard_hb is not None
                state = rig.guard_hb.read()
                seen.append(state.active * 10 + (1 if state.beat > 0 else 0))

        rig.api.on_call = on_call
        assert rig.confiner.confine(RECT) is True
        assert seen == [11]
        assert rig.confiner.confined and rig.confiner.rect == RECT

    def test_a_failed_set_clip_leaves_the_heartbeat_inactive(self, rig) -> None:
        assert rig.confiner.start()
        rig.api.set_ok = False
        assert rig.confiner.confine(RECT) is False
        assert rig.guard_hb is not None and rig.guard_hb.read().active == 0
        assert not rig.confiner.confined
        rig.confiner.release()
        assert "release" not in rig.api.names()  # it never held a clip

    def test_confining_again_moves_the_clip(self, rig) -> None:
        assert rig.confiner.start()
        assert rig.confiner.confine(RECT)
        new: Rect = (0, 0, 50, 50)
        assert rig.confiner.confine(new)
        assert rig.api.clip == new and rig.confiner.rect == new


class TestTick:
    def test_idle_when_not_confined(self, rig) -> None:
        assert rig.confiner.start()
        assert rig.confiner.tick() == "idle"

    def test_ok_when_the_clip_is_as_set(self, rig) -> None:
        assert rig.confiner.start() and rig.confiner.confine(RECT)
        before = rig.guard_hb.read().beat
        assert rig.confiner.tick() == "ok"
        assert rig.guard_hb.read().beat == before + 1
        assert rig.confiner.reapplied == 0

    def test_reapplied_when_something_else_changed_the_clip(self, rig) -> None:
        assert rig.confiner.start() and rig.confiner.confine(RECT)
        rig.api.clip = (0, 0, 1920, 1080)
        assert rig.confiner.tick() == "reapplied"
        assert rig.api.clip == RECT
        assert rig.confiner.reapplied == 1
        assert rig.confiner.tick() == "ok"
        assert rig.confiner.reapplied == 1

    def test_guard_released_releases_for_itself_before_forgetting(self, rig) -> None:
        # This used to assert the opposite ("forgets without releasing again").
        # That was the hole: the guard clears and then counts, and a tick
        # between the two re-applies the clip, so the count going up does not
        # mean nothing is clipped. See test_pointer_clip_release.py.
        assert rig.confiner.start() and rig.confiner.confine(RECT)
        rig.guard_hb.note_guard_release()
        rig.api.calls.clear()
        assert rig.confiner.tick() == "guard-released"
        assert not rig.confiner.confined and rig.confiner.rect is None
        assert rig.guard_hb.read().active == 0
        assert rig.api.names().count("release") == 1 and "set_clip" not in rig.api.names()
        assert rig.api.clip is None
        assert rig.confiner.tick() == "idle"

    def test_guard_lost_releases_the_clip(self, rig) -> None:
        assert rig.confiner.start() and rig.confiner.confine(RECT)
        rig.proc.running = False
        assert rig.confiner.tick() == "guard-lost"
        assert rig.api.clip is None
        assert not rig.confiner.confined
        assert rig.guard_hb.read().active == 0

    def test_a_guard_that_went_silent_is_lost_but_a_beating_one_is_not(self, rig) -> None:
        assert rig.confiner.start() and rig.confiner.confine(RECT)
        for _ in range(10):
            rig.clock.t += 0.3
            rig.tick_guard()
            assert rig.confiner.tick() == "ok"
        rig.clock.t += GUARD_SILENCE_S + 0.1
        assert rig.confiner.tick() == "guard-lost"

    def test_a_release_by_the_guard_wins_over_a_lost_guard(self, rig) -> None:
        assert rig.confiner.start() and rig.confiner.confine(RECT)
        rig.guard_hb.note_guard_release()
        rig.proc.running = False
        assert rig.confiner.tick() == "guard-released"

    def test_a_new_confine_after_a_guard_release_is_possible(self, rig) -> None:
        assert rig.confiner.start() and rig.confiner.confine(RECT)
        rig.guard_hb.note_guard_release()
        assert rig.confiner.tick() == "guard-released"
        assert rig.confiner.confine(RECT)
        assert rig.confiner.tick() == "ok"


class TestReleaseAndClose:
    def test_release_calls_the_api_only_when_a_clip_is_held(self, rig) -> None:
        assert rig.confiner.start()
        rig.confiner.release()
        assert "release" not in rig.api.names()
        assert rig.confiner.confine(RECT)
        rig.confiner.release()
        assert rig.api.names().count("release") == 1
        rig.confiner.release()
        assert rig.api.names().count("release") == 1
        assert rig.guard_hb.read().active == 0

    def test_release_never_raises(self, rig) -> None:
        assert rig.confiner.start() and rig.confiner.confine(RECT)
        rig.api.release_raises = True
        rig.confiner.release()
        assert not rig.confiner.confined

    def test_close_terminates_the_guard_and_releases_a_held_clip(self, rig) -> None:
        assert rig.confiner.start() and rig.confiner.confine(RECT)
        rig.confiner.close()
        assert rig.api.clip is None
        assert rig.proc.terminated == 1
        assert not rig.confiner.guard_alive

    def test_close_is_idempotent_and_ok_before_start(self, rig) -> None:
        PointerConfiner(api_factory=lambda: None).close()
        assert rig.confiner.start()
        rig.confiner.close()
        rig.confiner.close()
        assert rig.proc.terminated == 1

    def test_the_confiner_can_be_started_again_after_close(self, rig) -> None:
        assert rig.confiner.start()
        rig.confiner.close()
        rig.proc.running = True  # the next spawn is a fresh process
        assert rig.confiner.start()
        assert rig.spawned == 2

    def test_pointer_pass_throughs(self, rig) -> None:
        # Reading the pointer needs no guard (the unconfined stick depends on
        # it); moving it is allowed only while it is confined.
        assert rig.confiner.cursor_pos() == (5, 6)
        assert rig.confiner.set_cursor_pos(1, 2) is False
        assert rig.confiner.start()
        assert rig.confiner.cursor_pos() == (5, 6)
        assert rig.confiner.set_cursor_pos(1, 2) is False
        assert rig.confiner.confine(RECT)
        assert rig.confiner.set_cursor_pos(1, 2) is True


class TestTheSuiteNeverTouchesTheRealPointer:
    def test_default_fixtures_make_start_false_and_spawn_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        spawned: List[Any] = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: spawned.append(a))
        assert pointer_clip.win32_clip_api() is None
        assert PointerConfiner().start() is False
        assert spawned == []


@pytest.mark.skipif(sys.platform != "win32", reason="the guard watches a Windows process")
class TestTheRealGuardProcessInDryRun:
    @staticmethod
    def _wait(cond: Any, ceiling: float) -> bool:
        deadline = time.monotonic() + ceiling
        while time.monotonic() < deadline:
            if cond():
                return True
            time.sleep(0.02)
        return bool(cond())

    def test_it_beats_releases_a_stall_and_exits_with_its_parent(self) -> None:
        repo = Path(pointer_clip.__file__).resolve().parents[2]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        helper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        guard: Optional[subprocess.Popen[bytes]] = None
        hb = Heartbeat.create(_unique_name())
        try:
            env = dict(os.environ, ALPHA_OSK_CLIP_GUARD_DRY_RUN="1")
            guard = subprocess.Popen(
                [sys.executable, "-m", "src.platform.pointer_clip_guard", str(helper.pid), hb.name],
                cwd=str(repo),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
            assert self._wait(lambda: hb.read().guard_beat >= 3, 15.0), "guard never beat"
            assert hb.read().guard_releases == 0  # inactive: no release however long

            hb.beat()
            hb.set_active(True)
            assert self._wait(lambda: hb.read().guard_releases >= 1, 5.0), "no stall release"
            time.sleep(0.6)
            assert hb.read().guard_releases == 1  # once per stall

            assert guard.poll() is None
            helper.kill()
            helper.wait(timeout=10)
            assert self._wait(lambda: guard.poll() is not None, 10.0), "guard outlived parent"
            assert guard.returncode == 0
        finally:
            for p in (guard, helper):
                if p is not None and p.poll() is None:
                    p.kill()
                    p.wait(timeout=10)
            hb.close()
