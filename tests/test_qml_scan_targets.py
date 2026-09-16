"""Headless QML tests for the external switch-scanning contract.

An external scanner (Switchify, issue #106, and any other Windows assistive
technology) walks this keyboard's UI Automation tree, highlights each target
it finds, and activates the user's selection through Invoke. The contract it
reads is `docs/architecture/UIA_TARGETS.md`; these tests are what stops the
contract and the keyboard drifting apart.

What they can and cannot reach is worth stating, because it decides what is
asserted here and what had to be verified another way. Under the `offscreen`
platform plugin there is no Windows UIA provider at all, so nothing here can
prove that Qt maps `Accessible.id` onto an AutomationId or that Invoke
reaches `onPressAction`. Those are properties of Qt's Windows backend rather
than of this codebase, and they were verified against a live UIA client
before the contract was published (the findings are recorded in the design
doc). What these tests own is the half that *is* ours and the half that will
actually rot: that every on-screen key carries an identity, that the
identities are unique, that the names a scanner may speak are speakable, and
that the revision beacon moves when something a scanner cares about changes.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import (  # noqa: E402
        Q_ARG,
        QCoreApplication,
        QEvent,
        QMetaObject,
        QSettings,
        QUrl,
    )
    from PySide6.QtGui import QGuiApplication  # noqa: E402
    from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

    # Imported for the side effect: without QQuickItem somewhere in the
    # module, reading `root.contentItem` raises "Can't find converter for
    # 'QQuickItem*'", and walking the visual tree is the only way to reach a
    # Repeater's delegates.
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
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

# aosk.v1.<section>.<row>.<index>, plus the prediction form which carries a
# generation.  Kept as a literal rather than built from the QML property, so
# that a change to the scheme has to be made here too and is therefore a
# deliberate contract version bump rather than a silent one.
TARGET_ID_RE = re.compile(r"^aosk\.v1\.(grid|fn1|fn2|num|nav|pad)\.\d+\.\d+$")
PRED_ID_RE = re.compile(r"^aosk\.v1\.pred\.\d+\.g\d+$")


@pytest.fixture(scope="module")
def qapp():
    app = QGuiApplication.instance()
    if app is None:
        QCoreApplication.setOrganizationName(TEST_ORG)
        QCoreApplication.setApplicationName(TEST_APP)
        app = QGuiApplication([])
    assert QCoreApplication.organizationName() == TEST_ORG, (
        "another test already created a QGuiApplication under a different "
        "organisation, so these tests would write to the real user's settings"
    )
    return app


@pytest.fixture
def qml_root(qapp):
    warnings: list[str] = []
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
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    install_context_properties(engine, bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    try:
        yield root, warnings, bridge
    finally:
        del engine


def _key_items(root) -> list:
    """Every live KeyButton in the visual tree.

    Found by the presence of `targetId` rather than by class name, so a key
    drawn by some future component is still covered.
    """
    out: list = []

    def walk(item):
        if item is None:
            return
        for child in item.childItems():
            if child.property("targetId") is not None and child.property("keyText") is not None:
                out.append(child)
            walk(child)

    walk(root.property("contentItem"))
    return out


def _visible(item) -> bool:
    """Whether the item is actually on screen, parents included.

    A hidden panel's keys are still in the object tree; they are pruned from
    the accessibility tree, so the contract's "present means activatable" rule
    only concerns the visible ones.
    """
    node = item
    while node is not None:
        if not node.property("visible"):
            return False
        node = node.parentItem()
    return True


def _visible_keys(root) -> list:
    keys = [k for k in _key_items(root) if _visible(k)]
    assert keys, "walked the tree and found no visible keys at all"
    return keys


def _settle(root) -> None:
    """Let a property change propagate before measuring.

    Qt Quick re-evaluates in a polish step that runs before a frame, and the
    offscreen window renders none on its own, so a binding read immediately
    after a `setProperty` can still hand back the previous value. The
    revision-beacon tests measured the pre-change value without this and
    passed against a beacon that never moved.
    """
    QCoreApplication.processEvents()
    _ = root.property("scanRevision")
    QCoreApplication.processEvents()


class TestEveryKeyCarriesAnIdentity:
    """A key with no id is invisible to a scanner, which for its user means
    a key that does not exist. This is the guard that a new key, or a new
    surface drawing keys, cannot ship without one."""

    def test_every_visible_key_has_a_target_id(self, qml_root):
        root, _, _ = qml_root
        missing = [
            k.property("keyText") for k in _visible_keys(root) if not (k.property("targetId") or "")
        ]
        assert not missing, f"visible keys with no scan target id: {missing}"

    def test_every_id_matches_the_published_scheme(self, qml_root):
        root, _, _ = qml_root
        bad = [
            tid
            for k in _visible_keys(root)
            if not TARGET_ID_RE.match(tid := (k.property("targetId") or ""))
        ]
        assert not bad, f"ids that do not match the contract: {bad}"

    def test_ids_are_unique(self, qml_root):
        """Two keys sharing an id is the failure a scanner cannot detect: it
        reads as one target that moves, not as a bug."""
        root, _, _ = qml_root
        ids = [k.property("targetId") for k in _visible_keys(root)]
        dupes = {i for i in ids if ids.count(i) > 1}
        assert not dupes, f"duplicate scan target ids: {sorted(dupes)}"

    def test_a_key_without_an_id_is_ignored_rather_than_exposed(self, qml_root):
        """The inverse, and the reason `Accessible.ignored` is bound at all.
        Without it a surface that forgot to pass an id would put nameless,
        idless buttons in the tree instead of staying out of it."""
        root, _, _ = qml_root
        key = _visible_keys(root)[0]
        assert key.property("_scanIgnored") is False
        key.setProperty("targetId", "")
        _settle(root)
        assert key.property("_scanIgnored") is True


class TestTheNamesAreSpeakable:
    """A scanner may speak these, so an empty or glyph-only name is a target
    the user cannot be told about. The shipped qwerty layout has seven of
    them by construction, which is what this rule exists for."""

    def test_no_visible_key_is_nameless(self, qml_root):
        root, _, _ = qml_root
        nameless = [
            k.property("targetId")
            for k in _visible_keys(root)
            if not (k.property("_scanName") or "").strip()
        ]
        assert not nameless, f"keys a scanner could not announce: {nameless}"

    def test_a_glyph_cap_falls_back_to_the_keys_own_word(self, qml_root):
        """Backspace's cap is a glyph and the space bar has no cap at all.
        Both must still announce as words."""
        root, _, _ = qml_root
        names = {k.property("keyText"): k.property("_scanName") for k in _visible_keys(root)}
        assert names.get("backspace") == "backspace"
        assert names.get("space") == "space"

    def test_an_ordinary_cap_is_left_alone(self, qml_root):
        """The paired inverse: a rule that replaced every name with keyText
        would satisfy the two cases above and lose the actual labels."""
        root, _, _ = qml_root
        names = {k.property("keyText"): k.property("_scanName") for k in _visible_keys(root)}
        assert names.get("a") == "a"
        assert names.get("tab") == "Tab"

    def test_a_decorative_glyph_is_stripped_from_a_named_key(self, qml_root):
        """Shift's cap reads "⇧ Shift"; the glyph is decoration and a speech
        engine has nothing useful to do with it."""
        root, _, _ = qml_root
        shifts = [
            k.property("_scanName") for k in _visible_keys(root) if k.property("keyText") == "shift"
        ]
        assert shifts, "no shift key found"
        assert all(n == "Shift" for n in shifts), shifts


class TestOnlyRealTogglesReportAToggleState:
    """Claiming a toggle state a key does not have tells a scanner, and a
    screen reader, that the key is switched on."""

    def test_modifiers_are_toggle_targets(self, qml_root):
        root, _, _ = qml_root
        by_key = {k.property("keyText"): k for k in _visible_keys(root)}
        for name in ("shift", "ctrl", "alt", "capslock"):
            key = by_key.get(name)
            if key is not None:
                assert key.property("isToggleTarget") is True, name

    def test_a_letter_is_not_a_toggle_target(self, qml_root):
        root, _, _ = qml_root
        by_key = {k.property("keyText"): k for k in _visible_keys(root)}
        for name in ("a", "z", "space", "backspace"):
            key = by_key.get(name)
            if key is not None:
                assert key.property("isToggleTarget") is False, name

    def test_the_toggle_state_follows_the_modifier(self, qml_root):
        root, _, _ = qml_root
        shift = next(k for k in _visible_keys(root) if k.property("keyText") == "shift")
        assert shift.property("_scanChecked") is False
        root.setProperty("shiftOn", True)
        _settle(root)
        assert shift.property("_scanChecked") is True

    def test_a_locked_modifier_says_so(self, qml_root):
        """UI Automation has no state for "held until released", so it rides
        in the description. Paired with the unlocked case, because a binding
        stuck at "locked" would pass the positive half alone."""
        root, _, _ = qml_root
        shift = next(k for k in _visible_keys(root) if k.property("keyText") == "shift")
        assert shift.property("_scanDescription") == ""
        root.setProperty("shiftOn", True)
        root.setProperty("shiftLocked", True)
        _settle(root)
        assert shift.property("_scanDescription") == "locked"


class TestPredictionPillsGetAFreshIdentity:
    """The stale-selection guarantee: an outdated scan selection can never
    insert a word. It rests on two things, and each is tested on its own. The
    pills are rebuilt every round, so an element a scanner is holding from an
    earlier round is destroyed; and an Invoke carries its pill's generation,
    which is refused once that round is gone.

    The rebuild was first claimed as free Qt behaviour, and it was not:
    Repeater.setModel returns early when the new list compares equal to the
    old one, so a round of identical words kept the old pill objects, and the
    external test client (OwenMcGirr/alpha-osk-scan-lab, on PR #114) invoked a
    held one and got a keystroke. The identical round is the case to test."""

    def _pills(self, root) -> list:
        out: list = []

        def walk(item):
            if item is None:
                return
            for child in item.childItems():
                tid = child.property("scanTargetId")
                if tid and str(tid).startswith("aosk.v1.pred."):
                    out.append(child)
                walk(child)

        walk(root.property("contentItem"))
        return out

    def _pill_ids(self, root) -> list:
        return [str(p.property("scanTargetId")) for p in self._pills(root)]

    @staticmethod
    def _typed(bridge) -> list:
        """What reached the (mocked) synthesiser: the proof an insert happened."""
        return [
            c
            for c in bridge._synth.method_calls
            if c[0] in ("send_text", "send_key", "replace_text")
        ]

    @staticmethod
    def _flush_deletes() -> None:
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        QCoreApplication.processEvents()

    def test_pill_ids_match_the_published_scheme(self, qml_root):
        root, _, bridge = qml_root
        bridge.predictionsChanged.emit(["hello", "help", "held"])
        _settle(root)
        ids = self._pill_ids(root)
        assert ids, "no prediction pills were exposed"
        assert all(PRED_ID_RE.match(i) for i in ids), ids

    def test_a_new_round_gets_a_new_generation(self, qml_root):
        root, _, bridge = qml_root
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        first = self._pill_ids(root)
        bridge.predictionsChanged.emit(["world", "work"])
        _settle(root)
        second = self._pill_ids(root)
        assert first and second
        assert not set(first) & set(second), (
            f"an id survived a new prediction round: {first} vs {second}"
        )

    def test_even_an_identical_round_gets_a_new_generation(self, qml_root):
        """The case a "did the text change" check would miss. The bar being
        rebuilt is the event a stale selection has to be caught by, not the
        words being different."""
        root, _, bridge = qml_root
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        first = self._pill_ids(root)
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        second = self._pill_ids(root)
        assert first and second
        assert not set(first) & set(second), (
            f"an identical round reused its ids: {first} vs {second}"
        )

    def test_an_identical_round_destroys_the_pills_it_replaces(self, qml_root):
        """The regression the external client found. Before the fix this
        reused every pill, so a held element survived the round and stayed
        invokable, and only a client that compared ids first was safe."""
        root, _, bridge = qml_root
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        old = self._pills(root)
        assert old, "no prediction pills were exposed"
        destroyed: list = []
        for pill in old:
            pill.destroyed.connect(lambda *_: destroyed.append(1))

        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        self._flush_deletes()

        assert len(destroyed) == len(old), (
            f"{len(old) - len(destroyed)} of {len(old)} pills survived an "
            "identical round, so a scanner holding one could still invoke it"
        )
        assert self._pills(root), "the new round exposed no pills at all"

    def test_a_different_round_destroys_them_too(self, qml_root):
        """The inverse, so the test above cannot pass by destroying pills
        only when nothing changed."""
        root, _, bridge = qml_root
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        old = self._pills(root)
        destroyed: list = []
        for pill in old:
            pill.destroyed.connect(lambda *_: destroyed.append(1))

        bridge.predictionsChanged.emit(["world", "work"])
        _settle(root)
        self._flush_deletes()

        assert old and len(destroyed) == len(old)

    def test_an_invoke_from_a_round_that_is_gone_inserts_nothing(self, qml_root):
        """The second, independent half: the generation travels with the
        Invoke and a dead one is refused, so the guarantee does not rest on
        the rebuild alone."""
        root, _, bridge = qml_root
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        stale = int(root.property("predictionGeneration"))
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        assert int(root.property("predictionGeneration")) != stale
        bridge._synth.reset_mock()

        called = QMetaObject.invokeMethod(
            root, "invokeScanPrediction", Q_ARG("QVariant", "hello"), Q_ARG("QVariant", stale)
        )
        QCoreApplication.processEvents()

        # Without this the test passes against a keyboard with no such check
        # at all, since a call that never happens inserts nothing either.
        assert called, "invokeScanPrediction could not be called"

        assert self._typed(bridge) == [], f"a stale generation inserted text: {self._typed(bridge)}"

    def test_an_invoke_on_a_live_pill_still_inserts(self, qml_root):
        """The inverse, through the pill's own handler: a check that refused
        everything would satisfy the test above on its own."""
        root, _, bridge = qml_root
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        pills = self._pills(root)
        assert pills
        bridge._synth.reset_mock()

        assert QMetaObject.invokeMethod(pills[0], "scanInvoke")
        QCoreApplication.processEvents()

        assert self._typed(bridge), "invoking a live pill inserted nothing"

    def test_a_pills_id_is_fixed_for_its_lifetime(self, qml_root):
        """The id is built from the pill's own generation, not the live one,
        so it can never change under an element a scanner is holding."""
        root, _, bridge = qml_root
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        pill = self._pills(root)[0]
        before = str(pill.property("scanTargetId"))
        root.setProperty("predictionGeneration", int(root.property("predictionGeneration")) + 1)
        # Read before the deferred delete lands, which is the window a held
        # element could otherwise be read in.
        QCoreApplication.processEvents()
        assert str(pill.property("scanTargetId")) == before


class TestTheRevisionBeacon:
    """The one property a scanner polls. If it stops moving, a scanner sits
    on a stale snapshot and draws its overlay in the wrong places, which is
    silent rather than loud."""

    def test_it_is_exposed(self, qml_root):
        root, _, _ = qml_root
        assert root.property("scanRevision")

    def test_a_modifier_moves_it(self, qml_root):
        root, _, _ = qml_root
        before = root.property("scanRevision")
        root.setProperty("shiftOn", True)
        _settle(root)
        assert root.property("scanRevision") != before

    def test_a_panel_moves_it(self, qml_root):
        root, _, _ = qml_root
        before = root.property("scanRevision")
        root.setProperty("showNumpad", not root.property("showNumpad"))
        _settle(root)
        assert root.property("scanRevision") != before

    def test_a_resize_moves_it(self, qml_root):
        root, _, _ = qml_root
        before = root.property("scanRevision")
        root.setProperty("width", root.property("width") + 40)
        _settle(root)
        assert root.property("scanRevision") != before

    def test_a_new_prediction_round_moves_it(self, qml_root):
        root, _, bridge = qml_root
        before = root.property("scanRevision")
        bridge.predictionsChanged.emit(["hello"])
        _settle(root)
        assert root.property("scanRevision") != before

    def test_it_holds_still_when_nothing_changes(self, qml_root):
        """The inverse. A revision that changed on every read would satisfy
        every test above while making the beacon useless, since a scanner
        would re-snapshot the whole subtree on every poll."""
        root, _, _ = qml_root
        assert root.property("scanRevision") == root.property("scanRevision")


class TestHiddenSurfacesAreNotTargets:
    """ "Present means activatable" is the rule that lets a scanner skip
    checking availability per target. A key on a hidden panel must be absent,
    not present and disabled."""

    def test_a_hidden_panel_contributes_no_targets(self, qml_root):
        root, _, _ = qml_root
        root.setProperty("showNumpad", False)
        _settle(root)
        ids = [k.property("targetId") for k in _visible_keys(root)]
        assert not [i for i in ids if i.startswith("aosk.v1.pad.")]

    def test_showing_it_brings_them_back(self, qml_root):
        """Paired with the above: a walk that never found numpad keys at all
        would pass the negative half on its own."""
        root, _, _ = qml_root
        root.setProperty("showNumpad", True)
        _settle(root)
        ids = [k.property("targetId") for k in _visible_keys(root)]
        assert [i for i in ids if i.startswith("aosk.v1.pad.")]


def _minimize(root) -> None:
    QMetaObject.invokeMethod(root, "showMinimized")
    _settle(root)


def _restore(root) -> None:
    QMetaObject.invokeMethod(root, "showNormal")
    _settle(root)


class TestAMinimizedKeyboardOffersNothing:
    """A keyboard the user has put away is not a target. Windows keeps a
    minimized window's elements in the accessibility tree and reports them
    onscreen, so without this a scanner would go on highlighting and
    invoking keys that are not on screen, and "presence means activatable"
    would be false for as long as the keyboard stays minimized."""

    def _pills(self, root) -> list:
        return TestPredictionPillsGetAFreshIdentity._pills(None, root)  # type: ignore[arg-type]

    @pytest.fixture(autouse=True)
    def _can_minimize(self, qml_root):
        root, _, _ = qml_root
        _minimize(root)
        if root.property("scanWindowShown"):
            pytest.skip("this platform plugin does not report a minimized window")
        _restore(root)

    def test_keys_leave_the_tree(self, qml_root):
        root, _, _ = qml_root
        keys = _visible_keys(root)
        assert keys and not any(k.property("_scanIgnored") for k in keys)
        _minimize(root)
        assert all(k.property("_scanIgnored") for k in keys)

    def test_and_come_back_when_it_is_restored(self, qml_root):
        root, _, _ = qml_root
        keys = _visible_keys(root)
        _minimize(root)
        _restore(root)
        assert not any(k.property("_scanIgnored") for k in keys)

    def test_a_held_key_types_nothing_while_minimized(self, qml_root):
        root, _, bridge = qml_root
        key = next(
            k
            for k in _visible_keys(root)
            if str(k.property("targetId")).startswith("aosk.v1.grid.")
        )
        _minimize(root)
        bridge._synth.reset_mock()
        assert QMetaObject.invokeMethod(key, "activateFromAssistiveClient")
        QCoreApplication.processEvents()
        assert TestPredictionPillsGetAFreshIdentity._typed(bridge) == []

    def test_the_same_key_types_once_restored(self, qml_root):
        """The inverse, so a guard that refused everything cannot pass."""
        root, _, bridge = qml_root
        key = next(
            k
            for k in _visible_keys(root)
            if str(k.property("targetId")).startswith("aosk.v1.grid.")
        )
        _minimize(root)
        _restore(root)
        bridge._synth.reset_mock()
        assert QMetaObject.invokeMethod(key, "activateFromAssistiveClient")
        QCoreApplication.processEvents()
        assert TestPredictionPillsGetAFreshIdentity._typed(bridge)

    def test_a_pill_leaves_the_tree_and_inserts_nothing(self, qml_root):
        root, _, bridge = qml_root
        bridge.predictionsChanged.emit(["hello", "help"])
        _settle(root)
        pill = self._pills(root)[0]
        assert pill.property("scanIgnored") is False
        _minimize(root)
        assert pill.property("scanIgnored") is True
        bridge._synth.reset_mock()
        assert QMetaObject.invokeMethod(pill, "scanInvoke")
        QCoreApplication.processEvents()
        assert TestPredictionPillsGetAFreshIdentity._typed(bridge) == []

    def test_the_beacon_moves_when_it_is_minimized(self, qml_root):
        """A polling scanner learns of a minimize from the beacon alone."""
        root, _, _ = qml_root
        before = root.property("scanRevision")
        _minimize(root)
        minimized = root.property("scanRevision")
        _restore(root)
        assert minimized != before
        assert root.property("scanRevision") != minimized


class TestClosingTheKeyboardMinimizesIt:
    """A close that is not a quit minimizes the keyboard. The taskbar's
    Close and any UI Automation client's WindowPattern.Close both arrive
    as one, and Qt's default hid the keyboard while leaving the process
    running: off the taskbar, out of the tree, reachable only from the
    tray. Measured against a live client before this change."""

    @pytest.fixture(autouse=True)
    def _windows_only(self):
        if sys.platform != "win32":
            pytest.skip("the close-to-minimize rule is Windows only")

    def test_a_close_minimizes_rather_than_hiding(self, qml_root):
        root, _, _ = qml_root
        QMetaObject.invokeMethod(root, "close")
        _settle(root)
        assert root.property("visible") is True, "the keyboard was hidden by a close"
        assert root.property("scanWindowShown") is False, "the close did not minimize it"

    def test_a_quit_is_let_through(self, qml_root):
        """The inverse, and the half that matters most: Qt cancels a quit
        when a window refuses to close, so a keyboard that refused every
        close would also refuse its own close button."""
        root, _, _ = qml_root
        root.setProperty("quitting", True)
        QMetaObject.invokeMethod(root, "close")
        _settle(root)
        assert root.property("visible") is False


class TestARetainedTargetOnAHiddenPanelCannotType:
    """A hidden panel's keys are still alive, and a scanner may hold one.

    ``TestHiddenSurfacesAreNotTargets`` above covers the traversal half:
    hide the numpad and a fresh walk finds no ``pad.`` targets. That is not
    enough on its own, and the gap is the whole point of this class. Hiding
    a panel leaves every delegate inside it constructed, so a client that
    retained a target before the panel closed, or whose visibility check
    raced the close, still holds a live accessible interface. The guard used
    to check only the window, so invoking that retained interface typed a
    key that was not on screen.
    """

    def _pad_key(self, root):
        root.setProperty("showNumpad", True)
        _settle(root)
        return next(
            k for k in _visible_keys(root) if str(k.property("targetId")) == "aosk.v1.pad.0.0"
        )

    def test_a_key_on_a_hidden_panel_types_nothing(self, qml_root):
        root, _, bridge = qml_root
        key = self._pad_key(root)
        root.setProperty("showNumpad", False)
        _settle(root)
        bridge._synth.reset_mock()
        assert QMetaObject.invokeMethod(key, "activateFromAssistiveClient")
        QCoreApplication.processEvents()
        assert TestPredictionPillsGetAFreshIdentity._typed(bridge) == []

    def test_the_same_key_types_once_the_panel_is_shown(self, qml_root):
        """The inverse: a guard that refused everything would pass alone."""
        root, _, bridge = qml_root
        key = self._pad_key(root)
        bridge._synth.reset_mock()
        assert QMetaObject.invokeMethod(key, "activateFromAssistiveClient")
        QCoreApplication.processEvents()
        typed = TestPredictionPillsGetAFreshIdentity._typed(bridge)
        assert [(c[0], c[1]) for c in typed] == [("send_text", ("7",))]

    def test_a_key_the_panel_disables_types_nothing(self, qml_root):
        """The numpad's centre key is blank and disabled with NumLock off.

        The contract says presence means activatable, so it must leave the
        target set rather than sit there doing nothing when invoked.
        """
        root, _, bridge = qml_root
        root.setProperty("showNumpad", True)
        root.setProperty("numLockOn", False)
        _settle(root)
        centre = next(
            k for k in _key_items(root) if str(k.property("targetId")) == "aosk.v1.pad.1.1"
        )
        assert centre.property("_scanIgnored")
        bridge._synth.reset_mock()
        assert QMetaObject.invokeMethod(centre, "activateFromAssistiveClient")
        QCoreApplication.processEvents()
        assert TestPredictionPillsGetAFreshIdentity._typed(bridge) == []


class TestTheBeaconCarriesEveryTargetChangingState:
    """The beacon is the only thing a scanner polls, so a change it misses
    is a stale target map for as long as nothing else moves.

    Both cases here changed the target set while leaving the beacon
    byte-identical. They are asserted as a pair with the accessible state
    they describe, not as a beacon string on its own, because a beacon that
    changed for an unrelated reason would satisfy the string half alone.
    """

    def test_numlock_moves_it(self, qml_root):
        """NumLock rewrites ten names and actions with every id unchanged."""
        root, _, _ = qml_root
        root.setProperty("showNumpad", True)
        root.setProperty("numLockOn", True)
        _settle(root)
        before = root.property("scanRevision")
        digit = next(
            k for k in _key_items(root) if str(k.property("targetId")) == "aosk.v1.pad.0.0"
        )
        assert digit.property("_scanName") == "7"

        root.setProperty("numLockOn", False)
        _settle(root)

        assert digit.property("_scanName") == "Home"
        assert root.property("scanRevision") != before

    def test_dictation_emptying_the_pill_row_moves_it(self, qml_root):
        """`listening` removes every pill, and the generation does not move.

        The pills are removed rather than repopulated, so
        `predictionGeneration` is untouched and the old beacon said nothing
        had changed while every prediction target had gone.
        """
        root, _, _ = qml_root
        root.setProperty("predictions", ["alpha", "beta"])
        _settle(root)
        assert TestPredictionPillsGetAFreshIdentity._pills(None, root)
        before = root.property("scanRevision")

        root.setProperty("dictationState", "listening")
        _settle(root)

        assert not TestPredictionPillsGetAFreshIdentity._pills(None, root)
        assert root.property("scanRevision") != before

    def test_and_it_moves_back_when_dictation_stops(self, qml_root):
        root, _, _ = qml_root
        root.setProperty("predictions", ["alpha", "beta"])
        root.setProperty("dictationState", "listening")
        _settle(root)
        quiet = root.property("scanRevision")

        root.setProperty("dictationState", "idle")
        _settle(root)

        assert TestPredictionPillsGetAFreshIdentity._pills(None, root)
        assert root.property("scanRevision") != quiet


class TestEveryTargetHasSomethingToSay:
    """A scanner may speak a target's name, so an empty one is a target its
    user cannot identify.

    ``TestTheNamesAreSpeakable`` covers the default state. The numpad with
    NumLock off is the state it misses: four of its caps are bare glyph
    arrows and the panel set no `keyText`, so their accessible names came
    out empty.
    """

    @pytest.mark.parametrize("num_lock", [True, False])
    def test_the_numpad_names_every_target_in_both_states(self, qml_root, num_lock):
        root, _, _ = qml_root
        root.setProperty("showNumpad", True)
        root.setProperty("numLockOn", num_lock)
        _settle(root)
        pad = [
            k
            for k in _visible_keys(root)
            if str(k.property("targetId")).startswith("aosk.v1.pad.")
            and not k.property("_scanIgnored")
        ]
        assert pad, "walked the tree and found no numpad targets"
        empty = [k.property("targetId") for k in pad if not str(k.property("_scanName")).strip()]
        assert not empty, empty

    def test_the_arrow_keys_are_words_rather_than_glyphs(self, qml_root):
        """The specific case: a glyph-only cap has no spoken form at all."""
        root, _, _ = qml_root
        root.setProperty("showNumpad", True)
        root.setProperty("numLockOn", False)
        _settle(root)
        names = {
            str(k.property("targetId")): str(k.property("_scanName"))
            for k in _key_items(root)
            if str(k.property("targetId")).startswith("aosk.v1.pad.")
        }
        assert names["aosk.v1.pad.0.1"] == "Up"
        assert names["aosk.v1.pad.1.0"] == "Left"
        assert names["aosk.v1.pad.1.2"] == "Right"
        assert names["aosk.v1.pad.2.1"] == "Down"


class TestAnAssistiveActivationIsNotAPointerSample:
    """`pressDx` / `pressDy` persist from the last real click on a key.

    An Invoke that left them alone handed the bridge the coordinates of a
    click that did not happen. They reached two places: the live fuzzy
    position for that character, and `observe_press`, which teaches the
    per-slot pointer bias. For a user who scans sometimes and mouses at
    other times, that is the correction the prefix beam depends on being
    taught from presses nobody made. Zeroing them instead would train an
    artificial dead-centre click, which is why the source is carried
    explicitly rather than the numbers being overwritten.
    """

    def _grid_key(self, root):
        return next(
            k
            for k in _visible_keys(root)
            if str(k.property("targetId")).startswith("aosk.v1.grid.")
            and str(k.property("keyText")).strip() != ""
            and len(str(k.property("displayText"))) == 1
        )

    def test_an_invoke_contributes_no_pointer_sample(self, qml_root):
        root, _, bridge = qml_root
        key = self._grid_key(root)
        key.setProperty("pressDx", 0.37)
        key.setProperty("pressDy", -0.22)
        observed: list = []
        bridge._predictor.observe_press = lambda *a: observed.append(a)

        assert QMetaObject.invokeMethod(key, "activateFromAssistiveClient")
        QCoreApplication.processEvents()

        assert observed == []
        assert bridge._word_offsets
        assert bridge._word_offsets[-1][1] is None

    def test_a_real_press_still_carries_its_position(self, qml_root):
        """The inverse, and the half that stops this becoming 'never learn'.

        Driven through the bridge slot rather than a synthetic click,
        because the offsets a real press records reach the bridge the same
        way and a mouse event cannot be delivered reliably to a Repeater
        delegate under the offscreen plugin.
        """
        root, _, bridge = qml_root
        observed: list = []
        bridge._predictor.observe_press = lambda *a: observed.append(a)

        bridge.pressKey("a", 0.37, -0.22, True)

        assert observed == [("a", 0.37, -0.22)]
        assert bridge._word_offsets[-1] == ("a", (0.37, -0.22))

    def test_the_four_argument_slot_binds_from_qml(self, qml_root):
        """PySide overload resolution is the part Python tests cannot see.

        A four-argument call that failed to bind from QML would fall back
        to the three-argument overload with no warning anyone would notice,
        and every scanner press would teach a dead-centre click again.
        """
        root, _, bridge = qml_root
        observed: list = []
        bridge._predictor.observe_press = lambda *a: observed.append(a)
        ok = QMetaObject.invokeMethod(
            root,
            "scanTestPressKey",
            Q_ARG("QVariant", "a"),
            Q_ARG("QVariant", 0.4),
            Q_ARG("QVariant", -0.1),
            Q_ARG("QVariant", False),
        )
        QCoreApplication.processEvents()
        assert ok
        assert observed == []
        assert bridge._word_offsets[-1] == ("a", None)
