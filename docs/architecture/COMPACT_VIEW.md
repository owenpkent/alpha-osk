# Compact View

A denser keyboard for small screens, shipped as a **view preference** —
*Settings → Appearance → Panels → Compact View*. Off by default; the full-size
layout is untouched.

## Why the full-size layout is wide

`qwerty.json` is a faithful replica of a 104-key physical keyboard. Most of what
makes it wide exists to serve *ten fingers resting on a home row*, which is not
how Alpha-OSK is ever used — the user clicks one key at a time with a pointer.

`Main.qml` derives `keyW` from the **widest** row, and every narrower row is
centred (`Layout.alignment: Qt.AlignHCenter`), so the slack becomes symmetric
side gutters. Measured at the default 940 px window:

| Row | Units | Dead space | Per side |
|---|--:|--:|--:|
| number | 15.50 | 0 px | — |
| top (qwerty) | 14.30 | 71 px | 36 px |
| home (asdf) | 14.40 | 68 px | 34 px |
| bottom (zxcv) | 14.30 | 75 px | 38 px |
| **space** | **11.60** | **243 px** | **122 px** |

The space row alone wastes 26% of the keyboard's width, and it scales
proportionally — it is just as bad at every window size.

## The measurement that shaped the design

Before rearranging anything, mean pointer travel was measured across four
candidate arrangements, weighting every character-pair transition by its
frequency in English (Zipf-weighted over the Google 10k wordlist, with word
boundaries counted so Space transitions are realistic):

| Arrangement | Footprint | Mean travel |
|---|--:|--:|
| Current (desktop mirror) | 15.5u × 5 | 195 px |
| Compact 10×4 | 10u × 4 | 189 px |
| Compact 13×4 (shipped) | 13u × 4 | 186 px |
| Square 7×6 (QWERTY wrapped) | 7u × 6 | 179 px |

**Rearranging the letters buys nothing.** Everything lands within 8%, because
QWERTY adjacency dominates the distribution and every variant preserves it. Even
the radical square wrap saves only 8% while destroying the visual scan — and
QWERTY *is* the visual index for a user who reads the keyboard rather than
touch-types it.

So the letters stay exactly where they are. The win comes entirely from deleting
what a pointer cannot use:

| Deleted | Reclaimed |
|---|--:|
| Duplicate right-hand Shift / Ctrl / Alt | 266 px |
| Space at 6.0u → 3.0u | 173 px |
| Dedicated number row (→ `?123` layer) | one full row of height |
| Full-size Esc / `` ` `` / `[` / `]` / `\` | ~5u |

Net at identical key size: **−33% area, −16% width, −20% height**, and mean
travel improves 5%. At a 940 px window, keys grow **58 → 69 px (+20%)**.

## The layout

Thirteen columns, four rows. **Every row totals exactly 13.0 units**, so there
is nothing left to centre and the gutters vanish *by construction* — no
stretching or justification logic exists anywhere in the QML.

```
Base layer                               ?123 layer
  q w e r t y u i o p [ ⌫ ] Home           1 2 3 4 5 6 7 8 9 0 [ ⌫ ] Home
 Tab a s d f g h j k l ' Del PgUp         Tab - = [ ] \ ; ' ` Ins Esc Caps PgUp
  ⇧ z x c v b n m , / [Enter] PgDn        =\< ! @ # $ % : & ( ) [Enter] PgDn
 ?123 Ctl ⊞ Alt [space] . ← ↑ ↓ → End     ABC Ctl ⊞ Alt [space] . ← ↑ ↓ → End

=\< layer (second symbol page)
  ~ ^ * _ + { } | < > [ ⌫ ] Home
 Tab ° × ÷ ± ≈ ≠ ≤ ≥ Ins Esc Caps PgUp
 ?123 € £ ¥ ¢ § • © ® ™ [Enter] PgDn
 ABC Ctl ⊞ Alt [space] . ← ↑ ↓ → End
```

