"""Headless QML tests for tapping keys while the swipe overlay is active.

The overlay covers the whole main keyboard block with ``preventStealing:
true``, so while swipe typing is on it takes *every* press before a
KeyButton can see one. Everything about how a tap reaches the OS in that
state lives in QML, which is why this has to load the real ``Main.qml``
rather than exercise the bridge directly.

The regression these guard is issue #15: the overlay resolved its tap
fall-through through ``charKeyRegistry``, a list that by construction holds
only single-character keys, so Backspace, Delete, Tab, Enter, the arrows,
the modifiers, the ?123 layer key and the Number Row's Esc were all silently
swallowed. Turning on swipe typing removed the single key an imprecise
typist depends on most, with nothing on screen to explain it.

Two traps apply to writing tests here, both of which have already produced a
test in this repo that could not fail:

1. ``root.findChildren(QObject, name)`` does not find a Repeater's
   delegates. They are re-parented as *visual* children, so it returns an
   empty list and every assertion passes over nothing. Walk ``childItems()``
   and assert the result is non-empty.
2. Assert the keystroke reached the *synthesizer*. A test that only checks
   "no exception was raised" passes just as happily against the broken
   behaviour, because a dead tap is silent.
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
    from PySide6.QtQuick import QQuickItem  # noqa: E402,F401
    from PySide6.QtTest import QTest  # noqa: E402
except ImportError as exc:  # pragma: no cover - environment-dependent
    pytest.skip(
        f"Qt GUI libraries unavailable ({exc}); install libegl1/libgl1 to run "
        "the headless QML tests",
        allow_module_level=True,
    )

from src.keyboard_bridge import KeyboardBridge  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_MAIN = REPO_ROOT / "qml" / "Main.qml"

TEST_ORG = "alpha-osk-tests"
TEST_APP = "Alpha-OSK-Tests"

IGNORED_WARNING_FRAGMENTS = ("does not support customization",)

# The X11 keysym pressSpecialKey maps each action onto before handing it to
# the synthesizer. Asserting on this rather than on a bridge method proves
# the keystroke travelled the whole way to the platform layer.
KEYSYMS = {
    "backspace": "BackSpace",
    "delete": "Delete",
    "tab": "Tab",
    "return": "Return",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "escape": "Escape",
}


def _real_warnings(warnings: list[str]) -> list[str]:
    return [w for w in warnings if not any(f in w for f in IGNORED_WARNING_FRAGMENTS)]


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
def swipe_root(qapp):
    """Main.qml with swipe typing ON and a mocked key synthesizer."""
    warnings: list[str] = []
    QSettings(TEST_ORG, TEST_APP).clear()

    # Disarm the startup update check.  `savedAutoCheckUpdates` defaults to
    # true, so three seconds after load Main.qml fires a real HTTPS request
    # to the GitHub releases API from a daemon thread — in every headless QML
    # test that lives that long.  Besides making the suite depend on the
    # network, that thread emits its result back into a bridge the fixture
    # has already torn down, which surfaces as `RuntimeError: Signal source
    # has been deleted` or, in a longer run, a hard access violation that
    # kills the whole pytest process.  Writing the setting before the load
    # means `Component.onCompleted` never starts the timer at all.
    settings = QSettings(TEST_ORG, TEST_APP)
    settings.setValue("ui/savedAutoCheckUpdates", False)
    settings.sync()

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()

    # Pin game auto-compat OFF rather than inheriting whatever the runner's
    # geometry implies.
    #
    # `_window_is_game` treats a window that covers its whole monitor and has
    # no title bar as a game, and routes its keystrokes through send_key with
    # a 50 ms hold instead of send_text. An OSK shown under the offscreen
    # platform plugin is frameless and fills the virtual screen, so these
    # tests can classify *themselves* as a game, on some machines and not
    # others. That is not a hypothetical: it silently moved every typed
    # character onto the other channel and made an assertion here pass for
    # the wrong reason.
    #
    # The assertions below are channel-agnostic on purpose (see `_typed`), so
    # this is belt and braces. Pinning it means these tests exercise one
    # deliberate path rather than a coin flip on window geometry.
    bridge._foreground_timer.stop()
    bridge._game_auto_active = False

    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda errs: warnings.extend(e.toString() for e in errs))
    engine.rootContext().setContextProperty("keyboard", bridge)
    engine.load(QUrl.fromLocalFile(str(QML_MAIN)))

    assert engine.rootObjects(), "qml/Main.qml failed to load:\n  " + "\n  ".join(warnings)
    root = engine.rootObjects()[0]
    root.setProperty("swipeEnabled", True)
    # The overlay only intercepts what it covers, and it is positioned from
    # the laid-out geometry, so the window has to be realised before any of
    # this means anything.
    root.show()
    QCoreApplication.processEvents()
    assert root.property("swipeEnabled") is True

    try:
        yield root, warnings, synth
    finally:
        del engine


def _key_items(root) -> list:
    """Every KeyButton, via the VISUAL tree.

    `findChildren` returns [] here: a Repeater's delegates are re-parented as
    visual children and their QObject parent is the delegate model.
    """
    out: list = []

    def walk(item) -> None:
        for child in item.childItems():
            if child.property("kd") is not None:
                out.append(child)
            walk(child)

    walk(root.property("contentItem"))
    return out


def _find_key(root, **match) -> object:
    """The single visible KeyButton whose `kd` matches every given field."""
    hits = [
        item
        for item in _key_items(root)
        if item.isVisible()
        and all((item.property("kd") or {}).get(k) == v for k, v in match.items())
    ]
    assert hits, f"no visible key matching {match} (searched {len(_key_items(root))})"
    return hits[0]


def _tap(root, item) -> None:
    """Press and release over the centre of *item*, through the overlay."""
    point = item.mapToScene(item.boundingRect().center()).toPoint()
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
    QCoreApplication.processEvents()
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
    QCoreApplication.processEvents()


def _sent_keys(synth) -> list[str]:
    return [call.args[0] for call in synth.send_key.call_args_list if call.args]


def _typed(synth) -> list[str]:
    """Everything handed to the synthesizer, whichever call it went through.

    A character can legitimately reach the OS by two different paths, and
    which one the bridge picks is not this file's business. `send_text` is
    the normal route; `send_key` with a `hold_seconds` is the game-compat
    route, taken when the foreground window looks like a game.

    That distinction is environment-dependent *here*, which is the trap: the
    borderless-fullscreen heuristic asks whether the window covers its
    monitor and has no title bar, and an OSK shown under the offscreen
    platform plugin is frameless and fills the virtual screen, so these
    tests can classify themselves as a game depending on the runner. A test
    that watched only `send_text` therefore passed or failed on the geometry
    of the machine, not on the behaviour under test.
    """
    out = [c.args[0] for c in synth.send_text.call_args_list if c.args]
    out += _sent_keys(synth)
    return out


# KeyButton ramps a held key in two stages: a warm-up tick at `repeatDelay`,
# then the first actual repeat one `warmUpGrace` later. `warmUpGrace` is a
# fixed 300 ms on KeyButton that root.repeatDelay / repeatInterval do not
# scale, so the first repeat lands no earlier than repeatDelay + 300 ms
# however low the test turns the other two.
#
# That makes a fixed wait budget the wrong tool in both directions. Waiting
# 400 ms for a repeat left a 40 ms margin and failed on a loaded Windows
# runner; waiting 400 ms to prove a key does NOT repeat is worse, because it
# can pass without the ramp ever having had time to fire, which is a test
# that cannot fail.
_REPEAT_FLOOR_MS = 360  # repeatDelay(60) + warmUpGrace(300) in these tests
_REPEAT_SETTLE_MS = 900  # comfortably past the floor, for negative assertions


def _wait_for_repeats(synth, keysym: str, minimum: int = 2, timeout_ms: int = 4000) -> int:
    """Hold-wait until *keysym* has been sent *minimum* times, bounded.

    Polls rather than sleeping a fixed budget, so a slow runner costs time
    instead of a false failure. Returns the count actually observed, so the
    caller still asserts and a genuinely dead repeat still fails.
    """
    waited = 0
    step = 50
    while waited < timeout_ms:
        QTest.qWait(step)
        waited += step
        if _sent_keys(synth).count(keysym) >= minimum:
            break
    return _sent_keys(synth).count(keysym)


class TestOverlayIsActuallyInTheWay:
    """If the overlay were not intercepting, every test below would pass
    trivially against the old code, since the KeyButtons would just handle
    their own presses. Pin the precondition."""

    def test_overlay_covers_the_keyboard_and_is_enabled(self, swipe_root) -> None:
        root, warnings, _ = swipe_root
        overlay = root.findChild(QQuickItem, "swipeOverlay")
        assert overlay is not None, "swipeOverlay not found by objectName"
        assert overlay.property("enabled") is True
        assert overlay.width() > 0 and overlay.height() > 0
        assert _real_warnings(warnings) == []

    def test_the_two_registries_are_not_the_same_list(self, swipe_root) -> None:
        root, _, _ = swipe_root
        tappable = root.property("tappableKeyRegistry").toVariant()
        chars = root.property("charKeyRegistry").toVariant()
        assert tappable, "nothing registered as tappable"
        assert len(tappable) > len(chars), (
            "the tappable registry must be strictly larger than the swipe "
            "key-centre map, or specials are still missing from it"
        )
        # The swipe map must stay characters-only: a "backspace" centre is a
        # phantom letter in every shape match.
        assert all(
            e["kd"].get("type") == "char" and len(e["kd"].get("key", "")) == 1 for e in chars
        ), "a non-character key leaked into the swipe key-centre map"


class TestSpecialKeysAreNotDeadTaps:
    @pytest.mark.parametrize("action", ["backspace", "delete", "tab", "return"])
    def test_special_key_reaches_the_synthesizer(self, swipe_root, action: str) -> None:
        root, warnings, synth = swipe_root
        key = _find_key(root, type="special", action=action)
        synth.reset_mock()

        _tap(root, key)

        assert KEYSYMS[action] in _sent_keys(synth), (
            f"tapping {action!r} under the swipe overlay sent "
            f"{_sent_keys(synth)!r}; a dead tap sends nothing at all"
        )
        assert _real_warnings(warnings) == []

    def test_character_keys_still_work(self, swipe_root) -> None:
        """The tap fall-through for characters must survive the split."""
        root, _, synth = swipe_root
        key = _find_key(root, type="char", key="a")
        synth.reset_mock()

        _tap(root, key)

        assert "a" in _typed(synth), f"tapping 'a' sent nothing: {_typed(synth)!r}"

    def test_modifier_key_toggles(self, swipe_root) -> None:
        """Modifiers route through toggleShift, not pressSpecialKey, so this
        covers the other dispatch branch in Main.qml's onKeyPressed."""
        root, _, synth = swipe_root
        key = _find_key(root, type="modifier", action="shift")
        assert root.property("shiftOn") is False

        _tap(root, key)

        assert root.property("shiftOn") is True, "Shift did not toggle under the overlay"
        synth.hold_modifier.assert_called_with("shift")


