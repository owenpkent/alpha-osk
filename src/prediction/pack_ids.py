"""
The one rule for what a vocabulary-pack id may be.

Pack ids are used as filesystem directory names, both under the user's
packs directory (``PackManager.import_pack``) and inside a Data Backup
archive's ``packs/<pack_id>/...`` entries (``src/data_export.py``). Both
callers are validating the same untrusted string against the same risk,
so the rule lives here once rather than as two copies that can drift.

Two things are checked, and both are load-bearing:

- **The pattern.** An id must start with ``[a-z0-9]`` and continue with
  ``[a-z0-9_-]``, 1-64 characters total. A pack id of ``".."`` (or
  anything that resolves outside the packs directory) must be rejected
  before any ``shutil.rmtree`` / ``copytree`` call, and a hand-edited
  Data Backup archive can substitute ``../escape`` for a pack id, so
  export re-checks on the way out and import re-checks on the way in.
  The leading character is restricted to ``[a-z0-9]`` (never ``-`` or
  ``_``) because a real pack id is never anything else: the only place
  an id is ever *derived* is ``PackManager.import_pack``'s sanitiser,
  which strips leading/trailing ``_``/``-`` after collapsing everything
  outside ``[a-z0-9_-]``, so a legitimately created pack can't start
  with either character. A looser pattern that admits a leading ``-``
  or ``_`` doesn't describe any id this codebase can produce; it only
  widens what a crafted archive can smuggle through.
- **Reserved device names.** Windows reserves ``con``, ``prn``, ``aux``,
  ``nul``, ``com1``-``9`` and ``lpt1``-``9`` for every path component,
  regardless of case and regardless of any extension attached
  (``con.txt`` is exactly as unrepresentable as ``con``). The pattern
  above happily matches ``con``: it's a valid lowercase
  ``[a-z0-9_-]`` string. Without this check, a pack id of ``con``
  would hit a destination ``mkdir`` raising an uncaught ``OSError`` on
  Windows, partway through an import that had already replaced model
  files, analytics and snippets, leaving a half-applied import.
"""

from __future__ import annotations

import re

PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

RESERVED_DEVICE_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)


def is_reserved_device_name(name: str) -> bool:
    """True if *name* collides with a Windows reserved device name.

    Checked on the base name before any extension, case-insensitively,
    matching how Windows itself resolves these names.
    """
    base = name.split(".", 1)[0].lower()
    return base in RESERVED_DEVICE_NAMES


def is_valid_pack_id(name: str) -> bool:
    """True if *name* is safe to use as a pack directory / archive entry.

    Combines both checks above: the pattern must match AND the name must
    not collide with a reserved device name. Prefer this over the two
    pieces separately unless a call site genuinely needs them apart.
    """
    return bool(PACK_ID_RE.match(name)) and not is_reserved_device_name(name)
