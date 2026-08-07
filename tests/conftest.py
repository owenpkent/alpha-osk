"""Shared fixtures for Alpha-OSK tests."""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from hypothesis import HealthCheck, Verbosity, settings
except ImportError:  # pragma: no cover - hypothesis is a dev-only dep
    pass
else:
    # A property test that only fails on a machine with the right cached
    # corpus is worse than no property test: the failure is unreproducible
    # and the suite looks flaky. `database=None` disables the .hypothesis
    # example database so every run explores from the same seed, and the
    # deadline is off because example generation shares a process with a
    # 20k-word dictionary load and CI runners stall unpredictably.
    settings.register_profile(
        "alpha-osk",
        max_examples=150,
        database=None,
        deadline=None,
        derandomize=True,
        print_blob=True,
        suppress_health_check=[HealthCheck.too_slow],
        verbosity=Verbosity.normal,
    )
    # Fewer examples, same seed — for iterating locally without waiting.
    settings.register_profile(
        "alpha-osk-fast",
        parent=settings.get_profile("alpha-osk"),
        max_examples=25,
    )
    settings.load_profile("alpha-osk")


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
