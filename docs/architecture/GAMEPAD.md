# Gamepad (controller output)

Alpha-OSK can drive a virtual Xbox 360 controller, so the keys of a game
controller can be clicked the way the keys of a keyboard are. The
**Controller view** swaps the letter grid for a game pad. It is off by
default (*Appearance -> Panels -> Controller*), it needs a driver Alpha-OSK
does not install, and this first version is digital: no analog sticks yet.

The plan this is the first step of, and the decisions behind it, are in
[`../roadmap/NIMBUS_INTEGRATION.md`](../roadmap/NIMBUS_INTEGRATION.md).

## The pieces

| File | Role |
|------|------|
| `src/platform/padbus_client.py` | Standard-library `ctypes` client for the ViGEmBus driver's user-mode protocol. Plugs one Xbox 360 pad and submits its reports. Adopted from Nimbus. |
| `src/gamepad/state.py` | `PadState`: which controls are pressed or latched, and the `PadReport` that follows. Pure data. |
| `src/gamepad/device.py` | `PadDevice`, the two-method surface the rest sees (`apply`, `close`), its adapter over the client, and `pad_bus_available()`. |
| `src/gamepad/worker.py` | `PadWorker`: the one thread that owns the device. |
| `src/gamepad_bridge.py` | `GamepadBridge`: the QML-facing object, registered as the `gamepad` context property. |
| `qml/components/ControllerView.qml` | The view. Draws the controls and raises signals; `Main.qml` talks to `gamepad`. |

`KeyboardBridge` does not know any of this exists, and a test holds it to
that (`tests/test_gamepad_bridge.py`). The split is the one
`STRUCTURAL_REVIEW.md` 3.1 asks for and `TelemetryBridge` first made.

## The driver is detected, never installed

The pad needs ViGEmBus, a kernel driver. Alpha-OSK does not ship it, install
it or update it. `pad_bus_available()` says whether it is there, and without
it `GamepadBridge.setActive(True)` answers `False` and the status reads
`"unavailable"`. This is the dictation pattern: inert without the thing it
needs, and it says so.

ViGEmBus was archived by its author on 2023-11-02; 1.22.0 is its last
release. That is why this repo keeps it at arm's length. What becomes of the
driver in the long run is a question for the roadmap document, not this one.

## What a control is

The vocabulary is a closed set of lower-case ids, `state.CONTROL_IDS`:

- buttons `a b x y lb rb back start guide l3 r3` and
  `dpad_up dpad_down dpad_left dpad_right`
- triggers `lt rt`
- sixteen stick directions, `ls_n` through `rs_nw` (eight compass points
  for each stick)

An id outside the set changes nothing and is answered with a refusal. The
ids arrive from QML and, later, from an external switch scanner, so this is
an allow-list, as `key_actions` is.

Sticks and triggers are **digital** in this step: a direction is full
deflection (a unit vector, so a diagonal is not a corner) and a trigger is
all the way down. Analog control is a later phase.

## Press, latch, and the group rule

The rules are the keyboard's modifier idiom, carried over, and they live in
`PadState` so they can be tested without a controller:

- A control is down while it is **pressed** (a click being held, or a tap's
  minimum hold) or **latched** (it stays down until released).
- **Pressing a latched control lets go of the latch and presses nothing**,
  as a left tap on a locked Shift does.
- Some controls cannot be down together: the eight directions of one stick,
  and the two ends of one D-pad axis. Within such a group the most recent
  press wins while it lasts, and then the latched member (there is at most
  one) shows through again. Latching one member unlatches the others.

Pad latches are **not** modifiers. They never go through
`KeyboardBridge._release_sticky_modifiers`, and typing a letter must not
drop a held direction. Their one release site is `GamepadBridge`
(`releaseAll`, and every path that turns the pad off).

## The Controller view

The view takes the place of the letter grid's rows, at exactly their width
and summed height, so opening it never changes the window. The navigation
panel, the numpad, the function rows and compact's number row stay where
they are. The layout was chosen from a clickable mockup
(`docs/roadmap/controller-layout.html`, option A1).

**What is on it.** Triggers and bumpers along the top with Back, ABC and
Start between them; a stick at each end; Off, Guide, a D-pad and the four
face buttons in the middle; and two ordinary keys, Esc and Enter, because
games ask for them. Each stick is **eight direction keys around its stick
click** (`l3`, `r3`) until the analog stick replaces them.

**What a click does.**

- Left press: the control goes down, and comes up when the press ends.
- Right click: latch, with the lock bar a locked modifier shows. A left
  click lets go.
- **ABC** goes back to the letters and **leaves the pad plugged in with its
  latches** (the owner's decision, 2026-11-21: a game must not see a
  controller disconnect because the user stopped to type). The bar button
  then carries a dot and the accent colour.
