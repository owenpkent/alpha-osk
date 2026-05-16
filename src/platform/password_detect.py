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

macOS
-----
Uses AXUIElement via pyobjc's ``ApplicationServices`` bindings.  The
focused-element walk goes through the *frontmost application* rather
than the system-wide AX element — ``kAXFocusedUIElementAttribute`` on
``AXUIElementCreateSystemWide()`` returns ``kAXErrorCannotComplete``
in practice on modern macOS, so we use ``NSWorkspace
.frontmostApplication()`` → ``AXUIElementCreateApplication(pid)`` →
``kAXFocusedUIElementAttribute`` instead.  Password fields surface as
``kAXSubroleAttribute == "AXSecureTextField"`` (canonical) or
``kAXRoleAttribute == "AXSecureTextField"`` (fallback some apps use).
Works for Cocoa ``NSSecureTextField``, WebKit / Chromium
``<input type="password">``, and most Electron apps that expose
accessibility metadata.

Requires the Accessibility TCC grant — the same one the key
synthesizer needs.  Without it ``AXIsProcessTrusted()`` returns False
and the detector falls back to the null detector; users can still
toggle privacy mode manually.

Dependencies: ``ctypes`` (Windows), optional ``gi.repository.Atspi``
(Linux), optional ``pyobjc-framework-ApplicationServices`` (macOS).
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any, Optional, Protocol

_logger = logging.getLogger("PasswordDetect")


class _Detector(Protocol):
    """Platform-specific detector contract.

    A single method ``check()`` returns ``True`` iff the currently
    focused UI element is a password field.  Implementations are
    expected to be cheap (called from the keystroke hot path), to
    return ``False`` on any internal failure rather than raise, and
    to optionally expose a ``close()`` for resource cleanup at
    shutdown.
    """

    def check(self) -> bool:
        """Return ``True`` iff focus is currently on a password field."""
        ...


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
    if sys.platform == "darwin":
        det_mac = _MacOSAXDetector()
        if det_mac.available:
            _logger.info("Password detection: macOS AXUIElement")
            return det_mac
        _logger.info(
            "Password detection: macOS AX unavailable — "
            "grant Accessibility in System Settings and restart, or "
            "use the manual privacy toggle in the title bar."
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
#  macOS — AXUIElement focus walk via pyobjc
# ====================================================================== #

class _MacOSAXDetector:
    """Detect password fields by walking the focused app's AX tree.

    The canonical macOS signal for a password field is
    ``kAXSubroleAttribute == "AXSecureTextField"`` on the focused
    element.  Cocoa's ``NSSecureTextField`` surfaces it directly;
    WebKit (Safari, Mail compose) and Chromium (Chrome, Edge, Brave)
    map ``<input type="password">`` to the same subrole.

    Why we go through the focused *application* rather than the
    system-wide AX element: ``kAXFocusedUIElementAttribute`` on
    ``AXUIElementCreateSystemWide()`` returns
    ``kAXErrorCannotComplete (-25204)`` in practice — confirmed in dev
    on macOS 14/15.  The supported path is ``NSWorkspace
    .frontmostApplication()`` → ``AXUIElementCreateApplication(pid)``
    → ``kAXFocusedUIElementAttribute``, which gives us the focused
    element in *that* app's accessibility tree.  See the API probe
    in commit history if you want to verify.

    Required runtime grant: Accessibility (TCC).  The same grant the
    key synthesizer needs.  If ``AXIsProcessTrusted()`` is False we
    set ``available = False`` and ``_create_detector`` falls back to
    the null detector — the manual privacy toggle in the title bar
    still works.

    Caching: each ``check()`` builds an ``AXUIElementCreateApplication``
    handle for the current frontmost pid.  Cheap (microseconds), but
    not free, so we cache by pid and invalidate when frontmost
    changes.

    Threading: called from the Qt main thread (the bridge polls every
    200 ms + per-keystroke).  The AX APIs are thread-safe but the
    pyobjc bridge isn't tested across threads — keep all calls on the
    main thread.
    """

    def __init__(self) -> None:
        self.available = False
        self._AX: Any = None
        self._NSWorkspace: Any = None
        # Cache: (pid, AXUIElement-for-that-app).  Invalidated when
        # frontmost pid changes.
        self._cached_app_pid: Optional[int] = None
        self._cached_app_elem: Any = None

        try:
            import ApplicationServices  # type: ignore[import-not-found]
        except ImportError as exc:
            _logger.debug("ApplicationServices import failed: %s", exc)
            return
        try:
            from AppKit import NSWorkspace  # type: ignore[import-not-found]
        except ImportError as exc:
            _logger.debug("AppKit import failed: %s", exc)
            return

        # AX queries silently return -25211 (kAXErrorAPIDisabled) for
        # untrusted processes.  Probe once at init so we can log the
        # actionable hint instead of letting every check() fail.
        if not ApplicationServices.AXIsProcessTrusted():
            _logger.debug(
                "AXIsProcessTrusted=False — macOS password auto-detect "
                "needs the Accessibility TCC grant (same one the key "
                "synthesizer needs)"
            )
            return

        self._AX = ApplicationServices
        self._NSWorkspace = NSWorkspace
        self.available = True

    def _get_focused_app_elem(self) -> Any:
        """Return an ``AXUIElement`` for the currently-frontmost app.

        Caches by pid: if the frontmost app hasn't changed since the
        last call, returns the cached element.  Otherwise creates a
        fresh one via ``AXUIElementCreateApplication(pid)``.  Returns
        None if no app is frontmost.
        """
        app = self._NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        pid = int(app.processIdentifier())
        if pid != self._cached_app_pid:
            self._cached_app_elem = self._AX.AXUIElementCreateApplication(pid)
            self._cached_app_pid = pid
        return self._cached_app_elem

    def check(self) -> bool:
        if not self.available:
            return False
        try:
            app_elem = self._get_focused_app_elem()
            if app_elem is None:
                return False
            AX = self._AX
            err, focused = AX.AXUIElementCopyAttributeValue(
                app_elem, AX.kAXFocusedUIElementAttribute, None,
            )
            if err != 0 or focused is None:
                return False
            # The canonical password signal is the subrole.  We check
            # the role too as a belt-and-braces fallback: some apps
            # report ``AXSecureTextField`` as the role directly with no
            # subrole.  Either match means "treat as password field".
            err_s, subrole = AX.AXUIElementCopyAttributeValue(
                focused, AX.kAXSubroleAttribute, None,
            )
            if err_s == 0 and subrole == "AXSecureTextField":
                return True
            err_r, role = AX.AXUIElementCopyAttributeValue(
                focused, AX.kAXRoleAttribute, None,
            )
            if err_r == 0 and role == "AXSecureTextField":
                return True
            return False
        except Exception as exc:
            # AX queries can throw for windows that are mid-teardown
            # (e.g. the user just closed the focused window).  Treat
            # as "not a password field" so privacy mode releases
            # rather than getting stuck on.
            _logger.debug("AX check failed: %s", exc)
            return False


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
                # Best-effort teardown: a failing Release during shutdown
                # (e.g. process exit, COM apartment torn down underneath
                # us) must never raise into the caller.
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
                        # Init already failed; CoUninitialize is best-effort
                        # cleanup and must not mask the original error.
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
                    # Shutdown path: swallow so a teardown failure can't
                    # crash the host process during exit.
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
