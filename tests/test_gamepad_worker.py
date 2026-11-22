"""PadWorker over a fake device, on a real thread. Events are ceilings, never races."""

from __future__ import annotations

import threading
import time
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


class _BlockingDevice:
    """The first apply blocks on ``release`` (a stuck driver call)."""

    def __init__(self) -> None:
        self.applied: List[PadReport] = []
        self.events: List[str] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancelled = False
        self.closes = 0

    def apply(self, report: PadReport) -> None:
        self.applied.append(report)
        if len(self.applied) == 1:
            self.entered.set()
            assert self.release.wait(CEILING_S)

    def cancel(self) -> None:
        self.cancelled = True
        self.events.append("cancel")

    def close(self) -> None:
        self.closes += 1
        self.events.append("close")


class TestAStuckApply:
    def test_stop_times_out_cancels_first_and_reports_off_only_after_the_close(self) -> None:
        device = _BlockingDevice()
        events: List[str] = []
        off = threading.Event()

        def status(s: str, d: str) -> None:
            events.append(s)
            device.events.append(f"status:{s}")
            if s == "off":
                off.set()

        worker = PadWorker(lambda: device, status)
        worker.start()
        worker.submit(_report(7))
        assert device.entered.wait(CEILING_S)
        worker.stop(timeout_s=0.05)
        assert worker.running
        assert device.cancelled
        assert "off" not in events
        device.release.set()
        assert off.wait(CEILING_S)
        worker.stop()
        assert not worker.running
        assert device.applied == [_report(7), NEUTRAL]
        assert device.closes == 1
        assert device.events.index("cancel") < device.events.index("close")
        assert device.events.index("close") < device.events.index("status:off")

    def test_a_device_without_cancel_still_stops(self) -> None:
        h = _Harness()
        h.worker.start()
        assert h.got["on"].wait(CEILING_S)
        h.worker.stop()
        assert not h.worker.running


class _TimedDevice:
    def __init__(self, first_apply_s: float = 0.0, hold_first: bool = False) -> None:
        self.first_apply_s = first_apply_s
        self.log: List[Tuple[float, PadReport]] = []
        self.closes = 0
        # With ``hold_first`` the first write blocks until the test lets it go,
        # and says when it has started: "the worker has taken the first report
        # out of the queue" is then a fact, not a hope about scheduling.
        self.hold_first = hold_first
        self.entered = threading.Event()
        self.release = threading.Event()

    def apply(self, report: PadReport) -> None:
        if not self.log and self.hold_first:
            self.entered.set()
            assert self.release.wait(CEILING_S)
        if not self.log and self.first_apply_s:
            time.sleep(self.first_apply_s)
        self.log.append((time.perf_counter(), report))

    def close(self) -> None:
        self.closes += 1

    def wait_for(self, count: int) -> bool:
        deadline = time.perf_counter() + CEILING_S
        while len(self.log) < count and time.perf_counter() < deadline:
            time.sleep(0.002)
        return len(self.log) >= count


HOLD_S = 0.1
TOLERANCE_S = 0.01


def _timed(device: _TimedDevice, min_hold_s: float = HOLD_S) -> PadWorker:
    worker = PadWorker(lambda: device, lambda s, d: None)
    worker.min_hold_s = min_hold_s
    return worker


