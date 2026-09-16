"""Tests for run.py's launcher orchestration.

Focused on FIX 4 of the security audit: elevation (``ensure_admin_windows``)
must run AFTER dependency installation and the Python version check, not
before it. The previous order elevated first, so ``pip install -r
requirements.txt`` (against unpinned floors) and every import of this
repo's own code, both of which live in a user-writable directory, ran
under an admin token. These tests pin the observable call order of
``main()`` rather than the internals of ``ensure_admin_windows`` itself,
which is Windows/ctypes-specific and a no-op off Windows.
"""

from __future__ import annotations

import socketserver
import sys
import webbrowser

import pytest

import run


def _recorder(order: list[str], name: str, retval=None):
    def _fn(*_args, **_kwargs):
        order.append(name)
        return retval

    return _fn


class TestMainOrdering:
    """Elevation must be the LAST setup step before launching the
    keyboard, so a compromised run.py / src/ tree can't ride the UAC
    consent the user grants for what they believe is just "run the
    keyboard"."""

    def test_elevation_happens_after_deps_and_version_check(self, monkeypatch):
        order: list[str] = []
        monkeypatch.setattr(run.os, "chdir", _recorder(order, "chdir"))
        monkeypatch.setattr(
            run, "check_python_version", _recorder(order, "check_python_version", True)
        )
        monkeypatch.setattr(run, "check_system_deps", _recorder(order, "check_system_deps", []))
        monkeypatch.setattr(
            run, "setup_virtual_environment", _recorder(order, "setup_virtual_environment", True)
        )
        monkeypatch.setattr(run, "check_dependencies", _recorder(order, "check_dependencies", True))
        monkeypatch.setattr(run, "ensure_admin_windows", _recorder(order, "ensure_admin_windows"))
        monkeypatch.setattr(run, "run_keyboard", _recorder(order, "run_keyboard", 0))
        monkeypatch.setattr(run.sys, "argv", ["run.py"])

        rc = run.main()

        assert rc == 0
        assert order == [
            "chdir",
            "check_python_version",
            "check_system_deps",
            "setup_virtual_environment",
            "check_dependencies",
            "ensure_admin_windows",
            "run_keyboard",
        ], f"unexpected step order: {order}"

    def test_dashboard_mode_never_elevates(self, monkeypatch):
        # Dashboard mode returns before dependency installation is even
        # reached, so it must not trigger a UAC prompt at all.
        order: list[str] = []
        monkeypatch.setattr(run.os, "chdir", lambda *_a, **_kw: None)
        monkeypatch.setattr(run, "ensure_admin_windows", _recorder(order, "ensure_admin_windows"))
        monkeypatch.setattr(run, "run_dashboard", _recorder(order, "run_dashboard", 0))
        monkeypatch.setattr(run.sys, "argv", ["run.py", "--dashboard"])

        rc = run.main()

        assert rc == 0
        assert order == ["run_dashboard"]

    def test_aborts_before_elevation_when_python_version_check_fails(self, monkeypatch):
        order: list[str] = []
        monkeypatch.setattr(run.os, "chdir", lambda *_a, **_kw: None)
        monkeypatch.setattr(run, "check_python_version", lambda: False)
        monkeypatch.setattr(run, "ensure_admin_windows", _recorder(order, "ensure_admin_windows"))
        monkeypatch.setattr(run.sys, "argv", ["run.py"])

        rc = run.main()

        assert rc == 1
        assert order == [], "must not elevate once the version check already failed"

    def test_aborts_before_elevation_when_dependency_install_fails(self, monkeypatch):
        order: list[str] = []
        monkeypatch.setattr(run.os, "chdir", lambda *_a, **_kw: None)
        monkeypatch.setattr(run, "check_python_version", lambda: True)
        monkeypatch.setattr(run, "check_system_deps", lambda: [])
        monkeypatch.setattr(run, "setup_virtual_environment", lambda: True)
        monkeypatch.setattr(run, "check_dependencies", lambda: False)
        monkeypatch.setattr(run, "ensure_admin_windows", _recorder(order, "ensure_admin_windows"))
        monkeypatch.setattr(run.sys, "argv", ["run.py"])

        rc = run.main()

        assert rc == 1
        assert order == [], "must not elevate once dependency install already failed"


class TestDashboardBindsToLoopback:
    """FIX 2 of the security audit: the dev dashboard used to bind every
    interface (``socketserver.TCPServer(("", port), handler)``), which
    serves this repo's templates to the whole LAN, not just the machine
    running ``python run.py --dashboard``, for as long as it stays up."""

    def test_run_dashboard_binds_127_0_0_1_only(self, monkeypatch):
        captured: dict[str, object] = {}

        class FakeTCPServer:
            """Stands in for the real server so the test opens no socket."""

            def __init__(self, address, handler):
                captured["address"] = address

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def serve_forever(self):
                # Stand-in for Ctrl+C so run_dashboard returns instead of
                # blocking the test on a real event loop.
                raise KeyboardInterrupt

        # run_dashboard does `import socketserver` inside its own body,
        # which binds to the same cached module object this patches, so
        # the fake reaches it without touching run.py's internals.
        monkeypatch.setattr(socketserver, "TCPServer", FakeTCPServer)
        monkeypatch.setattr(webbrowser, "open", lambda *_a, **_kw: None)

        rc = run.run_dashboard()

        assert rc == 0
        assert captured["address"][0] == "127.0.0.1", (
            "the dashboard must bind loopback only, never every interface"
        )


class TestEnsureAdminWindowsIdempotent:
    """The relaunch must not loop: once ``IsUserAnAdmin()`` is true, as
    it is inside the process ``ShellExecuteW(..., "runas", ...)``
    spawns, ``ensure_admin_windows`` must fall straight through instead
    of re-elevating."""

    @pytest.mark.skipif(sys.platform != "win32", reason="ctypes.windll is Windows-only")
    def test_returns_immediately_when_already_admin(self, monkeypatch):
        import ctypes

        monkeypatch.setattr(run, "IS_WINDOWS", True)
        monkeypatch.setattr(ctypes.windll.shell32, "IsUserAnAdmin", lambda: True)
        shell_execute_calls = []
        monkeypatch.setattr(
            ctypes.windll.shell32,
            "ShellExecuteW",
            lambda *a: shell_execute_calls.append(a) or 42,
        )

        run.ensure_admin_windows()

        assert shell_execute_calls == [], "an already-admin process must not re-elevate"
