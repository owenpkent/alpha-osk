"""Tests for src/study/session.py.

See docs/research/STUDY_PROTOCOL.md sections 4, 5 and 8: the Williams
counterbalancing exists to spread first-order carryover (fatigue, or
practice transferring from one keyboard to the next) evenly across
conditions rather than letting it land on whichever condition happens to
run second; the block state machine exists so a session can be resumed on
another day from a single stored cursor; and the learning freeze (section
5.2) is why DESIGN_C, the one design with no task battery, is also the one
design that leaves learning on.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

import pytest

from src.study.metrics import Trial
from src.study.session import (
    DESIGN_A,
    DESIGN_B,
    DESIGN_C,
    STEP_BREAK,
    STEP_PRACTICE,
    STEP_TRIAL,
    STEP_WORKLOAD,
    Condition,
    Design,
    Session,
    build_plan,
    order_for,
    williams_orders,
)


def _phrases_for(design: Design, order: Sequence[Condition]) -> dict[str, list[str]]:
    needed = design.practice_phrases + design.scored_phrases
    return {c.id: [f"{c.id}-phrase-{i}" for i in range(needed)] for c in order}


def _make_session(design: Design = DESIGN_A) -> Session:
    order = design.conditions
    return Session.create(
        design, participant="P-test", participant_index=0, phrases=_phrases_for(design, order)
    )


def _trial_for(condition: str, phrase_index: int) -> Trial:
    return Trial(
        presented="hello",
        transcribed="hello",
        events=(),
        condition=condition,
        participant="P-test",
        phrase_index=phrase_index,
    )


def _valid_tlx() -> dict[str, int]:
    return {
        "mental": 10,
        "physical": 5,
        "temporal": 8,
        "performance": 12,
        "effort": 9,
        "frustration": 3,
    }


class TestWilliamsOrders:
    def test_n_equals_one_gives_one_order(self) -> None:
        assert williams_orders(1) == ((0,),)

    def test_n_equals_two_gives_exactly_the_square_and_its_reverse(self) -> None:
        assert williams_orders(2) == ((0, 1), (1, 0))

    def test_n_equals_four_first_row_matches_the_williams_construction(self) -> None:
        orders = williams_orders(4)
        assert len(orders) == 4
        assert orders[0] == (0, 1, 3, 2)

    def test_n_equals_three_needs_the_square_and_its_mirror(self) -> None:
        """Odd n cannot balance carryover with the square alone: with three
        rows each condition would follow only two of the others, once
        each, one transition short of covering itself too. That is why
        Design B (three arms) costs six orders against Design A's two, and
        why section 4 says three arms estimate order effects far less
        well at a realistic sample size.
        """
        assert len(williams_orders(3)) == 6

    def test_n_equals_zero_raises(self) -> None:
        with pytest.raises(ValueError):
            williams_orders(0)

    @pytest.mark.parametrize("n", [2, 3, 4])
    def test_every_condition_appears_in_every_position_equally_often(self, n: int) -> None:
        orders = williams_orders(n)
        for position in range(n):
            counts = Counter(order[position] for order in orders)
            assert set(counts) == set(range(n))
            assert len(set(counts.values())) == 1

    @pytest.mark.parametrize("n", [2, 3, 4])
    def test_every_ordered_adjacent_pair_appears_equally_often(self, n: int) -> None:
        """The property that actually separates a Williams design from a
        plain cyclic Latin square, which already balances *position* for
        free: a square can still let carryover, what preceded what, land
        unevenly on particular conditions. Checked over the generated
        orders rather than against a hardcoded count, since the point is
        that the construction has this property, not that these
        particular numbers do.
        """
        orders = williams_orders(n)
        pair_counts: Counter[tuple[int, int]] = Counter()
        for order in orders:
            for a, b in zip(order, order[1:]):
                pair_counts[(a, b)] += 1
        expected_pairs = {(a, b) for a in range(n) for b in range(n) if a != b}
        assert set(pair_counts) == expected_pairs
        assert len(set(pair_counts.values())) == 1


class TestOrderFor:
    def test_consecutive_participants_cycle_through_every_order_then_wrap(self) -> None:
        num_orders = len(williams_orders(len(DESIGN_A.conditions)))
        seen = [order_for(DESIGN_A, i) for i in range(num_orders)]
        assert len(set(seen)) == num_orders
        assert order_for(DESIGN_A, num_orders) == seen[0]
        assert order_for(DESIGN_A, num_orders + 1) == seen[1]

    def test_the_same_participant_index_always_gives_the_same_order(self) -> None:
        """Assignment is deterministic by enrolment position rather than
        randomised, which is what keeps the sample balanced if recruitment
        stops early.
        """
        first = order_for(DESIGN_A, 4)
        second = order_for(DESIGN_A, 4)
        assert first == second


class TestConditionRejectsAnUnknownKeyboard:
    def test_alpha_is_accepted(self) -> None:
        Condition(id="x", label="X", keyboard="alpha", predictions=True)

    def test_system_is_accepted(self) -> None:
        Condition(id="x", label="X", keyboard="system", predictions=False)

    def test_an_unknown_keyboard_string_raises(self) -> None:
        with pytest.raises(ValueError):
            Condition(id="x", label="X", keyboard="windows_osk", predictions=False)


class TestDesignRejectsAnEmptyOrDuplicateConditionList:
    def test_an_empty_condition_list_raises(self) -> None:
        with pytest.raises(ValueError):
            Design(id="Z", label="Z", conditions=(), scored_phrases=1, practice_phrases=1)

    def test_duplicate_condition_ids_raise(self) -> None:
        dup = Condition(id="dup", label="Dup", keyboard="alpha", predictions=True)
        with pytest.raises(ValueError):
            Design(id="Z", label="Z", conditions=(dup, dup), scored_phrases=1, practice_phrases=1)

    def test_distinct_condition_ids_are_accepted(self) -> None:
        a = Condition(id="a", label="A", keyboard="alpha", predictions=True)
        b = Condition(id="b", label="B", keyboard="alpha", predictions=False)
        Design(id="Z", label="Z", conditions=(a, b), scored_phrases=1, practice_phrases=1)


class TestBuildPlanStepCounts:
    def test_step_counts_and_ordering_for_design_a(self) -> None:
        order = DESIGN_A.conditions
        plan = build_plan(DESIGN_A, order, _phrases_for(DESIGN_A, order))

        for block_index, condition in enumerate(order):
            block_steps = [s for s in plan if s.block_index == block_index]
            practice_steps = [s for s in block_steps if s.kind == STEP_PRACTICE]
            trial_steps = [s for s in block_steps if s.kind == STEP_TRIAL]
            workload_steps = [s for s in block_steps if s.kind == STEP_WORKLOAD]
            assert len(practice_steps) == DESIGN_A.practice_phrases
            assert len(trial_steps) == DESIGN_A.scored_phrases
            assert len(workload_steps) == 1
            assert all(s.condition == condition for s in practice_steps)
            assert all(s.condition == condition for s in trial_steps)
            assert all(s.condition == condition for s in workload_steps)

        break_steps = [s for s in plan if s.kind == STEP_BREAK]
        assert len(break_steps) == len(order) - 1
        assert plan[-1].kind != STEP_BREAK

    def test_no_phrase_is_reused_within_a_participant_across_conditions(self) -> None:
        """Section 7: reusing a phrase across a participant's own
        conditions would measure memory of the phrase rather than of the
        keyboard, which is the confound the disjoint per-condition pools
        exist to avoid. With disjoint phrase lists per condition, nothing
        in build_plan may still produce a collision.
        """
        order = DESIGN_A.conditions
        plan = build_plan(DESIGN_A, order, _phrases_for(DESIGN_A, order))
        seen: set[str] = set()
        for step in plan:
            if step.kind in (STEP_PRACTICE, STEP_TRIAL):
                assert step.phrase not in seen
                seen.add(step.phrase)

    def test_too_few_phrases_for_a_condition_raises(self) -> None:
        order = DESIGN_A.conditions
        phrases = {c.id: ["only one phrase"] for c in order}
        with pytest.raises(ValueError):
            build_plan(DESIGN_A, order, phrases)


class TestSessionRecord:
    def test_record_advances_the_cursor_and_stores_the_trial(self) -> None:
        session = _make_session()
        step = session.current
        assert step is not None
        assert step.kind == STEP_PRACTICE
        trial = _trial_for(step.condition.id, step.phrase_index)

        session.record(trial)

        assert session.cursor == 1
        assert session.trials == [trial]

    def test_record_rejects_a_trial_recorded_against_the_wrong_condition(self) -> None:
        """The near-miss that matters: a trial recorded against the wrong
        block would silently mix one condition's data into another's,
        corrupting exactly the paired comparison the study exists to make.
        """
        session = _make_session()
        step = session.current
        assert step is not None
        other = next(c for c in DESIGN_A.conditions if c.id != step.condition.id)
        trial = _trial_for(other.id, step.phrase_index)

        with pytest.raises(ValueError):
            session.record(trial)

    def test_record_raises_at_a_workload_step(self) -> None:
        session = _make_session()
        needed = DESIGN_A.practice_phrases + DESIGN_A.scored_phrases
        for _ in range(needed):
            step = session.current
            assert step is not None
            session.record(_trial_for(step.condition.id, step.phrase_index))
        assert session.current is not None
        assert session.current.kind == STEP_WORKLOAD

        with pytest.raises(RuntimeError):
            session.record(_trial_for(session.order[0].id, 0))

    def test_record_raises_at_a_break_step(self) -> None:
        session = _make_session()
        needed = DESIGN_A.practice_phrases + DESIGN_A.scored_phrases
        for _ in range(needed):
            step = session.current
            assert step is not None
            session.record(_trial_for(step.condition.id, step.phrase_index))
        session.record_workload(session.order[0].id, _valid_tlx())
        assert session.current is not None
        assert session.current.kind == STEP_BREAK

        with pytest.raises(RuntimeError):
            session.record(_trial_for(session.order[0].id, 0))


class TestRecordWorkload:
    def _session_at_workload(self) -> Session:
        session = _make_session()
        needed = DESIGN_A.practice_phrases + DESIGN_A.scored_phrases
        for _ in range(needed):
            step = session.current
            assert step is not None
            session.record(_trial_for(step.condition.id, step.phrase_index))
        assert session.current is not None
        assert session.current.kind == STEP_WORKLOAD
        return session

    def test_six_scales_in_range_are_accepted(self) -> None:
        session = self._session_at_workload()
        condition_id = session.order[0].id
        session.record_workload(condition_id, _valid_tlx())
        assert session.workload[condition_id] == _valid_tlx()

    def test_a_scale_of_twenty_one_is_rejected_not_clamped(self) -> None:
        """Out-of-range is rejected rather than clamped: a silently
        clamped 21 would be indistinguishable on the record from a real
        20, and a scale the UI mis-sent is a bug worth failing on.
        """
        session = self._session_at_workload()
        scores = _valid_tlx()
        scores["mental"] = 21
        with pytest.raises(ValueError):
            session.record_workload(session.order[0].id, scores)

    def test_a_scale_of_minus_one_is_rejected_not_clamped(self) -> None:
        session = self._session_at_workload()
        scores = _valid_tlx()
        scores["mental"] = -1
        with pytest.raises(ValueError):
            session.record_workload(session.order[0].id, scores)

    def test_a_workload_filed_under_the_wrong_condition_is_rejected(self) -> None:
        """Workload is keyed by condition and is the RQ3 outcome, so a
        response accepted under the wrong key does not fail anywhere
        later: it silently swaps the two blocks' perceived-effort scores,
        which reads as a result rather than as an error.  This mirrors the
        check ``record`` already makes on a trial's condition.
        """
        session = self._session_at_workload()
        wrong = session.order[1].id
        assert wrong != session.order[0].id
        with pytest.raises(ValueError):
            session.record_workload(wrong, _valid_tlx())
        assert session.workload == {}

    def test_the_matching_condition_is_still_accepted(self) -> None:
        """The paired inverse: a check that rejected everything would
        satisfy the test above on its own.
        """
        session = self._session_at_workload()
        right = session.current.condition.id
        session.record_workload(right, _valid_tlx())
        assert right in session.workload


class TestSessionProgressTracking:
    def test_is_complete_scored_total_and_scored_done_track_a_full_run(self) -> None:
        session = _make_session()
        assert session.is_complete is False
        assert session.scored_total == DESIGN_A.scored_phrases * len(DESIGN_A.conditions)
        assert session.scored_done == 0

        while not session.is_complete:
            step = session.current
            assert step is not None
            if step.kind in (STEP_PRACTICE, STEP_TRIAL):
                session.record(_trial_for(step.condition.id, step.phrase_index))
            elif step.kind == STEP_WORKLOAD:
                session.record_workload(step.condition.id, _valid_tlx())
            else:
                session.advance()

        assert session.is_complete is True
        assert session.scored_done == session.scored_total


class TestScoredTrialsExcludesPractice:
    def test_scored_trials_returns_only_trial_steps_not_practice(self) -> None:
        """A filter on trial.condition alone cannot tell a practice trial
        from a scored one, since build_plan gives both the same condition
        id: the split has to be by plan position, matched on
        (condition, phrase_index). Recording practice and scored trials
        that share a condition is exactly the case a naive filter gets
        wrong.
        """
        session = _make_session()
        first_condition = session.order[0]
        needed = DESIGN_A.practice_phrases + DESIGN_A.scored_phrases
        trial_step_trials = []
        for _ in range(needed):
            step = session.current
            assert step is not None
            trial = _trial_for(step.condition.id, step.phrase_index)
            session.record(trial)
            if step.kind == STEP_TRIAL:
                trial_step_trials.append(trial)

        result = session.scored_trials(first_condition.id)

        assert len(result) == DESIGN_A.scored_phrases
        assert set(result) == set(trial_step_trials)


class TestDesignCIsTheFieldStudyDesign:
    def test_freeze_learning_differs_from_the_task_battery_designs(self) -> None:
        """Section 5.2: freezing exists to keep the prediction-on block
        from training the model it is scored on. Design C has no scored
        block at all, so it is the one design where the learning being
        observed is the point, and the flag must say so.
        """
        assert DESIGN_A.freeze_learning is True
        assert DESIGN_B.freeze_learning is True
        assert DESIGN_C.freeze_learning is False

    def test_design_c_generates_no_scored_steps(self) -> None:
        order = DESIGN_C.conditions
        plan = build_plan(DESIGN_C, order, _phrases_for(DESIGN_C, order))
        assert plan == ()
        assert not any(s.kind == STEP_TRIAL for s in plan)
