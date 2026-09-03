"""Prefix-aware spatial beam search: complete a prefix the user may have mistyped.

The fuzzy recognizer's other paths correct a *finished* word: the spatial
beam in ``fuzzy_recognizer`` only ever emits sequences exactly as long as
what was typed, and SymSpell reaches two edits further.  Neither can say
"the user is three letters into a nine-letter word and one of them is
wrong", which is the case almost every keystroke of a session is in.
Measured on the shipped dictionary before this existed, the intended word
was in the fuzzy source's top five 0.0% of the time after a mid-word slip
and 1.4% of the time with no error at all, and the n-gram completer needs
an exact prefix, so one mis-click cost 2.05 extra clicks per word.

This module searches over the dictionary's *live prefixes* instead: a beam
walks the typed characters, extending only prefixes some word actually
starts with, and scores each path as a noisy channel,
``sum(log P(typed_i | intended_i)) + w * log1p(freq(word))``.  The emission
is an unnormalised Gaussian in key-widths, so a perfectly hit key costs
nothing and long words no longer decay out of the beam (a correctly typed
``documentation`` returned no candidates at all before).  Four transitions
cover the four error shapes a click-driven keyboard produces: a slip onto a
neighbour (substitution), a click that never registered (omission), a
double or stray click (extra), and a transposition.  Omission and extra
are the two this user's input method produces most and the two the old
path was weakest on.  Same benchmark after: 27 / 70 / 91 / 97% recovery by
prefix length 3 to 6 with a flat frequency table; with the n-gram's
counts, which is how the hybrid runs it, 92% of clean 4-letter prefixes and
94% of slipped ones complete to the intended word, at about a millisecond.

The emission takes an optional per-character *position* (row, col in key
units) and falls back to the key's centre, so forwarding the click
coordinate later is plumbing rather than a rewrite of the scoring.
Nothing here logs, since every argument is typed content.
"""

from __future__ import annotations

import bisect
import math
from collections import defaultdict
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

Position = Tuple[float, float]


class PrefixIndex:
    """Every prefix of every dictionary word, with the best completions of each.

    Built once per dictionary (about 24,000 prefixes and 0.01 s for the
    shipped 10k list) and rebuilt when the dictionary changes.  Short
    prefixes have thousands of completions, so their top ``top_k`` by
    frequency are precomputed; longer ones are found by bisecting a sorted
    word list, where the range is small.
    """

    def __init__(
        self,
        dictionary: Mapping[str, float],
        *,
        top_k: int = 8,
        precompute_len: int = 4,
        max_scan: int = 2000,
    ) -> None:
        self.top_k = top_k
        self.precompute_len = precompute_len
        self.max_scan = max_scan
        self._freq: Dict[str, float] = {w: float(f) for w, f in dictionary.items() if w}
        self._words: List[str] = sorted(self._freq)
        self._live: Set[str] = set()
        children: Dict[str, Set[str]] = defaultdict(set)
        top: Dict[str, List[Tuple[float, str]]] = defaultdict(list)
        for word, freq in self._freq.items():
            for i in range(1, len(word) + 1):
                prefix = word[:i]
                self._live.add(prefix)
                if i < len(word):
                    children[prefix].add(word[i])
                if i <= precompute_len:
                    top[prefix].append((freq, word))
        self._children: Dict[str, str] = {p: "".join(sorted(s)) for p, s in children.items()}
        self._top: Dict[str, List[Tuple[float, str]]] = {}
        for prefix, entries in top.items():
            entries.sort(reverse=True)
            self._top[prefix] = entries[:top_k]

    def __len__(self) -> int:
        return len(self._live)

    def update_word(self, word: str, freq: float) -> None:
        """Add ``word`` or raise its frequency, keeping every table in step.

        Cheap (the word's own prefixes, and a re-sort of at most ``top_k``
        entries per short prefix), so the vocabulary can change on the
        keystroke path without a rebuild.  A word that once fell out of a
        short prefix's top ``top_k`` only comes back if its frequency now
        clears the list, which is also what a rebuild would decide.
        """
        if not word:
            return
        freq = float(freq)
        is_new = word not in self._freq
        if not is_new and freq <= self._freq[word]:
            return
        self._freq[word] = freq
        if is_new:
            bisect.insort(self._words, word)
            for i in range(1, len(word) + 1):
                prefix = word[:i]
                self._live.add(prefix)
                if i < len(word):
                    following = self._children.get(prefix, "")
                    if word[i] not in following:
                        self._children[prefix] = "".join(sorted(following + word[i]))
        for i in range(1, min(len(word), self.precompute_len) + 1):
            prefix = word[:i]
            entries = [(f, w) for f, w in self._top.get(prefix, []) if w != word]
            entries.append((freq, word))
            entries.sort(reverse=True)
            self._top[prefix] = entries[: self.top_k]

    def is_live(self, prefix: str) -> bool:
        return prefix in self._live

    def children(self, prefix: str) -> str:
        """The characters that can follow ``prefix`` in some word, as a string."""
        return self._children.get(prefix, "")

    def completions(self, prefix: str) -> List[Tuple[float, str]]:
        """Top ``top_k`` ``(freq, word)`` completions of ``prefix``, best first."""
        if len(prefix) <= self.precompute_len:
            return self._top.get(prefix, [])
        lo = bisect.bisect_left(self._words, prefix)
        hi = bisect.bisect_left(
            self._words, prefix + "￿", lo, min(len(self._words), lo + self.max_scan)
        )
        found = [(self._freq[w], w) for w in self._words[lo:hi] if w.startswith(prefix)]
        found.sort(reverse=True)
        return found[: self.top_k]


