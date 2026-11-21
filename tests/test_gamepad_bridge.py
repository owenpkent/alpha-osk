"""GamepadBridge over a fake worker and a fake clock. No thread, no bus."""

from __future__ import annotations

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


def test_keyboard_bridge_never_mentions_the_gamepad() -> None:
    """The bridge split (STRUCTURAL_REVIEW.md 3.1): GamepadBridge is its own surface.

    ``KeyboardBridge`` is already seventeen concerns; the pad must stay off it.
    """
    source = (Path(__file__).resolve().parent.parent / "src" / "keyboard_bridge.py").read_text(
        encoding="utf-8"
    )
    assert "gamepad" not in source.lower()
