"""Headless QML tests for the shared section height and Key Colours.

Two features land here because they are two halves of one change: the board
was ragged both vertically (three sections at three heights) and
semantically (a key's colour said which panel it sat in, never what it
did). Both live entirely in QML, so the Python suite cannot reach either.

The geometry half asserts against *measured* item positions rather than
recomputing the rectangle from the same properties the QML sets, for the
reason `TestNoDeadStripBetweenKeys` gives: an assertion that repeats the
implementation's arithmetic passes whatever that arithmetic does. It also
forces a frame after every toggle (`_relayout`), because a Qt Quick Layout
recomputes in a polish step the offscreen window never runs on its own; the
first version of the no-function-row test measured the board *before* its
own toggle had taken effect, and passed against a 6 px ragged edge.

The colour half's load-bearing test is `TestEveryLegendStaysReadable`. Nine
themes ship, several with a pale accent and one light outright, and the
whole promise of `qml/palette.js` is that no scheme can cost legibility on
any of them. That promise is worth exactly what the test that checks it is
worth, so it sweeps every scheme x every theme x every role rather than
spot-checking the theme the developer happens to run, and it sweeps the
hovered fill as well as the resting one.
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
    # 'QQuickItem*'", and without QQuickWindow the root comes back as a
    # plain QWindow with no grabWindow(), which `_relayout` depends on.
    from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402,F401
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

    The function row matters: with it hidden the panels' natural height is
    the larger one, which is the configuration a fresh install ships with
    and the one the first version of `sectionHeight` got wrong.
    `TestTheSectionsShareOneHeight` exercises both.
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
    """Flush Qt Quick's delegate creation and bindings.

    Anything that re-resolves the layout (a theme change, a scheme change)
    tears delegates down and rebuilds them, and one pass does not reliably
    finish that.
    """
    for _ in range(passes):
        QCoreApplication.processEvents()


def _relayout(root) -> None:
    """Force a layout pass after something inside the grid changes.

    Qt Quick Layouts recompute in a polish step that runs before a frame is
    rendered, and the offscreen window renders no frame on its own: after
    hiding the function row, `mainKeyboard.implicitHeight` sat at its old
    value through 300 event-loop passes and moved only once a frame was
    forced. A test that toggled a row and then measured was measuring the
    state before its own toggle, and passed against a ragged board.
    `grabWindow` renders one frame, which runs the polish.
    """
    _settle()
    root.grabWindow()
    _settle()


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


def _span(item) -> tuple[float, float]:
    _, y, _, h = _rect(item)
    return round(y, 1), round(y + h, 1)


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
        spans = {name: _span(item) for name, item in sections.items()}
        assert len(set(spans.values())) == 1, f"sections are ragged: {spans}"
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

    def test_panel_keys_grow_with_a_function_row_and_give_up_little_without(self, qml_root) -> None:
        # With the function row on the grid is the taller section and the
        # panels' keys grow to fill it: the arrows and the numpad become
        # cheaper targets. With it off the panels' natural height exceeds
        # the grid's by the nav gutter plus rounding, and their keys give
        # up that share, about 2 px. A version that floored the row at the
        # letters' height kept the keys and overhung the grid instead.
        root, _ = qml_root
        sections = _sections(root)
        letter_height = min(k.height() for k in _keys(sections["grid"]))
        for name in ("nav", "numpad"):
            for key in _keys(sections[name]):
                assert key.height() > letter_height + 1

        root.setProperty("showFunctionRow", False)
        _relayout(root)
        letter_height = min(k.height() for k in _keys(sections["grid"]))
        for name in ("nav", "numpad"):
            for key in _keys(sections[name]):
                assert key.height() >= letter_height - 3, (
                    f"a {name} key gave up more than the gutter's share: "
                    f"{key.height()} against letters at {letter_height}"
                )

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
        # The configuration the first version got wrong: the panels'
        # natural height is the larger one here, and a section height
        # taken as the max of the three left the grid centred 6 px inside
        # them. The grid's span has to actually move for this to have
        # tested anything, so that is asserted too.
        root, warnings = qml_root
        sections = _sections(root)
        before = _span(sections["grid"])
        root.setProperty("showFunctionRow", False)
        _relayout(root)
        spans = {name: _span(item) for name, item in sections.items()}
        assert spans["grid"] != before, "the toggle never took; nothing was measured"
        assert len(set(spans.values())) == 1, f"ragged with no function row: {spans}"
        assert _real_warnings(warnings) == []

    def test_the_shipped_defaults_line_up(self, qml_root) -> None:
        # A fresh install shows the nav cluster alone, with no function row
        # and no numpad. Measured at 1400 px before this: grid 134-463
        # against nav 128-469, 6 px ragged at each end.
        root, warnings = qml_root
        root.setProperty("showFunctionRow", False)
        root.setProperty("showNumpad", False)
        _relayout(root)
        sections = _sections(root)
        assert not sections["numpad"].isVisible()
        assert _span(sections["grid"]) == _span(sections["nav"]), (
            f"the shipped default is ragged: grid {_span(sections['grid'])}, "
            f"nav {_span(sections['nav'])}"
        )
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

    def test_an_unknown_scheme_is_off(self, qml_root) -> None:
        # A settings file from a build that knew a scheme this one does not
        # must land on the historical board, not on an empty table that
        # every surface would index into.
        root, _ = qml_root
        root.setProperty("keyColorScheme", "neon")
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
    def test_selecting_a_scheme_builds_a_table(self, qml_root) -> None:
        root, warnings = qml_root
        for scheme in COLOURED_SCHEMES:
            root.setProperty("keyColorScheme", scheme)
            _settle()
            table = root.property("keyRoles")
            assert table is not None, f"{scheme} produced no role table"
            assert set(table.toVariant()) == set(ROLES), f"{scheme} is missing a role"
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
    """The pills were the one surface a scheme did not reach.

    It reaches them through the RING only. Every case below is paired with
    the inverse it has to keep rejecting, because "the fill never changes"
    and "the ring always changes" are each satisfied by a rule that has
    stopped colouring the pills at all.
    """

    def test_off_leaves_the_pills_on_the_theme_key_colour(self, qml_root) -> None:
        root, _ = qml_root
        root.setProperty("keyColorScheme", "off")
        _settle()
        assert root.property("predPillFill") == root.property("themeKeyColor")
        assert root.property("predPillInk") == root.property("themeTextColor")
        assert root.property("predPillBorder") == root.property("themeAccent")

    def test_a_scheme_colours_the_ring_and_never_the_fill(self, qml_root) -> None:
        # Hueing the FILL was tried for a release and reversed on sight:
        # eight pills are the widest block of one colour on the board and
        # they sit above the keys rather than among them, so a wash across
        # all eight reads as a coloured panel rather than as eight things
        # to reach for.
        root, warnings = qml_root
        rings = set()
        for scheme in COLOURED_SCHEMES:
            root.setProperty("keyColorScheme", scheme)
            _settle()
            assert root.property("predPillFill") == root.property("themeKeyColor"), (
                f"{scheme} tinted the pill fill"
            )
            rings.add(root.property("predPillBorder").name())
        assert _real_warnings(warnings) == []
        # The inverse, and the half that bites: pinning every pill to the
        # theme's own two colours would satisfy the sweep above perfectly
        # while quietly putting the pills back outside the schemes' reach.
        assert len(rings) > 1, "no scheme reaches the pill ring"

    def test_a_scheme_with_nothing_to_say_keeps_the_accent_ring(self, qml_root) -> None:
        # The ring is what marks a pill as the thing to reach for, so a
        # scheme that leaves `pill.bar` clear must fall back to the full
        # theme accent rather than to no ring at all.
        root, _ = qml_root
        for scheme in ("mono", "signal"):
            root.setProperty("keyColorScheme", scheme)
            _settle()
            border = root.property("predPillBorder")
            assert border == root.property("themeAccent"), scheme
            assert border.alpha() == 255, scheme

    def test_monochrome_pills_match_the_letters(self, qml_root) -> None:
        # Lifting the fill toward the ink greys the row out against the
        # board. Flat plus the accent ring is what reads as crisp.
        root, _ = qml_root
        root.setProperty("keyColorScheme", "mono")
        _settle()
        assert root.property("predPillFill") == root.property("themeKeyColor")

    def test_the_pill_legend_stays_readable_on_every_theme(self, qml_root) -> None:
        # At rest and under the pointer. The hover lift used to be a plain
        # Qt.lighter on both the fill and the ink, which on a fill the wash
        # had left at exactly 4.5:1 dropped the Ink scheme on Light to
        # 2.9:1 the moment the user reached for the word.
        root, _ = qml_root
        themes = root.property("themeData").toVariant()
        for theme in themes:
            root.setProperty("currentTheme", theme)
            for scheme in COLOURED_SCHEMES:
                root.setProperty("keyColorScheme", scheme)
                _settle(3)
                ink = root.property("predPillInk")
                for state, fill in (
                    ("resting", root.property("predPillFill")),
                    ("hovered", root.property("predPillHoverFill")),
                ):
                    ratio = _contrast(ink, fill)
                    assert ratio >= 4.49, (
                        f"{scheme} on {theme}: the {state} pill label is only {ratio:.2f}:1"
                    )

    def test_the_hover_lift_still_happens_where_there_is_room(self, qml_root) -> None:
        # The guard must not turn into "no hover at all": on the historical
        # board the fill has headroom on every theme, and a pill that does
        # not react to the pointer reads as disabled.
        root, _ = qml_root
        root.setProperty("keyColorScheme", "off")
        themes = root.property("themeData").toVariant()
        lifted = 0
        for theme in themes:
            root.setProperty("currentTheme", theme)
            _settle(3)
            fill = root.property("predPillFill")
            if fill.name() == "#ffffff":
                # Light's keys are pure white, which nothing can lighten;
                # that pill has never had a fill lift, only the ring.
                continue
            assert root.property("predPillHoverFill") != fill, (
                f"the pills lost their hover lift on {theme}"
            )
            lifted += 1
        assert lifted >= 8


class TestKeysAreGivenTheRightJob:
    """Paired with the near-miss each rule has to keep rejecting."""

    def test_the_main_grid_names_each_key_its_job(self, qml_root) -> None:
        root, _ = qml_root
        grid = _sections(root)["grid"]
        expected = {
            "backspace": "kill",
            "return": "commit",
            "tab": "edit",
            "escape": "edit",
            "space": "edit",
            "shift": "mod",
            "ctrl": "mod",
            "caps": "mod",
            "a": "alpha",
            "5": "digit",
            ";": "punct",
        }
        for key_text, role in expected.items():
            matches = [k for k in _keys(grid) if k.property("keyText") == key_text]
            assert matches, f"no {key_text!r} key on the grid"
            for key in matches:
                assert key.property("role") == role, f"{key_text!r} is not {role}"

    def test_a_letter_is_never_treated_as_destructive(self, qml_root) -> None:
        # The inverse of the Backspace case: a rule that returned "kill"
        # for everything would satisfy the test above.
        root, _ = qml_root
        grid = _sections(root)["grid"]
        letters = [
            k
            for k in _keys(grid)
            if len(k.property("keyText") or "") == 1 and (k.property("keyText") or "").isalpha()
        ]
        assert len(letters) > 20
        assert all(k.property("role") == "alpha" for k in letters)

    def test_the_compact_grid_embeds_its_nav_column_as_navigation(self, qml_root) -> None:
        # The compact layouts carry Home / PgUp / PgDn / End / the arrows /
        # Insert in the grid itself, as special keys. They are the same job
        # NavigationPanel names "nav", and the first rule read every special
        # key it did not know as editing, which painted Home the same as
        # Tab on the one layout that carries them this way.
        root, _ = qml_root
        root.setProperty("compactView", True)
        _relayout(root)
        grid = _sections(root)["grid"]
        roles = {}
        for key in _keys(grid):
            roles.setdefault(key.property("keyText"), set()).add(key.property("role"))
        for name in ("home", "end", "pageup", "pagedown"):
            assert roles.get(name) == {"nav"}, f"compact {name} is {roles.get(name)}"
        assert roles.get("delete") == {"kill"}, "Del stays destructive on the compact grid"
        assert roles.get("tab") == {"edit"}

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

    def test_the_hovered_keycap_keeps_its_legend_readable(self, qml_root) -> None:
        # The resting sweep above is where the promise was checked, and
        # the hover lift is where it was broken: a plain Qt.lighter on a
        # fill the wash had left at 4.5:1 put Monochrome's Enter on
        # Blackboard at 3.1:1 under the pointer, which is the key the user
        # is about to press. The bar here is whatever the key cleared at
        # rest, capped at 4.5, so the historical board's own sub-4.5
        # surfaces (if any) keep their lift rather than being held to a
        # promise they never made.
        root, _ = qml_root
        themes = root.property("themeData").toVariant()
        for theme in themes:
            root.setProperty("currentTheme", theme)
            for scheme in SCHEMES:
                root.setProperty("keyColorScheme", scheme)
                _settle(3)
                for key in _keys(root):
                    ink = key.property("_roleInk")
                    resting = _contrast(ink, key.property("_roleFill"))
                    hovered = _contrast(ink, key.property("_hoverFill"))
                    assert hovered >= min(4.49, resting - 0.01), (
                        f"{scheme} on {theme}: {key.property('keyText')!r} drops to "
                        f"{hovered:.2f}:1 under the pointer from {resting:.2f}:1"
                    )

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
