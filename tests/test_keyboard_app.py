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
    _LOG_PURGE_SENTINEL_2,
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


class TestTheSecondLogPurge:
    """Generation 2 of the purge: the synthesizers' own leaks.

    Releases up to 1.4.1 logged the whole ``xdotool type`` command at
    ERROR when the tool stalled or failed (Linux), and a chorded key with
    no keycode by its character at WARNING (macOS).  An install on either
    platform that already ran the first purge can hold those records and
    owes a second purge.  The Windows synthesizer had no such site, and a
    purge costs the user their diagnostics, so Windows is not charged.
    Each purging case is paired with the platform that must be left alone.
    """

    @pytest.mark.parametrize("platform", ["linux", "macos"])
    def test_an_install_that_ran_the_first_purge_purges_again_once(
        self, tmp_path: Path, platform: str
    ) -> None:
        _seed_logs(tmp_path)
        (tmp_path / _LOG_PURGE_SENTINEL).write_text("generation 1", encoding="utf-8")

        removed = _purge_pre_fix_logs(tmp_path, platform=platform)

        assert removed == 4
        assert (tmp_path / _LOG_PURGE_SENTINEL_2).is_file()

        # Logs written by the fixed build are safe and must survive.
        (tmp_path / "alpha-osk.log").write_text("post-fix", encoding="utf-8")
        assert _purge_pre_fix_logs(tmp_path, platform=platform) == 0
        assert (tmp_path / "alpha-osk.log").exists()

    def test_a_macos_log_holding_the_old_chord_warning_is_removed(self, tmp_path: Path) -> None:
        """The record generation 2 exists for on macOS, as the base commit wrote it."""
        (tmp_path / _LOG_PURGE_SENTINEL).write_text("generation 1", encoding="utf-8")
        (tmp_path / "alpha-osk.log").write_text(
            "2026-09-01 10:00:00 [MacOSKeySynthesizer] WARNING: "
            "No keycode for 'é' and modifiers ['ctrl']\n",
            encoding="utf-8",
        )
        (tmp_path / "alpha-osk.log.1").write_text("rotation 1", encoding="utf-8")

        removed = _purge_pre_fix_logs(tmp_path, platform="macos")

        assert removed == 2
        assert sorted(tmp_path.glob("alpha-osk.log*")) == []
        assert (tmp_path / _LOG_PURGE_SENTINEL_2).is_file()

    def test_a_windows_install_that_ran_the_first_purge_keeps_its_logs(
        self, tmp_path: Path
    ) -> None:
        """Its synthesizer had no typed-content site, so a purge costs diagnostics for nothing."""
        _seed_logs(tmp_path)
        (tmp_path / _LOG_PURGE_SENTINEL).write_text("generation 1", encoding="utf-8")

        removed = _purge_pre_fix_logs(tmp_path, platform="windows")

        assert removed == 0
        assert len(list(tmp_path.glob("alpha-osk.log*"))) == 4
        assert not (tmp_path / _LOG_PURGE_SENTINEL_2).exists()

    @pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
    def test_an_install_that_never_purged_still_purges_everywhere(
        self, tmp_path: Path, platform: str
    ) -> None:
        """Generation 1 applied to every platform, and a generation is never retired."""
        _seed_logs(tmp_path)

        removed = _purge_pre_fix_logs(tmp_path, platform=platform)

        assert removed == 4
        assert (tmp_path / _LOG_PURGE_SENTINEL).is_file()

    @pytest.mark.parametrize("platform", ["linux", "macos"])
    def test_a_fresh_install_marks_both_generations_done(
        self, tmp_path: Path, platform: str
    ) -> None:
        removed = _purge_pre_fix_logs(tmp_path, platform=platform)

        assert removed == 0
        assert (tmp_path / _LOG_PURGE_SENTINEL).is_file()
        assert (tmp_path / _LOG_PURGE_SENTINEL_2).is_file()

    def test_the_default_platform_is_the_running_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_configure_logging`` passes no platform, so the default has to be right."""
        monkeypatch.setattr(keyboard_app, "CURRENT_PLATFORM", "linux")
        (tmp_path / _LOG_PURGE_SENTINEL).write_text("generation 1", encoding="utf-8")
        _seed_logs(tmp_path)

        assert _purge_pre_fix_logs(tmp_path) == 4

        monkeypatch.setattr(keyboard_app, "CURRENT_PLATFORM", "windows")
        other = tmp_path / "windows"
        other.mkdir()
        (other / _LOG_PURGE_SENTINEL).write_text("generation 1", encoding="utf-8")
        _seed_logs(other)

        assert _purge_pre_fix_logs(other) == 0


@pytest.fixture
def _restore_root_logging() -> Iterator[None]:
    """``_configure_logging`` replaces the root handlers process-wide.

    The suite shards with xdist, so leaving them swapped would break
    whatever ran next in this worker (caplog included).  The handler it
    opened is also closed here, or Windows will not let tmp_path be
    cleaned up afterwards.  Shared by every class in this file that runs
    the real logging setup.
    """
    root = logging.getLogger()
    original = list(root.handlers)
    level = root.level
    named = logging.getLogger("HybridPredictor")
    named_level = named.level
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
        named.setLevel(named_level)


class TestTheLogGoesWhereTheUIPointsUsers:
    """*Settings -> Data & Privacy -> Diagnostics* shows a path.

    It comes from ``platform.get_log_path()``, while the handler is
    opened by ``_configure_logging``.  A panel pointing at a file
    nothing writes is worse than no panel, so this pins the two
    together rather than asserting either one on its own.
    """

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


class TestTypedContentNeverReachesTheLog:
    """The prediction path logs its candidate words at DEBUG.

    That is allowed: DEBUG is the sanctioned home for typed content
    precisely because it is off in an ordinary session.  What is not
    allowed is pinning that logger to DEBUG at startup, which ``main()``
    used to do unconditionally -- the handlers ``_configure_logging``
    installs carry no level of their own, so every pill row the engine
    produced was written into the file users attach to bug reports.

    Two tests, because neither alone is honest.  The first states the
    property end to end; the second is what actually catches the line
    coming back, since the property test cannot run ``main()``.
    """

    def test_a_prediction_debug_record_does_not_reach_the_log_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        _restore_root_logging: None,
    ) -> None:
        """A default session must not write engine DEBUG records to disk."""
        import src.platform as platform_mod

        monkeypatch.setattr(platform_mod, "get_config_dir", lambda: tmp_path)
        monkeypatch.setattr(keyboard_app, "get_config_dir", lambda: tmp_path)

        opened = _configure_logging()
        assert opened is not None

        # Exactly what HybridPredictor emits on every keystroke.
        logging.getLogger("HybridPredictor").debug("MERGED result: %s", ["hunter2", "passphrase"])
        # An INFO record on the same logger proves the file is live, so a
        # test that passed because nothing was written at all would fail.
        logging.getLogger("HybridPredictor").info("engine ready")

        written = opened.read_text(encoding="utf-8")
        assert "engine ready" in written
        assert "hunter2" not in written
        assert "MERGED result" not in written

    def test_main_pins_no_logger_to_debug(self) -> None:
        """Source-level guard, deliberately.

        ``main()`` builds a QApplication and enters an event loop, so the
        property test above cannot execute it, and the defect lived in a
        single statement there.  Asserting on the source is brittle in
        the usual way but it is the only thing that fails if the line is
        restored, which is the whole point of the guard.
        """
        source = Path(keyboard_app.__file__).read_text(encoding="utf-8")
        body = source.split("def main(", 1)[1]
        assert "setLevel(logging.DEBUG)" not in body


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


class TestTheWindowIdDoesNotDependOnTheApplicationClass:
    """An external switch scanner finds the keyboard window by its UI
    Automation AutomationId, which Qt builds from the window's objectName
    prefixed with the application object's name, or with the application's
    C++ class name when it has none. Unnamed, the id was
    ``QApplication.alphaOskKeyboard`` in the shipped keyboard and
    ``QGuiApplication.alphaOskKeyboard`` in the test harness, and the
    published contract named the second (docs/architecture/UIA_TARGETS.md)."""

    def test_the_application_is_given_the_contract_name(self) -> None:
        app = MagicMock()
        keyboard_app._name_for_ui_automation(app)
        app.setObjectName.assert_called_once_with("alphaOsk")

    def test_the_name_is_the_one_the_contract_publishes(self) -> None:
        doc = Path(keyboard_app.__file__).resolve().parent.parent / "docs" / "architecture"
        text = (doc / "UIA_TARGETS.md").read_text(encoding="utf-8")
        assert f"{keyboard_app.UIA_APPLICATION_NAME}.alphaOskKeyboard" in text

    def test_main_names_the_application_it_builds(self) -> None:
        """Source-level, for the reason ``test_main_pins_no_logger_to_debug``
        gives: ``main()`` enters an event loop and cannot be executed here."""
        source = Path(keyboard_app.__file__).read_text(encoding="utf-8")
        body = source.split("def main(", 1)[1]
        built = body.index("app = QApplication(")
        assert "_name_for_ui_automation(app)" in body[built : built + 200]
