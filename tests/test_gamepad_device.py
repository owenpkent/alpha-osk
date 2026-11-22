"""The pad adapter: one report in, one submitted update out. No real bus."""

from __future__ import annotations

from typing import Any, List, Tuple

import pytest

from src.gamepad import device as device_module
from src.gamepad.device import open_pad, pad_bus_available
from src.gamepad.state import NEUTRAL, PadReport
from src.platform import padbus_client
from src.platform.padbus_client import X360ReportState


class _FakePad(X360ReportState):
    instances: List["_FakePad"] = []

    def __init__(self) -> None:
        super().__init__()
        self.submitted: List[Tuple[int, int, int, int, int, int, int]] = []
        self.updates = 0
        self.closes = 0
        self.cancels = 0
        _FakePad.instances.append(self)

    def update(self) -> None:
        self.updates += 1
        r = self.report
        self.submitted.append(
            (
                r.wButtons,
                r.bLeftTrigger,
                r.bRightTrigger,
                r.sThumbLX,
                r.sThumbLY,
                r.sThumbRX,
                r.sThumbRY,
            )
        )

    def close(self) -> None:
        self.closes += 1

    def cancel(self) -> None:
        self.cancels += 1


@pytest.fixture
def pad(monkeypatch: pytest.MonkeyPatch) -> _FakePad:
    _FakePad.instances = []
    monkeypatch.setattr(padbus_client, "X360Pad", _FakePad)
    open_pad_device = open_pad()
    assert len(_FakePad.instances) == 1
    fake = _FakePad.instances[0]
    fake.device = open_pad_device  # type: ignore[attr-defined]
    return fake


class TestApplyingAReport:
    def test_sets_exactly_the_reports_fields_and_submits_once(self, pad: Any) -> None:
        pad.device.apply(
            PadReport(buttons=0x1001, left_trigger=1.0, right_x=1.0, right_y=-1.0, left_x=0.5)
        )
        assert pad.updates == 1
        buttons, lt, rt, lx, ly, rx, ry = pad.submitted[0]
        assert buttons == 0x1001
        assert (lt, rt) == (255, 0)
        assert (lx, ly) == (round(0.5 * 32767), 0)
        assert (rx, ry) == (32767, -32767)

    def test_a_second_report_does_not_leave_the_first_ones_buttons_down(self, pad: Any) -> None:
        pad.device.apply(PadReport(buttons=0x0010, left_trigger=1.0, left_y=1.0))
        pad.device.apply(PadReport(buttons=0x0020))
        assert pad.submitted[1] == (0x0020, 0, 0, 0, 0, 0, 0)

    def test_a_neutral_report_clears_everything(self, pad: Any) -> None:
        pad.device.apply(PadReport(buttons=0xFFFF, right_trigger=1.0, right_x=1.0))
        pad.device.apply(NEUTRAL)
        assert pad.submitted[-1] == (0, 0, 0, 0, 0, 0, 0)


class TestClosing:
    def test_close_closes_the_pad(self, pad: Any) -> None:
        pad.device.close()
        assert pad.closes == 1

    def test_closing_twice_is_safe_and_closes_the_pad_once(self, pad: Any) -> None:
        pad.device.close()
        pad.device.close()
        assert pad.closes == 1

    def test_applying_after_close_does_nothing(self, pad: Any) -> None:
        pad.device.close()
        pad.device.apply(PadReport(buttons=1))
        assert pad.updates == 0


class TestCancelling:
    def test_cancel_reaches_the_pad(self, pad: Any) -> None:
        pad.device.cancel()
        assert pad.cancels == 1

    def test_cancel_after_close_is_harmless(self, pad: Any) -> None:
        pad.device.close()
        pad.device.cancel()
        assert pad.cancels == 0
        assert pad.closes == 1


class TestBusAvailability:
    def test_false_under_the_suites_guard(self) -> None:
        assert pad_bus_available() is False

    def test_false_off_windows_even_with_a_bus(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(padbus_client, "bus_present", lambda: True)
        monkeypatch.setattr(device_module.sys, "platform", "linux")
        assert pad_bus_available() is False

    def test_true_on_windows_when_a_bus_is_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(padbus_client, "bus_present", lambda: True)
        monkeypatch.setattr(device_module.sys, "platform", "win32")
        assert pad_bus_available() is True

    def test_a_raising_probe_is_false_not_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom() -> bool:
            raise OSError("no")

        monkeypatch.setattr(padbus_client, "bus_present", boom)
        monkeypatch.setattr(device_module.sys, "platform", "win32")
        assert pad_bus_available() is False
