"""The focus probe: UI Automation on its own thread, never the keyboard's.

A target that is slow to answer UIA (Thunderbird was measured at 317 ms)
used to hold every keystroke that asked.  The probe owns the asking now:
a keystroke waits a bounded time for a fresh reading, the polls read the
stored one, and a reading that lands late is applied when it lands.
These tests drive the probe with a fake detector whose answer can be
delayed, and the bridge with a probe over that detector.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from src import keyboard_bridge as kb
from src.keyboard_bridge import KeyboardBridge
from src.platform import password_detect
from src.platform.focus_probe import FocusProbe, FocusReading
from src.snippets import SnippetStore
from tests.qt_settings_scope import TEST_APP, TEST_ORG

STALL_S = 0.3
# What a keystroke may cost while the target stalls: the 25 ms request
# budget plus scheduling slack on a loaded CI shard.
KEYSTROKE_BUDGET_S = 0.15
WINDOW = 100
OTHER_WINDOW = 200


class FakeDetector:
    """A detector whose answer, delay and identity the test controls."""

    def __init__(self, *, token: str | None = "A", is_password: bool = False, delay_s: float = 0.0):
        self.token = token
        self.is_password = is_password
        self.delay_s = delay_s
        self.reads = 0
        self.read_on: set[int] = set()
        self.closed_on: int | None = None

    def read_focus(self) -> tuple[str | None, bool]:
        self.reads += 1
        self.read_on.add(threading.get_ident())
        if self.delay_s:
            time.sleep(self.delay_s)
        return self.token, self.is_password

    def check(self) -> bool:  # pragma: no cover - the probe prefers read_focus
        return self.is_password

    def close(self) -> None:
        self.closed_on = threading.get_ident()


class SingleMethodDetector:
    """The Win32 fallback's shape: ``check`` only, no element identity."""

    def __init__(self) -> None:
        self.checks = 0

    def check(self) -> bool:
        self.checks += 1
        return True


def _probe(detector, *, foreground: int = WINDOW, on_reading=None, interval_s: float = 10.0):
    """A started probe that reads only on request (the interval is idle)."""
    probe = FocusProbe(
        lambda: detector,
        foreground=lambda: foreground,
        on_reading=on_reading,
        interval_s=interval_s,
    )
    assert probe.start()
    return probe


