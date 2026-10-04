# Function keys F13-F24 and programmable keys

Moved verbatim from `CLAUDE.md` on 2026-09-20, which keeps a summary of the load-bearing rules. This is the full reasoning.

## Function Keys F13-F24 and Programmable Keys

Two features that arrived together and answer different halves of the same
request. **F13-F24** are more raw keys to bind *in other apps*;
**programmable actions** turn a click here into a chord or a phrase that
works everywhere immediately, with nothing to bind. Neither subsumes the
other, which is why both shipped: an unbound F13 does nothing until the
target app is taught to listen for it, and teaching every app is not
something a mouse-driven user should have to do.

### The extra keys are real keys
`VK_F13`-`VK_F24` (0x7C-0x87) on Windows, the `F13`-`F24` X11 keysyms on
Linux (which pass straight through `xdotool`, so `platform/linux.py` needed
no change). **macOS stops at F20**: Carbon names `kVK_F13` through
`kVK_F20` and there is no virtual keycode for F21-F24 at all, so they are
deliberately absent from `_VK_SPECIAL` rather than guessed - an invented
code would post some *other* key. A programmed action on F21-F24 still
works there, because that path never reaches the map.

They are worth having precisely because nothing binds them: no collision
with an app's own F5 or Alt+F4, so a game, OBS or AutoHotkey can take one
outright. `UNBOUND_FUNCTION_KEYS` is that set, surfaced to the editor so it
can say which keys are free before the user commits (reassigning F5 costs
them refresh in every app; reassigning F17 costs nothing).

### Its own panel toggle, not a second line in the F1-F12 row
*Settings -> Function Keys -> Show -> "Extra Function Keys (F13-F24)"*,
independent of the F1-F12 toggle. Someone who wants only the twelve macro
keys must not have to spend the height of the standard row to get them.
The extra row renders **above** F1-F12 so toggling it never moves the row
with muscle memory attached.

### `src/key_actions.py` owns the whole action vocabulary
The bridge switches on **nothing**. `KeyActionStore.execute` dispatches
through a registry of `KeyActionType` records, each of which knows its own
id, how to sanitise its payload, how to describe itself in one line, and
how to execute itself against an `ActionExecutor` (a two-method surface the
bridge implements: `send_chord`, `send_text`). Adding `launch` or `macro`
from the `MODULAR_LAYOUTS.md` vocabulary is **one entry there plus one
method on the executor**, with no branch in `pressSpecialKey` and no QML
edit: the editor builds its picker from `getKeyActionTypes()`, and switches
on the entry's `fields` to decide which inputs to show.

Three types ship:
- **`key`** - sends its own keystroke, and exists so a key can take a
  *custom keycap label without changing what it does*. That is the case
  for a key the user bound inside another app (Discord push-to-talk, an OBS
  scene): they need to find it on screen, and swallowing the keystroke here
  would silently break the very binding the label documents. Its `execute`
  returns **False**, which is what lets it be a peer in the registry rather
  than a special case the bridge has to know about.
- **`hotkey`** - one click fires Ctrl+Shift+S.
- **`text`** - inserts a stored phrase verbatim.

**`execute` returning a bool ("did I handle this tap") is the whole
interface.** False means the key falls through to its own keystroke.

### Where the dispatch sits, and why
Inside `pressSpecialKey`, at the point the keystroke would have been sent,
**not** at the top of the slot with an early return. Everything downstream
then still sees an ordinary special-key press: the sticky auto-release, the
`_NAV_KEYS` exception and the context bookkeeping. Returning early would
skip the auto-release, and a Shift the user tapped once would stay held at
the OS level for every keystroke after it. Guarded by
`tests/test_keyboard_bridge.py::TestProgrammableFunctionKeys::test_a_sticky_modifier_still_auto_releases_after_a_chord`,
paired with the locked-modifier inverse.

**A chord merges the user's held modifiers rather than replacing them**
(`_send_key(..., extra_modifiers=...)`). A macro key bound to Ctrl+S,
tapped while Shift is held, sends Ctrl+Shift+S, exactly as a physical macro
key would. The merge happens inside `_send_key` so the
`_note_own_keystroke` bookkeeping stays in one place; setting those flags
anywhere else is what once made the caret polls read our own inserts as the
user clicking elsewhere.

