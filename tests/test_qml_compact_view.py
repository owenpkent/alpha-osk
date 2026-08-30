"""Headless QML tests for the compact view.

Everything the compact view does lives in QML — layer filtering, the derived
`totalKeyUnits`, the ?123 layer switch — so the Python suite cannot reach it.
These tests load the real `qml/Main.qml` against the real `KeyboardBridge`
under the `offscreen` platform plugin and inspect the live root object.

That makes them the only guard against the failure mode this feature is most
prone to: a QML binding error, which is a *runtime* warning rather than an
import failure, and would otherwise ship as a keyboard that renders blank.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

# Must be set before QGuiApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# PySide6 imports fine on a bare headless box, but QtGui dlopens the host's
# libEGL / libGL and raises ImportError if they are absent. That happens at
# module scope, which pytest reports as a *collection* error and which aborts
# the whole run rather than failing this one module. Degrade to a skip so a
# contributor without the Qt system libs still gets the rest of the suite.
# CI installs the libs (see .github/workflows/ci.yml) so these still run there.
try:
    from PySide6.QtCore import QCoreApplication, QSettings, Qt, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

    # Imported for the side effect: without QQuickItem somewhere in the
    # module, reading `root.contentItem` raises "Can't find converter for
    # 'QQuickItem*'". Walking the visual tree is the only way to reach a
    # Repeater's delegates (see _rendered_rows).
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

# Qt Quick Controls emits this for every Rectangle-customised control in the
# app (HelpPanel, the settings toggles, ...). It predates this feature and is
# style chatter, not a defect — filtering it keeps the assertion meaningful
# for the things that *would* signal a broken binding.
IGNORED_WARNING_FRAGMENTS = ("does not support customization",)


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)]


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        # A distinct org/app name keeps the QML `Settings` element off the
        # real user's registry section — otherwise running the suite would
        # overwrite their saved layout, theme and window width.
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation — these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def qml_root(qapp):
    """Load Main.qml with a mocked-synth bridge, from clean settings."""
    warnings: list[str] = []

    # Main.qml's `Settings` element persists on change, so without this a
    # test that enables compact view leaks into every later test *and* into
    # subsequent runs. Clear the test-scoped store before each load.
    QSettings(TEST_ORG, TEST_APP).clear()

    # Disarm the startup update check.  `savedAutoCheckUpdates` defaults to
    # true, so three seconds after load Main.qml fires a real HTTPS request
    # to the GitHub releases API from a daemon thread, in every headless QML
    # test that lives that long.  Besides making the suite depend on the
    # network, that thread emits its result back into a bridge the fixture
    # has already torn down, which surfaces as `RuntimeError: Signal source
    # has been deleted` or, in a longer run, a hard access violation that
    # kills the whole pytest process.  Writing the setting before the load
    # means `Component.onCompleted` never starts the timer at all.
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    engine.rootContext().setContextProperty("keyboard", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    try:
        yield root, warnings, bridge
    finally:
        del engine


@pytest.fixture
def qml_root_factory(qapp):
    """Like `qml_root`, but lets each test seed QSettings before the engine
    loads Main.qml.

    Needed for anything read inside `Component.onCompleted` -- the saved
    window position restore, here -- since `qml_root` only hands back the
    root object *after* load, by which point the restore has already run
    against whatever `savedWindowX`/`savedWindowY` were already on disk.
    """
    engines: list[QQmlApplicationEngine] = []

    def _load(pre_settings: dict | None = None):
        warnings: list[str] = []
        QSettings(TEST_ORG, TEST_APP).clear()

        settings = QSettings(TEST_ORG, TEST_APP)
        settings.setValue("ui/savedAutoCheckUpdates", False)
        for key, value in (pre_settings or {}).items():
            settings.setValue(key, value)
        settings.sync()

        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.is_available.return_value = True
            synth.backend_name.return_value = "MockSynth"
            factory.return_value = synth
            bridge = KeyboardBridge()

        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
        engine.rootContext().setContextProperty("keyboard", bridge)
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
        engines.append(engine)
        return engine.rootObjects()[0], warnings, bridge

    yield _load
    for engine in engines:
        del engine


# One processEvents() pass does not reliably flush Qt Quick's delegate
# creation for a Repeater, and anything that re-resolves the layout (toggling
# compactView, switching layer) tears the rows down and rebuilds them. A
# caller that measured immediately after a single pass would intermittently
# see zero rows. Pump until the delegates exist, bounded so it cannot hang.
# Same reasoning, and the same "cannot mask a regression" argument, as
# _show() in test_qml_prediction_bar.py: a real breakage exhausts the budget
# and still fails the caller's non-empty assertion.
#
# Two details are load-bearing, and the second one segfaulted Linux CI in the
# prediction-bar version of this loop:
#
#  * The predicate must return a COUNT (or a bool), never the items. Those
#    are PySide wrappers for QML-owned QQuickItems, and a Repeater frees its
#    delegates on every model change, so a wrapper retained across a wait can
#    point at freed memory. Holding one killed the whole CI run with SIGSEGV.
_LAYOUT_PUMP_PASSES = 10


def _pump_until(count_fn) -> None:
    """Wait until *count_fn* reports a non-zero count, bounded.

    `count_fn` must not return QQuickItems. See the note above.
    """
    for _ in range(_LAYOUT_PUMP_PASSES):
        QCoreApplication.processEvents()
        if count_fn():
            return


def _theme_names(root) -> list[str]:
    """Theme ids from the live `themeData` map (a QML `var`, so unwrap it)."""
    value = root.property("themeData")
    data = value.toVariant() if hasattr(value, "toVariant") else value
    return list(data.keys())


def _row_units(row: dict) -> float:
    return sum(float(k.get("width", 1.0)) for k in row["keys"])


def _rows(root) -> list[dict]:
    """Read a QML `property var` list as plain Python.

    PySide hands back a QJSValue for `var` properties; toVariant() unwraps it.
    """
    value = root.property("visibleRows")
    return value.toVariant() if hasattr(value, "toVariant") else value


class TestMainQmlLoads:
    def test_loads_without_qml_errors(self, qml_root) -> None:
        _, warnings, _ = qml_root
        # Binding loops, ReferenceErrors and unresolved properties surface
        # here as runtime warnings, not as an import failure.
        assert _real_warnings(warnings) == []

    def test_defaults_to_full_size_layout(self, qml_root) -> None:
        root, _, _ = qml_root
        assert root.property("compactView") is False
        assert root.property("activeLayer") == "base"
        # 15.5u number row + nav/numpad if those panels are on by default.
        rows = _rows(root)
        assert max(_row_units(r) for r in rows) == pytest.approx(15.5)


class TestCompactViewSwitching:
    def test_enabling_compact_swaps_the_layout_and_resizes_units(self, qml_root) -> None:
        root, warnings, _ = qml_root
        root.setProperty("compactView", True)
        QCoreApplication.processEvents()

        rows = _rows(root)
        assert len(rows) == 4, "compact base layer is four rows"
        for row in rows:
            assert _row_units(row) == pytest.approx(13.0)
        assert _real_warnings(warnings) == []

    def test_widest_row_drives_total_key_units(self, qml_root) -> None:
        root, _, _ = qml_root
        # Panels off on both sides of the comparison. Compact forces them
        # off anyway (TestCompactViewForbidsTheSidePanels), so leaving the
        # default Navigation panel on would fold its 3.0u into the delta and
        # stop this measuring the main block at all.
        root.setProperty("showNavigation", False)
        root.setProperty("showNumpad", False)
        QCoreApplication.processEvents()
        base_units = root.property("totalKeyUnits")

        root.setProperty("compactView", True)
        QCoreApplication.processEvents()
        compact_units = root.property("totalKeyUnits")

        # Nothing else contributes now, so the difference is exactly the
        # 15.5u -> 13.0u main-block change.
        assert base_units - compact_units == pytest.approx(2.5)

    def test_layer_switch_swaps_visible_rows(self, qml_root) -> None:
        root, warnings, _ = qml_root
        root.setProperty("compactView", True)
        QCoreApplication.processEvents()

        base_ids = [r["id"] for r in _rows(root)]
        assert all(i.startswith("base-") for i in base_ids)

        root.setProperty("activeLayer", "sym")
        QCoreApplication.processEvents()
        sym_ids = [r["id"] for r in _rows(root)]
        assert all(i.startswith("sym-") for i in sym_ids)
        assert len(sym_ids) == 4
        assert _real_warnings(warnings) == []

    def test_sym_layer_rows_are_also_13_units(self, qml_root) -> None:
        root, _, _ = qml_root
        root.setProperty("compactView", True)
        root.setProperty("activeLayer", "sym")
        QCoreApplication.processEvents()
        for row in _rows(root):
            assert _row_units(row) == pytest.approx(13.0)

    def test_returning_to_full_size_restores_the_base_layer(self, qml_root) -> None:
        root, _, _ = qml_root
        root.setProperty("compactView", True)
        root.setProperty("activeLayer", "sym")
        QCoreApplication.processEvents()

        root.setProperty("compactView", False)
        QCoreApplication.processEvents()

        # Leaving the user stranded on a "sym" layer that the full-size
        # layout never defines would render an empty keyboard.
        assert root.property("activeLayer") == "base"
        rows = _rows(root)
        assert rows, "full-size layout rendered no rows"
        assert max(_row_units(r) for r in rows) == pytest.approx(15.5)


class TestNoGuttersInCompactView:
    def test_every_visible_row_matches_the_widest(self, qml_root) -> None:
        """The whole premise: equal rows mean nothing gets centred."""
        root, _, _ = qml_root
        root.setProperty("compactView", True)
        QCoreApplication.processEvents()

        for layer in ("base", "sym"):
            root.setProperty("activeLayer", layer)
            QCoreApplication.processEvents()
            units = [_row_units(r) for r in _rows(root)]
            assert min(units) == max(units), (
                f"{layer} layer has unequal rows {units} — the narrower ones "
                "get centred and the side gutters return"
            )


class TestNumberRowPanel:
    """The optional `Esc 1-0 - =` strip (qml/components/NumberRow.qml).

    Off by default, and the only surface that puts Esc back on the base
    layer after the Del/Esc trade documented in COMPACT_VIEW.md.
    """

    @pytest.fixture
    def number_row_defs(self, qapp) -> list[dict]:
        """`keyDefs` read off a standalone NumberRow instance."""
        from PySide6.QtQml import QQmlComponent, QQmlEngine

        engine = QQmlEngine()
        component = QQmlComponent(
            engine, QUrl.fromLocalFile(str(REPO_ROOT / "qml" / "components" / "NumberRow.qml"))
        )
        assert component.errors() == [], [e.toString() for e in component.errors()]
        item = component.create()
        assert item is not None
        value = item.property("keyDefs")
        defs = value.toVariant() if hasattr(value, "toVariant") else value
        try:
            yield defs
        finally:
            del item
            del engine

    def test_is_still_exactly_thirteen_units(self, number_row_defs) -> None:
        """13 x 1u is what makes it sit flush over the compact grid."""
        assert len(number_row_defs) == 13

    def test_leading_key_is_escape(self, number_row_defs) -> None:
        """Esc, not the physical keyboard's backtick.

        Compact traded Esc onto the ?123 layer to make room for Del, which
        put "get me out of this dialog" behind a hop. This row restores it
        at the top-left corner where a real keyboard keeps it.
        """
        assert number_row_defs[0].get("special") == "escape"
        assert number_row_defs[0].get("display") == "Esc"

    def test_backtick_is_gone_from_the_panel(self, number_row_defs) -> None:
        """Nothing in the row types ` or ~ any more — Esc took the slot."""
        typed = {d.get("key") for d in number_row_defs}
        typed |= {d.get("shifted") for d in number_row_defs}
        assert "`" not in typed
        assert "~" not in typed

    def test_backtick_survives_on_the_sym_layer(self) -> None:
        """The slot was a trade, not a deletion (mirrors the Esc guard in
        tests/test_layouts.py::TestCompactLayout)."""
        import json

        compact = json.loads(
            (REPO_ROOT / "data" / "layouts" / "qwerty-compact.json").read_text(encoding="utf-8")
        )
        sym_chars = [
            k
            for r in compact["rows"]
            if r["layer"] == "sym"
            for k in r["keys"]
            if k.get("type") == "char"
        ]
        assert "`" in {k["key"] for k in sym_chars}
        assert "~" in {k.get("shifted") for k in sym_chars}

    def test_digits_still_cover_the_full_span(self, number_row_defs) -> None:
        keys = [d.get("key") for d in number_row_defs if d.get("key")]
        assert keys == list("1234567890-=")

    def test_escape_stays_out_of_the_swipe_registry(self, qml_root) -> None:
        """A phantom "Esc" key centre would corrupt every swipe shape match.

        The panel's 12 char keys must register; its Esc must not. Asserted
        in compact view, where the base layer carries no digits and no
        `-`/`=` (those live on ?123), so every one of the 12 can only have
        come from this panel.

        The cost of staying out is that Esc is a dead tap while swipe
        typing is on, which is how every other special key already behaves
        under the overlay.
        """
        root, warnings, _ = qml_root
        # Compact brings the panel with it: showNumberRow is derived from
        # whether the active layout carries a `number` row of its own.
        root.setProperty("compactView", True)
        QCoreApplication.processEvents()

        entries = root.property("charKeyRegistry").toVariant()
        keys = [e["kd"]["key"] for e in entries]

        assert set("1234567890-=") <= set(keys)
        # registerCharKey only admits single-character char keys, so an Esc
        # that slipped through would show up as a multi-char entry.
        assert all(len(k) == 1 for k in keys), (
            f"non-character key leaked into the swipe registry: {[k for k in keys if len(k) != 1]}"
        )
        assert "Esc" not in keys
        assert _real_warnings(warnings) == []


