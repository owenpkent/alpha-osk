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

    def test_delete_is_on_the_entry_layer(self, path: Path) -> None:
        """Forward-delete must be one tap away, not behind a ?123 hop.

        Backspace alone means the caret has to be walked past a mistake and
        back; on a pointer-driven keyboard that is several extra clicks.
        Rows with no `layer` field are the full-size layouts' single layer.
        """
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = [r for r in data["rows"] if r.get("layer", "base") == "base"]
        actions = {k.get("action") for r in entry for k in r["keys"] if k.get("type") == "special"}
        assert "delete" in actions, f"{path.stem}: no Del key on the base layer"

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


class TestFullSizeLayoutsUnchanged:
    """The compact work must not perturb the shipped full-size layouts."""

    @pytest.mark.parametrize("name", ["qwerty", "dvorak", "colemak"])
    def test_no_layer_field(self, name: str) -> None:
        # Rows with no `layer` always render, which is what keeps the
        # full-size layouts working under the layer-filtering QML.
        for row in _load(f"{name}.json")["rows"]:
            assert "layer" not in row

    @pytest.mark.parametrize("name", ["qwerty", "dvorak", "colemak"])
    def test_widest_row_is_still_15_5_units(self, name: str) -> None:
        # Main.qml derives keyW from the widest row; this is the historical
        # number the default 940 px window width is tuned against.
        rows = _load(f"{name}.json")["rows"]
        widest = max(rows, key=_row_units)
        assert _row_units(widest) == pytest.approx(15.5)
        assert len(widest["keys"]) - 1 == 14, "gap count feeds layoutFixedPixels"


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

    def test_has_exactly_two_layers_of_four_rows(self, compact: dict) -> None:
        layers: dict[str, list] = {}
        for row in compact["rows"]:
            layers.setdefault(row["layer"], []).append(row)
        assert set(layers) == {"base", "sym"}
        assert len(layers["base"]) == 4
        assert len(layers["sym"]) == 4

    def test_bottom_row_identical_across_layers(self, compact: dict) -> None:
        """Space, modifiers and the arrows must not move on a layer switch."""
        rows = {r["id"]: r for r in compact["rows"]}
        base = [dict(k) for k in rows["base-4"]["keys"]]
        sym = [dict(k) for k in rows["sym-4"]["keys"]]
        # The layer key itself necessarily differs (?123 vs ABC).
        for keys in (base, sym):
            keys[0].pop("target")
            keys[0].pop("display")
        assert base == sym

    def test_nav_column_is_identical_across_layers(self, compact: dict) -> None:
        """PgUp/PgDn/Home/End hold position when switching to ?123."""
        rows = {r["id"]: r for r in compact["rows"]}
        for base_id, sym_id in (("base-1", "sym-1"), ("base-2", "sym-2"), ("base-3", "sym-3")):
            assert rows[base_id]["keys"][-1] == rows[sym_id]["keys"][-1], (
                f"nav key differs between {base_id} and {sym_id}"
            )

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

    def test_get_layout_rows_returns_both_layers(self) -> None:
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
        assert len(rows) == 8
        assert {r["layer"] for r in rows} == {"base", "sym"}
