"""A fake operating system for the keyboard to type into.

``KeyboardBridge`` is tested almost everywhere against a ``MagicMock``
synthesiser, which records calls and believes nothing about them.  That is
the right tool for "did the bridge call ``send_text``" and the wrong one for
the question this project's modifier bugs keep asking: *what did the
operating system end up doing*.  A standing Shift hold rewrites a pill from
"Hello" to "HELLO", a standing Ctrl turns every character of a snippet into
a chord, and neither is visible to a mock, because a mock has no state for a
hold to live in.

This is the equivalent of PowerToys' ``MockedInput``: a fake OS with a
key-state table, where every injected event is applied to the table and a
test asserts on the resulting *state* rather than on the call log.  The
fidelity is the point.  If the fake were lenient about a held modifier, a
test built on it would pass against the very bug ``_without_held_modifiers``
exists to prevent, and prove nothing.  ``tests/test_fake_os_keyboard.py``
pins the fake's own behaviour for that reason, each case paired with its
inverse.

Truthfulness rules, all taken from how ``WindowsKeySynthesizer`` behaves:

- ``hold_modifier`` / ``release_modifier`` edit a ``held`` set.  ``win`` is
  holdable (the Linux synthesiser refuses it; the bridge's invariants are the
  Windows ones).
- ``send_key`` is atomic: modifiers passed in that are not already held go
  down and up around the key, and a modifier that *was* held is left held
  (the "do not wrap an already-held modifier" synth invariant).
- Printable ASCII goes the scancode route, so a standing Shift shifts it
  (``a`` -> ``A``, ``1`` -> ``!``) and a standing Ctrl, Alt or Win makes it a
  chord instead of text.  Anything above ``U+007F`` goes the
  ``KEYEVENTF_UNICODE`` route, which ignores Shift but still cannot type
  under a chord modifier.
- ``replace_text`` is a Shift+Left selection followed by typing, so under a
  standing Ctrl, Alt or Win it is a chord and the text is left alone; that is
  the destructive case.
"""

from __future__ import annotations

from typing import Callable, Dict, FrozenSet, List, NamedTuple, Optional, Sequence, Set, Tuple

from src.platform.base import KeySynthesizerBase

MODIFIER_NAMES: Tuple[str, ...] = ("shift", "ctrl", "alt", "win")

#: Modifiers that turn a keystroke into a chord.  Shift is absent on purpose:
#: it changes *which* character arrives, it does not stop one arriving.
CHORD_MODIFIERS: FrozenSet[str] = frozenset({"ctrl", "alt", "win"})

#: US layout shifted forms of the non-letter scancode keys.
SHIFTED: Dict[str, str] = {
    **dict(zip("1234567890", "!@#$%^&*()")),
    **dict(zip("`-=[]\\;',./", '~_+{}|:"<>?')),
}

#: Named keys that insert text when no chord modifier is down.
_TEXT_KEYS: Dict[str, str] = {"space": " ", "Return": "\n", "Tab": "\t"}


class FakeOSError(OSError):
    """Raised by the failure-injection hook, in place of a failed ``SendInput``."""


class Event(NamedTuple):
    """One thing the fake OS observed.

    ``kind`` is one of ``down`` / ``up`` (a modifier edge), ``text`` (a
    character that reached the focused field), ``chord`` (a keystroke under a
    chord modifier, which reaches no text field) or ``edit`` (a named editing
    or navigation key).  ``detail`` is the character, key name or modifier.
    """

    kind: str
    detail: str
    modifiers: Tuple[str, ...] = ()


