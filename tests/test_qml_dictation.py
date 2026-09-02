"""Headless QML tests for the dictation surfaces in the suggestion bar.

Everything dictation puts on screen is pure QML geometry and visibility, so
the Python suite in `tests/test_dictation.py` cannot reach any of it. Same
harness as `tests/test_qml_prediction_bar.py`, deliberately copied rather
than reinvented: load the real `qml/Main.qml` under the offscreen platform
plugin against a real `KeyboardBridge`, fail on QML warnings, and inspect
the live objects.

The property the whole feature rests on is that `predBar.micReserve` is
**zero unless dictation is switched on** (see `docs/architecture/DICTATION.md`,
"The mic button, and the left edge"). `predRow.x` centres the pills in
`width - micReserve - clearCtxReserve`, so a reserve of 0 has to collapse
that expression back to exactly the single-reserve one it grew from, which
is what keeps the bar byte-identical for every user who never turns this on.

Two traps carried over verbatim from the sibling module, because both
already produced a test that could not fail:

* `root.findChildren(QObject, "name")` returns an EMPTY LIST for anything
  inside a `Repeater`. Delegates are re-parented as *visual* children, so
  their QObject parent is the delegate model rather than the item tree. A
  real eliding regression once shipped under a class named "no pill is ever
  truncated" for exactly this reason. Walk `childItems()` and assert the
  result is non-empty at the call site.
* Never assert `contentWidth <= width` on a `Text`. Once it elides,
  `contentWidth` measures the *shortened* string, so it fits by construction
  and the comparison can never fail. `Text.truncated` is the only honest
  signal.
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
try:
    # QQuickItem is imported for its side effect and never named below:
    # importing QtQuick is what registers the QQuickItem* type converter,
    # without which reading the window's `contentItem` raises
    # "Can't find converter for 'QQuickItem*'".
    from PySide6.QtCore import (  # noqa: E402
        QCoreApplication,
        QEventLoop,
        QObject,
        QSettings,
        QTimer,
        QUrl,
    )
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

# All the headless QML modules must share one QSettings scope, keyed per
# xdist worker. See tests/qt_settings_scope.py for why a per-module copy of
# the literal is a trap.
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)

# The reported crowding case from the prediction bar: eight long candidates
# sharing a prefix, which is what makes the no-elide sweep bite. Mirrored
# from `TestNoPillIsEverTruncated.CROWDED` in tests/test_qml_prediction_bar.py
# so the two sweeps measure the same bar against the same words.
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


def _font_is_a_placeholder() -> bool:
    """True when Qt resolved a fixed-width stand-in instead of a real font.

    On a box with no matching font installed, Qt falls back to a placeholder
    where every glyph has the same advance, which makes the pill fitter's
    arithmetic trivially self-consistent and the no-elide sweep below unable
    to fail whatever the fitter does.

    MUST NOT be called at import time: constructing a QFont before a
    QGuiApplication exists kills the interpreter outright, with no traceback
    and no pytest output at all.
    """
    font = QFont()
    font.setFamilies(["Ubuntu", "Noto Sans", "sans-serif"])
    font.setPixelSize(20)
    font.setWeight(QFont.Medium)
    metrics = QFontMetricsF(font)
    return metrics.horizontalAdvance("i") == metrics.horizontalAdvance("W")


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
        "organisation - these tests would write to the real user's settings"
    )
    return app


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


@pytest.fixture
def qml_root(qapp):
    warnings: list[str] = []
    QSettings(TEST_ORG, TEST_APP).clear()

    # Disarm the startup update check. `savedAutoCheckUpdates` defaults to
    # true, so three seconds after load Main.qml fires a real HTTPS request
    # to the GitHub releases API from a daemon thread, in every headless QML
    # test that lives that long. That thread emits its result back into a
    # bridge the fixture has already torn down, which surfaces as
    # "RuntimeError: Signal source has been deleted" or, in a longer run, a
    # hard access violation that kills the whole pytest process.
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
    install_context_properties(engine, bridge)
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


def _evaluate(obj, expression: str):
    """Evaluate a QML expression in `obj`'s own context, returning the value.

    Two things need this. `predBar` has no objectName, so the only way to
    read `micReserve` / `clearCtxReserve` is to resolve the id from a sibling
    that shares its context. And `QObject.property("elide")` raises
    "Can't find converter for 'QQuickText::TextFormat'" for QML's internal
    enums, which comparing against the constant in JS sidesteps entirely: the
    result is a plain bool.

    `QQmlExpression.evaluate()` returns (value, valueIsUndefined), and the
    undefined half is checked here so a typo in an expression fails as a
    typo rather than as the behaviour under test.
    """
    ctx = QQmlEngine.contextForObject(obj)
    value, is_undefined = QQmlExpression(ctx, obj, expression).evaluate()
    assert is_undefined is False, f"QML expression {expression!r} evaluated to undefined"
    return value


def _bar(root, expression: str):
    """Evaluate an expression against `predBar`, which carries no objectName."""
    return _evaluate(_child(root, "predictionRow"), expression)


def _scene_left(item) -> float:
    """An item's left edge in scene coordinates.

    Not `item.x`: the mic sits inside its own Row and the pair at the right
    end sit inside another, so their `x` values are relative to three
    different origins and comparing them compares nothing.
    """
    return item.mapToScene(item.boundingRect().topLeft()).x()


def _scene_right(item) -> float:
    return _scene_left(item) + item.property("width")


def _right_hand_buttons_left(root) -> float:
    """The left edge of the leftmost control parked at the bar's right end.

    Two live there, Snippets then clear-context, so the strip the pills and
    the transcript must both stay out of starts at the Snippets one. Written
    as a min rather than by naming one, so adding a third button cannot
    quietly make this measure the wrong edge.
    """
    return min(
        _scene_left(_child(root, "clearContextButton")),
        _scene_left(_child(root, "snippetsBarButton")),
    )


# Bound on the wait in `_show`. Pump until the pills exist rather than
# asserting after a single pass: `root.predictions` is not a binding,
# Main.qml assigns it imperatively, and `onActiveChanged` calls
# `clearPredictions()` on deactivation, so re-asserting each pass is what
# actually makes this deterministic.
_PILL_PUMP_PASSES = 10


def _show(root, predictions: list[str]):
    """Push predictions, wait for the pills, return the pill row.

    `_pill_texts` returns PySide wrappers for QML-owned QQuickItems, and the
    Repeater destroys and recreates its delegates whenever the model changes,
    so a wrapper held across an event-loop turn can point at freed memory.
    Counting rather than retaining drops every wrapper before the next wait.
    """
    for _ in range(_PILL_PUMP_PASSES):
        root.setProperty("predictions", predictions)
        QCoreApplication.processEvents()
        if _pill_count(root):
            break
    return _child(root, "predictionRow")


def _pill_texts(root) -> list:
    """Every pill label Text item, found through the VISUAL item tree.

    `root.findChildren(QObject, ...)` does NOT work here and silently returns
    an empty list, which is how a real eliding regression shipped under a
    test class that could not fail. Walk `childItems()` instead, and assert
    non-empty at the call site.

    **Never hold the result across an event-loop turn.** Use `_pill_count` if
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
    """How many pill labels exist right now, holding on to none of them."""
    return len(_pill_texts(root))