class TestHoldToRepeat:
    """Restoring the tap alone would have left "hold Backspace to delete a
    word" broken, which for a mouse-driven OSK is most of what Backspace is
    for. A press over a special key activates and *holds* it."""

    def test_backspace_types_on_press_not_on_release(self, swipe_root) -> None:
        root, _, synth = swipe_root
        key = _find_key(root, type="special", action="backspace")
        point = key.mapToScene(key.boundingRect().center()).toPoint()
        synth.reset_mock()

        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()
        assert "BackSpace" in _sent_keys(synth), (
            "Backspace must fire on press, not on release: activating on "
            "release is what makes auto-repeat impossible"
        )

        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()
        assert _sent_keys(synth).count("BackSpace") == 1, (
            f"one press produced {_sent_keys(synth)!r}; the release must not type a second time"
        )

    def test_held_backspace_repeats(self, swipe_root) -> None:
        root, _, synth = swipe_root
        key = _find_key(root, type="special", action="backspace")
        point = key.mapToScene(key.boundingRect().center()).toPoint()
        # Shorten the ramp so the test does not sit through the real
        # accessibility-tuned delay.
        root.setProperty("repeatDelay", 60)
        root.setProperty("repeatInterval", 20)
        QCoreApplication.processEvents()
        synth.reset_mock()

        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
        held = _wait_for_repeats(synth, "BackSpace")
        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

        assert held > 1, (
            f"holding Backspace under the overlay produced {held} keystroke(s); "
            "auto-repeat is not running"
        )
        # And it must stop on release rather than run away.
        after_release = _sent_keys(synth).count("BackSpace")
        QTest.qWait(120)
        assert _sent_keys(synth).count("BackSpace") == after_release, (
            "Backspace kept repeating after release"
        )

    def test_character_keys_never_repeat(self, swipe_root) -> None:
        """Held letters must not repeat, overlay or no overlay."""
        root, _, synth = swipe_root
        key = _find_key(root, type="char", key="a")
        point = key.mapToScene(key.boundingRect().center()).toPoint()
        root.setProperty("repeatDelay", 60)
        root.setProperty("repeatInterval", 20)
        QCoreApplication.processEvents()
        synth.reset_mock()

        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(_REPEAT_SETTLE_MS)
        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

        # == 1, not <= 1. "at most one" also passes when the key produced
        # nothing at all, i.e. when the press never landed, so it cannot tell
        # "does not repeat" from "does not work".
        assert _typed(synth).count("a") == 1, (
            f"expected exactly one 'a' from a held character key, got {_typed(synth)!r}"
        )