- **Off** lets go of everything, unplugs the pad and goes back to the
  letters.

**Geometry.** The controls sit on a 15.5 by 5 unit design grid whatever the
real grid is; in compact view it scales into 13 units by four rows. With
`W`, `H` the view's size and `s`, `r` the grid's key and row gaps, a control
at design `(x, y, w, h)` is at `x * (W + s) / 15.5`, `y * (H + r) / 5` and is
`w * (W + s) / 15.5 - s` by `h * (H + r) / 5 - r`. The outermost key edges
land on the view's edges and every gap equals the grid's. It is a plain
`Item` with computed positions, never `QtQuick.Layouts`.

**The height is the rows' own.** `Main.qml::gridRowsHeight` restates how
tall the data-driven rows are (each ceiled to a pixel, the number row four
pixels shorter, `rowSpacing` between), because the rows are hidden while
the view shows and cannot be asked. If the rows' heights change and this
does not, `sectionHeight` moves when the view opens;
`tests/test_qml_controller_view.py` compares the two in the full, compact,
panels-on and function-row configurations.

**Rules that are easy to break.**

- The component only raises signals. `Main.qml` owns `gamepad`, as it owns
  every other context property. A pad control never calls `keyboard`: no
  sticky-modifier release, no prediction, no key preview. Esc and Enter are
  the exception and take the grid's own `pressSpecialKey` path.
- **A press must always end.** `KeyButton.keyReleased` fires on release,
  cancel and drag-off; the view also remembers what it pressed and lets go
  of it if it is hidden or destroyed mid-press. A right click presses
  nothing, so its release is ignored.
- **Held is not the same as drawn pressed.** `KeyButton`'s safety timer
  clears the *visual* press after five seconds, and a controller control is
  routinely held longer than that. `KeyButton._pressHeld` tracks the
  accepted pointer press by itself, so moving off a control still releases
  it after the visual has gone. Windows can drop the mouse release when the
  pointer leaves this non-activating window; drag-off is what ends the
  press then.
- **A pad control is not a toggle target** (`isToggleTarget: false`). Its
  latch is in the accessible description; see `UIA_TARGETS.md`, *The
  controller*.
- **An assistive Invoke is `tap()`, never `press()`**: it has no release to
  pair with (`KeyButton.pressFromPointer` is false).
- The view is disabled, and dimmed, until the status is `"on"`. A press the
  bridge would refuse must not look like a dead key.
- `controllerViewActive` is never saved. The keyboard starts on the letters.
- The bar button is scan index 6 and is counted in `predButtonCount`; it has
  a title-bar twin for when suggestions are off.
- **The title strip has a width floor.** `Main.qml::titleStripFloor` is the
  grip, a drag target's worth of strip, and the title controls' measured
  width, and `minimumWidth` is the larger of that and the keys' own
  minimum. Without it the twin pushed the Update button out of a
  minimum-width compact window. It is derived from `titleButtons.width`, so
  the next control needs no edit; a control appearing at the minimum widens
  the window through `fitWidth`, and nothing shrinks it back. With no
  optional title control showing, the minimum is what it always was.
  **Growth the floor forces stays on screen**: a window grows to the right,
  so `keepFloorGrowthOnScreen` pulls it back by what the growth pushed past
  the edge of the screen it was on, or keeps its right edge where the user
  left it if it was already past. A width the user sets is not moved.

**Not yet seen outside a test.** The view has been rendered offscreen and
driven headlessly over a fake pad, and the backend has been run against the
real driver. As of this writing the two have not been used together in the
running app or in a game.

## A tap has a minimum length

A game reads the pad once a frame, so a press that lasts a millisecond can
be missed. `GamepadBridge.release()` therefore holds a control down for at
least `MIN_PRESS_MS` (100 ms) after its press; an earlier release is
deferred, not dropped. `tap()` is press followed by release, so it gets the
same floor, and it is what a scanner's one-shot activation will call.

The 100 ms is **provisional**. Keys needed 50 ms (`_GAME_KEY_HOLD_SECONDS`);
the pad's number has not been measured against real games yet.

One consequence: a second press of a control whose deferred release is still
pending cancels that release, so two taps closer together than the minimum
reach the game as one longer press. That is the honest outcome of a floor;
splitting them would mean inventing a gap the user did not make.

**The floor is kept where the device receives the press, not where it is
asked for.** The bridge's clock starts when a press is submitted. If an
earlier write to the device was slow, the press and its release would both
be waiting in the worker's queue and go out microseconds apart, in order
but with no duration, and a game would miss the tap. So `PadWorker` keeps
its own record of when each control went down *at the device* (each button
bit, each trigger, each stick leaving centre) and will not apply a report
that brings one up sooner than `min_hold_s` after that. The bridge sets
`min_hold_s` from `MIN_PRESS_MS`. In the ordinary case the bridge's deferred
release has already spaced the two reports and the worker waits for nothing;
a report that only moves a control between two non-zero values is never
delayed; and turning the pad off interrupts a hold at once.

