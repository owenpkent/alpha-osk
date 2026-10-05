"""The keyboard window's Z-order band: the *Always on Top* setting.

Everything here changes the window's stacking natively and never its Qt
flags.  ``setFlags`` on a shown window makes Qt rebuild the native window,
which undoes the extended styles and moves the window; both flag sets keep
``WindowStaysOnTopHint`` and only the band changes, so the setting can flip
at runtime.  See *Always on Top* in ``docs/architecture/WINDOW_CHROME.md``.

The keyboard never takes focus, so clicking it does not raise it.  With the
setting off a buried keyboard would be unreachable, so :class:`RaiseOnPressFilter`
raises it, without activating it, on any mouse press.

Per-platform calls are dispatched through :data:`CURRENT_PLATFORM` the way
``keyboard_app._apply_window_flags`` dispatches.
"""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QWindow

from . import CURRENT_PLATFORM, macos_window, windows_window, x11_window

_logger = logging.getLogger("window_band")


def set_keyboard_topmost(window: QWindow, on: bool) -> None:
    """Put ``window`` in the always-on-top band (``on``) or the ordinary one.

    Never activates the window.  Safe to call at any time; an unsupported
    session (Wayland, no pyobjc) is a logged no-op.  ``window`` is usually
    the keyboard; the floating pickers go through it too when the
    keyboard's own band change has taken them along
    (``keyboard_app._restore_floating_bands``).
    """
    try:
        if CURRENT_PLATFORM == "windows":
            windows_window.set_window_band(int(window.winId()), on)
        elif CURRENT_PLATFORM == "linux":
            if not x11_window.set_window_above(int(window.winId()), on):
                _logger.info("Always on Top: no X11 session, left to the window manager")
        elif CURRENT_PLATFORM == "macos":
            macos_window.set_window_level(window, on)
    except Exception as exc:
        _logger.warning("Could not change the keyboard's Z-order band: %s", exc)


def raise_keyboard(window: QWindow) -> None:
    """Raise ``window`` to the top of its band without activating it.

    Deliberately never ``SetForegroundWindow`` / ``makeKey``: the keyboard
    must not take focus from the app being typed into.
    """
    try:
        if CURRENT_PLATFORM == "windows":
            windows_window.bring_to_front_noactivate(int(window.winId()))
        elif CURRENT_PLATFORM == "linux":
            x11_window.raise_window(int(window.winId()))
        elif CURRENT_PLATFORM == "macos":
            macos_window.order_front(window)
    except Exception as exc:
        _logger.warning("Could not raise the keyboard: %s", exc)


class RaiseOnPressFilter(QObject):
    """Raise the keyboard on a mouse press while Always on Top is off.

    Always returns False: the press must still reach the QML key, so the
    keystroke is never delayed or swallowed.  The raise is a single
    non-activating call.
    """

    def __init__(
        self,
        window: QWindow,
        *,
        always_on_top: Callable[[], bool],
        raise_fn: Callable[[QWindow], object] = raise_keyboard,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._always_on_top = always_on_top
        self._raise = raise_fn

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        try:
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and watched is self._window
                and not self._always_on_top()
            ):
                self._raise(self._window)
        except Exception as exc:
            _logger.debug("Raise-on-press failed: %s", exc)
        return False


def _next_turn(fn: Callable[[], object]) -> None:
    from PySide6.QtCore import QTimer

    QTimer.singleShot(0, fn)


class ReassertOnExposeFilter(QObject):
    """Re-assert the saved band every time the window is mapped again (X11).

    Keeping ``WindowStaysOnTopHint`` while the setting is off is what lets
    the band flip at runtime, but on X11 Qt reads that flag back on every
    ``show()`` of a top-level window and writes ``_NET_WM_STATE_ABOVE``
    onto it while it is still unmapped.  So any hide and re-show (the tray
    hides a tucked keyboard and ``showNormal()`` brings it back) put the
    keyboard on top again while the setting still said off.  Windows has
    the quiet-restore hook for the same job; X11 had nothing.

    The trigger is the window becoming *exposed*, not ``show()``: an EWMH
    state change is a client message that only a window manager already
    managing the window acts on, and Qt reports exposure only once the
    window is mapped.  ``isExposed()`` is already updated when the event
    arrives, so each unexposed-to-exposed transition fires once.  The
    re-assert runs on the next event-loop turn so the expose itself is
    handled first.  Always returns False.
    """

    def __init__(
        self,
        window: QWindow,
        *,
        after_map: Callable[[], object],
        defer: Callable[[Callable[[], object]], None] = _next_turn,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._after_map = after_map
        self._defer = defer
        self._exposed = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        try:
            if watched is self._window and event.type() == QEvent.Type.Expose:
                exposed = bool(self._window.isExposed())
                if exposed and not self._exposed:
                    self._defer(self._after_map)
                self._exposed = exposed
        except Exception as exc:
            _logger.debug("Band re-assert on expose failed: %s", exc)
        return False
