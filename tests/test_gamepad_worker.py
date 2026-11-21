"""PadWorker over a fake device, on a real thread. Events are ceilings, never races."""

from __future__ import annotations

import threading
from typing import List, Optional, Tuple

from src.gamepad.state import NEUTRAL, PadReport
from src.gamepad.worker import MAX_QUEUED_REPORTS, PadWorker

CEILING_S = 5.0


class _Device:
    def __init__(self, fail_on: Optional[int] = None) -> None:
        self.applied: List[PadReport] = []
        self.closes = 0
        self.fail_on = fail_on
        self.seen = threading.Event()
        self.expect = 0

    def apply(self, report: PadReport) -> None:
        if self.fail_on is not None and len(self.applied) + 1 == self.fail_on:
            self.applied.append(report)
            raise OSError("secret detail from the driver")
        self.applied.append(report)
        if len(self.applied) >= self.expect:
            self.seen.set()

    def close(self) -> None:
        self.closes += 1


class _Harness:
    def __init__(self, device: Optional[_Device] = None, gate: Optional[threading.Event] = None):
        self.device = device or _Device()
        self.gate = gate
        self.events: List[Tuple[str, str]] = []
        self.thread_names: List[str] = []
        self.got = {k: threading.Event() for k in ("on", "error", "off")}
        self.worker = PadWorker(self._open, self._status)

    def _open(self) -> _Device:
        if self.gate is not None:
            assert self.gate.wait(CEILING_S)
        return self.device

    def _status(self, status: str, detail: str) -> None:
        self.events.append((status, detail))
        self.thread_names.append(threading.current_thread().name)
        self.got[status].set()


def _report(n: int) -> PadReport:
    return PadReport(buttons=n)


class TestOpening:
    def test_reports_on_from_the_worker_thread_after_a_successful_open(self) -> None:
        h = _Harness()
        h.worker.start()
        assert h.got["on"].wait(CEILING_S)
        assert h.events == [("on", "")]
        assert h.thread_names == ["pad-worker"]
        h.worker.stop()

    def test_an_open_failure_reports_error_with_only_the_type_name(self) -> None:
        events: List[Tuple[str, str]] = []
        done = threading.Event()

        def bad_open() -> _Device:
            raise PermissionError("C:\\secret\\path traceback text")

        def status(s: str, d: str) -> None:
            events.append((s, d))
            done.set()

        worker = PadWorker(bad_open, status)
        worker.start()
        assert done.wait(CEILING_S)
        worker.stop()
        assert events == [("error", "PermissionError")]
        assert not worker.running

    def test_a_report_submitted_before_the_open_finishes_is_applied_after(self) -> None:
        gate = threading.Event()
        h = _Harness(gate=gate)
        h.device.expect = 1
        h.worker.start()
        h.worker.submit(_report(7))
        assert not h.got["on"].is_set()
        gate.set()
        assert h.device.seen.wait(CEILING_S)
        assert h.device.applied[0] == _report(7)
        h.worker.stop()


class TestOrdering:
    def test_reports_are_applied_in_order_and_none_dropped(self) -> None:
        h = _Harness()
        h.device.expect = 5
        h.worker.start()
        assert h.got["on"].wait(CEILING_S)
        for n in (1, 2, 3, 4, 5):
            h.worker.submit(_report(n))
        assert h.device.seen.wait(CEILING_S)
        assert h.device.applied[:5] == [_report(n) for n in (1, 2, 3, 4, 5)]
        assert h.worker.dropped_reports == 0
        h.worker.stop()

    def test_the_queue_bound_drops_the_oldest_and_counts_them(self) -> None:
        h = _Harness(gate=threading.Event())
        h.worker.start()
        total = MAX_QUEUED_REPORTS + 10
        for n in range(1, total + 1):
            h.worker.submit(_report(n))
        assert h.worker.dropped_reports == 10
        h.device.expect = MAX_QUEUED_REPORTS
        assert h.gate is not None
        h.gate.set()
        assert h.device.seen.wait(CEILING_S)
        assert h.device.applied[0] == _report(11)
        assert h.device.applied[MAX_QUEUED_REPORTS - 1] == _report(total)
        h.worker.stop()

    def test_under_the_bound_nothing_is_counted_as_dropped(self) -> None:
        h = _Harness(gate=threading.Event())
        h.worker.start()
        for n in range(MAX_QUEUED_REPORTS):
            h.worker.submit(_report(n))
        assert h.worker.dropped_reports == 0
        assert h.gate is not None
        h.gate.set()
        h.worker.stop()


class TestStopping:
    def test_stop_applies_neutral_last_closes_once_and_reports_off_once(self) -> None:
        h = _Harness()
        h.worker.start()
        assert h.got["on"].wait(CEILING_S)
        h.worker.submit(_report(3))
        h.worker.stop()
        assert not h.worker.running
        assert h.device.applied[-1] == NEUTRAL
        assert h.device.closes == 1
        assert h.events.count(("off", "")) == 1

    def test_stop_discards_what_is_still_queued(self) -> None:
        h = _Harness(gate=threading.Event())
        h.worker.start()
        for n in (1, 2, 3):
            h.worker.submit(_report(n))
        assert h.gate is not None
        h.gate.set()
        h.worker.stop()
        # Whatever slipped through before the stop, neutral is the last word.
        assert h.device.applied[-1] == NEUTRAL

    def test_stop_twice_is_harmless(self) -> None:
        h = _Harness()
        h.worker.start()
        assert h.got["on"].wait(CEILING_S)
        h.worker.stop()
        h.worker.stop()
        assert h.device.closes == 1
        assert h.events.count(("off", "")) == 1

    def test_stop_before_start_is_harmless(self) -> None:
        h = _Harness()
        h.worker.stop()
        assert h.events == []
        assert not h.worker.running

    def test_a_stopped_worker_cannot_be_started(self) -> None:
        # Starting one would plug a pad only to unplug it at once, and a
        # controller appearing is something a game reacts to.
        opened: List[int] = []
        h = _Harness()
        h.worker = PadWorker(lambda: opened.append(1) or h.device, h._status)
        h.worker.stop()
        h.worker.start()
        assert not h.worker.running
        assert opened == []
        assert h.events == []

    def test_a_submit_after_stop_is_ignored(self) -> None:
        h = _Harness()
        h.worker.start()
        assert h.got["on"].wait(CEILING_S)
        h.worker.stop()
        before = list(h.device.applied)
        h.worker.submit(_report(9))
        assert h.device.applied == before


class TestFailing:
    def test_an_apply_failure_reports_error_closes_and_ends_the_thread(self) -> None:
        device = _Device(fail_on=1)
        h = _Harness(device)
        h.worker.start()
        assert h.got["on"].wait(CEILING_S)
        h.worker.submit(_report(1))
        assert h.got["error"].wait(CEILING_S)
        h.worker.stop()
        assert ("error", "OSError") in h.events
        assert all("secret" not in detail for _, detail in h.events)
        assert device.closes == 1
        assert not h.worker.running
        assert ("off", "") not in h.events

    def test_stop_after_an_error_exit_is_harmless(self) -> None:
        device = _Device(fail_on=1)
        h = _Harness(device)
        h.worker.start()
        assert h.got["on"].wait(CEILING_S)
        h.worker.submit(_report(1))
        assert h.got["error"].wait(CEILING_S)
        h.worker.stop()
        h.worker.stop()
        assert device.closes == 1
