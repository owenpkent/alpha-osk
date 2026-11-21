"""
Pure-Python client for the virtual gamepad bus behind Xbox 360 emulation.

Adopted from ``owenpkent/Nimbus-Adaptive-Controller``, ``src/padbus_client.py``
at commit ``bcbf5dc`` (MIT), with formatting and typing adapted to this
repo's gates. It is this repo's copy from here on: Alpha-OSK is absorbing
Nimbus (see ``docs/roadmap/NIMBUS_INTEGRATION.md``), so changes are made here
and not mirrored back.

Speaks the ViGEmBus user-mode protocol (``BusShared.h``, MIT) directly over
``DeviceIoControl`` with ``ctypes``: no ``vgamepad`` wheel, no bundled
``ViGEmClient.dll``, no driver installer at ``pip`` time. This is phase 0 of
``docs/vision/PAD_BUS_FORK_PLAN.md`` in the Nimbus repository.

The bus is a root-enumerated KMDF driver whose children pretend to be USB
Xbox 360 pads, so that Microsoft's inbox ``xusb22.sys`` binds to them and
XInput sees a real controller. Everything user mode does is a handful of
buffered IOCTLs on the bus's device interface:

1. open the interface (``CreateFileW``, overlapped, like ``ViGEmClient.cpp``)
2. ``CHECK_VERSION``: the bus and this client agree on the protocol version
3. ``PLUGIN_TARGET`` with a serial number, then ``WAIT_DEVICE_READY``, which
   pends until the child has powered up
4. ``XUSB_SUBMIT_REPORT`` on every update, from a 12-byte report
5. ``UNPLUG_TARGET`` or, just as good, closing the handle: the bus owns the
   pad by file object and removes it when the handle goes away

The IOCTL codes are derived from ``CTL_CODE`` rather than hardcoded, and the
device interface GUIDs to probe are a list (``BUS_INTERFACES``) so that a
second bus driver speaking the same protocol is one more entry, not a
migration.

:class:`X360Pad` keeps the method names of ``vgamepad.VX360Gamepad`` for the
calls Nimbus makes (``left_joystick_float`` and friends, ``press_button``,
``release_button``, ``update``, ``reset``, ``XUSB_BUTTON``), so that swapping
the library in Nimbus's ``src/vigem_interface.py``, ``src/mouse_hider.py`` and
test harness was mechanical, and a fallback to vgamepad stays one import
away. The float-to-integer conversion is the same ``round()`` vgamepad used,
so the shaping probe's expected values do not move.

The handle is opened with ``FILE_FLAG_OVERLAPPED`` on purpose. A pended
IOCTL (``WAIT_DEVICE_READY``, ``REQUEST_NOTIFICATION``) on a synchronous
handle would block every other call on that handle, reports included, so
every call here carries its own ``OVERLAPPED`` and event and waits on that.

One thing the reference client does not do, and this one must: keep the pad
alive through the bus's replug gap. A serial plugged again soon after it was
released can land on a child that is still being removed; the ready wait
completes on it, and once it is gone every report fails with
``ERROR_NO_MORE_ITEMS`` (or, from ViGEmBus 1.22, ``ERROR_DEV_NOT_EXIST``: the
driver's ``STATUS_DEVICE_DOES_NOT_EXIST``) until a replacement starts. ViGEmClient reports
success for those failed submits, so under vgamepad the pad simply went
dead. Here ``_plug`` skips serials whose child device is still present and
proves readiness with real reports, and ``update`` retries a failed report
briefly and re-plugs the pad on another serial if it is really gone. The
measurements behind this are in ``docs/vision/PAD_BUS_FORK_PLAN.md``,
section 17, in the Nimbus repository.

Windows only. The module imports cleanly anywhere and enumerates nothing at
import: call :func:`bus_present` when you need to know whether a bus device
interface exists. (Nimbus had an import-time ``PADBUS_AVAILABLE`` constant;
it is deliberately gone here.) Verified against a real bus by
``tests/probe_padbus_windows.py`` in the Nimbus repository; the protocol
constants and packing by ``tests/test_padbus_client.py``.
"""

from __future__ import annotations

import ctypes
import enum
import sys
import threading
import time
import uuid
from typing import List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

# ---- the contract ------------------------------------------------------------------
#: ``VIGEM_COMMON_VERSION``: what ``CHECK_VERSION`` carries and the bus compares.
INTERFACE_VERSION = 0x0001

