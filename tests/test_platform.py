"""Tests for the platform abstraction layer."""

from __future__ import annotations

import logging
import ntpath
import subprocess
import sys
from pathlib import Path

import pytest

from src.platform import CURRENT_PLATFORM, get_config_dir, get_model_dir
from src.platform.base import KeySynthesizerBase


def _flat(units):
    """Every event of an injector burst (units of groups), in order."""
    return [event for unit in units for group in unit for event in group]


class TestPlatformDetection:
    """Platform identification."""

    def test_current_platform_is_string(self):
        assert isinstance(CURRENT_PLATFORM, str)

    def test_current_platform_is_valid(self):
        assert CURRENT_PLATFORM in ("windows", "linux", "macos", "unsupported")

    def test_current_platform_matches_sys(self):
        if sys.platform == "win32":
            assert CURRENT_PLATFORM == "windows"
        elif sys.platform.startswith("linux"):
            assert CURRENT_PLATFORM == "linux"
        elif sys.platform == "darwin":
            assert CURRENT_PLATFORM == "macos"


class TestFactory:
    """create_key_synthesizer factory."""

    def test_factory_returns_synthesizer(self):
        from src.platform import create_key_synthesizer

        synth = create_key_synthesizer()
        assert isinstance(synth, KeySynthesizerBase)

    def test_factory_returns_correct_backend(self):
        from src.platform import create_key_synthesizer

        synth = create_key_synthesizer()
        if CURRENT_PLATFORM == "windows":
            from src.platform.windows import WindowsKeySynthesizer

            assert isinstance(synth, WindowsKeySynthesizer)
        elif CURRENT_PLATFORM == "linux":
            from src.platform.linux import LinuxKeySynthesizer

            assert isinstance(synth, LinuxKeySynthesizer)
        elif CURRENT_PLATFORM == "macos":
            from src.platform.macos import MacOSKeySynthesizer

            assert isinstance(synth, MacOSKeySynthesizer)

    def test_synthesizer_has_backend_name(self):
        from src.platform import create_key_synthesizer

        synth = create_key_synthesizer()
        name = synth.backend_name()
        assert isinstance(name, str)
        assert len(name) > 0

    def test_synthesizer_reports_availability(self):
        from src.platform import create_key_synthesizer

        synth = create_key_synthesizer()
        # Just verify it returns bool, not that it's True/False
        assert isinstance(synth.is_available(), bool)


class TestConfigPaths:
    """Configuration directory helpers.

    This class exists to test ``get_config_dir`` / ``get_model_dir``
    themselves, so it opts out of ``tests/conftest.py``'s autouse
    ``_stay_off_the_real_config_dir`` fixture by restoring the real
    function for the duration of every test here: the module-level
    import above (``from src.platform import ... get_config_dir,
    get_model_dir``) captured the pristine function object before that
    fixture ever runs, so re-assigning it back onto ``src.platform`` is
    just handing the module its own original attribute again, not
    reaching into another test's private state.  Without this,
    ``get_model_dir()`` (which looks up ``get_config_dir()`` unqualified
    from inside ``src/platform``, so it always resolves through whatever
    the module attribute currently is) would return a path under the
    fake directory while a direct ``get_config_dir()`` call in the same
    test -- bound straight to the original object -- returns the real
    one, and the two stop agreeing on which directory nests inside which.
    """

    @pytest.fixture(autouse=True)
    def _use_the_real_config_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.platform.get_config_dir", get_config_dir)

    def test_get_config_dir_returns_path(self):
        result = get_config_dir()
        assert isinstance(result, Path)

    def test_get_config_dir_exists(self):
        result = get_config_dir()
        assert result.exists()

    def test_get_config_dir_correct_platform(self):
        result = get_config_dir()
        if CURRENT_PLATFORM == "windows":
            assert "alpha-osk" in str(result)
        elif CURRENT_PLATFORM == "linux":
            assert ".config/alpha-osk" in str(result)
        elif CURRENT_PLATFORM == "macos":
            assert "Library/Application Support/alpha-osk" in str(result)

    def test_get_model_dir_returns_path(self):
        result = get_model_dir()
        assert isinstance(result, Path)

    def test_get_model_dir_is_under_config(self):
        config = get_config_dir()
        model = get_model_dir()
        assert str(model).startswith(str(config))

    def test_get_model_dir_exists(self):
        result = get_model_dir()
        assert result.exists()


class TestBaseSynthesizerInterface:
    """Verify the ABC contract."""

    def test_cannot_instantiate_base(self):
        with pytest.raises(TypeError):
            KeySynthesizerBase()

    def test_base_has_required_methods(self):
        methods = ["is_available", "backend_name", "send_key", "send_text", "send_combination"]
        for method in methods:
            assert hasattr(KeySynthesizerBase, method)


class TestLinuxReplaceText:
    """LinuxKeySynthesizer.replace_text() — atomic select-and-replace.

    These tests stub the subprocess runner and a synthesizer tool so they
    run on any OS (including the Windows CI lane, where xdotool is absent).
    """

    def _make_synth(self, tool: str, monkeypatch):
        from src.platform import linux as linux_mod

        calls: list[list[str]] = []
        monkeypatch.setattr(linux_mod, "_run", lambda cmd: calls.append(cmd))
        synth = linux_mod.LinuxKeySynthesizer.__new__(linux_mod.LinuxKeySynthesizer)
        synth._tool = tool
        return synth, calls

    def test_xdotool_chain_is_single_invocation(self, monkeypatch):
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.replace_text(3, "hello")
        # One `key` invocation carrying all 3 chords, plus one `type`.
        assert calls[0] == [
            "xdotool",
            "key",
            "--clearmodifiers",
            "shift+Left",
            "shift+Left",
            "shift+Left",
        ]
        assert calls[1] == ["xdotool", "type", "--clearmodifiers", "--", "hello"]
        assert len(calls) == 2

    def test_xdotool_zero_backspace_skips_selection(self, monkeypatch):
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.replace_text(0, "hi")
        # No shift+Left chain; falls through to send_text.
        assert calls == [["xdotool", "type", "--clearmodifiers", "--", "hi"]]

    def test_xdotool_empty_text_still_selects(self, monkeypatch):
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.replace_text(2, "")
        assert calls == [
            [
                "xdotool",
                "key",
                "--clearmodifiers",
                "shift+Left",
                "shift+Left",
            ]
        ]

    def test_ydotool_frames_shift_around_lefts(self, monkeypatch):
        synth, calls = self._make_synth("ydotool", monkeypatch)
        synth.replace_text(2, "hi")
        assert calls == [
            ["ydotool", "key", "--key-down", "shift"],
            ["ydotool", "key", "Left"],
            ["ydotool", "key", "Left"],
            ["ydotool", "key", "--key-up", "shift"],
            ["ydotool", "type", "--", "hi"],
        ]

    def test_no_tool_is_silent_noop(self, monkeypatch):
        synth, calls = self._make_synth(None, monkeypatch)
        synth.replace_text(3, "x")
        assert calls == []


