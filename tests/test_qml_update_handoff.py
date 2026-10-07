"""The relauncher's handoff reaches a freshly loaded keyboard.

``_update_relauncher._write_handoff`` writes ``update_handoff.json`` and
``Main.qml``'s ``Component.onCompleted`` reads it through
``keyboard.consumeUpdateHandoff()``. The two halves live in different
modules, so this loads the real ``Main.qml`` after the real writer ran and
checks the toast, rather than trusting that the formats still agree.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QObject, QSettings, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt GUI libraries unavailable ({exc})", allow_module_level=True)

import src.platform as platform_module  # noqa: E402
from src import _update_relauncher as relauncher  # noqa: E402
from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

QML_MAIN = Path(__file__).resolve().parent.parent / "qml" / "Main.qml"


def _sandboxed_config_dir() -> Path:
    """The conftest-patched config dir, refusing the developer's real one.

    The writer and the reader both resolve ``get_config_dir`` at call time,
    so a module-level import of it here would bypass the patch and drop a
    live breadcrumb into the real profile.
    """
    config = platform_module.get_config_dir()
    appdata = os.environ.get("APPDATA")
    if appdata:
        assert not config.resolve().is_relative_to(Path(appdata).resolve()), config
    assert not config.resolve().is_relative_to(Path.home() / ".config"), config
    return config


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


def _load_main():
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
    engine = QQmlApplicationEngine()
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    assert engine.rootObjects(), "qml/Main.qml failed to load"
    for _ in range(6):
        QCoreApplication.processEvents()
    return engine, engine.rootObjects()[0]


def _toast(root):
    for obj in root.findChildren(QObject):
        if obj.property("newVersion") is not None:
            return obj
    raise AssertionError("update toast not found in Main.qml")


def test_a_handoff_written_by_the_relauncher_shows_the_toast(qapp):
    config = _sandboxed_config_dir()
    path = config / "update_handoff.json"
    relauncher._write_handoff(config, "9.8.7", "9.8.6")
    assert path.is_file()
    engine, root = _load_main()
    try:
        toast = _toast(root)
        assert toast.property("newVersion") == "9.8.7"
        assert toast.property("previousVersion") == "9.8.6"
        assert toast.property("opened") or toast.property("visible")
        assert not path.exists(), "the breadcrumb is single-use"
    finally:
        del engine


def test_no_handoff_means_no_toast(qapp):
    config = _sandboxed_config_dir()
    assert not (config / "update_handoff.json").exists()
    engine, root = _load_main()
    try:
        toast = _toast(root)
        assert toast.property("newVersion") == ""
        assert not toast.property("opened")
    finally:
        del engine


def test_a_stale_handoff_is_not_announced(qapp, monkeypatch):
    config = _sandboxed_config_dir()
    real_time = time.time
    with monkeypatch.context() as m:
        m.setattr(relauncher.time, "time", lambda: real_time() - 3600)
        relauncher._write_handoff(config, "9.8.7", "9.8.6")
    engine, root = _load_main()
    try:
        assert _toast(root).property("newVersion") == ""
    finally:
        del engine
