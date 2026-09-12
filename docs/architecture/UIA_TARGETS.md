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

Match the top-level window on **AutomationId `QGuiApplication.alphaOskKeyboard`**.

Qt synthesises a window's AutomationId from its QML `objectName`, so
`Main.qml` pins `objectName: "alphaOskKeyboard"` for exactly this purpose.

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

## Stale predictions cannot fire

The guarantee is that a scan selection made against one round of predictions
can never insert a word from a later round.

It rests on **object lifetime**, not on the id. Swapping the prediction model
makes Qt's `Repeater` destroy and rebuild every pill delegate, so a client
holding an element from the previous round gets `UIA_E_ELEMENTNOTAVAILABLE`
and its `Invoke` does nothing at all. This was measured, including the case
where the new round produces identical words: a fresh array is a fresh array,
and the delegates are rebuilt either way.

The generation in the AutomationId is the client's half of the same fact: it
lets a scanner notice the change cheaply, and stops an id ever denoting two
different offers. Comparing the AutomationId (and RuntimeId, Name, enabled
and offscreen state) immediately before Invoke is still recommended, but the
guarantee does not depend on the client remembering to.

**Do not** optimise the pill row into a reused model. That would trade the
guarantee for a few allocations per keystroke.

## What was verified, and how

The Windows UIA behaviour below is Qt's, not this codebase's, and none of it
can be reached under the `offscreen` platform plugin the test suite uses. It
was verified against a live UIA3 client driving the real `Main.qml`, with key
synthesis replaced by a recorder so nothing reached the desktop.

| Claim | Result |
|-------|--------|
| `Accessible.id` becomes AutomationId | Confirmed |
| `Accessible.role: Button` becomes ControlType.Button | Confirmed |
| `Accessible.onPressAction` is reached by UIA Invoke | Confirmed, on a `WS_EX_NOACTIVATE` window |
| Invoke does not change the foreground window | Confirmed |
| `Accessible.checkable` / `checked` become TogglePattern | Confirmed, `Off` to `On` across an Invoke |
| `Accessible.description` becomes `FullDescription` (30159) | Confirmed. It is **not** HelpText or ItemStatus, both of which stay empty |
| Bounds are physical pixels | Confirmed at 150% scaling |
| A stale pill element dies | Confirmed, `UIA_E_ELEMENTNOTAVAILABLE` |
| The beacon moves on a state change | Confirmed |

Two traps for anyone re-running this:

- **Qt advertises `InvokePattern` on essentially every element**, including
  static text that does nothing when invoked. Pattern availability is not a
  usable filter for "this is a target". Filter on the `aosk.v1.` id prefix.
- **The legacy managed client is not a fair test.**
  `System.Windows.Automation` cannot resolve `FullDescription` and reports it
  as absent. Use a UIA3 client (`IUIAutomation`) or you will conclude a
  working field is broken.

## What the headless tests can and cannot hold

`tests/test_qml_scan_targets.py` cannot see any of the above, because there
is no UIA provider under `offscreen`. What it holds is the half that is ours
and the half that will actually rot: that every visible key carries an
identity, that identities are unique and well-formed, that names are
speakable, that only real toggles claim a toggle state, that a new prediction
round always yields new ids, and that the beacon moves.

PySide cannot read an attached `Accessible.*` property at all (`QQmlProperty`
reports it invalid), so every accessible value is kept in a named property
that the attached binding passes straight through. That is why
`KeyButton` has `_scanName`, `_scanChecked`, `_scanDescription` and
`_scanIgnored`, and why the `Accessible` block must stay free of logic: any
expression written directly into it is untestable.