#: Device interfaces to probe, in order, as (name, GUID). A Nimbus-owned bus
#: (section 8 of PAD_BUS_FORK_PLAN.md in the Nimbus repo) goes first once it
#: exists; ViGEmBus is
#: ``GUID_DEVINTERFACE_BUSENUM_VIGEM`` from ``BusShared.h``.
BUS_INTERFACES: List[Tuple[str, str]] = [
    ("ViGEmBus", "{96E42B22-F5E9-42F8-B043-ED0F932F014F}"),
]

FILE_DEVICE_BUS_EXTENDER = 0x2A
METHOD_BUFFERED = 0
FILE_READ_DATA = 0x0001
FILE_WRITE_DATA = 0x0002
IOCTL_VIGEM_BASE = 0x801


def ctl_code(
    function: int,
    access: int,
    device_type: int = FILE_DEVICE_BUS_EXTENDER,
    method: int = METHOD_BUFFERED,
) -> int:
    """The WDK ``CTL_CODE`` macro, so the codes below are derived, not typed in."""
    return (device_type << 16) | (access << 14) | (function << 2) | method


IOCTL_VIGEM_PLUGIN_TARGET = ctl_code(IOCTL_VIGEM_BASE + 0x000, FILE_WRITE_DATA)
IOCTL_VIGEM_UNPLUG_TARGET = ctl_code(IOCTL_VIGEM_BASE + 0x001, FILE_WRITE_DATA)
IOCTL_VIGEM_CHECK_VERSION = ctl_code(IOCTL_VIGEM_BASE + 0x002, FILE_WRITE_DATA)
IOCTL_VIGEM_WAIT_DEVICE_READY = ctl_code(IOCTL_VIGEM_BASE + 0x003, FILE_WRITE_DATA)
IOCTL_XUSB_REQUEST_NOTIFICATION = ctl_code(
    IOCTL_VIGEM_BASE + 0x200, FILE_READ_DATA | FILE_WRITE_DATA
)
IOCTL_XUSB_SUBMIT_REPORT = ctl_code(IOCTL_VIGEM_BASE + 0x201, FILE_WRITE_DATA)
IOCTL_XUSB_GET_USER_INDEX = ctl_code(IOCTL_VIGEM_BASE + 0x206, FILE_READ_DATA | FILE_WRITE_DATA)

#: ``VIGEM_TARGET_TYPE.Xbox360Wired``.
TARGET_XBOX360_WIRED = 0
#: Microsoft's Xbox 360 wired pad. This is what ``xusb22.sys`` binds to; do not change it.
XBOX360_VENDOR_ID = 0x045E
XBOX360_PRODUCT_ID = 0x028E
#: Serial numbers are unique per bus and other apps' pads occupy some, so plugging
#: walks 1..MAX_SERIALS the way ViGEmClient does.
MAX_SERIALS = 16
#: How long ``update`` retries a report that finds no device object for the
#: pad before re-plugging it (the measured gap is tens of milliseconds).
REPORT_RETRY_S = 0.15

# Win32 ULONG is 32-bit everywhere, but ``c_ulong`` follows the host C ABI and
# widens to 64 bits on LP64 (Linux, macOS). Pinning the width keeps these ioctl
# structs byte-identical to the driver's on any machine the tests run on.
ULONG = ctypes.c_uint32
USHORT = ctypes.c_ushort
SHORT = ctypes.c_short
UCHAR = ctypes.c_ubyte


class VIGEM_CHECK_VERSION(ctypes.Structure):
    _fields_ = [("Size", ULONG), ("Version", ULONG)]


class VIGEM_PLUGIN_TARGET(ctypes.Structure):
    _fields_ = [
        ("Size", ULONG),
        ("SerialNo", ULONG),
        ("TargetType", ULONG),
        ("VendorId", USHORT),
        ("ProductId", USHORT),
    ]


class VIGEM_UNPLUG_TARGET(ctypes.Structure):
    _fields_ = [("Size", ULONG), ("SerialNo", ULONG)]


class VIGEM_WAIT_DEVICE_READY(ctypes.Structure):
    _fields_ = [("Size", ULONG), ("SerialNo", ULONG)]


class XUSB_REPORT(ctypes.Structure):
    """The 12-byte Xbox 360 input report, field for field ``XINPUT_GAMEPAD``."""

    _fields_ = [
        ("wButtons", USHORT),
        ("bLeftTrigger", UCHAR),
        ("bRightTrigger", UCHAR),
        ("sThumbLX", SHORT),
        ("sThumbLY", SHORT),
        ("sThumbRX", SHORT),
        ("sThumbRY", SHORT),
    ]