def expect_pills(root) -> list:
    """Every pill label, refusing to return an empty list.

    The fail-closed counterpart to `_pill_texts`. Use this anywhere the test
    goes on to *iterate* the pills, because iterating nothing passes every
    assertion inside the loop and reports success.
    """
    pills = _pill_texts(root)
    assert pills, (
        "no prediction pills found in the visual tree. Either the bar really "
        "rendered nothing, or the lookup broke; both make the assertions that "
        "follow vacuous, so this fails rather than passing quietly."
    )
    return pills


def _truncated(root) -> list[str]:
    """Pill labels Qt actually had to elide - the ground truth for this bar.

    Asserting on `Text.truncated` beats re-deriving widths in the test: it is
    the same flag the hover ToolTip is gated on, so it cannot disagree with
    what the user sees.
    """
    return [t.property("text") for t in expect_pills(root) if t.property("truncated")]


def _wait_ms(ms: int) -> None:
    """Run the event loop for *ms*, so QML animations actually advance.

    `processEvents()` alone does not: an animation is driven by Qt's frame
    timer, so a test that only pumps pending events sees the property at
    its start value however many times it pumps.
    """
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _turn_dictation_on(root, *, configured: bool = True) -> None:
    """Switch the feature on the way the settings panel would."""
    root.setProperty("dictationEnabled", True)
    root.setProperty("dictationConfigured", configured)
    QCoreApplication.processEvents()


