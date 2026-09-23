"""Build a predictor off the UI thread and hand it over once complete."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable

from PySide6.QtCore import QObject, Qt, QThread, Signal

from .hybrid_predictor import HybridPredictor, LoadAborted

_logger = logging.getLogger(__name__)

# The factory takes an "abort?" callable the build can poll between its
# phases (HybridPredictor's ``abort_check``), so a cancel stops the CPU work
# rather than only discarding its result.
PredictorFactory = Callable[[Callable[[], bool]], HybridPredictor]

# While the engine builds, Python's default 5 ms switch interval lets the
# worker hold the GIL long enough to stall the UI thread by tens of
# milliseconds at a time (measured up to 113 ms), which lands inside every
# keystroke-timing window the keyboard has: auto-repeat, the key-preview
# floor, the click-settle poll.  A shorter interval makes the worker give
# the GIL back promptly.  Process-wide, so it is restored when the build
# ends.
_BUILD_SWITCH_INTERVAL_S = 0.001


class _Notifier(QObject):
    """A UI-thread object the worker emits through when the build ends.

    It is owned by the job rather than by the loader, so it outlives a loader
    that closing the keyboard destroys mid-build: the worker always emits on
    a live sender, and Qt drops the queued delivery itself when the receiver
    is gone.  Emitting on the loader directly would race its destruction.
    """

    finished = Signal()


class _LoadJob:
    def __init__(self, notifier: _Notifier) -> None:
        self.lock = threading.Lock()
        self.cancelled = False
        self.done = False
        self.result: HybridPredictor | None = None
        self.thread: threading.Thread | None = None
        self.notifier: _Notifier | None = notifier

    def cancel(self) -> None:
        with self.lock:
            self.cancelled = True
            if self.result is not None:
                self.result.deleteLater()
                self.result = None

    def is_cancelled(self) -> bool:
        return self.cancelled


def _build(factory: PredictorFactory, target_thread: QThread, job: _LoadJob) -> None:
    previous_interval = sys.getswitchinterval()
    sys.setswitchinterval(_BUILD_SWITCH_INTERVAL_S)
    predictor: HybridPredictor | None = None
    try:
        try:
            predictor = factory(job.is_cancelled)
        except LoadAborted:
            _logger.info("Prediction engine load cancelled")
        except Exception:
            _logger.exception("Prediction engine could not be loaded")
        if predictor is not None:
            try:
                with job.lock:
                    if job.cancelled:
                        # Still owned by this worker; Python destruction is
                        # fine here because the object never left this
                        # thread.
                        predictor = None
                    else:
                        predictor.moveToThread(target_thread)
                        job.result = predictor
            except Exception:
                # A hand-over that fails (the target thread already gone,
                # say) is a failed load, not a hung one.
                _logger.exception("Prediction engine could not be handed to the UI thread")
                with job.lock:
                    job.result = None
    finally:
        # Every exit path, including one nobody anticipated, marks the job
        # done: a job that never finishes leaves the bar on "Loading
        # suggestions..." with no Retry and no way back short of a restart.
        sys.setswitchinterval(previous_interval)
        with job.lock:
            job.done = True
            notifier = job.notifier
        if notifier is not None:
            notifier.finished.emit()


class PredictionLoader(QObject):
    """Publish on the UI thread; cancellation never waits for the build.

    The worker only owns a parentless predictor and a plain Python job. It
    never calls the bridge or emits through a QObject that closing the
    keyboard might already have destroyed (see ``_Notifier``).  Publication
    and cancellation share a lock so an abandoned result cannot outlive its
    target thread.
    """

    loaded = Signal(object)
    failed = Signal()

    def __init__(self, factory: PredictorFactory, parent: QObject) -> None:
        super().__init__(parent)
        self._factory = factory
        notifier = _Notifier()
        self._job = _LoadJob(notifier)
        self._started = False
        self._published = False
        # Queued explicitly: the emit comes from the worker thread and the
        # receiver is this object on the UI thread, and saying so beats
        # trusting AutoConnection to notice at emit time.
        notifier.finished.connect(self._publish, Qt.ConnectionType.QueuedConnection)
        self.destroyed.connect(self._job.cancel)

    def start(self) -> None:
        if self._started or self._job.cancelled:
            return
        self._started = True
        thread = threading.Thread(
            target=_build,
            args=(self._factory, self.thread(), self._job),
            name="alpha-osk-prediction-load",
            daemon=True,
        )
        self._job.thread = thread
        thread.start()

    def cancel(self, wait: float = 0.0) -> None:
        """Discard the build; with ``wait`` > 0, give it that long to stop.

        The wait is for shutdown: a worker still constructing QObjects while
        Qt tears down is how an early quit crashed.  The factory polls the
        cancel flag between its phases, so the join normally returns well
        inside the bound; the bound is for a phase that is mid-way.
        """
        self._job.cancel()
        thread = self._job.thread
        if wait > 0 and thread is not None and thread.is_alive():
            thread.join(timeout=wait)
            if thread.is_alive():
                _logger.warning("Prediction engine load did not stop within %.1fs", wait)

    def _publish(self) -> None:
        if self._published:
            return
        with self._job.lock:
            if self._job.cancelled or not self._job.done:
                return
            predictor = self._job.result
            self._job.result = None
        self._published = True
        notifier = self._job.notifier
        if notifier is not None:
            notifier.deleteLater()
            self._job.notifier = None
        if predictor is None:
            self.failed.emit()
        else:
            self.loaded.emit(predictor)
