"""Benchmarks delete only the model directories they create themselves."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")


@pytest.mark.parametrize("learn_half", [False, True])
@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize("failure", [None, RuntimeError, KeyboardInterrupt])
def test_benchmark_model_directory_lifetime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    learn_half: bool,
    supplied: bool,
    failure: type[BaseException] | None,
) -> None:
    disabled = logging.root.manager.disable
    try:
        ksr = importlib.import_module("scripts.bench.ksr")
    finally:
        logging.disable(disabled)
    monkeypatch.setattr(ksr.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(ksr, "load_corpus", lambda _: [])
    monkeypatch.setattr(ksr, "_warmup", lambda *_: None)
    monkeypatch.setattr(ksr, "print_table", lambda *_: None)
    seen: list[Path] = []

    def use_model(model_dir: Path, *args, **kwargs):
        seen.append(model_dir)
        assert model_dir.is_dir()
        (model_dir / "model.json").write_text("{}", encoding="utf-8")
        if failure is not None:
            raise failure("interrupted benchmark")
        return SimpleNamespace(_ngram=SimpleNamespace(unigrams={}, bigrams={}, trigrams={}))

    monkeypatch.setattr(ksr, "HybridPredictor", use_model)
    monkeypatch.setattr(ksr, "run_learn_half", use_model)
    argv = ["--conditions", ""]
    if learn_half:
        argv.append("--learn-half")
    if supplied:
        user_model = tmp_path / "my-model"
        user_model.mkdir()
        (user_model / "keep.txt").write_text("personal model", encoding="utf-8")
        argv += ["--model-dir", str(user_model)]

    if failure is None:
        assert ksr.main(argv) == 0
    else:
        with pytest.raises(failure, match="interrupted benchmark"):
            ksr.main(argv)

    assert len(seen) == 1
    if supplied:
        assert (seen[0] / "keep.txt").read_text(encoding="utf-8") == "personal model"
        assert (seen[0] / "model.json").exists()
    else:
        assert not seen[0].exists()