class TestTheMicCostsNothingWhileDictationIsOff:
    """A user who never turns dictation on must get the bar they had before.

    `docs/architecture/DICTATION.md` calls this load-bearing rather than an
    optimisation: `predRow.x` centres in `width - micReserve -
    clearCtxReserve`, so a zero reserve has to collapse that expression back
    to the single-reserve one it grew from, exactly.
    """

    def test_the_reserve_is_exactly_zero(self, qml_root):
        """A merely small reserve still moves every pill, so 0 is the claim."""
        root, _, _ = qml_root
        _show(root, ["hello", "help", "held"])
        assert root.property("dictationEnabled") is False
        assert _bar(root, "predBar.micReserve") == 0

    def test_the_row_sits_where_the_single_reserve_expression_puts_it(self, qml_root):
        """The mic code cannot be deleted to compare, so assert the equivalent.

        With `micReserve` at 0 the live `predRow.x` must equal the expression
        the bar used before a second reserve existed.
        """
        root, _, _ = qml_root
        row = _show(root, ["hello", "help", "held"])
        expect_pills(root)

        before_the_mic = _bar(
            root,
            "Math.max(8, (predBar.width - predBar.clearCtxReserve - predRow.width) / 2)",
        )
        assert row.property("x") == pytest.approx(before_the_mic)

    @pytest.mark.parametrize("width", [720, 940, 1240])
    def test_it_holds_at_every_window_width(self, qml_root, width):
        """Resizing re-runs the whole allocation, so once is not a proof."""
        root, _, _ = qml_root
        root.setProperty("width", width)
        row = _show(root, CROWDED)
        expect_pills(root)

        assert _bar(root, "predBar.micReserve") == 0
        before_the_mic = _bar(
            root,
            "Math.max(8, (predBar.width - predBar.clearCtxReserve - predRow.width) / 2)",
        )
        assert row.property("x") == pytest.approx(before_the_mic)

    def test_the_mic_and_the_transcript_are_both_off_screen(self, qml_root):
        """A zero-width reserve is worthless if the button still renders."""
        root, _, _ = qml_root
        _show(root, ["hello"])
        assert _child(root, "dictationMicButton").property("visible") is False
        assert _child(root, "dictationTranscript").property("visible") is False