class SpatialEmissions:
    """``log P(clicked | intended)`` for every key pair within reach.

    Unnormalised on purpose.  The recognizer's older ``SpatialKeyModel``
    normalises each key's neighbour distribution to sum to 1, which makes a
    correctly hit key worth 0.35 to 0.49 depending on how many neighbours it
    happens to have, so a perfect nine-letter typing multiplied down below
    the pruning floor and an edge key outscored a central one for identical
    accuracy.  Here a hit costs 0 and the cost of a miss depends only on the
    distance.
    """

    # Uncertainty, in key widths, when only the key is known: the click was
    # somewhere inside it, plus scatter.
    KEY_SIGMA = 0.85
    # Uncertainty when the click's position inside the key is known.  Set by
    # sweep against two simulated pointers (scripts/bench/ksr.py --pointer):
    # one whose misses are mostly random (bias 0.2, 0.15; noise 0.3), the
    # robustness check, and one whose misses are mostly systematic
    # (0.35, 0.25; 0.15), the case the learned bias is for.  Sharper than
    # this hurts both, because scatter then puts the intended key on the
    # expensive side of the click more
    # often than the extra precision helps (0.3 lost 1.6 points, 0.22 lost
    # 6); 0.55 never regressed either profile.  The beam already recovers
    # most of what a position could tell it from the reported key alone,
    # so the whole gain is modest: +0.3 to +0.8 points of keystroke savings
    # with the learned bias, depending on how systematic the pointer is.
    POSITION_SIGMA = 0.55

    def __init__(
        self,
        positions: Mapping[str, Position],
        *,
        sigma: Optional[float] = None,
        position_sigma: Optional[float] = None,
        radius: float = 2.2,
    ) -> None:
        self.sigma = self.KEY_SIGMA if sigma is None else sigma
        self.position_sigma = self.POSITION_SIGMA if position_sigma is None else position_sigma
        self.radius = radius
        self._positions: Dict[str, Position] = dict(positions)
        self._table: Dict[str, Dict[str, float]] = {}
        for key, pos in self._positions.items():
            self._table[key] = self.for_position(pos, sigma=self.sigma)

    def for_key(self, char: str) -> Optional[Dict[str, float]]:
        """Emissions for a click reported as ``char``, or ``None`` if unmapped."""
        return self._table.get(char)

    def for_position(self, pos: Position, *, sigma: Optional[float] = None) -> Dict[str, float]:
        """Emissions for a click at a known position in key units.

        Defaults to ``position_sigma``: a known position carries only the
        scatter's uncertainty, where the per-key table has to allow for the
        click being anywhere inside the key as well.
        """
        s = self.position_sigma if sigma is None else sigma
        r2 = self.radius * self.radius
        denom = 2.0 * s * s
        row: Dict[str, float] = {}
        for key, kpos in self._positions.items():
            d2 = (pos[0] - kpos[0]) ** 2 + (pos[1] - kpos[1]) ** 2
            if d2 <= r2:
                row[key] = -d2 / denom
        return row


