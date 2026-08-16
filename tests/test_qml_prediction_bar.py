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
    from PySide6.QtQml import QQmlApplicationEngine, QQmlEngine, QQmlExpression  # noqa: E402
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

# Re-exported so the existing references below keep reading TEST_ORG /
# TEST_APP.  All four QML modules must share one scope. See
# tests/qt_settings_scope.py for why a per-module copy is a trap.
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

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


def _scene_left(item) -> float:
    """An item's left edge in scene coordinates.

    Not `item.x`: the bar's buttons sit inside a Row, so their `x` is
    relative to that Row and comparing it against a sibling's `x` compares
    two different origins. That reads as "the pill row starts 500 px past
    the button", which is what this returned when a second button arrived.
    """
    return item.mapToScene(item.boundingRect().topLeft()).x()


def _first_button_left(root, clear_button) -> float:
    """The left edge of the leftmost button parked at the bar's right end.

    There are two now, Snippets then clear-context, so the strip the pills
    must stay out of starts at the Snippets one. Written as a min rather
    than by naming it, so adding a third button cannot quietly make this
    measure the wrong edge.
    """
    return min(_scene_left(clear_button), _scene_left(_child(root, "snippetsBarButton")))


class TestClearButtonNeverCoversPills:
    """The buttons parked at the right end own a strip the pills may not
    enter. There are two of them now (Snippets, then clear-context), so
    the leftmost is the one the row must stay clear of."""

    @pytest.mark.parametrize(
        "predictions",
        [LONG_PREDICTIONS, ["a", "the", "I"], ["hello"], LONG_PREDICTIONS[:2]],
        ids=["four-long", "three-short", "one", "two-long"],
    )
    def test_pill_row_stays_left_of_the_buttons(self, qml_root, predictions):
        root, warnings, _ = qml_root
        row, button = _show(root, predictions)

        row_right = _scene_left(row) + row.property("width")
        first_button = _first_button_left(root, button)
        assert row_right <= first_button, (
            f"pill row runs to {row_right}px but the buttons start at "
            f"{first_button}px — the last pill is under a button"
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

        row_right = _scene_left(row) + row.property("width")
        first_button = _first_button_left(root, button)
        assert row_right <= first_button

    def test_short_words_keep_their_natural_width(self, qml_root):
        """Max-min fairness, not an equal split — the anti-elide guarantee."""
        root, _, _ = qml_root
        row, _ = _show(root, ["I", "the", "internationalization"])
        widths = sorted(_unwrap(row.property("pillWidthList")))
        assert len(widths) == 3
        # "I" and "the" settle at the min-width floor while the long word
        # absorbs the slack. An equal split would make all three identical.
        assert widths[-1] > widths[0] * 1.5


class TestTheClearButtonIcon:
    """The circle-arrow must be drawn, and must sit in the middle.

    It used to be the glyph "⟲" in a `Text` with `anchors.centerIn`,
    which centres the text *item* and not the ink inside it.  A glyph's
    ink is rarely centred in its em box, so the ring the eye actually
    reads sat down and right of the button while the glyph's tail stuck
    out to the left.  It is now Feather's `rotate-ccw`, drawn from its
    published path data through the Canvas that was already there.

    **The obvious measurement is a trap, and so was the clever one.**
    The ink bounding box was centred in the glyph version too, to within
    half a pixel, because the tail hanging off one side cancelled the
    ring being pushed to the other -- so a bbox assertion alone would
    have passed against the bug it was written for.  This class briefly
    used a radial-spread metric instead (glyph 0.29, hand-drawn arc
    0.09, bar at 0.15), which did separate them.  That metric is gone
    because it no longer measures *us*: Feather puts the arrow outside
    the ring deliberately, which scores 0.147 against a 0.15 bar.  A
    test passing by three thousandths is not a test, and tightening the
    icon to satisfy it would mean redrawing a designer's composition to
    fit an assertion.

    What is left is the pair that is honest about what the code
    guarantees: the icon is *drawn* rather than typeset, which is what
    caught the original bug, and it is grossly centred, which catches a
    dropped or wrong transform.  The one-unit optical correction in
    `inkOffsetX` is deliberately *not* pinned here: it is a sub-pixel
    nicety at this size, and asserting it across renderers would buy a
    flake rather than a guard.
    """

    # Generous on purpose: a dropped translate puts the icon a third of
    # the button away, which is what this is for. Anything tighter is
    # measuring the antialiaser.
    _MAX_CENTRE_OFFSET_PX = 3.0

    def _button_pixels(self, root):
        button = _child(root, "clearContextButton")
        assert button.property("visible"), "the clear-context button is hidden"
        QCoreApplication.processEvents()

        top_left = button.mapToScene(button.boundingRect().topLeft())
        size = round(button.property("width"))
        shot = root.grabWindow()
        if shot.isNull():  # pragma: no cover - depends on the Qt renderer
            pytest.skip("this Qt build cannot grab an offscreen window")
        return button, shot.copy(round(top_left.x()), round(top_left.y()), size, size)

    def _ink(self, crop):
        """Every lit pixel of the icon, as (x, y)."""
        lit = []
        for y in range(crop.height()):
            for x in range(crop.width()):
                colour = crop.pixelColor(x, y)
                luminance = 0.299 * colour.red() + 0.587 * colour.green() + 0.114 * colour.blue()
                if luminance > 110:  # the icon, not the circle's own fill
                    lit.append((x, y))
        return lit

    def test_the_icon_is_centred_on_the_button(self, qml_root):
        root, _, _ = qml_root
        _, crop = self._button_pixels(root)
        lit = self._ink(crop)

        # Inverse first: "draw nothing" satisfies every centring bound
        # ever written, so the test has to insist there is an icon at all.
        assert len(lit) > 40, f"the button has almost no icon on it ({len(lit)} px)"

        xs = [x for x, _ in lit]
        ys = [y for _, y in lit]
        centre = (crop.width() - 1) / 2
        offset_x = (min(xs) + max(xs)) / 2 - centre
        offset_y = (min(ys) + max(ys)) / 2 - centre
        assert abs(offset_x) <= self._MAX_CENTRE_OFFSET_PX, (
            f"the icon sits {offset_x:+.1f}px off centre horizontally"
        )
        assert abs(offset_y) <= self._MAX_CENTRE_OFFSET_PX, (
            f"the icon sits {offset_y:+.1f}px off centre vertically"
        )

    def test_it_fills_a_sensible_share_of_the_button(self, qml_root):
        """Catches a broken scale, which centring alone would not notice."""
        root, _, _ = qml_root
        _, crop = self._button_pixels(root)
        lit = self._ink(crop)
        span = max(
            max(x for x, _ in lit) - min(x for x, _ in lit),
            max(y for _, y in lit) - min(y for _, y in lit),
        )
        assert 0.45 <= span / crop.width() <= 0.95, (
            f"the icon spans {span / crop.width():.0%} of the button"
        )

    def test_it_is_drawn_rather_than_typeset(self, qml_root):
        """States the fix, so a future "just use a glyph" reads as a change.

        Same reasoning as the padlock badge that was removed from the
        keycaps: any glyph small enough to fit is at the mercy of
        whatever font the host resolves it through.
        """
        root, _, _ = qml_root
        button = _child(root, "clearContextButton")
        glyphs = [
            child
            for child in button.findChildren(QQuickItem)
            if child.property("text") not in (None, "")
        ]
        assert glyphs == [], f"the icon is a text glyph again: {glyphs}"


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
            # The number row follows the view on its own now: it is derived
            # from whether the active layout already has one.
            root.setProperty("compactView", compact)
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
        assert _scene_left(row) + row.property("width") <= _first_button_left(root, button)

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
        assert _scene_left(row) + row.property("width") <= _first_button_left(root, button)


class TestNumberRowPanel:
    """Visibility is derived, not a setting: the panel fills in for layouts
    that carry no `number` row of their own, so digits are always on screen
    in both views and a full-size layout never gets a second, narrower
    number row stacked on the one built into its JSON."""

    def test_hidden_on_a_full_size_layout(self, qml_root):
        root, _, _ = qml_root
        assert root.property("showNumberRow") is False

    def test_shown_in_compact_view(self, qml_root):
        root, _, _ = qml_root
        root.setProperty("compactView", True)
        QCoreApplication.processEvents()
        assert root.property("showNumberRow") is True

    def test_renders_without_qml_errors(self, qml_root):
        root, warnings, _ = qml_root
        root.setProperty("compactView", True)
        QCoreApplication.processEvents()
        assert _real_warnings(warnings) == []

    def test_digits_register_as_char_keys(self, qml_root):
        """Unregistered keys are dead taps while the swipe overlay is on."""
        root, _, _ = qml_root
        root.setProperty("compactView", True)
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


class TestPillTextNeverAutoDetectsHtml:
    """Prediction pill text must render as plain text.

    Predictions can originate from an imported vocabulary pack's
    unsanitised dictionary.txt (src/prediction/vocabulary_pack.py). QML's
    `Text` defaults to `Text.AutoText`, which lets Qt auto-detect and
    render HTML, so a pack entry like `<img src='http://x'>` could fire an
    outbound request purely from being shown as a pill. See the security
    notes in CLAUDE.md's vocabulary-pack import-hardening section.
    """

    def test_pill_text_format_is_plain(self, qml_root):
        root, warnings, _ = qml_root
        _show(root, ["hello", "<b>world</b>", "world"])
        pills = expect_pills(root)
        # QObject.property("textFormat") raises "Can't find converter for
        # 'QQuickText::TextFormat'" -- PySide6 has no registered converter
        # for that internal enum. Evaluating a JS comparison against the
        # QML `Text.PlainText` constant sidesteps it entirely: the result
        # is a plain bool, which converts fine. QQmlExpression.evaluate()
        # returns (value, valueIsUndefined), not just the value.
        for pill in pills:
            ctx = QQmlEngine.contextForObject(pill)
            is_plain, is_undefined = QQmlExpression(
                ctx, pill, "textFormat === Text.PlainText"
            ).evaluate()
            assert is_undefined is False, "textFormat expression could not be evaluated"
            assert is_plain is True, (
                "a prediction pill is not forced to Text.PlainText -- an "
                "imported pack word containing markup could auto-render as HTML"
            )
