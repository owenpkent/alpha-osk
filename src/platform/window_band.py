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
    session (Wayland, no pyobjc) is a logged no-op.
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
            windows_window.raise_window_noactivate(int(window.winId()))
        elif CURRENT_PLATFORM == "linux":
            x11_window.raise_window(int(window.winId()))
        elif CURRENT_PLATFORM == "macos":
            macos_window.order_front(window)
    except Exception as exc:
        _logger.warning("Could not raise the keyboard: %s", exc)


def reapply_keyboard_band(window: QWindow, on: bool) -> None:
    """Re-assert the desired band after the window was shown again.

    With the setting off the restored keyboard is also raised above the
    applications it was hiding behind.
    """
    set_keyboard_topmost(window, on)
    if not on:
        raise_keyboard(window)


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
