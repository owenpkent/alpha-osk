"""Which controls are down, and the report that follows from them.

Pure data: no Qt, no device, no clock. ``GamepadBridge`` owns one
``PadState``, mutates it from its slots and hands ``report()`` to the pad
worker, so every rule about what a tap or a latch means is here and can be
tested without a controller.

The vocabulary is a closed set of lower-case ids (``CONTROL_IDS``). An id
that is not in it changes nothing: the ids arrive from QML and from an
external switch scanner, so this is an allow-list, the same stance
``key_actions`` takes.

Three rules shape the model, all of them the keyboard's own modifier
idiom carried over:

* A control is down while it is *pressed* (a click being held, or a tap's
  minimum hold) or *latched* (right-click: it stays down until released).
* Pressing a latched control lets go of the latch and presses nothing,
  which is what a left tap on a locked Shift does.
* Some controls cannot be down together: the eight directions of one
  stick, and the two ends of one D-pad axis. Within such a group the most
  recent press wins while it lasts, then the latched member (there is at
  most one) shows through again.

Sticks and triggers are digital here: a direction is full deflection and a
trigger is all the way down. Analog control is a later phase and will add
to this module, not replace its rules.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from ..platform.padbus_client import XUSB_BUTTON

#: Buttons that are one bit of ``wButtons``.
BUTTON_MASKS: Dict[str, int] = {
    "a": int(XUSB_BUTTON.XUSB_GAMEPAD_A),
    "b": int(XUSB_BUTTON.XUSB_GAMEPAD_B),
    "x": int(XUSB_BUTTON.XUSB_GAMEPAD_X),
    "y": int(XUSB_BUTTON.XUSB_GAMEPAD_Y),
    "lb": int(XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER),
    "rb": int(XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER),
    "back": int(XUSB_BUTTON.XUSB_GAMEPAD_BACK),
    "start": int(XUSB_BUTTON.XUSB_GAMEPAD_START),
    "guide": int(XUSB_BUTTON.XUSB_GAMEPAD_GUIDE),
    "l3": int(XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB),
    "r3": int(XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB),
    "dpad_up": int(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP),
    "dpad_down": int(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN),
    "dpad_left": int(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT),
    "dpad_right": int(XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT),
}

TRIGGERS: Tuple[str, ...] = ("lt", "rt")

_DIAGONAL = math.sqrt(0.5)

#: Compass point to a unit vector. X is right and Y is up, which is how
#: XInput reports a thumbstick.
_COMPASS: Dict[str, Tuple[float, float]] = {
    "n": (0.0, 1.0),
    "ne": (_DIAGONAL, _DIAGONAL),
    "e": (1.0, 0.0),
    "se": (_DIAGONAL, -_DIAGONAL),
    "s": (0.0, -1.0),
    "sw": (-_DIAGONAL, -_DIAGONAL),
    "w": (-1.0, 0.0),
    "nw": (-_DIAGONAL, _DIAGONAL),
}

STICKS: Tuple[str, ...] = ("ls", "rs")

#: ``ls_n`` .. ``rs_nw``: one stick held hard over in one of eight directions.
STICK_VECTORS: Dict[str, Tuple[float, float]] = {
    f"{stick}_{point}": vector for stick in STICKS for point, vector in _COMPASS.items()
}

CONTROL_IDS: FrozenSet[str] = (
    frozenset(BUTTON_MASKS) | frozenset(TRIGGERS) | frozenset(STICK_VECTORS)
)

#: Controls that cannot be down together, by group name.
_GROUP_OF: Dict[str, str] = {
    **{cid: cid.split("_", 1)[0] for cid in STICK_VECTORS},
    "dpad_up": "dpad_vertical",
    "dpad_down": "dpad_vertical",
    "dpad_left": "dpad_horizontal",
    "dpad_right": "dpad_horizontal",
}


def is_control(control_id: object) -> bool:
    """Whether ``control_id`` names a control. Anything else is refused."""
    return isinstance(control_id, str) and control_id in CONTROL_IDS


@dataclass(frozen=True)
class PadReport:
    """One Xbox 360 report, in the units the pad client's float setters take.

    Sticks are -1.0 to 1.0 per axis (Y up), triggers 0.0 to 1.0, and
    ``buttons`` is the ``wButtons`` word.
    """

    buttons: int = 0
    left_trigger: float = 0.0
    right_trigger: float = 0.0
    left_x: float = 0.0
    left_y: float = 0.0
    right_x: float = 0.0
    right_y: float = 0.0

    @property
    def is_neutral(self) -> bool:
        return self == NEUTRAL


#: Nothing pressed, both sticks centred. What a pad is left on before it goes.
NEUTRAL = PadReport()


class PadState:
    """The controls that are down right now."""

    def __init__(self) -> None:
        # Ordered, oldest first: within a group the most recent press wins.
        self._pressed: List[str] = []
        self._latched: Set[str] = set()

    # ---- reading ----

    @property
    def latched(self) -> FrozenSet[str]:
        return frozenset(self._latched)

    @property
    def pressed(self) -> FrozenSet[str]:
        return frozenset(self._pressed)

    def is_down(self, control_id: str) -> bool:
        """Whether the control is in the report, after the group rule."""
        return control_id in self._down()

    # ---- changing ----

    def press(self, control_id: str) -> bool:
        """Press a control. True when the report changed.

        Pressing a latched control releases the latch instead of pressing.
        """
        if not is_control(control_id):
            return False
        before = self.report()
        if control_id in self._latched:
            self._latched.discard(control_id)
        elif control_id not in self._pressed:
            self._pressed.append(control_id)
        return self.report() != before

    def release(self, control_id: str) -> bool:
        """Let go of a pressed control. A latch is not touched."""
        if control_id not in self._pressed:
            return False
        before = self.report()
        self._pressed.remove(control_id)
        return self.report() != before

    def toggle_latch(self, control_id: str) -> Optional[bool]:
        """Latch a control, or unlatch it. Returns the new latched state.

        ``None`` means the id was refused. Latching one member of a group
        unlatches the others: a stick points one way.
        """
        if not is_control(control_id):
            return None
        if control_id in self._latched:
            self._latched.discard(control_id)
            return False
        group = _GROUP_OF.get(control_id)
        if group is not None:
            self._latched -= {cid for cid in self._latched if _GROUP_OF.get(cid) == group}
        self._latched.add(control_id)
        return True

    def release_all(self) -> bool:
        """Drop every press and every latch. True when anything was down."""
        was_down = bool(self._pressed or self._latched)
        self._pressed.clear()
        self._latched.clear()
        return was_down

    # ---- the report ----

    def _down(self) -> Set[str]:
        down: Set[str] = set()
        winners: Dict[str, str] = {}
        for control_id in sorted(self._latched):
            group = _GROUP_OF.get(control_id)
            if group is None:
                down.add(control_id)
            else:
                winners[group] = control_id
        # Presses after latches, in press order, so the latest press in a
        # group is the one left standing.
        for control_id in self._pressed:
            group = _GROUP_OF.get(control_id)
            if group is None:
                down.add(control_id)
            else:
                winners[group] = control_id
        down.update(winners.values())
        return down

    def report(self) -> PadReport:
        down = self._down()
        buttons = 0
        sticks: Dict[str, Tuple[float, float]] = {stick: (0.0, 0.0) for stick in STICKS}
        for control_id in down:
            mask = BUTTON_MASKS.get(control_id)
            if mask is not None:
                buttons |= mask
                continue
            vector = STICK_VECTORS.get(control_id)
            if vector is not None:
                sticks[_GROUP_OF[control_id]] = vector
        return PadReport(
            buttons=buttons,
            left_trigger=1.0 if "lt" in down else 0.0,
            right_trigger=1.0 if "rt" in down else 0.0,
            left_x=sticks["ls"][0],
            left_y=sticks["ls"][1],
            right_x=sticks["rs"][0],
            right_y=sticks["rs"][1],
        )
