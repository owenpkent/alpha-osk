"""The keyboard window cannot be made taller than the screen (issue #131).

Height is content-bound (`outerLayout.implicitHeight + 80`) and the keys
scale with width, so before this a wide enough window put the bottom rows,
the space bar among them, below the display, and the resize handle that
would have undone it went with them.

Nothing bounds the height directly: a width<->height handler pair is the
mutual recursion `test_qml_ui_state_fuzz.py` exists to catch, and the issue
records why the proportional shrink such a net allows never reaches the
goal. Instead `Main.qml` inverts the layout in closed form: for a panel
configuration the window height is affine in `keyH`, so `maxWidthForScreen`
is one assignment, applied by `fitWidth` wherever the width is set by hand.

What these tests pin, in order of weight:

1. The affine model matches the real layout, under every panel
   configuration that changes the row count. This is the guard that makes
   the clamp safe to trust: a new child of `outerLayout` that is not added
   to the model fails here, not on a 32:9 display.
2. The widest window `fitWidth` allows fits the screen, and a window a
   little wider than that does not (the clamp bites where it has to, and
   nowhere earlier).
3. A persisted width wider than the screen is clamped on restore, which is
   the path a user who once dragged too wide hits on every launch.

The issue warns that the height at a given width was not a well-defined
value in this harness. The reason was a layout measured before its polish
pass ran: `_relayout` forces a frame, which `test_qml_key_colors.py`
documents, and with it the numbers below are stable to the pixel.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QSettings, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression  # noqa: E402
    from PySide6.QtQuick import QQuickWindow  # noqa: E402,F401
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

# `maxWidthForScreen` leaves this much of the screen's available height
# unused, the same slack the width clamp in `onScreenChanged` keeps.
SCREEN_SLACK = 40

# The configurations that change the number of key-high rows or the pill
# bar: (compact view, a function row showing, suggestions bar showing).
CONFIGURATIONS = [
    pytest.param(False, False, True, id="full-size"),
    pytest.param(False, True, True, id="full-size+fkeys"),
    pytest.param(False, False, False, id="full-size-no-pills"),
    pytest.param(True, False, True, id="compact"),
    pytest.param(True, True, False, id="compact+fkeys-no-pills"),
]


def _settle(passes: int = 12) -> None:
    for _ in range(passes):
        QCoreApplication.processEvents()


def _relayout(root) -> None:
    """Force the polish pass; see `test_qml_key_colors._relayout`.

    The pill bar hides through a 150 ms `Behavior`, which runs on the wall
    clock: a settings-driven hide at load is still mid-flight when a
    zero-time event loop measures, and the height reads a partly collapsed
    bar. That is the spread the issue could not explain; waiting it out is
    what makes the numbers stable.
    """
    _settle()
    QTest.qWait(250)
    root.grabWindow()
    _settle()


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation - these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def load_main(qapp):
    """Load Main.qml under the given persisted settings; yields (engine, root)."""
    engines = []

    def _load(**saved):
        warnings: list[str] = []
        QSettings(TEST_ORG, TEST_APP).clear()
        settings = QSettings(TEST_ORG, TEST_APP)
        # The startup update check fires a real HTTPS request from a daemon
        # thread that outlives the fixture.
        settings.setValue("ui/savedAutoCheckUpdates", False)
        settings.setValue("ui/savedShowNavigation", False)
        settings.setValue("ui/savedShowNumpad", False)
        for key, value in saved.items():
            settings.setValue(f"ui/{key}", value)
        settings.sync()

        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.is_available.return_value = True
            synth.backend_name.return_value = "MockSynth"
            factory.return_value = synth
            bridge = KeyboardBridge()

        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
        install_context_properties(engine, bridge)
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
        root = engine.rootObjects()[0]
        _relayout(root)
        assert not warnings, "QML warnings:\n  " + "\n  ".join(warnings)
        engines.append(engine)
        return engine, root

    try:
        yield _load
    finally:
        for engine in engines:
            del engine


def _call(engine, root, js: str):
    expr = QQmlExpression(engine.rootContext(), root, js)
    value, _undefined = expr.evaluate()
    assert not expr.hasError(), expr.error().toString()
    return value.toVariant() if hasattr(value, "toVariant") else value


def _available_height(root) -> int:
    """What `maxWidthForScreen` takes the screen to offer, less its slack."""
    screen = root.screen()
    return min(screen.size().height(), screen.availableVirtualSize().height()) - SCREEN_SLACK


def _configure(load_main, compact: bool, fkeys: bool, pills: bool):
    return load_main(
        savedCompactView=compact,
        savedShowFunctionRow=fkeys,
        savedSuggestionsEnabled=pills,
    )


class TestTheHeightModelMatchesTheLayout:
    """`modelHeight(keyH)` is the window's real height, to the pixel per row.

    Each `Row` ceils its height above its keys, so the model carries a
    pixel per key-high row; the tolerance is that, no more. A model that
    were off by a whole row (a child of `outerLayout` it does not know
    about) is off by `keyH`, tens of pixels, and fails every width.
    """

    @pytest.mark.parametrize("compact,fkeys,pills", CONFIGURATIONS)
    @pytest.mark.parametrize("width", [None, 1000, 1700])
    def test_model_height_is_the_real_height(self, load_main, compact, fkeys, pills, width):
        engine, root = _configure(load_main, compact, fkeys, pills)
        if width is None:
            width = root.property("minimumWidth")
        root.setProperty("width", width)
        _relayout(root)
        assert root.property("width") == width, "the harness never clamps a direct write"

        key_h = root.property("keyH")
        modelled = _call(engine, root, f"modelHeight({key_h})")
        real = root.property("height")
        rows = root.property("keyHighRows")
        assert abs(real - modelled) <= rows, (
            f"height {real} vs model {modelled} at width {width}, keyH {key_h:.2f}"
        )

    def test_the_configurations_differ(self, load_main):
        """Pair for the above: the model is not one constant that happens to fit."""
        _engine, plain = _configure(load_main, False, False, True)
        _engine, fkeys = _configure(load_main, False, True, True)
        _engine, no_pills = _configure(load_main, False, False, False)
        assert fkeys.property("keyHighRows") == plain.property("keyHighRows") + 1
        assert no_pills.property("heightPerKeyH") < plain.property("heightPerKeyH")


class TestTheWidestWindowFitsTheScreen:
    @pytest.mark.parametrize("compact,fkeys,pills", CONFIGURATIONS)
    def test_max_width_fits(self, load_main, compact, fkeys, pills):
        engine, root = _configure(load_main, compact, fkeys, pills)
        max_w = _call(engine, root, "maxWidthForScreen()")
        assert max_w >= root.property("minimumWidth")
        root.setProperty("width", max_w)
        _relayout(root)
        assert root.property("height") <= _available_height(root), (
            f"width {max_w} is allowed but gives height {root.property('height')}"
        )

    @pytest.mark.parametrize("compact,fkeys,pills", CONFIGURATIONS)
    def test_a_wider_window_would_not(self, load_main, compact, fkeys, pills):
        """The inverse: the clamp is placed at the limit, not well short of it.

        300 px of width is about 17 px of keyH on a full-size board, which
        is a hundred pixels of height across its rows: comfortably past the
        slack the clamp leaves, and nowhere near a whole row.
        """
        engine, root = _configure(load_main, compact, fkeys, pills)
        max_w = _call(engine, root, "maxWidthForScreen()")
        root.setProperty("width", max_w + 300)
        _relayout(root)
        assert root.property("height") > _available_height(root)


class TestFitWidthClampsBothWays:
    def test_bounds(self, load_main):
        engine, root = _configure(load_main, False, False, True)
        min_w = root.property("minimumWidth")
        max_w = _call(engine, root, "maxWidthForScreen()")
        assert min_w < max_w
        assert _call(engine, root, "fitWidth(0)") == min_w
        assert _call(engine, root, "fitWidth(1000000)") == max_w
        mid = (min_w + max_w) // 2
        assert _call(engine, root, f"fitWidth({mid})") == mid


class TestAPersistedWidthIsClampedOnRestore:
    """A width dragged too wide before this fix is persisted; the next launch
    must not come back off the bottom of the screen."""

    def test_an_oversize_saved_width_restores_to_the_widest_fit(self, load_main):
        engine, root = load_main(
            savedCompactView=True, savedSuggestionsEnabled=True, savedWindowWidth=20000
        )
        max_w = _call(engine, root, "maxWidthForScreen()")
        # `layoutFixedPixels` was taken at 20000 px, where `keySpacing` is
        # far wider than it is at the answer, so the clamp lands a little
        # narrow of the exact fit; it never lands wide of it.
        assert max_w - 40 <= root.property("width") <= max_w
        assert root.property("height") <= _available_height(root)

    def test_a_saved_width_that_fits_is_kept(self, load_main):
        """Pair: the clamp does not touch a width that fits."""
        engine, root = load_main(savedCompactView=True, savedWindowWidth=900)
        assert root.property("width") == 900
