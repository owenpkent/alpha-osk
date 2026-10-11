# ruff: noqa: F811  (the shared fixtures imported below are re-bound as test parameters)
"""Headless QML tests for the `bar` section of the switch-scanning contract.

The suggestion bar's buttons (dictation mic, Write with AI, Symbols,
Snippets, clear-context) and their title-bar twins are published as
`aosk.v1.bar.0.<index>` UI Automation buttons, one fixed index per function.
Normative text: docs/architecture/UIA_TARGETS.md, section "The bar".  The
key and pill halves of the contract are in tests/test_qml_scan_targets.py,
whose fixtures this module reuses.

Nothing here touches the microphone, the network or the desktop: the
dictation controller is replaced by a mock, and the mocked key synthesiser
comes from the shared fixture.
"""

from __future__ import annotations

import os
import re
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QMetaObject, QObject  # noqa: E402
from PySide6.QtTest import QSignalSpy  # noqa: E402

from tests.test_qml_scan_targets import (  # noqa: E402,F401
    PRED_ID_RE,
    _settle,
    _visible,
    _visible_keys,
    qapp,
    qml_root,
)

BAR_ID_RE = re.compile(r"^aosk\.v1\.bar\.0\.\d+$")

# function -> (bar object, title-bar twin object, fixed index)
CONTROLS = {
    "mic": ("dictationMicButton", "dictationTitleBarButton", 0),
    "write": ("writeWithAiBarButton", "writeWithAiTitleBarButton", 1),
    "symbols": ("symbolsBarButton", "symbolsTitleBarButton", 2),
    "snippets": ("snippetsBarButton", "snippetsTitleBarButton", 3),
    "clear": ("clearContextButton", None, 4),
}


def _item(root, name: str):
    obj = root.findChild(QObject, name)
    assert obj is not None, f"no object named {name!r} in Main.qml"
    return obj


def _enable_everything(root, bridge, suggestions: bool = True) -> None:
    """Every optional control present: dictation on, both write keys stored."""
    cfg = bridge._dictation_config
    cfg.enabled = True
    cfg.api_key = "d" * 40
    cfg.anthropic_key = "a" * 40
    cfg.writing_enabled = True
    root.setProperty("dictationEnabled", True)
    root.setProperty("writingConfigured", True)
    root.setProperty("suggestionsEnabled", suggestions)
    _settle(root)


def _id_of(root, name: str) -> str | None:
    """The id a scanner would see for this control, or None if it is absent
    from the accessibility tree (invisible or ignored)."""
    item = _item(root, name)
    if not _visible(item) or item.property("_scanIgnored"):
        return None
    return str(item.property("targetId"))


def _live_bar_ids(root) -> dict[str, str]:
    out: dict[str, str] = {}
    for func, (bar, twin, _idx) in CONTROLS.items():
        for name in (bar, twin):
            if name is None:
                continue
            tid = _id_of(root, name)
            if tid is not None:
                assert func not in out, f"{func} is published twice"
                out[func] = tid
    return out


