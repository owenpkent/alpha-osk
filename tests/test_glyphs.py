"""Tests for the Symbols & Emoji picker catalogue (src/glyphs.py).

The module is static data with no I/O, so the risk here isn't a runtime
failure, it's a *content* mistake that would only surface as a bug report
from Owen staring at a broken picker: an ASCII character wasting a slot
the base keyboard already covers, a duplicate glyph appearing twice in the
same tab, a skin-tone modifier nobody asked for, or a ZWJ sequence that
renders as three stacked glyphs in a 48px cell because the host font can't
compose it. Every test here pins one of those failure modes directly
against the data, rather than against a hand-picked sample of it.
"""

from __future__ import annotations

import re

from src.glyphs import GLYPH_CATEGORIES, MAX_RECENT, GlyphCategory, categories

_SKIN_TONE_MODIFIERS = {chr(cp) for cp in range(0x1F3FB, 0x1F3FF + 1)}
_ZWJ = "‍"

_EXPECTED_IDS = (
    "text",
    "arrows",
    "math",
    "money",
    "accents",
    "faces",
    "gestures",
    "nature",
    "food",
    "travel",
    "objects",
    "symbols",
)

_EXPECTED_LABELS = {
    "text": "Text",
    "arrows": "Arrows",
    "math": "Math",
    "money": "Money",
    "accents": "Accents",
    "faces": "Faces",
    "gestures": "Gestures",
    "nature": "Nature",
    "food": "Food",
    "travel": "Travel",
    "objects": "Objects",
    "symbols": "Symbols",
}

# The five categories that are typographic symbols rather than pictographs.
# Duplicates are forbidden *within* this group specifically per the spec,
# and the full-catalogue test below subsumes it, but keeping both makes a
# failure in just this group easy to name.
_TEXT_ISH_IDS = ("text", "arrows", "math", "money", "accents")

GLYPH_CATEGORIES_BY_ID = {c.id: c for c in GLYPH_CATEGORIES}


class TestCatalogueShape:
    """The catalogue is exactly the 12 documented categories, in order."""

    def test_there_are_exactly_twelve_categories(self) -> None:
        """A picker tab list that silently grew or shrank would be a UI
        regression nobody asked for; the count is part of the contract."""
        assert len(GLYPH_CATEGORIES) == 12

    def test_category_ids_and_order_match_the_spec(self) -> None:
        """The UI likely renders tabs in this order; reordering silently
        would move tabs under the user without anything failing loudly."""
        assert tuple(c.id for c in GLYPH_CATEGORIES) == _EXPECTED_IDS

    def test_category_labels_match_the_spec(self) -> None:
        """Labels are user-facing tab text, not internal identifiers, so
        they're pinned independently of the ids that route to them."""
        for category in GLYPH_CATEGORIES:
            assert category.label == _EXPECTED_LABELS[category.id]

    def test_every_category_is_a_glyph_category_instance(self) -> None:
        """Guards against someone swapping the tuple for a bare tuple of
        tuples, which would still "work" until a caller reads `.id`."""
        for category in GLYPH_CATEGORIES:
            assert isinstance(category, GlyphCategory)

    def test_category_ids_are_unique(self) -> None:
        """QML almost certainly keys the tab bar off `id`; a collision
        would make two tabs indistinguishable to the picker."""
        ids = [c.id for c in GLYPH_CATEGORIES]
        assert len(ids) == len(set(ids))

    def test_category_ids_are_lowercase_slugs(self) -> None:
        """The spec requires `[a-z_]+`; anything else (spaces, digits,
        uppercase) would be a surprising thing to see in a URL-ish id."""
        for category in GLYPH_CATEGORIES:
            assert re.fullmatch(r"[a-z_]+", category.id), category.id

    def test_every_label_is_non_empty(self) -> None:
        """An empty label would render as a blank, unclickable-looking tab."""
        for category in GLYPH_CATEGORIES:
            assert category.label.strip() != ""

    def test_max_recent_is_twenty_four(self) -> None:
        """Pinned so a future edit to the recents cap is a deliberate,
        reviewed change rather than an accidental one."""
        assert MAX_RECENT == 24


