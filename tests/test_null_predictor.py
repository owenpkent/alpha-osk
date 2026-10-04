"""The stand-in predictor the bridge holds while the real engine loads.

See src/prediction/null_predictor.py.  Readiness used to be about fifty
hand-copied ``if self._predictor is None`` guards, and a call site added
without its copy was an AttributeError on a keystroke in the first seconds
of every launch, invisible to the synchronous test bridges because they are
always ready.  The stand-in removes the guards; this file is what stops a
new call site from reaching a name the stand-in does not have, and drives
the keyboard against it the way a user typing during startup would.
"""

from __future__ import annotations

import ast
import inspect
import os
from contextlib import nullcontext
from typing import Dict, Iterable, List, Optional, Set, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

PySide6 = pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from src import keyboard_bridge as kb  # noqa: E402
from src import study_bridge  # noqa: E402
from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402
from src.prediction.ngram_predictor import NgramPredictor  # noqa: E402
from src.prediction.null_predictor import EngineNotLoaded, NullPredictor, is_loaded  # noqa: E402
from src.study.capture import RecordingSynthesizer  # noqa: E402
from src.study_bridge import StudyBridge  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

AttrPath = Tuple[str, ...]


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


# ----------------------------------------------------------------------
#  The inventory
#
#  Every attribute path either bridge reaches through ``self._predictor``,
#  including through a local alias (``ngram = self._predictor._ngram`` and
#  then ``ngram.bigrams``), must resolve on the stand-in.  Walking the
#  source rather than listing names is the point: a list is the methods
#  somebody remembered, which is how the learning freeze once shipped
#  missing two of its own (see tests/test_learning_freeze.py).
# ----------------------------------------------------------------------


def _is_predictor(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "_predictor"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _chain(node: ast.AST, aliases: Dict[str, AttrPath]) -> Optional[AttrPath]:
    """The attribute path after the predictor, or None if not rooted there."""
    names: List[str] = []
    while not _is_predictor(node):
        if isinstance(node, ast.Attribute):
            names.append(node.attr)
            node = node.value
        elif isinstance(node, ast.Name) and node.id in aliases:
            return aliases[node.id] + tuple(reversed(names))
        else:
            return None
    return tuple(reversed(names))


def predictor_paths(source: str) -> Set[AttrPath]:
    """Every non-empty attribute path rooted at ``self._predictor``."""
    paths: Set[AttrPath] = set()
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        aliases: Dict[str, AttrPath] = {}
        assigns = sorted(
            (n for n in ast.walk(fn) if isinstance(n, ast.Assign)), key=lambda n: n.lineno
        )
        for node in assigns:
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                chain = _chain(node.value, aliases)
                if chain is not None:
                    aliases[node.targets[0].id] = chain
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute):
                chain = _chain(node, aliases)
                if chain:
                    paths.add(chain)
    return paths


def unresolved(obj: object, paths: Iterable[AttrPath]) -> List[str]:
    """The shortest prefix of each path that *obj* does not have."""
    missing = set()
    for path in paths:
        target = obj
        for i, name in enumerate(path):
            if not hasattr(target, name):
                missing.add(".".join(path[: i + 1]))
                break
            target = getattr(target, name)
    return sorted(missing)


def _bridge_paths() -> Set[AttrPath]:
    return predictor_paths(inspect.getsource(kb)) | predictor_paths(inspect.getsource(study_bridge))


