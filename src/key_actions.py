"""
Programmable key actions - what a function key does when it is tapped.

Alpha-OSK ships F1-F24 as clickable keys.  F1-F12 are the standard ones
every app already binds; **F13-F24 exist precisely because almost nothing
binds them**, which makes them the natural home for a macro.  That is
only half a feature on its own though: an unbound F13 does nothing until
the *target* app is taught to listen for it, and teaching every app is
not something a mouse-driven user should have to do.  So any function
key may instead be assigned a local action that works everywhere
immediately: fire a chord (Ctrl+Shift+S), or type a stored phrase.

This module owns three things and nothing else:

1. **The action-type registry** (:data:`ACTION_TYPES`).  Each entry
   knows its own id, how to sanitise its payload, how to describe itself
   in one line for the UI, and how to execute itself.  Adding a new
   action type (``launch``, ``macro``, and the rest of the vocabulary
   sketched in ``docs/architecture/MODULAR_LAYOUTS.md``) means adding one
   entry here plus one method on :class:`ActionExecutor`.  It must not
   mean touching ``keyboard_bridge.py`` or the QML editor, which both
   drive off the registry rather than off a hardcoded list of types.
2. **The store** (:class:`KeyActionStore`), persisting assignments to
   ``key_actions.json`` in the config directory, saved synchronously on
   every mutation like ``snippets.json``.
3. **The sanitisers**, which are the security-relevant part: every field
   here is user-authored, lands in a QML property, and (for a chord) is
   handed to the platform synthesiser.  Chord keys and modifiers are
   validated against **allow-lists**, never a deny-list, for the same
   reason the import paths are.

Storage layout::

    {
      "version": 1,
      "actions": {
        "f13": {"type": "hotkey", "label": "Save", "key": "s",
                "modifiers": ["ctrl"]},
        "f14": {"type": "text", "label": "Sig", "text": "Best,\\nOwen"},
        "f15": {"type": "key", "label": "Push to talk"}
      }
    }

A key absent from ``actions`` behaves exactly as it did before this
module existed: tapping it sends its own keystroke.  ``{"type": "key"}``
is *not* the same as absent - it is how a key keeps the default
behaviour while carrying a custom keycap label, which is the case for a
key the user has bound inside another application (Discord push-to-talk,
an OBS scene) and wants to be able to find on screen.

**Deliberately not in the Data Backup archive.**  ``src/data_export.py``
carries the prediction model, analytics and snippets; adding a fourth
file means bumping ``SCHEMA_VERSION`` there and writing the
back-compatible import path, which the project requires alignment on
before changing.  Until then this file is machine-local, like the Qt
settings layer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from .platform import get_config_dir

_logger = logging.getLogger("KeyActions")

SCHEMA_VERSION = 1

_FILENAME = "key_actions.json"

# Every key that may carry an action.  Function keys only, for now: they
# are the keys with no character to type and no editing behaviour to
# preserve, so reassigning one costs nothing.  Widening this to, say, a
# dedicated macro pad is a matter of extending the tuple; nothing below
# assumes an "f" prefix.
FUNCTION_KEYS: Tuple[str, ...] = tuple(f"f{n}" for n in range(1, 25))

# The subset that carries no default binding in mainstream software, and
# is therefore safe to reassign without losing something the user had.
# Surfaced to QML so the editor can say so on the keys it applies to.
UNBOUND_FUNCTION_KEYS: Tuple[str, ...] = tuple(f"f{n}" for n in range(13, 25))

# Bounds.  A keycap is roughly 48 px wide at the sizes this row renders
# at, so a label longer than this cannot be read on the key regardless of
# what the store would accept.
MAX_LABEL_LEN = 12
MAX_TEXT_LEN = 500
MAX_MODIFIERS = 4
_MAX_FILE_BYTES = 256 * 1024

# Modifier names the synthesiser layer understands.  "win" is Super on
# Linux and Command on macOS; the platform layer does that mapping, this
# one only decides which names exist.
MODIFIERS: Tuple[str, ...] = ("ctrl", "alt", "shift", "win")

# Named keys a chord may target, in the bridge's own naming (the keys of
# ``pressSpecialKey``'s ``key_map``), so a chord's action key can be fed
# straight back through the same translation the row's own keystrokes
# use.  Anything not named here must be a single printable ASCII
# character, checked separately.
CHORD_SPECIAL_KEYS: Tuple[str, ...] = (
    "backspace",
    "return",
    "space",
    "tab",
    "escape",
    "left",
    "right",
    "up",
    "down",
    "delete",
    "home",
    "end",
    "pageup",
    "pagedown",
    "insert",
    "print",
    "scrolllock",
    "pause",
    "numlock",
) + FUNCTION_KEYS


def _clean_label(label: object) -> str:
    """Collapse *label* to a single trimmed line within the length cap.

    Reads straight out of parsed JSON, so it may be any type at all.

    Whitespace *runs* collapse to one space, where ``snippets._clean_label``
    replaces each character individually.  The difference matters here and
    not there: a keycap has twelve characters to work with, so a pasted
    "\\r\\n" turning into two spaces spends a sixth of the label on
    nothing.
    """
    if not isinstance(label, str):
        return ""
    return " ".join(label.split())[:MAX_LABEL_LEN]


def _clean_text(text: object) -> str:
    """Trim typed text to the cap, stripping unsafe control characters.

    Same rule as ``snippets._clean_value`` and for the same reason: this
    string is typed verbatim into whatever app has focus, so nothing that
    can act as an unintended keypress may survive.  Newline and tab are
    kept because a multi-line signature is a legitimate payload and
    sending a real Return between its lines is the intent; every other C0
    control character goes, along with DEL (0x7F), which sits above the
    C0 range and so would slip past an ``ord(ch) >= 0x20`` test alone.
    """
    if not isinstance(text, str):
        return ""
    cleaned = "".join(
        ch for ch in text if ch in ("\n", "\t") or (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    )
    return cleaned[:MAX_TEXT_LEN]


def _clean_modifiers(mods: object) -> List[str]:
    """Return the recognised modifiers in *mods*, de-duplicated and ordered.

    Allow-list, not a filter of known-bad names: this list is handed to
    the platform synthesiser, which on Linux turns it into argv for
    ``xdotool``.  Canonical order (ctrl, alt, shift, win) rather than the
    order the user clicked them in, so the same chord always compares and
    displays identically.
    """
    if not isinstance(mods, (list, tuple)):
        return []
    seen = {m.strip().lower() for m in mods if isinstance(m, str)}
    return [m for m in MODIFIERS if m in seen][:MAX_MODIFIERS]


def _clean_chord_key(key: object) -> str:
    """Return the chord's action key, or "" if it is not one we can send.

    Two shapes are accepted: a name from :data:`CHORD_SPECIAL_KEYS`, or a
    single printable ASCII character.  Letters are lowercased, because
    "Ctrl+S" and "Ctrl+s" are the same chord and only one of them should
    ever be stored.  Restricting characters to printable ASCII is the
    allow-list half: a chord key is fed to the synthesiser as a key
    *name*, and the platform layers translate the ASCII range (see
    ``linux._CHAR_TO_KEYSYM`` and ``windows.KEY_MAP``) but have nothing
    to say about a stray control character or an emoji.
    """
    if not isinstance(key, str):
        return ""
    name = key.strip().lower()
    if name in CHORD_SPECIAL_KEYS:
        return name
    stripped = key.strip()
    if len(stripped) == 1 and 0x21 <= ord(stripped) <= 0x7E:
        return stripped.lower()
    return ""


class ActionExecutor:
    """What an action type needs from the rest of the app to run.

    The bridge implements this.  It exists so an action type can be added
    to the registry below without the registry needing to know anything
    about ``KeyboardBridge``, its held-modifier context manager, or its
    prediction buffers - and so the action types are testable against a
    recording double instead of a live bridge.
    """

    def send_chord(self, key: str, modifiers: Sequence[str]) -> None:
        """Fire *key* with *modifiers* held, as one chord."""
        raise NotImplementedError

    def send_text(self, text: str) -> None:
        """Type *text* verbatim into the focused app."""
        raise NotImplementedError


class KeyActionType(NamedTuple):
    """One kind of thing a key can be programmed to do.

    ``execute`` returns True when it handled the tap and False when the
    key should fall through to its own default keystroke.  That is what
    lets ``"key"`` (keep the default behaviour, just relabel the keycap)
    live in the registry as a peer of the others rather than as a special
    case the bridge has to know about.
    """

    id: str
    label: str
    description: str
    # Which editor fields the UI shows for this type.  QML reads this
    # rather than switching on the id, so a new type gets its editor for
    # free as long as it reuses an existing field.
    fields: Tuple[str, ...]
    clean: Callable[[dict], Optional[dict]]
    describe: Callable[[dict], str]
    execute: Callable[[dict, ActionExecutor], bool]


# --- "key": send the key's own keystroke ------------------------------


def _clean_key(payload: dict) -> Optional[dict]:
    return {"type": "key"}


def _describe_key(payload: dict) -> str:
    return "Sends its own keystroke"


def _execute_key(payload: dict, executor: ActionExecutor) -> bool:
    return False


# --- "hotkey": fire a chord -------------------------------------------


def _clean_hotkey(payload: dict) -> Optional[dict]:
    """Validate a chord.  Returns None when there is no key to send.

    A chord with no action key is not a chord at all, and storing one
    would leave a key that looks programmed and does nothing when tapped.
    Modifiers alone are allowed to be empty: "F13 sends Escape" is a
    legitimate remap.
    """
    key = _clean_chord_key(payload.get("key"))
    if not key:
        return None
    return {
        "type": "hotkey",
        "key": key,
        "modifiers": _clean_modifiers(payload.get("modifiers")),
    }


def _describe_hotkey(payload: dict) -> str:
    # Rendered in the canonical modifier order rather than the payload's,
    # so a hand-edited file still displays the chord the way the store
    # would have written it and two spellings of one chord never read as
    # two different chords.
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    held = _clean_modifiers(payload.get("modifiers"))
    mods = [names[m] for m in held]
    key = str(payload.get("key", ""))
    shown = key.upper() if len(key) == 1 else key.capitalize()
    return "+".join(mods + [shown])


def _execute_hotkey(payload: dict, executor: ActionExecutor) -> bool:
    executor.send_chord(payload["key"], payload.get("modifiers", []))
    return True


# --- "text": type a stored phrase -------------------------------------


def _clean_text_action(payload: dict) -> Optional[dict]:
    text = _clean_text(payload.get("text"))
    if not text:
        return None
    return {"type": "text", "text": text}


def _describe_text(payload: dict) -> str:
    text = str(payload.get("text", "")).replace("\n", " ")
    return 'Types "' + text[:24] + ('..."' if len(text) > 24 else '"')


def _execute_text(payload: dict, executor: ActionExecutor) -> bool:
    executor.send_text(payload["text"])
    return True


ACTION_TYPES: Tuple[KeyActionType, ...] = (
    KeyActionType(
        id="key",
        label="Send the key",
        description="Tapping it sends the key itself, to bind inside another app.",
        fields=(),
        clean=_clean_key,
        describe=_describe_key,
        execute=_execute_key,
    ),
    KeyActionType(
        id="hotkey",
        label="Hotkey",
        description="Fire a shortcut like Ctrl+Shift+S in one click.",
        fields=("chord",),
        clean=_clean_hotkey,
        describe=_describe_hotkey,
        execute=_execute_hotkey,
    ),
    KeyActionType(
        id="text",
        label="Type text",
        description="Insert a stored phrase, signature or address.",
        fields=("text",),
        clean=_clean_text_action,
        describe=_describe_text,
        execute=_execute_text,
    ),
)

_BY_ID: Dict[str, KeyActionType] = {a.id: a for a in ACTION_TYPES}


def get_action_type(type_id: object) -> Optional[KeyActionType]:
    """Return the registry entry for *type_id*, or None if unknown."""
    return _BY_ID.get(type_id) if isinstance(type_id, str) else None


def action_type_info() -> List[Dict[str, object]]:
    """Describe the registry for QML.

    The editor builds its action-type picker from this rather than from a
    hardcoded list, so a new entry in :data:`ACTION_TYPES` appears in the
    UI with no QML change.  ``fields`` is what the editor switches on to
    decide which inputs to show.
    """
    return [
        {"id": a.id, "label": a.label, "description": a.description, "fields": list(a.fields)}
        for a in ACTION_TYPES
    ]


def clean_action(payload: object) -> Optional[dict]:
    """Sanitise one stored action record, or return None to drop it.

    Dropped rather than repaired when the type is unknown or its payload
    does not validate: a key with no entry falls back to sending itself,
    which is always a defensible thing for a key labelled F17 to do.  A
    half-repaired action is not.
    """
    if not isinstance(payload, dict):
        return None
    action_type = get_action_type(payload.get("type"))
    if action_type is None:
        return None
    cleaned = action_type.clean(payload)
    if cleaned is None:
        return None
    label = _clean_label(payload.get("label"))
    if label:
        cleaned["label"] = label
    return cleaned


def describe_action(payload: dict) -> str:
    """One-line human description of *payload*, for the editor and tests."""
    action_type = get_action_type(payload.get("type"))
    return action_type.describe(payload) if action_type else ""


class KeyActionStore:
    """Load, mutate and persist per-key action assignments."""

    def __init__(self, path: Optional[Path] = None) -> None:
        """Create the store.

        Args:
            path: Override for the key_actions.json location.  Defaults
                to ``<config_dir>/key_actions.json``.  Tests pass a temp
                path.
        """
        if path is None:
            path = get_config_dir() / _FILENAME
        self._path = Path(path)
        self._actions: Dict[str, dict] = {}
        self._loaded = False

    # --- Persistence ---------------------------------------------------

    def load(self) -> None:
        """Read assignments from disk.

        Tolerant throughout: a missing, oversized, corrupt or partially
        invalid file leaves the affected keys unassigned rather than
        raising.  An unassigned function key still works, so there is
        never a reason to let this path block startup.
        """
        self._loaded = True
        self._actions = {}
        try:
            if not self._path.exists():
                return
            if self._path.stat().st_size > _MAX_FILE_BYTES:
                _logger.warning("%s exceeds %d bytes - ignoring", _FILENAME, _MAX_FILE_BYTES)
                return
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            _logger.warning("Failed to load key actions (%s) - none applied", exc)
            return

        raw = data.get("actions") if isinstance(data, dict) else None
        if not isinstance(raw, dict):
            return
        for key, payload in raw.items():
            if key not in FUNCTION_KEYS:
                continue
            cleaned = clean_action(payload)
            if cleaned is not None:
                self._actions[key] = cleaned

    def save(self) -> None:
        """Write assignments to disk atomically (tempfile then rename)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": SCHEMA_VERSION, "actions": self._actions}
        tmp = self._path.with_suffix(self._path.suffix + ".saving")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
        except OSError as exc:
            _logger.warning("Failed to save key actions: %s", exc)
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # --- Accessors -----------------------------------------------------

    def get(self, key: str) -> Optional[dict]:
        """Return the action assigned to *key*, or None if unassigned."""
        if not self._loaded:
            self.load()
        action = self._actions.get(str(key).lower())
        return dict(action) if action else None

    def get_all(self) -> Dict[str, dict]:
        """Return a copy of every assignment (safe for QML to consume)."""
        if not self._loaded:
            self.load()
        return {k: dict(v) for k, v in self._actions.items()}

    def label_for(self, key: str) -> str:
        """Return the custom keycap label for *key*, or "" for the default."""
        action = self.get(key)
        return str(action.get("label", "")) if action else ""

    # --- Mutations (each persists immediately) -------------------------

    def set(self, key: str, payload: dict) -> bool:
        """Assign an action to *key*.  Returns True if anything changed.

        Returns False for a key outside :data:`FUNCTION_KEYS` and for a
        payload that does not validate, so the caller can tell the user
        the save did not land instead of flashing a confirmation over a
        write that never happened.  That failure mode is the one
        ``acceptSnippetOffer`` and ``setSnippet`` were both given bool
        returns for.
        """
        key = str(key).lower()
        if key not in FUNCTION_KEYS:
            return False
        cleaned = clean_action(payload)
        if cleaned is None:
            return False
        if not self._loaded:
            self.load()
        if self._actions.get(key) == cleaned:
            return False
        self._actions[key] = cleaned
        self.save()
        return True

    def clear(self, key: str) -> bool:
        """Drop *key*'s assignment.  Returns True if there was one."""
        key = str(key).lower()
        if not self._loaded:
            self.load()
        if key not in self._actions:
            return False
        del self._actions[key]
        self.save()
        return True

    def reload_from_disk(self) -> None:
        """Re-read key_actions.json (used after a data import)."""
        self._loaded = False
        self.load()

    # --- Dispatch ------------------------------------------------------

    def execute(self, key: str, executor: ActionExecutor) -> bool:
        """Run *key*'s action.  Returns False if it should send itself.

        The single entry point the bridge calls, so the bridge never
        switches on an action type and a new registry entry reaches it
        without an edit there.
        """
        action = self.get(key)
        if action is None:
            return False
        action_type = get_action_type(action.get("type"))
        if action_type is None:
            return False
        return action_type.execute(action, executor)
