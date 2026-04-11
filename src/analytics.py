"""
Typing Analytics — tracks session statistics for the analytics dashboard.

Tracks keystrokes, words, prediction usage, and error rates.
All data is session-scoped (resets on restart) and never leaves the device.
"""

from __future__ import annotations

import time
from collections import Counter
from typing import Any, Dict, List, Tuple

from PySide6.QtCore import QObject, Signal, Slot


class TypingAnalytics(QObject):
    """Tracks typing statistics for the current session."""

    statsUpdated = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._session_start = time.time()
        self._keystroke_count = 0
        self._word_count = 0
        self._prediction_hits = 0
        self._prediction_offers = 0
        self._backspace_count = 0
        self._word_freq: Counter[str] = Counter()
        self._key_freq: Counter[str] = Counter()
        self._prediction_rank_sum = 0
        self._prediction_rank_count = 0

    def record_keystroke(self, key: str) -> None:
        """Record a character key press."""
        self._keystroke_count += 1
        self._key_freq[key.lower()] += 1

    def record_word_completed(self, word: str) -> None:
        """Record a completed word (on space or return)."""
        if word:
            self._word_count += 1
            self._word_freq[word.lower()] += 1

    def record_prediction_selected(self, word: str, rank: int) -> None:
        """Record when user selects a prediction."""
        self._prediction_hits += 1
        self._prediction_rank_sum += rank
        self._prediction_rank_count += 1
        self.record_word_completed(word)

    def record_prediction_offered(self) -> None:
        """Record when predictions are shown to the user."""
        self._prediction_offers += 1

    def record_backspace(self) -> None:
        """Record a backspace press."""
        self._backspace_count += 1
        self._keystroke_count += 1

    @Slot(result="QVariant")
    def get_session_stats(self) -> Dict[str, Any]:
        """Return current session statistics as a dict for QML."""
        elapsed_min = max(0.1, (time.time() - self._session_start) / 60)
        top_words: List[Tuple[str, int]] = self._word_freq.most_common(10)

        return {
            "wpm": round(self._word_count / elapsed_min, 1),
            "totalWords": self._word_count,
            "totalKeystrokes": self._keystroke_count,
            "predictionHitRate": round(
                self._prediction_hits / max(1, self._word_count) * 100, 1
            ),
            "avgPredictionRank": round(
                self._prediction_rank_sum / max(1, self._prediction_rank_count), 1
            ),
            "backspaceRate": round(
                self._backspace_count / max(1, self._keystroke_count) * 100, 1
            ),
            "topWords": [{"word": w, "count": c} for w, c in top_words],
            "sessionMinutes": round(elapsed_min, 1),
            "predictionHits": self._prediction_hits,
            "predictionsOffered": self._prediction_offers,
        }