class TestTheCatalogueIsNotTriviallyEmpty:
    """Inverse coverage: a category that got emptied by a bad edit would
    still pass every other test here (no glyphs means no duplicates, no
    ASCII, no skin tones) unless something asserts a positive lower bound.
    """

    def test_every_category_has_at_least_fourteen_glyphs(self) -> None:
        for category in GLYPH_CATEGORIES:
            assert len(category.glyphs) >= 14, (
                f"{category.id!r} only has {len(category.glyphs)} glyphs"
            )

    def test_the_catalogue_has_several_hundred_glyphs_total(self) -> None:
        total = sum(len(c.glyphs) for c in GLYPH_CATEGORIES)
        assert total >= 300


class TestNoGlyphIsAsciiOrEmpty:
    """The base keyboard already types every ASCII symbol directly, so an
    ASCII entry here would be a picker slot spent saying something the
    keyboard already says on its own keys. An empty string would also be a
    dead, invisible tile."""

    def test_no_glyph_is_pure_ascii(self) -> None:
        for category in GLYPH_CATEGORIES:
            for glyph in category.glyphs:
                assert any(ord(ch) >= 0x80 for ch in glyph), (
                    f"{glyph!r} in {category.id!r} is pure ASCII"
                )

    def test_no_glyph_is_empty(self) -> None:
        for category in GLYPH_CATEGORIES:
            for glyph in category.glyphs:
                assert glyph != ""


class TestNoDuplicates:
    """Two different failure radii: within one tab (visually confusing,
    the user sees the same glyph twice while scanning) and across the
    whole catalogue (the spec's stricter global requirement)."""

    def test_no_duplicates_within_any_single_category(self) -> None:
        for category in GLYPH_CATEGORIES:
            assert len(category.glyphs) == len(set(category.glyphs)), category.id

    def test_no_duplicates_across_the_five_text_ish_categories(self) -> None:
        """text/arrows/math/money/accents share one visual register (plain
        monochrome symbols); a glyph repeated between them would mean the
        same key does two different things depending which tab it's tapped
        from, or just wastes a slot."""
        seen: set[str] = set()
        by_id = {c.id: c for c in GLYPH_CATEGORIES}
        for cat_id in _TEXT_ISH_IDS:
            for glyph in by_id[cat_id].glyphs:
                assert glyph not in seen, f"{glyph!r} duplicated across text-ish categories"
                seen.add(glyph)

    def test_no_duplicates_anywhere_in_the_whole_catalogue(self) -> None:
        """The spec's strongest requirement: not one repeated glyph across
        all twelve tabs, emoji categories included. A version that only
        checked the text-ish group could satisfy that test while still
        letting two emoji tabs collide."""
        seen: set[str] = set()
        for category in GLYPH_CATEGORIES:
            for glyph in category.glyphs:
                assert glyph not in seen, (
                    f"{glyph!r} appears in more than one category "
                    f"(second sighting in {category.id!r})"
                )
                seen.add(glyph)


class TestNoSkinToneOrZwj:
    """Skin-tone modifiers apply a tone the user never chose, and a ZWJ
    sequence the host font can't compose renders as several separate
    glyphs jammed into one picker cell. Both are worse than not offering
    the glyph at all, so neither may appear anywhere in the catalogue."""

    def test_no_glyph_contains_a_skin_tone_modifier(self) -> None:
        for category in GLYPH_CATEGORIES:
            for glyph in category.glyphs:
                assert not (set(glyph) & _SKIN_TONE_MODIFIERS), (
                    f"{glyph!r} in {category.id!r} carries a skin-tone modifier"
                )

    def test_no_glyph_contains_a_zero_width_joiner(self) -> None:
        for category in GLYPH_CATEGORIES:
            for glyph in category.glyphs:
                assert _ZWJ not in glyph, f"{glyph!r} in {category.id!r} is a ZWJ sequence"

    def test_every_glyph_is_at_most_a_base_code_point_plus_vs16(self) -> None:
        """ "Single base code point, optionally followed by VS16" is a
        length constraint: with ZWJ and skin tones both ruled out, nothing
        legitimate should ever be longer than two code points."""
        for category in GLYPH_CATEGORIES:
            for glyph in category.glyphs:
                assert 1 <= len(glyph) <= 2, f"{glyph!r} in {category.id!r} has length {len(glyph)}"
                if len(glyph) == 2:
                    assert ord(glyph[1]) == 0xFE0F, (
                        f"{glyph!r} in {category.id!r} has a second code point that isn't VS16"
                    )


