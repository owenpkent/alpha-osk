"""Tests for platform-specific password field detection.

The Linux path depends on PyGObject / AT-SPI which isn't typically
available in the unit-test venv, so the tests here mock the gi module
to exercise both the happy path (PASSWORD_TEXT focus event flips the
flag) and the fallback path (no gi → null detector).
"""

from __future__ import annotations

import sys
import types

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
        def __init__(self, callback): self.callback = callback
        def register(self, _name): pass

    class _EventListener:
        @staticmethod
        def new(callback):
            listener = _Listener(callback)
            registered.append(listener)
            return listener

    fake_atspi.EventListener = _EventListener  # type: ignore[attr-defined]
    fake_atspi.event_main = lambda: None       # type: ignore[attr-defined]
    fake_atspi.event_quit = lambda: None       # type: ignore[attr-defined]
    fake_atspi._listeners = registered         # type: ignore[attr-defined]

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
            type="object:state-changed:focused", detail1=1, source=source,
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
            type="object:state-changed:focused", detail1=1, source=source,
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
            type="object:state-changed:focused", detail1=0, source=source,
        )
        det._on_focus_event(event)
        assert det.check() is True  # unchanged

    def test_event_without_source_is_safe(self, monkeypatch):
        _install_fake_gi(monkeypatch)
        det = pd._LinuxATSPIDetector()
        det._on_focus_event(types.SimpleNamespace(
            type="focus:", source=None,
        ))
        # Shouldn't raise; flag remains False
        assert det.check() is False

    def test_get_state_raising_doesnt_kill_listener(self, monkeypatch):
        _install_fake_gi(monkeypatch)
        det = pd._LinuxATSPIDetector()

        class _Bad:
            def get_state_set(self): raise RuntimeError("boom")

        det._on_focus_event(types.SimpleNamespace(
            type="focus:", detail1=1, source=_Bad(),
        ))
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
