"""Fuzzy-correction recall benchmark: whole-word recovery and mid-word completion.

Two questions about :class:`~src.prediction.fuzzy_recognizer.FuzzyRecognizer`,
measured together because they share the same candidate generator and error
injector. First, whole-word recall: if a word is mistyped with exactly one
error (a spatial neighbour slip, an omitted key, an extra key, or a
transposition), how often is the intended word recovered in the top-1/3/5
candidates, and does that hold up as words get longer? Second, mid-word
recall: while the user is still typing, how often is the intended word (or at
least a word sharing its correct prefix) recoverable from a short, possibly
also mistyped, prefix?

What the numbers do NOT mean: every error here is synthetically injected by
this script (using the recognizer's own spatial neighbour table, so a "slip"
always lands on a physically adjacent key), not drawn from logged real typing
mistakes, so this measures the algorithm's internal behaviour rather than a
real-world correction rate. Each run loads a fresh instance of the static
dictionary named by ``--dictionary``; there is no persisted model here (unlike
``scripts/bench/ksr.py``, this script never touches ``--model-dir``, there
being no learned state to isolate).

Usage:
    python scripts/bench/fuzzy.py
    python scripts/bench/fuzzy.py --n 1000 --seed 42
    python scripts/bench/fuzzy.py --dictionary data/google-20000-supplement.txt
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Sequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # see ksr.py: same package import chain

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.prediction.fuzzy_recognizer import (  # noqa: E402
    FuzzyRecognizer,
    FuzzyWordGenerator,
    SpatialKeyModel,
)

DEFAULT_DICTIONARY = REPO_ROOT / "data" / "google-10000-english-usa-no-swears.txt"


class ErrorInjector:
    """Single-edit OSK typo generator, built on the recognizer's own spatial table.

    Using the recognizer's neighbour lookup (rather than a hand-picked
    substitution table) keeps every injected "slip" a physically plausible
    mistake on the same key layout the recognizer itself reasons about.
    """

    def __init__(self, spatial_model: SpatialKeyModel, rng: random.Random) -> None:
        self._spatial_model = spatial_model
        self._rng = rng

    def _neighbours(self, char: str) -> list[str]:
        near = [k for k in self._spatial_model.get_nearby_keys(char) if k != char and k.isalpha()]
        return near or [char]

    def slip(self, word: str) -> str:
        """Substitute one character for a physically adjacent key."""
        i = self._rng.randrange(len(word))
        return word[:i] + self._rng.choice(self._neighbours(word[i])) + word[i + 1 :]

    def drop(self, word: str) -> str:
        """Omit one keystroke."""
        i = self._rng.randrange(len(word))
        return word[:i] + word[i + 1 :]

    def dupe(self, word: str) -> str:
        """Double one keystroke (a lingering press registering twice)."""
        i = self._rng.randrange(len(word))
        return word[:i] + word[i] + word[i:]

    def swap(self, word: str) -> str:
        """Transpose two adjacent characters."""
        if len(word) < 2:
            return word
        i = self._rng.randrange(len(word) - 1)
        return word[:i] + word[i + 1] + word[i] + word[i + 2 :]


def whole_word_recall(
    generator: FuzzyWordGenerator,
    words: Sequence[str],
    mutator: Callable[[str], str],
    n: int,
    rng: random.Random,
) -> tuple[int, int, int, int, float]:
    """Return (hits@1, hits@3, hits@5, total, ms/query) for one error type."""
    hits1 = hits3 = hits5 = total = 0
    sample = rng.sample(list(words), min(n, len(words)))
    t0 = time.perf_counter()
    for word in sample:
        typed = mutator(word)
        if typed == word:
            continue
        candidates = [c for c, _ in generator.generate_candidates(typed)]
        total += 1
        if candidates[:1] == [word]:
            hits1 += 1
        if word in candidates[:3]:
            hits3 += 1
        if word in candidates[:5]:
            hits5 += 1
    elapsed_ms = (time.perf_counter() - t0) / max(total, 1) * 1000
    return hits1, hits3, hits5, total, elapsed_ms


def recall_by_length(
    generator: FuzzyWordGenerator,
    injector: ErrorInjector,
    pool: Sequence[str],
    n: int,
    rng: random.Random,
) -> tuple[int, int]:
    """Top-3 recall under a neighbour slip, for one word-length bucket."""
    hits = total = 0
    for word in rng.sample(list(pool), min(n, len(pool))):
        typed = injector.slip(word)
        if typed == word:
            continue
        candidates = [c for c, _ in generator.generate_candidates(typed)]
        total += 1
        if word in candidates[:3]:
            hits += 1
    return hits, total


def clean_prefix_completion(
    generator: FuzzyWordGenerator,
    dictionary: Mapping[str, float],
    n: int,
    rng: random.Random,
) -> tuple[int, int]:
    """Is a correctly-typed short prefix enough to surface the intended word?

    Tests prefix lengths 3 and 4 of words at least 6 letters long (so the
    prefix is genuinely partial), with no injected error: this is the floor
    the mid-word section measures against.
    """
    pool = [w for w in dictionary if len(w) >= 6 and w.isalpha()]
    hit = total = 0
    for word in rng.sample(pool, min(n, len(pool))):
        for prefix_len in (3, 4):
            prefix = word[:prefix_len]
            candidates = [c for c, _ in generator.generate_candidates(prefix)][:5]
            total += 1
            hit += word in candidates
    return hit, total


def mistyped_prefix_recall(
    generator: FuzzyWordGenerator,
    injector: ErrorInjector,
    dictionary: Mapping[str, float],
    prefix_len: int,
    n: int,
    rng: random.Random,
) -> tuple[int, int, int]:
    """Recall when the not-yet-finished prefix itself carries one slip.

    Reports two things: whether the exact intended word is recoverable, and
    the weaker "any word with the correct (untyped) prefix" bar, which is
    still a useful pill even when it is not the word the user meant.
    """
    pool = [w for w in dictionary if len(w) >= prefix_len + 2 and w.isalpha()]
    hit = hit_any = total = 0
    for word in rng.sample(pool, min(n, len(pool))):
        prefix = word[:prefix_len]
        typed = injector.slip(prefix)
        if typed == prefix:
            continue
        candidates = [c for c, _ in generator.generate_candidates(typed)][:5]
        total += 1
        hit += word in candidates
        hit_any += any(c.startswith(prefix) for c in candidates)
    return hit, hit_any, total


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fuzzy-correction recall benchmark (whole-word and mid-word).",
    )
    parser.add_argument(
        "--n", type=int, default=400, help="sample size per test cell (default: 400)"
    )
    parser.add_argument("--seed", type=int, default=7, help="random seed (default: 7)")
    parser.add_argument(
        "--dictionary",
        type=Path,
        default=DEFAULT_DICTIONARY,
        help=f"wordlist to load (default: {DEFAULT_DICTIONARY.relative_to(REPO_ROOT)})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rng = random.Random(args.seed)

    fr = FuzzyRecognizer()
    fr.load_dictionary(str(args.dictionary))
    generator = fr.word_generator
    dictionary = generator.dictionary
    print(f"dictionary: {len(dictionary)} words ({args.dictionary})")

    spatial_model = SpatialKeyModel()
    injector = ErrorInjector(spatial_model, rng)

    # The 2000 most frequent words of length >= 3: common enough to be worth
    # correcting, long enough that a single-character error is meaningful.
    ranked = sorted(dictionary, key=lambda x: -dictionary[x])
    words = [w for w in ranked if len(w) >= 3 and w.isalpha()][:2000]

    print("\n=== whole-word correction recall (target word is the intended one) ===")
    print(f"{'error type':<16} {'n':>5} {'top1':>7} {'top3':>7} {'top5':>7} {'ms/query':>10}")
    mutators: list[tuple[str, Callable[[str], str]]] = [
        ("neighbour slip", injector.slip),
        ("omitted key", injector.drop),
        ("extra key", injector.dupe),
        ("transposition", injector.swap),
    ]
    for label, mutator in mutators:
        hits1, hits3, hits5, total, ms = whole_word_recall(generator, words, mutator, args.n, rng)
        print(
            f"{label:<16} {total:5d} {hits1 / total:6.1%} {hits3 / total:6.1%} "
            f"{hits5 / total:6.1%} {ms:9.1f}"
        )

    print("\n=== recall by word length (neighbour slip) ===")
    print(f"{'length':<10} {'n':>5} {'top3':>7}")
    for lo, hi in ((3, 4), (5, 6), (7, 8), (9, 20)):
        pool = [w for w in words if lo <= len(w) <= hi]
        if len(pool) < 50:
            continue
        hits, total = recall_by_length(generator, injector, pool, args.n, rng)
        print(f"{f'{lo}-{hi}':<10} {total:5d} {hits / total:6.1%}")

    print("\n=== mid-word: clean prefix completion (len 3-4 of a >=6-letter word) ===")
    hit, total = clean_prefix_completion(generator, dictionary, args.n, rng)
    print(f"  target in top5: {hit / total:.1%}  (n={total})")

    print("\n=== mid-word: mistyped prefix (one neighbour slip inside the prefix) ===")
    print(f"{'prefix len':<12} {'n':>5} {'intended in top5':>18} {'any correct-prefix top5':>25}")
    for prefix_len in (3, 4, 5, 6):
        hit, hit_any, total = mistyped_prefix_recall(
            generator, injector, dictionary, prefix_len, args.n, rng
        )
        print(f"{prefix_len:<12} {total:5d} {hit / total:17.1%} {hit_any / total:24.1%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
