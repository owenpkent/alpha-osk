"""Property-based tests for the two hostile-input import paths.

``src/data_export.py`` (Data Backup archives) and
``src/prediction/vocabulary_pack.py`` (custom vocabulary packs) both take a
file or folder the user got from somewhere else and write it into the config
directory. Every existing test for them is an example test: a hand-built
zip-slip archive, a folder literally named ``..``, one oversize entry. Those
cover the attacks we thought of.

These tests state the *property* instead and let Hypothesis look for the
attacks we didn't. The load-bearing one is the same for both modules and is
the whole reason the hardening exists:

    nothing outside the destination directory is ever created, modified or
    removed, whatever the archive or folder is called.

Everything else here is a supporting invariant (the allow-list really is an
allow-list, caps trip on metadata rather than on extracted bytes, a valid
archive still round-trips). See CLAUDE.md, "Import paths are security-
critical", before loosening anything these assert.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from src.data_export import (
    _MAX_FILE_BYTES,
    _MODEL_FILES,
    _PACK_FILES,
    SCHEMA_VERSION,
    DataExportError,
    _allowed_archive_member,
    _validate_archive_entry,
    export_user_data,
    import_user_data,
    inspect_export,
)
from src.prediction.vocabulary_pack import _VALID_PACK_ID, PackManager

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# Path segments an attacker would reach for, mixed with the legitimate ones so
# generated names straddle the allow-list boundary instead of sitting well
# outside it (a name of pure junk is rejected by the first check and never
# exercises the later ones).
_SEGMENTS = [
    "..",
    ".",
    "models",
    "packs",
    "analytics.json",
    "snippets.json",
    "ngram_model.json",
    "ppm_model.json",
    "dictionary.txt",
    "bigrams.txt",
    "trigrams.txt",
    "pack.json",
    "manifest.json",
    # Things the extractor must never write.
    "telemetry.json",
    "boot.ini",
    "id_rsa",
    ".ssh",
    "etc",
    "passwd",
    "Windows",
    "System32",
    "AppData",
    "Roaming",
    # Pack ids: valid, and invalid in the ways the regex cares about.
    "medical",
    "my_pack",
    "a" * 64,
    "a" * 65,
    "BAD-ID",
    "pack id",
    "",
    "..%2f",
    "....",
    "~",
]

_PREFIXES = ["", "/", "\\", "C:", "C:\\", "//", "./"]

valid_pack_ids = st.from_regex(r"\A[a-z0-9][a-z0-9_\-]{0,12}\Z")

# Names the allow-list is *supposed* to admit. Generated as a first-class
# strategy rather than left to chance: a filter-until-allowed approach
# discards ~99% of random segment soup, which both trips Hypothesis's
# filter_too_much health check and means the contained-ness property is
# almost never actually exercised.
allowed_names = st.one_of(
    st.sampled_from(sorted(_MODEL_FILES.values())),
    st.builds(
        lambda pid, fname: f"packs/{pid}/{fname}",
        valid_pack_ids,
        st.sampled_from(sorted(_PACK_FILES)),
    ),
)

# Junk and traversal payloads: mostly rejected, and the reason each one is
# rejected differs (regex, `..` component, separator, drive letter).
junk_names = st.builds(
    lambda prefix, parts: prefix + "/".join(parts),
    st.sampled_from(_PREFIXES),
    st.lists(st.sampled_from(_SEGMENTS), min_size=1, max_size=4),
)


@st.composite
def _mutated_allowed(draw: st.DrawFn) -> str:
    """An allowed name with one hostile mutation applied.

    This is the interesting middle ground: names that look legitimate right
    up to the character that makes them dangerous, which is exactly where an
    allow-list is most likely to be too permissive.
    """
    name = draw(allowed_names)
    mutation = draw(
        st.sampled_from(
            [
                "prefix_parent",
                "prefix_abs",
                "prefix_drive",
                "backslash",
                "embed_parent",
                "embed_dot",
                "trailing_slash",
                "double_slash",
            ]
        )
    )
    if mutation == "prefix_parent":
        return "../" + name
    if mutation == "prefix_abs":
        return "/" + name
    if mutation == "prefix_drive":
        return "C:" + name
    if mutation == "backslash":
        return name.replace("/", "\\")
    if mutation == "embed_parent":
        head, _, tail = name.partition("/")
        return f"{head}/../{tail}" if tail else f"../{name}"
    if mutation == "embed_dot":
        head, _, tail = name.partition("/")
        return f"{head}/./{tail}" if tail else f"./{name}"
    if mutation == "trailing_slash":
        return name + "/"
    return name.replace("/", "//")


archive_names = st.one_of(allowed_names, junk_names, _mutated_allowed())

# Folder names that a filesystem will actually accept, but which still push on
# the sanitiser: casing, spaces, unicode, leading/trailing separators.
_NAME_ALPHABET = "abzABZ019 _-.éÜ漢"
folder_names = st.text(alphabet=_NAME_ALPHABET, min_size=1, max_size=16).filter(
    # Windows refuses names that end in a dot or space, and "." / ".." are not
    # creatable at all. Those are covered separately by the traversal test.
    lambda n: n.strip(". ") != "" and n[-1] not in ". " and n not in (".", "..")
)

file_contents = st.binary(min_size=0, max_size=512)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snapshot(root: Path) -> Dict[str, Optional[bytes]]:
    """Map every path under *root* to its bytes (None for directories)."""
    out: Dict[str, Optional[bytes]] = {}
    for p in sorted(root.rglob("*")):
        key = str(p.relative_to(root)).replace("\\", "/")
        out[key] = None if p.is_dir() else p.read_bytes()
    return out


def _build_archive(
    dest: Path,
    members: List[Tuple[str, bytes]],
    manifest: Optional[dict] = None,
) -> None:
    """Write a zip containing exactly *members*, plus a manifest."""
    if manifest is None:
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "app_version": "test",
            "exported_at": "2026-01-01T00:00:00+00:00",
            "files": [n for n, _ in members],
            "pack_ids": [],
        }
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Duplicate member names are legal in a zip but make "what did this write"
    # ambiguous, and zipfile warns about them. First one wins. Dedup on the
    # name zipfile will actually store, not the one passed in: on Windows
    # ZipInfo rewrites os.sep to "/", so "\\Roaming" and "/Roaming" collide.
    seen = {"manifest.json"}
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, payload in members:
            stored = zipfile.ZipInfo(name).filename
            # An empty member name cannot be stored at all: `writestr` indexes
            # the last character of the filename and raises IndexError on
            # Python <= 3.11. That is a limitation of the archive *writer*, not
            # something the importer could ever be handed, so skip it here
            # rather than narrowing the strategy. The empty name is still
            # generated and still exercised directly against the allow-list in
            # TestAllowListContainsExtraction, which is where it belongs.
            if not stored or stored in seen:
                continue
            seen.add(stored)
            zf.writestr(name, payload)


# ---------------------------------------------------------------------------
# data_export: the allow-list
# ---------------------------------------------------------------------------


class TestAllowListContainsExtraction:
    """`_allowed_archive_member` is the last line before bytes hit disk."""

    @given(name=archive_names)
    def test_allowed_names_cannot_escape_the_config_dir(self, name: str) -> None:
        """The zip-slip property, stated directly.

        Whatever the member is called, if the allow-list admits it then
        joining it onto the config dir must land *inside* the config dir.
        This is the assertion that would catch a `..` handling bug in any of
        the three places that validate names.
        """
        if not _allowed_archive_member(name):
            return

        config_dir = Path("/srv/alpha-osk-config").resolve()
        resolved = (config_dir / name).resolve()

        assert resolved != config_dir
        assert config_dir in resolved.parents, (
            f"allow-list admitted {name!r}, which resolves to {resolved} outside {config_dir}"
        )

    @given(name=archive_names)
    def test_allowed_names_are_relative_and_separator_clean(self, name: str) -> None:
        """No absolute path, drive prefix or backslash survives the filter."""
        if not _allowed_archive_member(name):
            return

        assert not name.startswith("/")
        assert not name.startswith("\\")
        assert "\\" not in name
        assert ".." not in Path(name).parts
        assert len(name) < 2 or name[1] != ":"

    @given(name=allowed_names)
    def test_the_legitimate_layout_is_still_accepted(self, name: str) -> None:
        """The other direction. An allow-list that rejects everything would
        satisfy every containment property above while quietly turning
        import into a no-op, so pin that real archives still get through."""
        assert _allowed_archive_member(name) is True

    @given(name=_mutated_allowed())
    def test_one_hostile_mutation_is_enough_to_reject(self, name: str) -> None:
        """Names that are legitimate right up to the character that makes
        them dangerous: `../models/ngram_model.json`, `C:analytics.json`,
        `packs\\id\\dictionary.txt`. This is where an allow-list is most
        likely to be one regex anchor away from too permissive."""
        if _allowed_archive_member(name):
            # The only mutation that can legitimately survive is one that
            # produced a name identical to a valid one.
            assert name in set(_MODEL_FILES.values()) or name.count("/") == 2

    def test_manifest_is_never_extracted(self) -> None:
        """It is consumed by inspect_export, never written to disk."""
        assert _allowed_archive_member("manifest.json") is False

    @given(
        name=st.builds(
            lambda prefix, suffix: f"{prefix}telemetry.json{suffix}",
            st.sampled_from(["", "models/", "packs/medical/", "../", "./", "/"]),
            st.sampled_from(["", "/", ".bak"]),
        )
    )
    def test_telemetry_can_never_be_written(self, name: str) -> None:
        """Copying anon_id across machines links contributions, which
        docs/PRIVACY.md promises not to do. A hand-edited archive must not
        be able to reintroduce it under any spelling."""
        assert _allowed_archive_member(name) is False


class TestEntryValidation:
    """`_validate_archive_entry` reads size metadata, not extracted bytes."""

    @given(size=st.integers(min_value=0, max_value=2**40))
    def test_per_file_cap_trips_on_metadata_alone(self, size: int) -> None:
        """A forged 1 TB entry must be refused without allocating anything.

        The ZipInfo here has no backing bytes at all, so if this ever needed
        to read the member to decide, the test could not pass.
        """
        entry = zipfile.ZipInfo("analytics.json")
        entry.file_size = size

        if size > _MAX_FILE_BYTES:
            with pytest.raises(DataExportError):
                _validate_archive_entry(entry)
        else:
            _validate_archive_entry(entry)

    @given(name=archive_names, size=st.integers(min_value=0, max_value=1024))
    def test_rejection_is_always_a_dataexporterror(self, name: str, size: int) -> None:
        """The bridge turns DataExportError into a user-facing string, so an
        escaping ValueError/OSError would surface as a crash instead."""
        entry = zipfile.ZipInfo(name)
        entry.file_size = size
        try:
            _validate_archive_entry(entry)
        except DataExportError:
            pass


# ---------------------------------------------------------------------------
# data_export: end-to-end import
# ---------------------------------------------------------------------------


class TestImportTouchesNothingOutside:
    @given(members=st.lists(st.tuples(archive_names, file_contents), min_size=1, max_size=6))
    def test_no_write_ever_lands_outside_the_config_dir(
        self, members: List[Tuple[str, bytes]]
    ) -> None:
        """The property the whole hardening exists for.

        A sandbox holds the config dir *and* a sibling `canary/` tree. After
        importing an arbitrary archive, the canary must be byte-identical and
        no new path may appear anywhere outside config_dir. Import is allowed
        to refuse (DataExportError) or to succeed; it is not allowed to
        write outside its own directory.
        """
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            config_dir = sandbox / "config"
            config_dir.mkdir()
            canary_dir = sandbox / "canary"
            canary_dir.mkdir()
            (canary_dir / "telemetry.json").write_text('{"anon_id": "secret"}')
            (canary_dir / "boot.ini").write_text("do not touch")

            archive = sandbox / "payload.zip"
            # Duplicate names in one zip are legal but make the "what was
            # written" bookkeeping ambiguous; drop them.
            seen = set()
            unique = []
            for name, payload in members:
                if name in seen:
                    continue
                seen.add(name)
                unique.append((name, payload))
            _build_archive(archive, unique)

            outside_before = _snapshot(canary_dir)

            try:
                import_user_data(archive, config_dir)
            except DataExportError:
                pass

            assert _snapshot(canary_dir) == outside_before, (
                f"import wrote outside config_dir with members {[n for n, _ in unique]}"
            )
            # Nothing new at the sandbox top level either.
            assert sorted(p.name for p in sandbox.iterdir()) == [
                "canary",
                "config",
                "payload.zip",
            ]

    @given(members=st.lists(st.tuples(archive_names, file_contents), max_size=6))
    def test_only_allow_listed_members_ever_appear_on_disk(
        self, members: List[Tuple[str, bytes]]
    ) -> None:
        """Extraction is allow-list, not deny-list: every file that shows up
        in config_dir must be one the allow-list admits."""
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            config_dir = sandbox / "config"
            config_dir.mkdir()
            archive = sandbox / "payload.zip"
            _build_archive(archive, list(dict(members).items()))

            try:
                import_user_data(archive, config_dir)
            except DataExportError:
                return

            for path in config_dir.rglob("*"):
                if path.is_dir():
                    continue
                rel = str(path.relative_to(config_dir)).replace("\\", "/")
                # The rescue archive import writes for rollback is ours, not
                # the payload's.
                if rel.startswith("exports/"):
                    continue
                assert _allowed_archive_member(rel), (
                    f"{rel!r} reached disk but the allow-list rejects it"
                )


class TestSchemaAndRoundTrip:
    @given(schema=st.integers(min_value=SCHEMA_VERSION + 1, max_value=2**31))
    def test_future_schema_is_always_refused(self, schema: int) -> None:
        """Half-applying an archive from a newer build is worse than
        refusing it, so this fails closed for every future version."""
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "future.zip"
            _build_archive(
                archive,
                [("analytics.json", b"{}")],
                manifest={
                    "schema_version": schema,
                    "app_version": "99.0.0",
                    "exported_at": "",
                    "files": ["analytics.json"],
                    "pack_ids": [],
                },
            )
            with pytest.raises(DataExportError, match="newer schema"):
                inspect_export(archive)

    @given(payload=st.binary(max_size=4096))
    def test_inspect_never_raises_anything_but_dataexporterror(self, payload: bytes) -> None:
        """Arbitrary bytes named .zip: a truncated download, a text file the
        user renamed, a corrupt transfer. All must come back as the typed
        error the bridge knows how to display."""
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "whatever.zip"
            src.write_bytes(payload)
            try:
                inspect_export(src)
            except DataExportError:
                pass

    @given(
        ngram=file_contents,
        analytics=file_contents,
        snippets=file_contents,
        pack_ids=st.lists(valid_pack_ids, min_size=0, max_size=3, unique=True),
    )
    def test_export_import_round_trips_byte_for_byte(
        self,
        ngram: bytes,
        analytics: bytes,
        snippets: bytes,
        pack_ids: List[str],
    ) -> None:
        """Hardening that rejects legitimate archives is just data loss, so
        pin the happy path against the same generator."""
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            source = sandbox / "source"
            (source / "models").mkdir(parents=True)
            (source / "models" / "ngram_model.json").write_bytes(ngram)
            (source / "analytics.json").write_bytes(analytics)
            (source / "snippets.json").write_bytes(snippets)
            for pid in pack_ids:
                pack = source / "packs" / pid
                pack.mkdir(parents=True)
                (pack / "dictionary.txt").write_bytes(b"alpha\nbeta\n")

            archive = sandbox / "export.zip"
            summary = export_user_data(source, archive)
            assert sorted(summary.pack_ids) == sorted(pack_ids)

            target = sandbox / "target"
            target.mkdir()
            import_user_data(archive, target)

            assert (target / "models" / "ngram_model.json").read_bytes() == ngram
            assert (target / "analytics.json").read_bytes() == analytics
            assert (target / "snippets.json").read_bytes() == snippets
            for pid in pack_ids:
                assert (target / "packs" / pid / "dictionary.txt").is_file()

    @given(
        pack_ids=st.lists(valid_pack_ids, min_size=1, max_size=3, unique=True),
    )
    def test_import_is_replace_not_merge(self, pack_ids: List[str]) -> None:
        """Packs absent from the archive are removed: the archive is the
        user's full snapshot, not a patch on top of what is already there."""
        assume("stale-pack" not in pack_ids)
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            source = sandbox / "source"
            (source / "models").mkdir(parents=True)
            for pid in pack_ids:
                pack = source / "packs" / pid
                pack.mkdir(parents=True)
                (pack / "dictionary.txt").write_bytes(b"alpha\n")

            archive = sandbox / "export.zip"
            export_user_data(source, archive)

            target = sandbox / "target"
            stale = target / "packs" / "stale-pack"
            stale.mkdir(parents=True)
            (stale / "dictionary.txt").write_bytes(b"old\n")

            import_user_data(archive, target)

            assert not stale.exists(), "a pack absent from the archive survived"
            for pid in pack_ids:
                assert (target / "packs" / pid / "dictionary.txt").is_file()


