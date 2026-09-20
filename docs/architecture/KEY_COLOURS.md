# Key Colours by role

Moved verbatim from `CLAUDE.md` on 2026-09-20, which keeps a summary of the load-bearing rules. This is the full reasoning.

## Key Colours by role

*Settings -> Appearance -> Key Colours*. A key's fill can encode **what the
key does** rather than only which surface it sits on. Six schemes: `mono`
(**the default**), `twotone`, `bands`, `ink`, `signal`, and `off` (the
historical per-surface tinting). The engine is `qml/palette.js`; the wiring
is `Main.qml`'s `keyRoles` / `keyRoleFor`, and the resolution is in
`KeyButton`.

**`mono` ships as the default** because it is the only scheme that cannot
clash on any theme (it has no hue at all outside the live `toggle` state)
while still telling the typing keys apart from the ones that do something.

**`off` hands down a null table and every surface reads null as "keep your
own tint".** That is what keeps the pre-feature board reachable in one
click, and it is why there is no per-surface `off` branch anywhere. Don't
add one.

**The resolution lives in `KeyButton`, not at the call sites.** Five
surfaces draw keys (the main grid, both function rows, the number row, the
two side panels) and between them they hold about fifty; a surface passes
`roleColors` down once and names each key's `role`, and `KeyButton` resolves
`_roleFill` / `_roleInk` / `_roleBar`. Resolving per site would be fifty
copies of one rule, which is the parallel-blocks failure this file warns
about for sticky-modifier release.

### The colour theory, and why it is not HSL

Two rules govern every colour, and both exist because nine themes ship:

- **No hue is ever a literal.** Every family hue is rotated off the *active
  theme's own accent*, so a Vaporwave board gets vaporwave role colours.
  A fixed "#4a9eff for navigation" is either invisible or garish on about
  half the themes.
- **The rotation happens in OKLCh, not HSL.** Rotating hue in HSL holds the
  *number* L constant while perceived lightness swings wildly (HSL yellow at
  L=50% is far brighter than HSL blue at L=50%), so an evenly-spaced HSL
  palette produces bands where some shout and others whisper. OKLCh is
  perceptually uniform, so holding L and C while rotating h gives hues that
  read as equal weight.

Roles split into two classes, and the split is semantic:

- **Anchored** (`kill` = Backspace/Del, `commit` = Enter, `mod` = the held
  modifiers). Their meaning has to survive a theme change, so the hue starts
  from a fixed anchor and is only *pulled* toward the accent, by 25% and
  never more than 22 degrees. That is inside the band where a hue keeps its
  name: enough to sit in the theme's world, not enough to stop meaning
  "stop" or "go". A Backspace that came out green on some theme would be
  worse than no colour at all.
- **Family** (`edit`, `nav`, `fn`, `op`). Nothing about navigation is
  inherently green, so these are pure theme derivation.

**Family hues are placed by farthest-point dispersion, not by a harmony.**
The first implementation used a square (tetradic) harmony rotated off the
accent, which reads as tidier colour theory and measured worse: a square is
four hues at a fixed 90 degrees, so its only freedom is one phase angle, and
with the accent plus three anchored hues to avoid there are themes where no
phase fits. On Amethyst the best available put `kill` 12 degrees from
`edit`, i.e. Backspace and Tab the same colour on the one scheme whose whole
purpose is telling them apart. Dispersion has a free choice per hue and
degrades gracefully: the tightest pair on any theme is now **30 degrees**.

**The hue's lightness comes from the key colour, clamped to [0.38, 0.84].**
Matching the keycap's lightness means a wash moves hue and chroma while
leaving brightness alone, so the bands carry equal weight and the board
keeps one even tone. The clamp is not tidiness: sRGB holds almost no chroma
near white or black, and on the Light theme (key colour `#ffffff`) every
family hue gamut-fit its way back to pure white and the whole scheme
collapsed to a single fill. `fromOklch` reduces chroma until the result fits
in gamut rather than clamping channels, because clamping shifts the hue and
does it worst exactly where the requested chroma is unreachable.

### The contrast promise

**Every fill goes through `washFor`, which walks the tint strength down
until the theme's own `textColor` clears 4.5:1.** A scheme therefore cannot
cost legibility on any theme: where a wash would bury the label, it yields.
This is the same rule and the same walk `accentWashFor` already used for the
compact view's editing keys, which is why `Main.qml`'s `accentWashFor` now
**delegates to `palette.js`** rather than keeping a second copy of the WCAG
maths (its `relativeLuminance` / `contrastRatio` wrappers went with it:
nothing called them, and anything needing the maths imports `palette.js`).
"Every fill" includes the lightness `step` toward the background that
Monochrome and Function are built from; it skipped the wash at first,
which happened to pass on nine themes and was a promise the code did not
keep.

The trap: a colour that is not a fill is easy to forget. The `ink` scheme
dims punctuation rather than hueing it, and the ungated version put
Vaporwave's punctuation at 4.32:1, under the bar the rest of the file
promises. `_dimmedInk` guards it. The *stripe* under a key is deliberately
not guarded, because it is not text and owes no ratio.

