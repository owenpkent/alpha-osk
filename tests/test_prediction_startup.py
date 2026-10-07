"""The keyboard remains usable while a separate thread builds predictions."""

from __future__ import annotations

import gc
import os
import sys
import threading
import time
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import (
    QCoreApplication,
    QEvent,
    QMetaObject,
    QObject,
    QSettings,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAccessible, QAccessibleActionInterface, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from src import keyboard_bridge as kb
from src.prediction import loader as loader_module
from src.prediction.hybrid_predictor import HybridPredictor, LoadAborted
from src.prediction.loader import _BUILD_SWITCH_INTERVAL_S, PredictionLoader
from src.prediction.null_predictor import NullPredictor
from src.study.capture import RecordingSynthesizer
from src.study_bridge import StudyBridge
from tests.qml_context import install_context_properties
from tests.qt_settings_scope import TEST_APP, TEST_ORG


class PredictorDouble(QObject):
    predictionsReady = Signal(list)
    predictionsRefined = Signal(list)
    modelLoading = Signal(bool)
    llmAvailableChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.built_on = threading.get_ident()
        self.enable_llm = False
        self.llm_available = False
        self.set_explicit_filter = Mock()
        self.set_merge_strategy = Mock()
        self.set_key_positions = Mock()
        self.predict_with_refinement = Mock(side_effect=self._predict)
        self.predict_tokens = Mock(return_value=[])
        self.predict_email_domains = Mock(return_value=[])
        self.get_available_packs = Mock(return_value=[{"id": "care", "name": "Care", "words": 3}])
        self.get_enabled_packs = Mock(return_value=["care"])
        self.get_user_packs_dir = Mock(return_value="/test/packs")
        self.save = Mock()
        self.learn = Mock(return_value=[])
        self.frozen_learning = Mock()

    def _predict(self, context, **kwargs):
        self.predictionsReady.emit(["hello"])


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG
    assert QCoreApplication.applicationName() == TEST_APP
    return app


def wait_for(condition, timeout=5):
    deadline = time.monotonic() + timeout
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(5)
    assert condition(), "startup did not reach the expected state"


def wait_for_job(*jobs):
    """Wait for each build to finish, then for its worker thread to exit.

    ``done`` is set before the worker's last emit and its thread teardown, so
    a test that returns on ``done`` alone hands the next test a worker still
    unwinding (a Python-created QObject's adopted thread going away) while
    the next test is already creating QObjects.  On a starved Windows runner
    that overlap was a worker crash and a build that never finished; joining
    keeps each test's threads inside the test.
    """
    for job in jobs:
        wait_for(lambda: job.done)
    for job in jobs:
        if job.thread is not None:
            job.thread.join(5)
            assert not job.thread.is_alive(), "the build worker did not exit"


@pytest.fixture
def pending(qapp, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    synth = RecordingSynthesizer()
    monkeypatch.setattr(kb, "create_key_synthesizer", lambda: synth)
    jobs = []

    def build(abort):
        entered.set()
        # Poll the cancel the way the real constructor does between its
        # phases, so a shutdown in a test ends the worker instead of
        # holding it on the release event.
        deadline = time.monotonic() + 10
        while not release.is_set():
            if abort():
                raise LoadAborted()
            if time.monotonic() > deadline:
                raise RuntimeError("test did not release the prediction worker")
            release.wait(0.005)
        return PredictorDouble()

    def loader(factory, parent):
        value = PredictionLoader(build, parent)
        jobs.append(value._job)
        return value

    monkeypatch.setattr(kb, "PredictionLoader", loader)
    bridge = kb.KeyboardBridge(defer_predictions=True)
    monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 0)
    try:
        yield bridge, synth, entered, release
    finally:
        bridge.shutdown()
        release.set()
        for job in jobs:
            wait_for_job(job)
        bridge.deleteLater()
        QCoreApplication.sendPostedEvents(None, 0)


def test_typing_and_event_loop_work_before_predictions_then_use_current_context(pending):
    bridge, synth, entered, release = pending
    bridge.startPredictionLoading()
    assert entered.wait(2)
    bridge.startPredictionLoading()  # Duplicate starts must not create a second engine.
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    for char in "hex":
        bridge.pressKey(char)
    bridge.pressSpecialKey("backspace")
    bridge.pressKey("l")
    wait_for(lambda: heartbeat)
    assert synth.transcript == "hel"
    assert bridge.predictionStatus == "loading"
    assert isinstance(bridge._predictor, NullPredictor)

    bridge.setFilterExplicit(False)
    bridge.setMergeStrategy("rrf")
    bridge.setPredictionCount(5)
    bridge.setLayout("azerty")
    release.set()
    wait_for(lambda: bridge.predictionStatus == "ready")
    predictor = bridge._predictor
    assert predictor.built_on != threading.get_ident()
    assert predictor.thread() == QThread.currentThread()
    assert predictor.parent() is bridge
    predictor.set_explicit_filter.assert_called_once_with(False)
    predictor.set_merge_strategy.assert_called_once_with("rrf")
    predictor.set_key_positions.assert_called_once()
    predictor.predict_with_refinement.assert_called_once_with("hel", n=5, offsets=[(0.0, 0.0)] * 3)
    assert bridge.predictions == ["hello"]


@pytest.mark.parametrize("privacy", [False, True])
def test_handoff_does_not_restore_context_cleared_during_loading(pending, privacy):
    bridge, synth, entered, release = pending
    bridge.startPredictionLoading()
    assert entered.wait(2)
    for char in "secret":
        bridge.pressKey(char)
    if privacy:
        bridge.setPrivacyMode(True)
        bridge.pressKey("x")
    else:
        bridge.resetContext()
    release.set()
    wait_for(lambda: bridge.predictionStatus == "ready")
    assert bridge._current_word == ""
    assert bridge._context_buffer == ""
    if privacy:
        bridge._predictor.predict_with_refinement.assert_not_called()
        assert bridge.predictions == []
        assert synth.transcript == "secretx"
    else:
        bridge._predictor.predict_with_refinement.assert_called_once_with("", n=8, offsets=None)


def test_closing_during_load_does_not_save_or_publish_a_partial_model(pending, tmp_path):
    bridge, _, entered, release = pending
    from src.platform import get_model_dir

    saved = get_model_dir() / "ngram_model.json"
    saved.write_text('{"sentinel": true}', encoding="utf-8")
    ready = []
    bridge.predictionEngineReady.connect(ready.append)
    bridge.startPredictionLoading()
    assert entered.wait(2)
    job = bridge._prediction_loader._job
    bridge.savePredictionModel()
    bridge.shutdown()
    release.set()
    wait_for_job(job)
    assert ready == []
    assert isinstance(bridge._predictor, NullPredictor)
    assert saved.read_text(encoding="utf-8") == '{"sentinel": true}'


@pytest.mark.parametrize("change", ["window", "password"])
def test_handoff_rechecks_focus_and_password_before_the_next_poll(pending, monkeypatch, change):
    bridge, _, entered, release = pending
    bridge.startPredictionLoading()
    assert entered.wait(2)
    bridge.pressKey("h")
    if change == "window":
        bridge._last_foreground_hwnd = 1
        monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 2)
        monkeypatch.setattr(kb, "focused_element_token", lambda: None)
    else:
        monkeypatch.setattr(kb, "is_password_field", lambda: True)
        bridge._last_sync_password_check = time.time() + 60
    release.set()
    wait_for(lambda: bridge.predictionStatus == "ready")
    assert bridge._current_word == ""
    assert bridge._context_buffer == ""
    bridge._predictor.learn.assert_not_called()
    if change == "password":
        assert bridge.privacyMode
        bridge._predictor.predict_with_refinement.assert_not_called()
    else:
        bridge._predictor.predict_with_refinement.assert_called_once_with("", n=8, offsets=None)