class TestMinimumHold:
    """A release is held back only when the caller says which press it ends (``after``)."""

    def test_a_release_behind_a_slow_first_write_waits_out_the_floor(self) -> None:
        device = _TimedDevice(first_apply_s=0.15)
        worker = _timed(device)
        worker.start()
        press = worker.submit(_report(1))
        worker.submit(NEUTRAL, after=press)
        assert device.wait_for(2)
        gap = device.log[1][0] - device.log[0][0]
        assert gap >= HOLD_S - TOLERANCE_S
        assert gap < CEILING_S
        assert device.log[1][1] == NEUTRAL
        worker.stop()

    def test_a_report_without_after_is_never_delayed(self) -> None:
        device = _TimedDevice()
        worker = _timed(device, min_hold_s=0.5)
        worker.start()
        start = time.perf_counter()
        worker.submit(_report(1))
        worker.submit(NEUTRAL)
        worker.submit(PadReport(left_y=1.0))
        worker.submit(PadReport(left_x=1.0))
        assert device.wait_for(4)
        assert device.log[3][0] - start < 0.4
        worker.stop()

    def test_a_press_applied_long_ago_adds_no_wait(self) -> None:
        device = _TimedDevice()
        worker = _timed(device, min_hold_s=0.2)
        worker.start()
        press = worker.submit(_report(1))
        assert device.wait_for(1)
        time.sleep(0.25)
        submitted = time.perf_counter()
        worker.submit(NEUTRAL, after=press)
        assert device.wait_for(2)
        assert device.log[1][0] - submitted < 0.15
        worker.stop()

    def test_an_unknown_sequence_number_adds_no_wait(self) -> None:
        device = _TimedDevice()
        worker = _timed(device, min_hold_s=0.5)
        worker.start()
        start = time.perf_counter()
        worker.submit(NEUTRAL, after=9999)
        assert device.wait_for(1)
        assert device.log[0][0] - start < 0.4
        worker.stop()

    def test_a_press_the_queue_dropped_adds_no_wait(self) -> None:
        device = _TimedDevice(hold_first=True)
        worker = _timed(device, min_hold_s=0.5)
        worker.start()
        worker.submit(_report(1))
        # The first report is in flight, so it is out of the queue and cannot
        # be evicted. Without this, a worker thread that starts late leaves it
        # queued, it is dropped with the press, and only MAX_QUEUED_REPORTS
        # reports can ever arrive where this test waits for one more.
        assert device.entered.wait(CEILING_S)
        press = worker.submit(_report(2))
        for n in range(MAX_QUEUED_REPORTS):
            worker.submit(_report(3 + n))
        assert worker.dropped_reports == 1, "the press was not the report that got dropped"
        worker.submit(NEUTRAL, after=press)
        assert worker.dropped_reports == 2
        start = time.perf_counter()
        device.release.set()
        # One in flight plus a full queue.
        assert device.wait_for(MAX_QUEUED_REPORTS + 1)
        assert device.log[0][1] == _report(1) and device.log[-1][1] == NEUTRAL
        assert _report(2) not in [r for _, r in device.log]
        assert device.log[-1][0] - start < 0.4, (
            "a release waited on a press that never reached the pad"
        )
        worker.stop()

    def test_reports_behind_a_waiting_release_stay_in_order(self) -> None:
        device = _TimedDevice()
        worker = _timed(device)
        worker.start()
        press = worker.submit(_report(1))
        worker.submit(NEUTRAL, after=press)
        worker.submit(_report(2))
        assert device.wait_for(3)
        assert [r for _, r in device.log] == [_report(1), NEUTRAL, _report(2)]
        worker.stop()

    def test_stop_during_a_hold_returns_promptly_and_ends_on_neutral(self) -> None:
        device = _TimedDevice()
        worker = _timed(device, min_hold_s=30.0)
        worker.start()
        press = worker.submit(_report(1))
        assert device.wait_for(1)
        worker.submit(_report(2), after=press)
        time.sleep(0.05)
        begin = time.perf_counter()
        worker.stop()
        assert time.perf_counter() - begin < 2.0
        assert not worker.running
        assert device.log[-1][1] == NEUTRAL
        assert _report(2) not in [r for _, r in device.log]
        assert device.closes == 1

    def test_a_zero_floor_adds_no_wait(self) -> None:
        device = _TimedDevice()
        worker = _timed(device, min_hold_s=0.0)
        worker.start()
        start = time.perf_counter()
        press = worker.submit(_report(1))
        worker.submit(NEUTRAL, after=press)
        assert device.wait_for(2)
        assert device.log[1][0] - start < 0.5
        worker.stop()

    def test_sequence_numbers_rise_by_one_per_submit(self) -> None:
        worker = _timed(_TimedDevice())
        assert [worker.submit(_report(n)) for n in range(1, 5)] == [1, 2, 3, 4]
        worker.stop()
        assert worker.submit(_report(9)) == 5

    def test_it_says_it_numbers_reports(self) -> None:
        assert PadWorker.numbers_reports is True
