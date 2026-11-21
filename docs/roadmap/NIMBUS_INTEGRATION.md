# Absorbing Nimbus: one app for typing and playing

**Status:** plan, written 2026-11-21. Nothing here is built. The end state
was chosen by the owner on that day; the phases, and every design marked
*open*, are proposals.

It was written from a read of Nimbus (`W:\dev\Nimbus-Adaptive-Controller`) at
commit `bcbf5dc` (2026-09-15) and of this repo at `730c165`. Driver facts
were checked against the upstream pages on the same day (*Sources*).

## The end state

**One app.** Alpha-OSK grows a native game controller and, in time, does
everything Nimbus does: sticks, triggers, buttons, shaping, layouts, profiles
and Game Mode. This is the path dictation took with MacroVox: the features
are rebuilt here to this repo's conventions, not embedded, and the sibling
app is left as it is until the owner decides its future.

Three reasons it is the right end state, all from Nimbus's own documents:

- **Nimbus's player has no keyboard.** Its problem statement says so in
  terms: everything goes through one pointer, so a hotkey "is not a recovery
  path for this player". In one app the keyboard is on screen already.
- **Nimbus's roadmap was walking toward us.** Its long-term list is keyboard
  output, voice and AAC. Alpha-OSK has shipped the first two.
- **Half of Nimbus is plumbing this repo already owns**: a window that never
  takes focus, always-on-top handling, an updater, opt-in telemetry, signed
  installers, themes, settings. None of that is ported; only the controller
  is.

## What Nimbus is, measured

| Part | Size | What it is |
|------|------|------------|
| Pad output, XInput | `padbus_client.py`, 729 lines | Standard-library `ctypes` client for the ViGEmBus protocol. No Qt. Unreleased in Nimbus, but measured against a real bus. |
| Pad output, DirectInput | `vjoy_interface.py`, 464 | vJoy through `pyvjoy` (x86 DLL only; exits the process if the DLL will not load). 8 axes, 128 buttons. |
| Shaping | about 120 lines in `config.py` | Dead zone, sensitivity curve, anti-deadzone, extremity, precision gain: pure functions on a magnitude. Tremor filter: a per-widget moving average. No numpy. |
| Widgets | `DraggableWidget.qml`, 987 | Joystick, button, slider or trigger, D-pad, steering wheel. |
| Macro stick | `MacroEditorDialog.qml`, 694 | A stick whose eight zones fire buttons, axes or turbo. There are no recorded macros. |
| Layout editor | `CustomLayout.qml` 1879, `WidgetPalette.qml` 660 | A free canvas: drag, resize, snap. |
| Profiles | `profile_repository.py` 154, plus `config.py` | JSON per layout in `%APPDATA%\ProjectNimbus`. |
| Game Mode | `mouse_hider.py` 699, `borderless.py` 969 | See *Game Mode* below. |
| The bridge | `bridge.py`, 3037 | Everything above wired to QML, on the GUI thread. |
| Accounts, cloud sync, telemetry, updater | about 1800 | Supabase sign-in, profile sync, Sentry. Added after the last tag. |
| Research | `driver/`, `netpad/`, `vm/`, `spectator/`, Linux `uinput` | A kernel mouse filter (test-signed), a pad over UDP, a Hyper-V track, scripted assists with no UI, and an unreleased Linux backend. |