**Three layers, and Shift is not one of them.** The `?123` page used to carry
a Shift key, which re-rendered its row 1 as `! @ # $ % ^ & * ( )` while row 3
already showed `! @ # $ % : & ( )` permanently: nine keys on screen saying the
same thing as another key on screen. Shift's slot is now a switch to a second
symbol page (`=\<`), the phone convention, so every glyph Shift used to reach
has a key of its own and the overlap is *structurally impossible* rather than
merely absent. All three layers are 13.0u with matching key counts
(12/13/12/11), so hopping pages never resizes a key. The `shifted` fields stay
on the symbol keys: right-click still types them, and right-click output is
never displayed, so it is a bonus rather than a duplicate.

**The nav column reads top to bottom as a scroll ladder**: Home, PgUp, PgDn,
End. Jump to the top, page up, page down, jump to the bottom.

**Del and Esc trade layers.** A 13u row has no spare unit, so putting
forward-delete on the base layer had to cost something, and Esc was the only
key there that isn't in the protected set below. Backspace-only editing means
walking the caret past a mistake and back, which is several extra clicks with a
pointer; Esc is comparatively rare in text entry. `Enter`/`Backspace` staying 2u
and the nav column staying put both rule out the alternatives.
`tests/test_layouts.py::TestCompactLayout::test_esc_is_still_reachable_from_the_sym_layer`
guards that this stayed a trade rather than becoming a deletion.

Design rules, all enforced by `tests/test_layouts.py`:

- **The bottom row and the nav column are identical on every layer.** Space, the
  modifiers, the period, the arrows and Home/PgUp/PgDn/End hold their exact
  position across a layer switch, so nothing reached for constantly moves under
  the pointer. The guarding tests derive the layer list from the file rather
  than naming layers: written against a hardcoded base/sym pair, they were blind
  to the second symbol page, which shipped with a bullet where every other layer
  has a period.
- **Arrows, Enter, Home, End, PgUp, PgDn and `/` are never behind a hop.** These
  were named explicitly as high-frequency keys.
- **Enter and Backspace stay 2u.** Both are high-frequency and Backspace
  additionally auto-repeats, so a 1u target would regress against full size.
- **Right-click covers the shifted variants**, so the base layer reaches more
  than it shows: `/`→`?`, `,`→`<`, `.`→`>`, `'`→`"`.
- **`:` gets a dedicated key on the `?123` layer**, in the slot `^` used to
  hold. Row 2 of that layer already carries `;`→`:` as a shifted variant, but a
  shifted variant is invisible: the keycap reads `;` and nothing on screen says
  a colon is one right-click away, so in practice the layer read as "no colon".
  Row 3 exists to surface exactly those shifted glyphs as their own keys, and it
  was already one short of the full set (`*` is missing for the same 13u
  reason), so `^` — the rarest of the nine in prose — pays for it. `^` is
  unchanged on row 1 as the shifted variant of `6`.
- `.` sits beside Space (phone convention) rather than next to `,`; that is what
  pays for `/` on row 3 without a fourteenth column.

## Getting the digits back without leaving compact

Compact renders a standalone `Esc` `1`–`0` `-` `=` strip above the keyboard
(`qml/components/NumberRow.qml`). Thirteen 1u keys, so it is exactly 13.0u and
sits flush over a compact grid with no gutters.

**There is no toggle.** `Main.qml::showNumberRow` is derived: true exactly when
the active layout JSON carries no row with `id: "number"`. The full-size
layouts all have one; the compact variants do not, so the panel fills in for
precisely the layouts that are missing it. Digits are therefore always on
screen in both views, and a full-size layout can never end up with this
narrower, centred strip stacked on top of the number row already inside its
JSON. It keys off the layout rather than off `compactView` because a letter
arrangement with no compact variant silently falls back to full size.

It is a panel rather than a fifth row in the layout JSON because the compact
layout's three layers must each be four rows of 13u
(`test_has_three_layers_of_four_rows`), and because a panel is independent of
which letter arrangement is selected. The digits behave like any other char key: shift shows and types the
shifted glyph, right-click types it without flipping sticky shift, both flash
the key preview, and every digit registers in `charKeyRegistry` so the swipe
overlay passes taps through instead of swallowing them.

