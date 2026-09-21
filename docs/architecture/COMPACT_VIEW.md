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
| Dedicated number row (→ standalone panel) | one full row of height |
| Full-size Esc / `` ` `` / `[` / `]` / `\` | ~5u |

Net at identical key size: **−33% area, −16% width, −20% height**, and mean
travel improves 5%. At a 940 px window, keys grow **58 → 69 px (+20%)**.

## The layout

Thirteen columns, four rows. **Every row totals exactly 13.0 units**, so there
is nothing left to centre and the gutters vanish *by construction* — no
stretching or justification logic exists anywhere in the QML.

```
Base layer                                ?123 layer
 Tab q w e r t y u i o p ⌫ Home            Tab ! @ # $ % ^ & * ( ) ⌫ Home
Caps a s d f g h j k l ' Del PgUp         Caps [ { ] } \ | ; : Ins Esc " PgUp
  ⇧ z x c v b n m , / [Enter] PgDn          ` ~ < > _ + ° € £ ™ [Enter] PgDn
 ?123 Ctl ⊞ Alt [space] . ← ↑ ↓ → End      ABC Ctl ⊞ Alt [space] . ← ↑ ↓ → End
```

**`w` sits above `s`, and that cost Backspace a unit.** The top row used to
open with `q` while the home row opened with Tab, so every home-row letter sat
one column right of the letter above it: `w` was over `a`, and the number row
panel (whose own leading slot is `Esc`) put `1` over `w` rather than over `q`.
On a grid where every key is the same size, that column relationship is most of
what a user reads the board by, and it is the one thing full size gets for free
(there, `w` is over `s` because Tab and Caps are the same width).

Compact now gets it the same way: both letter rows open with a 1u key, Tab over
Caps, which is also where a physical keyboard puts them. The left edge reads
`Esc` / `Tab` / `Caps` / `⇧` / `?123` straight down, Caps stops being behind a
hop, and the digits line up with the letters they belong to. A 13u row has no
spare unit, so the top row's new slot had to be paid for, and Backspace's
second unit is what paid: it is still accent-filled, still auto-repeats, and
still has the whole right edge of the row to itself.

**Two layers, and Shift is not one of them.** The `?123` page used to carry a
Shift key, which re-rendered its row 1 as `! @ # $ % ^ & * ( )` while row 3
already showed `! @ # $ % : & ( )` permanently: nine keys on screen saying the
same thing as another key on screen. Shift's slot became a switch to a second
symbol page (`=\<`), the phone convention, so every glyph Shift used to reach
had a key of its own and the overlap was *structurally impossible* rather than
merely absent. Both layers are 13.0u with matching key counts (13/13/12/11),
so hopping pages never resizes a key. The `shifted` fields stay on the symbol
keys: right-click still types them, and right-click output is never displayed,
so it is a bonus rather than a duplicate.

**The digits are not on `?123`, and the second page went with them.**
Reported: "compact mode symbol mode duplicates number row". The page opened
with `1 2 3 4 5 6 7 8 9 0`, and compact's standalone number row panel is on
screen on *every* layer (it is derived from the layout carrying no `number`
row of its own, not from a toggle), so hopping to `?123` put two identical
digit rows one above the other. The page was also repeating `-` and `=` on
row 2, and its row 3 (`! @ # $ % : & ( )`) was the number row's own
right-click set. Twelve of twenty-eight symbol slots were being spent on
glyphs already on screen.

Reclaiming them is what made one page enough. Row 1 is now the number row's
shifted set in digit order, which is the thing a keycap cannot show you (the
cap reads `6`, and nothing on it says `^` is one right-click away), and rows 2
and 3 carry every remaining ASCII symbol. The `=\<` page's thirteen non-ASCII
glyphs (`× ÷ ± ≈ ≠ ≤ ≥ ¥ ¢ § • © ®`) moved to the Symbols & Emoji window,
which is the same trade that removed the full-size `Sym` page: a picker is for
browsing and a key is for reaching something whose position you already know,
and nobody reaches for `≥` from muscle memory.

The alternative fix was to hide the number row panel while a symbol page is
up. It is a one-property change and it is wrong here: the compact keyboard
would lose a whole row of height on the hop, so every key below it, including
the `ABC` key you leave by, moves under the pointer. Nothing else in this
layout is allowed to move on a layer switch, and the digits would stop being
reachable without one.

**The nav column reads top to bottom as a scroll ladder**: Home, PgUp, PgDn,
End. Jump to the top, page up, page down, jump to the bottom.

**Del and Esc trade layers.** A 13u row has no spare unit, so putting
forward-delete on the base layer had to cost something, and Esc was the only
key there that isn't in the protected set below. Backspace-only editing means
walking the caret past a mistake and back, which is several extra clicks with a
pointer; Esc is comparatively rare in text entry. Enter staying 2u and the nav
column staying put both rule out the alternatives.
`tests/test_layouts.py::TestCompactLayout::test_esc_is_still_reachable_from_the_sym_layer`
guards that this stayed a trade rather than becoming a deletion.

