"""Recording synthesiser: the "application" a study trial types into.

The problem this solves. A trial needs the participant to type into a field we
own, with the prediction engine running exactly as it normally does, and with
nothing reaching the desktop.  The existing edit-mode intercept
(``setEditMode`` plus ``editKeyTyped``) gives the first and third of those and
deliberately not the second: it returns early, skipping "password detection,
analytics, predictions" in its own words.  Predictions are the thing under
study, so edit mode cannot carry a trial.

So the redirect happens at the **synthesiser** instead of at the keystroke
entry point.  ``KeyboardBridge`` funnels every character, pill, snippet and
special key through exactly three wrappers (``_send_key``, ``_send_text``,
``_replace_text``), which is already documented as the one place that knows
text is leaving the keyboard.  Swapping ``_synth`` for this class means a
trial inherits every invariant in that file for free: suffix-only insertion,
sticky-modifier release, the deferred auto-space, auto-capitalisation, the
context buffers, the pointer-bias model.  Nothing had to grow a study branch,
which matters because ``_press_char`` is the most invariant-dense function in
the project and a fourth mode inside it would be a standing hazard.

It follows that this class has to behave like a real text field, not like a
log.  It keeps a caret.  A participant who presses Left and fixes a letter in
the middle of a word is doing an ordinary thing, and a recorder that appended
blindly would report a transcript nobody typed and score it as an error.
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional, Sequence

from ..platform.base import KeySynthesizerBase
from .metrics import Event

# Special keys that move the caret rather than changing the text.  Anything
# not named here is recorded as a "special" action and leaves the text alone,
# which is the safe direction to be wrong: an unmodelled key costs one event
# classified coarsely, while a wrongly applied one corrupts the transcript
# that the whole trial is scored against.
_CARET_KEYS = {
    "Left": -1,
    "Right": 1,
}
_HOME_KEYS = {"Home", "Prior", "Page_Up"}
_END_KEYS = {"End", "Next", "Page_Down"}


class RecordingSynthesizer(KeySynthesizerBase):
    """Stands in for the platform synthesiser for the duration of a trial.

    Records what would have been typed, and what it cost in clicks, without
    anything reaching the OS.  One instance per trial: :meth:`reset` exists
    for reuse in tests rather than as part of the normal flow.
    """

    def __init__(
        self,
        clock: Optional[Callable[[], float]] = None,
        on_change: Optional[Callable[[], None]] = None,
    ) -> None:
        self._clock = clock or time.monotonic
        # A plain callable rather than a Qt signal, so this package stays
        # importable and testable with no display attached, which is what lets
        # the whole protocol be re-analysed offline.  StudyBridge adapts it to
        # a signal on the Qt side.
        self._on_change = on_change
        self._chars: List[str] = []
        self._caret = 0
        self._events: List[Event] = []
        self._origin: Optional[float] = None

    # --- KeySynthesizerBase ---

    def is_available(self) -> bool:
        return True

    def backend_name(self) -> str:
        return "study-recorder"

    def send_key(
        self,
        key_name: str,
        modifiers: Optional[List[str]] = None,
        hold_seconds: float = 0.0,
    ) -> None:
        if key_name == "BackSpace":
            self._backspace()
            self._record("backspace", "")
            return
        if key_name == "Delete":
            if self._caret < len(self._chars):
                del self._chars[self._caret]
            self._record("special", "")
            return
        if key_name in _CARET_KEYS:
            self._caret = max(0, min(len(self._chars), self._caret + _CARET_KEYS[key_name]))
            self._record("special", "")
            return
        if key_name in _HOME_KEYS:
            self._caret = 0
            self._record("special", "")
            return
        if key_name in _END_KEYS:
            self._caret = len(self._chars)
            self._record("special", "")
            return

        # A single printable character reaching send_key is the chord path in
        # _press_char (a modifier was active), not a special key.  It is still
        # one character the participant paid one click for, so it counts as
        # typing rather than as a command.
        if len(key_name) == 1 and not modifiers:
            self._insert(key_name)
            self._record("char", key_name)
            return
        self._record("special", "")

    def send_text(self, text: str) -> None:
        if not text:
            return
        self._insert(text)
        # One click that produced several characters is a pill, a snippet or a
        # programmed phrase.  One click that produced one character is typing.
        # That distinction is the entire measurement, so it is derived from
        # what arrived rather than from a flag some caller has to remember to
        # set: a caller that forgot would silently score a pill as typing and
        # report savings of zero.
        self._record("pill" if len(text) > 1 else "char", text)

    def send_combination(self, keys: Sequence[str]) -> None:
        self._record("special", "")

    def hold_modifier(self, key_name: str) -> None:
        """No-op: a held modifier is state, not text, and costs no click here.

        The click was already counted when the modifier key was pressed.
        """

    def release_modifier(self, key_name: str) -> None:
        """No-op, see :meth:`hold_modifier`."""

    def reset_modifier_state(self) -> None:
        """No-op, see :meth:`hold_modifier`."""

    def replace_text(self, backspace_count: int, text: str) -> None:
        """Select the last *backspace_count* characters and overwrite them.

        The prediction path falls back to this whenever a pill does not
        case-sensitively continue what was typed.  It is one click from the
        participant's point of view however many characters it rewrites, which
        is why it records a single "pill" event rather than a run of
        backspaces followed by an insert.
        """
        for _ in range(max(0, backspace_count)):
            self._backspace()
        self._insert(text)
        self._record("pill", text)

    # --- recording ---

    def note_offer(self, words: Sequence[str]) -> None:
        """Record that a row of suggestions became visible.

        Not an input action and never counted as one.  It exists so
        ``metrics.pill_acceptance_latency_ms`` can measure the gap between a
        suggestion appearing and being taken, which is the visual-search cost
        that Koester and Levine identified as the reason keystroke savings
        overstate benefit.  Measuring it is a large part of why this study is
        worth running.
        """
        cleaned = tuple(str(w) for w in words if w)
        if cleaned:
            self._record("offer", "", offered=cleaned)

    def _record(self, kind: str, text: str, offered: Sequence[str] = ()) -> None:
        now = self._clock()
        if self._origin is None:
            self._origin = now
        self._events.append(
            Event(
                t_ms=int(round((now - self._origin) * 1000.0)),
                kind=kind,
                text=text,
                offered=tuple(offered),
            )
        )
        if self._on_change is not None:
            self._on_change()

    def _insert(self, text: str) -> None:
        self._chars[self._caret : self._caret] = list(text)
        self._caret += len(text)

    def _backspace(self) -> None:
        if self._caret > 0:
            del self._chars[self._caret - 1]
            self._caret -= 1

    # --- results ---

    @property
    def transcript(self) -> str:
        return "".join(self._chars)

    @property
    def caret(self) -> int:
        return self._caret

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def reset(self) -> None:
        self._chars.clear()
        self._caret = 0
        self._events.clear()
        self._origin = None