class TestTurningDictationOnShiftsThePillsClear:
    """The mirror of TestClearButtonNeverCoversPills at the other end of the
    bar: a control parked at an edge owns a strip the pill row may not enter,
    or the outermost pill renders underneath a button."""

    def test_the_reserve_appears(self, qml_root):
        """Without a reserve the pills are centred over the mic, not beside it."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        _show(root, CROWDED)
        assert _bar(root, "predBar.micReserve") > 0

    @pytest.mark.parametrize(
        "predictions",
        [CROWDED, ["a", "the", "I"], ["hello"], CROWDED[:2]],
        ids=["eight-long", "three-short", "one", "two-long"],
    )
    def test_the_row_starts_right_of_the_mic(self, qml_root, predictions):
        """A pill under the mic is a pill that cannot be tapped."""
        root, warnings, _ = qml_root
        _turn_dictation_on(root)
        row = _show(root, predictions)
        expect_pills(root)

        mic_right = _scene_right(_child(root, "dictationMicButton"))
        assert _scene_left(row) >= mic_right, (
            f"the pill row starts at {_scene_left(row)}px but the mic runs to "
            f"{mic_right}px - the first pill is under the microphone"
        )
        assert _real_warnings(warnings) == []

    def test_the_row_still_clears_the_buttons_at_the_other_end(self, qml_root):
        """Taking width off the left must not push the row into the right end."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        row = _show(root, CROWDED)
        expect_pills(root)
        assert _scene_right(row) <= _right_hand_buttons_left(root)

    def test_it_holds_after_the_window_is_narrowed(self, qml_root):
        """The narrowest window is where two reserves compete for the bar."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        row = _show(root, CROWDED)

        root.setProperty("width", root.property("minimumWidth"))
        QCoreApplication.processEvents()
        _show(root, CROWDED)

        assert _scene_left(row) >= _scene_right(_child(root, "dictationMicButton"))
        assert _scene_right(row) <= _right_hand_buttons_left(root)


@pytest.mark.usefixtures("requires_real_font")
class TestNoPillElidesOnceTheMicTakesSpace:
    """The bar drops low-ranked pills rather than eliding any of them, and
    that guarantee has to survive a second reserve arriving on the left.

    Swept rather than spot-checked, and that is load-bearing. The sibling
    module documents the failure as a knife-edge: it only bites at the widths
    where the row packs tightly enough that the leftover-slack water-fill
    cannot cover the shortfall, so a handful of round numbers misses it
    entirely. The equivalent deficit failed 130 of 260 configurations there
    while passing at 940 px, the obvious width to check by hand.
    """

    def test_no_pill_is_truncated_at_any_width_in_either_view(self, qml_root):
        root, _, _ = qml_root
        _turn_dictation_on(root)
        failures = []
        for compact in (False, True):
            root.setProperty("compactView", compact)
            for width in range(720, 1241, 20):
                root.setProperty("width", width)
                _show(root, CROWDED)
                # Fail-closed: iterating zero pills passes every assertion
                # inside the loop, which is how the sibling module's whole
                # no-elide class ran vacuously for its entire life.
                assert _pill_count(root) > 0, f"no pills at compact={compact} w={width}"
                for label in _truncated(root):
                    failures.append(f"compact={compact} w={width} {label!r}")
        assert not failures, f"{len(failures)} pill(s) elided:\n  " + "\n  ".join(failures[:10])

    def test_survivors_still_clear_both_ends(self, qml_root):
        """Dropping pills is only correct if the ones kept are fully visible."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        root.setProperty("width", root.property("minimumWidth"))
        row = _show(root, CROWDED)
        expect_pills(root)

        assert _scene_left(row) >= _scene_right(_child(root, "dictationMicButton"))
        assert _scene_right(row) <= _right_hand_buttons_left(root)


class TestTheTranscriptReplacesThePills:
    """The two cannot share the row, and a pill shown during a run would be
    about a prefix the user is no longer typing."""

    def test_idle_shows_pills_and_no_transcript(self, qml_root):
        """The inverse half: a banner that never hides is worse than no banner."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        row = _show(root, CROWDED)

        assert root.property("dictationState") == "idle"
        assert row.property("visible") is True
        assert _child(root, "dictationTranscript").property("visible") is False
        assert _pill_count(root) > 0

    def test_listening_shows_the_transcript_and_no_pills(self, qml_root):
        root, _, _ = qml_root
        _turn_dictation_on(root)
        row = _show(root, CROWDED)
        assert _pill_count(root) > 0, "the pills never appeared, so hiding them proves nothing"

        root.setProperty("dictationState", "listening")
        QCoreApplication.processEvents()

        assert _child(root, "dictationTranscript").property("visible") is True
        assert row.property("visible") is False
        assert _pill_count(root) == 0, "the Repeater still built pills during a run"

    def test_the_pills_come_back_when_the_run_ends(self, qml_root):
        """A run that eats the suggestion bar permanently is a broken keyboard."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        row = _show(root, CROWDED)

        root.setProperty("dictationState", "listening")
        QCoreApplication.processEvents()
        root.setProperty("dictationState", "idle")
        _show(root, CROWDED)

        assert row.property("visible") is True
        assert _child(root, "dictationTranscript").property("visible") is False
        assert _pill_count(root) > 0