Design rules, all enforced by `tests/test_layouts.py`:

- **The bottom row and the nav column are identical on every layer.** Space, the
  modifiers, the period, the arrows and Home/PgUp/PgDn/End hold their exact
  position across a layer switch, so nothing reached for constantly moves under
  the pointer. The guarding tests derive the layer list from the file rather
  than naming layers: written against a hardcoded base/sym pair, they were blind
  to the second symbol page, which shipped with a bullet where every other layer
  had a period. That page is gone, but the derivation stays: it is what makes
  a page added later covered for free.
- **Arrows, Enter, Home, End, PgUp, PgDn and `/` are never behind a hop.** These
  were named explicitly as high-frequency keys.
- **Enter stays 2u.** It is high-frequency and it is the one key on the grid
  with no neighbour to confuse it with, so it keeps the second unit Backspace
  gave up to the column alignment above.
- **Right-click covers the shifted variants**, so the base layer reaches more
  than it shows: `/`→`?`, `,`→`<`, `.`→`>`, `'`→`"`.
- **Every glyph the `?123` layer offers has a key of its own**; none is
  reachable only by right-click. A shifted variant is invisible: the keycap
  reads `;` and nothing on screen says a colon is one right-click away, so for
  a while the layer read as "no colon". `:`, `{`, `}`, `|` and `~` each have a
  cap now, and `tests/test_layouts.py::test_every_shifted_variant_on_a_symbol_
  page_has_its_own_key` states the rule rather than the instances. It is also
  the constraint that sets the page's size: 29 slots, so 29 glyphs, which is
  why the digits going back to the number row panel is what bought the second
  page's removal rather than merely tidying it. The twenty-ninth is `"`, which
  landed there when Caps moved to the head of the row: it was the one common
  ASCII glyph compact had no key of its own for, and the row already pairs a
  base glyph with its shifted twin (`[` `{`, `]` `}`, `\` `|`, `;` `:`).
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
layout's two layers must each be four rows of 13u
(`test_has_two_layers_of_four_rows`), and because a panel is independent of
which letter arrangement is selected. The digits behave like any other char
key: shift shows and types the shifted glyph, right-click types it without
flipping sticky shift, and both flash the key preview.

**Being derived, it is on screen on every layer, so no layer may draw digits
of its own.** That is not a style rule, it is the arithmetic: a digit on
`?123` is this panel rendered twice, one row apart, which is what was
reported and what `test_no_digit_appears_on_a_symbol_layer` now refuses. The
panel is where the digits live in compact; a page that wants one should
right-click the panel's own key or take the hop back to it.

**The leading slot is `Esc`, not the physical keyboard's `` ` ``.** The Del/Esc
trade above put Esc behind a hop, and "get me out of this dialog" is a bad key
to make people navigate to. This row restores it at the top-left corner where a
real keyboard keeps it. Nothing is lost: `` ` ``→`~` stays on `?123` row 2, and
the full-size layouts carry their own `` ` `` in the layout JSON. The Esc here
duplicates the `?123` one deliberately, so `?123` stays the fallback for any
future layout that shows the compact grid without this panel.

One consequence of Esc being a special key rather than a char key: it takes no
key-preview bubble, since a bubble over Esc isn't "what it typed", which
matches the main grid.

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

---

# Implementation notes (from CLAUDE.md)

Moved verbatim from `CLAUDE.md` on 2026-09-20, which keeps a summary of the load-bearing rules. This is the full reasoning.

## Compact View

A denser 13x4 keyboard for small screens. Off by default; toggle in *Settings ->
Appearance -> Panels -> Compact View*. The design, the measurements behind it,
and the full rationale for every rule below live in
`docs/architecture/COMPACT_VIEW.md`.

Load-bearing rules:

- **Every row in a compact layout must total the same unit count** (13.0 for
  `qwerty-compact`). `Main.qml` centres any narrower row, so an unequal row
  brings back the exact side gutters this view exists to remove. Enforced by
  `tests/test_layouts.py::TestCompactLayout::test_every_row_is_exactly_13_units`.
- **Layers are a QML-side view concept and the backends never see them.** Rows
  carry an optional `"layer"` field and rows without one always render, which is
  what keeps the full-size layouts working; a `"type": "layer"` key sets
  `activeLayer` and deliberately does **not** call `keyboard.setLayout()` (that
  would persist as the user's layout preference). `activeLayer` resets to
  `"base"` on every layout change. Because the whole feature is data + QML, it
  needed zero backend work on either backend (Python on `main`, C++ on
  `cpp-rewrite`): don't "port" it.
- **`totalKeyUnits` is derived, not hardcoded** (`_widestRow` in `Main.qml`
  computes the widest visible row's units + gap count, and full-size layouts
  resolve to exactly the historical 15.5u / 14 gaps). Don't reintroduce the
  constant.
- **Compact is orthogonal to letter arrangement.** `resolveLayoutId()` combines
  `currentLayout` with the `compactView` bool into `<layout>-compact`, and a
  layout with no compact variant falls back to full size, so the toggle is always
  safe. Adding compact Dvorak is dropping `data/layouts/dvorak-compact.json` in
  place, no code change.
- **No panel that has to line up with the keyboard grid may use
  `QtQuick.Layouts`.** It rounds every child up to a whole pixel, so 13 keys of
  69.23 px each became 13 of 70, and the panel rendered 10 px wider than the grid
  it sits flush with, overhanging the window and clipping its last key. Number
  Row and Function Row are plain `Row`s, Navigation a plain `Grid`, Numpad a
  `Column` of `Row`s. Guarded by
  `tests/test_qml_compact_view.py::TestPanelsSitFlushWithTheGrid`.
- **The accent fill on the editing keys (Esc, Tab, Shift, Backspace, Del) is a
  derived wash over the theme's key colour, never the raw accent and never a
  constant.** `root.accentWashFor()` walks the alpha down from 0.35 until the
  theme's own `textColor` clears 4.5:1; a flat 35% dropped five of the nine
  themes below WCAG AA, on exactly the keys the style exists to make findable.
  The accent-coloured border carries the cue where the wash has to back off.
  Full-size layouts are deliberately untouched.
- **Rows 1 and 2 open with Tab and Caps, so `w` sits above `s`.** Full size
  reduces the same property to Tab and Caps being the same width; compact makes
  it the same way, and pays for the extra slot out of Backspace's second unit
  (1u on compact, 2u everywhere else). Both keys lead rows 1 and 2 on `?123`
  too: a symbol page leading with a glyph would move them under the pointer on
  every layer hop. Guards: `TestCompactLayout::test_w_sits_above_s` and
  `::test_the_left_column_is_the_same_on_both_layers`.
- **Del sits on the base layer, Esc on `?123`.** A 13u row has no spare unit, so
  the two traded places. The Number Row panel puts a second Esc back at the
  top-left and that duplicate is deliberate, so `?123` stays the fallback for a
  future layout that shows the compact grid without the panel. Don't swap them
  back without reading the rationale in the design doc.
- **The symbol page carries no Shift key.** Shift's slot became a second page
  (`=\<`), which made a glyph appearing twice on one screen *structurally
  impossible* rather than merely absent; reclaiming the digits from row 1 then
  made one page enough and the second page went. Shift does not come back: the
  modifier is held at the *OS* level, so a held Shift would make a key emit one
  glyph while displaying another, on a page with no Shift key to clear it from.
  The bottom row and the right-hand nav column are byte-identical on every
  layer, and the tests that guard that derive the layer list from the file
  rather than naming base/sym.
  `Main.qml`'s layer branch calls the idempotent `keyboard.releaseShift()` on
  every switch (never `if (shiftOn) toggleShift()`), because the modifier is held
  at the OS level and a Shift carried in from the letters page makes `1` emit `!`
  with the keycap still reading `1`. Guarded by
  `tests/test_layouts.py::TestNoDuplicateGlyphsWithinALayer` and
  `tests/test_qml_compact_view.py::TestSecondSymbolPage`.
- **Digits come back via a panel, not a fifth row, and not via a toggle.**
  `qml/components/NumberRow.qml` (13 x 1u, flush with the compact grid) renders
  above the keyboard whenever `Main.qml::showNumberRow` is true, which is
  derived: true exactly when the active layout JSON carries no `number` row of
  its own. That is the compact variants and nothing else, so digits are always
  on screen in both views and a full-size layout can never end up with a
  second, narrower number row stacked on the one built into its JSON. Keying it
  off the layout rather than `compactView` matters because a letter arrangement
  with no compact variant silently falls back to full size. Its leading key
  is **Esc, not `` ` ``** (backtick lives on `?123` row 2).
- **The panel is declared BELOW both function rows in `Main.qml`'s column**,
  so the stack reads F13-F24, F1-F12, digits, letters. It was declared first
  for one release, which on compact put F1-F12 between the digits and the
  letters: nothing on a desk stacks that way, and it read as the F-keys
  having been dropped into the middle of the keyboard. Full size never had
  the fault, because there the digits are the first of the data-driven rows
  and so already sit under both panels, which is exactly why this is worth
  pinning: the two views build the same stack out of different pieces and
  agree only by construction. Guarded by
  `tests/test_qml_compact_view.py::TestTheRowsStackLikeAPhysicalKeyboard`,
  which runs both views, and whose full-size half passes either way on
  purpose, as the guard against them parting.
- **The nav column reads Home / PgUp / PgDn / End top to bottom** (a scroll
  ladder: top, page up, page down, bottom; Owen asked for Home above PgUp).
  Pinned by
  `test_layouts.py::TestCompactLayout::test_nav_column_reads_top_to_bottom`.
- QML-only behaviour can't be covered by the Python suite, so
  `tests/test_qml_compact_view.py` and `tests/test_qml_prediction_bar.py` load the
  real `Main.qml` headlessly (`QT_QPA_PLATFORM=offscreen`) and fail on QML
  warnings. That's the only guard against a binding error shipping as a blank
  keyboard.
