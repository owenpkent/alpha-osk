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
    monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))
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

    def test_uses_fallback_pace_when_no_keystrokes(self, analytics: TypingAnalytics) -> None:
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
        monkeypatch.setattr(analytics_mod.time, "time", lambda: analytics._session_start + 10.0)
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

    def test_top_pick_persisted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        stats_file = tmp_path / "analytics.json"
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))
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
        stats_file.write_text(
            json.dumps(
                {
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
                }
            )
        )
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))
        a = TypingAnalytics()
        assert a._alltime_top_pick_count == 0
        assert a.get_session_stats()["alltimeTopPickRate"] == 0.0


class TestLoadCaps:
    """analytics.json is bounded by size before it is ever read (mirroring
    NgramPredictor.load / SnippetStore.load), and word_freq / key_freq are
    both bounded by entry count on load and on save."""

    def test_oversized_file_falls_back_to_empty_lifetime_counters(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats_file = tmp_path / "analytics.json"
        stats_file.write_text(json.dumps({"keystrokes": 12345, "words": 999, "sessions": 7}))
        assert stats_file.stat().st_size > 4
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))
        monkeypatch.setattr(TypingAnalytics, "_MAX_STATS_FILE_BYTES", 4)

        a = TypingAnalytics()

        assert a._alltime_keystrokes == 0
        assert a._alltime_words == 0
        assert a.get_session_stats()["alltimeKeystrokes"] == 0

    def test_partial_failure_never_leaves_a_half_loaded_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A NaN count (valid JSON, invalid data) fails converting
        word_freq to int partway through _load_alltime. The scalar
        counters parsed earlier in the same call must not stick around:
        either the whole load succeeds, or none of it does."""
        stats_file = tmp_path / "analytics.json"
        stats_file.write_text(
            json.dumps({"keystrokes": 999, "words": 999, "word_freq": {"x": float("nan")}})
        )
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))

        a = TypingAnalytics()

        assert a._alltime_keystrokes == 0
        assert a._alltime_words == 0

    @pytest.mark.parametrize(
        "payload",
        (
            {"keystrokes": "lots"},
            {"keystrokes": None},
            {"keystrokes": [1, 2]},
            {"keystrokes": True},
            {"minutes": "3.5"},
            {"minutes": float("inf")},
        ),
        ids=("str", "null", "list", "bool", "minutes-str", "minutes-inf"),
    )
    def test_a_wrong_typed_scalar_discards_the_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict
    ) -> None:
        """The scalar counters need the same type validation the frequency
        dicts already had.

        analytics.json is replaced wholesale by a Data Backup import from a
        file the user picked, so its contents are untrusted. A field that
        loaded as the wrong type would not fail here at all: it would fail
        much later, inside save(), where every one of these values is fed
        into an addition, and that runs from aboutToQuit and from
        exportUserData.
        """
        stats_file = tmp_path / "analytics.json"
        stats_file.write_text(json.dumps({"words": 42, **payload}))
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))

        a = TypingAnalytics()

        assert a._alltime_keystrokes == 0
        assert a._alltime_words == 0, "a rejected file must not leave sibling fields loaded"

    def test_negative_counters_clamp_to_zero(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A negative lifetime counter is meaningless and would render on
        the dashboard as a negative 'keystrokes saved'."""
        stats_file = tmp_path / "analytics.json"
        stats_file.write_text(json.dumps({"keystrokes_saved": -5000, "minutes": -3.0}))
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))

        a = TypingAnalytics()

        assert a._alltime_keystrokes_saved == 0
        assert a._alltime_minutes == 0.0

    def test_a_non_object_file_is_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats_file = tmp_path / "analytics.json"
        stats_file.write_text(json.dumps([1, 2, 3]))
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))

        assert TypingAnalytics()._alltime_keystrokes == 0

    def test_save_never_raises_on_a_poisoned_counter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """save() builds its whole payload out of additions, so it does that
        inside its try. It runs from aboutToQuit and exportUserData, neither
        of which can afford a propagating TypeError."""
        stats_file = tmp_path / "analytics.json"
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))
        a = TypingAnalytics()
        a._alltime_keystrokes = "poisoned"  # type: ignore[assignment]

        a.save()  # must not raise

    def test_word_freq_capped_on_load(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stats_file = tmp_path / "analytics.json"
        stats_file.write_text(json.dumps({"word_freq": {f"word{i}": i for i in range(10)}}))
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))
        monkeypatch.setattr(TypingAnalytics, "_WORD_FREQ_CAP", 3)

        a = TypingAnalytics()

        assert len(a._alltime_word_freq) == 3
        # The heaviest hitters survive (most_common), not an arbitrary slice.
        assert a._alltime_word_freq.most_common(1)[0][0] == "word9"

    def test_key_freq_capped_on_load(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        stats_file = tmp_path / "analytics.json"
        stats_file.write_text(
            json.dumps({"key_freq": {chr(97 + i): i for i in range(10)}})  # a..j
        )
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))
        monkeypatch.setattr(TypingAnalytics, "_KEY_FREQ_CAP", 3)

        a = TypingAnalytics()

        assert len(a._alltime_key_freq) == 3

    def test_key_freq_capped_on_save(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        stats_file = tmp_path / "analytics.json"
        monkeypatch.setattr(TypingAnalytics, "_get_stats_path", staticmethod(lambda: stats_file))
        monkeypatch.setattr(TypingAnalytics, "_KEY_FREQ_CAP", 3)
        a = TypingAnalytics()
        for i in range(10):
            for _ in range(i + 1):
                a.record_keystroke(chr(97 + i))

        a.save()

        on_disk = json.loads(stats_file.read_text())
        assert len(on_disk["key_freq"]) == 3
