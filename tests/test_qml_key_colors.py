"""Headless QML tests for the shared section height and Key Colours.

Two features land here because they are two halves of one change: the board
was ragged both vertically (three sections at three heights) and
semantically (a key's colour said which panel it sat in, never what it
did). Both live entirely in QML, so the Python suite cannot reach either.

The geometry half asserts against *measured* item positions rather than
recomputing the rectangle from the same properties the QML sets, for the
reason `TestNoDeadStripBetweenKeys` gives: an assertion that repeats the
implementation's arithmetic passes whatever that arithmetic does.

The colour half's load-bearing test is `TestEveryLegendStaysReadable`. Nine
themes ship, several with a pale accent and one light outright, and the
whole promise of `qml/palette.js` is that no scheme can cost legibility on
any of them. That promise is worth exactly what the test that checks it is
worth, so it sweeps every scheme x every theme x every role rather than
spot-checking the theme the developer happens to run.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QSettings, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

    # Imported for the side effect: without QQuickItem somewhere in the
    # module, reading `root.contentItem` raises "Can't find converter for
    # 'QQuickItem*'". Walking the visual tree is the only way to reach a
    # Repeater's delegates.
    from PySide6.QtQuick import QQuickItem  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)

# Every scheme the picker offers, and every theme it has to survive.
SCHEMES = ("off", "mono", "twotone", "bands", "ink", "signal")
COLOURED_SCHEMES = tuple(s for s in SCHEMES if s != "off")
ROLES = (
    "alpha",
    "digit",
    "punct",
    "mod",
    "edit",
    "kill",
    "commit",
    "nav",
    "fn",
    "op",
    "toggle",
    "pill",
)


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(f in w for f in IGNORED_WARNING_FRAGMENTS)]


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        # A distinct org/app name keeps the QML `Settings` element off the
        # real user's registry section - otherwise running the suite would
        # overwrite their saved theme and window width.
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation - these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def qml_root(qapp):
    """Load Main.qml with both side panels and the function row showing.

    The function row matters: with it hidden the keyboard grid is no longer
    the tallest section, which is the branch `sectionHeight` takes its max
    for. `TestTheSectionsShareOneHeight` exercises both.
    """
    warnings: list[str] = []
    QSettings(TEST_ORG, TEST_APP).clear()

    settings = QSettings(TEST_ORG, TEST_APP)
    # Disarm the startup update check: it fires a real HTTPS request from a
    # daemon thread that outlives the fixture. See test_qml_compact_view.
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.setValue("ui/savedShowFunctionRow", True)
    settings.setValue("ui/savedShowNumpad", True)
    settings.setValue("ui/savedShowNavigation", True)
    settings.sync()

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    root.setProperty("width", 1400)
    _settle()
    try:
        yield root, warnings
    finally:
        del engine


def _settle(passes: int = 12) -> None:
    """Flush Qt Quick's delegate creation and layout.

    Anything that re-resolves the layout (a theme change, a scheme change)
    tears delegates down and rebuilds them, and one pass does not reliably
    finish that.
    """
    for _ in range(passes):
        QCoreApplication.processEvents()


def _content(item):
    """The item to walk from.

    `Main.qml`'s root is a Window, which is not an Item and has no
    `childItems()`; its visual tree hangs off `contentItem`.
    """
    return item.contentItem() if hasattr(item, "contentItem") else item


def _descendants(item) -> list:
    out = []
    for child in _content(item).childItems():
        out.append(child)
        out.extend(_descendants(child))
    return out


def _keys(item) -> list:
    """Every visible KeyButton under *item*.

    A Repeater's delegates are visual children whose QObject parent is the
    model, so `findChildren` returns none of them; the tree has to be
    walked. Wrappers are used immediately and never held across a pump.
    """
    return [k for k in _descendants(item) if k.property("keyText") is not None and k.isVisible()]


def _rect(item) -> tuple[float, float, float, float]:
    p = item.mapToItem(None, 0, 0)
    return p.x(), p.y(), item.width(), item.height()


def _sections(root) -> dict:
    nav = root.findChild(QQuickItem, "navigationPanel")
    numpad = root.findChild(QQuickItem, "numpadPanel")
    fn_row = root.findChild(QQuickItem, "functionRowPanel")
    assert nav is not None and numpad is not None and fn_row is not None
    grid = fn_row.parentItem()
    return {"grid": grid, "nav": nav, "numpad": numpad}


def _rows(panel) -> list[tuple[float, float]]:
    """(top, height) per row of keys in a side panel, ordered top to bottom."""
    rows: dict[float, float] = {}
    for key in _keys(panel):
        _, y, _, h = _rect(key)
        rows[round(y, 1)] = h
    return [(y, rows[y]) for y in sorted(rows)]


def _relative_luminance(colour) -> float:
    def channel(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(colour.redF())
        + 0.7152 * channel(colour.greenF())
        + 0.0722 * channel(colour.blueF())
    )


def _contrast(a, b) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


class TestTheSectionsShareOneHeight:
    """The grid, the nav cluster, the numpad and both separators line up.

    Before this they were five different heights: measured at a 1400 px
    window, separators 105-433, grid 115-423, nav 130-408, numpad 134-404.
    """

    def test_all_three_sections_share_a_top_and_bottom_edge(self, qml_root) -> None:
        root, warnings = qml_root
        sections = _sections(root)
        tops, bottoms = set(), set()
        for item in sections.values():
            _, y, _, h = _rect(item)
            tops.add(round(y, 1))
            bottoms.add(round(y + h, 1))
        assert len(tops) == 1, f"sections start at different heights: {sorted(tops)}"
        assert len(bottoms) == 1, f"sections end at different heights: {sorted(bottoms)}"
        assert _real_warnings(warnings) == []

    def test_the_separators_match_the_sections_they_divide(self, qml_root) -> None:
        # `Layout.fillHeight` ran them 10 px past the board at each end,
        # which is two more edges lining up with nothing.
        root, _ = qml_root
        _, top, _, height = _rect(_sections(root)["grid"])
        separators = [
            item
            for item in _descendants(root)
            if item.isVisible()
            and item.property("keyText") is None
            and abs(item.width() - 1.0) < 0.01
            and item.height() > 100
        ]
        assert separators, "expected the two panel separators"
        for sep in separators:
            _, sy, _, sh = _rect(sep)
            assert (round(sy, 1), round(sh, 1)) == (round(top, 1), round(height, 1))

    def test_the_arrow_cluster_lands_on_the_bottom_rail(self, qml_root) -> None:
        root, _ = qml_root
        nav = _sections(root)["nav"]
        _, nav_top, _, nav_height = _rect(nav)
        last_top, last_height = _rows(nav)[-1]
        assert last_top + last_height == pytest.approx(nav_top + nav_height, abs=0.6)

    def test_the_nav_gutter_still_separates_the_arrows(self, qml_root) -> None:
        # A physical keyboard separates the nav block from the arrows, and
        # without it the six-key block and the arrows read as one
        # undifferentiated field. Filling the height must not spend it.
        root, _ = qml_root
        rows = _rows(_sections(root)["nav"])
        assert len(rows) == 5
        gaps = [rows[i + 1][0] - (rows[i][0] + rows[i][1]) for i in range(4)]
        assert gaps[2] > max(gaps[0], gaps[1], gaps[3]) + 1, (
            f"the arrow gutter is gone: gaps {gaps}"
        )

    def test_the_numpad_has_no_seam(self, qml_root) -> None:
        # The inverse, and a reversal: the numpad briefly carried a matching
        # gap after its third row so its rows would line up with the nav
        # cluster's. A physical numpad has no seam there, and one splitting
        # the digits off the 0 key is more obviously wrong than two panels
        # whose rows drift a pixel or two apart.
        root, _ = qml_root
        rows = _rows(_sections(root)["numpad"])
        assert len(rows) == 5
        gaps = [rows[i + 1][0] - (rows[i][0] + rows[i][1]) for i in range(4)]
        assert max(gaps) - min(gaps) < 1.5, f"the numpad rows are unevenly spaced: {gaps}"

    def test_panel_keys_are_never_shorter_than_the_letters(self, qml_root) -> None:
        # Filling the height must only ever grow a target. A version that
        # divided the height without the `Math.max` floor shrank them by
        # about 2 px whenever the panels were the taller section.
        root, _ = qml_root
        sections = _sections(root)
        letter_height = min(k.height() for k in _keys(sections["grid"]))
        for name in ("nav", "numpad"):
            for key in _keys(sections[name]):
                assert key.height() >= letter_height - 0.01

    def test_the_keyboard_grid_rows_are_still_flush(self, qml_root) -> None:
        # The grid was already straight; this change must not disturb it.
        root, _ = qml_root
        grid = _sections(root)["grid"]
        edges = set()
        for row in grid.childItems():
            if not row.isVisible():
                continue
            keys = sorted(_keys(row), key=lambda k: _rect(k)[0])
            if not keys:
                continue
            left = _rect(keys[0])[0]
            right = _rect(keys[-1])[0] + keys[-1].width()
            edges.add((round(left, 1), round(right, 1)))
        assert len(edges) == 1, f"grid rows no longer share their edges: {sorted(edges)}"

    def test_the_sections_still_line_up_without_the_function_row(self, qml_root) -> None:
        # The branch `sectionHeight` takes its max for: with the function
        # row hidden the panels are the tallest section, not the grid.
        root, warnings = qml_root
        root.setProperty("showFunctionRow", False)
        _settle()
        sections = _sections(root)
        spans = {
            name: (round(_rect(i)[1], 1), round(_rect(i)[1] + _rect(i)[3], 1))
            for name, i in sections.items()
        }
        assert len(set(spans.values())) == 1, f"ragged with no function row: {spans}"
        assert _real_warnings(warnings) == []


class TestTheDefaultScheme:
    """Monochrome ships as the default; "off" restores the historical board."""

    def test_the_default_scheme_is_monochrome(self, qml_root) -> None:
        root, _ = qml_root
        assert root.property("keyColorScheme") == "mono"

    def test_off_hands_down_no_role_table(self, qml_root) -> None:
        # Every surface reads a null table as "keep your own tint", which is
        # what lets "off" be byte-identical to the pre-feature board without
        # a per-surface branch anywhere.
        root, _ = qml_root
        root.setProperty("keyColorScheme", "off")
        _settle()
        assert root.property("keyRoles") is None

    def test_off_leaves_every_key_on_its_surface_colour(self, qml_root) -> None:
        root, _ = qml_root
        root.setProperty("keyColorScheme", "off")
        _settle()
        for key in _keys(root):
            assert key.property("_roleFill") == key.property("keyColor")
            assert key.property("_roleInk") == key.property("keyTextColor")

    def test_monochrome_really_is_monochrome(self, qml_root) -> None:
        # Modifiers took a theme-accent wash here for one revision, which
        # put a standing blue-grey on Caps and both Shifts. A modifier's
        # theme colour is its *click* colour; the resting cap stays neutral.
        # `toggle` is the deliberate exception, being a live state rather
        # than a resting key.
        root, _ = qml_root
        root.setProperty("keyColorScheme", "mono")
        _settle()
        table = root.property("keyRoles").toVariant()

        def saturation(colour) -> float:
            channels = (colour.redF(), colour.greenF(), colour.blueF())
            return max(channels) - min(channels)

        neutral = saturation(table["alpha"]["fill"])
        for role in ROLES:
            if role == "toggle":
                continue
            assert saturation(table[role]["fill"]) <= neutral + 0.02, (
                f"the {role} keycap picked up a hue; monochrome has none"
            )

    def test_a_modifiers_click_colour_is_theme_derived(self, qml_root) -> None:
        # What "theme the modifier colours" actually asks for, and it is
        # already true: KeyButton paints `accentColor` while a modifier is
        # active and `keyPressedColor` while it is held, and both are fed
        # straight from the theme. Pinned so a future scheme cannot quietly
        # hardcode either one.
        root, _ = qml_root
        grid = _sections(root)["grid"]
        modifiers = [k for k in _keys(grid) if k.property("role") == "mod"]
        assert len(modifiers) >= 6
        for key in modifiers:
            assert key.property("accentColor") == root.property("themeAccent")
            assert key.property("keyPressedColor") == root.property("themeKeyPressed")


class TestSchemesRepaintTheBoard:
    @pytest.mark.parametrize("scheme", COLOURED_SCHEMES)
    def test_selecting_a_scheme_builds_a_table(self, qml_root, scheme) -> None:
        root, warnings = qml_root
        root.setProperty("keyColorScheme", scheme)
        _settle()
        table = root.property("keyRoles")
        assert table is not None, f"{scheme} produced no role table"
        assert _real_warnings(warnings) == []

    def test_a_fill_scheme_actually_changes_keycaps(self, qml_root) -> None:
        root, _ = qml_root
        before = {}
        for key in _keys(root):
            before[key.property("keyText")] = key.property("_roleFill").name()
        root.setProperty("keyColorScheme", "bands")
        _settle()
        after = {}
        for key in _keys(root):
            after[key.property("keyText")] = key.property("_roleFill").name()
        changed = [k for k in before if k in after and before[k] != after[k]]
        assert len(changed) > 10, "Function bands repainted almost nothing"

    def test_the_ink_scheme_colours_letters_rather_than_keycaps(self, qml_root) -> None:
        # Its whole point is one flat field, so a fill-based assertion would
        # pass against a scheme that had quietly become another band scheme.
        root, _ = qml_root
        root.setProperty("keyColorScheme", "ink")
        _settle()
        fills, inks = set(), set()
        for key in _keys(root):
            fills.add(key.property("_roleFill").name())
            inks.add(key.property("_roleInk").name())
        assert len(fills) <= 2, f"ink scheme painted {len(fills)} different keycaps"
        assert len(inks) >= 5, "ink scheme did not vary the legend colour"

    def test_only_the_ink_scheme_draws_a_role_stripe(self, qml_root) -> None:
        root, _ = qml_root
        for scheme in SCHEMES:
            root.setProperty("keyColorScheme", scheme)
            _settle()
            striped = [k for k in _keys(root) if k.property("_roleBar").alpha() > 0]
            if scheme == "ink":
                assert striped, "ink scheme drew no stripes"
            else:
                assert not striped, f"{scheme} drew a role stripe"

    def test_signal_leaves_the_letters_alone(self, qml_root) -> None:
        # Minimum ink is the whole proposition; a version that tinted
        # navigation too would still look fine and would not be this scheme.
        root, _ = qml_root
        root.setProperty("keyColorScheme", "signal")
        _settle()
        table = root.property("keyRoles").toVariant()
        assert table["alpha"]["fill"] == table["nav"]["fill"] == table["fn"]["fill"]
        assert table["kill"]["fill"] != table["alpha"]["fill"]
        assert table["commit"]["fill"] != table["alpha"]["fill"]
        assert table["mod"]["fill"] != table["alpha"]["fill"]


class TestThePredictionPillsFollowTheScheme:
    """The pills were the one surface a scheme did not reach."""

    def test_off_leaves_the_pills_on_the_theme_key_colour(self, qml_root) -> None:
        root, _ = qml_root
        root.setProperty("keyColorScheme", "off")
        _settle()
        assert root.property("predPillFill") == root.property("themeKeyColor")
        assert root.property("predPillInk") == root.property("themeTextColor")
        assert root.property("predPillBorder") == root.property("themeAccent")

    @pytest.mark.parametrize("scheme", COLOURED_SCHEMES)
    def test_a_scheme_repaints_the_pills(self, qml_root, scheme) -> None:
        root, warnings = qml_root
        root.setProperty("keyColorScheme", scheme)
        _settle()
        fill = root.property("predPillFill")
        assert fill is not None
        # The ring is what marks a pill as the thing to reach for, so it
        # stays the full theme accent whatever the fill does. Blending it
        # toward the fill was tried once and washed the whole row out.
        assert root.property("predPillBorder") == root.property("themeAccent")
        assert _real_warnings(warnings) == []

    def test_monochrome_pills_match_the_letters(self, qml_root) -> None:
        # Lifting the fill toward the ink greys the row out against the
        # board. Flat plus the accent ring is what reads as crisp.
        root, _ = qml_root
        root.setProperty("keyColorScheme", "mono")
        _settle()
        assert root.property("predPillFill") == root.property("themeKeyColor")

    def test_the_pill_legend_stays_readable_on_every_theme(self, qml_root) -> None:
        root, _ = qml_root
        themes = root.property("themeData").toVariant()
        for theme in themes:
            root.setProperty("currentTheme", theme)
            for scheme in COLOURED_SCHEMES:
                root.setProperty("keyColorScheme", scheme)
                _settle(3)
                ratio = _contrast(root.property("predPillInk"), root.property("predPillFill"))
                assert ratio >= 4.49, f"{scheme} on {theme}: the pill label is only {ratio:.2f}:1"


class TestKeysAreGivenTheRightJob:
    """Paired with the near-miss each rule has to keep rejecting."""

    @pytest.mark.parametrize(
        "key_text,expected",
        [
            ("backspace", "kill"),
            ("return", "commit"),
            ("tab", "edit"),
            ("escape", "edit"),
            ("space", "edit"),
            ("shift", "mod"),
            ("ctrl", "mod"),
            ("caps", "mod"),
            ("a", "alpha"),
            ("5", "digit"),
            (";", "punct"),
        ],
    )
    def test_the_main_grid_names_each_key_its_job(self, qml_root, key_text, expected) -> None:
        root, _ = qml_root
        grid = _sections(root)["grid"]
        matches = [k for k in _keys(grid) if k.property("keyText") == key_text]
        assert matches, f"no {key_text!r} key on the grid"
        for key in matches:
            assert key.property("role") == expected

    def test_a_letter_is_never_treated_as_destructive(self, qml_root) -> None:
        # The inverse of the Backspace case: a rule that returned "kill"
        # for everything would satisfy the parametrised test above.
        root, _ = qml_root
        grid = _sections(root)["grid"]
        letters = [
            k
            for k in _keys(grid)
            if len(k.property("keyText") or "") == 1 and (k.property("keyText") or "").isalpha()
        ]
        assert len(letters) > 20
        assert all(k.property("role") == "alpha" for k in letters)

    def test_the_function_rows_are_function_keys(self, qml_root) -> None:
        root, _ = qml_root
        root.setProperty("showExtraFunctionRow", True)
        _settle()
        for name in ("functionRowPanel", "extraFunctionRowPanel"):
            panel = root.findChild(QQuickItem, name)
            keys = _keys(panel)
            assert len(keys) == 12
            assert all(k.property("role") == "fn" for k in keys)

    def test_the_numpad_role_follows_numlock(self, qml_root) -> None:
        # With NumLock off the digits *are* the navigation keys, so a
        # colour saying "digit" over a key that pages up would be a lie.
        root, _ = qml_root
        numpad = _sections(root)["numpad"]
        by_text = {}
        for key in _keys(numpad):
            by_text.setdefault(key.property("displayText"), []).append(key.property("role"))
        assert by_text.get("7") == ["digit"]
        assert by_text.get("Enter") == ["commit"]
        assert by_text.get("Num") == ["toggle"]
        assert by_text.get("/") == ["op"]

        numpad.setProperty("numLockOn", False)
        _settle()
        off = {}
        for key in _keys(numpad):
            off.setdefault(key.property("displayText"), []).append(key.property("role"))
        assert off.get("Home") == ["nav"], "7/Home kept its digit role with NumLock off"
        assert off.get("Del") == ["kill"], "./Del is destructive with NumLock off"

    def test_the_nav_cluster_marks_only_delete_as_destructive(self, qml_root) -> None:
        root, _ = qml_root
        nav = _sections(root)["nav"]
        roles = {k.property("keyText"): k.property("role") for k in _keys(nav)}
        assert roles["delete"] == "kill"
        assert {r for t, r in roles.items() if t != "delete"} == {"nav"}


class TestEveryLegendStaysReadable:
    """Every scheme, on every theme, keeps its legends at 4.5:1 or better.

    This is the test the whole colour engine exists to pass. The wash walks
    its own strength down until the theme's text colour clears the bar, so a
    failure here means either a new scheme skipped the wash or a fill was
    written as a literal.
    """

    def test_every_scheme_on_every_theme(self, qml_root) -> None:
        root, warnings = qml_root
        themes = root.property("themeData").toVariant()
        assert len(themes) == 9, "theme count changed; this sweep should follow it"

        worst = (99.0, "")
        for theme in themes:
            root.setProperty("currentTheme", theme)
            for scheme in COLOURED_SCHEMES:
                root.setProperty("keyColorScheme", scheme)
                _settle(3)
                table = root.property("keyRoles").toVariant()
                for role in ROLES:
                    face = table[role]
                    ratio = _contrast(face["ink"], face["fill"])
                    if ratio < worst[0]:
                        worst = (ratio, f"{scheme}/{theme}/{role}")
                    assert ratio >= 4.49, (
                        f"{scheme} on {theme}: the {role} legend is only "
                        f"{ratio:.2f}:1 on its own keycap"
                    )
        assert worst[0] >= 4.49, worst
        assert _real_warnings(warnings) == []

    def test_the_coloured_roles_stay_tellable_apart(self, qml_root) -> None:
        # A scheme whose bands all collapsed to one colour would satisfy
        # the contrast sweep above perfectly.
        root, _ = qml_root
        themes = root.property("themeData").toVariant()
        root.setProperty("keyColorScheme", "bands")
        for theme in themes:
            root.setProperty("currentTheme", theme)
            _settle(3)
            table = root.property("keyRoles").toVariant()
            fills = {table[role]["fill"].name() for role in ROLES}
            assert len(fills) >= 9, (
                f"Function bands produced only {len(fills)} distinct keycap colours on {theme}"
            )