class TestLinuxSendKeyPunctuationChord:
    """Modifier+punctuation chords on Linux must rewrite the literal
    char to its X11 keysym name — xdotool's chord parser uses ``+`` as
    the separator, so ``ctrl+-`` is malformed and the canonical form
    ``ctrl+minus`` is what triggers the app's shortcut handler.
    """

    def _make_synth(self, tool: str, monkeypatch):
        from src.platform import linux as linux_mod

        calls: list[list[str]] = []
        monkeypatch.setattr(linux_mod, "_run", lambda cmd: calls.append(cmd))
        synth = linux_mod.LinuxKeySynthesizer.__new__(linux_mod.LinuxKeySynthesizer)
        synth._tool = tool
        return synth, calls

    def test_xdotool_ctrl_minus_uses_keysym(self, monkeypatch):
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.send_key("-", modifiers=["ctrl"])
        assert calls == [["xdotool", "key", "--clearmodifiers", "ctrl+minus"]]

    def test_xdotool_ctrl_equals_uses_keysym(self, monkeypatch):
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.send_key("=", modifiers=["ctrl"])
        assert calls == [["xdotool", "key", "--clearmodifiers", "ctrl+equal"]]

    def test_xdotool_ctrl_slash_uses_keysym(self, monkeypatch):
        # VS Code / many editors: comment toggle.
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.send_key("/", modifiers=["ctrl"])
        assert calls == [["xdotool", "key", "--clearmodifiers", "ctrl+slash"]]

    def test_xdotool_letter_passes_through(self, monkeypatch):
        # Letters need no remap — xdotool accepts ``a`` verbatim.
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.send_key("a", modifiers=["ctrl"])
        assert calls == [["xdotool", "key", "--clearmodifiers", "ctrl+a"]]

    def test_xdotool_digit_passes_through(self, monkeypatch):
        # Digits need no remap either.
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.send_key("1", modifiers=["ctrl"])
        assert calls == [["xdotool", "key", "--clearmodifiers", "ctrl+1"]]

    def test_xdotool_no_modifiers_still_remaps(self, monkeypatch):
        # ``xdotool key -`` is also ambiguous — the dash looks like a
        # flag.  Always remap to the keysym name regardless of chord.
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.send_key("-")
        assert calls == [["xdotool", "key", "--clearmodifiers", "minus"]]

    def test_ydotool_ctrl_minus_remaps(self, monkeypatch):
        # ydotool would see ``-`` as a CLI flag start; keysym name
        # avoids that and is also closer to a real key name.
        synth, calls = self._make_synth("ydotool", monkeypatch)
        synth.send_key("-", modifiers=["ctrl"])
        assert calls == [["ydotool", "key", "minus"]]


class TestLinuxSuperNeverHeld:
    """Super/Meta must never be *held* on Linux.

    Regression: tapping Win held Super down (``xdotool keydown super``),
    and while Super is held the window manager grabs the pointer for
    window move/resize — every click (including on the OSK's own keys)
    became a WM gesture and the user got stuck, unable to tap Win again
    to release it. The hold is skipped; Super+<key> combos still fire as
    an atomic chord through ``send_key``.
    """

    def _make_synth(self, tool, monkeypatch):
        from src.platform import linux as linux_mod

        calls: list[list[str]] = []
        monkeypatch.setattr(linux_mod, "_run", lambda cmd: calls.append(cmd))
        synth = linux_mod.LinuxKeySynthesizer.__new__(linux_mod.LinuxKeySynthesizer)
        synth._tool = tool
        return synth, calls

    def test_hold_win_is_noop_xdotool(self, monkeypatch):
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.hold_modifier("win")
        assert calls == []  # No keydown super — the trap is never armed.

    def test_hold_super_is_noop_xdotool(self, monkeypatch):
        # Accept the raw keysym too, in case a caller passes "super".
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.hold_modifier("super")
        assert calls == []

    def test_hold_win_is_noop_ydotool(self, monkeypatch):
        synth, calls = self._make_synth("ydotool", monkeypatch)
        synth.hold_modifier("win")
        assert calls == []

    def test_hold_ctrl_still_holds(self, monkeypatch):
        # Only Super is special — Ctrl/Alt/Shift still hold so Ctrl+click
        # / Shift+drag selection in the target app keep working.
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.hold_modifier("ctrl")
        assert calls == [["xdotool", "keydown", "ctrl"]]

    def test_win_combo_still_chords(self, monkeypatch):
        # Win+D etc. must still work — emitted as a self-contained chord
        # that presses and releases Super atomically, never leaving it held.
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.send_key("d", modifiers=["win"])
        assert calls == [["xdotool", "key", "--clearmodifiers", "super+d"]]

    def test_release_win_still_issues_defensive_keyup(self, monkeypatch):
        # release stays functional — a keyup when Super isn't down is a
        # harmless no-op and clears any externally-stuck Super.
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.release_modifier("win")
        assert calls == [["xdotool", "keyup", "super"]]


class TestWindowsWinNeverHeld:
    """The Windows key must never be *held* on Windows either.

    Regression: tapping Win held ``VK_LWIN`` down, so the next click
    anywhere was Win+click, which Windows 11 sends to Click to Do: the
    screen froze for a moment while it captured it and a glow ringed the
    display.  Win+<key> chords still fire, wrapped by ``send_key``.
    """

    def _make_synth(self):
        from src.platform import windows as win_mod

        synth = win_mod.WindowsKeySynthesizer.__new__(win_mod.WindowsKeySynthesizer)
        captured: list = []
        synth._inject = lambda units, **kw: captured.append(_flat(units))
        synth._make_vk_scancode_event = lambda vk, key_down: ("vk", vk, key_down)
        synth._make_unicode_events = lambda c: [("uni", c)]
        synth._log_send = lambda msg: None

        class _StubUser32:
            def VkKeyScanW(self_inner, ch):
                return ord(ch.upper())

            def GetAsyncKeyState(self_inner, vk):
                return 0

        synth._user32 = _StubUser32()
        return synth, captured

    def test_hold_win_sends_nothing(self):
        synth, captured = self._make_synth()
        synth.hold_modifier("win")
        synth.hold_modifier("super")
        assert captured == []

    def test_release_win_sends_nothing(self):
        # A stray key-up could cut short a Win the user holds on a real
        # keyboard, and there is no hold of ours to undo.
        synth, captured = self._make_synth()
        synth.release_modifier("win")
        assert captured == []

    def test_hold_ctrl_still_holds(self):
        from src.platform.windows import VK_CONTROL

        synth, captured = self._make_synth()
        synth.hold_modifier("ctrl")
        assert captured == [[("vk", VK_CONTROL, True)]]

    def test_win_combo_still_chords(self):
        from src.platform.windows import VK_LWIN

        synth, captured = self._make_synth()
        synth.send_key("d", modifiers=["win"])
        assert captured == [
            [
                ("vk", VK_LWIN, True),
                ("vk", ord("D"), True),
                ("vk", ord("D"), False),
                ("vk", VK_LWIN, False),
            ]
        ]


class TestLinuxSendText:
    """``send_text()`` must end-of-options guard the typed text.

    Regression: ``xdotool type --clearmodifiers <text>`` and
    ``ydotool type <text>`` pass user-controlled text (typed words,
    inserted snippets) as the final argv token with
    nothing forcing positional interpretation. A typed string that
    exactly matches a recognised flag (``--help``, ``--window``) would
    be parsed as an option and silently dropped instead of typed: a
    correctness bug on an accessibility tool, where a keystroke that
    does nothing is a real problem. A literal ``"--"`` immediately
    before the text argument forces both tools to treat it as data.
    """

    def _make_synth(self, tool, monkeypatch):
        from src.platform import linux as linux_mod

        calls: list[list[str]] = []
        monkeypatch.setattr(linux_mod, "_run", lambda cmd: calls.append(cmd))
        synth = linux_mod.LinuxKeySynthesizer.__new__(linux_mod.LinuxKeySynthesizer)
        synth._tool = tool
        return synth, calls

    def test_xdotool_type_has_end_of_options_guard(self, monkeypatch):
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.send_text("hello")
        assert calls == [["xdotool", "type", "--clearmodifiers", "--", "hello"]]

    def test_ydotool_type_has_end_of_options_guard(self, monkeypatch):
        synth, calls = self._make_synth("ydotool", monkeypatch)
        synth.send_text("hello")
        assert calls == [["ydotool", "type", "--", "hello"]]

    def test_xdotool_flag_like_text_is_typed_as_data(self, monkeypatch):
        # The motivating case: a snippet or typed word that happens to
        # equal a real xdotool flag token must still reach `type` as data.
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.send_text("--help")
        assert calls == [["xdotool", "type", "--clearmodifiers", "--", "--help"]]

    def test_ydotool_flag_like_text_is_typed_as_data(self, monkeypatch):
        synth, calls = self._make_synth("ydotool", monkeypatch)
        synth.send_text("--window")
        assert calls == [["ydotool", "type", "--", "--window"]]

    def test_no_tool_is_silent_noop(self, monkeypatch):
        synth, calls = self._make_synth(None, monkeypatch)
        synth.send_text("x")
        assert calls == []


