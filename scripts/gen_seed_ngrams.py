"""Derive Alpha-OSK's base context seeds from an ARPA n-gram language model.

The shipped seeds (``data/common_bigrams.txt`` / ``common_trigrams.txt``) are
hand-written and cover 260 bigram prefixes against a ~19 000 word vocabulary,
so for about 98.6% of the words a user can type there is no base context at
all.  This script converts a public ARPA model into the same role, keeping the
part of it a prediction bar can actually use.

What it does, and why each step is here:

* **Restricts to our own vocabulary.**  The wordlist we ship is filtered for
  explicit content; an outside model is not.  Intersecting means the model can
  only ever reorder words we already accept, never introduce one.
* **Keeps the top N continuations per prefix.**  The bar shows at most five
  pills, so the tail of a 2 million edge model is unreachable by construction.
  Pruning is what turns 119 MB into something that can be rebuilt at launch.
* **Keeps the backoff weight for every prefix it retains.**  This is the part
  worth having: an ARPA model ships smoothing already computed, which is the
  "Katz / stupid backoff for sparse contexts" gap in
  ``docs/architecture/PREDICTION_NOTES.md``.  Dropping to counts alone throws
  it away.
* **Converts probabilities to counts on the curated scale.**  ``bigrams`` holds
  counts that ``NgramPredictor._context_probs`` normalises itself, so each
  prefix is given the same total base mass a single curated pair carries
  (``--mass``, default 50) and that mass is split across its continuations in
  proportion to their probability.  See ``docs/architecture/NGRAM_SEEDS.md``
  for why the mass is constant per prefix rather than scaled by confidence.

Usage:
    python scripts/gen_seed_ngrams.py MODEL.arpa --out data/seed_bigrams.txt
    python scripts/gen_seed_ngrams.py MODEL.arpa --order 3 --top 3 --out ...
    python scripts/gen_seed_ngrams.py MODEL.arpa --dry-run

The model is an input, not a dependency: it is downloaded once, converted, and
the generated file is what ships.  ``--source`` records where it came from in
the output header, because the licence of every model worth using requires
attribution.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

# Must be set before anything under src/ imports PySide6 (NgramPredictor is a
# QObject), or a headless run can fail trying to open a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.prediction.ngram_predictor import NgramPredictor  # noqa: E402

# ARPA section markers.  Kept as constants because a literal "\\2-grams:" in a
# comparison is easy to typo and impossible to spot in a diff.
DATA_MARKER = "\\data\\"
END_MARKER = "\\end\\"

#: Tokens that carry no meaning for a prediction bar.  ``<s>`` is deliberately
#: absent: it is the sentence-start context, which is the one thing the current
#: engine has no model of at all.
SKIP_TOKENS = frozenset({"<unk>", "</s>"})

SENTENCE_START = "<s>"


def section_marker(order: int) -> str:
    """The ARPA section header introducing n-grams of length ``order``."""
    return f"\\{order}-grams:"


class ArpaRows:
    """The rows of one ARPA order, grouped by context."""

    def __init__(self) -> None:
        #: context -> list of (log10 probability, continuation)
        self.by_context: dict[str, list[tuple[float, str]]] = defaultdict(list)
        #: context -> log10 backoff weight, for contexts that carry one
        self.backoff: dict[str, float] = {}
        #: context -> estimated log10 frequency of the context itself
        self.context_logfreq: dict[str, float] = {}


def parse_arpa(path: Path, order: int, vocab: set[str], keep_sentence_start: bool) -> ArpaRows:
    """Read an ARPA file up to ``order``, filtered to ``vocab``.

    Three things come out of one pass.  The ``order``-gram rows themselves;
    the backoff weights, which live on the ``order - 1`` section, because that
    is where the weight for a context of that length sits (the weight on
    ``how are`` is on the bigram line and governs every trigram under it); and
    an estimated frequency for each context, chained from the lower orders as
    ``log P(w1) + log P(w2 | w1)``, which is what ``--max-contexts`` ranks by.
    """
    rows = ArpaRows()
    want_grams = section_marker(order)
    want_backoff = section_marker(order - 1)
    # Probabilities of the shorter grams, needed to score a context's frequency.
    shorter_prob: dict[str, float] = {}
    current: str | None = None

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith("\\"):
                if line == END_MARKER:
                    break
                current = line
                continue
            if current is None:
                continue

            columns = line.split("\t")
            if len(columns) < 2:
                continue
            gram = columns[1]
            words = gram.split(" ")

            # Sections shorter than the target feed the frequency estimate, and
            # the order-1 section additionally carries the backoff weights.
            if len(words) < order:
                if not _context_allowed(words, vocab, keep_sentence_start):
                    continue
                shorter_prob[gram] = float(columns[0])
                if current == want_backoff and len(columns) >= 3:
                    rows.backoff[gram] = float(columns[2])
                continue

            if current != want_grams or len(words) != order:
                continue
            continuation = words[-1]
            if continuation in SKIP_TOKENS or continuation not in vocab:
                continue
            context_words = words[:-1]
            if not _context_allowed(context_words, vocab, keep_sentence_start):
                continue
            context = " ".join(context_words)
            rows.by_context[context].append((float(columns[0]), continuation))

    for context in rows.by_context:
        rows.context_logfreq[context] = _chain_logfreq(context.split(" "), shorter_prob)

    return rows


def _chain_logfreq(words: list[str], shorter_prob: dict[str, float]) -> float:
    """Estimate log10 P(context) as the chain of its own n-gram rows.

    A context absent from the shorter sections scores ``-inf`` so it sorts
    last: we know nothing about how often it occurs, which is itself a reason
    not to spend a shipped row on it.
    """
    total = 0.0
    for length in range(1, len(words) + 1):
        row = " ".join(words[:length])
        if row not in shorter_prob:
            return float("-inf")
        total += shorter_prob[row]
    return total


def _context_allowed(words: list[str], vocab: set[str], keep_sentence_start: bool) -> bool:
    """Whether every word of a context is one we are willing to key on."""
    for index, word in enumerate(words):
        if word == SENTENCE_START:
            # Sentence start is only meaningful as the leading token.
            if index != 0 or not keep_sentence_start:
                return False
            continue
        if word in SKIP_TOKENS or word not in vocab:
            return False
    return True


def to_counts(scored: list[tuple[float, str]], top: int, mass: float) -> list[tuple[str, int]]:
    """Turn log10 probabilities into integer counts on the curated scale.

    The kept continuations share ``mass`` in proportion to their probability,
    so every seeded prefix carries the same base evidence and the ratios
    within a row survive.  Everything floors at 1, since a row entry that
    rounds to zero is the same as not emitting it.
    """
    best = sorted(scored, reverse=True)[:top]
    if not best:
        return []
    total = sum(10**logprob for logprob, _ in best)
    if total <= 0:
        return []
    out: list[tuple[str, int]] = []
    for logprob, word in best:
        share = (10**logprob) / total
        out.append((word, max(1, round(mass * share))))
    return out


def select_contexts(rows: ArpaRows, max_contexts: int | None) -> list[str]:
    """The contexts to keep, most frequent first when a cap applies.

    Ranking by estimated context frequency rather than by the strength of the
    best continuation matters: a rare context with one near-certain follower
    scores highly on the latter and is still a row almost nobody reaches.
    """
    contexts = list(rows.by_context)
    if max_contexts is None or len(contexts) <= max_contexts:
        return contexts
    contexts.sort(key=lambda c: rows.context_logfreq.get(c, float("-inf")), reverse=True)
    return contexts[:max_contexts]


def write_seeds(
    path: Path,
    rows: ArpaRows,
    *,
    order: int,
    top: int,
    mass: float,
    source: str,
    max_contexts: int | None = None,
) -> tuple[int, int]:
    """Write the pruned model. Returns (contexts, edges)."""
    chosen = set(select_contexts(rows, max_contexts))
    kept: list[tuple[str, list[tuple[str, int]]]] = []
    for context, scored in rows.by_context.items():
        if context not in chosen:
            continue
        counts = to_counts(scored, top, mass)
        if counts:
            kept.append((context, counts))
    kept.sort()
    edges = sum(len(counts) for _, counts in kept)

    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(f"# Base {order}-gram context seeds for Alpha-OSK.\n")
        handle.write("# GENERATED by scripts/gen_seed_ngrams.py. Do not hand-edit:\n")
        handle.write("# regenerate instead, or the next run will silently drop your change.\n")
        handle.write("#\n")
        handle.write(f"# Source: {source}\n")
        handle.write(f"# Pruned to the shipped vocabulary, top {top} continuations per context,\n")
        handle.write(f"# {mass:g} counts of base mass per context split by probability.\n")
        handle.write("#\n")
        handle.write("# Format, mirroring ARPA's own self-describing shape:\n")
        handle.write("#   \\backoff:   <context> <log10 backoff weight>\n")
        handle.write("#   \\seeds:     <context> <continuation> <count>\n")
        handle.write(f"\n{DATA_MARKER}\n")
        handle.write(f"order={order}\n")
        handle.write(f"contexts={len(kept)}\n")
        handle.write(f"edges={edges}\n")

        handle.write("\n\\backoff:\n")
        written_backoff = 0
        for context, _ in kept:
            weight = rows.backoff.get(context)
            if weight is not None:
                handle.write(f"{context} {weight:.6g}\n")
                written_backoff += 1

        handle.write("\n\\seeds:\n")
        for context, counts in kept:
            for word, count in counts:
                handle.write(f"{context} {word} {count}\n")
        handle.write(f"\n{END_MARKER}\n")

    return len(kept), edges


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("model", type=Path, help="path to an uncompressed .arpa model")
    parser.add_argument("--out", type=Path, help="file to write (omit with --dry-run)")
    parser.add_argument(
        "--order", type=int, default=2, choices=(2, 3), help="n-gram order (default 2)"
    )
    parser.add_argument(
        "--top", type=int, default=5, help="continuations kept per context (default 5)"
    )
    parser.add_argument(
        "--mass", type=float, default=50.0, help="base counts per context (default 50)"
    )
    parser.add_argument(
        "--source",
        default="unspecified (pass --source; every usable model requires attribution)",
        help="provenance line recorded in the output header",
    )
    parser.add_argument("--no-sentence-start", action="store_true", help="drop <s> contexts")
    parser.add_argument(
        "--max-contexts",
        type=int,
        default=None,
        help="keep only the N most frequent contexts (trigrams need this; bigrams do not)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report sizes without writing")
    args = parser.parse_args()

    if not args.dry_run and args.out is None:
        parser.error("--out is required unless --dry-run is given")

    predictor = NgramPredictor()
    predictor.load_base_dictionary()
    vocab = set(predictor.unigrams)
    print(f"shipped vocabulary        {len(vocab):,}")

    started = time.perf_counter()
    rows = parse_arpa(args.model, args.order, vocab, not args.no_sentence_start)
    print(f"parsed {args.model.name} in {time.perf_counter() - started:.1f} s")

    contexts = len(rows.by_context)
    raw_edges = sum(len(v) for v in rows.by_context.values())
    print(f"in-vocabulary contexts    {contexts:,}   edges {raw_edges:,}")
    print(f"contexts with a backoff   {len(rows.backoff):,}")

    if args.dry_run:
        caps: list[int | None] = [None, 100_000, 50_000, 20_000]
        print()
        print(f"{'max ctx':>9}  {'top':>4}  {'contexts':>10}  {'edges':>12}  {'est MB':>7}")
        for cap in caps:
            selected = select_contexts(rows, cap)
            for top in (3, 5):
                edges = sum(min(len(rows.by_context[c]), top) for c in selected)
                # ~19 bytes a row, measured across the generated files
                print(
                    f"{('all' if cap is None else f'{cap:,}'):>9}  {top:>4}  "
                    f"{len(selected):>10,}  {edges:>12,}  {edges * 19 / 1e6:>7.2f}"
                )
        return 0

    out = args.out
    assert out is not None
    out.parent.mkdir(parents=True, exist_ok=True)
    written_contexts, written_edges = write_seeds(
        out,
        rows,
        order=args.order,
        top=args.top,
        mass=args.mass,
        source=args.source,
        max_contexts=args.max_contexts,
    )
    size = out.stat().st_size
    print()
    print(f"wrote {out}")
    print(f"  contexts {written_contexts:,}   edges {written_edges:,}   {size / 1e6:.2f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
