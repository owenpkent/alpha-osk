"""Behavioral parity coverage for the compact PrefixIndex representation."""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Mapping

import pytest

from src.prediction.fuzzy_recognizer import QWERTY_POSITIONS
from src.prediction.prefix_beam import PrefixBeam, PrefixIndex, SpatialEmissions


class ReferencePrefixIndex:
    """The public PrefixIndex behavior from baseline cb101da."""

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
        self._freq = {word: float(frequency) for word, frequency in dictionary.items() if word}
        self._words = sorted(self._freq)
        self._live: set[str] = set()
        children: dict[str, set[str]] = defaultdict(set)
        top: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for word, frequency in self._freq.items():
            for i in range(1, len(word) + 1):
                prefix = word[:i]
                self._live.add(prefix)
                if i < len(word):
                    children[prefix].add(word[i])
                if i <= precompute_len:
                    top[prefix].append((frequency, word))
        self._children = {prefix: "".join(sorted(chars)) for prefix, chars in children.items()}
        self._top: dict[str, list[tuple[float, str]]] = {}
        for prefix, entries in top.items():
            entries.sort(reverse=True)
            self._top[prefix] = entries[:top_k]

    def __len__(self) -> int:
        return len(self._live)

    def update_word(self, word: str, freq: float) -> None:
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
            entries = [
                (frequency, entry)
                for frequency, entry in self._top.get(prefix, [])
                if entry != word
            ]
            entries.append((freq, word))
            entries.sort(reverse=True)
            self._top[prefix] = entries[: self.top_k]

    def is_live(self, prefix: str) -> bool:
        return prefix in self._live

    def children(self, prefix: str) -> str:
        return self._children.get(prefix, "")

    def completions(self, prefix: str) -> list[tuple[float, str]]:
        if len(prefix) <= self.precompute_len:
            return self._top.get(prefix, [])
        lo = bisect.bisect_left(self._words, prefix)
        hi = bisect.bisect_left(
            self._words, prefix + "\uffff", lo, min(len(self._words), lo + self.max_scan)
        )
        found = [(self._freq[word], word) for word in self._words[lo:hi] if word.startswith(prefix)]
        found.sort(reverse=True)
        return found[: self.top_k]


def _prefixes(words: Mapping[str, float], *extra: str) -> list[str]:
    values = set(extra)
    values.add("")
    for word in words:
        values.update(word[:i] for i in range(1, len(word) + 1))
    return sorted(values)


def _assert_same_public_state(
    current: PrefixIndex,
    reference: ReferencePrefixIndex,
    prefixes: list[str],
) -> None:
    assert len(current) == len(reference)
    for prefix in prefixes:
        assert current.is_live(prefix) == reference.is_live(prefix), prefix.encode(
            "utf-8", "surrogatepass"
        )
        assert current.children(prefix) == reference.children(prefix), prefix.encode(
            "utf-8", "surrogatepass"
        )
        assert current.completions(prefix) == reference.completions(prefix), prefix.encode(
            "utf-8", "surrogatepass"
        )


@pytest.mark.parametrize(
    "top_k,precompute_len,max_scan",
    [(1, 1, 2), (3, 2, 4), (8, 4, 2000)],
)
def test_initial_index_matches_the_reference_across_all_public_methods(
    top_k, precompute_len, max_scan
):
    dictionary = {
        "cat": 4,
        "car": 4,
        "cats": 2,
        "café": 3,
        "dog": 5,
        "naïve": 6,
        "猫": 7,
        "猫咪": 8,
        "😀go": 9,
        "": 100,
    }
    options = {
        "top_k": top_k,
        "precompute_len": precompute_len,
        "max_scan": max_scan,
    }
    current = PrefixIndex(dictionary, **options)
    reference = ReferencePrefixIndex(dictionary, **options)

    _assert_same_public_state(
        current,
        reference,
        _prefixes(dictionary, "cab", "doz", "猫猫", "😀gone"),
    )


def test_tied_completions_keep_the_reference_order_in_base_and_updates():
    dictionary = {"alpha": 5, "alpine": 5, "alps": 5, "algebra": 5}
    current = PrefixIndex(dictionary, top_k=3, precompute_len=2)
    reference = ReferencePrefixIndex(dictionary, top_k=3, precompute_len=2)

    assert current.completions("al") == [(5.0, "alps"), (5.0, "alpine"), (5.0, "alpha")]
    current.update_word("alto", 5)
    reference.update_word("alto", 5)
    assert current.completions("al") == reference.completions("al")
    assert current.completions("al") == [(5.0, "alto"), (5.0, "alps"), (5.0, "alpine")]


