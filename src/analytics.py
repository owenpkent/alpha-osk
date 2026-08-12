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
        # Picks at rank 1 ("the first suggestion was the right one").
        # Tracked separately from rank_sum so the dashboard can show a
        # plain percentage without having to invert an average.
        self._top_pick_count = 0
        # WPM samples for sparkline (one per minute)
        self._wpm_samples: List[float] = []
        self._last_sample_time = time.time()
        self._words_at_last_sample = 0

        # All-time stats (loaded from / saved to disk).  Mirrors every
        # session counter so the dashboard can render lifetime versions
        # of every metric (WPM, hit rate, savings %, backspace rate,
        # top words / keys).  Without persisting these, any
        # aggregate-over-time reading is wrong the moment a session
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
        self._alltime_top_pick_count = 0
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

    def reload_from_disk(self) -> None:
        """Re-read lifetime stats from disk after an external swap.

        Used by the import path in :class:`KeyboardBridge` after
        ``analytics.json`` has been replaced by an imported archive.
        Resets in-memory lifetime counters then re-loads from the
        new file. Session counters are intentionally left alone — the
        running session is the user's *current* activity, not part of
        the imported snapshot.
        """
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
        self._alltime_top_pick_count = 0
        self._alltime_word_freq = Counter()
        self._alltime_key_freq = Counter()
        self._load_alltime()

    # Cap on the raw analytics.json file itself, checked via stat() before
    # it is ever read, mirroring NgramPredictor.load and SnippetStore.load
    # (both of which also stat() before opening rather than reading an
    # arbitrarily large file into memory first). A legitimate file is a
    # handful of scalar counters plus at most _WORD_FREQ_CAP +
    # _KEY_FREQ_CAP frequency entries, which comfortably fits in the low
    # hundreds of KB even pretty-printed; anything past this is assumed
    # corrupt or hostile. The Data Backup archive itself permits an
    # analytics.json up to 75 MB (data_export._MAX_FILE_BYTES, sized for
    # every kind of file the archive carries); this is the narrower,
    # content-aware limit for this file specifically.
    _MAX_STATS_FILE_BYTES = 5 * 1024 * 1024

    def _load_alltime(self) -> None:
        """Load all-time stats from disk.

        Parses into local variables first and only assigns to ``self``
        once everything has succeeded, so a partial failure partway
        through (a bad type in the middle of a dict, a cap trip) can
        never leave the in-memory state half-updated. Any failure here,
        including the size cap, falls back to whatever the caller already
        reset the lifetime counters to (a fresh instance: all zero; a
        manual ``reload_from_disk``: also all zero, since that resets
        before calling this) rather than raising and blocking startup.
        """
        if not self._stats_path.exists():
            return
        try:
            if self._stats_path.stat().st_size > self._MAX_STATS_FILE_BYTES:
                _logger.warning(
                    "analytics.json exceeds %d bytes, ignoring and starting fresh",
                    self._MAX_STATS_FILE_BYTES,
                )
                return
            data = json.loads(self._stats_path.read_text())
            keystrokes = data.get("keystrokes", 0)
            words = data.get("words", 0)
            predictions = data.get("predictions", 0)
            keystrokes_saved = data.get("keystrokes_saved", 0)
            sessions = data.get("sessions", 0)
            minutes = data.get("minutes", 0.0)
            backspaces = data.get("backspaces", 0)
            prediction_offers = data.get("prediction_offers", 0)
            prediction_rank_sum = data.get("prediction_rank_sum", 0)
            prediction_rank_count = data.get("prediction_rank_count", 0)
            top_pick_count = data.get("top_pick_count", 0)

            wf = data.get("word_freq", {})
            kf = data.get("key_freq", {})
            word_freq: Counter[str] = Counter()
            key_freq: Counter[str] = Counter()
            if isinstance(wf, dict):
                counted = Counter({k: int(v) for k, v in wf.items() if isinstance(v, (int, float))})
                # Entries beyond the cap are the long tail by definition
                # (most_common keeps the highest counts), so an old or
                # hostile file with more than _WORD_FREQ_CAP entries is
                # truncated to its heaviest hitters rather than refused
                # outright: unlike the size cap above, losing the tail of
                # a word-frequency table isn't a reason to also discard
                # every scalar counter (keystrokes, sessions, ...) in the
                # same file.
                word_freq = Counter(dict(counted.most_common(self._WORD_FREQ_CAP)))
            if isinstance(kf, dict):
                counted = Counter({k: int(v) for k, v in kf.items() if isinstance(v, (int, float))})
                key_freq = Counter(dict(counted.most_common(self._KEY_FREQ_CAP)))
        except (json.JSONDecodeError, OSError, ValueError, TypeError, OverflowError) as e:
            _logger.warning("Failed to load analytics: %s", e)
            return

        self._alltime_keystrokes = keystrokes
        self._alltime_words = words
        self._alltime_predictions = predictions
        self._alltime_keystrokes_saved = keystrokes_saved
        self._alltime_sessions = sessions
        self._alltime_minutes = minutes
        self._alltime_backspaces = backspaces
        self._alltime_prediction_offers = prediction_offers
        self._alltime_prediction_rank_sum = prediction_rank_sum
        self._alltime_prediction_rank_count = prediction_rank_count
        self._alltime_top_pick_count = top_pick_count
        self._alltime_word_freq = word_freq
        self._alltime_key_freq = key_freq
        _logger.info(
            "Loaded all-time analytics: %d words, %d keystrokes saved",
            self._alltime_words,
            self._alltime_keystrokes_saved,
        )

    # Caps on persisted unique word/key entries.  Top-N display only ever
    # needs the heavy hitters; without a cap, a few years of typing could
    # push word_freq into the megabytes.  Pruning keeps the most-typed
    # entries plus anything from the current session (so an in-progress
    # word frequency is never silently dropped).  key_freq has a far
    # smaller natural vocabulary than word_freq (every letter/digit/
    # punctuation key plus a handful of named specials), but it went
    # uncapped even after word_freq was bounded, so a hostile or
    # corrupted file could still grow it without limit; the same cap
    # value is already generous for a real keyboard's key set.
    _WORD_FREQ_CAP = 5000
    _KEY_FREQ_CAP = 5000

    def save(self) -> None:
        """Save all-time stats to disk (merges current session)."""
        merged_words = self._alltime_word_freq + self._word_freq
        if len(merged_words) > self._WORD_FREQ_CAP:
            # Keep only the top-N by count.  Counter.most_common is O(n log k)
            # which is fine at this scale.
            merged_words = Counter(dict(merged_words.most_common(self._WORD_FREQ_CAP)))
        merged_keys = self._alltime_key_freq + self._key_freq
        if len(merged_keys) > self._KEY_FREQ_CAP:
            merged_keys = Counter(dict(merged_keys.most_common(self._KEY_FREQ_CAP)))

        data = {
            "keystrokes": self._alltime_keystrokes + self._keystroke_count,
            "words": self._alltime_words + self._word_count,
            "predictions": self._alltime_predictions + self._prediction_hits,
            "keystrokes_saved": self._alltime_keystrokes_saved + self._keystrokes_saved,
            "sessions": self._alltime_sessions,
            "minutes": self._alltime_minutes + (time.time() - self._session_start) / 60,
            "backspaces": self._alltime_backspaces + self._backspace_count,
            "prediction_offers": self._alltime_prediction_offers + self._prediction_offers,
            "prediction_rank_sum": (self._alltime_prediction_rank_sum + self._prediction_rank_sum),
            "prediction_rank_count": (
                self._alltime_prediction_rank_count + self._prediction_rank_count
            ),
            "top_pick_count": self._alltime_top_pick_count + self._top_pick_count,
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

    def record_prediction_selected(self, word: str, rank: int, keystrokes_saved: int = 0) -> None:
        """Record when user selects a prediction.

        Args:
            word: The selected word
            rank: Position in the prediction list (1-based)
            keystrokes_saved: Characters the user didn't have to type
        """
        self._prediction_hits += 1
        self._prediction_rank_sum += rank
        self._prediction_rank_count += 1
        if rank == 1:
            self._top_pick_count += 1
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
        savings_pct = round(self._keystrokes_saved / max(1, total_typed) * 100, 1)

        # Lifetime aggregates — current session + persisted history.
        alltime_keystrokes = self._alltime_keystrokes + self._keystroke_count
        alltime_words = self._alltime_words + self._word_count
        alltime_predictions = self._alltime_predictions + self._prediction_hits
        alltime_saved = self._alltime_keystrokes_saved + self._keystrokes_saved
        alltime_backspaces = self._alltime_backspaces + self._backspace_count
        alltime_offers = self._alltime_prediction_offers + self._prediction_offers
        alltime_rank_count = self._alltime_prediction_rank_count + self._prediction_rank_count
        alltime_minutes = self._alltime_minutes + elapsed_min
        alltime_total_typed = alltime_keystrokes + alltime_saved

        # Lifetime top words = persisted Counter + current session, then
        # take top 5.  Combining with `+` is right: it sums counts for
        # any word that appears in both.
        alltime_top_words = (self._alltime_word_freq + self._word_freq).most_common(5)

        # Top-pick rate: % of prediction picks that were the first
        # suggestion.  Computed off rank_count (= total picks) so it
        # tracks "how often is suggestion #1 right" independently of
        # how many picks the user has made.
        alltime_top_picks = self._alltime_top_pick_count + self._top_pick_count
        session_top_pick_rate = round(
            self._top_pick_count / max(1, self._prediction_rank_count) * 100, 1
        )
        alltime_top_pick_rate = round(alltime_top_picks / max(1, alltime_rank_count) * 100, 1)

        # Acceptance rate: when a prediction was OFFERED to the user,
        # how often did they click one.  Distinct from
        # predictionHitRate (= hits / words completed): a word the
        # user typed without ever opening the suggestion bar isn't an
        # offer.  Acceptance asks "of the times we showed something,
        # how often was it useful enough to take".
        session_acceptance_rate = round(
            self._prediction_hits / max(1, self._prediction_offers) * 100, 1
        )
        alltime_acceptance_rate = round(alltime_predictions / max(1, alltime_offers) * 100, 1)

        # Time saved: keystrokes saved * the user's own seconds per
        # keystroke.  Using their own pace makes the number honest --
        # a slow OSK user genuinely saves more wall-clock time per
        # avoided keystroke than a fast one.  Falls back to 0.5 s/key
        # when there's no usage history yet (new install).
        session_pace = (
            (elapsed_min * 60.0) / self._keystroke_count if self._keystroke_count > 0 else 0.5
        )
        alltime_pace = (
            (alltime_minutes * 60.0) / alltime_keystrokes if alltime_keystrokes > 0 else 0.5
        )
        session_time_saved = self._keystrokes_saved * session_pace
        alltime_time_saved = alltime_saved * alltime_pace

        return {
            # Session
            "wpm": round(self._word_count / elapsed_min, 1),
            "sessionMinutes": round(elapsed_min, 1),
            "totalWords": self._word_count,
            "totalKeystrokes": self._keystroke_count,
            "totalBackspaces": self._backspace_count,
            "keystrokesSaved": self._keystrokes_saved,
            "savingsPercent": savings_pct,
            "predictionHitRate": round(self._prediction_hits / max(1, self._word_count) * 100, 1),
            "predictionHits": self._prediction_hits,
            "backspaceRate": round(self._backspace_count / max(1, self._keystroke_count) * 100, 1),
            "topWords": [{"word": w, "count": c} for w, c in top_words],
            "wpmSamples": self._wpm_samples,
            "topPickRate": session_top_pick_rate,
            "acceptanceRate": session_acceptance_rate,
            "timeSavedSeconds": round(session_time_saved, 1),
            "predictionOffers": self._prediction_offers,
            # Lifetime (= persisted history + current session)
            "alltimeWords": alltime_words,
            "alltimeKeystrokes": alltime_keystrokes,
            "alltimeKeystrokesSaved": alltime_saved,
            "alltimePredictionHits": alltime_predictions,
            "alltimeSessions": self._alltime_sessions,
            "alltimeMinutes": round(alltime_minutes, 1),
            "alltimeWpm": round(alltime_words / max(0.1, alltime_minutes), 1),
            "alltimeSavingsPercent": round(alltime_saved / max(1, alltime_total_typed) * 100, 1),
            "alltimePredictionHitRate": round(alltime_predictions / max(1, alltime_words) * 100, 1),
            "alltimeBackspaces": alltime_backspaces,
            "alltimeBackspaceRate": round(alltime_backspaces / max(1, alltime_keystrokes) * 100, 1),
            "alltimePredictionOffers": alltime_offers,
            "alltimeTopWords": [{"word": w, "count": c} for w, c in alltime_top_words],
            "alltimeTopPickRate": alltime_top_pick_rate,
            "alltimeAcceptanceRate": alltime_acceptance_rate,
            "alltimeTimeSavedSeconds": round(alltime_time_saved, 1),
        }
