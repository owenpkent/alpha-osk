"""Compact immutable storage for prefix-beam lookup tables."""

from __future__ import annotations

from array import array
from collections import defaultdict
from collections.abc import Iterator, Mapping

_UINT32_MAX = (1 << 32) - 1
_UINT64_MASK = (1 << 64) - 1


class PackedPrefixes:
    """Packed live-prefix keys with child text and top-completion word IDs."""

    __slots__ = (
        "_child_bytes",
        "_child_offsets",
        "_hash_slots",
        "_key_bytes",
        "_key_hashes",
        "_key_offsets",
        "_top_offsets",
        "_top_word_ids",
        "_words",
    )

    def __init__(
        self,
        words: tuple[str, ...],
        key_bytes: bytes,
        key_hashes: array[int],
        key_offsets: array[int],
        child_bytes: bytes,
        child_offsets: array[int],
        top_offsets: array[int],
        top_word_ids: array[int],
        hash_slots: array[int],
    ) -> None:
        self._words = words
        self._key_bytes = key_bytes
        self._key_hashes = key_hashes
        self._key_offsets = key_offsets
        self._child_bytes = child_bytes
        self._child_offsets = child_offsets
        self._top_offsets = top_offsets
        self._top_word_ids = top_word_ids
        self._hash_slots = hash_slots

    @classmethod
    def build(
        cls,
        frequencies: Mapping[str, float],
        *,
        top_k: int,
        precompute_len: int,
    ) -> PackedPrefixes:
        """Build packed rows with the same ordering as the Python index."""
        if array("I").itemsize != 4 or array("Q").itemsize != 8:
            raise RuntimeError("PackedPrefixes requires 32-bit and 64-bit unsigned arrays")

        words = tuple(frequencies)
        if len(words) > _UINT32_MAX:
            raise OverflowError("too many words for the packed prefix index")
        word_ids = {word: word_id for word_id, word in enumerate(words)}

        live: dict[str, None] = {}
        children: dict[str, set[str]] = defaultdict(set)
        top: dict[str, list[tuple[float, str]]] = defaultdict(list)
        for word, frequency in frequencies.items():
            for length in range(1, len(word) + 1):
                prefix = word[:length]
                live[prefix] = None
                if length < len(word):
                    children[prefix].add(word[length])
                if length <= precompute_len:
                    top[prefix].append((frequency, word))

        key_count = len(live)
        if key_count > _UINT32_MAX:
            raise OverflowError("too many keys for the packed prefix index")
        table_size = 1
        while table_size < key_count * 2:
            table_size <<= 1
        table_mask = table_size - 1

        hash_slots = array("I", [0]) * table_size
        key_data = bytearray()
        key_hashes = array("Q")
        key_offsets = array("I", [0])
        child_data = bytearray()
        child_offsets = array("I", [0])
        top_offsets = array("I", [0])
        top_word_ids = array("I")

        for key_index, prefix in enumerate(live):
            key_hash = hash(prefix) & _UINT64_MASK
            encoded = prefix.encode("utf-8", errors="surrogatepass")
            key_data.extend(encoded)
            if len(key_data) > _UINT32_MAX:
                raise OverflowError("prefix keys exceed the packed uint32 address space")
            key_offsets.append(len(key_data))
            key_hashes.append(key_hash)

            following = "".join(sorted(children.get(prefix, ())))
            child_data.extend(following.encode("utf-8", errors="surrogatepass"))
            if len(child_data) > _UINT32_MAX:
                raise OverflowError("prefix children exceed the packed uint32 address space")
            child_offsets.append(len(child_data))

            entries = top.get(prefix)
            if entries:
                entries.sort(reverse=True)
                top_word_ids.extend(word_ids[word] for _, word in entries[:top_k])
                if len(top_word_ids) > _UINT32_MAX:
                    raise OverflowError("prefix completions exceed the packed uint32 address space")
            top_offsets.append(len(top_word_ids))

            slot = key_hash & table_mask
            while hash_slots[slot]:
                slot = (slot + 1) & table_mask
            hash_slots[slot] = key_index + 1

        return cls(
            words,
            bytes(key_data),
            key_hashes,
            key_offsets,
            bytes(child_data),
            child_offsets,
            top_offsets,
            top_word_ids,
            hash_slots,
        )

    def contains(self, prefix: str) -> bool:
        """Return whether ``prefix`` is in the immutable base."""
        return self.find(prefix) is not None

    def children_at(self, key_index: int) -> str:
        """Return the child row for a key index returned by :meth:`find`."""
        start = self._child_offsets[key_index]
        end = self._child_offsets[key_index + 1]
        return self._child_bytes[start:end].decode("utf-8", errors="surrogatepass")

    def iter_top_words_at(self, key_index: int) -> Iterator[str]:
        """Yield the top row for a key index returned by :meth:`find`."""
        start = self._top_offsets[key_index]
        end = self._top_offsets[key_index + 1]
        for row_index in range(start, end):
            yield self._words[self._top_word_ids[row_index]]

    def find(self, prefix: str) -> int | None:
        """Return the packed key index for ``prefix``, or ``None``."""
        key_hash = hash(prefix) & _UINT64_MASK
        encoded: bytes | None = None
        table_mask = len(self._hash_slots) - 1
        slot = key_hash & table_mask

        while True:
            stored = self._hash_slots[slot]
            if not stored:
                return None
            key_index = stored - 1
            if self._key_hashes[key_index] == key_hash:
                if encoded is None:
                    encoded = prefix.encode("utf-8", errors="surrogatepass")
                start = self._key_offsets[key_index]
                end = self._key_offsets[key_index + 1]
                if end - start == len(encoded) and self._key_bytes.startswith(encoded, start, end):
                    return key_index
            slot = (slot + 1) & table_mask

    def __len__(self) -> int:
        return len(self._key_offsets) - 1
