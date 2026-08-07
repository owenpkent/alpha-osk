"""Headless QML tests for the prediction bar and the number-row panel.

Both are pure QML geometry, so the Python suite cannot reach them. Same
harness as `test_qml_compact_view.py`: load the real `qml/Main.qml` under the
offscreen platform plugin against a real `KeyboardBridge` and inspect the
live objects.

The invariant under test for the bar is that the clear-context (circle-arrow)
button owns a strip at the right edge that the pill row may never enter. The
`clearCtxReserve` property existed but was never subtracted from anything, so
the row was sized and centred against the full window width and the
right-hand pill rendered *underneath* the button.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

# Must be set before QGuiApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QObject, QSettings, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

from src.keyboard_bridge import KeyboardBridge  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

TEST_ORG = "alpha-osk-tests"
TEST_APP = "Alpha-OSK-Tests"

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)

# Long enough that the naive equal-split sizing would have elided them, so a
# regression in the water-fill shows up as truncation rather than passing by
# accident on short words.
LONG_PREDICTIONS = [
    "internationalization",
    "characteristically",
    "responsibilities",
    "acknowledgement",
]


def _real_warnings(warnings: list[str]) -> list[str]:
    return [
        w for w in warnings
        if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)
    ]


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
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
    warnings: list[str] = []
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


def _child(root, name: str) -> QObject:
    obj = root.findChild(QObject, name)
    assert obj is not None, f"no object named {name!r} in Main.qml"
    return obj


def _show(root, predictions: list[str]):
    """Push predictions and return (pill row, clear button)."""
    root.setProperty("predictions", predictions)
    QCoreApplication.processEvents()
    return _child(root, "predictionRow"), _child(root, "clearContextButton")


def _unwrap(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def _fit(row) -> tuple[list[str], list[float]]:
    """The words the row actually rendered, and their pill widths."""
    fit = _unwrap(row.property("fit"))
    return list(fit["words"]), list(fit["widths"])


def _truncated(root) -> list[str]:
    """Pill labels Qt actually had to elide — the ground truth for this bar.

    Asserting on `Text.truncated` beats re-deriving widths in the test: it is
    the same flag the hover ToolTip is gated on, so it cannot disagree with
    what the user sees.
    """
    return [
        t.property("text")
        for t in root.findChildren(QObject, "predictionPillText")
        if t.property("truncated")
    ]


class TestClearButtonNeverCoversPills:
    @pytest.mark.parametrize(
        "predictions",
        [LONG_PREDICTIONS, ["a", "the", "I"], ["hello"], LONG_PREDICTIONS[:2]],
        ids=["four-long", "three-short", "one", "two-long"],
    )
    def test_pill_row_stays_left_of_the_button(self, qml_root, predictions):
        root, warnings, _ = qml_root
        row, button = _show(root, predictions)

        row_right = row.property("x") + row.property("width")
        assert row_right <= button.property("x"), (
            f"pill row runs to {row_right}px but the clear button starts at "
            f"{button.property('x')}px — the last pill is under the button"
        )
        assert _real_warnings(warnings) == []

    def test_row_still_starts_inside_the_bar(self, qml_root):
        """The reserve must shrink the row, not shove it off the left edge."""
        root, _, _ = qml_root
        row, _ = _show(root, LONG_PREDICTIONS)
        assert row.property("x") >= 8

    def test_holds_after_the_window_is_narrowed(self, qml_root):
        """Resizing re-runs the allocation; the reserve has to survive it."""
        root, _, _ = qml_root
        row, button = _show(root, LONG_PREDICTIONS)

        root.setProperty("width", root.property("minimumWidth"))
        QCoreApplication.processEvents()

        row_right = row.property("x") + row.property("width")
        assert row_right <= button.property("x")

    def test_short_words_keep_their_natural_width(self, qml_root):
        """Max-min fairness, not an equal split — the anti-elide guarantee."""
        root, _, _ = qml_root
        row, _ = _show(root, ["I", "the", "internationalization"])
        widths = sorted(_unwrap(row.property("pillWidthList")))
        assert len(widths) == 3
        # "I" and "the" settle at the min-width floor while the long word
        # absorbs the slack. An equal split would make all three identical.
        assert widths[-1] > widths[0] * 1.5


class TestNoPillIsEverTruncated:
    """The bar drops low-ranked pills rather than eliding any of them.

    Reported from a live 940 px window: eight "documentation"-family
    candidates all rendered as "docu…", which is unusable — every pill looks
    identical, so there is nothing to choose between.
    """

    # The reported case: eight long candidates sharing a prefix.
    CROWDED = [
        "documentation", "document", "documented", "documenting",
        "documents", "documentary", "documentaries", "documentation's",
    ]

    def test_the_reported_case_renders_whole_words(self, qml_root):
        root, _, _ = qml_root
        row, _ = _show(root, self.CROWDED)

        assert _truncated(root) == [], "a pill was elided"
        words, _ = _fit(row)
        assert words, "the bar dropped everything"
        assert words == self.CROWDED[:len(words)], (
            "pills must be dropped from the tail — those are the lowest-ranked"
        )

    def test_dropping_is_the_last_resort(self, qml_root):
        """Padding compresses first; short lists must survive intact."""
        root, _, _ = qml_root
        row, _ = _show(root, ["the", "then", "there", "these"])
        words, _ = _fit(row)
        assert len(words) == 4
        assert _truncated(root) == []

    def test_survivors_still_clear_the_button(self, qml_root):
        root, _, _ = qml_root
        row, button = _show(root, self.CROWDED)
        assert row.property("x") + row.property("width") <= button.property("x")

    def test_narrow_window_drops_more_rather_than_eliding(self, qml_root):
        root, _, _ = qml_root
        row, _ = _show(root, self.CROWDED)
        wide_count = len(_fit(row)[0])

        root.setProperty("width", root.property("minimumWidth"))
        QCoreApplication.processEvents()

        narrow_count = len(_fit(row)[0])
        assert narrow_count <= wide_count
        assert _truncated(root) == []

    def test_a_single_oversized_word_still_fits_the_bar(self, qml_root):
        """Nothing left to drop — it may elide, but must not overflow."""
        root, _, _ = qml_root
        row, button = _show(root, ["pneumonoultramicroscopicsilicovolcanoconiosis" * 3])
        words, _ = _fit(row)
        assert len(words) == 1
        assert row.property("x") + row.property("width") <= button.property("x")


class TestNumberRowPanel:
    def test_off_by_default(self, qml_root):
        root, _, _ = qml_root
        assert root.property("showNumberRow") is False

    def test_enabling_it_renders_without_qml_errors(self, qml_root):
        root, warnings, _ = qml_root
        root.setProperty("showNumberRow", True)
        QCoreApplication.processEvents()
        assert _real_warnings(warnings) == []

    def test_digits_register_as_char_keys(self, qml_root):
        """Unregistered keys are dead taps while the swipe overlay is on."""
        root, _, _ = qml_root
        root.setProperty("compactView", True)
        root.setProperty("showNumberRow", True)
        QCoreApplication.processEvents()

        registry = root.property("charKeyRegistry")
        keys = {
            entry["kd"]["key"]
            for entry in (registry.toVariant() if hasattr(registry, "toVariant")
                          else registry)
        }
        assert set("1234567890") <= keys, (
            "number-row digits missing from charKeyRegistry — taps on them "
            "would be swallowed by the swipe overlay"
        )
