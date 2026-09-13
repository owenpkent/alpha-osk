# Switch-scanning targets over UI Automation

Alpha-OSK publishes every on-screen key and prediction pill as a UI
Automation element, so an external switch-scanning application can enumerate
the keyboard's targets, draw a scanning highlight over them, and activate the
user's selection. Switchify PC is the application this was built with
(issue #106); the contract is deliberately not specific to it, and any
Windows-permitted assistive technology can read it.

This document is the normative version of that contract. If it and the code
disagree, that is a bug in one of them, and `tests/test_qml_scan_targets.py`
is what is supposed to catch it.

## Why UI Automation rather than an IPC server

The original request was for a local IPC interface: a socket or pipe, a
message schema, a discovery mechanism, and an activation call. UI Automation
already is all of those, with the authorization model built in.

The deciding argument was authorization. A same-user pipe that activates keys
is a confused-deputy risk here specifically, because Alpha-OSK runs with
`uiAccess="true"` and so can send input to windows an ordinary same-user
process cannot. A pipe validating only the user would lend that ability to
any process running as the user. Doing it over UIA instead means Windows
decides who may drive the keyboard, at the integrity level it already
enforces for assistive technology, and we write no ACL code, no framing, no
PID or token checks, and no publisher policy.

The second argument is that a bespoke channel would have to re-invent
everything below: screen-space bounds that follow DPI, enabled and offscreen
state, a stable identity per control, and an activation verb. UIA has all of
them and the client's language already binds to it.

## Finding the keyboard

Match the top-level window on **AutomationId `alphaOsk.alphaOskKeyboard`**.

Qt builds a window's AutomationId by walking its accessible parents and
joining their names: the window's own `objectName`, prefixed with the
application object's. `Main.qml` pins the window's (`alphaOskKeyboard`) and
`keyboard_app.py` pins the application's (`alphaOsk`), both for exactly this
purpose.

Pinning the application's name is not decoration. Where an object has no
name Qt substitutes its C++ class name, so an unnamed application made the id
depend on which application class was constructed. The first version of this
document published `QGuiApplication.alphaOskKeyboard`, which is what the
headless test harness produced, while the shipped keyboard constructs a
`QApplication` and reported `QApplication.alphaOskKeyboard`. The external test
client found that; both unnamed forms and the pinned one were then confirmed
against a live UIA client.

Do not match on the window's Name (`Alpha-OSK`), which is user-facing text,
and do not match on the window class: Qt generates it
(`QQuickWindowQmlImpl_QML_0` at time of writing) and it changes on a Qt
upgrade. The project has the same rule internally for Compatibility Mode
detection, and for the same reason.

## The target set

Everything scannable lives in the window's subtree and is a `Button` whose
AutomationId begins with `aosk.v1.`.

| Section | What |
|---------|------|
| `grid`  | The main data-driven key grid, including the compact layouts' embedded navigation column |
| `fn1`   | The F1-F12 row |
| `fn2`   | The F13-F24 row |
| `num`   | The compact layouts' number row panel |
| `nav`   | The navigation panel |
| `pad`   | The numpad panel |
| `pred`  | The prediction pills |

**Presence means activatable.** A key on a hidden panel, or on a symbol layer
that is not showing, is absent from the tree rather than present and
disabled, because Qt prunes invisible items from the accessibility tree and
`Accessible.ignored` removes anything without an id. A scanner therefore does
not have to check availability per target, only to re-snapshot when the
revision changes.

Two consequences of how the keyboard draws itself, both of which will
otherwise look like bugs to a client:

- The prediction bar **drops** low-ranked pills rather than eliding them when
  the window is narrow, so the number of `pred` targets is what is on screen
  and changes with window width alone. It is not the configured maximum.
- The compact layouts carry their navigation column inside the main grid, so
  the same logical key is under `grid` there and under `nav` in the full-size
  layouts.

Out of scope for v1, and absent from the tree: the settings, help, snippets,
symbols and dashboard windows; secondary-click actions; held-key commands.

## Identity

    aosk.v1.<section>.<row>.<index>          keys
    aosk.v1.pred.<index>.g<generation>       prediction pills
    aosk.v1.revision                         the revision beacon

`row` and `index` are zero-based visual order, top to bottom and left to
right within the section. `aosk.v1` is the contract version and changes if
the scheme does.

The format is defined once, in `Main.qml`'s `scanTargetId`, and the six
surfaces that draw targets all call it. That is deliberate: two surfaces
computing the same format independently would eventually hand a scanner
colliding ids, and a collision is invisible to the client (it reads as one
target that moved, not as a fault).

## Names, and why they are not the keycaps

`Name` is a **speakable** label, not a copy of the cap.

Taking the cap verbatim was tried first and is wrong on the shipped qwerty
layout in seven places: the space bar's cap is the empty string, and
Backspace, Win and the four arrows are glyphs with no spoken form. A scanner
would have presented seven unlabelled or unpronounceable targets, one of them
the most-pressed key on the board.

The rule (`KeyButton._scanName`) is: strip everything outside printable ASCII
from the cap, and fall back to the key's own `keyText` if nothing survives.
Every caller already sets `keyText` to the key's word, because that is what
gets sent, so this needs no glyph table. `⌫` becomes `backspace`, the empty
space bar becomes `space`, `↑` becomes `up`, and `⇧ Shift` becomes `Shift`. A
key that genuinely types a non-ASCII glyph (the symbol layers' currency and
maths signs, an accented letter) falls back to `keyText`, which for those is
the character itself, so it keeps its own name.

Prediction pill Names are exactly the word on the pill, with no
transformation.

## State

- **Enabled / offscreen**: ordinary UIA meanings.
- **Modifiers and the lock-style toggles** (Shift, Ctrl, Alt, Win, Caps,
  NumLock) expose `TogglePattern`. `ToggleState` follows the sticky held
  state: `Off` when not held, `On` when held.
- **Right-click lock** ("held until released") has no UIA vocabulary, so it
  rides in **`FullDescription`** as the literal token `locked`, empty
  otherwise. It doubles as a sensible screen-reader announcement
  ("Shift, locked"), which is why it is a word rather than a code.

`FullDescription` is property id 30159 and is **UIA3 only**. A legacy
`System.Windows.Automation` client cannot resolve the id at all and will see
the property as absent rather than empty. This is the one field in the
contract invisible to a UIA2 client. It is reachable from the Rust `windows`
crate, which goes through `IUIAutomation`.

Nothing else about a key is a toggle. In particular a **programmed F-key is
not**: `FunctionRow` binds `isActive` on a reassigned key to mark it visually,
but that is a fact about the key rather than a state it is in, and reporting
it as a toggle would tell a scanner and a screen reader that F13 is switched
on.

## Bounds and DPI

`BoundingRectangle`, in physical screen pixels, negative coordinates
included, exactly as UIA reports it.

Alpha-OSK declares `PerMonitorV2` in its application manifest
(`build/windows/alpha-osk.exe.manifest`). A client that is not per-monitor
DPI aware will see rectangles scaled by the wrong factor; a constant ratio
offset in an overlay is the symptom to check first.

## The revision beacon

`aosk.v1.revision` is a zero-sized `StaticText` whose **Name** is a revision
string. It changes whenever anything in this contract changes: window
position and size, monitor or DPI, layout, compact view, active layer, any
panel's visibility, any modifier or lock state, the prediction generation,
and keyboard visibility.

The intended client loop is to poll that one property at whatever rate the
overlay needs, and take a full cached snapshot only when it moves. That keeps
the steady-state cost at a single cross-process property read instead of a
walk of sixty-odd elements at overlay frame rate.

The string's internal format is **not** part of the contract. Compare it for
equality; do not parse it.

A cached subtree snapshot of the whole keyboard measured **12.5 ms** for 83
elements on the development machine, so polling the tree directly is viable
if a client prefers it. The beacon exists so that it does not have to be.

## Activation

UIA `Invoke` on a target does what a primary click does.

- It raises the same one-shot QML signal, so sticky and locked modifiers,
  programmed function keys, layer switches and prediction insertion all
  behave exactly as under the mouse.
- It **does not** arm the auto-repeat timer. A scanner has no release event
  to stop one with, so a single Invoke on Backspace would otherwise repeat
  until the safety timer fired.
- It **does not** take focus. The keyboard window carries `WS_EX_NOACTIVATE`
  and the application receiving the text keeps its focus across the Invoke.
- It flashes the key briefly. That is not decoration: a switch user is
  looking at the keyboard rather than the text field, and without it the only
  feedback that a selection landed is a character appearing elsewhere.
- It goes through the same debounce a click does.

Privacy mode needs no special handling by a client. It already empties the
prediction bar, so those targets leave the tree with it.

## Showing, minimizing, and reading the state

A client puts the keyboard away and brings it back through the keyboard
window's standard UIA **WindowPattern**, and reads where it is from the same
pattern. There is no Alpha-OSK-specific control for this, deliberately: the
standard pattern is what every assistive technology already speaks, and a
second route would be a second thing to secure.

| To | Call |
|----|------|
| Put the keyboard away | `SetWindowVisualState(Minimized)` |
| Bring it back | `SetWindowVisualState(Normal)` |
| Read where it is | `WindowVisualState`: `Normal` or `Minimized` |

**Ignore `CanMinimize`.** It reads `False`, because the keyboard draws its own
title bar and Qt derives that property from the native minimize button the
window does not have. The call works regardless.

**The states a client can see are `Normal` and `Minimized`, and that is all
of them on Windows.** Every route the keyboard offers for putting itself away
(its own minimize button, the tray, the taskbar) minimizes rather than hides,
and a minimized keyboard stays in the tree under the same window id, with the
revision beacon still readable. A keyboard that is absent from the tree is not
running.

**While minimized, the keyboard offers no targets.** Every key and pill leaves
the tree, and an Invoke on an element a client is still holding from before
the minimize inserts nothing. Windows would otherwise keep reporting those
elements as present and onscreen, and "presence means activatable" would be
false for as long as the keyboard stayed minimized. The window and the beacon
stay, so the state stays readable and the keyboard stays recallable. A key
Invoke refused this way is not reported as an error to the client, so check
`WindowVisualState` rather than relying on an Invoke failing.

**The beacon moves on every change of state**, including a minimize or restore
the user makes directly (the minimize button, the tray, the taskbar), so a
client polling the beacon learns of it without polling the window as well.

**Restoring never takes focus.** The application receiving text keeps the
foreground across a restore, whoever asks for it. This needed work, and the
reason matters to anyone changing it: Qt restores a minimized window with the
activating form of `ShowWindow` whatever the window's flags, and on this
keyboard the tray's restore code, `SetWindowVisualState(Normal)` and a plain
`ShowWindow(SW_RESTORE)` from another process all left the keyboard in the
foreground. (A real click on the taskbar button was not measured. A click
there has already moved the foreground to the taskbar, so it is not the case
this protects.) For a client restoring the keyboard on the user's behalf,
that is a restore that silently sends the next keystroke somewhere the user
did not choose. `QuietRestoreFilter` in `src/platform/windows_window.py`
declines the `WM_QUERYOPEN` that Windows sends before each of those restores
and performs the restore itself with `SW_SHOWNOACTIVATE`.

**`WindowPattern.Close` minimizes the keyboard; it does not quit it.** A
client should never offer it as a way to hide the keyboard, and never needs
to. Qt's default for a close was to hide the window while leaving the process
running, which took the keyboard off the taskbar and out of the tree, where
no client could find it again, and the taskbar's own Close did the same
(both measured). A
close that is not part of a quit now minimizes instead. The keyboard's own
close button and the tray's Quit still end it.

### What this does and does not protect

These controls give a client nothing a process could not already do to the
window. Anything that can reach the keyboard through UIA can equally call
`ShowWindow`, post it a close message, or end the process, and Windows decides
who can do any of those. What the changes above remove is a set of harms that
standard, well-meaning calls used to cause:

- a restore that moved the foreground, and with it the user's next keystroke;
- a close that stranded the keyboard running but out of reach;
- targets that stayed invokable while the keyboard was not on screen.

A hostile process on the same desktop can still minimize or close the
keyboard. That is a property of the Windows desktop, not of this contract, and
this contract does not make it easier.

## Stale predictions cannot fire

The guarantee is that a scan selection made against one round of predictions
can never insert a word from a later round.

It rests on two things on the keyboard's side, and neither depends on the
client comparing anything first.

1. **Every round rebuilds the pills.** Each entry in the pill row's model
   carries the round's generation, so no two rounds compare equal and Qt's
   `Repeater` destroys every pill from the previous round. A client still
   holding one of those elements finds its `Invoke` fails, and nothing is
   inserted. The error a client sees depends on the client, so do not match
   on a specific one: a UIA3 client was seen to get
   `UIA_E_ELEMENTNOTAVAILABLE`, and the managed `System.Windows.Automation`
   client gets an `InvalidOperationException` reading "Unsupported Pattern".
2. **An Invoke carries its pill's generation, and a dead one is refused.**
   The pill hands its own generation to `invokeScanPrediction` in
   `Main.qml`, which inserts nothing unless that is still the live round.
   Part 1 should make this unreachable; it exists so that a later change
   letting a pill survive a round fails closed instead of inserting.

**The first version of this guarantee was wrong, and the way it was wrong is
worth knowing.** It claimed part 1 as free Qt behaviour: swap the model and
the delegates are rebuilt, identical words included. They are not.
`QQuickRepeater::setModel` returns early when the new list compares equal to
the old one, so a round of identical words kept the old pill objects. The
external test client (`OwenMcGirr/alpha-osk-scan-lab`) held a pill across
such a round, invoked it, and got a keystroke. The earlier measurement had
only exercised a round that changed the words.

The generation in the AutomationId is the client's half. It is built from the
pill's own generation, so it never changes under an element a client is
holding, and it lets a scanner notice a new round cheaply. Comparing the
AutomationId (and Name, enabled and offscreen state) immediately before
Invoke is still recommended, but the guarantee no longer rests on it.

**Do not** strip the generation out of the pill model to save allocations,
and do not drop the generation check because the rebuild "already handles
it". Each was the part that was missing once.

## What was verified, and how

The Windows UIA behaviour below is Qt's, not this codebase's, and none of it
can be reached under the `offscreen` platform plugin the test suite uses. It
was verified against live UIA clients driving the real `Main.qml`, with key
synthesis replaced by a recorder so nothing reached the desktop. The original
contract was checked with a UIA3 client. The window-id, stale-pill and
window-state rows were checked with the managed `System.Windows.Automation`
client, from a harness with its own settings key and config directory, so the
developer's running keyboard was never touched. None of those rows depend on
`FullDescription`, the one property that client cannot read.

| Claim | Result |
|-------|--------|
| `Accessible.id` becomes AutomationId | Confirmed |
| `Accessible.role: Button` becomes ControlType.Button | Confirmed |
| `Accessible.onPressAction` is reached by UIA Invoke | Confirmed, on a `WS_EX_NOACTIVATE` window |
| Invoke does not change the foreground window | Confirmed |
| `Accessible.checkable` / `checked` become TogglePattern | Confirmed, `Off` to `On` across an Invoke |
| `Accessible.description` becomes `FullDescription` (30159) | Confirmed. It is **not** HelpText or ItemStatus, both of which stay empty |
| Bounds are physical pixels | Confirmed at 150% scaling |
| A pill destroyed by a new round cannot be invoked | Confirmed, with a control that invokes the same held element with no round in between and succeeds. UIA3 reported `UIA_E_ELEMENTNOTAVAILABLE`; the managed client reports "Unsupported Pattern" |
| `SetWindowVisualState(Minimized)` / `(Normal)` and `WindowVisualState` on the keyboard window | Confirmed. Minimizing never moved the foreground; the window stayed discoverable by id with the beacon readable |
| A minimized keyboard's keys and pills leave the tree, and a held one inserts nothing | Confirmed, with the same key inserting once the keyboard was restored |
| Restoring leaves the foreground alone | Confirmed for `SetWindowVisualState(Normal)`, an external `ShowWindow(SW_RESTORE)`, and the tray's restore. Without the filter the same restores took the foreground (on a plain window every time; on the keyboard whenever Windows' foreground rules allowed it). A control window with ordinary flags showed each run could see a steal |
| Declining the restore but not letting its own restore through | **Refuted**: the keyboard declined its own restore 409 times and never came back. Hence `_restoring` |
| Adding `SWP_NOACTIVATE` to `WM_WINDOWPOSCHANGING` instead | **Refuted**: no effect; the activation on restore does not go through those flags |
| `WindowPattern.Close` | Before: the process kept running with the keyboard hidden and absent from the tree. After: the keyboard minimizes and stays discoverable, and the application's own quit still exits |
| An identical round rebuilds the pills | **Refuted** in the first version (the pills survived and a held one inserted); fixed by carrying the generation in the model, and held by `test_an_identical_round_destroys_the_pills_it_replaces` |
| The window's AutomationId | `alphaOsk.alphaOskKeyboard` with the application named. Unnamed it is the application's class name: `QApplication.` in the shipped keyboard, `QGuiApplication.` in the test harness |
| The beacon moves on a state change | Confirmed |