**A text action *is* `_commit_verbatim_insert`, not a copy of it.**
`_send_text_action` is a one-line call, because a programmed phrase is a
purely literal insert with nothing to add on either end, which is exactly
what that helper is for and what `insertSnippet` and `insertGlyph` already
call. So it inherits the whole prologue rather than restating it:
`_release_sticky_modifiers()` **before** the insert (a held Shift would
otherwise deliver the phrase in capitals, and `_make_char_scancode_events`
cannot cancel a standing hold), the send inside `_without_held_modifiers()`
via `_send_literal_text`, a deferred auto-space settled as prose, an armed
auto-capital spent, and the seven fields both other callers reset. It was
written out inline first, before the helper existed, and the copy was
already one field behind (`_word_prefix_lost`, which arrived with the
helper): parallel blocks drifting is the failure this file warns about for
sticky-modifier release, and this is the same shape. Not gated on privacy
mode: the user tapped the key, so the text must reach the app either way,
and nothing on this path learns or logs its content.

**`pressSpecialKey`'s name map is hoisted to the class**
(`_SPECIAL_KEY_NAMES`) because a programmed chord resolves its action key
through the same map, so `"return"` reaches the synth as `"Return"`. A
second copy inside the slot would be one more pair of parallel blocks to
keep in sync, which is the failure mode this file warns about for the
sticky-modifier release.

### Storage
`key_actions.json` in the config dir, saved synchronously on every mutation
(atomic tempfile-then-rename), same shape and same tolerance as
`snippets.json`: a missing, oversized (256 KB cap), corrupt or partially
invalid file leaves the affected keys unassigned rather than raising, and a
bad entry is dropped **individually** so one unusable assignment does not
cost the eleven the user got right. An unassigned function key still works,
so this path is never allowed to block startup.

**Sanitisation is allow-list, not deny-list**, for the usual reason: a
chord's modifiers and action key are handed to the platform synthesiser,
which on Linux turns them into argv for `xdotool`. Modifiers come from
`MODIFIERS` and are stored in canonical order (so two spellings of one
chord never read as two chords); an action key must be a name from
`CHORD_SPECIAL_KEYS` or a single printable ASCII character, because the
platform layers translate that range and have nothing to say about a
control character or an emoji. A hotkey with **no** action key is refused
outright rather than stored: it would leave a key that looks programmed and
does nothing when tapped, which is indistinguishable from a tap that failed
to register, so the user taps it again. Text follows
`snippets._clean_value` exactly (newline and tab kept, every other C0
control character and DEL stripped, capped).

`setKeyAction` **returns a bool and QML honours it** - the editor flashes
"Saved" only on True, and its failure toast otherwise. A green confirmation
over a write that never happened is the failure `setSnippet` and
`acceptSnippetOffer` were both given bool returns for.

**The slot's bool and `KeyActionStore.set`'s bool are not the same
question, and the slot must not just forward it.** The store answers "did
anything change", which is what decides whether the file is rewritten and
`keyActionsChanged` emitted, so it is False for a valid payload identical
to the one already stored. The slot answers "did my save stick". Those
differ in exactly one case, re-saving an unchanged action, which is an
ordinary thing to do (open the editor on a key that already does what you
want, tap Save) and which read as a red "could not be saved" over state
that was exactly right. The slot therefore re-validates and treats an
unchanged assignment as success, emitting nothing since nothing moved.
Guarded by
`tests/test_keyboard_bridge.py::TestProgrammableFunctionKeys::test_saving_an_unchanged_action_still_reports_success`,
paired with the inverse that a refused key, an invalid payload and an
unknown action type are all still reported as failures: a slot that simply
returned True would satisfy the first on its own.

**Deliberately NOT in the Data Backup archive.** Adding a fourth file to
`_MODEL_FILES` means bumping `data_export.SCHEMA_VERSION` and writing the
back-compatible import path, which this project requires alignment on
before changing. Until then it is machine-local, like the Qt settings layer.

