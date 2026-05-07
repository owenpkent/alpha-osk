"""Tests for the TypingAnalytics counters surfaced on the dashboard."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analytics import TypingAnalytics


@pytest.fixture
def analytics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TypingAnalytics:
    """A fresh TypingAnalytics whose persisted stats live in tmp_path."""
    stats_file = tmp_path / "analytics.json"
    monkeypatch.setattr(
        TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file)
    )
    return TypingAnalytics()


class TestTopPickCount:
    """Top-pick rate is "% of picks where the first suggestion was right"."""

    def test_rank_one_increments_top_pick(self, analytics: TypingAnalytics) -> None:
        analytics.record_prediction_selected("hello", rank=1, keystrokes_saved=3)
        stats = analytics.get_session_stats()
        assert stats["topPickRate"] == 100.0

    def test_other_ranks_do_not_increment(self, analytics: TypingAnalytics) -> None:
        analytics.record_prediction_selected("hello", rank=2)
        analytics.record_prediction_selected("world", rank=3)
        stats = analytics.get_session_stats()
        assert stats["topPickRate"] == 0.0

    def test_mixed_ranks(self, analytics: TypingAnalytics) -> None:
        analytics.record_prediction_selected("a", rank=1)
        analytics.record_prediction_selected("b", rank=1)
        analytics.record_prediction_selected("c", rank=3)
        analytics.record_prediction_selected("d", rank=2)
        stats = analytics.get_session_stats()
        assert stats["topPickRate"] == 50.0  # 2 of 4

    def test_no_picks_yields_zero_rate(self, analytics: TypingAnalytics) -> None:
        stats = analytics.get_session_stats()
        assert stats["topPickRate"] == 0.0


class TestTimeSaved:
    """Time saved uses the user's own keystroke pace, not a constant."""

    def test_no_savings_yields_zero(self, analytics: TypingAnalytics) -> None:
        for c in "hello":
            analytics.record_keystroke(c)
        stats = analytics.get_session_stats()
        assert stats["timeSavedSeconds"] == 0.0

    def test_uses_fallback_pace_when_no_keystrokes(
        self, analytics: TypingAnalytics
    ) -> None:
        # A prediction selected before any keystrokes have been counted
        # has no observed pace to draw from; the fallback of 0.5 s/key
        # keeps the tile from rendering "0 s saved" for a fresh user.
        analytics.record_prediction_selected("hello", rank=1, keystrokes_saved=10)
        # record_prediction_selected calls record_word_completed but does
        # NOT count keystrokes -- pace falls back to 0.5 s/key.
        # session pace = (elapsed_min * 60) / 0  => fallback 0.5.
        stats = analytics.get_session_stats()
        assert stats["timeSavedSeconds"] == pytest.approx(5.0)

    def test_uses_observed_pace(
        self, analytics: TypingAnalytics, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Force a known elapsed time so the pace math is deterministic.
        # Need >= 6 s so the elapsed_min floor of 0.1 doesn't kick in.
        # 20 keystrokes over 10 seconds = 0.5 s/key.  10 saved at that
        # pace = 5 s of typing avoided.
        import src.analytics as analytics_mod

        for c in "abcdefghijklmnopqrst":
            analytics.record_keystroke(c)
        monkeypatch.setattr(
            analytics_mod.time, "time",
            lambda: analytics._session_start + 10.0
        )
        analytics._keystrokes_saved = 10
        stats = analytics.get_session_stats()
        assert stats["timeSavedSeconds"] == pytest.approx(5.0)


class TestQualityScoreRemoved:
    """The composite quality score and its API are gone."""

    def test_no_quality_score_fields(self, analytics: TypingAnalytics) -> None:
        stats = analytics.get_session_stats()
        assert "qualityScore" not in stats
        assert "alltimeQualityScore" not in stats

    def test_no_compute_method(self) -> None:
        assert not hasattr(TypingAnalytics, "_compute_quality_score")


class TestPersistenceRoundTrip:
    """top_pick_count must survive save/load."""

    def test_top_pick_persisted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats_file = tmp_path / "analytics.json"
        monkeypatch.setattr(
            TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file)
        )
        a = TypingAnalytics()
        a.record_prediction_selected("hello", rank=1, keystrokes_saved=3)
        a.record_prediction_selected("world", rank=2)
        a.save()

        on_disk = json.loads(stats_file.read_text())
        assert on_disk["top_pick_count"] == 1
        assert on_disk["prediction_rank_count"] == 2

        b = TypingAnalytics()
        # Lifetime rate counts the picks loaded from disk: 1 of 2
        # were rank-1 = 50%.
        assert b.get_session_stats()["alltimeTopPickRate"] == 50.0

    def test_load_tolerates_missing_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Older analytics.json files (pre-this-change) won't have the
        # field at all.  Load must not crash and must default to 0.
        stats_file = tmp_path / "analytics.json"
        stats_file.write_text(json.dumps({
            "keystrokes": 100,
            "words": 20,
            "predictions": 5,
            "keystrokes_saved": 30,
            "sessions": 3,
            "minutes": 12.5,
            "backspaces": 2,
            "prediction_offers": 8,
            "prediction_rank_sum": 9,
            "prediction_rank_count": 5,
        }))
        monkeypatch.setattr(
            TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file)
        )
        a = TypingAnalytics()
        assert a._alltime_top_pick_count == 0
        assert a.get_session_stats()["alltimeTopPickRate"] == 0.0
