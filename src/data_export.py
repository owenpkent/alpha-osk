"""
User data export / import.

Bundles a user's prediction model, lifetime analytics, and imported
vocabulary packs into a single ``.zip`` archive that can be moved
between machines. Telemetry state (``telemetry.json`` — contains the
anon_id) is intentionally excluded. Copying it across machines would
link the user's contributions, which the telemetry consent docs
explicitly promise not to do (see ``docs/architecture/TELEMETRY.md``).

Archive layout
--------------
A normal ``.zip``::

    manifest.json
    models/ngram_model.json
    models/ppm_model.json
    analytics.json
    snippets.json
    packs/<pack_id>/dictionary.txt
    packs/<pack_id>/bigrams.txt          (optional)
    packs/<pack_id>/trigrams.txt         (optional)
    packs/<pack_id>/pack.json            (optional)

``manifest.json`` carries the schema version, app version that wrote
the file, an ISO-8601 UTC timestamp, the list of file paths included,
and the list of pack ids. Future schema bumps refuse incompatible
files at inspect time so the user sees a clear error rather than a
half-restored state.

Security
--------
Import validates every entry:

- name does not contain ``..`` components or absolute paths (zip-slip);
- per-file uncompressed size is capped (``_MAX_FILE_BYTES``);
- total uncompressed size is capped (``_MAX_TOTAL_UNCOMPRESSED``);
- only members matching the expected layout are extracted; anything
  else is silently ignored.

Replace semantics
-----------------
Import is *replace*, not *merge*. Imported files overwrite the
corresponding files in the config directory; packs not present in the
archive are removed (the imported state is "the user's full snapshot
at export time"). Before applying, the current state is written to a
timestamped rescue archive in ``<config_dir>/exports/`` so the user
can roll back by importing that file.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set

_logger = logging.getLogger("DataExport")

SCHEMA_VERSION = 1

# Files we include (relative to config_dir) mapped to their target
# name inside the archive. The two are kept symmetric for now but
# split so a future rename in config_dir doesn't break old archives.
_MODEL_FILES = {
    "models/ngram_model.json": "models/ngram_model.json",
    "models/ppm_model.json": "models/ppm_model.json",
    "analytics.json": "analytics.json",
    # User-defined quick-insert snippets (name / email / phone / address
    # / canned phrases).  Included so the user's snippets move between
    # machines with the rest of their data.  Like the model files this
    # is replace-on-import; the bridge calls SnippetStore.reload_from_disk
    # afterwards so the running session picks it up without a restart.
    "snippets.json": "snippets.json",
}

# Hard caps. A legitimate export grown through normal use sits well
# under these; anything bigger suggests a corrupt / crafted file and
# is refused rather than risking OOM or filesystem-fill on import.
_MAX_ARCHIVE_BYTES = 200 * 1024 * 1024        # zip-on-disk cap
_MAX_FILE_BYTES = 75 * 1024 * 1024            # per-entry uncompressed
_MAX_TOTAL_UNCOMPRESSED = 500 * 1024 * 1024   # sum of uncompressed

# Pack ids are already sanitised by PackManager.import_pack but we
# re-check on export AND on import: defence against a hand-edited
# archive substituting `../escape` for a pack id.
_PACK_ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")

_REQUIRED_PACK_FILE = "dictionary.txt"
_PACK_FILES = frozenset({"dictionary.txt", "bigrams.txt", "trigrams.txt", "pack.json"})


class DataExportError(Exception):
    """Raised when an export or import fails."""


@dataclass
class ExportSummary:
    """Lightweight description of an export archive's contents."""
    path: Path
    schema_version: int
    app_version: str
    exported_at: str
    files: List[str] = field(default_factory=list)
    pack_ids: List[str] = field(default_factory=list)
    bytes: int = 0


def _read_app_version() -> str:
    try:
        from . import __version__ as v_mod
        return str(getattr(v_mod, "__version__", "unknown"))
    except Exception:
        return "unknown"


def suggested_export_name(now: Optional[datetime] = None) -> str:
    """Default filename for a fresh export, e.g. ``Alpha-OSK-Export-2026-05-19-143022.zip``."""
    now = now or datetime.now()
    return f"Alpha-OSK-Export-{now.strftime('%Y-%m-%d-%H%M%S')}.zip"


