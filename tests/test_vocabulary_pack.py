"""Tests for the vocabulary pack system."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.prediction.ngram_predictor import NgramPredictor
from src.prediction.vocabulary_pack import (
    PACK_BIGRAM_WEIGHT,
    PACK_UNIGRAM_WEIGHT,
    PackManager,
    VocabularyPack,
)

DATA_PACKS_DIR = Path(__file__).parent.parent / "data" / "packs"


# --- Fixtures ---


@pytest.fixture
def sample_pack_dir(tmp_path: Path) -> Path:
    """Create a minimal valid pack on disk."""
    pack_dir = tmp_path / "test_pack"
    pack_dir.mkdir()

    (pack_dir / "pack.json").write_text(
        json.dumps({"name": "Test Pack", "description": "For testing", "version": 1})
    )
    (pack_dir / "dictionary.txt").write_text("# Test dictionary\nalpha\nbeta\ngamma\ndelta\n")
    (pack_dir / "bigrams.txt").write_text("# Test bigrams\nalpha beta\ngamma delta\n")
    (pack_dir / "trigrams.txt").write_text("# Test trigrams\nalpha beta gamma\n")
    return pack_dir


@pytest.fixture
def sample_pack(sample_pack_dir: Path) -> VocabularyPack:
    """A loaded sample pack."""
    pack = VocabularyPack.from_directory(sample_pack_dir)
    assert pack is not None
    pack.load()
    return pack


@pytest.fixture
def packs_manager(tmp_path: Path) -> PackManager:
    """Manager with a temp packs directory containing one pack."""
    packs_dir = tmp_path / "packs"
    packs_dir.mkdir()

    # Create two packs
    for name in ("pack_a", "pack_b"):
        d = packs_dir / name
        d.mkdir()
        (d / "pack.json").write_text(
            json.dumps({"name": name.replace("_", " ").title(), "description": f"Test {name}"})
        )
        (d / "dictionary.txt").write_text(f"word_{name}_one\nword_{name}_two\n")
        (d / "bigrams.txt").write_text(f"word_{name}_one word_{name}_two\n")

    return PackManager(packs_dir=packs_dir)


# --- VocabularyPack ---


class TestVocabularyPack:
    """Pack loading and data structure."""

    def test_from_directory_valid(self, sample_pack_dir: Path):
        pack = VocabularyPack.from_directory(sample_pack_dir)
        assert pack is not None
        assert pack.name == "Test Pack"
        assert pack.description == "For testing"

    def test_from_directory_nonexistent(self, tmp_path: Path):
        result = VocabularyPack.from_directory(tmp_path / "nope")
        assert result is None

    def test_from_directory_no_metadata(self, tmp_path: Path):
        d = tmp_path / "bare_pack"
        d.mkdir()
        (d / "dictionary.txt").write_text("word\n")
        pack = VocabularyPack.from_directory(d)
        assert pack is not None
        assert pack.name == "bare_pack"  # Falls back to dir name

    def test_load_dictionary(self, sample_pack: VocabularyPack):
        assert "alpha" in sample_pack.words
        assert "beta" in sample_pack.words
        assert len(sample_pack.words) == 4

    def test_load_bigrams(self, sample_pack: VocabularyPack):
        assert "alpha" in sample_pack.bigrams
        assert "beta" in sample_pack.bigrams["alpha"]

    def test_load_trigrams(self, sample_pack: VocabularyPack):
        assert "alpha beta" in sample_pack.trigrams
        assert "gamma" in sample_pack.trigrams["alpha beta"]

    def test_load_skips_comments(self, sample_pack: VocabularyPack):
        # Comments should not be loaded as words
        for word in sample_pack.words:
            assert not word.startswith("#")

    def test_unload_clears_data(self, sample_pack: VocabularyPack):
        sample_pack.unload()
        assert len(sample_pack.words) == 0
        assert len(sample_pack.bigrams) == 0
        assert len(sample_pack.trigrams) == 0

    def test_get_info(self, sample_pack: VocabularyPack):
        info = sample_pack.get_info()
        assert info["name"] == "Test Pack"
        assert info["words"] == 4
        assert info["bigrams"] == 2
        assert info["trigrams"] == 1

    def test_enabled_default_false(self, sample_pack_dir: Path):
        pack = VocabularyPack.from_directory(sample_pack_dir)
        assert pack is not None
        assert not pack.enabled


# --- PackManager ---


class TestPackManager:
    """Pack discovery, enable/disable."""

    def test_discovers_packs(self, packs_manager: PackManager):
        available = packs_manager.get_available_packs()
        assert "pack_a" in available
        assert "pack_b" in available

    def test_no_packs_dir_is_safe(self, tmp_path: Path):
        mgr = PackManager(packs_dir=tmp_path / "nonexistent")
        assert mgr.get_available_packs() == []

    def test_enable_pack(self, packs_manager: PackManager):
        assert packs_manager.enable_pack("pack_a")
        assert "pack_a" in packs_manager.get_enabled_packs()

    def test_enable_nonexistent(self, packs_manager: PackManager):
        assert not packs_manager.enable_pack("nope")

    def test_disable_pack(self, packs_manager: PackManager):
        packs_manager.enable_pack("pack_a")
        assert packs_manager.disable_pack("pack_a")
        assert "pack_a" not in packs_manager.get_enabled_packs()

    def test_enable_is_idempotent(self, packs_manager: PackManager):
        assert packs_manager.enable_pack("pack_a")
        assert packs_manager.enable_pack("pack_a")  # Again — should not fail

    def test_disable_already_disabled(self, packs_manager: PackManager):
        assert packs_manager.disable_pack("pack_a")  # Never enabled

    def test_multiple_packs_enabled(self, packs_manager: PackManager):
        packs_manager.enable_pack("pack_a")
        packs_manager.enable_pack("pack_b")
        enabled = packs_manager.get_enabled_packs()
        assert "pack_a" in enabled
        assert "pack_b" in enabled

    def test_get_pack_info(self, packs_manager: PackManager):
        info = packs_manager.get_pack_info("pack_a")
        assert info is not None
        assert info["name"] == "Pack A"

    def test_get_all_pack_info(self, packs_manager: PackManager):
        all_info = packs_manager.get_all_pack_info()
        assert len(all_info) == 2

    def test_apply_to_predictor(self, packs_manager: PackManager):
        packs_manager.enable_pack("pack_a")
        predictor = NgramPredictor()
        packs_manager.apply_to_predictor(predictor)
        assert predictor.unigrams["word_pack_a_one"] >= PACK_UNIGRAM_WEIGHT
        assert predictor.bigrams["word_pack_a_one"]["word_pack_a_two"] >= PACK_BIGRAM_WEIGHT

    def test_disabled_pack_not_applied(self, packs_manager: PackManager):
        packs_manager.enable_pack("pack_a")
        packs_manager.disable_pack("pack_a")
        predictor = NgramPredictor()
        packs_manager.apply_to_predictor(predictor)
        # Disabled pack should not inject vocabulary
        assert predictor.unigrams.get("word_pack_a_one", 0) == 0


# --- Integration with HybridPredictor ---
#
# Built-in packs (medical / programming / etc.) were removed -- the
# vocab system is now import-only, so these tests no longer have a
# pre-shipped pack to drive against.  The TestRealPacks class that
# used to live here (per-pack dictionary / bigram / trigram structure
# checks across 5 hardcoded built-ins) is gone for the same reason.
# If a future release reintroduces a built-in pack, mirror that class
# back in for it; the `sample_pack_dir` fixture above shows the shape.


class TestPackHybridIntegration:
    """Test packs through the HybridPredictor interface."""

    @pytest.fixture
    def predictor(self, tmp_path: Path):
        pytest.importorskip("PySide6")
        from src.prediction.hybrid_predictor import HybridPredictor

        return HybridPredictor(model_dir=tmp_path / "models", enable_llm=False)

    def test_get_available_packs_returns_list(self, predictor):
        # No built-ins ship anymore; user-imported packs land in the
        # real config dir.  Just confirm the call returns a list.
        packs = predictor.get_available_packs()
        assert isinstance(packs, list)

    def test_enable_invalid_pack(self, predictor):
        assert not predictor.enable_vocabulary_pack("nonexistent")


class TestImportPackSecurity:
    """Adversarial input to PackManager.import_pack — no path traversal."""

    def _mgr(self, tmp_path: Path) -> PackManager:
        packs_dir = tmp_path / "builtin"
        packs_dir.mkdir()
        user_dir = tmp_path / "user_packs"
        user_dir.mkdir()
        return PackManager(packs_dir=packs_dir, user_packs_dir=user_dir)

    def _make_source_with_dict(self, path: Path) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        (path / "dictionary.txt").write_text("alpha\nbeta\n")
        return path

    def test_rejects_dotdot_name(self, tmp_path: Path):
        """A source folder whose .name is '..' must not blow away
        user_packs_dir's parent via rmtree / copytree."""
        mgr = self._mgr(tmp_path)
        # Build a path whose pathlib .name is literally ".."
        bad_source = self._make_source_with_dict(tmp_path / "evil")
        # pathlib path ending in '..' has .name == '..'
        traversal = Path(str(bad_source) + "/..")
        assert traversal.name == ".."
        result = mgr.import_pack(traversal)
        assert result is None
        # The builtin dir must still exist — rmtree should never have run on it
        assert (tmp_path / "builtin").is_dir()
        assert (tmp_path / "user_packs").is_dir()

    def test_rejects_control_chars_in_name(self, tmp_path: Path):
        """Non-alphanumeric junk is stripped; if nothing valid remains,
        the import is rejected rather than silently creating a directory
        with an empty or strange name."""
        mgr = self._mgr(tmp_path)
        bad = self._make_source_with_dict(tmp_path / "---")
        assert mgr.import_pack(bad) is None

    def test_accepts_normal_name(self, tmp_path: Path):
        mgr = self._mgr(tmp_path)
        src = self._make_source_with_dict(tmp_path / "My Pack")
        pack_id = mgr.import_pack(src)
        assert pack_id == "my_pack"
        assert (tmp_path / "user_packs" / "my_pack").is_dir()

    def test_destination_cannot_escape_user_packs_dir(self, tmp_path: Path):
        """Even if the sanitiser somehow passed, the resolve() check
        catches symlinked user_packs paths pointing elsewhere."""
        mgr = self._mgr(tmp_path)
        src = self._make_source_with_dict(tmp_path / "ok_pack")
        # Regular case still works
        assert mgr.import_pack(src) == "ok_pack"

    def test_rejects_reserved_windows_device_name(self, tmp_path: Path):
        """A source folder that sanitises to a Windows reserved device
        name (con, nul, com1, ...) must be rejected: it passes
        _VALID_PACK_ID (a normal-looking lowercase id) but
        dest_dir.mkdir() would raise an uncaught OSError for it on
        Windows."""
        mgr = self._mgr(tmp_path)
        src = self._make_source_with_dict(tmp_path / "con")
        assert mgr.import_pack(src) is None
        assert list((tmp_path / "user_packs").iterdir()) == []

    def test_rejects_reserved_name_case_insensitively(self, tmp_path: Path):
        mgr = self._mgr(tmp_path)
        src = self._make_source_with_dict(tmp_path / "COM1")
        assert mgr.import_pack(src) is None

    def test_accepts_name_that_merely_contains_a_reserved_word(self, tmp_path: Path):
        """Only an exact reserved-name match is rejected; a name that
        merely starts with one (e.g. "console") is a normal import."""
        mgr = self._mgr(tmp_path)
        src = self._make_source_with_dict(tmp_path / "console")
        assert mgr.import_pack(src) == "console"


