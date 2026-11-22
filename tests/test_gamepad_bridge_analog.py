"""The bridge's analog slots over a fake worker. No thread, no bus."""

from __future__ import annotations

from typing import Any, Callable, List

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication

from src.gamepad.state import BUTTON_MASKS, NEUTRAL, PadReport
from src.gamepad_bridge import GamepadBridge
from tests.qt_settings_scope import TEST_APP, TEST_ORG


class _FakeWorker:
    instances: List["_FakeWorker"] = []

    def __init__(self, open_device: Callable[[], Any], on_status: Callable[[str, str], None]):
        self.on_status = on_status
        self.submitted: List[PadReport] = []
        _FakeWorker.instances.append(self)

    def start(self) -> None:
        self.on_status("on", "")

    def submit(self, report: PadReport) -> None:
        self.submitted.append(report)

    def stop(self) -> None:
        pass


def _pump() -> None:
    QCoreApplication.sendPostedEvents(None, 0)
    QCoreApplication.processEvents()


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


def _make(qapp: Any) -> GamepadBridge:
    return GamepadBridge(bus_available=lambda: True, worker_factory=_FakeWorker)


@pytest.fixture
def pad(qapp: Any) -> GamepadBridge:
    bridge = _make(qapp)
    assert bridge.setActive(True)
    _pump()
    assert bridge.status == "on"
    return bridge


def _last() -> PadReport:
    return _FakeWorker.instances[-1].submitted[-1]


class TestRefusals:
    def test_everything_is_refused_while_off(self, qapp: Any) -> None:
        bridge = _make(qapp)
        assert bridge.setStick("ls", 0.5, 0.5) is False
        assert bridge.setTrigger("rt", 1.0) is False
        assert _FakeWorker.instances == []

    @pytest.mark.parametrize("stick", ["", "lt", "LS", "ls_n", "l3"])
    def test_an_unknown_stick_is_refused(self, pad: GamepadBridge, stick: str) -> None:
        before = len(_FakeWorker.instances[-1].submitted)
        assert pad.setStick(stick, 0.5, 0.5) is False
        assert len(_FakeWorker.instances[-1].submitted) == before

    @pytest.mark.parametrize("trigger", ["", "ls", "LT", "rb"])
    def test_an_unknown_trigger_is_refused(self, pad: GamepadBridge, trigger: str) -> None:
        assert pad.setTrigger(trigger, 1.0) is False
        assert _last() == NEUTRAL

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_value_is_refused(self, pad: GamepadBridge, bad: float) -> None:
        assert pad.setStick("ls", bad, 0.0) is False
        assert pad.setStick("ls", 0.0, bad) is False
        assert pad.setTrigger("lt", bad) is False
        assert _last() == NEUTRAL

    def test_a_good_value_is_taken(self, pad: GamepadBridge) -> None:
        assert pad.setStick("rs", 0.0, 0.0) is True
        assert pad.setTrigger("lt", 0.0) is True


class TestPositions:
    def test_a_stick_position_is_submitted(self, pad: GamepadBridge) -> None:
        assert pad.setStick("ls", 0.5, -0.25) is True
        assert (_last().left_x, _last().left_y) == (0.5, -0.25)
        assert (_last().right_x, _last().right_y) == (0.0, 0.0)
        assert pad.setStick("rs", -1.0, 0.0) is True
        assert (_last().right_x, _last().right_y) == (-1.0, 0.0)
        assert (_last().left_x, _last().left_y) == (0.5, -0.25)

    def test_a_trigger_value_is_submitted(self, pad: GamepadBridge) -> None:
        assert pad.setTrigger("rt", 0.75) is True
        assert _last().right_trigger == 0.75
        assert _last().left_trigger == 0.0
        assert pad.setTrigger("lt", 1.0) is True
        assert _last().left_trigger == 1.0

    def test_center_stick_submits_neutral(self, pad: GamepadBridge) -> None:
        pad.setStick("ls", 0.5, 0.5)
        pad.centerStick("ls")
        assert _last() == NEUTRAL

    def test_center_stick_leaves_the_other_stick_alone(self, pad: GamepadBridge) -> None:
        pad.setStick("ls", 0.5, 0.5)
        pad.setStick("rs", 0.2, 0.0)
        pad.centerStick("ls")
        assert (_last().left_x, _last().left_y) == (0.0, 0.0)
        assert (_last().right_x, _last().right_y) == (0.2, 0.0)

    def test_clear_trigger_submits_neutral(self, pad: GamepadBridge) -> None:
        pad.setTrigger("rt", 1.0)
        pad.clearTrigger("rt")
        assert _last() == NEUTRAL

    def test_a_position_survives_a_button_tap(self, pad: GamepadBridge) -> None:
        pad.setStick("ls", 0.5, 0.5)
        pad.setTrigger("rt", 0.5)
        assert pad.press("a") is True
        report = _last()
        assert report.buttons & BUTTON_MASKS["a"]
        assert (report.left_x, report.left_y) == (0.5, 0.5)
        assert report.right_trigger == 0.5
        pad.release("a")
        pad.tap("b")
        assert (_last().left_x, _last().left_y) == (0.5, 0.5)

    def test_turning_off_clears_the_positions(self, pad: GamepadBridge, qapp: Any) -> None:
        pad.setStick("ls", 0.5, 0.5)
        pad.setTrigger("rt", 1.0)
        worker = _FakeWorker.instances[-1]
        pad.setActive(False)
        _pump()
        assert pad.status == "off"
        assert pad.setActive(True) is True
        _pump()
        assert _FakeWorker.instances[-1] is not worker
        assert _FakeWorker.instances[-1].submitted[-1] == NEUTRAL
        # And a fresh position starts from nothing.
        pad.setStick("rs", 0.1, 0.0)
        assert (_last().left_x, _last().left_y) == (0.0, 0.0)
        assert _last().right_trigger == 0.0

    def test_release_all_clears_them_too(self, pad: GamepadBridge) -> None:
        pad.setStick("ls", 0.5, 0.5)
        pad.releaseAll()
        assert _last() == NEUTRAL
