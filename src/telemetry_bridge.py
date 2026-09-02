"""
QML-facing wrapper around the opt-in telemetry pipeline.

``KeyboardBridge`` already carries seventeen distinct concerns (see
``docs/architecture/STRUCTURAL_REVIEW.md`` section 3.1); telemetry was one
of the cheapest to pull off because it already delegated everything real
to ``TelemetryClient`` and touched only three call sites in QML. This
module is the first cut along that seam - a measurement of what moving a
bolt-on feature surface off the bridge costs, not a commitment to move the
rest of them.

``TelemetryClient`` (in ``src/telemetry.py``) remains the source of truth
for the consent flag and everything else about *what* gets sent; this
class only owns the QObject plumbing QML needs to reach it - the hourly
submit timer and the three pass-through slots.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import QObject, QTimer, Slot

from .telemetry import TelemetryClient

_logger = logging.getLogger("TelemetryBridge")


class TelemetryBridge(QObject):
    """Owns the ``TelemetryClient`` and the hourly maybe-submit timer.

    Registered as the ``telemetry`` QML context property in
    ``keyboard_app.py``, alongside ``keyboard`` (the ``KeyboardBridge``).
    """

    def __init__(
        self,
        analytics_provider: Optional[Callable[[], Dict[str, Any]]],
        *,
        app_version: str = "",
        os_name: str = "",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)

        # Telemetry - opt-in, off by default.  Pulls lifetime counters
        # from the analytics dashboard's getter so there is one source
        # of truth.  Settings → Data & Privacy → Privacy controls it; the QTimer below
        # checks once an hour whether the weekly window has elapsed.
        # See docs/architecture/TELEMETRY.md (design) and docs/PRIVACY.md (user-facing).
        self._telemetry = TelemetryClient(
            analytics_provider=analytics_provider,
            app_version=app_version,
            os_name=os_name,
        )
        self._telemetry_timer = QTimer(self)
        # Hourly tick is plenty: maybe_submit() short-circuits unless
        # the 7-day window has elapsed, and we want the timer to be
        # cheap (a no-op call costs ~5 microseconds).
        self._telemetry_timer.setInterval(60 * 60 * 1000)
        self._telemetry_timer.timeout.connect(self._telemetry.maybe_submit)
        self._telemetry_timer.start()

    # --- Telemetry (opt-in usage stats) ---

    @Slot(result=bool)
    def getEnabled(self) -> bool:
        """QML reads this on Settings panel mount to render the toggle."""
        return self._telemetry.enabled

    @Slot(bool)
    def setEnabled(self, enabled: bool) -> None:
        """Toggle the opt-in telemetry pipeline.  Off → On generates a
        new anon_id and starts the weekly clock; On → Off clears the
        anon_id (so future opt-in cycles cannot be linked).  See
        docs/PRIVACY.md.
        """
        if enabled:
            self._telemetry.enable()
        else:
            self._telemetry.disable()
        _logger.info("Telemetry consent: %s", enabled)

    @Slot(result=bool)
    def forgetData(self) -> bool:
        """Ask the server to delete this user's contributed row.
        Triggered by the 'Delete my contributed data' button in
        Settings → Data & Privacy → Privacy.  Returns True if the request was sent.
        """
        return self._telemetry.forget()

    def shutdown(self) -> None:
        """Stop the submit timer and make a last-chance on-quit submit.

        Called from the app's quit sequence, before ``KeyboardBridge``'s
        own ``shutdown()``, to preserve the relative order the two steps
        had when they were both part of the bridge's ``shutdown()``.
        """
        try:
            self._telemetry_timer.stop()
        except RuntimeError:
            pass  # already deleted by Qt; harmless

        # Last-chance telemetry submit on the on-quit path.  Internally
        # gated on consent + endpoint + anon_id + a 60 s anti-spam
        # window, so calling it unconditionally here is safe.
        try:
            self._telemetry.submit_on_quit()
        except Exception as e:
            _logger.info("telemetry on-quit submit failed: %s", e)
