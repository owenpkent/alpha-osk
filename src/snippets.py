"""
Snippets - user-defined quick-insert text.

A small store of frequently-typed personal data and phrases (name,
email, phone, address, signatures, canned replies) that the user can
tap once to insert verbatim into the focused application, instead of
typing them out and fighting prediction every time.

Each entry is a ``{"label": str, "value": str}`` pair:

- ``label`` is the short text shown on the button in the Snippets popup
  (e.g. "Email").
- ``value`` is the exact text typed into the target app when the entry
  is tapped (e.g. "owen@example.com").

The list is persisted as ``snippets.json`` in the config directory
(alongside ``analytics.json`` and ``telemetry.json``).  It is saved
synchronously on every mutation, so there is no on-quit save path to
wire up — a crash never loses more than the keystroke in flight.

Storage layout::

    {
      "version": 1,
      "snippets": [
        {"label": "Name", "value": "..."},
        {"label": "Email", "value": "..."}
      ]
    }

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

from .platform import get_config_dir

_logger = logging.getLogger("Snippets")

SCHEMA_VERSION = 1

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
    """Trim a value to the length cap, preserving internal characters.

    Newlines are kept — a snippet may legitimately be a multi-line
    block (e.g. a mailing address).  Only the overall length is bounded.
    """
    return str(value)[:MAX_VALUE_LEN]


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
            cleaned.append({"label": label, "value": value})
            if len(cleaned) >= MAX_SNIPPETS:
                break

        # A file that parsed but held nothing usable falls back to the
        # seeded slots so the user is never left with an empty list they
        # didn't deliberately create.
        self._snippets = cleaned if cleaned else self._default_snippets()

    def save(self) -> None:
        """Write snippets to disk atomically (tempfile then rename)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": SCHEMA_VERSION, "snippets": self._snippets}
        tmp = self._path.with_suffix(self._path.suffix + ".saving")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            tmp.replace(self._path)
        except OSError as exc:
            _logger.warning("Failed to save snippets: %s", exc)
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    # --- Seeding -------------------------------------------------------

    @staticmethod
    def _default_snippets() -> List[Dict[str, str]]:
        return [{"label": lbl, "value": ""} for lbl in _DEFAULT_LABELS]

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

    def set(self, index: int, label: str, value: str) -> bool:
        """Replace the label + value at *index*.  Returns True on change."""
        if not self._loaded:
            self.load()
        if not (0 <= index < len(self._snippets)):
            return False
        self._snippets[index] = {
            "label": _clean_label(label),
            "value": _clean_value(value),
        }
        self.save()
        return True

    def add(self, label: str = "", value: str = "") -> bool:
        """Append a new snippet.  Returns False if at the size cap."""
        if not self._loaded:
            self.load()
        if len(self._snippets) >= MAX_SNIPPETS:
            return False
        self._snippets.append({
            "label": _clean_label(label),
            "value": _clean_value(value),
        })
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
