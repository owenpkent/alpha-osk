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

Two injection modes are used:

1. **Virtual-Key mode** (``send_key``, ``send_combination``):
   Sends a ``KEYBDINPUT`` with a virtual-key code (``wVk``).  Used for
   special keys (Backspace, F-keys, modifiers, arrows, etc.) and for
   modifier+key combos (Ctrl+C, Alt+F4, etc.).

2. **Unicode mode** (``send_text``):
   Sends a ``KEYBDINPUT`` with ``KEYEVENTF_UNICODE`` and the character's
   UTF-16 code point in ``wScan``.  This handles the full Unicode range
   without needing to know the user's keyboard layout — emoji, accented
   characters, CJK, everything just works.

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
``docs/WINDOWS.md`` for signing instructions.

Virtual-Key Code Reference
--------------------------
Full list: https://learn.microsoft.com/en-us/windows/win32/inputdev/virtual-key-codes

Dependencies
------------
None beyond Python's standard library (``ctypes``).

See Also
--------
- ``base.py`` — abstract interface this class implements.
- ``docs/PLATFORM_ARCHITECTURE.md`` — design rationale.
- ``docs/WINDOWS.md`` — Windows-specific setup and signing guide.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
from typing import Dict, List, Optional

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
        dwExtraInfo: Extra info pointer (typically 0).
    """
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
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
        modifiers = modifiers or []
        events: List[INPUT] = []

        # Press modifiers
        for mod in modifiers:
            vk = _KEY_MAP.get(mod)
            if vk is not None:
                events.append(self._make_key_event(vk, key_down=True))

        # Press + release action key
        vk = self._resolve_vk(key_name)
        if vk is not None:
            events.append(self._make_key_event(vk, key_down=True))
            events.append(self._make_key_event(vk, key_down=False))
        else:
            # Fallback: treat as single Unicode character
            if len(key_name) == 1:
                events.extend(self._make_unicode_events(key_name))
            else:
                _logger.warning("Unknown key name: %s", key_name)
                return

        # Release modifiers (reverse order)
        for mod in reversed(modifiers):
            vk = _KEY_MAP.get(mod)
            if vk is not None:
                events.append(self._make_key_event(vk, key_down=False))

        self._inject(events)
        self._log_send(
            f"key={key_name}"
            + (f" mods={modifiers}" if modifiers else "")
        )

    def send_text(self, text: str) -> None:
        """
        Type a Unicode string using ``KEYEVENTF_UNICODE``.

        Each character is sent as a key-down + key-up pair with its
        UTF-16 code point in ``wScan``.  This bypasses the keyboard
        layout entirely — any Unicode character works, including emoji.

        Args:
            text: String to type (any length).
        """
        if not text:
            return

        events: List[INPUT] = []
        for char in text:
            events.extend(self._make_unicode_events(char))

        self._inject(events)
        self._log_send(f"text='{text}'")

    def replace_text(self, backspace_count: int, text: str) -> None:
        """
        Atomically erase characters then type replacement text.

        Builds all backspace key-down/up pairs and Unicode character
        events into a **single** ``SendInput`` call so the target
        application's input queue sees the whole operation as one burst
        with no gaps for other events to interleave.

        Args:
            backspace_count: Number of ``Backspace`` presses to send.
            text: Replacement string to type after deletions.
        """
        events: List[INPUT] = []

        for _ in range(backspace_count):
            events.append(self._make_key_event(VK_BACK, key_down=True))
            events.append(self._make_key_event(VK_BACK, key_down=False))

        for char in text:
            events.extend(self._make_unicode_events(char))

        self._inject(events)
        self._log_send(
            f"replace backspaces={backspace_count} text='{text}'"
        )

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

    def _make_key_event(self, vk: int, key_down: bool) -> INPUT:
        """
        Build an INPUT structure for a virtual-key press or release.

        Automatically sets ``KEYEVENTF_EXTENDEDKEY`` for navigation and
        edit keys that require it.

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

        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp._input.ki.wVk = vk
        inp._input.ki.wScan = 0
        inp._input.ki.dwFlags = flags
        inp._input.ki.time = 0
        inp._input.ki.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))
        return inp

    def _make_unicode_events(self, char: str) -> List[INPUT]:
        """
        Build INPUT structures for a Unicode character (key-down + key-up).

        Uses ``KEYEVENTF_UNICODE`` with the character's UTF-16 code point
        in ``wScan``.  For characters outside the Basic Multilingual Plane
        (code point > 0xFFFF), sends a surrogate pair.

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
                inp._input.ki.dwExtraInfo = ctypes.pointer(ctypes.c_ulong(0))
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
                    inp._input.ki.dwExtraInfo = ctypes.pointer(
                        ctypes.c_ulong(0)
                    )
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