How a stick is driven there today: press and drag, or triple-click to lock
the pointer (it is confined with `ClipCursor`, hidden, and warped back to the
stick's centre after every movement), then triple-click again to leave.

## What carries over, and how

| Nimbus | Here | How |
|--------|------|-----|
| `padbus_client.py` | **Taken whole** | Vendored as `src/platform/padbus_client.py`, source commit noted. The one file that is a library already. |
| Shaping maths | **Ported** | About 120 lines into `src/gamepad/shaping.py`, with Nimbus's property checks as the tests. Defaults and ranges kept, so a Nimbus profile means the same thing here. |
| Buttons, D-pad, triggers | **Rebuilt** | On `KeyButton`: tap and right-click latch, the modifier idiom. |
| Joystick, wheel, slider | **Redesigned** | Drag-free. See *The stick*. |
| Macro stick zones | **Rebuilt** | As actions in `key_actions.py` (a `pad` type), so one vocabulary serves function keys, pad keys and zones. |
| Free-canvas editor | **Not ported** | Controller layouts become layout JSON on the unit grid, edited by the layout editor `MODULAR_LAYOUTS.md` already plans. One editor, built once. |
| Profiles | **Converted** | An importer from Nimbus profile JSON to a grid layout, written to the import-hardening rules. Per-game switching is `MODULAR_LAYOUTS.md` Level 4. |
| Game Focus Mode | **Already here** | `WS_EX_NOACTIVATE`, stronger: this window is `uiAccess`-signed and installed where that works. |
| Always on Top, updater, telemetry, settings | **Already here** | Nothing to port. |
| Game Mode | **Redesigned** | In a helper process. See *Game Mode*. |
| vJoy | **Open** | Only if a real use needs DirectInput (flight sims, more than 14 buttons). It would need its own `ctypes` client; `pyvjoy` is not an option. |
| Accounts and cloud sync | **Not ported** | Alpha-OSK has no accounts. Profiles travel in the Data Backup archive instead. |
| Sentry, Nimbus telemetry | **Not ported** | Telemetry here is ten named fields. A pad metric would be a payload change, which needs alignment first. |
| Kernel mouse filter, netpad, vm | **Not ported** | Research. Its own README records the filter disabling the only mouse on 2026-09-07. |
| Spectator assists | **Not ported** | Harness-only in Nimbus, with no UI. |
| Linux `uinput` | **Later** | A second backend behind the same pad interface. macOS has no equivalent. |

Nothing is embedded. Nimbus's bridge installs a process-wide native event
filter and owns its `QApplication`; hosting it would put that in front of
every key click.

## The three hard problems

### 1. The stick (shape decided, needs a prototype)

A stick held by a mouse button is a sustained precise drag, the one gesture
this keyboard's users cannot be asked to make. Nimbus's answer is a
triple-click and a captured pointer. Neither belongs here.

The proposal is **Move mode's idiom**, which this repo already ships and
tests: tap the stick to pick it up, the pointer deflects it with no button
held, tap again to put it down.

| Question | Starting position |
|----------|-------------------|
| Deflection | Absolute: the pointer's offset from where the stick was picked up, full deflection at `travel_px` (Nimbus's field, kept). |
| Putting down | Left tap: release and centre. Right tap: hold the current deflection (a latch, with the `lockBar` cue). |
| While it is held | An overlay over our window, as `windowMoveOverlay` does, so the ending click can hit no key. |
| The pointer leaving our window | **Decided 2026-11-21: it does not leave.** While a stick is held the pointer is confined to the stick's travel area (the square of `travel_px` around where it was picked up), so it cannot wander onto the game and full deflection is a wall, not a guess. See *Confining the pointer* below. |
| Where motion is read | A timer reading the pointer position, not a hook and not a capture. |
| Two sticks, one pointer | One stick is held at a time; the other keeps its latch. |
| Rate | Nimbus submits on every mouse event, on the GUI thread. Here reports go out on a fixed tick off the Qt thread, as key events go through `SerialInjector`. |

#### Confining the pointer

The owner's words were "ideally mouse would stay in the area while held", so this is the target, and the
prototype has to show it holds. On Windows that is `ClipCursor` with a small rectangle, which is a confinement
and not a hook: no pointer event is intercepted or swallowed. What the prototype must settle:

- **Games set a clip of their own**, and Windows drops ours on a foreground change. The tick that reads the
  pointer re-applies the rectangle while a stick is held.
- **Release cannot depend on the Qt thread.** A stalled keyboard with the pointer boxed into a few hundred
  pixels is the failure this plan must not have. The clip is released on put-down, on shutdown, and by a
  watchdog off the Qt thread when the window stops answering. What Windows does with a clip whose process
  has died is to be measured, not assumed.
- **If confinement cannot be made reliable** in some game, the fallback is that the stick keeps following a
  free pointer. Dropping the stick when the pointer leaves the window was the third option and was not chosen.
- **Linux and macOS** need their own answer, which has not been looked at.

The rule under *Game Mode* below stands: this confines the pointer to our own controls for as long as one is
held, and nothing here withholds it from the user.