class TestEveryRowFitsTheContentArea:
    """No keyboard row may render wider than the space the sizer reserved.

    `keyW` is solved from `(width - layoutFixedPixels) / totalKeyUnits`, so
    the two halves of `_widestRow` have to describe the *same* worst case: a
    row costs `units * keyW + gaps * keySpacing`, and the row with the most
    units is not necessarily the row with the most gaps.  In every compact
    layout they are provably different rows, because the format pins every
    row to the same 13.0u while the letter row carries an extra key.  Taking
    `gaps` from whichever row won the units comparison under-reserved one
    keySpacing and pushed the widest row past the margin.

    Swept rather than spot-checked: the overflow is a fixed few pixels, so it
    hides completely at any width where the rows happen to sit well inside
    the frame, and only the widest row in the densest configuration shows it.

    The exact-arithmetic version of this property is
    `test_fixed_pixels_reserves_gaps_for_the_row_with_the_most_keys` below;
    prefer that one when diagnosing, since it carries no float slop.
    """

    # Both view modes, both compact layers, panels off (panels take units
    # away from the main block, which masks the deficit).
    WIDTHS = (940, 1000, 1100, 1240)

    # `Row` reports an implicit width one pixel above the arithmetic sum of
    # its children: `units * keyW` lands a few ulps above the integer it
    # should be (keyW is a division that rarely terminates in binary) and the
    # positioner ceils.  It is a constant 1 px in *both* view modes and at
    # every width below, including on the full-size layouts this feature never
    # touched, so it is Qt rounding rather than anything the sizer controls.
    # Allowed for, not asserted against: the regression this test exists for
    # is keySpacing-sized (2-3 px), so 1 px of slack still catches it.
    POSITIONER_SLOP_PX = 1

    @staticmethod
    def _rendered_rows(root) -> list:
        """Every laid-out keyboard row, via the VISUAL tree.

        `findChildren` cannot see a Repeater's delegates: they are re-parented
        as visual children, so their QObject parent is the delegate model.
        The rows are the items carrying a `rowData` property.
        """
        found: list = []

        def walk(item) -> None:
            for child in item.childItems():
                if child.property("rowData") is not None:
                    found.append(child)
                walk(child)

        walk(root.property("contentItem"))
        return found

    @classmethod
    def _expect_rows(cls, root, label: str) -> list:
        """Rendered rows, refusing to return an empty list.

        Fail-closed counterpart to `_rendered_rows`: every caller below
        iterates the result, and iterating nothing passes every assertion in
        the loop. See the same pattern, and the bug it hid, in
        `expect_pills` in test_qml_prediction_bar.py.
        """
        rows = cls._rendered_rows(root)
        assert rows, (
            f"no keyboard rows found in the visual tree ({label}). Either the "
            "keyboard rendered nothing or the lookup broke; both make the "
            "assertions that follow vacuous."
        )
        return rows

    def _assert_rows_fit(self, root, warnings, label: str) -> None:
        for width in self.WIDTHS:
            root.setProperty("width", width)
            # len(), not the list: never retain QML-owned items across a wait.
            _pump_until(lambda: len(self._rendered_rows(root)))

            rows = self._expect_rows(root, f"{label} at width {width}")

            # 8 px margin on each side is the content area the rows are
            # centred in; layoutFixedPixels reserves exactly that 16 px.
            available = width - 16
            for row in rows:
                keys = len(row.property("rowData")["keys"])
                assert row.width() <= available + self.POSITIONER_SLOP_PX, (
                    f"{label}: a {keys}-key row rendered "
                    f"{row.width() - available:.0f} px past the content area "
                    f"at window width {width} "
                    f"(row {row.width():.0f} px vs {available} px available)"
                )
        assert _real_warnings(warnings) == []

    def test_full_size_rows_fit(self, qml_root) -> None:
        root, warnings, _ = qml_root
        root.setProperty("showNavigation", False)
        root.setProperty("showNumpad", False)
        QCoreApplication.processEvents()
        self._assert_rows_fit(root, warnings, "full-size")

    def test_compact_base_layer_rows_fit(self, qml_root) -> None:
        root, warnings, _ = qml_root
        root.setProperty("showNavigation", False)
        root.setProperty("showNumpad", False)
        root.setProperty("compactView", True)
        QCoreApplication.processEvents()
        assert root.property("activeLayer") == "base"
        self._assert_rows_fit(root, warnings, "compact base layer")

    def test_compact_sym_layer_rows_fit(self, qml_root) -> None:
        root, warnings, _ = qml_root
        root.setProperty("showNavigation", False)
        root.setProperty("showNumpad", False)
        root.setProperty("compactView", True)
        root.setProperty("activeLayer", "sym")
        QCoreApplication.processEvents()
        self._assert_rows_fit(root, warnings, "compact ?123 layer")

    def test_switching_layer_does_not_resize_the_keys(self, qml_root) -> None:
        """?123 must not change key width.

        Both halves of `_widestRow` are maxed across the *visible* rows, so a
        layer whose widest row differs in units or in key count would re-solve
        keyW and make every key jump on the hop.  The layout format already
        pins units per row; this pins the pixel consequence, which is the part
        a user would actually see.
        """
        root, warnings, _ = qml_root
        root.setProperty("compactView", True)
        QCoreApplication.processEvents()
        base_key_w = root.property("keyW")
        base_fixed = root.property("layoutFixedPixels")

        root.setProperty("activeLayer", "sym")
        QCoreApplication.processEvents()

        assert root.property("keyW") == pytest.approx(base_key_w)
        assert root.property("layoutFixedPixels") == pytest.approx(base_fixed)
        assert _real_warnings(warnings) == []

    @pytest.mark.parametrize(
        ("compact", "layer"),
        [(False, "base"), (True, "base"), (True, "sym")],
    )
    def test_fixed_pixels_reserves_gaps_for_the_row_with_the_most_keys(
        self, qml_root, compact: bool, layer: str
    ) -> None:
        """The same property as the sweep above, in exact integers.

        `layoutFixedPixels` must reserve a gap for every inter-key space in
        the row that has the most keys, which, when several rows tie on
        units, is not the row that won the units comparison.  Stated on the
        reserved pixels rather than on rendered widths so it carries no
        positioner rounding: this is the assertion that actually fails the
        moment `_widestRow` reads `gaps` off the wrong row.
        """
        root, warnings, _ = qml_root
        root.setProperty("showNavigation", False)
        root.setProperty("showNumpad", False)
        root.setProperty("compactView", compact)
        root.setProperty("activeLayer", layer)
        QCoreApplication.processEvents()

        rows = _rows(root)
        assert rows, "no visible rows to measure"
        most_keys = max(len(r["keys"]) for r in rows)
        widest_units = max(_row_units(r) for r in rows)

        # Sanity: the tie this guards against must actually be present in the
        # compact layouts, or the test would pass for the wrong reason.
        if compact:
            tied = [r for r in rows if _row_units(r) == pytest.approx(widest_units)]
            assert len(tied) > 1, "compact rows are meant to tie on units"
            assert len({len(r["keys"]) for r in tied}) > 1, (
                "tied rows all have the same key count, so this layout cannot "
                "exercise the units-vs-gaps split"
            )

        expected = 16 + (most_keys - 1) * root.property("keySpacing")
        assert root.property("layoutFixedPixels") == pytest.approx(expected), (
            f"reserved {root.property('layoutFixedPixels')} px for gaps but the "
            f"widest row has {most_keys} keys ({most_keys - 1} gaps)"
        )
        assert root.property("totalKeyUnits") == pytest.approx(widest_units)
        assert _real_warnings(warnings) == []


