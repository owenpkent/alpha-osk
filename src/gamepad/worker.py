"""One thread that owns the pad, so plugging it in and every report stay off Qt.

``on_status`` is always called from the worker thread, never the caller's.
It receives ``("on", "")``, ``("error", <exception type name>)`` or
``("off", "")``; exception text is never passed on.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Callable, Deque, Dict, Optional, Set

from .device import PadDevice
from .state import NEUTRAL, PadReport

MAX_QUEUED_REPORTS = 256
STOP_TIMEOUT_S = 2.0

_logger = logging.getLogger("PadWorker")


class PadWorker:
    def __init__(
        self,
        open_device: Callable[[], PadDevice],
        on_status: Callable[[str, str], None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._open_device = open_device
        self._on_status = on_status
        self._cond = threading.Condition()
        self._queue: Deque[PadReport] = collections.deque()
        self._stopping = False
        self._thread: Optional[threading.Thread] = None
        self.dropped_reports = 0
        #: Shortest time a control stays down at the device, measured from
        #: when its press was applied (not submitted), so a slow earlier write
        #: cannot squash a tap. Set before ``start()``.
        self.min_hold_s = 0.0
        self._clock = clock
        self._device: Optional[PadDevice] = None
        self._down_at: Dict[str, float] = {}

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        with self._cond:
            # A stopped worker stays stopped: starting one after its stop()
            # would plug a pad only to unplug it, and a pad appearing is
            # something games react to.
            if self._thread is not None or self._stopping:
                return
            self._thread = threading.Thread(target=self._run, name="pad-worker", daemon=True)
            self._thread.start()

    def submit(self, report: PadReport) -> None:
        with self._cond:
            if self._stopping:
                return
            if len(self._queue) >= MAX_QUEUED_REPORTS:
                self._queue.popleft()
                self.dropped_reports += 1
            self._queue.append(report)
            self._cond.notify()

    def stop(self, timeout_s: float = STOP_TIMEOUT_S) -> None:
        try:
            with self._cond:
                self._stopping = True
                self._queue.clear()
                self._cond.notify_all()
                thread = self._thread
                device = self._device
            # Before the join: an apply stuck in a re-plug must not replay
            # the report it holds once it wakes.
            cancel = getattr(device, "cancel", None)
            if cancel is not None:
                try:
                    cancel()
                except Exception:
                    _logger.debug("pad cancel failed", exc_info=True)
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout_s)
        except Exception:
            _logger.debug("pad worker stop failed", exc_info=True)

    def _status(self, status: str, detail: str) -> None:
        try:
            self._on_status(status, detail)
        except Exception:
            _logger.debug("pad status callback failed", exc_info=True)

    @staticmethod
    def _controls_down(report: PadReport) -> Set[str]:
        down = {f"b{bit}" for bit in range(32) if report.buttons >> bit & 1}
        if report.left_trigger > 0:
            down.add("lt")
        if report.right_trigger > 0:
            down.add("rt")
        if (report.left_x, report.left_y) != (0.0, 0.0):
            down.add("ls")
        if (report.right_x, report.right_y) != (0.0, 0.0):
            down.add("rs")
        return down

    def _hold_wait_s(self, report: PadReport) -> float:
        """How long until every control this report lets go of has been down long enough."""
        if self.min_hold_s <= 0 or not self._down_at:
            return 0.0
        now = self._clock()
        releasing = set(self._down_at) - self._controls_down(report)
        return max((self._down_at[c] + self.min_hold_s - now for c in releasing), default=0.0)

    def _note_applied(self, report: PadReport) -> None:
        down = self._controls_down(report)
        now = self._clock()
        for control in down:
            self._down_at.setdefault(control, now)
        for control in set(self._down_at) - down:
            del self._down_at[control]

    def _run(self) -> None:
        try:
            device = self._open_device()
        except Exception as exc:
            self._status("error", type(exc).__name__)
            return
        with self._cond:
            self._device = device
        self._status("on", "")
        off = False
        try:
            while True:
                with self._cond:
                    while not self._queue and not self._stopping:
                        self._cond.wait()
                    while not self._stopping:
                        wait_s = self._hold_wait_s(self._queue[0])
                        if wait_s <= 0:
                            break
                        # Queued reports stay queued, in order; stop() wakes this.
                        self._cond.wait(wait_s)
                    if self._stopping:
                        break
                    report = self._queue.popleft()
                try:
                    device.apply(report)
                except Exception as exc:
                    self._status("error", type(exc).__name__)
                    return
                self._note_applied(report)
            try:
                device.apply(NEUTRAL)
            except Exception:
                _logger.debug("neutral report on stop failed", exc_info=True)
            off = True
        finally:
            try:
                device.close()
            except Exception:
                _logger.debug("pad close failed", exc_info=True)
            # After the close: "off" must mean the pad is really gone.
            if off:
                self._status("off", "")
