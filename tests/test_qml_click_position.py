"""KeyButton reports where inside the key a press landed.

``KeyButton.qml`` has always received ``mouse.x / mouse.y`` on press and
used it for the ripple; it now also publishes the press as a fraction of
the key's width and height from its centre (``pressDx`` / ``pressDy``),
which ``Main.qml`` hands to the bridge with the character.  This loads
the component on its own in a ``QQuickView`` (one item, not a Repeater
delegate, so the scene point is reliable under the offscreen plugin,
unlike the trap the prediction-bar tests document) and presses at a known
point.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QPoint, Qt, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQuick import QQuickView  # noqa: E402
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

KEYBUTTON_QML = Path(__file__).resolve().parent.parent / "qml" / "components" / "KeyButton.qml"


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation; these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def key(qapp):
    view = QQuickView()
    warnings: list[str] = []
    view.engine().warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
    view.setSource(QUrl.fromLocalFile(str(KEYBUTTON_QML)))
    root = view.rootObject()
    assert root is not None, f"KeyButton.qml failed to load: {warnings}"
    root.setWidth(100)
    root.setHeight(60)
    view.resize(100, 60)
    view.show()
    QTest.qWaitForWindowExposed(view)
    yield view, root
    view.close()


class TestThePressReportsItsOffset:
    def test_a_press_off_centre_is_reported_as_a_fraction_of_the_key(self, key):
        view, root = key
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(90, 15))
        try:
            assert root.property("pressDx") == pytest.approx(0.4, abs=0.02)
            assert root.property("pressDy") == pytest.approx(-0.25, abs=0.02)
        finally:
            QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(90, 15))

    def test_a_press_dead_centre_reports_zero(self, key):
        view, root = key
        QTest.mousePress(view, Qt.LeftButton, Qt.NoModifier, QPoint(50, 30))
        try:
            assert root.property("pressDx") == pytest.approx(0.0, abs=0.02)
            assert root.property("pressDy") == pytest.approx(0.0, abs=0.02)
        finally:
            QTest.mouseRelease(view, Qt.LeftButton, Qt.NoModifier, QPoint(50, 30))

    def test_a_right_click_reports_it_too(self, key):
        view, root = key
        QTest.mousePress(view, Qt.RightButton, Qt.NoModifier, QPoint(10, 45))
        try:
            assert root.property("pressDx") == pytest.approx(-0.4, abs=0.02)
            assert root.property("pressDy") == pytest.approx(0.25, abs=0.02)
        finally:
            QTest.mouseRelease(view, Qt.RightButton, Qt.NoModifier, QPoint(10, 45))