Triggers and sliders are the one-dimensional case of the same thing. Every
analog control also has a digital fallback (eight directions, full or
nothing), because that is what a switch scanner can reach.

### 2. Game Mode and the single pointer

The pointer is the only input, and the game wants it too. What Nimbus's Game
Mode does about that, mechanically:

1. Never takes focus when clicked. *Already true here.*
2. Sends a small circular stick movement at 30 Hz so the game stays in
   controller mode and lets the cursor go.
3. Runs a low-level mouse hook that swallows pointer movement over the game
   window and passes it over its own.
4. Releases the game's cursor clip every 2 ms, and can convert the game to
   borderless.

What that does not solve, by Nimbus's own measurement: a game reading Raw
Input still sees the mouse (Elden Ring's camera turned with every event
swallowed), and its input-ownership document says the repo has "no complete
answer" for an ordinary pointer on Windows. **Absorbing Nimbus inherits that
problem unchanged.** It is not made worse, and one thing improves: recovery
no longer needs a keyboard the player does not have.

The rule proposed for this repo: **nothing that withholds or redirects the
pointer runs inside the keyboard process.** The owner types with this
window; a stalled hook in it is a frozen mouse. Steps 3 and 4 go in a small
helper executable with a watchdog that releases everything when the keyboard
stops answering, built the way the update helper is (its own exe, its own
manifest, its own build guard). Step 2 is ordinary pad output and stays in
process.

### 3. The driver

The XInput path needs ViGEmBus, a kernel driver that its author archived on
2023-11-02 (last release 1.22.0, BSD-3-Clause, signed binaries still install
on Windows 10 and 11, no successor named). Today that is Nimbus's problem.
In the end state it is this repo's:

- **Until parity:** detect, never install. The controller is absent when the
  bus is, and says where the driver comes from.
- **At parity:** the installer has to offer the driver, as Nimbus's does.
  That is a change to the build and signing pipeline and gets its own
  alignment.
- **The long hedge** is Nimbus's `PAD_BUS_FORK_PLAN.md`: fork the bus, and do
  not ship the fork until the anti-cheat question is answered with evidence.
  The vendored client already probes a list of buses, so a second driver is
  one more entry. That plan moves here with the feature.

**ViGEmBus was not installed on the owner's machine** when this was written
(checked 2026-11-21: `bus_present()` was false). The owner approved
installing it the same day, for the phase 0 measurements.

## Where the controller lives (decided: the Controller view)

A controller does not fit beside a 15.5u grid, and a game rarely wants the
letter keys and the sticks in the same second. The owner chose the
**Controller view** on 2026-11-21: the window swaps the letter grid for the pad, with one
key to swap back, the way compact's `?123` layer swaps a page. Layers are
already a QML-side concept the backend never sees.

The alternatives are drawn beside it in
[`controller-layout.html`](controller-layout.html), four clickable replicas
at one key size (open the file locally):

| | Placement | Window on a 1920 x 1080 screen | Stick | Letters and pad together |
|---|-----------|-------------------------------|-------|--------------------------|
| A | Controller view (**chosen**) | 962 px wide, 18.4 % covered, as today | 212 px | No, one key swaps |
| B | Two halves beside the letters | 1476 px, 28.2 % | 157 px | Yes |
| C | One panel, like the numpad | 1280 px, 24.5 % | 157 px, one stick at a time | Yes |
| D | A floating window | two windows, 27.3 % | 159 px | Yes |

The figures are the page's own, at today's key size. Its sticks already use
the pick-up and put-down behaviour from *The stick*, with a control for what
happens when the pointer leaves the window, so both open designs can be
tried on the one page.

## How it sits in the existing architecture

