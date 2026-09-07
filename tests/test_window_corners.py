"""Who rounds the window's corners.

The transparent windows here are ``WS_EX_LAYERED`` on Windows, and a QML
``radius`` on a layered window leaves corner pixels that come back white
rather than transparent: a bright notch biting into a corner of the
keyboard. So on Windows the background is squared off and DWM masks the
corner instead. The measurements and the reasoning are under *Who rounds
the window corners* in ``CLAUDE.md``.

**The compositing itself cannot be tested here.** The offscreen platform
plugin has no compositor, and the offscreen render was correct the whole
time this bug was visible on screen, so a test that rendered the corner and
looked at it would have passed against the bug. What these tests pin is the
half that is checkable: which side is asked to do the rounding, that every
transparent window agrees about it, what the DWM call asks for, and that it
can never break startup.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QObject, QSettings, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
    from PySide6.QtQuick import QQuickItem  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from src.platform import windows_window  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

ON_WINDOWS = sys.platform == "win32"

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)

# The windows whose background is transparent, and therefore layered on
# Windows.  Each names the object carrying `selfRoundedCorners`: the two
# pickers hold it on the Window, the dashboard on the panel filling it.
TRANSPARENT_WINDOWS = ("snippetsWindow", "symbolsWindow", "vizContent")

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(f in w for f in IGNORED_WARNING_FRAGMENTS)]


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


@pytest.fixture(scope="module")
def qml_root(qapp):
    """Main.qml, loaded once for the module.

    Every test here only reads properties, so one load serves them all; the
    sibling QML modules reload per test because their tests mutate state.
    """
    QSettings(TEST_ORG, TEST_APP).clear()
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()

    warnings: list[str] = []
    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    QCoreApplication.processEvents()
    try:
        yield root, warnings
    finally:
        del engine


class TestWhoRoundsTheCorners:
    def test_windows_hands_the_corner_to_the_compositor(self, qml_root) -> None:
        # On Windows the background must be square so nothing is left
        # unpainted; everywhere else we round it ourselves.  The radius is
        # the same decision expressed in pixels.
        root, warnings = qml_root
        assert root.property("selfRoundedCorners") is (not ON_WINDOWS)
        assert root.property("windowRadius") == (0 if ON_WINDOWS else 10)
        assert _real_warnings(warnings) == []

    def test_the_background_actually_uses_it(self, qml_root) -> None:
        # The property is only worth anything if the rectangle is bound to
        # it; a stale literal on the Rectangle would satisfy the test above
        # and still paint the notch.
        root, _ = qml_root
        background = root.findChild(QQuickItem, "windowBackground")
        assert background is not None, "windowBackground not found"
        assert background.property("radius") == root.property("windowRadius")

    def test_every_transparent_window_agrees(self, qml_root) -> None:
        # The pickers and the dashboard are transparent in exactly the same
        # way, so a fix that reached only the keyboard would leave the notch
        # on the windows that float over the app the user is typing into.
        # The dashboard was missed once, which is why the list is named.
        root, _ = qml_root
        expected = root.property("selfRoundedCorners")
        for name in TRANSPARENT_WINDOWS:
            # Two of these are Windows rather than Items, so the lookup is
            # typed on QObject; a QQuickItem-typed findChild returns None
            # for them and every assertion after it would never run.
            target = root.findChild(QObject, name)
            assert target is not None, f"{name} not found"
            assert target.property("selfRoundedCorners") == expected, (
                f"{name} disagrees with the keyboard about who rounds corners"
            )

    def test_the_screenshot_script_can_take_the_rounding_back(self, qml_root) -> None:
        # capture_screenshots.py renders through the offscreen plugin, where
        # Qt.platform.os still says "windows" but nothing composites the
        # corner, so it sets this back.  A readonly property would make
        # that assignment a silent no-op.
        root, _ = qml_root
        before = root.property("selfRoundedCorners")
        try:
            root.setProperty("selfRoundedCorners", True)
            assert root.property("windowRadius") == 10
            assert root.findChild(QObject, "vizContent").property("selfRoundedCorners") is True
        finally:
            root.setProperty("selfRoundedCorners", before)


class TestTheDwmCall:
    """The Win32 half, run on every platform through a fake ``windll``.

    ``sys.platform`` is patched to ``win32`` for the duration, the same
    arrangement ``tests/test_windows_window.py`` uses, so the body mypy
    prunes under ``--platform linux`` is still exercised on the Linux shards.
    """

    @pytest.fixture
    def windll(self, monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
        monkeypatch.setattr(sys, "platform", "win32")
        fake = MagicMock()
        fake.dwmapi.DwmSetWindowAttribute.return_value = 0
        with patch("ctypes.windll", fake):
            yield fake

    def test_it_asks_for_round_corners(self, windll: MagicMock) -> None:
        # The constants are what make the call mean anything: a transposed
        # attribute id or DWMWCP_DONOTROUND would be swallowed silently.
        windows_window._prefer_dwm_rounded_corners(0x1234)
        (hwnd, attribute, value, size), _ = windll.dwmapi.DwmSetWindowAttribute.call_args
        assert hwnd == 0x1234
        assert attribute == DWMWA_WINDOW_CORNER_PREFERENCE
        assert value._obj.value == DWMWCP_ROUND
        assert size == 4

    def test_apply_extended_styles_reaches_it_before_any_early_return(
        self, windll: MagicMock
    ) -> None:
        # A failed style read returns early and is logged; the corner needs
        # nothing that read computes, so it must not be lost with it.
        windll.kernel32.GetLastError.return_value = 5
        windll.user32.GetWindowLongW.return_value = 0
        root = MagicMock()
        root.winId.return_value = 0x1234
        windows_window.apply_extended_styles(root)
        windll.dwmapi.DwmSetWindowAttribute.assert_called_once()

    def test_the_dashboard_gets_only_the_corner(self, windll: MagicMock) -> None:
        # The dashboard is allowed to take focus, so it must not go through
        # apply_extended_styles and pick up WS_EX_NOACTIVATE with the corner.
        window = MagicMock()
        window.winId.return_value = 0x1234
        windows_window.prefer_dwm_rounded_corners(window)
        windll.dwmapi.DwmSetWindowAttribute.assert_called_once()
        windll.user32.SetWindowLongW.assert_not_called()

    def test_it_never_raises_when_dwm_refuses(self, windll: MagicMock) -> None:
        # Windows 10 has no DWMWA_WINDOW_CORNER_PREFERENCE, so the call
        # fails there by design and the window keeps square corners. A
        # cosmetic difference is never a reason to fail startup.
        windll.dwmapi.DwmSetWindowAttribute.return_value = -2147024809
        windows_window._prefer_dwm_rounded_corners(0)

    def test_it_swallows_a_missing_dwmapi(self, windll: MagicMock) -> None:
        windll.dwmapi.DwmSetWindowAttribute.side_effect = OSError("no dwmapi")
        windows_window._prefer_dwm_rounded_corners(0)

    def test_a_window_with_no_native_handle_is_left_alone(self, windll: MagicMock) -> None:
        window = MagicMock()
        window.winId.side_effect = RuntimeError("not created yet")
        windows_window.prefer_dwm_rounded_corners(window)
        windll.dwmapi.DwmSetWindowAttribute.assert_not_called()