def test_destroying_loader_parent_during_construction_cancels_publication(qapp):
    entered = threading.Event()
    release = threading.Event()

    def build(abort):
        entered.set()
        assert release.wait(5)
        return PredictorDouble()

    parent = QObject()
    loader = PredictionLoader(build, parent)
    job = loader._job
    ready = []
    loader.loaded.connect(ready.append)
    loader.start()
    try:
        assert entered.wait(2)
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    finally:
        release.set()
        wait_for_job(job)
    assert ready == []


def test_failed_load_can_be_retried_while_typing_still_works(pending, monkeypatch):
    bridge, synth, _, _ = pending
    attempts = []

    def build(abort):
        attempts.append(True)
        if len(attempts) == 1:
            raise RuntimeError("intentional startup failure")
        return PredictorDouble()

    monkeypatch.setattr(
        kb, "PredictionLoader", lambda factory, parent: PredictionLoader(build, parent)
    )
    bridge.startPredictionLoading()
    wait_for(lambda: bridge.predictionStatus == "error")
    bridge.pressKey("h")
    assert synth.transcript == "h"
    bridge.startPredictionLoading()
    wait_for(lambda: bridge.predictionStatus == "ready")
    assert len(attempts) == 2
    bridge._predictor.predict_with_refinement.assert_called_once_with(
        "h", n=8, offsets=[(0.0, 0.0)]
    )


