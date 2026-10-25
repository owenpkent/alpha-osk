"""Subprocess probe for the two-screen window restore test (not collected by pytest).

The offscreen platform picks its screens before ``QGuiApplication`` exists,
and the test session shares one app, so the case runs in its own process.

Usage: qml_two_screen_probe.py <settings_dir> <width> <x> <y> <compact 0|1>
(width 0: a fresh install, nothing saved)
Prints one JSON object on stdout.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QCoreApplication, QSettings, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression  # noqa: E402
from PySide6.QtQuick import QQuickWindow  # noqa: E402,F401
from PySide6.QtTest import QTest  # noqa: E402

from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402


def main() -> int:
    settings_dir, width, x, y, compact = sys.argv[1:6]
    QCoreApplication.setOrganizationName(TEST_ORG)
    QCoreApplication.setApplicationName(TEST_APP)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, settings_dir)
    app = QGuiApplication([])

    # Not `QSettings(org, app)`: that constructor ignores the default format and
    # would write the registry. The QML `Settings {}` uses the default one.
    settings = QSettings(QSettings.Format.IniFormat, QSettings.Scope.UserScope, TEST_ORG, TEST_APP)
    settings.clear()
    saved: dict[str, object] = {"savedAutoCheckUpdates": False}
    # A width of 0 is a fresh install: no saved geometry, default panels.
    if int(width) > 0:
        saved.update(
            savedShowNavigation=False,
            savedShowNumpad=False,
            savedCompactView=compact == "1",
            savedWindowWidth=int(width),
            savedWindowX=int(x),
            savedWindowY=int(y),
        )
    for key, value in saved.items():
        settings.setValue(f"ui/{key}", value)
    settings.sync()

    warnings: list[str] = []
    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(REPO_ROOT / "qml" / "Main.qml")))
    if not engine.rootObjects():
        print(json.dumps({"error": "load failed", "warnings": warnings}))
        return 1
    root = engine.rootObjects()[0]
    for _ in range(12):
        QCoreApplication.processEvents()
    QTest.qWait(250)
    root.grabWindow()
    for _ in range(12):
        QCoreApplication.processEvents()

    expr = QQmlExpression(engine.rootContext(), root, "maxWidthForScreen()")
    value, _ = expr.evaluate()
    out = {
        "screens": len(app.screens()),
        "width": root.property("width"),
        "height": root.property("height"),
        "x": root.property("x"),
        "y": root.property("y"),
        "maxWidth": value.toVariant() if hasattr(value, "toVariant") else value,
        "screen": root.screen().name(),
        "warnings": warnings,
    }
    print(json.dumps(out))
    del engine
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
