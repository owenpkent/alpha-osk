"""Build a predictor off the UI thread and hand it over once complete."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from .hybrid_predictor import HybridPredictor

_logger = logging.getLogger(__name__)


class _LoadJob:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.cancelled = False
        self.done = False
        self.result: HybridPredictor | None = None

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True
            if self.result is not None:
                self.result.deleteLater()
                self.result = None


def _build(factory: Callable[[], HybridPredictor], target_thread: QThread, job: _LoadJob) -> None:
    try:
        predictor = factory()
    except Exception:
        _logger.exception("Prediction engine could not be loaded")
        with job.lock:
            job.done = True
        return
    with job.lock:
        if job.cancelled:
            # Still owned by this worker, so destruction happens here too.
            job.done = True
            return
        predictor.moveToThread(target_thread)
        job.result = predictor
        job.done = True


class PredictionLoader(QObject):
    """Publish on the UI thread; cancellation never waits for the build.

    The worker only owns a parentless predictor and a plain Python job. It
    never calls the bridge or emits through a QObject that closing the
    keyboard might already have destroyed. Publication and cancellation
    share a lock so an abandoned result cannot outlive its target thread.
    """

    loaded = Signal(object)
    failed = Signal()

    def __init__(self, factory: Callable[[], HybridPredictor], parent: QObject) -> None:
        super().__init__(parent)
        self._factory = factory
        self._job = _LoadJob()
        self._started = False
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._poll)
        self.destroyed.connect(self._job.cancel)

    def start(self) -> None:
        if self._started or self._job.cancelled:
            return
        self._started = True
        threading.Thread(
            target=_build,
            args=(self._factory, self.thread(), self._job),
            name="alpha-osk-prediction-load",
            daemon=True,
        ).start()
        self._timer.start()

    def cancel(self) -> None:
        self._timer.stop()
        self._job.cancel()

    def _poll(self) -> None:
        with self._job.lock:
            if self._job.cancelled or not self._job.done:
                return
            predictor = self._job.result
            self._job.result = None
        self._timer.stop()
        if predictor is None:
            self.failed.emit()
        else:
            self.loaded.emit(predictor)