def test_data_changes_are_refused_until_loading_finishes(pending, tmp_path):
    bridge, _, _, _ = pending
    bridge.clearUserData()
    bridge.reloadDictionary()
    assert bridge.importUserData(str(tmp_path / "missing.zip"))
    assert bridge.exportUserData(str(tmp_path / "backup.zip"))
    assert not (tmp_path / "backup.zip").exists()
    assert not bridge.importTextFile(str(tmp_path / "words.txt"))
    assert bridge.getLearnedTokens() == []
    assert bridge.getVisualizationData()["words"] == []
    assert not bridge.forgetToken("12345")


def test_study_waits_for_the_real_predictor(pending, tmp_path):
    bridge, _, _, _ = pending
    study = StudyBridge(keyboard=bridge, predictor=bridge._predictor, config_dir=tmp_path)
    study.recordConsent("A", 0)
    assert not study.startSession()
    predictor = PredictorDouble()
    predictor.frozen_learning.return_value = nullcontext()
    study.set_predictor(predictor)
    assert study.startSession()
    predictor.frozen_learning.assert_called_once()
    study.shutdown()


def test_window_displays_loading_then_suggestions_without_covering_keys(pending, qapp):
    bridge, synth, entered, release = pending
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.clear()
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "qml" / "Main.qml")))
    try:
        assert engine.rootObjects(), warnings
        root = engine.rootObjects()[0]
        status = root.findChild(QObject, "predictionStartupStatus")
        spinner = root.findChild(QObject, "predictionStartupSpinner")
        wait_for(lambda: root.isVisible())
        assert status.property("visible")
        assert spinner.property("running")
        bridge._prediction_load_failed()
        retry = root.findChild(QObject, "predictionStartupRetry")
        assert retry.property("visible")
        assert not spinner.property("running")
        assert QMetaObject.invokeMethod(retry, "clicked")
        assert entered.wait(2)
        assert not retry.property("visible")
        assert spinner.property("running")
        bridge.pressKey("h")
        assert synth.transcript == "h"
        bridge.setPrivacyMode(True)
        assert not status.property("visible")
        assert not spinner.property("running")
        bridge.setPrivacyMode(False)
        release.set()
        wait_for(lambda: bridge.predictionStatus == "ready")
        assert not status.property("visible")
        assert not spinner.property("running")
        assert root.property("predictionsArePresent")
        packs = root.findChild(QObject, "vocabularyPackSettings")
        assert packs.property("availablePacks") == [{"id": "care", "name": "Care", "words": 3}]
        assert packs.property("enabledPacks") == ["care"]
        assert packs.property("packsDir") == "/test/packs"
        assert warnings == []
    finally:
        bridge.shutdown()
        del engine


def test_an_assistive_clients_press_action_retries_the_load(pending, qapp):
    """A switch scanner or screen reader invokes Press, never the MouseArea.

    The sibling test calls the `clicked` signal directly, which an
    assistive client cannot do; this one goes through the accessibility
    action interface, which is the only route such a client has.
    """
    bridge, _, entered, release = pending
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.clear()
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "qml" / "Main.qml")))
    try:
        assert engine.rootObjects(), warnings
        root = engine.rootObjects()[0]
        wait_for(lambda: root.isVisible())
        bridge._prediction_load_failed()
        retry = root.findChild(QObject, "predictionStartupRetry")
        assert retry.property("visible")
        assert bridge.predictionStatus == "error"
        QAccessible.setActive(True)
        iface = QAccessible.queryAccessibleInterface(retry)
        assert iface is not None
        actions = iface.actionInterface()
        assert actions is not None
        actions.doAction(QAccessibleActionInterface.pressAction())
        assert entered.wait(2)
        assert bridge.predictionStatus == "loading"
    finally:
        release.set()
        bridge.shutdown()
        del engine


