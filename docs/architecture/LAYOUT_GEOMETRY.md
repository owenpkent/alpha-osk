# Layout geometry

Moved verbatim from `CLAUDE.md` on 2026-09-20, which keeps a summary of the load-bearing rules. This is the full reasoning.

## Dead space between keys

Every `KeyButton`'s MouseArea reaches half a gap past the key's own slot
(`hitMarginH` / `hitMarginV`, anchored with negative margins), so two
neighbours meet in the middle of the gap between their caps and no strip of
the grid types nothing.

The gap was `keySpacing` wide horizontally (1 px below a 1111 px window, 2 px
above) and `rowSpacing` plus the positioner's rounding vertically, and a click
landing in it was a **silent** miss. That is strictly worse than a click on
the wrong key, which the prefix beam and the pointer-bias model between them
usually recover, because no character is emitted for the engine to see at all.

Four things about it are load-bearing:

- **Half each, not all of it.** Two hit areas that overlap resolve to
  whichever key was declared later (the right-hand or the lower one), never
  the nearer one, so an over-generous margin quietly hands every borderline
  click to the same side.
- **The vertical share carries an extra half pixel, and that is not slop.** A
  `Row` reports a height ceiled above its tallest key (53 against 52.719 at
  one width), and the remainder sits below the keys, inside no key, on top of
  `rowSpacing`. The gap between two rows is therefore `rowSpacing` plus up to
  a pixel neither row can predict, so each key takes half a pixel more than
  its half. Vertical neighbours then overlap by under a pixel instead of
  leaving a strip under a pixel wide, which is the right way round: an
  overlap resolves to the lower key, a gap resolves to nothing at all.
- **`mouse.x` / `mouse.y` are relative to the enlarged area**, so the press
  handler subtracts the margin back out before the ripple's origin and
  `pressDx` / `pressDy`. A press in the gap then reads just past +/-0.5,
  which is true, and is signal rather than noise for the pointer-bias model
  (`PointerModel` clamps at 1.0).
- **A new key or panel has to be passed both margins**; there is no cascade
  and the default is 0, so a forgotten binding is a silently dead strip
  rather than an error. `Main.qml` owns the numbers (`keyHitMarginH` /
  `keyHitMarginV`, plus `panelHitMarginV` for the two side panels, which lay
  their own rows out on `keySpacing` rather than `rowSpacing`) and hands them
  to the grid delegate and to each of the four panels.

Two alternatives were rejected. Growing the keycaps to fill the gap changes
what the keyboard looks like. One MouseArea over the whole grid resolving
each press to the nearest key is the swipe overlay's design flaw exactly (see
*Removed: Swipe / Glide Typing*): an interceptor that owns every press turns
every key it does not know about into a dead tap, and that bill was paid
three times.

`FunctionRow`'s deliberate `keySpacing * 4` between its three groups keeps a
dead strip in the middle of it. That is the same trade as the gutter between
two panels: a separator, not a gap nobody meant to leave.

Guarded by `tests/test_qml_compact_view.py::TestNoDeadStripBetweenKeys`, which
measures the live MouseArea rather than recomputing the rectangle from the
same properties the QML sets, and which drives a real click at a whole pixel
lying strictly between two key slots. That last part is the half that matters:
no geometric assertion can tell you whether Qt still delivers a press to a
child outside its parent's bounds, which is what the whole approach rests on.

## Removed: the full-size symbol layer

`qwerty` / `dvorak` / `colemak` briefly carried one symbol page of 34 glyphs
(`° × ÷ ± € £ © ™ … → ¿`), reached from a `Sym` key at each end of the
space row. It was removed on 2026-09-05, and the room those two keys held went
to the space bar. Both halves had shipped together in #51.

