"""Tests for platform-specific password field detection.

The Linux path depends on PyGObject / AT-SPI which isn't typically
available in the unit-test venv, so the tests here mock the gi module
to exercise both the happy path (PASSWORD_TEXT focus event flips the
flag) and the fallback path (no gi → null detector).
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
import threading
import time
import types
from unittest.mock import MagicMock

import pytest

from src.platform import password_detect as pd


def _install_fake_gi(monkeypatch, *, init_rc: int = 0, raise_on_init: bool = False):
    """Inject a minimal fake ``gi.repository.Atspi`` into sys.modules.

    Returns the fake ``Atspi`` module so the test can trigger focus
    events on demand.
    """
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda name, ver: None  # type: ignore[attr-defined]

    fake_repo = types.ModuleType("gi.repository")
    fake_atspi = types.ModuleType("gi.repository.Atspi")

    state_type = types.SimpleNamespace(PASSWORD_TEXT="PASSWORD_TEXT_SENTINEL")
    fake_atspi.StateType = state_type  # type: ignore[attr-defined]

    def _init() -> int:
        if raise_on_init:
            raise RuntimeError("bus missing")
        return init_rc

    fake_atspi.init = _init  # type: ignore[attr-defined]

    # Keep a slot we can call from the test to deliver a focus event.
    registered: list = []

    class _Listener:
        def __init__(self, callback):
            self.callback = callback

        def register(self, _name):
            pass

    class _EventListener:
        @staticmethod
        def new(callback):
            listener = _Listener(callback)
            registered.append(listener)
            return listener

    fake_atspi.EventListener = _EventListener  # type: ignore[attr-defined]
    fake_atspi.event_main = lambda: None  # type: ignore[attr-defined]
    fake_atspi.event_quit = lambda: None  # type: ignore[attr-defined]
    fake_atspi._listeners = registered  # type: ignore[attr-defined]

    fake_repo.Atspi = fake_atspi  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(sys.modules, "gi.repository", fake_repo)
    monkeypatch.setitem(sys.modules, "gi.repository.Atspi", fake_atspi)
    return fake_atspi


def _state_set(contains_password: bool):
    class _StateSet:
        def contains(self, token):
            return contains_password and token == "PASSWORD_TEXT_SENTINEL"

    return _StateSet()


class TestLinuxATSPI:
    """_LinuxATSPIDetector end-to-end via a fake gi module."""

    def test_no_gi_module_falls_back(self, monkeypatch):
        # Ensure `import gi` fails
        monkeypatch.setitem(sys.modules, "gi", None)
        det = pd._LinuxATSPIDetector()
        assert det.available is False
        assert det.check() is False

    def test_init_failure_falls_back(self, monkeypatch):
        _install_fake_gi(monkeypatch, raise_on_init=True)
        det = pd._LinuxATSPIDetector()
        assert det.available is False

    def test_init_nonzero_rc_falls_back(self, monkeypatch):
        _install_fake_gi(monkeypatch, init_rc=7)
        det = pd._LinuxATSPIDetector()
        assert det.available is False

    def test_happy_path_sets_available(self, monkeypatch):
        _install_fake_gi(monkeypatch, init_rc=0)
        det = pd._LinuxATSPIDetector()
        assert det.available is True
        assert det.check() is False  # nothing focused yet

    def test_focus_event_with_password_flips_flag(self, monkeypatch):
        _install_fake_gi(monkeypatch)
        det = pd._LinuxATSPIDetector()

        source = types.SimpleNamespace(get_state_set=lambda: _state_set(True))
        event = types.SimpleNamespace(
            type="object:state-changed:focused",
            detail1=1,
            source=source,
        )
        det._on_focus_event(event)
        assert det.check() is True

    def test_focus_event_on_normal_widget_flips_flag_off(self, monkeypatch):
        _install_fake_gi(monkeypatch)
        det = pd._LinuxATSPIDetector()
        # Pre-seed as if a password was focused.
        det._is_password = True

        source = types.SimpleNamespace(get_state_set=lambda: _state_set(False))
        event = types.SimpleNamespace(
            type="object:state-changed:focused",
            detail1=1,
            source=source,
        )
        det._on_focus_event(event)
        assert det.check() is False

    def test_defocus_event_is_ignored(self, monkeypatch):
        """detail1=0 means focus LEFT the source — not a statement about
        the new focus target, so we must not flip the flag."""
        _install_fake_gi(monkeypatch)
        det = pd._LinuxATSPIDetector()
        det._is_password = True

        source = types.SimpleNamespace(get_state_set=lambda: _state_set(False))
        event = types.SimpleNamespace(
            type="object:state-changed:focused",
            detail1=0,
            source=source,
        )
        det._on_focus_event(event)
        assert det.check() is True  # unchanged

    def test_event_without_source_is_safe(self, monkeypatch):
        _install_fake_gi(monkeypatch)
        det = pd._LinuxATSPIDetector()
        det._on_focus_event(
            types.SimpleNamespace(
                type="focus:",
                source=None,
            )
        )
        # Shouldn't raise; flag remains False
        assert det.check() is False

    def test_get_state_raising_doesnt_kill_listener(self, monkeypatch):
        _install_fake_gi(monkeypatch)
        det = pd._LinuxATSPIDetector()

        class _Bad:
            def get_state_set(self):
                raise RuntimeError("boom")

        det._on_focus_event(
            types.SimpleNamespace(
                type="focus:",
                detail1=1,
                source=_Bad(),
            )
        )
        assert det.check() is False  # no crash, no flip


class TestCreateDetector:
    """_create_detector picks the right backend per platform."""

    def test_linux_prefers_atspi_when_available(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        _install_fake_gi(monkeypatch)
        det = pd._create_detector()
        assert isinstance(det, pd._LinuxATSPIDetector)

    def test_linux_falls_back_to_null_without_gi(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setitem(sys.modules, "gi", None)
        det = pd._create_detector()
        assert isinstance(det, pd._NullDetector)

    def test_is_password_field_public_api_returns_bool(self, monkeypatch):
        # Force a fresh detector creation on the module.
        monkeypatch.setattr(pd, "_detector", None)
        assert isinstance(pd.is_password_field(), bool)


class TestDetectionAvailable:
    """detection_available() surfaces whether the active detector has a
    working backend, vs. the null fallback. Exposed to QML via
    KeyboardBridge.passwordDetectionAvailable so the UI can tell the
    user the manual Learning toggle is their only protection.
    """

    def test_false_when_detector_is_null(self, monkeypatch):
        monkeypatch.setattr(pd, "_detector", pd._NullDetector())
        assert pd.detection_available() is False

    def test_true_when_detector_is_not_null(self, monkeypatch):
        monkeypatch.setattr(pd, "_detector", MagicMock())
        assert pd.detection_available() is True

    def test_lazily_creates_detector_on_first_call(self, monkeypatch):
        monkeypatch.setattr(pd, "_detector", None)
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setitem(sys.modules, "gi", None)  # force the null fallback
        assert pd.detection_available() is False
        assert isinstance(pd._detector, pd._NullDetector)


class TestUnavailableDetectionWarns:
    """Falling back to the null detector must log a warning, not routine
    info: it means auto-detection can never turn on this session, so the
    user needs to know the manual toggle is now load-bearing.
    """

    def test_linux_without_gi_logs_a_warning(self, monkeypatch, caplog):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setitem(sys.modules, "gi", None)
        with caplog.at_level(logging.WARNING, logger="PasswordDetect"):
            pd._create_detector()
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    def test_unsupported_platform_logs_a_warning(self, monkeypatch, caplog):
        monkeypatch.setattr(sys, "platform", "freebsd")
        with caplog.at_level(logging.WARNING, logger="PasswordDetect"):
            pd._create_detector()
        assert any(r.levelno == logging.WARNING for r in caplog.records)


# A hidden password edit control owned by a child process. "pump" answers
# messages; "hang" never pumps, which is what a hung Resolve or Premiere
# looks like from our side of SendMessage.
_EDIT_OWNER_SCRIPT = r"""
import ctypes, sys, time
from ctypes import wintypes
user32 = ctypes.WinDLL("user32")
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
ES_PASSWORD = 0x0020
hwnd = user32.CreateWindowExW(0, "EDIT", None, ES_PASSWORD, 0, 0, 0, 0, None, None, None, None)
print(hwnd or 0, flush=True)
if sys.argv[1] == "hang":
    time.sleep(60)