class TestEveryBarControlPublishesItsId:
    def test_all_five_are_present_and_well_formed_with_the_bar_showing(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        ids = _live_bar_ids(root)
        assert set(ids) == set(CONTROLS)
        for func, (_b, _t, idx) in CONTROLS.items():
            assert ids[func] == f"aosk.v1.bar.0.{idx}", func
            assert BAR_ID_RE.match(ids[func])

    def test_names_are_speakable_and_distinct(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        names = [str(_item(root, bar).property("_scanName")) for bar, _t, _i in CONTROLS.values()]
        assert all(n.strip() for n in names)
        assert len(set(names)) == len(names)

    def test_a_hidden_control_is_simply_absent(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        root.setProperty("writingConfigured", False)
        _settle(root)
        ids = _live_bar_ids(root)
        assert "write" not in ids
        assert set(ids) == set(CONTROLS) - {"write"}

    def test_dictation_off_removes_mic_and_write_only(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        root.setProperty("dictationEnabled", False)
        _settle(root)
        assert set(_live_bar_ids(root)) == {"symbols", "snippets", "clear"}


class TestIndicesAreStableWhenASiblingIsHidden:
    def test_hiding_any_one_control_moves_no_other_id(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        full = _live_bar_ids(root)
        # Hide each hideable control in turn and compare the survivors.
        for prop, func in (("writingConfigured", "write"), ("dictationEnabled", "mic")):
            original = root.property(prop)
            root.setProperty(prop, False)
            _settle(root)
            after = _live_bar_ids(root)
            for survivor, tid in after.items():
                assert tid == full[survivor], f"{survivor} shifted when {func} was hidden"
            root.setProperty(prop, original)
            _settle(root)
        assert _live_bar_ids(root) == full


class TestTheTwinCarriesTheBarCounterpartsId:
    def test_with_suggestions_off_the_twin_has_the_documented_id(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        before = _live_bar_ids(root)
        root.setProperty("suggestionsEnabled", False)
        _settle(root)
        after = _live_bar_ids(root)
        # Every function with a twin keeps its id; the ring has none.
        assert set(after) == set(CONTROLS) - {"clear"}
        for func in after:
            assert after[func] == before[func], func

    def test_the_bar_copy_is_gone_when_the_twin_shows(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge, suggestions=False)
        for func, (bar, twin, _i) in CONTROLS.items():
            assert _id_of(root, bar) is None, f"{func} bar copy still exposed"
            if twin is not None:
                assert _id_of(root, twin) is not None, func

    def test_never_both_at_once(self, qml_root):
        root, _, bridge = qml_root
        for suggestions in (True, False):
            _enable_everything(root, bridge, suggestions)
            for func, (bar, twin, _i) in CONTROLS.items():
                if twin is None:
                    continue
                both = _id_of(root, bar) is not None and _id_of(root, twin) is not None
                assert not both, f"{func}: bar copy and twin exposed together"


class TestNoTwoVisibleTargetsShareAnId:
    @pytest.mark.parametrize("suggestions", [True, False])
    def test_bar_ids_are_unique_across_the_whole_tree(self, qml_root, suggestions):
        root, _, bridge = qml_root
        _enable_everything(root, bridge, suggestions)
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        ids = [str(k.property("targetId")) for k in _visible_keys(root)]
        ids += list(_live_bar_ids(root).values())
        assert len(ids) == len(set(ids)), sorted(i for i in ids if ids.count(i) > 1)
        assert not any(PRED_ID_RE.match(i) and ".bar." in i for i in ids)


class TestInvoke:
    def _spy(self, item):
        return QSignalSpy(item, "2activated()")

    @pytest.mark.parametrize("func", list(CONTROLS))
    def test_invoke_raises_the_same_signal_a_click_does(self, qml_root, func):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        item = _item(root, CONTROLS[func][0])
        spy = self._spy(item)
        assert QMetaObject.invokeMethod(item, "activateFromAssistiveClient")
        QCoreApplication.processEvents()
        assert spy.count() == 1

    def test_invoke_flashes_the_button(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        item = _item(root, "clearContextButton")
        QMetaObject.invokeMethod(item, "activateFromAssistiveClient")
        assert item.property("_flash") is True

    @pytest.mark.parametrize("func", [f for f, c in CONTROLS.items() if c[1]])
    def test_a_twin_invokes_when_it_is_the_one_showing(self, qml_root, func):
        root, _, bridge = qml_root
        _enable_everything(root, bridge, suggestions=False)
        twin = _item(root, CONTROLS[func][1])
        spy = self._spy(twin)
        QMetaObject.invokeMethod(twin, "activateFromAssistiveClient")
        assert spy.count() == 1

    def test_a_hidden_control_refuses_a_held_invoke(self, qml_root):
        """Presence means activatable, and the check runs at activation too."""
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        item = _item(root, "writeWithAiBarButton")
        spy = self._spy(item)
        root.setProperty("writingConfigured", False)
        _settle(root)
        QMetaObject.invokeMethod(item, "activateFromAssistiveClient")
        assert spy.count() == 0

    def test_a_bar_copy_refuses_once_suggestions_are_off(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        item = _item(root, "snippetsBarButton")
        spy = self._spy(item)
        root.setProperty("suggestionsEnabled", False)
        _settle(root)
        QMetaObject.invokeMethod(item, "activateFromAssistiveClient")
        assert spy.count() == 0

    def test_a_minimized_keyboard_refuses(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        item = _item(root, "clearContextButton")
        spy = self._spy(item)
        QMetaObject.invokeMethod(root, "showMinimized")
        QCoreApplication.processEvents()
        try:
            QMetaObject.invokeMethod(item, "activateFromAssistiveClient")
            assert spy.count() == 0
            assert item.property("_scanIgnored") is True
        finally:
            QMetaObject.invokeMethod(root, "showNormal")
            QCoreApplication.processEvents()

    def test_clear_invoke_clears_the_context(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        bridge._current_word = "abc"
        item = _item(root, "clearContextButton")
        QMetaObject.invokeMethod(item, "activateFromAssistiveClient")
        QCoreApplication.processEvents()
        assert bridge._current_word == ""


class TestDictationInvokeReachesTheBridgeSlot:
    @pytest.fixture
    def controller(self, qml_root):
        _root, _, bridge = qml_root
        mock = MagicMock()
        bridge._dictation_controller = lambda: mock
        return mock

    def test_mic_invoke_toggles_dictation_exactly_as_a_click(self, qml_root, controller):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        mic = _item(root, "dictationMicButton")
        QMetaObject.invokeMethod(mic, "activated")  # a click's path
        QMetaObject.invokeMethod(mic, "activateFromAssistiveClient")
        QCoreApplication.processEvents()
        assert controller.toggle.call_count == 2
        assert all(c.args == () for c in controller.toggle.call_args_list)

    def test_write_invoke_toggles_the_write_run(self, qml_root, controller):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        write = _item(root, "writeWithAiBarButton")
        QMetaObject.invokeMethod(write, "activated")
        QMetaObject.invokeMethod(write, "activateFromAssistiveClient")
        QCoreApplication.processEvents()
        assert [c.args for c in controller.toggle.call_args_list] == [("write",), ("write",)]

    @pytest.mark.parametrize("state", ["connecting", "finishing", "listening"])
    def test_mic_stays_activatable_in_every_busy_state(self, qml_root, controller, state):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        root.setProperty("dictationState", state)
        _settle(root)
        mic = _item(root, "dictationMicButton")
        assert mic.property("_scanIgnored") is False
        QMetaObject.invokeMethod(mic, "activateFromAssistiveClient")
        assert controller.toggle.call_count == 1

    def test_polishing_is_still_activatable_and_the_bridge_decides(self, qml_root, controller):
        """The controller treats a tap while polishing as a no-op; the button
        stays present so the id does not flicker out mid-run."""
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        root.setProperty("dictationState", "polishing")
        _settle(root)
        mic = _item(root, "dictationMicButton")
        assert mic.property("_scanIgnored") is False


class TestToggleState:
    def test_only_mic_and_write_are_toggles(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        for func, (bar, _t, _i) in CONTROLS.items():
            expected = func in ("mic", "write")
            assert _item(root, bar).property("isToggleTarget") is expected, func

    def test_the_mic_reports_on_while_dictating_and_the_pen_does_not(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        root.setProperty("dictationState", "listening")
        root.setProperty("writeRunPending", False)
        _settle(root)
        assert _item(root, "dictationMicButton").property("_scanChecked") is True
        assert _item(root, "writeWithAiBarButton").property("_scanChecked") is False

    def test_the_pen_reports_on_for_its_own_run(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        root.setProperty("writeRunPending", True)
        root.setProperty("dictationState", "listening")
        _settle(root)
        assert _item(root, "writeWithAiBarButton").property("_scanChecked") is True
        assert _item(root, "dictationMicButton").property("_scanChecked") is False


class TestTheRevisionCarriesTheBar:
    def test_a_control_appearing_moves_the_beacon(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        root.setProperty("writingConfigured", False)
        _settle(root)
        without = root.property("scanRevision")
        root.setProperty("writingConfigured", True)
        _settle(root)
        assert root.property("scanRevision") != without

    def test_a_control_disappearing_moves_the_beacon(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        with_all = root.property("scanRevision")
        root.setProperty("dictationEnabled", False)
        _settle(root)
        assert root.property("scanRevision") != with_all

    def test_a_toggle_state_change_moves_the_beacon(self, qml_root):
        root, _, bridge = qml_root
        _enable_everything(root, bridge)
        idle = root.property("scanRevision")
        root.setProperty("dictationState", "listening")
        _settle(root)
        assert root.property("scanRevision") != idle
