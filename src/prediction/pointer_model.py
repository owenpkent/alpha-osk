"""Where inside a key the user tends to click: a learned per-slot offset.

A click on the keyboard lands somewhere inside a key's rectangle, and the
engine used to keep only *which* key: a click dead-centre and a click one
pixel from the edge produced the same candidates.  ``KeyButton.qml`` now
hands the bridge the click's offset within the key (fraction of the key's
width and height, centre = 0), which the prefix beam turns into a
continuous position.  On its own that sharpens the beam's distribution
(the intended key's surprisal fell from 1.66 to 0.95 bits in simulation)
without changing which key is the single best guess.  The win beyond that
comes from what this module holds: a pointer that systematically lands a
little right and low of where it is aimed is exactly the error a fixed
key grid cannot see and a per-user one can (75% to 86% intended-key
recovery in the same simulation), and Weir et al. (UIST 2012) and Gboard's
spatial personalisation both report gains from about 200 samples.

Offsets are keyed by **physical slot** (the key's row and column in the
spatial model), not by character.  The bias belongs to the pointer, not
the letter, so it survives a Dvorak or Colemak remap, where the same slot
holds a different character, and it does not need resetting on a layout
switch.  Each slot's estimate is shrunk toward the global mean with a
prior of ``PRIOR`` pseudo-observations, so a key pressed a handful of
times borrows the user's overall bias rather than its own noise, which is
Gboard's clustering idea in its simplest form.

Privacy: the bridge only observes outside privacy mode; nothing here is
typed content, and nothing here logs.  The table rides in
``ngram_model.json`` beside the token store for the same reasons that
store does: one file for everything the user has taught the engine, with
the load caps and the backup and Clear Learned Data behaviour already in
place.
"""

from __future__ import annotations

import math
from typing import Dict, List, Mapping, Tuple

# Pseudo-observations pulling a slot toward the global mean: after this
# many presses on a key its own mean and the user's overall bias weigh
# equally.
PRIOR = 10.0
# An offset further than this from the key centre is not a click on the
# key (the rectangle spans -0.5 to 0.5); clamp rather than reject so a
# press on the very edge still counts.
MAX_OFFSET = 1.0
# More slots than any layout has keys; a file claiming more is not ours.
MAX_SLOTS = 256


def slot_id(position: Tuple[float, float]) -> str:
    """The persisted key for a spatial-model position, e.g. ``"1,5.25"``."""
    return f"{position[0]:g},{position[1]:g}"


class PointerModel:
    """Per-slot mean click offset, shrunk toward the global mean."""

    def __init__(self) -> None:
        # slot -> [count, sum dx, sum dy]
        self._slots: Dict[str, List[float]] = {}
        self._total: List[float] = [0.0, 0.0, 0.0]

    def __len__(self) -> int:
        """Total observations, all slots."""
        return int(self._total[0])

    def observe(self, slot: str, dx: float, dy: float) -> None:
        if not slot or not (math.isfinite(dx) and math.isfinite(dy)):
            return
        if slot not in self._slots and len(self._slots) >= MAX_SLOTS:
            return
        dx = max(-MAX_OFFSET, min(MAX_OFFSET, dx))
        dy = max(-MAX_OFFSET, min(MAX_OFFSET, dy))
        row = self._slots.setdefault(slot, [0.0, 0.0, 0.0])
        row[0] += 1.0
        row[1] += dx
        row[2] += dy
        self._total[0] += 1.0
        self._total[1] += dx
        self._total[2] += dy

    def bias(self, slot: str) -> Tuple[float, float]:
        """The estimated systematic offset for ``slot``.

        The global mean when the slot is unseen; otherwise the slot's own
        mean shrunk toward the global one by ``PRIOR`` pseudo-observations.
        """
        n_all = self._total[0]
        if n_all <= 0.0:
            return (0.0, 0.0)
        gx = self._total[1] / n_all
        gy = self._total[2] / n_all
        row = self._slots.get(slot)
        if row is None:
            return (gx, gy)
        n, sx, sy = row
        return ((sx + PRIOR * gx) / (n + PRIOR), (sy + PRIOR * gy) / (n + PRIOR))

    def correct(self, slot: str, dx: float, dy: float) -> Tuple[float, float]:
        """``(dx, dy)`` with the learned bias for ``slot`` taken out."""
        bx, by = self.bias(slot)
        return (dx - bx, dy - by)

    def clear(self) -> None:
        self._slots.clear()
        self._total = [0.0, 0.0, 0.0]

    def to_dict(self) -> Dict[str, List[float]]:
        return {
            slot: [round(n, 3), round(sx, 4), round(sy, 4)]
            for slot, (n, sx, sy) in self._slots.items()
            if n > 0
        }

    def from_dict(self, raw: object) -> None:
        """Replace the table from a persisted mapping, skipping bad entries.

        Malformed slots are dropped one at a time rather than failing the
        file, the rule the token store applies: this rides in the model
        file with the vocabulary, and one bad row is no reason to lose it.
        """
        self.clear()
        if not isinstance(raw, Mapping):
            return
        for slot, row in raw.items():
            if len(self._slots) >= MAX_SLOTS:
                break
            if not isinstance(slot, str) or not isinstance(row, (list, tuple)) or len(row) != 3:
                continue
            try:
                n, sx, sy = (float(v) for v in row)
            except (TypeError, ValueError):
                continue
            if any(isinstance(v, bool) for v in row):
                continue
            if not (math.isfinite(n) and math.isfinite(sx) and math.isfinite(sy)) or n <= 0:
                continue
            # A mean outside the key is not a mean of clicks on it.
            if abs(sx / n) > MAX_OFFSET or abs(sy / n) > MAX_OFFSET:
                continue
            self._slots[slot] = [n, sx, sy]
            self._total[0] += n
            self._total[1] += sx
            self._total[2] += sy
