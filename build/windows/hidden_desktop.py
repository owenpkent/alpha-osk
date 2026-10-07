"""Run a program on a desktop nobody is looking at.

A frozen exe that cannot start does not fail quietly.  PyInstaller's
bootloader raises a modal ``MessageBox`` ("Failed to load Python DLL"), and
Qt's offscreen platform does nothing about it, because the dialog comes from
the bootloader before Python or Qt exist.  The build's check that the staged
update helper starts (``build.py::verify_helper_stage_runs``) exists to catch
exactly a broken stage, so run on the interactive desktop it would put that
dialog in front of whoever is at the machine, and a failing build would wait
on a click.

So the program runs on a desktop created for it in the current window
station.  It has no window, no input and no display; any dialog lands there
unseen, and the timeout kills the process by the pid we got.  Windows only.
"""

from __future__ import annotations

import ctypes
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from typing import Optional

_GENERIC_ALL = 0x10000000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_NO_WINDOW = 0x08000000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_STILL_ACTIVE = 259


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def _environment_block(env: Mapping[str, str]) -> "ctypes.Array[ctypes.c_wchar]":
    """A unicode environment block: ``K=V`` entries, each NUL-ended, then one more NUL."""
    text = "".join(f"{key}={value}\0" for key, value in sorted(env.items())) + "\0"
    return ctypes.create_unicode_buffer(text, len(text))


def run_on_hidden_desktop(
    argv: Sequence[str],
    *,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
    timeout_s: float = 60.0,
) -> Optional[int]:
    """Run ``argv`` on a private, invisible desktop; return its exit code.

    None means it did not finish within ``timeout_s`` and was terminated (by
    the pid this call started, nothing else).  Raises ``OSError`` when the
    desktop or the process could not be created, so a caller never falls back
    to the visible desktop by accident.  stdout and stderr are not captured:
    the programs run this way report through a file.
    """
    if sys.platform != "win32":
        raise OSError("hidden desktops are a Windows feature")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    user32.CreateDesktopW.restype = wintypes.HANDLE
    user32.CreateDesktopW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    name = f"aosk-test-{uuid.uuid4().hex[:12]}"
    desktop = user32.CreateDesktopW(name, None, None, 0, _GENERIC_ALL, None)
    if not desktop:
        raise OSError(f"CreateDesktopW failed (error {ctypes.get_last_error()})")
    try:
        startup = _STARTUPINFOW()
        startup.cb = ctypes.sizeof(_STARTUPINFOW)
        startup.lpDesktop = f"WinSta0\\{name}"
        info = _PROCESS_INFORMATION()
        command = ctypes.create_unicode_buffer(subprocess_list2cmdline(argv))
        block = _environment_block(env if env is not None else dict(os.environ))
        ok = kernel32.CreateProcessW(
            None,
            command,
            None,
            None,
            False,
            _CREATE_UNICODE_ENVIRONMENT | _CREATE_NO_WINDOW,
            ctypes.cast(block, wintypes.LPVOID),
            cwd,
            ctypes.byref(startup),
            ctypes.byref(info),
        )
        if not ok:
            raise OSError(f"CreateProcessW failed (error {ctypes.get_last_error()})")
        try:
            waited = kernel32.WaitForSingleObject(info.hProcess, int(timeout_s * 1000))
            if waited == _WAIT_OBJECT_0:
                code = wintypes.DWORD()
                kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
                return int(code.value)
            kernel32.TerminateProcess(info.hProcess, 1)
            kernel32.WaitForSingleObject(info.hProcess, 5000)
            return None
        finally:
            kernel32.CloseHandle(info.hThread)
            kernel32.CloseHandle(info.hProcess)
    finally:
        user32.CloseDesktop(desktop)


def subprocess_list2cmdline(argv: Sequence[str]) -> str:
    """The Windows command line for ``argv`` (the same quoting ``subprocess`` uses)."""
    import subprocess

    return subprocess.list2cmdline(list(argv))