**Why.** Every one of the 34 is also in the Symbols & Emoji window below,
which is one click away in the suggestion bar on every layout, so the layer
was a second route to a set that already had one, and it charged the two
widest keys on the space row after the space bar for it. The two surfaces are
genuinely different, which is why the layer was built: the picker is a
*browsing* surface (categories, paging, a Recent page, a window that floats
over the app you are typing into), the layer was a *positional* one (two
clicks, nothing covering the screen, findable by memory). That distinction is
not worth a fifth of the space row to a pointer that reaches for the space bar
after every word.

**What went with it**: the `sym-top` / `sym-home` / `sym-bottom` rows and the
`"layer": "base"` fields on the three letter rows across the three full-size
layout files (both were added by the same feature, so the files are back to
declaring no layers at all), the two `Sym` keys, the `symLayer` case in
`Main.qml`'s `isActive` switch, and the "a layer key whose target is already
showing goes back to base" branch beside it. That branch existed only for
`Sym`, whose entry key sat on the always-visible space row; every other layer
key in the project targets something it is not on, so it was dead for them.
`TestNoDuplicateGlyphsWithinALayer`'s helper still folds an unlayered row into
whichever layer is being read, which is what compact needs and what full size
needed before this page existed. All of it is recoverable in full from the
commit before the removal.

**The one thing that must not be undone.** The space bar's centre stays at
8.25u, which is what makes the widening free rather than something to relearn:
every click that landed on it before still lands on it, and the new target is
added at both ends. On a flush row that centre is `(15.5 + left - right) / 2`,
and since `left` is Ctrl + Win + Alt and `right` is Alt + Ctrl, the whole
expression collapses to `(15.5 + Win) / 2` **as long as the four Ctrl / Alt
keys are equal**. So the rule to keep if these widths are ever retuned is just
that: Win stays 1.0u, and the four Ctrl / Alt keys stay equal to each other.
Nothing else about the row matters to the centre. What moved instead is
Ctrl / Win / Alt, outward on both sides, taken deliberately: the space bar is
pressed after every word and those five are not. Pinned by
`tests/test_layouts.py::TestTheFullSizeSpaceRow`.

Two things this deliberately did **not** touch. Del stays off the full-size
grid and Enter stays at 2.3u: those were the other half of the same commit and
are what puts Q over A (see *Why nothing moves* under
`TestTheLetterColumnsLineUp`). And Compact View's `?123` / `=\<` pages are
**not** removable by the same argument: 13 units cannot hold letters and
digits at once, so compact has no other route to either, and the picker is not
a substitute for a digit.

## Full-size rows are flush (every row is 15.5u)

`Main.qml` centres each row against the widest one, so a row totalling less
than the widest sits inside it by half the difference at each end. The
full-size rows used to total 15.5 / 14.3 / 14.9 / 14.3 / 14.6, so the
keyboard's left and right edges stepped in and out five times, by up to 0.6u
(about 9 px at the default window). Reported as the keyboard looking "lumpy",
which is the right word: nothing was wrong with any single key, but no two
rows began in the same place.

**This is the rule Compact View has enforced from the start** (*every row in a
compact layout must total the same unit count*, see that section), applied to
full size at last. The two views are now held to one rule rather than two.

The widths are **derived from each row's own middle-key budget, not chosen**,
which is why they come out as tidily as they do. The middles are 12.0u, 11.0u
and 10.0u, leaving 3.5u, 4.5u and 5.5u for the outer keys:

