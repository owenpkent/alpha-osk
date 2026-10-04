"""Read back what a built exe's embedded manifest asks Windows for.

Why this exists
---------------
Releases 1.2.0 through 1.5.0 shipped ``alpha-osk.exe`` with
``uiAccess="false"`` in its embedded manifest, although
``alpha-osk.exe.manifest`` says ``"true"``.  PyInstaller 6 rewrites the
``requestedExecutionLevel`` element from ``EXE(uac_admin=...,
uac_uiaccess=...)`` and ignores what the supplied manifest says
(PyInstaller 5, which built 1.1.0 and earlier, kept the file's
``uiAccess``), so the attribute was replaced at every build from the
upgrade on and nothing noticed: the keyboard still runs without
UIAccess, it just cannot type into elevated windows.  The fix is one keyword in the spec; this module is the check
that it stays fixed, run by ``build.py`` on the artefact itself rather
than trusted to the spec having been right.

How it reads the manifest
-------------------------
Straight from the PE resource table with ``LoadLibraryExW`` in
data-file mode, the way the loader finds it (``RT_MANIFEST``, resource
id 1, ``CREATEPROCESS_MANIFEST_RESOURCE_ID``), and parsed as XML rather
than searched as text: the manifest carries a long comment that itself
mentions ``uiAccess="true"``, so a substring search would pass against
the bug.  Deliberately imports nothing from PyInstaller, so the check
does not agree with the code it guards only by construction.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

RT_MANIFEST = 24
CREATEPROCESS_MANIFEST_RESOURCE_ID = 1
LOAD_LIBRARY_AS_DATAFILE = 0x2
ERROR_RESOURCE_DATA_NOT_FOUND = 1812
ERROR_RESOURCE_TYPE_NOT_FOUND = 1813
ERROR_RESOURCE_NAME_NOT_FOUND = 1814
_NO_RESOURCE = {
    ERROR_RESOURCE_DATA_NOT_FOUND,
    ERROR_RESOURCE_TYPE_NOT_FOUND,
    ERROR_RESOURCE_NAME_NOT_FOUND,
}


def read_embedded_manifest(exe_path: str | Path) -> bytes | None:
    """The exe's ``RT_MANIFEST`` resource 1, or None when it has none.

    Raises ``OSError`` when the file cannot be opened as a PE image at
    all, which is a different answer from "it carries no manifest".
    """
    if sys.platform != "win32":
        raise OSError("reading an exe's resources needs Windows")

    import ctypes
    from ctypes import wintypes

    # A private handle rather than ctypes.windll.kernel32, so the argtypes
    # set here cannot leak into any other module sharing the global one.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD]
    kernel32.LoadLibraryExW.restype = ctypes.c_void_p
    kernel32.FindResourceW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.FindResourceW.restype = ctypes.c_void_p
    kernel32.SizeofResource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.SizeofResource.restype = wintypes.DWORD
    kernel32.LoadResource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.LoadResource.restype = ctypes.c_void_p
    kernel32.LockResource.argtypes = [ctypes.c_void_p]
    kernel32.LockResource.restype = ctypes.c_void_p
    kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
    kernel32.FreeLibrary.restype = wintypes.BOOL

    module = kernel32.LoadLibraryExW(str(exe_path), None, LOAD_LIBRARY_AS_DATAFILE)
    if not module:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        # MAKEINTRESOURCE: an integer id passed where a name pointer goes.
        resource = kernel32.FindResourceW(module, CREATEPROCESS_MANIFEST_RESOURCE_ID, RT_MANIFEST)
        if not resource:
            code = ctypes.get_last_error()
            if code in _NO_RESOURCE:
                return None
            raise ctypes.WinError(code)
        size = kernel32.SizeofResource(module, resource)
        loaded = kernel32.LoadResource(module, resource)
        if not size or not loaded:
            raise ctypes.WinError(ctypes.get_last_error())
        data = kernel32.LockResource(loaded)
        if not data:
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(data, size)
    finally:
        kernel32.FreeLibrary(module)


def _local(name: str) -> str:
    """``{urn:...}requestedExecutionLevel`` -> ``requestedExecutionLevel``."""
    return name.rsplit("}", 1)[-1]


def requested_execution_level(manifest_xml: bytes | str) -> dict[str, str] | None:
    """The attributes of the manifest's ``requestedExecutionLevel``, or None.

    Matched by local name, because manifests in the wild put it in the
    ``asm.v3`` default namespace (ours, PyInstaller's) or behind an
    ``asm.v2`` prefix, and Windows honours both.
    """
    root = ET.fromstring(manifest_xml)
    for element in root.iter():
        if _local(element.tag) == "requestedExecutionLevel":
            return {_local(key): value.strip() for key, value in element.attrib.items()}
    return None


def requests_uiaccess(exe_path: str | Path) -> bool:
    """Does this exe's embedded manifest ask Windows for UIAccess?

    Exactly ``uiAccess="true"``: a missing manifest, a missing element, a
    missing attribute and any other spelling all read as no, so the build
    guard built on this fails closed.
    """
    manifest = read_embedded_manifest(exe_path)
    if manifest is None:
        return False
    level = requested_execution_level(manifest)
    if level is None:
        return False
    return level.get("uiAccess") == "true"
