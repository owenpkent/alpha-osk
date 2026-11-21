# Incorporating Nimbus: an exploration

**Status:** exploration only, written 2026-11-21. Nothing here is built or
decided. It was written from a read of Nimbus at commit `bcbf5dc`
(2026-09-15, tag `v1.5.0`) and of this repo at `730c165`. Driver facts were
checked against the upstream pages on the same day and are cited in
*Sources*.

**The question:** Nimbus (`W:\dev\Nimbus-Adaptive-Controller`) turns mouse
input into a virtual game controller. What would it mean for Alpha-OSK to
incorporate it, and which version of that is worth building?

## The short answer

- **Do not embed Nimbus.** Its UI and its 3000-line bridge own their process
  (`QApplication`, a process-wide native event filter, Game Mode, mouse
  isolation). Hosting that inside the keyboard would import everything this
  repo has spent a year keeping out.
- **One piece of Nimbus is worth taking whole:** `src/padbus_client.py`, a
  729-line, standard-library-only `ctypes` client that plugs in an Xbox 360
  pad and feeds it reports. It is MIT, has no Qt in it, and needs no new
  dependency. That matches how dictation was built here (on what the wheel
  already ships).
- **The version worth building is a small gamepad panel of buttons**, made
  the way the numpad is made, that taps and latches pad buttons and
  directions. Not sticks you drag. It works only when the pad driver is
  already on the machine, and is absent otherwise.
- **Nimbus stays the real controller.** Analog sticks, curves, profiles, Game
  Mode and the pointer-ownership work stay there. The keyboard gets the part
  that is keyboard-shaped: discrete presses.

The decision this document needs from the owner is at the end.

## What Nimbus is today

Several lines in [`ECOSYSTEM.md`](ECOSYSTEM.md) are older than the code.
What the repo says now:

