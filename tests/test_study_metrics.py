"""Tests for src/study/metrics.py.

No Qt, no conftest fixtures: the module under test is pure functions over
plain dataclasses, so these tests build Trial/Event values directly.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from src.study.metrics import (
    ErrorCounts,
    Event,
    Trial,
    entry_rate_wpm,
    error_taxonomy,
    input_actions,
    keystrokes_per_character,
    levenshtein,
    msd_error_rate,
    pill_acceptance_latency_ms,
    realised_savings,
    summarise,
)


def _trial(
    events: Sequence[Event],
    presented: str = "hello world",
    transcribed: str | None = None,
    condition: str = "alpha_on",
    participant: str = "P1",
    phrase_index: int = 0,
) -> Trial:
    return Trial(
        presented=presented,
        transcribed=presented if transcribed is None else transcribed,
        events=tuple(events),
        condition=condition,
        participant=participant,
        phrase_index=phrase_index,
    )


class TestEntryRateIsTheStandardFormula:
    """(len(transcribed) - 1) / seconds * 60 / 5, timed over input actions only."""

    def test_a_hand_computed_example(self) -> None:
        # 25 characters, 25 char events spaced 250 ms apart so the span
        # from first to last event is exactly 6.0 s.
        transcribed = "a" * 25
        events = [Event(t_ms=i * 250, kind="char", text="a") for i in range(25)]
        trial = _trial(events, transcribed=transcribed)
        assert entry_rate_wpm(trial) == pytest.approx((25 - 1) / 6.0 * 60 / 5)
        assert entry_rate_wpm(trial) == pytest.approx(48.0)

    def test_offer_events_are_not_timed_against(self) -> None:
        """An offer is instrumentation and must not count as an input action."""
        transcribed = "a" * 25
        events = [
            Event(t_ms=-1000, kind="offer", offered=("a",)),
            *(Event(t_ms=i * 250, kind="char", text="a") for i in range(25)),
        ]
        trial = _trial(events, transcribed=transcribed)
        assert entry_rate_wpm(trial) == pytest.approx(48.0)

    def test_a_single_input_action_is_zero(self) -> None:
        trial = _trial([Event(t_ms=0, kind="char", text="a")], transcribed="a")
        assert entry_rate_wpm(trial) == 0.0

    def test_zero_elapsed_time_is_zero(self) -> None:
        events = [
            Event(t_ms=100, kind="char", text="a"),
            Event(t_ms=100, kind="char", text="b"),
        ]
        trial = _trial(events, transcribed="ab")
        assert entry_rate_wpm(trial) == 0.0

    def test_empty_transcribed_is_zero(self) -> None:
        events = [
            Event(t_ms=0, kind="char", text=""),
            Event(t_ms=1000, kind="backspace"),
        ]
        trial = _trial(events, transcribed="")
        assert entry_rate_wpm(trial) == 0.0


class TestLevenshteinIsExact:
    def test_kitten_sitting(self) -> None:
        assert levenshtein("kitten", "sitting") == 3

    def test_empty_against_three_chars(self) -> None:
        assert levenshtein("", "abc") == 3

    def test_identical_strings(self) -> None:
        assert levenshtein("alpha", "alpha") == 0

    def test_is_symmetric(self) -> None:
        assert levenshtein("kitten", "sitting") == levenshtein("sitting", "kitten")


class TestMsdErrorRate:
    def test_perfect_transcription_is_zero(self) -> None:
        trial = _trial([], presented="the quick fox", transcribed="the quick fox")
        assert msd_error_rate(trial) == 0.0

    def test_one_substitution_in_ten_characters_is_ten_percent(self) -> None:
        trial = _trial([], presented="abcdefghij", transcribed="abcdefghiX")
        assert msd_error_rate(trial) == pytest.approx(10.0)

    def test_both_empty_is_zero(self) -> None:
        trial = _trial([], presented="", transcribed="")
        assert msd_error_rate(trial) == 0.0


class TestKspcCanGoBelowOne:
    """The pair that matters: a pill counted as N keystrokes would pass the
    backspace case below but fail this one.
    """

    def test_a_pill_contributing_eight_characters_drops_kspc_below_one(self) -> None:
        # "pro" typed char by char, then one pill tap inserts "gramming".
        events = [
            Event(t_ms=0, kind="char", text="p"),
            Event(t_ms=100, kind="char", text="r"),
            Event(t_ms=200, kind="char", text="o"),
            Event(t_ms=300, kind="pill", text="gramming", pill_rank=1),
        ]
        trial = _trial(events, presented="programming", transcribed="programming")
        assert keystrokes_per_character(trial) == pytest.approx(4 / 11)
        assert keystrokes_per_character(trial) < 1.0

    def test_backspaces_push_kspc_above_one(self) -> None:
        # c, a, x (typo), backspace, t -> "cat" in 5 actions for 3 characters.
        events = [
            Event(t_ms=0, kind="char", text="c"),
            Event(t_ms=100, kind="char", text="a"),
            Event(t_ms=200, kind="char", text="x"),
            Event(t_ms=300, kind="backspace"),
            Event(t_ms=400, kind="char", text="t"),
        ]
        trial = _trial(events, presented="cat", transcribed="cat")
        assert keystrokes_per_character(trial) == pytest.approx(5 / 3)
        assert keystrokes_per_character(trial) > 1.0

    def test_empty_transcribed_is_zero(self) -> None:
        trial = _trial([Event(t_ms=0, kind="char", text="")], transcribed="")
        assert keystrokes_per_character(trial) == 0.0


class TestRealisedSavings:
    def test_character_by_character_typing_saves_nothing(self) -> None:
        events = [
            Event(t_ms=0, kind="char", text="c"),
            Event(t_ms=100, kind="char", text="a"),
            Event(t_ms=200, kind="char", text="t"),
        ]
        trial = _trial(events, presented="cat", transcribed="cat")
        assert realised_savings(trial) == 0.0

    def test_a_pill_tap_produces_positive_savings(self) -> None:
        events = [
            Event(t_ms=0, kind="char", text="p"),
            Event(t_ms=100, kind="char", text="r"),
            Event(t_ms=200, kind="char", text="o"),
            Event(t_ms=300, kind="pill", text="gramming", pill_rank=1),
        ]
        trial = _trial(events, presented="programming", transcribed="programming")
        assert realised_savings(trial) == pytest.approx((1 - 4 / 11) * 100)
        assert realised_savings(trial) > 0.0

    def test_the_floor_holds_for_a_backspace_heavy_trial(self) -> None:
        """More actions than characters would go negative unclamped; it must not."""
        events = [
            Event(t_ms=0, kind="char", text="c"),
            Event(t_ms=100, kind="char", text="a"),
            Event(t_ms=200, kind="char", text="x"),
            Event(t_ms=300, kind="backspace"),
            Event(t_ms=400, kind="char", text="t"),
        ]
        trial = _trial(events, presented="cat", transcribed="cat")
        assert realised_savings(trial) == 0.0


class TestErrorTaxonomy:
    def test_a_clean_trial_is_all_correct(self) -> None:
        events = [
            Event(t_ms=0, kind="char", text="h"),
            Event(t_ms=100, kind="char", text="i"),
        ]
        trial = _trial(events, presented="hi", transcribed="hi")
        counts = error_taxonomy(trial)
        assert counts == ErrorCounts(correct=2, incorrect_not_fixed=0, incorrect_fixed=0, fixes=0)
        assert counts.uncorrected_error_rate == 0.0
        assert counts.corrected_error_rate == 0.0
        assert counts.total_error_rate == 0.0

    def test_two_uncorrected_substitutions_and_three_backspaces(self) -> None:
        """Hand-computed: presented "abcdef" vs transcribed "abXdeZ" is a
        2-substitution levenshtein distance (INF=2). The event stream below
        presses backspace three times (F=3): the first is a no-op on an
        empty transcript, the other two each remove a real character
        (IF=2). correct = max(0, 6 - 2) = 4.
        """
        events = [
            Event(t_ms=0, kind="backspace"),  # no-op: nothing to delete yet
            Event(t_ms=100, kind="char", text="a"),
            Event(t_ms=200, kind="char", text="b"),
            Event(t_ms=300, kind="char", text="c"),
            Event(t_ms=400, kind="backspace"),  # removes "c"
            Event(t_ms=500, kind="char", text="X"),
            Event(t_ms=600, kind="char", text="d"),
            Event(t_ms=700, kind="char", text="e"),
            Event(t_ms=800, kind="char", text="f"),
            Event(t_ms=900, kind="backspace"),  # removes "f"
            Event(t_ms=1000, kind="char", text="Z"),
        ]
        trial = _trial(events, presented="abcdef", transcribed="abXdeZ")
        counts = error_taxonomy(trial)
        assert counts == ErrorCounts(correct=4, incorrect_not_fixed=2, incorrect_fixed=2, fixes=3)
        assert counts.uncorrected_error_rate == pytest.approx(25.0)
        assert counts.corrected_error_rate == pytest.approx(25.0)
        assert counts.total_error_rate == pytest.approx(50.0)

    def test_zero_denominator_returns_zero_not_an_exception(self) -> None:
        trial = _trial([], presented="", transcribed="")
        counts = error_taxonomy(trial)
        assert counts == ErrorCounts(correct=0, incorrect_not_fixed=0, incorrect_fixed=0, fixes=0)
        assert counts.uncorrected_error_rate == 0.0
        assert counts.corrected_error_rate == 0.0
        assert counts.total_error_rate == 0.0


class TestPillAcceptanceLatency:
    def test_a_pill_with_a_preceding_matching_offer(self) -> None:
        events = [
            Event(t_ms=100, kind="offer", offered=("hello", "help")),
            Event(t_ms=150, kind="pill", text="hello", pill_rank=1),
        ]
        trial = _trial(events)
        assert pill_acceptance_latency_ms(trial) == (50,)

    def test_a_pill_with_no_preceding_offer_is_skipped_not_sentinelled(self) -> None:
        """The near-miss: an implementation emitting -1 or 0 for "no offer"
        would corrupt every average this feeds. It must contribute nothing.
        """
        events = [Event(t_ms=150, kind="pill", text="world", pill_rank=1)]
        trial = _trial(events)
        assert pill_acceptance_latency_ms(trial) == ()

    def test_an_offer_after_the_pill_does_not_count(self) -> None:
        events = [
            Event(t_ms=150, kind="pill", text="hello", pill_rank=1),
            Event(t_ms=200, kind="offer", offered=("hello",)),
        ]
        trial = _trial(events)
        assert pill_acceptance_latency_ms(trial) == ()

    def test_multiple_pills_pool_in_trial_order(self) -> None:
        events = [
            Event(t_ms=0, kind="offer", offered=("hi",)),
            Event(t_ms=40, kind="pill", text="hi", pill_rank=1),
            Event(t_ms=100, kind="offer", offered=("there",)),
            Event(t_ms=180, kind="pill", text="there", pill_rank=1),
        ]
        trial = _trial(events)
        assert pill_acceptance_latency_ms(trial) == (40, 80)


class TestInputActionsHelper:
    def test_offer_is_excluded(self) -> None:
        events = [
            Event(t_ms=0, kind="offer", offered=("a",)),
            Event(t_ms=10, kind="char", text="a"),
            Event(t_ms=20, kind="backspace"),
            Event(t_ms=30, kind="pill", text="apple"),
            Event(t_ms=40, kind="special", text=" "),
        ]
        trial = _trial(events)
        assert [e.kind for e in input_actions(trial)] == ["char", "backspace", "pill", "special"]


class TestSummarise:
    def _make(self, condition: str, presented: str, transcribed: str, span_ms: int) -> Trial:
        events = [
            Event(t_ms=0, kind="char", text=transcribed[:1] if transcribed else ""),
            Event(t_ms=span_ms, kind="char", text=transcribed[1:] if len(transcribed) > 1 else ""),
        ]
        return _trial(events, presented=presented, transcribed=transcribed, condition=condition)

    def test_empty_sequence_returns_all_zeros(self) -> None:
        result = summarise([])
        assert result["trials"] == 0.0
        assert all(value == 0.0 for value in result.values())

    def test_mixed_conditions_raise(self) -> None:
        trials = [
            self._make("alpha_on", "hi", "hi", 1000),
            self._make("alpha_off", "hi", "hi", 1000),
        ]
        with pytest.raises(ValueError):
            summarise(trials)

    def test_counts_are_totals_and_rates_are_unweighted_averages(self) -> None:
        # Trial 1: perfect, 2 char actions, 2 characters -> wpm (1)/1*60/5=12.0
        trial_1 = self._make("alpha_on", "hi", "hi", 1000)
        # Trial 2: perfect, 2 char actions, 2 characters over 2s -> wpm (1)/2*60/5=6.0
        trial_2 = self._make("alpha_on", "ok", "ok", 2000)
        result = summarise([trial_1, trial_2])

        assert result["trials"] == 2.0
        assert result["input_actions"] == 4.0
        assert result["characters"] == 4.0
        assert result["pills_accepted"] == 0.0
        assert result["entry_rate_wpm"] == pytest.approx((12.0 + 6.0) / 2)
        assert result["msd_error_rate"] == 0.0
        assert result["kspc"] == pytest.approx(1.0)
        assert result["realised_savings"] == pytest.approx(0.0)

    def test_pill_latencies_pool_across_trials_for_the_median(self) -> None:
        events_1 = [
            Event(t_ms=0, kind="offer", offered=("hi",)),
            Event(t_ms=50, kind="pill", text="hi", pill_rank=1),
        ]
        events_2 = [
            Event(t_ms=0, kind="offer", offered=("ok",)),
            Event(t_ms=150, kind="pill", text="ok", pill_rank=1),
        ]
        trial_1 = _trial(events_1, presented="hi", transcribed="hi", condition="alpha_on")
        trial_2 = _trial(events_2, presented="ok", transcribed="ok", condition="alpha_on")
        result = summarise([trial_1, trial_2])
        assert result["median_pill_latency_ms"] == pytest.approx(100.0)
        assert result["pills_accepted"] == 2.0