class TestPackInputCaps:
    """pack.json / dictionary.txt / bigrams.txt / trigrams.txt and the
    import source tree are all bounded, mirroring the caps every other
    loader in this codebase already has (model files 50 MB, Data Backup
    archives 75 MB per file, snippets.json 1 MB). Every cap below is
    monkeypatched to a small value so the test doesn't need to write
    megabytes of fixture data, matching the pattern already used for
    _MAX_FILE_BYTES in tests/test_data_export.py."""

    def test_oversized_pack_json_falls_back_to_directory_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.prediction import vocabulary_pack as vp

        monkeypatch.setattr(vp, "_MAX_PACK_META_BYTES", 10)
        pack_dir = tmp_path / "my_pack"
        pack_dir.mkdir()
        (pack_dir / "pack.json").write_text(
            json.dumps({"name": "Way too long a name for the patched cap"})
        )
        (pack_dir / "dictionary.txt").write_text("alpha\n")

        pack = VocabularyPack.from_directory(pack_dir)

        assert pack is not None
        assert pack.name == "my_pack"  # metadata ignored; fell back to dir name

    def test_oversized_dictionary_is_skipped_entirely(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.prediction import vocabulary_pack as vp

        monkeypatch.setattr(vp, "_MAX_PACK_FILE_BYTES", 10)
        pack_dir = tmp_path / "big_pack"
        pack_dir.mkdir()
        (pack_dir / "dictionary.txt").write_text("alpha\nbeta\ngamma\ndelta\n")  # > 10 bytes

        pack = VocabularyPack.from_directory(pack_dir)
        assert pack is not None
        assert pack.load() is False
        assert pack.words == set()

    def test_dictionary_word_count_is_capped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.prediction import vocabulary_pack as vp

        monkeypatch.setattr(vp, "_MAX_PACK_WORDS", 3)
        pack_dir = tmp_path / "many_words"
        pack_dir.mkdir()
        (pack_dir / "dictionary.txt").write_text("\n".join(f"word{i}" for i in range(20)))

        pack = VocabularyPack.from_directory(pack_dir)
        assert pack is not None
        assert pack.load() is True
        assert len(pack.words) == 3

    def test_bigram_entry_count_is_capped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.prediction import vocabulary_pack as vp

        monkeypatch.setattr(vp, "_MAX_PACK_BIGRAM_ENTRIES", 2)
        pack_dir = tmp_path / "many_bigrams"
        pack_dir.mkdir()
        (pack_dir / "dictionary.txt").write_text("a\n")
        (pack_dir / "bigrams.txt").write_text("\n".join(f"w{i} w{i + 1}" for i in range(20)))

        pack = VocabularyPack.from_directory(pack_dir)
        assert pack is not None
        pack.load()
        total_bigram_entries = sum(len(v) for v in pack.bigrams.values())
        assert total_bigram_entries == 2

    def test_trigram_entry_count_is_capped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.prediction import vocabulary_pack as vp

        monkeypatch.setattr(vp, "_MAX_PACK_TRIGRAM_ENTRIES", 2)
        pack_dir = tmp_path / "many_trigrams"
        pack_dir.mkdir()
        (pack_dir / "dictionary.txt").write_text("a\n")
        (pack_dir / "trigrams.txt").write_text(
            "\n".join(f"w{i} w{i + 1} w{i + 2}" for i in range(20))
        )

        pack = VocabularyPack.from_directory(pack_dir)
        assert pack is not None
        pack.load()
        total_trigram_entries = sum(len(v) for v in pack.trigrams.values())
        assert total_trigram_entries == 2

    def test_import_rejects_oversized_source_tree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.prediction import vocabulary_pack as vp

        monkeypatch.setattr(vp, "_MAX_PACK_IMPORT_TOTAL_BYTES", 10)
        packs_dir = tmp_path / "builtin"
        packs_dir.mkdir()
        user_dir = tmp_path / "user_packs"
        user_dir.mkdir()
        mgr = PackManager(packs_dir=packs_dir, user_packs_dir=user_dir)

        source = tmp_path / "big_source"
        source.mkdir()
        (source / "dictionary.txt").write_text("x" * 100)  # > patched 10-byte cap

        assert mgr.import_pack(source) is None
        assert list(user_dir.iterdir()) == []


class TestPackMetadataIsSanitised:
    """`pack.json`'s name / description are attacker-controlled: they
    arrive with an imported pack and nothing else validates them.

    Two consumers care.  The Settings UI renders both fields, which is why
    the QML side forces `Text.PlainText`.  This module *logs* the name on
    every cap-trip and load message, and the diagnostic log is the file
    users attach to bug reports, so an embedded newline lets a pack name
    forge whole log lines in it.
    """

    @staticmethod
    def _pack_with_meta(tmp_path: Path, meta: object) -> VocabularyPack:
        pack_dir = tmp_path / "my_pack"
        pack_dir.mkdir()
        (pack_dir / "pack.json").write_text(json.dumps(meta))
        pack = VocabularyPack.from_directory(pack_dir)
        assert pack is not None
        return pack

    def test_newlines_in_the_name_are_collapsed(self, tmp_path: Path) -> None:
        pack = self._pack_with_meta(
            tmp_path,
            {"name": "Innocent\n2026-01-01 [Updater] ERROR: forged line"},
        )
        assert "\n" not in pack.name
        assert "\r" not in pack.name

    def test_newlines_in_the_description_are_collapsed(self, tmp_path: Path) -> None:
        pack = self._pack_with_meta(tmp_path, {"description": "line one\nline two"})
        assert pack.description == "line one line two"

    def test_an_overlong_field_is_bounded(self, tmp_path: Path) -> None:
        from src.prediction import vocabulary_pack as vp

        pack = self._pack_with_meta(tmp_path, {"name": "x" * 5000})
        assert len(pack.name) == vp._MAX_PACK_META_FIELD_LEN

    @pytest.mark.parametrize("value", ([1, 2], {"a": 1}, None, 7), ids=str)
    def test_a_non_string_name_falls_back_to_the_directory_name(
        self, tmp_path: Path, value: object
    ) -> None:
        """Coercing would render a JSON list as the literal "[1, 2]"."""
        pack = self._pack_with_meta(tmp_path, {"name": value})
        assert pack.name == "my_pack"

    def test_a_non_integer_version_falls_back_to_1(self, tmp_path: Path) -> None:
        pack = self._pack_with_meta(tmp_path, {"version": "not a number"})
        assert pack.version == 1

    def test_a_non_object_pack_json_falls_back_entirely(self, tmp_path: Path) -> None:
        pack = self._pack_with_meta(tmp_path, ["not", "an", "object"])
        assert pack.name == "my_pack"
        assert pack.description == ""
        assert pack.version == 1

    def test_a_normal_pack_json_is_untouched(self, tmp_path: Path) -> None:
        """The inverse test: a sanitiser that emptied every field would
        satisfy every assertion above."""
        pack = self._pack_with_meta(
            tmp_path, {"name": "Medical Terms", "description": "Clinical vocab", "version": 3}
        )
        assert pack.name == "Medical Terms"
        assert pack.description == "Clinical vocab"
        assert pack.version == 3