**The hover lift is guarded too, and was the second place the promise
broke.** `KeyButton` and the pills used to lift the fill with a plain
`Qt.lighter` under the pointer, which on a fill the wash had left at
exactly 4.5:1 overshoots the bar: Monochrome's Enter on Blackboard measured
3.1:1 hovered, the Ink scheme's pill on Light 2.9:1, on the key the user is
about to press. `palette.js::hoverFill` walks the lift down until the
legend still clears what it cleared at rest, capped at 4.5 so a surface the
theme itself put under the bar keeps its lift rather than losing it. The
pill's *ink* is no longer lifted with the fill, since lightening both
toward each other is what drains the contrast; the lift and the thicker
ring are the cue. `legibleInk` picks its pole at `INK_POLE_LUMINANCE`
(0.179, where black and white contrast equally with the ground), not at
the 0.35 first used, which for a ground between the two walked toward the
pole that cannot reach 4.5:1 and fell through to the fallback unchecked;
no shipped theme sits in that band, so only a future one would have hit
it.

Guarded by `tests/test_qml_key_colors.py::TestEveryLegendStaysReadable`,
which sweeps every scheme x every theme x every role (540 combinations,
resting and hovered) rather than spot-checking the developer's own theme,
and whose paired inverse asserts the bands stay *tellable apart* -- a
scheme whose colours all collapsed to one fill would satisfy a contrast
sweep perfectly.

### Roles

**A modifier's theme colour is its CLICK colour, not its resting one.**
`mono`'s modifiers took a theme-accent wash for one revision, on a reading
of "theme the modifier colours" that turned out to be the wrong one: it put
a standing blue-grey on Caps and both Shifts, which is a keyboard with
colour on it rather than a monochrome one. `KeyButton` already paints
`accentColor` while a modifier is active and `keyPressedColor` while it is
held, both fed straight from the theme, so the resting cap has no reason to
carry it as well. `test_monochrome_really_is_monochrome` now asserts every
role but `toggle` is neutral, and
`test_a_modifiers_click_colour_is_theme_derived` pins the half that is
actually wanted.

**The prediction pills carry a role too (`pill`), and a scheme colours
their RING, never their fill.** They were the one surface a scheme did not
reach, which made them the only thing on screen that did not change when the
board did. The fill is **flat, exactly the letter keys' own colour, on every
scheme and every theme**; a scheme's pill colour goes in `pill.bar`, which
`Main.qml` draws as the border, and a scheme that leaves it clear keeps the
full theme accent ring. `Main.qml`'s `predPillFill` / `predPillInk` /
`predPillBorder` are the wiring. **`bands` is the only scheme with a ring
colour of its own** (the commit hue, so a pill reads as related to Enter
without competing with it); `ink` was given one too and it was reversed on
sight, because that scheme's legend is already a dim hue and a dim hairline
round a dim word is the one combination that stops reading as something to
reach for.

**Three things about the pills have each been got wrong once, and all three
read as either "washed out" or "coloured panel".**
(1) Hueing the **fill** shipped for a release and was reversed on sight:
eight pills are the widest block of one colour on the board and they sit
*above* the keys rather than among them, so a wash across all eight reads as
a coloured panel rather than as eight things to reach for.
(2) Blending the **ring** toward the fill, so a coloured pill would read as
outlined rather than ringed, drained the row: the ring is the only thing
marking the pills as what the user is meant to reach for. A scheme may
recolour the ring; it may not soften it.
(3) `mono`'s pill fill was lifted toward the ink (tried at 0.14) and greyed
out against the board. Flat plus a ring is what reads as crisp.
Guarded by `tests/test_qml_key_colors.py::TestThePredictionPillsFollowTheScheme`,
where "the fill never changes" and "the ring always changes" are each paired
with the inverse, because either on its own is satisfied by a rule that has
stopped colouring the pills at all.

`roleForKey` reads the layout JSON's `type` **plus the key itself**, because
the JSON says `char` for a letter, a digit and a bracket alike and those are
three different jobs. `alpha` / `digit` / `punct` / `mod` / `edit` / `kill`
/ `commit` / `nav` / `fn` / `op` / `toggle`. The schemes themselves are one
builder each in `roleMap`'s `builders` object, and the set of ids the
engine knows *is* that object's keys, so an unknown id (a settings file
from a build with a scheme this one lacks) reads as `off` rather than as an
empty table every surface would index into.

Three role assignments are worth knowing:

- **The numpad's roles follow NumLock.** With it off the digits *are* the
  navigation keys and `.` *is* Delete, so a colour saying "digit" over a key
  that pages up would be a lie. The role is `NumpadPanel.numRole`, one
  property on the panel rather than a ternary on each of ten keys.
- **`NumberRow` reads its own role inline** rather than through
  `keyRoleFor`, because its `keyDefs` are its own shape rather than the
  layout JSON's. Esc is `edit`; `-` and `=` are `punct`, not digits.
- **The compact layouts embed the nav column in the grid**, as `special`
  keys with `home` / `end` / `pageup` / `pagedown` / `insert` / arrow
  actions, and `roleForKey` names those `nav`. Its first version read every
  special key it did not know as `edit`, which painted Home the same as Tab
  on the one layout that carries them this way. Guarded by
  `TestKeysAreGivenTheRightJob::test_the_compact_grid_embeds_its_nav_column_as_navigation`.

`KeyButton`'s role stripe hides itself whenever the key is pressed, active
or locked: those states repaint the fill underneath it, and the lock bar
draws in the same place, so two bars stacked would read as one smear rather
than as the lock cue.
