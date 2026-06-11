"""
Abstract Base Class for Key Synthesis
======================================

Defines the interface that every platform-specific key synthesizer must
implement.  The bridge layer (``keyboard_bridge.py``) programs against
this interface so it never has to know whether it's running on Linux or
Windows.

Class Hierarchy::

    KeySynthesizerBase  (ABC — this file)
    ├── LinuxKeySynthesizer   (linux.py  — xdotool / ydotool)
    └── WindowsKeySynthesizer (windows.py — Win32 SendInput)

Design Notes
------------
- Every public method that sends input is **fire-and-forget**: it returns
  immediately and never blocks the Qt event loop.
- ``send_key`` accepts a *platform-neutral* key name (e.g. ``"Return"``,
  ``"BackSpace"``, ``"F5"``).  Each backend maps these to OS-specific
  codes in its own ``_KEY_MAP``.
- Modifier state (Ctrl, Alt, Shift, Win/Super) is tracked by the caller
  (``KeyboardBridge``) and passed into ``send_key`` / ``send_combination``
  so the synthesizer itself is stateless.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List, Optional

_logger = logging.getLogger("KeySynthesizer")


class KeySynthesizerBase(ABC):
    """
    Abstract interface for sending synthetic keyboard input to the OS.

    Subclasses must implement:

    - :meth:`is_available` — can we actually send keys?
    - :meth:`send_key` — inject a single keystroke (with optional modifiers).
    - :meth:`send_text` — inject an arbitrary Unicode string.
    - :meth:`send_combination` — inject a key chord (e.g. Ctrl+Shift+S).
    - :meth:`backend_name` — human-readable name for logs / UI.
    """

    # ------------------------------------------------------------------ #
    #  Availability
    # ------------------------------------------------------------------ #

    @abstractmethod
    def is_available(self) -> bool:
        """
        Return True if the backend is ready to send input.

        On Linux this checks for ``xdotool`` / ``ydotool`` on ``$PATH``.
        On Windows this always returns True (SendInput is a system call).
        """
        ...

    @abstractmethod
    def backend_name(self) -> str:
        """
        Return a short human-readable identifier for the active backend.

        Examples: ``"xdotool"``, ``"ydotool"``, ``"SendInput"``,
        ``"SendInput+UIAccess"``.
        """
        ...

    # ------------------------------------------------------------------ #
    #  Key injection
    # ------------------------------------------------------------------ #

    @abstractmethod
    def send_key(
        self,
        key_name: str,
        modifiers: Optional[List[str]] = None,
        hold_seconds: float = 0.0,
    ) -> None:
        """
        Inject a single keystroke, optionally modified.

        Args:
            key_name:
                Platform-neutral key name.  Character keys are lowercase
                letters (``"a"``–``"z"``); special keys use Qt/Xdotool
                naming (``"BackSpace"``, ``"Return"``, ``"F1"``, etc.).
            modifiers:
                Optional list of active modifier names.  Valid values are
                ``"ctrl"``, ``"alt"``, ``"shift"``, ``"win"`` (Super on
                Linux, Win on Windows).  The backend builds the correct
                key combination.
            hold_seconds:
                How long to hold the action key down between its key-down
                and key-up events, in seconds.  The default ``0.0`` sends
                down+up in one atomic batch (fastest, correct for normal
                apps).  A small positive value (~0.05) keeps the key
                physically held across one or more game render frames so
                games that *poll* keyboard state per frame (DirectInput /
                Raw Input / ``GetAsyncKeyState``) actually observe the
                press.  A zero-gap down/up can fall entirely between two
                polls and be missed.  See the bridge's game-compat path.
                Backends may ignore this if they can't hold a key.

        Example::

            synth.send_key("c", modifiers=["ctrl"])   # Ctrl+C
            synth.send_key("BackSpace")                # plain Backspace
        """
        ...

    @abstractmethod
    def send_text(self, text: str) -> None:
        """
        Inject a string of Unicode text character-by-character.

        This is the fast path for normal typing where no modifiers are
        involved.  Backends should prefer the OS "type string" API when
        available (``xdotool type``, or ``SendInput`` with
        ``KEYEVENTF_UNICODE``).

        Args:
            text: Arbitrary Unicode string (may be one character).
        """
        ...

    @abstractmethod
    def send_combination(self, keys: List[str]) -> None:
        """
        Inject a multi-key chord (all keys pressed together, then released).

        Args:
            keys:
                Ordered list of key names forming the chord.  Modifiers
                first, action key last — e.g. ``["ctrl", "shift", "s"]``.

        Example::

            synth.send_combination(["ctrl", "alt", "Delete"])
        """
        ...

    # ------------------------------------------------------------------ #
    #  Modifier hold / release
    # ------------------------------------------------------------------ #

    def hold_modifier(self, key_name: str) -> None:
        """
        Send a modifier key-down event (Ctrl, Alt, Shift, Win/Super).

        The modifier stays held at the OS level until :meth:`release_modifier`
        is called.  This allows modifier+mouse-click combinations (e.g.
        Ctrl+click to open a hyperlink).

        The default implementation is a no-op — backends that support
        independent key-down / key-up override this.

        Args:
            key_name: Modifier name (``"ctrl"``, ``"alt"``, ``"shift"``,
                      ``"win"``).
        """

    def release_modifier(self, key_name: str) -> None:
        """
        Send a modifier key-up event, releasing a previously held modifier.

        Args:
            key_name: Modifier name (same as :meth:`hold_modifier`).
        """

    def reset_modifier_state(self) -> None:
        """
        Defensively release Ctrl, Alt, Shift, and Super/Win at the OS level.

        Intended for startup — if a previous alpha-osk instance crashed
        or was killed while a modifier was "active" (sticky), that
        ``keydown`` may still be pinned at the window server with no
        matching ``keyup``. Clearing them here lets a fresh session
        start from a known state.

        Safe to call whenever the app is certain no user modifier is
        *intentionally* held (e.g. app init). Do NOT call during
        interactive use — it would release a physically-held modifier.

        The default implementation is a no-op. Backends override if
        stray OS-level modifier state is possible on their platform.
        """

    def replace_text(self, backspace_count: int, text: str) -> None:
        """
        Atomically erase *backspace_count* characters then type *text*.

        Used by prediction selection to replace a partially-typed word with
        the full prediction.  Backends that can batch input events into a
        single OS call should override this to avoid race conditions with
        the target application's input queue.

        The default implementation falls back to sequential calls.

        Args:
            backspace_count: Number of ``Backspace`` key presses to send.
            text: Replacement text to type after the deletions.
        """
        for _ in range(backspace_count):
            self.send_key("BackSpace")
        self.send_text(text)

    # ------------------------------------------------------------------ #
    #  Helpers available to all backends
    # ------------------------------------------------------------------ #

    def _log_send(self, description: str) -> None:
        """Convenience debug logger used by subclasses."""
        _logger.debug("SEND: %s", description)
