"""Headless QML tests for the research-study window and its interaction
with the suggestion bar.

Same harness as `tests/test_qml_snippets.py`: load the real `qml/Main.qml`
against a real `KeyboardBridge` and a real `StudyBridge` under the
`offscreen` platform plugin, and drive the loaded objects directly. Python
cannot reach any of this on its own -- which view is showing, whether the
pill row goes empty, whether the bar's height moves -- because all of it is
QML state and QML bindings.

The load-bearing test in this file is
`TestTheStudyPredictionsOffCondition`, which is not really a study-window
test at all: it guards `qml/Main.qml`'s own pill-row Repeater against a
regression in the *other* direction. `KeyboardBridge` and `StudyBridge` are
Python and already covered by `tests/test_keyboard_bridge.py` /
`tests/test_study_bridge.py`; nothing here duplicates that. What only a
loaded `Main.qml` can prove is that `study.suppressPredictions` reaches the
Repeater's `model:` binding and that doing so never touches
`predBar`'s own height.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

# Must be set before QGuiApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# See the note in test_qml_compact_view.py / test_qml_snippets.py: QtGui
# dlopens the host's libEGL / libGL at module scope, and an ImportError
# there aborts the whole run as a collection error rather than failing only
# this module.
try:
    from PySide6.QtCore import (  # noqa: E402
        QCoreApplication,
        QObject,
        QSettings,
        Qt,
        QUrl,
    )
    from PySide6.QtGui import QGuiApplication  # noqa: E402

    # Imported for the side effect: without QQuickItem somewhere in the
    # module, reading an item's `contentItem` raises "Can't find converter
    # for 'QQuickItem*'". Without QQuickWindow the root comes back as a bare
    # QWindow with no grabWindow(), which `_relayout` depends on.
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
    from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402,F401
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from src.study_bridge import StudyBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)]


def _settle(passes: int = 12) -> None:
    """Flush Qt Quick's delegate creation and bindings."""
    for _ in range(passes):
        QCoreApplication.processEvents()


def _relayout(root) -> None:
    """Force a layout pass. Qt Quick Layouts recompute in a polish step the
    offscreen window never runs on its own, so a height read right after a
    property change can still be the value from before the change."""
    _settle()
    root.grabWindow()
    _settle()


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation; these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def study_window(qapp, tmp_path):
    """Load Main.qml and hand back (root, studyWindow, studyBridge, bridge, warnings)."""
    warnings: list[str] = []
    QSettings(TEST_ORG, TEST_APP).clear()

    # Disarm the startup update check -- see test_qml_compact_view.py, where
    # the live HTTPS request from a daemon thread outliving the fixture
    # surfaces as a hard crash.
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()

    # config_dir is passed explicitly rather than relying only on the
    # autouse `_stay_off_the_real_config_dir` fixture: this store also
    # writes a trial log, and a study test is exactly the kind of test
    # that would otherwise leave files under the developer's own
    # %APPDATA%/alpha-osk if that guard were ever loosened.
    study = StudyBridge(
        keyboard=bridge,
        predictor=bridge._predictor,
        config_dir=tmp_path / "study",
    )

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    install_context_properties(engine, bridge, study=study)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    window = root.findChild(QObject, "studyWindow")
    assert window is not None, "studyWindow not found in the loaded QML"
    try:
        yield root, window, study, bridge, warnings
    finally:
        del engine


def _find_named(window, name: str):
    """First item named *name* in the window's visual tree, or None.

    `findChild` does not reach these: items declared in a Window's body are
    reparented under its contentItem, so the QObject parent chain a
    QObject-level search walks is not the tree they live in. Same helper as
    tests/test_qml_snippets.py.
    """
    found = []

    def walk(item):
        if item is None or found:
            return
        for child in item.childItems():
            if child.objectName() == name:
                found.append(child)
                return
            walk(child)

    walk(window.property("contentItem"))
    return found[0] if found else None


