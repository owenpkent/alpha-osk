"""
Vocabulary Pack system for domain-specific prediction.

Packs are additive layers of vocabulary (unigrams, bigrams, trigrams)
that users can enable/disable independently of their accessibility profile.

Pack format (on disk):
    data/packs/<pack-name>/
    ├── pack.json          # Metadata (name, description, version)
    ├── dictionary.txt     # One word per line
    ├── bigrams.txt        # word1 word2 per line (optional)
    └── trigrams.txt       # word1 word2 word3 per line (optional)
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

_logger = logging.getLogger("VocabularyPack")

# Weight for pack vocabulary (lower than user-learned so personal typing wins)
PACK_UNIGRAM_WEIGHT = 3
PACK_BIGRAM_WEIGHT = 30
PACK_TRIGRAM_WEIGHT = 30


@dataclass
class VocabularyPack:
    """A loadable vocabulary pack for a specific domain."""

    name: str
    description: str
    path: Path
    version: int = 1
    enabled: bool = False

    # Loaded data
    words: Set[str] = field(default_factory=set)
    bigrams: Dict[str, Dict[str, int]] = field(default_factory=dict)
    trigrams: Dict[str, Dict[str, int]] = field(default_factory=dict)

    @classmethod
    def from_directory(cls, pack_dir: Path) -> Optional[VocabularyPack]:
        """
        Load a vocabulary pack from a directory.

        Args:
            pack_dir: Path to the pack directory

        Returns:
            VocabularyPack if valid, None if missing/invalid
        """
        if not pack_dir.is_dir():
            return None

        # Load metadata
        meta_path = pack_dir / "pack.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text())
                name = meta.get("name", pack_dir.name)
                description = meta.get("description", "")
                version = meta.get("version", 1)
            except (json.JSONDecodeError, OSError):
                name = pack_dir.name
                description = ""
                version = 1
        else:
            name = pack_dir.name
            description = ""
            version = 1

        pack = cls(
            name=name,
            description=description,
            path=pack_dir,
            version=version,
        )
        return pack

    def load(self) -> bool:
        """
        Load vocabulary data from disk into memory.

        Returns:
            True if any data was loaded
        """
        loaded_any = False

        # Load dictionary (one word per line)
        dict_path = self.path / "dictionary.txt"
        if dict_path.exists():
            try:
                with open(dict_path) as f:
                    for line in f:
                        word = line.strip().lower()
                        if word and not word.startswith("#"):
                            self.words.add(word)
                loaded_any = bool(self.words)
                _logger.info("Pack '%s': loaded %d words", self.name, len(self.words))
            except OSError as e:
                _logger.error("Pack '%s': failed to load dictionary: %s", self.name, e)

        # Load bigrams
        bigrams_path = self.path / "bigrams.txt"
        if bigrams_path.exists():
            try:
                count = 0
                with open(bigrams_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split()
                        if len(parts) >= 2:
                            w1, w2 = parts[0].lower(), parts[1].lower()
                            if w1 not in self.bigrams:
                                self.bigrams[w1] = {}
                            self.bigrams[w1][w2] = self.bigrams[w1].get(w2, 0) + PACK_BIGRAM_WEIGHT
                            count += 1
                _logger.info("Pack '%s': loaded %d bigrams", self.name, count)
                loaded_any = loaded_any or count > 0
            except OSError as e:
                _logger.error("Pack '%s': failed to load bigrams: %s", self.name, e)

        # Load trigrams
        trigrams_path = self.path / "trigrams.txt"
        if trigrams_path.exists():
            try:
                count = 0
                with open(trigrams_path) as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split()
                        if len(parts) >= 3:
                            w1, w2, w3 = parts[0].lower(), parts[1].lower(), parts[2].lower()
                            key = f"{w1} {w2}"
                            if key not in self.trigrams:
                                self.trigrams[key] = {}
                            self.trigrams[key][w3] = (
                                self.trigrams[key].get(w3, 0) + PACK_TRIGRAM_WEIGHT
                            )
                            count += 1
                _logger.info("Pack '%s': loaded %d trigrams", self.name, count)
                loaded_any = loaded_any or count > 0
            except OSError as e:
                _logger.error("Pack '%s': failed to load trigrams: %s", self.name, e)

        return loaded_any

    def unload(self) -> None:
        """Clear loaded data from memory."""
        self.words.clear()
        self.bigrams.clear()
        self.trigrams.clear()

    def get_info(self) -> dict:
        """Return pack metadata for UI display."""
        return {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "enabled": self.enabled,
            "words": len(self.words),
            "bigrams": sum(len(v) for v in self.bigrams.values()),
            "trigrams": sum(len(v) for v in self.trigrams.values()),
        }


class PackManager:
    """
    Manages vocabulary packs — discovery, loading, and application to the predictor.

    Packs are discovered from ``data/packs/`` on init. Users can enable/disable
    packs at runtime. Enabled packs inject their vocabulary into the predictor's
    n-gram model.
    """

    def __init__(self, packs_dir: Optional[Path] = None,
                 user_packs_dir: Optional[Path] = None):
        """
        Initialize the pack manager.

        Args:
            packs_dir: Directory containing built-in pack subdirectories.
                       Defaults to ``data/packs/`` relative to project root.
            user_packs_dir: Directory for user-imported custom packs.
                           Defaults to ``<config_dir>/packs/``.
        """
        if packs_dir is None:
            packs_dir = Path(__file__).parent.parent.parent / "data" / "packs"

        if user_packs_dir is None:
            from ..platform import get_config_dir
            user_packs_dir = get_config_dir() / "packs"
            user_packs_dir.mkdir(parents=True, exist_ok=True)

        self._packs_dir = packs_dir
        self._user_packs_dir = user_packs_dir
        self._packs: Dict[str, VocabularyPack] = {}

        self._discover_packs()

    def _discover_packs(self) -> None:
        """Scan both built-in and user packs directories."""
        for packs_dir in (self._packs_dir, self._user_packs_dir):
            if not packs_dir.is_dir():
                continue
            for item in sorted(packs_dir.iterdir()):
                if item.is_dir() and not item.name.startswith("."):
                    pack = VocabularyPack.from_directory(item)
                    if pack is not None and item.name not in self._packs:
                        self._packs[item.name] = pack
                        _logger.info("Discovered pack: %s (%s)", pack.name, packs_dir)

        _logger.info("Found %d vocabulary packs", len(self._packs))

    def get_available_packs(self) -> List[str]:
        """Return list of available pack IDs."""
        return list(self._packs.keys())

    def get_pack_info(self, pack_id: str) -> Optional[dict]:
        """Return metadata for a pack."""
        pack = self._packs.get(pack_id)
        if pack:
            return pack.get_info()
        return None

    def get_all_pack_info(self) -> List[dict]:
        """Return metadata for all packs."""
        return [pack.get_info() for pack in self._packs.values()]

    def get_enabled_packs(self) -> List[str]:
        """Return list of enabled pack IDs."""
        return [pid for pid, pack in self._packs.items() if pack.enabled]

    def enable_pack(self, pack_id: str) -> bool:
        """
        Enable a pack and load its data.

        Args:
            pack_id: Pack directory name

        Returns:
            True if pack was found and enabled
        """
        pack = self._packs.get(pack_id)
        if pack is None:
            _logger.warning("Pack not found: %s", pack_id)
            return False

        if pack.enabled:
            return True  # Already enabled

        pack.load()
        pack.enabled = True
        _logger.info("Enabled pack: %s (%d words)", pack.name, len(pack.words))
        return True

    def disable_pack(self, pack_id: str) -> bool:
        """
        Disable a pack and unload its data.

        Args:
            pack_id: Pack directory name

        Returns:
            True if pack was found and disabled
        """
        pack = self._packs.get(pack_id)
        if pack is None:
            return False

        if not pack.enabled:
            return True  # Already disabled

        pack.unload()
        pack.enabled = False
        _logger.info("Disabled pack: %s", pack.name)
        return True

    def apply_to_predictor(self, predictor) -> None:
        """
        Inject all enabled packs' vocabulary into a predictor.

        Called by HybridPredictor when predictions are generated.
        Only applies packs that are currently enabled and loaded.

        Args:
            predictor: NgramPredictor instance to inject into
        """
        for pack in self._packs.values():
            if not pack.enabled:
                continue

            # Inject unigrams
            for word in pack.words:
                predictor.unigrams[word] = max(
                    predictor.unigrams.get(word, 0), PACK_UNIGRAM_WEIGHT
                )

            # Inject bigrams
            for w1, targets in pack.bigrams.items():
                for w2, weight in targets.items():
                    predictor.bigrams[w1][w2] = max(
                        predictor.bigrams[w1].get(w2, 0), weight
                    )

            # Inject trigrams
            for key, targets in pack.trigrams.items():
                for w3, weight in targets.items():
                    predictor.trigrams[key][w3] = max(
                        predictor.trigrams[key].get(w3, 0), weight
                    )

    def import_pack(self, source_dir: Path) -> Optional[str]:
        """
        Import a vocabulary pack from an external directory.

        Copies the pack folder into the user packs directory. The source
        must contain at least a ``dictionary.txt`` file. A ``pack.json``
        is optional (the folder name is used as the pack ID).

        Args:
            source_dir: Path to the pack folder to import.

        Returns:
            The pack ID (folder name) on success, None on failure.
        """
        source_dir = Path(source_dir)
        if not source_dir.is_dir():
            _logger.error("Import source is not a directory: %s", source_dir)
            return None

        # Must have at least a dictionary.txt
        if not (source_dir / "dictionary.txt").exists():
            _logger.error("Import source missing dictionary.txt: %s", source_dir)
            return None

        pack_id = source_dir.name.lower().replace(" ", "_")
        dest_dir = self._user_packs_dir / pack_id

        # Don't overwrite built-in packs
        if pack_id in self._packs and (self._packs_dir / pack_id).is_dir():
            _logger.error("Cannot overwrite built-in pack: %s", pack_id)
            return None

        try:
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(source_dir, dest_dir)

            # Generate pack.json if missing
            if not (dest_dir / "pack.json").exists():
                meta = {
                    "name": source_dir.name,
                    "description": f"Custom pack imported from {source_dir.name}",
                    "version": 1,
                }
                (dest_dir / "pack.json").write_text(json.dumps(meta))

            # Register the new pack
            pack = VocabularyPack.from_directory(dest_dir)
            if pack is not None:
                self._packs[pack_id] = pack
                _logger.info("Imported pack: %s (%d)", pack.name, len(list(dest_dir.iterdir())))
                return pack_id

        except Exception as e:
            _logger.error("Failed to import pack from %s: %s", source_dir, e)

        return None

    def get_user_packs_dir(self) -> Path:
        """Return the user packs directory path."""
        return self._user_packs_dir

    def get_pack(self, pack_id: str) -> Optional[VocabularyPack]:
        """Get a pack by ID (for testing/direct access)."""
        return self._packs.get(pack_id)
