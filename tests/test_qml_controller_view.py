"""The Controller view: a virtual game pad drawn where the letter grid is.

Loads the real ``Main.qml`` headlessly with a ``GamepadBridge`` built over a
fake worker, so nothing here can reach the pad bus (the conftest guards stay
on, and the bridge's ``bus_available`` is a lambda). What is pinned:

* the view takes exactly the grid's rectangle, so the window never changes;
* a pad control talks to ``gamepad`` only, and a press always ends;
* ABC leaves the pad plugged in with its latches, Off unplugs it;
* the scan contract: a ``game`` section present only while the view shows.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, List, Optional
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import (  # noqa: E402
        QCoreApplication,
        QMetaObject,
        QObject,
        QSettings,
        Qt,
        QUrl,
    )
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from pathlib import Path  # noqa: E402

from src.gamepad.state import BUTTON_MASKS, NEUTRAL, PadReport  # noqa: E402
from src.gamepad_bridge import GamepadBridge  # noqa: E402
from src.keyboard_bridge import KeyboardBridge  # noqa: E402
from tests.qml_context import install_context_properties  # noqa: E402
from tests.qt_settings_scope import TEST_APP, TEST_ORG  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

PAD_ID_RE = re.compile(r"^aosk\.v1\.game\.0\.\d+$")

#: Every control the view draws, in scan-index order (the documented table).
PAD_SCAN_ORDER = [
    "lt", "lb", "rb", "rt", "back", "guide", "start", "off", "abc",
    "ls_nw", "ls_n", "ls_ne", "ls_w", "l3", "ls_e", "ls_sw", "ls_s", "ls_se",
    "rs_nw", "rs_n", "rs_ne", "rs_w", "r3", "rs_e", "rs_sw", "rs_s", "rs_se",
    "dpad_up", "dpad_left", "dpad_right", "dpad_down",
    "y", "x", "b", "a", "esc", "enter",
]  # fmt: skip


class _FakeWorker:
    """Records submits and stops; ``start`` reports "on" the way the thread would."""

    instances: List["_FakeWorker"] = []

    def __init__(self, open_device: Callable[[], Any], on_status: Callable[[str, str], None]):
        self.on_status = on_status
        self.submitted: List[PadReport] = []
        self.stops = 0
        _FakeWorker.instances.append(self)

    def start(self) -> None:
        self.on_status("on", "")

    def submit(self, report: PadReport) -> None:
        self.submitted.append(report)

    def stop(self) -> None:
        self.stops += 1


def _settle(passes: int = 12) -> None:
    for _ in range(passes):
        QCoreApplication.processEvents()


def _relayout(root) -> None:
    """Force the polish pass, and wait out the pill bar's 150 ms `Behavior`."""
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


class _Loaded:
    def __init__(self, engine, root, warnings, bridge, pad):
        self.engine = engine
        self.root = root
        self.warnings = warnings
        self.bridge = bridge
        self.pad = pad

    @property
    def worker(self) -> Optional[_FakeWorker]:
        return _FakeWorker.instances[-1] if _FakeWorker.instances else None

    def last_report(self) -> PadReport:
        assert self.worker is not None and self.worker.submitted
        return self.worker.submitted[-1]


@pytest.fixture
def load(qapp):
    engines = []
    _FakeWorker.instances = []

    def _load(*, bus: bool = True, enabled: bool = True, **saved) -> _Loaded:
        warnings: List[str] = []
        QSettings(TEST_ORG, TEST_APP).clear()
        settings = QSettings(TEST_ORG, TEST_APP)
        settings.setValue("ui/savedAutoCheckUpdates", False)
        settings.setValue("ui/savedControllerEnabled", enabled)
        settings.setValue("ui/savedShowNavigation", saved.pop("savedShowNavigation", False))
        settings.setValue("ui/savedShowNumpad", saved.pop("savedShowNumpad", False))
        for key, value in saved.items():
            settings.setValue(f"ui/{key}", value)
        settings.sync()

        with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
            synth = MagicMock()
            synth.is_available.return_value = True
            synth.backend_name.return_value = "MockSynth"
            factory.return_value = synth
            bridge = KeyboardBridge()
        pad = GamepadBridge(bus_available=lambda: bus, worker_factory=_FakeWorker, parent=bridge)

        engine = QQmlApplicationEngine()
        engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
        install_context_properties(engine, bridge, gamepad=pad)
        engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
        root = engine.rootObjects()[0]
        _relayout(root)
        assert not warnings, "QML warnings:\n  " + "\n  ".join(warnings)
        engines.append(engine)
        return _Loaded(engine, root, warnings, bridge, pad)

    try:
        yield _load
    finally:
        for engine in engines:
            del engine


