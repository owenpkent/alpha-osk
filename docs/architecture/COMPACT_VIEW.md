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
 Tab a s d f g h j k l ' Esc PgDn        Tab - = [ ] \ ; ' ` Ins Del Caps PgDn
  ⇧ z x c v b n m , / [Enter] Home        ⇧ ! @ # $ % ^ & ( ) [Enter] Home
 ?123 Ctl ⊞ Alt [space] . ← ↑ ↓ → End    ABC Ctl ⊞ Alt [space] . ← ↑ ↓ → End
```

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
- `.` sits beside Space (phone convention) rather than next to `,`; that is what
  pays for `/` on row 3 without a fourteenth column.

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
