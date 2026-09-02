"""Tests for app-level wiring in ``src/keyboard_app.py``.

Covers the one-time purge of diagnostic logs written before the
typed-content fix (those files could hold a transcript of what the user
typed, including a password typed while privacy mode was active, so
removing them on upgrade is part of that fix rather than cleanup), the
exception hooks that put a crash into that log in the first place, and
that the composition root itself is no longer exempt from mypy.

The Windows window-styling tests (always-on-top, the taskbar-button dance)
moved to ``tests/test_windows_window.py`` alongside the code they cover,
``src/platform/windows_window.py``.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import src.keyboard_app as keyboard_app
from src.keyboard_app import (
    _LOG_PURGE_SENTINEL,
    _configure_logging,
    _install_exception_hooks,
    _purge_pre_fix_logs,
)


def _seed_logs(config_dir: Path) -> None:
    """Write a main log plus the three rotations the handler can produce."""
    (config_dir / "alpha-osk.log").write_text("secret typed text", encoding="utf-8")
    for n in (1, 2, 3):
        (config_dir / f"alpha-osk.log.{n}").write_text(f"rotation {n}", encoding="utf-8")


class TestPurgePreFixLogs:
    def test_removes_the_log_and_every_rotation(self, tmp_path: Path) -> None:
        _seed_logs(tmp_path)

        removed = _purge_pre_fix_logs(tmp_path)

        assert removed == 4
        assert sorted(tmp_path.glob("alpha-osk.log*")) == []

    def test_writes_a_sentinel_so_it_runs_once(self, tmp_path: Path) -> None:
        _seed_logs(tmp_path)

        _purge_pre_fix_logs(tmp_path)

        assert (tmp_path / _LOG_PURGE_SENTINEL).is_file()

    def test_second_run_is_a_no_op(self, tmp_path: Path) -> None:
        """A user who wants logs kept across restarts must not fight us."""
        _seed_logs(tmp_path)
        _purge_pre_fix_logs(tmp_path)

        # Logs written by the *fixed* build are safe and must survive.
        (tmp_path / "alpha-osk.log").write_text("post-fix, no typed content", encoding="utf-8")

        removed = _purge_pre_fix_logs(tmp_path)

        assert removed == 0
        assert (tmp_path / "alpha-osk.log").exists()

    def test_no_logs_present_still_marks_done(self, tmp_path: Path) -> None:
        """A fresh install has nothing to purge but must not re-check forever."""
        removed = _purge_pre_fix_logs(tmp_path)

        assert removed == 0
        assert (tmp_path / _LOG_PURGE_SENTINEL).is_file()

    def test_leaves_unrelated_files_alone(self, tmp_path: Path) -> None:
        """The glob must not reach the model, snippets or analytics files."""
        _seed_logs(tmp_path)
        (tmp_path / "snippets.json").write_text("{}", encoding="utf-8")
        (tmp_path / "analytics.json").write_text("{}", encoding="utf-8")

        _purge_pre_fix_logs(tmp_path)

        assert (tmp_path / "snippets.json").exists()
        assert (tmp_path / "analytics.json").exists()

    def test_unreadable_config_dir_does_not_raise(self, tmp_path: Path) -> None:
        """Logging setup must never be the reason the keyboard fails to start."""
        missing = tmp_path / "does-not-exist"

        removed = _purge_pre_fix_logs(missing)

        assert removed == 0


class TestTheLogGoesWhereTheUIPointsUsers:
    """*Settings -> Data & Privacy -> Diagnostics* shows a path.

    It comes from ``platform.get_log_path()``, while the handler is
    opened by ``_configure_logging``.  A panel pointing at a file
    nothing writes is worse than no panel, so this pins the two
    together rather than asserting either one on its own.
    """

    @pytest.fixture
    def _restore_root_logging(self) -> Iterator[None]:
        """``_configure_logging`` replaces the root handlers process-wide.

        The suite shards with xdist, so leaving them swapped would break
        whatever ran next in this worker (caplog included).  The handler
        it opened is also closed here, or Windows will not let tmp_path
        be cleaned up afterwards.
        """
        root = logging.getLogger()
        original = list(root.handlers)
        level = root.level
        try:
            yield
        finally:
            for handler in list(root.handlers):
                root.removeHandler(handler)
                if handler not in original:
                    handler.close()
            for handler in original:
                root.addHandler(handler)
            root.setLevel(level)

    def test_the_handler_writes_the_file_the_panel_names(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _restore_root_logging: None,
    ) -> None:
        import src.platform as platform_mod

        monkeypatch.setattr(platform_mod, "get_config_dir", lambda: tmp_path)
        monkeypatch.setattr(keyboard_app, "get_config_dir", lambda: tmp_path)

        opened = _configure_logging()
        logging.getLogger("KeyboardApp").error("a record")

        assert opened == platform_mod.get_log_path()
        assert opened is not None
        assert "a record" in opened.read_text(encoding="utf-8")


def _boom() -> tuple:
    """Return a real (type, value, traceback) triple for a raised error."""
    try:
        raise ValueError("the-canary-blew-up")
    except ValueError:
        return sys.exc_info()


class TestUncaughtTracebacksReachTheLog:
    """The log exists so a crash in a frozen build leaves a trace.

    Only failures somebody wrapped in ``try`` / ``except`` +
    ``_logger.exception`` ever reached it before: everything else went to
    ``sys.excepthook``, which writes to stderr, and a windowed
    PyInstaller build has no stderr at all (``sys.stderr`` is ``None``).
    The traceback was discarded at the moment it was worth the most, in
    the file users are asked to attach to a bug report.
    """

    @pytest.fixture
    def hooks(self) -> Iterator[MagicMock]:
        """Install the hooks over a recorder, and put the world back.

        The recorder stands in for the interpreter's real hook so the
        chaining assertion has something to check *and* so a deliberately
        raised traceback is not printed across the test output.  The
        install flag is reset both ways: these are process-global, and
        the suite shards with xdist.
        """
        previous_sys = sys.excepthook
        previous_thread = threading.excepthook
        previous_flag = keyboard_app._exception_hooks_installed

        recorder = MagicMock()
        sys.excepthook = recorder
        threading.excepthook = recorder
        keyboard_app._exception_hooks_installed = False
        _install_exception_hooks()
        try:
            yield recorder
        finally:
            sys.excepthook = previous_sys
            threading.excepthook = previous_thread
            keyboard_app._exception_hooks_installed = previous_flag

    def test_an_uncaught_traceback_is_logged(
        self, hooks: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        exc_type, exc, tb = _boom()

        with caplog.at_level(logging.CRITICAL):
            sys.excepthook(exc_type, exc, tb)

        assert "Uncaught exception" in caplog.text
        # The traceback itself, not just the message: a one-line record
        # would name the error without saying where it came from.
        assert "the-canary-blew-up" in caplog.text
        assert "ValueError" in caplog.text
        assert "_boom" in caplog.text

    def test_the_previous_hook_still_runs(self, hooks: MagicMock) -> None:
        """Chaining, not replacing: a dev run keeps its stderr traceback."""
        exc_type, exc, tb = _boom()

        sys.excepthook(exc_type, exc, tb)

        hooks.assert_called_once_with(exc_type, exc, tb)

    def test_ctrl_c_is_not_logged_as_a_crash(
        self, hooks: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Ctrl-C is a user action.  It still chains, it just isn't a fault."""
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            exc_type, exc, tb = sys.exc_info()

        with caplog.at_level(logging.CRITICAL):
            sys.excepthook(exc_type, exc, tb)

        assert caplog.text == ""
        hooks.assert_called_once()

    def test_a_thread_crash_reaches_the_log(
        self, hooks: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``sys.excepthook`` never sees a worker thread's exception.

        The dictation capture, the updater's download worker and Linux's
        AT-SPI listener all run off the main thread, so without the
        threading hook a crash in any of them is silent.
        """
        exc_type, exc, tb = _boom()
        args = threading.ExceptHookArgs((exc_type, exc, tb, threading.current_thread()))

        with caplog.at_level(logging.CRITICAL):
            threading.excepthook(args)

        assert "the-canary-blew-up" in caplog.text
        assert threading.current_thread().name in caplog.text
        hooks.assert_called_once_with(args)

    def test_installing_twice_does_not_double_log(
        self, hooks: MagicMock, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Re-entering main() in one process must not chain hook onto hook."""
        _install_exception_hooks()
        exc_type, exc, tb = _boom()

        with caplog.at_level(logging.CRITICAL):
            sys.excepthook(exc_type, exc, tb)

        assert caplog.text.count("Uncaught exception") == 1
        hooks.assert_called_once()


class TestTheCompositionRootIsTypeChecked:
    """``src/keyboard_app.py`` is the composition root: logging setup, the
    singleton lock, the tray, the exception hooks, and ``main()`` itself.

    It used to carry a blanket mypy ``ignore_errors`` override alongside the
    two ctypes-heavy platform backends, justified by its own ctypes-heavy
    Windows/macOS window-styling code. That code has since moved to
    ``src/platform/windows_window.py`` and ``src/platform/macos_window.py``
    (see ``tests/test_windows_window.py`` / ``tests/test_macos_window.py``),
    which means the composition root has no ctypes left in it and no reason
    to be exempt. While it was exempt, its ordering constraints -- logging
    before the exception hooks, the singleton check before expensive setup,
    the taskbar identity before the first window -- were enforced only by
    comments, never by the type checker. This asserts the exemption stays
    gone rather than quietly creeping back the next time someone adds a
    ctypes call to `main()` and reaches for the easy fix.
    """

    def test_keyboard_app_is_not_in_the_ignore_errors_override(self) -> None:
        # tomllib landed in 3.11; the project floor is 3.10 (see pyproject's
        # own [tool.mypy] comment), so a 3.10 test run skips rather than
        # reaching for a TOML dependency this project doesn't otherwise need.
        tomllib = pytest.importorskip("tomllib")

        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        overrides = data.get("tool", {}).get("mypy", {}).get("overrides", [])
        for override in overrides:
            if not override.get("ignore_errors"):
                continue
            modules = override.get("module", [])
            if isinstance(modules, str):
                modules = [modules]
            assert "src.keyboard_app" not in modules, (
                "src.keyboard_app is back in a mypy ignore_errors override; "
                "the composition root (logging, the singleton lock, the tray, "
                "the exception hooks, main()) is meant to be type-checked "
                "like the rest of the file tree now that its ctypes code "
                "lives in src/platform/windows_window.py and macos_window.py"
            )