def export_user_data(config_dir: Path, dest: Path) -> ExportSummary:
    """Write the current user data in *config_dir* to a zip at *dest*.

    Returns an :class:`ExportSummary` describing what was written.
    Raises :class:`DataExportError` if *config_dir* is missing or
    *dest* is not writable.
    """
    config_dir = Path(config_dir)
    dest = Path(dest)
    if not config_dir.is_dir():
        raise DataExportError(f"Config directory not found: {config_dir}")

    dest.parent.mkdir(parents=True, exist_ok=True)

    files_included: List[str] = []
    pack_ids: List[str] = []
    file_payloads: List[tuple[str, Path]] = []

    for relpath, archive_name in _MODEL_FILES.items():
        src = config_dir / relpath
        if src.is_file():
            file_payloads.append((archive_name, src))
            files_included.append(archive_name)

    packs_dir = config_dir / "packs"
    if packs_dir.is_dir():
        for pack_entry in sorted(packs_dir.iterdir()):
            if not pack_entry.is_dir():
                continue
            if not _PACK_ID_RE.match(pack_entry.name):
                _logger.warning(
                    "Skipping pack with non-sanitised id during export: %s",
                    pack_entry.name,
                )
                continue
            # Skip symlinks that point outside packs_dir.
            try:
                resolved = pack_entry.resolve()
            except OSError:
                continue
            if not str(resolved).startswith(str(packs_dir.resolve())):
                _logger.warning(
                    "Skipping pack that resolves outside packs_dir: %s",
                    pack_entry.name,
                )
                continue
            if not (pack_entry / _REQUIRED_PACK_FILE).is_file():
                continue
            pack_ids.append(pack_entry.name)
            for f in pack_entry.iterdir():
                if not f.is_file():
                    continue
                if f.name not in _PACK_FILES:
                    continue
                archive_name = f"packs/{pack_entry.name}/{f.name}"
                file_payloads.append((archive_name, f))
                files_included.append(archive_name)

    timestamp = datetime.now(timezone.utc).isoformat()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "app_version": _read_app_version(),
        "exported_at": timestamp,
        "files": files_included,
        "pack_ids": pack_ids,
    }

    try:
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
            for archive_name, src in file_payloads:
                zf.write(src, archive_name)
    except OSError as exc:
        raise DataExportError(f"Failed to write export to {dest}: {exc}") from exc

    _logger.info(
        "Exported %d files (%d packs) to %s",
        len(files_included), len(pack_ids), dest,
    )
    return ExportSummary(
        path=dest,
        schema_version=SCHEMA_VERSION,
        app_version=str(manifest["app_version"]),
        exported_at=timestamp,
        files=files_included,
        pack_ids=pack_ids,
        bytes=dest.stat().st_size,
    )


def _validate_archive_entry(entry: zipfile.ZipInfo) -> None:
    name = entry.filename
    if not name or name.endswith("/"):
        # Directory entries are harmless but contribute nothing; treat
        # as allowed but the extractor filters them out by allow-list.
        return
    if name.startswith("/") or name.startswith("\\"):
        raise DataExportError(f"Refusing archive entry with absolute path: {name!r}")
    # Windows drive prefix (C:, D:, ...) — bare drive specifier in an
    # archive name should never occur from a clean zip.
    if len(name) >= 2 and name[1] == ":":
        raise DataExportError(f"Refusing archive entry with drive prefix: {name!r}")
    if "\\" in name:
        raise DataExportError(f"Refusing archive entry with backslash: {name!r}")
    if ".." in Path(name).parts:
        raise DataExportError(f"Refusing archive entry with .. component: {name!r}")
    if entry.file_size > _MAX_FILE_BYTES:
        raise DataExportError(
            f"Archive entry {name!r} exceeds per-file cap "
            f"({entry.file_size} > {_MAX_FILE_BYTES})"
        )


def _allowed_archive_member(name: str) -> bool:
    """Allow-list for which archive members get extracted on import.

    Anything else (stray files, manifest, junk a hand-edited archive
    snuck in) is silently ignored at extraction time. The manifest is
    consumed separately by :func:`inspect_export`.
    """
    if name == "manifest.json":
        return False  # consumed separately, not extracted
    if name in _MODEL_FILES.values():
        return True
    parts = name.split("/")
    if len(parts) == 3 and parts[0] == "packs":
        pack_id, filename = parts[1], parts[2]
        return bool(_PACK_ID_RE.match(pack_id)) and filename in _PACK_FILES
    return False


