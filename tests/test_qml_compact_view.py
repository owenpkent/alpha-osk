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

from PySide6.QtCore import QCoreApplication, QSettings, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

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
    return [
        w for w in warnings
        if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)
    ]


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
    engine.warnings.connect(
        lambda errs: warnings.extend(e.toString() for e in errs)
    )
    engine.rootContext().setContextProperty("keyboard", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), (
        "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    )
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
    def test_enabling_compact_swaps_the_layout_and_resizes_units(
        self, qml_root
    ) -> None:
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

    def test_returning_to_full_size_restores_the_base_layer(
        self, qml_root
    ) -> None:
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
