"""
One key-event burst at a time, paced, and off the caller's thread when long.

``WindowsKeySynthesizer`` hands every burst of events it builds to a
:class:`SerialInjector`, which decides how the burst reaches the OS:

- **Atomic**: the whole burst in one ``send`` call.  For a chord
  (Ctrl down, C, Ctrl up) and for the Shift+Left selection that opens a
  ``replace_text``, where a gap between the events would let a click of
  the user's land *inside* the gesture (Shift+click extends a selection
  the next keystroke then overwrites; review of PR #244).
- **Paced**: one ``send`` call per event with ``gap_s`` between them.
  Streaming clients (Moonlight over Sunshine) drop key-ups out of a
  zero-gap burst and leave a letter held on the far side, so typed text
  goes out paced.  A burst short enough to fit ``inline_budget_s`` runs on
  the caller's thread, which keeps a suggestion pick synchronous; a longer
  one (a dictated passage, a programmed text action) runs on a worker
  thread so the UI thread is not blocked for seconds (same review).

Only one burst is ever in flight.  The synthesizer calls :meth:`settle`
before building the next burst, so what it reads about the OS key state
is current and the order of its calls is the order of the events.  A
background burst can be :meth:`cancel`-led, and stops only at a point
where no key it pressed is still down, so an abandoned insert never
leaves a key held.

This module is platform-neutral so its behaviour is tested on every CI
runner; the Windows synthesizer supplies the ``send`` and ``key_state``
callables.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Generic, Hashable, List, Optional, Sequence, Set, Tuple, TypeVar

T = TypeVar("T")

# (key identity, is_down) for a key event; None for an event that is not
# a key (nothing in this app sends one, but the injector does not care).
KeyState = Optional[Tuple[Hashable, bool]]


class SerialInjector(Generic[T]):
    """Serialise, pace and background the delivery of key-event bursts."""

    def __init__(
        self,
        send: Callable[[Sequence[T]], Tuple[int, int]],
        *,
        gap_s: float,
        inline_budget_s: float,
        key_state: Callable[[T], KeyState],
        sleep: Callable[[float], None] = time.sleep,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            send: Delivers a batch to the OS in one call and returns
                ``(events injected, last error code)``.  ``error`` is 0
                when every event went in.
            gap_s: Pause between consecutive events of a paced burst.
            inline_budget_s: A paced burst whose gaps add up to at most
                this runs on the caller's thread; a longer one runs on a
                worker thread.
            key_state: Maps an event to ``(key identity, is_down)`` so a
                cancel can wait for every pressed key to be released.
            sleep: Injected for tests; the worker and the inline path both
                pace through it.
        """
        self._send = send
        self._gap_s = gap_s
        self._inline_budget_s = inline_budget_s
        self._key_state = key_state
        self._sleep = sleep
        self._log = logger or logging.getLogger(__name__)
        # Held by whichever thread is delivering a burst.  Inline bursts and
        # the worker both take it, so two bursts can never interleave.
        self._delivering = threading.Lock()
        self._state = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._cancel = threading.Event()

    # ------------------------------------------------------------------ #
    #  Queries
    # ------------------------------------------------------------------ #

    @property
    def busy(self) -> bool:
        """True while a background burst is still being delivered."""
        worker = self._worker
        return worker is not None and worker.is_alive()

    def paced_duration_s(self, count: int) -> float:
        """How long the gaps of a paced burst of ``count`` events take."""
        return max(count - 1, 0) * self._gap_s

    # ------------------------------------------------------------------ #
    #  Control
    # ------------------------------------------------------------------ #

    def settle(self, timeout: float) -> bool:
        """Wait for a background burst to finish.

        Returns False if it was still running when ``timeout`` elapsed;
        the caller then goes ahead anyway (the delivery lock still keeps
        the two bursts from interleaving) and should say so in the log.
        """
        worker = self._worker
        if worker is None or not worker.is_alive():
            return True
        worker.join(timeout)
        return not worker.is_alive()

    def cancel(self) -> None:
        """Abandon the rest of a background burst.

        Takes effect at the next point where no key the burst pressed is
        still down, so a Shift wrap or a surrogate pair is finished before
        the burst stops.  A no-op when nothing is running in the
        background: an inline burst has already finished by the time its
        caller could ask, and the flag must not leak into the next burst.
        """
        with self._state:
            if self._worker is not None and self._worker.is_alive():
                self._cancel.set()

    def inject(self, events: Sequence[T], *, atomic: bool = False) -> None:
        """Deliver ``events`` in order, atomically or paced.

        A paced burst that fits ``inline_budget_s`` is delivered before
        this returns; a longer one is handed to a worker thread and this
        returns at once.  Either way a burst still running in the
        background is waited for first, so bursts never overlap or
        reorder.
        """
        if not events:
            return
        events = list(events)
        if not self.settle(timeout=60.0):
            self._log.warning("A background key burst did not finish; sending the next one anyway")

        if atomic or self.paced_duration_s(len(events)) <= self._inline_budget_s:
            with self._delivering:
                if atomic:
                    sent, error = self._send(events)
                    self._report(sent, error, len(events))
                else:
                    self._deliver_paced(events, cancel=None)
            return

        with self._state:
            self._cancel.clear()
            worker = threading.Thread(
                target=self._deliver_in_background,
                args=(events,),
                name="alpha-osk-key-injector",
                daemon=True,
            )
            self._worker = worker
            worker.start()

    # ------------------------------------------------------------------ #
    #  Delivery
    # ------------------------------------------------------------------ #

    def _deliver_in_background(self, events: List[T]) -> None:
        with self._delivering:
            try:
                self._deliver_paced(events, cancel=self._cancel)
            except Exception:
                # A worker's exception would otherwise vanish into the
                # thread's default hook; the UI thread carries on either way.
                self._log.warning("Background key burst failed", exc_info=True)

    def _deliver_paced(self, events: List[T], *, cancel: Optional[threading.Event]) -> None:
        total_sent = 0
        last_error = 0
        down: Set[Hashable] = set()
        delivered = 0
        for i, event in enumerate(events):
            if i:
                if cancel is not None and cancel.is_set() and not down:
                    self._log.debug("Key burst cancelled after %d/%d events", i, len(events))
                    break
                self._sleep(self._gap_s)
            sent, error = self._send([event])
            total_sent += sent
            if error:
                last_error = error
            delivered += 1
            state = self._key_state(event)
            if state is not None:
                key, is_down = state
                if is_down:
                    down.add(key)
                else:
                    down.discard(key)
        self._report(total_sent, last_error, delivered)

    def _report(self, sent: int, error: int, expected: int) -> None:
        if sent != expected:
            self._log.warning(
                "SendInput injected %d/%d events (error=%d). "
                "This may indicate insufficient privileges: consider "
                "UIAccess or running as admin.",
                sent,
                expected,
                error,
            )
