"""The keyboard remains usable while a separate thread builds predictions."""

from __future__ import annotations

import ast
import inspect
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
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from src import keyboard_bridge as kb
from src.prediction.hybrid_predictor import HybridPredictor, LoadAborted
from src.prediction.loader import _BUILD_SWITCH_INTERVAL_S, PredictionLoader
from src.study.capture import RecordingSynthesizer
from src.study_bridge import StudyBridge
from tests.qml_context import install_context_properties
from tests.qt_settings_scope import TEST_APP, TEST_ORG

# The same fragment every other headless QML module ignores: Controls
# elsewhere in Main.qml (Popup, Slider, ToolTip) warn under the native style
# regardless of this feature.  The startup surface itself is drawn from
# plain items so it adds nothing to that list.
IGNORED_WARNING_FRAGMENTS = ("does not support customization",)


def real_warnings(warnings):
    return [w for w in warnings if not any(frag in w for frag in IGNORED_WARNING_FRAGMENTS)]


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
            wait_for(lambda: job.done)
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
    assert bridge._predictor is None

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
    wait_for(lambda: job.done)
    assert ready == []
    assert bridge._predictor is None
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
        wait_for(lambda: job.done)
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
        assert real_warnings(warnings) == []
    finally:
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
    wait_for(lambda: job.done)
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
    wait_for(lambda: loader._job.done)
    assert seen == [_BUILD_SWITCH_INTERVAL_S]
    assert sys.getswitchinterval() == before


def test_publication_does_not_poll(qapp):
    """The worker wakes the UI thread once; nothing ticks meanwhile."""
    parent = QObject()
    loader = PredictionLoader(lambda abort: PredictorDouble(), parent)
    assert not [c for c in loader.children() if isinstance(c, QTimer)]


class TestEveryPredictorCallSiteIsGuarded:
    """Readiness is a rule every call site has to remember, so it is inventoried.

    ``_predictor`` is None until the loader finishes, and a slot that reaches
    it unguarded is an AttributeError on a keystroke in the first seconds of
    every launch: invisible in the synchronous test bridges, which are
    always ready, and loud in the frozen build.  This walks the bridge's
    source and fails on any method that touches ``self._predictor.<attr>``
    without first comparing ``self._predictor`` against None, the same way
    ``test_learning_freeze.py`` inventories the predictor's own mutators.
    """

    # Methods that only ever run with the engine in hand: the one that
    # installs it, and the loader callback that receives it.
    KNOWN_READY = {"_connect_predictor", "_finish_prediction_load"}

    @staticmethod
    def _is_predictor_attr(node):
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
            and node.attr == "_predictor"
        )

    def _unguarded_methods(self):
        tree = ast.parse(inspect.getsource(kb))
        bridge = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "KeyboardBridge"
        )
        offenders = []
        for fn in bridge.body:
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            uses = [
                n.lineno
                for n in ast.walk(fn)
                if isinstance(n, ast.Attribute) and self._is_predictor_attr(n.value)
            ]
            if not uses:
                continue
            guards = [
                n.lineno
                for n in ast.walk(fn)
                if isinstance(n, ast.Compare)
                and self._is_predictor_attr(n.left)
                and all(isinstance(op, (ast.Is, ast.IsNot)) for op in n.ops)
            ]
            if not guards or min(uses) < min(guards):
                offenders.append(fn.name)
        return offenders

    def test_every_method_that_reaches_the_engine_checks_it_is_there(self):
        assert sorted(set(self._unguarded_methods()) - self.KNOWN_READY) == []

    def test_the_inventory_can_fail(self):
        """A walker that found nothing would pass the test above for ever."""
        src = "class KeyboardBridge:\n    def f(self):\n        return self._predictor.x\n"
        tree = ast.parse(src)
        fn = tree.body[0].body[0]
        uses = [n for n in ast.walk(fn) if isinstance(n, ast.Attribute)]
        assert any(self._is_predictor_attr(u.value) for u in uses)


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
        assert real_warnings(warnings) == []
    finally:
        bridge.shutdown()
        del engine
