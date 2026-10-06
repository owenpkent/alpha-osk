"""The signals between the keyboard, the updater and the update helper.

Three small things, each the cheapest durable form its sender could use:
marker files in the helper's stage (they outlive the keyboard, which the
installer kills), and a named Win32 event the *new* keyboard creates once its
window has been painted (the helper opens it for waiting only).

The event tests use a per-test event name.  The real name is shared with
whatever keyboard is running on the machine, which has already created and
set it; a test on the real name would pass or fail on the developer's own
session rather than on the code.
"""

from __future__ import annotations

import ctypes
import sys
import uuid
from pathlib import Path

import pytest

from src import update_signals


class TestTheMarkerFiles:
    def test_touch_creates_the_file_and_its_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "stage" / "installer-launched"
        assert update_signals.touch(target) is True
        assert target.is_file()

    def test_a_write_that_cannot_happen_is_reported_not_raised(self, tmp_path: Path) -> None:
        blocker = tmp_path / "a-file"
        blocker.write_bytes(b"")
        # A directory cannot be made under a file.
        assert update_signals.touch(blocker / "installer-launched") is False

    def test_the_names_are_the_ones_the_helper_reads(self) -> None:
        assert update_signals.INSTALLER_LAUNCHED_FILE == "installer-launched"
        assert update_signals.CANCEL_FILE == "cancel"
        assert update_signals.KEYBOARD_SHOWN_EVENT.startswith("Local\\")


@pytest.fixture
def private_event(monkeypatch: pytest.MonkeyPatch):
    """A unique event name, and a clean module state, restored afterwards."""
    name = f"Local\\AlphaOSK.Test.{uuid.uuid4().hex}"
    monkeypatch.setattr(update_signals, "KEYBOARD_SHOWN_EVENT", name)
    monkeypatch.setattr(update_signals, "_shown_event_handle", None)
    yield name
    handle = update_signals._shown_event_handle
    if handle is not None:
        update_signals.close_event(handle)


@pytest.mark.skipif(sys.platform != "win32", reason="named Win32 events")
class TestTheKeyboardShownEvent:
    def test_it_does_not_exist_until_the_keyboard_announces(self, private_event) -> None:
        assert update_signals.open_keyboard_shown_event() is None

    def test_an_announce_is_seen_by_a_waiter_that_opened_it_afterwards(self, private_event) -> None:
        assert update_signals.announce_keyboard_shown() is True
        handle = update_signals.open_keyboard_shown_event()
        assert handle is not None
        try:
            assert update_signals.event_is_set(handle) is True
        finally:
            update_signals.close_event(handle)

    def test_a_waiter_that_opened_first_sees_the_set(self, private_event) -> None:
        """The helper polls from before the keyboard exists, so it can only
        open the event once the keyboard has created it, and what it must then
        see is the state, not a one-shot pulse: manual-reset."""
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateEventW.restype = ctypes.c_void_p
        kernel32.CreateEventW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_wchar_p,
        ]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        # Something else created it first, unsignalled.
        creator = kernel32.CreateEventW(None, True, False, private_event)
        assert creator
        try:
            handle = update_signals.open_keyboard_shown_event()
            assert handle is not None
            try:
                assert update_signals.event_is_set(handle) is False, "created clear"
                assert update_signals.announce_keyboard_shown() is True
                assert update_signals.event_is_set(handle) is True
            finally:
                update_signals.close_event(handle)
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(creator))

    def test_announcing_twice_is_harmless(self, private_event) -> None:
        assert update_signals.announce_keyboard_shown() is True
        assert update_signals.announce_keyboard_shown() is True

    def test_the_handle_is_kept_so_the_event_outlives_the_call(self, private_event) -> None:
        """An event exists only while a handle to it is open: dropped at the
        end of the call, a helper arriving a second later would find nothing."""
        update_signals.announce_keyboard_shown()
        assert update_signals._shown_event_handle is not None
        handle = update_signals.open_keyboard_shown_event()
        assert handle is not None
        update_signals.close_event(handle)


class TestOffWindows:
    def test_it_is_inert_elsewhere(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert update_signals.announce_keyboard_shown() is False
        assert update_signals.open_keyboard_shown_event() is None
        assert update_signals.event_is_set(1) is False
        update_signals.close_event(1)  # must not raise
