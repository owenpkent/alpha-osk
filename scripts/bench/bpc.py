"""Cross-entropy (bits per character) of the PPM character model on held-out text.

This is Dasher's intrinsic language-model metric: the mean over characters of
``-log2 p(true next char | preceding context)``, with no UI in the loop. It is
what decides whether the PPM model (trained and persisted, but out of the
word merge since 2026-09-03) earns a place back.

The model is built the way the shipped app builds it: a default
``PPMPredictor`` trained on ``data/training_corpus.txt`` (comment lines
removed, via ``HybridPredictor._read_training_corpus``), then written to and
reloaded from a scoped temporary model directory, which is what a second
launch does. The user's config directory is never read or written.

Held-out hygiene: the evaluation corpora are loaded through ``ksr.py``
(``load_corpus``, so they are normalised the same way). Any evaluation
sentence that also appears in the training corpus (after the same
normalisation) is excluded from scoring and counted, unless ``--keep-seen``
is passed. Each sentence is scored independently from an empty context.

Characters outside the model's alphabet are mapped to a space by the model's
own ``_normalize``. Scoring them as a space would flatter the model, so they
are skipped and counted instead. A probability of exactly zero would be an
infinite cost; it is floored at ``--floor`` (default 1e-12) and counted.

Usage::

    python scripts/bench/bpc.py
    python scripts/bench/bpc.py --corpus aac-dev --corpus aac-test --order 5
    python scripts/bench/bpc.py --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.bench.ksr import CORPORA, load_corpus, normalise  # noqa: E402
from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402
from src.prediction.ppm_predictor import PPMPredictor  # noqa: E402

DEFAULT_FLOOR = 1e-12


@dataclass
class BpcResult:
    corpus: str
    order: int
    sentences: int
    sentences_seen_in_training: int
    chars_scored: int
    unknown_chars: int
    zero_probability: int
    bits_per_char: float
    perplexity: float
    uniform_bits_per_char: float


def score_text(
    model: PPMPredictor, sentences: Sequence[str], floor: float = DEFAULT_FLOOR
) -> tuple[float, int, int, int]:
    """Return ``(total_bits, chars_scored, unknown_chars, zero_probability)``."""
    total_bits = 0.0
    scored = unknown = zeros = 0
    for sentence in sentences:
        for i, ch in enumerate(sentence.lower()):
            if ch not in model.alphabet:
                unknown += 1
                continue
            p = model.get_probabilities(sentence[:i]).get(ch, 0.0)
            if p <= 0.0:
                zeros += 1
                p = floor
            total_bits -= math.log2(p)
            scored += 1
    return total_bits, scored, unknown, zeros


def train_shipped_model(model_dir: Path, order: int) -> tuple[PPMPredictor, str]:
    """A PPM built as the app builds it, round-tripped through ``model_dir``."""
    text = HybridPredictor._read_training_corpus()
    if not text:
        raise SystemExit("data/training_corpus.txt is missing or empty")
    fresh = PPMPredictor(max_order=order)
    fresh.train(text)
    path = model_dir / "ppm_model.json"
    fresh.save(path)
    return PPMPredictor(max_order=order, model_path=path), text


def seen_in_training(sentences: Sequence[str], training_text: str) -> set[str]:
    """Evaluation sentences that also occur as a whole line of the training text."""
    trained = {normalise(line) for line in training_text.splitlines()}
    return {s for s in sentences if s in trained}


def evaluate(
    model: PPMPredictor,
    corpus: str,
    sentences: Sequence[str],
    training_text: str,
    *,
    keep_seen: bool = False,
    floor: float = DEFAULT_FLOOR,
) -> BpcResult:
    seen = seen_in_training(sentences, training_text)
    kept = list(sentences) if keep_seen else [s for s in sentences if s not in seen]
    bits, scored, unknown, zeros = score_text(model, kept, floor)
    bpc = bits / scored if scored else float("nan")
    return BpcResult(
        corpus=corpus,
        order=model.max_order,
        sentences=len(kept),
        sentences_seen_in_training=len(seen),
        chars_scored=scored,
        unknown_chars=unknown,
        zero_probability=zeros,
        bits_per_char=bpc,
        perplexity=2**bpc if scored else float("nan"),
        uniform_bits_per_char=math.log2(len(model.alphabet)),
    )


def print_table(results: Sequence[BpcResult]) -> None:
    header = (
        f"{'corpus':<9} {'order':>5} {'sents':>6} {'seen':>5} {'chars':>8} "
        f"{'unk':>5} {'p=0':>4} {'bits/char':>10} {'perplex':>8} {'uniform':>8}"
    )
    print(header)
    print("-" * len(header))
    for r in results:
        print(
            f"{r.corpus:<9} {r.order:>5} {r.sentences:>6} {r.sentences_seen_in_training:>5} "
            f"{r.chars_scored:>8} {r.unknown_chars:>5} {r.zero_probability:>4} "
            f"{r.bits_per_char:>10.3f} {r.perplexity:>8.2f} {r.uniform_bits_per_char:>8.3f}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bits-per-character cross-entropy of the PPM model on held-out text.",
    )
    parser.add_argument(
        "--corpus",
        action="append",
        choices=sorted(CORPORA),
        help="evaluation set, repeatable (default: all). "
        + "; ".join(f"{k}: {v}" for k, v in CORPORA.items()),
    )
    parser.add_argument(
        "--order",
        type=int,
        action="append",
        help="PPM max context order, repeatable (default: 8, the shipped value)",
    )
    parser.add_argument(
        "--keep-seen",
        action="store_true",
        help="score sentences that also occur in the training corpus (default: exclude them)",
    )
    parser.add_argument(
        "--floor",
        type=float,
        default=DEFAULT_FLOOR,
        help=f"floor for an exact-zero probability, counted (default: {DEFAULT_FLOOR})",
    )
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    corpora = args.corpus or sorted(CORPORA)
    orders = args.order or [8]

    results: list[BpcResult] = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="alpha-osk-bench-bpc-") as tmp:
        for order in orders:
            model, training_text = train_shipped_model(Path(tmp), order)
            for name in corpora:
                results.append(
                    evaluate(
                        model,
                        name,
                        load_corpus(name),
                        training_text,
                        keep_seen=args.keep_seen,
                        floor=args.floor,
                    )
                )
    elapsed = time.perf_counter() - started

    if args.json:
        print(json.dumps({"results": [asdict(r) for r in results], "seconds": elapsed}, indent=2))
    else:
        print_table(results)
        print(f"\n{elapsed:.1f}s; training: data/training_corpus.txt (seen lines excluded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
