"""Shared fixtures for Alpha-OSK tests."""

from __future__ import annotations

import os
import sys
import zlib
from pathlib import Path

import pytest

# A test process that crashes must not put a Windows "Application Error"
# dialog on the developer's desktop: it waits for a click, and on a machine
# driven by an on-screen keyboard that is a modal box in the way.  Set at
# import, so every xdist worker sets it for itself (children inherit it
# too).  faulthandler still prints the Python stacks.
if sys.platform == "win32":
    import ctypes

    _SEM_FAILCRITICALERRORS = 0x0001
    _SEM_NOGPFAULTERRORBOX = 0x0002
    ctypes.windll.kernel32.SetErrorMode(_SEM_FAILCRITICALERRORS | _SEM_NOGPFAULTERRORBOX)

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

# Headless QML tests run under the Qt Quick Controls style the app ships
# with. `keyboard_app._setup_platform_env` sets "Basic" before Qt starts, so
# no shipped build ever runs a native style; a test process that left the
# choice to Qt got the native style on Windows, which warns "does not support
# customization" for every customised control in the app. Those warnings then
# had to be whitelisted, and a whitelist on a fail-on-any-QML-warning test is
# a hole in it. Set here rather than per module because Qt fixes the style
# for the whole process at the first Controls import, and xdist puts
# unrelated modules in one worker.
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

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
    # The nightly fuzz job (.github/workflows/fuzz-nightly.yml). Not
    # derandomized, so each night draws new examples; `print_blob` makes a
    # failure print the `@reproduce_failure` line that replays it exactly.
    # Never the default: select it with `--hypothesis-profile=alpha-osk-deep`.
    settings.register_profile(
        "alpha-osk-deep",
        parent=settings.get_profile("alpha-osk"),
        max_examples=3000,
        derandomize=False,
        print_blob=True,
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

    ``caret_position_token()`` is the third, and it reads the same
    desktop: the click poll now compares it across ticks to tell a click
    that moved the caret from one that did not.  Stubbed to ``None``,
    which is the fail-closed answer (no caret published) and therefore
    the *conservative* one -- a test that does not care about the caret
    gets the reset-on-every-click behaviour, which is what every test
    written before that refinement assumed.  Left live, whether the
    developer happened to have a text cursor blinking somewhere decided
    whether the reset fired, and the assertions about it would pass or
    fail per machine.

    The fourth is the focus probe (``start_focus_probe``), the thread
    that owns UI Automation on Windows and reads the live desktop five
    times a second.  Stubbed to ``None``, so every bridge takes the
    direct path through the three names above; a test of the probe path
    patches the same name with a factory over a fake detector (see
    ``tests/test_focus_probe.py``), which also keeps a real thread from
    outliving the test that forgot to stop it.

    All four stubs are plain module attributes, so any test that wants
    another answer patches the same name and wins.

    This fixture is function-scoped, so it is not yet in place while a
    module-scoped fixture builds its bridge; the probe stub is repeated
    for the whole session in ``_no_real_focus_probe_anywhere`` below.
    """
    monkeypatch.setattr("src.keyboard_bridge.is_password_field", lambda: False)
    monkeypatch.setattr("src.keyboard_bridge.external_click_detected", lambda: False)
    monkeypatch.setattr("src.keyboard_bridge.caret_position_token", lambda: None)
    monkeypatch.setattr("src.keyboard_bridge.start_focus_probe", lambda on_reading=None: None)


@pytest.fixture(scope="session", autouse=True)
def _no_real_focus_probe_anywhere():
    """Keep the real focus probe out of bridges built by module fixtures.

    Pytest sets up higher-scoped fixtures before function-scoped ones, so
    a ``scope="module"`` fixture that builds a ``KeyboardBridge`` ran
    before ``_unplug_the_live_desktop`` and started a real UI Automation
    probe on Windows that nothing stopped.  It outlived its module and
    crashed a later test's worker with an access violation (CI run
    44616698984, during tests/test_qml_dictation.py).  Function-scoped
    patches of the same name still win inside their test.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.keyboard_bridge.start_focus_probe", lambda on_reading=None: None)
        yield