Two traps for anyone re-running this:

- **Qt advertises `InvokePattern` on essentially every element**, including
  static text that does nothing when invoked. Pattern availability is not a
  usable filter for "this is a target". Filter on the `aosk.v1.` id prefix.
- **The legacy managed client is not a fair test.**
  `System.Windows.Automation` cannot resolve `FullDescription` and reports it
  as absent. Use a UIA3 client (`IUIAutomation`) or you will conclude a
  working field is broken.

### Independent verification by an external client

The external test client written for this contract,
[`OwenMcGirr/alpha-osk-scan-lab`](https://github.com/OwenMcGirr/alpha-osk-scan-lab),
is a Rust UIA3 client and a click-through overlay, independent of this repo.
It re-ran the stale-prediction and window-id fixes against commit `b75ec07`.
Its results and raw reports are in that repo's
[`VALIDATION.md` at `ba6a598`](https://github.com/OwenMcGirr/alpha-osk-scan-lab/blob/ba6a598b29aabd21120f7110ec4315464746ccdc/VALIDATION.md).

- **Stale predictions:** it held an old pill across a round of identical
  words, and separately across a round of different words, and invoked it with
  no client-side comparison first. Both produced zero synthesis calls, and a
  current pill still inserted.
- **Window id:** it finds the keyboard by `alphaOsk.alphaOskKeyboard` alone,
  with both earlier forms removed.
- **Overall:** all 13 of its live UIA checks passed, including foreground
  preservation across Invoke.

That run predates the window-state work in `69d6e56`, so it covers none of
*Showing, minimizing, and reading the state*. It also recorded synthesis
rather than injecting desktop input.

**Still unverified by anyone:**

- the signed, installed build with UIAccess active;
- mixed-DPI and multi-monitor alignment, including negative coordinates;
- a physical switch driving the scanner;
- text inserted into a real application on the desktop;
- a real click on the taskbar button to restore the keyboard.

## What the headless tests can and cannot hold

`tests/test_qml_scan_targets.py` cannot see any of the above, because there
is no UIA provider under `offscreen`. What it holds is the half that is ours
and the half that will actually rot: that every visible key carries an
identity, that identities are unique and well-formed, that names are
speakable, that only real toggles claim a toggle state, that a new prediction
round always yields new ids, that an identical round still destroys the
pills it replaces, that an Invoke from a dead generation inserts nothing,
that a minimized keyboard offers no targets and inserts nothing, that a close
minimizes while a quit still closes, and that the beacon moves.
`tests/test_windows_window.py` holds the quiet-restore filter's decision
message by message, including the reentrancy guard.

PySide cannot read an attached `Accessible.*` property at all (`QQmlProperty`
reports it invalid), so every accessible value is kept in a named property
that the attached binding passes straight through. That is why
`KeyButton` has `_scanName`, `_scanChecked`, `_scanDescription` and
`_scanIgnored`, and why the `Accessible` block must stay free of logic: any
expression written directly into it is untestable.