class TestSwipingStillWorks:
    def test_a_drag_from_a_letter_is_still_a_swipe(self, swipe_root) -> None:
        """The press-to-hold path must not capture gestures that start on a
        character key, or swipe typing itself would be broken by this fix."""
        root, _, synth = swipe_root
        start = _find_key(root, type="char", key="q")
        end = _find_key(root, type="char", key="p")
        p0 = start.mapToScene(start.boundingRect().center()).toPoint()
        p1 = end.mapToScene(end.boundingRect().center()).toPoint()
        synth.reset_mock()

        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, p0)
        QCoreApplication.processEvents()
        # Nothing may be typed on press: for a character key the gesture is
        # genuinely ambiguous until it ends.
        assert not _typed(synth), (
            f"pressing a letter typed {_typed(synth)!r} immediately, so it can never become a swipe"
        )

        steps = 12
        for i in range(1, steps + 1):
            QTest.mouseMove(root, _lerp(p0, p1, i / steps))
        QCoreApplication.processEvents()

        overlay = root.findChild(QQuickItem, "swipeOverlay")
        assert overlay.property("_isSwipe") is True, (
            "dragging across the letter row did not promote to a swipe"
        )
        assert overlay.property("_heldKey") is None, (
            "a letter was captured as a key hold, which blocks swiping"
        )
        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, p1)
        QCoreApplication.processEvents()

    def test_dragging_off_a_held_special_aborts_it(self, swipe_root) -> None:
        """Same escape hatch a KeyButton gives you without the overlay:
        press the wrong key, slide off, nothing more happens.

        "Nothing" has to mean nothing at all, which is the part that is easy
        to get wrong. Dragging off releases the held key, and if that were the
        only state the gesture carried, the rest of it would fall back into
        the ordinary press-drag-release logic: the release would then be read
        as a *tap* on whatever now sits under the cursor, so sliding off
        Backspace onto "g" would type a "g".
        """
        root, _, synth = swipe_root
        key = _find_key(root, type="special", action="backspace")
        far = _find_key(root, type="char", key="g")
        point = key.mapToScene(key.boundingRect().center()).toPoint()
        away = far.mapToScene(far.boundingRect().center()).toPoint()
        root.setProperty("repeatDelay", 60)
        root.setProperty("repeatInterval", 20)
        QCoreApplication.processEvents()
        synth.reset_mock()

        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()
        before = _sent_keys(synth).count("BackSpace")

        QTest.mouseMove(root, away)
        QTest.qWait(_REPEAT_SETTLE_MS)

        assert _sent_keys(synth).count("BackSpace") == before, (
            "a held special key kept repeating after the pointer left it"
        )

        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, away)
        QCoreApplication.processEvents()

        assert "g" not in _typed(synth), (
            f"releasing over 'g' after sliding off Backspace typed {_typed(synth)!r}: "
            "the abort fell through to the tap path"
        )
        assert _sent_keys(synth).count("BackSpace") == before, (
            "the release re-fired the key the gesture had already abandoned"
        )

    def test_dragging_far_off_a_special_is_not_decoded_as_a_swipe(self, swipe_root) -> None:
        """A gesture that began on a special key must never reach the
        recogniser, however far it travels. Only character keys are swipe
        starts, so a "swipe" beginning on Backspace can only decode noise."""
        root, _, synth = swipe_root
        key = _find_key(root, type="special", action="backspace")
        far = _find_key(root, type="char", key="q")
        p0 = key.mapToScene(key.boundingRect().center()).toPoint()
        p1 = far.mapToScene(far.boundingRect().center()).toPoint()
        synth.reset_mock()

        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, p0)
        for i in range(1, 13):
            QTest.mouseMove(root, _lerp(p0, p1, i / 12))
        QCoreApplication.processEvents()

        overlay = root.findChild(QQuickItem, "swipeOverlay")
        assert overlay.property("_isSwipe") is False, (
            "a drag that began on Backspace promoted to a swipe"
        )

        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, p1)
        QCoreApplication.processEvents()
        # send_text specifically: a decoded swipe inserts its word through
        # that call, and the Backspace press itself legitimately shows up in
        # send_key, so _typed() would be non-empty either way.
        assert not synth.send_text.called, (
            f"a gesture starting on Backspace produced text: {synth.send_text.call_args_list}"
        )


