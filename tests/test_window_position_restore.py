"""The keyboard comes back where it was left.

It used to creep instead. ``Main.qml`` restores the saved position in
``Component.onCompleted``, and ``keyboard_app.py::_apply_window_flags``
then called ``setFlags`` on the already shown window to make it frameless.
On Windows, taking the frame off a shown window keeps the old outer
rectangle as the new client one, so the keyboard jumped 7 px left and
30 px up (and 15 px wider) right after the restore, and ``onXChanged``
saved the shifted spot. Measured on a real Windows desktop at 150%:
restored to (872, 902), sitting at (865, 872) a moment later.

The shift itself needs a native frame and cannot happen under the
offscreen platform, so what is pinned here is the cause: the window is
born with the flags, and the Python call that follows asks for exactly
the same set, which Qt treats as nothing to do. Each is paired with the
inverse that keeps it honest.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QSettings, Qt, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
    from PySide6.QtQuick import QQuickWindow  # noqa: E402,F401
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src import keyboard_app  # noqa: E402
from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

SAVED_X = 12
SAVED_Y = 34

OSK_HINTS = (
    Qt.WindowType.FramelessWindowHint,
    Qt.WindowType.WindowStaysOnTopHint,
    Qt.WindowType.WindowDoesNotAcceptFocus,
)


def _settle(passes: int = 8) -> None:
    for _ in range(passes):
        QCoreApplication.processEvents()


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
def root(qapp):
    warnings: list[str] = []
    QSettings(TEST_ORG, TEST_APP).clear()
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.setValue("ui/savedAutoCheckUpdates", False)
    # The offscreen screen is 800x800 and the default board is wider, which
    # would clamp the restore and make the position assertion meaningless.
    # Compact at its minimum width is the smallest real keyboard.
    settings.setValue("ui/savedCompactView", True)
    settings.setValue("ui/savedShowNavigation", False)
    settings.setValue("ui/savedShowNumpad", False)
    settings.setValue("ui/savedWindowWidth", 1)
    settings.setValue("ui/savedWindowX", SAVED_X)
    settings.setValue("ui/savedWindowY", SAVED_Y)
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
    window = engine.rootObjects()[0]
    _settle()
    try:
        yield window
    finally:
        del engine


class TestTheWindowIsBornWithItsFlags:
    def test_main_qml_declares_the_osk_flags_itself(self, root) -> None:
        flags = root.flags()
        for hint in OSK_HINTS:
            assert flags & hint, f"{hint} missing before Python touched the window"

    def test_the_python_call_asks_for_exactly_the_same_set(self, root) -> None:
        before = root.flags()
        with patch.object(keyboard_app, "CURRENT_PLATFORM", "linux"):
            keyboard_app._apply_window_flags(root)
        assert root.flags() == before

    def test_a_differing_set_would_be_seen(self, root) -> None:
        """The inverse: the equality above is capable of failing."""
        before = root.flags()
        root.setFlags(before ^ Qt.WindowType.WindowDoesNotAcceptFocus)
        assert root.flags() != before


class TestTheSavedPositionIsRestored:
    def test_the_keyboard_opens_where_it_was_left(self, root) -> None:
        assert (root.x(), root.y()) == (SAVED_X, SAVED_Y)

    def test_applying_the_flags_does_not_move_it(self, root) -> None:
        with patch.object(keyboard_app, "CURRENT_PLATFORM", "linux"):
            keyboard_app._apply_window_flags(root)
        _settle()
        assert (root.x(), root.y()) == (SAVED_X, SAVED_Y)