class TestLinuxRunTimeout:
    """``_run()`` must bound every subprocess call.

    Regression: ``_run`` called ``subprocess.run(...)`` with no
    ``timeout=``, so a hung xdotool/ydotool (dead X server, wedged
    binary) would freeze the whole on-screen keyboard indefinitely -
    ``_run`` executes synchronously on Qt's UI thread for every
    keystroke. These tests stub ``subprocess.run`` itself (not the
    ``_run`` wrapper) so they can assert on the arguments ``_run``
    passes down, and on what happens when the call times out.
    """

    def test_run_passes_a_bounded_timeout(self, monkeypatch):
        from src.platform import linux as linux_mod

        captured: dict = {}

        def fake_run(cmd, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(linux_mod.subprocess, "run", fake_run)
        linux_mod._run(["xdotool", "key", "a"])
        assert captured.get("timeout") == linux_mod._SUBPROCESS_TIMEOUT_S
        assert 0 < captured["timeout"] < 30  # generous, but not "forever"

    def test_timeout_expired_is_swallowed_not_raised(self, monkeypatch):
        from src.platform import linux as linux_mod

        def fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 0))

        monkeypatch.setattr(linux_mod.subprocess, "run", fake_run)
        # Must not raise - a wedged backend should degrade to a dropped
        # keystroke, never an unhandled exception on the UI thread.
        linux_mod._run(["xdotool", "key", "a"])

    def test_other_subprocess_failure_still_swallowed(self, monkeypatch):
        from src.platform import linux as linux_mod

        def fake_run(cmd, **kwargs):
            raise OSError("xdotool binary vanished")

        monkeypatch.setattr(linux_mod.subprocess, "run", fake_run)
        linux_mod._run(["xdotool", "key", "a"])  # must not raise


class TestLinuxDiagnosticsNeverCarryTypedText:
    """No record at INFO or above from the Linux synthesizer may hold typed text.

    Regression: ``_run`` logged the whole command at ERROR on a timeout
    or any other failure, and ``send_text`` builds that command as
    ``xdotool type --clearmodifiers -- <text>``, so on a host where
    xdotool stalled ``alpha-osk.log`` recorded the characters typed,
    password fields included (privacy mode deliberately still types, so
    it cannot gate this layer).  ``send_key`` also logged the key name at
    WARNING when no tool was installed, and the bridge's chord path hands
    it the typed letter.  ``alpha-osk.log`` is the file users attach to
    bug reports.

    Every negative case is paired with the positive half: a record *is*
    still emitted and still names the tool and subcommand, so a fix that
    simply deleted the logging would fail here too.
    """

    SECRET = "hunter2-Secr3t"

    @staticmethod
    def _make_synth(tool):
        from src.platform import linux as linux_mod

        synth = linux_mod.LinuxKeySynthesizer.__new__(linux_mod.LinuxKeySynthesizer)
        synth._tool = tool
        return synth

    @staticmethod
    def _failing_run(monkeypatch, exc_factory):
        from src.platform import linux as linux_mod

        def fake_run(cmd, **kwargs):
            raise exc_factory(cmd, kwargs)

        monkeypatch.setattr(linux_mod.subprocess, "run", fake_run)

    @staticmethod
    def _loud(caplog):
        return [r for r in caplog.records if r.levelno >= logging.INFO]

    def test_a_timed_out_type_command_logs_no_text(self, monkeypatch, caplog):
        self._failing_run(
            monkeypatch,
            lambda cmd, kw: subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 0)),
        )
        synth = self._make_synth("xdotool")

        with caplog.at_level(logging.INFO, logger="LinuxKeySynthesizer"):
            synth.send_text(self.SECRET)

        loud = self._loud(caplog)
        assert [r.levelno for r in loud] == [logging.ERROR]
        message = loud[0].getMessage()
        assert self.SECRET not in message
        assert "xdotool type" in message
        assert "timed out" in message

    def test_a_failed_command_logs_neither_the_text_nor_the_exception_message(
        self, monkeypatch, caplog
    ):
        # The exception's own message quotes the command, as TimeoutExpired's
        # does for real, so str(exc) is as unsafe as cmd itself.
        self._failing_run(monkeypatch, lambda cmd, kw: OSError(2, f"cannot exec {cmd!r}"))
        synth = self._make_synth("xdotool")

        with caplog.at_level(logging.INFO, logger="LinuxKeySynthesizer"):
            synth.send_text(self.SECRET)

        loud = self._loud(caplog)
        assert [r.levelno for r in loud] == [logging.ERROR]
        message = loud[0].getMessage()
        assert self.SECRET not in message
        assert "xdotool type" in message
        # What a bug report needs survives: the class and the errno.
        assert "FileNotFoundError" in message
        assert "errno 2" in message

    def test_a_replace_text_timeout_on_ydotool_logs_no_text(self, monkeypatch, caplog):
        # replace_text is the other path that puts typed text on a command
        # line, and ydotool is the other tool name the record must carry.
        self._failing_run(
            monkeypatch,
            lambda cmd, kw: subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 0)),
        )
        synth = self._make_synth("ydotool")

        with caplog.at_level(logging.INFO, logger="LinuxKeySynthesizer"):
            synth.replace_text(3, self.SECRET)

        loud = self._loud(caplog)
        assert loud, "a stalled backend must still leave a record"
        assert all(self.SECRET not in r.getMessage() for r in loud)
        assert any(r.getMessage().startswith("ydotool ") for r in loud)

    def test_a_missing_tool_logs_no_key_name(self, monkeypatch, caplog):
        from src.platform import linux as linux_mod

        calls: list[list[str]] = []
        monkeypatch.setattr(linux_mod, "_run", lambda cmd: calls.append(cmd))
        synth = self._make_synth(None)

        with caplog.at_level(logging.INFO, logger="LinuxKeySynthesizer"):
            synth.send_key(self.SECRET, modifiers=["ctrl"])

        loud = self._loud(caplog)
        assert [r.levelno for r in loud] == [logging.WARNING]
        assert self.SECRET not in loud[0].getMessage()
        assert calls == []

    def test_an_empty_command_is_described_without_indexing_it(self, monkeypatch, caplog):
        from src.platform import linux as linux_mod

        self._failing_run(monkeypatch, lambda cmd, kw: ValueError("no command"))

        with caplog.at_level(logging.INFO, logger="LinuxKeySynthesizer"):
            linux_mod._run([])  # must not raise

        loud = self._loud(caplog)
        assert [r.levelno for r in loud] == [logging.ERROR]
        assert "ValueError" in loud[0].getMessage()


class TestMacOSDiagnosticsNeverCarryTypedText:
    """The macOS synthesizer's one loud site that sees a key name names no key.

    ``send_key`` warns when a chorded key has no keycode.  The bridge's
    chord path hands it the typed letter, so the record carries the key's
    length and the modifiers, never the key itself.  Paired with the
    positive half: the warning is still emitted and nothing is posted.
    """

    SECRET = "hunter2-Secr3t"

    def test_a_chord_with_no_keycode_logs_no_key_name(self, monkeypatch, caplog):
        from src.platform import macos as macos_mod

        synth = macos_mod.MacOSKeySynthesizer.__new__(macos_mod.MacOSKeySynthesizer)
        synth._available = True
        monkeypatch.setattr(synth, "_resolve_keycode", lambda name: None)
        posted: list = []
        monkeypatch.setattr(synth, "_post_keycode", lambda *args: posted.append(args))

        with caplog.at_level(logging.INFO):
            synth.send_key(self.SECRET, modifiers=["ctrl"])

        loud = [r for r in caplog.records if r.levelno >= logging.INFO]
        assert [r.levelno for r in loud] == [logging.WARNING]
        message = loud[0].getMessage()
        assert self.SECRET not in message
        assert "ctrl" in message
        assert posted == []