| Concern | Rule that already exists | What the controller does |
|---------|--------------------------|--------------------------|
| Bridge size | New surfaces split off (`TelemetryBridge` is the model) | Its own `GamepadBridge` and `gamepad` context property. `KeyboardBridge` does not reference it. |
| Package | One directory per subsystem (`src/dictation/`) | `src/gamepad/`: shaping, state, bridge. The bus client sits in `src/platform/`. |
| Threads | Nothing on the Qt thread blocks | Plugging waits on the device, so it happens on a worker. Reports go out on a fixed tick, off the Qt thread. |
| Geometry | Unit grid, `sectionHeight`, no `QtQuick.Layouts`, `hitMarginH` / `hitMarginV` on every key | Pad keys are `KeyButton`s in layout JSON. A stick is a multi-unit cell. |
| Window size | A new child of `outerLayout` joins the width model | The view is added to `maxWidthForScreen` and its test. |
| Colours | Role table from `qml/palette.js` | Pad keys take roles. Xbox's face-button colours are not used as literals. |
| Scanning | Every visible key is a UIA `Button`, ids append-only | A `pad` section, feeding `scanRevision`. A switch user gains a controller. |
| Sticky state | One release site for modifiers | Pad latches are not modifiers and get their own single release site. Typing a letter must not drop a held direction. |
| Actions | `key_actions.py` is a registry | One new `pad` type and one `ActionExecutor` method. |
| Shutdown | `shutdown()` releases anything held | A neutral report, then the handle closes, which unplugs the pad. |
| Pad lifetime | none yet | Plugged while the Controller view is in use, not at launch: a controller appearing changes what some games do. |
| Settings | The 8-step wiring; no secret in `Settings {}` | A seventh category, *Controller*. That is seven cards on a six-card home, so it is a layout question too. |
| Imports | Sanitise, cap, allow-list | The Nimbus profile importer is an import path and is written to those rules from the first line. |
| Data Backup | Schema changes need a `SCHEMA_VERSION` bump | Controller layouts join the archive when they exist; that is a schema change. |
| Tests | Autouse guards keep tests off the live desktop | A guard so no test opens the real bus. Everything is tested over a fake pad. |
| Logging, privacy mode | No typed content in the log | A pad carries no text. State changes log at DEBUG. |

## Phases

Each phase ends in something usable, and each is its own run of pull
requests.

| | Phase | Ends with | Depends on |
|---|-------|-----------|------------|
| 0 | **Probe.** Vendor the client; a script that plugs a pad and answers the measurements below. | Numbers. No UI. | The driver installed on a test machine. |
| 1 | **Controller view, digital.** Pad output, buttons, D-pad, eight-way sticks, tap and latch. | Menus and turn-based games playable. | 0, and the view's mockup. |
| 2 | **Analog.** The drag-free stick, triggers, shaping, precision modifier, tremor filter. | Movement and camera in games that give up the cursor. | The stick's mockup and prototype. |
| 3 | **Layouts.** Pad keys in layout JSON, `pad` actions, zone sticks, shipped Xbox and adaptive layouts. | Function keys and custom layouts can fire pad input. | `MODULAR_LAYOUTS.md` phase 1. |
| 4 | **Game Mode.** Controller-mode pulse in process; pointer handling and borderless in a helper. | Nimbus's game compatibility, with a watchdog. | 2. |
| 5 | **Profiles.** Per-game switching, Nimbus profile import, the driver in the installer. | A Nimbus user can move over. | 3, and a build-pipeline decision. |
| 6 | **Parity review.** vJoy, Linux `uinput`, and what happens to Nimbus. | The owner's decision on the sibling app. | 5. |

Phases 0 and 1 are small. Phase 2 is where the design risk is. Phase 4 is
where the engineering risk is.

## To measure in phase 0

1. Does a game read the pad while the pointer is over our window? XInput is
   not tied to the pointer, but some games ignore a controller when they are
   not the foreground window. Ours never takes the foreground.
2. How long must a tap be held to register? Keys needed 50 ms.
3. Does a pad appearing mid-game disturb it (prompts, input mode, a pause)?
4. Does the bus open from a `uiAccess` process? Nimbus's manifest asks for
   `uiAccess`, but nothing shows it running from a trusted location.
5. Does the vendored client keep a pad alive across sleep and resume?

### Measured so far (2026-11-21, no game)

ViGEmBus 1.22.0 was installed on the owner's machine (installer checked
against the SHA-256 and signer thumbprint Nimbus pins in
`build_tools/fetch_redist.ps1`), and Nimbus's client was run unmodified
against it from an ordinary, non-elevated Python process, reading the pad
back through `XInputGetState`:

