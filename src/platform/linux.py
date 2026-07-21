"""
Linux Key Synthesizer
======================

Implements key synthesis for Linux using **xdotool** (X11) or **ydotool**
(Wayland) as subprocess backends.

Backend Selection
-----------------
1. If ``xdotool`` is on ``$PATH`` and ``$WAYLAND_DISPLAY`` is *not* set →
   use xdotool (X11).
2. If ``ydotool`` is on ``$PATH`` → use ydotool (Wayland-compatible).
3. Otherwise → :meth:`is_available` returns False and all send methods
   log a warning and no-op.

Key Name Mapping
----------------
The bridge layer uses platform-neutral names (``"BackSpace"``, ``"Return"``,
``"F1"``, etc.).  These happen to match xdotool's X11 keysym names exactly,
so the Linux backend's key map is mostly pass-through.  ydotool uses
different keycode integers — those are mapped in ``_YDOTOOL_KEY_MAP``.

Dependencies
------------
- ``xdotool``:  ``sudo apt install xdotool``
- ``ydotool``:  ``sudo apt install ydotool`` (needs ``ydotoold`` running)

See Also
--------
- ``base.py`` — abstract interface this class implements.
- ``docs/architecture/PLATFORM_ARCHITECTURE.md`` — design rationale.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Dict, List, Optional

from .base import KeySynthesizerBase

_logger = logging.getLogger("LinuxKeySynthesizer")


# Map punctuation characters to their X11 keysym names.  xdotool's chord
# syntax (``ctrl+key``) uses ``+`` as the modifier separator and parses
# the action key as a keysym name — passing the literal ``-`` builds an
# ambiguous ``ctrl+-`` that doesn't trigger app shortcuts.  Mapping
# ``-`` → ``minus`` produces the canonical ``ctrl+minus`` form which
# browsers/editors handle as Ctrl+OEM_MINUS (zoom out, etc.).  Same
# story for ``=`` → ``equal`` and the rest of the symbol row.  Letters
# and digits don't need translation — xdotool accepts ``a`` and ``1``
# verbatim — so the map only carries the characters that conflict with
# the chord parser.
_CHAR_TO_KEYSYM: Dict[str, str] = {
    # Unshifted symbol row (US layout)
    "-": "minus",
    "=": "equal",
    "[": "bracketleft",
    "]": "bracketright",
    "\\": "backslash",
    ";": "semicolon",
    "'": "apostrophe",
    ",": "comma",
    ".": "period",
    "/": "slash",
    "`": "grave",
    " ": "space",
    # Shifted variants — the OSK upper layer routes these through
    # send_key when a non-shift modifier is also active (e.g. user has
    # Ctrl held and Shift toggled, then taps ``1`` which displays ``!``).
    "_": "underscore",
    "+": "plus",
    "{": "braceleft",
    "}": "braceright",
    "|": "bar",
    ":": "colon",
    '"': "quotedbl",
    "<": "less",
    ">": "greater",
    "?": "question",
    "~": "asciitilde",
    "!": "exclam",
    "@": "at",
    "#": "numbersign",
    "$": "dollar",
    "%": "percent",
    "^": "asciicircum",
    "&": "ampersand",
    "*": "asterisk",
    "(": "parenleft",
    ")": "parenright",
}


def _run(cmd: List[str]) -> None:
    """Run a key-synthesis command synchronously.

    Modifier events MUST be ordered correctly relative to the key events
    they wrap — if ``keydown ctrl`` / ``keyup ctrl`` are fired as
    ``subprocess.Popen`` with no wait, they race each other and the
    target app can see the ``keyup`` first, leaving Ctrl stuck held.
    ``subprocess.run`` blocks until the tool exits (~5–15 ms), which is
    negligible for key input and guarantees event ordering.
    """
    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception as exc:
        _logger.error("Command failed %s: %s", cmd, exc)


class LinuxKeySynthesizer(KeySynthesizerBase):
    """
    Linux key synthesis via xdotool (X11) or ydotool (Wayland).

    Attributes:
        _tool: Name of the detected tool (``"xdotool"`` or ``"ydotool"``),
               or ``None`` if neither is available.
    """

    def __init__(self) -> None:
        self._tool = self._detect_tool()
        if self._tool:
            _logger.info("Linux key synthesizer ready: %s", self._tool)
        else:
            _logger.warning(
                "No key synthesis tool found. "
                "Install xdotool (X11) or ydotool (Wayland)."
            )

    # ------------------------------------------------------------------ #
    #  Detection
    # ------------------------------------------------------------------ #

    @staticmethod
    def _detect_tool() -> Optional[str]:
        """
        Detect the best available key synthesis tool.

        Prefers xdotool on X11, ydotool on Wayland.

        Returns:
            ``"xdotool"``, ``"ydotool"``, or ``None``.
        """
        is_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))

        if is_wayland:
            # Prefer ydotool on Wayland
            if shutil.which("ydotool"):
                return "ydotool"
            if shutil.which("xdotool"):
                _logger.warning(
                    "Wayland detected but only xdotool found. "
                    "Some features may not work. Install ydotool."
                )
                return "xdotool"
        else:
            # X11 — prefer xdotool
            if shutil.which("xdotool"):
                return "xdotool"
            if shutil.which("ydotool"):
                return "ydotool"

        return None

    # ------------------------------------------------------------------ #
    #  Interface implementation
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        """True if xdotool or ydotool is on $PATH."""
        return self._tool is not None

    def backend_name(self) -> str:
        """Return ``"xdotool"``, ``"ydotool"``, or ``"none"``."""
        return self._tool or "none"

    def send_key(
        self,
        key_name: str,
        modifiers: Optional[List[str]] = None,
    ) -> None:
        """
        Send a single key event, optionally with modifier keys.

        For xdotool the modifiers are joined with ``+`` to form a chord
        string (e.g. ``ctrl+shift+c``).

        Args:
            key_name: Platform-neutral key name (xdotool keysym).
            modifiers: Optional list of ``"ctrl"``, ``"alt"``, ``"shift"``,
                       ``"win"`` strings.
        """
        if not self._tool:
            _logger.warning("No synth tool — cannot send key: %s", key_name)
            return

        modifiers = modifiers or []
        # Map "win" → "super" for xdotool
        mapped_mods = [("super" if m == "win" else m) for m in modifiers]

        # Translate literal punctuation to the X11 keysym name so the
        # chord parser doesn't trip on the ``+`` separator (``ctrl+-``
        # is malformed; ``ctrl+minus`` is canonical).  Letters/digits
        # pass through unchanged.
        action_key = _CHAR_TO_KEYSYM.get(key_name, key_name)

        if self._tool == "xdotool":
            if mapped_mods:
                combo = "+".join(mapped_mods + [action_key])
                self._log_send(f"xdotool key {combo}")
                _run(["xdotool", "key", "--clearmodifiers", combo])
            else:
                self._log_send(f"xdotool key {action_key}")
                _run(["xdotool", "key", "--clearmodifiers", action_key])
        elif self._tool == "ydotool":
            self._log_send(f"ydotool key {action_key}")
            _run(["ydotool", "key", action_key])

    def send_text(self, text: str) -> None:
        """
        Type a string of text using ``xdotool type``.

        Falls back to sending individual key events on ydotool.

        Args:
            text: The Unicode string to type.
        """
        if not self._tool:
            return

        if self._tool == "xdotool":
            self._log_send(f"xdotool type '{text}'")
            _run(["xdotool", "type", "--clearmodifiers", text])
        elif self._tool == "ydotool":
            self._log_send(f"ydotool type '{text}'")
            _run(["ydotool", "type", text])

    def hold_modifier(self, key_name: str) -> None:
        """Send a modifier key-down so it stays held at the OS level.

        Super/Meta is deliberately *never* held on Linux. While Super is
        held, X11/Wayland window managers grab the pointer for window
        move/resize gestures (Super+drag = move, Super+right-button =
        resize), so every mouse click — including clicks on the OSK's own
        keys — is swallowed as a WM gesture instead of reaching the
        keyboard. The user then can't tap Win again to release it and is
        stuck. We skip the hold; Super+<key> combos (Win+D, Win+L,
        Win+arrow) still work because send_key() emits them as an atomic
        ``xdotool key super+<key>`` chord. (Holding Super buys nothing
        anyway — you can't Super+drag from an on-screen keyboard.)
        """
        if not self._tool:
            return
        if key_name in ("win", "super"):
            return
        mapped = "super" if key_name == "win" else key_name
        if self._tool == "xdotool":
            self._log_send(f"xdotool keydown {mapped}")
            _run(["xdotool", "keydown", mapped])
        elif self._tool == "ydotool":
            self._log_send(f"ydotool key --key-down {mapped}")
            _run(["ydotool", "key", "--key-down", mapped])

    def release_modifier(self, key_name: str) -> None:
        """Send a modifier key-up to release a held modifier.

        Kept functional for ``win``/``super`` even though ``hold_modifier``
        never holds it: issuing a ``keyup super`` is a harmless no-op when
        Super isn't down, and acts as defensive cleanup if Super ever got
        stuck (e.g. a physical Super key, or an external grab).
        """
        if not self._tool:
            return
        mapped = "super" if key_name == "win" else key_name
        if self._tool == "xdotool":
            self._log_send(f"xdotool keyup {mapped}")
            _run(["xdotool", "keyup", mapped])
        elif self._tool == "ydotool":
            self._log_send(f"ydotool key --key-up {mapped}")
            _run(["ydotool", "key", "--key-up", mapped])

    def reset_modifier_state(self) -> None:
        """Release Ctrl/Alt/Shift/Super at the X server / Wayland compositor.

        Safe to call at startup; each ``keyup`` is a no-op if the
        modifier wasn't held. See the base class docstring for why you
        must NOT call this during interactive typing.
        """
        if not self._tool:
            return
        modifiers = ("ctrl", "alt", "shift", "super")
        _logger.info("Resetting OS modifier state (defensive keyup)")
        for mod in modifiers:
            if self._tool == "xdotool":
                _run(["xdotool", "keyup", mod])
            elif self._tool == "ydotool":
                _run(["ydotool", "key", "--key-up", mod])

    def send_combination(self, keys: List[str]) -> None:
        """
        Send a multi-key chord (e.g. Ctrl+Alt+Delete).

        Args:
            keys: Ordered list of key names. Modifiers first, action key
                  last.
        """
        if not keys:
            return
        # Last key is the action key; everything before is a modifier
        *modifiers, action_key = keys
        self.send_key(action_key, modifiers=modifiers if modifiers else None)

    def replace_text(self, backspace_count: int, text: str) -> None:
        """Atomically select-and-replace *backspace_count* chars with *text*.

        Mirrors the Windows SendInput path: select N characters with
        Shift+Left, then type the replacement so the selection is
        overwritten in one motion.  Using Shift+Left instead of
        Backspace keeps the input non-empty — Slack/Teams close their
        compose area when a field is emptied between keystrokes.

        xdotool: a single ``xdotool key`` invocation runs N ``shift+Left``
        chords in order; each chord is applied-then-released atomically
        by xdotool, so no stray modifier can leak out if the process is
        killed mid-call.  Shelling out twice (one ``key`` + one ``type``)
        is still race-free because `_run` blocks on each command.

        ydotool: there is no chord-chain syntax, so we frame the N Left
        presses with explicit ``--key-down shift`` / ``--key-up shift``.
        If the process dies between the two, shift would stick — not
        worse than the pre-existing sticky-modifier hold path.
        """
        if not self._tool:
            return
        if backspace_count <= 0:
            self.send_text(text)
            return

        if self._tool == "xdotool":
            chords = ["shift+Left"] * backspace_count
            self._log_send(
                f"xdotool key shift+Left×{backspace_count} + type '{text}'"
            )
            _run(["xdotool", "key", "--clearmodifiers", *chords])
            if text:
                _run(["xdotool", "type", "--clearmodifiers", text])
        elif self._tool == "ydotool":
            self._log_send(
                f"ydotool shift+Left×{backspace_count} + type '{text}'"
            )
            _run(["ydotool", "key", "--key-down", "shift"])
            for _ in range(backspace_count):
                _run(["ydotool", "key", "Left"])
            _run(["ydotool", "key", "--key-up", "shift"])
            if text:
                _run(["ydotool", "type", text])
