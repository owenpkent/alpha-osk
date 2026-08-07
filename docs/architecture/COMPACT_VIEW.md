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
Base layer                              ?123 layer
  q w e r t y u i o p [ ⌫ ] PgUp          1 2 3 4 5 6 7 8 9 0 [ ⌫ ] PgUp
 Tab a s d f g h j k l ' Del PgDn        Tab - = [ ] \ ; ' ` Ins Esc Caps PgDn
  ⇧ z x c v b n m , / [Enter] Home        ⇧ ! @ # $ % : & ( ) [Enter] Home
 ?123 Ctl ⊞ Alt [space] . ← ↑ ↓ → End    ABC Ctl ⊞ Alt [space] . ← ↑ ↓ → End
```

**Del and Esc trade layers.** A 13u row has no spare unit, so putting
forward-delete on the base layer had to cost something, and Esc was the only
key there that isn't in the protected set below. Backspace-only editing means
walking the caret past a mistake and back, which is several extra clicks with a
pointer; Esc is comparatively rare in text entry. `Enter`/`Backspace` staying 2u
and the nav column staying put both rule out the alternatives.
`tests/test_layouts.py::TestCompactLayout::test_esc_is_still_reachable_from_the_sym_layer`
guards that this stayed a trade rather than becoming a deletion.

Design rules, all enforced by `tests/test_layouts.py`:

- **The bottom row and the nav column are identical on both layers.** Space, the
  modifiers, the arrows and Home/End/PgUp/PgDn hold their exact position across a
  layer switch, so nothing reached for constantly moves under the pointer.
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

*Settings → Appearance → Panels → **Number Row*** adds a standalone
`Esc` `1`–`0` `-` `=` strip above the keyboard (`qml/components/NumberRow.qml`,
off by default). Thirteen 1u keys, so it is exactly 13.0u and sits flush over a
compact grid with no gutters.

It is a panel rather than a fifth row in the layout JSON because the compact
layout's two layers must both be four rows of 13u (`test_has_exactly_two_layers_of_four_rows`),
and because a panel toggles independently of which letter arrangement is
selected. The digits behave like any other char key: shift shows and types the
shifted glyph, right-click types it without flipping sticky shift, both flash
the key preview, and every digit registers in `charKeyRegistry` so the swipe
overlay passes taps through instead of swallowing them.

**The leading slot is `Esc`, not the physical keyboard's `` ` ``.** The Del/Esc
trade above put Esc behind a hop, and "get me out of this dialog" is a bad key
to make people navigate to. This row restores it at the top-left corner where a
real keyboard keeps it. Nothing is lost: `` ` ``→`~` stays on `?123` row 2, and
the full-size layouts carry their own `` ` `` in the layout JSON. The Esc here
duplicates the `?123` one deliberately — this panel is optional and off by
default, so `?123` has to stay the fallback for anyone who never enables it.

Two consequences of Esc being a special key rather than a char key: it takes no
key-preview bubble (a bubble over Esc isn't "what it typed", matching the main
grid), and it is **not** in `charKeyRegistry`, because a phantom "Esc" centre
would corrupt every swipe shape match. That makes it a dead tap while swipe
typing is on — which is exactly how Backspace, Tab and Enter already behave
under the overlay, not a regression specific to this row.

Enabling it alongside a full-size layout is allowed but pointless: those layouts
carry their own number row, so you get a narrower centred duplicate.

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