class TestSecondSymbolPage:
    r"""?123 -> =\< -> ?123, and what happens to a held Shift on the way.

    Reported: on ?123, holding Shift re-rendered row 1 as ! @ # $ % ^ & * ( )
    while row 3 already showed ! @ # $ % : & ( ). Shift on the symbol pages is
    now a switch to a second page instead, which is the phone convention and
    makes the overlap impossible rather than merely absent. The layout half of
    that is asserted in tests/test_layouts.py; this is the QML half.
    """

    @staticmethod
    def _key_items(root) -> list:
        out: list = []

        def walk(item) -> None:
            for child in item.childItems():
                if child.property("kd") is not None:
                    out.append(child)
                walk(child)

        walk(root.property("contentItem"))
        return out

    @classmethod
    def _layer_key(cls, root, target: str):
        hits = [
            i
            for i in cls._key_items(root)
            if i.isVisible() and (i.property("kd") or {}).get("target") == target
        ]
        assert hits, f"no visible layer key targeting {target!r}"
        return hits[0]

    @classmethod
    def _tap(cls, root, target: str) -> None:
        """Tap the layer key for *target*, holding on to no QML-owned item.

        Resolving the item to a bare point and dropping the reference before
        any pumping is deliberate. Tapping a layer key changes `activeLayer`,
        which makes the Repeater tear down and rebuild every row, so the
        delegate we just found is freed during the very `processEvents` call
        below. A PySide wrapper that outlives its item is the hazard that
        segfaulted CI once already; see the rule in GOTCHAS.md.
        """
        key = cls._layer_key(root, target)
        point = key.mapToScene(key.boundingRect().center()).toPoint()
        del key
        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()
        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

    @pytest.fixture
    def compact_shown(self, qml_root):
        root, warnings, bridge = qml_root
        root.setProperty("compactView", True)
        root.show()
        _pump_until(lambda: len(self._key_items(root)))
        assert root.property("activeLayer") == "base"
        return root, warnings, bridge

    def test_the_pages_chain_and_come_back(self, compact_shown) -> None:
        root, warnings, _ = compact_shown

        self._tap(root, "sym")
        assert root.property("activeLayer") == "sym"
        assert len(_rows(root)) == 4

        self._tap(root, "sym2")
        assert root.property("activeLayer") == "sym2"
        assert len(_rows(root)) == 4

        # Back to ?123, then out to letters. A page you cannot leave is worse
        # than a page that does not exist.
        self._tap(root, "sym")
        assert root.property("activeLayer") == "sym"
        self._tap(root, "base")
        assert root.property("activeLayer") == "base"
        assert _real_warnings(warnings) == []

    def test_second_page_keys_are_the_same_size(self, compact_shown) -> None:
        """All three pages are 13.0u with matching key counts, so hopping
        between them must not resize anything under the pointer."""
        root, _, _ = compact_shown
        base_w, base_fixed = root.property("keyW"), root.property("layoutFixedPixels")

        for target in ("sym", "sym2"):
            self._tap(root, target)
            assert root.property("keyW") == pytest.approx(base_w), f"keys resized on {target}"
            assert root.property("layoutFixedPixels") == pytest.approx(base_fixed)

    def test_switching_layer_drops_a_held_shift(self, compact_shown) -> None:
        """The symbol pages carry no Shift key, so one carried in from the
        letters page could never be cleared from there.

        It would not merely be stuck. The modifier is held at the OS level, so
        tapping "1" would emit "!" while the keycap still read "1": the output
        and the display would disagree, which is worse than either alone.
        """
        root, warnings, bridge = compact_shown
        bridge.toggleShift()
        QCoreApplication.processEvents()
        assert root.property("shiftOn") is True, "precondition: Shift is held"

        self._tap(root, "sym")

        assert root.property("activeLayer") == "sym"
        assert root.property("shiftOn") is False, (
            "Shift survived the hop to a page that has no Shift key to clear it"
        )
        assert _real_warnings(warnings) == []

    def test_no_shift_key_exists_on_either_symbol_page(self, compact_shown) -> None:
        """Belt and braces against the QML rendering one anyway."""
        root, _, _ = compact_shown
        for target in ("sym", "sym2"):
            self._tap(root, target)
            visible = [i for i in self._key_items(root) if i.isVisible()]
            assert visible, "no keys rendered"
            actions = {(i.property("kd") or {}).get("action") for i in visible}
            assert "shift" not in actions, f"{target} still renders a Shift key"


