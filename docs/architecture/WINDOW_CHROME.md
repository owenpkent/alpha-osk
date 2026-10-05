# Window chrome: corners, taskbar button, title-bar menu, Move mode, magnetic edges

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

## The taskbar button appears on launch (hide, restyle, re-show)

Reported as the taskbar icon "not fully inflating until you click it". The
right style bits were not the whole answer: the shell decides whether a
window gets a taskbar button **at the moment it becomes visible**, and the
keyboard becomes visible from QML's `visible: true` before
`apply_extended_styles` runs, so the shell files it as a tool window and
never looks again. Measured on the installed build four seconds after
launch: `APPWINDOW` set, `TOOLWINDOW` clear, and no running-window button
at all, only the 66 px pinned stub with no label and no running dot, until
a click on that stub activated the window.

- MSDN's rule for changing a visible window's taskbar presence is hide,
  change the style, show. `apply_extended_styles` does exactly that on the
  `taskbar_button` path: `ShowWindow(SW_HIDE)` before the style writes,
  `ShowWindow(SW_SHOWNOACTIVATE)` after the `SWP_FRAMECHANGED` flush.
  Proven from outside first: that pair on the running keyboard, with no
  style change at all, attached it as "Alpha-OSK - 1 running window" at
  once and left the foreground alone.
- **The re-show is in a `finally`**, because every early return in the
  style writes now happens with the keyboard hidden, and a keyboard that
  vanishes at launch is worse than the bug. It is `SW_SHOWNOACTIVATE`,
  never `SW_SHOW` / `SW_SHOWNORMAL`, which take the foreground.
- A window that was not visible is left alone, and the floating windows
  (`taskbar_button=False`) are never blinked: they must not have a button.
