"""The serial injector: paced or atomic groups, cancel units, inline or queued.

Platform-neutral, so these run on every CI runner.  Events are plain
strings; a *group* is one send call and a *unit* one character or
gesture.  The Windows synthesizer's own use of the injector is pinned in
tests/test_platform.py::TestWindowsInjectPacing.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional, Sequence, Tuple

import pytest

from src.platform.serial_injector import SerialInjector

GAP = 0.003


Burst = List[List[List[str]]]  # units of groups of events


def paced(*events: str) -> Burst:
    """A burst of one unit, one group per event."""
    return [[[event] for event in events]]


def atomic(*events: str) -> Burst:
    """A burst of one unit of one group."""
    return [[list(events)]]


def tap(key: str) -> Burst:
    """A burst of one unit: key down, key up, paced."""
    return paced(f"{key}+", f"{key}-")


class Harness:
    """A fake OS sink plus a scripted sleep, both logging in order."""

    def __init__(
        self,
        *,
        gap_s: float = GAP,
        inline_budget_s: float = 0.1,
        send_result: Optional[Callable[[Sequence[str]], Tuple[int, int]]] = None,
    ) -> None:
        self.log: List[tuple] = []
        self._lock = threading.Lock()
        # Called in turn, one per gap, before the gate (if any) is waited on.
        self.on_sleep: List[Callable[[], None]] = []
        self.gate: Optional[threading.Event] = None
        # Set by the scripted sleep once the worker waits at the gate.
        self.parked = threading.Event()
        self._send_result = send_result

        def send(events: Sequence[str]) -> Tuple[int, int]:
            with self._lock:
                self.log.append(("send", list(events)))
            if self._send_result is not None:
                return self._send_result(events)
            return len(events), 0

        def sleep(seconds: float) -> None:
            with self._lock:
                self.log.append(("sleep", seconds))
                index = sum(1 for entry in self.log if entry[0] == "sleep") - 1
            if index < len(self.on_sleep):
                self.on_sleep[index]()
            if self.gate is not None:
                self.parked.set()
                self.gate.wait(5)

        self.injector: SerialInjector[str] = SerialInjector(
            send,
            gap_s=gap_s,
            inline_budget_s=inline_budget_s,
            sleep=sleep,
            logger=logging.getLogger("test.serial_injector"),
        )

    @property
    def sent(self) -> List[str]:
        return [event for kind, payload in self.log if kind == "send" for event in payload]

    @property
    def calls(self) -> List[List[str]]:
        return [payload for kind, payload in self.log if kind == "send"]

    def background(self) -> "Harness":
        """Gate every sleep so the worker parks until the test opens it."""
        self.gate = threading.Event()
        return self

    def wait_parked(self) -> None:
        """Block until the worker has started and is waiting at the gate.

        A cancel or shutdown issued before the worker popped its first
        job would drop that job whole, so every gated test waits here
        before acting on the worker.
        """
        assert self.parked.wait(5), "the worker never reached its first gap"

    def finish(self) -> None:
        assert self.gate is not None
        self.gate.set()
        assert self.injector.settle(5)


class TestPacedAndAtomicGroups:
    def test_one_send_per_group_with_a_gap_between_and_none_around(self):
        h = Harness()
        h.injector.inject(tap("a") + tap("b"))
        assert h.log == [
            ("send", ["a+"]),
            ("sleep", GAP),
            ("send", ["a-"]),
            ("sleep", GAP),
            ("send", ["b+"]),
            ("sleep", GAP),
            ("send", ["b-"]),
        ]

    def test_a_single_group_never_sleeps(self):
        h = Harness()
        h.injector.inject(atomic("shift+"))
        assert h.log == [("send", ["shift+"])]

    def test_nothing_is_nothing(self):
        h = Harness()
        h.injector.inject([])
        h.injector.inject([[]])
        assert h.log == []
        assert not h.injector.busy

    def test_an_atomic_group_is_one_call_and_no_sleep_inside(self):
        h = Harness()
        chord = atomic("ctrl+", "c+", "c-", "ctrl-")
        h.injector.inject(chord)
        assert h.log == [("send", ["ctrl+", "c+", "c-", "ctrl-"])]

    def test_groups_of_one_unit_are_paced_like_any_others(self):
        """A shift-wrapped capital (one atomic group) between plain letters."""
        h = Harness()
        h.injector.inject(tap("a") + atomic("shift+", "r+", "r-", "shift-") + tap("b"))
        assert h.calls == [["a+"], ["a-"], ["shift+", "r+", "r-", "shift-"], ["b+"], ["b-"]]
        assert sum(1 for entry in h.log if entry[0] == "sleep") == 4

    def test_paced_duration_counts_the_gaps_between_groups(self):
        h = Harness(gap_s=0.01)
        assert h.injector.paced_duration_s(0) == 0
        assert h.injector.paced_duration_s(1) == 0
        assert h.injector.paced_duration_s(5) == pytest.approx(0.04)


class TestInlineOrQueued:
    def test_a_burst_within_the_budget_is_delivered_before_inject_returns(self):
        h = Harness(gap_s=0.01, inline_budget_s=0.05)  # up to 6 groups inline
        h.injector.inject(tap("a") + tap("b") + tap("c"))
        assert not h.injector.busy
        assert len(h.sent) == 6

    def test_a_burst_over_the_budget_is_delivered_by_a_worker(self):
        h = Harness(gap_s=0.01, inline_budget_s=0.05).background()
        units = tap("a") + tap("b") + tap("c") + tap("d")  # 7 gaps: over the budget
        h.injector.inject(units)
        h.wait_parked()
        assert h.injector.busy
        assert len(h.sent) < 8
        h.finish()
        assert not h.injector.busy
        assert h.sent == ["a+", "a-", "b+", "b-", "c+", "c-", "d+", "d-"]

    def test_a_burst_behind_a_running_one_queues_and_inject_returns_at_once(self):
        """Nothing on the caller's thread waits for the worker: the second
        burst, however short, joins the FIFO and lands after the first,
        one gap behind it."""
        h = Harness(gap_s=0.01, inline_budget_s=0.05).background()
        first = tap("a") + tap("b") + tap("c") + tap("d")
        h.injector.inject(first)
        h.wait_parked()
        h.injector.inject(tap("z"))  # returns immediately, worker parked
        assert h.injector.busy
        assert "z+" not in h.sent
        h.finish()
        assert h.sent == ["a+", "a-", "b+", "b-", "c+", "c-", "d+", "d-", "z+", "z-"]
        # One gap between the last group of the first burst and the first of the second.
        joined = h.log.index(("send", ["d-"]))
        assert h.log[joined + 1 : joined + 3] == [("sleep", 0.01), ("send", ["z+"])]

    def test_several_queued_bursts_keep_their_order(self):
        h = Harness(gap_s=0.01, inline_budget_s=0.0).background()
        for key in "abc":
            h.injector.inject(tap(key))
            h.wait_parked()
        h.finish()
        assert h.sent == ["a+", "a-", "b+", "b-", "c+", "c-"]

    def test_the_worker_exits_when_the_queue_is_empty_and_inline_resumes(self):
        h = Harness(gap_s=0.01, inline_budget_s=0.05).background()
        h.injector.inject(tap("a") + tap("b") + tap("c") + tap("d"))
        h.wait_parked()
        h.finish()
        h.gate = None
        h.injector.inject(tap("z"))
        assert not h.injector.busy
        assert h.sent[-2:] == ["z+", "z-"]

    def test_settle_when_idle_is_immediate(self):
        h = Harness()
        assert h.injector.settle(0)

    def test_a_failing_worker_is_logged_and_does_not_wedge_the_next_burst(self, caplog):
        def boom(events: Sequence[str]) -> Tuple[int, int]:
            raise RuntimeError("sink gone")

        h = Harness(gap_s=0.01, inline_budget_s=0.0, send_result=boom)
        with caplog.at_level(logging.WARNING, logger="test.serial_injector"):
            h.injector.inject(tap("a"))
            assert h.injector.settle(5)
        assert "Background key burst failed" in caplog.text
        assert not h.injector.busy


class TestCancel:
    def _background(self, **kwargs) -> Harness:
        # A zero budget sends every multi-group burst to the worker.
        return Harness(gap_s=0.001, inline_budget_s=0.0, **kwargs)

    def test_cancel_stops_at_the_next_unit_boundary(self):
        """Requested in the gap after a-down: the unit (a-up) is finished,
        then the burst stops before b.  The check runs before each unit,
        so a cancel set inside a unit takes effect at the next boundary."""
        h = self._background()
        h.on_sleep = [h.injector.cancel]
        h.injector.inject(tap("a") + tap("b"), cancellable=True)
        assert h.injector.settle(5)
        assert h.sent == ["a+", "a-"]

    def test_a_surrogate_pair_is_one_unit(self):
        """Four paced groups, one character: a cancel in the first gap
        still delivers all four (review of cc0f8d1)."""
        h = self._background()
        h.on_sleep = [h.injector.cancel]
        emoji = paced("hi+", "hi-", "lo+", "lo-")
        h.injector.inject(emoji + tap("x") + tap("y"), cancellable=True)
        assert h.injector.settle(5)
        assert h.sent == ["hi+", "hi-", "lo+", "lo-"]

    def test_cancel_drops_queued_cancellable_bursts_too(self):
        h = self._background().background()
        h.injector.inject(tap("a") + tap("b"), cancellable=True)
        h.wait_parked()
        h.injector.inject(tap("c"), cancellable=True)
        h.injector.cancel()
        h.finish()
        assert "c+" not in h.sent
        assert h.sent in (["a+", "a-"], ["a+", "a-", "b+", "b-"])

    def test_cancel_leaves_bursts_that_are_not_cancellable_alone(self):
        """A queued modifier re-hold or a key the user tapped is delivered;
        only typed text is dropped."""
        h = self._background().background()
        h.injector.inject(tap("a") + tap("b"), cancellable=True)
        h.wait_parked()
        h.injector.inject(atomic("shift+"))
        h.injector.inject(tap("z"))
        h.injector.cancel()
        h.finish()
        assert h.sent[-3:] == ["shift+", "z+", "z-"]
        assert "b+" not in h.sent

    def test_cancel_does_not_touch_a_running_burst_that_is_not_cancellable(self):
        h = self._background()
        h.on_sleep = [h.injector.cancel]
        h.injector.inject(tap("a") + tap("b"))
        assert h.injector.settle(5)
        assert h.sent == ["a+", "a-", "b+", "b-"]

    def test_cancel_when_idle_is_harmless(self):
        h = self._background()
        h.injector.cancel()
        h.injector.inject(tap("a") + tap("b"), cancellable=True)
        assert h.injector.settle(5)
        assert h.sent == ["a+", "a-", "b+", "b-"]

    def test_cancel_does_not_touch_an_inline_burst(self):
        """Inline bursts finish before their caller can ask."""
        h = Harness()
        h.on_sleep = [h.injector.cancel]
        h.injector.inject(tap("a") + tap("b"), cancellable=True)
        assert h.sent == ["a+", "a-", "b+", "b-"]


class TestShutdown:
    def test_shutdown_drops_the_queue_finishes_the_unit_and_joins(self):
        h = Harness(gap_s=0.001, inline_budget_s=0.0).background()
        h.injector.inject(paced("s+", "a+", "a-", "s-") + tap("b"))
        h.wait_parked()
        h.injector.inject(atomic("shift+"))  # queued, not cancellable, dropped anyway
        threading.Timer(0.05, h.gate.set).start()  # type: ignore[union-attr]
        assert h.injector.shutdown(5)
        assert not h.injector.busy
        assert h.sent == ["s+", "a+", "a-", "s-"]

    def test_after_shutdown_the_next_burst_goes_out_inline(self):
        h = Harness(gap_s=0.001, inline_budget_s=0.0).background()
        h.injector.inject(tap("a") + tap("b"))
        h.wait_parked()
        threading.Timer(0.05, h.gate.set).start()  # type: ignore[union-attr]
        assert h.injector.shutdown(5)
        h.gate = None
        h.injector.inject(atomic("shift-"))
        assert h.sent[-1] == "shift-"

    def test_shutdown_when_idle_is_immediate(self):
        h = Harness()
        assert h.injector.shutdown(0)


class TestShortCounts:
    def test_a_partial_paced_burst_warns_once_with_the_sum_and_last_error(self, caplog):
        calls = {"n": 0}

        def flaky(events: Sequence[str]) -> Tuple[int, int]:
            calls["n"] += 1
            if calls["n"] == 2:
                return 0, 5
            return len(events), 0

        h = Harness(send_result=flaky)
        with caplog.at_level(logging.WARNING, logger="test.serial_injector"):
            h.injector.inject(tap("a") + paced("b+"))
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "injected 2/3 events (error=5)" in warnings[0].getMessage()
        # Every group was still attempted, in order.
        assert h.sent == ["a+", "a-", "b+"]

    def test_a_partial_atomic_group_warns(self, caplog):
        h = Harness(send_result=lambda events: (1, 87))
        with caplog.at_level(logging.WARNING, logger="test.serial_injector"):
            h.injector.inject(atomic("a+", "a-"))
        assert "injected 1/2 events (error=87)" in caplog.text

    def test_a_full_burst_is_quiet(self, caplog):
        h = Harness()
        with caplog.at_level(logging.WARNING, logger="test.serial_injector"):
            h.injector.inject(tap("a"))
        assert caplog.text == ""