class TestEveryCallSiteResolvesOnTheStandIn:
    def test_every_path_either_bridge_reaches_exists_on_the_stand_in(self):
        assert unresolved(NullPredictor(), _bridge_paths()) == []

    def test_the_stand_in_takes_the_engines_parameters(self):
        """A name that exists but takes different arguments still raises.

        Compared on name, kind and default, so a call that works against the
        engine works against the stand-in.  Properties and instance
        attributes are left to the test above.
        """

        def shape(fn):
            params = list(inspect.signature(fn).parameters.values())[1:]
            return [(p.name, p.kind, p.default) for p in params]

        compared, mismatched = [], []
        for name in sorted({path[0] for path in _bridge_paths()}):
            try:
                static = inspect.getattr_static(HybridPredictor, name)
            except AttributeError:
                continue  # an instance attribute, such as _ngram
            if isinstance(static, property):
                continue
            compared.append(name)
            if shape(getattr(HybridPredictor, name)) != shape(getattr(NullPredictor, name)):
                mismatched.append(name)
        assert mismatched == []
        assert "predict" in compared and "frozen_learning" in compared

    def test_the_walk_finds_the_call_sites_it_exists_for(self):
        """A walker that found nothing would pass both tests above for ever.

        One direct call on the keystroke path, one reach through ``_ngram``,
        one only an alias reveals (getVisualizationData's ``ngram``), and the
        study's freeze.
        """
        paths = _bridge_paths()
        assert ("predict_with_refinement",) in paths
        assert ("_ngram", "tokens", "forget") in paths
        assert ("_ngram", "user_vocab") in paths
        assert ("frozen_learning",) in paths

    def test_the_inventory_can_fail(self):
        source = (
            "class B:\n"
            "    def f(self):\n"
            "        self._predictor.direct()\n"
            "        view = self._predictor._ngram\n"
            "        return view.through_alias\n"
        )
        paths = predictor_paths(source)
        assert ("direct",) in paths
        assert ("_ngram", "through_alias") in paths
        assert unresolved(NullPredictor(), paths) == ["_ngram.through_alias", "direct"]

    def test_the_dashboard_sees_the_same_stats_fields(self):
        """Zeros in the same fields, not a dashboard with holes in it."""
        assert set(NullPredictor()._ngram.get_stats()) == set(NgramPredictor().get_stats())


# ----------------------------------------------------------------------
#  Readiness and the study
# ----------------------------------------------------------------------


class TestReadinessIsExplicit:
    def test_the_stand_in_and_nothing_are_not_loaded(self):
        assert not is_loaded(NullPredictor())
        assert not is_loaded(None)

    def test_anything_else_is(self):
        """The inverse: a check that said "not loaded" to everything would
        keep every refusing slot refusing for ever, and pass the case above.
        """
        assert is_loaded(object())

    def test_the_stand_in_refuses_to_freeze(self):
        with pytest.raises(EngineNotLoaded):
            NullPredictor().frozen_learning()


class _Double:
    def __init__(self, freeze):
        self.freeze = freeze

    def frozen_learning(self):
        return self.freeze()


class TestTheStudyNeverStartsAgainstTheStandIn:
    def test_a_session_is_refused_while_the_engine_loads(self, qapp, tmp_path):
        study = StudyBridge(predictor=NullPredictor(), config_dir=tmp_path)
        study.recordConsent("A", 0)
        assert not study.startSession()
        assert study.currentStep() == {"kind": "none"}

    def test_and_starts_once_the_engine_is_there(self, qapp, tmp_path):
        study = StudyBridge(predictor=NullPredictor(), config_dir=tmp_path)
        study.recordConsent("A", 0)
        study.set_predictor(_Double(nullcontext))
        assert study.startSession()
        assert study.currentStep()["kind"] != "none"
        study.shutdown()

    def test_a_freeze_that_raises_leaves_no_session_behind(self, qapp, tmp_path):
        """Defence behind the readiness check: if the freeze ever fails, no
        session may be left published to run unfrozen."""

        def refuse():
            raise EngineNotLoaded("refused")

        study = StudyBridge(predictor=_Double(refuse), config_dir=tmp_path)
        study.recordConsent("A", 0)
        with pytest.raises(EngineNotLoaded):
            study.startSession()
        assert study.currentStep() == {"kind": "none"}


# ----------------------------------------------------------------------
#  The keyboard, driven against the stand-in
# ----------------------------------------------------------------------


@pytest.fixture
def loading_bridge(qapp, monkeypatch):
    """A bridge whose engine never arrives: the first seconds of a launch."""
    synth = RecordingSynthesizer()
    monkeypatch.setattr(kb, "create_key_synthesizer", lambda: synth)
    bridge = kb.KeyboardBridge(defer_predictions=True)
    monkeypatch.setattr(bridge, "_get_foreground_window_id", lambda: 0)
    emitted: List[list] = []
    bridge.predictionsChanged.connect(lambda preds: emitted.append(list(preds)))
    try:
        yield bridge, synth, emitted
    finally:
        bridge.shutdown()
        bridge.deleteLater()
        QCoreApplication.sendPostedEvents(None, 0)


