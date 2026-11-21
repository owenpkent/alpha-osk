"""The virtual pad as the worker sees it: apply a report, close.

``padbus_client`` is looked up through the module on every call so a test
can swap ``X360Pad`` out; nothing here opens the bus at import time.
"""

from __future__ import annotations

import sys
from typing import Any, Protocol

from ..platform import padbus_client
from .state import PadReport


class PadDevice(Protocol):
    def apply(self, report: PadReport) -> None: ...

    def close(self) -> None: ...


def pad_bus_available() -> bool:
    """Whether a pad bus exists. Enumeration only; never raises."""
    if sys.platform != "win32":
        return False
    try:
        return bool(padbus_client.bus_present())
    except Exception:
        return False


class _X360Device:
    def __init__(self, pad: Any) -> None:
        self._pad: Any = pad

    def apply(self, report: PadReport) -> None:
        pad = self._pad
        if pad is None:
            return
        # reset() first so a button absent from this report is not left down.
        pad.reset()
        pad.press_button(report.buttons)
        pad.left_trigger_float(report.left_trigger)
        pad.right_trigger_float(report.right_trigger)
        pad.left_joystick_float(report.left_x, report.left_y)
        pad.right_joystick_float(report.right_x, report.right_y)
        pad.update()

    def close(self) -> None:
        pad, self._pad = self._pad, None
        if pad is not None:
            pad.close()


def open_pad() -> PadDevice:
    """Plug in a new virtual Xbox 360 pad (blocks about 110 ms)."""
    return _X360Device(padbus_client.X360Pad())