| Fact | Detail |
|------|--------|
| Shipping | Yes. NSIS installer, GitHub releases, MIT licence, CI. Latest tag `v1.5.0` (some files still say 1.4.3). |
| Stack | Python, PySide6 and QML, PyInstaller and NSIS, the same EV token. Its manifest also requests `uiAccess="true"`. |
| Output, XInput | `src/padbus_client.py`: its own client for the ViGEmBus protocol. It replaced the `vgamepad` package on 2026-09-09. |
| Output, DirectInput | `src/vjoy_interface.py` over `pyvjoy`, which calls `sys.exit` when the vJoy DLL will not load. |
| Drivers | Both need a kernel driver. Nimbus's installer offers vJoy 2.1.9.1 and ViGEmBus 1.22.0. With neither, the app runs in simulation mode. |
| Stick input | A triple-click captures the pointer ("mouse lock"); relative motion then deflects the stick. There is no hover or dwell mode. |
| Focus | Game Focus Mode: `WS_EX_NOACTIVATE` plus `MA_NOACTIVATE` on `WM_MOUSEACTIVATE`. |
| Keyboard output | Not implemented. No keyboard mode, no IPC, no plugin surface, and no mention of Alpha-OSK anywhere. |
| Config | JSON profiles in `%APPDATA%\ProjectNimbus\profiles\`: a canvas of widgets, each with a mapping. |

Two things about the driver matter more than the rest:

1. **ViGEmBus is retired.** The upstream repository was archived on
   2023-11-02 and its last release is 1.22.0. It is BSD-3-Clause, the signed
   binaries still install on Windows 10 and 11, and no successor is named.
   Nimbus's XInput path rests on a driver nobody will fix.
2. **Nimbus already has a plan for that**
   (`docs/vision/PAD_BUS_FORK_PLAN.md`): fork the bus as a funded side
   project, and do not ship the fork until the anti-cheat question is
   answered. `padbus_client.py` probes a *list* of bus interfaces, so a second
   driver speaking the same protocol is one more entry. Anything Alpha-OSK
   builds on that client inherits the hedge for free.

## Four ways to "incorporate" it

| | Shape | Cost | What the user gets | Verdict |
|---|-------|------|--------------------|---------|
| A | **Launch button.** A gamepad control that starts Nimbus (ECOSYSTEM Phase 2). | Small | One click saved, once per session. | Not worth a permanent control. A function key can already be programmed if `key_actions.py` grows a `launch` type; see *Open questions* 5. |
| B | **Native gamepad panel.** Vendor `padbus_client.py`; add a panel of pad buttons beside the nav cluster and numpad. | Medium | Menus, turn-based and slow-paced games playable from the keyboard alone; pad buttons reachable by a switch scanner. | **Recommended.** |
| C | **Embed Nimbus's UI** (ECOSYSTEM Phase 5): its QML widgets inside our window. | Large | Sticks and sliders in the keyboard window. | No. See *Why not embed*. |
| D | **One pad, two apps.** A local process owns the pad; Nimbus and Alpha-OSK both feed it over IPC. | Large, and it is Nimbus's project, not ours | A single controller that both tools drive. | Not now. See *Two apps, one controller*. |

### Why not embed (C)

- Nimbus's `ControllerBridge` (`src/bridge.py`, 3037 lines) couples Qt, Win32
  hooks, pointer isolation and telemetry. It is not a library.
- It installs a native event filter on `QApplication.instance()`. In our
  process that filter would sit in front of every keyboard click.
- Its stick is a pointer capture. Our window must never own the pointer: an
  interceptor that owns every press is the flaw that caused issue #15 and
  ended swipe typing (see *Removed: Swipe / Glide Typing* in `CLAUDE.md`).
- Its layout is a free drag-and-drop canvas of widgets; ours is a unit grid
  whose panels share one height and line up to the pixel.
- It would bring `numpy`, `httpx`, `keyring`, `sentry-sdk` and `pyvjoy` into a
  build whose spec currently *excludes* `pygame` and `pyvjoy`
  (`build/windows/alpha-osk.spec`).

## The recommended shape (B), in detail

### What is taken from Nimbus

- **`padbus_client.py`, vendored** as `src/platform/padbus_client.py` with
  the source commit noted at the top, the same way okstudio-style files are
  vendored. It imports `ctypes`, `enum`, `sys`, `time`, `uuid` and `typing`,
  and nothing else. It already handles the bus's replug gap, which the
  library it replaced did not.
- **Nothing else.** No shaping code (there are no analog axes to shape), no
  profile loader, no vJoy. vJoy is DirectInput only, x86-DLL only through
  `pyvjoy`, and can exit the process on import.

A vendored copy drifts. The alternative is a small shared package both
repos install, which is the right answer the day a second file is shared and
overkill for one. Record the commit and re-vendor by hand until then.

### What the panel is

A fourth section beside the grid, the nav cluster and the numpad
(*Appearance -> Panels -> Gamepad*, off by default):

- **Buttons:** A, B, X, Y, LB, RB, LT, RT, Start, Back, L3, R3, Guide.
- **Directions:** a D-pad, and the left stick as eight direction keys
  (full deflection only). The right stick is the same again, or left out of
  the first version.
- **Left tap:** a press held for a short fixed time, then released. This is
  the game-mode key hold (`_GAME_KEY_HOLD_SECONDS`) applied to a pad.
- **Right-click:** latch, with the same 3 px `lockBar` cue the modifiers
  use; a left tap releases. This is how a direction is held to walk and a
  trigger is held to accelerate, without anyone holding a mouse button down.
- **Triggers are digital** (0 or full) in the first version.

Everything is a click or a latch. Nothing is a drag. That is the rule this
keyboard already lives by: a sustained precise drag is the one gesture an
imprecise-motor user cannot make.

### How it sits in the existing architecture

| Concern | Rule that already exists | What the panel does |
|---------|--------------------------|---------------------|
| Bridge size | Split new surfaces off (`STRUCTURAL_REVIEW.md` 3.1; `TelemetryBridge` is the model) | Its own `GamepadBridge` `QObject` and `gamepad` context property, registered in `keyboard_app.py` and `tests/qml_context.py`. `KeyboardBridge` does not reference it. |
| Geometry | Sections lay out to `Main.qml::sectionHeight`; panels absorb into key heights; no `QtQuick.Layouts` | Built like `NumpadPanel.qml`: `keyH`-derived `implicitHeight`, `hitMarginH` / `hitMarginV` on every key. |
| Window width | A new child of `outerLayout` joins the `maxWidthForScreen` model | Added to the model and to `tests/test_qml_window_height.py`. |
| Colours | Role table from `qml/palette.js`, resolved in `KeyButton` | Pad keys take existing roles. The Xbox face-button colours are **not** used as literals: no hue is a literal here. |
| Scanning | Every visible key is a UIA `Button`; ids are append-only | A new `pad` section (`aosk.v1.pad.<row>.<index>`), feeding `scanRevision`. A switch user gains pad buttons, which nothing else here offers. |
| Sticky state | One release site (`_release_sticky_modifiers`) | Pad latches are **not** modifiers and do not go through it: typing a letter must not drop a held direction. They get their own single release site on the gamepad bridge. |
| Shutdown | `shutdown()` releases anything held | Sends a neutral report, then closes the handle (closing unplugs the pad). |
| Settings | The 8-step wiring; a secret never goes in `Settings {}` | One toggle, in *Appearance -> Panels*. No secret, so `appSettings` is fine. |
| Threads | Nothing on the Qt thread blocks | `X360Pad` plugging waits for the device (`WAIT_DEVICE_READY`). Plug on a worker, as the focus probe does; reports are single IOCTLs. |
| Tests | Autouse guards keep tests off the live desktop | A guard so no test opens the real bus (the pattern of `_no_real_update_relauncher`); the panel is tested over a fake pad. |
| Logging | Nothing typed reaches the log | Nothing here is typed content. Log pad state changes at DEBUG only. |
| Privacy mode | Suppresses learning and context | No interaction: a pad press carries no text. |

### The driver: detect, never install

Alpha-OSK's installer does not gain a kernel driver. The panel's toggle is
live only when `bus_present()` is true, and otherwise says what is missing
and where it comes from (Nimbus's installer, or the ViGEmBus 1.22.0 setup).
That is the dictation pattern: inert without the thing it needs, and it says
so.

Reasons to hold that line:

- The driver is retired. Shipping it makes its future our release problem.
- The build and signing pipeline is a flag-in-the-PR area; this keeps the
  change out of it entirely.
- Someone who wants a virtual controller is one install away from Nimbus,
  which is the better controller.

### Pad lifetime: only while it is wanted

A plugged pad is visible to every game and to Steam, and some games switch
their prompts or their input mode when a controller appears. So the pad is
plugged when the panel is turned on and unplugged when it is turned off, not
at launch. Whether it should also come and go with `_window_is_game` is an
open question (below); the simple rule is the starting point.

## What this does not solve

- **Games that take the pointer.** A first-person game that captures the
  cursor leaves nothing to click the keyboard with. This is true of the
  letter keys today. Nimbus's Game Mode, mouse hider and its
  input-ownership work (`docs/vision/INPUT_OWNERSHIP_SOLUTION.md`) are the
  answer to it, and they are the strongest reason the panel does not replace
  Nimbus.
- **Analog control.** Eight directions at full deflection will not steer a
  car. Racing and flight stay in Nimbus.
- **Anti-cheat.** A virtual pad is a virtual pad. Nimbus limits its aim
  assist to single-player games for the same reason; the panel adds no
  assistance of any kind, only buttons.
- **Linux and macOS.** Linux could follow over `uinput` (Nimbus has it,
  unreleased). macOS has no equivalent. The panel is Windows-only at first
  and absent elsewhere.

## Two apps, one controller

If Nimbus and the panel are both on, the machine has two controllers
(players 1 and 2), and a game will usually listen to one. Three ways out, in
order of cost:

1. **Yield.** The panel refuses to plug while a Nimbus process is running,
   and says so. Cheap, and correct for the common case: someone running
   Nimbus has the better controller already.
2. **Stay separate, document it.** Some games accept any controller.
3. **One owner.** Nimbus's own design work proposes a local process that
   owns the pad with the UI as a client. If that is built, Alpha-OSK could be
   a second client. But this repo chose UI Automation over an IPC server for
   a reason (`UIA_TARGETS.md`: a pipe served by a `uiAccess` process is a
   confused deputy), and both apps request `uiAccess`. Any such channel needs
   that review first, and the same caution applies to ECOSYSTEM Phase 3's
   "broadcast over a named pipe".

Start with 1.

## Open questions, to settle by measurement before building

1. **Does a game read the pad while the pointer is over the keyboard?**
   XInput is not tied to the pointer, but some games ignore a controller
   when they are not the foreground window. Our window never takes the
   foreground, so this should hold. Measure it on three or four real games.
2. **How long must a tap be held?** 50 ms was found for keys. A pad is
   polled per frame; measure rather than assume.
3. **Does plugging a pad while a game runs disturb it** (prompt glyphs,
   input mode, a "controller connected" pause)? This decides whether the pad
   may follow game detection or must follow only the toggle.
4. **Does the bus open cleanly from a `uiAccess` process?** Nimbus's
   manifest asks for `uiAccess` too, but its trusted-location handling is not
   in evidence, so it may never have run that way.
5. **What token does a program launched from the keyboard get?** Shape A,
   and any `launch` action, starts a process from a `uiAccess` parent. Check
   what the child inherits before offering it.
6. **Is eight-way enough for the left stick** in the games the owner
   actually plays, or does it need a half-deflection ring (walk and run)?

Questions 1 to 4 are one afternoon with a throwaway script over the vendored
client. Nothing in the app needs to change to answer them.

## If the answer is yes: the order of work

1. **Probe.** Vendor the client; a script under `scripts/` that plugs a pad
   and taps A, to answer questions 1 to 4. No UI.
2. **Mockup.** The panel's layout as a local page in `docs/roadmap/` for the
   owner to click through: where it sits, which keys, how a latch reads.
3. **Bridge.** `GamepadBridge` over a fake pad, with tests: tap, latch,
   release-all, shutdown, driver absent, Nimbus running.
4. **Panel.** QML, settings toggle, window-width model, scan targets, key
   roles.
5. **Docs.** An `architecture/GAMEPAD.md`, a *Key rules* line for the single
   release site, and the corrections to `ECOSYSTEM.md` listed below.

Each step is its own pull request. Steps 1 and 2 commit to nothing.

## Corrections owed to ECOSYSTEM.md

Left for the pull request that acts on this, so the record changes once:

- The *Nimbus Features Relevant to Alpha-OSK* table (curves for key repeat,
  tremor filtering for key presses, a widget canvas as a layout editor) is
  speculative, and none of it has been wanted since.
- "Auto-updater: reference implementation for Alpha-OSK's planned updater":
  Alpha-OSK's updater shipped long ago.
- Phase 3's named-pipe broadcast predates the UIA-not-IPC decision.
- Phase 5 says the widgets "can be embedded in a single QML layout". They
  cannot, for the reasons under *Why not embed*.
- Recommended step 4 waits on "Nimbus keyboard output mode", which does not
  exist and is not planned there.

## The decision

Which of these is wanted?

- **A. Probe first (recommended).** Vendor the client and answer open
  questions 1 to 4 with a script. One small pull request, no UI, nothing
  shipped. The panel is decided after, with numbers.
- **B. Go straight to the mockup.** Skip the probe and look at the panel's
  layout first.
- **C. Launch button only.** A control that starts Nimbus, and no more.
- **D. Leave them separate.** Keep this document as the record of why.

## Sources

- Nimbus at `bcbf5dc`: `README.md`, `CLAUDE.md`, `src/padbus_client.py`,
  `src/bridge.py`, `src/window_utils.py`, `qml/components/DraggableWidget.qml`,
  `build_tools/installer.nsi`, `build_tools/Nimbus-Adaptive-Controller.manifest`,
  `docs/vision/PAD_BUS_FORK_PLAN.md`, `docs/vision/INPUT_OWNERSHIP_SOLUTION.md`.
- ViGEmBus: <https://github.com/nefarius/ViGEmBus> (archived 2023-11-02,
  BSD-3-Clause, last release 1.22.0) and
  <https://docs.nefarius.at/projects/ViGEm/End-of-Life/>.
- vgamepad: <https://github.com/yannbouteiller/vgamepad> (MIT; runs the
  ViGEmBus installer at install time, which is why Nimbus dropped it).
- vJoy: <https://github.com/jshafer817/vJoy> (MIT fork; no releases listed).
- pyvjoy: <https://github.com/tidzo/pyvjoy> (no licence file on the page;
  x86 DLL only).