class TestTheTranscriptStaysInsideTheBar:
    """The banner spans the space the pills normally occupy, which means it
    inherits their obligation to both edges: the mic on the left and the
    Snippets / clear-context pair on the right."""

    LONG = (
        "this is a long spoken phrase that has not been typed into the "
        "application yet and would happily run off both ends of the bar"
    )

    def test_it_never_overlaps_either_end(self, qml_root):
        root, _, _ = qml_root
        _turn_dictation_on(root)
        root.setProperty("dictationTranscript", self.LONG)
        root.setProperty("dictationState", "listening")
        QCoreApplication.processEvents()

        banner = _child(root, "dictationTranscript")
        assert banner.property("visible") is True
        assert banner.property("width") > 0, "a zero-width banner clears everything"

        mic_right = _scene_right(_child(root, "dictationMicButton"))
        assert _scene_left(banner) >= mic_right
        assert _scene_right(banner) <= _right_hand_buttons_left(root)

    def test_it_holds_in_the_narrowest_window(self, qml_root):
        """Both reserves bite hardest where there is least bar to divide."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        root.setProperty("dictationTranscript", self.LONG)
        root.setProperty("dictationState", "listening")
        root.setProperty("width", root.property("minimumWidth"))
        QCoreApplication.processEvents()

        banner = _child(root, "dictationTranscript")
        assert _scene_left(banner) >= _scene_right(_child(root, "dictationMicButton"))
        assert _scene_right(banner) <= _right_hand_buttons_left(root)


class TestTheTranscriptElidesFromTheLeft:
    """While speaking, the tail is what the user is checking, so the newest
    words are the ones that must survive. This is the opposite of every other
    elide in Main.qml, which makes it the one most likely to be "corrected"
    back to the default by someone tidying up."""

    def test_the_elide_mode_is_left(self, qml_root):
        root, _, _ = qml_root
        _turn_dictation_on(root)
        root.setProperty("dictationTranscript", TestTheTranscriptStaysInsideTheBar.LONG)
        root.setProperty("dictationState", "listening")
        QCoreApplication.processEvents()

        text = _child(root, "dictationTranscriptText")
        assert _evaluate(text, "elide === Text.ElideLeft") is True, (
            "the transcript elides from somewhere other than the left, so the "
            "words the user just said are the ones being thrown away"
        )

    def test_the_tail_is_what_survives_a_long_phrase(self, qml_root):
        """States the consequence, not the enum, so an equivalent break shows.

        Asserted on `truncated` rather than on `contentWidth <= width`: once
        a Text elides, contentWidth measures the shortened string, so that
        comparison fits by construction and can never fail.
        """
        root, _, _ = qml_root
        _turn_dictation_on(root)
        root.setProperty("width", root.property("minimumWidth"))
        root.setProperty("dictationTranscript", TestTheTranscriptStaysInsideTheBar.LONG * 3)
        root.setProperty("dictationState", "listening")
        QCoreApplication.processEvents()

        text = _child(root, "dictationTranscriptText")
        assert text.property("truncated") is True, (
            "the phrase was not long enough to elide here, so this test is "
            "not exercising the elide at all"
        )
        assert _evaluate(text, "elide === Text.ElideLeft") is True


class TestTheTranscriptNeverAutoDetectsHtml:
    """The transcript is whatever the user said, arriving from a third-party
    recogniser, so it is exactly as untrusted as an imported pack's word
    list. QML's `Text` defaults to `Text.AutoText`, which sniffs the string
    for markup, so an `<img src=...>` in a transcript could fire an outbound
    request purely from being displayed."""

    def test_the_text_format_is_plain(self, qml_root):
        root, _, _ = qml_root
        _turn_dictation_on(root)
        root.setProperty("dictationTranscript", "<img src='http://example.invalid/x'>")
        root.setProperty("dictationState", "listening")
        QCoreApplication.processEvents()

        text = _child(root, "dictationTranscriptText")
        # QObject.property("textFormat") raises "Can't find converter for
        # 'QQuickText::TextFormat'": PySide6 has no converter for that
        # internal enum, so compare against the QML constant in JS instead.
        assert _evaluate(text, "textFormat === Text.PlainText") is True, (
            "the transcript is not forced to Text.PlainText - markup in a "
            "recognised phrase could auto-render as HTML"
        )


class TestTheBarIsNeverBlankDuringARun:
    """The banner has taken the pills' place by the time the first word is
    recognised, so with nothing to show yet it has to say what is happening
    or the bar reads as broken."""

    def _placeholder_for(self, root, state: str) -> str:
        root.setProperty("dictationTranscript", "")
        root.setProperty("dictationState", state)
        QCoreApplication.processEvents()
        return _child(root, "dictationTranscriptText").property("text")

    def test_listening_with_nothing_said_yet_still_shows_something(self, qml_root):
        root, _, _ = qml_root
        _turn_dictation_on(root)
        assert self._placeholder_for(root, "listening").strip() != ""

    def test_connecting_and_listening_do_not_read_the_same(self, qml_root):
        """Connecting must look busy without looking like recording, or the
        user clicks again and starts a second run."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        connecting = self._placeholder_for(root, "connecting")
        listening = self._placeholder_for(root, "listening")
        assert connecting.strip() != ""
        assert connecting != listening

    def test_a_real_phrase_replaces_the_placeholder(self, qml_root):
        """The inverse: a placeholder that never gets out of the way is worse."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        self._placeholder_for(root, "listening")
        root.setProperty("dictationTranscript", "hello world")
        QCoreApplication.processEvents()
        assert _child(root, "dictationTranscriptText").property("text") == "hello world"


class TestTheTitleBarMirrorCoversTheCollapsedBar:
    """The suggestion bar collapses to zero height when suggestions are
    switched off, taking everything in it. An unrelated setting must not be
    able to remove the only way into a feature, which is the precedent
    `snippetsTitleBarButton` set and the hole the clear-context button
    still has."""

    def test_the_mirror_appears_when_the_bar_collapses(self, qml_root):
        root, _, _ = qml_root
        _turn_dictation_on(root)
        root.setProperty("suggestionsEnabled", False)
        QCoreApplication.processEvents()

        mirror = _child(root, "dictationTitleBarButton")
        assert mirror.property("visible") is True
        assert mirror.property("width") > 0, "a visible zero-width button is not a target"

    def test_the_snippets_mirror_appears_with_it(self, qml_root):
        """Same hole, same fix; asserted together so one cannot regress alone."""
        root, _, _ = qml_root
        root.setProperty("suggestionsEnabled", False)
        QCoreApplication.processEvents()

        mirror = _child(root, "snippetsTitleBarButton")
        assert mirror.property("visible") is True
        assert mirror.property("width") > 0

    def test_there_are_never_two_entry_points_at_once(self, qml_root):
        """A mirror showing beside the real button is a second thing to learn."""
        root, _, _ = qml_root
        _turn_dictation_on(root)
        root.setProperty("suggestionsEnabled", True)
        QCoreApplication.processEvents()

        assert _child(root, "dictationMicButton").property("visible") is True
        assert _child(root, "dictationTitleBarButton").property("visible") is False
        assert _child(root, "snippetsTitleBarButton").property("visible") is False

    def test_the_mirror_stays_away_when_the_feature_is_off(self, qml_root):
        """Switching suggestions off must not conjure a mic nobody asked for."""
        root, _, _ = qml_root
        root.setProperty("suggestionsEnabled", False)
        QCoreApplication.processEvents()

        assert root.property("dictationEnabled") is False
        assert _child(root, "dictationTitleBarButton").property("visible") is False


class TestTheMicGreysRatherThanLying:
    """Enabled but unusable is a real state: the toggle is on and no API
    key has been entered yet. It has to read as unavailable rather than as
    broken, so the button greys out and explains itself in its tooltip
    instead of failing on click."""

    def test_it_is_dimmed_without_a_key(self, qml_root):
        root, _, _ = qml_root
        _turn_dictation_on(root, configured=False)
        mic = _child(root, "dictationMicButton")
        assert mic.property("visible") is True
        assert mic.property("opacity") < 0.75, (
            "an unconfigured mic looks exactly like a working one, so the "
            "only way to find out is to click it and have nothing happen"
        )

    def test_it_is_fully_opaque_once_configured(self, qml_root):
        """The inverse: a permanently grey button reads as permanently broken."""
        root, _, _ = qml_root
        _turn_dictation_on(root, configured=True)
        assert _child(root, "dictationMicButton").property("opacity") == pytest.approx(1.0)

    def test_a_busy_pulse_does_not_eat_the_opacity_binding(self, qml_root):
        """The `connecting` pulse must hand `opacity` back when it stops.

        Written as `SequentialAnimation on opacity`, a QML animation takes
        ownership of the property it animates and never returns it, so the
        first connect would destroy the binding below and leave the button
        parked wherever the loop happened to be, which for half of each
        cycle is nearly transparent. The pulse therefore drives a separate
        `pulse` property that carries no binding, and `opacity` multiplies
        it in. This test runs a real pulse and then checks the binding is
        still live, which is the only way to tell the two forms apart.
        """
        root, _, _ = qml_root
        _turn_dictation_on(root, configured=True)
        mic = _child(root, "dictationMicButton")

        # Stop mid-cycle, where the difference is largest: the broken form
        # strands the button at whatever the loop was passing through.
        root.setProperty("dictationState", "connecting")
        _wait_ms(430)
        root.setProperty("dictationState", "idle")
        _wait_ms(120)
        assert mic.property("opacity") == pytest.approx(1.0), (
            "the pulse left the button dimmed after the run ended"
        )

        # The real proof that the binding survived: change what it depends
        # on and see the value follow.  An opacity that merely reads 1.0
        # could be a dead property that happens to hold the right number.
        root.setProperty("dictationConfigured", False)
        QCoreApplication.processEvents()
        assert mic.property("opacity") < 0.75, (
            "opacity no longer tracks dictationConfigured, so the animation "
            "took the binding with it"
        )

    def test_the_title_bar_mirror_greys_the_same_way(self, qml_root):
        """Two entry points to one feature must not disagree about its state."""
        root, _, _ = qml_root
        _turn_dictation_on(root, configured=False)
        root.setProperty("suggestionsEnabled", False)
        QCoreApplication.processEvents()

        mirror = _child(root, "dictationTitleBarButton")
        assert mirror.property("visible") is True
        assert mirror.property("opacity") < 0.75


class TestPrivacyModeTakesTheTranscriptWithIt:
    """Privacy mode has to mean "stop doing this", not "stop starting new
    ones". The Python side cancels the run rather than stopping it, and the
    banner must not go on rendering what was being said next to the
    "Learning paused" indicator that replaced the pills."""

    def test_the_transcript_is_hidden_in_privacy_mode(self, qml_root):
        root, _, bridge = qml_root
        _turn_dictation_on(root)
        root.setProperty("dictationTranscript", "my password is")
        root.setProperty("dictationState", "listening")
        QCoreApplication.processEvents()
        assert _child(root, "dictationTranscript").property("visible") is True

        bridge.setPrivacyMode(True)
        QCoreApplication.processEvents()

        assert root.property("privacyMode") is True
        assert _child(root, "dictationTranscript").property("visible") is False

    def test_the_pills_stay_hidden_too(self, qml_root):
        """Neither surface may fill the gap the other left."""
        root, _, bridge = qml_root
        _turn_dictation_on(root)
        root.setProperty("dictationState", "listening")
        bridge.setPrivacyMode(True)
        QCoreApplication.processEvents()

        assert _child(root, "predictionRow").property("visible") is False
        assert _pill_count(root) == 0
