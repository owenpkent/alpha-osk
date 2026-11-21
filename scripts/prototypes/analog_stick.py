"""Throwaway test window for the pick-up analog stick, run by hand.

A small always-on-top window that never takes focus. Click a stick to pick it
up: the pointer is walled into the stick's travel square (Win32 ClipCursor,
under an out-of-process guard) and moves the stick with no button held; a
left click drops it back to centre, a right click leaves it held in place.

``main()`` is the only thing that builds the real pointer confiner and the
real virtual pad, and it is guarded by ``__name__``: importing this module
constructs nothing and opens nothing, which is what lets the tests drive
``StickPrototype`` over fakes.
"""

from __future__ import annotations

import atexit
import logging
import sys
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.gamepad.stick import IDLE, split_drive  # noqa: E402
from src.gamepad.stick_session import StickSession  # noqa: E402

QML_PATH = Path(__file__).with_name("AnalogStick.qml")
TICK_MS = 8

_logger = logging.getLogger("StickPrototype")


class BridgeSink:
    """Adapts ``GamepadBridge``'s camelCase slots to the session's ``StickSink``."""

    def __init__(self, bridge: Any) -> None:
        self._bridge = bridge

    def set_stick(self, stick: str, x: float, y: float) -> object:
        return self._bridge.setStick(stick, x, y)

    def center_stick(self, stick: str) -> object:
        return self._bridge.centerStick(stick)

    def set_trigger(self, trigger: str, value: float) -> object:
        return self._bridge.setTrigger(trigger, value)

    def clear_trigger(self, trigger: str) -> object:
        return self._bridge.clearTrigger(trigger)


class StickPrototype(QObject):
    """What ``AnalogStick.qml`` talks to, over injected collaborators."""

    statusChanged = Signal()
    guardChanged = Signal()
    confineChanged = Signal()
    recentreChanged = Signal()
    driveChanged = Signal()
    readoutChanged = Signal()
    noteChanged = Signal()
    quitRequested = Signal()

    def __init__(
        self,
        bridge: Any,
        confiner: Any,
        session: StickSession,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._confiner = confiner
        self._session = session
        self._started = False
        self._note = ""
        self._status = ""
        self._guard = False
        self._clip = "idle"
        self._tick_ms = 0.0
        self._last_tick_at: Optional[float] = None
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_tick)
        self._refresh()

    # ---- properties ----

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(bool, notify=guardChanged)
    def guard(self) -> bool:
        return self._guard

    def _get_confine(self) -> bool:
        return self._session.confine

    def _set_confine(self, value: bool) -> None:
        if bool(value) != self._session.confine:
            self._session.confine = bool(value)
            self.confineChanged.emit()

    confine = Property(bool, _get_confine, _set_confine, notify=confineChanged)

    def _get_recentre(self) -> bool:
        return self._session.recentre

    def _set_recentre(self, value: bool) -> None:
        if bool(value) != self._session.recentre:
            self._session.recentre = bool(value)
            self.recentreChanged.emit()

    recentre = Property(bool, _get_recentre, _set_recentre, notify=recentreChanged)

    def _get_drive(self) -> bool:
        return self._session.drive

    def _set_drive(self, value: bool) -> None:
        if bool(value) != self._session.drive:
            self._session.drive = bool(value)  # releases a deflected left stick first
            self.driveChanged.emit()
            self._refresh()

    drive = Property(bool, _get_drive, _set_drive, notify=driveChanged)

    @Property(str, notify=readoutChanged)
    def held(self) -> str:
        return self._session.held or ""

    @Property(float, notify=readoutChanged)
    def lsX(self) -> float:
        return self._session.position("ls")[0]

    @Property(float, notify=readoutChanged)
    def lsY(self) -> float:
        return self._session.position("ls")[1]

    @Property(float, notify=readoutChanged)
    def rsX(self) -> float:
        return self._session.position("rs")[0]

    @Property(float, notify=readoutChanged)
    def rsY(self) -> float:
        return self._session.position("rs")[1]

    @Property(float, notify=readoutChanged)
    def lt(self) -> float:
        return self._triggers()[0]

    @Property(float, notify=readoutChanged)
    def rt(self) -> float:
        return self._triggers()[1]

    @Property(str, notify=readoutChanged)
    def lsState(self) -> str:
        return self._session.state("ls")

    @Property(str, notify=readoutChanged)
    def rsState(self) -> str:
        return self._session.state("rs")

    @Property(str, notify=readoutChanged)
    def clip(self) -> str:
        return self._clip

    @Property(int, notify=readoutChanged)
    def reapplied(self) -> int:
        return int(getattr(self._confiner, "reapplied", 0))

    @Property(float, notify=readoutChanged)
    def tickMs(self) -> float:
        return self._tick_ms

    @Property("QVariantList", notify=readoutChanged)  # type: ignore[arg-type]
    def latched(self) -> List[str]:
        return list(getattr(self._bridge, "latched", []) or [])

    @Property(str, notify=noteChanged)
    def note(self) -> str:
        return self._note

    # ---- slots ----

    @Slot()
    def connectPad(self) -> None:
        try:
            if not self._started:
                self._started = bool(self._confiner.start())
            if not self._started:
                if self._session.confine:
                    self._session.confine = False
                    self.confineChanged.emit()
                self._set_note("Pointer confinement is unavailable (no guard).")
            else:
                self._set_note("")
            self._bridge.setActive(True)
        except Exception as exc:
            _logger.warning("connectPad failed: %s", type(exc).__name__)
            self._set_note("Could not connect.")
        self._refresh()

    @Slot()
    def disconnectPad(self) -> None:
        self._release_everything()
        try:
            self._bridge.setActive(False)
        except Exception as exc:
            _logger.warning("disconnectPad failed: %s", type(exc).__name__)
        self._refresh()

    @Slot(str, float, float, float, result=str)
    def pickUp(self, stick: str, centreDx: float, centreDy: float, travel: float) -> str:
        """``centreDx/Dy``: physical pixels from the pointer to the stick's centre.

        The pointer is read through the confiner and the offsets added, so no
        global coordinate mapping is needed (the trick Move mode uses).
        """
        try:
            pointer = self._confiner.cursor_pos()
            if pointer is None:
                return "refused"
            centre = (pointer[0] + int(round(centreDx)), pointer[1] + int(round(centreDy)))
            result = str(self._session.pick_up(stick, centre, int(round(travel))))
        except Exception as exc:
            _logger.warning("pickUp failed: %s", type(exc).__name__)
            result = "refused"
        self._sync_timer()
        self._refresh()
        return result

    @Slot()
    def drop(self) -> None:
        self._session.drop()
        self._sync_timer()
        self._refresh()

    @Slot()
    def hold(self) -> None:
        self._session.hold()
        self._sync_timer()
        self._refresh()

    @Slot(str)
    def tapButton(self, control_id: str) -> None:
        try:
            self._bridge.tap(control_id)
        except Exception as exc:
            _logger.warning("tapButton failed: %s", type(exc).__name__)

    @Slot(str)
    def latchButton(self, control_id: str) -> None:
        try:
            self._bridge.toggleLatch(control_id)
        except Exception as exc:
            _logger.warning("latchButton failed: %s", type(exc).__name__)
        self._refresh()

    @Slot()
    def quit(self) -> None:
        self.shutdown()
        self.quitRequested.emit()

    # ---- teardown (also called by main's every exit path) ----

    def shutdown(self) -> None:
        """Release the pointer and the pad. Idempotent, never raises."""
        self._release_everything()
        try:
            self._confiner.close()
        except Exception as exc:
            _logger.warning("confiner close failed: %s", type(exc).__name__)
        self._started = False
        try:
            self._bridge.shutdown()
        except Exception as exc:
            _logger.warning("bridge shutdown failed: %s", type(exc).__name__)

    # ---- internals ----

    def _release_everything(self) -> None:
        self._timer.stop()
        try:
            self._session.release_all()
        except Exception as exc:
            _logger.warning("release_all failed: %s", type(exc).__name__)
            try:
                self._confiner.release()
            except Exception:
                pass

    def _triggers(self) -> Tuple[float, float]:
        if self._session.drive and self._session.state("ls") != IDLE:
            _steer, throttle, brake = split_drive(*self._session.position("ls"))
            return (brake, throttle)
        return (0.0, 0.0)

    def _set_note(self, note: str) -> None:
        if note != self._note:
            self._note = note
            self.noteChanged.emit()

    def _sync_timer(self) -> None:
        busy = any(self._session.state(s) != IDLE for s in ("ls", "rs"))
        if busy and not self._timer.isActive():
            self._last_tick_at = None
            self._timer.start()
        elif not busy and self._timer.isActive():
            self._timer.stop()

    def _on_tick(self) -> None:
        now = time.perf_counter()
        if self._last_tick_at is not None:
            gap = (now - self._last_tick_at) * 1000.0
            self._tick_ms = gap if self._tick_ms == 0.0 else self._tick_ms * 0.9 + gap * 0.1
        self._last_tick_at = now
        self._clip = self._session.tick()
        self._sync_timer()
        self._refresh()

    def _refresh(self) -> None:
        status = str(getattr(self._bridge, "status", ""))
        if status != self._status:
            self._status = status
            self.statusChanged.emit()
        guard = bool(self._started and getattr(self._confiner, "guard_alive", False))
        if guard != self._guard:
            self._guard = guard
            self.guardChanged.emit()
        self.readoutChanged.emit()


