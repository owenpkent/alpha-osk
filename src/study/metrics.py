"""Text-entry metrics for the user study.

Pure functions plus frozen dataclasses. No I/O, no Qt, no logging: a Trial is
handed in fully formed (by whatever records the study session) and every
function here is a deterministic computation over it, so the metrics can be
unit tested without touching the keyboard, a display, or a filesystem.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

EVENT_KINDS = ("char", "backspace", "pill", "special", "offer")

# The physical clicks a participant paid for. "offer" is instrumentation
# (a prediction row appearing on screen) and never counts as an action.
_INPUT_ACTION_KINDS = ("char", "backspace", "pill", "special")


@dataclass(frozen=True)
class Event:
    """One recorded action inside a trial."""

    t_ms: int
    kind: str
    text: str = ""
    pill_rank: int | None = None
    offered: tuple[str, ...] = ()


@dataclass(frozen=True)
class Trial:
    presented: str
    transcribed: str
    events: tuple[Event, ...]
    condition: str
    participant: str
    phrase_index: int


@dataclass(frozen=True)
class ErrorCounts:
    """Soukoreff and MacKenzie (2003) input-stream error taxonomy.

    This is the widely used approximation, not the full alignment-based
    taxonomy: the full version aligns the input stream against the presented
    text character by character, which is ambiguous the moment a single pill
    tap inserts several characters at once. The approximation below trades
    that precision for a definition that still makes sense on a predictive
    keyboard.
    """

    correct: int
    incorrect_not_fixed: int
    incorrect_fixed: int
    fixes: int

    @property
    def _denominator(self) -> int:
        return self.correct + self.incorrect_not_fixed + self.incorrect_fixed

    @property
    def uncorrected_error_rate(self) -> float:
        denom = self._denominator
        if denom == 0:
            return 0.0
        return self.incorrect_not_fixed / denom * 100.0

    @property
    def corrected_error_rate(self) -> float:
        denom = self._denominator
        if denom == 0:
            return 0.0
        return self.incorrect_fixed / denom * 100.0

    @property
    def total_error_rate(self) -> float:
        denom = self._denominator
        if denom == 0:
            return 0.0
        return (self.incorrect_not_fixed + self.incorrect_fixed) / denom * 100.0


def levenshtein(a: str, b: str) -> int:
    """Exact edit distance via two-row dynamic programming, O(min(len)) memory."""
    if len(a) < len(b):
        a, b = b, a
    previous_row = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current_row = [i] + [0] * len(b)
        for j, char_b in enumerate(b, start=1):
            deletion = previous_row[j] + 1
            insertion = current_row[j - 1] + 1
            substitution = previous_row[j - 1] + (0 if char_a == char_b else 1)
            current_row[j] = min(deletion, insertion, substitution)
        previous_row = current_row
    return previous_row[-1]


def input_actions(trial: Trial) -> tuple[Event, ...]:
    """Every event that was a physical click the participant paid for."""
    return tuple(e for e in trial.events if e.kind in _INPUT_ACTION_KINDS)


def entry_rate_wpm(trial: Trial) -> float:
    """Standard Wobbrock/MacKenzie words-per-minute.

    (len(transcribed) - 1) / seconds * 60 / 5, where seconds spans the first
    to the last input-action event. The "- 1" is deliberate and standard:
    timing starts on the first keypress, so that first character is free.
    """
    actions = input_actions(trial)
    if len(actions) < 2 or not trial.transcribed:
        return 0.0
    seconds = (actions[-1].t_ms - actions[0].t_ms) / 1000.0
    if seconds <= 0:
        return 0.0
    return (len(trial.transcribed) - 1) / seconds * 60.0 / 5.0


def msd_error_rate(trial: Trial) -> float:
    """Minimum string distance error rate, as a percentage."""
    denom = max(len(trial.presented), len(trial.transcribed))
    if denom == 0:
        return 0.0
    return levenshtein(trial.presented, trial.transcribed) / denom * 100.0


def keystrokes_per_character(trial: Trial) -> float:
    """Input actions per character of the final transcript.

    On a predictive keyboard this legitimately goes BELOW 1.0, because one
    pill tap contributes many characters at the cost of one action. That is
    the opposite of the physical-keyboard case, where KSPC >= 1 always, and
    it is the point of measuring it here: it is direct evidence that
    prediction is doing work, not an artefact to explain away.
    """
    if not trial.transcribed:
        return 0.0
    return len(input_actions(trial)) / len(trial.transcribed)


def realised_savings(trial: Trial) -> float:
    """Percentage of keystrokes avoided, floored at 0.0 but not capped above.

    This is the directly measured counterpart to the offline benchmark's
    keystroke savings rate (see ksr.py in scripts/bench). Comparing the two,
    what the engine promises against what a real participant actually saved,
    is the whole point of running this study.
    """
    if not trial.transcribed:
        return 0.0
    ratio = len(input_actions(trial)) / len(trial.transcribed)
    return max(0.0, (1.0 - ratio) * 100.0)


def error_taxonomy(trial: Trial) -> ErrorCounts:
    """Soukoreff/MacKenzie C / INF / IF / F counts for one trial.

    INF is the levenshtein distance between what was presented and what was
    left on screen: errors the participant never fixed. F counts every
    backspace they pressed, fixed or not. IF, characters actually removed by
    a backspace, needs the event stream replayed rather than counted
    directly, because a backspace with nothing left to delete (a slip at the
    start of a word, or two backspaces in a row) still costs a click (it
    counts toward F) without removing a character (it does not count toward
    IF).
    """
    fixes = 0
    incorrect_fixed = 0
    running: list[str] = []
    for event in trial.events:
        if event.kind == "backspace":
            fixes += 1
            if running:
                running.pop()
                incorrect_fixed += 1
        elif event.kind in ("char", "pill", "special"):
            running.extend(event.text)
    incorrect_not_fixed = levenshtein(trial.presented, trial.transcribed)
    correct = max(0, len(trial.transcribed) - incorrect_not_fixed)
    return ErrorCounts(
        correct=correct,
        incorrect_not_fixed=incorrect_not_fixed,
        incorrect_fixed=incorrect_fixed,
        fixes=fixes,
    )


def pill_acceptance_latency_ms(trial: Trial) -> tuple[int, ...]:
    """Gap, in ms, from each pill's most recent matching offer to its tap.

    A pill with no preceding offer that named its text is skipped outright
    rather than given a sentinel: there is nothing to measure a latency
    against, and a 0 or a -1 here would silently read as a real value in an
    average.
    """
    gaps: list[int] = []
    last_offer_t_ms: dict[str, int] = {}
    for event in trial.events:
        if event.kind == "offer":
            for word in event.offered:
                last_offer_t_ms[word] = event.t_ms
        elif event.kind == "pill":
            offer_t = last_offer_t_ms.get(event.text)
            if offer_t is not None:
                gaps.append(event.t_ms - offer_t)
    return tuple(gaps)


def summarise(trials: Sequence[Trial]) -> dict[str, float]:
    """Aggregate metrics across trials of one condition.

    Per-trial rate metrics are averaged unweighted across trials: each
    phrase is one observation, which is the convention in this literature
    (a long phrase does not get to outvote a short one). Counts are totals.
    """
    if not trials:
        return {
            "trials": 0.0,
            "entry_rate_wpm": 0.0,
            "msd_error_rate": 0.0,
            "kspc": 0.0,
            "realised_savings": 0.0,
            "uncorrected_error_rate": 0.0,
            "corrected_error_rate": 0.0,
            "total_error_rate": 0.0,
            "median_pill_latency_ms": 0.0,
            "pills_accepted": 0.0,
            "input_actions": 0.0,
            "characters": 0.0,
        }

    condition = trials[0].condition
    if any(trial.condition != condition for trial in trials):
        raise ValueError("summarise() requires every trial to share one condition")

    n = len(trials)
    wpm_sum = 0.0
    msd_sum = 0.0
    kspc_sum = 0.0
    savings_sum = 0.0
    uncorrected_sum = 0.0
    corrected_sum = 0.0
    total_sum = 0.0
    all_latencies: list[int] = []
    pills_accepted = 0
    total_input_actions = 0
    total_characters = 0

    for trial in trials:
        wpm_sum += entry_rate_wpm(trial)
        msd_sum += msd_error_rate(trial)
        kspc_sum += keystrokes_per_character(trial)
        savings_sum += realised_savings(trial)
        errors = error_taxonomy(trial)
        uncorrected_sum += errors.uncorrected_error_rate
        corrected_sum += errors.corrected_error_rate
        total_sum += errors.total_error_rate
        all_latencies.extend(pill_acceptance_latency_ms(trial))
        actions = input_actions(trial)
        pills_accepted += sum(1 for e in actions if e.kind == "pill")
        total_input_actions += len(actions)
        total_characters += len(trial.transcribed)

    median_latency = float(statistics.median(all_latencies)) if all_latencies else 0.0

    return {
        "trials": float(n),
        "entry_rate_wpm": wpm_sum / n,
        "msd_error_rate": msd_sum / n,
        "kspc": kspc_sum / n,
        "realised_savings": savings_sum / n,
        "uncorrected_error_rate": uncorrected_sum / n,
        "corrected_error_rate": corrected_sum / n,
        "total_error_rate": total_sum / n,
        "median_pill_latency_ms": median_latency,
        "pills_accepted": float(pills_accepted),
        "input_actions": float(total_input_actions),
        "characters": float(total_characters),
    }
