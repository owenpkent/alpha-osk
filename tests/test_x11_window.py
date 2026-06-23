"""Tests for the off-screen 'Tuck away' window-type helper.

The real ``XChangeProperty`` effect needs a live X server and is exercised
manually; here we lock down the platform gating and the always-safe no-op
behaviour so the helper can never crash a non-X11 session or a malformed call.
"""

from __future__ import annotations

import importlib

import pytest

import src.platform.x11_window as x11w


@pytest.fixture
def fresh_module(monkeypatch):
    """Reload the module so cached display/atoms don't leak between tests."""
    importlib.reload(x11w)
    yield x11w


def test_is_x11_true_only_for_linux_x11_with_display(monkeypatch, fresh_module):
    monkeypatch.setattr(fresh_module.sys, "platform", "linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("DISPLAY", ":0")
    assert fresh_module.is_x11() is True


@pytest.mark.parametrize(
    "platform,session,display",
    [
        ("win32", "x11", ":0"),       # Windows
        ("darwin", "x11", ":0"),      # macOS
        ("linux", "wayland", ":0"),   # Wayland session
        ("linux", "x11", ""),         # no DISPLAY
    ],
)
def test_is_x11_false_off_x11(monkeypatch, fresh_module, platform, session, display):
    monkeypatch.setattr(fresh_module.sys, "platform", platform)
    monkeypatch.setenv("XDG_SESSION_TYPE", session)
    if display:
        monkeypatch.setenv("DISPLAY", display)
    else:
        monkeypatch.delenv("DISPLAY", raising=False)
    assert fresh_module.is_x11() is False


def test_set_window_dock_noop_when_not_x11(monkeypatch, fresh_module):
    """Off X11 the call must short-circuit without touching libX11."""
    monkeypatch.setattr(fresh_module, "is_x11", lambda: False)
    # If it tried to open a display this would raise; it must not.
    monkeypatch.setattr(
        fresh_module, "_ensure_display",
        lambda: (_ for _ in ()).throw(AssertionError("must not open display")),
    )
    assert fresh_module.set_window_dock(12345, True) is False


def test_set_window_dock_noop_for_zero_window_id(monkeypatch, fresh_module):
    """A not-yet-realized window (winId()==0) must be a safe no-op."""
    monkeypatch.setattr(fresh_module, "is_x11", lambda: True)
    assert fresh_module.set_window_dock(0, True) is False


def test_set_window_dock_false_when_display_unavailable(monkeypatch, fresh_module):
    """If libX11/display can't be opened, fail closed rather than raising."""
    monkeypatch.setattr(fresh_module, "is_x11", lambda: True)
    monkeypatch.setattr(fresh_module, "_ensure_display", lambda: None)
    assert fresh_module.set_window_dock(12345, True) is False
