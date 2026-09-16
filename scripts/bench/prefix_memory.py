"""Measure PrefixIndex memory, latency, and parity with a prior revision.

Each measured implementation and phase runs in a fresh child process. The
tracemalloc worker reports retained and peak Python allocations. A separate
untraced worker reports build and lookup timing plus process RSS when psutil is
available. The equivalence worker compares every vocabulary prefix and a
deterministic sample of PrefixBeam outputs.

Examples:
    python scripts/bench/prefix_memory.py
    python scripts/bench/prefix_memory.py --reference cb101da
    python scripts/bench/prefix_memory.py --word-list data/future-words.txt
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
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _vocabulary(word_list: str | None, base_dictionary: str | None) -> dict[str, float]:
    """Extract stable base counts with no saved or live user data."""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.prediction.language import ENGLISH
    from src.prediction.ngram_predictor import NgramPredictor

    profile = replace(
        ENGLISH,
        dictionary=Path(base_dictionary) if base_dictionary else ENGLISH.dictionary,
        extra_vocabulary=Path(word_list) if word_list else None,
    )
    predictor = NgramPredictor(profile=profile)
    predictor.load_base_dictionary()
    return {word: float(frequency) for word, frequency in predictor._base_unigrams.items()}


def _prefix_module(implementation: str, reference_file: str | None) -> ModuleType:
    if implementation == "current":
        from src.prediction import prefix_beam

        return prefix_beam
    if not reference_file:
        raise ValueError("reference worker requires --_reference-file")
    name = "_alpha_osk_reference_prefix_beam"
    spec = importlib.util.spec_from_file_location(name, reference_file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import reference module at {reference_file}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _build(module: ModuleType, vocabulary: dict[str, float]):
    return module.PrefixIndex(vocabulary)


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


def _memory_worker(module: ModuleType, vocabulary: dict[str, float]) -> dict[str, Any]:
    gc.collect()
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    index = _build(module, vocabulary)
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    return {
        "retained_bytes": retained - baseline,
        "peak_bytes": peak - baseline,
        "indexed_prefixes": len(index),
    }


def _all_prefixes(vocabulary: dict[str, float]) -> list[str]:
    prefixes: set[str] = set()
    for word in vocabulary:
        prefixes.update(word[:length] for length in range(1, len(word) + 1))
    return sorted(prefixes)


def _sample_words(vocabulary: dict[str, float], n: int, seed: int) -> list[str]:
    eligible = sorted(word for word in vocabulary if len(word) >= 4)
    return random.Random(seed).sample(eligible, min(n, len(eligible)))


def _timing_prefixes(vocabulary: dict[str, float], n: int, seed: int) -> list[str]:
    prefixes: set[str] = {"", "qzxqzx", "notaword"}
    for word in _sample_words(vocabulary, n, seed):
        prefixes.update(word[:length] for length in range(1, len(word) + 1))
        prefixes.add(word + "x")
    return sorted(prefixes)


def _percentile_ns(values: list[int], percentile: float) -> int:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


def _novel_words(vocabulary: dict[str, float]) -> list[tuple[str, float]]:
    result = []
    for position, candidate in enumerate(
        ("zorblat", "quendrix", "maventon", "plisket", "cafégraph", "猫咪語")
    ):
        while candidate in vocabulary:
            candidate += "x"
        result.append((candidate, float(20 + position)))
    return result


def _overlay_updates(vocabulary: dict[str, float], sources: list[str]) -> list[tuple[str, float]]:
    updates = _novel_words(vocabulary)
    updates.extend(
        (word, vocabulary[word] + 10_000 + position) for position, word in enumerate(sources[:16])
    )
    return updates


def _timing_worker(
    module: ModuleType, vocabulary: dict[str, float], n: int, seed: int
) -> dict[str, Any]:
    prefixes = _timing_prefixes(vocabulary, n, seed)
    sources = _sample_words(vocabulary, n, seed)
    gc.collect()
    process_before = _process_memory()
    started = time.perf_counter_ns()
    index = _build(module, vocabulary)
    build_ns = time.perf_counter_ns() - started
    gc.collect()
    process_after = _process_memory()

    for prefix in prefixes[: min(100, len(prefixes))]:
        index.is_live(prefix)
        index.children(prefix)
        index.completions(prefix)
    query_samples = []
    for prefix in prefixes:
        started = time.perf_counter_ns()
        index.is_live(prefix)
        index.children(prefix)
        index.completions(prefix)
        query_samples.append(time.perf_counter_ns() - started)

    update_samples = []
    for word, frequency in _overlay_updates(vocabulary, sources):
        started = time.perf_counter_ns()
        index.update_word(word, frequency)
        update_samples.append(time.perf_counter_ns() - started)

    result: dict[str, Any] = {
        "build_ns": build_ns,
        "query_count": len(query_samples),
        "query_p50_ns": _percentile_ns(query_samples, 0.50),
        "query_p95_ns": _percentile_ns(query_samples, 0.95),
        "query_p99_ns": _percentile_ns(query_samples, 0.99),
        "update_count": len(update_samples),
        "update_p50_ns": _percentile_ns(update_samples, 0.50),
        "update_p95_ns": _percentile_ns(update_samples, 0.95),
    }
    if process_before is not None and process_after is not None:
        result["process_memory"] = _process_delta(process_before, process_after)
    return result


def _prefix_state(index: Any, prefix: str) -> tuple[bool, str, list[tuple[float, str]]]:
    return index.is_live(prefix), index.children(prefix), index.completions(prefix)


def _compare_prefixes(
    current: Any,
    reference: Any,
    prefixes: list[str],
    phase: str,
) -> dict[str, Any] | None:
    if len(current) != len(reference):
        return {
            "phase": phase,
            "operation": "len",
            "current": len(current),
            "reference": len(reference),
        }
    for prefix in prefixes:
        current_state = _prefix_state(current, prefix)
        reference_state = _prefix_state(reference, prefix)
        if current_state != reference_state:
            return {
                "phase": phase,
                "operation": "prefix-state",
                "prefix": prefix,
                "current": current_state,
                "reference": reference_state,
            }
    return None


def _changed_letter(char: str) -> str:
    if "a" <= char <= "z":
        return chr((ord(char) - ord("a") + 1) % 26 + ord("a"))
    return "x"


def _beam_queries(vocabulary: dict[str, float], n: int, seed: int) -> list[str]:
    queries: set[str] = {"", "a", "sp", "ap", "teh", "qzxqzx"}
    for word in _sample_words(vocabulary, n, seed):
        length = min(6, max(3, len(word) - 2))
        prefix = word[:length]
        queries.add(prefix)
        position = min(1, len(prefix) - 1)
        queries.add(prefix[:position] + _changed_letter(prefix[position]) + prefix[position + 1 :])
        queries.add(prefix[:position] + prefix[position + 1 :])
        queries.add(prefix[:position] + prefix[position] + prefix[position:])
        if len(prefix) > 1 and prefix[0] != prefix[1]:
            queries.add(prefix[1] + prefix[0] + prefix[2:])
    return sorted(queries)


def _compare_beams(
    current_module: ModuleType,
    reference_module: ModuleType,
    current_index: Any,
    reference_index: Any,
    queries: list[str],
    phase: str,
) -> dict[str, Any] | None:
    from src.prediction.fuzzy_recognizer import QWERTY_POSITIONS

    current = current_module.PrefixBeam(
        current_index, current_module.SpatialEmissions(QWERTY_POSITIONS)
    )
    reference = reference_module.PrefixBeam(
        reference_index, reference_module.SpatialEmissions(QWERTY_POSITIONS)
    )
    for query in queries:
        current_result = current.complete(query, 5)
        reference_result = reference.complete(query, 5)
        if current_result != reference_result:
            return {
                "phase": phase,
                "operation": "beam",
                "query": query,
                "current": current_result,
                "reference": reference_result,
            }
    return None


def _equivalence_worker(
    current_module: ModuleType,
    reference_module: ModuleType,
    vocabulary: dict[str, float],
    n: int,
    seed: int,
) -> dict[str, Any]:
    current = _build(current_module, vocabulary)
    reference = _build(reference_module, vocabulary)
    prefixes = _all_prefixes(vocabulary)
    prefixes.extend(("", "qzxqzx", "notaword", "￿", "😀missing"))
    queries = _beam_queries(vocabulary, n, seed)

    mismatch = _compare_prefixes(current, reference, prefixes, "base")
    beam_checks = 0
    if mismatch is None:
        mismatch = _compare_beams(
            current_module,
            reference_module,
            current,
            reference,
            queries,
            "base",
        )
        beam_checks += len(queries)

    sources = _sample_words(vocabulary, n, seed)
    updates = _overlay_updates(vocabulary, sources)
    expanded = dict(vocabulary)
    if mismatch is None:
        for word, frequency in updates:
            current.update_word(word, frequency)
            reference.update_word(word, frequency)
            expanded[word] = max(frequency, expanded.get(word, float("-inf")))
        updated_prefixes = _all_prefixes(expanded)
        updated_prefixes.extend(("", "qzxqzx", "notaword", "￿", "😀missing"))
        mismatch = _compare_prefixes(current, reference, updated_prefixes, "overlay")
        prefixes = updated_prefixes
    if mismatch is None:
        overlay_queries = sorted(
            set(queries).union(word[: min(6, len(word))] for word, _ in updates)
        )
        mismatch = _compare_beams(
            current_module,
            reference_module,
            current,
            reference,
            overlay_queries,
            "overlay",
        )
        beam_checks += len(overlay_queries)

    return {
        "checked": True,
        "matched": mismatch is None,
        "prefix_states_compared": len(prefixes),
        "beam_outputs_compared": beam_checks,
        "overlay_updates": len(updates),
        "mismatch": mismatch,
    }


def _worker(args: argparse.Namespace) -> int:
    vocabulary = _vocabulary(args.word_list, args._base_dictionary)
    if args._worker == "equivalence":
        current_module = _prefix_module("current", None)
        reference_module = _prefix_module("reference", args._reference_file)
        result = _equivalence_worker(
            current_module,
            reference_module,
            vocabulary,
            args.n,
            args.seed,
        )
    else:
        module = _prefix_module(args._implementation, args._reference_file)
        if args._worker == "memory":
            result = _memory_worker(module, vocabulary)
        else:
            result = _timing_worker(module, vocabulary, args.n, args.seed)
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
    if args.word_list is not None:
        command.extend(("--word-list", str(args.word_list)))
    if args._base_dictionary is not None:
        command.extend(("--_base-dictionary", str(args._base_dictionary)))
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


def _extract_file(commit: str, repository_path: str, destination: Path) -> None:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{repository_path}"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            completed.stderr.strip() or f"cannot read {repository_path} at commit {commit}"
        )
    destination.write_text(completed.stdout, encoding="utf-8")


def _main(args: argparse.Namespace) -> int:
    def run(
        reference_file: Path | None,
        reference_commit: str | None = None,
        vocabulary_commit: str | None = None,
    ) -> int:
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
            "scope": "PrefixIndex only; PrefixBeam is checked for parity but not timed",
            "environment": {
                "python": platform.python_version(),
                "implementation": platform.python_implementation(),
                "platform": platform.platform(),
                "hash_seed": args.seed,
            },
            "vocabulary_words": metrics["current"]["memory"]["vocabulary_words"],
            "word_list": str(args.word_list) if args.word_list is not None else None,
            "reference_revision": args.reference,
            "reference_commit": reference_commit,
            "legacy_vocabulary_revision": args.legacy_vocabulary,
            "legacy_vocabulary_commit": vocabulary_commit,
            "equivalence": equivalence,
            "metrics": metrics,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
        return 1 if equivalence.get("matched") is False else 0

    if not args.reference and not args.legacy_vocabulary:
        return run(None)
    reference_commit = _resolve_reference(args.reference) if args.reference else None
    vocabulary_commit = (
        _resolve_reference(args.legacy_vocabulary) if args.legacy_vocabulary else None
    )
    with tempfile.TemporaryDirectory(prefix="alpha-osk-prefix-memory-") as temp_dir:
        reference_file = None
        if reference_commit is not None:
            reference_file = Path(temp_dir) / "prefix_beam_reference.py"
            _extract_file(reference_commit, "src/prediction/prefix_beam.py", reference_file)
        if vocabulary_commit is not None:
            base_dictionary = Path(temp_dir) / "base_dictionary.txt"
            _extract_file(vocabulary_commit, "data/base_dictionary.txt", base_dictionary)
            args._base_dictionary = base_dictionary
        return run(reference_file, reference_commit, vocabulary_commit)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", metavar="REV", help="compare with prefix_beam.py at REV")
    parser.add_argument(
        "--legacy-vocabulary",
        metavar="REV",
        help="load base_dictionary.txt at REV and disable the profile's extra vocabulary",
    )
    parser.add_argument(
        "--word-list",
        type=Path,
        help="extend stable base counts with an unranked one-word-per-line vocabulary",
    )
    parser.add_argument(
        "--n", type=int, default=300, help="words sampled for timing and beam parity (default: 300)"
    )
    parser.add_argument("--seed", type=int, default=7, help="sampling and hash seed (default: 7)")
    parser.add_argument(
        "--_worker", choices=("memory", "timing", "equivalence"), help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--_implementation", choices=("current", "reference"), help=argparse.SUPPRESS
    )
    parser.add_argument("--_reference-file", help=argparse.SUPPRESS)
    parser.add_argument("--_base-dictionary", type=Path, help=argparse.SUPPRESS)
    return parser


if __name__ == "__main__":
    parsed = _parser().parse_args()
    if parsed.n <= 0:
        raise SystemExit("--n must be positive")
    if not 0 <= parsed.seed <= 2**32 - 1:
        raise SystemExit("--seed must be between 0 and 4294967295")
    if parsed.reference and parsed.reference.startswith("-"):
        raise SystemExit("--reference must not begin with '-'")
    if parsed.legacy_vocabulary and parsed.legacy_vocabulary.startswith("-"):
        raise SystemExit("--legacy-vocabulary must not begin with '-'")
    if parsed.word_list is not None:
        parsed.word_list = parsed.word_list.resolve()
        if not parsed.word_list.is_file():
            raise SystemExit(f"--word-list does not exist: {parsed.word_list}")
    raise SystemExit(_worker(parsed) if parsed._worker else _main(parsed))
