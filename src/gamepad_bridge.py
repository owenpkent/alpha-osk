"""
QML-facing game controller, a sibling of ``TelemetryBridge``.

Registered as the ``gamepad`` context property in ``keyboard_app.py``.
``KeyboardBridge`` does not know it exists (the split in
``docs/architecture/STRUCTURAL_REVIEW.md`` section 3.1) and this module
does not import it.

Everything slow lives on the pad worker thread (``gamepad/worker.py``);
this object owns the ``PadState``, the minimum-hold timers and the status
the QML shows. Nothing typed by the user passes through here, and only
exception type names are ever logged.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot

from .gamepad.device import PadDevice, open_pad, pad_bus_available
from .gamepad.state import CONTROL_IDS, PadReport, PadState, is_control
from .gamepad.worker import PadWorker

_logger = logging.getLogger("GamepadBridge")

_FAILED_MESSAGE = "The virtual controller stopped working."
_DISCONNECTING_MESSAGE = "The controller is still disconnecting. Try again in a moment."


class _StatusNotifier(QObject):
    """Carries a worker-thread status to the bridge on the Qt thread.

    Parentless and held by the bridge, like ``_FocusNotifier``: it must
    outlive any emit the worker thread is still inside when it is stopped.
    """

    status = Signal(int, str, str)


class GamepadBridge(QObject):
    #: Shortest time a control stays down, in ms. Provisional: a game that
    #: polls once a frame can miss anything shorter; to be measured against
    #: real games.
    MIN_PRESS_MS = 100

    availableChanged = Signal()
    statusChanged = Signal()
    latchedChanged = Signal()
    failed = Signal(str)

    def __init__(
        self,
        *,
        open_device: Callable[[], PadDevice] = open_pad,
        bus_available: Callable[[], bool] = pad_bus_available,
        worker_factory: Callable[..., Any] = PadWorker,
        clock: Callable[[], float] = time.monotonic,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._open_device = open_device
        self._bus_available = bus_available
        self._worker_factory = worker_factory
        self._clock = clock
        self._state = PadState()
        self._available = False
        self._status = "off"
        self._worker: Any = None
        self._generation = 0
        #: Stopped workers whose thread outlived the stop timeout (stuck in a
        #: driver call). A new pad must not start beside one.
        self._retiring: List[Any] = []
        self._last_report: PadReport = self._state.report()
        self._latched_view: List[str] = []
        self._pressed_at: Dict[str, float] = {}
        self._release_timers: Dict[str, QTimer] = {}
        self._notifier = _StatusNotifier()
        self._notifier.status.connect(self._on_worker_status, Qt.ConnectionType.QueuedConnection)

    # ---- properties ----

    @Property(bool, notify=availableChanged)
    def available(self) -> bool:
        return self._available

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property("QVariantList", notify=latchedChanged)  # type: ignore[arg-type]
    def latched(self) -> List[str]:
        return list(self._latched_view)

    # ---- availability and on/off ----

    @Slot(result=bool)
    def refreshAvailability(self) -> bool:
        try:
            available = bool(self._bus_available())
        except Exception as exc:
            _logger.warning("pad bus probe failed: %s", type(exc).__name__)
            available = False
        if available != self._available:
            self._available = available
            self.availableChanged.emit()
        return available

    @Slot(bool, result=bool)
    def setActive(self, active: bool) -> bool:
        try:
            if active:
                return self._turn_on()
            self._turn_off()
            return False
        except Exception as exc:
            _logger.warning("gamepad setActive failed: %s", type(exc).__name__)
            self._turn_off_quietly()
            return False

    def _turn_on(self) -> bool:
        if self._status in ("on", "connecting"):
            return True
        self._retiring = [w for w in self._retiring if getattr(w, "running", False)]
        if self._retiring:
            self.failed.emit(_DISCONNECTING_MESSAGE)
            return False
        if not self.refreshAvailability():
            self._set_status("unavailable")
            return False
        self._release_everything()
        self._generation += 1
        generation = self._generation
        notifier = self._notifier

        def on_status(status: str, detail: str) -> None:
            notifier.status.emit(generation, status, detail)

        self._set_status("connecting")
        try:
            worker = self._worker_factory(self._open_device, on_status)
            self._worker = worker
            if hasattr(worker, "min_hold_s"):
                worker.min_hold_s = self.MIN_PRESS_MS / 1000.0
            worker.start()
        except Exception as exc:
            _logger.warning("pad worker failed to start: %s", type(exc).__name__)
            self._drop_worker()
            self._set_status("error")
            self.failed.emit(_FAILED_MESSAGE)
            return False
        return True

    def _turn_off(self) -> None:
        self._generation += 1
        self._release_everything()
        self._drop_worker()
        self._set_status("off")

    def _turn_off_quietly(self) -> None:
        try:
            self._turn_off()
        except Exception:
            pass

    def _drop_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                worker.stop()
            except Exception as exc:
                _logger.warning("pad worker stop failed: %s", type(exc).__name__)
            if getattr(worker, "running", False):
                self._retiring.append(worker)

    def shutdown(self) -> None:
        """Turn the pad off. Idempotent; called from the app's quit sequence."""
        self._turn_off_quietly()

    # ---- worker status (Qt thread) ----

    @Slot(int, str, str)
    def _on_worker_status(self, generation: int, status: str, detail: str) -> None:
        try:
            if generation != self._generation or self._worker is None:
                return
            if status == "on":
                self._set_status("on")
                self._submit(force=True)
            elif status == "error":
                _logger.warning("pad worker error: %s", detail)
                self._release_everything()
                self._drop_worker()
                self._set_status("error")
                self.failed.emit(_FAILED_MESSAGE)
        except Exception as exc:
            _logger.warning("gamepad status handling failed: %s", type(exc).__name__)

    # ---- controls ----

    @Slot(str, result=bool)
    def press(self, control_id: str) -> bool:
        try:
            if self._status != "on" or not is_control(control_id):
                return False
            self._cancel_release(control_id)
            self._pressed_at[control_id] = self._clock()
            self._state.press(control_id)
            self._publish()
            return True
        except Exception as exc:
            _logger.warning("gamepad press failed: %s", type(exc).__name__)
            return False

    @Slot(str)
    def release(self, control_id: str) -> None:
        try:
            if not is_control(control_id):
                return
            pressed_at = self._pressed_at.get(control_id)
            if pressed_at is not None:
                remaining_ms = self.MIN_PRESS_MS - (self._clock() - pressed_at) * 1000.0
                if remaining_ms > 0:
                    self._defer_release(control_id, int(remaining_ms) + 1)
                    return
            self._apply_release(control_id)
        except Exception as exc:
            _logger.warning("gamepad release failed: %s", type(exc).__name__)

    @Slot(str, result=bool)
    def tap(self, control_id: str) -> bool:
        if not self.press(control_id):
            return False
        self.release(control_id)
        return True

    @Slot(str, result=bool)
    def toggleLatch(self, control_id: str) -> bool:
        try:
            if self._status != "on" or not is_control(control_id):
                return False
            result = bool(self._state.toggle_latch(control_id))
            self._publish()
            return result
        except Exception as exc:
            _logger.warning("gamepad latch failed: %s", type(exc).__name__)
            return False

    @Slot(str, result=bool)
    def isLatched(self, control_id: str) -> bool:
        return control_id in self._state.latched

    @Slot()
    def releaseAll(self) -> None:
        try:
            self._release_everything()
            self._publish()
        except Exception as exc:
            _logger.warning("gamepad releaseAll failed: %s", type(exc).__name__)

    @Slot(result="QVariantList")
    def getControlIds(self) -> List[str]:
        return sorted(CONTROL_IDS)

    # ---- internals ----

    def _defer_release(self, control_id: str, delay_ms: int) -> None:
        timer = self._release_timers.get(control_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda cid=control_id: self._apply_release(cid))
            self._release_timers[control_id] = timer
        timer.start(delay_ms)

    def _cancel_release(self, control_id: str) -> None:
        timer = self._release_timers.get(control_id)
        if timer is not None:
            timer.stop()

    def _apply_release(self, control_id: str) -> None:
        try:
            self._cancel_release(control_id)
            self._pressed_at.pop(control_id, None)
            self._state.release(control_id)
            self._publish()
        except Exception as exc:
            _logger.warning("gamepad release failed: %s", type(exc).__name__)

    def _release_everything(self) -> None:
        for timer in self._release_timers.values():
            timer.stop()
        self._pressed_at.clear()
        self._state.release_all()
        self._refresh_latched()

    def _refresh_latched(self) -> None:
        view = sorted(self._state.latched)
        if view != self._latched_view:
            self._latched_view = view
            self.latchedChanged.emit()

    def _publish(self) -> None:
        self._refresh_latched()
        self._submit()

    def _submit(self, *, force: bool = False) -> None:
        report = self._state.report()
        changed = report != self._last_report
        self._last_report = report
        if self._status == "on" and self._worker is not None and (changed or force):
            self._worker.submit(report)

    def _set_status(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        _logger.info("Gamepad status: %s", status)
        self.statusChanged.emit()
