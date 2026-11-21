"""A stick you pick up and put down: the geometry, with no pointer and no Qt.

The keyboard's users cannot be asked to hold a mouse button and drag, so an
analog stick here is not dragged. It is **picked up** with a click, follows
the pointer with no button held, and is **put down** with another click:
dropped back to centre, or left held where it is. This is the idiom Move
mode already uses for the window.

While a stick is held the pointer is confined to the stick's travel square
(``platform/pointer_clip.py``), so full deflection is a wall the pointer
stops at, and the click that puts the stick down cannot land on the game.
This module only does the arithmetic: where the anchor is, what rectangle to
confine to, and what deflection a pointer position means. Everything is in
physical screen pixels, the unit the clip takes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

Point = Tuple[int, int]
Rect = Tuple[int, int, int, int]

IDLE = "idle"
HELD = "held"
LATCHED = "latched"


@dataclass(frozen=True)
class PickUp:
    """What picking a stick up asks of the caller."""

    #: The pointer position that means "centred".
    anchor: Point
    #: Where to confine the pointer: a Win32 RECT, right and bottom exclusive.
    clip: Rect
    #: Where to move the pointer first, or ``None`` to leave it where it is.
    warp_to: Optional[Point]


def travel_square(anchor: Point, travel: int) -> Rect:
    """The square the pointer may roam: ``travel`` pixels each way from ``anchor``."""
    x, y = anchor
    return (x - travel, y - travel, x + travel + 1, y + travel + 1)


def deflection(pointer: Point, anchor: Point, travel: int) -> Tuple[float, float]:
    """Pointer offset from the anchor as a stick position, X right and Y up.

    The pointer can reach the corners of its square, a stick cannot: an
    offset past the unit circle is pulled back onto it, keeping direction.
    """
    x = (pointer[0] - anchor[0]) / travel
    # Screen Y grows downward; a stick's Y grows upward.
    y = -(pointer[1] - anchor[1]) / travel
    magnitude = math.hypot(x, y)
    if magnitude > 1.0:
        x, y = x / magnitude, y / magnitude
    return (x, y)


def split_drive(x: float, y: float) -> Tuple[float, float, float]:
    """One stick as a car's controls: ``(steer, throttle, brake)``.

    Pushing up is the throttle and pulling down is the brake, each 0.0 to
    1.0, and never both. A single pointer can only hold one stick, so this
    is what lets one stick drive.
    """
    return (x, max(0.0, y), max(0.0, -y))


class StickGesture:
    """One stick's pick-up state: idle, held (following the pointer) or latched."""

    def __init__(self) -> None:
        self.state = IDLE
        self.position: Tuple[float, float] = (0.0, 0.0)
        self._anchor: Point = (0, 0)
        self._travel = 1

    @property
    def anchor(self) -> Point:
        return self._anchor

    @property
    def travel(self) -> int:
        return self._travel

    def pick_up(self, pointer: Point, centre: Point, travel: int, *, recentre: bool) -> PickUp:
        """Start following the pointer from a centred stick.

        With ``recentre`` the stick's drawn centre is the anchor and the
        pointer is moved onto it, so the knob sits under the pointer and the
        confinement is the stick's own square. Without it the anchor is
        wherever the click landed and the pointer is not moved.
        """
        travel = max(1, int(travel))
        anchor = centre if recentre else pointer
        self._anchor = anchor
        self._travel = travel
        self.state = HELD
        self.position = (0.0, 0.0)
        return PickUp(
            anchor=anchor,
            clip=travel_square(anchor, travel),
            warp_to=centre if recentre and pointer != centre else None,
        )

    def move(self, pointer: Point) -> Tuple[float, float]:
        """The pointer moved. Only a held stick follows it."""
        if self.state == HELD:
            self.position = deflection(pointer, self._anchor, self._travel)
        return self.position

    def hold(self) -> Tuple[float, float]:
        """Put the stick down where it is. It stays deflected until released."""
        if self.state == HELD:
            self.state = LATCHED
        return self.position

    def release(self) -> None:
        """Back to centre from any state: a drop, or letting go of a held stick."""
        self.state = IDLE
        self.position = (0.0, 0.0)
