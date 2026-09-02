"""Tempfile-then-rename writes, shared by every persistent store.

Every loader in this codebase already rejects a corrupt file (a size cap,
a type check, a schema fallback), which only matters if a corrupt file can
happen in the first place. A plain ``open(path, "w")`` followed by a write
is exactly how one does: a crash, a killed process, or a power loss between
the open and the close leaves a truncated or half-written file in the
place the previous good one used to be, and for ``ngram_model.json`` that
is data the user cannot recreate. Writing to a temp file in the same
directory and renaming it into place means the swap is atomic at the
filesystem level: a reader (or a re-launch) sees either the old file or
the new one, never a partial one.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Write *text* to *path* atomically.

    Creates the parent directory if needed, writes to a temp file in that
    same directory (so the final rename stays on one filesystem and is
    atomic), flushes and fsyncs before closing (a rename can otherwise
    land before the bytes do, on some filesystems), then ``os.replace``s
    it into place. If *mode* is given, the temp file's permissions are
    narrowed before the rename (a no-op, swallowed, on Windows). On any
    exception the temp file is removed and the exception re-raised; the
    caller's own exception handling and logging are untouched by this
    helper on purpose.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if mode is not None:
            try:
                os.chmod(tmp_path, mode)
            except OSError:
                pass
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_json(
    path: Path,
    data: Any,
    *,
    indent: int | None = None,
    ensure_ascii: bool = True,
    mode: int | None = None,
) -> None:
    """Serialise *data* to JSON and write it atomically via :func:`atomic_write_text`.

    Serialisation happens before any file is touched, so a value that
    ``json.dumps`` refuses (a non-serialisable object, a circular
    reference) never creates a temp file at all.
    """
    text = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    atomic_write_text(path, text, mode=mode)