class TestHiddenPanelsDoNotClaimPresses:
    """A KeyButton inside a hidden panel is still constructed and still
    registers, so with the Number Row switched off its keys sit in the
    registry carrying stale geometry. They must not be able to claim a press
    aimed at the key the user can actually see."""

    def test_the_hidden_number_row_really_is_registered(self, swipe_root) -> None:
        """Precondition for the test below. If hidden keys ever stop
        registering, that test would pass for the wrong reason."""
        root, _, _ = swipe_root
        assert root.property("showNumberRow") is False
        hidden = [i for i in _key_items(root) if not i.isVisible()]
        assert hidden, "expected the hidden Number Row's keys to exist"

        registered = {
            id(e["item"]) for e in root.property("tappableKeyRegistry").toVariant() if e["item"]
        }
        assert registered, "nothing registered"

    def test_a_tap_lands_on_the_visible_key_not_a_hidden_one(self, swipe_root) -> None:
        """Tap every visible special key in turn and assert the keystroke
        that arrives is that key's, never a digit from the hidden panel."""
        root, _, synth = swipe_root
        for action in ("backspace", "tab", "return"):
            key = _find_key(root, type="special", action=action)
            synth.reset_mock()
            _tap(root, key)
            typed = _typed(synth)
            assert typed == [KEYSYMS[action]], (
                f"tapping {action!r} produced {typed!r}: a hidden key claimed the press"
            )
            # Checked through _typed, not send_text: under the game-compat
            # path a digit reaches the OS via send_key, so watching send_text
            # alone would let exactly the failure this test exists for pass.
            assert not [c for c in typed if c in "1234567890"], (
                f"tapping {action!r} also typed a digit: {typed!r}"
            )