else:
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 window messages")
class TestTheWin32ProbeNeverHangsTheKeyboard:
    """A hung foreground app must not hang the keyboard's UI thread.

    The probe runs on the UI thread. With a plain ``SendMessageW`` a hung
    target never answered, and Windows closed the keyboard as hung
    (AppHangXProcB1, blocked on Resolve, Acrobat and Premiere Pro).
    """

    @staticmethod
    def _edit_owner(mode: str):
        proc = subprocess.Popen(
            [sys.executable, "-c", _EDIT_OWNER_SCRIPT, mode],
            stdout=subprocess.PIPE,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
        )
        assert proc.stdout is not None
        hwnd = int(proc.stdout.readline().strip() or 0)
        if not hwnd:
            proc.kill()
            pytest.fail("child process could not create its edit control")
        return proc, hwnd

    @staticmethod
    def _detector_focused_on(hwnd: int):
        """The real detector, with the foreground lookups pointed at ``hwnd``."""
        real = ctypes.windll.user32  # type: ignore[attr-defined]

        def gui_thread_info(_tid, info_ref):
            info_ref._obj.hwndFocus = hwnd
            return 1

        class _FocusedOn:
            GetForegroundWindow = staticmethod(lambda: hwnd)
            GetWindowThreadProcessId = staticmethod(lambda _h, _p: 1)
            GetGUIThreadInfo = staticmethod(gui_thread_info)

            def __getattr__(self, name):
                return getattr(real, name)

        det = pd._WindowsWin32Detector()
        det._user32 = _FocusedOn()
        return det

    def test_a_hung_target_answers_not_a_password_field_promptly(self):
        proc, hwnd = self._edit_owner("hang")
        try:
            det = self._detector_focused_on(hwnd)
            started = time.monotonic()
            assert det.check() is False
            assert time.monotonic() - started < 2.0
        finally:
            proc.kill()
            proc.wait()

    def test_a_responsive_password_field_is_still_detected(self):
        proc, hwnd = self._edit_owner("pump")
        try:
            assert self._detector_focused_on(hwnd).check() is True
        finally:
            proc.kill()
            proc.wait()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")
