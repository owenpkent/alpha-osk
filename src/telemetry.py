"""
Anonymous, opt-in usage telemetry client.

Off by default. Sends a small weekly report of lifetime counters to a
Cloudflare Worker we control. Never sends content, words, or per-key
data. Privacy mode counters are already excluded from the source values
in keyboard_bridge.py, so password-field activity never enters the
totals.

Design: docs/architecture/TELEMETRY.md
Privacy: docs/PRIVACY.md
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

_logger = logging.getLogger("Telemetry")

# The endpoint is read from this constant. Override per-build before
# shipping a release that has the toggle enabled. Empty string disables
# all network traffic (the client treats it as "endpoint not configured"
# and silently no-ops every submit).
DEFAULT_ENDPOINT = ""

# Submit at most once per WEEK. The first submission lands ~7 days
# after opt-in, not immediately on toggle, so a user toggling out of
# curiosity doesn't accidentally send anything.
SUBMIT_INTERVAL_SECONDS = 7 * 24 * 60 * 60

# HTTP timeout (seconds). Must be short -- we don't want the keyboard
# blocking on a flaky server. Submit runs on the on-quit path too.
HTTP_TIMEOUT_SECONDS = 5.0

# Backoff sequence for transient failures (network error, 5xx, 429).
# Three attempts within a single submit call, then drop until next week.
RETRY_BACKOFFS_SECONDS = (5, 30, 120)

# Sanity ceilings mirror the worker's validation. Anything above is
# clamped client-side too, so a corrupted analytics.json can't get the
# install banned by repeatedly POSTing rejected payloads.
_MAX_KEYSTROKES = 1_000_000_000
_MAX_WORDS = 200_000_000
_MAX_MINUTES = 5_000_000.0
_MAX_SESSIONS = 10_000_000


class TelemetryClient:
    """Manages opt-in consent, anon_id lifecycle, and weekly submits.

    State lives in <config_dir>/telemetry.json (separate from
    analytics.json to avoid two-writer contention -- TypingAnalytics
    owns analytics.json on the on-quit path).
    """

    def __init__(
        self,
        *,
        state_path: Optional[Path] = None,
        endpoint: str = DEFAULT_ENDPOINT,
        analytics_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        app_version: str = "",
        os_name: str = "",
        # Hooks for tests.
        now: Callable[[], float] = time.time,
        submit_fn: Optional[Callable[[str, bytes], int]] = None,
    ) -> None:
        self._state_path = state_path or self._default_state_path()
        self._endpoint = endpoint.rstrip("/")
        self._analytics_provider = analytics_provider
        self._app_version = app_version
        self._os_name = os_name
        self._now = now
        self._submit_fn = submit_fn or self._default_submit_fn

        self._enabled = False
        self._anon_id: Optional[str] = None
        self._last_submit_ts: float = 0.0
        self._load_state()

    @staticmethod
    def _default_state_path() -> Path:
        from .platform import get_config_dir
        return get_config_dir() / "telemetry.json"

    # --- state ---

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            _logger.warning("telemetry state unreadable, starting fresh: %s", e)
            return
        self._enabled = bool(data.get("enabled", False))
        anon = data.get("anon_id")
        if isinstance(anon, str) and len(anon) >= 32:
            self._anon_id = anon
        try:
            self._last_submit_ts = float(data.get("last_submit_ts", 0.0))
        except (TypeError, ValueError):
            self._last_submit_ts = 0.0

    def _save_state(self) -> None:
        data = {
            "enabled": self._enabled,
            "anon_id": self._anon_id,
            "last_submit_ts": self._last_submit_ts,
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(json.dumps(data, indent=2))
        except OSError as e:
            _logger.warning("failed to persist telemetry state: %s", e)

    # --- consent ---

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def anon_id(self) -> Optional[str]:
        return self._anon_id

    def enable(self) -> None:
        """Turn telemetry on. Generates a new anon_id if none exists."""
        if not self._anon_id:
            self._anon_id = str(uuid.uuid4())
        self._enabled = True
        # Reset the submit clock so the first send lands SUBMIT_INTERVAL
        # from now, not immediately. A user toggling on out of curiosity
        # has a full week to toggle off again before anything goes out.
        self._last_submit_ts = self._now()
        self._save_state()

    def disable(self) -> None:
        """Turn telemetry off. Clears anon_id so re-opt-in gets a fresh
        identifier (prior contributions cannot be linked to future ones).
        Does NOT delete already-submitted data on the server -- call
        forget() for that.
        """
        self._enabled = False
        self._anon_id = None
        self._last_submit_ts = 0.0
        self._save_state()

    # --- submit ---

    def maybe_submit(self) -> bool:
        """Submit if (a) opted in, (b) endpoint configured, (c) at least
        SUBMIT_INTERVAL since the last successful submit. Returns True if
        a submission was attempted (whether or not it succeeded).
        """
        if not self._enabled:
            return False
        if not self._endpoint:
            return False
        if not self._anon_id:
            return False
        if self._analytics_provider is None:
            return False
        if self._now() - self._last_submit_ts < SUBMIT_INTERVAL_SECONDS:
            return False
        return self._submit_now()

    def submit_on_quit(self) -> bool:
        """Last-chance submit on app exit. Bypasses the weekly interval
        because the user might not run the app again for a long time and
        the public counter would lag accordingly. Still gated on consent
        + endpoint + anon_id.
        """
        if not self._enabled:
            return False
        if not self._endpoint or not self._anon_id:
            return False
        if self._analytics_provider is None:
            return False
        # Skip if we just submitted (don't double-up if the user closes
        # right after a weekly send).
        if self._now() - self._last_submit_ts < 60:
            return False
        return self._submit_now()

    def _submit_now(self) -> bool:
        try:
            payload = self._build_payload()
        except Exception as e:
            _logger.warning("telemetry payload build failed: %s", e)
            return True
        body = json.dumps(payload).encode("utf-8")
        url = f"{self._endpoint}/v1/submit"

        for attempt, backoff in enumerate(RETRY_BACKOFFS_SECONDS):
            try:
                status = self._submit_fn(url, body)
            except Exception as e:
                _logger.info("telemetry submit attempt %d failed: %s",
                             attempt + 1, e)
                if attempt < len(RETRY_BACKOFFS_SECONDS) - 1:
                    time.sleep(backoff)
                continue

            if 200 <= status < 300:
                self._last_submit_ts = self._now()
                self._save_state()
                _logger.info("telemetry submitted (status %d)", status)
                return True

            # Permanent client errors (4xx other than 429) won't get
            # better with retry. Drop and wait for next week.
            if 400 <= status < 500 and status != 429:
                _logger.info("telemetry rejected (status %d), dropping",
                             status)
                return True

            # 5xx or 429: retry with backoff.
            if attempt < len(RETRY_BACKOFFS_SECONDS) - 1:
                time.sleep(backoff)

        _logger.info("telemetry submit failed after retries, will retry next week")
        return True

    def forget(self) -> bool:
        """Ask the server to delete this user's row. Used by the
        "Delete my contributed data" button in Settings. Always returns
        True if the request was attempted; the server returns 204
        regardless of whether the id existed.
        """
        if not self._endpoint or not self._anon_id:
            return False
        body = json.dumps({"anon_id": self._anon_id}).encode("utf-8")
        url = f"{self._endpoint}/v1/forget"
        try:
            self._submit_fn(url, body)
        except Exception as e:
            _logger.warning("telemetry forget failed: %s", e)
            return False
        return True

    # --- payload ---

    def _build_payload(self) -> Dict[str, Any]:
        assert self._analytics_provider is not None
        stats = self._analytics_provider()

        def _int(key: str, cap: int) -> int:
            v = stats.get(key, 0)
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = 0
            return max(0, min(cap, v))

        def _num(key: str, cap: float) -> float:
            v = stats.get(key, 0.0)
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = 0.0
            return max(0.0, min(cap, v))

        return {
            "anon_id":           self._anon_id,
            "app_version":       self._app_version or "unknown",
            "os":                self._os_name or "unknown",
            "keystrokes":        _int("alltimeKeystrokes", _MAX_KEYSTROKES),
            "words":             _int("alltimeWords", _MAX_WORDS),
            "predictions":       _int("alltimePredictionHits", _MAX_WORDS),
            "keystrokes_saved":  _int("alltimeKeystrokesSaved", _MAX_KEYSTROKES),
            "minutes":           _num("alltimeMinutes", _MAX_MINUTES),
            "sessions":          _int("alltimeSessions", _MAX_SESSIONS),
            "prediction_offers": _int("alltimePredictionOffers", _MAX_WORDS),
        }

    # --- HTTP ---

    @staticmethod
    def _default_submit_fn(url: str, body: bytes) -> int:
        """Default submit: POST JSON, return status code. Raises on
        network error so the retry loop can catch.
        """
        # Refuse anything but https://. The endpoint comes from a build
        # constant, but a misconfigured build that pointed at file:// or
        # plain http:// would otherwise leak telemetry over an
        # unauthenticated channel.
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise urllib.error.URLError(f"refusing non-https endpoint: {url!r}")

        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:  # noqa: S310
                return int(resp.status)
        except urllib.error.HTTPError as e:
            # HTTPError IS a response with a status code -- not a
            # network failure. Return the status so the caller can
            # decide retry vs drop.
            return int(e.code)
