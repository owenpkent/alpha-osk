"""Shared fixtures for Alpha-OSK tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_real_update_relauncher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the suite spawning real, detached update-relauncher processes.

    ``download_and_install`` ends by calling ``_spawn_relauncher``, which
    launches ``python -m src.keyboard_app --update-relauncher`` as a
    *detached* process, deliberately, so it outlives the app it is
    replacing.  Several tests drive ``download_and_install`` far enough
    to reach it while stubbing only ``_launch_installer``, so every run
    of ``tests/test_updater.py`` left four of these behind.

    They do not clean themselves up.  The helper waits on a
    ``--parent-pid`` that is gone the moment the pytest worker exits, and
    it has no exit path for that, so each one sits there forever holding
    a console window (see ``TODO.md``).  Running the suite a few times
    leaves a row of empty terminals on screen and a pile of stranded
    processes -- which is how this was found: they were mistaken for the
    pre-push hook popping windows.

    Autouse rather than per-test because the property wanted is "no test
    spawns a real OS process", and that has to hold for tests nobody has
    written yet.  A test that wants different behaviour patches the same
    name and wins, since its own monkeypatch applies after this one.
    """
    try:
        from src import updater
    except ImportError:  # pragma: no cover - updater is always importable
        return
    monkeypatch.setattr(updater, "_spawn_relauncher", lambda *a, **kw: True)


@pytest.fixture
def tmp_model_dir(tmp_path: Path) -> Path:
    """Temporary directory for model files."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    return model_dir


@pytest.fixture
def sample_corpus() -> str:
    """Small text corpus for training predictors."""
    return (
        "the quick brown fox jumps over the lazy dog. "
        "the cat sat on the mat. "
        "I want to go to the store. "
        "how are you doing today. "
        "please help me with this. "
        "thank you very much for your help. "
        "hello how are you. "
        "the weather is nice today. "
        "I need to finish this work. "
        "can you help me please. "
    )


@pytest.fixture
def small_dictionary() -> dict[str, float]:
    """Small word→frequency map for testing fuzzy recognition."""
    return {
        word: 1.0
        for word in (
            "the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog",
            "cat", "sat", "mat", "hello", "help", "please", "thank", "thanks",
            "want", "need", "going", "today", "weather", "nice", "work",
            "store", "finish", "doing", "much", "your", "very", "with",
            "this", "that", "have", "from", "they", "will", "would",
            "there", "their", "what", "about", "which", "could", "should",
        )
    }