class FakeOSKeyboard(KeySynthesizerBase):
    """Implements the synthesiser interface against an in-memory key-state table.

    ``text`` and ``caret`` model the focused text field, ``held`` the OS
    key-state table (only the four modifiers are tracked, since those are the
    only keys the bridge ever holds), and ``events`` is the ordered log.
    """

    def __init__(self) -> None:
        self.held: Set[str] = set()
        self._chars: List[str] = []
        self.caret = 0
        self.events: List[Event] = []
        self.calls = 0
        # Failure injection, the equivalent of PowerToys'
        # ``sendVirtualInputShouldFail``.  ``fail_on`` sees ``(method, n)``
        # with n the 1-based call count across the whole synthesiser, and a
        # True return raises *before* the call has any effect, like a
        # SendInput that injected nothing.
        self.fail_on: Optional[Callable[[str, int], bool]] = None

    # --- failure injection ---

    def fail_after(self, n: int, *, methods: Optional[Sequence[str]] = None) -> None:
        """Make the call that is *n* calls from now raise, once.

        ``methods`` restricts which method names count towards ``n``.
        """
        wanted = frozenset(methods) if methods is not None else None
        seen = 0
        fired = False

        def should_fail(method: str, _count: int) -> bool:
            nonlocal seen, fired
            if fired or (wanted is not None and method not in wanted):
                return False
            seen += 1
            if seen == n:
                fired = True
                return True
            return False

        self.fail_on = should_fail

    def _tick(self, method: str) -> None:
        self.calls += 1
        if self.fail_on is not None and self.fail_on(method, self.calls):
            raise FakeOSError(f"injected failure in {method} (call {self.calls})")

    # --- observable state ---

    @property
    def text(self) -> str:
        return "".join(self._chars)

    def chords(self) -> List[Event]:
        """Keystrokes that went to the OS as a chord rather than as text."""
        return [e for e in self.events if e.kind == "chord"]

    def clear_log(self) -> None:
        self.events.clear()

    # --- KeySynthesizerBase ---

    def is_available(self) -> bool:
        return True

    def backend_name(self) -> str:
        return "fake-os"

    def hold_modifier(self, key_name: str) -> None:
        self._tick("hold_modifier")
        if key_name in MODIFIER_NAMES:
            self.held.add(key_name)
            self.events.append(Event("down", key_name))

    def release_modifier(self, key_name: str) -> None:
        self._tick("release_modifier")
        if key_name in MODIFIER_NAMES:
            self.held.discard(key_name)
            self.events.append(Event("up", key_name))

    def reset_modifier_state(self) -> None:
        self._tick("reset_modifier_state")
        for name in sorted(self.held):
            self.events.append(Event("up", name))
        self.held.clear()

    def send_key(
        self,
        key_name: str,
        modifiers: Optional[List[str]] = None,
        hold_seconds: float = 0.0,
    ) -> None:
        self._tick("send_key")
        # Atomic wrap: a modifier that is not held goes down and up around the
        # key, a modifier that is held is left exactly as it was.
        effective = set(self.held) | {m for m in (modifiers or ()) if m in MODIFIER_NAMES}
        self._apply_key(key_name, effective)

    def send_text(self, text: str) -> None:
        self._tick("send_text")
        for ch in text:
            self._type_char(ch, set(self.held))

    def send_combination(self, keys: List[str]) -> None:
        self._tick("send_combination")
        mods = tuple(k for k in keys[:-1] if k in MODIFIER_NAMES)
        self.events.append(Event("chord", keys[-1] if keys else "", mods))

    def replace_text(self, backspace_count: int, text: str) -> None:
        self._tick("replace_text")
        effective = set(self.held)
        if effective & CHORD_MODIFIERS:
            # Shift+Left under a standing Ctrl/Alt/Win is a different chord
            # (Ctrl+Shift+Left selects by word), and the selection never
            # becomes the plain one the caller wanted.  Nothing is deleted,
            # and the replacement arrives as chords as well.
            self.events.append(Event("chord", "Left", tuple(sorted(effective | {"shift"}))))
            for ch in text:
                self._type_char(ch, effective)
            return
        for _ in range(max(0, backspace_count)):
            self._backspace()
        for ch in text:
            self._type_char(ch, effective)

    # --- the OS's own rules ---

    def _apply_key(self, key_name: str, effective: Set[str]) -> None:
        mods = tuple(sorted(effective))
        if effective & CHORD_MODIFIERS:
            self.events.append(Event("chord", key_name, mods))
            return
        if key_name == "BackSpace":
            self._backspace()
            self.events.append(Event("edit", key_name, mods))
        elif key_name == "Delete":
            if self.caret < len(self._chars):
                del self._chars[self.caret]
            self.events.append(Event("edit", key_name, mods))
        elif key_name == "Left":
            self.caret = max(0, self.caret - 1)
            self.events.append(Event("edit", key_name, mods))
        elif key_name == "Right":
            self.caret = min(len(self._chars), self.caret + 1)
            self.events.append(Event("edit", key_name, mods))
        elif key_name in ("Home", "Page_Up"):
            self.caret = 0
            self.events.append(Event("edit", key_name, mods))
        elif key_name in ("End", "Page_Down"):
            self.caret = len(self._chars)
            self.events.append(Event("edit", key_name, mods))
        elif key_name in _TEXT_KEYS:
            self._insert(_TEXT_KEYS[key_name], mods)
        elif len(key_name) == 1:
            self._type_char(key_name, effective)
        else:
            # Up, Down, Escape, function keys: a keystroke the field ignores.
            self.events.append(Event("edit", key_name, mods))

    def _type_char(self, ch: str, effective: Set[str]) -> None:
        mods = tuple(sorted(effective))
        if effective & CHORD_MODIFIERS:
            self.events.append(Event("chord", ch, mods))
            return
        if "shift" in effective and ch.isascii():
            # The scancode route: the OS sees a key plus a standing Shift.
            ch = ch.upper() if ch.isalpha() else SHIFTED.get(ch, ch)
        self._insert(ch, mods)

    def _insert(self, ch: str, mods: Tuple[str, ...] = ()) -> None:
        self._chars.insert(self.caret, ch)
        self.caret += 1
        self.events.append(Event("text", ch, mods))

    def _backspace(self) -> None:
        if self.caret > 0:
            del self._chars[self.caret - 1]
            self.caret -= 1