## One thread owns the device

Measured on the owner's machine against ViGEmBus 1.22.0 (2026-11-21):
plugging a pad took 109 ms on the first run after the driver was installed
and 16 ms on a later one, a report takes about 0.01 ms, and unplugging takes
1 ms. Through the whole stack (`GamepadBridge` to `XInputGetState`) a tap
was down at once and came up after 109 ms, a latch held through another
button's tap, and turning the pad off left no controller behind.

The plug can be too long for the Qt thread, and a
report's *failure* path is worse: the client retries and can re-plug, which
blocks for seconds. So:

- `PadWorker` opens the device on its own thread and applies reports there.
  Nothing on the Qt thread waits on it except `stop()`, which is bounded.
- **Reports are applied in order and none is skipped.** A tap is a press
  report and a release report 100 ms apart; a "latest wins" mailbox could
  merge them into nothing. The queue is bounded (`MAX_QUEUED_REPORTS`), and
  past the bound it drops the oldest and counts it.
- The worker reports `on`, `error` or `off` from its own thread;
  `GamepadBridge` brings that to the Qt thread through a queued signal and
  ignores a status from a worker it has already replaced.
- An error from the device ends the worker. There is no automatic retry:
  the bridge goes to `"error"`, lets go of everything and says so.
- **A worker is not forgotten until its pad is closed.** `stop()` waits a
  bounded two seconds, but the client's own recovery can sit in a re-plug
  for five. A worker still running after its stop is kept as *retiring*, and
  the bridge refuses to start another while one is (it says the controller
  is still disconnecting). Otherwise Off would leave the old pad connected,
  the next On would plug a second one, and a game would see two.
- **A cancelled pad can only send neutral.** `stop()` cancels the device
  before it joins. From then on `X360Pad` submits an all-zero report whatever
  it was holding and starts no new re-plug, so a recovery that finishes
  after Off cannot replay a button the user has already let go of. The
  worker reports `off` only after the pad has been closed.
- **"The pad's device is gone" is two error codes.** The client heals a
  missing child device by retrying and then re-plugging. It recognises
  `ERROR_NO_MORE_ITEMS` (259), seen on 1.21, and `ERROR_DEV_NOT_EXIST` (55),
  which is what ViGEmBus 1.22.0's `STATUS_DEVICE_DOES_NOT_EXIST` becomes.
  Any other error ends the worker at once.

## Nothing is left held down

Every way out sends a neutral report first: turning the pad off, an error,
and `shutdown()` on app quit (which `keyboard_app.py` calls before
`KeyboardBridge.shutdown()`). Closing the handle then unplugs the pad; the
bus owns a pad by file handle, so a crashed process cannot leave one behind.

## The pad exists only while it is wanted

Constructing `GamepadBridge` probes nothing and starts nothing. A controller
appearing changes how some games behave (prompts, input mode), so it is
never plugged at launch: the pad is plugged the first time the Controller
view is opened, and stays plugged while the letters show. It is unplugged
by the Off key, by turning the Controller setting off, when the keyboard is
minimized or hidden (a pad nobody can reach must not sit in front of a game
with a direction held), on a device error, and on quit.

## Tests never touch a real bus

The driver may be installed on a developer's machine, and a plugged pad is
visible to every program on it. `tests/conftest.py` therefore makes
`padbus_client.open_bus` raise and `find_bus` return nothing, per test and
for the session (`_no_real_pad_bus`, `_no_real_pad_bus_anywhere`). A test
that needs a bus puts its own fake over the guard. Do not weaken the guard
to make a test pass.

| Test file | Covers |
|-----------|--------|
| `tests/test_padbus_client.py` | Protocol constants, packing, and `X360Pad` over a fake bus handle |
| `tests/test_gamepad_state.py` | The press, latch and group rules, with property tests |
| `tests/test_gamepad_device.py` | The adapter, and the availability probe never raising |
| `tests/test_gamepad_worker.py` | Ordering, the neutral report on the way out, both failure paths |
| `tests/test_gamepad_bridge.py` | The slots, the minimum hold, status handling, the bridge split |
| `tests/test_qml_controller_view.py` | The view in the real `Main.qml`: size unchanged, geometry, press and latch, ABC and Off, scan targets |

## Not here yet

- Analog sticks and triggers.
- Latching through UI Automation. A scanner's Invoke taps; it cannot hold a
  control down (`UIA_TARGETS.md`, *The controller*).
- A measured tap length, and any measurement in a real game.
- Linux and macOS. The client imports cleanly there and reports no bus.
