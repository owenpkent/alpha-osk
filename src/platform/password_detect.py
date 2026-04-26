"""
Password Field Detection
=========================

Detects whether the currently focused UI element is a password field,
so the on-screen keyboard can suppress prediction and learning to
protect sensitive input.

Windows
-------
Uses the UI Automation COM interface (``IUIAutomation``) via ctypes to
query ``UIA_IsPasswordPropertyId`` on the focused element.  This works
for native Win32 controls **and** web browsers (Chrome, Edge, Firefox
all expose password state through UIA).  Falls back to the Win32
``EM_GETPASSWORDCHAR`` message if UIA fails to initialise.

Linux
-----
Uses AT-SPI 2 via PyGObject (``gi.repository.Atspi``).  An event
listener running on a dedicated GLib thread fires whenever focus
moves; we read each focused accessible's state set and cache whether
``STATE_PASSWORD_TEXT`` is set.  Works for GTK (GtkEntry with
``visibility=false``), Qt (QLineEdit in Password echo mode), and
browsers that expose accessibility metadata (Firefox, Chromium).

Requires ``gir1.2-atspi-2.0`` + ``python3-gi`` on the host *and* the
at-spi-2 bus running (both are default on Ubuntu GNOME).  If
PyGObject or the bus is absent, falls back silently to the null
detector and users can still toggle privacy mode manually.

Dependencies: ``ctypes`` (Windows), optional ``gi.repository.Atspi``
(Linux).
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any, Optional, Protocol

_logger = logging.getLogger("PasswordDetect")


class _Detector(Protocol):
    def check(self) -> bool: ...


# ====================================================================== #
#  Public API
# ====================================================================== #

_detector: Optional[_Detector] = None  # lazy-initialised


def is_password_field() -> bool:
    """Return True if the currently focused UI element is a password field."""
    global _detector
    if _detector is None:
        _detector = _create_detector()
    try:
        return _detector.check()
    except Exception:
        return False


def shutdown() -> None:
    """Release any resources held by the active detector.

    Safe to call multiple times.  The Windows UIA detector holds a COM
    interface pointer and a CoInitializeEx token that should be paired
    with a Release/CoUninitialize on graceful exit.  No-op for other
    detectors.
    """
    global _detector
    det = _detector
    _detector = None
    if det is None:
        return
    close = getattr(det, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            _logger.debug("Password detector close failed: %s", exc)


def _create_detector() -> _Detector:
    if sys.platform == "win32":
        det = _WindowsUIADetector()
        if det.available:
            _logger.info("Password detection: Windows UIA")
            return det
        _logger.info("Password detection: Windows Win32 fallback")
        return _WindowsWin32Detector()
    if sys.platform.startswith("linux"):
        det_linux = _LinuxATSPIDetector()
        if det_linux.available:
            _logger.info("Password detection: Linux AT-SPI")
            return det_linux
        _logger.info(
            "Password detection: AT-SPI unavailable — "
            "install python3-gi + gir1.2-atspi-2.0, or use the "
            "manual privacy toggle."
        )
        return _NullDetector()
    _logger.info("Password detection: not available on this platform")
    return _NullDetector()


# ====================================================================== #
#  Null detector (unsupported platforms)
# ====================================================================== #

class _NullDetector:
    def check(self) -> bool:
        return False


# ====================================================================== #
#  Linux — AT-SPI 2 via PyGObject
# ====================================================================== #

class _LinuxATSPIDetector:
    """Detect password fields via AT-SPI 2 focus events.

    AT-SPI delivers focus events through a GLib main loop.  Running
    that loop on the Qt thread would clash (two main loops, one set of
    dbus sockets), so we spawn a daemon thread that owns the loop and
    updates ``_is_password`` on each focus-change event.  The Qt side
    only ever reads the flag — no locking needed for a single bool on
    CPython.

    Event source of truth: ``object:state-changed:focused`` fires with
    the newly-focused accessible as ``event.source``.  We read its
    state set and look for ``Atspi.StateType.PASSWORD_TEXT``.  When
    focus leaves a password field we flip back to False on the next
    focus event (including refocus onto a non-password widget).
    """

    def __init__(self) -> None:
        self.available = False
        self._is_password = False
        # Declared as Any because the gi.repository module is resolved
        # at runtime only — mypy sees the None fallback and would flag
        # every attribute access as an error otherwise.
        self._Atspi: Any = None

        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # type: ignore[import-not-found]
        except Exception as exc:
            _logger.debug("AT-SPI import failed: %s", exc)
            return

        # Atspi.init() returns 0 on success, non-zero if the bus isn't
        # reachable (no at-spi-2-core running, or dbus session missing).
        try:
            init_rc = Atspi.init()
        except Exception as exc:
            _logger.debug("Atspi.init raised: %s", exc)
            return
        if init_rc not in (0, 1):  # 0 = first init, 1 = already inited
            _logger.debug("Atspi.init returned %s", init_rc)
            return

        self._Atspi = Atspi
        if not self._start_listener_thread():
            return
        self.available = True

    def _start_listener_thread(self) -> bool:
        """Spin up the GLib main loop owning the AT-SPI event listener."""
        import threading

        def run() -> None:
            Atspi = self._Atspi
            try:
                listener = Atspi.EventListener.new(self._on_focus_event)
                listener.register("object:state-changed:focused")
                # focus: is the legacy event name — some toolkits still emit it.
                listener.register("focus:")
                Atspi.event_main()  # blocks until Atspi.event_quit()
            except Exception as exc:
                _logger.debug("AT-SPI listener thread died: %s", exc)

        try:
            t = threading.Thread(
                target=run, name="atspi-focus", daemon=True
            )
            t.start()
            return True
        except Exception as exc:
            _logger.debug("Failed to start AT-SPI thread: %s", exc)
            return False

    def _on_focus_event(self, event: Any) -> None:
        """Focus-change callback. Runs on the GLib thread.

        For ``state-changed:focused`` events AT-SPI sets
        ``event.detail1 == 1`` when focus arrived on the source, and 0
        when focus left it.  Legacy ``focus:`` events only fire on
        arrival.  We only update state on arrival — a defocus event
        says nothing about the new focus target.
        """
        try:
            # Ignore defocus events; the next arrival event will tell us
            # what to do.
            name = getattr(event, "type", "") or ""
            if name.startswith("object:state-changed:focused"):
                if getattr(event, "detail1", 0) == 0:
                    return

            source = getattr(event, "source", None)
            if source is None:
                return
            state_set = source.get_state_set()
            self._is_password = bool(
                state_set.contains(self._Atspi.StateType.PASSWORD_TEXT)
            )
        except Exception:
            # Any per-event failure shouldn't kill the listener.
            pass

    def check(self) -> bool:
        return self._is_password


# ====================================================================== #
#  Windows — UI Automation via COM (ctypes)
# ====================================================================== #

if sys.platform == "win32":
    import ctypes.wintypes as wintypes

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", ctypes.c_ulong),
            ("Data2", ctypes.c_ushort),
            ("Data3", ctypes.c_ushort),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    # CUIAutomation {ff48dba4-60ef-4201-aa87-54103eef594e}
    _CLSID_CUIAutomation = _GUID(
        0xFF48DBA4, 0x60EF, 0x4201,
        (ctypes.c_ubyte * 8)(0xAA, 0x87, 0x54, 0x10, 0x3E, 0xEF, 0x59, 0x4E),
    )
    # IUIAutomation {30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}
    _IID_IUIAutomation = _GUID(
        0x30CBE57D, 0xD9D0, 0x452A,
        (ctypes.c_ubyte * 8)(0xAB, 0x13, 0x7A, 0xC5, 0xAC, 0x48, 0x25, 0xEE),
    )

    _UIA_IsPasswordPropertyId = 30019
    _VT_BOOL = 11

    class _VARIANT(ctypes.Structure):
        """Minimal COM VARIANT (24 bytes on 64-bit)."""
        _fields_ = [
            ("vt", ctypes.c_ushort),
            ("wReserved1", ctypes.c_ushort),
            ("wReserved2", ctypes.c_ushort),
            ("wReserved3", ctypes.c_ushort),
            ("val", ctypes.c_longlong),
            ("_pad", ctypes.c_longlong),
        ]

    def _vtable_func(obj: ctypes.c_void_p, index: int, restype: type,
                     *argtypes: type) -> Any:
        """Get a function pointer from a COM object's vtable."""
        vtable = ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0]
        fptr = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))[index]
        proto = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
        return proto(fptr)

    def _com_release(obj: ctypes.c_void_p) -> None:
        """Call IUnknown::Release (vtable index 2)."""
        if obj:
            try:
                _vtable_func(obj, 2, ctypes.c_ulong)(obj)
            except Exception:
                pass

    class _WindowsUIADetector:
        """Detect password fields via IUIAutomation COM interface."""

        def __init__(self) -> None:
            self.available = False
            self._automation = ctypes.c_void_p()
            # Did *we* initialise COM?  Only call CoUninitialize if so —
            # never tear down a context another caller set up.  S_FALSE
            # (1) means COM was already initialised on this thread, so
            # we did not take ownership and must not uninit.
            self._owns_com = False

            try:
                ole32 = ctypes.windll.ole32
                hr = ole32.CoInitializeEx(None, 0)
                if hr == 0:  # S_OK — we initialised it
                    self._owns_com = True
                elif hr != 1:  # S_FALSE = already inited; anything else fails
                    return

                hr = ole32.CoCreateInstance(
                    ctypes.byref(_CLSID_CUIAutomation), None, 1,  # CLSCTX_INPROC_SERVER
                    ctypes.byref(_IID_IUIAutomation),
                    ctypes.byref(self._automation),
                )
                if hr != 0 or not self._automation:
                    if self._owns_com:
                        ole32.CoUninitialize()
                        self._owns_com = False
                    return

                self.available = True
            except Exception as exc:
                _logger.debug("UIA init failed: %s", exc)
                if self._owns_com:
                    try:
                        ctypes.windll.ole32.CoUninitialize()
                    except Exception:
                        pass
                    self._owns_com = False

        def close(self) -> None:
            """Release the IUIAutomation interface and uninitialise COM."""
            if self._automation:
                _com_release(self._automation)
                self._automation = ctypes.c_void_p()
            self.available = False
            if self._owns_com:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass
                self._owns_com = False

        def check(self) -> bool:
            if not self.available or not self._automation:
                return False

            element = ctypes.c_void_p()
            try:
                # IUIAutomation::GetFocusedElement — vtable index 8
                get_focused = _vtable_func(
                    self._automation, 8,
                    ctypes.c_long, ctypes.POINTER(ctypes.c_void_p),
                )
                hr = get_focused(self._automation, ctypes.byref(element))
                if hr != 0 or not element:
                    return False

                # IUIAutomationElement::GetCurrentPropertyValue — vtable index 10
                variant = _VARIANT()
                get_prop = _vtable_func(
                    element, 10,
                    ctypes.c_long, ctypes.c_int, ctypes.POINTER(_VARIANT),
                )
                hr = get_prop(element, _UIA_IsPasswordPropertyId, ctypes.byref(variant))
                if hr != 0:
                    return False

                return bool(variant.vt == _VT_BOOL and variant.val != 0)

            except Exception:
                return False
            finally:
                _com_release(element)

    # ------------------------------------------------------------------
    #  Win32 fallback (EM_GETPASSWORDCHAR)
    # ------------------------------------------------------------------

    _EM_GETPASSWORDCHAR = 0x00D2

    class _GUITHREADINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("hwndActive", wintypes.HWND),
            ("hwndFocus", wintypes.HWND),
            ("hwndCapture", wintypes.HWND),
            ("hwndMenuOwner", wintypes.HWND),
            ("hwndMoveSize", wintypes.HWND),
            ("hwndCaret", wintypes.HWND),
            ("rcCaret", wintypes.RECT),
        ]

    class _WindowsWin32Detector:
        """Fallback: detect password edit controls via EM_GETPASSWORDCHAR."""

        def __init__(self) -> None:
            self._user32 = ctypes.windll.user32

        def check(self) -> bool:
            try:
                hwnd = self._user32.GetForegroundWindow()
                if not hwnd:
                    return False

                tid = self._user32.GetWindowThreadProcessId(hwnd, None)
                if not tid:
                    return False

                info = _GUITHREADINFO()
                info.cbSize = ctypes.sizeof(info)
                if not self._user32.GetGUIThreadInfo(tid, ctypes.byref(info)):
                    return False

                focused = info.hwndFocus
                if not focused:
                    return False

                # EM_GETPASSWORDCHAR returns the mask char (e.g. '*') or 0
                result: int = self._user32.SendMessageW(
                    focused, _EM_GETPASSWORDCHAR, 0, 0
                )
                return result != 0

            except Exception:
                return False