class TestUIAStartsUnderQt:
    """UIA detection must start on the thread Qt has already claimed.

    Qt's Windows platform plugin initialises the UI thread as a
    single-threaded apartment, so a multithreaded ``CoInitializeEx``
    answers RPC_E_CHANGED_MODE. Treating that as failure kept every real
    launch on the Win32 fallback, which cannot see browser password fields.
    """

    @staticmethod
    def _in_thread(fn, apartment: int | None):
        """Run ``fn`` on a fresh thread whose COM state is set up first.

        ``apartment`` 2 is COINIT_APARTMENTTHREADED, the state Qt's Windows
        platform plugin leaves the UI thread in; None leaves COM untouched.
        """
        result: dict = {}

        def body():
            ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
            if apartment is not None:
                ole32.CoInitializeEx(None, apartment)
            try:
                result["value"] = fn()
            except BaseException as exc:  # noqa: BLE001 - re-raised on the test thread
                result["error"] = exc
            finally:
                if apartment is not None:
                    ole32.CoUninitialize()

        t = threading.Thread(target=body)
        t.start()
        t.join(30)
        if "error" in result:
            raise result["error"]
        return result["value"]

    @staticmethod
    def _read_timeouts(det) -> tuple[int, int]:
        values = []
        for slot in (pd._VT_GET_CONNECTION_TIMEOUT, pd._VT_GET_TRANSACTION_TIMEOUT):
            get = pd._vtable_func(
                det._automation, slot, ctypes.c_long, ctypes.POINTER(ctypes.c_ulong)
            )
            out = ctypes.c_ulong()
            assert get(det._automation, ctypes.byref(out)) == 0
            values.append(out.value)
        return values[0], values[1]

    def test_it_starts_on_a_single_threaded_apartment(self):
        def build():
            det = pd._WindowsUIADetector()
            try:
                return det.available, det._owns_com
            finally:
                det.close()

        available, owns_com = self._in_thread(build, apartment=2)
        assert available is True
        # Qt owns that apartment; closing the detector must not tear it down.
        assert owns_com is False

    def test_closing_leaves_the_apartment_it_did_not_open(self):
        def build_and_close():
            pd._WindowsUIADetector().close()
            ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
            # S_FALSE (1): COM is still initialised as an STA on this thread.
            hr = ole32.CoInitializeEx(None, 2)
            ole32.CoUninitialize()
            return hr

        assert self._in_thread(build_and_close, apartment=2) == 1

    def test_it_still_starts_where_nothing_initialised_com(self):
        def build():
            det = pd._WindowsUIADetector()
            try:
                return det.available
            finally:
                det.close()

        assert self._in_thread(build, apartment=None) is True

    def test_every_call_is_bounded_well_below_the_hang_threshold(self):
        def build():
            det = pd._WindowsUIADetector()
            try:
                return self._read_timeouts(det)
            finally:
                det.close()

        connection, transaction = self._in_thread(build, apartment=2)
        assert (connection, transaction) == (
            pd._UIA_CONNECTION_TIMEOUT_MS,
            pd._UIA_TRANSACTION_TIMEOUT_MS,
        )
        # UIA's defaults are 2000 and 20000 ms; Windows calls a window hung at 5 s.
        assert connection <= 500 and transaction <= 500

    def test_the_real_qt_platform_gets_uia(self):
        """End to end: a QGuiApplication on the real Windows platform, no window."""
        script = (
            "import sys\n"
            "from PySide6.QtGui import QGuiApplication\n"
            "app = QGuiApplication(['probe', '-platform', 'windows'])\n"
            "from src.platform import password_detect as pd\n"
            "print(pd._create_detector().__class__.__name__)\n"
        )
        env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
        out = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
        )
        assert out.stdout.strip().splitlines()[-1] == "_WindowsUIADetector", out.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows UI Automation")
