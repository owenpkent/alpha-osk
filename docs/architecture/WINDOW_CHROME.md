# Window chrome: corners, title-bar menu, Move mode, magnetic edges

Moved verbatim from `CLAUDE.md` on 2026-09-20, which keeps a summary of the load-bearing rules. This is the full reasoning.

## Who rounds the window corners

Every window here is frameless, and four of them (the keyboard, the Snippets
and Symbols pickers, and the Dashboard) are `color: "transparent"`, which on
Windows makes them `WS_EX_LAYERED`. They used to round their own corners
with a `radius` on the QML background rectangle, which leaves the pixels
outside the arc unpainted, and **on a layered window those do not composite
the desktop the way a transparent pixel should: they come back white**. What
the user sees is a small bright notch biting into a corner of the keyboard,
appearing and disappearing depending on what happens to be behind it, which
is why it looks intermittent and unrelated to anything. (Settings and Help
are opaque `#1e1e1e` windows with a rounded panel inside; their corners show
the window's own dark colour rather than white, so they are left alone.)

Measured on the real `Main.qml`, against a magenta backdrop placed behind
all four corners:

| configuration | corner pixel |
|---|---|
| radius 10, Windows 11 default rounding | `#ffffff` |
| radius 10, `DWMWCP_DONOTROUND` | `#ffffff` |
| radius 0, `DWMWCP_ROUND` | the backdrop, correctly |

**Turning Windows' own rounding off fixes nothing, and that is the part
worth remembering**: the notch is the unpainted region, not the rounding.
The fix is to leave nothing unpainted. `Main.qml::selfRoundedCorners` is
false on Windows, which takes `windowRadius` to 0 for the background, the
title bar and the shadow, and
`windows_window.py::_prefer_dwm_rounded_corners` sets
`DWMWA_WINDOW_CORNER_PREFERENCE` to `DWMWCP_ROUND` so the compositor masks
an opaque window, which it antialiases properly.

Six things follow:

- **The title bar's radius has to follow the background's.** Rounding it
  while the background behind it is square swaps the notch for a lighter
  wedge in each top corner, since what shows through is then the background
  rather than the desktop. The Dashboard's header is the same case.
- **The three floating windows are the same shape and needed the same fix.**
  `SnippetsWindow`, `SymbolsWindow` and the Dashboard (`vizWindow`, whose
  `ModelVisualization` panel draws the background) are all transparent with
  a rounded background, so a fix reaching only the keyboard would have left
  the notch on the windows that float over whatever the user is typing into.
  The Dashboard was in fact missed by the first version. They take
  `selfRoundedCorners` as a required property from `Main.qml` rather than
  each reading `Qt.platform.os`, so the rule is stated once, and
  `keyboard_app.py::_wire_floating_windows` names all three so the DWM call
  reaches them once they are shown. The Dashboard gets only that call: it is
  allowed to take focus (it has no keys on it), so it must not go through
  `apply_extended_styles` and pick up `WS_EX_NOACTIVATE` with the corner.
- **The DWM call runs before the style writes, not after.** Each of those
  returns early on failure and is logged, and the corner needs nothing they
  compute, so it must not be lost with them.
- **The DWM call is best-effort and must stay that way.**
  `DWMWA_WINDOW_CORNER_PREFERENCE` is Windows 11 and later; on Windows 10 it
  fails and the window keeps the square corners QML gave it, which is what
  every other window on that desktop looks like. A window that fails to
  round is cosmetic, never a reason to fail startup.
- **The screenshot script takes the rounding back.** `Qt.platform.os` still
  reads `"windows"` under the offscreen plugin, but there is no compositor
  behind it, so `scripts/capture_screenshots.py` sets `selfRoundedCorners`
  back to true after loading `Main.qml` or every screenshot regenerated on
  the Windows dev machine comes out with hard square corners. That is why
  the property is not `readonly`.
- **The offscreen render was correct the whole time the bug was on screen.**
  `assets/screenshots/dark-theme-keyboard.png` had a properly antialiased
  alpha-0 corner while the live window showed the notch, so a test that
  rendered the corner and looked at it would have passed against the bug.
  `tests/test_window_corners.py` therefore pins the checkable half (which
  side is asked to round, that every transparent window agrees, what the DWM
  call asks for and that it cannot raise) and says in its docstring why it
  cannot pin the rest.

Not yet checked on a real desktop: the background keeps its 1 px theme
border while its radius is 0, so along the corner arc DWM's mask clips that
border and draws its own. If that reads wrong on a light theme, the answer
is `DWMWA_BORDER_COLOR`, not a radius on the QML side.

## Title-bar window menu, and click-free Move

Right-clicking the title bar opens the menu a real window's caption strip
gives you: **Move**, **Minimize**, **Tuck away / Bring back** (X11 only),
**Close**. The keyboard is frameless and `WS_EX_NOACTIVATE`, so it has no OS
system menu and no gesture that reaches one (Alt+Space wants the focus we
deliberately never take). Everything lives in `qml/Main.qml`; guarded by
`tests/test_qml_window_menu.py`.

Three of the four entries also have a caption button a few pixels away, and
that is the point rather than a redundancy: those are 28x24 targets bunched at
the far right end of the bar, and the menu puts the same actions under the
pointer wherever it already is on the strip the user grabs the window by.

- **`titleBarMenuArea` is declared *before* every other input-taking child of
  `titleBar` and accepts only `Qt.RightButton`.** Only the corner-rounding
  Rectangle sits above it in the file, and that accepts nothing. Being first
  puts it underneath
  everything, and taking only the right button means it consumes nothing else:
  a left press still reaches `dragArea` and the caption buttons above it,
  while a right press finds no taker up there and falls through. That is what
  makes the *whole* strip a menu target, buttons and the gaps between them
  included, rather than only the region `dragArea` covers (which stops 332 px
  short of the right edge). The failure mode to avoid is declaring it on top:
  it would silently kill dragging the window. `dragArea` shields it well
  enough that "a left press does not open the menu" is not a falsifiable test,
  so the guard is
  `TestRightClickingTheTitleBarOpensTheMenu::test_a_left_drag_on_the_strip_still_moves_the_window`,
  which presses, travels and asserts the window followed.
- **Rows come from a model (`windowMenu.actions`), not four near-identical
  blocks**, and are **word-only, no icons**: any glyph small enough to sit in a
  menu row is at the mercy of the host emoji font, which on Windows renders in
  colour and ignores the ink it is given (same reason as the lock badge and the
  clear-context ring). The Tuck row is built unconditionally and **collapses to
  zero height** off X11, because a row for something that cannot happen is
  worse than no row. **Close carries a rule and a 9 px gap above it**: it is
  the one row that ends the session, there is no undo, and it must not sit
  flush against Minimize under an imprecise pointer.

### Move mode

**The one entry with no button behind it, and the reason the menu is worth
having.** Dragging the title bar means holding the button down for the whole
travel, which is the single gesture this keyboard's user cannot reliably make
(the same argument that removed swipe typing). Move mode splits it into two
taps with a free hand in between: pick the window up, move it, put it down.

- **The window follows the pointer by `(current - anchor)` in the overlay's own
  coordinates, and that is self-correcting**: the window slides out from under
  the pointer by exactly the delta, which puts the pointer back on the anchor
  and makes the next delta zero. It converges instead of running away, and it
  needs no global coordinates. A sub-pixel delta is left to accumulate rather
  than rounded away, or the same delta stays pending on every later event.
- **The anchor is dropped on `onExited` as well as on open.** A fast flick can
  outrun the window it is dragging; measuring the way back in against the
  anchor the excursion started from teleports the window by however far the
  pointer went while it was away.
- **`windowMoveOverlay` covers the whole window and is `enabled: root.moveMode`,
  not merely hidden.** It has to swallow the click that ends the move, or that
  click lands on a key, a pill or the Close button.
- **Left puts it down, right puts it back** (`_moveReturnX/Y`). There is no
  Escape: this window never holds focus, so a physical Escape goes to the app
  behind us and the OSK's own Esc key is synthesised into that app too. The
  cancel is what makes the mode safe to try for someone who could not recover
  a window that landed somewhere unreachable, so don't drop it.
- A hint banner rides on the overlay saying which click does which. It is not
  garnish: a keyboard that has started following the pointer with nothing on
  screen to say why is alarming, and the mode has no other tell.
- The landing spot persists for free, since `onXChanged` / `onYChanged` already
  restart `saveGeometryTimer`.

### Magnetic edges

Both ways of moving the window (the title-bar drag and Move mode) pass their
proposed position through `Main.qml::snapWindowPos`, which pulls it flush to
a screen edge, or to the screen's horizontal centre, when it comes within
`snapThreshold` (24 px). *Settings -> Appearance -> Window -> Snap to Screen
Edges*, default ON. Landing a drag flush against an edge otherwise means
holding the button down for the whole travel and then releasing within a
pixel or two, which is the gesture this keyboard exists to avoid needing.

24 rather than the ~10 a mouse-driven desktop uses, for the same reason
`hitMarginH` exists: the pointer this forgives is slower and less accurate
than the one those defaults were chosen for.

Four things are load-bearing:

- **It snaps against `screenBoundsAt`, the screen the window is on**, never
  the primary one. A monitor to the left has negative coordinates a
  primary-screen calculation cannot express, which is the bug the snippets
  restore documents one window over.
- **The axes are decided independently**, so a keyboard flush on the bottom
  edge still slides freely along it.
- **Horizontal centre is a target and vertical centre is not.** Centring a
  wide, short keyboard is something people do; parking it halfway down the
  screen is not, and a snap nobody wanted reads as the window sticking for
  no reason.
- **The snapped value is never written back into whatever the caller
  accumulates, and that is what makes an edge magnetic rather than a trap.**
  Feed it back and every later delta is measured from the snap point, so a
  pointer moving inside the zone never builds up the travel it needs to
  leave and the window is stuck there for good. `dragArea` avoids that for
  free, because it recomputes its proposal from the press origin on every
  event rather than accumulating. `windowMoveOverlay` cannot: its whole
  design is accumulated deltas in its own coordinates, so it keeps an
  unsnapped shadow position (`freeX` / `freeY`) and shows the snapped version
  of it.

**Move mode also has to re-anchor by however far the window *actually*
went** (`anchorX = mouse.x - (root.x - beforeX)`). The self-correction that
makes the mode work at all rests on the window moving the whole delta, which
slides it out from under the pointer and puts the pointer back on the anchor;
while a snap is holding the window still it does not, so measuring the next
event against the old anchor counts the same travel again on every event and
the pointer leaves the zone in a fraction of the threshold. Compensating
keeps `anchor == pointer - window` true either way, so `dx` is the pointer's
own travel and nothing else.

Deliberately not extended to the Snippets and Symbols windows: those are
dragged clear of the field being filled in, which is a "not here" gesture
rather than a "exactly there" one, and they have no Move mode to share the
shadow-position machinery with. The left/right resize handles are untouched
too.

Guarded by `TestSnappingToScreenEdges` in `tests/test_qml_window_menu.py`,
where every positive is paired with the near-miss it must reject (a rule
that clamped every position to the nearest edge satisfies the snap
assertions perfectly and makes the window impossible to park anywhere else),
and where the escape-the-edge case walks out in 10 px steps rather than one
jump, because a single jump passes against the re-anchoring bug as well.
The multi-monitor half cannot be exercised headlessly, so every target is
derived from `screenBoundsAt` rather than `Screen.width` and the class
docstring says so. `_park` turns snapping **off**, so the file's existing
displacement assertions still measure the follow and nothing else.

**Testing note.** `tests/test_qml_window_menu.py` drives the pointer in
*desktop* coordinates, not window-local ones. In move mode the two are not
interchangeable: the window slides by exactly the delta, so the same local
point maps back to the same global point and Qt drops the second event as a
duplicate. Two other offscreen-plugin traps are pinned in that file: a window
parked at a negative x is reported 4 px adrift of where it was put (hence
`PARKED_X`/`PARKED_Y`), and a closed `Popup`'s rows all report
`visible: false`, so any assertion about which rows are showing has to open the
menu first or it passes against anything at all.
