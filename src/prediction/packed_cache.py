"""Process-level reuse of the immutable packed indexes.

``PackedDeletes`` and ``PackedPrefixes`` are built from a word-frequency
mapping and never mutate afterwards: every method on them past ``__init__``
is a read accessor, and everything that changes as the user types goes into
the mutable overlay that ``SymSpell`` and ``PrefixIndex`` keep beside them.
That is what makes them shareable between instances, and it is the whole
reason the packed representation is worth having beyond its smaller
footprint.

It matters because building them is not cheap at the shipped vocabulary
size: the deletion index alone walks about 4.3 million deletion variants.
The application pays that once per launch, so on its own it would only be a
startup cost. The test suite builds a predictor roughly 1,300 times, which
turned it into a suite that no CI shard could finish inside its timeout.

The cache is keyed on a digest of the exact input rather than on anything
about where the words came from, because a caller can hand us any mapping,
and two callers with equal mappings must get equal indexes. Building the
digest costs a single pass over the mapping, which is three orders of
magnitude cheaper than the build it avoids.

**The digest is the whole key, so it has to be one that cannot collide in
practice.** A 128-bit BLAKE2b over the items is; Python's own ``hash`` is
not, and a collision here would not raise, it would silently handle one
vocabulary's predictions with another vocabulary's index.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from typing import Any, Callable, Mapping, Tuple, TypeVar

T = TypeVar("T")

#: Two slots, not more. The shipped dictionary dominates in both the
#: application and the suite, and a cached entry retains the whole packed
#: structure for the life of the process, so this is a memory ceiling as
#: much as a hit-rate knob: a larger cache would keep several vocabularies'
#: buffers alive at once for a hit rate that measurement did not support.
_MAX_ENTRIES = 2

_cache: "OrderedDict[bytes, Any]" = OrderedDict()

_hits = 0
_misses = 0


def _digest(mapping: Mapping[str, float], params: Tuple[Any, ...]) -> bytes:
    """A collision-resistant key for ``mapping`` under ``params``.

    ``repr`` of the items list in one call rather than a per-item loop:
    the loop was measurably slower at the shipped size, and this runs on
    every construction, including the ones that go on to hit the cache.
    """
    digest = hashlib.blake2b(digest_size=16)
    digest.update(repr(params).encode("utf-8"))
    digest.update(b"\x00")
    digest.update(repr(list(mapping.items())).encode("utf-8"))
    return digest.digest()


def build_cached(
    builder: Callable[[], T],
    mapping: Mapping[str, float],
    params: Tuple[Any, ...],
) -> T:
    """Return a shared index for ``mapping``, building it only if unseen.

    ``builder`` is a thunk rather than the class, so this module stays
    ignorant of the two build signatures and neither of them has to grow a
    cache parameter.
    """
    global _hits, _misses

    key = _digest(mapping, params)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        _hits += 1
        return cached  # type: ignore[no-any-return]

    _misses += 1
    built = builder()
    _cache[key] = built
    while len(_cache) > _MAX_ENTRIES:
        _cache.popitem(last=False)
    return built


def cache_stats() -> Tuple[int, int, int]:
    """``(hits, misses, entries)``, for tests and the benchmarks."""
    return _hits, _misses, len(_cache)


def clear_cache() -> None:
    """Drop every shared index.

    For tests that care about build behaviour rather than its result, and
    for anything measuring a cold build.
    """
    global _hits, _misses
    _cache.clear()
    _hits = 0
    _misses = 0