class XUSB_SUBMIT_REPORT(ctypes.Structure):
    _fields_ = [("Size", ULONG), ("SerialNo", ULONG), ("Report", XUSB_REPORT)]


class XUSB_REQUEST_NOTIFICATION(ctypes.Structure):
    _fields_ = [
        ("Size", ULONG),
        ("SerialNo", ULONG),
        ("LargeMotor", UCHAR),
        ("SmallMotor", UCHAR),
        ("LedNumber", UCHAR),
    ]


class XUSB_GET_USER_INDEX(ctypes.Structure):
    _fields_ = [("Size", ULONG), ("SerialNo", ULONG), ("UserIndex", ULONG)]


class XUSB_BUTTON(enum.IntFlag):
    """Button masks of ``wButtons``; the ``XINPUT_GAMEPAD_*`` values plus Guide."""

    XUSB_GAMEPAD_DPAD_UP = 0x0001
    XUSB_GAMEPAD_DPAD_DOWN = 0x0002
    XUSB_GAMEPAD_DPAD_LEFT = 0x0004
    XUSB_GAMEPAD_DPAD_RIGHT = 0x0008
    XUSB_GAMEPAD_START = 0x0010
    XUSB_GAMEPAD_BACK = 0x0020
    XUSB_GAMEPAD_LEFT_THUMB = 0x0040
    XUSB_GAMEPAD_RIGHT_THUMB = 0x0080
    XUSB_GAMEPAD_LEFT_SHOULDER = 0x0100
    XUSB_GAMEPAD_RIGHT_SHOULDER = 0x0200
    XUSB_GAMEPAD_GUIDE = 0x0400
    XUSB_GAMEPAD_A = 0x1000
    XUSB_GAMEPAD_B = 0x2000
    XUSB_GAMEPAD_X = 0x4000
    XUSB_GAMEPAD_Y = 0x8000


class GUID(ctypes.Structure):
    """A Windows GUID; ``bytes(GUID.from_string(s))`` equals ``uuid.UUID(s).bytes_le``."""

    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, text: str) -> "GUID":
        u = uuid.UUID(text)
        g = cls()
        g.Data1 = u.time_low
        g.Data2 = u.time_mid
        g.Data3 = u.time_hi_version
        g.Data4 = (ctypes.c_ubyte * 8)(*u.bytes[8:])
        return g


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", ctypes.c_uint32),
        ("OffsetHigh", ctypes.c_uint32),
        ("hEvent", ctypes.c_void_p),
    ]


class PadBusError(OSError):
    """Any failure talking to the bus; ``winerror`` is the Win32 code when there is one."""

    def __init__(self, message: str, winerror: int = 0) -> None:
        super().__init__(message)
        self.winerror = winerror


class PadBusNotFound(PadBusError):
    """No bus device interface is present, or none spoke our protocol version."""


class PadBusTimeout(PadBusError):
    """A pended IOCTL did not complete within the caller's timeout."""


def _clamp_i16(value: int) -> int:
    return max(-32768, min(32767, int(value)))


def _clamp_u8(value: int) -> int:
    return max(0, min(255, int(value)))


class X360ReportState:
    """
    The Xbox 360 report plus the vgamepad-shaped setters, with no I/O.

    :class:`X360Pad` adds the bus. Keeping the state separate lets the fast
    tests check the packing and the conversions without a driver.
    """

    def __init__(self) -> None:
        self.report = XUSB_REPORT()

    def reset(self) -> None:
        """Zero every field. Like vgamepad, this does not submit; call ``update``."""
        self.report = XUSB_REPORT()

    def press_button(self, button: int) -> None:
        self.report.wButtons = (self.report.wButtons | int(button)) & 0xFFFF

    def release_button(self, button: int) -> None:
        self.report.wButtons = self.report.wButtons & (~int(button) & 0xFFFF)

    def left_trigger(self, value: int) -> None:
        self.report.bLeftTrigger = _clamp_u8(value)

    def right_trigger(self, value: int) -> None:
        self.report.bRightTrigger = _clamp_u8(value)

    def left_trigger_float(self, value_float: float) -> None:
        self.left_trigger(round(value_float * 255))

    def right_trigger_float(self, value_float: float) -> None:
        self.right_trigger(round(value_float * 255))

    def left_joystick(self, x_value: int, y_value: int) -> None:
        self.report.sThumbLX = _clamp_i16(x_value)
        self.report.sThumbLY = _clamp_i16(y_value)

    def right_joystick(self, x_value: int, y_value: int) -> None:
        self.report.sThumbRX = _clamp_i16(x_value)
        self.report.sThumbRY = _clamp_i16(y_value)

    def left_joystick_float(self, x_value_float: float, y_value_float: float) -> None:
        self.left_joystick(round(x_value_float * 32767), round(y_value_float * 32767))

    def right_joystick_float(self, x_value_float: float, y_value_float: float) -> None:
        self.right_joystick(round(x_value_float * 32767), round(y_value_float * 32767))


