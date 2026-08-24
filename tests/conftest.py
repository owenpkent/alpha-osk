"""Shared fixtures for Alpha-OSK tests."""

from __future__ import annotations

import os
import sys
import zlib
from pathlib import Path

import pytest

# ----------------------------------------------------------------------
#  Sharding across machines (--shard-id / --shard-count)
# ----------------------------------------------------------------------
#
# `-n auto` spreads the suite over the cores of ONE machine.  This spreads
# it over several machines, which is a different axis and the one CI was
# short of: the runners have 4 cores against a dev box's 16, so the same
# suite that takes a minute locally took 14 minutes on ubuntu and 19 on
# windows, and every push waited on the slower of the two.  Both are
# needed together -- each shard still runs `-n auto` over its own cores.
#
# Deliberately not `pytest-split`: it is a fine library, but it wants a
# recorded durations file to balance well, that file is a build artefact
# that goes stale silently, and this repo pins every dependency exactly
# and CVE-scans the lockfiles, so a new one has a real ongoing cost.
# Assigning by a hash of the node id balances well enough at this size
# (1600 tests over 4 shards) and has no moving parts.
#
# **The hash must be stable across processes, so it cannot be `hash()`.**
# CPython salts string hashing per process unless PYTHONHASHSEED is set,
# and every xdist worker inside a shard is its own process: with the
# builtin, workers would disagree about which tests belong to the shard,
# which does not fail loudly, it just runs some tests twice and others
# never.  `zlib.crc32` is stable by definition.


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("sharding")
    group.addoption(
        "--shard-id",
        type=int,
        default=None,
        help="Run only the tests belonging to this shard (0-based). Requires --shard-count.",
    )
    group.addoption(
        "--shard-count",
        type=int,
        default=None,
        help="Total number of shards the suite is split into.",
    )


def shard_bucket(nodeid: str, shard_count: int) -> int:
    """Which shard *nodeid* belongs to.  Stable across processes.

    Module level and named without a leading underscore so
    ``tests/test_sharding.py`` can assert the partition property against
    it: the one way this can fail is silently, by dropping a test from
    every shard at once, and a suite that quietly runs fewer tests than
    it collected still reports green.
    """
    return zlib.crc32(nodeid.encode("utf-8")) % shard_count


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Keep only this shard's tests, by a stable hash of the node id.

    Deselecting rather than dropping, so the run reports how many tests
    the shard skipped and a shard that accidentally matches nothing is
    visible instead of reading as a fast green run.
    """
    shard_id = config.getoption("--shard-id")
    shard_count = config.getoption("--shard-count")
    if shard_id is None and shard_count is None:
        return
    if shard_id is None or shard_count is None:
        raise pytest.UsageError("--shard-id and --shard-count must be given together")
    if shard_count < 1:
        raise pytest.UsageError("--shard-count must be at least 1")
    if not 0 <= shard_id < shard_count:
        raise pytest.UsageError(f"--shard-id must be in [0, {shard_count})")

    keep, drop = [], []
    for item in items:
        bucket = shard_bucket(item.nodeid, shard_count)
        (keep if bucket == shard_id else drop).append(item)
    items[:] = keep
    config.hook.pytest_deselected(items=drop)


# Point Qt's offscreen plugin at the system fonts on Windows.
#
# Without this it resolves a fixed-width placeholder where every glyph has
# the same advance, which makes the prediction bar's width arithmetic
# trivially self-consistent. `requires_real_font` in
# tests/test_qml_prediction_bar.py detects that and skips, honestly, so
# seven pill-fitting tests ran on CI and nowhere else.
#
# That is a hole in the promise `check.py` makes, which is that passing it
# locally means passing CI: those seven cover the no-elide guarantee, they
# are the ones a bar-geometry change is most likely to break, and a change
# that broke them went out green from this machine and came back red from
# CI twenty minutes later. Set here rather than per module because it has
# to be in place before any QGuiApplication is constructed, and four test
# modules construct one.
#
# `setdefault`, so a contributor who has aimed Qt at their own font
# directory keeps it.
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

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


@pytest.fixture(autouse=True)
def _unplug_the_live_desktop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the bridge's environment probes from reading the real desktop.

    ``KeyboardBridge`` is constructed for real in these tests, and two of
    its inputs are the machine it happens to be running on:
    ``is_password_field()`` is called synchronously on *every* keystroke
    (``_check_password_field_sync``), and ``external_click_detected()``
    is polled on a timer.  On a Windows dev box both answer questions
    about whatever window the developer left focused, so a password field
    on screen flips the bridge into privacy mode mid-test and every
    ``_current_word`` assertion after it fails.  That reproduces as "the
    suite is flaky", passes on re-run, and passes on CI (Linux has no
    detector at all), which is the worst shape a failure can take.

    Both stubs are plain module attributes, so any test that wants the
    other answer patches the same name and wins.
    """
    monkeypatch.setattr("src.keyboard_bridge.is_password_field", lambda: False)
    monkeypatch.setattr("src.keyboard_bridge.external_click_detected", lambda: False)


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


