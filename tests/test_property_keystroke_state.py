"""Stateful property test: the bridge against a fake operating system.

``KeyboardBridge`` keeps its own idea of which modifiers are down
(``_shift_active`` and friends) and tells the OS through ``hold_modifier`` /
``release_modifier``.  Those are two copies of one fact, and the project's
hardest-won rules are all about keeping them equal: the sticky auto-release
lives in one method so that no path forgets it, ``_without_held_modifiers``
exists because a hold the bridge forgot to drop rewrites a verbatim insert,
and ``shutdown`` releases whatever is left.  Example-based tests check those
at the orderings someone thought of.  This hunts for the ordering nobody did.

It drives a real bridge (real prediction engine, so pills really appear)
with ``tests/fake_os_keyboard.py`` as its synthesiser, applies random
sequences of the user's actions, and after **every** step asserts:

1. the OS's held-key table equals exactly the modifiers the bridge reports
   active (Caps Lock is not one: it holds nothing);
2. a right-click lock implies the modifier is active, since a lock the
   bridge believes in while the OS holds nothing is a lie in one direction;
3. a verbatim insert (glyph, snippet, pill) reaches the OS as exactly the
   intended text and never as chords, whatever modifiers were active.

and on teardown, that ``shutdown()`` leaves nothing held.  A second machine
makes calls to the OS fail at random and asserts the same agreement.

The fake is deliberately unforgiving (see its docstring): a held Shift
uppercases the pill and a held Ctrl turns it into chords, which is what
makes invariant 3 capable of failing.

Conventions follow ``tests/conftest.py``: derandomized, no ``assume()``, and
strategies that draw the interesting shape directly.  Privacy mode is set
through its slot, never by poking ``_privacy_mode``.  The bridge is built
per example (about 0.4 s with the shared packed caches), so the example
counts here are lower than the pure-function suites'.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
)

from src.keyboard_bridge import KeyboardBridge
from src.prediction.token_predictor import TokenPredictor
from src.snippets import SnippetStore
from tests.fake_os_keyboard import MODIFIER_NAMES, FakeOSError, FakeOSKeyboard

_TYPED_KEYS = list("abcdefghijklmnopqrstuvwxyz0123456789.,;:!?'-/@")
_LITERAL_KEYS = list("ABCXYZabc!@#.,'1")
_SPECIAL_KEYS = [
    "backspace",
    "space",
    "return",
    "tab",
    "left",
    "right",
    "up",
    "down",
    "delete",
    "home",
    "end",
    "escape",
]
#: Verbatim glyphs, ASCII ones included on purpose: a standing Shift shifts
#: an ASCII glyph through the scancode route but leaves a non-ASCII one alone,
#: so both routes of the fake need exercising.
_GLYPHS = ["é", "€", "→", "\U0001f600", "A", "a", "1", "#", "~"]
_SNIPPETS = [
    ("name", "Owen P. Kent"),
    ("addr", "12 Main St, Apt 4"),
    ("lower", "hello world"),
    ("multi", "line one\nline two"),
]
_FAILABLE = ["send_key", "send_text", "replace_text", "hold_modifier", "release_modifier"]
_OWNERS = ["prediction", "snippets", "keyaction"]

_SETTINGS = settings(
    max_examples=15,
    stateful_step_count=25,
    deadline=None,
    suppress_health_check=list(HealthCheck),
)


def build_bridge(tmp: Path) -> Tuple[KeyboardBridge, FakeOSKeyboard]:
    """A real bridge typing into a fresh fake OS, isolated from the developer's data.

    The snippet store is swapped before anything can write to it (the default
    one is the developer's own), and the token store is emptied so one saved
    address cannot outrank the words under test.
    """
    fake = FakeOSKeyboard()
    with patch("src.keyboard_bridge.create_key_synthesizer", return_value=fake):
        bridge = KeyboardBridge()
    snippets_file = tmp / "snippets.json"
    snippets_file.write_text(
        json.dumps({"snippets": [{"label": k, "value": v} for k, v in _SNIPPETS]}),
        encoding="utf-8",
    )
    bridge._snippets = SnippetStore(snippets_file)
    bridge._snippets.load()
    bridge._predictor._ngram.tokens = TokenPredictor()
    fake.clear_log()
    return bridge, fake


class _BridgeMachine(RuleBasedStateMachine):
    """Shared set-up and the agreement invariants; subclasses add the rules."""

    def __init__(self) -> None:
        super().__init__()
        self._tmp = Path(tempfile.mkdtemp(prefix="aosk-keystate-"))
        self.bridge, self.fake = build_bridge(self._tmp)
        self.shut_down = False

    # --- helpers ---

    def _bridge_modifiers(self) -> set:
        return {name for name in MODIFIER_NAMES if getattr(self.bridge, f"{name}Active")}

    def _locked(self, name: str) -> bool:
        return bool(getattr(self.bridge, f"_{name}_locked"))

    # --- invariants ---

    #: Whether the OS and the bridge must agree in both directions.  A machine
    #: that makes OS calls fail relaxes this to "the OS holds nothing the bridge
    #: does not know about": a failed key-down legitimately leaves the bridge
    #: believing a hold the OS never made (a stale highlight, which the user
    #: taps away), while a stuck OS key with no keycap lit is unrecoverable.
    strict = True

    @invariant()
    def the_os_holds_exactly_what_the_bridge_reports(self) -> None:
        if not self.strict:
            stuck = self.fake.held - self._bridge_modifiers()
            assert not stuck, f"OS holds {sorted(stuck)} that the bridge does not believe in"
            return
        assert self.fake.held == self._bridge_modifiers(), (
            f"OS holds {sorted(self.fake.held)}, bridge reports {sorted(self._bridge_modifiers())}"
        )

    @invariant()
    def a_lock_implies_the_modifier_is_active(self) -> None:
        for name in MODIFIER_NAMES:
            if self._locked(name):
                assert getattr(self.bridge, f"{name}Active"), f"{name} locked but not active"

    def teardown(self) -> None:
        try:
            if not self.shut_down:
                self.bridge.shutdown()
            assert self.fake.held == set(), f"shutdown left {sorted(self.fake.held)} held"
        finally:
            shutil.rmtree(self._tmp, ignore_errors=True)


class KeystrokeStateMachine(_BridgeMachine):
    """Every user action the bridge turns into OS input, in any order."""

    def _before(self) -> tuple:
        return self.fake.text, self.fake.caret, len(self.fake.chords())

    # --- plain typing ---

    @rule(key=st.sampled_from(_TYPED_KEYS))
    def press_key(self, key: str) -> None:
        self.bridge.pressKey(key)

    @rule(char=st.sampled_from(_LITERAL_KEYS))
    def press_key_literal(self, char: str) -> None:
        self.bridge.pressKeyLiteral(char)

    @rule(name=st.sampled_from(_SPECIAL_KEYS))
    def press_special_key(self, name: str) -> None:
        self.bridge.pressSpecialKey(name)

    # --- modifiers ---

    @rule()
    def toggle_shift(self) -> None:
        self.bridge.toggleShift()

    @rule()
    def toggle_ctrl(self) -> None:
        self.bridge.toggleCtrl()

    @rule()
    def toggle_alt(self) -> None:
        self.bridge.toggleAlt()

    @rule()
    def toggle_win(self) -> None:
        self.bridge.toggleWin()

    @rule(name=st.sampled_from(MODIFIER_NAMES))
    def lock_modifier(self, name: str) -> None:
        self.bridge.lockModifier(name)

    @rule()
    def toggle_caps_lock(self) -> None:
        self.bridge.toggleCapsLock()
        assert "caps" not in self.fake.held

    # --- verbatim inserts ---

    @precondition(lambda self: bool(self.bridge._predictions))
    @rule(pick=st.integers(min_value=0, max_value=7))
    def press_prediction(self, pick: int) -> None:
        bridge = self.bridge
        word = bridge._predictions[pick % len(bridge._predictions)]
        is_token = word in bridge._token_pill_words
        text, caret, chords = self._before()
        deferred = bool(bridge._deferred_auto_space)
        bridge.pressPrediction(word)
        assert len(self.fake.chords()) == chords, "a pill tap reached the OS as a chord"
        before_caret = self.fake.text[: self.fake.caret]
        if is_token:
            assert before_caret.endswith(word), (before_caret, word)
            return
        # A deferred auto-space can owe the pill a capital it was never shown.
        accepted = {word + " "}
        if deferred and word[:1].islower():
            accepted.add(word[0].upper() + word[1:] + " ")
        assert any(before_caret.endswith(a) for a in accepted), (
            f"pill {word!r} arrived as {before_caret[-len(word) - 3 :]!r}"
        )

    @rule(glyph=st.sampled_from(_GLYPHS))
    def insert_glyph(self, glyph: str) -> None:
        self._verbatim(lambda: self.bridge.insertGlyph(glyph), glyph)

    @rule(index=st.integers(min_value=0, max_value=len(_SNIPPETS) - 1))
    def insert_snippet(self, index: int) -> None:
        self._verbatim(lambda: self.bridge.insertSnippet(index), _SNIPPETS[index][1])

    def _verbatim(self, act, intended: str) -> None:
        bridge = self.bridge
        text, caret, chords = self._before()
        in_edit = bridge._edit_mode_active
        deferred = " " if bridge._deferred_auto_space and not in_edit else ""
        act()
        assert len(self.fake.chords()) == chords, "a verbatim insert reached the OS as a chord"
        arrived = self.fake.text[caret : self.fake.caret]
        expected = "" if in_edit else deferred + intended
        assert arrived == expected, f"intended {expected!r}, OS received {arrived!r}"
        assert self.fake.text[:caret] == text[:caret]

    # --- sessions, privacy, context ---

    @rule(owner=st.sampled_from(_OWNERS))
    def begin_edit_session(self, owner: str) -> None:
        self.bridge.beginEditSession(owner)

    @rule(owner=st.sampled_from(_OWNERS))
    def end_edit_session(self, owner: str) -> None:
        self.bridge.endEditSession(owner)

    @rule(enabled=st.booleans())
    def set_privacy_mode(self, enabled: bool) -> None:
        self.bridge.setPrivacyMode(enabled)

    @rule()
    def reset_context(self) -> None:
        self.bridge.resetContext()

    @rule()
    def reset_modifiers(self) -> None:
        self.bridge.resetModifiers()


class FailingOSStateMachine(_BridgeMachine):
    """The same agreement, with OS calls that fail at random.

    ``SendInput`` can fail (a blocked desktop, a UIPI refusal).  Whatever the
    bridge does with the exception, it must not leave the OS holding a key
    the bridge no longer believes in, which is the "stuck Shift in every other
    app" failure.  The armed failure fires once, on the Nth OS call of the
    next action, and the action's exception is swallowed here because the
    point is the state it leaves behind.
    """

    strict = False

    def _act(self, n: int, act) -> None:
        self.fake.fail_after(n, methods=_FAILABLE)
        try:
            act()
        except FakeOSError:
            pass
        finally:
            self.fake.fail_on = None

    @rule(n=st.integers(1, 6), key=st.sampled_from(_TYPED_KEYS))
    def press_key(self, n: int, key: str) -> None:
        self._act(n, lambda: self.bridge.pressKey(key))

    @rule(n=st.integers(1, 6), name=st.sampled_from(["space", "backspace", "left", "return"]))
    def press_special_key(self, n: int, name: str) -> None:
        self._act(n, lambda: self.bridge.pressSpecialKey(name))

    @rule(n=st.integers(1, 6), which=st.sampled_from(["Shift", "Ctrl", "Alt", "Win"]))
    def toggle(self, n: int, which: str) -> None:
        self._act(n, getattr(self.bridge, f"toggle{which}"))

    @rule(n=st.integers(1, 6), glyph=st.sampled_from(_GLYPHS))
    def insert_glyph(self, n: int, glyph: str) -> None:
        self._act(n, lambda: self.bridge.insertGlyph(glyph))

    @rule(n=st.integers(1, 6), index=st.integers(0, len(_SNIPPETS) - 1))
    def insert_snippet(self, n: int, index: int) -> None:
        self._act(n, lambda: self.bridge.insertSnippet(index))

    @precondition(lambda self: bool(self.bridge._predictions))
    @rule(n=st.integers(1, 6))
    def press_prediction(self, n: int) -> None:
        word: Optional[str] = self.bridge._predictions[0]
        self._act(n, lambda: self.bridge.pressPrediction(word or ""))


KeystrokeStateMachine.TestCase.settings = _SETTINGS
TestBridgeAgreesWithTheOS = KeystrokeStateMachine.TestCase

FailingOSStateMachine.TestCase.settings = _SETTINGS
TestBridgeAgreesWithTheOSUnderFailure = FailingOSStateMachine.TestCase


@pytest.fixture
def rig(tmp_path: Path):
    bridge, fake = build_bridge(tmp_path)
    yield bridge, fake
    bridge.shutdown()


class TestDefectsTheMachinesFound:
    """Minimal repros of the two defects the state machines found, now fixed.

    ``resetModifiers`` left a right-click lock behind as a phantom, and a
    key-up that raised left the OS holding a modifier the bridge had already
    forgotten. The machines above now cover both paths too; these keep the
    reduced cases readable.
    """

    def test_a_lock_can_be_taken_again_after_reset_modifiers(self, rig) -> None:
        bridge, fake = rig
        bridge.lockModifier("ctrl")
        bridge.resetModifiers()
        bridge.lockModifier("ctrl")
        assert bridge.ctrlActive
        assert fake.held == {"ctrl"}

    def test_reset_modifiers_with_no_lock_leaves_a_clean_slate(self, rig) -> None:
        """The inverse: without a lock the same sequence behaves."""
        bridge, fake = rig
        bridge.toggleCtrl()
        bridge.resetModifiers()
        assert not bridge.ctrlActive
        assert fake.held == set()
        bridge.lockModifier("ctrl")
        assert bridge.ctrlActive
        assert fake.held == {"ctrl"}

    def test_a_failed_key_up_does_not_strand_a_modifier(self, rig) -> None:
        bridge, fake = rig
        bridge.toggleShift()
        fake.fail_after(1, methods=["release_modifier"])
        with pytest.raises(FakeOSError):
            bridge.pressKey("a")
        stuck = fake.held - {n for n in MODIFIER_NAMES if getattr(bridge, f"{n}Active")}
        assert not stuck, f"OS still holds {sorted(stuck)}"

    def test_a_failed_key_down_is_visible_on_the_keycap(self, rig) -> None:
        """The inverse: a failed key-down leaves the bridge believing the hold.

        That is the recoverable direction (a lit key the user taps off), which
        is why the failure machine tolerates it and not the stuck key above.
        """
        bridge, fake = rig
        fake.fail_after(1, methods=["hold_modifier"])
        with pytest.raises(FakeOSError):
            bridge.toggleShift()
        assert fake.held == set()
        assert bridge.shiftActive
        bridge.toggleShift()
        assert not bridge.shiftActive
        assert fake.held == set()
