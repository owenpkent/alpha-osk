"""The last few dictation runs, kept so a lost sentence can be recovered.

Each entry is the raw transcript, what was finally produced (cleaned text,
or a written draft), and, when it was captured, the audio as a 16 kHz mono
WAV.  The point is recovery: a dictated paragraph that landed in the wrong
window, or a cleanup that mangled something, is otherwise gone, and for
someone who types slowly that is minutes of work.

Layout, under ``<config>/dictation_history/``: one ``index.json`` and one
``<id>.wav`` per entry that has audio.

Three rules are load-bearing:

* **A file path is never read from the index.**  The WAV for an entry is
  always built from its id, and an id is only accepted if it matches
  ``^[0-9a-f]{16}$``.  The index is a file a user, an older build or a
  restored backup may have damaged, so a stored path would be a
  path-traversal primitive for the delete and play calls.
* **Nothing here raises on IO trouble.**  History is a convenience riding
  alongside typing; a full disk must not break dictation.  Failures are
  logged at WARNING without content and the call returns False or skips.
* **This is typed-content storage, so it is excluded from the Data Backup
  archive** (``data_export`` is an allow-list and never names it;
  ``tests/test_dictation_history.py`` pins that) and nothing here logs a
  transcript.  It lives in the config dir, so the Delete / Clear calls and
  the toggle are the user's way to remove it.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
import tempfile
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..atomic_write import atomic_write_json

_logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

MAX_ENTRIES = 20
MAX_AUDIO_BYTES_TOTAL = 200 * 1024 * 1024
MAX_INDEX_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
MAX_SECONDS = 3600.0

_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_WAV_RE = re.compile(r"^([0-9a-f]{16})\.wav$")
PURPOSES = ("dictate", "write")

# Match `audio.SAMPLE_RATE` / `CHANNELS`; spelled here so this module
# imports no Qt.
_SAMPLE_RATE = 16000
_CHANNELS = 1
_SAMPLE_WIDTH = 2


def _root() -> Path:
    from ..platform import get_config_dir

    return get_config_dir() / "dictation_history"


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and _ID_RE.match(value) is not None


def _clean_entry(raw: Any) -> dict[str, Any] | None:
    """Validate one stored entry, or ``None`` to drop it."""
    if not isinstance(raw, dict) or not _valid_id(raw.get("id")):
        return None
    purpose = raw.get("purpose")
    text_raw = raw.get("raw")
    result = raw.get("result")
    if purpose not in PURPOSES or not isinstance(text_raw, str) or not isinstance(result, str):
        return None
    created = raw.get("created")
    stored_seconds = raw.get("seconds")
    seconds = 0.0
    if isinstance(stored_seconds, (int, float)) and not isinstance(stored_seconds, bool):
        try:
            seconds = float(stored_seconds)
        except OverflowError:
            seconds = 0.0
        if not math.isfinite(seconds):
            seconds = 0.0
    return {
        "id": raw["id"],
        "created": created[:40] if isinstance(created, str) else "",
        "purpose": purpose,
        "raw": text_raw[:MAX_TEXT_CHARS],
        "result": result[:MAX_TEXT_CHARS],
        "cleaned": raw.get("cleaned") is True,
        "seconds": max(0.0, min(MAX_SECONDS, seconds)),
    }


class DictationHistory:
    """The on-disk history.  Newest entry first in memory and on disk."""

    def __init__(self, directory: Path | None = None) -> None:
        self._explicit_dir = directory
        self._entries: list[dict[str, Any]] | None = None

    # --- paths ----------------------------------------------------------

    def _dir(self) -> Path:
        return self._explicit_dir if self._explicit_dir is not None else _root()

    def _index_path(self) -> Path:
        return self._dir() / "index.json"

    def _wav_path(self, entry_id: str) -> Path:
        # Only ever called with an id that passed `_valid_id`.
        return self._dir() / f"{entry_id}.wav"

    # --- loading --------------------------------------------------------

    def _load(self) -> list[dict[str, Any]]:
        if self._entries is not None:
            return self._entries
        entries: list[dict[str, Any]] = []
        rejected = False
        try:
            path = self._index_path()
            if path.exists():
                if path.stat().st_size > MAX_INDEX_BYTES:
                    _logger.warning("dictation history index exceeds the size cap, ignoring it")
                    rejected = True
                else:
                    with open(path, encoding="utf-8") as fh:
                        data = json.load(fh)
                    items = data.get("entries") if isinstance(data, dict) else None
                    seen: set[str] = set()
                    for item in items if isinstance(items, list) else []:
                        cleaned = _clean_entry(item)
                        if cleaned is not None and cleaned["id"] not in seen:
                            seen.add(cleaned["id"])
                            entries.append(cleaned)
        except (OSError, ValueError, RecursionError):
            _logger.warning("dictation history index could not be read", exc_info=True)
            entries = []
            rejected = True
        self._entries = entries[:MAX_ENTRIES]
        if not rejected:
            # An index that could not be read says nothing about which
            # recordings are owned, so sweeping "orphans" against an empty
            # list would delete every one of them.  They are kept until an
            # index that loads cleanly says otherwise.
            self._remove_orphans()
        return self._entries

    def _remove_orphans(self) -> None:
        """Delete WAVs no entry owns.  Only files named ``<valid-id>.wav``."""
        assert self._entries is not None
        known = {e["id"] for e in self._entries}
        try:
            directory = self._dir()
            if not directory.is_dir():
                return
            for child in directory.iterdir():
                match = _WAV_RE.match(child.name)
                if match and match.group(1) not in known and child.is_file():
                    self._unlink(child)
        except OSError:
            _logger.warning("dictation history could not be scanned", exc_info=True)

    @staticmethod
    def _unlink(path: Path) -> None:
        try:
            path.unlink()
        except OSError:
            _logger.warning("dictation history file could not be removed", exc_info=True)

    # --- saving ---------------------------------------------------------

    def _payload(self) -> dict[str, Any]:
        assert self._entries is not None
        return {"schema_version": SCHEMA_VERSION, "entries": self._entries}

    def _encoded_bytes(self) -> int:
        """The size the index will have on disk, in the writer's own encoding.

        ``atomic_write_json`` escapes non-ASCII, so a character of
        Japanese costs six bytes rather than three; the only honest
        measure is to encode the payload exactly as the writer will.
        """
        return len(json.dumps(self._payload(), indent=2, ensure_ascii=True).encode("utf-8"))

    def _save(self) -> bool:
        try:
            atomic_write_json(self._index_path(), self._payload(), indent=2)
        except OSError:
            _logger.warning("dictation history index could not be saved", exc_info=True)
            return False
        return True

    def _write_wav(self, entry_id: str, pcm: bytes) -> bool:
        directory = self._dir()
        tmp_name = ""
        try:
            directory.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=".wav-", suffix=".tmp")
            with os.fdopen(fd, "wb") as fh, wave.open(fh, "wb") as wav:
                wav.setnchannels(_CHANNELS)
                wav.setsampwidth(_SAMPLE_WIDTH)
                wav.setframerate(_SAMPLE_RATE)
                wav.writeframes(pcm[: len(pcm) - (len(pcm) % _SAMPLE_WIDTH)])
            os.replace(tmp_name, self._wav_path(entry_id))
            return True
        except (OSError, wave.Error):
            _logger.warning("dictation history audio could not be saved", exc_info=True)
            if tmp_name:
                self._unlink(Path(tmp_name))
            return False

    def _audio_bytes(self) -> int:
        total = 0
        for entry in self._load():
            try:
                total += self._wav_path(entry["id"]).stat().st_size
            except OSError:
                pass
        return total

    def _prune(self) -> None:
        entries = self._load()
        while len(entries) > MAX_ENTRIES:
            self._drop(entries.pop())
        # Oldest entries go first when the disk budget is exceeded.  The
        # newest is never evicted by its own size alone.
        while len(entries) > 1 and self._audio_bytes() > MAX_AUDIO_BYTES_TOTAL:
            self._drop(entries.pop())
        # The index has a byte cap of its own, and `_load` refuses a file
        # over it unread.  Each entry's text is capped, but twenty entries
        # of non-ASCII text at the cap encode past the index cap, and a
        # saved index the loader rejects reads back as nothing.
        while len(entries) > 1 and self._encoded_bytes() > MAX_INDEX_BYTES:
            self._drop(entries.pop())

    def _drop(self, entry: dict[str, Any]) -> None:
        self._unlink(self._wav_path(entry["id"]))

    # --- public API -----------------------------------------------------

    def add(
        self,
        purpose: str,
        raw: str,
        result: str,
        cleaned: bool,
        seconds: float,
        pcm: bytes | None,
    ) -> str:
        """Record a run.  Returns its id, or ``""`` if it could not be kept."""
        if purpose not in PURPOSES:
            return ""
        entries = self._load()
        entry_id = secrets.token_hex(8)
        entry = _clean_entry(
            {
                "id": entry_id,
                "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "purpose": purpose,
                "raw": raw,
                "result": result,
                "cleaned": bool(cleaned),
                "seconds": seconds,
            }
        )
        if entry is None:  # pragma: no cover - the literal above is always valid
            return ""
        if pcm:
            self._write_wav(entry_id, pcm)
        entries.insert(0, entry)
        self._prune()
        if not self._save():
            return ""
        return entry_id

    def entries(self) -> list[dict[str, Any]]:
        """Newest first.  No filesystem paths, only ``hasAudio``."""
        out: list[dict[str, Any]] = []
        for entry in self._load():
            item = dict(entry)
            item["hasAudio"] = self._wav_path(entry["id"]).is_file()
            out.append(item)
        return out

    def get(self, entry_id: str) -> dict[str, Any] | None:
        if not _valid_id(entry_id):
            return None
        for entry in self._load():
            if entry["id"] == entry_id:
                return dict(entry)
        return None

    def audio_path(self, entry_id: str) -> Path | None:
        if self.get(entry_id) is None:
            return None
        path = self._wav_path(entry_id)
        return path if path.is_file() else None

    def update_result(self, entry_id: str, result: str, cleaned: bool = True) -> bool:
        if not _valid_id(entry_id) or not isinstance(result, str):
            return False
        for entry in self._load():
            if entry["id"] == entry_id:
                entry["result"] = result[:MAX_TEXT_CHARS]
                entry["cleaned"] = bool(cleaned)
                return self._save()
        return False

    def delete(self, entry_id: str) -> bool:
        if not _valid_id(entry_id):
            return False
        entries = self._load()
        for i, entry in enumerate(entries):
            if entry["id"] == entry_id:
                self._drop(entries.pop(i))
                return self._save()
        return False

    def clear(self) -> None:
        entries = self._load()
        for entry in entries:
            self._drop(entry)
        entries.clear()
        self._remove_orphans()
        self._save()
