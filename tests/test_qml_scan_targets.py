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
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QCoreApplication, QSettings, QUrl  # noqa: E402
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
    """The stale-selection guarantee. Its load-bearing half is that Repeater
    destroys and rebuilds the delegates, which is Qt behaviour and was
    verified against a live UIA client; what is asserted here is the half
    this repo controls, that the id never denotes two different offers."""

    def _pill_ids(self, root) -> list:
        out: list = []

        def walk(item):
            if item is None:
                return
            for child in item.childItems():
                tid = child.property("scanTargetId")
                if tid and str(tid).startswith("aosk.v1.pred."):
                    out.append(str(tid))
                walk(child)

        walk(root.property("contentItem"))
        return out

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
