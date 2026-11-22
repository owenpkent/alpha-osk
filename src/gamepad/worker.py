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
from typing import Callable, Deque, Dict, Optional, Tuple

from .device import PadDevice
from .state import NEUTRAL, PadReport

MAX_QUEUED_REPORTS = 256
#: How many applied sequence numbers are remembered for ``after`` lookups.
MAX_REMEMBERED_APPLIES = 1024
STOP_TIMEOUT_S = 2.0

_logger = logging.getLogger("PadWorker")


class PadWorker:
    #: The bridge reads this to tell a worker that numbers reports from a fake.
    numbers_reports = True

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
        self._queue: Deque[Tuple[int, PadReport, Optional[int]]] = collections.deque()
        self._stopping = False
        self._thread: Optional[threading.Thread] = None
        self.dropped_reports = 0
        #: Shortest time a press stays down at the device, measured from when
        #: its report was applied (not submitted), so a slow earlier write
        #: cannot squash a tap. Only a report submitted with ``after`` waits;
        #: whatever else a report contains, it is never delayed. Set before
        #: ``start()``.
        self.min_hold_s = 0.0
        self._clock = clock
        self._device: Optional[PadDevice] = None
        self._seq = 0
        #: Sequence number -> when that report was applied, oldest evicted first.
        self._applied_at: Dict[int, float] = {}

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

    def submit(self, report: PadReport, *, after: Optional[int] = None) -> int:
        """Queue ``report`` and return its sequence number.

        ``after`` is the sequence number of the report whose press this one
        ends; the worker holds this report back until that press has been at
        the device for ``min_hold_s``. The caller says so because the worker
        cannot tell a release from a stick moving on: guessing from report
        contents would delay analog input.
        """
        with self._cond:
            self._seq += 1
            seq = self._seq
            if self._stopping:
                return seq
            if len(self._queue) >= MAX_QUEUED_REPORTS:
                self._queue.popleft()
                self.dropped_reports += 1
            self._queue.append((seq, report, after))
            self._cond.notify()
            return seq

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

    def _hold_wait_s(self, after: Optional[int]) -> float:
        """How long until the press ``after`` names has been applied for ``min_hold_s``."""
        if after is None or self.min_hold_s <= 0:
            return 0.0
        applied_at = self._applied_at.get(after)
        if applied_at is None:
            return 0.0
        return applied_at + self.min_hold_s - self._clock()

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
                        wait_s = self._hold_wait_s(self._queue[0][2])
                        if wait_s <= 0:
                            break
                        # Queued reports stay queued, in order; stop() wakes this.
                        self._cond.wait(wait_s)
                    if self._stopping:
                        break
                    seq, report, _after = self._queue.popleft()
                try:
                    device.apply(report)
                except Exception as exc:
                    self._status("error", type(exc).__name__)
                    return
                self._applied_at[seq] = self._clock()
                if len(self._applied_at) > MAX_REMEMBERED_APPLIES:
                    del self._applied_at[next(iter(self._applied_at))]
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