class TestHoldingALetterRepeatsOnlyWhenAskedFor:
    """Requested: holding a letter did nothing but type it once.

    That was deliberate, not broken. A mouse-driven key is held by *not
    letting go* of the button, and a slow release is ordinary on a
    keyboard built for slow motor input, so a repeating letter turns one
    intended character into several. Backspace and the arrows have always
    repeated because there the worst case is an extra deletion the user
    can see happen; an extra "a" mid-word is a typo the prediction engine
    then learns from.

    So it is a setting rather than a flip, and these pin both directions
    plus the keys that must not follow it either way.
    """

    @staticmethod
    def _repeat_flags(root) -> dict:
        out: dict = {}
        for item in TestSecondSymbolPage._key_items(root):
            kd = item.property("kd")
            if hasattr(kd, "toVariant"):
                kd = kd.toVariant()
            if not isinstance(kd, dict):
                continue
            name = kd.get("key") if kd.get("type") == "char" else kd.get("action")
            if name:
                out.setdefault(str(name), item.property("enableRepeat"))
        return out

    def test_on_by_default_a_letter_repeats(self, qml_root) -> None:
        """The default moved to on at the user's request, having met the
        absence of it as a bug. A setting you have to go and find is not a
        neutral default; it is the feature being off for everyone who does
        not know it exists."""
        root, warnings, _ = qml_root
        _pump_until(lambda: len(self._repeat_flags(root)) > 0)

        flags = self._repeat_flags(root)
        assert flags.get("a") is True, "a letter did not arm the repeat timer"
        assert _real_warnings(warnings) == []

    def test_turning_it_off_disarms_the_letters(self, qml_root) -> None:
        """Off is still reachable, for anyone the original argument does
        describe: a grip that cannot release inside the warm-up grace."""
        root, _, _ = qml_root
        root.setProperty("characterRepeat", False)
        _pump_until(lambda: len(self._repeat_flags(root)) > 0)

        assert self._repeat_flags(root).get("a") is False

    @pytest.mark.parametrize("setting", [False, True])
    def test_backspace_repeats_either_way(self, qml_root, setting: bool) -> None:
        """The inverse half. This setting is about *letters*, and must not
        become the switch that governs the keys that always repeated."""
        root, _, _ = qml_root
        root.setProperty("characterRepeat", setting)
        _pump_until(lambda: len(self._repeat_flags(root)) > 0)

        assert self._repeat_flags(root).get("backspace") is True

    @pytest.mark.parametrize("setting", [False, True])
    def test_tab_never_repeats(self, qml_root, setting: bool) -> None:
        """Tab is not in the repeatable set and is not a character, so it
        is unaffected from both sides."""
        root, _, _ = qml_root
        root.setProperty("characterRepeat", setting)
        _pump_until(lambda: len(self._repeat_flags(root)) > 0)

        assert self._repeat_flags(root).get("tab") is False


