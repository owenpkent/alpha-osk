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
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QMetaObject, QPoint, Qt, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQuick import QQuickView  # noqa: E402
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from src.prediction.token_predictor import TokenPredictor  # noqa: E402
from src.snippets import SnippetStore  # noqa: E402
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


class TestTheThreeArgumentSlotIsReachableFromQml:
    """The Python tests call ``pressKey(ch, dx, dy)`` directly, which never
    touches Qt's overload resolution: with Python defaults both call shapes
    work whatever the ``@Slot`` decorators say.  A call from QML goes
    through the meta-object system instead, and if the three-argument
    overload failed to bind there every press would silently degrade to
    the key centre with no warning anyone would see.  So this invokes both
    slots, in both shapes, from a QML function against a real bridge.
    """

    @pytest.fixture
    def bridge(self, qapp, tmp_path: Path) -> KeyboardBridge:
        # The same shape as the fixture in test_click_position.py, for the
        # same reason: the bridge reads the developer's own config directory
        # on construction and must not write to it.
        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.is_available.return_value = True
            synth.backend_name.return_value = "MockSynth"
            factory.return_value = synth
            b = KeyboardBridge()
            b._synth = synth
            b._snippets = SnippetStore(tmp_path / "snippets.json")
            b._snippets.load()
            b._predictor._ngram.tokens = TokenPredictor()
            return b

    def test_qml_can_pass_the_click_position_and_still_omit_it(
        self, qapp, bridge: KeyboardBridge, tmp_path: Path
    ):
        caller = tmp_path / "Caller.qml"
        caller.write_text(
            "import QtQuick 2.15\n"
            "Item {\n"
            "    function press() {\n"
            '        keyboard.pressKey("h", 0.25, -0.1)\n'
            '        keyboard.pressKeyLiteral("A", 0.3, 0.2)\n'
            '        keyboard.pressKey("x")\n'
            "    }\n"
            "}\n"
        )
        view = QQuickView()
        warnings: list[str] = []
        view.engine().warnings.connect(lambda ws: warnings.extend(w.toString() for w in ws))
        view.rootContext().setContextProperty("keyboard", bridge)
        view.setSource(QUrl.fromLocalFile(str(caller)))
        root = view.rootObject()
        assert root is not None, f"Caller.qml failed to load: {warnings}"
        QMetaObject.invokeMethod(root, "press")
        assert warnings == []
        assert bridge._current_word == "hAx"
        assert bridge._word_offsets == [("h", 0.25, -0.1), ("A", 0.3, 0.2), ("x", 0.0, 0.0)]