# ---- Windows plumbing ----------------------------------------------------------------
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_ATTRIBUTE_NORMAL = 0x00000080
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_INVALID_FUNCTION = 1
ERROR_NO_MORE_ITEMS = 259
ERROR_DEV_NOT_EXIST = 55
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
CR_SUCCESS = 0
CM_GET_DEVICE_INTERFACE_LIST_PRESENT = 0
CM_GETIDLIST_FILTER_ENUMERATOR = 0x00000001
CM_GETIDLIST_FILTER_PRESENT = 0x00000100
#: The device id every Xbox 360 child of the bus enumerates under; its instance
#: id is the serial, ``%02d``. A present devnode here means that serial is taken,
#: by anyone's pad or by one that is still being removed.
XBOX360_DEVICE_ID = f"USB\\VID_{XBOX360_VENDOR_ID:04X}&PID_{XBOX360_PRODUCT_ID:04X}"

if IS_WINDOWS:
    from ctypes import wintypes

    # Annotated, not inferred: off Windows typeshed has no ``WinDLL``, so the
    # call degrades to ``Any`` there (see pointer.py for the same idiom).
    _kernel32: ctypes.CDLL = ctypes.WinDLL(  # type: ignore[attr-defined]
        "kernel32", use_last_error=True
    )
    _cfgmgr32: ctypes.CDLL = ctypes.WinDLL(  # type: ignore[attr-defined]
        "cfgmgr32", use_last_error=True
    )
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    _cfgmgr32.CM_Get_Device_Interface_List_SizeW.argtypes = [
        ctypes.POINTER(wintypes.ULONG),
        ctypes.POINTER(GUID),
        wintypes.LPCWSTR,
        wintypes.ULONG,
    ]
    _cfgmgr32.CM_Get_Device_Interface_List_SizeW.restype = wintypes.DWORD
    _cfgmgr32.CM_Get_Device_Interface_ListW.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    _cfgmgr32.CM_Get_Device_Interface_ListW.restype = wintypes.DWORD
    _cfgmgr32.CM_Get_Device_ID_List_SizeW.argtypes = [
        ctypes.POINTER(wintypes.ULONG),
        wintypes.LPCWSTR,
        wintypes.ULONG,
    ]
    _cfgmgr32.CM_Get_Device_ID_List_SizeW.restype = wintypes.DWORD
    _cfgmgr32.CM_Get_Device_ID_ListW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.ULONG,
        wintypes.ULONG,
    ]
    _cfgmgr32.CM_Get_Device_ID_ListW.restype = wintypes.DWORD

    _kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _kernel32.CreateFileW.restype = wintypes.HANDLE
    _kernel32.DeviceIoControl.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(OVERLAPPED),
    ]
    _kernel32.DeviceIoControl.restype = wintypes.BOOL
    _kernel32.GetOverlappedResult.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    ]
    _kernel32.GetOverlappedResult.restype = wintypes.BOOL
    _kernel32.CreateEventW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
    _kernel32.CancelIoEx.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def _is_missing_child(winerror: int) -> bool:
    """Whether the bus says the pad's device object is gone (retry or re-plug).

    ViGEmBus 1.21 reports ERROR_NO_MORE_ITEMS; 1.22 reports
    STATUS_DEVICE_DOES_NOT_EXIST, which Windows maps to ERROR_DEV_NOT_EXIST.
    """
    return winerror in (ERROR_NO_MORE_ITEMS, ERROR_DEV_NOT_EXIST)


def _last_error() -> int:
    """``ctypes.get_last_error``, which typeshed only declares on Windows."""
    return int(ctypes.get_last_error())  # type: ignore[attr-defined]


