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

# The three letter rows and the symbol row that replaces each of them.
SWAPPED_ROWS = [("top", "sym-top"), ("home", "sym-home"), ("bottom", "sym-bottom")]


class TestFullSizeSymbolLayer:
    """The full-size layouts carry one symbol page, reached from the space row.

    Compact View had ``?123`` and ``=\\<`` from the start and the full-size
    layouts had nothing, so every glyph outside a physical keyboard's
    printing was reachable in one view and not the other. These tests pin
    the four properties that make the page cost nothing to have: the grid
    does not move, digits and space never leave the screen, the page can
    always be left again, and nothing on it duplicates a glyph the base
    layer could already type.
    """

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_the_grid_does_not_move_when_the_layer_switches(self, name: str) -> None:
        """A symbol row matches the letter row it replaces, unit for unit.

        Rows are centred individually, so a symbol row even slightly
        narrower than its base counterpart re-indents the whole page and
        every key lands somewhere new. On a keyboard driven by an imprecise
        pointer that is the difference between a hop the user can predict
        and one they have to re-aim after.
        """
        rows = {r["id"]: r for r in _load(f"{name}.json")["rows"]}
        for base_id, sym_id in SWAPPED_ROWS:
            base, sym = rows[base_id], rows[sym_id]
            assert _row_units(sym) == pytest.approx(_row_units(base)), (
                f"{name}/{sym_id} is {_row_units(sym)}u against {base_id}'s {_row_units(base)}u"
            )
            assert len(sym["keys"]) == len(base["keys"]), (
                f"{name}/{sym_id} has {len(sym['keys'])} keys against "
                f"{base_id}'s {len(base['keys'])}: equal totals alone still "
                "move every key, because the gaps between them move"
            )

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_digits_and_space_never_disappear(self, name: str) -> None:
        """The number and space rows carry no `layer`, so they render on
        every page. That is what lets the symbol layer swap only the three
        letter rows: digits stay one tap away instead of going behind the
        hop the way they do in Compact View, and the space bar, the most
        clicked key on the keyboard, never moves or vanishes."""
        for row in _load(f"{name}.json")["rows"]:
            if row["id"] in ("number", "space"):
                assert "layer" not in row, f"{name}/{row['id']} is behind a layer"

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_the_entry_keys_flank_the_space_bar_symmetrically(self, name: str) -> None:
        """Equal width added to both ends of a centred row leaves every key
        already in it exactly where it was. That is the whole reason there
        are two Sym keys rather than one: a single key appended to either
        end would have slid Ctrl, Win, Alt and the space bar sideways by
        half a key width on a layout the user has used daily for months."""
        space = next(r for r in _load(f"{name}.json")["rows"] if r["id"] == "space")
        first, last = space["keys"][0], space["keys"][-1]
        for end in (first, last):
            assert end.get("type") == "layer" and end.get("target") == "sym", (
                f"{name}: the space row does not open the symbol page from both ends"
            )
        assert first["width"] == last["width"], (
            f"{name}: Sym keys are {first['width']}u and {last['width']}u, so the "
            "row is no longer symmetric and everything inside it has moved"
        )

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_the_symbol_page_can_always_be_left(self, name: str) -> None:
        """Two ways back, and both are needed. The ABC keys sit where the
        Shift keys they replace were (a symbol page must carry no Shift, see
        TestNoDuplicateGlyphsWithinALayer), and the Sym key on the space row
        is on a row that renders on every page, so QML sends a layer key
        already showing its own target back to base."""
        rows = _load(f"{name}.json")["rows"]
        sym_rows = [r for r in rows if r.get("layer") == "sym"]
        back = [
            k
            for r in sym_rows
            for k in r["keys"]
            if k.get("type") == "layer" and k.get("target") == "base"
        ]
        assert back, f"{name}: the symbol page has no ABC key"

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_the_editing_keys_keep_their_slots(self, name: str) -> None:
        """Tab, Caps and Enter are in the same position and width on the
        symbol page as on the letters. Full size has the room compact did
        not, so a comma typed on the symbol page does not cost a hop back to
        reach Enter. The top row's right end is a plain character on both
        pages: Del is not on the grid (it lives above the arrows on the
        Navigation panel), so only Tab is pinned there."""
        rows = {r["id"]: r for r in _load(f"{name}.json")["rows"]}
        pinned = {"top": (0,), "home": (0, -1)}
        for base_id, sym_id in SWAPPED_ROWS[:2]:
            base, sym = rows[base_id], rows[sym_id]
            for index in pinned[base_id]:
                assert base["keys"][index] == sym["keys"][index], (
                    f"{name}/{sym_id}: the key at index {index} differs from {base_id}"
                )

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_every_symbol_key_types_itself_literally(self, name: str) -> None:
        """`literal` routes the key through pressKeyLiteral, which skips the
        shift / caps-lock case normalisation pressKey applies.

        Caps Lock deliberately survives a layer switch, and Python's upper()
        is not the identity on every non-ASCII character: without this, Caps
        Lock plus the micro sign typed a Greek capital Mu.
        """
        for row in _load(f"{name}.json")["rows"]:
            if row.get("layer") != "sym":
                continue
            for key in row["keys"]:
                if key.get("type") == "char":
                    assert key.get("literal") is True, (
                        f"{name}/{row['id']}: {key['key']!r} is not marked literal"
                    )

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_no_symbol_repeats_what_the_base_layer_already_types(self, name: str) -> None:
        """The page is worth a hop only for glyphs that have nowhere else to
        come from. Every ASCII symbol is already on the base layer, either
        printed on a key or as a shifted variant that Shift and right-click
        both reach, so putting one here would spend a slot saying something
        the keyboard already said. This is the same property
        TestNoDuplicateGlyphsWithinALayer states within a single page,
        applied across the hop.
        """
        rows = _load(f"{name}.json")["rows"]
        base = [r for r in rows if r.get("layer", "base") == "base"]
        reachable = {k["key"] for r in base for k in r["keys"] if k.get("type") == "char"}
        reachable |= {k["shifted"] for r in base for k in r["keys"] if k.get("shifted")}
        repeats = sorted(
            {
                k["key"]
                for r in rows
                if r.get("layer") == "sym"
                for k in r["keys"]
                if k.get("type") == "char" and k["key"] in reachable
            }
        )
        assert not repeats, f"{name}: {repeats} are already on the base layer"

    @pytest.mark.parametrize("name", FULL_SIZE)
    def test_widest_row_is_still_15_5_units(self, name: str) -> None:
        # Main.qml derives keyW from the widest row; this is the historical
        # number the default 940 px window width is tuned against.
        rows = _load(f"{name}.json")["rows"]
        widest = max(rows, key=_row_units)
        assert _row_units(widest) == pytest.approx(15.5)
        assert len(widest["keys"]) - 1 == 14, "gap count feeds layoutFixedPixels"