def test_a_failed_hand_over_is_a_failed_load_not_a_hung_one(pending):
    """Every exit from the build marks the job done.

    The first version set ``done`` on the success path and on a factory
    exception only, so anything else (here: moveToThread refusing) left the
    bar on "Loading suggestions..." for the life of the process, with no
    Retry and no way back short of a restart.
    """
    bridge, _, entered, release = pending

    class Unmovable(PredictorDouble):
        def moveToThread(self, thread):  # noqa: N802 - Qt name
            raise RuntimeError("target thread is gone")

    def build(abort):
        entered.set()
        assert release.wait(5)
        return Unmovable()

    bridge._prediction_loader = None
    loader = PredictionLoader(build, bridge)
    bridge._prediction_loader = loader
    loader.loaded.connect(bridge._finish_prediction_load)
    loader.failed.connect(bridge._prediction_load_failed)
    bridge._prediction_status = "loading"
    loader.start()
    assert entered.wait(2)
    job = loader._job
    release.set()
    wait_for_job(job)
    wait_for(lambda: bridge.predictionStatus == "error")
    assert bridge._prediction_loader is None, "Retry must be possible again"


def test_cancelling_stops_the_build_at_its_next_checkpoint(qapp):
    """A cancel ends the CPU work, not only the publication.

    Without this, quitting during the load left the worker constructing
    QObjects while Qt tore down.  The factory polls the abort callable the
    way HybridPredictor does between its phases.
    """
    entered = threading.Event()

    def build(abort):
        entered.set()
        deadline = time.monotonic() + 5
        while not abort():
            assert time.monotonic() < deadline, "cancel never reached the worker"
            time.sleep(0.001)
        raise LoadAborted()

    parent = QObject()
    loader = PredictionLoader(build, parent)
    published = []
    loader.loaded.connect(published.append)
    loader.failed.connect(lambda: published.append("failed"))
    loader.start()
    assert entered.wait(2)
    loader.cancel(wait=2.0)
    assert not loader._job.thread.is_alive()
    QTest.qWait(50)
    assert published == []


def test_shutdown_joins_a_cooperative_build(pending):
    bridge, _, entered, _ = pending
    bridge.startPredictionLoading()
    assert entered.wait(2)
    thread = bridge._prediction_loader._job.thread
    bridge.shutdown()
    assert not thread.is_alive()


def test_the_real_constructor_honours_the_abort_at_its_first_checkpoint(tmp_path):
    """The abort is polled inside HybridPredictor, not only around it."""
    calls = []

    def abort():
        calls.append(True)
        return True

    with pytest.raises(LoadAborted):
        HybridPredictor(model_dir=tmp_path, enable_llm=False, abort_check=abort)
    assert len(calls) == 1
    # And the inverse: a check that never fires builds the engine as before.
    engine = HybridPredictor(model_dir=tmp_path, enable_llm=False, abort_check=lambda: False)
    assert engine.predict("the", n=3)


def test_the_gil_switch_interval_is_lowered_for_the_build_and_restored_after(qapp):
    """The build holds the GIL against the UI thread otherwise.

    A shorter switch interval is what keeps a held key's auto-repeat from
    bunching during the five seconds an engine takes to build.  It is a
    process-wide setting, so restoring it is half the property.
    """
    before = sys.getswitchinterval()
    seen = []

    def build(abort):
        seen.append(sys.getswitchinterval())
        return PredictorDouble()

    parent = QObject()
    loader = PredictionLoader(build, parent)
    loader.start()
    wait_for_job(loader._job)
    assert seen == [pytest.approx(_BUILD_SWITCH_INTERVAL_S)]
    assert sys.getswitchinterval() == before


@pytest.fixture
def manage_gc(monkeypatch):
    """Opt in as keyboard_app.main() does; the suite runs without it.

    ``gc.freeze`` is recorded rather than run: a real freeze here would pin
    every object in this worker, which is the leak the opt-in exists to keep
    out of the suite.  Yields the list of recorded freezes.
    """
    freezes = []
    monkeypatch.setattr(loader_module, "_manage_gc", True)
    monkeypatch.setattr(gc, "freeze", lambda: freezes.append("freeze"))
    yield freezes
    # A failure mid-test must not leave the collector off for the worker.
    if not gc.isenabled():
        gc.enable()


