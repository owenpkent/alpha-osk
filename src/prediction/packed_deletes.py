"""Compact immutable storage for a SymSpell deletion index."""

from __future__ import annotations

from array import array
from collections.abc import Callable, Iterable, Iterator

_UINT32_MAX = (1 << 32) - 1


class PackedDeletes:
    """Deletion keys and postings stored in contiguous byte and uint32 arrays."""

    __slots__ = (
        "_hash_slots",
        "_key_bytes",
        "_key_offsets",
        "_posting_offsets",
        "_postings",
        "_words",
    )

    def __init__(
        self,
        words: tuple[str, ...],
        key_bytes: bytes,
        key_offsets: array[int],
        posting_offsets: array[int],
        postings: array[int],
        hash_slots: array[int],
    ) -> None:
        self._words = words
        self._key_bytes = key_bytes
        self._key_offsets = key_offsets
        self._posting_offsets = posting_offsets
        self._postings = postings
        self._hash_slots = hash_slots

    @classmethod
    def build(
        cls,
        words: Iterable[str],
        variants_for: Callable[[str], Iterable[str]],
    ) -> PackedDeletes:
        """Build a packed index while preserving input order in every posting."""
        if array("I").itemsize != 4:
            raise RuntimeError("PackedDeletes requires a 32-bit unsigned-int array type")

        word_table = tuple(words)
        if len(word_table) > _UINT32_MAX:
            raise OverflowError("too many words for the packed deletion index")

        counts: dict[str, int] = {}
        for word in word_table:
            for key in variants_for(word):
                counts[key] = counts.get(key, 0) + 1

        key_count = len(counts)
        if key_count > _UINT32_MAX:
            raise OverflowError("too many deletion keys for the packed index")
        table_size = 1
        while table_size < key_count * 2:
            table_size <<= 1

        hash_slots = array("I", [0]) * table_size
        key_data = bytearray()
        key_offsets = array("I", [0])
        posting_offsets = array("I", [0])
        posting_total = 0
        table_mask = table_size - 1

        for key_index, (key, count) in enumerate(counts.items()):
            encoded = key.encode("utf-8", errors="surrogatepass")
            key_data.extend(encoded)
            if len(key_data) > _UINT32_MAX:
                raise OverflowError("deletion keys exceed the packed uint32 address space")
            key_offsets.append(len(key_data))

            posting_start = posting_total
            posting_total += count
            if posting_total > _UINT32_MAX:
                raise OverflowError("deletion postings exceed the packed uint32 address space")
            posting_offsets.append(posting_total)
            counts[key] = posting_start

            slot = hash(key) & table_mask
            while hash_slots[slot]:
                slot = (slot + 1) & table_mask
            hash_slots[slot] = key_index + 1

        postings = array("I", [0]) * posting_total
        for word_id, word in enumerate(word_table):
            for key in variants_for(word):
                cursor = counts[key]
                postings[cursor] = word_id
                counts[key] = cursor + 1

        return cls(
            word_table,
            bytes(key_data),
            key_offsets,
            posting_offsets,
            postings,
            hash_slots,
        )

    def iter_words(self, key: str) -> Iterator[str]:
        """Yield words for ``key`` in their original insertion order."""
        key_index = self._find_key(key)
        if key_index is None:
            return
        start = self._posting_offsets[key_index]
        end = self._posting_offsets[key_index + 1]
        for posting_index in range(start, end):
            yield self._words[self._postings[posting_index]]

    def _find_key(self, key: str) -> int | None:
        encoded = key.encode("utf-8", errors="surrogatepass")
        table_mask = len(self._hash_slots) - 1
        slot = hash(key) & table_mask

        while True:
            stored = self._hash_slots[slot]
            if not stored:
                return None
            key_index = stored - 1
            start = self._key_offsets[key_index]
            end = self._key_offsets[key_index + 1]
            if end - start == len(encoded) and self._key_bytes.startswith(encoded, start, end):
                return key_index
            slot = (slot + 1) & table_mask

    def __len__(self) -> int:
        return len(self._key_offsets) - 1

    @property
    def posting_count(self) -> int:
        """Number of word IDs stored across all deletion-key postings."""
        return len(self._postings)
