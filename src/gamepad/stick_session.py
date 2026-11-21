"""One pick-up gesture per stick, tied to the pointer confiner and a sink.

Pure Python over injected collaborators (no Qt), so the whole thing runs in
tests against fakes. The rule that shapes every method: **fail towards
freeing the pointer**. The pointer is the user's only input device, so any
error, a lost guard or a refused clip ends with the clip released and the
stick centred, never a stick left following a pointer nobody confines.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Protocol, Tuple

from .stick import IDLE, LATCHED, StickGesture, split_drive

_logger = logging.getLogger("StickSession")

STICKS = ("ls", "rs")


class StickSink(Protocol):
    def set_stick(self, stick: str, x: float, y: float) -> object: ...
    def center_stick(self, stick: str) -> object: ...
    def set_trigger(self, trigger: str, value: float) -> object: ...
    def clear_trigger(self, trigger: str) -> object: ...


class StickSession:
    def __init__(self, confiner: Any, sink: StickSink) -> None:
        self._confiner = confiner
        self._sink = sink
        self._gestures: Dict[str, StickGesture] = {s: StickGesture() for s in STICKS}
        self._drive = False
        #: Use the pointer clip, when the confiner allows it.
        self.confine = True
        #: Move the pointer onto the stick's centre on pick-up.
        self.recentre = True
        self.held: Optional[str] = None
        self.confined = False
        self.last_tick = "idle"

    # ---- drive mode ----

    @property
    def drive(self) -> bool:
        return self._drive

    @drive.setter
    def drive(self, value: bool) -> None:
        value = bool(value)
        if value == self._drive:
            return
        # Release under the old mode so no trigger is left held by the switch.
        if self.state("ls") != IDLE:
            self.release("ls")
        self._drive = value

    # ---- reads ----

    def state(self, stick: str) -> str:
        gesture = self._gestures.get(stick)
        return gesture.state if gesture is not None else IDLE

    def position(self, stick: str) -> Tuple[float, float]:
        gesture = self._gestures.get(stick)
        return gesture.position if gesture is not None else (0.0, 0.0)

    # ---- gestures ----

    def pick_up(self, stick: str, centre: Tuple[int, int], travel: int) -> str:
        """``"held"``, ``"held-unconfined"``, ``"released"`` or ``"refused"``."""
        if stick not in self._gestures:
            return "refused"
        try:
            gesture = self._gestures[stick]
            if gesture.state == LATCHED:
                self.release(stick)
                return "released"
            if self.held is not None and self.held != stick:
                self.hold()
            if self.held == stick:
                self._free_pointer()
            pointer = self._confiner.cursor_pos()
            if pointer is None:
                return "refused"
            confined = False
            if self.confine:
                pick = gesture.pick_up(pointer, centre, travel, recentre=self.recentre)
                if self._confiner.confine(pick.clip):
                    confined = True
                    # After the clip, so the pointer lands inside it.
                    if pick.warp_to is not None:
                        self._confiner.set_cursor_pos(*pick.warp_to)
                else:
                    # Never move a pointer that is not walled in.
                    gesture.pick_up(pointer, centre, travel, recentre=False)
            else:
                gesture.pick_up(pointer, centre, travel, recentre=False)
            self.held = stick
            self.confined = confined
            self._send(stick, 0.0, 0.0)
            return "held" if confined else "held-unconfined"
        except Exception as exc:
            _logger.warning("stick pick_up failed: %s", type(exc).__name__)
            self._fail()
            return "refused"

    def tick(self) -> str:
        stick = self.held
        if stick is None:
            self.last_tick = "idle"
            return "idle"
        try:
            status = "ok"
            if self.confined:
                status = str(self._confiner.tick())
                if status in ("guard-released", "guard-lost"):
                    self.drop()
                    self.last_tick = status
                    return status
            pointer = self._confiner.cursor_pos()
            if pointer is None:
                self.last_tick = "no-pointer"
                return "no-pointer"
            x, y = self._gestures[stick].move(pointer)
            self._send(stick, x, y)
            if status not in ("ok", "reapplied"):
                status = "ok"
            self.last_tick = status
            return status
        except Exception as exc:
            _logger.warning("stick tick failed: %s", type(exc).__name__)
            self._fail()
            self.last_tick = "error"
            return "error"

    def drop(self) -> None:
        """The held stick goes back to centre."""
        if self.held is not None:
            self.release(self.held)

    def hold(self) -> None:
        """The held stick stays where it is; it no longer keeps the pointer."""
        stick = self.held
        if stick is None:
            return
        self._free_pointer()
        try:
            self._gestures[stick].hold()
        except Exception as exc:
            _logger.warning("stick hold failed: %s", type(exc).__name__)

    def release(self, stick: str) -> None:
        """Centre a stick from any state."""
        if stick not in self._gestures:
            return
        if stick == self.held:
            self._free_pointer()
        try:
            self._gestures[stick].release()
            self._sink.center_stick(stick)
            if stick == "ls" and self._drive:
                self._sink.clear_trigger("rt")
                self._sink.clear_trigger("lt")
        except Exception as exc:
            _logger.warning("stick release failed: %s", type(exc).__name__)

    def release_all(self) -> None:
        self._free_pointer()
        for stick in STICKS:
            self.release(stick)
        self._free_pointer()

    # ---- internals ----

    def _send(self, stick: str, x: float, y: float) -> None:
        if self._drive and stick == "ls":
            steer, throttle, brake = split_drive(x, y)
            self._sink.set_stick("ls", steer, 0.0)
            self._sink.set_trigger("rt", throttle)
            self._sink.set_trigger("lt", brake)
        else:
            self._sink.set_stick(stick, x, y)

    def _free_pointer(self) -> None:
        """Let the pointer go, and forget which stick had it. Never raises."""
        try:
            self._confiner.release()
        except Exception as exc:
            _logger.warning("pointer release failed: %s", type(exc).__name__)
        self.held = None
        self.confined = False

    def _fail(self) -> None:
        self._free_pointer()
        for stick in STICKS:
            try:
                self.release(stick)
            except Exception:
                pass
        self._free_pointer()


__all__ = ["StickSession", "StickSink"]