def test_the_build_interval_survives_windows_whole_millisecond_waits():
    """CPython on Windows waits for the GIL in whole milliseconds, truncated,
    so an interval under 1 ms is 0 there and every waiting thread spins.  On
    a one- or two-core CI runner that starved this file's worker threads
    until their 5 s waits expired.  The inverse: elsewhere the shorter value
    is kept, since it is what keeps a keystroke from being late.
    """
    if sys.platform == "win32":
        assert _BUILD_SWITCH_INTERVAL_S >= 0.001
    else:
        assert _BUILD_SWITCH_INTERVAL_S == pytest.approx(0.0001)


def test_the_collector_is_paused_for_the_build_and_resumed_after(qapp, manage_gc):
    """A collection holds the GIL for its whole run, which no switch interval
    can interrupt; the build's full collections were the 50-90 ms key stalls.

    Paired with its inverse: a build that fails still resumes the collector,
    since leaving it off would grow the process for the rest of the session.
    """
    assert gc.isenabled()
    seen = []

    def build(abort):
        seen.append(gc.isenabled())
        return PredictorDouble()

    def broken(abort):
        seen.append(gc.isenabled())
        raise RuntimeError("model file is corrupt")

    for factory in (build, broken):
        parent = QObject()
        loader = PredictionLoader(factory, parent)
        loader.start()
        wait_for_job(loader._job)
        assert gc.isenabled()
    assert seen == [False, False]


@pytest.mark.parametrize(
    "opted_in, collector_during_build, expected",
    [(True, False, ["freeze"]), (False, True, [])],
)
def test_the_collector_is_managed_only_when_the_app_opts_in(
    qapp, monkeypatch, opted_in, collector_during_build, expected
):
    """Opted in, the build pauses the collector and ends in a freeze: a
    collection at the end would be one more stall right before "ready", and
    freezing keeps the engine out of later collections.

    The inverse is the half that matters for this suite: without the opt-in
    the collector is never touched.  On a slow CI runner a real build
    outlives its test, and pausing then freezing kept earlier tests' dead
    bridges alive with their timers firing until later tests timed out.
    """
    calls = []
    seen = []
    monkeypatch.setattr(gc, "freeze", lambda: calls.append("freeze"))
    monkeypatch.setattr(gc, "collect", lambda *a: calls.append("collect") or 0)
    monkeypatch.setattr(loader_module, "_manage_gc", opted_in)

    def build(abort):
        seen.append(gc.isenabled())
        return PredictorDouble()

    parent = QObject()
    loader = PredictionLoader(build, parent)
    loader.start()
    wait_for_job(loader._job)
    assert seen == [collector_during_build]
    assert calls == expected
    assert gc.isenabled()


def test_overlapping_builds_restore_the_settings_the_first_one_found(qapp, manage_gc):
    """Only the last build out restores, so the order they end in is moot."""
    before_interval = sys.getswitchinterval()
    gates = [threading.Event(), threading.Event()]
    entered = [threading.Event(), threading.Event()]

    def factory_for(i):
        def build(abort):
            entered[i].set()
            assert gates[i].wait(5)
            return PredictorDouble()

        return build

    parents = [QObject(), QObject()]
    loaders = [PredictionLoader(factory_for(i), parents[i]) for i in range(2)]
    for i, loader in enumerate(loaders):
        loader.start()
        assert entered[i].wait(2)
    # The first one in ends first, which is the order that used to strand
    # the process on the second build's saved (build-time) settings.
    gates[0].set()
    wait_for_job(loaders[0]._job)
    assert sys.getswitchinterval() == pytest.approx(_BUILD_SWITCH_INTERVAL_S)
    assert not gc.isenabled()
    assert manage_gc == [], "froze while a build was still running"
    gates[1].set()
    wait_for_job(loaders[1]._job)
    assert sys.getswitchinterval() == before_interval
    assert gc.isenabled()
    assert manage_gc == ["freeze"], "the last build out freezes, exactly once"