class TestPanelsSitFlushWithTheGrid:
    """The Number Row and Function Row panels must consume the window exactly
    the way a keyboard row does.

    Reported with a screenshot: in compact view the number row overhung the
    window and its last key was clipped. Cause was `RowLayout`, which rounds
    every child up to a whole pixel: 13 keys of 69.23 px became 13 of 70, so
    the row rendered 10 px wider than the grid it is meant to sit flush with.
    The keyboard rows use a plain `Row` positioner, which keeps the float.

    Note the earlier row-fit sweep could not have caught this: it finds rows
    by their `rowData` property, and these panels are separate components.
    """

    WIDTHS = (940, 1005, 1100, 1240)
    # Same 1 px `Row` float-ceil slop the keyboard rows carry; see
    # TestEveryRowFitsTheContentArea.POSITIONER_SLOP_PX.
    SLOP_PX = 1

    @staticmethod
    def _panel(root, name: str):
        """The panel item, by objectName.

        These are ordinary children of the layout column rather than Repeater
        delegates, so findChild reaches them (unlike the keyboard rows, which
        it cannot see at all).
        """
        panel = root.findChild(QQuickItem, name)
        assert panel is not None, f"no panel named {name!r}"
        return panel

    @staticmethod
    def _widest_layout_row(root) -> float:
        rows = TestEveryRowFitsTheContentArea._rendered_rows(root)
        assert rows, "no keyboard rows rendered"
        return max(r.width() for r in rows)

    def test_number_row_matches_the_widest_keyboard_row(self, qml_root) -> None:
        root, warnings, _ = qml_root
        root.setProperty("showNavigation", False)
        root.setProperty("showNumpad", False)
        # Compact is the configuration that shows this panel at all: the
        # full-size layouts carry their own number row, so showNumberRow
        # derives to False there.
        root.setProperty("compactView", True)
        _pump_until(lambda: self._panel(root, "numberRowPanel").width() > 0)

        for width in self.WIDTHS:
            root.setProperty("width", width)
            _pump_until(lambda: self._panel(root, "numberRowPanel").width() > 0)
            panel = self._panel(root, "numberRowPanel")
            assert panel.width() > 0, f"number row not rendered at {width}"
            grid = self._widest_layout_row(root)

            assert panel.width() == pytest.approx(grid, abs=1.0), (
                f"number row is {panel.width() - grid:+.0f} px off the keyboard "
                f"grid at window width {width} ({panel.width():.0f} vs {grid:.0f}). "
                "Both are 13 units; they must render identically or the panel "
                "will not line up with the keys under it."
            )
            assert panel.width() <= width - 16 + self.SLOP_PX, (
                f"number row overhangs the content area by "
                f"{panel.width() - (width - 16):.0f} px at window width {width}"
            )
        assert _real_warnings(warnings) == []

    def test_function_row_matches_the_widest_keyboard_row(self, qml_root) -> None:
        """Real geometry, not the identity assertion this replaced.

        `keyWidth: fnRow.keyW` binds straight through to `root.keyW`, and a
        plain `Row` never resizes a child, so `key.width() == root.keyW` by
        construction: reading that value from both sides and comparing them
        cannot fail. This measures something that can: the row's rendered
        total width against the width its own documented geometry implies,
        built from the SAME `keyW`/`keySpacing` the keyboard grid below it
        uses.

        Unlike the Number Row, this panel is deliberately narrower than the
        grid (12 keys against a 13/15.5-unit grid; see the design note in
        FunctionRow.qml), so plain equality with the widest row is the wrong
        assertion here - that is the property the three rejected redesigns
        each tried to satisfy, and photographing them side by side is why
        they were reverted. What has to hold instead is the row's own
        formula: 12 keys, 9 ordinary gaps inside the three 4-key groups, and
        two group gaps (the row's own spacing on both sides of a
        keySpacing*2 spacer) worth 4*keySpacing each. A wrong key count, a
        resized spacer, or a changed group gap all move the rendered width
        off that formula, and the row must also stay strictly narrower than
        the grid, since drifting up to (or past) it is exactly the shape the
        rejected redesigns had.
        """
        root, warnings, _ = qml_root
        root.setProperty("showNavigation", False)
        root.setProperty("showNumpad", False)
        root.setProperty("showFunctionRow", True)

        for compact in (True, False):
            root.setProperty("compactView", compact)
            _pump_until(lambda: self._panel(root, "functionRowPanel").width() > 0)

            for width in self.WIDTHS:
                root.setProperty("width", width)
                _pump_until(lambda: self._panel(root, "functionRowPanel").width() > 0)
                panel = self._panel(root, "functionRowPanel")
                grid = self._widest_layout_row(root)
                key_w = root.property("keyW")
                key_spacing = root.property("keySpacing")
                expected = 12 * key_w + 9 * key_spacing + 2 * (4 * key_spacing)

                assert panel.width() == pytest.approx(expected, abs=1.0), (
                    f"function row is {panel.width() - expected:+.1f} px off "
                    f"its own geometry at window width {width} "
                    f"(compact={compact}): {panel.width():.1f} vs "
                    f"{expected:.1f} expected from 12 keys + 9 internal gaps + "
                    "2 group gaps. Catches a wrong key count, a resized "
                    "spacer or a changed group gap, none of which the "
                    "identity assertion this replaced could see."
                )
                assert panel.width() < grid, (
                    f"function row ({panel.width():.1f}) is not narrower "
                    f"than the widest keyboard row ({grid:.1f}) at window "
                    f"width {width} (compact={compact}); it was meant to "
                    "stay inset, not fill the grid."
                )
        assert _real_warnings(warnings) == []

    def test_the_function_row_never_overhangs_the_window(self, qml_root) -> None:
        """Weak on its own, which is why it is not the only F-row test:
        a row that is too narrow passes it trivially. It still catches the
        one direction that clips keys off the edge."""
        root, warnings, _ = qml_root
        root.setProperty("showNavigation", False)
        root.setProperty("showNumpad", False)
        root.setProperty("compactView", True)
        root.setProperty("showFunctionRow", True)
        _pump_until(lambda: self._panel(root, "functionRowPanel").width() > 0)

        for width in self.WIDTHS:
            root.setProperty("width", width)
            _pump_until(lambda: self._panel(root, "functionRowPanel").width() > 0)
            panel = self._panel(root, "functionRowPanel")
            assert panel.width() <= width - 16 + self.SLOP_PX, (
                f"function row overhangs by {panel.width() - (width - 16):.0f} px "
                f"at window width {width}"
            )
        assert _real_warnings(warnings) == []

    def test_side_panels_do_not_push_the_window_over(self, qml_root) -> None:
        """The Navigation and Numpad panels are subject to the same trap.

        Both were left on `GridLayout` when the rounding rule was written, and
        every case in the two tests above sets showNavigation/showNumpad to
        False, so nothing measured them. Main.qml reserves an exact float unit
        budget for each panel when it derives minimumWidth, so a panel that
        rounds its columns up costs pixels the window was never given.
        """
        root, warnings, _ = qml_root
        # Full-size view: compact forbids both panels (see
        # TestCompactViewForbidsTheSidePanels), and the column-budget
        # arithmetic under test is the same in either view.
        root.setProperty("compactView", False)
        root.setProperty("showNavigation", True)
        root.setProperty("showNumpad", True)
        _pump_until(lambda: self._panel(root, "navigationPanel").width() > 0)

        for width in self.WIDTHS:
            root.setProperty("width", width)
            _pump_until(lambda: self._panel(root, "navigationPanel").width() > 0)
            for name, columns in (("navigationPanel", 3), ("numpadPanel", 4)):
                panel = self._panel(root, name)
                assert panel.width() > 0, f"{name} not rendered at {width}"
                keyw = root.property("keyW")
                spacing = root.property("keySpacing")
                expected = columns * keyw + (columns - 1) * spacing
                assert panel.width() == pytest.approx(expected, abs=self.SLOP_PX), (
                    f"{name} is {panel.width() - expected:+.1f} px off its reserved "
                    f"{columns}-column budget at window width {width} "
                    f"({panel.width():.1f} vs {expected:.1f}). A whole-pixel-rounding "
                    "positioner would show up here."
                )
        assert _real_warnings(warnings) == []


