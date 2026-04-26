"""
Typing Analytics — tracks session and all-time statistics.

Tracks keystrokes saved, prediction usage, typing speed, and error rates.
Session stats reset on restart. All-time stats persist to disk.
Data never leaves the device.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PySide6.QtCore import QObject, Signal, Slot

_logger = logging.getLogger("Analytics")


class TypingAnalytics(QObject):
    """Tracks typing statistics for the current session and all-time."""

    statsUpdated = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        # Session stats (reset on restart)
        self._session_start = time.time()
        self._keystroke_count = 0
        self._word_count = 0
        self._prediction_hits = 0
        self._prediction_offers = 0
        self._backspace_count = 0
        self._keystrokes_saved = 0
        self._word_freq: Counter[str] = Counter()
        self._key_freq: Counter[str] = Counter()
        self._prediction_rank_sum = 0
        self._prediction_rank_count = 0
        # WPM samples for sparkline (one per minute)
        self._wpm_samples: List[float] = []
        self._last_sample_time = time.time()
        self._words_at_last_sample = 0

        # All-time stats (loaded from / saved to disk).  Mirrors every
        # session counter so the dashboard can render lifetime versions
        # of every metric (WPM, hit rate, savings %, backspace rate,
        # quality score, top words / keys).  Without persisting these,
        # any aggregate-over-time reading is wrong the moment a session
        # ends.
        self._alltime_keystrokes = 0
        self._alltime_words = 0
        self._alltime_predictions = 0
        self._alltime_keystrokes_saved = 0
        self._alltime_sessions = 0
        self._alltime_minutes = 0.0
        self._alltime_backspaces = 0
        self._alltime_prediction_offers = 0
        self._alltime_prediction_rank_sum = 0
        self._alltime_prediction_rank_count = 0
        self._alltime_word_freq: Counter[str] = Counter()
        self._alltime_key_freq: Counter[str] = Counter()

        # Load persisted stats
        self._stats_path = self._get_stats_path()
        self._load_alltime()
        self._alltime_sessions += 1

    @staticmethod
    def _get_stats_path() -> Path:
        """Get the path for persisted analytics."""
        from .platform import get_config_dir
        return get_config_dir() / "analytics.json"

    def _load_alltime(self) -> None:
        """Load all-time stats from disk."""
        if not self._stats_path.exists():
            return
        try:
            data = json.loads(self._stats_path.read_text())
            self._alltime_keystrokes = data.get("keystrokes", 0)
            self._alltime_words = data.get("words", 0)
            self._alltime_predictions = data.get("predictions", 0)
            self._alltime_keystrokes_saved = data.get("keystrokes_saved", 0)
            self._alltime_sessions = data.get("sessions", 0)
            self._alltime_minutes = data.get("minutes", 0.0)
            self._alltime_backspaces = data.get("backspaces", 0)
            self._alltime_prediction_offers = data.get("prediction_offers", 0)
            self._alltime_prediction_rank_sum = data.get("prediction_rank_sum", 0)
            self._alltime_prediction_rank_count = data.get("prediction_rank_count", 0)
            wf = data.get("word_freq", {})
            kf = data.get("key_freq", {})
            if isinstance(wf, dict):
                self._alltime_word_freq = Counter(
                    {k: int(v) for k, v in wf.items() if isinstance(v, (int, float))}
                )
            if isinstance(kf, dict):
                self._alltime_key_freq = Counter(
                    {k: int(v) for k, v in kf.items() if isinstance(v, (int, float))}
                )
            _logger.info("Loaded all-time analytics: %d words, %d keystrokes saved",
                         self._alltime_words, self._alltime_keystrokes_saved)
        except (json.JSONDecodeError, OSError) as e:
            _logger.warning("Failed to load analytics: %s", e)

    # Cap on persisted unique-word entries.  Top-N display only ever needs
    # the heavy hitters; without a cap, a few years of typing could push
    # word_freq into the megabytes.  Pruning keeps the most-typed entries
    # plus anything from the current session (so an in-progress word
    # frequency is never silently dropped).
    _WORD_FREQ_CAP = 5000

    def save(self) -> None:
        """Save all-time stats to disk (merges current session)."""
        merged_words = self._alltime_word_freq + self._word_freq
        if len(merged_words) > self._WORD_FREQ_CAP:
            # Keep only the top-N by count.  Counter.most_common is O(n log k)
            # which is fine at this scale.
            merged_words = Counter(dict(merged_words.most_common(self._WORD_FREQ_CAP)))
        merged_keys = self._alltime_key_freq + self._key_freq

        data = {
            "keystrokes": self._alltime_keystrokes + self._keystroke_count,
            "words": self._alltime_words + self._word_count,
            "predictions": self._alltime_predictions + self._prediction_hits,
            "keystrokes_saved": self._alltime_keystrokes_saved + self._keystrokes_saved,
            "sessions": self._alltime_sessions,
            "minutes": self._alltime_minutes + (time.time() - self._session_start) / 60,
            "backspaces": self._alltime_backspaces + self._backspace_count,
            "prediction_offers": self._alltime_prediction_offers + self._prediction_offers,
            "prediction_rank_sum": (
                self._alltime_prediction_rank_sum + self._prediction_rank_sum
            ),
            "prediction_rank_count": (
                self._alltime_prediction_rank_count + self._prediction_rank_count
            ),
            "word_freq": dict(merged_words),
            "key_freq": dict(merged_keys),
        }
        try:
            self._stats_path.write_text(json.dumps(data, indent=2))
            _logger.info("Saved analytics to %s", self._stats_path)
        except OSError as e:
            _logger.warning("Failed to save analytics: %s", e)

    def record_keystroke(self, key: str) -> None:
        """Record a character key press."""
        self._keystroke_count += 1
        self._key_freq[key.lower()] += 1
        self._maybe_sample_wpm()

    def record_word_completed(self, word: str) -> None:
        """Record a completed word (on space or return)."""
        if word:
            self._word_count += 1
            self._word_freq[word.lower()] += 1

    def record_prediction_selected(self, word: str, rank: int,
                                   keystrokes_saved: int = 0) -> None:
        """Record when user selects a prediction.

        Args:
            word: The selected word
            rank: Position in the prediction list (1-based)
            keystrokes_saved: Characters the user didn't have to type
        """
        self._prediction_hits += 1
        self._prediction_rank_sum += rank
        self._prediction_rank_count += 1
        self._keystrokes_saved += keystrokes_saved
        self.record_word_completed(word)

    def record_prediction_offered(self) -> None:
        """Record when predictions are shown to the user."""
        self._prediction_offers += 1

    def record_backspace(self) -> None:
        """Record a backspace press."""
        self._backspace_count += 1
        self._keystroke_count += 1

    def _maybe_sample_wpm(self) -> None:
        """Sample WPM once per minute for the sparkline."""
        now = time.time()
        if now - self._last_sample_time >= 60:
            words_this_interval = self._word_count - self._words_at_last_sample
            self._wpm_samples.append(float(words_this_interval))
            self._words_at_last_sample = self._word_count
            self._last_sample_time = now
            # Keep last 30 samples (30 minutes of history)
            if len(self._wpm_samples) > 30:
                self._wpm_samples = self._wpm_samples[-30:]

    @Slot(result="QVariant")
    def get_session_stats(self) -> Dict[str, Any]:
        """Return current session + all-time statistics for QML."""
        elapsed_min = max(0.1, (time.time() - self._session_start) / 60)
        top_words: List[Tuple[str, int]] = self._word_freq.most_common(5)

        total_typed = self._keystroke_count + self._keystrokes_saved
        savings_pct = (
            round(self._keystrokes_saved / max(1, total_typed) * 100, 1)
        )

        # Lifetime aggregates — current session + persisted history.
        alltime_keystrokes = self._alltime_keystrokes + self._keystroke_count
        alltime_words = self._alltime_words + self._word_count
        alltime_predictions = self._alltime_predictions + self._prediction_hits
        alltime_saved = self._alltime_keystrokes_saved + self._keystrokes_saved
        alltime_backspaces = self._alltime_backspaces + self._backspace_count
        alltime_offers = self._alltime_prediction_offers + self._prediction_offers
        alltime_rank_sum = self._alltime_prediction_rank_sum + self._prediction_rank_sum
        alltime_rank_count = self._alltime_prediction_rank_count + self._prediction_rank_count
        alltime_minutes = self._alltime_minutes + elapsed_min
        alltime_total_typed = alltime_keystrokes + alltime_saved

        # Lifetime top words = persisted Counter + current session, then
        # take top 5.  Combining with `+` is right: it sums counts for
        # any word that appears in both.
        alltime_top_words = (self._alltime_word_freq + self._word_freq).most_common(5)

        # Quality scores
        session_quality = self._compute_quality_score()
        alltime_quality = self._compute_quality_score(
            words=alltime_words,
            keystrokes=alltime_keystrokes,
            keystrokes_saved=alltime_saved,
            prediction_hits=alltime_predictions,
            backspaces=alltime_backspaces,
            rank_sum=alltime_rank_sum,
            rank_count=alltime_rank_count,
        )

        return {
            # Session
            "wpm": round(self._word_count / elapsed_min, 1),
            "sessionMinutes": round(elapsed_min, 1),
            "totalWords": self._word_count,
            "totalKeystrokes": self._keystroke_count,
            "keystrokesSaved": self._keystrokes_saved,
            "savingsPercent": savings_pct,
            "predictionHitRate": round(
                self._prediction_hits / max(1, self._word_count) * 100, 1
            ),
            "predictionHits": self._prediction_hits,
            "backspaceRate": round(
                self._backspace_count / max(1, self._keystroke_count) * 100, 1
            ),
            "topWords": [{"word": w, "count": c} for w, c in top_words],
            "wpmSamples": self._wpm_samples,
            "qualityScore": session_quality,

            # Lifetime (= persisted history + current session)
            "alltimeWords": alltime_words,
            "alltimeKeystrokes": alltime_keystrokes,
            "alltimeKeystrokesSaved": alltime_saved,
            "alltimePredictionHits": alltime_predictions,
            "alltimeSessions": self._alltime_sessions,
            "alltimeMinutes": round(alltime_minutes, 1),
            "alltimeWpm": round(alltime_words / max(0.1, alltime_minutes), 1),
            "alltimeSavingsPercent": round(
                alltime_saved / max(1, alltime_total_typed) * 100, 1
            ),
            "alltimePredictionHitRate": round(
                alltime_predictions / max(1, alltime_words) * 100, 1
            ),
            "alltimeBackspaceRate": round(
                alltime_backspaces / max(1, alltime_keystrokes) * 100, 1
            ),
            "alltimePredictionOffers": alltime_offers,
            "alltimeTopWords": [{"word": w, "count": c} for w, c in alltime_top_words],
            "alltimeQualityScore": alltime_quality,
        }

    def _compute_quality_score(
        self,
        *,
        words: int | None = None,
        keystrokes: int | None = None,
        keystrokes_saved: int | None = None,
        prediction_hits: int | None = None,
        backspaces: int | None = None,
        rank_sum: int | None = None,
        rank_count: int | None = None,
    ) -> int:
        """
        Compute a prediction quality score from 0 to 100.

        Defaults to the current session's counters; pass keyword args to
        score over a different aggregate (e.g. lifetime totals).

        Weighted combination:
        - Keystroke savings rate (40%): % of total effort saved.
        - Prediction hit rate (25%): % of words completed via prediction.
        - Rank accuracy (20%): How often users pick the #1 prediction.
          Avg rank 1.0 = perfect, 5.0 = poor.
          Scored as 100 * (1 - (avg_rank - 1) / 4), clamped.
        - Low correction rate (15%): Inverse of backspace rate.

        Returns 0 if fewer than 5 words have been typed (not enough data).
        """
        words = self._word_count if words is None else words
        keystrokes = self._keystroke_count if keystrokes is None else keystrokes
        keystrokes_saved = self._keystrokes_saved if keystrokes_saved is None else keystrokes_saved
        prediction_hits = self._prediction_hits if prediction_hits is None else prediction_hits
        backspaces = self._backspace_count if backspaces is None else backspaces
        rank_sum = self._prediction_rank_sum if rank_sum is None else rank_sum
        rank_count = self._prediction_rank_count if rank_count is None else rank_count

        if words < 5:
            return 0

        total_effort = keystrokes + keystrokes_saved
        savings = (keystrokes_saved / max(1, total_effort)) * 100
        hit_rate = (prediction_hits / max(1, words)) * 100

        if rank_count > 0:
            avg_rank = rank_sum / rank_count
            rank_score = max(0, min(100, 100 * (1 - (avg_rank - 1) / 4)))
        else:
            rank_score = 50  # neutral if no predictions used

        backspace_pct = (backspaces / max(1, keystrokes)) * 100
        correction_score = max(0, 100 - backspace_pct * 3)

        score = (
            savings * 0.40
            + hit_rate * 0.25
            + rank_score * 0.20
            + correction_score * 0.15
        )
        return round(min(100, max(0, score)))