def _lerp(p0, p1, t: float):
    """Point t of the way from p0 to p1, as a QPoint."""
    from PySide6.QtCore import QPoint

    return QPoint(
        round(p0.x() + (p1.x() - p0.x()) * t),
        round(p0.y() + (p1.y() - p0.y()) * t),
    )


class TestSwipeOffPathIsUnaffected:
    """The press lifecycle was extracted out of KeyButton's MouseArea into
    `_acceptPress` / `_pressVisual` / `_activate` / `_endPress` so the overlay
    could drive a key remotely. That refactor sits under **every** key in the
    app, and every other test in this file has swipe ON, so the ordinary path
    a user takes with swipe off (the default) would otherwise be exercised by
    nothing at all.

    These press keys with the overlay disabled, so the events reach
    `KeyButton`'s own MouseArea rather than the overlay.
    """

    @pytest.fixture
    def plain_root(self, swipe_root):
        root, warnings, synth = swipe_root
        root.setProperty("swipeEnabled", False)
        QCoreApplication.processEvents()
        overlay = root.findChild(QQuickItem, "swipeOverlay")
        # The overlay hides itself when disabled, which is what stops it hit
        # testing. Assert it, so these cannot silently keep testing the
        # overlay path and report a pass that means nothing.
        assert overlay.property("enabled") is False
        assert not overlay.isVisible(), (
            "disabled overlay is still visible, so it still takes presses"
        )
        return root, warnings, synth

    @pytest.mark.parametrize("action", ["backspace", "delete", "tab", "return"])
    def test_special_key_still_types_with_swipe_off(self, plain_root, action: str) -> None:
        root, warnings, synth = plain_root
        key = _find_key(root, type="special", action=action)
        synth.reset_mock()

        _tap(root, key)

        assert KEYSYMS[action] in _sent_keys(synth), (
            f"tapping {action!r} with swipe OFF sent {_sent_keys(synth)!r}"
        )
        assert _real_warnings(warnings) == []

    def test_character_key_still_types_with_swipe_off(self, plain_root) -> None:
        root, _, synth = plain_root
        key = _find_key(root, type="char", key="a")
        synth.reset_mock()

        _tap(root, key)

        assert "a" in _typed(synth), f"tapping 'a' with swipe OFF sent nothing: {_typed(synth)!r}"

    def test_hold_to_repeat_still_works_with_swipe_off(self, plain_root) -> None:
        """The case the refactor most plausibly breaks: `_activate` arms the
        repeat timer, and it is now called from two places."""
        root, _, synth = plain_root
        key = _find_key(root, type="special", action="backspace")
        point = key.mapToScene(key.boundingRect().center()).toPoint()
        root.setProperty("repeatDelay", 60)
        root.setProperty("repeatInterval", 20)
        QCoreApplication.processEvents()
        synth.reset_mock()

        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
        held = _wait_for_repeats(synth, "BackSpace")
        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

        assert held > 1, f"holding Backspace with swipe OFF produced {held} keystroke(s)"

        after = _sent_keys(synth).count("BackSpace")
        QTest.qWait(120)
        assert _sent_keys(synth).count("BackSpace") == after, (
            "Backspace kept repeating after release with swipe OFF"
        )

    def test_character_keys_never_repeat_with_swipe_off(self, plain_root) -> None:
        root, _, synth = plain_root
        key = _find_key(root, type="char", key="a")
        point = key.mapToScene(key.boundingRect().center()).toPoint()
        root.setProperty("repeatDelay", 60)
        root.setProperty("repeatInterval", 20)
        QCoreApplication.processEvents()
        synth.reset_mock()

        QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
        QTest.qWait(_REPEAT_SETTLE_MS)
        QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

        # == 1, not <= 1: see the swipe-on version of this test.
        assert _typed(synth).count("a") == 1, (
            f"expected exactly one 'a' from a held character key with swipe OFF, "
            f"got {_typed(synth)!r}"
        )

    def test_right_click_types_the_shifted_variant_with_swipe_off(self, plain_root) -> None:
        """`_acceptPress` / `_pressVisual` are shared with the right-button
        branch, which returns before `_activate`. A refactor that dropped that
        early return would make right-click also type the unshifted key."""
        root, _, synth = plain_root
        key = _find_key(root, type="char", key="1")
        point = key.mapToScene(key.boundingRect().center()).toPoint()
        synth.reset_mock()

        QTest.mousePress(root, Qt.RightButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()
        QTest.mouseRelease(root, Qt.RightButton, Qt.NoModifier, point)
        QCoreApplication.processEvents()

        typed = _typed(synth)
        # Membership, not position. Two things make ordering meaningless
        # here: "!" is punctuation, so auto-space-after-punctuation (on by
        # default) appends a space of its own, and `_typed` concatenates two
        # separate call channels rather than interleaving them chronologically.
        # The property that actually matters is which characters reached the
        # synthesizer at all.
        assert "!" in typed, f"right-clicking '1' typed {typed!r}, expected '!' among them"
        assert "1" not in typed, (
            f"right-click also typed the unshifted key: {typed!r}. The right-button "
            "branch must return before _activate()"
        )