class TestWindowsReplaceText:
    """WindowsKeySynthesizer.replace_text() — terminal-aware select-and-replace.

    Bypasses ``__init__`` (which loads user32.dll) and stubs the event
    builders so the tests can assert the dispatch behavior on any OS,
    including the Linux CI lane.
    """

    def _make_synth(self, foreground_class: str):
        from src.platform import windows as win_mod

        synth = win_mod.WindowsKeySynthesizer.__new__(win_mod.WindowsKeySynthesizer)
        captured: list = []
        synth._inject = lambda units, **kw: captured.append(_flat(units))
        synth._make_key_event = lambda vk, key_down: ("vk", vk, key_down)
        synth._make_unicode_events = lambda c: [("uni", c)]
        # Force the per-char dispatch in replace_text to take the
        # UNICODE branch so this suite can keep asserting the
        # branching behaviour of replace_text itself, independent of
        # the scancode-resolution logic which has its own coverage in
        # TestWindowsScancodeDispatch.
        synth._make_char_scancode_events = lambda c: None
        # replace_text now probes whether Shift is already held (to avoid
        # releasing a sticky/locked Shift). Default to not-held so the
        # existing Shift-wrap assertions stand; overridden per-test below.
        synth._modifier_already_held = lambda mod: False
        # Bypass GetForegroundWindow / GetClassNameW directly. Mocking
        # them via ctypes is brittle off-Windows.
        synth._get_foreground_window_class = lambda: foreground_class
        return synth, captured

    def test_terminal_uses_backspace_path(self):
        from src.platform.windows import VK_BACK

        synth, captured = self._make_synth("ConsoleWindowClass")
        synth.replace_text(3, "Ow")
        assert captured == [
            [
                ("vk", VK_BACK, True),
                ("vk", VK_BACK, False),
                ("vk", VK_BACK, True),
                ("vk", VK_BACK, False),
                ("vk", VK_BACK, True),
                ("vk", VK_BACK, False),
                ("uni", "O"),
                ("uni", "w"),
            ]
        ]

    def test_windows_terminal_class_also_uses_backspace(self):
        from src.platform.windows import VK_BACK

        synth, captured = self._make_synth("CASCADIA_HOSTING_WINDOW_CLASS")
        synth.replace_text(1, "x")
        assert captured == [
            [
                ("vk", VK_BACK, True),
                ("vk", VK_BACK, False),
                ("uni", "x"),
            ]
        ]

    def test_mintty_class_also_uses_backspace(self):
        from src.platform.windows import VK_BACK

        synth, captured = self._make_synth("mintty")
        synth.replace_text(2, "")
        assert captured == [
            [
                ("vk", VK_BACK, True),
                ("vk", VK_BACK, False),
                ("vk", VK_BACK, True),
                ("vk", VK_BACK, False),
            ]
        ]

    def test_non_terminal_uses_shift_left_path(self):
        from src.platform.windows import VK_LEFT, VK_SHIFT

        synth, captured = self._make_synth("Chrome_WidgetWin_1")
        synth.replace_text(2, "hi")
        # One burst: the selection (one atomic group, see
        # TestWindowsInjectPacing) followed by the paced replacement.
        assert captured == [
            [
                ("vk", VK_SHIFT, True),
                ("vk", VK_LEFT, True),
                ("vk", VK_LEFT, False),
                ("vk", VK_LEFT, True),
                ("vk", VK_LEFT, False),
                ("vk", VK_SHIFT, False),
                ("uni", "h"),
                ("uni", "i"),
            ]
        ]

    def test_held_shift_not_wrapped_in_selection(self):
        # Regression for right-click "lock": when Shift is already held
        # (locked or sticky), replace_text must NOT emit its own Shift
        # press/release — the Left presses select under the standing hold,
        # and a trailing Shift key-up would silently release the locked
        # Shift while the OSK still shows it held.
        from src.platform.windows import VK_LEFT, VK_SHIFT

        synth, captured = self._make_synth("Chrome_WidgetWin_1")
        synth._modifier_already_held = lambda mod: mod == "shift"
        synth.replace_text(2, "hi")
        # No Shift bookends; Left presses ride the standing hold.
        assert captured == [
            [
                ("vk", VK_LEFT, True),
                ("vk", VK_LEFT, False),
                ("vk", VK_LEFT, True),
                ("vk", VK_LEFT, False),
                ("uni", "h"),
                ("uni", "i"),
            ]
        ]
        assert ("vk", VK_SHIFT, True) not in captured[0]
        assert ("vk", VK_SHIFT, False) not in captured[0]

    def test_zero_backspace_in_terminal_just_types(self):
        synth, captured = self._make_synth("ConsoleWindowClass")
        synth.replace_text(0, "abc")
        assert captured == [[("uni", "a"), ("uni", "b"), ("uni", "c")]]

    def test_zero_backspace_outside_terminal_skips_selection(self):
        from src.platform.windows import VK_SHIFT

        synth, captured = self._make_synth("Notepad")
        synth.replace_text(0, "abc")
        # No Shift bookends when there's nothing to select.
        events = captured[0]
        assert all(e[0] == "uni" for e in events)
        assert ("vk", VK_SHIFT, True) not in events

    def test_unknown_class_treated_as_non_terminal(self):
        from src.platform.windows import VK_SHIFT

        synth, captured = self._make_synth("")
        synth.replace_text(1, "x")
        # Empty class name (e.g. GetClassNameW failed) → safe default
        # is the existing Shift+Left path, not BackSpace.
        assert ("vk", VK_SHIFT, True) in captured[0]


class TestWindowsSendKeyPunctuationChord:
    """Modifier+punctuation chords (Ctrl+-, Ctrl+=) must produce a real
    VK keystroke, not a Unicode injection — apps' shortcut handlers
    listen for WM_KEYDOWN(VK_OEM_*), and Unicode events alone don't
    trigger zoom/etc. when a modifier is held.
    """

    def _make_synth(self, vk_scan_results: dict):
        from src.platform import windows as win_mod

        synth = win_mod.WindowsKeySynthesizer.__new__(win_mod.WindowsKeySynthesizer)
        captured: list = []
        synth._inject = lambda units, **kw: captured.append(_flat(units))
        # send_key builds chord events via _make_vk_scancode_event (scancode
        # mode, for remote-desktop relay). Stub it to a tuple so these tests
        # stay focused on VK resolution / shift-prepend / unicode fallback;
        # the scancode-mode flags themselves are covered by
        # TestWindowsChordScancodeMode below.
        synth._make_vk_scancode_event = lambda vk, key_down: ("vk", vk, key_down)
        synth._make_unicode_events = lambda c: [("uni", c)]

        class _StubUser32:
            def VkKeyScanW(self_inner, ch):
                return vk_scan_results.get(ch, -1)

            # send_key now probes whether each modifier is already held
            # (to avoid dropping a sticky/locked hold). No modifier is
            # held in these chord tests, so report all keys up.
            def GetAsyncKeyState(self_inner, vk):
                return 0

        synth._user32 = _StubUser32()
        return synth, captured

    def test_ctrl_minus_uses_vk_oem_minus(self):
        from src.platform.windows import VK_CONTROL

        # US-layout VkKeyScanW('-') = (low=VK_OEM_MINUS=0xBD, high=0)
        synth, captured = self._make_synth({"-": 0x00BD})
        synth.send_key("-", modifiers=["ctrl"])
        # Ctrl-down → VK_OEM_MINUS-down → VK_OEM_MINUS-up → Ctrl-up,
        # all virtual-key events (no Unicode injection).
        assert captured == [
            [
                ("vk", VK_CONTROL, True),
                ("vk", 0xBD, True),
                ("vk", 0xBD, False),
                ("vk", VK_CONTROL, False),
            ]
        ]

    def test_ctrl_equals_uses_vk_oem_plus(self):
        from src.platform.windows import VK_CONTROL

        # US-layout VkKeyScanW('=') = (low=VK_OEM_PLUS=0xBB, high=0)
        synth, captured = self._make_synth({"=": 0x00BB})
        synth.send_key("=", modifiers=["ctrl"])
        assert captured == [
            [
                ("vk", VK_CONTROL, True),
                ("vk", 0xBB, True),
                ("vk", 0xBB, False),
                ("vk", VK_CONTROL, False),
            ]
        ]

    def test_shift_required_char_prepends_shift(self):
        from src.platform.windows import VK_CONTROL, VK_SHIFT

        # US-layout VkKeyScanW('+') = (low=VK_OEM_PLUS=0xBB, high=1) —
        # '+' is Shift+'=' physically, so the synth must add a Shift
        # press around the chord.
        synth, captured = self._make_synth({"+": 0x01BB})
        synth.send_key("+", modifiers=["ctrl"])
        # Shift gets prepended, so order is Shift→Ctrl press, then key,
        # then Ctrl→Shift release.
        assert captured == [
            [
                ("vk", VK_SHIFT, True),
                ("vk", VK_CONTROL, True),
                ("vk", 0xBB, True),
                ("vk", 0xBB, False),
                ("vk", VK_CONTROL, False),
                ("vk", VK_SHIFT, False),
            ]
        ]

    def test_unmappable_char_falls_back_to_unicode(self):
        from src.platform.windows import VK_CONTROL

        # VkKeyScanW returns -1 for chars not on the active layout.
        synth, captured = self._make_synth({})  # everything → -1
        synth.send_key("ñ", modifiers=["ctrl"])
        # Ctrl is still wrapped around the keystroke; the action key
        # falls through to the Unicode path.
        assert captured == [
            [
                ("vk", VK_CONTROL, True),
                ("uni", "ñ"),
                ("vk", VK_CONTROL, False),
            ]
        ]

    def test_already_held_modifier_is_not_wrapped(self):
        # Regression for right-click "lock": when Ctrl is already held at
        # the OS (hold_modifier put it down, and it must STAY down), send_key
        # must NOT wrap the action key with a Ctrl down/up. The trailing
        # Ctrl-up would silently release the lock, breaking a following
        # Ctrl+click / Alt+Tab even though the OSK still shows it held.
        from src.platform.windows import VK_CONTROL

        synth, captured = self._make_synth({"-": 0x00BD})
        # Report Ctrl physically held; everything else up.
        synth._user32.GetAsyncKeyState = lambda vk: 0x8000 if vk == VK_CONTROL else 0
        synth.send_key("-", modifiers=["ctrl"])
        # Only the action key — Ctrl is supplied by the standing hold.
        assert captured == [
            [
                ("vk", 0xBD, True),
                ("vk", 0xBD, False),
            ]
        ]


