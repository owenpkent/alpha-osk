"""Tests for src/prediction/pack_ids.py, the one place the vocabulary
pack-id validation rule lives.

Both src/data_export.py and src/prediction/vocabulary_pack.py used to
carry their own copy of this rule, and had already drifted: the
data_export copy admitted a leading '-' or '_', the vocabulary_pack
copy did not. This file pins the rule itself; the "the two callers
cannot drift again" property is pinned separately at the bottom, by
checking both callers import the same objects rather than reimplementing
them, and by a behavioural check on the one real tightening this
refactor makes.

Every accepted case is paired with the near-miss it must reject, per
the project's testing convention: a rule that happened to also accept
the good cases would pass a suite of positive-only examples.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from src.prediction.pack_ids import (
    RESERVED_DEVICE_NAMES,
    is_reserved_device_name,
    is_valid_pack_id,
)


class TestThePattern:
    """is_valid_pack_id: ^[a-z0-9][a-z0-9_-]{0,63}$, 1-64 characters,
    never a leading '-' or '_'."""

    def test_a_leading_hyphen_is_rejected(self) -> None:
        assert is_valid_pack_id("medical")
        assert not is_valid_pack_id("-medical")

    def test_a_leading_underscore_is_rejected(self) -> None:
        assert is_valid_pack_id("medical")
        assert not is_valid_pack_id("_medical")

    def test_a_space_is_rejected(self) -> None:
        assert is_valid_pack_id("pack-1")
        assert not is_valid_pack_id("pack 1")

    def test_length_cap_is_64(self) -> None:
        assert is_valid_pack_id("a" * 64)
        assert not is_valid_pack_id("a" * 65)

    def test_uppercase_is_rejected(self) -> None:
        assert is_valid_pack_id("medical")
        assert not is_valid_pack_id("Medical")
        assert not is_valid_pack_id("MEDICAL")

    @pytest.mark.parametrize("bad", ["..", "../x", "a/b", ""])
    def test_traversal_shapes_and_empty_are_rejected(self, bad: str) -> None:
        assert not is_valid_pack_id(bad)


class TestReservedDeviceNames:
    """Windows reserves con/prn/aux/nul/com1-9/lpt1-9 for every path
    component, matched case-insensitively and before any extension.
    Each of these matches the pattern above (it looks like a normal
    lowercase id) and must still be rejected by the combined check."""

    @pytest.mark.parametrize("name", ["con", "CON", "con.txt", "com1", "lpt9"])
    def test_reserved_name_is_flagged(self, name: str) -> None:
        assert is_reserved_device_name(name)

    @pytest.mark.parametrize("name", ["console", "com10"])
    def test_a_name_that_merely_starts_with_one_is_not_flagged(self, name: str) -> None:
        assert not is_reserved_device_name(name)

    @pytest.mark.parametrize("name", ["con", "CON", "com1", "lpt9"])
    def test_reserved_name_fails_the_combined_check(self, name: str) -> None:
        assert not is_valid_pack_id(name)

    @pytest.mark.parametrize("name", ["console", "com10"])
    def test_near_miss_passes_the_combined_check(self, name: str) -> None:
        assert is_valid_pack_id(name)

    def test_the_reserved_set_itself(self) -> None:
        assert "con" in RESERVED_DEVICE_NAMES
        assert "lpt9" in RESERVED_DEVICE_NAMES
        assert "console" not in RESERVED_DEVICE_NAMES


class TestBothCallersImportTheSharedRule:
    """The rule must be imported, not reimplemented, at each call site.
    Identity checks are the direct way to pin that: if either caller
    ever went back to defining its own compiled pattern or its own
    predicate function, this fails immediately rather than waiting for
    a behavioural gap between the two to surface first."""

    def test_data_export_imports_the_shared_objects(self) -> None:
        from src import data_export
        from src.prediction import pack_ids

        assert data_export.is_valid_pack_id is pack_ids.is_valid_pack_id
        assert data_export.PACK_ID_RE is pack_ids.PACK_ID_RE

    def test_vocabulary_pack_imports_the_shared_predicate(self) -> None:
        from src.prediction import pack_ids, vocabulary_pack

        assert vocabulary_pack.is_valid_pack_id is pack_ids.is_valid_pack_id


class TestTheTighteningIsActuallyEnforced:
    """Behavioural pin for the one real tightening this refactor makes:
    data_export's own copy of the rule used to admit a leading '-' or
    '_'; the shared (stricter) rule does not. Fixture shape matches
    tests/test_data_export.py::TestReservedPackNames, which exercises
    the sibling case (a reserved device name) the same way."""

    def test_archive_with_a_leading_hyphen_pack_id_is_skipped_on_import(
        self, tmp_path: Path
    ) -> None:
        from src.data_export import SCHEMA_VERSION, import_user_data

        manifest = json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "app_version": "1.0",
                "exported_at": "",
                "files": [],
                "pack_ids": ["-evil", "good_pack"],
            }
        )
        archive = tmp_path / "hyphen.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("manifest.json", manifest)
            zf.writestr("packs/-evil/dictionary.txt", "alpha\n")
            zf.writestr("packs/good_pack/dictionary.txt", "beta\n")

        dst = tmp_path / "dst"
        dst.mkdir()
        import_user_data(archive, dst)  # must not raise

        assert not (dst / "packs" / "-evil").exists()
        assert (dst / "packs" / "good_pack" / "dictionary.txt").is_file()

    def test_import_pack_never_derives_a_leading_hyphen_id(self, tmp_path: Path) -> None:
        """PackManager.import_pack's sanitiser (re.sub + .strip("_-"))
        already can't produce a leading '-'/'_' id, which is exactly
        why the stricter shared pattern is safe to enforce everywhere:
        it describes every id the legitimate path can produce and
        nothing else. A source folder name that is nothing but leading
        hyphens strips down to a clean id rather than an invalid one."""
        from src.prediction.vocabulary_pack import PackManager

        packs_dir = tmp_path / "builtin"
        packs_dir.mkdir()
        user_dir = tmp_path / "user_packs"
        user_dir.mkdir()
        mgr = PackManager(packs_dir=packs_dir, user_packs_dir=user_dir)

        source = tmp_path / "src" / "---evil"
        source.mkdir(parents=True)
        (source / "dictionary.txt").write_text("alpha\n")

        pack_id = mgr.import_pack(source)
        assert pack_id == "evil"
        assert is_valid_pack_id(pack_id)