# ---- helpers over the visual tree ----


def _walk(item):
    for child in item.childItems():
        yield child
        yield from _walk(child)


def _all(root) -> list:
    return list(_walk(root.property("contentItem")))


def _by_name(root, name: str):
    for item in _all(root):
        if item.objectName() == name:
            return item
    return None


def _must(root, name: str):
    item = _by_name(root, name)
    assert item is not None, f"no item named {name!r}"
    return item


def _visible(item) -> bool:
    node = item
    while node is not None:
        if not node.property("visible"):
            return False
        node = node.parentItem()
    return True


def _pad_keys(root) -> dict:
    return {
        item.objectName()[len("padKey_") :]: item
        for item in _all(root)
        if item.objectName().startswith("padKey_")
    }


def _grid_keys(root) -> list:
    return [i for i in _all(root) if str(i.property("targetId") or "").startswith("aosk.v1.grid.")]


def _rect(item):
    top_left = item.mapToScene(item.boundingRect().topLeft())
    return top_left.x(), top_left.y(), item.width(), item.height()


def _edges(items) -> tuple:
    rects = [_rect(i) for i in items]
    return (
        min(r[0] for r in rects),
        min(r[1] for r in rects),
        max(r[0] + r[2] for r in rects),
        max(r[1] + r[3] for r in rects),
    )


def _click(root, item, button=Qt.LeftButton, hold: bool = False) -> None:
    point = item.mapToScene(item.boundingRect().center()).toPoint()
    QTest.mousePress(root, button, Qt.NoModifier, point)
    _settle()
    if not hold:
        QTest.mouseRelease(root, button, Qt.NoModifier, point)
        _settle()


def _release(root, item, button=Qt.LeftButton) -> None:
    point = item.mapToScene(item.boundingRect().center()).toPoint()
    QTest.mouseRelease(root, button, Qt.NoModifier, point)
    _settle()


def _invoke(item) -> None:
    assert QMetaObject.invokeMethod(item, "activateFromAssistiveClient")
    _settle()


def _bar_button(root, twin: bool = False):
    return _must(root, "controllerTitleBarButton" if twin else "controllerBarButton")


def _open(L: _Loaded) -> None:
    _invoke(_bar_button(L.root))
    _settle()
    _relayout(L.root)
    assert L.root.property("controllerViewActive") is True


def _live_bar_ids(root) -> dict:
    out = {}
    for name in [
        "dictationMicButton", "dictationTitleBarButton",
        "writeWithAiBarButton", "writeWithAiTitleBarButton",
        "symbolsBarButton", "symbolsTitleBarButton",
        "snippetsBarButton", "snippetsTitleBarButton",
        "dictationHistoryBarButton", "dictationHistoryTitleBarButton",
        "controllerBarButton", "controllerTitleBarButton",
    ]:  # fmt: skip
        item = _by_name(root, name)
        if item is not None and _visible(item) and not item.property("_scanIgnored"):
            out[name] = str(item.property("targetId"))
    return out


def _toast_open(root) -> bool:
    toast = root.findChild(QObject, "snippetProblemToast")
    assert toast is not None
    return bool(toast.property("opened"))


def _toast_text(root) -> str:
    toast = root.findChild(QObject, "snippetProblemToast")
    return str(toast.property("message"))


def _geometry(root) -> tuple:
    bar = _must(root, "predictionBar")
    return root.property("width"), root.property("height"), bar.height()


CONFIGS = [
    pytest.param({}, id="full"),
    pytest.param({"savedCompactView": True}, id="compact"),
    pytest.param({"savedShowNavigation": True, "savedShowNumpad": True}, id="full+panels"),
    pytest.param({"savedShowFunctionRow": True}, id="full+fkeys"),
]


