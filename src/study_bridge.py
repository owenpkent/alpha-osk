"""
QML-facing wrapper around the research study harness.

Its own context property (``study``), registered next to ``keyboard`` and
``telemetry`` in ``keyboard_app.py``, which is the shape
``docs/architecture/STRUCTURAL_REVIEW.md`` section 3.1 prescribes for a new
feature surface: a QObject of its own rather than another concern bolted onto
``KeyboardBridge``.

Everything real lives in ``src/study/``, which is deliberately Qt-free so the
protocol can be re-analysed, re-run and tested with no display attached. This
class owns only the plumbing: the session's lifetime, the learning freeze's
lifetime, the trial capture's lifetime, and the slots QML calls.

The study and the opt-in telemetry channel are separate things with separate
consent, and this module never touches the other one. See
``docs/research/STUDY_PROTOCOL.md`` section 10 for why they are kept apart.
"""

from __future__ import annotations

import logging
import random
from contextlib import ExitStack
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from .study import export, metrics, phrases
from .study.capture import RecordingSynthesizer
from .study.config import StudyStore
from .study.session import DEFAULT_DESIGN, DESIGNS, STEP_PRACTICE, STEP_TRIAL, Session

_logger = logging.getLogger("StudyBridge")

# Bumping this invalidates consent: a participant who agreed to version 0.1
# has not agreed to 0.2, and `hasConsented` compares against it rather than
# merely checking that some consent exists. Keep it in step with the heading
# of docs/research/STUDY_CONSENT.md.
CONSENT_VERSION = "0.1"


