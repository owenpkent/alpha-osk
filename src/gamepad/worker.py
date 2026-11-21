"""One thread that owns the pad, so plugging it in and every report stay off Qt.

``on_status`` is always called from the worker thread, never the caller's.
It receives ``("on", "")``, ``("error", <exception type name>)`` or
``("off", "")``; exception text is never passed on.
"""

from __future__ import annotations

import collections
import logging
import threading
from typing import Callable, Deque, Optional

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
    ) -> None:
        self._open_device = open_device
        self._on_status = on_status
        self._cond = threading.Condition()
        self._queue: Deque[PadReport] = collections.deque()
        self._stopping = False
        self._thread: Optional[threading.Thread] = None
        self.dropped_reports = 0

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
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout_s)
        except Exception:
            _logger.debug("pad worker stop failed", exc_info=True)

    def _status(self, status: str, detail: str) -> None:
        try:
            self._on_status(status, detail)
        except Exception:
            _logger.debug("pad status callback failed", exc_info=True)

    def _run(self) -> None:
        try:
            device = self._open_device()
        except Exception as exc:
            self._status("error", type(exc).__name__)
            return
        self._status("on", "")
        try:
            while True:
                with self._cond:
                    while not self._queue and not self._stopping:
                        self._cond.wait()
                    if self._stopping:
                        break
                    report = self._queue.popleft()
                try:
                    device.apply(report)
                except Exception as exc:
                    self._status("error", type(exc).__name__)
                    return
            try:
                device.apply(NEUTRAL)
            except Exception:
                _logger.debug("neutral report on stop failed", exc_info=True)
            self._status("off", "")
        finally:
            try:
                device.close()
            except Exception:
                _logger.debug("pad close failed", exc_info=True)
