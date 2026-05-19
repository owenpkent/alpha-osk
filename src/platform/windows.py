"""
Windows Key Synthesizer
========================

Implements key synthesis for Windows using the **Win32 SendInput API**
via Python's built-in ``ctypes`` module — no third-party dependencies
required.

How It Works
------------
Windows provides ``SendInput()`` in ``user32.dll`` which injects keyboard
(and mouse) events at the lowest level of the input stack.  This means
Alpha-OSK keystrokes are indistinguishable from physical keyboard input
to every application, including games and system dialogs.

Three injection modes are used. ``send_text`` and the typed portion of
``replace_text`` dispatch per character between modes 2 and 3 below.

1. **Virtual-Key mode** (``send_key``, ``send_combination``):
   Sends a ``KEYBDINPUT`` with a virtual-key code in ``wVk`` and the
   layout scancode in ``wScan``.  Used for special keys (Backspace,
   F-keys, modifiers, arrows, etc.) and for modifier+key combos
   (Ctrl+C, Alt+F4, etc.).

2. **Scancode mode** (``send_text`` default for ASCII):
   Sends a ``KEYBDINPUT`` with ``wVk = 0``, the layout scancode in
   ``wScan``, and the ``KEYEVENTF_SCANCODE`` flag.  The OS looks up
   the virtual key from the scancode under the active layout and
   dispatches a normal ``WM_KEYDOWN(VK_X)`` plus ``WM_CHAR`` (the same
   path a physical keypress takes).  This is what the Windows
   on-screen keyboard does, and it is what makes Alpha-OSK reach
   apps that filter on real virtual-key codes or read raw scancodes:
   Blender, VirtualBox, DirectInput games, raw-input 3D / CAD tools.

3. **Unicode mode** (``send_text`` per-char fallback):
   Sends a ``KEYBDINPUT`` with ``KEYEVENTF_UNICODE`` and the
   character's UTF-16 code point in ``wScan``.  Layout-independent
   and covers the full Unicode range (emoji, accented characters,
   CJK).  Used per character when scancode mode is unsafe: non-ASCII
   chars, unmappable chars on the active layout, AltGr-required
   chords, dead-key triggers, and the corner case where Shift is
   physically held but the char does not need shift.

   Unicode mode produces a ``WM_KEYDOWN`` with the sentinel
   ``VK_PACKET (0xE7)``.  Apps that filter on real VKs ignore it,
   which is why scancode mode is preferred for ASCII even though
   Unicode mode would otherwise be simpler.

UIAccess (EV Code Signing)
--------------------------
By default, a standard-privilege process **cannot** send input to windows
running at a higher integrity level (e.g. an elevated Command Prompt or a
UAC dialog).  To bypass this restriction, Alpha-OSK can be built with a
**UIAccess manifest** and **EV code-signed**.  When running with UIAccess:

- Keystrokes reach elevated windows.
- The keyboard can appear above the Secure Desktop (Ctrl+Alt+Del screen).
- The keyboard can appear above UAC consent prompts.

The manifest file ``alpha-osk.exe.manifest`` enables this.  See
``docs/build/WINDOWS.md`` for signing instructions.

Virtual-Key Code Reference
--------------------------
Full list: https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes

Dependencies
------------
None beyond Python's standard library (``ctypes``).

See Also
--------
- ``base.py`` — abstract interface this class implements.
- ``docs/architecture/PLATFORM_ARCHITECTURE.md`` — design rationale.
- ``docs/build/WINDOWS.md`` — Windows-specific setup and signing guide.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
from typing import Dict, List, Optional, Tuple

from .base import KeySynthesizerBase

_logger = logging.getLogger("WindowsKeySynthesizer")


# ====================================================================== #
#  Win32 Constants
# ====================================================================== #

INPUT_KEYBOARD = 1

# KEYBDINPUT.dwFlags
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# MapVirtualKey translation modes (winuser.h)
MAPVK_VK_TO_VSC = 0   # VK → scancode using the active layout
MAPVK_VK_TO_CHAR = 2  # VK → unshifted char; bit 31 set indicates a dead key

# Virtual-Key Codes (subset used by Alpha-OSK)
# https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes
VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12          # Alt
VK_PAUSE = 0x13
VK_CAPITAL = 0x14       # Caps Lock
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21         # Page Up
VK_NEXT = 0x22          # Page Down
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_SNAPSHOT = 0x2C      # Print Screen
VK_INSERT = 0x2D
VK_DELETE = 0x2E
VK_LWIN = 0x5B          # Left Windows key
VK_NUMPAD0 = 0x60
VK_NUMPAD1 = 0x61
VK_NUMPAD2 = 0x62
VK_NUMPAD3 = 0x63
VK_NUMPAD4 = 0x64
VK_NUMPAD5 = 0x65
VK_NUMPAD6 = 0x66
VK_NUMPAD7 = 0x67
VK_NUMPAD8 = 0x68
VK_NUMPAD9 = 0x69
VK_MULTIPLY = 0x6A
VK_ADD = 0x6B
VK_SUBTRACT = 0x6D
VK_DECIMAL = 0x6E
VK_DIVIDE = 0x6F
VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F4 = 0x73
VK_F5 = 0x74
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91        # Scroll Lock


# ====================================================================== #
#  Win32 Structures (ctypes)
# ====================================================================== #

# ULONG_PTR is a pointer-sized unsigned integer (4 bytes on 32-bit,
# 8 bytes on 64-bit Windows).  c_size_t is the cross-version match;
# wintypes.ULONG_PTR isn't guaranteed across all Python versions we
# support.
ULONG_PTR = ctypes.c_size_t


class KEYBDINPUT(ctypes.Structure):
    """
    Win32 KEYBDINPUT structure.

    See: https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-keybdinput

    Fields:
        wVk:         Virtual-key code (0 for Unicode mode).
        wScan:       Hardware scan code, or Unicode code point when
                     ``KEYEVENTF_UNICODE`` is set.
        dwFlags:     Combination of ``KEYEVENTF_*`` constants.
        time:        Timestamp (0 = system fills it in).
        dwExtraInfo: Opaque ULONG_PTR — application-defined data
                     retrievable via ``GetMessageExtraInfo``.  Set to 0
                     since we don't tag our input.  MSDN types this as
                     ``ULONG_PTR`` (an integer, not a real pointer); the
                     old ``POINTER(c_ulong)`` typing was wrong and led
                     us to allocate Python objects whose addresses
                     could be reaped before SendInput consumed them.
    """
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    """
    Union inside INPUT.

    On 64-bit Windows the union must be exactly 28 bytes — the size of
    MOUSEINPUT (the largest member).  If we omit MOUSEINPUT the union is
    only 20 bytes (KEYBDINPUT), making ctypes.sizeof(INPUT) = 32 instead
    of the required 40.  SendInput rejects every call when cbSize is wrong,
    returning 0 events injected with GetLastError() == 0 (silent failure).

    The _padding field forces the union to 28 bytes so the full INPUT
    struct rounds to 40 bytes on 64-bit, matching what Windows expects.
    """
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("_padding", ctypes.c_byte * 28),  # sizeof(MOUSEINPUT) on 64-bit
    ]


class INPUT(ctypes.Structure):
    """
    Win32 INPUT structure passed to ``SendInput()``.

    See: https://learn.microsoft.com/en-us/windows/win32/api/winuser/ns-winuser-input
    """
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


# ====================================================================== #
#  Key Name → Virtual-Key Code Mapping
# ====================================================================== #

# Maps platform-neutral key names (used by keyboard_bridge.py) to
# Windows virtual-key codes.  Character keys (a-z, 0-9) are handled
# separately by ord(char.upper()).
_KEY_MAP: Dict[str, int] = {
    # Special keys
    "BackSpace": VK_BACK,
    "Tab": VK_TAB,
    "Return": VK_RETURN,
    "Escape": VK_ESCAPE,
    "space": VK_SPACE,
    "Delete": VK_DELETE,
    "Insert": VK_INSERT,

    # Navigation
    "Left": VK_LEFT,
    "Right": VK_RIGHT,
    "Up": VK_UP,
    "Down": VK_DOWN,
    "Home": VK_HOME,
    "End": VK_END,
    "Page_Up": VK_PRIOR,
    "Page_Down": VK_NEXT,

    # Function keys
    "F1": VK_F1, "F2": VK_F2, "F3": VK_F3, "F4": VK_F4,
    "F5": VK_F5, "F6": VK_F6, "F7": VK_F7, "F8": VK_F8,
    "F9": VK_F9, "F10": VK_F10, "F11": VK_F11, "F12": VK_F12,

    # Lock / misc keys
    "Num_Lock": VK_NUMLOCK,
    "Scroll_Lock": VK_SCROLL,
    "Pause": VK_PAUSE,
    "Print": VK_SNAPSHOT,
    "Caps_Lock": VK_CAPITAL,

    # Modifiers (used when building combos)
    "ctrl": VK_CONTROL,
    "alt": VK_MENU,
    "shift": VK_SHIFT,
    "win": VK_LWIN,
    "super": VK_LWIN,
}

# Keys that require the EXTENDEDKEY flag on Windows.
# These are keys whose scan codes are preceded by 0xE0 in the keyboard
# scan code table.
_EXTENDED_KEYS = {
    VK_INSERT, VK_DELETE, VK_HOME, VK_END, VK_PRIOR, VK_NEXT,
    VK_LEFT, VK_RIGHT, VK_UP, VK_DOWN,
    VK_SNAPSHOT, VK_LWIN,
    VK_NUMLOCK,  # Numlock is extended in the enhanced keyboard
}


# ====================================================================== #
#  WindowsKeySynthesizer
# ====================================================================== #

class WindowsKeySynthesizer(KeySynthesizerBase):
    """
    Windows key synthesis using the Win32 ``SendInput`` API.

    This implementation:

    - Uses **virtual-key codes** for special keys and modifier combos.
    - Uses **KEYEVENTF_UNICODE** for plain text (layout-independent).
    - Correctly sets **KEYEVENTF_EXTENDEDKEY** for navigation/edit keys.
    - Works with **UIAccess** when the binary is EV code-signed.

    Attributes:
        _send_input: Reference to ``user32.SendInput``.
        _has_ui_access: Whether UIAccess privileges are active.
    """

    def __init__(self) -> None:
        # Use use_last_error=True so ctypes.get_last_error() captures the real
        # Win32 error code after each SendInput call (windll doesn't do this by default).
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._send_input = self._user32.SendInput
        self._send_input.argtypes = [
            wintypes.UINT,                      # nInputs
            ctypes.POINTER(INPUT),              # pInputs
            ctypes.c_int,                       # cbSize
        ]
        self._send_input.restype = wintypes.UINT

        # Pin argtypes so the foreground-window-class probe (used to
        # detect terminals in replace_text) returns correct values on 64-bit.
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetClassNameW.argtypes = [
            wintypes.HWND, wintypes.LPWSTR, ctypes.c_int,
        ]
        self._user32.GetClassNameW.restype = ctypes.c_int

        # VkKeyScanW maps a Unicode character to the VK + shift state that
        # would type it on the current keyboard layout.  Used in send_key
        # so that modifier+punctuation chords (Ctrl+-, Ctrl+=, Ctrl+/, ...)
        # produce real WM_KEYDOWN(VK_OEM_*) events instead of Unicode
        # injection — apps' shortcut handlers listen for the former.
        self._user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
        self._user32.VkKeyScanW.restype = wintypes.SHORT

        # MapVirtualKeyW translates a virtual-key code to its hardware
        # scancode under the current keyboard layout.  We populate
        # KEYBDINPUT.wScan with this so synthesised events look like
        # physical keystrokes. Necessary for remote-desktop tools
        # (TeamViewer, RDP, VNC, AnyDesk) that forward keystrokes
        # *by scancode* over the wire.  With wScan=0, those tools
        # either drop the event or forward it with broken modifier
        # state, so Ctrl+V / Ctrl+C / Ctrl+click silently no-op on the
        # remote machine.  Local Windows apps key off VK and ignore
        # wScan, so this is backwards-compatible.
        #
        # MapVirtualKeyW also serves the scancode-mode character path:
        # MAPVK_VK_TO_VSC for the scancode itself, MAPVK_VK_TO_CHAR for
        # the dead-key probe (bit 31 set on return = this VK produces a
        # dead key on the active layout).
        self._user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
        self._user32.MapVirtualKeyW.restype = wintypes.UINT

        # GetKeyState reports the toggle state of Caps Lock (bit 0).
        # GetAsyncKeyState reports whether Shift is physically held
        # right now (high bit). Both feed the scancode-mode dispatch
        # in _resolve_char_scancode so we emit the correct shift
        # wrap regardless of OS-side caps / shift state.
        self._user32.GetKeyState.argtypes = [ctypes.c_int]
        self._user32.GetKeyState.restype = wintypes.SHORT
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = wintypes.SHORT

        # Check UIAccess status
        self._has_ui_access = self._check_ui_access()
        if self._has_ui_access:
            _logger.info(
                "Windows key synthesizer ready (SendInput + UIAccess)"
            )
        else:
            _logger.info(
                "Windows key synthesizer ready (SendInput — no UIAccess). "
                "To send input to elevated windows, build with UIAccess "
                "manifest and EV code-sign the binary."
            )

    # ------------------------------------------------------------------ #
    #  Interface implementation
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """Always True on Windows — SendInput is a system call."""
        return True

    def backend_name(self) -> str:
        """Return ``"SendInput"`` or ``"SendInput+UIAccess"``."""
        if self._has_ui_access:
            return "SendInput+UIAccess"
        return "SendInput"

    def send_key(
        self,
        key_name: str,
        modifiers: Optional[List[str]] = None,
    ) -> None:
        """
        Send a single key press+release, optionally with modifiers.

        Builds an array of INPUT events:
          1. Press each modifier.
          2. Press the action key.
          3. Release the action key.
          4. Release each modifier (reverse order).

        Then injects them all atomically with a single ``SendInput`` call.

        Args:
            key_name: Platform-neutral key name or single character.
            modifiers: Optional modifier names (``"ctrl"``, ``"alt"``,
                       ``"shift"``, ``"win"``).
        """
        modifiers = list(modifiers or [])

        # Resolve the action key.  For single-char keys not in the
        # explicit map (punctuation: -, =, /, [, etc.), fall back to
        # VkKeyScanW so chords like Ctrl+- produce a real
        # WM_KEYDOWN(VK_OEM_MINUS) — Unicode injection alone wouldn't
        # trigger app shortcut handlers (browser zoom, etc.).
        vk = self._resolve_vk(key_name)
        unicode_fallback = False
        if vk is None and len(key_name) == 1:
            resolved = self._resolve_char_vk(key_name)
            if resolved is not None:
                vk, needs_shift = resolved
                if needs_shift and "shift" not in modifiers:
                    modifiers.insert(0, "shift")
            else:
                unicode_fallback = True
        elif vk is None:
            _logger.warning("Unknown key name: %s", key_name)
            return

        events: List[INPUT] = []

        # Press modifiers
        for mod in modifiers:
            mod_vk = _KEY_MAP.get(mod)
            if mod_vk is not None:
                events.append(self._make_key_event(mod_vk, key_down=True))

        # Press + release action key
        if unicode_fallback:
            events.extend(self._make_unicode_events(key_name))
        else:
            assert vk is not None
            events.append(self._make_key_event(vk, key_down=True))
            events.append(self._make_key_event(vk, key_down=False))

        # Release modifiers (reverse order)
        for mod in reversed(modifiers):
            mod_vk = _KEY_MAP.get(mod)
            if mod_vk is not None:
                events.append(self._make_key_event(mod_vk, key_down=False))

        self._inject(events)
        self._log_send(
            f"key={key_name}"
            + (f" mods={modifiers}" if modifiers else "")
        )

    def send_text(self, text: str) -> None:
        """Type a string via scancode injection where possible, falling
        back to ``KEYEVENTF_UNICODE`` per character.

        Dispatch
        --------
        For each char we try ``_make_char_scancode_events`` first.  When
        that returns a result, the char goes out as a real ``WM_KEYDOWN``
        with the proper VK derived from the scancode (the same path a
        physical keystroke takes).  Apps that listen for ``WM_KEYDOWN``
        rather than ``WM_CHAR`` (Blender shortcuts, VirtualBox forwarded
        input to the guest VM, DirectInput games, CAD tools) only see
        clicked letters via this path. The Windows on-screen keyboard
        uses scancode mode for the same reason.

        When ``_make_char_scancode_events`` returns ``None`` we fall
        back to ``_make_unicode_events`` for that char alone. UNICODE
        injection is layout-independent and covers any Unicode point
        including emoji and CJK, but produces a ``VK_PACKET`` event
        that some apps filter out.  See ``_resolve_char_scancode`` for
        the exhaustive list of fall-back cases.

        Mixing modes inside one ``send_text`` call is fine: each char
        is one or two ``INPUT`` events, the kernel processes the
        array in order, and target apps see them as a normal sequence.

        Args:
            text: String to type (any length).
        """
        if not text:
            return

        events: List[INPUT] = []
        for char in text:
            scancode_events = self._make_char_scancode_events(char)
            if scancode_events is not None:
                events.extend(scancode_events)
            else:
                events.extend(self._make_unicode_events(char))

        self._inject(events)
        self._log_send(f"text='{text}'")

    def replace_text(self, backspace_count: int, text: str) -> None:
        """
        Atomically select-and-replace characters with new text.

        Default path uses Shift+Left selection so the field never goes
        empty — chat apps (Slack, Teams, Discord) close their compose
        area if Backspace empties the input.  Typing the replacement
        text overwrites the selection.

        Terminals are an exception.  In ``ConsoleWindowClass`` (cmd /
        powershell / conhost), Windows Terminal, and mintty, Shift+Left
        moves the cursor instead of selecting, so the typed-letters
        prefix would survive *and* the inserted word would land at the
        new cursor position — leaving the user's original characters at
        the end of the inserted word.  For those windows we fall back
        to BackSpace + retype, which terminal line editors handle
        correctly.  (BackSpace would break Slack et al., but those
        aren't terminals.)
        """
        # The typed-replacement portion uses the same scancode-first
        # dispatch as send_text so prediction-pill insertion in
        # Blender / VirtualBox / DirectInput apps produces real
        # WM_KEYDOWN events. See send_text and _resolve_char_scancode
        # for the rationale.
        def _typed_events_for(s: str) -> List[INPUT]:
            out: List[INPUT] = []
            for char in s:
                scancode_events = self._make_char_scancode_events(char)
                if scancode_events is not None:
                    out.extend(scancode_events)
                else:
                    out.extend(self._make_unicode_events(char))
            return out

        if self._foreground_is_terminal():
            events: List[INPUT] = []
            for _ in range(backspace_count):
                events.append(self._make_key_event(VK_BACK, key_down=True))
                events.append(self._make_key_event(VK_BACK, key_down=False))
            events.extend(_typed_events_for(text))
            if events:
                self._inject(events)
            self._log_send(
                f"replace (terminal) backspace={backspace_count} text='{text}'"
            )
            return

        events = []

        if backspace_count > 0:
            # Hold Shift, press Left N times, release Shift → selects N chars
            events.append(self._make_key_event(VK_SHIFT, key_down=True))
            for _ in range(backspace_count):
                events.append(self._make_key_event(VK_LEFT, key_down=True))
                events.append(self._make_key_event(VK_LEFT, key_down=False))
            events.append(self._make_key_event(VK_SHIFT, key_down=False))

        # Typing the replacement overwrites the selection
        events.extend(_typed_events_for(text))

        self._inject(events)
        self._log_send(
            f"replace select={backspace_count} text='{text}'"
        )

    # Window classes where Shift+Left moves the cursor without
    # selecting, breaking the default replace_text path.  Add new
    # entries here as terminal emulators are encountered.
    _TERMINAL_WINDOW_CLASSES = frozenset({
        "ConsoleWindowClass",             # cmd.exe / powershell / conhost
        "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
        "mintty",                         # Git Bash / Cygwin / MSYS2
    })

    def _foreground_is_terminal(self) -> bool:
        """True iff the current foreground window is a known terminal."""
        return self._get_foreground_window_class() in self._TERMINAL_WINDOW_CLASSES

    def _get_foreground_window_class(self) -> str:
        """Win32 class name of the foreground window, or '' on failure."""
        try:
            hwnd = self._user32.GetForegroundWindow()
            if not hwnd:
                return ""
            buf = ctypes.create_unicode_buffer(256)
            n = self._user32.GetClassNameW(hwnd, buf, 256)
            if n <= 0:
                return ""
            return buf.value
        except OSError:
            return ""

    def send_combination(self, keys: List[str]) -> None:
        """
        Send a key combination (all pressed together, then released).

        Example: ``["ctrl", "shift", "s"]`` → Ctrl+Shift+S.

        Args:
            keys: Key names in press order (modifiers first).
        """
        if not keys:
            return

        events: List[INPUT] = []

        # Press all keys in order
        for key in keys:
            vk = self._resolve_vk(key)
            if vk is not None:
                events.append(self._make_key_event(vk, key_down=True))

        # Release all keys in reverse order
        for key in reversed(keys):
            vk = self._resolve_vk(key)
            if vk is not None:
                events.append(self._make_key_event(vk, key_down=False))

        self._inject(events)
        self._log_send(f"combo={keys}")

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _resolve_vk(self, key_name: str) -> Optional[int]:
        """
        Resolve a platform-neutral key name to a Windows virtual-key code.

        Checks ``_KEY_MAP`` first, then falls back to ``ord(char.upper())``
        for single alphanumeric characters (A-Z, 0-9 map directly to their
        ASCII code as VK codes).

        Args:
            key_name: Platform-neutral key name or single character.

        Returns:
            Virtual-key code, or None if unresolvable.
        """
        # Lookup in explicit map first
        if key_name in _KEY_MAP:
            return _KEY_MAP[key_name]

        # Single character: use VK code = uppercase ASCII
        if len(key_name) == 1:
            ch = key_name.upper()
            code = ord(ch)
            # A-Z (0x41-0x5A) and 0-9 (0x30-0x39) have VK = ASCII
            if (0x41 <= code <= 0x5A) or (0x30 <= code <= 0x39):
                return code

        return None

    def _resolve_char_vk(self, char: str) -> Optional[Tuple[int, bool]]:
        """Resolve a single character to a (vk, needs_shift) pair via the
        active keyboard layout.

        Used as a fallback for punctuation keys (``-``, ``=``, ``/`` ...)
        that don't have a direct ASCII→VK mapping.  Letting the OS
        translate via ``VkKeyScanW`` makes us layout-aware (US, UK,
        German, ...) without enumerating every layout's OEM codes.

        Returns:
            ``(vk, needs_shift)`` if the character is typeable on the
            current layout, else ``None``.  ``needs_shift`` is True for
            characters that require Shift on this layout (e.g. ``+``
            on US is Shift+VK_OEM_PLUS).
        """
        if len(char) != 1:
            return None
        try:
            result = self._user32.VkKeyScanW(char)
        except (OSError, ValueError):
            return None
        # VkKeyScanW returns -1 (0xFFFF as SHORT) when the char has no
        # single-keystroke mapping on this layout.
        if result == -1:
            return None
        vk = result & 0xFF
        shift_state = (result >> 8) & 0xFF
        if vk == 0xFF:
            return None
        # bit 0 = shift, bit 1 = ctrl, bit 2 = alt.  We only honour
        # shift here — a layout that needs Ctrl+Alt (AltGr) to type a
        # character is too exotic for our chord path; fall back to
        # Unicode in that case by returning None.
        if shift_state & 0b110:
            return None
        return vk, bool(shift_state & 1)

    def _make_key_event(self, vk: int, key_down: bool) -> INPUT:
        """
        Build an INPUT structure for a virtual-key press or release.

        Automatically sets ``KEYEVENTF_EXTENDEDKEY`` for navigation and
        edit keys that require it, and populates ``wScan`` with the
        scancode looked up via ``MapVirtualKeyW`` under the current
        keyboard layout.

        Populating the scancode is required for remote-desktop tools
        (TeamViewer / RDP / VNC / AnyDesk) — they forward keystrokes
        by *scancode* over the wire.  With ``wScan=0`` the remote side
        either drops the event or forwards it with broken modifier
        state, which manifests as Ctrl+V (and friends) silently failing
        when the foreground app is a remote-desktop client.  Local
        Windows apps key off ``wVk`` and ignore ``wScan``, so this is
        backwards-compatible.

        Args:
            vk: Virtual-key code.
            key_down: True for key-down, False for key-up.

        Returns:
            Populated INPUT structure.
        """
        flags = 0
        if not key_down:
            flags |= KEYEVENTF_KEYUP
        if vk in _EXTENDED_KEYS:
            flags |= KEYEVENTF_EXTENDEDKEY

        # MapVirtualKeyW returns 0 on failure (no scancode for this VK
        # under the active layout); leave wScan=0 in that case rather
        # than reject the event — local dispatch via wVk still works.
        scancode = self._user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)

        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp._input.ki.wVk = vk
        inp._input.ki.wScan = scancode
        inp._input.ki.dwFlags = flags
        inp._input.ki.time = 0
        inp._input.ki.dwExtraInfo = 0
        return inp

    def _resolve_char_scancode(
        self, char: str,
    ) -> Optional[Tuple[int, int, bool]]:
        """Resolve a character to ``(vk, scancode, needs_shift)`` for
        scancode-mode injection, or ``None`` to signal the caller to
        fall back to ``KEYEVENTF_UNICODE``.

        Why this exists
        ---------------
        ``KEYEVENTF_UNICODE`` synthesises a ``WM_KEYDOWN`` with the
        sentinel ``VK_PACKET (0xE7)`` followed by ``WM_CHAR``.  Apps
        that read raw scancodes or filter on real VKs (Blender's GHOST
        layer, VirtualBox's keyboard filter driver, DirectInput games,
        many CAD / DAW tools) ignore ``VK_PACKET`` events entirely, so
        clicked letters silently no-op there even though Notepad, chat
        apps, and browsers handle them fine.

        ``KEYEVENTF_SCANCODE`` instead tells the OS "this is a physical
        key with this scancode."  The OS looks up the VK from the
        scancode using the active layout and dispatches a normal
        ``WM_KEYDOWN(VK_X)`` plus ``WM_CHAR``.  Indistinguishable from
        a real keypress, which is why the Windows OSK uses this mode.

        When we return None
        -------------------
        - ``char`` is non-ASCII (>= U+0080).  These would need
          per-layout scancodes that may not exist.  UNICODE mode covers
          every code point, so the fallback is safe and lossless.
        - ``VkKeyScanW`` returns -1: the char has no single-keystroke
          mapping on the active layout (typing ``ñ`` on US English).
        - The layout requires AltGr (Ctrl+Alt) or a bare Ctrl modifier
          to type the char (German ``@`` is AltGr+Q).  We don't try to
          synthesise AltGr because its semantics vary across layouts.
        - The VK is a dead-key trigger on the active layout
          (``MAPVK_VK_TO_CHAR`` returns a value with bit 31 set).
          Sending the scancode would arm a dead-key composition that
          consumes the next keypress, not produce the character.
        - Shift is physically held by the user *and* this char doesn't
          need shift.  We can't safely release a key the user is
          holding; UNICODE bypasses the shift state entirely.

        Caps Lock handling
        ------------------
        ``VkKeyScanW`` returns the shift state assuming Caps Lock is
        off.  For alphabetic chars, the OS XORs Caps Lock with the
        Shift state.  We query ``GetKeyState(VK_CAPITAL)`` and flip
        ``needs_shift`` accordingly, so a clicked lowercase ``a``
        types ``a`` even when the user has the OS Caps Lock LED on.

        Args:
            char: Single character to resolve.

        Returns:
            ``(vk, scancode, needs_shift)`` tuple suitable for
            ``_make_char_scancode_events``, or ``None`` to fall back.
        """
        if not char or len(char) != 1:
            return None
        if ord(char) >= 0x80:
            return None  # non-ASCII: UNICODE handles the full range
        try:
            raw = self._user32.VkKeyScanW(char)
        except (OSError, ValueError):
            return None
        # SHORT-typed: -1 indicates "no mapping on this layout."
        if raw == -1:
            return None
        vk = raw & 0xFF
        if vk == 0 or vk == 0xFF:
            return None
        shift_state = (raw >> 8) & 0xFF
        # Bits: 0=Shift, 1=Ctrl, 2=Alt. Anything beyond Shift means
        # the layout needs a chord we don't synthesise here.
        if shift_state & 0b110:
            return None
        layout_shift = bool(shift_state & 1)

        # Dead-key probe. MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR) returns
        # the unshifted char with bit 31 set when the VK is a dead-key
        # trigger on this layout (e.g. apostrophe on US-International).
        try:
            dead_probe = self._user32.MapVirtualKeyW(vk, MAPVK_VK_TO_CHAR)
        except (OSError, ValueError):
            return None
        if dead_probe & 0x80000000:
            return None

        try:
            scancode = self._user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        except (OSError, ValueError):
            return None
        if scancode == 0:
            return None

        needs_shift = layout_shift
        # OS Caps Lock inverts shift for letters only. Digits and
        # punctuation are unaffected by Caps Lock.
        if char.isalpha():
            try:
                caps_on = bool(self._user32.GetKeyState(VK_CAPITAL) & 1)
            except (OSError, ValueError):
                caps_on = False
            if caps_on:
                needs_shift = not needs_shift

        # If shift is currently physically held and we don't want
        # shift for this char, we'd produce the wrong character.
        # We can't release the user's physical shift, so bail to
        # UNICODE which bypasses shift state entirely.
        try:
            shift_held = bool(
                self._user32.GetAsyncKeyState(VK_SHIFT) & 0x8000,
            )
        except (OSError, ValueError):
            shift_held = False
        if shift_held and not needs_shift:
            return None

        return vk, scancode, needs_shift

    def _make_char_scancode_events(self, char: str) -> Optional[List[INPUT]]:
        """Build scancode-mode INPUT events for a character, or
        return ``None`` to signal the caller should fall back to
        ``_make_unicode_events``.

        Wraps with a synthetic Shift press/release when the char needs
        shift but shift isn't already held.  Skips the wrap when shift
        is already held (whether by the user or by the OSK's sticky
        modifier state) since adding a redundant press would later be
        unbalanced by our release.

        Returns ``None`` for the same conditions as
        ``_resolve_char_scancode``.
        """
        resolved = self._resolve_char_scancode(char)
        if resolved is None:
            return None
        vk, scancode, needs_shift = resolved

        try:
            shift_already_held = bool(
                self._user32.GetAsyncKeyState(VK_SHIFT) & 0x8000,
            )
        except (OSError, ValueError):
            shift_already_held = False

        events: List[INPUT] = []
        wrap_with_shift = needs_shift and not shift_already_held
        if wrap_with_shift:
            try:
                shift_sc = self._user32.MapVirtualKeyW(
                    VK_SHIFT, MAPVK_VK_TO_VSC,
                )
            except (OSError, ValueError):
                shift_sc = 0
            # If we couldn't resolve a shift scancode, bail to UNICODE
            # rather than send half the events.
            if shift_sc == 0:
                return None
            events.append(self._make_scancode_event(shift_sc, key_down=True))

        events.append(self._make_scancode_event(scancode, key_down=True))
        events.append(self._make_scancode_event(scancode, key_down=False))

        if wrap_with_shift:
            try:
                shift_sc = self._user32.MapVirtualKeyW(
                    VK_SHIFT, MAPVK_VK_TO_VSC,
                )
            except (OSError, ValueError):
                shift_sc = 0
            if shift_sc == 0:
                # We already pressed shift down; without a release the
                # OS would see a stuck shift. Synthesise a VK-mode
                # release as a safety net (matches what
                # release_modifier does).
                events.append(self._make_key_event(VK_SHIFT, key_down=False))
            else:
                events.append(self._make_scancode_event(shift_sc, key_down=False))

        return events

    def _make_scancode_event(self, scancode: int, key_down: bool) -> INPUT:
        """Build a pure-scancode INPUT event (``wVk=0``,
        ``KEYEVENTF_SCANCODE`` set).  Used by the character-typing
        path where we want the OS to derive the VK from the scancode,
        same as a physical keypress.
        """
        flags = KEYEVENTF_SCANCODE
        if not key_down:
            flags |= KEYEVENTF_KEYUP
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp._input.ki.wVk = 0
        inp._input.ki.wScan = scancode
        inp._input.ki.dwFlags = flags
        inp._input.ki.time = 0
        inp._input.ki.dwExtraInfo = 0
        return inp

    def _make_unicode_events(self, char: str) -> List[INPUT]:
        """
        Build INPUT structures for a Unicode character (key-down + key-up).

        Uses ``KEYEVENTF_UNICODE`` with the character's UTF-16 code point
        in ``wScan``.  For characters outside the Basic Multilingual Plane
        (code point > 0xFFFF), sends a surrogate pair.

        This is the **fallback** path: see ``_resolve_char_scancode``
        for why we prefer SCANCODE mode for ASCII input. UNICODE is
        used when the char is non-ASCII, has no layout mapping, or
        needs a chord (AltGr) we can't safely synthesise.

        Args:
            char: Single Unicode character.

        Returns:
            List of 2 (or 4 for surrogate pairs) INPUT structures.
        """
        events = []
        code_point = ord(char)

        if code_point <= 0xFFFF:
            # BMP character — single pair
            for key_up in (False, True):
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp._input.ki.wVk = 0
                inp._input.ki.wScan = code_point
                inp._input.ki.dwFlags = (
                    KEYEVENTF_UNICODE
                    | (KEYEVENTF_KEYUP if key_up else 0)
                )
                inp._input.ki.time = 0
                inp._input.ki.dwExtraInfo = 0
                events.append(inp)
        else:
            # Supplementary character — UTF-16 surrogate pair
            high = 0xD800 + ((code_point - 0x10000) >> 10)
            low = 0xDC00 + ((code_point - 0x10000) & 0x3FF)
            for surrogate in (high, low):
                for key_up in (False, True):
                    inp = INPUT()
                    inp.type = INPUT_KEYBOARD
                    inp._input.ki.wVk = 0
                    inp._input.ki.wScan = surrogate
                    inp._input.ki.dwFlags = (
                        KEYEVENTF_UNICODE
                        | (KEYEVENTF_KEYUP if key_up else 0)
                    )
                    inp._input.ki.time = 0
                    inp._input.ki.dwExtraInfo = 0
                    events.append(inp)

        return events

    def hold_modifier(self, key_name: str) -> None:
        """Send a modifier key-down so it stays held at the OS level."""
        vk = _KEY_MAP.get(key_name)
        if vk is not None:
            self._inject([self._make_key_event(vk, key_down=True)])
            self._log_send(f"hold modifier {key_name} (vk=0x{vk:02X})")

    def release_modifier(self, key_name: str) -> None:
        """Send a modifier key-up to release a held modifier."""
        vk = _KEY_MAP.get(key_name)
        if vk is not None:
            self._inject([self._make_key_event(vk, key_down=False)])
            self._log_send(f"release modifier {key_name} (vk=0x{vk:02X})")

    def _inject(self, events: List[INPUT]) -> None:
        """
        Call ``SendInput`` to inject an array of INPUT events atomically.

        Args:
            events: List of INPUT structures to inject.

        Raises:
            Logs a warning if SendInput returns fewer events than expected
            (indicates a permissions issue or input blocked).
        """
        if not events:
            return

        n = len(events)
        arr = (INPUT * n)(*events)
        sent = self._send_input(n, arr, ctypes.sizeof(INPUT))

        if sent != n:
            error = ctypes.get_last_error()
            _logger.warning(
                "SendInput injected %d/%d events (error=%d). "
                "This may indicate insufficient privileges — consider "
                "UIAccess or running as admin.",
                sent, n, error,
            )

    @staticmethod
    def _check_ui_access() -> bool:
        """
        Check if the current process has UIAccess privileges.

        UIAccess allows sending input to elevated (high-integrity) windows
        without running as administrator.  Requires:

        1. A ``<requestedExecutionLevel uiAccess="true"/>`` manifest.
        2. The binary must be EV code-signed.
        3. The binary must reside in a secure location (``Program Files``
           or ``Windows\\System32``).

        Returns:
            True if UIAccess is active.
        """
        try:
            TOKEN_QUERY = 0x0008
            TokenUIAccess = 26

            kernel32 = ctypes.windll.kernel32
            advapi32 = ctypes.windll.advapi32

            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(),
                TOKEN_QUERY,
                ctypes.byref(token),
            ):
                return False

            ui_access = wintypes.DWORD(0)
            ret_len = wintypes.DWORD(0)
            result = advapi32.GetTokenInformation(
                token,
                TokenUIAccess,
                ctypes.byref(ui_access),
                ctypes.sizeof(ui_access),
                ctypes.byref(ret_len),
            )
            kernel32.CloseHandle(token)
            return bool(result and ui_access.value)
        except Exception:
            return False


# ====================================================================== #
#  Windows Shortcut Helpers
# ====================================================================== #


def create_shortcut(
    shortcut_path: str,
    target_path: str,
    description: str = "",
    icon_path: str = "",
) -> bool:
    """
    Create a Windows ``.lnk`` shortcut file using COM automation.

    Uses ``WScript.Shell`` via ``comtypes`` / ``win32com`` if available,
    falling back to PowerShell if not.

    Args:
        shortcut_path: Full path for the ``.lnk`` file to create.
        target_path: Path to the executable the shortcut points to.
        description: Optional tooltip description.
        icon_path: Optional path to the icon (``.ico`` or ``.exe``).

    Returns:
        True if the shortcut was created successfully.
    """
    _logger.info("Creating shortcut: %s → %s", shortcut_path, target_path)

    # Try PowerShell (always available, no extra deps)
    try:
        # Escape for PowerShell double-quoted strings
        def _ps_escape(s: str) -> str:
            return s.replace('`', '``').replace('"', '""').replace('$', '`$')

        ps_script = (
            f'$ws = New-Object -ComObject WScript.Shell; '
            f'$sc = $ws.CreateShortcut("{_ps_escape(shortcut_path)}"); '
            f'$sc.TargetPath = "{_ps_escape(target_path)}"; '
        )
        if description:
            ps_script += f'$sc.Description = "{_ps_escape(description)}"; '
        if icon_path:
            ps_script += f'$sc.IconLocation = "{_ps_escape(icon_path)}"; '
        ps_script += '$sc.Save()'

        import subprocess
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=10,
            # Suppress the PowerShell console window during shortcut
            # creation; otherwise a cmd window flashes (and on GUI-only
            # hosts can stick around) every time we touch the Start Menu.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            _logger.info("Shortcut created: %s", shortcut_path)
            return True
        else:
            _logger.warning("PowerShell shortcut creation failed: %s", result.stderr)
            return False
    except Exception as e:
        _logger.error("Failed to create shortcut: %s", e)
        return False


def add_to_startup(exe_path: str, app_name: str = "Alpha-OSK") -> bool:
    """
    Add Alpha-OSK to the Windows Startup folder so it launches on login.

    Creates a ``.lnk`` shortcut in::

        %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\

    Args:
        exe_path: Full path to ``alpha-osk.exe``.
        app_name: Display name for the shortcut.

    Returns:
        True if the startup shortcut was created.
    """
    import os
    startup_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    )
    shortcut = os.path.join(startup_dir, f"{app_name}.lnk")
    return create_shortcut(shortcut, exe_path, description=app_name)


def remove_from_startup(app_name: str = "Alpha-OSK") -> bool:
    """
    Remove Alpha-OSK from the Windows Startup folder.

    Args:
        app_name: Display name used when adding the shortcut.

    Returns:
        True if the shortcut was removed (or didn't exist).
    """
    import os
    startup_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup",
    )
    shortcut = os.path.join(startup_dir, f"{app_name}.lnk")
    try:
        if os.path.exists(shortcut):
            os.remove(shortcut)
            _logger.info("Removed startup shortcut: %s", shortcut)
        return True
    except Exception as e:
        _logger.error("Failed to remove startup shortcut: %s", e)
        return False


def create_start_menu_shortcut(
    exe_path: str,
    app_name: str = "Alpha-OSK",
) -> bool:
    """
    Create a Start Menu shortcut for Alpha-OSK.

    Creates::

        %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Alpha-OSK\\Alpha-OSK.lnk

    Args:
        exe_path: Full path to ``alpha-osk.exe``.
        app_name: Display name and folder name.

    Returns:
        True if the shortcut was created.
    """
    import os
    menu_dir = os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs", app_name,
    )
    os.makedirs(menu_dir, exist_ok=True)
    shortcut = os.path.join(menu_dir, f"{app_name}.lnk")
    return create_shortcut(shortcut, exe_path, description=app_name)


def create_desktop_shortcut(
    exe_path: str,
    app_name: str = "Alpha-OSK",
) -> bool:
    """
    Create a Desktop shortcut for Alpha-OSK.

    Args:
        exe_path: Full path to ``alpha-osk.exe``.
        app_name: Display name for the shortcut.

    Returns:
        True if the shortcut was created.
    """
    import os
    desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    shortcut = os.path.join(desktop, f"{app_name}.lnk")
    return create_shortcut(shortcut, exe_path, description=app_name)
