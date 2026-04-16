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
    (pack_dir / "dictionary.txt").write_text(
        "# Test dictionary\nalpha\nbeta\ngamma\ndelta\n"
    )
    (pack_dir / "bigrams.txt").write_text(
        "# Test bigrams\nalpha beta\ngamma delta\n"
    )
    (pack_dir / "trigrams.txt").write_text(
        "# Test trigrams\nalpha beta gamma\n"
    )
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


# --- Real packs on disk ---


class TestRealPacks:
    """Verify the actual packs in data/packs/ are valid."""

    @pytest.fixture(params=["medical", "programming", "academic", "gaming", "business"])
    def pack_id(self, request) -> str:
        return request.param

    def test_pack_directory_exists(self, pack_id: str):
        assert (DATA_PACKS_DIR / pack_id).is_dir()

    def test_pack_has_metadata(self, pack_id: str):
        meta = DATA_PACKS_DIR / pack_id / "pack.json"
        assert meta.exists()
        data = json.loads(meta.read_text())
        assert "name" in data
        assert "description" in data

    def test_pack_has_dictionary(self, pack_id: str):
        dict_path = DATA_PACKS_DIR / pack_id / "dictionary.txt"
        assert dict_path.exists()
        words = [
            line.strip()
            for line in dict_path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        assert len(words) >= 50, f"Pack '{pack_id}' has only {len(words)} words"

    def test_dictionary_is_one_word_per_line(self, pack_id: str):
        dict_path = DATA_PACKS_DIR / pack_id / "dictionary.txt"
        for i, line in enumerate(dict_path.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Allow hyphenated compound words and single words
            assert " " not in line, (
                f"Pack '{pack_id}' line {i}: multi-word entry {line!r}"
            )

    def test_pack_loads_successfully(self, pack_id: str):
        pack = VocabularyPack.from_directory(DATA_PACKS_DIR / pack_id)
        assert pack is not None
        assert pack.load()
        assert len(pack.words) > 0

    def test_pack_bigrams_valid(self, pack_id: str):
        bigrams_path = DATA_PACKS_DIR / pack_id / "bigrams.txt"
        if not bigrams_path.exists():
            pytest.skip("No bigrams file")
        for i, line in enumerate(bigrams_path.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            assert len(parts) >= 2, (
                f"Pack '{pack_id}' bigrams line {i}: needs 2+ words, got {line!r}"
            )

    def test_pack_trigrams_valid(self, pack_id: str):
        trigrams_path = DATA_PACKS_DIR / pack_id / "trigrams.txt"
        if not trigrams_path.exists():
            pytest.skip("No trigrams file")
        for i, line in enumerate(trigrams_path.read_text().splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            assert len(parts) >= 3, (
                f"Pack '{pack_id}' trigrams line {i}: needs 3+ words, got {line!r}"
            )


# --- Integration with HybridPredictor ---


class TestPackHybridIntegration:
    """Test packs through the HybridPredictor interface."""

    @pytest.fixture
    def predictor(self, tmp_path: Path):
        pytest.importorskip("PySide6")
        from src.prediction.hybrid_predictor import HybridPredictor

        return HybridPredictor(model_dir=tmp_path / "models", enable_llm=False)

    def test_get_available_packs_returns_list(self, predictor):
        packs = predictor.get_available_packs()
        assert isinstance(packs, list)
        # Should find our 5 real packs
        assert len(packs) >= 5

    def test_enable_and_disable_pack(self, predictor):
        assert predictor.enable_vocabulary_pack("medical")
        assert "medical" in predictor.get_enabled_packs()
        assert predictor.disable_vocabulary_pack("medical")
        assert "medical" not in predictor.get_enabled_packs()

    def test_enable_invalid_pack(self, predictor):
        assert not predictor.enable_vocabulary_pack("nonexistent")

    def test_enabled_pack_affects_predictions(self, predictor):
        predictor.enable_vocabulary_pack("medical")
        # Medical terms should now be known to the predictor
        assert predictor._ngram.unigrams.get("diagnosis", 0) > 0


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