def _error_text(err: int) -> str:
    try:
        # ``FormatError`` is Windows only in typeshed, as above.
        return f"{ctypes.FormatError(err).strip()} (error {err})"  # type: ignore[attr-defined]
    except Exception:
        return f"error {err}"


def interface_paths(guid_text: str) -> List[str]:
    """Device paths of every present interface of the class ``guid_text``, via cfgmgr32."""
    if not IS_WINDOWS:
        return []
    guid = GUID.from_string(guid_text)
    length = wintypes.ULONG(0)
    cr = _cfgmgr32.CM_Get_Device_Interface_List_SizeW(
        ctypes.byref(length), ctypes.byref(guid), None, CM_GET_DEVICE_INTERFACE_LIST_PRESENT
    )
    if cr != CR_SUCCESS or length.value <= 1:
        return []
    buf = ctypes.create_unicode_buffer(length.value)
    cr = _cfgmgr32.CM_Get_Device_Interface_ListW(
        ctypes.byref(guid), None, buf, length.value, CM_GET_DEVICE_INTERFACE_LIST_PRESENT
    )
    if cr != CR_SUCCESS:
        return []
    return [p for p in "".join(buf[: length.value]).split("\x00") if p]


def present_child_serials() -> List[int]:
    """
    Serials of every Xbox 360 child device present on the machine right now,
    from the PnP device list: pads other programs own, and pads that were
    unplugged but are still being removed. Empty on an error or off Windows.
    """
    if not IS_WINDOWS:
        return []
    try:
        length = wintypes.ULONG(0)
        flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
        if (
            _cfgmgr32.CM_Get_Device_ID_List_SizeW(ctypes.byref(length), "USB", flags) != CR_SUCCESS
            or length.value <= 1
        ):
            return []
        buf = ctypes.create_unicode_buffer(length.value)
        if _cfgmgr32.CM_Get_Device_ID_ListW("USB", buf, length.value, flags) != CR_SUCCESS:
            return []
    except Exception:
        return []
    prefix = XBOX360_DEVICE_ID.upper() + "\\"
    serials: List[int] = []
    for device_id in "".join(buf[: length.value]).split("\x00"):
        if device_id.upper().startswith(prefix):
            instance = device_id.rsplit("\\", 1)[-1]
            try:
                serials.append(int(instance, 10))
            except ValueError:
                pass
    return serials


def find_bus(interfaces: List[Tuple[str, str]] = BUS_INTERFACES) -> Optional[Tuple[str, str]]:
    """First present bus as (name, device path), probing ``interfaces`` in order; None if none."""
    for name, guid in interfaces:
        try:
            paths = interface_paths(guid)
        except Exception:
            paths = []
        if paths:
            return name, paths[0]
    return None


def bus_present() -> bool:
    """True when a bus device interface exists. Enumeration only; nothing is opened."""
    return find_bus() is not None


