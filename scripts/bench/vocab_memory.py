"""Measure SymSpell memory, build time, lookup latency, and equivalence.

This benchmark covers only the shipped vocabulary's SymSpell index. It
deliberately excludes PrefixIndex, context tables, QML, and the live user model.
Each implementation and measurement phase runs in a fresh child process.
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import math
import os
import platform
import random
import subprocess
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _vocabulary() -> dict[str, int]:
    """Load only shipped data, never the user's saved model."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.prediction.ngram_predictor import NgramPredictor

    predictor = NgramPredictor()
    predictor.load_base_dictionary()
    return dict(predictor._base_unigrams)


def _symspell_class(implementation: str, reference_file: str | None):
    if implementation == "current":
        from src.prediction.symspell import SymSpell

        return SymSpell
    if not reference_file:
        raise ValueError("reference worker requires --_reference-file")
    name = "_alpha_osk_reference_symspell"
    spec = importlib.util.spec_from_file_location(name, reference_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import reference module at {reference_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.SymSpell


def _build(cls: Any, vocabulary: dict[str, int]):
    index = cls(max_edit_distance=2, prefix_length=7)
    index.add_dictionary(vocabulary.items())
    index.prepare()
    return index


def _process_memory() -> dict[str, int] | None:
    try:
        import psutil
    except ImportError:
        return None
    info = psutil.Process().memory_info()
    result = {"rss_bytes": int(info.rss)}
    private = getattr(info, "private", None)
    if private is not None:
        result["private_bytes"] = int(private)
    return result


def _process_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int | str]:
    result: dict[str, int | str] = {
        "provider": "psutil",
        "rss_baseline_bytes": before["rss_bytes"],
        "rss_after_build_bytes": after["rss_bytes"],
        "rss_build_delta_bytes": after["rss_bytes"] - before["rss_bytes"],
    }
    if "private_bytes" in before and "private_bytes" in after:
        result.update(
            {
                "private_baseline_bytes": before["private_bytes"],
                "private_after_build_bytes": after["private_bytes"],
                "private_build_delta_bytes": after["private_bytes"] - before["private_bytes"],
            }
        )
    return result


def _memory_worker(cls: Any, vocabulary: dict[str, int]) -> dict[str, Any]:
    gc.collect()
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    index = _build(cls, vocabulary)
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    return {
        "retained_bytes": retained - baseline,
        "peak_bytes": peak - baseline,
        "indexed_words": len(index),
    }


def _changed_letter(char: str) -> str:
    if "a" <= char <= "z":
        return chr((ord(char) - ord("a") + 1) % 26 + ord("a"))
    return "x"


def _queries(vocabulary: dict[str, int], n: int, seed: int) -> tuple[list[str], list[str]]:
    rng = random.Random(seed)
    eligible = sorted(word for word in vocabulary if len(word) >= 4)
    sources = rng.sample(eligible, min(n, len(eligible)))
    queries: list[str] = []
    for word in sources:
        pos = rng.randrange(len(word))
        queries.extend(
            (
                word[:pos] + _changed_letter(word[pos]) + word[pos + 1 :],
                word[:pos] + word[pos + 1 :],
                word[:pos] + word[pos] + word[pos:],
            )
        )
        swap = rng.randrange(len(word) - 1)
        if word[swap] == word[swap + 1]:
            swap = next((i for i in range(len(word) - 1) if word[i] != word[i + 1]), swap)
        queries.append(word[:swap] + word[swap + 1] + word[swap] + word[swap + 2 :])
    longest = max(vocabulary, key=len)
    queries.extend(("", "a", "i", sources[0], sources[-1], longest, "qzxqzx", "zzzzzzzzzz"))
    return sources, queries


def _percentile_ns(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _timing_worker(cls: Any, vocabulary: dict[str, int], n: int, seed: int) -> dict[str, Any]:
    sources, queries = _queries(vocabulary, n, seed)
    gc.collect()
    process_before = _process_memory()
    started = time.perf_counter_ns()
    index = _build(cls, vocabulary)
    build_ns = time.perf_counter_ns() - started
    gc.collect()
    process_after = _process_memory()
    for query in queries[: min(50, len(queries))]:
        index.lookup(query)
    samples: list[int] = []
    for query in queries:
        started = time.perf_counter_ns()
        index.lookup(query)
        samples.append(time.perf_counter_ns() - started)
    result: dict[str, Any] = {
        "build_ns": build_ns,
        "lookup_p50_ns": _percentile_ns(samples, 0.50),
        "lookup_p95_ns": _percentile_ns(samples, 0.95),
        "lookup_p99_ns": _percentile_ns(samples, 0.99),
        "query_count": len(queries),
        "source_word_count": len(sources),
    }
    if process_before is not None and process_after is not None:
        result["process_memory"] = _process_delta(process_before, process_after)
    return result


def _overlay(index: Any, vocabulary: dict[str, int], sources: list[str]) -> list[str]:
    new_words = ["zorblat", "quendrix", "maventon", "plisket"]
    for i, word in enumerate(new_words):
        while word in vocabulary:
            word += "x"
        new_words[i] = word
        index.add_word(word, 20 + i)
    for i, word in enumerate(sources[:12]):
        index.add_word(word, vocabulary[word] + 10_000 + i)
    return new_words


def _equivalence_worker(
    current_cls: Any, reference_cls: Any, vocabulary: dict[str, int], n: int, seed: int
) -> dict[str, Any]:
    sources, queries = _queries(vocabulary, n, seed)
    current = _build(current_cls, vocabulary)
    reference = _build(reference_cls, vocabulary)

    def compare(phase: str, inputs: list[str]) -> dict[str, Any] | None:
        for query in inputs:
            current_result = current.lookup(query)
            reference_result = reference.lookup(query)
            if current_result != reference_result:
                return {
                    "phase": phase,
                    "query": query,
                    "current": current_result,
                    "reference": reference_result,
                }
        return None

    mismatch = compare("base", queries)
    checked = len(queries)
    if mismatch is None:
        current_new = _overlay(current, vocabulary, sources)
        reference_new = _overlay(reference, vocabulary, sources)
        overlay_queries = list(queries)
        for word in current_new:
            overlay_queries.extend((word, word[:-1], word + word[-1]))
        if current_new != reference_new:
            mismatch = {
                "phase": "overlay-setup",
                "current": current_new,
                "reference": reference_new,
            }
        else:
            mismatch = compare("overlay", overlay_queries)
            checked += len(overlay_queries)
    return {
        "checked": True,
        "matched": mismatch is None,
        "lookups_compared": checked,
        "mismatch": mismatch,
    }


def _worker(args: argparse.Namespace) -> int:
    vocabulary = _vocabulary()
    current_cls = _symspell_class("current", None)
    if args._worker == "equivalence":
        reference_cls = _symspell_class("reference", args._reference_file)
        result = _equivalence_worker(current_cls, reference_cls, vocabulary, args.n, args.seed)
    else:
        cls = _symspell_class(args._implementation, args._reference_file)
        if args._worker == "memory":
            result = _memory_worker(cls, vocabulary)
        else:
            result = _timing_worker(cls, vocabulary, args.n, args.seed)
    result["vocabulary_words"] = len(vocabulary)
    print(json.dumps(result, sort_keys=True))
    return 1 if result.get("matched") is False else 0


def _run_child(
    worker: str,
    implementation: str,
    args: argparse.Namespace,
    reference_file: Path | None,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_worker",
        worker,
        "--_implementation",
        implementation,
        "--n",
        str(args.n),
        "--seed",
        str(args.seed),
    ]
    if reference_file is not None:
        command.extend(("--_reference-file", str(reference_file)))
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONHASHSEED"] = str(args.seed)
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        encoding="utf-8",
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if not completed.stdout.strip():
        raise RuntimeError(completed.stderr.strip() or f"{worker} worker produced no output")
    result = json.loads(completed.stdout)
    if completed.returncode and worker != "equivalence":
        raise RuntimeError(completed.stderr.strip() or f"{worker} worker failed")
    return result


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
        ["git", "show", f"{commit}:src/prediction/symspell.py"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"cannot read commit {commit}")
    destination.write_text(completed.stdout, encoding="utf-8")


def _main(args: argparse.Namespace) -> int:
    def run(reference_file: Path | None, reference_commit: str | None = None) -> int:
        implementations = ["current"]
        if reference_file is not None:
            implementations.append("reference")
        metrics: dict[str, Any] = {}
        for implementation in implementations:
            metrics[implementation] = {
                "memory": _run_child("memory", implementation, args, reference_file),
                "timing": _run_child("timing", implementation, args, reference_file),
            }
        equivalence: dict[str, Any] = {"checked": False}
        if reference_file is not None:
            equivalence = _run_child("equivalence", "current", args, reference_file)
        output = {
            "scope": "SymSpell only; excludes PrefixIndex, GUI, context tables, and live data",
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "hash_seed": args.seed,
            },
            "vocabulary_words": metrics["current"]["memory"]["vocabulary_words"],
            "query_count": metrics["current"]["timing"]["query_count"],
            "reference_revision": args.reference,
            "reference_commit": reference_commit,
            "equivalence": equivalence,
            "metrics": metrics,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 1 if equivalence.get("matched") is False else 0

    if not args.reference:
        return run(None)
    reference_commit = _resolve_reference(args.reference)
    with tempfile.TemporaryDirectory(prefix="alpha-osk-vocab-memory-") as temp_dir:
        reference_file = Path(temp_dir) / "symspell_reference.py"
        _extract_reference(reference_commit, reference_file)
        return run(reference_file, reference_commit)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", metavar="REV", help="compare with symspell.py at REV")
    parser.add_argument(
        "--n", type=int, default=300, help="real source words to edit (default: 300)"
    )
    parser.add_argument("--seed", type=int, default=7, help="sampling and hash seed (default: 7)")
    parser.add_argument(
        "--_worker", choices=("memory", "timing", "equivalence"), help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--_implementation", choices=("current", "reference"), help=argparse.SUPPRESS
    )
    parser.add_argument("--_reference-file", help=argparse.SUPPRESS)
    return parser


if __name__ == "__main__":
    parsed = _parser().parse_args()
    if parsed.n <= 0:
        raise SystemExit("--n must be positive")
    if not 0 <= parsed.seed <= 2**32 - 1:
        raise SystemExit("--seed must be between 0 and 4294967295")
    if parsed.reference and parsed.reference.startswith("-"):
        raise SystemExit("--reference must not begin with '-'")
    raise SystemExit(_worker(parsed) if parsed._worker else _main(parsed))
