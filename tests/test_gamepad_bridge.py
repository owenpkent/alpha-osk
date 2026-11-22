"""GamepadBridge over a fake worker and a fake clock. No thread, no bus."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable, List, Optional

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication

from src.gamepad.state import BUTTON_MASKS, NEUTRAL, PadReport
from src.gamepad_bridge import GamepadBridge
from tests.qt_settings_scope import TEST_APP, TEST_ORG


class _FakeWorker:
    """Records submits; ``start`` answers with ``reply`` the way the real thread would."""

    instances: List["_FakeWorker"] = []

    def __init__(self, open_device: Callable[[], Any], on_status: Callable[[str, str], None]):
        self.on_status = on_status
        self.submitted: List[PadReport] = []
        self.stops = 0
        self.started = False
        self.reply: Optional[str] = "on"
        _FakeWorker.instances.append(self)

    def start(self) -> None:
        self.started = True
        if self.reply is not None:
            self.on_status(self.reply, "" if self.reply == "on" else "OSError")

    def submit(self, report: PadReport) -> None:
        self.submitted.append(report)

    def stop(self) -> None:
        self.stops += 1


class _StickyWorker(_FakeWorker):
    """A worker that is still running after ``stop`` until ``finish`` is called."""

    def __init__(self, open_device: Callable[[], Any], on_status: Callable[[str, str], None]):
        super().__init__(open_device, on_status)
        self.running = False

    def start(self) -> None:
        super().start()
        self.running = True

    def finish(self) -> None:
        self.running = False


class _CleanWorker(_StickyWorker):
    def stop(self) -> None:
        super().stop()
        self.running = False


class _HoldWorker(_FakeWorker):
    def __init__(self, open_device: Callable[[], Any], on_status: Callable[[str, str], None]):
        super().__init__(open_device, on_status)
        self.min_hold_s = 0.0


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _pump() -> None:
    QCoreApplication.sendPostedEvents(None, 0)
    QCoreApplication.processEvents()


def _wait_ms(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


@pytest.fixture(scope="module")
def qapp() -> Any:
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG
    return app


@pytest.fixture(autouse=True)
def _fresh_workers() -> None:
    _FakeWorker.instances = []


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


def _make(
    qapp: Any, clock: _Clock, *, bus: Any = True, factory: Any = _FakeWorker
) -> GamepadBridge:
    probe = bus if callable(bus) else (lambda: bus)
    return GamepadBridge(bus_available=probe, worker_factory=factory, clock=clock)


@pytest.fixture
def pad(qapp: Any, clock: _Clock) -> GamepadBridge:
    bridge = _make(qapp, clock)
    assert bridge.setActive(True)
    _pump()
    assert bridge.status == "on"
    return bridge


def _worker() -> _FakeWorker:
    return _FakeWorker.instances[-1]


class TestConstruction:
    def test_probes_nothing_and_starts_nothing(self, qapp: Any, clock: _Clock) -> None:
        probes: List[int] = []
        bridge = _make(qapp, clock, bus=lambda: probes.append(1) or True)
        assert probes == []
        assert _FakeWorker.instances == []
        assert bridge.status == "off"
        assert bridge.available is False
        assert list(bridge.latched) == []


class TestTurningOn:
    def test_no_bus_is_unavailable_and_starts_no_worker(self, qapp: Any, clock: _Clock) -> None:
        bridge = _make(qapp, clock, bus=False)
        assert bridge.setActive(True) is False
        assert bridge.status == "unavailable"
        assert bridge.available is False
        assert _FakeWorker.instances == []

    def test_with_a_bus_it_connects_then_is_on_and_submits_neutral(
        self, qapp: Any, clock: _Clock
    ) -> None:
        bridge = _make(qapp, clock)
        seen: List[str] = []
        bridge.statusChanged.connect(lambda: seen.append(bridge.status))
        assert bridge.setActive(True) is True
        assert bridge.status == "connecting"
        _pump()
        assert seen == ["connecting", "on"]
        assert bridge.available is True
        assert _worker().submitted == [NEUTRAL]

    def test_turning_on_twice_makes_one_worker(self, pad: GamepadBridge) -> None:
        assert pad.setActive(True) is True
        assert len(_FakeWorker.instances) == 1

    def test_a_probe_that_raises_is_unavailable_not_an_error(
        self, qapp: Any, clock: _Clock
    ) -> None:
        def boom() -> bool:
            raise RuntimeError("x")

        bridge = _make(qapp, clock, bus=boom)
        assert bridge.setActive(True) is False
        assert bridge.status == "unavailable"

    def test_a_worker_factory_that_raises_ends_in_error_and_does_not_raise(
        self, qapp: Any, clock: _Clock
    ) -> None:
        def bad(*a: Any) -> Any:
            raise RuntimeError("x")

        bridge = _make(qapp, clock, factory=bad)
        failures: List[str] = []
        bridge.failed.connect(failures.append)
        assert bridge.setActive(True) is False
        assert bridge.status == "error"
        assert len(failures) == 1


class TestRefusals:
    def test_everything_is_refused_while_off(self, qapp: Any, clock: _Clock) -> None:
        bridge = _make(qapp, clock)
        assert bridge.press("a") is False
        assert bridge.tap("a") is False
        assert bridge.toggleLatch("a") is False
        assert bridge.isLatched("a") is False

    def test_everything_is_refused_while_still_connecting(self, qapp: Any, clock: _Clock) -> None:
        bridge = _make(qapp, clock)
        bridge.setActive(True)
        assert bridge.status == "connecting"
        assert bridge.press("a") is False
        assert bridge.toggleLatch("a") is False

    @pytest.mark.parametrize("bad", ["", "A", "__class__", "ls_up", " a"])
    def test_unknown_ids_are_refused_while_on(self, pad: GamepadBridge, bad: str) -> None:
        sent = len(_worker().submitted)
        assert pad.press(bad) is False
        assert pad.tap(bad) is False
        assert pad.toggleLatch(bad) is False
        pad.release(bad)
        assert len(_worker().submitted) == sent

    def test_a_real_id_is_accepted_while_on(self, pad: GamepadBridge) -> None:
        assert pad.press("a") is True


class TestPressAndRelease:
    def test_a_press_submits_the_pressed_report(self, pad: GamepadBridge) -> None:
        pad.press("a")
        assert _worker().submitted[-1] == PadReport(buttons=BUTTON_MASKS["a"])

    def test_a_release_after_the_minimum_hold_submits_neutral(
        self, pad: GamepadBridge, clock: _Clock
    ) -> None:
        pad.press("a")
        clock.now += 0.2
        pad.release("a")
        assert _worker().submitted[-1] == NEUTRAL

    def test_a_release_before_the_minimum_hold_is_deferred(
        self, pad: GamepadBridge, clock: _Clock
    ) -> None:
        pad.press("a")
        clock.now += 0.01
        pad.release("a")
        # Still down against the real interval: the timer has not had it yet.
        _pump()
        assert _worker().submitted[-1] == PadReport(buttons=BUTTON_MASKS["a"])
        pad._release_timers["a"].setInterval(1)
        pad._release_timers["a"].start()
        _wait_ms(50)
        assert _worker().submitted[-1] == NEUTRAL

    def test_a_repress_cancels_a_pending_deferred_release(
        self, pad: GamepadBridge, clock: _Clock
    ) -> None:
        pad.press("a")
        clock.now += 0.01
        pad.release("a")
        assert pad._release_timers["a"].isActive()
        pad.press("a")
        assert not pad._release_timers["a"].isActive()
        _wait_ms(150)
        assert _worker().submitted[-1] == PadReport(buttons=BUTTON_MASKS["a"])

    def test_tap_presses_then_releases_under_the_minimum_hold(self, pad: GamepadBridge) -> None:
        assert pad.tap("b") is True
        assert PadReport(buttons=BUTTON_MASKS["b"]) in _worker().submitted
        assert _worker().submitted[-1] == PadReport(buttons=BUTTON_MASKS["b"])
        pad._release_timers["b"].setInterval(1)
        pad._release_timers["b"].start()
        _wait_ms(50)
        assert _worker().submitted[-1] == NEUTRAL

    def test_releasing_something_never_pressed_submits_nothing(self, pad: GamepadBridge) -> None:
        sent = len(_worker().submitted)
        pad.release("a")
        assert len(_worker().submitted) == sent


class TestLatching:
    def test_a_latch_survives_other_presses_and_is_listed(
        self, pad: GamepadBridge, clock: _Clock
    ) -> None:
        assert pad.toggleLatch("lt") is True
        assert pad.isLatched("lt") is True
        assert list(pad.latched) == ["lt"]
        pad.press("a")
        clock.now += 0.2
        pad.release("a")
        assert pad.isLatched("lt") is True
        assert _worker().submitted[-1].left_trigger == 1.0

    def test_latched_changed_fires_only_on_real_changes(
        self, pad: GamepadBridge, clock: _Clock
    ) -> None:
        count: List[int] = []
        pad.latchedChanged.connect(lambda: count.append(1))
        pad.press("a")
        clock.now += 0.2
        pad.release("a")
        assert count == []
        pad.toggleLatch("lt")
        assert len(count) == 1
        pad.toggleLatch("lt")
        assert len(count) == 2

    def test_pressing_a_latched_control_unlatches_it(self, pad: GamepadBridge) -> None:
        pad.toggleLatch("lt")
        assert pad.press("lt") is True
        assert pad.isLatched("lt") is False
        assert list(pad.latched) == []

    def test_unlatching_returns_false(self, pad: GamepadBridge) -> None:
        pad.toggleLatch("x")
        assert pad.toggleLatch("x") is False


class TestTurningOff:
    def test_releases_everything_stops_the_worker_and_ends_off(
        self, pad: GamepadBridge, clock: _Clock
    ) -> None:
        pad.toggleLatch("lt")
        pad.press("a")
        clock.now += 0.01
        pad.release("a")
        worker = _worker()
        assert pad.setActive(False) is False
        assert worker.stops == 1
        assert pad.status == "off"
        assert list(pad.latched) == []
        assert all(not t.isActive() for t in pad._release_timers.values())
        assert pad.press("a") is False

    def test_a_stale_on_from_the_old_worker_does_not_flip_status_back(
        self, qapp: Any, clock: _Clock
    ) -> None:
        bridge = _make(qapp, clock)
        bridge.setActive(True)  # the "on" is queued, not yet delivered
        bridge.setActive(False)
        _pump()
        assert bridge.status == "off"

    def test_a_stale_status_from_a_previous_worker_is_ignored(self, pad: GamepadBridge) -> None:
        old = _worker()
        pad.setActive(False)
        pad.setActive(True)
        _pump()
        assert pad.status == "on"
        old.on_status("error", "OSError")
        _pump()
        assert pad.status == "on"

    def test_shutdown_twice_is_safe(self, pad: GamepadBridge) -> None:
        pad.shutdown()
        pad.shutdown()
        assert pad.status == "off"
        assert _worker().stops == 1


class TestAWorkerThatOutlivesItsStop:
    def test_turning_on_again_is_refused_until_it_has_really_stopped(
        self, qapp: Any, clock: _Clock
    ) -> None:
        bridge = _make(qapp, clock, factory=_StickyWorker)
        failures: List[str] = []
        bridge.failed.connect(failures.append)
        assert bridge.setActive(True)
        _pump()
        first = _worker()
        assert bridge.setActive(False) is False
        assert first.running
        assert bridge.setActive(True) is False
        assert failures == ["The controller is still disconnecting. Try again in a moment."]
        assert len(_FakeWorker.instances) == 1
        assert bridge.status == "off"
        # A late status from the retiring worker is ignored.
        first.on_status("on", "")
        _pump()
        assert bridge.status == "off"
        first.finish()
        assert bridge.setActive(True) is True
        _pump()
        assert len(_FakeWorker.instances) == 2
        assert bridge.status == "on"

    def test_a_worker_that_stopped_cleanly_does_not_block_the_next_on(
        self, qapp: Any, clock: _Clock
    ) -> None:
        bridge = _make(qapp, clock, factory=_CleanWorker)
        assert bridge.setActive(True)
        _pump()
        bridge.setActive(False)
        assert bridge.setActive(True) is True
        assert len(_FakeWorker.instances) == 2

    def test_workers_without_a_running_attribute_never_block(
        self, qapp: Any, clock: _Clock
    ) -> None:
        bridge = _make(qapp, clock)
        assert bridge.setActive(True)
        bridge.setActive(False)
        assert bridge.setActive(True) is True

    def test_shutdown_with_a_stuck_worker_does_not_raise(self, qapp: Any, clock: _Clock) -> None:
        bridge = _make(qapp, clock, factory=_StickyWorker)
        assert bridge.setActive(True)
        bridge.shutdown()
        bridge.shutdown()


class TestTheWorkersMinimumHold:
    def test_it_is_set_from_min_press_ms_when_the_worker_has_one(
        self, qapp: Any, clock: _Clock
    ) -> None:
        bridge = _make(qapp, clock, factory=_HoldWorker)
        assert bridge.setActive(True)
        assert _worker().min_hold_s == GamepadBridge.MIN_PRESS_MS / 1000.0  # type: ignore[attr-defined]

    def test_a_worker_without_one_is_left_alone(self, qapp: Any, clock: _Clock) -> None:
        bridge = _make(qapp, clock)
        assert bridge.setActive(True)
        assert not hasattr(_worker(), "min_hold_s")


class TestWorkerErrors:
    def test_an_error_ends_in_error_emits_failed_and_releases(self, pad: GamepadBridge) -> None:
        pad.toggleLatch("lt")
        failures: List[str] = []
        pad.failed.connect(failures.append)
        worker = _worker()
        worker.on_status("error", "OSError")
        _pump()
        assert pad.status == "error"
        assert failures and "OSError" not in failures[0]
        assert list(pad.latched) == []
        assert worker.stops == 1

    def test_an_error_while_connecting_is_also_reported(self, qapp: Any, clock: _Clock) -> None:
        class Failing(_FakeWorker):
            def __init__(self, *a: Any) -> None:
                super().__init__(*a)
                self.reply = "error"

        bridge = _make(qapp, clock, factory=Failing)
        failures: List[str] = []
        bridge.failed.connect(failures.append)
        bridge.setActive(True)
        _pump()
        assert bridge.status == "error"
        assert len(failures) == 1

    def test_a_clean_run_emits_no_failure(self, qapp: Any, clock: _Clock) -> None:
        bridge = _make(qapp, clock)
        failures: List[str] = []
        bridge.failed.connect(failures.append)
        bridge.setActive(True)
        _pump()
        assert failures == []


class TestControlIds:
    def test_sorted_and_includes_the_buttons(self, pad: GamepadBridge) -> None:
        ids = list(pad.getControlIds())
        assert ids == sorted(ids)
        assert "a" in ids and "ls_n" in ids


class _TimedPad:
    """A device that stamps every apply and can stall the next one."""

    def __init__(self) -> None:
        self.log: List[tuple[float, PadReport]] = []
        self.closes = 0
        self.hold: Optional[threading.Event] = None
        self.entered = threading.Event()

    def stall_next(self) -> threading.Event:
        self.entered.clear()
        self.hold = threading.Event()
        return self.hold

    def apply(self, report: PadReport) -> None:
        hold = self.hold
        if hold is not None:
            self.hold = None
            self.entered.set()
            assert hold.wait(CEILING_S)
        self.log.append((time.perf_counter(), report))

    def close(self) -> None:
        self.closes += 1


CEILING_S = 5.0
TOLERANCE_S = 0.015


def _pump_until(condition: Callable[[], bool], timeout_s: float = CEILING_S) -> bool:
    deadline = time.perf_counter() + timeout_s
    while not condition() and time.perf_counter() < deadline:
        _pump()
        time.sleep(0.002)
    return condition()


class TestTheRealWorkerHoldsARelease:
    """The real thread and worker under the real bridge, over a fake device."""

    @pytest.fixture
    def rig(self, qapp: Any) -> Any:
        device = _TimedPad()
        bridge = GamepadBridge(bus_available=lambda: True, open_device=lambda: device)
        assert bridge.setActive(True)
        assert _pump_until(lambda: bridge.status == "on")
        assert _pump_until(lambda: len(device.log) >= 1)
        yield bridge, device
        bridge.shutdown()

    @staticmethod
    def _hold_s() -> float:
        return GamepadBridge.MIN_PRESS_MS / 1000.0

    def test_a_direction_tapped_over_a_latched_one_is_held_at_the_device(self, rig: Any) -> None:
        bridge, device = rig
        assert bridge.toggleLatch("ls_e")
        assert _pump_until(lambda: len(device.log) >= 2)
        stall = device.stall_next()
        assert bridge.tap("ls_n")
        assert device.entered.wait(CEILING_S)
        _wait_ms(int(self._hold_s() * 1000) + 100)  # the bridge's release timer has fired
        stall.set()
        assert _pump_until(lambda: len(device.log) >= 4)
        (_, _), (_, east), (t_north, north), (t_back, back) = device.log[:4]
        assert east.left_x > 0 and east.left_y == 0
        assert north.left_y != 0
        assert back == east
        assert t_back - t_north >= self._hold_s() - TOLERANCE_S

    def test_a_delayed_button_tap_is_held_the_floor(self, rig: Any) -> None:
        bridge, device = rig
        stall = device.stall_next()
        assert bridge.tap("a")
        assert device.entered.wait(CEILING_S)
        _wait_ms(int(self._hold_s() * 1000) + 100)
        stall.set()
        assert _pump_until(lambda: len(device.log) >= 3)
        (_, _), (t_down, down), (t_up, up) = device.log[:3]
        assert down.buttons == BUTTON_MASKS["a"] and up == NEUTRAL
        assert t_up - t_down >= self._hold_s() - TOLERANCE_S

    def test_a_press_behind_a_waiting_release_goes_in_order_and_is_not_held(self, rig: Any) -> None:
        bridge, device = rig
        stall = device.stall_next()
        assert bridge.tap("a")
        assert device.entered.wait(CEILING_S)
        _wait_ms(int(self._hold_s() * 1000) + 100)
        stall.set()
        assert _pump_until(lambda: len(device.log) >= 2)
        assert bridge.press("b")  # queued behind the release that is still waiting
        assert _pump_until(lambda: len(device.log) >= 4)
        reports = [r for _, r in device.log]
        assert reports[1].buttons == BUTTON_MASKS["a"]
        assert reports[2] == NEUTRAL
        assert reports[3].buttons == BUTTON_MASKS["b"]
        assert device.log[3][0] - device.log[2][0] < self._hold_s() - 0.02
        bridge.release("b")

    def test_turning_off_during_a_waiting_hold_is_prompt_and_ends_on_neutral(
        self, rig: Any
    ) -> None:
        bridge, device = rig
        assert bridge.toggleLatch("ls_e")
        assert _pump_until(lambda: len(device.log) >= 2)
        bridge._worker.min_hold_s = 30.0  # long enough that no load can end the wait early
        stall = device.stall_next()
        assert bridge.tap("ls_n")
        assert device.entered.wait(CEILING_S)
        _wait_ms(int(self._hold_s() * 1000) + 100)
        stall.set()
        assert _pump_until(lambda: len(device.log) >= 3)  # north applied, release waiting
        begin = time.perf_counter()
        assert bridge.setActive(False) is False
        assert time.perf_counter() - begin < 2.0
        assert device.log[-1][1] == NEUTRAL
        assert len(device.log) == 4  # the held-back east report was never applied
        assert device.closes == 1


def test_keyboard_bridge_never_mentions_the_gamepad() -> None:
    """The bridge split (STRUCTURAL_REVIEW.md 3.1): GamepadBridge is its own surface.

    ``KeyboardBridge`` is already seventeen concerns; the pad must stay off it.
    """
    source = (Path(__file__).resolve().parent.parent / "src" / "keyboard_bridge.py").read_text(
        encoding="utf-8"
    )
    assert "gamepad" not in source.lower()