class _BusHandle:
    """One overlapped handle to a bus device interface and the IOCTL plumbing on it."""

    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path
        handle = _kernel32.CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OVERLAPPED,
            None,
        )
        if handle is None or handle == INVALID_HANDLE_VALUE:
            err = _last_error()
            raise PadBusError(f"cannot open {name} at {path}: {_error_text(err)}", err)
        self.handle: Optional[int] = handle

    def ioctl(
        self,
        code: int,
        inbuf: Optional[ctypes.Structure] = None,
        outbuf: Optional[ctypes.Structure] = None,
        timeout_ms: Optional[int] = None,
    ) -> int:
        """
        One buffered IOCTL, waited to completion. Returns the bytes returned.

        Every call gets its own ``OVERLAPPED`` and event, so a pended request on
        one thread never blocks another. With ``timeout_ms`` the request is
        cancelled and reaped before :class:`PadBusTimeout` is raised, so the
        ``OVERLAPPED`` never outlives the I/O that references it.
        """
        if self.handle is None:
            raise PadBusError("bus handle is closed")
        event = _kernel32.CreateEventW(None, True, False, None)
        if not event:
            err = _last_error()
            raise PadBusError(f"CreateEvent failed: {_error_text(err)}", err)
        overlapped = OVERLAPPED()
        overlapped.hEvent = event
        returned = wintypes.DWORD(0)
        try:
            ok = _kernel32.DeviceIoControl(
                self.handle,
                code,
                ctypes.byref(inbuf) if inbuf is not None else None,
                ctypes.sizeof(inbuf) if inbuf is not None else 0,
                ctypes.byref(outbuf) if outbuf is not None else None,
                ctypes.sizeof(outbuf) if outbuf is not None else 0,
                ctypes.byref(returned),
                ctypes.byref(overlapped),
            )
            if ok:
                return returned.value
            err = _last_error()
            if err != ERROR_IO_PENDING:
                raise PadBusError(
                    f"IOCTL 0x{code:08X} on {self.name} failed: {_error_text(err)}", err
                )
            if timeout_ms is not None:
                if _kernel32.WaitForSingleObject(event, int(timeout_ms)) != WAIT_OBJECT_0:
                    _kernel32.CancelIoEx(self.handle, ctypes.byref(overlapped))
                    _kernel32.GetOverlappedResult(
                        self.handle, ctypes.byref(overlapped), ctypes.byref(returned), True
                    )
                    raise PadBusTimeout(
                        f"IOCTL 0x{code:08X} on {self.name} timed out after {timeout_ms} ms"
                    )
            if not _kernel32.GetOverlappedResult(
                self.handle, ctypes.byref(overlapped), ctypes.byref(returned), True
            ):
                err = _last_error()
                raise PadBusError(
                    f"IOCTL 0x{code:08X} on {self.name} failed: {_error_text(err)}", err
                )
            return returned.value
        finally:
            _kernel32.CloseHandle(event)

    def check_version(self, timeout_ms: int = 2000) -> None:
        req = VIGEM_CHECK_VERSION(
            Size=ctypes.sizeof(VIGEM_CHECK_VERSION), Version=INTERFACE_VERSION
        )
        self.ioctl(IOCTL_VIGEM_CHECK_VERSION, req, timeout_ms=timeout_ms)

    def close(self) -> None:
        if self.handle is not None:
            _kernel32.CloseHandle(self.handle)
            self.handle = None


def open_bus(interfaces: List[Tuple[str, str]] = BUS_INTERFACES) -> _BusHandle:
    """Open the first present bus whose ``CHECK_VERSION`` agrees with ours."""
    if not IS_WINDOWS:
        raise PadBusNotFound("the virtual gamepad bus is Windows only")
    last: Optional[PadBusError] = None
    for name, guid in interfaces:
        for path in interface_paths(guid):
            try:
                bus = _BusHandle(name, path)
            except PadBusError as exc:
                last = exc
                continue
            try:
                bus.check_version()
                return bus
            except PadBusError as exc:
                last = exc
                bus.close()
    detail = f": {last}" if last else ""
    raise PadBusNotFound(
        "no virtual gamepad bus found (is ViGEmBus installed?)" + detail,
        last.winerror if last else 0,
    )