- **top** splits its budget evenly, so Tab and `\` are both **1.75u**;
- **home** must give Caps exactly what Tab has (see below), leaving Enter the
  remainder, **2.75u**;
- **bottom** splits evenly, so both Shifts are **2.75u**;
- **space** is Ctrl / Win / Alt at 1.25 / 1.0 / 1.25 and the bar takes the
  rest, **9.5u**.

So the whole keyboard is 1.0u, 1.75u and 2.75u keys plus Backspace (1.5u) and
the space bar. `TestEveryFullSizeRowIsFlush` pins the rule, the two symmetric
rows, and that shared-width consequence.

**Equal units are not equal pixels, and the second half is the one that
bites.** A row measures `units * keyW + (keys - 1) * keySpacing`, and the rows
carry very different key counts: 15, 14, 13, 12 and 6. The space row is
therefore nine gaps short of the number row, which at the default window is
18 px, and since each row is centred on its own it sat 9 px inside the grid at
each end. Making the unit totals equal straightened four edges and left the
fifth visibly short, which is exactly what was reported. Compact View has the
same shape at a smaller scale (10 to 12 gaps across its rows).

So **each row absorbs its own gap shortfall into its own keys**: the `Row`
delegate in `Main.qml` derives `rowKeyW` as
`keyW + (_widestRow.gaps - (keys - 1)) * keySpacing / rowUnits`. Three things
follow, and each was a live alternative:

- **Gaps stay identical everywhere.** Widening each row's `spacing` to fill
  would also have squared the edges, and would have given the space row 5.6 px
  gutters against 2 px elsewhere. Absorbing into the keys is invisible: about
  1 px on a 60 px key.
- **The widest row is unchanged by construction** (its shortfall is zero), so
  `keyW`, the side panels and the window's width budget are all exactly what
  they were. That is why this needed no change to `totalKeyUnits` or
  `layoutFixedPixels`.
- **It cannot be made exact, and the residual is the positioner's.** Qt Quick
  snaps child positions to whole pixels, so a row of fractionally-wide keys
  accumulates rounding along its length and its centred origin can land a pixel
  either side of its neighbour's. Non-compact comes out exact; two of compact's
  four rows sit 1 px across. `TestEveryGridRowIsPixelFlush` therefore asserts
  equal *widths* exactly and equal *origins* to within a pixel, and keeps the
  two apart deliberately, so a row that went genuinely short fails loudly
  instead of hiding under the tolerance.

One measurement trap that cost a while: a `Repeater` is itself a zero-sized
`QQuickItem` sitting in the positioner beside its delegates, so a `Row`'s own
`width` can carry a phantom pixel past the last key that draws nothing. Measure
from the first key's left edge to the last key's right edge, not the row's
bounding box, or two compact rows read as 1 px wider than they render.

**The letter alignment got sturdier, and the mechanism changed.** W over S (for
WASD) reduces to `inset_top + Tab == inset_home + Caps`. With the rows flush
both insets are zero, so it is now simply **Tab and Caps must be the same
width**. Before, the rows had different totals and the differing indents
happened to cancel the differing Tab and Caps widths, so the alignment held by
a coincidence between four numbers, and any one of them moving broke it. It did
break once: a Del key past the backslash made the top row 0.9u wider than the
home row and landed W between A and S. Del stays off the grid for that reason;
see `TestTheLetterColumnsLineUp`.

One thing that is *not* implied by the flush rule: the window's width budget
comes from `_widestRow`, which tracks max units and max **gap count**
independently rather than reading both off one row. So the tie the flush rule
creates is harmless, and the gap budget still comes from the number row's 15
keys. A row that gained keys would cost a `keySpacing` even though its unit
total cannot change, which is the half
`TestTheLetterColumnsLineUp::test_the_space_row_still_costs_no_window_width`
still guards.

## The three sections share one height

`Main.qml`'s `sectionHeight`. The keyboard grid, the nav cluster and the
numpad are laid out side by side in one `RowLayout`, and they used to be
three different heights that the layout centred against one another. With
the two separators (which carried `Layout.fillHeight`) that is **five
different top edges and five different bottom edges**. Measured at a 1400 px
window with both panels on, before the fix:

| | top | bottom | height |
|---|---|---|---|
| separators | 105 | 433 | 328 |
| grid | 115 | 423 | 308 |
| nav | 130 | 408 | 278 |
| numpad | 134 | 404 | 270 |

Nothing was wrong with any one section; no two of them began or ended in the
same place. The arrow cluster floated 19 px clear of the bottom.

Every section now lays out to `sectionHeight` and the panels divide it
between their own five rows, so **the keys grow rather than the gaps**.
Five things about it are load-bearing:

- **The panels' `implicitHeight` is computed from `keyH`, never read back
  off their own grid.** `rowH` derives from the height the layout hands the
  panel, and the layout falls back to `implicitHeight` when it hands it
  none, so a panel whose implicit height came from its grid would close a
  binding loop. `Math.ceil(keyH)` in that expression is not slop: a `Row`
  reports a height ceiled above its tallest key (the same fact
  `keyHitMarginV` carries half a pixel for), so the ceiled figure is what
  the grid actually renders at and anything else leaves the panel a pixel
  short.
- **`sectionHeight` is the grid's implicit height, and the panels fit it;
  there is no max and no floor.** It was first written as a `Math.max` over
  the grid and the panels' natural heights, with each panel's `rowH`
  floored at `keyH`, to keep the panel keys at full height when the
  function row is hidden (the panels are then the taller section, by the
  nav gutter plus rounding). That cannot work: the grid's own implicit
  height is what defines the section, so the grid cannot grow into a
  larger one, and it sat centred 6 px inside the panels in exactly the
  configuration a fresh install ships with (nav on, no function row, no
  numpad), which is the ragged edge this exists to remove. The panels are
  the side that can fit, so they fit, and with the function row hidden
  their keys come out about 2 px shorter than the letters. That is the
  trade: a straight edge for 2 px, in the one configuration where the
  choice arises at all.
- **The arrows land on the bottom rail with nothing positioning them there.**
  `arrowGap` already opened *above* the Up key (a Grid aligns cell content
  to the top, so the gutter had to be built that way), so once the panel is
  as tall as the grid the cluster is flush by construction. Don't add a
  bottom anchor; there is nothing to fix.
- **The nav cluster keeps its arrow gutter and the numpad has none**, so
  their rows do *not* line up with each other, and their keys differ in
  height by the gutter's share (about 1.6 px). Both panels briefly shared
  one rhythm (a matching gap after the numpad's third row) so their rows
  would align; it was reversed on sight. A physical numpad has no seam
  there, and one splitting the digits off the `0` key is more obviously
  wrong than two panels whose rows drift a pixel or two apart. The property
  that carried the gap lingered at zero for one revision "for a caller that
  wants one" and was removed: nothing read it, and a field nothing reads is
  worse than a field that does not exist yet.
- **The separators take `Layout.preferredHeight: root.sectionHeight`**, not
  `fillHeight`. `mainLayout` runs about 20 px taller than its contents
  (the window's height is `outerLayout.implicitHeight + 80` against 60 px of
  chrome), so filling ran them 10 px past the board at each end.

The consequence to know: **with a function row showing, nav and numpad
keys are taller than the letters**, about 12% at the default width. That
is a gain rather than a cost, and it is the reason "grow the keys" was
chosen over "grow the gaps": those are the arrows and the numpad, and a
taller target is a cheaper click. The main grid is untouched in every
configuration, so `keyW`, the window's width budget and every row's flush
edges are exactly what they were. Guarded by
`tests/test_qml_key_colors.py::TestTheSectionsShareOneHeight`, which
measures live item positions rather than recomputing them from the
properties the QML sets, and which covers the function row hidden and the
shipped default configuration as well as the fixture's everything-on one.

**A test that toggles a row has to force a frame before it measures**
(`_relayout` in that file, which calls `grabWindow`). Qt Quick Layouts
recompute in a polish step that runs before a frame is rendered, and the
offscreen window renders none on its own: after hiding the function row,
`mainKeyboard.implicitHeight` sat at its old value through 300 event-loop
passes and moved only once a frame was forced. The first version of the
no-function-row test settled with `processEvents` alone, measured the
board *before* its own toggle had taken effect, and passed against the 6
px ragged edge above. The live app is unaffected, since it renders frames
continuously; only a headless test can measure a layout that has not
happened yet. The test now also asserts the grid's span actually moved.
