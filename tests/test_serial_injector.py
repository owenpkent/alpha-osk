"""The serial injector: paced or atomic delivery, inline or in the background.

Platform-neutral, so these run on every CI runner.  Events are
``(key, is_down)`` tuples and ``key_state`` is the identity function; the
Windows synthesizer's own use of the injector is pinned in
tests/test_platform.py::TestWindowsInjectPacing.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, List, Optional, Sequence, Tuple

import pytest

from src.platform.serial_injector import SerialInjector

Event = Tuple[str, bool]


def down(key: str) -> Event:
    return (key, True)


def up(key: str) -> Event:
    return (key, False)


def tap(key: str) -> List[Event]:
    return [down(key), up(key)]


class Harness:
    """A fake OS sink plus a scripted sleep, both logging in order."""

    def __init__(
        self,
        *,
        gap_s: float = 0.003,
        inline_budget_s: float = 0.1,
        send_result: Optional[Callable[[Sequence[Event]], Tuple[int, int]]] = None,
    ) -> None:
        self.log: List[tuple] = []
        self._lock = threading.Lock()
        self.on_sleep: List[Callable[[], None]] = []
        self.gate: Optional[threading.Event] = None
        self._send_result = send_result

        def send(events: Sequence[Event]) -> Tuple[int, int]:
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
                self.gate.wait(5)

        self.injector: SerialInjector[Event] = SerialInjector(
            send,
            gap_s=gap_s,
            inline_budget_s=inline_budget_s,
            key_state=lambda event: event,
            sleep=sleep,
            logger=logging.getLogger("test.serial_injector"),
        )

    @property
    def sent(self) -> List[Event]:
        return [event for kind, payload in self.log if kind == "send" for event in payload]

    @property
    def send_calls(self) -> List[List[Event]]:
        return [payload for kind, payload in self.log if kind == "send"]

    @property
    def sleeps(self) -> int:
        return sum(1 for entry in self.log if entry[0] == "sleep")


class TestPacedDelivery:
    def test_one_send_per_event_with_a_gap_between_and_none_around(self):
        h = Harness()
        h.injector.inject(tap("a") + tap("b"))
        assert h.log == [
            ("send", [down("a")]),
            ("sleep", 0.003),
            ("send", [up("a")]),
            ("sleep", 0.003),
            ("send", [down("b")]),
            ("sleep", 0.003),
            ("send", [up("b")]),
        ]

    def test_a_single_event_never_sleeps(self):
        h = Harness()
        h.injector.inject([down("shift")])
        assert h.log == [("send", [down("shift")])]

    def test_nothing_is_nothing(self):
        h = Harness()
        h.injector.inject([])
        assert h.log == []
        assert not h.injector.busy

    def test_atomic_is_one_call_and_no_sleep(self):
        h = Harness()
        chord = [down("ctrl"), down("c"), up("c"), up("ctrl")]
        h.injector.inject(chord, atomic=True)
        assert h.log == [("send", chord)]

    def test_paced_duration_counts_the_gaps(self):
        h = Harness(gap_s=0.01)
        assert h.injector.paced_duration_s(0) == 0
        assert h.injector.paced_duration_s(1) == 0
        assert h.injector.paced_duration_s(5) == pytest.approx(0.04)


class TestInlineOrBackground:
    def test_a_burst_within_the_budget_is_delivered_before_inject_returns(self):
        h = Harness(gap_s=0.01, inline_budget_s=0.05)  # up to 6 events inline
        h.injector.inject(tap("a") + tap("b") + tap("c"))
        assert not h.injector.busy
        assert len(h.sent) == 6

    def test_a_burst_over_the_budget_is_delivered_by_a_worker(self):
        h = Harness(gap_s=0.01, inline_budget_s=0.05)
        h.gate = threading.Event()
        events = tap("a") + tap("b") + tap("c") + tap("d")  # 7 gaps: over the budget
        h.injector.inject(events)
        assert h.injector.busy
        assert len(h.sent) < len(events)
        h.gate.set()
        assert h.injector.settle(5)
        assert not h.injector.busy
        assert h.sent == events

    def test_the_next_burst_waits_for_the_background_one(self):
        """Order holds across the thread boundary, and no two bursts interleave."""
        h = Harness(gap_s=0.01, inline_budget_s=0.05)
        h.gate = threading.Event()
        first = tap("a") + tap("b") + tap("c") + tap("d")
        h.injector.inject(first)
        assert h.injector.busy
        threading.Timer(0.05, h.gate.set).start()

        h.injector.inject(tap("z"))
        assert not h.injector.busy
        assert h.sent == first + tap("z")

    def test_settle_when_idle_is_immediate(self):
        h = Harness()
        assert h.injector.settle(0)

    def test_a_failing_worker_is_logged_and_does_not_wedge_the_next_burst(self, caplog):
        def boom(events: Sequence[Event]) -> Tuple[int, int]:
            raise RuntimeError("sink gone")

        h = Harness(gap_s=0.01, inline_budget_s=0.0, send_result=boom)
        with caplog.at_level(logging.WARNING, logger="test.serial_injector"):
            h.injector.inject(tap("a"))
            assert h.injector.settle(5)
        assert "Background key burst failed" in caplog.text
        assert not h.injector.busy


class TestCancel:
    def _background(self, **kwargs) -> Harness:
        # A zero budget sends every multi-event burst to the worker.
        return Harness(gap_s=0.001, inline_budget_s=0.0, **kwargs)

    def test_cancel_stops_at_the_next_point_where_no_key_is_down(self):
        """Requested while Shift and A are both down: the wrap is finished
        (A up, Shift up), then the burst stops; B is never pressed."""
        h = self._background()
        events = [down("shift"), down("a"), up("a"), up("shift")] + tap("b") + tap("c")
        # Sleep #0 sits between shift-down and a-down; sleep #1 follows a-down.
        h.on_sleep = [lambda: None, h.injector.cancel]
        h.injector.inject(events)
        assert h.injector.settle(5)
        assert h.sent == [down("shift"), down("a"), up("a"), up("shift")]

    def test_cancel_while_a_key_is_down_releases_it_and_stops(self):
        """Requested in the gap after A down: A is released, then the burst
        stops before B.  The check runs before each gap, so a cancel set
        during a gap takes effect at the next one."""
        h = self._background()
        h.on_sleep = [h.injector.cancel]
        h.injector.inject(tap("a") + tap("b"))
        assert h.injector.settle(5)
        assert h.sent == tap("a")

    def test_cancel_when_idle_does_not_leak_into_the_next_burst(self):
        h = self._background()
        h.injector.cancel()
        h.injector.inject(tap("a") + tap("b"))
        assert h.injector.settle(5)
        assert h.sent == tap("a") + tap("b")

    def test_cancel_does_not_touch_an_inline_burst(self):
        """Inline bursts finish before their caller can ask; the flag is only
        ever set while a worker is alive."""
        h = Harness()
        h.on_sleep = [h.injector.cancel]
        h.injector.inject(tap("a") + tap("b"))
        assert h.sent == tap("a") + tap("b")


class TestShortCounts:
    def test_a_partial_paced_burst_warns_once_with_the_sum_and_last_error(self, caplog):
        calls = {"n": 0}

        def flaky(events: Sequence[Event]) -> Tuple[int, int]:
            calls["n"] += 1
            if calls["n"] == 2:
                return 0, 5
            return len(events), 0

        h = Harness(send_result=flaky)
        with caplog.at_level(logging.WARNING, logger="test.serial_injector"):
            h.injector.inject(tap("a") + [down("b")])
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "injected 2/3 events (error=5)" in warnings[0].getMessage()
        # Every event was still attempted, in order.
        assert h.sent == tap("a") + [down("b")]

    def test_a_partial_atomic_burst_warns(self, caplog):
        h = Harness(send_result=lambda events: (1, 87))
        with caplog.at_level(logging.WARNING, logger="test.serial_injector"):
            h.injector.inject(tap("a"), atomic=True)
        assert "injected 1/2 events (error=87)" in caplog.text

    def test_a_full_burst_is_quiet(self, caplog):
        h = Harness()
        with caplog.at_level(logging.WARNING, logger="test.serial_injector"):
            h.injector.inject(tap("a"))
        assert caplog.text == ""
