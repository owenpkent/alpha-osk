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
    from PySide6.QtCore import QCoreApplication, QSettings, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

TEST_ORG = "alpha-osk-tests"
TEST_APP = "Alpha-OSK-Tests"

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
        base_units = root.property("totalKeyUnits")

        root.setProperty("compactView", True)
        QCoreApplication.processEvents()
        compact_units = root.property("totalKeyUnits")

        # Panels contribute the same constant to both, so the difference is
        # exactly the 15.5u -> 13.0u main-block change.
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
        root.setProperty("compactView", True)
        root.setProperty("showNumberRow", True)
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