@pytest.fixture(scope="session", autouse=True)
def _no_mode_picked_from_the_real_foreground_app():
    """Keep the foreground poll from choosing game or compat mode off the desktop.

    The bridge's 4 Hz poll classifies the real window in front of the
    tests: ``_window_is_game`` (a fullscreen browser or video reads as a
    game, by the borderless-fullscreen heuristic) and
    ``_window_needs_compat_mode`` (an IDE or remote-desktop client, by exe
    name).  In game mode a character goes out through ``send_key`` with a
    hold, so a test counting ``send_text`` calls saw nothing typed, but
    only once a test ran long enough for the poll to tick, which load
    decides: four QML click tests failed at 16 workers and passed alone.
    Session-scoped so module-scoped bridges are covered too; a test that
    wants either mode sets ``_game_auto_active`` / ``_compat_auto_active``
    or patches the same name back (``TestGameKeyHold`` does).
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("src.keyboard_bridge._window_is_game", lambda hwnd: False)
        mp.setattr("src.keyboard_bridge._window_needs_compat_mode", lambda hwnd: False)
        yield


@pytest.fixture(scope="session", autouse=True)
def _no_llm_loader_thread():
    """Never start the optional LLM loader thread.

    ``HybridPredictor.enable_llm = True`` (and the bridge paths that set it)
    starts a daemon thread that imports ``transformers`` and fetches
    DistilGPT-2 from Hugging Face.  Where those packages are installed (a
    developer's Python, not CI's venv) the thread outlived its test and was
    still importing torch while later tests drove Qt on the main thread,
    which crashed workers with an access violation, and the suite
    downloaded a 340 MB model.  No test needs the real load; one that
    does can patch the same name back.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "src.prediction.hybrid_predictor.HybridPredictor._load_llm_async",
            lambda self: None,
        )
        yield


@pytest.fixture(autouse=True)
def _no_real_update_relauncher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop the suite spawning real, detached update-relauncher processes.

    ``download_and_install`` ends by calling ``_spawn_relauncher``, which
    launches the update helper (``alpha-osk-relauncher.exe``) as a
    *detached* process, deliberately, so it outlives the app it is
    replacing.  Several tests drive ``download_and_install`` far enough
    to reach it while stubbing only ``_launch_installer``, so every run
    of ``tests/test_updater.py`` left four of these behind.

    They outlive the run.  The helper is bounded (it always was: a dead
    ``--parent-pid`` returns from the parent wait immediately, and the
    phases sum to a ceiling), but "bounded" meant minutes of a detached,
    console-less process per spawn, and the suite spawned four per run.
    That is how this was found: a row of empty terminals on screen,
    mistaken for the pre-push hook popping windows.  The dev-mode path
    exits promptly now rather than waiting out an installer that does
    not exist, so the cost is smaller than it was; the guard stays,
    because a test has no business spawning a detached process at all.

    Autouse rather than per-test because the property wanted is "no test
    spawns a real OS process", and that has to hold for tests nobody has
    written yet.  A test that wants different behaviour patches the same
    name and wins, since its own monkeypatch applies after this one.
    """
    try:
        from src import updater
    except ImportError:  # pragma: no cover - updater is always importable
        return
    monkeypatch.setattr(updater, "_spawn_relauncher", lambda *a, **kw: None)


def _refuse_the_real_pad_bus(mp: pytest.MonkeyPatch) -> None:
    """Patch the two entry points that touch the real virtual gamepad bus."""
    from src.platform import padbus_client

    def _no_bus(*args: object, **kwargs: object) -> None:
        raise padbus_client.PadBusNotFound("the test suite never opens the real pad bus")

    mp.setattr("src.platform.padbus_client.open_bus", _no_bus)
    mp.setattr("src.platform.padbus_client.find_bus", lambda *a, **kw: None)


@pytest.fixture(autouse=True)
def _no_real_pad_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep every test away from the real virtual gamepad bus.

    The ViGEmBus driver may be installed on a developer's machine, and a
    pad plugged into it is a real controller, visible to every program
    (a game open in the background would see it move). So no test may
    reach ``open_bus``: it raises ``PadBusNotFound``, and ``find_bus``
    reports no bus, so ``bus_present()`` is False.

    A test that wants a bus patches its own fake over this one, which wins
    because its monkeypatch applies later. The session-scoped twin below
    covers module-scoped fixtures built before this one runs.
    """
    _refuse_the_real_pad_bus(monkeypatch)