class PrefixBeam:
    """Complete a possibly mistyped prefix against a :class:`PrefixIndex`."""

    # States kept per typed character.  Forty is where the benchmark stopped
    # improving; the search is about a millisecond there.
    BEAM_WIDTH = 40
    # Weight on ``log1p(freq)`` when ranking completions of a path.  Below
    # about 0.4 rare words with a perfect spatial match outrank common ones
    # with a near miss; above about 0.7 frequency starts to override what
    # was actually typed.
    FREQ_WEIGHT = 0.55
    # Log penalties for the three non-substitution transitions, set by
    # sweep against the shipped dictionary with real frequencies (a slip
    # onto an adjacent key costs -0.69 at sigma 0.85, a diagonal -0.87).
    # A swap priced like one slip takes mid-word transposition recovery
    # from 89% to 96% at no cost elsewhere.  Omission and extra pull
    # against each other, since a cheaper omission explains a doubled
    # click away as something else: at -2.0 dropped clicks recover 83%
    # and doubled ones 52%, at -3.0 it is 58% and 66%.  -2.5 (71% / 58%)
    # leans toward omission, the error the AAC literature and the
    # whole-word benchmark both put first for this kind of input.
    LOG_OMIT = -2.5
    LOG_EXTRA = -3.4
    LOG_SWAP = -1.0
    # Below this many typed characters there is no evidence of an error to
    # act on and the n-gram completer's exact-prefix match is the better
    # source; the same guard ``should_autocorrect`` applies.
    MIN_TYPED = 3
    # A path costing more than this (one cheap edit: an adjacent slip is
    # -0.69, a diagonal -0.87, a swap -1.0) may not be bought past the
    # completions of the exact typed prefix by frequency alone.
    FREQUENCY_MAY_BUY = -1.5

    def __init__(self, index: PrefixIndex, emissions: SpatialEmissions) -> None:
        self.index = index
        self.emissions = emissions

    def complete(
        self,
        typed: str,
        n: int = 5,
        positions: Optional[Sequence[Optional[Position]]] = None,
    ) -> List[Tuple[str, float]]:
        """Top-``n`` ``(word, score)`` completions of ``typed``.

        ``positions``, when given, holds one optional ``(row, col)`` per
        typed character; ``None`` entries (and a missing sequence) fall back
        to the reported key's centre.  Scores are relative, in ``(0, 1]``
        with the best at 1.0, so the merge's sum-to-1 normalisation sees
        positives.  See ``_protect_exact_completions`` for the one rule
        applied on top of the path scores.
        """
        typed = typed.lower()
        if len(typed) < self.MIN_TYPED or n <= 0:
            return []
        emits: List[Optional[Dict[str, float]]] = []
        for i, char in enumerate(typed):
            pos = positions[i] if positions is not None and i < len(positions) else None
            if pos is not None:
                emits.append(self.emissions.for_position(pos))
            else:
                emits.append(self.emissions.for_key(char))

        index = self.index
        states: List[Dict[str, float]] = [dict() for _ in range(len(typed) + 1)]
        states[0][""] = 0.0

        def put(bucket: Dict[str, float], prefix: str, score: float) -> None:
            if score > bucket.get(prefix, -math.inf):
                bucket[prefix] = score

        for i, char in enumerate(typed):
            current = states[i]
            if not current:
                continue
            if len(current) > self.BEAM_WIDTH:
                current = dict(sorted(current.items(), key=lambda kv: -kv[1])[: self.BEAM_WIDTH])
                states[i] = current
            emit = emits[i]
            nxt = states[i + 1]
            for prefix, score in current.items():
                # Substitution, which includes the exact key at cost 0.  An
                # unmapped character (apostrophe, punctuation) only matches
                # itself.
                if emit is None:
                    candidate = prefix + char
                    if index.is_live(candidate):
                        put(nxt, candidate, score)
                else:
                    for key, lp in emit.items():
                        candidate = prefix + key
                        if index.is_live(candidate):
                            put(nxt, candidate, score + lp)
                # Extra click: the typed character was spurious.
                put(nxt, prefix, score + self.LOG_EXTRA)
                # Omitted click: the word has a character that was never
                # typed, then the typed one lands as above.
                for missing in index.children(prefix):
                    grown = prefix + missing
                    if emit is None:
                        candidate = grown + char
                        if index.is_live(candidate):
                            put(nxt, candidate, score + self.LOG_OMIT)
                    else:
                        for key, lp in emit.items():
                            candidate = grown + key
                            if index.is_live(candidate):
                                put(nxt, candidate, score + self.LOG_OMIT + lp)
                # Transposition of two adjacent typed characters, taken
                # literally: a swap is a motor-ordering error, not a spatial one.
                if i + 1 < len(typed) and typed[i + 1] != char:
                    candidate = prefix + typed[i + 1] + char
                    if index.is_live(candidate):
                        put(states[i + 2], candidate, score + self.LOG_SWAP)

        final = states[len(typed)]
        if len(final) > self.BEAM_WIDTH:
            final = dict(sorted(final.items(), key=lambda kv: -kv[1])[: self.BEAM_WIDTH])
        scored: Dict[str, float] = {}
        best_path: Dict[str, float] = {}
        for prefix, score in final.items():
            for freq, word in index.completions(prefix):
                value = score + self.FREQ_WEIGHT * math.log1p(freq)
                if value > scored.get(word, -math.inf):
                    scored[word] = value
                    best_path[word] = score
        if not scored:
            return []
        self._protect_exact_completions(typed, scored, best_path)
        ranked = sorted(scored.items(), key=lambda kv: -kv[1])[:n]
        best = ranked[0][1]
        return [(word, math.exp(value - best)) for word, value in ranked]

    def _protect_exact_completions(
        self, typed: str, scored: Dict[str, float], best_path: Dict[str, float]
    ) -> None:
        """What was typed is evidence too.

        When the typed prefix is itself live, a candidate reached only by
        paths costing more than one cheap edit is moved just below the best
        of the exact prefix's own completions, its order among its peers
        kept.  For a common word this changes nothing, its exact path
        already won; for a rare, pack or freshly learned word it is the
        difference between being offered and not, because base counts are
        rank-derived (up to 9,885) and a word typed three times sits at 3,
        so on frequency alone a typed ``zorb`` ranked ``spent`` (four slips
        away) above ``zorblat``.  One cheap edit still competes on
        frequency, which is what keeps ``teh`` offering ``the`` ahead of the
        rare word that happens to start with ``teh``.
        """
        if not self.index.is_live(typed):
            return
        exact = {word for _, word in self.index.completions(typed)}
        exact_best = max((scored[w] for w in exact if w in scored), default=None)
        if exact_best is None:
            return
        clamped = [
            word
            for word, path in best_path.items()
            if word not in exact and path < self.FREQUENCY_MAY_BUY and scored[word] >= exact_best
        ]
        if not clamped:
            return
        shift = max(scored[w] for w in clamped) - exact_best + 1e-3
        for word in clamped:
            scored[word] -= shift