class TestTypingWhileTheEngineLoads:
    def test_typing_editing_and_moving_never_raise_and_offer_nothing(self, loading_bridge):
        bridge, synth, emitted = loading_bridge
        assert isinstance(bridge._predictor, NullPredictor)
        bridge.setAutocorrectEnabled(True)  # so the space reaches check_autocorrect

        for char in "hello":
            bridge.pressKey(char, 0.1, -0.1)  # an offset reaches observe_press
        assert synth.transcript == "hello"
        bridge.pressSpecialKey("space")
        for char in "wo":
            bridge.pressKey(char)
        bridge.pressSpecialKey("backspace")
        bridge.pressSpecialKey("backspace")
        bridge.pressSpecialKey("backspace")  # back into "hello": the unlearn path
        bridge.pressSpecialKey("space")
        bridge.pressKey(".")  # sentence end: learn and auto-capitalise
        bridge.pressSpecialKey("return")
        for key in ("left", "right", "up", "down", "home", "end"):
            bridge.pressSpecialKey(key)
        bridge.toggleShift()
        bridge.pressKey("a")
        bridge.toggleCapsLock()
        bridge.pressKey("b")
        bridge.toggleCapsLock()
        for char in "555-1234":  # the structured-token bar
            bridge.pressKey(char)
        bridge.pressSpecialKey("space")
        for char in "owen@gm":  # the email-domain bar
            bridge.pressKey(char)
        bridge.pressSpecialKey("return")

        assert bridge.predictions == []
        assert all(preds == [] for preds in emitted)

    def test_a_stale_pill_tap_and_an_edit_still_reach_the_app(self, loading_bridge):
        """The insert is never gated (the user tapped it); only learning is."""
        bridge, synth, emitted = loading_bridge
        for char in "hel":
            bridge.pressKey(char)
        bridge.pressPrediction("hello")
        assert synth.transcript.startswith("hello")
        bridge.editPrediction("wrold", "world")
        assert "world" in synth.transcript
        bridge.resetContext()
        assert bridge.predictions == []
        assert all(preds == [] for preds in emitted)

    def test_settings_and_dashboard_slots_answer_and_nothing_is_written(self, loading_bridge):
        bridge, _, emitted = loading_bridge
        from src.platform import get_model_dir

        bridge.setFilterExplicit(False)
        bridge.setMergeStrategy("rrf")
        bridge.setLlmEnabled(False)
        bridge.setPredictionCount(5)
        bridge.setLayout("dvorak")
        assert bridge.llmEnabled is False
        assert bridge.llmAvailable is False

        assert bridge.getPredictionStats() == {}
        assert bridge.getAvailablePacks() == []
        assert bridge.getEnabledPacks() == []
        assert bridge.getUserPacksDir() == ""
        assert not bridge.enableVocabularyPack("care")
        assert not bridge.disableVocabularyPack("care")
        assert bridge.checkAutocorrect("teh") == ""
        assert bridge.getKeyAlternatives("a") == []
        assert bridge.getLearnedTokens() == []
        assert not bridge.forgetToken("555-1234")
        viz = bridge.getVisualizationData()
        assert viz["words"] == [] and viz["edges"] == []
        assert viz["stats"]["blacklistCount"] == 0
        assert bridge.getWordContext("hello")["successors"] == []
        for slot in (
            bridge.blacklistWord,
            bridge.markBadSuggestion,
            bridge.markGoodSuggestion,
            bridge.unprefer,
            bridge.unblacklistWord,
            bridge.undisprefer,
        ):
            slot("hello")
        bridge.reloadDictionary()

        # The refusals, which say so rather than reporting success.
        bridge.savePredictionModel()
        bridge.clearUserData()
        assert bridge.exportUserData(str(get_model_dir() / "backup.zip"))
        assert bridge.importVocabularyPack(str(get_model_dir())) == ""

        assert isinstance(bridge._predictor, NullPredictor)
        assert not (get_model_dir() / "ngram_model.json").exists()
        assert not (get_model_dir() / "backup.zip").exists()
        assert bridge.predictions == []
        assert all(preds == [] for preds in emitted)
