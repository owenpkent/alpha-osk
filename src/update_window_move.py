"""Moving the update window: the pure half.

The update screen sits over the keyboard it replaced, and the user may want
it somewhere else (the installer's progress, a window they are reading).  It
has two ways to be moved, for the same reason the keyboard has two: dragging
means holding the button down for the whole travel, which is the one gesture
a user with imprecise motor control cannot reliably make.

* **Drag**: press on the window body, move, release.
* **Carry** (the keyboard's Move mode, ``qml/Main.qml::windowMoveOverlay``):
  click a button to pick the window up, move the pointer with no button held,
  left click to put it down, right click to put it back where it was.

This module is the state machine and the geometry, with no Qt and no Win32,
so both can be tested with plain tuples.  Positions are physical pixels,
because the helper places its window with Win32 and Qt's logical coordinates
do not agree across monitors of different scale.

How it follows the pointer
==========================

The window goes to ``grab_window + (cursor - grab_cursor)``, the *unclamped*
position, passed through the desktop clamp.  The clamped value is only ever
shown, never fed back into the accumulated position, which is the rule the
keyboard's Move mode states about its snapped value: feed a limit back in and
every later movement is measured from the limit, so a window pushed against
an edge cannot be brought back by the travel that pushed it there.  Working
from the absolute cursor position, where the keyboard's mode works from
pointer-relative deltas, is the same self-correction without the bookkeeping:
there is nothing to drift, and because the window keeps the grab offset the
pointer stays on it (so the click that puts it down always lands on it).
"""

from __future__ import annotations

import enum
from collections.abc import Callable, Sequence
from typing import Optional

Point = tuple[int, int]
Size = tuple[int, int]
# (left, top, right, bottom), like Win32's RECT.
Edges = tuple[int, int, int, int]


def _clamp_into(pos: Point, size: Size, area: Edges) -> Point:
    """``pos`` moved the least that puts a window of ``size`` inside ``area``.

    A window larger than the area is pinned to the area's top-left corner,
    which keeps its first lines readable rather than centring it off both
    edges.
    """
    left, top, right, bottom = area
    x = max(left, min(pos[0], right - size[0]))
    y = max(top, min(pos[1], bottom - size[1]))
    return (x, y)


def desktop_edges(monitors: Sequence[Edges]) -> Optional[Edges]:
    """The bounding box of every monitor, or None when there are none."""
    if not monitors:
        return None
    return (
        min(m[0] for m in monitors),
        min(m[1] for m in monitors),
        max(m[2] for m in monitors),
        max(m[3] for m in monitors),
    )


def clamp_to_desktop(pos: Point, size: Size, monitors: Sequence[Edges]) -> Point:
    """Keep a window of ``size`` at ``pos`` on the whole virtual desktop.

    Two steps.  First the bounding box of all monitors, not the primary one:
    a monitor to the left of the primary has negative coordinates a
    primary-screen clamp can never reach.  Then, because a bounding box of an
    L-shaped arrangement includes corners no monitor covers, a window whose
    centre fell in such a gap is pulled into the nearest monitor, so it can
    never be left somewhere nobody can see it.  With no monitor list the
    position is returned as it came.
    """
    box = desktop_edges(monitors)
    if box is None:
        return pos
    pos = _clamp_into(pos, size, box)
    cx = pos[0] + size[0] // 2
    cy = pos[1] + size[1] // 2
    if any(m[0] <= cx < m[2] and m[1] <= cy < m[3] for m in monitors):
        return pos

    def gap(m: Edges) -> int:
        dx = max(m[0] - cx, 0, cx - (m[2] - 1))
        dy = max(m[1] - cy, 0, cy - (m[3] - 1))
        return dx * dx + dy * dy

    nearest = min(monitors, key=gap)
    return _clamp_into(pos, size, nearest)


class MoveState(enum.Enum):
    IDLE = "idle"
    DRAGGING = "dragging"
    CARRYING = "carrying"


class WindowMover:
    """The drag / carry state machine.

    The driver (the update window) feeds it the cursor and the window's own
    position and applies what it returns; it never touches a window itself.

    * ``press`` / ``follow`` / ``release``: the drag.
    * ``pick_up`` / ``follow`` / ``put_down`` or ``put_back``: the carry.

    ``moved`` becomes true the first time the user actually moves the window,
    and stays true: it is what stops the window being re-centred on the
    keyboard's old position (when the failure screen grows) over the spot the
    user chose.
    """

    def __init__(self, monitors: Callable[[], Sequence[Edges]]) -> None:
        self._monitors = monitors
        self.state = MoveState.IDLE
        self.moved = False
        self._grab_cursor: Point = (0, 0)
        self._grab_window: Point = (0, 0)
        self._return_to: Point = (0, 0)
        self._moved_before_carry = False

    @property
    def active(self) -> bool:
        return self.state is not MoveState.IDLE

    # -- the drag -----------------------------------------------------------

    def press(self, cursor: Point, window: Point) -> bool:
        """The pointer went down on the window body.  True when a drag began."""
        if self.state is not MoveState.IDLE:
            return False
        self._grab(cursor, window)
        self.state = MoveState.DRAGGING
        return True

    def release(self) -> None:
        """The button came up: a drag ends where it is."""
        if self.state is MoveState.DRAGGING:
            self.state = MoveState.IDLE

    # -- the carry ----------------------------------------------------------

    def pick_up(self, cursor: Point, window: Point) -> bool:
        """The Move button was clicked.  True when the window is now being carried."""
        if self.state is not MoveState.IDLE:
            return False
        self._grab(cursor, window)
        self._return_to = window
        self._moved_before_carry = self.moved
        self.state = MoveState.CARRYING
        return True

    def put_down(self) -> None:
        """Left click: leave the window where it is."""
        if self.state is MoveState.CARRYING:
            self.state = MoveState.IDLE

    def put_back(self) -> Optional[Point]:
        """Right click: where the window was when it was picked up, or None if not carrying."""
        if self.state is not MoveState.CARRYING:
            return None
        self.state = MoveState.IDLE
        self.moved = self._moved_before_carry
        return self._return_to

    # -- both ---------------------------------------------------------------

    def follow(self, cursor: Point, size: Size) -> Optional[Point]:
        """Where the window should be for a pointer at ``cursor``, or None when idle."""
        if self.state is MoveState.IDLE:
            return None
        free = (
            self._grab_window[0] + cursor[0] - self._grab_cursor[0],
            self._grab_window[1] + cursor[1] - self._grab_cursor[1],
        )
        shown = clamp_to_desktop(free, size, self._monitors())
        if shown != self._grab_window:
            self.moved = True
        return shown

    def keep_in_view(self, window: Point, size: Size) -> Point:
        """``window`` clamped onto the desktop (the window grew, or a monitor went)."""
        return clamp_to_desktop(window, size, self._monitors())

    def _grab(self, cursor: Point, window: Point) -> None:
        self._grab_cursor = cursor
        self._grab_window = window
