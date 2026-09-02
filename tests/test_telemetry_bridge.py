"""Tests for TelemetryBridge, the QML-facing wrapper around the opt-in
telemetry pipeline.

This is the first surface pulled off ``KeyboardBridge`` per
``docs/architecture/STRUCTURAL_REVIEW.md`` section 3.1 -- telemetry
already delegated everything real to ``TelemetryClient`` (tested
directly in ``test_telemetry.py``), so what is left to verify here is
the QObject plumbing: the timer runs, ``shutdown()`` tears it down in
the right order and never lets a submit failure escape, and the three
slots delegate rather than reimplement anything.
"""

from __future__ import annotations

import logging
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from src.telemetry_bridge import TelemetryBridge  # noqa: E402


def _stats() -> Dict[str, Any]:
    """A reasonable lifetime stats dict, matching what the analytics
    provider callable is expected to return."""
    return {
        "alltimeKeystrokes": 1000,
        "alltimeWords": 200,
        "alltimePredictionHits": 50,
        "alltimeKeystrokesSaved": 300,
        "alltimeMinutes": 25.5,
        "alltimeSessions": 5,
        "alltimePredictionOffers": 80,
    }


@pytest.fixture
def qapp():
    """A Qt application for the timer machinery under test.

    ``TestTheHourlyTimer`` asserts ``QTimer.isActive()``, and a ``QTimer``
    started with no ``QCoreApplication`` instance in the process silently
    fails to arm -- ``start()`` returns, but ``isActive()`` reads back
    False, which is indistinguishable from a bug in ``TelemetryBridge``
    unless the timer had a real application to register with.

    **It must be a ``QGuiApplication``, not a ``QCoreApplication``.** This
    module needs no GUI, but an application cannot be upgraded once
    created, and under ``-n auto`` this module and the headless QML
    modules land in the same worker process often enough: creating a core
    app here would leave any QML test that ran afterwards in the same
    worker unable to build the ``QGuiApplication`` its engine needs. Same
    reasoning as the identical fixture in ``test_dictation.py``.
    """
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication

    from tests.qt_settings_scope import TEST_APP, TEST_ORG

    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test created a Qt application under a different "
        "organisation - QML Settings would write to the real user's store"
    )
    return app


@pytest.fixture
def telemetry_bridge(qapp) -> TelemetryBridge:
    """A TelemetryBridge with a plain analytics-provider stub.

    The bridge builds its own ``TelemetryClient`` internally -- the whole
    point of the split is that ``TelemetryClient`` stays the source of
    truth for consent -- so tests that need to control what the client
    does swap out ``._telemetry``, the same ``bridge._foo`` pattern the
    rest of this suite uses to reach into a bridge's backing objects.
    """
    return TelemetryBridge(_stats, app_version="1.0.16", os_name="windows")


class TestTheHourlyTimer:
    """The QTimer that periodically calls ``TelemetryClient.maybe_submit``."""

    def test_the_timer_is_running_after_construction(
        self, telemetry_bridge: TelemetryBridge
    ) -> None:
        timer = telemetry_bridge._telemetry_timer
        assert timer.isActive()

    def test_the_interval_is_one_hour(self, telemetry_bridge: TelemetryBridge) -> None:
        # Hourly tick is plenty: maybe_submit() short-circuits unless the
        # 7-day window has elapsed, and a no-op call costs ~5 microseconds.
        assert telemetry_bridge._telemetry_timer.interval() == 60 * 60 * 1000


class TestShutdown:
    """Stops the timer and makes a last-chance on-quit submit."""

    def test_shutdown_stops_the_timer(self, telemetry_bridge: TelemetryBridge) -> None:
        telemetry_bridge._telemetry = MagicMock()
        telemetry_bridge.shutdown()
        assert not telemetry_bridge._telemetry_timer.isActive()

    def test_shutdown_calls_submit_on_quit_once(self, telemetry_bridge: TelemetryBridge) -> None:
        mock_client = MagicMock()
        telemetry_bridge._telemetry = mock_client
        telemetry_bridge.shutdown()
        mock_client.submit_on_quit.assert_called_once()

    def test_shutdown_swallows_and_logs_a_submit_failure(
        self, telemetry_bridge: TelemetryBridge, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Internally gated on consent + endpoint + anon_id + a 60 s
        # anti-spam window, so a real client would rarely raise here --
        # but the on-quit path must not let *any* exception from it
        # escape and abort the rest of the app's teardown.
        mock_client = MagicMock()
        mock_client.submit_on_quit.side_effect = RuntimeError("simulated network failure")
        telemetry_bridge._telemetry = mock_client

        with caplog.at_level(logging.INFO, logger="TelemetryBridge"):
            telemetry_bridge.shutdown()  # must not raise

        assert "telemetry on-quit submit failed" in caplog.text
        assert "simulated network failure" in caplog.text


class TestTheThreeSlots:
    """getEnabled / setEnabled / forgetData delegate to TelemetryClient."""

    def test_get_enabled_reads_the_client(self, telemetry_bridge: TelemetryBridge) -> None:
        mock_client = MagicMock()
        mock_client.enabled = True
        telemetry_bridge._telemetry = mock_client
        assert telemetry_bridge.getEnabled() is True

    def test_set_enabled_true_calls_enable(self, telemetry_bridge: TelemetryBridge) -> None:
        mock_client = MagicMock()
        telemetry_bridge._telemetry = mock_client
        telemetry_bridge.setEnabled(True)
        mock_client.enable.assert_called_once()
        mock_client.disable.assert_not_called()

    def test_set_enabled_false_calls_disable(self, telemetry_bridge: TelemetryBridge) -> None:
        mock_client = MagicMock()
        telemetry_bridge._telemetry = mock_client
        telemetry_bridge.setEnabled(False)
        mock_client.disable.assert_called_once()
        mock_client.enable.assert_not_called()

    def test_forget_data_calls_forget_and_returns_its_result(
        self, telemetry_bridge: TelemetryBridge
    ) -> None:
        mock_client = MagicMock()
        mock_client.forget.return_value = True
        telemetry_bridge._telemetry = mock_client
        assert telemetry_bridge.forgetData() is True
        mock_client.forget.assert_called_once()
