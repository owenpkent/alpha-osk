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

# PySide6 imports fine on a bare headless box, but QtGui dlopens the host's
# libEGL / libGL and raises ImportError if they are absent. That happens at
# module scope, which pytest reports as a *collection* error and which aborts
# the whole run rather than failing this one module. Degrade to a skip so a
# contributor without the Qt system libs still gets the rest of the suite.
# CI installs the libs (see .github/workflows/ci.yml) so these still run there.
try:
    # QQuickItem is imported for its side effect and never named below:
    # importing QtQuick is what registers the QQuickItem* type converter,
    # without which reading the window's `contentItem` raises
    # "Can't find converter for 'QQuickItem*'".
    from PySide6.QtCore import QCoreApplication, QObject, QSettings, QUrl  # noqa: E402
    from PySide6.QtGui import QFont, QFontMetricsF, QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
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


def _font_is_a_placeholder() -> bool:
    """True when Qt resolved a fixed-width stand-in instead of a real font.

    On a box with no matching font installed, Qt falls back to a placeholder
    where every glyph has the same advance. That makes the pill fitter's
    arithmetic trivially self-consistent: `advanceWidth` and `boundingRect`
    agree by construction, proportional text never packs tightly, and the
    260-configuration no-elide sweep below cannot fail whatever the fitter
    does. It "passed" on a Windows dev box for exactly this reason while a
    real eliding bug was live on Linux.

    Detected by measuring the narrowest and widest ASCII letters: in any
    proportional font "i" is far narrower than "W"; in the placeholder they
    are identical.

    MUST NOT be called at import time. Constructing a QFont before a
    QGuiApplication exists kills the interpreter outright, with no traceback
    and no pytest output at all, which is a memorable way to spend ten
    minutes. Hence the fixture below rather than a module-level skipif.
    """
    font = QFont()
    font.setFamilies(["Ubuntu", "Noto Sans", "sans-serif"])
    font.setPixelSize(20)
    font.setWeight(QFont.Medium)
    metrics = QFontMetricsF(font)
    return metrics.horizontalAdvance("i") == metrics.horizontalAdvance("W")