class TestWindowsScancodeDispatch:
    """``send_text`` and the typed portion of ``replace_text`` route ASCII
    chars through scancode-mode injection so they reach apps that listen
    for ``WM_KEYDOWN`` rather than ``WM_CHAR`` (Blender, VirtualBox,
    DirectInput games). Non-ASCII / dead-key / AltGr chars fall back to
    ``KEYEVENTF_UNICODE`` per-char.

    These tests stub ``_user32`` so they run on the Linux CI lane the
    same as on Windows. The stub mimics the Win32 calls
    ``_resolve_char_scancode`` makes:

    - ``VkKeyScanW(ch)``: returns the SHORT-encoded ``(shift_state << 8) |
      vk``, or -1 if the char has no single-keystroke mapping.
    - ``MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)``: VK → scancode.
    - ``MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR)``: VK → unshifted char with
      bit 31 set when the VK triggers a dead-key composition.
    - ``GetKeyState(VK_CAPITAL)``: bit 0 = Caps Lock LED on.
    - ``GetAsyncKeyState(VK_SHIFT)``: high bit = Shift physically held
      right now.
    """

    # US-layout reference values used across the tests.
    _US = {
        # char → VkKeyScanW return: (shift_state << 8) | vk
        "VK_SCAN": {
            "a": 0x0041,  # VK_A, no shift
            "A": 0x0141,  # VK_A + shift
            "z": 0x005A,
            "1": 0x0031,
            "!": 0x0131,  # Shift+1
            "-": 0x00BD,  # VK_OEM_MINUS
            ".": 0x00BE,
            "@": 0x0232,  # Ctrl required (US-International for some chars; here
            # we use it just to drive the AltGr/Ctrl-required path)
        },
        # vk → scancode
        "VSC": {
            0x10: 0x2A,  # VK_SHIFT → left shift scancode
            0x41: 0x1E,  # A
            0x5A: 0x2C,  # Z
            0x31: 0x02,  # 1
            0xBD: 0x0C,  # -
            0xBE: 0x34,  # .
        },
        # vk → MAPVK_VK_TO_CHAR result. Bit 31 set = dead key.
        "CHAR": {
            0x41: 0x61,
            0x5A: 0x7A,
            0x31: 0x31,
            0xBD: 0x2D,
            0xBE: 0x2E,
        },
    }

    def _make_synth(
        self,
        *,
        vk_scan: dict[str, int] | None = None,
        vsc: dict[int, int] | None = None,
        char_probe: dict[int, int] | None = None,
        caps_on: bool = False,
        shift_held: bool = False,
    ):
        from src.platform import windows as win_mod

        # ``dict(base, **overrides)`` requires string keys; vsc/char_probe
        # use int VK codes, so merge with ``|`` instead.
        vk_scan = {**self._US["VK_SCAN"], **(vk_scan or {})}
        vsc = {**self._US["VSC"], **(vsc or {})}
        char_probe = {**self._US["CHAR"], **(char_probe or {})}

        synth = win_mod.WindowsKeySynthesizer.__new__(win_mod.WindowsKeySynthesizer)
        captured: list = []
        synth._inject = lambda units, **kw: captured.append(_flat(units))

        # Real _make_unicode_events would build INPUT structures; for
        # tests we substitute a marker so the assertion shape is
        # readable. The scancode helper builds real INPUT objects via
        # _make_scancode_event, which we replace with a marker for the
        # same reason.
        synth._make_unicode_events = lambda c: [("uni", c)]
        synth._make_scancode_event = lambda scancode, key_down: ("sc", scancode, key_down)
        # _make_char_scancode_events also calls _make_key_event as a
        # safety-net branch. We don't expect it to fire under any
        # tested input, but stub it so an unexpected call surfaces
        # as a clear marker rather than an AttributeError.
        synth._make_key_event = lambda vk, key_down: ("vk_fallback", vk, key_down)

        class _StubUser32:
            def VkKeyScanW(self_inner, ch):
                return vk_scan.get(ch, -1)

            def MapVirtualKeyW(self_inner, vk, mode):
                if mode == win_mod.MAPVK_VK_TO_VSC:
                    return vsc.get(vk, 0)
                if mode == win_mod.MAPVK_VK_TO_CHAR:
                    return char_probe.get(vk, 0)
                return 0

            def GetKeyState(self_inner, vk):
                return 1 if (vk == win_mod.VK_CAPITAL and caps_on) else 0

            def GetAsyncKeyState(self_inner, vk):
                return 0x8000 if (vk == win_mod.VK_SHIFT and shift_held) else 0

        synth._user32 = _StubUser32()
        return synth, captured

    # ------------------------------------------------------------------ #
    #  _resolve_char_scancode
    # ------------------------------------------------------------------ #

    def test_resolve_lowercase_letter(self):
        synth, _ = self._make_synth()
        assert synth._resolve_char_scancode("a") == (0x41, 0x1E, False)

    def test_resolve_uppercase_letter_needs_shift(self):
        synth, _ = self._make_synth()
        assert synth._resolve_char_scancode("A") == (0x41, 0x1E, True)

    def test_resolve_digit_no_shift(self):
        synth, _ = self._make_synth()
        assert synth._resolve_char_scancode("1") == (0x31, 0x02, False)

    def test_resolve_shifted_punctuation_needs_shift(self):
        synth, _ = self._make_synth()
        assert synth._resolve_char_scancode("!") == (0x31, 0x02, True)

    def test_caps_lock_inverts_shift_for_letters(self):
        # With Caps Lock on, lowercase 'a' needs shift to produce 'a'
        # (the OS uppercases by default), and uppercase 'A' no longer
        # needs shift.
        synth, _ = self._make_synth(caps_on=True)
        assert synth._resolve_char_scancode("a") == (0x41, 0x1E, True)
        assert synth._resolve_char_scancode("A") == (0x41, 0x1E, False)

    def test_caps_lock_does_not_affect_digits(self):
        synth, _ = self._make_synth(caps_on=True)
        # Digits and punctuation are unaffected by Caps Lock.
        assert synth._resolve_char_scancode("1") == (0x31, 0x02, False)
        assert synth._resolve_char_scancode("!") == (0x31, 0x02, True)

    def test_non_ascii_falls_back_to_unicode(self):
        synth, _ = self._make_synth()
        assert synth._resolve_char_scancode("ñ") is None
        assert synth._resolve_char_scancode("é") is None
        assert synth._resolve_char_scancode("漢") is None

    def test_unmappable_char_falls_back(self):
        # VkKeyScanW returns -1 for chars without a single-keystroke mapping.
        synth, _ = self._make_synth(vk_scan={"x": -1})
        # 'x' isn't in our US table either; explicit -1 here just for clarity.
        assert synth._resolve_char_scancode("x") is None

    def test_altgr_required_char_falls_back(self):
        # Ctrl bit set in shift_state → return None (we don't synthesise AltGr).
        # Stub a char where shift_state has Ctrl bit (0b010 = 0x02).
        synth, _ = self._make_synth(vk_scan={"@": 0x0232})
        assert synth._resolve_char_scancode("@") is None

    def test_dead_key_char_falls_back(self):
        # Set bit 31 on the MAPVK_VK_TO_CHAR result for VK_OEM_7
        # (apostrophe), simulating US-International where ' is a dead key.
        synth, _ = self._make_synth(
            vk_scan={"'": 0x00DE},  # VK_OEM_7, no shift
            vsc={0xDE: 0x28},
            char_probe={0xDE: 0x80000027},  # 0x27 (apostrophe) + dead-key bit
        )
        assert synth._resolve_char_scancode("'") is None

    def test_external_shift_held_blocks_no_shift_chars(self):
        # If the user is physically holding Shift and we'd send a
        # char that doesn't need shift, we'd produce the wrong char.
        # Bail to UNICODE.
        synth, _ = self._make_synth(shift_held=True)
        assert synth._resolve_char_scancode("a") is None

    def test_external_shift_held_ok_for_shift_chars(self):
        # If shift is held and the char needs shift, that's fine —
        # we just don't add a redundant wrap.
        synth, _ = self._make_synth(shift_held=True)
        assert synth._resolve_char_scancode("A") == (0x41, 0x1E, True)

    # ------------------------------------------------------------------ #
    #  _make_char_scancode_events
    # ------------------------------------------------------------------ #

    def test_lowercase_letter_no_shift_wrap(self):
        synth, _ = self._make_synth()
        events = synth._make_char_scancode_events("a")
        assert events == [
            ("sc", 0x1E, True),
            ("sc", 0x1E, False),
        ]

    def test_uppercase_letter_wraps_with_shift(self):
        synth, _ = self._make_synth()
        events = synth._make_char_scancode_events("A")
        assert events == [
            ("sc", 0x2A, True),  # left shift down
            ("sc", 0x1E, True),  # A down
            ("sc", 0x1E, False),  # A up
            ("sc", 0x2A, False),  # left shift up
        ]

    def test_uppercase_letter_skips_wrap_when_shift_already_held(self):
        # Shift held by sticky modifier or physical key: don't double up.
        synth, _ = self._make_synth(shift_held=True)
        events = synth._make_char_scancode_events("A")
        assert events == [
            ("sc", 0x1E, True),
            ("sc", 0x1E, False),
        ]

    def test_returns_none_when_resolve_fails(self):
        synth, _ = self._make_synth()
        assert synth._make_char_scancode_events("漢") is None

    # ------------------------------------------------------------------ #
    #  send_text dispatch
    # ------------------------------------------------------------------ #

    def test_send_text_pure_ascii_uses_scancode(self):
        synth, captured = self._make_synth()
        synth.send_text("Hi")
        # Real _make_char_scancode_events would resolve scancodes for
        # H (shift+VK_H) and i (VK_I); our stub doesn't include H/i in
        # the VSC map so the events depend on what the resolver finds.
        # Check the simpler "az" pair instead.
        captured.clear()
        synth.send_text("az")
        assert captured == [
            [
                ("sc", 0x1E, True),
                ("sc", 0x1E, False),
                ("sc", 0x2C, True),
                ("sc", 0x2C, False),
            ]
        ]

    def test_send_text_mixed_ascii_and_emoji(self):
        # ASCII goes scancode, emoji falls back to UNICODE within the
        # same call. The order in the resulting INPUT array preserves
        # text order so the target app sees the chars in sequence.
        synth, captured = self._make_synth()
        synth.send_text("a漢z")
        assert captured == [
            [
                ("sc", 0x1E, True),
                ("sc", 0x1E, False),
                ("uni", "漢"),
                ("sc", 0x2C, True),
                ("sc", 0x2C, False),
            ]
        ]

    def test_send_text_empty_string_no_inject(self):
        synth, captured = self._make_synth()
        synth.send_text("")
        assert captured == []

    def test_send_text_all_unicode_when_layout_lacks_chars(self):
        # If VkKeyScanW returns -1 for everything (e.g. typing on a
        # keyboard layout that lacks Latin chars), every char falls
        # back to UNICODE and nothing changes about behaviour vs the
        # pre-scancode implementation.
        synth, captured = self._make_synth(
            vk_scan={"a": -1, "b": -1, "c": -1},
        )
        synth.send_text("abc")
        assert captured == [
            [
                ("uni", "a"),
                ("uni", "b"),
                ("uni", "c"),
            ]
        ]