class TestCompactViewForbidsTheSidePanels:
    """Compact view and the Navigation / Numpad panels are exclusive.

    The two panels cost roughly 470 px of window width, which is precisely
    what compact exists to hand back, so allowing all three at once let the
    user pick a mode and then silently undo it. Compact wins and forces both
    off, with the Settings toggles disabled while it is on.

    The restore half is the part that is easy to break: the forced-off state
    must not be written to `savedShowNavigation` / `savedShowNumpad`, or
    turning compact on once would discard the user's real preference and
    leaving compact could never bring the panels back.
    """

    @staticmethod
    def _set(root, name: str, value) -> None:
        root.setProperty(name, value)
        QCoreApplication.processEvents()

    def test_turning_compact_on_drops_both_panels(self, qml_root) -> None:
        root, warnings, _ = qml_root
        self._set(root, "showNavigation", True)
        self._set(root, "showNumpad", True)

        self._set(root, "compactView", True)

        assert root.property("showNavigation") is False
        assert root.property("showNumpad") is False
        assert _real_warnings(warnings) == []

    def test_leaving_compact_restores_what_the_user_had(self, qml_root) -> None:
        root, warnings, _ = qml_root
        self._set(root, "showNavigation", True)
        self._set(root, "showNumpad", False)

        self._set(root, "compactView", True)
        self._set(root, "compactView", False)

        assert root.property("showNavigation") is True, (
            "compact view discarded the user's Navigation preference instead of suspending it"
        )
        assert root.property("showNumpad") is False
        assert _real_warnings(warnings) == []

    def test_the_panels_stay_off_across_a_compact_round_trip(self, qml_root) -> None:
        """Both off before compact must mean both off after it."""
        root, _, _ = qml_root
        self._set(root, "showNavigation", False)
        self._set(root, "showNumpad", False)

        self._set(root, "compactView", True)
        self._set(root, "compactView", False)

        assert root.property("showNavigation") is False
        assert root.property("showNumpad") is False