**The leading slot is `Esc`, not the physical keyboard's `` ` ``.** The Del/Esc
trade above put Esc behind a hop, and "get me out of this dialog" is a bad key
to make people navigate to. This row restores it at the top-left corner where a
real keyboard keeps it. Nothing is lost: `` ` ``→`~` stays on `?123` row 2, and
the full-size layouts carry their own `` ` `` in the layout JSON. The Esc here
duplicates the `?123` one deliberately, so `?123` stays the fallback for any
future layout that shows the compact grid without this panel.

Two consequences of Esc being a special key rather than a char key: it takes no
key-preview bubble (a bubble over Esc isn't "what it typed", matching the main
grid), and it is **not** in `charKeyRegistry`, because a phantom "Esc" centre
would corrupt every swipe shape match. That makes it a dead tap while swipe
typing is on — which is exactly how Backspace, Tab and Enter already behave
under the overlay, not a regression specific to this row.

## Compact turns the side panels off

The Navigation cluster and the Numpad cost roughly 470 px of window width,
which is exactly what compact exists to hand back, so the three are no longer
independent: turning compact on forces both panels off, and their toggles
render disabled (with a reason on the second line) while it is on. Offering all
three freely meant a user could pick the small-screen mode and then, one toggle
later, silently undo it.

`onCompactViewChanged` in `Main.qml` does the forcing; the restore is the half
that is easy to break. `onShowNavigationChanged` / `onShowNumpadChanged` skip
their `appSettings` write while `compactView` is true, so the forced-off state
never overwrites the user's real preference, and leaving compact reads it back
out and restores whatever they had. `Component.onCompleted` applies the same
rule on a cold start (`savedShowNavigation && !compactView`), otherwise
quitting in compact would bring the panels back on next launch. Guarded by
`tests/test_qml_compact_view.py::TestCompactViewForbidsTheSidePanels`.

## How layers work

Layers are a **QML-side view concept** — the Python and C++ backends know
nothing about them, which is why the compact view needed no backend change on
either.

- Rows in a layout JSON may carry a `"layer"` field. `Main.qml` filters
  `layoutRows` into `visibleRows`, keeping rows whose layer matches
  `root.activeLayer`. **Rows with no `layer` field always render**, so the
  full-size layouts are unaffected.
- A key of `"type": "layer"` with a `"target"` sets `root.activeLayer`. It
  deliberately does **not** call `keyboard.setLayout()` — that would persist as
  the user's layout preference and make `getCurrentLayout()` report the symbol
  layer.
- `activeLayer` resets to `"base"` on any layout change (`onLayoutDataChanged`
  and `applyLayout`). Leaving a user on a `sym` layer that the next layout does
  not define would render an empty keyboard.

## Sizing is derived, not hardcoded

`totalKeyUnits` and the gap count used by `layoutFixedPixels` are computed from
the widest **visible** row (`_widestRow` in `Main.qml`) rather than the former
hardcoded `15.5` / `14`. Full-size layouts resolve to exactly those historical
numbers, so the default 940 px window is unchanged; the compact view resolves to
13.0 / 12. Adding a layout with a different column count now Just Works.

## Compact is orthogonal to letter arrangement

`currentLayout` remains the letter arrangement (`qwerty` / `dvorak` / `colemak`)
and `compactView` is a separate boolean. `resolveLayoutId()` combines them:
`qwerty` + compact → `qwerty-compact`. **A layout with no `-compact` variant
falls back to full size**, so the toggle is always safe — today only QWERTY has
one. Compact variants are filtered out of the Settings layout picker
(`pickableLayouts`) so the user cannot pick a letter arrangement and a density
from the same control and get a contradiction.

To add a compact Dvorak, drop `data/layouts/dvorak-compact.json` in place. No
code change — `_load_layouts` globs the directory.

## Window resizing on toggle

`onCompactViewChanged` resizes the window to preserve **key size** rather than
window width. Giving the screen back is the entire point of the feature; keeping
the window fixed and merely growing the keys would miss it. The user can still
resize freely afterwards.

## Accent-filled editing keys

