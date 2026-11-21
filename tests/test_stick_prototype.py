"""The analog-stick prototype window, over fakes, headless.

Nothing here can reach the real pointer or the pad bus: the confiner and the
bridge are fakes, ``main()`` is never called, and the QML loads on the
offscreen platform plugin.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Callable, List

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QPoint, Qt, QUrl  # noqa: E402
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt GUI libraries unavailable ({exc})", allow_module_level=True)

from src.gamepad.stick import HELD, IDLE, LATCHED  # noqa: E402
from src.gamepad.stick_session import StickSession  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402
from tests.test_gamepad_stick_session import FakeConfiner, FakeSink  # noqa: E402

PROTO_PATH = Path(__file__).resolve().parent.parent / "scripts" / "prototypes" / "analog_stick.py"


def _load_module() -> Any:
    spec = importlib.util.spec_from_file_location("analog_stick_prototype", PROTO_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["analog_stick_prototype"] = module
    spec.loader.exec_module(module)
    return module


proto_mod = _load_module()


class FakeBridge:
    def __init__(self) -> None:
        self.status = "off"
        self.latched: List[str] = []
        self.calls: List[Any] = []
        self.shutdowns = 0
        self.sink = FakeSink()

    def setActive(self, active: bool) -> bool:
        self.status = "on" if active else "off"
        return active

    def setStick(self, stick: str, x: float, y: float) -> bool:
        return self.sink.set_stick(stick, x, y) or True

    def centerStick(self, stick: str) -> None:
        self.sink.center_stick(stick)

    def setTrigger(self, trigger: str, value: float) -> bool:
        self.sink.set_trigger(trigger, value)
        return True

    def clearTrigger(self, trigger: str) -> None:
        self.sink.clear_trigger(trigger)

    def tap(self, control_id: str) -> bool:
        self.calls.append(("tap", control_id))
        return True

    def toggleLatch(self, control_id: str) -> bool:
        self.calls.append(("latch", control_id))
        return True

    def shutdown(self) -> None:
        self.shutdowns += 1


class StartableConfiner(FakeConfiner):
    def __init__(self) -> None:
        super().__init__()
        self.starts = 0
        self.start_result = True
        self.guard_alive = False
        self.reapplied = 0
        self.closed = 0

    def start(self) -> bool:
        self.starts += 1
        self.guard_alive = self.start_result
        return self.start_result

    def close(self) -> None:
        self.calls.append(("close",))
        self.closed += 1


@pytest.fixture(scope="module")
def qapp() -> Any:
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG
    return app


class _Rig:
    def __init__(self, qapp: Any) -> None:
        self.bridge = FakeBridge()
        self.confiner = StartableConfiner()
        self.session = StickSession(self.confiner, proto_mod.BridgeSink(self.bridge))
        self.proto = proto_mod.StickPrototype(self.bridge, self.confiner, self.session)
        self.warnings: List[str] = []
        self.engine = QQmlApplicationEngine()
        self.engine.warnings.connect(lambda errs: self.warnings.extend(e.toString() for e in errs))
        self.engine.rootContext().setContextProperty("stick", self.proto)
        self.engine.load(QUrl.fromLocalFile(str(proto_mod.QML_PATH)))
        assert self.engine.rootObjects(), "AnalogStick.qml failed to load:\n" + "\n".join(
            self.warnings
        )
        self.window = self.engine.rootObjects()[0]
        QTest.qWait(30)

    def until(self, predicate: Callable[[], bool], ms: int = 2000) -> bool:
        waited = 0
        while waited < ms:
            if predicate():
                return True
            QTest.qWait(10)
            waited += 10
        return predicate()


@pytest.fixture
def rig(qapp: Any):
    r = _Rig(qapp)
    try:
        yield r
    finally:
        r.proto.shutdown()
        del r.engine


def test_importing_the_module_built_nothing_real() -> None:
    assert hasattr(proto_mod, "main") and hasattr(proto_mod, "StickPrototype")
    assert proto_mod.__name__ == "analog_stick_prototype"


def test_the_qml_loads_without_warnings(rig: _Rig) -> None:
    assert rig.warnings == []
    assert rig.window.flags() & Qt.WindowType.WindowDoesNotAcceptFocus


class TestConnect:
    def test_connect_starts_the_confiner_and_the_pad(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        assert rig.confiner.starts == 1
        assert rig.bridge.status == "on" and rig.proto.status == "on"
        assert rig.proto.guard is True
        assert rig.proto.note == ""

    def test_connecting_twice_starts_the_confiner_once(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.proto.connectPad()
        assert rig.confiner.starts == 1

    def test_an_unstartable_confiner_forces_confine_off_and_says_so(self, rig: _Rig) -> None:
        rig.confiner.start_result = False
        rig.proto.connectPad()
        assert rig.proto.confine is False and rig.session.confine is False
        assert rig.proto.guard is False
        assert "unavailable" in rig.proto.note
        assert rig.proto.status == "on"

    def test_disconnect_turns_the_pad_off_and_lets_a_stick_go(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.proto.pickUp("ls", 0, 0, 100)
        rig.proto.disconnectPad()
        assert rig.bridge.status == "off"
        assert rig.session.held is None
        assert rig.proto.held == ""


class TestSticks:
    def test_pick_up_then_ticks_move_the_reported_position(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.confiner.pointer = (480, 380)
        assert rig.proto.pickUp("ls", 20.0, 20.0, 100.0) == "held"
        # Centre is the pointer plus the offsets, and the pointer was warped there.
        assert rig.confiner.pointer == (500, 400)
        assert rig.proto.held == "ls" and rig.proto.lsState == HELD
        rig.confiner.pointer = (550, 400)
        assert rig.until(lambda: rig.proto.lsX == pytest.approx(0.5))
        assert rig.proto.lsY == pytest.approx(0.0)
        assert rig.proto.clip == "ok"
        assert rig.proto.tickMs >= 0.0

    def test_drop_centres_it_and_stops_the_timer(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.proto.pickUp("ls", 0, 0, 100)
        rig.confiner.pointer = (rig.confiner.pointer[0] + 50, rig.confiner.pointer[1])
        assert rig.until(lambda: rig.proto.lsX > 0.4)
        rig.proto.drop()
        assert rig.proto.lsX == 0.0 and rig.proto.lsState == IDLE
        assert rig.proto.held == ""
        assert ("center", "ls") in rig.bridge.sink.calls
        assert not rig.proto._timer.isActive()

    def test_hold_latches_and_stops_following(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.proto.pickUp("rs", 0, 0, 100)
        rig.confiner.pointer = (rig.confiner.pointer[0] + 50, rig.confiner.pointer[1])
        assert rig.until(lambda: rig.proto.rsX > 0.4)
        rig.proto.hold()
        held_at = rig.proto.rsX
        assert rig.proto.rsState == LATCHED and rig.proto.held == ""
        rig.confiner.pointer = (rig.confiner.pointer[0] + 40, rig.confiner.pointer[1])
        QTest.qWait(60)
        assert rig.proto.rsX == held_at
        assert "release" in rig.confiner.names()

    def test_pick_up_without_a_pointer_is_refused(self, rig: _Rig) -> None:
        rig.confiner.pointer = None
        assert rig.proto.pickUp("ls", 0, 0, 100) == "refused"
        assert rig.proto.held == ""

    def test_a_lost_guard_lets_the_stick_go(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.proto.pickUp("ls", 0, 0, 100)
        rig.confiner.tick_results = ["guard-lost"]
        assert rig.until(lambda: rig.proto.held == "")
        assert rig.proto.clip == "guard-lost"
        assert rig.proto.lsState == IDLE

    def test_pad_buttons_go_to_the_bridge(self, rig: _Rig) -> None:
        rig.proto.tapButton("a")
        rig.proto.latchButton("lb")
        assert rig.bridge.calls == [("tap", "a"), ("latch", "lb")]


class TestToggles:
    def test_the_toggles_change_the_sessions_flags(self, rig: _Rig) -> None:
        rig.proto.confine = False
        rig.proto.recentre = False
        rig.proto.drive = True
        assert (rig.session.confine, rig.session.recentre, rig.session.drive) == (
            False,
            False,
            True,
        )
        assert (rig.proto.confine, rig.proto.recentre, rig.proto.drive) == (False, False, True)

    def test_drive_shows_the_triggers(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.proto.drive = True
        rig.proto.pickUp("ls", 0, 0, 100)
        rig.confiner.pointer = (rig.confiner.pointer[0], rig.confiner.pointer[1] - 50)
        assert rig.until(lambda: rig.proto.rt > 0.4)
        assert rig.proto.lt == 0.0

    def test_switching_drive_off_lets_a_held_stick_go(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.proto.drive = True
        rig.proto.pickUp("ls", 0, 0, 100)
        rig.proto.drive = False
        assert rig.proto.held == "" and rig.proto.lsState == IDLE
        assert ("clear", "rt") in rig.bridge.sink.calls


class TestQuit:
    def test_quit_releases_everything(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.proto.pickUp("ls", 0, 0, 100)
        quits: List[int] = []
        rig.proto.quitRequested.connect(lambda: quits.append(1))
        rig.proto.quit()
        assert quits == [1]
        assert rig.session.held is None and rig.session.state("ls") == IDLE
        assert "release" in rig.confiner.names()
        assert rig.confiner.closed == 1
        assert rig.bridge.shutdowns == 1
        assert not rig.proto._timer.isActive()

    def test_shutdown_still_frees_the_pointer_when_the_session_raises(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.proto.pickUp("ls", 0, 0, 100)

        def boom() -> None:
            raise RuntimeError("boom")

        rig.session.release_all = boom  # type: ignore[method-assign]
        rig.proto.shutdown()
        assert "release" in rig.confiner.names()
        assert rig.confiner.closed == 1


class TestClicksInTheWindow:
    def test_a_click_on_a_stick_picks_it_up_and_a_click_anywhere_drops_it(self, rig: _Rig) -> None:
        rig.proto.connectPad()
        rig.window.show()
        QTest.qWaitForWindowExposed(rig.window)
        QTest.mouseClick(rig.window, Qt.MouseButton.LeftButton, pos=QPoint(110, 134))
        assert rig.session.held == "ls"
        QTest.mouseClick(rig.window, Qt.MouseButton.RightButton, pos=QPoint(300, 300))
        assert rig.until(lambda: rig.session.state("ls") == LATCHED)
        assert rig.session.held is None
        # A click on a latched stick lets it go instead of picking it up again.
        QTest.mouseClick(rig.window, Qt.MouseButton.LeftButton, pos=QPoint(110, 134))
        assert rig.session.state("ls") == IDLE
        assert rig.warnings == []
