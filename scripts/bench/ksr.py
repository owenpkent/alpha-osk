"""Potential keystroke-savings (Trnka and McCoy style) benchmark for the hybrid engine.

For every word in a small held-out set of everyday sentences, this simulates a
user who reads the prediction bar perfectly: at each prefix length (starting
from zero letters typed) it asks :class:`HybridPredictor` for its top-N pills
and pretends the user clicks the instant the intended word appears. Keystroke
Savings Rate (KSR) compares the clicks that idealised user needed against the
cost of typing every letter of every word plus one space. Alongside KSR this
prints the next-word hit rate at an empty prefix, the share of words the
engine never predicted at any prefix length, the median prefix length at
first hit, and predict() latency (p50/p95).

What the numbers do NOT mean: the held-out sentences are a small, hand-written
sample (30 sentences), not logged real-world typing, and the "instant click"
model is an upper bound, a real user reads and clicks slower and sometimes
misses a pill that was there. This script never injects typing errors; for
error-corruption recall see ``scripts/bench/fuzzy.py``. Unless ``--model-dir``
is given, every run builds a brand-new model in a fresh temporary directory,
so numbers reflect a cold-start engine with no personal learning (except
where ``--learn-half`` explicitly simulates some), and the live model under
the user's config dir is never read or written.

Usage:
    python scripts/bench/ksr.py
    python scripts/bench/ksr.py --pills 3 --conditions full,no-ppm,no-fuzzy
    python scripts/bench/ksr.py --conditions rank,rrf,linear,loglinear
    python scripts/bench/ksr.py --learn-half
    python scripts/bench/ksr.py --model-dir C:\\scratch\\some-model-dir
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Sequence

# Must be set before anything under src/ pulls in PySide6 (HybridPredictor is
# a QObject), or a headless run can crash trying to open a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import logging  # noqa: E402

logging.disable(logging.CRITICAL)  # keep INFO-level engine-init noise off the table

from src.prediction.fuzzy_recognizer import SpatialKeyModel  # noqa: E402
from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402

# 30 held-out everyday sentences. Written by hand, not drawn from
# data/training_corpus.txt, so the engine has never seen them at construction.
HELD_OUT: list[str] = """
could you move my appointment to thursday afternoon instead
i will be a few minutes late because the bus was delayed
thanks for sending that over i will take a look tonight
we need to pick up milk and bread on the way home
the meeting has been moved to the small conference room
let me know if there is anything else you need from me
my physical therapist wants me to do these exercises every day
i think the new medication is working better than the old one
can we talk about this tomorrow when i have more time
please call me back when you get this message
the weather is supposed to be nice this weekend
i finished reading the book you recommended last month
do you want to get lunch somewhere near the office
the package should arrive sometime on monday or tuesday
i am not sure what time the store closes on sundays
she said the results would be ready by the end of the week
we should probably leave early to avoid the traffic
thank you so much for all of your help with this
i have a question about the invoice you sent last week
the kids are excited about the trip to the beach
he asked me to remind you about the dentist appointment
i would rather stay home and watch a movie tonight
the doctor said everything looks normal on the scan
could you send me the address for the new restaurant
i need to renew my prescription before it runs out
we had a great time at the party on saturday night
sorry i missed your call earlier i was in a meeting
the printer in the hallway is out of paper again
it might rain later so bring an umbrella just in case
i will send you the updated version first thing tomorrow
""".strip().splitlines()

VALID_CONDITIONS = {
    "full",
    "no-ppm",
    "no-fuzzy",
    "legacy-fuzzy",
    "ppm-merge",
    "rank",
    "rrf",
    "linear",
    "loglinear",
}


@dataclass
class KsrResult:
    """One condition's row: every rate is a fraction in [0, 1], not a percent."""

    label: str
    ksr: float
    next_word_hit_rate: float
    never_predicted: float
    median_prefix_at_hit: float | None
    p50_latency_ms: float
    p95_latency_ms: float


def _warmup(hp: HybridPredictor, calls: int = 100) -> None:
    """Absorb the engine's cold-cache cost before any timed measurement.

    The first ~100 predict() calls against a freshly built predictor run
    roughly 6x slower than steady state, which is warm-up cost (cache and
    lazy-init effects inside the n-gram/PPM/fuzzy tables), not a property of
    whichever condition happens to run first.
    """
    done = 0
    for sentence in HELD_OUT:
        for i in range(len(sentence) + 1):
            hp.predict(sentence[:i], 5)
            done += 1
            if done >= calls:
                return


