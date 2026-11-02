"""
One key-event burst at a time, paced, queued, and off the caller's thread.

``WindowsKeySynthesizer`` hands every burst it builds to a
:class:`SerialInjector` as a list of **units**, each a list of **groups**,
each a list of events:

- A *group* is one ``send`` call (one ``SendInput``).  A chord (Ctrl down,
  C, Ctrl up), the Shift+Left selection that opens ``replace_text`` and a
  shift-wrapped character (Shift down, R, R up, Shift up) are each one
  group, so no gap exists while a modifier is down: a click of the user's
  landing in such a gap would be Shift+click, and the keystrokes that
  follow would act on the wrong text (review of PR #244).  A plain typed
  character is two groups, key-down and key-up, because streaming clients
  (Moonlight over Sunshine) drop key-ups out of a zero-gap burst and leave
  the letter held on the far side.
- ``gap_s`` is slept between consecutive groups, never before the first
  group of a quiet injector or after the last.
- A *unit* is the smallest thing a cancel may split: one character (a
  UTF-16 surrogate pair included), one gesture.  A cancelled burst stops
  at the next unit boundary, so it never leaves a key down or half a
  character typed.

Only one group is ever in flight, and bursts are delivered in the order
they were handed in.  A burst that fits ``inline_budget_s`` while the
injector is idle is delivered before :meth:`inject` returns (a suggestion
pick stays synchronous); anything else joins a FIFO drained by one worker
thread, and :meth:`inject` returns at once.  **Nothing on the caller's
thread ever waits for the worker** except :meth:`shutdown` and the test
helper :meth:`settle`: joining it from a Qt callback froze the UI for the
length of a dictated passage (same review, twice).

A burst marked *cancellable* (typed text) is dropped by :meth:`cancel`,
queued or running; everything else (a modifier hold or release, a chord, a
key the user tapped meanwhile) is still delivered, so a locked modifier
the bridge re-holds after an insert is never lost to a cancel.

This module is platform-neutral so its behaviour is tested on every CI
runner; the Windows synthesizer supplies ``send``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Callable, Deque, Generic, List, Optional, Sequence, Tuple, TypeVar, Union

T = TypeVar("T")

Group = Sequence[T]  # one send call
# One character or gesture: the cancel boundary.  A float between two
# groups is a pause in seconds that replaces the standard gap there (the
# game-compat hold between a key-down and its key-up), delivered by
# whichever thread delivers the unit.
Unit = Sequence[Union[Group[T], float]]


class _Job(Generic[T]):
    __slots__ = ("units", "cancellable", "cancelled")

    def __init__(self, units: Sequence[Unit[T]], cancellable: bool) -> None:
        self.units: List[List[Union[List[T], float]]] = [
            [float(item) if isinstance(item, (int, float)) else list(item) for item in unit]
            for unit in units
        ]
        self.cancellable = cancellable
        self.cancelled = False

    @property
    def group_count(self) -> int:
        return sum(1 for unit in self.units for item in unit if isinstance(item, list))

    def gaps_s(self, gap_s: float) -> float:
        """How long the sleeps between this job's groups take, pauses included."""
        total = 0.0
        groups_seen = 0
        pause = 0.0
        for unit in self.units:
            for item in unit:
                if not isinstance(item, list):
                    pause = max(pause, item)
                    continue
                if groups_seen:
                    total += max(gap_s, pause)
                pause = 0.0
                groups_seen += 1
        return total


