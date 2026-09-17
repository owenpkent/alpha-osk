"""Compare fuzzy-index memory and startup across vocabulary growth scenarios.

The measured object is a FuzzyRecognizer with both SymSpell and PrefixIndex
materialized. Vocabulary construction happens before measurement. The benchmark
excludes n-gram memory, context tables, PPM, QML, and the live user model.
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[2]
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCENARIOS = ("baseline_reference", "baseline_current", "expanded_current")


def _vocabulary(scenario: str, historical_base: Path) -> dict[str, int]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from src.prediction.language import ENGLISH
    from src.prediction.ngram_predictor import NgramPredictor

    if scenario.startswith("baseline_"):
        profile = replace(ENGLISH, extra_vocabulary=None, dictionary=historical_base)
    else:
        profile = ENGLISH
    predictor = NgramPredictor(profile=profile)
    predictor.load_base_dictionary()
    return dict(predictor._base_unigrams)


def _import_file(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import module at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _recognizer_class(
    scenario: str, reference_symspell: Path | None, reference_prefix: Path | None
) -> Any:
    import src.prediction.fuzzy_recognizer as fuzzy_module

    if scenario == "baseline_reference":
        if reference_symspell is None or reference_prefix is None:
            raise ValueError("reference scenario requires historical modules")
        symspell = _import_file("_alpha_osk_growth_symspell", reference_symspell)
        prefix = _import_file("_alpha_osk_growth_prefix", reference_prefix)
        fuzzy_module.SymSpell = symspell.SymSpell
        fuzzy_module.PrefixIndex = prefix.PrefixIndex
    return fuzzy_module.FuzzyRecognizer


def _materialize(recognizer_class: Any, frequencies: dict[str, int]) -> Any:
    recognizer = recognizer_class()
    recognizer.set_frequencies(frequencies)
    recognizer.get_fuzzy_predictions("hel", 5)
    return recognizer


def _index_stats(recognizer: Any) -> dict[str, int]:
    generator = recognizer.word_generator
    prefix = generator._prefix_beam.index
    return {
        "fuzzy_words": len(generator.dictionary),
        "symspell_words": len(generator._symspell),
        "live_prefixes": len(prefix),
    }


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


def _memory_worker(recognizer_class: Any, frequencies: dict[str, int]) -> dict[str, Any]:
    gc.collect()
    tracemalloc.start()
    baseline, _ = tracemalloc.get_traced_memory()
    tracemalloc.reset_peak()
    recognizer = _materialize(recognizer_class, frequencies)
    gc.collect()
    retained, peak = tracemalloc.get_traced_memory()
    return {
        "retained_bytes": retained - baseline,
        "peak_bytes": peak - baseline,
        **_index_stats(recognizer),
    }


def _timing_worker(recognizer_class: Any, frequencies: dict[str, int]) -> dict[str, Any]:
    gc.collect()
    process_before = _process_memory()
    started = time.perf_counter_ns()
    recognizer = _materialize(recognizer_class, frequencies)
    startup_ns = time.perf_counter_ns() - started
    gc.collect()
    process_after = _process_memory()
    result: dict[str, Any] = {
        "constructor_first_prefix_ns": startup_ns,
        **_index_stats(recognizer),
    }
    if process_before is not None and process_after is not None:
        result["process_memory"] = _process_delta(process_before, process_after)
    return result


def _worker(args: argparse.Namespace) -> int:
    historical_base = Path(args._historical_base)
    frequencies = _vocabulary(args._scenario, historical_base)
    recognizer_class = _recognizer_class(
        args._scenario,
        Path(args._reference_symspell) if args._reference_symspell else None,
        Path(args._reference_prefix) if args._reference_prefix else None,
    )
    if args._phase == "memory":
        result = _memory_worker(recognizer_class, frequencies)
    else:
        result = _timing_worker(recognizer_class, frequencies)
    result.update({"scenario": args._scenario, "vocabulary_words": len(frequencies)})
    print(json.dumps(result, sort_keys=True))
    return 0


def _run_child(
    scenario: str,
    phase: str,
    historical_base: Path,
    reference_symspell: Path,
    reference_prefix: Path,
) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_scenario",
        scenario,
        "--_phase",
        phase,
        "--_historical-base",
        str(historical_base),
    ]
    if scenario == "baseline_reference":
        command.extend(
            (
                "--_reference-symspell",
                str(reference_symspell),
                "--_reference-prefix",
                str(reference_prefix),
            )
        )
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONHASHSEED"] = "7"
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        capture_output=True,
        encoding="utf-8",
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"{scenario} {phase} worker failed")
    if not completed.stdout.strip():
        raise RuntimeError(f"{scenario} {phase} worker produced no output")
    return json.loads(completed.stdout)


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


def _extract(commit: str, repository_path: str, destination: Path) -> None:
    completed = subprocess.run(
        ["git", "show", f"{commit}:{repository_path}"],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or f"cannot read {repository_path}")
    destination.write_text(completed.stdout, encoding="utf-8")


def _main(args: argparse.Namespace) -> int:
    commit = _resolve_reference(args.reference)
    with tempfile.TemporaryDirectory(prefix="alpha-osk-vocab-growth-") as temp_dir:
        temporary = Path(temp_dir)
        historical_base = temporary / "base_dictionary.txt"
        reference_symspell = temporary / "symspell.py"
        reference_prefix = temporary / "prefix_beam.py"
        _extract(commit, "data/base_dictionary.txt", historical_base)
        _extract(commit, "src/prediction/symspell.py", reference_symspell)
        _extract(commit, "src/prediction/prefix_beam.py", reference_prefix)
        metrics: dict[str, Any] = {}
        for scenario in SCENARIOS:
            metrics[scenario] = {
                "memory": _run_child(
                    scenario,
                    "memory",
                    historical_base,
                    reference_symspell,
                    reference_prefix,
                ),
                "timing": _run_child(
                    scenario,
                    "timing",
                    historical_base,
                    reference_symspell,
                    reference_prefix,
                ),
            }
    output = {
        "scope": (
            "FuzzyRecognizer with materialized SymSpell and PrefixIndex; excludes n-gram "
            "memory, contexts, PPM, GUI, and live data"
        ),
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "hash_seed": 7,
        },
        "reference_revision": args.reference,
        "reference_commit": commit,
        "warm_prefix": "hel",
        "metrics": metrics,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference",
        default="cb101da",
        metavar="REV",
        help="historical index and base-dictionary revision (default: cb101da)",
    )
    parser.add_argument("--_scenario", choices=SCENARIOS, help=argparse.SUPPRESS)
    parser.add_argument("--_phase", choices=("memory", "timing"), help=argparse.SUPPRESS)
    parser.add_argument("--_historical-base", help=argparse.SUPPRESS)
    parser.add_argument("--_reference-symspell", help=argparse.SUPPRESS)
    parser.add_argument("--_reference-prefix", help=argparse.SUPPRESS)
    return parser


if __name__ == "__main__":
    parsed = _parser().parse_args()
    if parsed.reference.startswith("-"):
        raise SystemExit("--reference must not begin with '-'")
    if parsed._scenario:
        if not parsed._phase or not parsed._historical_base:
            raise SystemExit("worker requires --_phase and --_historical-base")
        raise SystemExit(_worker(parsed))
    raise SystemExit(_main(parsed))
