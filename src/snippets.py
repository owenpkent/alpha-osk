"""
Snippets - user-defined quick-insert text.

A small store of frequently-typed personal data and phrases (name,
email, phone, address, signatures, canned replies) that the user can
tap once to insert verbatim into the focused application, instead of
typing them out and fighting prediction every time.

Each entry is a ``{"label": str, "value": str, "color": str}`` record:

- ``label`` is the short text shown on the button in the Snippets popup
  (e.g. "Email").
- ``value`` is the exact text typed into the target app when the entry
  is tapped (e.g. "owen@example.com").
- ``color`` is an optional tag name from :data:`SNIPPET_COLORS` (empty
  for untagged), used only to tint the tile so a grid of a dozen
  snippets can be scanned by colour instead of read.

The list is persisted as ``snippets.json`` in the config directory
(alongside ``analytics.json`` and ``telemetry.json``).  It is saved
synchronously on every mutation, so there is no on-quit save path to
wire up — a crash never loses more than the keystroke in flight.

Storage layout::

    {
      "version": 2,
      "snippets": [
        {"label": "Name", "value": "...", "color": ""},
        {"label": "Email", "value": "...", "color": "blue"}
      ]
    }

Version 2 added ``color``.  Nothing reads the version field on load, and
that is deliberate rather than an oversight: an entry missing ``color``
reads as untagged and an entry carrying an unknown one is retagged to
untagged, so a file from either side of the bump loads correctly on its
own merits.  A version gate would refuse files this loader can in fact
read.

The store ships with four pre-made, empty, labelled slots (Name /
Email / Phone / Address) on first launch so the user has obvious
places to fill in rather than a blank list.  Every field (labels
included) is editable and deletable.

Why this lives in its own module rather than Qt Settings: snippet
*values* are user data the user would want to move between machines,
so the file is folded into the Data Backup archive (see
``src/data_export.py``).  Qt Settings (registry / config) is for UI
preferences that are quick to reconfigure and deliberately excluded
from the backup.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .atomic_write import atomic_write_json
from .platform import get_config_dir

_logger = logging.getLogger("Snippets")

SCHEMA_VERSION = 2

# Colour tags are stored as *names* from this allow-list, never as the
# hex the UI ends up drawing.  snippets.json is replace-on-import from an
# archive the user picked (see src/data_export.py), and the stored string
# lands in a QML `color` property, so a value from an untrusted file must
# never reach it verbatim.  An unrecognised name degrades to untagged
# rather than rejecting the snippet: the label and the value are the part
# worth keeping.
#
# QML owns the actual hexes, because they have to stay legible on nine
# themes; this tuple is the single source of truth for which names exist,
# and the bridge hands it to QML (``getSnippetColors``) so the swatch row
# can never offer a tag the store would silently drop.
#
# "" is not a missing value, it is the *grey* default: an untagged snippet
# renders in the theme's own neutral key colour, and the first swatch is a
# grey one rather than an empty hole. That is also why there is no grey in
# the list, and why the blue-grey "slate" that was briefly here is gone: a
# tag that reads as the default is a tag that cannot be seen.
SNIPPET_COLORS = ("", "red", "amber", "green", "blue", "purple")

# Bounds.  These are generous for the intended use (a handful of
# personal-info fields) while keeping the file small and rejecting a
# corrupt or hostile snippets.json that tries to balloon memory.
MAX_SNIPPETS = 50
MAX_LABEL_LEN = 40
MAX_VALUE_LEN = 2000
_MAX_FILE_BYTES = 1 * 1024 * 1024  # 1 MB — snippets.json is tiny in practice

# Pre-made empty slots seeded on first launch.  Labels only; the user
# fills in the values.
_DEFAULT_LABELS = ("Name", "Email", "Phone", "Address")

_SNIPPETS_FILENAME = "snippets.json"


def _clean_label(label: str) -> str:
    """Collapse a label to a single trimmed line within the length cap."""
    label = str(label).replace("\r", " ").replace("\n", " ").strip()
    return label[:MAX_LABEL_LEN]


def _clean_value(value: str) -> str:
    """Trim a value to the length cap, stripping unsafe control characters.

    Newlines are kept: a snippet may legitimately be a multi-line block
    (e.g. a mailing address), and typing one is meant to send a real
    keypress between lines. Every other C0 control character is
    stripped, in particular carriage return. On Linux ``xdotool type``
    turns an embedded newline into a real Return keypress, and on
    Windows a raw carriage return reaching a console behaves the same
    way, so nothing that can act as an unintended keypress may survive
    into a value that gets typed verbatim into whatever app currently
    has focus.

    DEL (0x7F) is stripped along with the C0 range even though it sits
    above it numerically: the rule this implements is "no control
    character other than the two we deliberately allow", and an
    ``ord(ch) >= 0x20`` test alone would let DEL through on a
    technicality.
    """
    cleaned = "".join(
        ch for ch in str(value) if ch in ("\n", "\t") or (ord(ch) >= 0x20 and ord(ch) != 0x7F)
    )
    return cleaned[:MAX_VALUE_LEN]


def _clean_color(color: object) -> str:
    """Return *color* if it names an allowed tag, otherwise "" (untagged).

    Non-strings included: this reads straight out of parsed JSON, so the
    field can be any type at all.
    """
    if not isinstance(color, str):
        return ""
    name = color.strip().lower()
    return name if name in SNIPPET_COLORS else ""


class SnippetStore:
    """Load, mutate, and persist the user's quick-insert snippets."""

    def __init__(self, path: Optional[Path] = None) -> None:
        """Create the store.

        Args:
            path: Override for the snippets.json location.  Defaults to
                ``<config_dir>/snippets.json``.  Tests pass a temp path.
        """
        if path is None:
            path = get_config_dir() / _SNIPPETS_FILENAME
        self._path = Path(path)
        self._snippets: List[Dict[str, str]] = []
        self._loaded = False

    # --- Persistence ---------------------------------------------------

    def load(self) -> None:
        """Load snippets from disk, seeding defaults if absent or invalid.

        Idempotent and tolerant: any read / parse error falls back to
        the seeded defaults rather than raising, so a corrupt file never
        blocks startup.  An oversized file is rejected outright.
        """
        self._loaded = True
        try:
            if not self._path.exists():
                self._seed_defaults()
                self.save()
                return
            if self._path.stat().st_size > _MAX_FILE_BYTES:
                _logger.warning(
                    "snippets.json exceeds %d bytes — ignoring and reseeding",
                    _MAX_FILE_BYTES,
                )
                self._seed_defaults()
                return
            with open(self._path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            _logger.warning("Failed to load snippets (%s) — using defaults", exc)
            self._seed_defaults()
            return

        raw = data.get("snippets") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            self._seed_defaults()
            return

        cleaned: List[Dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            label = _clean_label(item.get("label", ""))
            value = _clean_value(item.get("value", ""))
            # Drop entries that are entirely empty (no label AND no value).
            if not label and not value:
                continue
            color = _clean_color(item.get("color"))
            cleaned.append({"label": label, "value": value, "color": color})
            if len(cleaned) >= MAX_SNIPPETS:
                break

        # A file that parsed but held nothing usable falls back to the
        # seeded slots so the user is never left with an empty list they
        # didn't deliberately create.
        self._snippets = cleaned if cleaned else self._default_snippets()

    def save(self) -> None:
        """Write snippets to disk atomically (tempfile then rename)."""
        payload = {"version": SCHEMA_VERSION, "snippets": self._snippets}
        try:
            atomic_write_json(self._path, payload, indent=2, ensure_ascii=False)
        except OSError as exc:
            _logger.warning("Failed to save snippets: %s", exc)

    # --- Seeding -------------------------------------------------------

    @staticmethod
    def _default_snippets() -> List[Dict[str, str]]:
        return [{"label": lbl, "value": "", "color": ""} for lbl in _DEFAULT_LABELS]

    def _seed_defaults(self) -> None:
        self._snippets = self._default_snippets()

    # --- Accessors -----------------------------------------------------

    def get_all(self) -> List[Dict[str, str]]:
        """Return a copy of the snippet list (safe for QML to consume)."""
        if not self._loaded:
            self.load()
        return [dict(s) for s in self._snippets]

    def get_value(self, index: int) -> Optional[str]:
        """Return the value at *index*, or None if out of range."""
        if not self._loaded:
            self.load()
        if 0 <= index < len(self._snippets):
            return self._snippets[index]["value"]
        return None

    # --- Mutations (each persists immediately) -------------------------

    def set(self, index: int, label: str, value: str, color: Optional[str] = None) -> bool:
        """Replace the label + value at *index*.  Returns True on change.

        *color* defaults to None meaning "leave the existing tag alone".
        The editor only edits the label and the value, so a save from it
        must not silently clear a tag set from the actions sheet.
        """
        if not self._loaded:
            self.load()
        if not (0 <= index < len(self._snippets)):
            return False
        existing = self._snippets[index].get("color", "")
        self._snippets[index] = {
            "label": _clean_label(label),
            "value": _clean_value(value),
            "color": existing if color is None else _clean_color(color),
        }
        self.save()
        return True

    def set_color(self, index: int, color: str) -> bool:
        """Tag the snippet at *index*.  Returns True if the tag changed.

        Returns False for an unchanged tag as well as an out-of-range
        index, so the bridge does not emit a list-changed signal (and QML
        does not rebuild the grid) for a tap that selected the colour the
        snippet already had.
        """
        if not self._loaded:
            self.load()
        if not (0 <= index < len(self._snippets)):
            return False
        cleaned = _clean_color(color)
        if self._snippets[index].get("color", "") == cleaned:
            return False
        self._snippets[index]["color"] = cleaned
        self.save()
        return True

    def add(self, label: str = "", value: str = "", color: str = "") -> bool:
        """Append a new snippet.  Returns False if at the size cap."""
        if not self._loaded:
            self.load()
        if len(self._snippets) >= MAX_SNIPPETS:
            return False
        self._snippets.append(
            {
                "label": _clean_label(label),
                "value": _clean_value(value),
                "color": _clean_color(color),
            }
        )
        self.save()
        return True

    def delete(self, index: int) -> bool:
        """Remove the snippet at *index*.  Returns True on change."""
        if not self._loaded:
            self.load()
        if not (0 <= index < len(self._snippets)):
            return False
        del self._snippets[index]
        self.save()
        return True

    def move(self, index: int, direction: int) -> bool:
        """Move the snippet at *index* up (-1) or down (+1) one position.

        Returns True if the list changed.  No-op at the ends.
        """
        if not self._loaded:
            self.load()
        if direction not in (-1, 1):
            return False
        target = index + direction
        if not (0 <= index < len(self._snippets)):
            return False
        if not (0 <= target < len(self._snippets)):
            return False
        self._snippets[index], self._snippets[target] = (
            self._snippets[target],
            self._snippets[index],
        )
        self.save()
        return True

    def reload_from_disk(self) -> None:
        """Re-read snippets.json from disk (used after a data import)."""
        self._loaded = False
        self.load()