def test_a_build_entering_during_the_last_ones_restoration_waits_for_it(
    qapp, monkeypatch, manage_gc
):
    """The last build out restores the collector under the settings lock.

    Restored after releasing it, a build arriving in that gap saw no active
    builds and a still-disabled collector, saved "disabled" as the baseline,
    and left the collector off for the rest of the process when it finished.
    So the new build must not get in until the collector is back.
    """
    assert gc.isenabled()
    real_enable = gc.enable
    restoring, release = threading.Event(), threading.Event()

    def gated_enable():
        restoring.set()
        assert release.wait(5)
        real_enable()

    monkeypatch.setattr(gc, "enable", gated_enable)
    entered, go = threading.Event(), threading.Event()

    def late_build(abort):
        entered.set()
        assert go.wait(5)
        return PredictorDouble()

    parents = [QObject(), QObject()]
    first = PredictionLoader(lambda abort: PredictorDouble(), parents[0])
    first.start()
    assert restoring.wait(5)
    second = PredictionLoader(late_build, parents[1])
    second.start()
    assert not entered.wait(0.3), "the new build got in before the collector was back"
    release.set()
    assert entered.wait(5)
    go.set()
    wait_for_job(first._job, second._job)
    assert gc.isenabled()


def test_publication_does_not_poll(qapp):
    """The worker wakes the UI thread once; nothing ticks meanwhile."""
    parent = QObject()
    loader = PredictionLoader(lambda abort: PredictorDouble(), parent)
    assert not [c for c in loader.children() if isinstance(c, QTimer)]


def test_the_suite_renders_qml_in_the_controls_style_the_app_ships(monkeypatch):
    """The QML tests here allow no warnings at all, and that rests on this.

    tests/conftest.py picks the Qt Quick Controls style for the suite; the
    app picks its own in keyboard_app._setup_platform_env.  If the two parted,
    the suite would be measuring a keyboard nobody runs, and under a native
    style it would also start warning "does not support customization" for
    every customised control, which is how a whitelist crept in last time.
    """
    from src import keyboard_app

    suite_style = os.environ["QT_QUICK_CONTROLS_STYLE"]
    monkeypatch.delenv("QT_QUICK_CONTROLS_STYLE")
    keyboard_app._setup_platform_env()
    assert os.environ["QT_QUICK_CONTROLS_STYLE"] == suite_style


def test_settings_buttons_say_why_they_are_inert_and_the_study_names_retry(pending, qapp):
    """A disabled MouseArea with no visible change is a silent dead tap."""
    bridge, _, entered, release = pending
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.clear()
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda errors: warnings.extend(e.toString() for e in errors))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "qml" / "Main.qml")))
    try:
        assert engine.rootObjects(), warnings
        root = engine.rootObjects()[0]
        save_label = root.findChild(QObject, "saveModelLabel")
        clear_label = root.findChild(QObject, "clearModelLabel")
        save_button = root.findChild(QObject, "saveModelButton")
        study_window = root.findChild(QObject, "studyWindow")
        assert save_label.property("text") == "Loading suggestions..."
        assert clear_label.property("text") == "Loading suggestions..."
        assert save_button.property("opacity") < 1.0

        bridge._prediction_load_failed()
        assert "Retry" in save_label.property("text")
        assert "Retry" in clear_label.property("text")
        study_window.beginSession()
        assert "Retry" in study_window.property("startError")

        bridge.startPredictionLoading()
        assert entered.wait(2)
        study_window.beginSession()
        assert "Wait" in study_window.property("startError")
        release.set()
        wait_for(lambda: bridge.predictionStatus == "ready")
        assert save_label.property("text") == "Save Now"
        assert clear_label.property("text") == "Clear Learned Data"
        assert save_button.property("opacity") == 1.0
        assert warnings == []
    finally:
        bridge.shutdown()
        del engine


def test_a_cancelled_build_restores_the_collector_too(qapp, manage_gc):
    """The app's path, cancelled mid-build (closing the keyboard while it
    loads): the collector must come back even though nothing is published."""
    entered = threading.Event()
    release = threading.Event()

    def build(abort):
        entered.set()
        assert release.wait(5)
        return PredictorDouble()

    parent = QObject()
    loader = PredictionLoader(build, parent)
    job = loader._job
    loader.start()
    try:
        assert entered.wait(2)
        assert not gc.isenabled()
        parent.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    finally:
        release.set()
        wait_for_job(job)
    assert gc.isenabled()
    assert manage_gc == ["freeze"]