def inspect_export(src: Path) -> ExportSummary:
    """Validate an export archive and return its manifest.

    Performs the full validation that :func:`import_user_data` would,
    so a file that fails inspection cannot smuggle anything past
    import. Use this to show the user a preview before applying.
    """
    src = Path(src)
    if not src.is_file():
        raise DataExportError(f"Export file not found: {src}")
    if src.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise DataExportError(
            f"Export file too large ({src.stat().st_size} > {_MAX_ARCHIVE_BYTES})"
        )
    try:
        zf = zipfile.ZipFile(src, "r")
    except zipfile.BadZipFile as exc:
        raise DataExportError(f"Not a valid .zip file: {src}") from exc
    with zf:
        total_uncompressed = 0
        for entry in zf.infolist():
            _validate_archive_entry(entry)
            total_uncompressed += entry.file_size
            if total_uncompressed > _MAX_TOTAL_UNCOMPRESSED:
                raise DataExportError(
                    f"Archive uncompressed size exceeds cap "
                    f"({total_uncompressed} > {_MAX_TOTAL_UNCOMPRESSED})"
                )
        names = {e.filename for e in zf.infolist()}
        if "manifest.json" not in names:
            raise DataExportError("Archive missing manifest.json")
        with zf.open("manifest.json") as f:
            try:
                manifest = json.load(f)
            except json.JSONDecodeError as exc:
                raise DataExportError(f"manifest.json is not valid JSON: {exc}") from exc
        if not isinstance(manifest, dict):
            raise DataExportError("manifest.json is not a JSON object")
        schema = manifest.get("schema_version")
        if not isinstance(schema, int):
            raise DataExportError("manifest.json missing integer schema_version")
        if schema > SCHEMA_VERSION:
            raise DataExportError(
                f"Export was written with a newer schema (got {schema}, "
                f"max supported {SCHEMA_VERSION}). Upgrade Alpha-OSK first."
            )
    return ExportSummary(
        path=src,
        schema_version=schema,
        app_version=str(manifest.get("app_version", "unknown")),
        exported_at=str(manifest.get("exported_at", "")),
        files=[str(p) for p in manifest.get("files", []) if isinstance(p, str)],
        pack_ids=[str(p) for p in manifest.get("pack_ids", []) if isinstance(p, str)],
        bytes=src.stat().st_size,
    )


def import_user_data(src: Path, config_dir: Path) -> ExportSummary:
    """Replace the user data in *config_dir* with the contents of *src*.

    Before overwriting anything, the current state is written to a
    rescue archive in ``<config_dir>/exports/`` so the user can revert.
    Rescue export failures are logged but do not abort the import.

    Model files are replaced via tempfile-then-rename so a partial
    write does not corrupt the existing file. Packs are full-replace:
    every pack directory under ``packs_dir`` is removed and re-extracted
    from the archive.
    """
    config_dir = Path(config_dir)
    src = Path(src)
    summary = inspect_export(src)

    config_dir.mkdir(parents=True, exist_ok=True)

    exports_dir = config_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    rescue_path = exports_dir / f"rescue-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}.zip"
    try:
        export_user_data(config_dir, rescue_path)
    except DataExportError as exc:
        _logger.warning("Rescue export failed (continuing import anyway): %s", exc)

    with zipfile.ZipFile(src, "r") as zf:
        archive_names = {e.filename for e in zf.infolist()}

        # Model files: copy out via tempfile then atomic rename.
        for relpath, archive_name in _MODEL_FILES.items():
            if archive_name not in archive_names:
                continue
            entry = zf.getinfo(archive_name)
            _validate_archive_entry(entry)
            dest = config_dir / relpath
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".importing")
            try:
                with zf.open(entry) as src_f, open(tmp, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                tmp.replace(dest)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

        # Packs: full replace. Remove all existing packs (matching the
        # id regex, defensively) before extraction.
        packs_dir = config_dir / "packs"
        packs_dir.mkdir(parents=True, exist_ok=True)
        packs_root = packs_dir.resolve()
        for existing in packs_dir.iterdir():
            if not existing.is_dir():
                continue
            if not _PACK_ID_RE.match(existing.name):
                continue
            try:
                resolved = existing.resolve()
            except OSError:
                continue
            # Defence against symlinks pointing outside packs_dir.
            if not str(resolved).startswith(str(packs_root)):
                continue
            shutil.rmtree(resolved, ignore_errors=True)

        # Extract pack files from the allow-list.
        extracted_pack_ids: Set[str] = set()
        for entry in zf.infolist():
            _validate_archive_entry(entry)
            if not _allowed_archive_member(entry.filename):
                continue
            parts = entry.filename.split("/")
            if len(parts) != 3 or parts[0] != "packs":
                continue
            pack_id, filename = parts[1], parts[2]
            if not _PACK_ID_RE.match(pack_id):
                continue
            if filename not in _PACK_FILES:
                continue
            dest_dir = packs_dir / pack_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / filename
            tmp = dest_file.with_suffix(dest_file.suffix + ".importing")
            try:
                with zf.open(entry) as src_f, open(tmp, "wb") as dst_f:
                    shutil.copyfileobj(src_f, dst_f)
                tmp.replace(dest_file)
                extracted_pack_ids.add(pack_id)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass

    _logger.info(
        "Imported %d files (%d packs) from %s",
        len(summary.files), len(summary.pack_ids), src,
    )
    return summary