class TestTheButton:
    def test_default_has_no_button_no_view_and_no_warnings(self, load):
        L = load(enabled=False)
        assert L.root.property("controllerEnabled") is False
        assert L.root.property("controllerViewActive") is False
        assert not _visible(_bar_button(L.root))
        assert _must(L.root, "controllerView").property("visible") is False
        assert "controllerBarButton" not in _live_bar_ids(L.root)
        assert not L.warnings

    def test_enabling_adds_exactly_one_bar_target_and_moves_no_other(self, load):
        L = load(enabled=False)
        before = _live_bar_ids(L.root)
        L.root.setProperty("controllerEnabled", True)
        _relayout(L.root)
        after = _live_bar_ids(L.root)
        assert after.pop("controllerBarButton") == "aosk.v1.bar.0.6"
        assert after == before
        assert not L.warnings

    def test_with_suggestions_off_the_twin_carries_the_same_id(self, load):
        L = load(savedSuggestionsEnabled=False)
        ids = _live_bar_ids(L.root)
        assert ids.get("controllerTitleBarButton") == "aosk.v1.bar.0.6"
        assert "controllerBarButton" not in ids

    def test_the_bar_reserves_room_for_it(self, load):
        L = load(enabled=False)
        bar = _must(L.root, "predictionBar")
        without = bar.property("rightButtonsReserve")
        L.root.setProperty("controllerEnabled", True)
        _relayout(L.root)
        assert bar.property("rightButtonsReserve") > without

    def test_the_setting_is_restored_from_disk(self, load):
        L = load(enabled=True)
        assert L.root.property("controllerEnabled") is True
        # ...but the view is never restored, and no pad is plugged at launch.
        assert L.root.property("controllerViewActive") is False
        assert L.worker is None
        assert L.pad.status == "off"


class TestOpeningTheView:
    @pytest.mark.parametrize("saved", CONFIGS)
    def test_it_replaces_the_letters_without_changing_the_window(self, load, saved):
        L = load(**saved)
        assert L.root.property("predictionsArePresent") is True, "pills were showing"
        letters_before = [k for k in _grid_keys(L.root) if _visible(k)]
        assert letters_before
        geometry_before = _geometry(L.root)
        section_before = L.root.property("sectionHeight")

        _open(L)

        assert L.pad.status == "on"
        assert _geometry(L.root) == geometry_before
        assert L.root.property("sectionHeight") == section_before
        assert not any(_visible(k) for k in _grid_keys(L.root))
        assert _must(L.root, "controllerView").property("visible") is True
        assert all(_visible(k) for k in _pad_keys(L.root).values())
        # The pills go, the bar's height stays.
        assert L.root.property("predictionsArePresent") is False
        assert not L.warnings

    def test_going_back_restores_the_same_geometry(self, load):
        L = load()
        geometry = _geometry(L.root)
        _open(L)
        _invoke(_bar_button(L.root))
        _relayout(L.root)
        assert L.root.property("controllerViewActive") is False
        assert _geometry(L.root) == geometry
        assert L.root.property("predictionsArePresent") is True
        assert any(_visible(k) for k in _grid_keys(L.root))
        assert L.pad.status == "on", "the pad stays plugged in"

    def test_without_the_driver_it_does_not_open(self, load):
        L = load(bus=False)
        geometry = _geometry(L.root)
        _invoke(_bar_button(L.root))
        _relayout(L.root)
        assert L.root.property("controllerViewActive") is False
        assert L.pad.status == "unavailable"
        assert L.worker is None
        assert _toast_open(L.root)
        assert "ViGEmBus" in _toast_text(L.root)
        assert _geometry(L.root) == geometry
        assert any(_visible(k) for k in _grid_keys(L.root))