### The editor, and the two routes into it
`qml/components/KeyActionEditor.qml`, a Popup (not the floating Window the
snippets editor uses: that window exists to be dragged clear of the field
being filled in, and this one is not editing anything in the app behind
us). It is kept **short and parked at the top** for the reason that does
apply: the user clicks OSK keys to type a label, so the editor must not
cover the letter grid it is being typed with.

Same two invariants as the prediction-edit popup, both easy to undo:
`modal: false` (a modal popup installs an event-blocking overlay, so no OSK
key would fire and the field could never be typed into) and
`closePolicy: Popup.CloseOnEscape` **only** (every OSK key click is a
press-outside). Keystrokes arrive through the bridge's edit-mode intercept,
never Qt focus. `closePolicyBits` is a plain-int mirror of `closePolicy`
that exists only so the headless test can read it: PySide has no converter
for `QFlags<QQuickPopup::ClosePolicyFlag>`, so an assertion on the real
property errors instead of guarding anything.

This editor is the third surface sharing edit mode (with the prediction
popup and the snippets editor), so it owns the mode under its own name:
`onOpened: keyboard.beginEditSession("keyaction")`,
`onClosed: keyboard.endEditSession("keyaction")`, and its `Connections`
block only listens while `editor.opened && keyboard.editOwner ===
"keyaction"`. A second, unconditionally-enabled `Connections` block closes
the editor the moment another surface takes the mode over
(`onEditOwnerChanged`), the same shape the prediction popup uses and for
the same reason: folding that handler into the ownership-gated block would
race the very `editOwnerChanged` signal that disables it. See *Editing a
Prediction* for why a shared bool was not enough on its own.

**It has to supply the text-box behaviour the window flags take away, and
that is the same three things the snippets editor lists.** Clicks reach the
fields (the `MouseArea` recording which box is being typed into sets
`mouse.accepted = false` and passes the press down, so caret placement,
double-click-for-a-word and drag-select all still work), Tab changes field,
and **Shift with an arrow selects rather than moving the caret**
(`_moveCaret`, reading the injected `shiftOn`). None of the three come for
free: this window never holds OS focus, so Qt's own key handling never sees
the modifier, and without the third there is no way at all to select a range
in a 500-character phrase with an imprecise pointer. `shiftOn` is still true
at that point because the bridge's edit-mode intercept emits and returns
*before* its auto-release block. Guarded by
`tests/test_qml_function_row.py::TestTheEditor`, where the Shift case is
paired with the inverse that a bare arrow still just moves the caret: an
unconditional `moveCursorSelection` would satisfy the first on its own.

**Chord capture is a mode, not a field.** Tapping the "Key" slot sets
`editTarget = "chord"`, and the next key pressed *on the OSK* becomes the
chord's action key - which is the only way to name Enter or an arrow
without a second picker listing every key we can send. The modifier chips
are ordinary buttons in the popup.

**The only route into the editor is *Settings -> Function Keys***, which
lists all twenty-four with what each one currently does and opens the editor
on a tap. Right-clicking an F-key used to open it too; that was removed at
Owen's request (2026-09-13), so a stray right-click on the row never pops an
editor over the letters. Right-click on an F-key now does nothing. Don't add
it back. Guarded by
`tests/test_qml_function_row.py::TestTheSettingsListIsTheLeftClickRoute::test_a_right_click_on_a_key_does_not_open_the_editor`.

**That page replaced an Edit toggle on the row itself**, which flipped both
rows into an assign mode where a left-click opened the editor. The list
answers the same requirement strictly better: its rows are far bigger
targets than a 36 px keycap, there is no mode to get into or out of (the
mode's only exit was the same key that entered it, sitting one pixel from
F12), and it is the only surface that shows an assignment the user has
forgotten making, which twelve identical keycaps cannot. Removing the
toggle also gave the row its thirteenth key's width back. **Don't put a
mode toggle back on the row without first checking that page is gone.**

**Tapping a row hands off to the editor on the keyboard window, and hides
the settings window to do it.** The editor is typed into with the OSK's own
keys and the settings window cannot hold OS focus, so the editor cannot
live inside it (the Deepgram key field carries the same note); leaving a
360x540 window parked mid-screen would cover the editor, the letter grid it
is typed with, or both. `root.settingsReturnView` brings settings back on
the same page afterwards, which is the one documented exception to
"re-opening Settings always lands on the home grid" (see *Settings Panel
Structure*). The inverse matters as much and is tested: an editor opened any
other way must **not** pop the settings window open behind it.