- The offscreen suite cannot see the shell, so `tests/test_windows_window.py::
  TestTheTaskbarButtonAppearsOnLaunch` pins the call order and the failure
  paths; the live check is a UI Automation walk of `Shell_TrayWnd` for a
  button named `Alpha-OSK - 1 running window` (as opposed to the pinned
  stub) a few seconds after launch, with no click.

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
  included, rather than only the region `dragArea` covers (which stops at the
  button row's left edge). The failure mode to avoid is declaring it on top:
  it would silently kill dragging the window. `dragArea` shields it well
  enough that "a left press does not open the menu" is not a falsifiable test,
  so the guard is
  `TestRightClickingTheTitleBarOpensTheMenu::test_a_left_drag_on_the_strip_still_moves_the_window`,
  which presses, travels and asserts the window followed.
- **`dragArea` reserves the button row's measured width, never a constant.**
  It reserved a hard-coded 332 px for a row that measures 198 px plus its
  margin in a typical session, which left a 126 px band between the grip
  region and the first button that dragged nothing, and on the 812 px
  compact window that was a sixth of the strip (reported as "the full title
  bar on compact is not draggable"). The margin is bound to
  `titleButtons.width`, and `Row` lays out only visible children, so the
  suggestion-bar mirrors and the X11-only Tuck button come and go without a
  matching edit. Guarded by
  `tests/test_qml_window_menu.py::TestTheWholeStripDrags`, which drags from
  a point inside the old reserve on the compact window and is paired with a
  press on the Learning switch that must toggle it rather than move the
  window.
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

## Notifications and taskbar previews draw over the keyboard

Reported on 1.6.0 as "Slack notifications go under the keyboard, and window
previews". The cause is UIAccess itself, which 1.6.0 was the first build to
actually run with. A UIAccess process's always-on-top window lives in the
`ZBID_UIACCESS` Z-order band, above the notification band (toasts, which is
how Slack notifies) and above Explorer's own topmost windows (taskbar
previews). Windows' `osk.exe` sits in the same band and covers them the same
way.

**There is no "topmost but in the ordinary band" state to settle into.** A
signed UIAccess probe installed under Program Files measured it on
2026-10-04 (`GetWindowBand`, 1 = `ZBID_DESKTOP`, 2 = `ZBID_UIACCESS`):

| Window | Band |
|--------|------|
| `CreateWindowEx` with `WS_EX_TOPMOST` | 2 |
| `CreateWindowEx` plain | 1 |
| ... then `SetWindowPos(HWND_TOPMOST)` | 2 |
| a topmost one after `SetWindowPos(HWND_NOTOPMOST)` | 1 |
| ... and `HWND_TOPMOST` again | 2 |
| `CreateWindowInBand(ZBID_DESKTOP)` with `WS_EX_TOPMOST` | 1 |
| ... then `SetWindowPos(HWND_TOPMOST)` | 2 |

The band follows topmost-ness both ways. The one exception (created in
`ZBID_DESKTOP` with the style already set) is undone by the next
`HWND_TOPMOST`, which Qt issues on its own, and Qt creates its windows with
`CreateWindowEx` anyway. Giving UIAccess up would cost typing into elevated
windows, which is what 1.6.0 shipped to fix.

**So the keyboard steps aside.** `windows_window.ShellPopupYielder` listens to
out-of-context WinEvents (show / hide / destroy / cloak / uncloak, filtered to
whole windows) and, while one recognised popup is on screen, makes every
visible always-on-top window of ours `HWND_NOTOPMOST`. That leaves the keyboard
above every ordinary application but below the shell's topmost windows. When
the last popup goes it makes them topmost again. Both passes walk the windows
in one order, keyboard first (`keyboard_app._always_on_top_windows`), so an
open picker stays above the keyboard rather than being buried under it.

What counts (`is_shell_popup`), measured on Windows 11 24H2 with a WinEvent
recorder:

- **A toast** is ShellExperienceHost's `Windows.UI.Core.CoreWindow` in
  `ZBID_IMMERSIVE_NOTIFICATION` (4). It is *uncloaked* to show and *cloaked* to
  dismiss, and the same window is reused for the next toast, which is why the
  cloak events are hooked and why a snapshot of "new visible windows" never
  saw it. The class alone is not enough: every store app's window is a
  `CoreWindow`, so the band is what marks a toast. The title ("New
  notification") is localised and is not used.
- **Taskbar previews** are hosted in Explorer's full-screen
  `XamlExplorerHostIslandWindow`, shown when the pointer reaches a taskbar
  button and hidden when it leaves the taskbar. The per-button
  `Xaml_WindowedPopupClass` popups (owned by `Shell_TrayWnd`) live inside
  that session, so the host alone covers them. `TaskListThumbnailWnd` is the
  Windows 10 preview window.

**The demotion has to be held, not fired once.** `HWND_NOTOPMOST` places a
window above the ordinary ones at the moment of the call and nothing more: the
next application the user activates, or that raises a window of its own, goes
above it, and the keyboard never takes focus so it cannot climb back by being
clicked. Review reproduced it with the real yielder and two hidden windows: the
keyboard was above the application right after yielding, and below it, for as
long as the toast stayed up, after the application was raised with
`SetWindowPos(HWND_TOP, SWP_NOACTIVATE)`. A maximised application would hide
every key for the life of a notification. So `EVENT_SYSTEM_FOREGROUND` is hooked
too, and while stepped aside every foreground change (and every poll tick, for
a window raised without activation, which fires nothing we hook) raises our
windows to `HWND_TOP` without activating them, keyboard first. Within the
ordinary group that is above every application and still below the shell's
popups. On top, the event is ignored: topmost already beats everything.

A 150 ms restore delay keeps a toast replaced by the next one, or the pointer
sliding between taskbar buttons, from flickering the Z-order, and a 1 s poll
while stepped aside drops a popup that is gone, hidden or cloaked without its
event having arrived.

**A picker opened during the yield is demoted too.** The first held version
only ever re-raised with `HWND_TOP`, which moves a window within its band and
never out of one. A picker (Snippets, Symbols, the study window) opened while
a toast was up came up topmost, because `_wire_floating_windows` runs
`apply_extended_styles` on every show and that call asks for `HWND_TOPMOST`;
its show event is not a shell popup's, so the yielder ignored it, and the next
re-raise left it where it was: over the notification, for the rest of the
notification's life. Review reproduced it by adding a topmost picker during an
active toast and watching `WS_EX_TOPMOST` survive the poll. The yielder now
keeps the set of windows it has demoted in this yield; on every re-raise
(foreground change or poll) a window of ours that is not in the set is
demoted before it is raised, and the floating-window wiring reports each
picker's show to `ShellPopupYielder.window_shown` right after styling it, so
the demotion lands in the same breath rather than up to a second later. The
set is cleared on restore, so a later yield starts from scratch. The yielder
is installed before the floating windows are wired, which is what lets the
wiring be handed its hook.

**Partial hook registration is rolled back.** The three `SetWinEventHook`
calls share one ctypes callback, and that callback is a local of the installer
until it is pinned to the yielder on success. The first version returned `None`
when any hook failed and left the ones that had taken registered, so the
callback was collected while Windows still held its function pointer (a hook
is only dropped with its thread, never by returning from the function), and
the next window shown anywhere on the desktop would have called freed memory.
Every hook that takes is now unhooked if a later one fails or anything in the
installer raises, before the callback can go out of scope.
`tests/test_shell_popup_yield.py::TestInstallingTheHooks` drives the real
registration path against a fake `user32` for each partial-success order.

The live check that does not need a signed build: a throwaway always-on-top
`QWindow` with the yield installed, a real toast fired from PowerShell, and
`WS_EX_TOPMOST` sampled every 50 ms. It went `False` 0.3 s after the toast and
`True` again when the toast was dismissed. Tests: `tests/test_shell_popup_yield.py`.

## Always on Top (Appearance -> Window, default ON)

With the setting on, nothing differs from before. With it off the keyboard is an ordinary-band window that other apps can cover.

- **The Z-band is changed natively, never through `setFlags`.** `setFlags` on a shown window makes Qt rebuild the native window, which drops the extended styles and moves the window. Both flag sets keep `WindowStaysOnTopHint`; only the band moves: `SetWindowPos(HWND_TOPMOST / HWND_NOTOPMOST, SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)` on Windows, an EWMH `_NET_WM_STATE` client message (`_NET_WM_STATE_ABOVE`, add or remove) to the root window on X11 (a logged no-op on Wayland), `setLevel_` floating vs normal on macOS (untested on a real Mac). All of it sits behind `src/platform/window_band.py::set_keyboard_topmost`, dispatched like `_apply_window_flags`.
- **Startup ends in the saved state.** QML pushes the saved value into the bridge in `Component.onCompleted`, which runs before `keyboard_app` writes the window styles, so `_apply_window_flags(root, bridge.alwaysOnTop)` hands it to `apply_extended_styles(topmost=...)` and the one `SetWindowPos` there asks for the right band. Otherwise a user who turned it off would start topmost and be corrected later. The bridge only holds the value and emits `alwaysOnTopChanged`; `keyboard_app` owns the window.
- **Press-to-raise.** The keyboard never takes focus, so clicking it does not raise it, and a buried keyboard would be unreachable. With the setting off, `RaiseOnPressFilter` (an event filter on the root window) raises it on `MouseButtonPress` with `windows_window.bring_to_front_noactivate`: `HWND_TOPMOST` then `HWND_NOTOPMOST`, both `SWP_NOACTIVATE` (X11 `XRaiseWindow`, macOS `orderFront_`). **A plain `HWND_TOP` does not work here, measured live**: Windows ignores a background process's request to stack a window above the foreground app, and a click on a `WS_EX_NOACTIVATE` window does not make our process the foreground one, so the keyboard stayed buried (the tray icon raised it, because a tray click does grant the foreground). Passing through the topmost band is not subject to that rule and ends at the top of the ordinary band. The shell-popup yielder keeps `HWND_TOP`, since the momentary topmost step would flash the keyboard over the notification it is yielding to. It always returns False: the press still reaches the key, with no added latency. `QuietRestoreFilter` re-applies the band (and raises, when off) after a restore, in case Qt re-asserts topmost on show. **On X11 Qt does re-assert it, on every show**: `QXcbWindow::show()` writes `_NET_WM_STATE_ABOVE` from the retained flag onto the still-unmapped window, so hiding a tucked keyboard from the tray and restoring it put it back on top with the setting off. `window_band.ReassertOnExposeFilter` is the X11 counterpart: it re-applies the saved band on each unexposed-to-exposed transition, one event-loop turn later. Exposure rather than show, because a `_NET_WM_STATE` client message counts only once the window manager has mapped the window, and Qt reports exposure only after that. Nothing calls `SetForegroundWindow` or `makeKey`.
- **Interplay with the shell-popup yield.** `ShellPopupYielder` evaluates its window set live through `_always_on_top_windows(root, keyboard_on_top)`. Because the keyboard keeps the Qt flag when off, the flag cannot select it; the setting is consulted on every call, so while off the keyboard is neither stepped aside nor put back into the topmost band. Turning the setting on mid-aside defers the native call (`_apply_always_on_top`): the yielder's restore now sees the keyboard and topmosts it when the popup goes, instead of the keyboard jumping over the popup.
- Scope: only the main keyboard window. The Snippets, Symbols and Study pickers and the settings, help and dashboard windows stay topmost, **and that takes work, because they are the keyboard's owned windows.** They are declared inside the keyboard's QML tree, so they are its transient children and on Windows its owned windows, and Win32 moves owned windows with their owner: `HWND_NOTOPMOST` on the keyboard makes every owned window non-topmost, `HWND_TOPMOST` makes them topmost. So turning the setting off took an open picker out of the band with it, and every press-to-front (topmost, then not) did it again to a picker that had been put back. Review reproduced it with two nested QML windows and this code's own helpers. A non-topmost owner may own a topmost window, so `keyboard_app._restore_floating_bands` simply re-topmosts each visible floating window (`_floating_on_top_windows`, the yielder's set minus the keyboard) **after** the keyboard's own move, owner first or the demotion would take them along again. The three paths that move the keyboard out of the band (the setting going off, press-to-front through `_raise_keyboard_keeping_pickers`, the post-restore reapply) all end there. Not while the yielder has them stepped aside: the ordinary band is where they belong during a notification and an owned window already sits above its owner there; the yielder's restore puts them back. **A hidden window is taken along too, and repaired when it is shown, not before**: `_restore_floating_bands` visits only visible windows, so every floating window, Settings, Help and the Dashboard included, goes back in the topmost band on its own show and only then reports to the yielder (`keyboard_app._floating_window_styles`, the table `_wire_floating_windows` connects). Without that a Settings window closed and reopened after one keyboard click came back coverable, which review reproduced on a real owned window. The mocked-handle tests cannot see any of this, which is why `tests/test_window_band.py::TestThePickersKeepTheirBand` fakes the propagation.
- Tests: `tests/test_window_band.py`, and the round trip in `tests/test_qml_window_menu.py`.
