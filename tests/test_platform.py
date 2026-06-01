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
        synth._inject = lambda events: captured.append(list(events))
        synth._make_key_event = lambda vk, key_down: ("vk", vk, key_down)
        synth._make_unicode_events = lambda c: [("uni", c)]
        # Force the per-char dispatch in replace_text to take the
        # UNICODE branch so this suite can keep asserting the
        # branching behaviour of replace_text itself, independent of
        # the scancode-resolution logic which has its own coverage in
        # TestWindowsScancodeDispatch.
        synth._make_char_scancode_events = lambda c: None
        # Bypass GetForegroundWindow / GetClassNameW directly. Mocking
        # them via ctypes is brittle off-Windows.
        synth._get_foreground_window_class = lambda: foreground_class
        return synth, captured

    def test_terminal_uses_backspace_path(self):
        from src.platform.windows import VK_BACK
        synth, captured = self._make_synth("ConsoleWindowClass")
        synth.replace_text(3, "Ow")
        assert captured == [[
            ("vk", VK_BACK, True),  ("vk", VK_BACK, False),
            ("vk", VK_BACK, True),  ("vk", VK_BACK, False),
            ("vk", VK_BACK, True),  ("vk", VK_BACK, False),
            ("uni", "O"), ("uni", "w"),
        ]]

    def test_windows_terminal_class_also_uses_backspace(self):
        from src.platform.windows import VK_BACK
        synth, captured = self._make_synth("CASCADIA_HOSTING_WINDOW_CLASS")
        synth.replace_text(1, "x")
        assert captured == [[
            ("vk", VK_BACK, True), ("vk", VK_BACK, False),
            ("uni", "x"),
        ]]

    def test_mintty_class_also_uses_backspace(self):
        from src.platform.windows import VK_BACK
        synth, captured = self._make_synth("mintty")
        synth.replace_text(2, "")
        assert captured == [[
            ("vk", VK_BACK, True), ("vk", VK_BACK, False),
            ("vk", VK_BACK, True), ("vk", VK_BACK, False),
        ]]

    def test_non_terminal_uses_shift_left_path(self):
        from src.platform.windows import VK_LEFT, VK_SHIFT
        synth, captured = self._make_synth("Chrome_WidgetWin_1")
        synth.replace_text(2, "hi")
        assert captured == [[
            ("vk", VK_SHIFT, True),
            ("vk", VK_LEFT, True),  ("vk", VK_LEFT, False),
            ("vk", VK_LEFT, True),  ("vk", VK_LEFT, False),
            ("vk", VK_SHIFT, False),
            ("uni", "h"), ("uni", "i"),
        ]]

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
        synth._inject = lambda events: captured.append(list(events))
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
        synth._user32 = _StubUser32()
        return synth, captured

    def test_ctrl_minus_uses_vk_oem_minus(self):
        from src.platform.windows import VK_CONTROL
        # US-layout VkKeyScanW('-') = (low=VK_OEM_MINUS=0xBD, high=0)
        synth, captured = self._make_synth({"-": 0x00BD})
        synth.send_key("-", modifiers=["ctrl"])
        # Ctrl-down → VK_OEM_MINUS-down → VK_OEM_MINUS-up → Ctrl-up,
        # all virtual-key events (no Unicode injection).
        assert captured == [[
            ("vk", VK_CONTROL, True),
            ("vk", 0xBD, True), ("vk", 0xBD, False),
            ("vk", VK_CONTROL, False),
        ]]

    def test_ctrl_equals_uses_vk_oem_plus(self):
        from src.platform.windows import VK_CONTROL
        # US-layout VkKeyScanW('=') = (low=VK_OEM_PLUS=0xBB, high=0)
        synth, captured = self._make_synth({"=": 0x00BB})
        synth.send_key("=", modifiers=["ctrl"])
        assert captured == [[
            ("vk", VK_CONTROL, True),
            ("vk", 0xBB, True), ("vk", 0xBB, False),
            ("vk", VK_CONTROL, False),
        ]]

    def test_shift_required_char_prepends_shift(self):
        from src.platform.windows import VK_CONTROL, VK_SHIFT
        # US-layout VkKeyScanW('+') = (low=VK_OEM_PLUS=0xBB, high=1) —
        # '+' is Shift+'=' physically, so the synth must add a Shift
        # press around the chord.
        synth, captured = self._make_synth({"+": 0x01BB})
        synth.send_key("+", modifiers=["ctrl"])
        # Shift gets prepended, so order is Shift→Ctrl press, then key,
        # then Ctrl→Shift release.
        assert captured == [[
            ("vk", VK_SHIFT, True),
            ("vk", VK_CONTROL, True),
            ("vk", 0xBB, True), ("vk", 0xBB, False),
            ("vk", VK_CONTROL, False),
            ("vk", VK_SHIFT, False),
        ]]

    def test_unmappable_char_falls_back_to_unicode(self):
        from src.platform.windows import VK_CONTROL
        # VkKeyScanW returns -1 for chars not on the active layout.
        synth, captured = self._make_synth({})  # everything → -1
        synth.send_key("ñ", modifiers=["ctrl"])
        # Ctrl is still wrapped around the keystroke; the action key
        # falls through to the Unicode path.
        assert captured == [[
            ("vk", VK_CONTROL, True),
            ("uni", "ñ"),
            ("vk", VK_CONTROL, False),
        ]]


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
            0x10: 0x2A,   # VK_SHIFT → left shift scancode
            0x41: 0x1E,   # A
            0x5A: 0x2C,   # Z
            0x31: 0x02,   # 1
            0xBD: 0x0C,   # -
            0xBE: 0x34,   # .
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
        synth._inject = lambda events: captured.append(list(events))

        # Real _make_unicode_events would build INPUT structures; for
        # tests we substitute a marker so the assertion shape is
        # readable. The scancode helper builds real INPUT objects via
        # _make_scancode_event, which we replace with a marker for the
        # same reason.
        synth._make_unicode_events = lambda c: [("uni", c)]
        synth._make_scancode_event = (
            lambda scancode, key_down: ("sc", scancode, key_down)
        )
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
            vk_scan={"'": 0x00DE},      # VK_OEM_7, no shift
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
            ("sc", 0x2A, True),   # left shift down
            ("sc", 0x1E, True),   # A down
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
        assert captured == [[
            ("sc", 0x1E, True), ("sc", 0x1E, False),
            ("sc", 0x2C, True), ("sc", 0x2C, False),
        ]]

    def test_send_text_mixed_ascii_and_emoji(self):
        # ASCII goes scancode, emoji falls back to UNICODE within the
        # same call. The order in the resulting INPUT array preserves
        # text order so the target app sees the chars in sequence.
        synth, captured = self._make_synth()
        synth.send_text("a漢z")
        assert captured == [[
            ("sc", 0x1E, True), ("sc", 0x1E, False),
            ("uni", "漢"),
            ("sc", 0x2C, True), ("sc", 0x2C, False),
        ]]

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
        assert captured == [[
            ("uni", "a"), ("uni", "b"), ("uni", "c"),
        ]]


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
        assert ki.wVk == 0                       # scancode mode ignores wVk
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
        synth._make_key_event = (
            lambda vk, key_down: captured.update(vk=vk, key_down=key_down) or "fallback"
        )
        result = synth._make_vk_scancode_event(0x99, key_down=True)
        assert result == "fallback"
        assert captured == {"vk": 0x99, "key_down": True}

    def test_hold_modifier_uses_scancode_mode(self):
        from src.platform import windows as win_mod
        synth = self._make_synth({win_mod.VK_CONTROL: 0x1D})
        captured: list = []
        synth._inject = lambda events: captured.append(list(events))
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
