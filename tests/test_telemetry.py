"""Tests for the opt-in telemetry client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

from src.telemetry import (
    SUBMIT_INTERVAL_SECONDS,
    TelemetryClient,
)


def _stats(**overrides: Any) -> Dict[str, Any]:
    """A reasonable lifetime stats dict, with overrides."""
    base = {
        "alltimeKeystrokes": 1000,
        "alltimeWords": 200,
        "alltimePredictionHits": 50,
        "alltimeKeystrokesSaved": 300,
        "alltimeMinutes": 25.5,
        "alltimeSessions": 5,
        "alltimePredictionOffers": 80,
    }
    base.update(overrides)
    return base


class _FakeClock:
    """Manual clock so we can step time deterministically."""

    def __init__(self, t: float = 1_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


class _FakeSubmit:
    """Capture submit calls; return a configurable status."""

    def __init__(self, status: int = 204) -> None:
        self.status = status
        self.calls: List[Tuple[str, bytes]] = []
        self.raise_on_call = False

    def __call__(self, url: str, body: bytes) -> int:
        self.calls.append((url, body))
        if self.raise_on_call:
            raise OSError("simulated network failure")
        return self.status


@pytest.fixture
def state_file(tmp_path: Path) -> Path:
    return tmp_path / "telemetry.json"


def _client(state_file: Path, **kw: Any) -> Tuple[TelemetryClient, _FakeClock, _FakeSubmit]:
    clock = _FakeClock()
    submit = _FakeSubmit(status=kw.pop("status", 204))
    client = TelemetryClient(
        state_path=state_file,
        endpoint=kw.pop("endpoint", "https://test.example/"),
        analytics_provider=kw.pop("analytics", lambda: _stats()),
        app_version=kw.pop("app_version", "1.0.16"),
        os_name=kw.pop("os_name", "windows"),
        now=clock,
        submit_fn=submit,
    )
    return client, clock, submit


class TestConsentGate:
    """Nothing is sent unless the user has explicitly opted in."""

    def test_off_by_default(self, state_file: Path) -> None:
        c, _, submit = _client(state_file)
        assert c.enabled is False
        assert c.maybe_submit() is False
        assert submit.calls == []

    def test_no_anon_id_until_enabled(self, state_file: Path) -> None:
        c, _, _ = _client(state_file)
        assert c.anon_id is None

    def test_disabled_does_not_submit_even_after_long_wait(self, state_file: Path) -> None:
        c, clock, submit = _client(state_file)
        clock.tick(SUBMIT_INTERVAL_SECONDS * 2)
        assert c.maybe_submit() is False
        assert submit.calls == []


class TestEnableLifecycle:
    def test_enable_generates_anon_id(self, state_file: Path) -> None:
        c, _, _ = _client(state_file)
        c.enable()
        assert c.enabled is True
        assert c.anon_id is not None
        assert len(c.anon_id) >= 32

    def test_enable_persists_state(self, state_file: Path) -> None:
        c, _, _ = _client(state_file)
        c.enable()
        on_disk = json.loads(state_file.read_text())
        assert on_disk["enabled"] is True
        assert on_disk["anon_id"] == c.anon_id

    def test_disable_clears_anon_id(self, state_file: Path) -> None:
        c, _, _ = _client(state_file)
        c.enable()
        first_id = c.anon_id
        c.disable()
        assert c.enabled is False
        assert c.anon_id is None
        c.enable()
        # New opt-in gets a new id; prior contribution can't be linked.
        assert c.anon_id != first_id

    def test_load_existing_state(self, state_file: Path) -> None:
        state_file.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "anon_id": "1234567890abcdef" * 2,
                    "last_submit_ts": 999.0,
                }
            )
        )
        c, _, _ = _client(state_file)
        assert c.enabled is True
        assert c.anon_id == "1234567890abcdef" * 2

    def test_corrupt_state_starts_fresh(self, state_file: Path) -> None:
        state_file.write_text("not json")
        c, _, _ = _client(state_file)
        assert c.enabled is False
        assert c.anon_id is None


class TestSubmitCadence:
    """Weekly window enforced; first submit lands SUBMIT_INTERVAL after opt-in."""

    def test_no_submit_immediately_after_enable(self, state_file: Path) -> None:
        c, _, submit = _client(state_file)
        c.enable()
        assert c.maybe_submit() is False
        assert submit.calls == []

    def test_no_submit_before_one_week(self, state_file: Path) -> None:
        c, clock, submit = _client(state_file)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS - 1)
        assert c.maybe_submit() is False
        assert submit.calls == []

    def test_submits_after_one_week(self, state_file: Path) -> None:
        c, clock, submit = _client(state_file)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        assert c.maybe_submit() is True
        assert len(submit.calls) == 1

    def test_no_double_submit_within_window(self, state_file: Path) -> None:
        c, clock, submit = _client(state_file)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        clock.tick(60)
        c.maybe_submit()
        assert len(submit.calls) == 1


class TestPayload:
    def test_payload_shape(self, state_file: Path) -> None:
        c, clock, submit = _client(state_file)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        url, body = submit.calls[0]
        assert url.endswith("/v1/submit")
        payload = json.loads(body)
        assert payload["anon_id"] == c.anon_id
        assert payload["app_version"] == "1.0.16"
        assert payload["os"] == "windows"
        assert payload["keystrokes"] == 1000
        assert payload["words"] == 200
        assert payload["predictions"] == 50
        assert payload["keystrokes_saved"] == 300
        assert payload["minutes"] == 25.5
        assert payload["sessions"] == 5
        assert payload["prediction_offers"] == 80

    def test_payload_clamps_overflow(self, state_file: Path) -> None:
        c, clock, submit = _client(
            state_file,
            analytics=lambda: _stats(alltimeKeystrokes=10**18),
        )
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        payload = json.loads(submit.calls[0][1])
        assert payload["keystrokes"] == 1_000_000_000  # clamped to ceiling

    def test_payload_handles_missing_keys(self, state_file: Path) -> None:
        c, clock, submit = _client(state_file, analytics=lambda: {})
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        payload = json.loads(submit.calls[0][1])
        assert payload["keystrokes"] == 0
        assert payload["words"] == 0


class TestRetryAndFailure:
    def test_5xx_retries(self, state_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        c, clock, submit = _client(state_file, status=503)
        # Don't actually sleep between retries -- the test would hang.
        import src.telemetry as telemetry_mod

        monkeypatch.setattr(telemetry_mod.time, "sleep", lambda _: None)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        assert len(submit.calls) == 3  # 3 retries

    def test_4xx_does_not_retry(self, state_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        c, clock, submit = _client(state_file, status=400)
        import src.telemetry as telemetry_mod

        monkeypatch.setattr(telemetry_mod.time, "sleep", lambda _: None)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        assert len(submit.calls) == 1  # gave up after the 400

    def test_429_does_retry(self, state_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        c, clock, submit = _client(state_file, status=429)
        import src.telemetry as telemetry_mod

        monkeypatch.setattr(telemetry_mod.time, "sleep", lambda _: None)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        assert len(submit.calls) == 3  # 429 is treated as transient

    def test_network_error_retries(self, state_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        c, clock, submit = _client(state_file)
        submit.raise_on_call = True
        import src.telemetry as telemetry_mod

        monkeypatch.setattr(telemetry_mod.time, "sleep", lambda _: None)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        assert len(submit.calls) == 3

    def test_failed_submit_does_not_advance_clock(
        self, state_file: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        c, clock, submit = _client(state_file, status=500)
        import src.telemetry as telemetry_mod

        monkeypatch.setattr(telemetry_mod.time, "sleep", lambda _: None)
        c.enable()
        enable_ts = c._last_submit_ts
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        # All 3 attempts failed -- last_submit_ts must NOT have advanced
        # (otherwise we'd wait another full week before retrying).
        assert c._last_submit_ts == enable_ts


class TestEndpointGuards:
    def test_no_endpoint_means_no_submit(self, state_file: Path) -> None:
        c, clock, submit = _client(state_file, endpoint="")
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        assert c.maybe_submit() is False
        assert submit.calls == []


class TestSubmitOnQuit:
    def test_on_quit_skips_if_just_submitted(self, state_file: Path) -> None:
        c, clock, submit = _client(state_file)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        # Simulate quit immediately after a successful weekly submit.
        clock.tick(10)
        assert c.submit_on_quit() is False
        assert len(submit.calls) == 1

    def test_on_quit_sends_if_long_gap(self, state_file: Path) -> None:
        c, clock, submit = _client(state_file)
        c.enable()
        clock.tick(SUBMIT_INTERVAL_SECONDS + 1)
        c.maybe_submit()
        clock.tick(3600)  # an hour later
        assert c.submit_on_quit() is True
        assert len(submit.calls) == 2

    def test_on_quit_skipped_when_disabled(self, state_file: Path) -> None:
        c, _, submit = _client(state_file)
        assert c.submit_on_quit() is False
        assert submit.calls == []


class TestForget:
    def test_forget_posts_anon_id(self, state_file: Path) -> None:
        c, _, submit = _client(state_file)
        c.enable()
        result = c.forget()
        assert result is True
        url, body = submit.calls[0]
        assert url.endswith("/v1/forget")
        payload = json.loads(body)
        assert payload["anon_id"] == c.anon_id

    def test_forget_noop_when_no_anon_id(self, state_file: Path) -> None:
        c, _, submit = _client(state_file)
        assert c.forget() is False
        assert submit.calls == []
