#!/usr/bin/env python3
"""Reference (Python) backend runner for the cross-backend conformance harness.

Reads conformance requests as JSONL on stdin and writes results as JSONL on
stdout, one output line per input line, preserving `id`. The C++ backend must
implement the *same* stdin/stdout contract (see tests/conformance/README.md) so
the two can be diffed line-for-line.

Request line (JSON object):
    {"id": "<str>", "mode": "predict", "context": "<str>", "n": 5}
    {"id": "<str>", "mode": "autocorrect", "typed_word": "<str>", "context": "<str>"}

Result line (JSON object):
    {"id": "<str>", "mode": "predict", "result": ["w1", "w2", ...]}
    {"id": "<str>", "mode": "autocorrect", "corrected": "the"}   # or null

The model directory is passed explicitly so both backends load an identical,
pinned model state instead of the user's live (mutating) model. Point it at an
empty dir to compare the cold-start behaviour trained from the shared `data/`
files (deterministic and identical across a single checkout).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make `src` importable when run as a plain script (no install step).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402


def _build_predictor(model_dir: Path, merge_strategy: str) -> HybridPredictor:
    predictor = HybridPredictor(model_dir=model_dir, enable_llm=False)
    predictor.set_merge_strategy(merge_strategy)
    return predictor


def _handle(predictor: HybridPredictor, req: dict) -> dict:
    mode = req.get("mode", "predict")
    out: dict = {"id": req.get("id"), "mode": mode}
    if mode == "predict":
        n = int(req.get("n", 5))
        out["result"] = list(predictor.predict(req.get("context", ""), n))
    elif mode == "autocorrect":
        corrected = predictor.check_autocorrect(req.get("typed_word", ""), req.get("context", ""))
        out["corrected"] = corrected
    else:
        out["error"] = f"unknown mode: {mode!r}"
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-dir",
        required=True,
        type=Path,
        help="Pinned model directory both backends load (may be empty for cold start).",
    )
    parser.add_argument(
        "--merge-strategy",
        default="rank",
        choices=["rank", "rrf", "linear", "loglinear"],
        help="Hybrid merge strategy (default: rank, the shipped default).",
    )
    args = parser.parse_args(argv)

    predictor = _build_predictor(args.model_dir, args.merge_strategy)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        result = _handle(predictor, req)
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