@pytest.fixture(autouse=True)
def _stay_off_the_real_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the suite reading or writing the developer's real config directory.

    ``KeyboardBridge()`` and ``HybridPredictor()`` are constructed for real
    across several test modules (``tests/test_layouts.py``,
    ``tests/test_snippets.py``, the QML fuzz/property suites, and more),
    and both resolve their storage through ``get_config_dir()`` /
    ``get_model_dir()`` in ``src/platform``, which on a dev box is the real
    ``%APPDATA%/alpha-osk``.  That has already produced two defects: a word
    the developer had genuinely typed and learned outranked a candidate a
    test had just taught the engine (green on CI, which has no such file,
    red on the one machine that does), and the snippet auto-detection
    tests were overwriting the developer's real saved email and phone
    number with no undo.  ``tests/test_keyboard_bridge.py::bridge`` already
    works around both by hand, for the one fixture it covers; this closes
    the same hole for every other place a bridge or predictor gets built.

    ``get_model_dir()`` looks up ``get_config_dir()`` unqualified from
    inside ``src/platform``, so patching the one name there covers both.
    Most other callers import ``get_config_dir`` *inside* the function that
    uses it, which resolves against ``src.platform`` at call time and is
    covered by the same patch.  Two modules bind their own copy at import
    time instead (``from .platform import get_config_dir`` at module
    scope) and need patching directly, or they keep reading the real path
    through their own reference: ``src.snippets`` (``SnippetStore()``'s
    default constructor) and ``src.keyboard_app``.

    A test that wants the real function needs no opt-out: it just has to
    import ``get_config_dir`` by name, the way ``tests/test_platform.py``
    does at module load, before this fixture ever runs. That captures the
    original function object directly, which this fixture's
    ``monkeypatch.setattr`` calls (module *attribute* assignments) never
    touch. A test that wants a different fake path patches the same
    dotted name and wins, same as the other autouse guards here.
    """
    fake_config_dir = tmp_path / "fake-appdata" / "alpha-osk"

    def _fake_get_config_dir() -> Path:
        fake_config_dir.mkdir(parents=True, exist_ok=True)
        return fake_config_dir

    monkeypatch.setattr("src.platform.get_config_dir", _fake_get_config_dir)
    monkeypatch.setattr("src.snippets.get_config_dir", _fake_get_config_dir)
    try:
        import src.keyboard_app  # noqa: F401  -- needs PySide6 to import at all
    except ImportError:  # pragma: no cover - PySide6 missing in this environment
        return
    monkeypatch.setattr("src.keyboard_app.get_config_dir", _fake_get_config_dir)


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
            "the",
            "quick",
            "brown",
            "fox",
            "jumps",
            "over",
            "lazy",
            "dog",
            "cat",
            "sat",
            "mat",
            "hello",
            "help",
            "please",
            "thank",
            "thanks",
            "want",
            "need",
            "going",
            "today",
            "weather",
            "nice",
            "work",
            "store",
            "finish",
            "doing",
            "much",
            "your",
            "very",
            "with",
            "this",
            "that",
            "have",
            "from",
            "they",
            "will",
            "would",
            "there",
            "their",
            "what",
            "about",
            "which",
            "could",
            "should",
        )
    }
