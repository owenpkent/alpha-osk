"""Tests for src/data_export.py."""

from __future__ import annotations

import json
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
            zf.writestr("manifest.json", json.dumps({
                "schema_version": SCHEMA_VERSION + 99,
                "app_version": "999.0.0",
                "exported_at": "",
                "files": [],
                "pack_ids": [],
            }))
        with pytest.raises(DataExportError, match="newer schema"):
            inspect_export(f)

    def test_zip_slip_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "evil.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("manifest.json", json.dumps({
                "schema_version": SCHEMA_VERSION,
                "app_version": "1.0", "exported_at": "", "files": [], "pack_ids": [],
            }))
            zf.writestr("../escape.json", "pwned")
        with pytest.raises(DataExportError, match=r"\.\."):
            inspect_export(f)

    def test_absolute_path_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "evil.zip"
        with zipfile.ZipFile(f, "w") as zf:
            zf.writestr("manifest.json", json.dumps({
                "schema_version": SCHEMA_VERSION,
                "app_version": "1.0", "exported_at": "", "files": [], "pack_ids": [],
            }))
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
            zf.writestr("manifest.json", json.dumps({
                "schema_version": SCHEMA_VERSION,
                "app_version": "1.0",
                "exported_at": "",
                "files": ["telemetry.json"],
                "pack_ids": [],
            }))
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
            zf.writestr("manifest.json", json.dumps({
                "schema_version": SCHEMA_VERSION,
                "app_version": "1.0", "exported_at": "", "files": [], "pack_ids": [],
            }))
            zf.writestr("models/ngram_model.json", b"x" * 64)  # > patched cap
        with pytest.raises(DataExportError, match="per-file cap"):
            inspect_export(f)


class TestSuggestedName:
    def test_format(self) -> None:
        from datetime import datetime
        name = suggested_export_name(datetime(2026, 5, 19, 14, 30, 22))
        assert name == "Alpha-OSK-Export-2026-05-19-143022.zip"
