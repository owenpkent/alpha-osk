"""Build a predictor off the UI thread and hand it over once complete."""

from __future__ import annotations

import gc
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
#
# 0.1 ms rather than 1 ms: every time the UI thread re-enters Python (a slot,
# a property read, the return from a ctypes call) it waits up to one interval
# for the worker to let go, and a keystroke does that several times, so at
# 1 ms a key still reached the synthesiser ~4.5 ms late (median) and the
# next frame ~10 ms late.  At 0.1 ms both match an idle build, and the build
# itself takes no measurably longer (2.7 s either way, real model).
_BUILD_SWITCH_INTERVAL_S = 0.0001

# The switch interval cannot preempt the garbage collector: a collection is
# one C call that holds the GIL from start to finish, and the build's few
# hundred thousand fresh objects trigger four full (generation 2)
# collections of 30-60 ms each, two of them back to back.  Those were the
# keystrokes that lagged by 50-90 ms, and a fast typist's second tap on the
# same key could land inside KeyButton's 150 ms debounce and be dropped.
# The build makes almost no cyclic garbage (3 unreachable objects with the
# real model), so collection is paused for its duration, and at the end
# everything alive is moved into the permanent generation with gc.freeze()
# rather than collected.  A collect there would be one more ~30 ms stall
# right before "ready", and re-enabling without either would hand the whole
# backlog to the next allocation, wherever that happens.  Freezing also
# takes the engine's ~200 000 long-lived objects out of every later full
# collection, which otherwise costs ~25 ms each time one lands mid-typing
# (measured: 24 ms before the freeze, under 0.1 ms after).  The price is
# that those few cyclic objects are never reclaimed.
_settings_lock = threading.Lock()
_active_builds = 0
_saved_interval = 0.0
_saved_gc_enabled = True


def _enter_build_settings() -> None:
    """Lower the switch interval and pause the collector; nests safely.

    Both are process-wide, so only the first build in saves them and only the
    last one out restores them: two overlapping builds restoring in the wrong
    order would otherwise leave the process on the build's settings for good.
    """
    global _active_builds, _saved_interval, _saved_gc_enabled
    with _settings_lock:
        if _active_builds == 0:
            _saved_interval = sys.getswitchinterval()
            _saved_gc_enabled = gc.isenabled()
        _active_builds += 1
        sys.setswitchinterval(_BUILD_SWITCH_INTERVAL_S)
        gc.disable()


def _exit_build_settings() -> None:
    global _active_builds
    with _settings_lock:
        _active_builds -= 1
        if _active_builds:
            return
        sys.setswitchinterval(_saved_interval)
        # The collector comes back under the same lock that counted this
        # build out.  Released first, a build entering in the gap would find
        # no active builds and a disabled collector, save that as the
        # baseline, and switch the collector off for good on its own way out.
        if _saved_gc_enabled:
            gc.freeze()
            gc.enable()


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
    predictor: HybridPredictor | None = None
    entered = False
    try:
        _enter_build_settings()
        entered = True
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
        try:
            if entered:
                _exit_build_settings()
        except Exception:
            _logger.exception("Could not restore the interpreter settings after the build")
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