def _pill_texts(root) -> list:
    """Every pill label Text item, found through the VISUAL item tree.

    `findChildren` returns an empty list here: a Repeater's delegates are
    re-parented as visual children of the Repeater's parent item, not QObject
    children of the Repeater itself. See tests/test_qml_prediction_bar.py's
    `_pill_texts`, which this mirrors.
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
    return len(_pill_texts(root))


def _unwrap(value):
    """A QML `var` holding a JS object/array arrives as a QJSValue, which
    Python can neither index nor iterate. `.toVariant()` converts it; a
    value that already came through as a plain Python object (str, bool,
    list) has no such method and is returned unchanged."""
    return value.toVariant() if hasattr(value, "toVariant") else value


def _agree_and_reach_a_trial(window, study, enrolment_index: int) -> None:
    """Drive the window exactly the way a participant would: consent,
    background, instructions, Start. `enrolment_index` picks the Williams
    order (see src/study/session.py::williams_orders), which is how the
    predictions-off test below lands its very first practice step on the
    ALPHA_OFF condition without reaching into the session directly."""
    window.setProperty("enrolNumber", enrolment_index)
    window.agreeToConsent()
    assert study.hasConsented()
    window.setProperty("bgPointer", "Mouse")
    window.setProperty("bgExperience", "A few times")
    window.submitBackground()
    window.beginSession()


class TestTheWindowLoads:
    def test_it_loads_with_no_qml_warnings(self, study_window):
        _root, _window, _study, _bridge, warnings = study_window
        assert _real_warnings(warnings) == [], "\n".join(_real_warnings(warnings))

    def test_the_window_is_a_standalone_component(self, study_window):
        """Pins the extraction into its own file: QML names a component's
        generated class after its file, so this fails if StudyWindow were
        ever inlined back into Main.qml."""
        _root, window, _study, _bridge, _w = study_window
        class_name = window.metaObject().className()
        assert class_name.startswith("StudyWindow"), class_name

    def test_it_carries_the_no_focus_flag_and_object_name(self, study_window):
        root, window, _study, _bridge, _w = study_window
        assert window.objectName() == "studyWindow"
        flags = int(window.property("flags"))
        assert flags & int(Qt.WindowDoesNotAcceptFocus), (
            "studyWindow must never accept OS focus, or clicking it would "
            "steal focus from the app the participant is typing into"
        )
        # Bound from Main.qml's root, not restated as a literal here -- see
        # "Who rounds the window corners" in CLAUDE.md.
        assert window.property("selfRoundedCorners") == root.property("selfRoundedCorners")


class TestOnlyOneViewIsEverShowing:
    """Nine sibling ColumnLayouts are each gated on `currentView` alone, so
    a botched condition shows two at once rather than failing loudly --
    the same shape test_qml_snippets.py guards for its three views."""

    # (the value `currentView` actually takes, the objectName of the
    # ColumnLayout gated on it) -- the two are spelled differently on
    # purpose (a state name versus a Text-tree lookup name), and conflating
    # them is exactly the mistake this test has to not make.
    VIEW_STATES = (
        ("consent", "studyConsentView"),
        ("background", "studyBackgroundView"),
        ("instructions", "studyInstructionsView"),
        ("trial", "studyTrialView"),
        ("stopConfirm", "studyStopConfirmView"),
        ("workload", "studyWorkloadView"),
        ("break", "studyBreakView"),
        ("review", "studyReviewView"),
        ("done", "studyDoneView"),
    )

    def test_every_view_exists_and_they_are_mutually_exclusive(self, study_window):
        _root, window, _study, _bridge, _w = study_window
        items = {name: _find_named(window, name) for _state, name in self.VIEW_STATES}
        for name, item in items.items():
            assert item is not None, f"{name} not found in StudyWindow.qml"

        for state, target_name in self.VIEW_STATES:
            window.setProperty("currentView", state)
            for name, item in items.items():
                assert item.property("visible") == (name == target_name), (
                    f"with currentView={state!r}, {name}.visible was {item.property('visible')!r}"
                )


class TestTheConsentThroughTrialFlow:
    """The window's own JS functions, driven the way a tap on each button
    would drive them, reach a live trial with no bridge signal missed."""

    def test_agreeing_then_answering_background_reaches_a_trial(self, study_window):
        _root, window, study, _bridge, _w = study_window
        window.syncView()
        assert window.property("currentView") == "consent"

        window.setProperty("enrolNumber", 0)
        window.agreeToConsent()
        assert window.property("currentView") == "background"

        window.setProperty("bgPointer", "Mouse")
        window.setProperty("bgExperience", "A few times")
        window.submitBackground()
        assert window.property("currentView") == "instructions"

        window.beginSession()
        assert window.property("startError") == ""
        assert window.property("currentView") == "trial"
        assert study.capturing is True

    def test_stop_then_resume_returns_to_the_same_trial(self, study_window):
        _root, window, study, _bridge, _w = study_window
        _agree_and_reach_a_trial(window, study, enrolment_index=0)
        phrase = _unwrap(window.property("step"))["phrase"]

        window.requestStop()
        assert window.property("currentView") == "stopConfirm"
        assert study.capturing is False

        window.resumeFromStop()
        assert window.property("currentView") == "trial"
        assert study.capturing is True
        assert _unwrap(window.property("step"))["phrase"] == phrase, (
            "Stop must not advance the session -- the same phrase should "
            "still be current after Resume"
        )


class TestTheStudyPredictionsOffCondition:
    """study.suppressPredictions blanks the pill row without moving the bar.

    This guards protocol section 5.1. The study's predictions-off condition
    has to look, in every way except the pills themselves, identical to
    predictions being on: using the `suggestionsEnabled` setting instead
    would collapse the suggestion bar to zero height and move every key up
    with it, which would confound the very thing under test (does
    prediction help) with a change in key geometry, in a pointing task, on
    the population whose pointing accuracy is what makes that geometry
    change matter. A bar that moves between conditions invalidates the
    study's own comparison as surely as a bar that fails to go empty.
    """

    def test_the_pills_disappear_but_the_bar_never_moves(self, study_window):
        root, window, study, _bridge, _w = study_window
        _relayout(root)

        pred_bar = root.findChild(QObject, "predictionBar")
        assert pred_bar is not None, "predictionBar not found in Main.qml"

        root.setProperty("predictions", ["hello", "help", "held"])
        _settle()
        assert _pill_count(root) > 0, (
            "no pills rendered before suppression; the rest of this test "
            "would pass vacuously against an already-empty bar"
        )
        # Reach a live predictions-off trial exactly as a participant would:
        # enrolment index 0 gives the Williams order (0, 1) for a 2-condition
        # design, i.e. ALPHA_ON first; 1 gives (1, 0), ALPHA_OFF first, so the
        # very first practice step already has predictions=False.
        _agree_and_reach_a_trial(window, study, enrolment_index=1)
        assert study.capturing is True
        assert study.suppressPredictions is True, (
            "enrolment index 1 should have put the participant's first "
            "block on the predictions-off condition; see "
            "src/study/session.py::williams_orders"
        )

        # Predictions keep being computed and handed to the bar exactly as
        # they would outside the study (that is the point: the engine must
        # not know it is being studied), so push some again and confirm the
        # Repeater itself, not just the underlying list, is what goes empty.
        root.setProperty("predictions", ["hello", "help", "held"])
        _settle()
        _relayout(root)
        assert _pill_count(root) == 0, (
            "pills still rendered while study.suppressPredictions is true"
        )
        height_suppressed = pred_bar.property("height")

        # Measure the two heights with the study window ALREADY OPEN and a
        # trial ALREADY RUNNING, so suppression is the only thing that differs
        # between them. Comparing against a height taken before the study
        # opened measures the study window's own effect on the layout too,
        # which drifts the bar by a fraction of a pixel and says nothing about
        # the property under test.
        study.abandonTrial()
        _settle()
        assert study.suppressPredictions is False
        root.setProperty("predictions", ["hello", "help", "held"])
        _settle()
        _relayout(root)
        assert _pill_count(root) > 0, (
            "pills did not come back after suppression ended, so the "
            "comparison below would be between two blank bars"
        )
        height_shown = pred_bar.property("height")

        # A tolerance, deliberately, and a tight one. Qt Quick lays out in
        # floats and the row's own rounding moves the bar by hundredths of a
        # pixel between frames, which is not what this test is about. The
        # failure it guards is the bar COLLAPSING, which is the full pill
        # height (about 49 px here), so a one-pixel window cannot hide it.
        # This mirrors TestEveryGridRowIsPixelFlush, which asserts widths
        # exactly and origins to within a pixel for the same reason.
        assert abs(height_shown - height_suppressed) < 1.0, (
            "the suggestion bar's height changed when predictions were "
            "suppressed for the study: it must only ever go empty, never "
            "shrink, or the predictions-on/off comparison is confounded "
            f"with a change in key geometry ({height_suppressed} vs "
            f"{height_shown})"
        )
        assert height_suppressed > 1.0, (
            "the bar collapsed entirely, which is exactly the "
            "suggestionsEnabled behaviour this must not reproduce"
        )