def _slipped(word: str, at: int, rng: random.Random) -> str:
    """*word* with the character at *at* replaced by one of its spatial neighbours."""
    model = _SPATIAL
    options = [k for k in model.get_nearby_keys(word[at]) if k != word[at] and k.isalpha()]
    if not options:
        return word
    return word[:at] + rng.choice(options) + word[at + 1 :]


_SPATIAL = SpatialKeyModel()


def measure(
    hp: HybridPredictor,
    label: str,
    sentences: Sequence[str],
    top: int,
    *,
    slip_at: int | None = None,
) -> KsrResult:
    """Run the greedy keystroke-savings simulation over *sentences*.

    For every word, at every prefix length from empty to fully-typed, this
    asks the engine for its top-*top* predictions and records the first
    prefix length at which the intended word appears (the idealised
    "clicked the instant it showed up" user). Savings are counted against
    typing every letter of the word plus one trailing space.
    """
    # A fixed seed per measurement, so every condition slips the same words
    # the same way and the rows stay comparable.
    rng = random.Random(23)
    clicks_with = clicks_without = 0
    next_word_hits = next_word_total = 0
    first_hit_prefixes: list[int] = []
    latencies_s: list[float] = []

    for sentence in sentences:
        words = sentence.split()
        for wi, word in enumerate(words):
            context = " ".join(words[:wi]) + (" " if wi else "")
            baseline = len(word) + 1
            used = baseline
            # With ``slip_at`` set, one click of every word of four letters or
            # more lands on a neighbouring key and is never corrected by the
            # simulated user: the question is what the bar does about it.
            shown = word
            if slip_at is not None and len(word) > slip_at + 2:
                shown = _slipped(word, slip_at, rng)
            for i in range(len(word)):
                t0 = time.perf_counter()
                preds = hp.predict(context + shown[:i], top)
                latencies_s.append(time.perf_counter() - t0)
                if i == 0:
                    next_word_total += 1
                    next_word_hits += word in preds
                if word in preds:
                    used = i + 1
                    first_hit_prefixes.append(i)
                    break
            clicks_with += used
            clicks_without += baseline

    sorted_lat = sorted(latencies_s)
    p50 = statistics.median(sorted_lat) if sorted_lat else 0.0
    p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0.0

    return KsrResult(
        label=label,
        ksr=1 - clicks_with / clicks_without,
        next_word_hit_rate=next_word_hits / next_word_total,
        never_predicted=1 - len(first_hit_prefixes) / next_word_total,
        median_prefix_at_hit=(
            statistics.median(first_hit_prefixes) if first_hit_prefixes else None
        ),
        p50_latency_ms=p50 * 1000,
        p95_latency_ms=p95 * 1000,
    )


@contextmanager
def apply_condition(hp: HybridPredictor, name: str) -> Iterator[None]:
    """Temporarily reconfigure *hp* for one named ablation or merge strategy.

    Restores the prior state on exit, so every condition in ``--conditions``
    can run back-to-back against one predictor instance instead of paying
    full model construction per condition.
    """
    # PPMWordPredictor memoises completions per context, so a condition
    # timed after another over the same sentences would read a warm cache
    # (2.5 ms) where a user typing new text pays the full cost (26 ms).
    # Every condition starts cold.
    hp._ppm_word._completion_cache.clear()
    if name == "full":
        yield
        return
    if name == "no-ppm":
        previous_ppm = hp._enable_ppm
        hp._enable_ppm = False
        try:
            yield
        finally:
            hp._enable_ppm = previous_ppm
        return
    if name == "no-fuzzy":
        real_get_fuzzy = hp._fuzzy.get_fuzzy_predictions
        hp._fuzzy.get_fuzzy_predictions = lambda *a, **k: []  # type: ignore[method-assign]
        try:
            yield
        finally:
            hp._fuzzy.get_fuzzy_predictions = real_get_fuzzy  # type: ignore[method-assign]
        return
    if name == "ppm-merge":
        # Put the character model's word candidates back into the merge, the
        # way the engine ran before 2026-09-03.
        previous_merge = hp._ppm_in_merge
        hp._ppm_in_merge = True
        try:
            yield
        finally:
            hp._ppm_in_merge = previous_merge
        return
    if name == "legacy-fuzzy":
        # The pre-beam fuzzy source: whole-word correction offered mid-word.
        previous_mode = hp._fuzzy.prefix_completion
        hp._fuzzy.prefix_completion = False
        try:
            yield
        finally:
            hp._fuzzy.prefix_completion = previous_mode
        return
    if name in ("rank", "rrf", "linear", "loglinear"):
        previous_strategy = hp._merge_strategy
        hp._merge_strategy = name
        try:
            yield
        finally:
            hp._merge_strategy = previous_strategy
        return
    raise ValueError(f"unknown condition: {name!r}")