class TestGeometry:
    @pytest.mark.parametrize("saved", CONFIGS)
    def test_the_view_is_flush_with_the_grid_it_replaced(self, load, saved):
        L = load(**saved)
        grid = _edges([k for k in _grid_keys(L.root) if _visible(k)])
        _open(L)
        pad = _edges(list(_pad_keys(L.root).values()))
        # The grid's own rows are centred and each ceils its width, so their
        # edges are ragged by a pixel (x 8 or 9, right 932 or 933 at the
        # default width): the view must sit inside that, not beat it.
        assert pad[0] == pytest.approx(grid[0], abs=1.0), "left"
        assert pad[2] == pytest.approx(grid[2], abs=1.0), "right"
        assert pad[1] == pytest.approx(grid[1], abs=0.5), "top"
        # A Row ceils its height above its keys, so the last letter row's
        # keys end up to a pixel short of the rows' summed height.
        assert pad[3] == pytest.approx(grid[3], abs=1.0), "bottom"

    @pytest.mark.parametrize("saved", CONFIGS)
    def test_no_two_controls_overlap_and_every_gap_is_the_grids(self, load, saved):
        L = load(**saved)
        _open(L)
        keys = _pad_keys(L.root)
        spacing = L.root.property("keySpacing")
        rects = {k: _rect(v) for k, v in keys.items()}
        names = list(rects)
        for i, a in enumerate(names):
            ax, ay, aw, ah = rects[a]
            for b in names[i + 1 :]:
                bx, by, bw, bh = rects[b]
                apart_x = bx >= ax + aw - 0.01 or ax >= bx + bw - 0.01
                apart_y = by >= ay + ah - 0.01 or ay >= by + bh - 0.01
                assert apart_x or apart_y, f"{a} overlaps {b}"
        # Neighbouring cells of a stick are one key gap apart, both ways.
        for left, right in (("ls_nw", "ls_n"), ("ls_n", "ls_ne"), ("rs_w", "r3")):
            lx, _ly, lw, _lh = rects[left]
            assert rects[right][0] - (lx + lw) == pytest.approx(spacing, abs=0.01), left
        row_gap = L.root.property("rowSpacing")
        for top, bottom in (("ls_nw", "ls_w"), ("ls_w", "ls_sw"), ("rs_ne", "rs_e")):
            _tx, ty, _tw, th = rects[top]
            assert rects[bottom][1] - (ty + th) == pytest.approx(row_gap, abs=0.01), top

    def test_every_key_has_both_hit_margins(self, load):
        L = load()
        _open(L)
        for name, key in _pad_keys(L.root).items():
            assert key.property("hitMarginH") > 0, name
            assert key.property("hitMarginV") > 0, name

    def test_each_stick_has_eight_directions_and_a_click(self, load):
        L = load()
        _open(L)
        keys = _pad_keys(L.root)
        for prefix, click in (("ls", "l3"), ("rs", "r3")):
            directions = [k for k in keys if re.fullmatch(prefix + r"_(n|ne|e|se|s|sw|w|nw)", k)]
            assert len(directions) == 8
            assert click in keys
        assert len(keys) == len(PAD_SCAN_ORDER) == 37

    def test_no_label_is_a_glyph_outside_ascii(self, load):
        L = load()
        _open(L)
        for name, key in _pad_keys(L.root).items():
            text = str(key.property("displayText"))
            assert text.isascii(), f"{name} shows {text!r}"


class TestPressingControls:
    def test_a_left_click_presses_then_releases_after_the_minimum_hold(self, load):
        L = load()
        _open(L)
        a = _pad_keys(L.root)["a"]
        _click(L.root, a, hold=True)
        assert L.last_report() == PadReport(buttons=BUTTON_MASKS["a"])
        _release(L.root, a)
        # Floor, not a race: the bridge's minimum hold has not passed yet.
        assert L.last_report() == PadReport(buttons=BUTTON_MASKS["a"])
        QTest.qWait(int(L.pad.MIN_PRESS_MS) + 150)
        assert L.last_report() == NEUTRAL

    def test_a_press_that_ends_by_leaving_the_key_still_releases(self, load):
        L = load()
        _open(L)
        b = _pad_keys(L.root)["b"]
        _click(L.root, b, hold=True)
        assert L.last_report().buttons == BUTTON_MASKS["b"]
        # Released somewhere else entirely: the grab still ends the press.
        QTest.mouseRelease(L.root, Qt.LeftButton, Qt.NoModifier, QPoint_far(L.root))
        _settle()
        QTest.qWait(int(L.pad.MIN_PRESS_MS) + 150)
        assert L.last_report() == NEUTRAL

    def test_hiding_the_view_mid_press_lets_go(self, load):
        L = load()
        _open(L)
        _click(L.root, _pad_keys(L.root)["x"], hold=True)
        assert L.last_report().buttons == BUTTON_MASKS["x"]
        L.root.setProperty("controllerViewActive", False)
        _settle()
        QTest.qWait(int(L.pad.MIN_PRESS_MS) + 150)
        assert L.last_report() == NEUTRAL

    def test_a_right_click_latches_and_a_left_click_unlatches(self, load):
        L = load()
        _open(L)
        key = _pad_keys(L.root)["y"]
        _click(L.root, key, Qt.RightButton)
        assert list(L.pad.latched) == ["y"]
        assert key.property("isLocked") is True
        assert L.last_report().buttons == BUTTON_MASKS["y"]
        QTest.qWait(200)  # clear the key's own double-click debounce
        _click(L.root, key)
        assert list(L.pad.latched) == []
        assert key.property("isLocked") is False
        QTest.qWait(int(L.pad.MIN_PRESS_MS) + 150)
        assert L.last_report() == NEUTRAL

    def test_one_stick_holds_one_direction_and_the_other_keeps_its_own(self, load):
        L = load()
        _open(L)
        keys = _pad_keys(L.root)
        _click(L.root, keys["ls_n"], Qt.RightButton)
        _click(L.root, keys["ls_e"], Qt.RightButton)
        assert list(L.pad.latched) == ["ls_e"]
        assert keys["ls_n"].property("isLocked") is False
        assert keys["ls_e"].property("isLocked") is True
        _click(L.root, keys["rs_s"], Qt.RightButton)
        assert sorted(L.pad.latched) == ["ls_e", "rs_s"]

    def test_the_pad_never_touches_the_keyboard_bridge(self, load):
        L = load()
        pressed: List[Any] = []
        L.bridge.pressKey = lambda *a, **k: pressed.append(a)  # type: ignore[method-assign]
        L.bridge.pressSpecialKey = lambda *a, **k: pressed.append(a)  # type: ignore[method-assign]
        _open(L)
        keys = _pad_keys(L.root)
        for name in ("a", "ls_n", "dpad_left", "lt"):
            _click(L.root, keys[name])
            QTest.qWait(200)
        assert pressed == []

    def test_esc_and_enter_are_ordinary_keys(self, load):
        L = load()
        sent: List[str] = []
        _open(L)
        # The Python slot is what the grid's own Esc and Enter call.
        L.bridge.pressSpecialKey = lambda action, *a: sent.append(action)  # type: ignore[method-assign]
        keys = _pad_keys(L.root)
        before = len(L.worker.submitted)
        _click(L.root, keys["esc"])
        QTest.qWait(200)
        _click(L.root, keys["enter"])
        assert sent == ["escape", "return"]
        assert len(L.worker.submitted) == before, "no pad report for an ordinary key"