Esc, Tab, Shift, Backspace and Del carry `"style": "accent"` in the compact
layout JSON, resolved by `root.accentKeyColor` in `Main.qml`. The compact grid
is uniform, so unlike the full-size layouts there are no size cues to tell the
editing keys apart from the letters: they have to be findable by colour.
Full-size layouts are deliberately untouched.

The fill is a **wash of the accent over the theme's key colour, not the raw
accent**. Three themes have a pale accent (Blackboard `#ffffaa`, Spaceship
`#00ff9f`) and Typewriter is a light theme with near-black text, so a saturated
fill would destroy the label contrast. Same reason Enter is a muted `#2a5a2a`.

**The wash strength is derived, not fixed.** A flat 35% was measured against all
nine themes and dropped the label below WCAG AA on five of them:

| Theme | Contrast at 0% wash | at a flat 35% |
|-------|--------------------|---------------|
| Blackboard | 6.19:1 | 2.66:1 |
| Vaporwave | 6.17:1 | 2.97:1 |
| Forest | 7.53:1 | 3.33:1 |
| Spaceship | 10.37:1 | 3.85:1 |
| Ocean | 6.96:1 | 4.44:1 |

That is the worst place in the UI to lose contrast, because these are the exact
keys the style exists to make findable, and Forest could not be rescued by
swapping the label to black or white either (best case 4.37). So
`root.accentWashFor()` walks the alpha down from 0.35 until the theme's own
`textColor` clears 4.5:1. **Don't reintroduce a constant here.**

Accent keys also take an accent-coloured border, which carries the cue on the
themes where the wash has to back off to 0.12-0.21; a border sits beside the
label rather than behind it, so it costs no contrast.

Pinned by `tests/test_layouts.py::TestCompactEditingKeysAreAccented` (which
keys) and `tests/test_qml_compact_view.py::TestAccentKeysStayReadable` (the
contrast floor, plus the inverse test that the wash is still visible, so "stop
tinting" cannot pass as a fix).

## No panel that lines up with the grid may use `QtQuick.Layouts`

Number Row and Function Row are plain `Row`s, Navigation is a plain `Grid`,
Numpad is a `Column` of `Row`s.

`Main.qml` reserves an exact float unit budget for each panel when it derives
`minimumWidth`, so a rounding positioner costs pixels the window was never
given. `QtQuick.Layouts` rounds every child up to a whole pixel: 13 keys of
69.23 px each became 13 of 70, and the panel rendered 10 px wider than the
keyboard grid it is supposed to sit flush with, overhanging the window and
clipping its last key. The keyboard rows are plain `Row` positioners, which keep
`keyW` as the float it is; a panel that sizes itself any other way cannot line
up with the keys underneath it.

Guarded by `tests/test_qml_compact_view.py::TestPanelsSitFlushWithTheGrid`,
which asserts the panel width equals the widest keyboard row rather than merely
fitting the window, because "fits" was already true of the broken version at
some widths.

## Leaving a letters page never carries Shift onto a symbol page

`Main.qml`'s layer branch calls the idempotent `keyboard.releaseShift()` on
every switch, never `if (shiftOn) toggleShift()`: `root.shiftOn` is a mirror
kept alive by signal delivery rather than a live binding, so a flip could turn
Shift *on* here.

It is load-bearing because the modifier is held at the OS level. A Shift carried
in from the letters page would make `1` emit `!` while the keycap still read
`1`, and the symbol pages have no Shift key to clear it from. Caps Lock is left
alone, since it only affects letters.

## Adding a compact layout

1. Every row must total the same unit count, or the gutters return.
2. Tag each row with `"layer"`; name the entry layer `"base"`.
3. Give every non-base layer a `"type": "layer"` key pointing back to `"base"`,
   or it becomes a dead end (`test_every_layer_is_reachable`).
4. Keep the bottom row and any edge column identical across layers.
5. Name the file `<base-layout>-compact.json` with a matching `id`.

`tests/test_layouts.py` enforces 1–5 structurally;
`tests/test_qml_compact_view.py` loads the real `Main.qml` headlessly
(`QT_QPA_PLATFORM=offscreen`) and asserts the layer switching and derived
sizing behave, since a QML binding error is a runtime warning that would
otherwise ship as a blank keyboard.
