"""Conditions, counterbalancing, and the block state machine.

The three designs in section 4 of the protocol are entries in :data:`DESIGNS`,
not three implementations.  A design is a condition list plus counts, so
choosing between "prediction on versus off", "and also the system keyboard"
and "no task battery at all" is configuration.  That is deliberate: the
protocol documents all three because the choice between them is a recruitment
question that may be answered differently once real participants exist, and
the harness should not have to be rewritten when it is.

Nothing here touches disk or Qt.  A session is a plan (a precomputed list of
steps), an index into it, and the trials recorded so far, which makes resume
after a break, or on another day, an integer rather than a reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from .metrics import Trial

STEP_PRACTICE = "practice"
STEP_TRIAL = "trial"
STEP_WORKLOAD = "workload"
STEP_BREAK = "break"


@dataclass(frozen=True)
class Condition:
    """One arm of the study."""

    id: str
    label: str
    keyboard: str
    """"alpha" for Alpha-OSK, "system" for the OS on-screen keyboard."""
    predictions: bool

    def __post_init__(self) -> None:
        if self.keyboard not in ("alpha", "system"):
            raise ValueError(f"unknown keyboard: {self.keyboard!r}")


@dataclass(frozen=True)
class Design:
    id: str
    label: str
    conditions: tuple[Condition, ...]
    scored_phrases: int
    practice_phrases: int
    freeze_learning: bool = True
    """Whether model mutation is suspended for the session.

    True for every task-battery design, and the reason is protocol section
    5.2: without it the prediction-on block trains the model it is scored on,
    which inflates that condition specifically and does so more the later the
    block runs.  A field-study design leaves it False, because there the
    learning is the thing being observed.
    """

    def __post_init__(self) -> None:
        if not self.conditions:
            raise ValueError("a design needs at least one condition")
        ids = [c.id for c in self.conditions]
        if len(set(ids)) != len(ids):
            raise ValueError(f"duplicate condition ids: {ids}")


ALPHA_ON = Condition(id="alpha_on", label="Predictions on", keyboard="alpha", predictions=True)
ALPHA_OFF = Condition(id="alpha_off", label="Predictions off", keyboard="alpha", predictions=False)
SYSTEM_KEYBOARD = Condition(
    id="system", label="System keyboard", keyboard="system", predictions=False
)

DESIGN_A = Design(
    id="A",
    label="Prediction on versus off",
    conditions=(ALPHA_ON, ALPHA_OFF),
    scored_phrases=10,
    practice_phrases=5,
)
DESIGN_B = Design(
    id="B",
    label="Prediction on versus off, plus the system keyboard",
    conditions=(ALPHA_ON, ALPHA_OFF, SYSTEM_KEYBOARD),
    scored_phrases=10,
    practice_phrases=5,
)
DESIGN_C = Design(
    id="C",
    label="Longitudinal field use",
    conditions=(ALPHA_ON,),
    scored_phrases=0,
    practice_phrases=0,
    freeze_learning=False,
)

DESIGNS: Mapping[str, Design] = {d.id: d for d in (DESIGN_A, DESIGN_B, DESIGN_C)}

DEFAULT_DESIGN = DESIGN_A


def williams_orders(n: int) -> tuple[tuple[int, ...], ...]:
    """Every condition order in a Williams design over ``n`` conditions.

    A plain Latin square balances each condition across positions but not
    across what preceded it, so a carryover effect (fatigue, or practice
    transferring from one keyboard to the next) lands unevenly on particular
    conditions.  A Williams design balances first-order carryover too: every
    condition follows every other equally often.

    Even ``n`` needs one square (``n`` sequences), odd ``n`` needs the square
    and its mirror (``2n``), which is why Design B costs six orders against
    Design A's two, and why section 4 says three arms estimate order effects
    far less well at a realistic sample size.
    """
    if n < 1:
        raise ValueError("n must be at least 1")
    if n == 1:
        return ((0,),)

    # Williams first row: 0, 1, n-1, 2, n-2, 3, n-3 ...
    first: list[int] = []
    for j in range(n):
        if j == 0:
            first.append(0)
        elif j % 2 == 1:
            first.append((j + 1) // 2)
        else:
            first.append(n - j // 2)

    square = tuple(tuple((v + i) % n for v in first) for i in range(n))
    if n % 2 == 0:
        return square
    return square + tuple(tuple(reversed(row)) for row in square)


def order_for(design: Design, participant_index: int) -> tuple[Condition, ...]:
    """The condition order this participant runs, by their enrolment number.

    Assignment is by position in the enrolment sequence rather than at random,
    so the orders stay balanced at every point during recruitment.  A study
    that stops early, which this one might, then still has a balanced sample
    instead of whatever the coin produced.
    """
    orders = williams_orders(len(design.conditions))
    row = orders[participant_index % len(orders)]
    return tuple(design.conditions[i] for i in row)


@dataclass(frozen=True)
class Step:
    """One thing the participant is asked to do."""

    kind: str
    condition: Condition
    block_index: int
    phrase: str = ""
    phrase_index: int = -1

    @property
    def is_scored(self) -> bool:
        return self.kind == STEP_TRIAL


def build_plan(
    design: Design,
    order: Sequence[Condition],
    phrases: Mapping[str, Sequence[str]],
) -> tuple[Step, ...]:
    """Expand a design and a condition order into the full list of steps.

    ``phrases`` maps a condition id to the phrases assigned to it.  They are
    assigned per condition rather than drawn from one pool as the session runs
    because a participant must never meet the same phrase twice: the second
    encounter measures memory, and the whole comparison is between conditions.
    """
    plan: list[Step] = []
    for block_index, condition in enumerate(order):
        assigned = list(phrases.get(condition.id, ()))
        needed = design.practice_phrases + design.scored_phrases
        if len(assigned) < needed:
            raise ValueError(
                f"condition {condition.id!r} needs {needed} phrases, got {len(assigned)}"
            )
        cursor = 0
        for _ in range(design.practice_phrases):
            plan.append(
                Step(
                    kind=STEP_PRACTICE,
                    condition=condition,
                    block_index=block_index,
                    phrase=assigned[cursor],
                    phrase_index=cursor,
                )
            )
            cursor += 1
        for _ in range(design.scored_phrases):
            plan.append(
                Step(
                    kind=STEP_TRIAL,
                    condition=condition,
                    block_index=block_index,
                    phrase=assigned[cursor],
                    phrase_index=cursor,
                )
            )
            cursor += 1
        if design.scored_phrases:
            plan.append(Step(kind=STEP_WORKLOAD, condition=condition, block_index=block_index))
        if block_index < len(order) - 1:
            plan.append(Step(kind=STEP_BREAK, condition=condition, block_index=block_index))
    return tuple(plan)


@dataclass
class Session:
    """A run through one participant's plan.

    ``cursor`` is the whole of the resumable state, which is what makes
    "continue on another day" a stored integer rather than a replay.
    """

    design: Design
    order: tuple[Condition, ...]
    plan: tuple[Step, ...]
    participant: str
    cursor: int = 0
    trials: list[Trial] = field(default_factory=list)
    workload: dict[str, dict[str, int]] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        design: Design,
        participant: str,
        participant_index: int,
        phrases: Mapping[str, Sequence[str]],
    ) -> "Session":
        order = order_for(design, participant_index)
        return cls(
            design=design,
            order=order,
            plan=build_plan(design, order, phrases),
            participant=participant,
        )

    @property
    def is_complete(self) -> bool:
        return self.cursor >= len(self.plan)

    @property
    def current(self) -> Step | None:
        return None if self.is_complete else self.plan[self.cursor]

    @property
    def scored_total(self) -> int:
        return sum(1 for s in self.plan if s.is_scored)

    @property
    def scored_done(self) -> int:
        return sum(1 for s in self.plan[: self.cursor] if s.is_scored)

    def advance(self) -> None:
        if not self.is_complete:
            self.cursor += 1

    def record(self, trial: Trial) -> None:
        """Store a finished trial and move on.

        Practice trials are recorded and marked rather than dropped, per
        protocol section 8: a practice block that went badly is evidence about
        the session, and silently discarding it hides that.
        """
        step = self.current
        if step is None:
            raise RuntimeError("session is already complete")
        if step.kind not in (STEP_TRIAL, STEP_PRACTICE):
            raise RuntimeError(f"step {step.kind!r} does not take a trial")
        if trial.condition != step.condition.id:
            raise ValueError(
                f"trial condition {trial.condition!r} does not match step {step.condition.id!r}"
            )
        self.trials.append(trial)
        self.advance()

    def record_workload(self, condition_id: str, scores: Mapping[str, int]) -> None:
        """Store one raw NASA-TLX response and move on.

        Six unweighted scales, each 0 to 20.  Out-of-range values are rejected
        rather than clamped: a scale the UI mis-sent is a bug worth failing on,
        and a silently clamped 21 is indistinguishable from a real 20.

        The condition is checked against the step's own, exactly as
        :meth:`record` checks a trial's.  Workload is keyed by condition and
        is the RQ3 outcome, so a response filed under the wrong key does not
        fail anywhere: it silently swaps the two blocks' perceived-effort
        scores, which is a result rather than an error.
        """
        step = self.current
        if step is None or step.kind != STEP_WORKLOAD:
            raise RuntimeError("not at a workload step")
        if condition_id != step.condition.id:
            raise ValueError(
                f"workload condition {condition_id!r} does not match step {step.condition.id!r}"
            )
        for name, value in scores.items():
            if not 0 <= value <= 20:
                raise ValueError(f"TLX scale {name!r} out of range: {value}")
        self.workload[condition_id] = dict(scores)
        self.advance()

    def scored_trials(self, condition_id: str) -> tuple[Trial, ...]:
        """Scored trials for one condition, practice excluded.

        Practice steps and scored steps both produce a Trial carrying the same
        condition id, so the split is by plan position rather than by anything
        on the trial itself.
        """
        practice = {(s.condition.id, s.phrase_index) for s in self.plan if s.kind == STEP_PRACTICE}
        return tuple(
            t
            for t in self.trials
            if t.condition == condition_id and (t.condition, t.phrase_index) not in practice
        )