class X360Pad(X360ReportState):
    """
    One virtual Xbox 360 pad on the bus.

    Opening plugs it in and waits for it to power up; ``update`` submits the
    current report; ``close`` (or the object going away, or the process
    dying) unplugs it. The setters are inherited and named as vgamepad named
    them.

    Parameters
    ----------
    serial : int, optional
        Force a bus serial number. By default the first free one from 1 up is
        taken, which is what ViGEmClient does.
    interfaces : list of (name, GUID), optional
        The buses to probe, in order. Defaults to ``BUS_INTERFACES``.
    ready_timeout_ms : int
        How long to wait for the child device to power up.
    """

    def __init__(
        self,
        serial: Optional[int] = None,
        interfaces: List[Tuple[str, str]] = BUS_INTERFACES,
        ready_timeout_ms: int = 5000,
    ) -> None:
        super().__init__()
        self._bus: Optional[_BusHandle] = open_bus(interfaces)
        self.bus_name = self._bus.name
        self.bus_path = self._bus.path
        self.serial = 0
        #: Serials skipped while plugging: those whose child device was still
        #: present (someone's pad, or one still being removed) and any the bus
        #: accepted but that failed the verify report. See ``_plug``.
        self.skipped_serials: List[int] = []
        #: Reports that went through only after a retry, and (old, new) serial
        #: pairs of re-plugs; both zero on a healthy bus. See ``update``.
        self.recovered_reports = 0
        self.replugs: List[Tuple[int, int]] = []
        self._ready_timeout_ms = ready_timeout_ms
        self._cancelled = threading.Event()
        try:
            self._plug(serial, ready_timeout_ms)
        except Exception:
            self._bus.close()
            self._bus = None
            raise

    # ---- lifecycle ----
    def _plug(
        self, serial: Optional[int], ready_timeout_ms: int, avoid: Tuple[int, ...] = ()
    ) -> None:
        assert self._bus is not None
        # Why this is more than "plug, wait ready": the bus finds a pad by
        # serial with WdfChildListRetrievePdo, which returns nothing while a
        # serial's device object is between an old child dying and its
        # replacement starting. Every report in that gap fails with
        # ERROR_NO_MORE_ITEMS (ERROR_DEV_NOT_EXIST on 1.22). The gap opens
        # when a serial released a moment ago is plugged again (by anyone: PnP removes the old child
        # asynchronously, and an XInput reader that has not re-polled keeps it
        # alive longer), and the ready wait can complete on the old child, so
        # it proves nothing then. Measured on ViGEmBus 1.21.442.0, 2026-09-09:
        # 5 to 12 dead pads in a 20-iteration close-and-plug storm with XInput
        # loaded, and the pad of a process started right after another one
        # unplugged. ViGEmClient's report submit returns success for every
        # failure but access denied, so vgamepad never surfaced this; the same
        # dead pads happened under it silently.
        #
        # Three layers: skip serials whose child device is still present (not
        # free, whoever owns it); prove readiness with real reports for up to
        # the ready timeout rather than trusting the ready wait; and, in
        # update(), heal a pad that dies later by re-plugging on another serial.
        if serial:
            candidates = [int(serial)]
        else:
            taken = set(present_child_serials())
            candidates = [s for s in range(1, MAX_SERIALS + 1) if s not in taken and s not in avoid]
            self.skipped_serials.extend(s for s in range(1, MAX_SERIALS + 1) if s in taken)
        last: Optional[PadBusError] = None
        for s in candidates:
            req = VIGEM_PLUGIN_TARGET(
                Size=ctypes.sizeof(VIGEM_PLUGIN_TARGET),
                SerialNo=s,
                TargetType=TARGET_XBOX360_WIRED,
                VendorId=XBOX360_VENDOR_ID,
                ProductId=XBOX360_PRODUCT_ID,
            )
            try:
                self._bus.ioctl(IOCTL_VIGEM_PLUGIN_TARGET, req, timeout_ms=ready_timeout_ms)
            except PadBusError as exc:
                last = exc
                continue
            self.serial = s
            wait = VIGEM_WAIT_DEVICE_READY(Size=ctypes.sizeof(VIGEM_WAIT_DEVICE_READY), SerialNo=s)
            try:
                self._bus.ioctl(IOCTL_VIGEM_WAIT_DEVICE_READY, wait, timeout_ms=ready_timeout_ms)
            except PadBusError as exc:
                if exc.winerror != ERROR_INVALID_FUNCTION:
                    self._unplug_quietly()
                    raise PadBusError(
                        f"pad {s} on {self.bus_name} plugged in but never became ready: {exc}",
                        exc.winerror,
                    )
                # A bus older than 1.17 has no WAIT_DEVICE_READY; there the
                # plug-in request itself pended until the child was up.
            # Prove it with reports: the current one (neutral on a new pad, the
            # live state on a re-plug) until it goes through, for at most the
            # ready timeout.
            deadline = time.monotonic() + ready_timeout_ms / 1000.0
            while True:
                try:
                    self._submit()
                    return
                except PadBusError as exc:
                    if not _is_missing_child(exc.winerror):
                        self._unplug_quietly()
                        raise
                    last = exc
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.005)
            self.skipped_serials.append(s)
            self._unplug_quietly()
        raise PadBusError(
            f"no working pad slot on {self.bus_name} in {len(candidates)} tries"
            + (f": {last}" if last else ""),
            last.winerror if last else 0,
        )

    def _unplug_quietly(self) -> None:
        try:
            self.unplug()
        except PadBusError:
            pass

    def _submit(self) -> None:
        assert self._bus is not None
        req = XUSB_SUBMIT_REPORT(Size=ctypes.sizeof(XUSB_SUBMIT_REPORT), SerialNo=self.serial)
        # Once cancelled the pad may only ever say "nothing pressed", however
        # late a stuck re-plug finishes: no path sends the held report.
        req.Report = XUSB_REPORT() if self._cancelled.is_set() else self.report
        self._bus.ioctl(IOCTL_XUSB_SUBMIT_REPORT, req, timeout_ms=1000)

    def cancel(self) -> None:
        """Make every later report neutral and start no new re-plug.

        Thread-safe and idempotent. Called from another thread while
        ``update`` may be waiting out a re-plug; ``close`` is unchanged.
        """
        self._cancelled.set()

    @property
    def plugged(self) -> bool:
        return self._bus is not None and self.serial != 0

    def update(self) -> None:
        """
        Submit the current report. Raises :class:`PadBusError` if the bus refuses it.

        A pad whose device object has gone (see ``_plug``) gets healed here:
        the report is retried for a short window, and if the pad is really
        gone it is re-plugged on another serial and the report resubmitted, so
        the game sees a brief reconnect instead of a stick that never moves
        again. ``recovered_reports`` and ``replugs`` count the two outcomes.
        Both the 259 and 55 codes mean "gone" (see ``_is_missing_child``).
        After ``cancel`` no new re-plug starts and only neutral is sent.
        """
        if self._bus is None or not self.serial:
            raise PadBusError("pad is not plugged in")
        try:
            self._submit()
            return
        except PadBusError as exc:
            if not _is_missing_child(exc.winerror):
                raise
            first = exc
        deadline = time.monotonic() + REPORT_RETRY_S
        while time.monotonic() < deadline:
            if self._cancelled.is_set():
                raise first
            time.sleep(0.005)
            try:
                self._submit()
                self.recovered_reports += 1
                return
            except PadBusError as exc:
                if not _is_missing_child(exc.winerror):
                    raise
        if self._cancelled.is_set():
            raise first
        old = self.serial
        self._unplug_quietly()
        self._plug(None, self._ready_timeout_ms, avoid=(old,))
        self.replugs.append((old, self.serial))

    def user_index(self) -> Optional[int]:
        """
        The XInput user index (0 to 3) the pad was given, or None if the bus did
        not answer. It is the LED slot, and it is what would tell a harness
        whether its pad is player one. ViGEmBus 1.21.442.0 accepts the query
        and writes nothing back (measured 2026-09-09), so on that bus this is
        always None and a caller has to find its pad through XInput instead.
        """
        if self._bus is None or not self.serial:
            return None
        sentinel = 0xFFFFFFFF
        req = XUSB_GET_USER_INDEX(
            Size=ctypes.sizeof(XUSB_GET_USER_INDEX), SerialNo=self.serial, UserIndex=sentinel
        )
        try:
            self._bus.ioctl(IOCTL_XUSB_GET_USER_INDEX, req, req, timeout_ms=1000)
        except PadBusError:
            return None
        if req.UserIndex == sentinel or req.UserIndex > 3:
            return None
        return int(req.UserIndex)

    def request_notification(
        self, timeout_ms: Optional[int] = None
    ) -> Optional[Tuple[int, int, int]]:
        """
        The next output report from the game's side of the pad, as
        ``(large_motor, small_motor, led_number)`` with the motors scaled to a
        byte; None on timeout. A fresh pad hands over a few queued by the
        driver stack's setup (four on ViGEmBus 1.21), after which the call
        pends until the game rumbles. Run it on its own thread if you want it;
        Nimbus does not use rumble today.
        """
        if self._bus is None or not self.serial:
            return None
        req = XUSB_REQUEST_NOTIFICATION(
            Size=ctypes.sizeof(XUSB_REQUEST_NOTIFICATION), SerialNo=self.serial
        )
        try:
            self._bus.ioctl(IOCTL_XUSB_REQUEST_NOTIFICATION, req, req, timeout_ms=timeout_ms)
        except PadBusTimeout:
            return None
        return int(req.LargeMotor), int(req.SmallMotor), int(req.LedNumber)

    def unplug(self) -> None:
        """Remove the pad from the bus now rather than when the handle closes."""
        if self._bus is None or not self.serial:
            return
        req = VIGEM_UNPLUG_TARGET(Size=ctypes.sizeof(VIGEM_UNPLUG_TARGET), SerialNo=self.serial)
        try:
            self._bus.ioctl(IOCTL_VIGEM_UNPLUG_TARGET, req, timeout_ms=2000)
        finally:
            self.serial = 0

    def close(self) -> None:
        """Unplug and close the bus handle. Safe to call twice."""
        if self._bus is None:
            return
        try:
            self.unplug()
        except PadBusError:
            pass
        self._bus.close()
        self._bus = None

    def __enter__(self) -> "X360Pad":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