def _settle(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(0.01)


class TestTheProbeAlone:
    def test_a_responsive_detector_answers_the_request(self):
        det = FakeDetector(token="field", is_password=True)
        probe = _probe(det)
        try:
            reading = probe.request(0.5)
            assert reading is not None
            assert (reading.hwnd, reading.token, reading.is_password) == (WINDOW, "field", True)
            assert probe.latest() is reading
        finally:
            probe.stop()

    def test_a_stalled_detector_does_not_hold_the_request(self):
        det = FakeDetector(is_password=True, delay_s=STALL_S)
        delivered: list[FocusReading] = []
        probe = _probe(det, on_reading=delivered.append)
        try:
            started = time.monotonic()
            reading = probe.request(0.025)
            elapsed = time.monotonic() - started
            assert reading is None
            assert elapsed < KEYSTROKE_BUDGET_S
            # The answer is late, not lost.
            deadline = time.monotonic() + 2.0
            while not delivered and time.monotonic() < deadline:
                time.sleep(0.01)
            assert delivered and delivered[-1].is_password
            latest = probe.latest()
            assert latest is not None and latest.is_password
        finally:
            probe.stop()

    def test_every_read_happens_on_the_probe_thread(self):
        det = FakeDetector()
        probe = _probe(det)
        try:
            assert probe.request(0.5) is not None
            assert probe._thread is not None
            assert det.read_on == {probe._thread.ident}
            assert threading.get_ident() not in det.read_on
        finally:
            probe.stop()

    def test_a_request_is_answered_by_a_reading_taken_after_it(self):
        det = FakeDetector(token="before")
        probe = _probe(det)
        try:
            first = probe.request(0.5)
            assert first is not None and first.token == "before"
            det.token = "after"
            second = probe.request(0.5)
            assert second is not None
            assert second.token == "after"
            assert second.seq > first.seq
        finally:
            probe.stop()

    def test_a_request_waits_for_a_read_that_begins_after_it(self):
        """A read in flight captured focus before the request (PR #246 review, P1).

        The first read captures a plain field and stalls in its property
        reads; focus moves to a password field in the same window; a
        keystroke requests a fresh reading.  Waiting for the next read to
        *complete* handed back the plain field's answer, inside the
        budget and wrong.
        """
        releases = [threading.Event(), threading.Event(), threading.Event()]
        captured: list[tuple[str | None, bool]] = []

        class GatedDetector(FakeDetector):
            def read_focus(self) -> tuple[str | None, bool]:
                self.reads += 1
                answer = (self.token, self.is_password)
                captured.append(answer)
                releases[min(self.reads - 1, 2)].wait(2.0)
                return answer

        det = GatedDetector(token="plain-field", is_password=False)
        probe = _probe(det)
        try:
            deadline = time.monotonic() + 2.0
            while det.reads < 1 and time.monotonic() < deadline:
                time.sleep(0.005)
            assert det.reads == 1  # the first read is in flight, holding the plain field
            det.token, det.is_password = "password-field", True
            # Let the first read finish while the request waits, then the second.
            threading.Timer(0.05, releases[0].set).start()
            threading.Timer(0.15, releases[1].set).start()
            reading = probe.request(1.0)
            assert reading is not None
            assert reading.seq == 2
            assert (reading.token, reading.is_password) == ("password-field", True)
        finally:
            for release in releases:
                release.set()
            probe.stop()

    def test_the_timer_reads_on_its_own(self):
        det = FakeDetector()
        probe = _probe(det, interval_s=0.02)
        try:
            deadline = time.monotonic() + 2.0
            while det.reads < 3 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert det.reads >= 3
        finally:
            probe.stop()

    def test_stop_closes_the_detector_on_its_own_thread_and_ends_it(self):
        det = FakeDetector()
        probe = _probe(det)
        assert probe.running
        thread = probe._thread
        probe.stop()
        assert not probe.running
        assert thread is not None
        assert det.closed_on == thread.ident

    def test_nothing_is_delivered_after_stop(self):
        det = FakeDetector(is_password=True, delay_s=STALL_S)
        delivered: list[FocusReading] = []
        probe = _probe(det, on_reading=delivered.append)
        probe.request(0.01)  # the read is now in flight
        probe.stop(timeout_s=0.01)  # returns before the stalled read does
        _settle(STALL_S + 0.2)
        assert delivered == []
        assert probe.request(0.1) is None

    def test_a_factory_with_nothing_to_offer_is_unavailable(self):
        probe = FocusProbe(lambda: None, foreground=lambda: WINDOW)
        assert probe.start() is False
        assert probe.available is False
        assert probe.request(0.1) is None
        probe.stop()
        assert not probe.running

    def test_a_factory_that_raises_is_unavailable(self):
        def boom():
            raise RuntimeError("no COM today")

        probe = FocusProbe(boom, foreground=lambda: WINDOW)
        assert probe.start() is False
        probe.stop()

    def test_a_single_method_detector_still_reads(self):
        det = SingleMethodDetector()
        probe = _probe(det)
        try:
            reading = probe.request(0.5)
            assert reading is not None
            assert reading.is_password is True
            assert reading.token is None
            assert det.checks >= 1
        finally:
            probe.stop()

    def test_the_reading_carries_the_foreground_it_was_taken_under(self):
        det = FakeDetector()
        probe = _probe(det, foreground=OTHER_WINDOW)
        try:
            reading = probe.request(0.5)
            assert reading is not None and reading.hwnd == OTHER_WINDOW
            assert reading.read_at <= time.monotonic()
        finally:
            probe.stop()


class TestStartingTheRealProbe:
    def test_there_is_no_probe_off_windows(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        assert password_detect.start_focus_probe() is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")
    def test_on_windows_it_reads_the_desktop_from_its_own_thread(self):
        delivered: list[FocusReading] = []
        probe = password_detect.start_focus_probe(delivered.append)
        assert probe is not None
        try:
            assert probe.available
            reading = probe.request(2.0)
            assert reading is not None
            assert isinstance(reading.is_password, bool)
            assert delivered and delivered[-1] is reading
        finally:
            probe.stop()
        assert not probe.running


# ---------------------------------------------------------------------- #
#  The bridge over a probe
# ---------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG
    assert QCoreApplication.applicationName() == TEST_APP
    return app


class ProbeHarness:
    """A bridge whose focus probe reads a fake detector on a real thread."""

    def __init__(self, tmp_path: Path, monkeypatch, detector: FakeDetector, probe_window: int):
        self.detector = detector
        self.probe: FocusProbe | None = None

        def start(on_reading=None):
            self.probe = FocusProbe(
                lambda: detector,
                foreground=lambda: probe_window,
                on_reading=on_reading,
                interval_s=10.0,
            )
            assert self.probe.start()
            return self.probe

        monkeypatch.setattr("src.keyboard_bridge.start_focus_probe", start)
        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.busy = False
            synth.is_available.return_value = True
            synth.backend_name.return_value = "MockSynth"
            factory.return_value = synth
            self.bridge = KeyboardBridge()
            self.bridge._synth = synth
            self.bridge._snippets = SnippetStore(tmp_path / "snippets.json")
            self.bridge._snippets.load()
        self.bridge._password_timer.stop()
        self.bridge._foreground_timer.stop()
        monkeypatch.setattr(self.bridge, "_get_foreground_window_id", lambda: WINDOW)
        assert self.bridge._focus_probe is self.probe

    def wait_for(self, condition, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while not condition() and time.monotonic() < deadline:
            QTest.qWait(5)
        return condition()

    def close(self) -> None:
        self.bridge.shutdown()


@pytest.fixture
def harness_factory(qapp, tmp_path, monkeypatch):
    made: list[ProbeHarness] = []

    def make(detector: FakeDetector, *, probe_window: int = WINDOW) -> ProbeHarness:
        h = ProbeHarness(tmp_path, monkeypatch, detector, probe_window)
        made.append(h)
        return h

    yield make
    for h in made:
        h.close()


class TestAKeystrokeIsNeverHeldByTheTarget:
    def test_it_returns_inside_the_budget_while_the_target_stalls(self, harness_factory):
        h = harness_factory(FakeDetector(delay_s=STALL_S))
        # The check is timed on its own: the rest of a keystroke (the
        # prediction query) is what a loaded CI shard makes slow, and it
        # is not what this bounds.
        started = time.monotonic()
        h.bridge._check_password_field_sync()
        elapsed = time.monotonic() - started
        assert elapsed < KEYSTROKE_BUDGET_S
        h.bridge.pressKey("h")
        assert h.bridge._current_word == "h"

    def test_a_late_password_answer_still_switches_privacy_mode_on(self, harness_factory):
        h = harness_factory(FakeDetector(is_password=True, delay_s=STALL_S))
        h.bridge.pressKey("h")
        # The answer had not arrived: the key went out and the word is tracked.
        assert h.bridge.privacyMode is False
        assert h.bridge._current_word == "h"
        # It arrives, on the UI thread, and does what the timer would have.
        assert h.wait_for(lambda: h.bridge.privacyMode is True)
        assert h.bridge._current_word == ""

    def test_a_prompt_password_answer_turns_privacy_mode_on_before_the_key(self, harness_factory):
        h = harness_factory(FakeDetector(is_password=True))
        h.bridge.pressKey("h")
        assert h.bridge.privacyMode is True
        assert h.bridge._current_word == ""

    def test_a_fresh_stored_reading_answers_without_asking_again(
        self, harness_factory, monkeypatch
    ):
        """Pinned, not timed: a keystroke on a loaded shard can outlast 50 ms."""
        det = FakeDetector()
        h = harness_factory(det)
        monkeypatch.setattr(kb, "_FOCUS_READING_FRESH_S", 10.0)
        h.bridge.pressKey("h")
        reads = det.reads
        assert reads >= 1
        h.bridge.pressKey("e")  # inside the freshness window
        assert det.reads == reads
        monkeypatch.setattr(kb, "_FOCUS_READING_FRESH_S", -1.0)  # every reading is stale
        h.bridge.pressKey("l")
        assert det.reads == reads + 1


class TestAReadingForAnotherWindowIsIgnored:
    def test_on_the_keystroke_path(self, harness_factory):
        h = harness_factory(FakeDetector(is_password=True), probe_window=OTHER_WINDOW)
        h.bridge.pressKey("h")
        assert h.bridge.privacyMode is False
        assert h.bridge._current_word == "h"

    def test_when_it_arrives_late(self, harness_factory):
        h = harness_factory(
            FakeDetector(is_password=True, delay_s=STALL_S), probe_window=OTHER_WINDOW
        )
        h.bridge.pressKey("h")
        assert h.probe is not None
        assert h.wait_for(lambda: h.probe.latest() is not None)
        QTest.qWait(50)  # let the queued signal through
        assert h.bridge.privacyMode is False
        assert h.bridge._current_word == "h"

    def test_on_the_timer(self, harness_factory):
        h = harness_factory(FakeDetector(is_password=True), probe_window=OTHER_WINDOW)
        assert h.probe is not None
        assert h.probe.request(0.5) is not None
        h.bridge._check_password_field()
        assert h.bridge.privacyMode is False


class TestAnOlderQueuedReadingNeverUndoesANewerOne:
    def test_a_stale_password_reading_draining_late_keeps_the_word(self, harness_factory):
        """PR #246 review, P2: the queued road can be older than what a keystroke applied.

        A "password" reading is queued; a newer "not" reading is taken;
        a keystroke applies the newer one and tracks the character; the
        queue drains.  Applying the old "password" there turned privacy
        on and wiped the character, which is on screen and now untracked.
        """
        det = FakeDetector(is_password=True)
        h = harness_factory(det)
        assert h.probe is not None
        old = h.probe.request(0.5)  # queued, not yet processed
        assert old is not None and old.is_password
        det.is_password = False
        new = h.probe.request(0.5)
        assert new is not None and new.seq > old.seq
        h.bridge.pressKey("h")
        assert h.bridge.privacyMode is False
        assert h.bridge._current_word == "h"
        QTest.qWait(50)  # the queue drains: old "password", then new "not"
        assert h.bridge.privacyMode is False
        assert h.bridge._current_word == "h"

    def test_a_newer_reading_still_applies_after_an_older_one(self, harness_factory):
        det = FakeDetector(is_password=False)
        h = harness_factory(det)
        assert h.probe is not None
        assert h.probe.request(0.5) is not None
        h.bridge._check_password_field()
        assert h.bridge.privacyMode is False
        det.is_password = True
        assert h.probe.request(0.5) is not None
        h.bridge._check_password_field()
        assert h.bridge.privacyMode is True

    def test_a_fresh_password_reading_dropped_mid_transition_still_protects_the_key(
        self, harness_factory, monkeypatch
    ):
        """Second review of PR #246: dropping a reading must not consume it.

        ``GetForegroundWindow`` reads 0 during an activation change.  A
        fresh "password" reading delivered on that tick is dropped; the
        window is in front again before the reading is 50 ms old and a
        key is pressed.  The keystroke takes the stored reading (fresh,
        window matches, no refresh), so if the drop had moved the
        watermark the key went out with privacy off.
        """
        det = FakeDetector(is_password=True)
        h = harness_factory(det)
        assert h.probe is not None
        monkeypatch.setattr(kb, "_FOCUS_READING_FRESH_S", 10.0)  # pinned fresh
        reading = h.probe.request(0.5)
        assert reading is not None
        monkeypatch.setattr(h.bridge, "_get_foreground_window_id", lambda: 0)
        QTest.qWait(30)  # the queued delivery lands while the foreground reads 0
        assert h.bridge.privacyMode is False
        monkeypatch.setattr(h.bridge, "_get_foreground_window_id", lambda: WINDOW)
        reads = det.reads
        h.bridge.pressKey("h")
        assert det.reads == reads  # no refresh: the stored reading answered
        assert h.bridge.privacyMode is True
        assert h.bridge._current_word == ""

    def test_a_strictly_older_reading_is_still_rejected_after_a_drop(
        self, harness_factory, monkeypatch
    ):
        det = FakeDetector(is_password=False)
        h = harness_factory(det)
        assert h.probe is not None
        assert h.probe.request(0.5) is not None
        QTest.qWait(30)
        det.is_password = True
        old = h.probe.request(0.5)  # "password", seq k
        assert old is not None
        det.is_password = False
        new = h.probe.request(0.5)  # "not", seq k + 1
        assert new is not None
        h.bridge._apply_focus_reading(new)
        monkeypatch.setattr(h.bridge, "_get_foreground_window_id", lambda: 0)
        QTest.qWait(30)  # both drain while the foreground reads 0: neither applies
        monkeypatch.setattr(h.bridge, "_get_foreground_window_id", lambda: WINDOW)
        h.bridge._apply_focus_reading(old)  # older than what was applied
        assert h.bridge.privacyMode is False


class TestTheTimerReadsTheStoredAnswer:
    def test_it_applies_the_latest_reading_without_asking(self, harness_factory):
        det = FakeDetector(is_password=True)
        h = harness_factory(det)
        assert h.probe is not None
        assert h.probe.request(0.5) is not None
        reads = det.reads
        h.bridge._check_password_field()
        assert h.bridge.privacyMode is True
        assert det.reads == reads

    def test_it_clears_privacy_mode_when_the_field_is_left(self, harness_factory):
        det = FakeDetector(is_password=True)
        h = harness_factory(det)
        assert h.probe is not None
        assert h.probe.request(0.5) is not None
        h.bridge._check_password_field()
        assert h.bridge.privacyMode is True
        det.is_password = False
        assert h.probe.request(0.5) is not None
        h.bridge._check_password_field()
        assert h.bridge.privacyMode is False

    def test_the_manual_switch_still_overrides_it(self, harness_factory):
        det = FakeDetector(is_password=False)
        h = harness_factory(det)
        h.bridge.setPrivacyMode(True)
        assert h.probe is not None
        assert h.probe.request(0.5) is not None
        h.bridge._check_password_field()
        h.bridge.pressKey("h")
        assert h.bridge.privacyMode is True


class TestTheFocusPollReadsTheStoredToken:
    def _poll(self, h: ProbeHarness, token: str) -> None:
        h.detector.token = token
        assert h.probe is not None
        assert h.probe.request(0.5) is not None
        h.bridge._check_foreground_window()

    def test_a_changed_element_resets_the_word(self, harness_factory):
        h = harness_factory(FakeDetector())
        self._poll(h, "first-field")
        h.bridge.pressKey("h")
        assert h.bridge._current_word == "h"
        h.bridge._last_own_keystroke_at = float("-inf")  # age the keystroke past the grace
        self._poll(h, "second-field")
        assert h.bridge._current_word == ""
        assert h.bridge._last_focus_token == "second-field"

    def test_a_reading_older_than_a_reset_does_not_seed(self, harness_factory):
        """The stored reading names the field the user just left.

        Tab resets before the probe has read the new field.  Seeding the
        baseline from the old reading would compare it against the new
        field's token one poll later and wipe whatever was typed there in
        between: the ``hellis`` bug by another road.
        """
        h = harness_factory(FakeDetector())
        self._poll(h, "old-field")
        assert h.bridge._last_focus_token == "old-field"
        h.bridge._reset_typing_context()  # what Tab does
        h.bridge._check_foreground_window()  # the stored reading is older than the reset
        assert h.bridge._last_focus_token is None
        # A reading in the same clock tick as the reset is ignored too
        # (``time.monotonic`` ticks every 15.6 ms on Windows under 3.12),
        # which is the conservative side; the probe's next tick seeds.
        _settle(0.03)
        h.bridge.pressKey("h")  # typed into the new field before the probe reads it
        h.bridge._last_own_keystroke_at = float("-inf")
        self._poll(h, "new-field")  # the first reading after the reset seeds
        assert h.bridge._last_focus_token == "new-field"
        assert h.bridge._current_word == "h"

    def test_a_reading_for_another_window_says_nothing(self, harness_factory):
        h = harness_factory(FakeDetector(token="elsewhere"), probe_window=OTHER_WINDOW)
        assert h.probe is not None
        assert h.probe.request(0.5) is not None
        h.bridge._check_foreground_window()
        assert h.bridge._last_focus_token is None

    def test_the_churn_grace_is_judged_at_the_reading_s_own_time(
        self, harness_factory, monkeypatch
    ):
        """A reading is what focus looked like when it was taken, not when the poll ran."""
        monkeypatch.setattr(kb, "_FOCUS_CHURN_GRACE_S", 0.05)
        h = harness_factory(FakeDetector())
        self._poll(h, "empty-composer")
        h.bridge.pressKey("h")
        h.detector.token = "composer-with-text"
        assert h.probe is not None
        reading = h.probe.request(0.5)
        assert reading is not None
        # Taken 10 ms after the keystroke (inside the grace); the poll
        # runs long after it (outside the grace, measured from now).
        h.bridge._last_own_keystroke_at = reading.read_at - 0.01
        _settle(0.1)
        h.bridge._check_foreground_window()
        assert h.bridge._current_word == "h"
        assert h.bridge._last_focus_token == "composer-with-text"

    def test_and_a_reading_taken_after_the_grace_still_resets(self, harness_factory, monkeypatch):
        monkeypatch.setattr(kb, "_FOCUS_CHURN_GRACE_S", 0.05)
        h = harness_factory(FakeDetector())
        self._poll(h, "empty-composer")
        h.bridge.pressKey("h")
        h.detector.token = "another-field"
        assert h.probe is not None
        reading = h.probe.request(0.5)
        assert reading is not None
        h.bridge._last_own_keystroke_at = reading.read_at - 0.1
        h.bridge._check_foreground_window()
        assert h.bridge._current_word == ""


class TestShutdownStopsTheProbe:
    def test_the_thread_ends_and_the_detector_closes_on_it(self, harness_factory):
        det = FakeDetector()
        h = harness_factory(det)
        probe = h.probe
        assert probe is not None and probe.running
        thread = probe._thread
        h.bridge.shutdown()
        assert not probe.running
        assert thread is not None and det.closed_on == thread.ident
        assert h.bridge._focus_probe is None

    def test_a_reading_in_flight_at_shutdown_lands_nowhere(self, harness_factory):
        det = FakeDetector(is_password=True, delay_s=STALL_S)
        h = harness_factory(det)
        h.bridge.pressKey("h")  # the read is in flight
        h.bridge.shutdown()
        _settle(STALL_S + 0.2)
        QTest.qWait(50)
        assert h.bridge.privacyMode is False


class TestDetectionAvailabilityComesFromTheProbe:
    def test_an_available_probe_reports_available(self, harness_factory):
        h = harness_factory(FakeDetector())
        assert h.bridge.passwordDetectionAvailable is True

    def test_without_a_probe_the_direct_answer_stands(self, qapp, tmp_path, monkeypatch):
        monkeypatch.setattr("src.keyboard_bridge.start_focus_probe", lambda on_reading=None: None)
        monkeypatch.setattr("src.keyboard_bridge.detection_available", lambda: False)
        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.busy = False
            synth.is_available.return_value = True
            factory.return_value = synth
            bridge = KeyboardBridge()
        try:
            assert bridge._focus_probe is None
            assert bridge.passwordDetectionAvailable is False
        finally:
            bridge.shutdown()
