"""Tests for the outside-click probe (``src/platform/pointer.py``).

The ctypes calls themselves can only run on Windows, so the seams under
test are the two layers above them: the bit arithmetic that turns
``GetAsyncKeyState``'s two flags into "a press happened", and the
composition that decides whether that press belongs to us.  Both are
where the behaviour lives; both are platform-independent.
"""

import os

import pytest

from src.platform import pointer


@pytest.fixture(autouse=True)
def _as_windows(monkeypatch):
    """Run the Windows path on every host, with a clean button baseline."""
    monkeypatch.setattr(pointer, "_IS_WINDOWS", True)
    monkeypatch.setattr(pointer, "_button_was_down", False)


class TestExternalClickDetected:
    def _probe(self, monkeypatch, *, pressed: bool, pid):
        monkeypatch.setattr(pointer, "_left_button_pressed_since_last_call", lambda: pressed)
        monkeypatch.setattr(pointer, "_pid_under_cursor", lambda: pid)

    def test_a_click_on_another_process_is_reported(self, monkeypatch):
        self._probe(monkeypatch, pressed=True, pid=os.getpid() + 1)
        assert pointer.external_click_detected() is True

    def test_a_click_on_our_own_window_is_not(self, monkeypatch):
        """Every key tap is a click. Without this the keyboard would wipe
        its own prediction context on each keystroke."""
        self._probe(monkeypatch, pressed=True, pid=os.getpid())
        assert pointer.external_click_detected() is False

    def test_an_unreadable_owner_fails_closed(self, monkeypatch):
        """None means "don't know", and an unreadable signal must never be
        the reason the user's typing context is thrown away."""
        self._probe(monkeypatch, pressed=True, pid=None)
        assert pointer.external_click_detected() is False

    def test_no_press_means_the_pointer_is_never_read(self, monkeypatch):
        calls = []
        monkeypatch.setattr(pointer, "_left_button_pressed_since_last_call", lambda: False)
        monkeypatch.setattr(pointer, "_pid_under_cursor", lambda: calls.append(1))
        assert pointer.external_click_detected() is False
        assert calls == []

    def test_other_platforms_are_a_no_op(self, monkeypatch):
        monkeypatch.setattr(pointer, "_IS_WINDOWS", False)
        self._probe(monkeypatch, pressed=True, pid=os.getpid() + 1)
        assert pointer.external_click_detected() is False

    def test_a_failing_probe_is_swallowed(self, monkeypatch):
        def boom():
            raise OSError("user32 said no")

        monkeypatch.setattr(pointer, "_left_button_pressed_since_last_call", boom)
        assert pointer.external_click_detected() is False


class TestPressDetection:
    """Both of ``GetAsyncKeyState``'s bits, because each has a hole."""

    def _states(self, monkeypatch, values):
        it = iter(values)
        monkeypatch.setattr(pointer, "_read_left_button_state", lambda: next(it))

    def test_a_button_down_transition_counts(self, monkeypatch):
        # Low bit absent: another process on the desktop got there first.
        self._states(monkeypatch, [0x0000, 0x8000])
        assert pointer._left_button_pressed_since_last_call() is False
        assert pointer._left_button_pressed_since_last_call() is True

    def test_a_held_button_only_counts_once(self, monkeypatch):
        """A drag-select is one press, not one per poll."""
        self._states(monkeypatch, [0x8000, 0x8000, 0x8000])
        assert pointer._left_button_pressed_since_last_call() is True
        assert pointer._left_button_pressed_since_last_call() is False
        assert pointer._left_button_pressed_since_last_call() is False

    def test_a_click_that_ends_between_polls_still_counts(self, monkeypatch):
        """The hole the high bit alone has: button already released by the
        time we look, so only "pressed since last call" saw it."""
        self._states(monkeypatch, [0x0000, 0x0001])
        assert pointer._left_button_pressed_since_last_call() is False
        assert pointer._left_button_pressed_since_last_call() is True

    def test_an_idle_button_never_counts(self, monkeypatch):
        self._states(monkeypatch, [0x0000, 0x0000])
        assert pointer._left_button_pressed_since_last_call() is False
        assert pointer._left_button_pressed_since_last_call() is False

    def test_an_unreadable_state_counts_as_nothing(self, monkeypatch):
        self._states(monkeypatch, [None])
        assert pointer._left_button_pressed_since_last_call() is False
