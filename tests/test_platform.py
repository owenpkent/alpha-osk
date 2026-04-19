"""Tests for the platform abstraction layer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.platform import CURRENT_PLATFORM, get_config_dir, get_model_dir
from src.platform.base import KeySynthesizerBase


class TestPlatformDetection:
    """Platform identification."""

    def test_current_platform_is_string(self):
        assert isinstance(CURRENT_PLATFORM, str)

    def test_current_platform_is_valid(self):
        assert CURRENT_PLATFORM in ("windows", "linux", "unsupported")

    def test_current_platform_matches_sys(self):
        if sys.platform == "win32":
            assert CURRENT_PLATFORM == "windows"
        elif sys.platform.startswith("linux"):
            assert CURRENT_PLATFORM == "linux"


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
    """Configuration directory helpers."""

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
            "xdotool", "key", "--clearmodifiers",
            "shift+Left", "shift+Left", "shift+Left",
        ]
        assert calls[1] == ["xdotool", "type", "--clearmodifiers", "hello"]
        assert len(calls) == 2

    def test_xdotool_zero_backspace_skips_selection(self, monkeypatch):
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.replace_text(0, "hi")
        # No shift+Left chain; falls through to send_text.
        assert calls == [["xdotool", "type", "--clearmodifiers", "hi"]]

    def test_xdotool_empty_text_still_selects(self, monkeypatch):
        synth, calls = self._make_synth("xdotool", monkeypatch)
        synth.replace_text(2, "")
        assert calls == [[
            "xdotool", "key", "--clearmodifiers",
            "shift+Left", "shift+Left",
        ]]

    def test_ydotool_frames_shift_around_lefts(self, monkeypatch):
        synth, calls = self._make_synth("ydotool", monkeypatch)
        synth.replace_text(2, "hi")
        assert calls == [
            ["ydotool", "key", "--key-down", "shift"],
            ["ydotool", "key", "Left"],
            ["ydotool", "key", "Left"],
            ["ydotool", "key", "--key-up", "shift"],
            ["ydotool", "type", "hi"],
        ]

    def test_no_tool_is_silent_noop(self, monkeypatch):
        synth, calls = self._make_synth(None, monkeypatch)
        synth.replace_text(3, "x")
        assert calls == []


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