class StudyBridge(QObject):
    """Owns the study store, the running session, and the trial capture."""

    stepChanged = Signal()
    sessionComplete = Signal()
    transcriptChanged = Signal()
    captureChanged = Signal()

    def __init__(
        self,
        keyboard: Any = None,
        predictor: Any = None,
        *,
        app_version: str = "",
        os_name: str = "",
        config_dir: Optional[Path] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._keyboard = keyboard
        self._predictor = predictor
        self._app_version = app_version
        self._os_name = os_name
        self._store = StudyStore(config_dir)
        self._session: Optional[Session] = None
        self._recorder: Optional[RecordingSynthesizer] = None
        # Tracked rather than discovered by attempting the disconnect: PySide
        # logs a RuntimeWarning for a disconnect that had nothing to undo
        # instead of raising, so a try/except around it neither prevents the
        # noise nor tells us anything. The off condition never connects.
        self._offers_connected = False

        # The freeze is a context manager (see HybridPredictor.frozen_learning)
        # but a session spans many QML round trips, so it cannot sit in a
        # `with` block. An ExitStack keeps the manager's guarantee (the
        # restore runs even if the body raises) while letting its lifetime be
        # the session's rather than one call's.
        self._freeze = ExitStack()

    # --- consent ---

    @Slot(result=bool)
    def hasConsented(self) -> bool:
        """Whether this participant has agreed to the CURRENT consent version.

        Version-checked rather than merely present: someone who agreed to 0.1
        has not agreed to whatever 0.2 says, and silently carrying old consent
        forward across a revision is the way a consent record stops meaning
        anything.
        """
        state = self._store.state
        return state.has_consented and state.consent_version == CONSENT_VERSION

    @Slot(result=str)
    def consentVersion(self) -> str:
        return CONSENT_VERSION

    @Slot(str, int, result=str)
    @Slot(str, result=str)
    def recordConsent(self, design_id: str = "", enrolment_index: int = -1) -> str:
        """Record consent and mint a participant code.

        ``enrolment_index`` is the participant's position in the recruitment
        sequence, which is what keeps the condition orders balanced (see
        ``session.order_for``). It has to be supplied by the researcher when
        the participant registers interest, because a machine cannot know how
        many people enrolled before it. Passing -1 falls back to a random
        draw, which still runs a valid session and gives up the balance
        guarantee, so the UI should ask for it rather than rely on this.
        """
        design = DESIGNS.get(design_id or DEFAULT_DESIGN.id, DEFAULT_DESIGN)
        index = enrolment_index if enrolment_index >= 0 else random.randrange(1_000_000)
        return self._store.record_consent(
            consent_version=CONSENT_VERSION,
            design_id=design.id,
            participant_index=index,
        )

    @Slot("QVariantMap")
    def setBackground(self, answers: Dict[str, Any]) -> None:
        """Store the background questionnaire answers."""
        self._store.state.background = {str(k): v for k, v in dict(answers).items()}
        self._store.save()

    # --- session ---

    @Slot(result=bool)
    def startSession(self) -> bool:
        """Build the plan, freeze learning, and resume where we left off.

        Returns False rather than raising if consent is missing or the phrase
        pool cannot fill the design, because both are recoverable states the
        UI has to be able to show a message for.
        """
        if not self.hasConsented():
            return False
        state = self._store.state
        design = DESIGNS.get(state.design_id, DEFAULT_DESIGN)

        pool = list(phrases.build_pool())
        needed = design.practice_phrases + design.scored_phrases
        assigned: Dict[str, List[str]] = {}
        # Seeded on the participant code so a resumed session rebuilds exactly
        # the same assignment. Rebuilding it differently would hand someone a
        # phrase they had already typed, which measures memory.
        rng = random.Random(state.participant)
        rng.shuffle(pool)
        cursor = 0
        for condition in design.conditions:
            if cursor + needed > len(pool):
                _logger.warning("phrase pool too small for design %s", design.id)
                return False
            assigned[condition.id] = pool[cursor : cursor + needed]
            cursor += needed

        try:
            session = Session.create(design, state.participant, state.participant_index, assigned)
        except ValueError as e:
            _logger.warning("could not build session: %s", e)
            return False

        session.cursor = min(state.cursor, len(session.plan))
        self._session = session

        if design.freeze_learning and self._predictor is not None:
            self._freeze.enter_context(self._predictor.frozen_learning())

        self.stepChanged.emit()
        return True

    @Slot(result="QVariantMap")
    def currentStep(self) -> Dict[str, Any]:
        """What the participant is being asked to do right now."""
        session = self._session
        if session is None:
            return {"kind": "none"}
        step = session.current
        if step is None:
            return {"kind": "done"}
        return {
            "kind": step.kind,
            "phrase": step.phrase,
            "condition": step.condition.id,
            "conditionLabel": step.condition.label,
            "predictions": step.condition.predictions,
            "keyboard": step.condition.keyboard,
            "blockIndex": step.block_index,
            "blockCount": len(session.order),
            "scoredDone": session.scored_done,
            "scoredTotal": session.scored_total,
        }

    # --- one trial ---

    @Property(bool, notify=captureChanged)
    def capturing(self) -> bool:
        """Whether keystrokes are currently going into a trial, not the desktop."""
        return self._recorder is not None

    @Property(bool, notify=captureChanged)
    def suppressPredictions(self) -> bool:
        """Whether the suggestion bar must render empty right now.

        Bound by ``Main.qml``, which blanks the pill row while leaving the
        bar's height untouched.  Emptying the row rather than turning
        suggestions off is load-bearing, and protocol section 5.1 is the
        reason: the ``suggestionsEnabled`` setting collapses the bar to zero
        height and moves every key up with it, so using it for the off
        condition would confound prediction with a change in key geometry, in
        a pointing task, on the population whose pointing accuracy is the
        thing being studied.
        """
        step = self._session.current if self._session is not None else None
        if step is None or self._recorder is None:
            return False
        return not step.condition.predictions

    @Property(str, notify=transcriptChanged)
    def transcript(self) -> str:
        """What the participant has typed into the current trial so far."""
        return self._recorder.transcript if self._recorder is not None else ""

    @Slot(result=bool)
    def beginTrial(self) -> bool:
        """Start capturing keystrokes into a recorder instead of the desktop."""
        session = self._session
        if session is None or self._recorder is not None or self._keyboard is None:
            return False
        step = session.current
        if step is None or step.kind not in (STEP_TRIAL, STEP_PRACTICE):
            return False

        recorder = RecordingSynthesizer(on_change=self._on_recorder_changed)
        self._recorder = recorder
        self._keyboard.begin_study_capture(recorder)
        # Offers are only recorded when they were actually visible.  In the
        # off condition the engine still runs (nothing about it changes, which
        # is the point) but the bar renders empty, so a recorded offer would
        # claim the participant declined a suggestion they were never shown.
        if step.condition.predictions:
            self._keyboard.predictionsChanged.connect(self._on_predictions)
            self._offers_connected = True
        self.captureChanged.emit()
        self.transcriptChanged.emit()
        return True

    @Slot(result=bool)
    def submitTrial(self) -> bool:
        """End capture, store the trial, and advance to the next step."""
        session = self._session
        recorder = self._recorder
        if session is None or recorder is None:
            return False
        step = session.current
        if step is None:
            return False

        trial = metrics.Trial(
            presented=step.phrase,
            transcribed=recorder.transcript,
            events=recorder.events,
            condition=step.condition.id,
            participant=session.participant,
            phrase_index=step.phrase_index,
        )
        self._end_capture()

        record = export.trial_to_dict(trial)
        record["practice"] = step.kind != STEP_TRIAL
        # Persisted before the cursor moves, so a crash costs the trial in
        # flight and nothing earlier.  Asking a participant in this population
        # to repeat a block is a cost that may simply not be payable.
        self._store.append_trial(record)

        try:
            session.record(trial)
        except (RuntimeError, ValueError) as e:
            _logger.warning("could not record trial: %s", e)
            return False

        self._store.set_cursor(session.cursor)
        self._emit_progress(session)
        return True

    @Slot()
    def abandonTrial(self) -> None:
        """Stop capturing and throw the trial away.

        The way out for a participant who wants to stop mid-phrase.  Nothing
        is stored and the step is not advanced, so resuming re-presents the
        same phrase rather than skipping one.
        """
        self._end_capture()

    def _end_capture(self) -> None:
        if self._recorder is None:
            return
        if self._keyboard is not None:
            if self._offers_connected:
                self._keyboard.predictionsChanged.disconnect(self._on_predictions)
                self._offers_connected = False
            self._keyboard.end_study_capture()
        self._recorder = None
        self.captureChanged.emit()
        self.transcriptChanged.emit()

    def _on_predictions(self, words: Any) -> None:
        if self._recorder is not None:
            self._recorder.note_offer([str(w) for w in words])

    def _on_recorder_changed(self) -> None:
        self.transcriptChanged.emit()

    # --- workload, breaks, progress ---

    @Slot(str, "QVariantMap", result=bool)
    def recordWorkload(self, condition_id: str, scores: Dict[str, Any]) -> bool:
        session = self._session
        if session is None:
            return False
        try:
            session.record_workload(condition_id, {str(k): int(v) for k, v in scores.items()})
        except (RuntimeError, ValueError) as e:
            _logger.warning("could not record workload: %s", e)
            return False
        self._store.set_cursor(session.cursor)
        self._emit_progress(session)
        return True

    @Slot()
    def skipStep(self) -> None:
        """Advance past a break, which is the one step with nothing to record."""
        session = self._session
        if session is None:
            return
        session.advance()
        self._store.set_cursor(session.cursor)
        self._emit_progress(session)

    def _emit_progress(self, session: Session) -> None:
        self.stepChanged.emit()
        if session.is_complete:
            self._thaw()
            self.sessionComplete.emit()

    # --- results ---

    @Slot(result="QVariantMap")
    def reviewSummary(self) -> Dict[str, Any]:
        """The participant's own numbers, shown before they decide to submit."""
        session = self._session
        if session is None:
            return {}
        return export.review_summary(session)

    @Slot(str, result=bool)
    def saveBundle(self, path: str) -> bool:
        """Write the submission bundle where the participant can read it."""
        session = self._session
        if session is None:
            return False
        bundle = export.build_bundle(
            session,
            self._store.state,
            app_version=self._app_version,
            os_name=self._os_name,
        )
        if not export.write_bundle(Path(path), bundle):
            return False
        self._store.mark_submitted()
        return True

    @Slot(result=str)
    def suggestedBundlePath(self) -> str:
        """Where the bundle is written by default, next to the other data."""
        return str(self._store.state_path.with_name("study-bundle.json"))

    @Slot()
    def withdraw(self) -> None:
        """Discard everything, immediately and locally.

        Ends capture and thaws first. A participant who withdraws mid-trial
        must not be left with a keyboard that types into nothing, or one that
        has quietly stopped learning: those are the two ways this feature
        could damage someone who declined to take part.
        """
        self._end_capture()
        self._thaw()
        self._session = None
        self._store.withdraw()
        self.stepChanged.emit()

    def _thaw(self) -> None:
        self._freeze.close()
        self._freeze = ExitStack()

    def shutdown(self) -> None:
        """Release the capture and the learning freeze on the way out.

        Quitting mid-session must restore both. The ExitStack would thaw on
        collection anyway, but relying on the interpreter to tear down a
        user-visible behaviour change is not something to leave to chance,
        and the synthesiser swap has no such fallback at all: a session that
        quit while capturing would otherwise leave the next launch's keyboard
        typing into a dead recorder.
        """
        self._end_capture()
        self._thaw()