_HEADER = (
    f"{'condition':<20} {'KSR':>7} {'next-word hit':>15} {'never predicted':>17} "
    f"{'median prefix':>14} {'p50 ms':>8} {'p95 ms':>8}"
)


def _format_row(r: KsrResult) -> str:
    median = "n/a" if r.median_prefix_at_hit is None else f"{r.median_prefix_at_hit:g}"
    return (
        f"{r.label:<20} {r.ksr:6.1%} {r.next_word_hit_rate:14.1%} {r.never_predicted:16.1%} "
        f"{median:>14} {r.p50_latency_ms:8.1f} {r.p95_latency_ms:8.1f}"
    )


def print_table(results: Sequence[KsrResult]) -> None:
    print(_HEADER)
    for r in results:
        print(_format_row(r))


def run_learn_half(model_dir: Path, top: int) -> None:
    """Learn sentences 1-15 once, test on 16-30, and report before/after/oracle.

    "oracle" learns the test half into a second, otherwise-identical model
    (once, the same way "after" learns the train half) so the before/after
    gap can be read against an upper bound: what one pass over the exact
    test material itself buys, rather than an arbitrary ceiling.
    """
    train, test = HELD_OUT[:15], HELD_OUT[15:]
    print(f"learn-half: train on sentences 1-15, test on sentences 16-30 (n={len(test)})\n")

    hp = HybridPredictor(model_dir=model_dir / "before-after", enable_llm=False)
    _warmup(hp)
    before = measure(hp, "before learning", test, top)

    for sentence in train:
        hp.learn(sentence)
    after = measure(hp, "after learning train half", test, top)

    hp_oracle = HybridPredictor(model_dir=model_dir / "oracle", enable_llm=False)
    _warmup(hp_oracle)
    for sentence in test:
        hp_oracle.learn(sentence)
    oracle = measure(hp_oracle, "oracle (learned test half)", test, top)

    print_table([before, after, oracle])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Potential keystroke-savings benchmark for the hybrid prediction engine.",
    )
    parser.add_argument(
        "--pills", type=int, default=5, help="pills considered per predict() call (default: 5)"
    )
    parser.add_argument(
        "--conditions",
        type=str,
        default="full",
        help=f"comma-separated list from {sorted(VALID_CONDITIONS)} (default: full)",
    )
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=None,
        help="model directory to use; default is a FRESH temporary directory "
        "(the live model under the user's config dir is never touched)",
    )
    parser.add_argument(
        "--mis-click",
        action="store_true",
        help=(
            "also run every condition with one neighbour slip on the second character of "
            'each word, left uncorrected, and report it as a second row ("+slip")'
        ),
    )
    parser.add_argument(
        "--learn-half",
        action="store_true",
        help="learn sentences 1-15 once, test on 16-30, report before/after plus an oracle row "
        "(ignores --conditions)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    conditions = [c.strip() for c in args.conditions.split(",") if c.strip()]
    unknown = sorted(set(conditions) - VALID_CONDITIONS)
    if unknown:
        parser.error(
            f"unknown condition(s): {', '.join(unknown)} (choose from {sorted(VALID_CONDITIONS)})"
        )

    if args.model_dir is not None:
        model_dir = args.model_dir
        print(f"model dir: {model_dir}  (user-supplied)")
    else:
        model_dir = Path(tempfile.mkdtemp(prefix="alpha-osk-bench-ksr-"))
        print(f"model dir: {model_dir}  (fresh temp dir, the live model is never touched)")

    if args.learn_half:
        run_learn_half(model_dir, args.pills)
        return 0

    hp = HybridPredictor(model_dir=model_dir, enable_llm=False)
    ng = hp._ngram
    print(
        f"model: {len(ng.unigrams)} unigrams, {len(ng.bigrams)} bigram prefixes "
        f"({sum(len(v) for v in ng.bigrams.values())} edges), {len(ng.trigrams)} trigram prefixes"
    )
    print(f"held-out: {len(HELD_OUT)} sentences, {sum(len(s.split()) for s in HELD_OUT)} words\n")

    _warmup(hp)

    results: list[KsrResult] = []
    for name in conditions:
        with apply_condition(hp, name):
            results.append(measure(hp, name, HELD_OUT, args.pills))
            if args.mis_click:
                results.append(measure(hp, name + " +slip", HELD_OUT, args.pills, slip_at=1))

    print_table(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
