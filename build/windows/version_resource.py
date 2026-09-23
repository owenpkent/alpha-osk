"""The Windows version resource stamped into ``alpha-osk.exe``.

Why the exe needs one
---------------------
The shell names things after it.  A taskbar pin made from the *running*
button is named from the exe's ``FileDescription``, and when there is none
it falls back to the bare filename: the shipped keyboard pinned as
``alpha-osk``, lowercase, and Explorer suffixed ``(2)`` because a pin of
that name already existed.  Task Manager, the Open With list and the
"Do you want to allow this app" prompts read the same fields.  The
installer already writes its own version block (``VIAddVersionKey`` in
``build.py``); this is the matching one for the application, kept to the
same publisher and copyright strings so the two cannot disagree.

How it is applied
-----------------
``alpha-osk.spec`` writes :func:`version_info_text` to the PyInstaller
work directory and hands the path to ``EXE(version=...)``.  The text is
PyInstaller's own ``VSVersionInfo`` format (what ``pyi-grab_version``
emits), generated rather than checked in so it can never lag
``src/__version__.py``.  This module is imported by the spec through
``importlib`` (``build/windows`` is not a package) and by its tests the
same way, and deliberately imports nothing from PyInstaller so the tests
run wherever the suite does.
"""

from __future__ import annotations

import re

APP_NAME = "Alpha-OSK"
EXE_NAME = "alpha-osk.exe"
# Must match APP_PUBLISHER in build.py: the installer's version block and the
# exe's are read side by side in the file properties dialog.
PUBLISHER = "OK Studio Inc."
COPYRIGHT = f"Copyright (C) {PUBLISHER}"

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def version_tuple(version: str) -> tuple[int, int, int, int]:
    """``"1.5.0"`` -> ``(1, 5, 0, 0)``: the four-part numeric form the
    fixed-file-info block needs.  A pre-release suffix is dropped, since the
    binary block has nowhere to put it; the string fields keep the full text.
    """
    match = _VERSION_RE.match(version)
    if not match:
        raise ValueError(f"not a dotted version: {version!r}")
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch, 0)


def version_info_text(version: str) -> str:
    """The ``VSVersionInfo`` source for ``EXE(version=...)``."""
    numeric = version_tuple(version)
    strings = [
        ("CompanyName", PUBLISHER),
        ("FileDescription", APP_NAME),
        ("FileVersion", version),
        ("InternalName", APP_NAME.lower()),
        ("LegalCopyright", COPYRIGHT),
        ("OriginalFilename", EXE_NAME),
        ("ProductName", APP_NAME),
        ("ProductVersion", version),
    ]
    string_structs = ",\n".join(
        f"        StringStruct({key!r}, {value!r})" for key, value in strings
    )
    return (
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers={numeric!r},\n"
        f"    prodvers={numeric!r},\n"
        "    mask=0x3F,\n"
        "    flags=0x0,\n"
        "    OS=0x40004,\n"
        "    fileType=0x1,\n"
        "    subtype=0x0,\n"
        "    date=(0, 0)\n"
        "  ),\n"
        "  kids=[\n"
        "    StringFileInfo([\n"
        "      StringTable('040904B0', [\n"
        f"{string_structs}\n"
        "      ])\n"
        "    ]),\n"
        "    VarFileInfo([VarStruct('Translation', [1033, 1200])])\n"
        "  ]\n"
        ")\n"
    )