class TestTheLetterColumnsLineUp:
    """W sits directly above S on the full-size layouts, for WASD gaming.

    Main.qml centres every row against the widest one (each row is a `Row`
    with `Layout.alignment: Qt.AlignHCenter`), so a row's horizontal offset
    is `(widest_units - row_units) / 2` and its first letter's left edge is
    that offset plus the leading modifier's width.  The top row used to
    carry a Del key past the backslash, which made it 0.9u wider than the
    home row and so pushed the whole letter block four fifths of a key
    left: W landed between A and S.  On a pointer-driven keyboard that
    turns every W->S in a WASD pair into a diagonal drag, which is the one
    movement slow motor input is worst at.

    Del leaves the main grid altogether: it already sits above the arrows
    on the Navigation panel, which is shown by default, and the space row
    cannot take it since the symbol layer put a Sym key at each end (a
    third key there overflows the 15.5u number row and widens the window).
    Enter grew 1.8u -> 2.3u (standard ANSI is 2.25u) to take back the
    half-unit the top row lost. Both halves are needed: dropping Del alone
    leaves W a quarter-key short, and widening Enter alone overshoots. The
    symbol page follows, because each of its rows has to match the letter
    row it replaces in units and key count.

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
        # Del is free only while the space row stays clear of the widest
        # row; past that it would widen the whole keyboard.
        rows, widest = self._rows(name)
        assert _row_units(rows["space"]) < widest


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

    # A row with no `layer` field renders on *every* layer, which is how both
    # the full-size layouts and their symbol page are built. Skipping such a
    # row (`if r.get("layer")`) returned the empty set for qwerty / dvorak /
    # colemak, so two of the tests below iterated nothing and passed without
    # asserting anything while the parametrize ids advertised coverage of all
    # four layouts. Defaulting it to "base" fixed that and was right while
    # full size had a single layer; now that it has two, "base" is one layer
    # too few, and a glyph put on the always-visible number row and on the
    # symbol page as well would collide on screen with nothing to catch it.

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