class TestKnownPictographsCarryVs16WhereTheDefaultPresentationIsText:
    """Spot-check the classic cases named in the spec: a heart, a check
    mark and a warning sign all default to a monochrome text glyph, so
    without VS16 they'd render flat beside every colour emoji around them.
    Star and cross mark are already emoji-default, so the absence of VS16
    on those is the correct choice, not an oversight."""

    def test_red_heart_carries_vs16(self) -> None:
        assert "❤️" in GLYPH_CATEGORIES_BY_ID["symbols"].glyphs

    def test_heavy_check_mark_carries_vs16(self) -> None:
        assert "✔️" in GLYPH_CATEGORIES_BY_ID["symbols"].glyphs

    def test_warning_sign_carries_vs16(self) -> None:
        assert "⚠️" in GLYPH_CATEGORIES_BY_ID["symbols"].glyphs

    def test_star_needs_no_vs16_because_it_is_already_emoji_default(self) -> None:
        assert "⭐" in GLYPH_CATEGORIES_BY_ID["symbols"].glyphs

    def test_cross_mark_needs_no_vs16_because_it_is_already_emoji_default(self) -> None:
        assert "❌" in GLYPH_CATEGORIES_BY_ID["symbols"].glyphs


class TestAccentsAreLowercaseOnly:
    """The spec is explicit that accents are lowercase-only, to avoid
    doubling the tab's size for little gain now that Recent exists."""

    def test_no_accent_glyph_is_uppercase(self) -> None:
        accents = GLYPH_CATEGORIES_BY_ID["accents"]
        for glyph in accents.glyphs:
            assert glyph == glyph.lower(), f"{glyph!r} is not lowercase"


class TestCategoriesFunction:
    """`categories()` is what actually crosses the QML boundary via a Qt
    slot; the NamedTuple/tuple data above never does directly."""

    def test_categories_returns_a_plain_list(self) -> None:
        result = categories()
        assert type(result) is list

    def test_each_entry_is_a_plain_dict_not_a_namedtuple(self) -> None:
        """A NamedTuple crossing the Qt/QML boundary doesn't arrive as a
        JS object the way a dict does; QML would see an opaque value it
        can't index by `.id` / `.label` / `.glyphs`. This is the test that
        would have caught returning `GLYPH_CATEGORIES` unconverted."""
        for entry in categories():
            assert type(entry) is dict
            assert not isinstance(entry, GlyphCategory)

    def test_each_glyphs_value_is_a_plain_list_not_a_tuple(self) -> None:
        for entry in categories():
            assert type(entry["glyphs"]) is list

    def test_entries_have_exactly_the_three_expected_keys(self) -> None:
        for entry in categories():
            assert set(entry.keys()) == {"id", "label", "glyphs"}

    def test_entry_values_have_plain_str_and_list_types(self) -> None:
        for entry in categories():
            assert type(entry["id"]) is str
            assert type(entry["label"]) is str
            for glyph in entry["glyphs"]:
                assert type(glyph) is str

    def test_categories_matches_glyph_categories_content(self) -> None:
        """The conversion must be a faithful mirror, not just the right
        shape: same ids in the same order, same glyphs in the same order."""
        result = categories()
        assert [entry["id"] for entry in result] == [c.id for c in GLYPH_CATEGORIES]
        for entry, category in zip(result, GLYPH_CATEGORIES):
            assert entry["label"] == category.label
            assert entry["glyphs"] == list(category.glyphs)

    def test_calling_categories_twice_returns_independent_lists(self) -> None:
        """A caller mutating one returned list must not corrupt the module's
        static data for the next caller (e.g. two picker windows open)."""
        first = categories()
        first[0]["glyphs"].append("should not persist")
        second = categories()
        assert "should not persist" not in second[0]["glyphs"]