def _apply_no_activate(window: Any) -> None:
    """Reuse the keyboard's own no-activate styling for a floating window."""
    if sys.platform != "win32":
        return
    try:
        from src.platform import windows_window

        windows_window.apply_extended_styles(window)
    except Exception as exc:
        _logger.warning("no-activate styling failed: %s", type(exc).__name__)


def main() -> int:
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine

    from src.gamepad_bridge import GamepadBridge
    from src.platform.pointer_clip import PointerConfiner

    logging.basicConfig(level=logging.INFO)
    app = QGuiApplication(sys.argv)
    app.setApplicationName("alpha-osk-stick-prototype")
    confiner = PointerConfiner()
    bridge = GamepadBridge()
    session = StickSession(confiner, BridgeSink(bridge))
    proto = StickPrototype(bridge, confiner, session)

    done = False

    def cleanup() -> None:
        # Every exit path funnels here; each step is independent so a failure
        # in one can never leave the pointer clipped.
        nonlocal done
        if done:
            return
        done = True
        for step in (session.release_all, confiner.close, bridge.shutdown):
            try:
                step()
            except Exception:
                pass

    atexit.register(cleanup)
    app.aboutToQuit.connect(cleanup)
    proto.quitRequested.connect(app.quit)
    code = 1
    try:
        engine = QQmlApplicationEngine()
        engine.rootContext().setContextProperty("stick", proto)
        engine.load(QUrl.fromLocalFile(str(QML_PATH)))
        if not engine.rootObjects():
            return 1
        _apply_no_activate(engine.rootObjects()[0])
        code = app.exec()
    finally:
        cleanup()
    return code


if __name__ == "__main__":
    sys.exit(main())
