"""The recording synthesiser, and the bridge swap that installs it.

The integration half matters more than the unit half here. The whole reason
capture happens at the synthesiser rather than as a mode inside ``_press_char``
is that a trial should exercise the real keystroke path, so the tests that
count are the ones that drive a real ``KeyboardBridge`` and check that
predictions still fire, that a pill still inserts a suffix, and that nothing
reached the desktop.
"""

from __future__ import annotations

import pytest

from src.study import metrics
from src.study.capture import RecordingSynthesizer


class FakeClock:
    """Monotonic ms-resolution clock the tests advance by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, ms: int) -> None:
        self.now += ms / 1000.0


class TestTheRecorderBehavesLikeATextField:
    def test_typing_appends(self) -> None:
        r = RecordingSynthesizer()
        for ch in "hello":
            r.send_text(ch)
        assert r.transcript == "hello"

    def test_backspace_removes_the_character_before_the_caret(self) -> None:
        r = RecordingSynthesizer()
        r.send_text("hello")
        r.send_key("BackSpace")
        assert r.transcript == "hell"

    def test_backspace_on_an_empty_field_is_harmless(self) -> None:
        """Still one click, so it must still be recorded, but there is
        nothing to delete. The paired inverse of the test above: an
        implementation that popped unconditionally would raise here.
        """
        r = RecordingSynthesizer()
        r.send_key("BackSpace")
        assert r.transcript == ""
        assert [e.kind for e in r.events] == ["backspace"]

    def test_a_caret_move_lets_a_fix_land_mid_word(self) -> None:
        """A participant who arrows back and fixes a letter is doing an
        ordinary thing. A recorder that appended blindly would report a
        transcript nobody typed and then score it as an error, which would
        show up in the data as a participant who could not type.
        """
        r = RecordingSynthesizer()
        r.send_text("helo")
        r.send_key("Left")
        r.send_text("l")
        assert r.transcript == "hello"

    def test_home_and_end_move_to_the_edges(self) -> None:
        r = RecordingSynthesizer()
        r.send_text("bc")
        r.send_key("Home")
        r.send_text("a")
        r.send_key("End")
        r.send_text("d")
        assert r.transcript == "abcd"

    def test_delete_removes_the_character_after_the_caret(self) -> None:
        r = RecordingSynthesizer()
        r.send_text("axb")
        r.send_key("Home")
        r.send_key("Right")
        r.send_key("Delete")
        assert r.transcript == "ab"

    def test_the_caret_cannot_run_off_either_end(self) -> None:
        r = RecordingSynthesizer()
        r.send_text("ab")
        for _ in range(5):
            r.send_key("Left")
        assert r.caret == 0
        for _ in range(5):
            r.send_key("Right")
        assert r.caret == 2

    def test_replace_text_overwrites_the_tail(self) -> None:
        r = RecordingSynthesizer()
        r.send_text("hwl")
        r.replace_text(3, "hello")
        assert r.transcript == "hello"


class TestActionKindIsInferredFromWhatArrived:
    """One click that produced several characters is a pill; one click that
    produced one character is typing. That distinction is the entire
    measurement, and deriving it from what arrived rather than from a flag a
    caller sets means a caller who forgot cannot silently score a pill as
    typing and report savings of zero.
    """

    def test_a_single_character_is_typing(self) -> None:
        r = RecordingSynthesizer()
        r.send_text("a")
        assert [e.kind for e in r.events] == ["char"]

    def test_a_multi_character_insert_is_a_pill(self) -> None:
        r = RecordingSynthesizer()
        r.send_text("llo ")
        assert [e.kind for e in r.events] == ["pill"]

    def test_replace_text_is_one_pill_not_a_run_of_backspaces(self) -> None:
        """It is one click from the participant's point of view however many
        characters it rewrites. Recording it as N backspaces plus an insert
        would charge them for clicks they never made and drive realised
        savings negative.
        """
        r = RecordingSynthesizer()
        for ch in "hwl":
            r.send_text(ch)
        r.replace_text(3, "hello")
        assert [e.kind for e in r.events] == ["char", "char", "char", "pill"]

    def test_a_special_key_is_not_typing(self) -> None:
        r = RecordingSynthesizer()
        r.send_key("Return")
        assert [e.kind for e in r.events] == ["special"]

    def test_a_chord_path_character_still_counts_as_typing(self) -> None:
        """_press_char routes a character through send_key when a modifier is
        active. It is still one character the participant paid one click for.
        """
        r = RecordingSynthesizer()
        r.send_key("a")
        assert [e.kind for e in r.events] == ["char"]
        assert r.transcript == "a"

    def test_an_empty_send_records_nothing(self) -> None:
        r = RecordingSynthesizer()
        r.send_text("")
        assert r.events == ()


class TestOffersAreInstrumentationNotActions:
    def test_an_offer_never_counts_as_an_input_action(self) -> None:
        r = RecordingSynthesizer()
        r.note_offer(["hello", "help"])
        r.send_text("h")
        trial = metrics.Trial("h", r.transcript, r.events, "c", "p", 0)
        assert len(metrics.input_actions(trial)) == 1

    def test_an_empty_offer_row_is_not_recorded(self) -> None:
        """The bar blanks constantly during normal typing, and a stream of
        empty offers would swamp the event log without saying anything.
        """
        r = RecordingSynthesizer()
        r.note_offer([])
        r.note_offer(["", ""])
        assert r.events == ()


class TestTimestampsAreRelativeToTheFirstEvent:
    def test_the_first_event_is_time_zero(self) -> None:
        clock = FakeClock()
        r = RecordingSynthesizer(clock=clock)
        clock.advance_ms(5000)
        r.send_text("a")
        assert r.events[0].t_ms == 0

    def test_later_events_carry_the_elapsed_gap(self) -> None:
        clock = FakeClock()
        r = RecordingSynthesizer(clock=clock)
        r.send_text("a")
        clock.advance_ms(250)
        r.send_text("b")
        assert [e.t_ms for e in r.events] == [0, 250]


class TestTheChangeCallback:
    def test_it_fires_on_every_recorded_event(self) -> None:
        calls: list[int] = []
        r = RecordingSynthesizer(on_change=lambda: calls.append(1))
        r.send_text("a")
        r.send_key("BackSpace")
        assert len(calls) == 2

    def test_a_recorder_with_no_callback_still_works(self) -> None:
        r = RecordingSynthesizer()
        r.send_text("a")
        assert r.transcript == "a"


class TestModifierStateIsNotText:
    def test_holding_and_releasing_records_nothing(self) -> None:
        """The click was already counted when the modifier key was pressed.
        Counting it again here would charge it twice.
        """
        r = RecordingSynthesizer()
        r.hold_modifier("shift")
        r.release_modifier("shift")
        r.reset_modifier_state()
        assert r.events == ()
        assert r.transcript == ""


class TestTheBridgeSwapsAndRestores:
    """The integration half. These drive a real KeyboardBridge, which is the
    whole reason capture lives at the synthesiser: a trial has to exercise the
    real keystroke path or it is not measuring the thing under study.
    """

    @pytest.fixture()
    def bridge(self):  # type: ignore[no-untyped-def]
        from src.keyboard_bridge import KeyboardBridge

        return KeyboardBridge()

    def test_typing_goes_to_the_recorder_and_not_the_desktop(self, bridge) -> None:  # type: ignore[no-untyped-def]
        r = RecordingSynthesizer()
        bridge.begin_study_capture(r)
        for ch in "hell":
            bridge.pressKey(ch)
        assert r.transcript == "hell"
        assert bridge.study_capture_active is True

    def test_predictions_still_run_while_capturing(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """The reason capture is not built on edit mode. That intercept
        returns before the prediction path in its own words ("skip everything
        else: password detection, analytics, predictions"), and predictions
        are the thing being studied, so a trial run through it would measure
        an engine that was switched off.
        """
        r = RecordingSynthesizer()
        bridge.begin_study_capture(r)
        for ch in "hell":
            bridge.pressKey(ch)
        assert bridge.predictions, "no suggestions were produced while capturing"
        assert "hello" in [p.lower() for p in bridge.predictions]

    def test_a_pill_inserts_only_the_unseen_suffix(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """Suffix-only insertion is what makes a pill one click rather than a
        retype, and it is the behaviour realised savings is measuring. If the
        redirect had broken it, every trial would score as if prediction did
        nothing.
        """
        r = RecordingSynthesizer()
        bridge.begin_study_capture(r)
        for ch in "hell":
            bridge.pressKey(ch)
        bridge.pressPrediction("hello")
        assert r.transcript.startswith("hello")
        pills = [e for e in r.events if e.kind == "pill"]
        assert len(pills) == 1
        assert len(pills[0].text) < len("hello")

    def test_ending_capture_restores_the_desktop_synthesizer(self, bridge) -> None:  # type: ignore[no-untyped-def]
        original = bridge._synth
        r = RecordingSynthesizer()
        bridge.begin_study_capture(r)
        assert bridge._synth is r
        bridge.end_study_capture()
        assert bridge._synth is original
        assert bridge.study_capture_active is False

    def test_beginning_twice_does_not_lose_the_desktop_synthesizer(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """The paired inverse of the restore test. A second begin that
        overwrote the saved synthesiser would leave the keyboard permanently
        typing into a dead recorder after the study ended.
        """
        original = bridge._synth
        bridge.begin_study_capture(RecordingSynthesizer())
        bridge.begin_study_capture(RecordingSynthesizer())
        bridge.end_study_capture()
        assert bridge._synth is original

    def test_compat_mode_is_forced_off_while_capturing(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """Compat mode replaces a one-click pill with a run of backspaces to
        work around apps that intercept keystrokes. A trial types into a
        recorder, so there is nothing to work around, and leaving it on would
        score a participant's realised savings near zero for a reason that has
        nothing to do with them or the engine.
        """
        bridge._compat_manual = True
        assert bridge._in_compat_mode() is True
        bridge.begin_study_capture(RecordingSynthesizer())
        assert bridge._in_compat_mode() is False
        bridge.end_study_capture()
        assert bridge._in_compat_mode() is True

    def test_game_mode_is_forced_off_while_capturing(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """The per-key hold exists so a key survives a game's input poll, and
        a recorder polls nothing. Leaving it on drops characters: the game
        path sends through ``_send_key``, which attaches the sticky
        modifiers, and the recorder only counts a single character as typing
        when none came with it.
        """
        bridge._game_auto_active = True
        assert bridge._in_game_mode() is True
        bridge.begin_study_capture(RecordingSynthesizer())
        assert bridge._in_game_mode() is False
        bridge.end_study_capture()
        assert bridge._in_game_mode() is True

    def test_a_shifted_character_is_recorded_as_typing_while_capturing(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """The failure the guard above prevents, driven end to end.

        With a game detected behind the keyboard, a capital used to reach
        ``RecordingSynthesizer.send_key`` carrying "shift", miss its
        single-character branch and land as a "special": absent from the
        transcript, but still counted as an input action. That inflates KSPC
        and deflates realised savings on a trial typed perfectly.
        """
        recorder = RecordingSynthesizer()
        bridge._game_auto_active = True
        bridge.begin_study_capture(recorder)
        try:
            bridge.toggleShift()
            bridge.pressKey("h")
        finally:
            bridge.end_study_capture()

        assert recorder.transcript == "H"
        assert [e.kind for e in recorder.events] == ["char"]

    def test_a_real_chord_is_still_not_typing_while_capturing(self, bridge) -> None:  # type: ignore[no-untyped-def]
        """The near-miss the test above must not swallow.

        Ctrl+C is a command, not a character, and must stay out of the
        transcript however the game flag is set. A recorder that simply
        inserted every single character it was handed would pass the shifted
        case and fail this one.
        """
        recorder = RecordingSynthesizer()
        bridge._game_auto_active = True
        bridge.begin_study_capture(recorder)
        try:
            bridge.toggleCtrl()
            bridge.pressKey("c")
        finally:
            bridge.end_study_capture()

        assert recorder.transcript == ""
        assert "char" not in [e.kind for e in recorder.events]


class TestTheOffConditionBlanksTheBarWithoutMovingIt:
    """Protocol section 5.1.

    The off condition must keep the suggestion bar present, reserved and
    empty. The QML half of that is tested headlessly; this is the Python half,
    the flag QML binds. Using the ``suggestionsEnabled`` setting instead would
    collapse the bar to zero height and move every key on the keyboard up,
    confounding prediction with a change in key geometry, in a pointing task,
    on the population whose pointing accuracy is the thing being studied.
    """

    def _bridge_at(self, tmp_path, predictions: bool):  # type: ignore[no-untyped-def]
        from src.keyboard_bridge import KeyboardBridge
        from src.study_bridge import StudyBridge

        kb = KeyboardBridge()
        sb = StudyBridge(keyboard=kb, predictor=None, config_dir=tmp_path)
        sb.recordConsent("A", 0)
        assert sb.startSession()
        # Walk to the first step of the condition we want.
        session = sb._session
        assert session is not None
        while True:
            step = session.current
            assert step is not None, "ran out of plan before finding the condition"
            if step.kind in ("practice", "trial") and step.condition.predictions is predictions:
                break
            session.advance()
        return kb, sb

    def test_the_off_condition_suppresses(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        _, sb = self._bridge_at(tmp_path, predictions=False)
        assert sb.beginTrial() is True
        assert sb.suppressPredictions is True

    def test_the_on_condition_does_not(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The paired inverse. A property hardwired to True would satisfy the
        test above and would blank the bar in the condition being measured.
        """
        _, sb = self._bridge_at(tmp_path, predictions=True)
        assert sb.beginTrial() is True
        assert sb.suppressPredictions is False

    def test_nothing_is_suppressed_outside_a_trial(self, tmp_path) -> None:
        """The keyboard belongs to the participant the rest of the time."""
        _, sb = self._bridge_at(tmp_path, predictions=False)
        assert sb.suppressPredictions is False

    def test_the_engine_still_runs_in_the_off_condition(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Suppression is about what is rendered, not about switching the
        engine off. Nothing about the engine may differ between conditions
        except whether the participant can see its output, or the comparison
        stops being about prediction and starts being about two builds.
        """
        kb, sb = self._bridge_at(tmp_path, predictions=False)
        sb.beginTrial()
        for ch in "hell":
            kb.pressKey(ch)
        assert kb.predictions, "the engine stopped producing suggestions"

    def test_an_offer_is_not_recorded_when_the_bar_was_blank(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """A recorded offer claims the participant saw a suggestion and
        declined it. In the off condition they were never shown one, so
        recording it would put a visual-search cost into the data for a search
        that could not have happened.
        """
        kb, sb = self._bridge_at(tmp_path, predictions=False)
        sb.beginTrial()
        for ch in "hell":
            kb.pressKey(ch)
        recorder = sb._recorder
        assert recorder is not None
        assert [e for e in recorder.events if e.kind == "offer"] == []
