"""Who rounds the window's corners.

Every window in this app is frameless and, on Windows, ``WS_EX_LAYERED``.
Rounding a corner ourselves with a QML ``radius`` leaves the pixels outside
the arc unpainted, and on a layered window those do not composite the
desktop the way a transparent pixel should: they come back white. What the
user sees is a bright notch biting into a corner of the keyboard.

Measured on the real ``Main.qml`` against a magenta backdrop placed behind
all four corners:

===========================================  =========================
configuration                                corner pixel
===========================================  =========================
radius 10, Windows 11 default rounding       ``#ffffff``
radius 10, ``DWMWCP_DONOTROUND``             ``#ffffff``
radius 0, ``DWMWCP_ROUND``                   the backdrop, correctly
===========================================  =========================

Turning Windows' own rounding off fixes nothing, which is the part worth
remembering: the notch is the *unpainted region*, not the rounding. So on
Windows the background is squared off and DWM masks the corner instead.

**The compositing itself cannot be tested here.** The offscreen platform
plugin has no compositor, and the offscreen render was correct the whole
time this bug was visible on screen, so a test that rendered the corner and
looked at it would have passed against the bug. What these tests pin is the
half that is checkable: which side is asked to do the rounding, that every
window agrees about it, and that the DWM call can never break startup.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QSettings, QUrl  # noqa: E402
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
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

ON_WINDOWS = sys.platform == "win32"


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
def qml_root(qapp):
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
        yield root
    finally:
        del engine


class TestWhoRoundsTheCorners:
    def test_windows_hands_the_corner_to_the_compositor(self, qml_root) -> None:
        root = qml_root
        self_rounded = root.property("selfRoundedCorners")
        assert self_rounded is (not ON_WINDOWS), (
            "on Windows the background must be square so nothing is left "
            "unpainted; everywhere else we round it ourselves"
        )

    def test_the_radius_follows_that_decision(self, qml_root) -> None:
        root = qml_root
        radius = root.property("windowRadius")
        assert radius == (0 if ON_WINDOWS else 10)

    def test_the_background_actually_uses_it(self, qml_root) -> None:
        # The property is only worth anything if the rectangle is bound to
        # it; a stale literal on the Rectangle would satisfy the two tests
        # above and still paint the notch.
        root = qml_root
        background = root.findChild(QQuickItem, "windowBackground")
        assert background is not None, "windowBackground not found"
        assert background.property("radius") == root.property("windowRadius")

    def test_every_window_agrees(self, qml_root) -> None:
        # The snippets and symbols windows are frameless and transparent in
        # exactly the same way, so a fix that reached only the keyboard
        # would leave the notch on the two windows that float over the app
        # the user is typing into.
        root = qml_root
        expected = root.property("selfRoundedCorners")
        for name in ("snippetsWindow", "symbolsWindow"):
            # These are Windows rather than Items, so a QQuickItem-typed
            # findChild returns None and every assertion after it would
            # silently never run.
            window = next(
                (c for c in root.findChildren(object) if c.objectName() == name),
                None,
            )
            assert window is not None, f"{name} not found"
            assert window.property("selfRoundedCorners") == expected, (
                f"{name} disagrees with the keyboard about who rounds corners"
            )


@pytest.mark.skipif(not ON_WINDOWS, reason="DWM is Windows-only")
class TestTheDwmCallIsSafe:
    def test_it_never_raises_when_dwm_refuses(self) -> None:
        # Windows 10 has no DWMWA_WINDOW_CORNER_PREFERENCE, so the call
        # fails there by design and the window keeps square corners. A
        # cosmetic difference is never a reason to fail startup.
        from src.platform import windows_window

        with patch("ctypes.windll") as windll:
            windll.dwmapi.DwmSetWindowAttribute.return_value = -2147024809
            windows_window._prefer_dwm_rounded_corners(0)

    def test_it_swallows_a_missing_dwmapi(self) -> None:
        from src.platform import windows_window

        with patch("ctypes.windll") as windll:
            windll.dwmapi.DwmSetWindowAttribute.side_effect = OSError("no dwmapi")
            windows_window._prefer_dwm_rounded_corners(0)