class TestUIASkipsWhatCannotAnswer:
    """A hung foreground app, or our own window, is never asked."""

    @staticmethod
    def _foreground(monkeypatch, *, hung: bool, pid: int):
        def thread_process_id(_hwnd, pid_ref):
            pid_ref._obj.value = pid
            return 1

        fake = types.SimpleNamespace(
            GetForegroundWindow=lambda: 0x1234,
            IsHungAppWindow=lambda _hwnd: 1 if hung else 0,
            GetWindowThreadProcessId=thread_process_id,
        )
        monkeypatch.setattr(ctypes.windll, "user32", fake)  # type: ignore[attr-defined]

    @staticmethod
    def _uia_calls(monkeypatch) -> list:
        calls: list = []

        def spy(*args, **kwargs):
            calls.append(args)
            raise AssertionError("UIA was queried")

        monkeypatch.setattr(pd, "_vtable_func", spy)
        return calls

    @staticmethod
    def _armed_detector():
        det = pd._WindowsUIADetector.__new__(pd._WindowsUIADetector)
        det.available = True
        det._automation = ctypes.c_void_p(1)
        det._owns_com = False
        return det

    def test_a_hung_app_is_not_queried(self, monkeypatch):
        self._foreground(monkeypatch, hung=True, pid=os.getpid() + 1)
        calls = self._uia_calls(monkeypatch)
        det = self._armed_detector()
        assert det.check() is False
        assert det.focus_token() is None
        assert calls == []

    def test_our_own_window_is_not_queried(self, monkeypatch):
        self._foreground(monkeypatch, hung=False, pid=os.getpid())
        calls = self._uia_calls(monkeypatch)
        det = self._armed_detector()
        assert det.check() is False
        assert det.focus_token() is None
        assert calls == []

    def test_a_responsive_app_is_queried(self, monkeypatch):
        self._foreground(monkeypatch, hung=False, pid=os.getpid() + 1)
        calls = self._uia_calls(monkeypatch)
        self._armed_detector().check()
        assert calls != []
