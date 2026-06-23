"""X11 window-type control for the off-screen "Tuck away" affordance.

On GNOME/Mutter (and most X11 WMs) a normal *managed* window is force-clamped
on-screen: a programmatic move to a negative coordinate is snapped back so the
whole window stays inside the work area (verified empirically on Mutter 46 —
``setX(-400)`` reads back as the work-area inset, never off-screen). That is why
the keyboard cannot be dragged off a screen edge via the reliable manual-move
path.

The one window *type* that Mutter exempts from this clamp while still honouring
always-on-top (``_NET_WM_STATE_ABOVE``) and no-focus (``InputHint=False``) is
``_NET_WM_WINDOW_TYPE_DOCK``. Promoting the window to DOCK lets it travel fully
off-screen; the cost is that DOCK drops the taskbar entry and makes
``showMinimized()`` inert. So the app only promotes to DOCK *while the window is
parked off-screen* and reverts to ``_NET_WM_WINDOW_TYPE_NORMAL`` (taskbar +
minimise restored) the moment it comes back on-screen. See the "Tuck away"
notes in ``docs/architecture/GOTCHAS.md``.

PySide6 6.x has no ``QtX11Extras`` and no Qt enum maps to the DOCK atom, so we
set ``_NET_WM_WINDOW_TYPE`` on the live X11 window id via a thin ctypes binding
to ``libX11``. Everything here is a safe no-op off X11 (Windows/macOS aren't
clamped and move off-screen via the plain manual path; Wayland ignores the
property and has no clamp-escape — Onboard likewise disables its override path
on Wayland), so callers never need to special-case the platform.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys

_logger = logging.getLogger("x11_window")

# ICCCM/Xlib constants.
_XA_ATOM = 4
_PROP_MODE_REPLACE = 0

_PROP = b"_NET_WM_WINDOW_TYPE"
_TYPE_DOCK = b"_NET_WM_WINDOW_TYPE_DOCK"
_TYPE_NORMAL = b"_NET_WM_WINDOW_TYPE_NORMAL"

# Cached, process-wide. The display connection here is intentionally separate
# from Qt's own xcb connection — we only ever XChangeProperty + XFlush, which is
# self-contained.
_xlib: "ctypes.CDLL | None" = None
_dpy: "int | None" = None
_atom_cache: "dict[bytes, int]" = {}


def is_x11() -> bool:
    """True only on a Linux X11 session where the DOCK clamp-escape applies."""
    return (
        sys.platform.startswith("linux")
        and os.environ.get("XDG_SESSION_TYPE", "").lower() == "x11"
        and bool(os.environ.get("DISPLAY"))
    )


def _ensure_display() -> "int | None":
    """Open (once) and return the libX11 display handle, or None on failure."""
    global _xlib, _dpy
    if _dpy is not None:
        return _dpy
    try:
        xlib = ctypes.CDLL("libX11.so.6")
        # Pin argtypes/restype so 64-bit handles/atoms aren't truncated to int.
        xlib.XOpenDisplay.restype = ctypes.c_void_p
        xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        xlib.XInternAtom.restype = ctypes.c_ulong
        xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        xlib.XChangeProperty.restype = ctypes.c_int
        xlib.XChangeProperty.argtypes = [
            ctypes.c_void_p,  # display
            ctypes.c_ulong,   # window (XID)
            ctypes.c_ulong,   # property atom
            ctypes.c_ulong,   # type atom
            ctypes.c_int,     # format
            ctypes.c_int,     # mode
            ctypes.c_void_p,  # data
            ctypes.c_int,     # nelements
        ]
        xlib.XFlush.restype = ctypes.c_int
        xlib.XFlush.argtypes = [ctypes.c_void_p]

        disp_name = os.environ.get("DISPLAY", "").encode() or None
        dpy = xlib.XOpenDisplay(disp_name)
        if not dpy:
            _logger.debug("XOpenDisplay returned NULL; tuck disabled")
            return None
        _xlib = xlib
        _dpy = dpy
        return _dpy
    except Exception:  # pragma: no cover - depends on host libX11
        _logger.debug("libX11 binding failed; tuck disabled", exc_info=True)
        return None


def _atom(name: bytes) -> "int | None":
    if name in _atom_cache:
        return _atom_cache[name]
    dpy = _ensure_display()
    if dpy is None or _xlib is None:
        return None
    atom = int(_xlib.XInternAtom(dpy, name, 0))  # only_if_exists=False
    _atom_cache[name] = atom
    return atom


def set_window_dock(win_id: int, dock: bool) -> bool:
    """Set a window's ``_NET_WM_WINDOW_TYPE`` to DOCK (``dock=True``) or NORMAL.

    ``win_id`` is the native X11 window id (``int(qwindow.winId())``). Returns
    True if the property was written, False on any non-X11 session, missing
    window id, or libX11 failure. Always safe to call.
    """
    if not is_x11() or not win_id:
        return False
    dpy = _ensure_display()
    if dpy is None or _xlib is None:
        return False
    prop = _atom(_PROP)
    target = _atom(_TYPE_DOCK if dock else _TYPE_NORMAL)
    if prop is None or target is None:
        return False
    try:
        value = ctypes.c_ulong(target)
        _xlib.XChangeProperty(
            dpy,
            ctypes.c_ulong(win_id),
            ctypes.c_ulong(prop),
            ctypes.c_ulong(_XA_ATOM),
            32,
            _PROP_MODE_REPLACE,
            ctypes.byref(value),
            1,
        )
        _xlib.XFlush(dpy)
        return True
    except Exception:  # pragma: no cover - depends on host libX11
        _logger.debug("XChangeProperty failed for win_id=%s", win_id, exc_info=True)
        return False