@pytest.fixture(scope="session", autouse=True)
def _no_real_pad_bus_anywhere():
    """Session-scoped twin of ``_no_real_pad_bus`` for module-scoped fixtures."""
    with pytest.MonkeyPatch.context() as mp:
        _refuse_the_real_pad_bus(mp)
        yield


def _refuse_the_real_pointer_clip(mp: pytest.MonkeyPatch) -> None:
    """Make the real pointer api unavailable (``win32_clip_api`` returns None)."""
    mp.setattr("src.platform.pointer_clip.win32_clip_api", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_pointer_clip(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite never confines or moves the developer's real pointer.

    The pointer is this app's user's only input device, and a clip outlives
    the process that set it, so a stray ``ClipCursor`` from a test could box
    it in. ``win32_clip_api`` returns None, so ``PointerConfiner.start()`` is
    False and no guard process is spawned. A test that wants an api passes
    its own fake to ``PointerConfiner(api_factory=...)``.
    """
    _refuse_the_real_pointer_clip(monkeypatch)


@pytest.fixture(scope="session", autouse=True)
def _no_real_pointer_clip_anywhere():
    """Session-scoped twin of ``_no_real_pointer_clip`` for module-scoped fixtures."""
    with pytest.MonkeyPatch.context() as mp:
        _refuse_the_real_pointer_clip(mp)
        yield


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
    default constructor), ``src.key_actions``
    (``KeyActionStore()``'s, constructed by every ``KeyboardBridge``)
    and ``src.keyboard_app``.

    A test that wants the real function needs no opt-out: it just has to
    import ``get_config_dir`` by name, the way ``tests/test_platform.py``
    does at module load, before this fixture ever runs. That captures the
    original function object directly, which this fixture's
    ``monkeypatch.setattr`` calls (module *attribute* assignments) never
    touch. A test that wants a different fake path patches the same
    dotted name and wins, same as the other autouse guards here.

    Being function-scoped, it is not yet in place while a module-scoped
    fixture builds its bridge; ``_stay_off_the_real_config_dir_for_the_session``
    below covers those with a session-wide fake directory.
    """
    _redirect_config_dir(monkeypatch, tmp_path / "fake-appdata" / "alpha-osk")


@pytest.fixture(scope="session", autouse=True)
def _stay_off_the_real_config_dir_for_the_session(tmp_path_factory: pytest.TempPathFactory):
    """The same redirect for the whole session, under its own temp directory.

    Pytest sets up higher-scoped fixtures first, so a ``scope="module"``
    fixture that builds a ``KeyboardBridge`` would otherwise load, and could
    write, the developer's real ``%APPDATA%/alpha-osk``.  Each test still
    gets its own directory from the function-scoped fixture above.
    """
    with pytest.MonkeyPatch.context() as mp:
        _redirect_config_dir(mp, tmp_path_factory.mktemp("session-appdata") / "alpha-osk")
        yield


def _redirect_config_dir(mp: pytest.MonkeyPatch, fake_config_dir: Path) -> None:
    """Point every ``get_config_dir`` binding the app holds at *fake_config_dir*."""

    def _fake_get_config_dir() -> Path:
        fake_config_dir.mkdir(parents=True, exist_ok=True)
        return fake_config_dir

    mp.setattr("src.platform.get_config_dir", _fake_get_config_dir)
    mp.setattr("src.snippets.get_config_dir", _fake_get_config_dir)
    mp.setattr("src.key_actions.get_config_dir", _fake_get_config_dir)
    try:
        import src.keyboard_app  # noqa: F401  -- needs PySide6 to import at all
    except ImportError:  # pragma: no cover - PySide6 missing in this environment
        return
    mp.setattr("src.keyboard_app.get_config_dir", _fake_get_config_dir)


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