class TestWindowsChordScancodeMode:
    """Modifier chords (Ctrl+V, Alt+Tab) and held modifiers must inject in
    *scancode mode* (``KEYEVENTF_SCANCODE``, ``wVk=0``), not wVk mode.

    Remote-desktop tools (TeamViewer / RDP / VNC) forward keystrokes by
    scancode over the wire and reliably relay scancode-mode events but drop
    the modifier half of a wVk-mode chord — the symptom being plain typing
    works over TeamViewer but Ctrl+V silently fails. ``send_text``'s letters
    were already scancode mode; this verifies ``send_key`` / ``hold_modifier``
    match.
    """

    def _make_synth(self, vsc: dict[int, int]):
        from src.platform import windows as win_mod

        synth = win_mod.WindowsKeySynthesizer.__new__(win_mod.WindowsKeySynthesizer)

        class _StubUser32:
            def MapVirtualKeyW(self_inner, vk, mode):
                if mode == win_mod.MAPVK_VK_TO_VSC:
                    return vsc.get(vk, 0)
                return 0

        synth._user32 = _StubUser32()
        return synth

    def test_scancode_mode_for_normal_vk(self):
        from src.platform import windows as win_mod

        synth = self._make_synth({0x41: 0x1E})  # VK_A → scancode 0x1E
        ev = synth._make_vk_scancode_event(0x41, key_down=True)
        ki = ev._input.ki
        assert ki.wVk == 0  # scancode mode ignores wVk
        assert ki.wScan == 0x1E
        assert ki.dwFlags & win_mod.KEYEVENTF_SCANCODE
        assert not (ki.dwFlags & win_mod.KEYEVENTF_KEYUP)

    def test_keyup_sets_keyup_flag(self):
        from src.platform import windows as win_mod

        synth = self._make_synth({0x41: 0x1E})
        ev = synth._make_vk_scancode_event(0x41, key_down=False)
        assert ev._input.ki.dwFlags & win_mod.KEYEVENTF_KEYUP

    def test_extended_key_sets_extended_flag(self):
        from src.platform import windows as win_mod

        # VK_LEFT is in _EXTENDED_KEYS → must carry the E0 prefix flag.
        synth = self._make_synth({win_mod.VK_LEFT: 0x4B})
        ev = synth._make_vk_scancode_event(win_mod.VK_LEFT, key_down=True)
        ki = ev._input.ki
        assert ki.dwFlags & win_mod.KEYEVENTF_SCANCODE
        assert ki.dwFlags & win_mod.KEYEVENTF_EXTENDEDKEY

    def test_no_scancode_falls_back_to_wvk_mode(self):
        # A VK with no scancode on this layout (MapVirtualKeyW → 0) must fall
        # back to the wVk-mode builder rather than emit wScan=0 scancode mode.
        synth = self._make_synth({})  # everything → 0
        captured = {}
        synth._make_key_event = lambda vk, key_down: (
            captured.update(vk=vk, key_down=key_down) or "fallback"
        )
        result = synth._make_vk_scancode_event(0x99, key_down=True)
        assert result == "fallback"
        assert captured == {"vk": 0x99, "key_down": True}

    def test_hold_modifier_uses_scancode_mode(self):
        from src.platform import windows as win_mod

        synth = self._make_synth({win_mod.VK_CONTROL: 0x1D})
        captured: list = []
        synth._inject = lambda units, **kw: captured.append(_flat(units))
        synth._log_send = lambda msg: None
        synth.hold_modifier("ctrl")
        assert len(captured) == 1 and len(captured[0]) == 1
        ki = captured[0][0]._input.ki
        assert ki.wVk == 0
        assert ki.wScan == 0x1D
        assert ki.dwFlags & win_mod.KEYEVENTF_SCANCODE