# ---------------------------------------------------------------------------
# vocabulary_pack: PackManager.import_pack
# ---------------------------------------------------------------------------


class TestPackImportStaysInsideUserPacksDir:
    @staticmethod
    def _sandbox(tmp: str) -> Tuple[PackManager, Path, Path]:
        sandbox = Path(tmp)
        builtin = sandbox / "builtin"
        builtin.mkdir()
        user = sandbox / "user_packs"
        user.mkdir()
        canary = sandbox / "canary"
        canary.mkdir()
        (canary / "important.txt").write_text("do not touch")
        return PackManager(packs_dir=builtin, user_packs_dir=user), user, sandbox

    @given(name=folder_names)
    def test_accepted_id_is_always_sanitised_and_contained(self, name: str) -> None:
        """Whatever the source folder is called, an accepted import lands at
        a directory whose name matches the id regex and which sits strictly
        under user_packs_dir."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr, user_dir, _ = self._sandbox(tmp)
            source = Path(tmp) / "src" / name
            source.mkdir(parents=True)
            (source / "dictionary.txt").write_text("alpha\nbeta\n")

            pack_id = mgr.import_pack(source)
            if pack_id is None:
                return

            assert _VALID_PACK_ID.match(pack_id), (
                f"accepted an unsanitised id {pack_id!r} from folder {name!r}"
            )
            dest = (user_dir / pack_id).resolve()
            assert dest.is_dir()
            assert user_dir.resolve() in dest.parents

    @given(name=folder_names, traversal=st.sampled_from(["", "/..", "/.", "/../.."]))
    def test_nothing_outside_user_packs_dir_is_touched(self, name: str, traversal: str) -> None:
        """import_pack calls rmtree and copytree. A source path whose `.name`
        is `..` used to be enough to aim rmtree at user_packs_dir's parent,
        which is where the canary lives."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr, user_dir, sandbox = self._sandbox(tmp)
            source = Path(tmp) / "src" / name
            source.mkdir(parents=True)
            (source / "dictionary.txt").write_text("alpha\n")

            target = Path(str(source) + traversal) if traversal else source

            before = _snapshot(sandbox / "canary")
            builtin_existed = (sandbox / "builtin").is_dir()

            mgr.import_pack(target)

            assert _snapshot(sandbox / "canary") == before, (
                f"import_pack({target}) modified files outside user_packs_dir"
            )
            assert (sandbox / "builtin").is_dir() == builtin_existed
            assert user_dir.is_dir()

    @given(name=folder_names)
    def test_symlinked_secrets_are_never_dereferenced(self, name: str) -> None:
        """shutil's default symlinks=False copies link *contents*, so a pack
        containing a link to a private key would import the key itself."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr, user_dir, sandbox = self._sandbox(tmp)
            secret = sandbox / "secret.txt"
            secret.write_text("PRIVATE KEY MATERIAL")

            source = Path(tmp) / "src" / name
            source.mkdir(parents=True)
            (source / "dictionary.txt").write_text("alpha\n")
            try:
                (source / "leak.txt").symlink_to(secret)
            except (OSError, NotImplementedError):
                pytest.skip("symlink creation not permitted on this host")

            pack_id = mgr.import_pack(source)
            if pack_id is None:
                return

            for path in (user_dir / pack_id).rglob("*"):
                if path.is_file():
                    assert "PRIVATE KEY MATERIAL" not in path.read_text(errors="ignore"), (
                        f"{path} dereferenced a symlink out of the pack"
                    )

    @given(name=folder_names)
    def test_missing_dictionary_is_always_rejected(self, name: str) -> None:
        """dictionary.txt is the one required file; without it there is no
        pack, and nothing should be created."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr, user_dir, _ = self._sandbox(tmp)
            source = Path(tmp) / "src" / name
            source.mkdir(parents=True)
            (source / "bigrams.txt").write_text("a b\n")

            assert mgr.import_pack(source) is None
            assert list(user_dir.iterdir()) == []


class TestPackImportIsIdempotent:
    @given(pack_id=valid_pack_ids, rounds=st.integers(min_value=2, max_value=4))
    def test_reimporting_the_same_pack_converges(self, pack_id: str, rounds: int) -> None:
        """Re-import rmtree's the old copy first. Repeating it must leave
        exactly one pack directory, not stack duplicates or half-delete."""
        with tempfile.TemporaryDirectory() as tmp:
            sandbox = Path(tmp)
            builtin = sandbox / "builtin"
            builtin.mkdir()
            user = sandbox / "user_packs"
            user.mkdir()
            mgr = PackManager(packs_dir=builtin, user_packs_dir=user)

            source = sandbox / "src" / pack_id
            source.mkdir(parents=True)
            (source / "dictionary.txt").write_text("alpha\nbeta\n")

            results = [mgr.import_pack(source) for _ in range(rounds)]

            assert len(set(results)) == 1, f"id drifted across imports: {results}"
            if results[0] is None:
                return
            assert sorted(p.name for p in user.iterdir()) == [results[0]]
            assert (user / results[0] / "dictionary.txt").read_text() == ("alpha\nbeta\n")

            shutil.rmtree(source.parent)