def test_long_prefix_completions_preserve_the_max_scan_boundary():
    dictionary = {
        "prefaa": 1,
        "prefab": 100,
        "prefac": 90,
        "prefad": 80,
        "prefae": 70,
        "other": 1000,
    }
    current = PrefixIndex(dictionary, top_k=8, precompute_len=2, max_scan=3)
    reference = ReferencePrefixIndex(dictionary, top_k=8, precompute_len=2, max_scan=3)

    assert current.completions("pref") == reference.completions("pref")
    assert current.completions("pref") == [
        (100.0, "prefab"),
        (90.0, "prefac"),
        (1.0, "prefaa"),
    ]

    current.update_word("prefa0", 500)
    reference.update_word("prefa0", 500)
    assert current.completions("pref") == reference.completions("pref")
    assert current.completions("pref") == [
        (500.0, "prefa0"),
        (100.0, "prefab"),
        (1.0, "prefaa"),
    ]


def test_monotonic_and_new_word_updates_match_after_every_step():
    dictionary = {"cat": 3, "cats": 2, "car": 1, "dog": 4}
    current = PrefixIndex(dictionary, top_k=3, precompute_len=2)
    reference = ReferencePrefixIndex(dictionary, top_k=3, precompute_len=2)
    updates = [
        ("", 999),
        ("cat", 2),
        ("cat", 3),
        ("car", 10),
        ("cab", 10),
        ("cabin", 11),
        ("café", 12),
        ("猫咪", 13),
    ]
    known = dict(dictionary)
    for word, frequency in updates:
        current.update_word(word, frequency)
        reference.update_word(word, frequency)
        if word:
            known[word] = max(float(frequency), float(known.get(word, float("-inf"))))
        _assert_same_public_state(
            current,
            reference,
            _prefixes(known, "ca", "cabx", "do", "猫", "猫猫"),
        )


def test_empty_index_and_lone_surrogate_follow_reference_behavior():
    current = PrefixIndex({})
    reference = ReferencePrefixIndex({})
    _assert_same_public_state(current, reference, ["", "a", "😀"])

    encoded = b"a\xed\xa0\x80b"
    word = encoded.decode("utf-8", "surrogatepass")
    current.update_word(word, 7)
    reference.update_word(word, 7)
    _assert_same_public_state(current, reference, _prefixes({word: 7}, "a", word + "x"))
    safe = [
        (frequency, candidate.encode("utf-8", "surrogatepass"))
        for frequency, candidate in current.completions("a")
    ]
    assert safe == [(7.0, encoded)]


def test_hash_collisions_preserve_public_prefix_behavior(monkeypatch):
    from src.prediction import packed_prefixes

    monkeypatch.setattr(packed_prefixes, "hash", lambda _prefix: 0, raising=False)
    dictionary = {
        "alpha": 8,
        "alpine": 7,
        "beta": 6,
        "café": 5,
        "猫咪": 4,
        "😀go": 3,
    }
    current = PrefixIndex(dictionary, top_k=3, precompute_len=2)
    reference = ReferencePrefixIndex(dictionary, top_k=3, precompute_len=2)
    prefixes = _prefixes(dictionary, "", "alpz", "missing", "猫猫", "😀gone")
    _assert_same_public_state(current, reference, prefixes)

    current.update_word("alps", 9)
    reference.update_word("alps", 9)
    _assert_same_public_state(current, reference, _prefixes({**dictionary, "alps": 9}, *prefixes))


@pytest.mark.parametrize("typed", ["zorb", "sp", "ap", "teh", "helo", "", "z"])
def test_small_prefix_beam_outputs_match_the_reference(typed):
    dictionary = {
        "zorblat": 3,
        "spent": 9000,
        "spend": 8000,
        "the": 7000,
        "there": 6000,
        "hello": 5000,
        "help": 4000,
    }
    current_index = PrefixIndex(dictionary)
    reference_index = ReferencePrefixIndex(dictionary)
    current = PrefixBeam(current_index, SpatialEmissions(QWERTY_POSITIONS))
    reference = PrefixBeam(reference_index, SpatialEmissions(QWERTY_POSITIONS))

    assert current.complete(typed, 5) == reference.complete(typed, 5)

    current_index.update_word("zorbific", 10)
    reference_index.update_word("zorbific", 10)
    assert current.complete(typed, 5) == reference.complete(typed, 5)