def QPoint_far(root):
    from PySide6.QtCore import QPoint

    return QPoint(root.property("width") - 2, 2)


class TestAssistiveActivation:
    def test_an_invoke_on_a_pad_control_is_one_press_and_one_release(self, load):
        L = load()
        _open(L)
        _invoke(_pad_keys(L.root)["a"])
        assert L.last_report() == PadReport(buttons=BUTTON_MASKS["a"])
        QTest.qWait(int(L.pad.MIN_PRESS_MS) + 150)
        assert L.last_report() == NEUTRAL

    def test_an_invoke_after_a_mouse_click_is_still_a_tap(self, load):
        L = load()
        _open(L)
        key = _pad_keys(L.root)["b"]
        _click(L.root, key)
        QTest.qWait(int(L.pad.MIN_PRESS_MS) + 250)
        _invoke(key)
        QTest.qWait(int(L.pad.MIN_PRESS_MS) + 150)
        assert L.last_report() == NEUTRAL

    def test_a_scanner_cannot_reach_a_control_while_the_letters_show(self, load):
        L = load()
        a = _pad_keys(L.root)["a"]
        assert not _visible(a)
        assert a.property("_scanIgnored") is True
        before = len(L.worker.submitted) if L.worker else 0
        _invoke(a)
        assert (len(L.worker.submitted) if L.worker else 0) == before


class TestABCAndOff:
    def test_abc_returns_to_the_letters_and_the_pad_stays_plugged_in(self, load):
        L = load()
        _open(L)
        keys = _pad_keys(L.root)
        _click(L.root, keys["ls_n"], Qt.RightButton)
        _click(L.root, keys["abc"])
        _relayout(L.root)
        assert L.root.property("controllerViewActive") is False
        assert L.pad.status == "on"
        assert list(L.pad.latched) == ["ls_n"]
        assert L.worker.stops == 0
        assert any(_visible(k) for k in _grid_keys(L.root))
        assert _must(L.root, "controllerConnectedDot").property("visible") is True

    def test_typing_a_letter_does_not_release_a_latch(self, load):
        L = load()
        _open(L)
        _click(L.root, _pad_keys(L.root)["ls_n"], Qt.RightButton)
        _click(L.root, _pad_keys(L.root)["abc"])
        _relayout(L.root)
        L.bridge.pressKey("a")
        L.bridge.pressSpecialKey("space")
        _settle()
        assert list(L.pad.latched) == ["ls_n"]
        assert L.last_report().left_y == 1.0

    def test_the_bar_button_returns_to_the_view_without_replugging(self, load):
        L = load()
        _open(L)
        _click(L.root, _pad_keys(L.root)["abc"])
        _relayout(L.root)
        _invoke(_bar_button(L.root))
        _relayout(L.root)
        assert L.root.property("controllerViewActive") is True
        assert len(_FakeWorker.instances) == 1
        assert _must(L.root, "controllerConnectedDot").property("visible") is False

    def test_off_unplugs_and_returns_to_the_letters(self, load):
        L = load()
        _open(L)
        _click(L.root, _pad_keys(L.root)["a"], Qt.RightButton)
        _click(L.root, _pad_keys(L.root)["off"])
        _relayout(L.root)
        assert L.pad.status == "off"
        assert list(L.pad.latched) == []
        assert L.root.property("controllerViewActive") is False
        assert L.worker.stops >= 1
        assert any(_visible(k) for k in _grid_keys(L.root))


