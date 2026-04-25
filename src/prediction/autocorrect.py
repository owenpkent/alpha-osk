"""
Common-misspellings table for the hybrid autocorrect path.

This is the **fast path**: a simple lowercase ``wrong → right`` lookup
that catches well-known English misspellings the spatial / edit-distance
fuzzy machinery would either miss (silent letters: "definately" →
"definitely") or only weakly correct ("recieve" → "receive" — caught
by transposition, but a direct hit is faster and more confident).

The slow path (fuzzy ``should_autocorrect``) runs after this when the
typed word isn't a known misspelling but also isn't in any dictionary.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional

_logger = logging.getLogger("Autocorrect")


class CommonMisspellings:
    """Lowercase misspelling → correction mapping loaded from a flat file."""

    def __init__(self) -> None:
        self._table: Dict[str, str] = {}

    def load(self, path: Path) -> bool:
        if not path.exists():
            _logger.warning("Common misspellings file missing: %s", path)
            return False
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(None, 1)  # split on first whitespace run
                    if len(parts) != 2:
                        continue
                    wrong, right = parts[0].lower(), parts[1].strip().lower()
                    if not wrong or not right or wrong == right:
                        continue
                    self._table[wrong] = right
            _logger.info("Common misspellings loaded: %d entries", len(self._table))
            return True
        except OSError as e:
            _logger.error("Failed to load misspellings: %s", e)
            return False

    def lookup(self, word: str) -> Optional[str]:
        """Return the canonical spelling if ``word`` is a known misspelling.

        Casing of the *typed* word is preserved by the caller — this
        method works in lowercase and returns lowercase.
        """
        if not word:
            return None
        return self._table.get(word.lower())

    def __len__(self) -> int:
        return len(self._table)