**Every key takes its share of the gap around it.**
`FunctionRow`'s `hitMarginH` / `hitMarginV` default to 0 and there is no
cascade, so a `KeyButton` whose caller forgets to pass them leaves the
strip between it and its neighbour dead (see *Dead space between keys*).
A new key added to an existing row is the likeliest place for that to be
missed, because the row around it already works. The same applies to the
whole F13-F24 panel, which is a second instance of this component and so
needs its own bindings from `Main.qml`. Guarded by
`tests/test_qml_compact_view.py::TestNoDeadStripBetweenKeys::test_every_key_in_every_panel_takes_a_share_too`,
which walks the tree with both function rows switched on and fails on any
key holding a zero margin.

### Geometry: the keys fill the grid, the group gap never gives

**The row spans the keyboard grid exactly, and it is the key width that
absorbs the leftover.** `FunctionRow._fillKeyW` divides `maxWidth` (the
grid width, passed by `Main.qml`) between the twelve keys after taking out
9 internal gaps and 2 group gaps; `_groupGap` is a fixed `keySpacing * 4`.
At a 940 px window that makes an F-key 75.6 px against the 58.7 px key
directly below it, about 29% wider.

**This reverses the earlier decision, on purpose and with the picture in
front of us.** The row used to draw each F-key exactly one grid column wide
and centre the result, which is what the original note in `FunctionRow.qml`
defended against three rejected redesigns that each tried to fill the width
by stretching keys. That note said not to revisit the inset "without
rendering the result next to the number row", which is exactly what was
done the second time, and stretching won: on a keyboard driven by an
imprecise pointer, a quarter more target width outranks lining up with the
column below. The accepted cost is that no F-key lines up with the key
under it any more, and at 29% wider and 30% shorter the row reads a little
bar-like. **The rule survives, pointing the other way: don't change this
back without rendering it next to the number row.**

**The group gap is fixed because a gap that gives is a gap that disappears
exactly when the row is tightest.** It used to be the thing that gave, and
while the Edit toggle made this row 13 keys against compact's 13-unit grid
there were 3 px of slack, so it clamped to `keySpacing` and 4-4-4 rendered
as one undifferentiated run, on the view where telling twelve identical
keys apart matters most. The grouping now survives in both views.

`tests/test_qml_compact_view.py::TestPanelsSitFlushWithTheGrid::test_function_row_fills_the_widest_keyboard_row`
pins three things, and the last two are what a width check alone cannot
see: the panel is flush with the grid; the fill width accounts for 12 keys
plus 9 internal gaps plus 2 group gaps (so a wrong key count or a changed
gap moves it); and the group gap holds at the 4-4-4 width in **both**
views. It used to assert that no key ever grew, which is the assertion this
change reverses.

### Testing notes
`tests/test_key_actions.py` (store, registry, sanitisers, dispatch against a
five-line recording executor), `tests/test_keyboard_bridge.py::TestExtraFunctionKeys`
/ `TestProgrammableFunctionKeys`, and `tests/test_qml_function_row.py`
(headless Main.qml). Every positive case is paired with the near-miss it
must reject, and the pairs that bite are the ones where a payload *looks*
valid: a hotkey with no action key, a modifier name the synth layer has
never heard of, a key name we cannot send.

Two Qt-side traps worth knowing before adding an assertion here:
- **The editor is a `Popup`, so `findChild(QQuickItem, ...)` returns None**
  and every assertion after it silently never runs. `QQuickPopup` is not an
  Item; search for `QObject`.
- **A QML `var` holding a JS array or object arrives as a `QJSValue`**,
  which Python cannot iterate or index. Call `.toVariant()`. This applies to
  the two key registries and to the editor's `chordMods`.

`KeyActionStore` binds `get_config_dir` at module scope, exactly like
`src/snippets.py`, so `tests/conftest.py::_stay_off_the_real_config_dir`
patches `src.key_actions.get_config_dir` by name. Without that line the
suite rewrites the developer's own key assignments, which is the same
failure the snippet store already had once.