class TestPlatformInfo:
    """get_platform_info diagnostic."""

    def test_platform_info_returns_dict(self):
        from src.platform import get_platform_info

        info = get_platform_info()
        assert isinstance(info, dict)

    def test_platform_info_has_platform(self):
        from src.platform import get_platform_info

        info = get_platform_info()
        assert "platform" in info
        assert info["platform"] == CURRENT_PLATFORM

    def test_platform_info_has_python(self):
        from src.platform import get_platform_info

        info = get_platform_info()
        assert "python" in info


class TestPowerShellIsNeverInvokedByBareName:
    """``CreateProcess`` searches the CWD before System32.

    With no explicit application name, Windows resolves a bare
    ``powershell`` against the launching process's own directory and then
    the current working directory, ahead of System32.  The updater's
    signature check is a PowerShell call, and it is the entire
    Authenticode trust gate for auto-update: a planted ``powershell.exe``
    that prints a convincing ``Valid|<thumbprint>|<CN>|<version>`` line
    defeats it outright.  So the path has to be absolute at every call
    site, and ``shutil.which`` is not an acceptable fallback because on
    Windows it prepends the current directory to the search path -- the
    exact hole being closed.
    """

    def test_the_helper_returns_an_absolute_system32_path(self) -> None:
        from src.platform import powershell_path

        resolved = powershell_path()
        # ntpath, not os.path: the helper builds a Windows path on every
        # host, and posixpath.isabs() does not recognise a drive letter,
        # so os.path.isabs() would fail this on the Linux CI shard.
        assert ntpath.isabs(resolved)
        assert resolved.lower().endswith("powershell.exe")
        assert "system32" in resolved.lower()

    def test_the_helper_follows_system_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Not hardcoded to C:, for hosts installed on another volume."""
        from src.platform import powershell_path

        monkeypatch.setenv("SystemRoot", r"D:\Windows")
        assert powershell_path().startswith(r"D:\Windows")

    @pytest.mark.parametrize(
        "source",
        ["src/updater.py", "src/platform/windows.py"],
    )
    def test_no_call_site_passes_a_bare_name(self, source: str) -> None:
        """The guard that fails if either call site regresses."""
        text = Path(source).read_text(encoding="utf-8")
        assert '"powershell"' not in text, (
            f"{source} invokes powershell by bare name; use "
            "platform.powershell_path() so CWD cannot win"
        )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows backend")
class TestWindowsInjectPacing:
    """How the Windows synthesizer uses the serial injector.

    Typed text is paced, one SendInput call per event; a chord, the
    Shift+Left selection and a shift-wrapped character are each one call;
    a long text types in the background and nothing on the caller's
    thread waits for it.  The injector's own rules (inline budget, FIFO,
    cancel units) are covered on every runner by test_serial_injector.py.
    """

    # A small US layout for the real scancode builder: VkKeyScanW (shift
    # state << 8 | VK), VK -> scancode, VK -> unshifted char.
    _VK_SCAN = {**{c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz"}}
    _VK_SCAN.update({c: 0x100 | ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"})
    _VK_SCAN[" "] = 0x20
    _VSC = {ord(c): 0x11 + i for i, c in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
    _VSC[0x20] = 0x39
    _VSC[0x10] = 0x2A  # VK_SHIFT -> left shift scancode
    SHIFT_SC = 0x2A

    def _make(self, sleep=None, *, shift_held: bool = False):
        """A synthesizer with a fake SendInput, a fake (or gated) sleep and a
        stub user32, so the real scancode builder runs.

        The log holds ``("send", n, [key...])`` per SendInput call and
        ``("sleep", s)`` per pacing gap, in order.  A key is its VK for a
        VK event, else its scancode or Unicode code unit.
        """
        import threading

        from src.platform import windows as win_mod

        log: list = []
        log_lock = threading.Lock()
        synth = win_mod.WindowsKeySynthesizer.__new__(win_mod.WindowsKeySynthesizer)
        vk_scan, vsc = self._VK_SCAN, self._VSC

        def fake_send(n, arr, size):
            keys = [arr[i]._input.ki.wVk or arr[i]._input.ki.wScan for i in range(n)]
            with log_lock:
                log.append(("send", n, keys))
            return n

        def fake_sleep(s):
            with log_lock:
                log.append(("sleep", s))
            if sleep is not None:
                sleep(s)

        class _StubUser32:
            def VkKeyScanW(self_inner, ch):
                return vk_scan.get(ch, -1)

            def MapVirtualKeyW(self_inner, vk, mode):
                if mode == win_mod.MAPVK_VK_TO_VSC:
                    return vsc.get(vk, 0)
                if mode == win_mod.MAPVK_VK_TO_CHAR:
                    return vk if 0x41 <= vk <= 0x5A or vk == 0x20 else 0
                return 0

            def GetKeyState(self_inner, vk):
                return 0

            def GetAsyncKeyState(self_inner, vk):
                return 0x8000 if (vk == win_mod.VK_SHIFT and shift_held) else 0

        synth._user32 = _StubUser32()
        synth._send_input = fake_send
        synth._injector = synth._build_injector(sleep=fake_sleep)
        synth._log_send = lambda msg: None
        synth._make_vk_scancode_event = lambda vk, key_down: synth_event(win_mod, vk, key_down)
        synth._make_key_event = lambda vk, key_down: synth_event(win_mod, vk, key_down)
        synth._foreground_is_terminal = lambda: False
        return win_mod, synth, log

    @staticmethod
    def _sends(log):
        return [entry for entry in log if entry[0] == "send"]

    @staticmethod
    def _sent_keys(log):
        return [key for entry in log if entry[0] == "send" for key in entry[2]]

    def _assert_shift_never_spans_a_gap(self, win_mod, log):
        """An outside click can only land between SendInput calls, so Shift
        must not be down at any pacing gap: the P2 case from the review of
        PR #244, for the selection and for a shift-wrapped character."""
        shift_down = False
        for entry in log:
            if entry[0] == "sleep":
                assert not shift_down, "Shift was held across a pacing gap"
            else:
                for key in entry[2]:
                    if key in (win_mod.VK_SHIFT, self.SHIFT_SC):
                        shift_down = not shift_down
        assert not shift_down

    # -- pacing ------------------------------------------------------------

    def test_lowercase_text_is_one_call_per_event_with_gaps_between(self):
        win_mod, synth, log = self._make()
        synth.send_text("ab")
        gap = win_mod.INTER_EVENT_DELAY_S
        assert gap == 0.003
        assert log == [
            ("send", 1, [0x11]),  # a down (scancode)
            ("sleep", gap),
            ("send", 1, [0x11]),
            ("sleep", gap),
            ("send", 1, [0x12]),  # b
            ("sleep", gap),
            ("send", 1, [0x12]),
        ]

    def test_a_single_event_does_not_sleep(self):
        win_mod, synth, log = self._make()
        synth.hold_modifier("ctrl")
        assert log == [("send", 1, [win_mod.VK_CONTROL])]

    def test_empty_text_does_nothing(self):
        win_mod, synth, log = self._make()
        synth.send_text("")
        assert log == []

    def test_a_bare_key_is_paced(self):
        win_mod, synth, log = self._make()
        synth.send_key("Return")
        assert [entry[0] for entry in log] == ["send", "sleep", "send"]

    # -- atomic gestures ---------------------------------------------------

    def test_a_chord_is_one_send_input_call(self):
        """Ctrl down, C, Ctrl up: no gap a click could land in as Ctrl+click."""
        win_mod, synth, log = self._make()
        synth.send_key("c", modifiers=["ctrl"])
        assert log == [("send", 4, [win_mod.VK_CONTROL, ord("C"), ord("C"), win_mod.VK_CONTROL])]

    def test_a_shift_wrapped_character_is_one_call(self):
        """The real scancode builder wraps "R" in Shift; that wrap is one
        group, so a capital in paced text leaves no gap with Shift down."""
        win_mod, synth, log = self._make()
        synth.send_text("Ra")
        assert self._sends(log)[0] == ("send", 4, [self.SHIFT_SC, 0x22, 0x22, self.SHIFT_SC])
        assert [n for _, n, _ in self._sends(log)] == [4, 1, 1]
        self._assert_shift_never_spans_a_gap(win_mod, log)

    def test_replace_text_selects_in_one_call_and_shift_never_spans_a_gap(self):
        """``replace_text(5, "Right ")`` from "keep wrong": the selection is
        one call and so is the capital R, so an outside click at any gap
        finds Shift up and cannot extend the selection over "keep "."""
        win_mod, synth, log = self._make()
        synth.replace_text(5, "Right ")

        sends = self._sends(log)
        assert sends[0][1] == 12  # Shift, 5 x (Left down, Left up), Shift
        assert sends[0][2][0] == win_mod.VK_SHIFT and sends[0][2][-1] == win_mod.VK_SHIFT
        assert sends[0][2][1:-1] == [win_mod.VK_LEFT] * 10
        assert sends[1] == ("send", 4, [self.SHIFT_SC, 0x22, 0x22, self.SHIFT_SC])  # R
        assert [n for _, n, _ in sends[2:]] == [1] * 10  # "ight " paced
        self._assert_shift_never_spans_a_gap(win_mod, log)

    def test_replace_text_with_nothing_to_select_only_types(self):
        win_mod, synth, log = self._make()
        synth.replace_text(0, "ab")
        assert [n for _, n, _ in self._sends(log)] == [1, 1, 1, 1]

    # -- inline or background ----------------------------------------------

    def test_a_word_is_typed_before_send_text_returns(self):
        """A suggestion pick stays synchronous: no background thread for a word."""
        win_mod, synth, log = self._make()
        synth.send_text("sixteen letters!")
        assert not synth.busy
        assert len(self._sends(log)) == 32

    def test_a_long_text_returns_before_it_is_typed(self):
        """The P1 case from the review of PR #244: a dictated passage used to
        block the Qt thread for (N-1) x 3 ms; now the call returns while a
        worker types it."""
        import threading

        gate = threading.Event()
        win_mod, synth, log = self._make(sleep=lambda s: gate.wait(5))

        synth.send_text("x" * 100)  # 200 groups, 0.6 s of gaps: over the inline budget
        assert synth.busy
        assert len(self._sends(log)) < 200
        gate.set()
        assert synth._injector.settle(5)
        assert not synth.busy
        assert len(self._sends(log)) == 200

    def test_a_key_during_a_long_text_returns_at_once_and_lands_after_it(self):
        """The second P1 case: a key tapped while a passage is typing used to
        join the worker on the Qt thread for the rest of the passage.  It
        now queues and the call returns; the order still holds."""
        import threading

        gate = threading.Event()
        win_mod, synth, log = self._make(sleep=lambda s: gate.wait(5))
        synth.send_text("x" * 100)
        assert synth.busy

        synth.send_key("Return")  # must not block
        assert synth.busy
        gate.set()
        assert synth._injector.settle(5)
        keys = self._sent_keys(log)
        assert keys[:200] == [0x28] * 200
        assert keys[200:] == [win_mod.VK_RETURN] * 2

    def test_re_holding_a_locked_shift_after_a_long_text_does_not_wait(self):
        """The third P1 case: ``_without_held_modifiers`` releases a locked
        Shift, sends the text, and re-holds it in a ``finally``.  The
        re-hold must queue, not join; and a capital typed afterwards must
        be built against the Shift that *will* be held once the queue
        drains (``_os_key_down``), or its own Shift wrap would release the
        lock."""
        import threading

        gate = threading.Event()
        win_mod, synth, log = self._make(sleep=lambda s: gate.wait(5))
        synth.send_text("x" * 100)
        synth.hold_modifier("shift")  # returns at once
        assert synth.busy
        synth.send_text("A")  # built while the hold is still queued
        gate.set()
        assert synth._injector.settle(5)

        keys = self._sent_keys(log)
        assert keys[200:] == [win_mod.VK_SHIFT, 0x11, 0x11], "A carried its own Shift wrap"

    def test_a_locked_shift_reads_as_held_only_while_the_hold_is_queued(self):
        """Once the injector is idle the OS is authoritative again."""
        win_mod, synth, log = self._make()
        synth.hold_modifier("shift")  # inline: delivered, injector idle
        assert not synth.busy
        # The stub OS says Shift is up (nothing really held), so the
        # prediction must not linger: "A" gets its wrap.
        synth.send_text("A")
        assert self._sends(log)[-1] == ("send", 4, [self.SHIFT_SC, 0x11, 0x11, self.SHIFT_SC])

    # -- cancel and shutdown -----------------------------------------------

    def test_cancel_pending_stops_a_long_text_with_no_key_left_down(self):
        import threading

        gate = threading.Event()
        win_mod, synth, log = self._make(sleep=lambda s: gate.wait(5))
        synth.send_text("x" * 100)
        synth.cancel_pending()
        gate.set()
        assert synth._injector.settle(5)

        sends = self._sends(log)
        assert 0 < len(sends) < 200
        assert len(sends) % 2 == 0, "a key was left down"

    def test_cancel_does_not_split_a_surrogate_pair(self):
        """An emoji is two UTF-16 code units, four events; a cancel in the
        first gap must still deliver all four (review of cc0f8d1)."""
        import threading

        gate = threading.Event()
        win_mod, synth, log = self._make(sleep=lambda s: gate.wait(5))
        synth.send_text(chr(0x1F600) + "x" * 100)
        synth.cancel_pending()
        gate.set()
        assert synth._injector.settle(5)
        assert self._sent_keys(log) == [0xD83D, 0xD83D, 0xDE00, 0xDE00]

    def test_cancel_keeps_a_queued_re_hold(self):
        """The re-hold of a locked modifier queued behind the text is not
        typed text: a cancel drops the text, the hold still lands."""
        import threading

        gate = threading.Event()
        win_mod, synth, log = self._make(sleep=lambda s: gate.wait(5))
        synth.send_text("x" * 100)
        synth.hold_modifier("shift")
        synth.cancel_pending()
        gate.set()
        assert synth._injector.settle(5)
        keys = self._sent_keys(log)
        assert len(keys) < 201
        assert keys[-1] == win_mod.VK_SHIFT

    def test_cancel_pending_when_idle_is_harmless(self):
        win_mod, synth, log = self._make()
        synth.cancel_pending()
        synth.send_text("ab")
        assert len(self._sends(log)) == 4

    def test_shutdown_finishes_the_character_in_flight_and_stops(self):
        """Quitting mid-insert: the worker is joined, the shift-wrapped
        character in flight is complete, the rest is dropped, and a
        modifier release sent next goes out at once (review of cc0f8d1)."""
        import threading

        gate = threading.Event()
        win_mod, synth, log = self._make(sleep=lambda s: gate.wait(5))
        synth.send_text("aB" * 60)
        assert synth.busy
        threading.Timer(0.05, gate.set).start()

        synth.shutdown()
        assert not synth.busy
        keys = self._sent_keys(log)
        assert len(keys) < 240
        assert keys.count(self.SHIFT_SC) % 2 == 0, "a Shift wrap was left open"
        assert keys.count(0x11) % 2 == 0 and keys.count(0x12) % 2 == 0, "a key was left down"

        synth.release_modifier("shift")
        assert self._sent_keys(log)[-1] == win_mod.VK_SHIFT


def synth_event(win_mod, vk: int, key_down: bool = True):
    inp = win_mod.INPUT()
    inp.type = win_mod.INPUT_KEYBOARD
    inp._input.ki.wVk = vk
    inp._input.ki.dwFlags = 0 if key_down else win_mod.KEYEVENTF_KEYUP
    return inp