class TestEveryWayOut:
    def test_a_worker_error_closes_the_view_and_says_so(self, load):
        L = load()
        _open(L)
        L.worker.on_status("error", "OSError")
        _settle()
        _relayout(L.root)
        assert L.pad.status == "error"
        assert L.root.property("controllerViewActive") is False
        assert _toast_open(L.root)
        assert _toast_text(L.root)
        assert any(_visible(k) for k in _grid_keys(L.root))

    def test_turning_the_setting_off_unplugs_the_pad(self, load):
        L = load()
        _open(L)
        L.root.setProperty("controllerEnabled", False)
        _relayout(L.root)
        assert L.pad.status == "off"
        assert L.root.property("controllerViewActive") is False
        assert not _visible(_bar_button(L.root))
        assert L.worker.stops >= 1

    def test_turning_the_setting_off_also_unplugs_a_pad_left_connected(self, load):
        L = load()
        _open(L)
        _click(L.root, _pad_keys(L.root)["abc"])
        L.root.setProperty("controllerEnabled", False)
        _settle()
        assert L.pad.status == "off"

    def test_minimizing_the_keyboard_unplugs_the_pad(self, load):
        L = load()
        _open(L)
        QMetaObject.invokeMethod(L.root, "showMinimized")
        _settle()
        QTest.qWait(100)
        _settle()
        assert L.pad.status == "off"
        assert L.root.property("controllerViewActive") is False


class TestScanTargets:
    def test_every_control_has_a_unique_id_in_the_game_section(self, load):
        L = load()
        _open(L)
        keys = _pad_keys(L.root)
        ids = {name: str(k.property("targetId")) for name, k in keys.items()}
        assert all(PAD_ID_RE.match(i) for i in ids.values()), ids
        assert len(set(ids.values())) == len(ids)
        for index, name in enumerate(PAD_SCAN_ORDER):
            assert ids[name] == f"aosk.v1.game.0.{index}", name
        assert all(not k.property("_scanIgnored") for k in keys.values())

    def test_the_names_are_speakable_and_distinct(self, load):
        L = load()
        _open(L)
        names = [str(k.property("_scanName")) for k in _pad_keys(L.root).values()]
        assert all(n.strip() and n.isascii() for n in names)
        assert len(set(names)) == len(names)

    def test_a_latch_is_reported_as_a_toggle_state(self, load):
        L = load()
        _open(L)
        key = _pad_keys(L.root)["a"]
        assert key.property("isToggleTarget") is True
        assert key.property("_scanChecked") is False
        _click(L.root, key, Qt.RightButton)
        assert key.property("_scanChecked") is True

    def test_no_pad_target_exists_while_the_letters_show(self, load):
        L = load()
        for key in _pad_keys(L.root).values():
            assert key.property("_scanIgnored") is True
        _open(L)
        _click(L.root, _pad_keys(L.root)["abc"])
        _relayout(L.root)
        for key in _pad_keys(L.root).values():
            assert key.property("_scanIgnored") is True

    def test_no_letter_is_offered_while_the_view_shows(self, load):
        L = load()
        _open(L)
        for key in _grid_keys(L.root):
            assert key.property("_scanIgnored") is True

    def test_the_revision_beacon_moves_with_the_view_and_with_a_latch(self, load):
        L = load()
        closed = L.root.property("scanRevision")
        _open(L)
        opened = L.root.property("scanRevision")
        assert opened != closed
        _click(L.root, _pad_keys(L.root)["a"], Qt.RightButton)
        latched = L.root.property("scanRevision")
        assert latched != opened
        _click(L.root, _pad_keys(L.root)["abc"])
        _relayout(L.root)
        assert L.root.property("scanRevision") != latched