@pytest.fixture
def requires_real_font(qapp):
    """Skip tests whose meaning depends on real proportional metrics.

    A skip is the honest outcome: it reports "not verified here" rather than
    a green tick that means nothing. CI installs real fonts, so these run
    there, and CI is where the pill-width bugs actually showed up.
    """
    if _font_is_a_placeholder():
        pytest.skip(
            "Qt resolved a fixed-width placeholder font, so pill-width "
            "assertions are vacuous on this machine (every glyph is the same "
            "width). CI runs them against real fonts."
        )


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)]


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
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    engine.rootContext().setContextProperty("keyboard", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    try:
        yield root, warnings, bridge
    finally:
        del engine


def _child(root, name: str) -> QObject:
    obj = root.findChild(QObject, name)
    assert obj is not None, f"no object named {name!r} in Main.qml"
    return obj


# Bound on the wait in _show. Pump until the pills exist rather than
# asserting after a single pass, which saw an empty pill row perhaps 40% of
# the time at a different window width each run (issue #16).
#
# Issue #16 attributed that to Repeater delegate incubation not being flushed
# by one processEvents(). That is not the mechanism, and the distinction
# matters because it is what makes the loop below correct rather than
# superstition: `root.predictions` is not a binding, Main.qml assigns it
# imperatively from the bridge, and `onActiveChanged` calls
# `clearPredictions()` on deactivation. Waiting longer gave that path *more*
# chances to fire, so waiting harder made it worse, not better. Re-asserting
# the predictions each pass is what actually fixes it.
_PILL_PUMP_PASSES = 10


def _show(root, predictions: list[str]):
    """Push predictions, wait for the pills, return (pill row, clear button).

    Two details are load-bearing here, and they fix two different failures
    that both presented as "the pill sweep is flaky".

    `_pill_texts` returns PySide wrappers for QML-owned QQuickItems. The
    Repeater destroys and recreates its delegates whenever the model changes,
    so a wrapper that outlives an event-loop turn can be pointing at freed
    memory, and the next `processEvents()` walks straight into it. The first
    version of this loop held the returned list across the next pass and
    Linux CI died with SIGSEGV inside `processEvents` (exit 139, which takes
    the whole run down rather than failing one test). Counting drops every
    wrapper before the next wait.

    This still cannot hide a real regression: a genuine "the bar dropped
    everything" bug exhausts the budget and arrives at the caller's non-empty
    assertion anyway. The loop only buys time for work already queued.
    """
    for _ in range(_PILL_PUMP_PASSES):
        # Re-asserted every pass, not set once before the loop. `predictions`
        # is not a binding: Main.qml's Connections block assigns it
        # imperatively from the bridge, and `onActiveChanged` calls
        # `clearPredictions()` whenever the window loses activation. So an
        # activation change landing mid-wait makes the bridge push an empty
        # list over whatever the test asked for, and every pill disappears.
        # Pushing the wanted state each pass makes the test say what it means
        # ("show these") instead of depending on the window never being
        # deactivated by the environment.
        root.setProperty("predictions", predictions)
        QCoreApplication.processEvents()
        if _pill_count(root):
            break
    return _child(root, "predictionRow"), _child(root, "clearContextButton")


def _unwrap(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def _fit(row) -> tuple[list[str], list[float]]:
    """The words the row actually rendered, and their pill widths."""
    fit = _unwrap(row.property("fit"))
    return list(fit["words"]), list(fit["widths"])


def _pill_texts(root) -> list:
    """Every pill label Text item, found through the VISUAL item tree.

    `root.findChildren(QObject, ...)` does NOT work here and silently returns
    an empty list: a Repeater's delegates are re-parented as *visual* children
    of the Repeater's parent item, and their QObject parent is the delegate
    model, not the item tree. Every truncation assertion in this file used to
    go through findChildren, so all of them passed vacuously against zero
    pills — which is how a real eliding regression shipped under a test named
    "no pill is ever truncated". Walk `childItems()` instead, and assert
    non-empty at the call site.

    **Never hold the result across an event-loop turn.** These are PySide
    wrappers for items QML owns, and the Repeater frees its delegates on every
    model change, so a retained wrapper can outlive the object it points at.
    Doing that inside a wait loop segfaulted Linux CI. Use `_pill_count` if
    all you need is "are there any yet".
    """
    out = []

    def walk(item):
        for child in item.childItems():
            if child.objectName() == "predictionPillText":
                out.append(child)
            walk(child)

    walk(root.property("contentItem"))
    return out


def _pill_count(root) -> int:
    """How many pill labels exist right now, holding on to none of them.

    The readiness probe for `_show`'s wait loop. Returning an int rather than
    the items is the whole point: every wrapper is dropped before the caller
    waits again. See the warning on `_pill_texts`.
    """
    return len(_pill_texts(root))


def expect_pills(root) -> list:
    """Every pill label, refusing to return an empty list.

    The fail-closed counterpart to `_pill_texts`. Use this anywhere the test
    goes on to *iterate* the pills, because iterating nothing passes every
    assertion inside the loop and reports success. That is not hypothetical:
    it is how a whole class named "no pill is ever truncated" ran against
    zero pills for its entire life while a real eliding bug shipped.

    A test that genuinely expects no pills should say so with `_pill_count`.
    """
    pills = _pill_texts(root)
    assert pills, (
        "no prediction pills found in the visual tree. Either the bar really "
        "rendered nothing, or the lookup broke; both make the assertions that "
        "follow vacuous, so this fails rather than passing quietly."
    )
    return pills


def _truncated(root) -> list[str]:
    """Pill labels Qt actually had to elide — the ground truth for this bar.

    Asserting on `Text.truncated` beats re-deriving widths in the test: it is
    the same flag the hover ToolTip is gated on, so it cannot disagree with
    what the user sees.
    """
    return [t.property("text") for t in expect_pills(root) if t.property("truncated")]


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


@pytest.mark.usefixtures("requires_real_font")
class TestNoPillIsEverTruncated:
    """The bar drops low-ranked pills rather than eliding any of them.

    Reported from a live 940 px window: eight "documentation"-family
    candidates all rendered as "docu…", which is unusable — every pill looks
    identical, so there is nothing to choose between.
    """

    # The reported case: eight long candidates sharing a prefix.
    CROWDED = [
        "documentation",
        "document",
        "documented",
        "documenting",
        "documents",
        "documentary",
        "documentaries",
        "documentation's",
    ]

    def test_the_pills_are_actually_findable(self, qml_root):
        """Guards every other assertion in this class.

        `_truncated` used to walk QObject children, where a Repeater's
        delegates do not live, so it returned [] no matter what was on
        screen and every no-elide assertion below passed against zero pills.
        A real eliding regression shipped underneath them. If this fails,
        distrust the rest of this class rather than the bar.
        """
        root, _, _ = qml_root
        row, _ = _show(root, self.CROWDED)
        pills = expect_pills(root)
        words, _ = _fit(row)

        assert len(pills) == len(words) > 0
        assert [p.property("text") for p in pills] == words

    def test_every_pill_has_room_for_its_own_text(self, qml_root):
        """The no-elide guarantee, asserted as the arithmetic behind it.

        `Text.truncated` only reports a *failure* after the fact. This checks
        the invariant that prevents it: the width computeFit hands each pill
        must cover the text plus the inset the delegate reserves on both
        sides. The two used to be derived from different fractions of
        `predHorizontalPad` (0.45 reserved vs 0.56 consumed), so text-driven
        pills were born ~4 px too narrow and only survived when leftover
        slack happened to top them up.

        Swept rather than spot-checked, and that is load-bearing. The failure
        is a knife-edge: it only bites at the widths where the row packs
        tightly enough that the leftover-slack water-fill cannot cover the
        shortfall, so a handful of round numbers misses it entirely. With the
        deficit present this sweep fails at 130 of the 260 configurations
        below; at 940px non-compact (the obvious width to spot-check) it does
        not fail at all.

        Assert on `truncated`, never on `contentWidth <= width`: once a Text
        elides, `contentWidth` measures the *shortened* string, so it fits by
        construction and the comparison can never fail. That check looks like
        arithmetic proof and is actually unfalsifiable.
        """
        root, _, _ = qml_root
        failures = []
        for compact in (False, True):
            root.setProperty("compactView", compact)
            root.setProperty("showNumberRow", compact)
            for width in range(720, 1240, 4):
                root.setProperty("width", width)
                _show(root, self.CROWDED)
                for label in _truncated(root):
                    failures.append(f"compact={compact} w={width} {label!r}")
        assert not failures, f"{len(failures)} pill(s) elided:\n  " + "\n  ".join(failures[:10])

    def test_the_reported_case_renders_whole_words(self, qml_root):
        root, _, _ = qml_root
        row, _ = _show(root, self.CROWDED)

        expect_pills(root)
        assert _truncated(root) == [], "a pill was elided"
        words, _ = _fit(row)
        assert words, "the bar dropped everything"
        assert words == self.CROWDED[: len(words)], (
            "pills must be dropped from the tail — those are the lowest-ranked"
        )

    def test_dropping_is_the_last_resort(self, qml_root):
        """Padding compresses first; short lists must survive intact."""
        root, _, _ = qml_root
        row, _ = _show(root, ["the", "then", "there", "these"])
        words, _ = _fit(row)
        assert len(words) == 4
        assert len(expect_pills(root)) == 4
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
        expect_pills(root)
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
            for entry in (registry.toVariant() if hasattr(registry, "toVariant") else registry)
        }
        assert set("1234567890") <= keys, (
            "number-row digits missing from charKeyRegistry — taps on them "
            "would be swallowed by the swipe overlay"
        )