class SerialInjector(Generic[T]):
    """Serialise, pace, queue and background the delivery of key-event bursts."""

    def __init__(
        self,
        send: Callable[[Sequence[T]], Tuple[int, int]],
        *,
        gap_s: float,
        inline_budget_s: float,
        sleep: Callable[[float], None] = time.sleep,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        """
        Args:
            send: Delivers one group to the OS in one call and returns
                ``(events injected, last error code)``; the error is 0 when
                every event went in.
            gap_s: Pause between consecutive groups.
            inline_budget_s: A burst whose gaps add up to at most this, met
                while the injector is idle, is delivered on the caller's
                thread; anything else is queued for the worker.
            sleep: Injected for tests; every gap goes through it.
        """
        self._send = send
        self._gap_s = gap_s
        self._inline_budget_s = inline_budget_s
        self._sleep = sleep
        self._log = logger or logging.getLogger(__name__)
        # Held by whichever thread is delivering, so an inline burst and
        # the worker can never interleave.
        self._delivering = threading.Lock()
        # Guards the queue, the worker handle and the running job.
        self._state = threading.Lock()
        self._queue: Deque[_Job[T]] = deque()
        self._worker: Optional[threading.Thread] = None
        self._current: Optional[_Job[T]] = None

    # ------------------------------------------------------------------ #
    #  Queries
    # ------------------------------------------------------------------ #

    @property
    def busy(self) -> bool:
        """True while anything is queued or still being delivered by the worker."""
        with self._state:
            return self._worker is not None

    def paced_duration_s(self, group_count: int) -> float:
        """How long the gaps of a burst of ``group_count`` groups take."""
        return max(group_count - 1, 0) * self._gap_s

    # ------------------------------------------------------------------ #
    #  Control
    # ------------------------------------------------------------------ #

    def inject(self, units: Sequence[Unit[T]], *, cancellable: bool = False) -> None:
        """Deliver ``units`` in order, after everything handed in before.

        Returns before delivery unless the injector is idle and the burst
        fits the inline budget.  ``cancellable`` marks a burst
        :meth:`cancel` may drop.
        """
        job: _Job[T] = _Job(units, cancellable)
        if not job.group_count:
            return
        with self._state:
            idle = self._worker is None
            inline = idle and job.gaps_s(self._gap_s) <= self._inline_budget_s
            if not inline:
                self._queue.append(job)
                if self._worker is None:
                    self._worker = threading.Thread(
                        target=self._drain,
                        name="alpha-osk-key-injector",
                        daemon=True,
                    )
                    self._worker.start()
        if inline:
            with self._delivering:
                self._deliver(job, lead_gap=False)

    def cancel(self) -> None:
        """Drop every cancellable burst, queued or running.

        The running one stops at its next unit boundary, so a shift wrap
        or a surrogate pair is finished first.  Bursts that are not
        cancellable stay queued and are delivered.
        """
        with self._state:
            self._queue = deque(job for job in self._queue if not job.cancellable)
            if self._current is not None and self._current.cancellable:
                self._current.cancelled = True

    def shutdown(self, timeout: float) -> bool:
        """Stop typing: drop the text, deliver the rest, join the worker.

        The same drop as :meth:`cancel` (typed text, queued or running at
        its next unit boundary), then a wait for the worker to deliver
        what is left.  What is left is never dropped: a queued modifier
        *release* may balance a hold that has already reached the OS, and
        the bridge, whose flag went down when it queued the release, will
        not send another; dropping it would leave Shift held at exit
        (review of dbd1c9c).  Holds, chords and tapped keys are a few
        groups each, so the wait is short.

        Returns False if the worker was still alive when ``timeout``
        elapsed.  Afterwards the injector is idle, so a modifier release
        the caller sends next goes out inline.
        """
        self.cancel()
        return self.settle(timeout)

    def settle(self, timeout: float) -> bool:
        """Wait for the worker to drain (tests and shutdown only)."""
        with self._state:
            worker = self._worker
        if worker is None:
            return True
        worker.join(timeout)
        return not worker.is_alive()

    # ------------------------------------------------------------------ #
    #  Delivery
    # ------------------------------------------------------------------ #

    def _drain(self) -> None:
        with self._delivering:
            first = True
            while True:
                with self._state:
                    if not self._queue:
                        self._worker = None
                        return
                    job = self._queue.popleft()
                    self._current = job
                try:
                    self._deliver(job, lead_gap=not first)
                except Exception:
                    # Would otherwise vanish into the thread's default
                    # hook; the UI thread carries on either way.
                    self._log.warning("Background key burst failed", exc_info=True)
                finally:
                    with self._state:
                        self._current = None
                first = False

    def _deliver(self, job: _Job[T], *, lead_gap: bool) -> None:
        total_sent = 0
        expected = 0
        last_error = 0
        groups_sent = 0
        pause = 0.0
        for unit in job.units:
            if job.cancelled:
                self._log.debug(
                    "Key burst cancelled after %d/%d groups", groups_sent, job.group_count
                )
                break
            for item in unit:
                if not isinstance(item, list):
                    # A hold between two groups: replaces the gap before
                    # the next group, however the unit reached us.
                    pause = max(pause, item)
                    continue
                if groups_sent or lead_gap:
                    self._sleep(max(self._gap_s, pause))
                pause = 0.0
                sent, error = self._send(item)
                total_sent += sent
                expected += len(item)
                if error:
                    last_error = error
                groups_sent += 1
        if total_sent != expected:
            self._log.warning(
                "SendInput injected %d/%d events (error=%d). "
                "This may indicate insufficient privileges: consider "
                "UIAccess or running as admin.",
                total_sent,
                expected,
                last_error,
            )