class TestAccentKeysStayReadable:
    """The accent-filled editing keys must keep a legible label on every theme.

    The style exists so Esc / Tab / Shift / Backspace / Del are findable by
    colour on the compact grid, which has no size cues. A flat 35% accent wash
    inverted that: it dropped the label below WCAG AA on five of the nine
    themes (Blackboard 6.19 -> 2.66, Vaporwave 6.17 -> 2.97, Forest 7.53 ->
    3.33, Spaceship 10.37 -> 3.85, Ocean 6.96 -> 4.44), so the keys the change
    was meant to help were the hardest to read on the board.

    Asserted against the live `accentKeyColor` the QML actually resolves, per
    theme, rather than against the formula: a future edit to the wash is only
    safe if the resulting colour still clears the ratio.
    """

    # WCAG 2.1 AA for body text. Key labels are 10-16 px DemiBold, which is
    # "normal" text under the spec (the 3:1 large-text allowance starts at
    # 18.66 px bold), so 4.5 is the applicable threshold, not 3.
    MIN_RATIO = 4.5

    @staticmethod
    def _relative_luminance(color) -> float:
        def channel(v: float) -> float:
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return (
            0.2126 * channel(color.redF())
            + 0.7152 * channel(color.greenF())
            + 0.0722 * channel(color.blueF())
        )

    @classmethod
    def _contrast(cls, a, b) -> float:
        la, lb = cls._relative_luminance(a), cls._relative_luminance(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    def test_label_clears_wcag_aa_on_every_theme(self, qml_root) -> None:
        root, warnings, _ = qml_root
        themes = _theme_names(root)
        assert len(themes) == 9, f"expected 9 themes, found {themes}"

        for theme in themes:
            root.setProperty("currentTheme", theme)
            QCoreApplication.processEvents()
            text = root.property("themeTextColor")
            accent_key = root.property("accentKeyColor")
            ratio = self._contrast(text, accent_key)
            assert ratio >= self.MIN_RATIO, (
                f"theme {theme!r}: the accent key fill {accent_key.name()} leaves "
                f"the label {text.name()} at {ratio:.2f}:1, below WCAG AA "
                f"({self.MIN_RATIO}:1). The wash has to yield to the label, not "
                "the other way round: these are the keys the style exists to "
                "make findable."
            )
        assert _real_warnings(warnings) == []

    def test_the_wash_is_still_visible_where_it_had_to_back_off(self, qml_root) -> None:
        """The inverse test, so 'pass by not tinting at all' cannot be the fix.

        Backing the alpha off far enough always satisfies the contrast test
        above, in the limit by leaving the key colour untouched. That would
        satisfy the letter of the rule and silently delete the feature, so
        pin that every theme's accent key is still visibly distinct from an
        ordinary key.
        """
        root, warnings, _ = qml_root
        for theme in _theme_names(root):
            root.setProperty("currentTheme", theme)
            QCoreApplication.processEvents()
            plain = root.property("themeKeyColor")
            accent_key = root.property("accentKeyColor")
            delta = max(
                abs(accent_key.redF() - plain.redF()),
                abs(accent_key.greenF() - plain.greenF()),
                abs(accent_key.blueF() - plain.blueF()),
            )
            assert delta >= 0.05, (
                f"theme {theme!r}: the accent key {accent_key.name()} is within "
                f"{delta:.3f} of a plain key {plain.name()}, so the style no "
                "longer marks anything"
            )
        assert _real_warnings(warnings) == []


class TestTheMainWindowRestoreClampsToTheWholeDesktop:
    """The main window's own saved-position restore used to clamp against
    Screen.width/Screen.height (the primary screen), the exact bug PR #31
    fixed for the snippets window: a position saved on a second monitor
    snaps back to the primary display on every launch, and a monitor to
    the left of the primary has negative coordinates that collapse to 0.
    It now reuses root.clampedWindowPos()/root.desktopBounds(), the same
    helper the snippets window's own restore uses.

    The offscreen platform plugin only ever reports one screen, so this
    cannot exercise the multi-monitor arithmetic directly -- the same
    caveat TestTheRestoredPositionIsClampedToTheWholeDesktop documents for
    the snippets window (that class covers the left-edge-collapses-to-
    origin case directly against clampedWindowPos(), so it isn't repeated
    here). What these pin instead: the restored x on the *live* window is
    exactly what clampedWindowPos() computes (not some other value a
    hand-rolled Screen.width formula would also happen to produce), and
    the -1000000 "never positioned" sentinel still takes the centered
    default path rather than being run through the clamp.

    Every case below pins `ui/savedWindowWidth` to a value comfortably
    smaller than the offscreen platform's single 800x800 screen. Left at
    its fresh-install default the window is wider than that screen
    (~1160px), which pushes every position toward or past x=0 -- and the
    offscreen QPA plugin snaps an actual window position of exactly 0 to
    2px in from the edge (verified directly: assigning root.x = 0 reads
    back as 2). That snap is a property of the headless platform, not of
    clampedWindowPos() itself, which is a pure function and unaffected;
    picking a width that keeps every assertion here clear of the edge
    sidesteps it entirely.
    """

    _FITS_ON_SCREEN_WIDTH = 500

    def test_a_position_already_on_screen_survives_the_restore_unchanged(
        self, qml_root_factory
    ) -> None:
        root, warnings, _bridge = qml_root_factory(
            {
                "ui/savedWindowWidth": self._FITS_ON_SCREEN_WIDTH,
                "ui/savedWindowX": 50,
                "ui/savedWindowY": 60,
            }
        )
        assert _real_warnings(warnings) == []
        assert root.property("x") == pytest.approx(50)
        assert root.property("y") == pytest.approx(60)

    def test_a_saved_x_past_the_right_edge_is_pulled_back_by_the_shared_clamp(
        self, qml_root_factory
    ) -> None:
        root, _warnings, _bridge = qml_root_factory(
            {
                "ui/savedWindowWidth": self._FITS_ON_SCREEN_WIDTH,
                "ui/savedWindowX": 999999,
                "ui/savedWindowY": 60,
            }
        )
        bounds = root.desktopBounds()
        right = bounds.property("right").toNumber()
        width = root.property("width")
        # On this single-screen offscreen run, Screen.width and
        # desktopBounds().right agree, so this alone can't tell "reused
        # the shared helper" apart from "kept the old Screen.width
        # formula" by value -- the same caveat the snippets-window
        # version of this test documents. What it does pin is that the
        # pulled-back x is exactly right-edge-minus-width, the formula
        # clampedWindowPos() uses, and not some other value (0, a stale
        # width, ...).
        assert root.property("x") == pytest.approx(right - width)
        assert root.property("y") == pytest.approx(60)

    def test_the_never_positioned_sentinel_still_centers_instead_of_clamping(
        self, qml_root_factory
    ) -> None:
        # No pre-set savedWindowX/Y: QSettings falls back to the
        # -1000000 sentinel default, which must still take the
        # centered/bottom path. If the sentinel were accidentally run
        # through clampedWindowPos(-1000000, ...), x would land at the
        # desktop's left edge instead of the centered value asserted
        # here.
        root, warnings, _bridge = qml_root_factory(
            {"ui/savedWindowWidth": self._FITS_ON_SCREEN_WIDTH}
        )
        bounds = root.desktopBounds()
        right = bounds.property("right").toNumber()
        width = root.property("width")
        assert _real_warnings(warnings) == []
        assert root.property("x") == pytest.approx((right - width) / 2)


class TestTheFullSizeSymbolPage:
    r"""The full-size layouts reach one symbol page from the space row.

    Compact View has had ``?123`` and ``=\<`` from the start while the
    full-size layouts had nothing, so anything outside a physical keyboard's
    printing was reachable in one view and not the other. The data half of
    this is asserted in tests/test_layouts.py; this is the QML half, and it
    covers the three things only a live load can show: that the page can be
    left again, that reaching it moves nothing on screen, and that a key on
    it types the glyph printed on its cap.
    """

    @staticmethod
    def _visible_keys(root) -> list:
        return [i for i in TestSecondSymbolPage._key_items(root) if i.isVisible()]

    @classmethod
    def _key_data(cls, root) -> list[dict]:
        out: list[dict] = []
        for item in cls._visible_keys(root):
            kd = item.property("kd")
            if hasattr(kd, "toVariant"):
                kd = kd.toVariant()
            if isinstance(kd, dict):
                out.append(kd)
        return out

    @classmethod
    def _click_point(cls, root, match):
        """Scene-centre of the first visible key *match* accepts, for tapping.

        Separate from _key_point, which measures. Returns a bare point and
        drops the item reference before returning, for the same reason
        TestSecondSymbolPage._tap does: the caller pumps next, and a layer
        switch frees the delegate.
        """
        for item in cls._visible_keys(root):
            kd = item.property("kd")
            if hasattr(kd, "toVariant"):
                kd = kd.toVariant()
            if isinstance(kd, dict) and match(kd):
                point = item.mapToScene(item.boundingRect().center()).toPoint()
                del item
                return point
        return None

    @classmethod
    def _key_point(cls, root, match):
        """Centre of the first visible key *match* accepts, measured from the
        top-left corner of the key grid.

        Not scene coordinates, and the difference is what makes the
        assertion honest rather than flaky. The first tap on a non-char key
        settles the chrome above the keyboard by one pixel (Caps does it too,
        on a tree with no symbol layer in it), so a scene-y comparison across
        a tap fails by 1 px for a reason that has nothing to do with the
        grid. Measuring from the grid's own corner normalises that away while
        still catching the failure this test exists for: a row rendered in the
        wrong order moves the space bar by a whole row.

        Returns bare numbers and drops every item reference before returning:
        callers pump the event loop next, and a layer switch frees the
        Repeater's delegates. Holding a PySide wrapper across that is what
        segfaulted CI once already.
        """
        found = None
        left = top = None
        for item in cls._visible_keys(root):
            corner = item.mapToScene(item.boundingRect().topLeft())
            left = corner.x() if left is None else min(left, corner.x())
            top = corner.y() if top is None else min(top, corner.y())
            kd = item.property("kd")
            if hasattr(kd, "toVariant"):
                kd = kd.toVariant()
            if isinstance(kd, dict) and match(kd):
                centre = item.mapToScene(item.boundingRect().center())
                found = (centre.x(), centre.y())
        if found is None:
            return None
        return (round(found[0] - left, 3), round(found[1] - top, 3))

    @pytest.fixture
    def full_size(self, qml_root):
        root, warnings, bridge = qml_root
        root.show()
        _pump_until(lambda: len(self._visible_keys(root)))
        assert root.property("compactView") is False, "precondition: full size"
        assert root.property("activeLayer") == "base"
        return root, warnings, bridge

    def test_the_sym_key_is_both_the_way_in_and_the_way_out(self, full_size) -> None:
        """The entry key sits on the space row, which carries no `layer` and
        therefore renders on every page. Tapping it a second time has to come
        back: on the page it opened it is the key the pointer is already on,
        and re-selecting the layer already showing is a dead tap.
        """
        root, warnings, _ = full_size

        TestSecondSymbolPage._tap(root, "sym")
        assert root.property("activeLayer") == "sym"

        TestSecondSymbolPage._tap(root, "sym")
        assert root.property("activeLayer") == "base", (
            "the Sym key did not come back out of the page it opened"
        )
        assert _real_warnings(warnings) == []

    def test_abc_leaves_the_page_as_well(self, full_size) -> None:
        """Two ways back, and the second is not redundant: ABC sits in the
        slots the Shift keys had, so it is the wide target already under a
        pointer that reached for Shift out of habit."""
        root, warnings, _ = full_size

        TestSecondSymbolPage._tap(root, "sym")
        TestSecondSymbolPage._tap(root, "base")

        assert root.property("activeLayer") == "base"
        assert _real_warnings(warnings) == []

    def test_the_space_bar_does_not_move(self, full_size) -> None:
        """The one measurement the layout tests cannot make.

        Matching unit totals per row are what should keep the grid still, but
        rows are centred individually and the space row grew by a key at each
        end, so this asserts the result rather than the arithmetic behind it:
        the most-clicked key on the keyboard is in the same place on both
        pages, to the pixel.
        """
        root, warnings, _ = full_size

        before = self._key_point(root, lambda kd: kd.get("action") == "space")
        assert before is not None, "no space bar rendered"
        key_w = root.property("keyW")

        TestSecondSymbolPage._tap(root, "sym")
        _pump_until(lambda: len(self._visible_keys(root)))

        after = self._key_point(root, lambda kd: kd.get("action") == "space")
        assert after is not None, "the space bar left the screen on the symbol page"
        assert after == before, f"the space bar moved from {before} to {after} within the grid"
        assert root.property("keyW") == pytest.approx(key_w), "the keys resized"
        assert _real_warnings(warnings) == []

    def test_the_digits_stay_on_screen(self, full_size) -> None:
        """Compact View puts its digits behind the ?123 hop because a 13u row
        has nowhere else for them. Full size has a number row of its own and
        it carries no `layer`, so the symbol page swaps only the three letter
        rows and a digit never costs a second hop.
        """
        root, _, _ = full_size
        TestSecondSymbolPage._tap(root, "sym")

        glyphs = {kd.get("key") for kd in self._key_data(root) if kd.get("type") == "char"}
        assert set("1234567890") <= glyphs, "digits went behind the symbol hop"

    def test_a_symbol_key_types_the_glyph_on_its_cap(self, full_size) -> None:
        """Caps Lock deliberately survives a layer switch (it only affects
        letters, and this page has none), and Python's ``str.upper()`` is not
        the identity on every non-ASCII character. Without the `literal` flag
        routing these keys through pressKeyLiteral, Caps Lock plus the micro
        sign typed a Greek capital Mu: the key emitted one glyph while the cap
        displayed another, which is the same disagreement the symbol pages
        carry no Shift key in order to avoid.
        """
        root, warnings, bridge = full_size
        bridge.toggleCapsLock()
        QCoreApplication.processEvents()
        assert root.property("capsOn") is True, "precondition: Caps Lock is on"

        TestSecondSymbolPage._tap(root, "sym")
        _pump_until(lambda: len(self._visible_keys(root)))

        point = self._click_point(root, lambda kd: kd.get("key") == "µ")
        assert point is not None, "no micro-sign key on the symbol page"

        bridge._synth.send_text.reset_mock()
        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()
        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

        sent = [call.args[0] for call in bridge._synth.send_text.call_args_list]
        assert sent == ["µ"], f"typed {sent!r} instead of the glyph on the cap"
        assert _real_warnings(warnings) == []

    def test_swipe_is_disabled_on_the_symbol_page(self, full_size) -> None:
        """A swipe is a shape matched against letter centres, and off the base
        layer the registry holds the symbol page's instead. Disabling the
        overlay hands every press back to the keys' own MouseAreas, which is
        the ordinary swipe-off path, so nothing on the page becomes a dead
        tap the way the specials did when one registry served both jobs.
        """
        root, warnings, _ = full_size
        root.setProperty("swipeEnabled", True)
        QCoreApplication.processEvents()

        overlay = root.findChild(QQuickItem, "swipeOverlay")
        assert overlay is not None, "swipeOverlay not found by objectName"
        assert overlay.property("enabled") is True, "precondition: swipe is on"

        TestSecondSymbolPage._tap(root, "sym")
        overlay = root.findChild(QQuickItem, "swipeOverlay")
        assert overlay.property("enabled") is False, "the overlay still owns every press"

        TestSecondSymbolPage._tap(root, "sym")
        overlay = root.findChild(QQuickItem, "swipeOverlay")
        assert overlay.property("enabled") is True, "swipe never came back"
        assert _real_warnings(warnings) == []