| What | Result | What it means here |
|------|--------|--------------------|
| Plugging a pad (`X360Pad()` returns) | 109 ms | Too long for the Qt thread. Plug on a worker, as planned. |
| Pad visible to XInput after that | 7 ms | No settling wait is needed before the first report. |
| Press or release reaching XInput, 50 each | median 0.01 ms, max 0.15 ms, none missed | A report is synchronous in practice. |
| Cost of one `update()` call, 200 calls | median 0.01 ms, max 0.07 ms | Cheap enough for any tick rate. It still stays off the Qt thread, because its failure path retries and can re-plug, which blocks for seconds. |
| Analog round trip | sent 0.05, read 1638 of 32767, exact | The float conversion matches. |
| Unplugging (`close()`) | 1 ms until the XInput slot is empty | Shutdown costs nothing. |
| Bus-reported player index | none | 1.22.0 does not answer the query (Nimbus recorded the same of 1.21.442), so the pad's slot is found through XInput. |
| Retried reports, re-plugs | 0, 0 | A clean bus. |

That closes none of the five questions above, which all need a real game, a
`uiAccess` build or a sleep cycle. It does show the client works on this
machine as it stands.

## What this plan does not promise

- **Raw Input mouse-look.** Unsolved in Nimbus, unsolved here.
- **Anti-cheat.** A virtual pad is visible to every process. No assist of any
  kind is ported.
- **Analog parity on a switch.** A scanner gets the digital fallbacks.
- **A date for retiring Nimbus.** Phase 6 is a decision, not a deletion.

## Decisions that are the owner's

Settled 2026-11-21:

- The end state is one app.
- ViGEmBus may be installed on the working machine for the measurements.
- The controller lives in a Controller view that swaps with the letter grid.
- While a stick is held, the pointer stays in the stick's area.

Later, each at its phase:

- Whether confinement holds up in real games, from the prototype (phase 2).
- Whether vJoy is wanted at all (phase 6).
- Whether this installer ever carries a kernel driver (phase 5).
- What the combined product is called, and what becomes of Nimbus's site
  and installer (phase 6).

## The next step

Two things, independent of each other:

- **The probe (phase 0).** Install ViGEmBus 1.22.0, vendor the client, and
  run the five measurements.
- **The mockups.** Drawn 2026-11-21 as
  [`controller-layout.html`](controller-layout.html); the owner chose placement A
  and pointer confinement from it.

## Corrections owed to ECOSYSTEM.md

Left for the pull request that starts phase 1, so the record changes once:

- The phased plan there (launch buttons, a named-pipe broadcast, embedded
  widgets) is superseded by this document for Nimbus.
- Phase 3's named pipe predates the UIA-not-IPC decision
  (`UIA_TARGETS.md`): a pipe served by a `uiAccess` process is a confused
  deputy.
- Phase 5 says the three PySide6 apps' widgets "can be embedded in a single
  QML layout". Nimbus's cannot.
- "Auto-updater: reference implementation for Alpha-OSK's planned updater":
  the updater here shipped long ago.
- Recommended step 4 waits on a Nimbus keyboard output mode, which does not
  exist.

## Sources

- Nimbus at `bcbf5dc`: `README.md`, `CLAUDE.md`, `CHANGELOG.md`,
  `src/padbus_client.py`, `src/bridge.py`, `src/config.py`,
  `src/mouse_hider.py`, `src/window_utils.py`,
  `qml/components/DraggableWidget.qml`, `qml/layouts/CustomLayout.qml`,
  `profiles/*.json`, `docs/GAME_COMPATIBILITY.md`, `docs/NEXT_STEPS.md`,
  `docs/vision/PROBLEM_STATEMENT.md`,
  `docs/vision/INPUT_OWNERSHIP_SOLUTION.md`,
  `docs/vision/PAD_BUS_FORK_PLAN.md`, `driver/README.md`.
- ViGEmBus: <https://github.com/nefarius/ViGEmBus> (archived 2023-11-02,
  BSD-3-Clause, last release 1.22.0) and
  <https://docs.nefarius.at/projects/ViGEm/End-of-Life/>.
- vJoy: <https://github.com/jshafer817/vJoy> (MIT fork; no releases listed).
- pyvjoy: <https://github.com/tidzo/pyvjoy> (no licence file on the page;
  x86 DLL only).
