"""Structural tests for the keyboard layout JSON files in data/layouts/.

The layout system is data-driven — `KeyboardBridge._load_layouts` globs the
directory and `getLayoutRows()` hands the rows straight to QML — so a
malformed layout file fails at runtime in the UI rather than at import time.
These tests are the guard rail for that.

The compact view (`qwerty-compact`) carries the extra invariants: it is a
uniform grid, so *every* row must total the same number of key-width units.
If a row drifts, the QML centres it and the side gutters the compact view
exists to remove come straight back.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

LAYOUTS_DIR = Path(__file__).resolve().parent.parent / "data" / "layouts"
NAV_PANEL = Path(__file__).resolve().parent.parent / "qml" / "components" / "NavigationPanel.qml"

# Special-key actions KeyboardBridge.pressSpecialKey knows how to dispatch.
KNOWN_SPECIAL_ACTIONS = frozenset(
    {
        "backspace",
        "delete",
        "down",
        "end",
        "escape",
        "home",
        "insert",
        "left",
        "pagedown",
        "pageup",
        "return",
        "right",
        "space",
        "tab",
        "up",
        "numlock",
        "print",
        "pause",
        "scrolllock",
    }
)

KNOWN_MODIFIER_ACTIONS = frozenset({"shift", "caps", "ctrl", "alt", "win"})

COMPACT_UNITS = 13.0


def _load(name: str) -> dict:
    return json.loads((LAYOUTS_DIR / name).read_text(encoding="utf-8"))


def _row_units(row: dict) -> float:
    return sum(float(k.get("width", 1.0)) for k in row["keys"])


def all_layout_files() -> list[Path]:
    return sorted(LAYOUTS_DIR.glob("*.json"))


def test_layouts_dir_exists() -> None:
    assert LAYOUTS_DIR.is_dir()
    assert all_layout_files(), "no layout JSON files found"


@pytest.mark.parametrize("path", all_layout_files(), ids=lambda p: p.stem)
class TestEveryLayout:
    """Invariants that hold for full-size and compact layouts alike."""

    def test_parses_and_has_required_shape(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["id"], "layout needs an id"
        assert data["name"], "layout needs a display name"
        assert data["rows"], "layout needs rows"

    def test_id_matches_filename(self, path: Path) -> None:
        # _load_layouts falls back to the stem, so a mismatch silently
        # registers the layout under a different id than the file suggests.
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == path.stem

    def test_keys_declare_known_types_and_actions(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data["rows"]:
            for key in row["keys"]:
                ktype = key.get("type")
                assert ktype in {"char", "special", "modifier", "layer"}, (
                    f"{path.stem}/{row['id']}: unknown key type {ktype!r}"
                )
                if ktype == "char":
                    assert key.get("key"), f"{path.stem}: char key with no `key`"
                elif ktype == "special":
                    assert key["action"] in KNOWN_SPECIAL_ACTIONS, (
                        f"{path.stem}: unhandled special action {key['action']!r}"
                    )
                elif ktype == "modifier":
                    assert key["action"] in KNOWN_MODIFIER_ACTIONS, (
                        f"{path.stem}: unhandled modifier {key['action']!r}"
                    )

    def test_delete_is_where_each_layout_family_keeps_it(self, path: Path) -> None:
        """Forward-delete must be one tap away, and where it lives differs.

        Backspace alone means the caret has to be walked past a mistake and
        back; on a pointer-driven keyboard that is several extra clicks. The
        compact layouts have no room beside them for the Navigation panel,
        so Del has to be on their base layer, not behind a ?123 hop. The
        full-size layouts keep it *off* the grid on purpose: it sits above
        the arrows on the Navigation panel (shown by default), and the top
        row losing it is what lets Q sit over A (see
        ``TestTheLetterColumnsLineUp``).
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        compact = "compactOf" in data
        base = [r for r in data["rows"] if r.get("layer", "base") == "base"]
        actions = {k.get("action") for r in base for k in r["keys"] if k.get("type") == "special"}
        if compact:
            assert "delete" in actions, f"{path.stem}: no Del key on the base layer"
        else:
            everywhere = {k.get("action") for r in data["rows"] for k in r["keys"]}
            assert "delete" not in everywhere, f"{path.stem}: Del is back on the main grid"

    def test_modifiers_carry_a_state_key(self, path: Path) -> None:
        # Without stateKey the QML `isActive` binding can never highlight the
        # key, so a toggled modifier looks inactive while it is held.
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data["rows"]:
            for key in row["keys"]:
                if key.get("type") == "modifier":
                    assert key.get("stateKey"), (
                        f"{path.stem}: modifier {key['action']!r} has no stateKey"
                    )

    def test_layer_keys_target_a_layer_that_exists(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        declared = {r["layer"] for r in data["rows"] if r.get("layer")}
        for row in data["rows"]:
            for key in row["keys"]:
                if key.get("type") == "layer":
                    assert key.get("target") in declared, (
                        f"{path.stem}: layer key targets {key.get('target')!r}, "
                        f"which is not one of {sorted(declared)}"
                    )

    def test_every_layer_is_reachable(self, path: Path) -> None:
        """No layer may be a dead end — each must be targeted from another."""
        data = json.loads(path.read_text(encoding="utf-8"))
        declared = {r["layer"] for r in data["rows"] if r.get("layer")}
        if not declared:
            return  # single-layer layout, nothing to reach
        targeted = {
            key["target"]
            for row in data["rows"]
            for key in row["keys"]
            if key.get("type") == "layer"
        }
        # "base" is where QML starts, so it's reachable by definition.
        assert declared - targeted - {"base"} == set(), (
            f"{path.stem}: unreachable layer(s) {sorted(declared - targeted - {'base'})}"
        )


FULL_SIZE = ["qwerty", "dvorak", "colemak"]

# Where the space bar's centre sits, in key-width units from the left edge of
# the widest row. It is the one number the 6.0u -> 9.0u widening had to leave
# alone, so it is written down rather than recomputed from the row it guards.
SPACE_CENTRE = 8.25


class TestTheFullSizeSpaceRow:
    """The full-size layouts have one layer, and the space bar took the room.

    They briefly carried a symbol page, reached from a ``Sym`` key at each end
    of the space row. It was removed: all 34 of its glyphs are also in the
    Symbols & Emoji picker, which is one click away in the suggestion bar on
    every layout, so the layer was spending 3.0u of the bottom row, its two
    widest keys after the space bar, on a second route to glyphs that already
    had one. The picker is a browsing surface (categories, paging, a Recent
    page) and the layer was a positional one, which is a real distinction and
    the reason it was built, but not one worth a fifth of that row to a
    pointer that reaches for the space bar more often than for anything else.

    These tests pin what the removal bought and the one thing it had to not
    cost.
    """

    @staticmethod
    def _space_row(name: str) -> dict:
        return next(r for r in _load(f"{name}.json")["rows"] if r["id"] == "space")

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_the_space_bar_grew_around_its_own_centre(self, name: str) -> None:
        """6.0u -> 9.5u, with the centre exactly where it was.

        This is the property the whole change rests on. On a flush row the
        bar's centre is (15.5 + left - right) / 2, where left is Ctrl + Win +
        Alt and right is Alt + Ctrl, so the whole expression collapses to
        (15.5 + Win) / 2 as long as the four Ctrl / Alt keys are equal. Win is
        1.0u and they are all 1.25u, so the centre is 8.25u, exactly where it
        sat when two 1.5u Sym keys flanked a 6.0u bar on a 14.6u row.

        That is what makes the widening free rather than something to relearn:
        every click that landed on the space bar before still lands on it, and
        the new target is added at both ends. What moved instead is Ctrl / Win
        / Alt, outward on both sides, which is the trade, taken deliberately:
        the space bar is pressed after every word and those five are not.

        The rule to keep if these widths are ever retuned: Win stays 1.0u and
        the four Ctrl / Alt keys stay equal to each other. Nothing else about
        the row matters to the centre.
        """
        rows = _load(f"{name}.json")["rows"]
        row = self._space_row(name)
        widest = max(_row_units(r) for r in rows)

        space_index = next(i for i, k in enumerate(row["keys"]) if k.get("action") == "space")
        space = row["keys"][space_index]
        assert space["width"] == pytest.approx(9.5), (
            f"{name}: the space bar is {space['width']}u, not 9.5u"
        )

        x = (widest - _row_units(row)) / 2.0
        for key in row["keys"][:space_index]:
            x += float(key.get("width", 1.0))
        centre = x + space["width"] / 2.0
        assert centre == pytest.approx(SPACE_CENTRE, abs=0.001), (
            f"{name}: the space bar's centre moved to {centre}u from {SPACE_CENTRE}u, "
            "so clicks that used to land on it can now miss"
        )

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_the_space_row_opens_no_layer(self, name: str) -> None:
        """Stated as a property so re-adding a Sym key fails here, next to
        the reasoning, rather than on screen. If a symbol page ever comes
        back it should not come back on this row: the space bar is the thing
        that row is for, and the Symbols & Emoji picker already reaches every
        glyph such a page would carry."""
        kinds = [k.get("type") for k in self._space_row(name)["keys"]]
        assert "layer" not in kinds, (
            f"{name}: the space row has a layer key again, at the space bar's expense"
        )

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_a_full_size_layout_declares_a_single_layer(self, name: str) -> None:
        """No row carries a `layer` field, so every row renders always.

        Compact View has to hide things behind ?123 because 13 units cannot
        hold letters and digits at once. Full size has the room, and paying a
        hop for glyphs the picker already lists is the trade this removal
        reversed.
        """
        layered = [r["id"] for r in _load(f"{name}.json")["rows"] if r.get("layer")]
        assert not layered, f"{name}: rows {layered} are behind a layer"

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_widest_row_is_still_15_5_units(self, name: str) -> None:
        # Main.qml derives keyW from the widest row; this is the historical
        # number the default 940 px window width is tuned against.
        rows = _load(f"{name}.json")["rows"]
        widest = max(rows, key=_row_units)
        assert _row_units(widest) == pytest.approx(15.5)
        assert len(widest["keys"]) - 1 == 14, "gap count feeds layoutFixedPixels"


class TestEveryFullSizeRowIsFlush:
    """Every row totals exactly 15.5u, so all four edges of the grid are straight.

    `Main.qml` centres each row against the widest one, so a row that totals
    less than the widest sits inside it by half the difference at each end.
    With five different totals the keyboard's left and right edges stepped in
    and out five times, by up to 0.6u (about 9 px at the default window), which
    is what read as lumpy: nothing was wrong with any single key, but no two
    rows began in the same place.

    This is the rule Compact View has enforced from the start, where
    `TestCompactLayout::test_every_row_is_exactly_13_units` states it in the
    same shape and for the same reason. Full size never got it. Adding it here
    means the two views are held to one rule rather than to two, and it is why
    the widths below are derived from each row's own middle-key budget rather
    than chosen: the top and bottom rows split what is left evenly (so both are
    symmetric), and the home row gives Caps whatever Tab has and Enter the
    remainder.
    """

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_every_row_is_exactly_15_5_units(self, name: str) -> None:
        ragged = {
            r["id"]: _row_units(r)
            for r in _load(f"{name}.json")["rows"]
            if abs(_row_units(r) - 15.5) > 1e-9
        }
        assert not ragged, (
            f"{name}: {ragged} do not total 15.5u, so they are centred inside the "
            "grid and the keyboard's edges step in and out at those rows"
        )

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_the_top_and_bottom_rows_are_symmetric(self, name: str) -> None:
        """Tab matches backslash, and the two Shifts match each other.

        Not decoration: on a flush row the only freedom left is how the
        leftover budget is split between the two outer keys, and splitting it
        evenly is what stops one end of a row looking heavier than the other.
        The home row is deliberately exempt, because Caps is pinned to Tab's
        width by the letter alignment below and Enter takes what is left.
        """
        rows = {r["id"]: r for r in _load(f"{name}.json")["rows"]}
        for rid in ("top", "bottom"):
            keys = rows[rid]["keys"]
            first, last = keys[0].get("width", 1.0), keys[-1].get("width", 1.0)
            assert first == pytest.approx(last), (
                f"{name}/{rid}: outer keys are {first}u and {last}u, so the row is lopsided"
            )

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_the_big_keys_share_one_width(self, name: str) -> None:
        """Enter and both Shifts are all 2.75u; Tab, Caps and backslash all 1.75u.

        A consequence of the two rules above rather than a target in its own
        right, but worth pinning, because it is the thing a reader sees: the
        whole keyboard is built from 1.0u, 1.75u and 2.75u keys plus Backspace
        and the space bar. A retune that breaks this has almost certainly
        broken the flush rule or the alignment as well.
        """
        rows = {r["id"]: r for r in _load(f"{name}.json")["rows"]}
        assert {
            rows["home"]["keys"][-1]["width"],
            rows["bottom"]["keys"][0]["width"],
            rows["bottom"]["keys"][-1]["width"],
        } == {2.75}, f"{name}: Enter and the Shifts no longer share a width"
        assert {
            rows["top"]["keys"][0]["width"],
            rows["top"]["keys"][-1]["width"],
            rows["home"]["keys"][0]["width"],
        } == {1.75}, f"{name}: Tab, Caps and backslash no longer share a width"


class TestTheLetterColumnsLineUp:
    """W sits directly above S on the full-size layouts, for WASD gaming.

    Main.qml centres every row against the widest one, so a row's first
    letter sits at `(widest_units - row_units) / 2` plus the leading
    modifier's width. Now that every row is flush at 15.5u (see
    TestEveryFullSizeRowIsFlush) the first term is zero on both rows, so the
    alignment reduces to a single rule: **Tab and Caps must be the same
    width**, and they are, both 1.75u.

    That is a much sturdier arrangement than the one it replaced. Before, the
    rows had different totals and the differing indents happened to cancel the
    differing Tab and Caps widths, so the alignment held by arithmetic
    coincidence between four numbers and any one of them moving broke it. It
    did break: the top row carried a Del key past the backslash, which made it
    0.9u wider than the home row and pushed the whole letter block four fifths
    of a key left, landing W between A and S. On a pointer-driven keyboard that
    turns every W->S in a WASD pair into a diagonal drag, which is the one
    movement slow motor input is worst at.

    Del stays off the main grid: it already sits above the arrows on the
    Navigation panel, which is shown by default, and the space row has no room
    for it, since the space bar took the 3.0u the symbol layer's two Sym keys
    used to hold (see TestTheFullSizeSpaceRow).

    These assertions are in key-width units and deliberately ignore
    `keySpacing`: the top row carries one more gap than the home row, so
    the true residual is half a gap, which is 1 px at the default window
    width and never exceeds 2 px.
    """

    LAYOUTS = ["qwerty", "dvorak", "colemak"]

    @staticmethod
    def _rows(name: str) -> tuple[dict[str, dict], float]:
        rows = {r["id"]: r for r in _load(f"{name}.json")["rows"]}
        return rows, max(_row_units(r) for r in rows.values())

    @staticmethod
    def _centre(row: dict, index: int, widest: float) -> float:
        """Centre of the key at `index`, in key-width units from the left."""
        x = (widest - _row_units(row)) / 2.0
        for key in row["keys"][:index]:
            x += float(key.get("width", 1.0))
        return x + float(row["keys"][index].get("width", 1.0)) / 2.0

    @pytest.mark.parametrize("name", LAYOUTS)
    def test_the_top_row_letters_sit_over_the_home_row_letters(self, name: str) -> None:
        rows, widest = self._rows(name)
        # Index 1 is the first letter on both rows (index 0 is Tab / Caps).
        assert self._centre(rows["top"], 1, widest) == pytest.approx(
            self._centre(rows["home"], 1, widest), abs=0.02
        )

    def test_w_is_directly_above_s(self) -> None:
        rows, widest = self._rows("qwerty")
        top = [k.get("key") for k in rows["top"]["keys"]]
        home = [k.get("key") for k in rows["home"]["keys"]]
        assert self._centre(rows["top"], top.index("w"), widest) == pytest.approx(
            self._centre(rows["home"], home.index("s"), widest), abs=0.02
        )

    @pytest.mark.parametrize("name", LAYOUTS)
    def test_del_is_off_the_grid_and_on_the_navigation_panel(self, name: str) -> None:
        # Del has to stay reachable (walking the caret past a mistake and
        # back is several clicks), and the place it stays reachable is the
        # Navigation panel, above the arrows. Pin both halves: nothing on
        # the grid, and the panel still has it.
        rows, _ = self._rows(name)
        on_grid = [k.get("action") for r in rows.values() for k in r["keys"]]
        assert "delete" not in on_grid, f"{name}: Del is back on the main grid"
        panel = (NAV_PANEL).read_text(encoding="utf-8")
        assert 'keyText: "delete"' in panel, "NavigationPanel.qml lost its Del key"

    @pytest.mark.parametrize("name", LAYOUTS)
    def test_the_space_row_still_costs_no_window_width(self, name: str) -> None:
        """The space row may match the widest row but must never drive it.

        This used to be a strict `<`, which was right while the rows had
        different totals: the space row had slack, and Del was free only
        while it kept it. Under the flush rule the space row is *tied* with
        every other row at 15.5u, which is the intended state rather than a
        regression, so the units check is now `<=`.

        The half that still bites is the gap count. `Main.qml` derives
        `layoutFixedPixels` from `_widestRow.gaps`, and it takes that from
        whichever row has the most keys rather than from the widest row, so a
        key added to the space row costs the window a whole `keySpacing` even
        though the row's unit total cannot change. The number row's 15 keys
        set that budget today; the space row's 6 must stay well under it.
        """
        rows, widest = self._rows(name)
        assert _row_units(rows["space"]) <= widest
        assert len(rows["space"]["keys"]) < len(rows["number"]["keys"]), (
            f"{name}: the space row now has the most keys, so it sets the "
            "window's gap budget instead of the number row"
        )


class TestCompactLayout:
    """qwerty-compact is a uniform grid — the invariants are tighter."""

    @pytest.fixture
    def compact(self) -> dict:
        return _load("qwerty-compact.json")

    def test_declares_the_layout_it_is_a_variant_of(self, compact: dict) -> None:
        # Main.qml derives the compact id as `<base>-compact`; this field
        # documents the pairing for anyone reading the data alone.
        assert compact["compactOf"] == "qwerty"
        assert compact["id"] == f"{compact['compactOf']}-compact"

    def test_every_row_is_exactly_13_units(self, compact: dict) -> None:
        """The whole point: equal-width rows leave nothing to centre."""
        for row in compact["rows"]:
            assert _row_units(row) == pytest.approx(COMPACT_UNITS), (
                f"row {row['id']} is {_row_units(row)}u, not {COMPACT_UNITS}u — "
                "unequal rows get centred and the side gutters come back"
            )

    def test_has_three_layers_of_four_rows(self, compact: dict) -> None:
        r"""base, ?123 and =\< , four rows each.

        The second symbol page exists because Shift on ?123 re-rendered row 1
        as the glyphs row 3 already showed. Replacing Shift with a page switch
        is the phone convention and makes the overlap structurally impossible;
        see TestNoDuplicateGlyphsWithinALayer.
        """
        layers: dict[str, list] = {}
        for row in compact["rows"]:
            layers.setdefault(row["layer"], []).append(row)
        assert set(layers) == {"base", "sym", "sym2"}
        for name, rows in layers.items():
            assert len(rows) == 4, f"{name} has {len(rows)} rows, expected 4"

    @staticmethod
    def _other_layers(compact: dict) -> set[str]:
        return {r["layer"] for r in compact["rows"]} - {"base"}

    def test_bottom_row_identical_across_layers(self, compact: dict) -> None:
        """Space, modifiers, the period and the arrows must not move on a
        layer switch.

        Derived from the layer list rather than a hardcoded base/sym pair:
        the second symbol page was added while this test named only those
        two, so it shipped with a bullet where every other layer has a
        period and the suite stayed green. Any layer added later is covered
        without touching this test.
        """
        rows = {r["id"]: r for r in compact["rows"]}
        base = [dict(k) for k in rows["base-4"]["keys"]]
        # The layer key itself necessarily differs (?123 vs ABC).
        base[0].pop("target")
        base[0].pop("display")
        for layer in sorted(self._other_layers(compact)):
            other = [dict(k) for k in rows[f"{layer}-4"]["keys"]]
            other[0].pop("target")
            other[0].pop("display")
            assert other == base, f"bottom row of {layer} differs from base"

    def test_nav_column_is_identical_across_layers(self, compact: dict) -> None:
        """Home/PgUp/PgDn/End hold position on every layer, not just ?123."""
        rows = {r["id"]: r for r in compact["rows"]}
        for layer in sorted(self._other_layers(compact)):
            for n in (1, 2, 3, 4):
                assert rows[f"base-{n}"]["keys"][-1] == rows[f"{layer}-{n}"]["keys"][-1], (
                    f"nav key differs between base-{n} and {layer}-{n}"
                )

    def test_nav_column_reads_top_to_bottom(self, compact: dict) -> None:
        """Home above PgUp above PgDn above End.

        The column is a vertical scroll ladder: jump to the top, page up,
        page down, jump to the bottom. The order is muscle memory, so pin it
        rather than leaving it to whoever next edits the row.
        """
        rows = {r["id"]: r for r in compact["rows"]}
        column = [rows[f"base-{n}"]["keys"][-1]["action"] for n in (1, 2, 3, 4)]
        assert column == ["home", "pageup", "pagedown", "end"]

    def test_keys_owen_named_are_on_the_base_layer(self, compact: dict) -> None:
        """Arrows, Enter, Home/End, PgUp/PgDn and / — never behind a hop."""
        base_rows = [r for r in compact["rows"] if r["layer"] == "base"]
        actions = {k["action"] for r in base_rows for k in r["keys"] if k.get("type") == "special"}
        chars = {k["key"] for r in base_rows for k in r["keys"] if k.get("type") == "char"}
        assert {"left", "up", "down", "right"} <= actions, "arrows must be visible"
        assert "return" in actions, "Enter must be visible"
        assert {"home", "end"} <= actions
        assert {"pageup", "pagedown"} <= actions
        assert "/" in chars

    def test_enter_and_backspace_are_double_width(self, compact: dict) -> None:
        # Both are high-frequency; Backspace additionally auto-repeats, so a
        # 1u target would be a regression against the full-size layout.
        for row in compact["rows"]:
            for key in row["keys"]:
                if key.get("action") in ("return", "backspace"):
                    assert key["width"] == 2.0, f"{key['action']} in {row['id']} is {key['width']}u"

    def test_esc_is_still_reachable_from_the_sym_layer(self, compact: dict) -> None:
        """Del took Esc's base-layer slot; Esc took Del's on ?123.

        There is no spare unit in a 13u row, so putting Del on the base layer
        had to cost something. Esc was the only non-protected key there (see
        test_keys_owen_named_are_on_the_base_layer for the protected set) and
        it is far rarer than forward-delete in text entry. Guard that the
        swap was a trade and not a deletion.
        """
        sym_rows = [r for r in compact["rows"] if r["layer"] == "sym"]
        actions = {
            k.get("action") for r in sym_rows for k in r["keys"] if k.get("type") == "special"
        }
        assert "escape" in actions

    def test_alphabet_is_complete_on_the_base_layer(self, compact: dict) -> None:
        base_rows = [r for r in compact["rows"] if r["layer"] == "base"]
        letters = {
            k["key"]
            for r in base_rows
            for k in r["keys"]
            if k.get("type") == "char" and k["key"].isalpha()
        }
        assert letters == set("abcdefghijklmnopqrstuvwxyz")

    def test_digits_are_complete_on_the_sym_layer(self, compact: dict) -> None:
        sym_rows = [r for r in compact["rows"] if r["layer"] == "sym"]
        digits = {
            k["key"]
            for r in sym_rows
            for k in r["keys"]
            if k.get("type") == "char" and k["key"].isdigit()
        }
        assert digits == set("0123456789")

    def test_colon_has_a_dedicated_key_on_the_sym_layer(self, compact: dict) -> None:
        """A shifted variant is invisible, so `;`→`:` read as "no colon".

        Row 2 of ?123 carries `;` with `shifted: ":"`, but the keycap says
        `;` and nothing on screen signals that a colon is one right-click
        away. Row 3 exists to surface those glyphs as keys in their own
        right, so the colon gets one there.
        """
        sym_rows = [r for r in compact["rows"] if r["layer"] == "sym"]
        chars = [k for r in sym_rows for k in r["keys"] if k.get("type") == "char"]
        assert ":" in {k["key"] for k in chars}

    def test_caret_survives_the_colon_taking_its_slot(self, compact: dict) -> None:
        """`^` paid for the colon's 1u — a trade, not a deletion.

        It is the rarest of row 3's symbols in prose, and it stays
        reachable as the shifted variant of `6` on row 1.
        """
        sym_rows = [r for r in compact["rows"] if r["layer"] == "sym"]
        chars = [k for r in sym_rows for k in r["keys"] if k.get("type") == "char"]
        assert "^" in {k.get("shifted") for k in chars}

    def test_shifted_variants_cover_the_punctuation_owen_asked_for(self, compact: dict) -> None:
        """Right-click types `shifted`, so `/` must carry `?`."""
        shifted = {
            k["key"]: k.get("shifted")
            for r in compact["rows"]
            for k in r["keys"]
            if k.get("type") == "char"
        }
        assert shifted["/"] == "?"
        assert shifted[","] == "<"
        assert shifted["."] == ">"
        assert shifted["'"] == '"'


class TestBridgeDiscoversCompactLayout:
    """The compact layout must need no backend change to be picked up."""

    def test_load_layouts_finds_it(self) -> None:
        pytest.importorskip("PySide6")
        from unittest.mock import MagicMock, patch

        from src.keyboard_bridge import KeyboardBridge

        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.is_available.return_value = True
            synth.backend_name.return_value = "MockSynth"
            factory.return_value = synth
            bridge = KeyboardBridge()

        ids = {entry["id"] for entry in bridge.getAvailableLayouts()}
        assert "qwerty-compact" in ids
        assert "qwerty" in ids

    def test_get_layout_rows_returns_every_layer(self) -> None:
        pytest.importorskip("PySide6")
        from unittest.mock import MagicMock, patch

        from src.keyboard_bridge import KeyboardBridge

        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.is_available.return_value = True
            synth.backend_name.return_value = "MockSynth"
            factory.return_value = synth
            bridge = KeyboardBridge()

        bridge.setLayout("qwerty-compact")
        rows = bridge.getLayoutRows()
        # The bridge is layer-agnostic — it hands QML every row and the
        # filtering happens there. Guard that contract explicitly.
        assert len(rows) == 12
        assert {r["layer"] for r in rows} == {"base", "sym", "sym2"}


@pytest.mark.parametrize("path", all_layout_files(), ids=lambda p: p.stem)
class TestNoDuplicateGlyphsWithinALayer:
    """Reported: on the ?123 page, Shift turned row 1 into ! @ # $ % ^ & * ( )
    while row 3 already showed ! @ # $ % : & ( ) permanently. Nine of the keys
    on screen were saying the same thing as another key on screen.

    The fix replaced Shift on the symbol pages with a switch to a second page,
    the phone convention, so every glyph Shift used to reach has a key of its
    own. These tests state the property rather than the fix, so a future edit
    that reintroduces an overlap fails here rather than on a user's screen.
    """

    # A row with no `layer` field renders on *every* layer, which is how the
    # full-size layouts are built (they have no layers at all) and how
    # compact's always-visible rows are. Skipping such a row
    # (`if r.get("layer")`) returned the empty set for qwerty / dvorak /
    # colemak, so two of the tests below iterated nothing and passed without
    # asserting anything while the parametrize ids advertised coverage of all
    # four layouts. Defaulting it to the layer being read is what fixed that,
    # and it keeps working if a layer is ever added back: a glyph on an
    # always-visible row would collide with one on the new page.

    @staticmethod
    def _rows_on(path: Path, layer: str) -> list[dict]:
        """Every row that renders while *layer* is showing."""
        data = json.loads(path.read_text(encoding="utf-8"))
        return [row for row in data["rows"] if row.get("layer", layer) == layer]

    @staticmethod
    def _layer_glyphs(path: Path, layer: str) -> list[str]:
        """Every glyph a user can *see* on *layer*, in key order."""
        return [
            key["key"]
            for row in TestNoDuplicateGlyphsWithinALayer._rows_on(path, layer)
            for key in row["keys"]
            if key.get("type") == "char" and key.get("key")
        ]

    @staticmethod
    def _layers(path: Path) -> set[str]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {r.get("layer", "base") for r in data["rows"]}

    def test_no_glyph_appears_twice_on_one_layer(self, path: Path) -> None:
        for layer in self._layers(path):
            glyphs = self._layer_glyphs(path, layer)
            dupes = sorted({g for g in glyphs if glyphs.count(g) > 1})
            assert not dupes, f"{path.stem}/{layer}: {dupes} appear on more than one key"

    def test_shifted_variants_never_duplicate_a_visible_key(self, path: Path) -> None:
        """The reported bug, stated as a property rather than as its fix.

        On a layer that has a Shift key, holding Shift re-renders every key
        that declares a `shifted` variant. If some other key on that same
        layer already shows that glyph unshifted, the two keys become
        indistinguishable while Shift is down: on ?123 that made nine of them
        say the same thing as row 3, which is what was reported.

        This is the assertion that fails on the buggy data. The
        no-Shift-on-symbol-pages test above only describes how it was fixed,
        so on its own it would let an equivalent overlap through on a layer
        that kept its Shift key.
        """
        by_layer: dict[str, list] = {
            layer: [k for row in self._rows_on(path, layer) for k in row["keys"]]
            for layer in self._layers(path)
        }

        for layer, keys in by_layer.items():
            has_shift = any(
                k.get("type") == "modifier" and k.get("action") == "shift" for k in keys
            )
            if not has_shift:
                continue
            visible = {k["key"] for k in keys if k.get("type") == "char" and k.get("key")}
            collisions = sorted({k["shifted"] for k in keys if k.get("shifted") in visible})
            assert not collisions, (
                f"{path.stem}/{layer}: holding Shift renders {collisions}, which "
                "other keys on the same layer already show unshifted"
            )

    def test_symbol_pages_carry_no_shift_key(self, path: Path) -> None:
        """Shift is meaningless on a page with no letters, and worse than
        meaningless here: the modifier is held at the OS level, so with the
        shifted variants still declared for right-click, a held Shift made a
        key emit one glyph while displaying another."""
        data = json.loads(path.read_text(encoding="utf-8"))
        for row in data["rows"]:
            layer = row.get("layer", "base")
            if layer == "base":
                continue
            actions = [k.get("action") for k in row["keys"] if k.get("type") == "modifier"]
            assert "shift" not in actions, (
                f"{path.stem}/{layer}: a symbol page must not carry a Shift key"
            )

    def test_every_shifted_variant_on_a_symbol_page_has_its_own_key(self, path: Path) -> None:
        """The shifted variants stay declared, because right-click still types
        them, but none may be the *only* way to reach a glyph on these pages:
        that was the discoverability problem the dedicated symbol row was
        added to solve, and it is why Shift cannot simply be deleted."""
        data = json.loads(path.read_text(encoding="utf-8"))
        symbol_layers = self._layers(path) - {"base"}
        if not symbol_layers:
            return
        visible = {g for layer in symbol_layers for g in self._layer_glyphs(path, layer)}
        visible |= set(self._layer_glyphs(path, "base"))
        # Base-layer shifted variants are reachable by the base layer's own
        # Shift key, which still exists there.
        visible |= {
            k["shifted"]
            for row in data["rows"]
            if row.get("layer", "base") == "base"
            for k in row["keys"]
            if k.get("shifted")
        }
        unreachable = sorted(
            {
                k["shifted"]
                for row in data["rows"]
                if row.get("layer", "base") in symbol_layers
                for k in row["keys"]
                if k.get("shifted") and k["shifted"] not in visible
            }
        )
        assert not unreachable, (
            f"{path.stem}: {unreachable} are only reachable by Shift on a page "
            "that has no Shift key"
        )


class TestCompactEditingKeysAreAccented:
    """Esc, Tab, Shift, Backspace and Del are accent-filled on the compact
    layouts.

    Requested because the compact grid is uniform: with every key the same
    size there are no shape cues, so the keys a user reaches for without
    looking have to be found by colour. The full-size layouts keep their
    ordinary styling, where the wide Backspace and Shift are already
    distinguishable by size.
    """

    ACCENTED = {"escape", "tab", "shift", "backspace", "delete"}

    @pytest.mark.parametrize("name", ["qwerty-compact"])
    def test_every_editing_key_is_accented(self, name: str) -> None:
        data = _load(f"{name}.json")
        missing = [
            f"{row['id']}:{key['action']}"
            for row in data["rows"]
            for key in row["keys"]
            if key.get("action") in self.ACCENTED
            and key.get("type") in {"special", "modifier"}
            and key.get("style") != "accent"
        ]
        assert not missing, f"{name}: not accent-styled: {missing}"

    @pytest.mark.parametrize("name", ["qwerty-compact"])
    def test_nothing_else_is_accented(self, name: str) -> None:
        """The point is that these keys stand out. Accenting anything else
        dilutes them back into the grid."""
        data = _load(f"{name}.json")
        stray = [
            f"{row['id']}:{key.get('action') or key.get('key')}"
            for row in data["rows"]
            for key in row["keys"]
            if key.get("style") == "accent" and key.get("action") not in self.ACCENTED
        ]
        assert not stray, f"{name}: unexpected accent keys: {stray}"

    @pytest.mark.parametrize("name", ["qwerty", "dvorak", "colemak"])
    def test_full_size_layouts_are_untouched(self, name: str) -> None:
        data = _load(f"{name}.json")
        accented = [
            key.get("action")
            for row in data["rows"]
            for key in row["keys"]
            if key.get("style") == "accent"
        ]
        assert not accented, f"{name}: accent styling leaked onto a full-size layout"
