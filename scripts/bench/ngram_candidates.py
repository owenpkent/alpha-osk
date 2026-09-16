"""Compare current and historical n-gram candidate-selection latency.

Both implementations read the same expanded model snapshot. The benchmark
uses shipped vocabulary, seeds, and training text only. It excludes fuzzy
matching, PPM, QML, the live user model, and end-to-end prediction merging.
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

CLEAN_PREFIXES = (
    "h",
    "he",
    "hel",
    "help",
    "whe",
    "wheel",
    "bec",
    "because",
    "acc",
    "access",
    "comm",
    "prediction",
)
SLIPPED_PREFIXES = (
    "hw",
    "hwl",
    "wje",
    "wheek",
    "bwc",
    "becquse",
    "avc",
    "acceas",
    "cimm",
    "predictoon",
)
NEXT_WORD_CONTEXTS = (
    "i ",
    "i want ",
    "can you ",
    "thank you ",
    "the ",
    "please ",
    "i need to ",
    "how are ",
    "could you ",
    "we ",
)


def _read_training_corpus() -> str:
    path = ROOT / "data" / "training_corpus.txt"
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = (line for line in text.splitlines() if line.strip() and not line.startswith("#"))
    return "\n".join(lines)


def _expanded_snapshot() -> Any:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.prediction.ngram_predictor import NgramPredictor

    predictor = NgramPredictor()
    predictor.load_base_dictionary()
    predictor.load_common_bigrams()
    predictor.load_common_trigrams()
    predictor.load_seed_ngrams()
    predictor.load_seed_ngrams(ROOT / "data" / "seed_trigrams.txt")
    corpus = _read_training_corpus()
    if corpus:
        predictor.load_corpus(corpus)
    return predictor


def _resolve_reference(revision: str) -> str:
    if revision.startswith("-"):
        raise ValueError("--reference must not begin with '-'")
    completed = subprocess.run(
        ["git", "rev-parse", "--verify", "--end-of-options", f"{revision}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"cannot resolve revision {revision}")
    return completed.stdout.strip()


def _extract_reference(commit: str, destination: Path) -> None:
    completed = subprocess.run(
        ["git", "show", f"{commit}:src/prediction/ngram_predictor.py"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"cannot read n-gram at {commit}")
    destination.write_text(completed.stdout, encoding="utf-8")


def _reference_class(path: Path) -> Any:
    name = "src.prediction._reference_ngram"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import reference module at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.NgramPredictor


def _percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _summary(samples: list[int]) -> dict[str, int]:
    return {
        "samples": len(samples),
        "p50_ns": _percentile(samples, 0.50),
        "p95_ns": _percentile(samples, 0.95),
    }


def _warm_current(current: Any, predictions: int) -> int:
    contexts = CLEAN_PREFIXES + SLIPPED_PREFIXES + NEXT_WORD_CONTEXTS
    for context in contexts:
        current.predict_with_scores(context, predictions)
    return len(contexts)


def _paired_samples(
    current: Any,
    reference: Any,
    contexts: tuple[str, ...],
    repeats: int,
    predictions: int,
) -> dict[str, list[int]]:
    implementations = {"current": current, "reference": reference}
    samples: dict[str, list[int]] = {"current": [], "reference": []}
    for repeat in range(repeats):
        for index, context in enumerate(contexts):
            order = ("current", "reference")
            if (repeat + index) % 2:
                order = tuple(reversed(order))
            for label in order:
                started = time.perf_counter_ns()
                implementations[label].predict_with_scores(context, predictions)
                samples[label].append(time.perf_counter_ns() - started)
    return samples


def _benchmark(
    current: Any, reference_class: Any, repeats: int, predictions: int
) -> dict[str, Any]:
    reference = reference_class()
    warm_calls = _warm_current(current, predictions)
    reference.__dict__ = current.__dict__.copy()

    phases = {
        "midword": CLEAN_PREFIXES + SLIPPED_PREFIXES,
        "nextword": NEXT_WORD_CONTEXTS,
    }
    results: dict[str, Any] = {}
    gc.collect()
    for phase, contexts in phases.items():
        samples = _paired_samples(current, reference, contexts, repeats, predictions)
        results[phase] = {
            "contexts": len(contexts),
            "current": _summary(samples["current"]),
            "reference": _summary(samples["reference"]),
        }
    return {"warm_current_calls": warm_calls, "phases": results}


def _main(args: argparse.Namespace) -> int:
    commit = _resolve_reference(args.reference)
    with tempfile.TemporaryDirectory(prefix="alpha-osk-ngram-candidates-") as temp_dir:
        reference_path = Path(temp_dir) / "ngram_predictor.py"
        _extract_reference(commit, reference_path)
        current = _expanded_snapshot()
        reference_class = _reference_class(reference_path)
        measured = _benchmark(current, reference_class, args.repeats, args.predictions)

    output = {
        "scope": (
            "NgramPredictor.predict_with_scores candidate selection on one shipped expanded "
            "snapshot; excludes fuzzy, PPM, QML, live data, and hybrid merging"
        ),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "hash_seed": os.environ.get("PYTHONHASHSEED"),
        },
        "reference_revision": args.reference,
        "reference_commit": commit,
        "snapshot": {
            "copy_method": "reference.__dict__ = current.__dict__.copy()",
            "base_words": len(current._base_unigrams),
            "user_words": len(current.user_vocab),
            "bigram_edges": sum(len(row) for row in current.bigrams.values()),
            "trigram_edges": sum(len(row) for row in current.trigrams.values()),
        },
        "workload": {
            "repeats": args.repeats,
            "predictions_per_call": args.predictions,
            "clean_prefixes": list(CLEAN_PREFIXES),
            "slipped_prefixes": list(SLIPPED_PREFIXES),
            "nextword_contexts": list(NEXT_WORD_CONTEXTS),
        },
        **measured,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        default="cb101da",
        metavar="REV",
        help="historical n-gram revision (default: cb101da)",
    )
    parser.add_argument("--repeats", type=int, default=9, help="paired repeats (default: 9)")
    parser.add_argument(
        "--predictions",
        type=int,
        default=10,
        help="candidates requested per call (default: 10, matching Hybrid n=5)",
    )
    return parser


if __name__ == "__main__":
    parsed = _parser().parse_args()
    if parsed.reference.startswith("-"):
        raise SystemExit("--reference must not begin with '-'")
    if parsed.repeats <= 0:
        raise SystemExit("--repeats must be positive")
    if parsed.predictions <= 0:
        raise SystemExit("--predictions must be positive")
    raise SystemExit(_main(parsed))
