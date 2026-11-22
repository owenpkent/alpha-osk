# Gamepad (controller output)

Alpha-OSK can drive a virtual Xbox 360 controller, so the keys of a game
controller can be clicked the way the keys of a keyboard are. This page
covers the backend. **Nothing in the UI uses it yet**: the Controller view
is the next piece of work, and until it lands the pad is never plugged.

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
but with no duration, and a game would miss the tap. So the bridge tells the
worker which report ends which press. `PadWorker.submit()` numbers every
report; the bridge remembers the number of the report that put a control
down and submits the report that lets it up with `after=<that number>`. The
worker records when each report reached the device and will not apply one
sooner than `min_hold_s` after the report it is `after`. The bridge sets
`min_hold_s` from `MIN_PRESS_MS`.

It is an explicit link on purpose. An earlier version had the worker guess
presses and releases from what a report contained, and it could not see a
direction tapped over a latched one (east held, north tapped: the stick is
non-zero throughout, so nothing looked like a release, and north reached the
device for nine microseconds). A report with no `after` is never delayed,
whatever it contains, which is what keeps a continuous analog stick and a
new press immediate. In the ordinary case the bridge's deferred release has
already spaced the two reports and the worker waits for nothing; turning
the pad off interrupts a hold at once.

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
  it was holding, so a recovery that finishes after Off cannot replay a
  button the user has already let go of. **Off never connects another
  controller either**: cancellation is checked after the old pad's unplug
  and at every step of trying a replacement, and a candidate already
  plugged when the cancel lands is unplugged again. The
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

Constructing `GamepadBridge` probes nothing and starts nothing. The pad is
plugged by `setActive(True)` and unplugged by `setActive(False)`, and
nothing calls either yet. A controller appearing changes how some games
behave (prompts, input mode), so it is never plugged at launch.

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

## Not here yet

- Any UI. No key, button or setting reaches this code.
- Analog sticks and triggers.
- Scan targets for pad controls (`UIA_TARGETS.md` will gain a `pad` section
  with the view).
- Linux and macOS. The client imports cleanly there and reports no bus.
