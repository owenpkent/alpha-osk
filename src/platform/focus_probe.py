"""
Focus probe: one background thread that asks "what has focus?"
================================================================

The keyboard thread used to ask UI Automation directly, twice: the
password check (every 200 ms and on every keystroke) and the
focused-element check (every 250 ms).  Both are cross-process COM
calls, and a target that is slow to answer (Thunderbird was measured at
317 ms) stalls the keystroke that asked.  This module moves the asking
onto a thread of its own.

The probe owns the detector (and so the COM apartment and the UIA
connection) for the thread's whole life; nothing on the keyboard thread
touches UIA again.  It takes one :class:`FocusReading` per tick, about
five times a second, and one more whenever :meth:`FocusProbe.request`
asks.  Every reading records the foreground window it was taken under
and the time it was taken, so a consumer can refuse one that belongs to
another window or predates something it already knows.

The contract on the keyboard side:

- :meth:`FocusProbe.latest` is the stored answer; the polls read it.
- :meth:`FocusProbe.request` asks for a fresh one and waits a bounded
  time for it.  The wait is what is bounded, not the probe: a stalled
  UIA call cannot be interrupted, so while the target stalls every
  request inside the stall comes back ``None`` and the reading lands
  late, through ``on_reading``, once the call returns.
- ``on_reading`` runs on the probe thread for every reading taken.  The
  bridge turns it into a queued Qt signal; nothing here knows about Qt.

The detector factory runs on the probe thread, so a detector that
initialises COM does so in the apartment it will be used from.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol

_logger = logging.getLogger("FocusProbe")

# How often the probe reads on its own, between requests.
DEFAULT_INTERVAL_S = 0.2
# How long ``start`` waits for the thread to build its detector.  COM
# initialisation and ``CoCreateInstance`` take milliseconds; the bound
# is for a machine where they do not.
START_TIMEOUT_S = 2.0
# How long ``stop`` waits for the thread.  Bounded because the thread may
# be inside a UIA call that the target is slow to answer; the daemon
# thread then dies with the process, as it would have anyway.
STOP_TIMEOUT_S = 0.5


class FocusDetector(Protocol):
    """What the probe asks of the platform detector it is handed."""

    def check(self) -> bool: ...


@dataclass(frozen=True)
class FocusReading:
    """One answer to "what has focus?".

    ``hwnd`` is the foreground window the reading was taken under (0 where
    the platform has none).  ``token`` identifies the focused element, or
    is ``None`` when the detector could not say.  ``is_password`` is
    ``False`` on any failure (fail open, as every detector does).
    ``read_at`` is ``time.monotonic()`` just before the detector was
    asked, so a consumer comparing it against its own clock knows what
    the reading could and could not have seen.  ``seq`` counts readings
    from 1.
    """

    hwnd: int
    token: Optional[str]
    is_password: bool
    read_at: float
    seq: int


def _read_with(detector: Any) -> tuple[Optional[str], bool]:
    """Ask one detector for the focused element's token and password state.

    A detector that offers ``read_focus`` answers both from one
    ``GetFocusedElement``; anything else is asked twice through the
    single-purpose methods, ``focus_token`` being optional.
    """
    read_focus = getattr(detector, "read_focus", None)
    if callable(read_focus):
        both: Any = read_focus()
        token, is_password = both
        return (token if isinstance(token, str) else None, bool(is_password))
    token = None
    focus_token = getattr(detector, "focus_token", None)
    if callable(focus_token):
        try:
            got = focus_token()
        except Exception:
            got = None
        token = got if isinstance(got, str) else None
    try:
        is_password = bool(detector.check())
    except Exception:
        is_password = False
    return token, is_password


class FocusProbe:
    """A thread that owns a focus detector and publishes its readings.

    ``detector_factory`` is called once, on the probe thread; it may
    return ``None`` to say the platform has nothing to offer, in which
    case ``start`` returns ``False`` and the thread exits.  ``foreground``
    returns the foreground window handle (0 for none).  ``on_reading``
    is called on the probe thread with every reading taken.
    """

    def __init__(
        self,
        detector_factory: Callable[[], Optional[Any]],
        *,
        foreground: Callable[[], int],
        on_reading: Optional[Callable[[FocusReading], None]] = None,
        interval_s: float = DEFAULT_INTERVAL_S,
        name: str = "alpha-osk-focus-probe",
    ) -> None:
        self._factory = detector_factory
        self._foreground = foreground
        self._on_reading = on_reading
        self._interval_s = interval_s
        self._name = name
        self._cond = threading.Condition()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._started = threading.Event()
        self._latest: Optional[FocusReading] = None
        # Reads begun and reads completed, both under ``_cond``.  A request
        # waits for a read that *begins* after it: one already in flight
        # captured the focused element before the request was made, and
        # if focus moved to a password field in the meantime its answer
        # is the old field's, inside the budget and wrong.
        self._begun = 0
        self._seq = 0
        self._available = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    @property
    def available(self) -> bool:
        """Did the thread build a detector?  Meaningful after ``start``."""
        return self._available

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self, timeout_s: float = START_TIMEOUT_S) -> bool:
        """Start the thread and wait for its detector; False if there is none."""
        if self._thread is not None:
            return self._available
        self._thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread.start()
        if not self._started.wait(timeout_s):
            _logger.warning("Focus probe did not start within %.1fs", timeout_s)
            return False
        return self._available

    def stop(self, timeout_s: float = STOP_TIMEOUT_S) -> None:
        """Ask the thread to finish and wait a bounded time for it.

        The detector is closed on the probe thread, by the loop's own
        exit, so COM is released from the apartment that created it.
        After this no reading is delivered, even if the thread is still
        inside a call that outlives the wait.
        """
        self._stop.set()
        self._on_reading = None
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout_s)
            if thread.is_alive():
                _logger.warning("Focus probe did not stop within %.2fs", timeout_s)

    # ------------------------------------------------------------------ #
    #  Readings
    # ------------------------------------------------------------------ #

    def latest(self) -> Optional[FocusReading]:
        """The most recent reading, or None before the first."""
        with self._cond:
            return self._latest

    def request(self, timeout_s: float) -> Optional[FocusReading]:
        """Take a reading now and wait up to ``timeout_s`` for it.

        Returns a reading from a read that *began* after this call, or
        ``None`` when none arrived in time (the thread is stalled in a
        call, or stopped).  A late reading still reaches ``on_reading``
        and ``latest``.
        """
        if not self._available or self._stop.is_set():
            return None
        with self._cond:
            # The next read to begin, not the next to complete: a read in
            # flight predates this request (review of PR #246, P1).
            want = self._begun + 1
            self._wake.set()
            deadline = time.monotonic() + timeout_s
            while self._seq < want:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._stop.is_set():
                    return None
                self._cond.wait(remaining)
            return self._latest

    # ------------------------------------------------------------------ #
    #  The thread
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        detector: Optional[Any] = None
        try:
            try:
                detector = self._factory()
            except Exception:
                _logger.exception("Focus probe could not build its detector")
                detector = None
            self._available = detector is not None
            self._started.set()
            if detector is None:
                return
            while not self._stop.is_set():
                # Cleared *before* the read: a request that arrives while
                # the reading is being taken asked for the one after it
                # (its ``want`` is past this seq), and setting the flag
                # again makes the wait below return at once.  Cleared
                # after, that request would sleep a whole interval.
                self._wake.clear()
                self._take(detector)
                self._wake.wait(self._interval_s)
        finally:
            self._started.set()
            close = getattr(detector, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    _logger.debug("Focus probe detector close failed", exc_info=True)

    def _take(self, detector: Any) -> None:
        with self._cond:
            self._begun += 1
        read_at = time.monotonic()
        try:
            hwnd = int(self._foreground() or 0)
        except Exception:
            hwnd = 0
        try:
            token, is_password = _read_with(detector)
        except Exception:
            token, is_password = None, False
        with self._cond:
            self._seq += 1
            reading = FocusReading(hwnd, token, is_password, read_at, self._seq)
            self._latest = reading
            self._cond.notify_all()
        on_reading = self._on_reading
        if on_reading is not None and not self._stop.is_set():
            try:
                on_reading(reading)
            except Exception:
                _logger.debug("Focus probe listener failed", exc_info=True)
