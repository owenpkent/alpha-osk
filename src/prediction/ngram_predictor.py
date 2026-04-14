"""
N-gram based word prediction engine.

Fast, lightweight prediction using word frequency and context.
This is the "instant" layer of the hybrid approach.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

_logger = logging.getLogger("NgramPredictor")


class NgramPredictor:
    """
    N-gram based predictor for instant word suggestions.

    Uses unigram (word frequency) and bigram (word pairs) models
    to predict the next word based on context.
    """

    def __init__(self, model_path: Optional[Path] = None):
        """
        Initialize the predictor.

        Args:
            model_path: Path to saved model file. If None, starts with empty model.
        """
        # Unigram: word -> frequency
        self.unigrams: Dict[str, int] = defaultdict(int)
        # Bigram: (prev_word, word) -> frequency
        self.bigrams: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # Trigram: (prev2, prev1, word) -> frequency (optional, more context)
        self.trigrams: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Total word count for probability calculation
        self.total_words = 0

        # User-specific vocabulary boost
        self.user_vocab: Dict[str, int] = defaultdict(int)

        # Word suppression: blacklisted words never appear, dispreferred are downweighted
        self.blacklist: set[str] = set()
        self.dispreference: Dict[str, int] = defaultdict(int)

        # Auto-rehabilitation: track how many times a blacklisted word is typed
        self._blacklist_type_count: Dict[str, int] = defaultdict(int)
        self._rehabilitate_threshold = 3

        # Capitalization: lowercase → preferred form (e.g. "owen" → "Owen")
        self.capitalization: Dict[str, str] = {}
        # Words that are ALWAYS capitalized regardless of position
        self._always_capitalize: Dict[str, str] = {
            "i": "I", "i'm": "I'm", "i'll": "I'll", "i'd": "I'd",
            "i've": "I've",
        }
        # Words that are common English AND names — only capitalize at
        # sentence start, not mid-sentence (avoids "the Jack was loose")
        self._ambiguous_names: set = {
            "bell", "bud", "carol", "chase", "cliff", "dale", "dawn",
            "dean", "don", "drew", "earl", "faith", "fern", "flora",
            "frank", "gene", "glen", "grace", "grant", "guy", "heath",
            "hope", "hunter", "iris", "ivy", "jack", "jade", "jean",
            "jerry", "jimmy", "joe", "john", "joy", "june", "lance",
            "lee", "lily", "mark", "matt", "max", "mercy", "mike",
            "nick", "norm", "olive", "pat", "pearl", "penny", "peter",
            "ray", "reed", "rob", "robin", "rocky", "rose", "ruby",
            "sandy", "sue", "terry", "troy", "wade", "will", "violet",
            "may", "bill", "bob", "art", "bar",
        }
        self._load_proper_nouns()

        # Recency decay: every N learn() calls, scale user frequencies down
        # so recent words gradually outweigh older ones
        self._learn_count = 0
        self._decay_interval = 50  # decay every 50 learn() calls
        self._decay_factor = 0.95  # multiply by this on each decay

        # Load Google 10K wordlist (frequency-ranked) if available
        self._load_frequency_wordlist()

        # Fallback common words if wordlist not available
        if self.total_words == 0:
            self._common_words = [
                "the", "be", "to", "of", "and", "a", "in", "that", "have", "I",
                "it", "for", "not", "on", "with", "he", "as", "you", "do", "at",
                "this", "but", "his", "by", "from", "they", "we", "say", "her", "she",
                "or", "an", "will", "my", "one", "all", "would", "there", "their", "what",
                "so", "up", "out", "if", "about", "who", "get", "which", "go", "me",
                "is", "are", "was", "were", "been", "being", "am", "can", "could", "may",
                "might", "must", "shall", "should", "will", "would", "need", "want", "like",
                "hello", "hi", "thanks", "thank", "please", "yes", "no", "okay", "ok",
            ]
            for word in self._common_words:
                self.unigrams[word] = 100
            self.total_words = len(self._common_words) * 100

        # Load saved model if provided
        if model_path and model_path.exists():
            self.load(model_path)

    def _load_frequency_wordlist(self) -> None:
        """
        Load Google 10K wordlist as frequency-ranked vocabulary.

        Words are ranked by frequency in Google's Trillion Word Corpus.
        Position in file = frequency rank (line 1 = most common word).
        """
        wordlist_path = (
            Path(__file__).parent.parent.parent / "data" / "google-10000-english-usa-no-swears.txt"
        )

        if not wordlist_path.exists():
            _logger.debug("Google 10K wordlist not found: %s", wordlist_path)
            return

        try:
            with open(wordlist_path, "r") as f:
                words = [line.strip().lower() for line in f if line.strip()]

            # Assign frequency based on position (higher = more common)
            # Top word gets 10000, second gets 9999, etc.
            max_freq = len(words)
            for rank, word in enumerate(words):
                frequency = max_freq - rank
                self.unigrams[word] = frequency
                self.total_words += frequency

            _logger.info("Google 10K wordlist loaded: %d words", len(words))
        except Exception as e:
            _logger.warning("Failed to load Google 10K wordlist: %s", e)

        # Load supplementary 20K wordlist (lower frequency tier)
        supplement_path = (
            Path(__file__).parent.parent.parent / "data" / "google-20000-supplement.txt"
        )
        if supplement_path.exists():
            try:
                with open(supplement_path, "r") as f:
                    supplement = [line.strip().lower() for line in f if line.strip()]
                for rank, word in enumerate(supplement):
                    if word not in self.unigrams:
                        # Lower frequency tier: these words rank below the 10K list
                        frequency = max(1, 500 - rank // 20)
                        self.unigrams[word] = frequency
                        self.total_words += frequency
                _logger.info("Supplement wordlist loaded: %d words", len(supplement))
            except Exception as e:
                _logger.warning("Failed to load supplement wordlist: %s", e)

    def _load_proper_nouns(self) -> None:
        """Load built-in proper nouns for auto-capitalization."""
        path = Path(__file__).parent.parent.parent / "data" / "proper_nouns.txt"
        if not path.exists():
            return
        try:
            with open(path, "r") as f:
                for line in f:
                    word = line.strip()
                    if word and not word.startswith("#"):
                        self.capitalization[word.lower()] = word
            _logger.info("Proper nouns loaded: %d entries", len(self.capitalization))
        except Exception as e:
            _logger.warning("Failed to load proper nouns: %s", e)

    def learn_capitalization(self, word: str) -> bool:
        """Learn preferred capitalization from user typing.

        Only stores non-trivial capitalization (not all-lower or first-letter-upper
        for words that aren't already known proper nouns).  Single-character
        words are skipped (handled by _always_capitalize).

        Returns True if a new or updated capitalization was saved.
        """
        if not word or len(word) < 2:
            return False
        lower = word.lower()
        existing = self.capitalization.get(lower)
        # Always learn if user typed something with mixed case like "iPhone"
        # or capitalized a word that isn't in our proper nouns list
        if word != lower and word != lower.capitalize():
            # Unusual casing like "iPhone", "McDonald" — always learn
            self.capitalization[lower] = word
        elif word[0].isupper() and word[1:].islower():
            # Standard proper noun casing: "Owen", "Paris"
            # Learn it (may override existing entry with user preference)
            self.capitalization[lower] = word
        else:
            return False
        return self.capitalization.get(lower) != existing

    def get_capitalized(self, word: str, sentence_start: bool = False) -> str:
        """Return context-aware capitalization for a word.

        Three tiers (same model as Android/Gboard):
        1. Always capitalize: "I", "I'm", "I'll", "I'd", "I've"
        2. Sentence-start only: proper nouns that are also common words
           (e.g. "jack", "may", "will") — only capitalized after .!? or
           at the start of input.
        3. Always capitalize: unambiguous proper nouns ("Monday", "Paris",
           "iPhone") and user-taught capitalizations.

        Args:
            word: The word to capitalize (usually lowercase).
            sentence_start: True if this word follows .!? or is the first
                word in the input.
        """
        lower = word.lower()

        # Tier 1: always capitalize
        if lower in self._always_capitalize:
            return self._always_capitalize[lower]

        preferred = self.capitalization.get(lower)
        if preferred is None:
            # No capitalization rule — return as-is, or capitalize at
            # sentence start (like Android does for all words)
            return word.capitalize() if sentence_start else word

        # Tier 2: ambiguous name/word — only capitalize at sentence start
        if lower in self._ambiguous_names and not sentence_start:
            return word

        # Tier 3: unambiguous proper noun or user-taught — always capitalize
        return preferred

    def predict(self, context: str, n: int = 5) -> List[str]:
        """
        Predict next words based on context.

        Args:
            context: The text typed so far (full or partial word at end)
            n: Number of predictions to return

        Returns:
            List of predicted words, most likely first
        """
        # IMPORTANT: Check for trailing space BEFORE stripping
        # Trailing space = user finished word, predict NEXT word
        # No trailing space = user typing, complete CURRENT word
        ends_with_space = context.endswith(" ")

        context_clean = context.lower().strip()
        if not context_clean:
            return self._top_unigrams(n)

        # Split into words
        words = self._tokenize(context_clean)

        # Check if user is mid-word (no trailing space in original)
        partial_word = ""
        if not ends_with_space and words:
            # User is typing a partial word - complete it
            partial_word = words[-1]
            words = words[:-1]
        # else: User finished word (space at end) - predict next word

        # Get candidates
        candidates: Dict[str, float] = {}

        # Trigram predictions (if we have 2+ previous words)
        if len(words) >= 2:
            key = f"{words[-2]} {words[-1]}"
            if key in self.trigrams:
                for word, freq in self.trigrams[key].items():
                    if self._matches_partial(word, partial_word):
                        # Weight trigrams highest
                        candidates[word] = candidates.get(word, 0) + freq * 3

        # Bigram predictions (if we have 1+ previous words)
        if len(words) >= 1:
            prev_word = words[-1]
            if prev_word in self.bigrams:
                for word, freq in self.bigrams[prev_word].items():
                    if self._matches_partial(word, partial_word):
                        candidates[word] = candidates.get(word, 0) + freq * 2  # Weight bigrams

        # Unigram fallback + partial matching
        for word, freq in self.unigrams.items():
            if self._matches_partial(word, partial_word):
                candidates[word] = candidates.get(word, 0) + freq * 0.5

        # Boost user vocabulary
        for word, boost in self.user_vocab.items():
            if word in candidates:
                candidates[word] *= (1 + boost * 0.1)

        # Sort by score and return top n
        sorted_candidates = sorted(candidates.items(), key=lambda x: -x[1])
        return [word for word, _ in sorted_candidates[:n]]

    def _matches_partial(self, word: str, partial: str) -> bool:
        """Check if word matches partial input."""
        if not partial:
            return True
        return word.startswith(partial)

    def _top_unigrams(self, n: int) -> List[str]:
        """Get top n words by frequency."""
        sorted_words = sorted(self.unigrams.items(), key=lambda x: -x[1])
        return [word for word, _ in sorted_words[:n]]

    def _tokenize(self, text: str) -> List[str]:
        """Split text into words."""
        # Simple tokenization: split on non-alphanumeric
        words = re.findall(r"[a-zA-Z']+", text.lower())
        return words

    def learn(self, text: str) -> List[str]:
        """
        Learn from new text, updating n-gram frequencies.

        Args:
            text: Text to learn from

        Returns:
            List of words that were new to user_vocab (first time learned).
        """
        words = self._tokenize(text)
        if not words:
            return []

        new_words: List[str] = []

        # Update unigrams
        for word in words:
            was_new = word not in self.user_vocab
            self.unigrams[word] += 1
            self.user_vocab[word] += 1
            self.total_words += 1
            if was_new:
                new_words.append(word)

        # Update bigrams
        for i in range(1, len(words)):
            prev_word = words[i - 1]
            curr_word = words[i]
            self.bigrams[prev_word][curr_word] += 1

        # Update trigrams
        for i in range(2, len(words)):
            key = f"{words[i-2]} {words[i-1]}"
            curr_word = words[i]
            self.trigrams[key][curr_word] += 1

        # Periodic recency decay so old words don't dominate
        self._learn_count += 1
        if self._learn_count >= self._decay_interval:
            self._apply_decay()
            self._learn_count = 0

        return new_words

    def _apply_decay(self) -> None:
        """Scale down user-learned frequencies so recent words outweigh old ones."""
        factor = self._decay_factor
        min_freq = 1

        # Decay user vocab boost
        to_remove = []
        for word in self.user_vocab:
            self.user_vocab[word] = int(self.user_vocab[word] * factor)
            if self.user_vocab[word] < min_freq:
                to_remove.append(word)
        for word in to_remove:
            del self.user_vocab[word]

        # Decay user-learned bigrams (only those above base dictionary levels)
        for prev_word in list(self.bigrams):
            for word in list(self.bigrams[prev_word]):
                self.bigrams[prev_word][word] = max(
                    min_freq, int(self.bigrams[prev_word][word] * factor)
                )

        _logger.debug("Applied recency decay (factor=%.2f)", factor)

    def blacklist_word(self, word: str) -> None:
        """Permanently suppress a word from predictions."""
        self.blacklist.add(word.lower())
        self._blacklist_type_count.pop(word.lower(), None)
        _logger.info("Blacklisted word: %s", word)

    def unblacklist_word(self, word: str) -> None:
        """Re-enable a previously blacklisted word."""
        self.blacklist.discard(word.lower())
        self._blacklist_type_count.pop(word.lower(), None)
        _logger.info("Unblacklisted word: %s", word)

    def mark_bad(self, word: str) -> None:
        """Downweight a word in future predictions."""
        self.dispreference[word.lower()] += 1
        _logger.info("Marked bad: %s (weight now %d)", word, self.dispreference[word.lower()])

    def remove_dispreference(self, word: str) -> None:
        """Remove dispreference penalty from a word."""
        word_lower = word.lower()
        if word_lower in self.dispreference:
            del self.dispreference[word_lower]
            _logger.info("Removed dispreference: %s", word)

    def is_suppressed(self, word: str) -> bool:
        """Check if a word is blacklisted."""
        return word.lower() in self.blacklist

    def get_dispreference(self, word: str) -> int:
        """Get the dispreference weight for a word."""
        return self.dispreference.get(word.lower(), 0)

    def record_typed_word(self, word: str) -> Optional[str]:
        """Track typed words for auto-rehabilitation of blacklisted words.

        If a blacklisted word is manually typed enough times, it is
        automatically restored to predictions.

        Returns the word if rehabilitated, None otherwise.
        """
        word_lower = word.lower()
        if word_lower in self.blacklist:
            self._blacklist_type_count[word_lower] += 1
            if self._blacklist_type_count[word_lower] >= self._rehabilitate_threshold:
                self.unblacklist_word(word_lower)
                _logger.info("Auto-rehabilitated word: %s (typed %d times)",
                             word_lower, self._rehabilitate_threshold)
                return word_lower
        return None

    def learn_word(self, word: str) -> None:
        """Learn a single word (boost its frequency)."""
        word = word.lower().strip()
        if word:
            self.unigrams[word] += 5
            self.user_vocab[word] += 5
            self.total_words += 5

    def save(self, path: Path) -> None:
        """Save model to JSON file."""
        data = {
            "unigrams": dict(self.unigrams),
            "bigrams": {k: dict(v) for k, v in self.bigrams.items()},
            "trigrams": {k: dict(v) for k, v in self.trigrams.items()},
            "user_vocab": dict(self.user_vocab),
            "total_words": self.total_words,
            "blacklist": sorted(self.blacklist),
            "dispreference": dict(self.dispreference),
            "blacklist_type_count": dict(self._blacklist_type_count),
            "capitalization": dict(self.capitalization),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
        _logger.info("Model saved to %s", path)

    def load(self, path: Path) -> None:
        """Load model from JSON file."""
        try:
            with open(path) as f:
                data = json.load(f)

            self.unigrams = defaultdict(int, data.get("unigrams", {}))
            self.bigrams = defaultdict(
                lambda: defaultdict(int),
                {k: defaultdict(int, v) for k, v in data.get("bigrams", {}).items()}
            )
            self.trigrams = defaultdict(
                lambda: defaultdict(int),
                {k: defaultdict(int, v) for k, v in data.get("trigrams", {}).items()}
            )
            self.user_vocab = defaultdict(int, data.get("user_vocab", {}))
            self.total_words = data.get("total_words", 0)
            self.blacklist = set(data.get("blacklist", []))
            self.dispreference = defaultdict(int, data.get("dispreference", {}))
            self._blacklist_type_count = defaultdict(int, data.get("blacklist_type_count", {}))
            # Merge saved capitalization with built-in proper nouns (user overrides win)
            saved_caps = data.get("capitalization", {})
            self.capitalization.update(saved_caps)
            _logger.info("Model loaded from %s (%d blacklisted, %d capitalizations)",
                         path, len(self.blacklist), len(self.capitalization))
        except Exception as e:
            _logger.warning("Failed to load model from %s: %s", path, e)

    def load_corpus(self, text: str) -> None:
        """Load a large corpus for initial training."""
        _logger.info("Loading corpus (%d chars)...", len(text))
        self.learn(text)
        _logger.info("Corpus loaded. Total words: %d", self.total_words)

    def load_base_dictionary(self, dict_path: Optional[Path] = None) -> bool:
        """
        Load base dictionary file to bootstrap predictions.

        Args:
            dict_path: Path to dictionary file. If None, uses default location.

        Returns:
            True if loaded successfully
        """
        if dict_path is None:
            # Default location relative to this file
            dict_path = Path(__file__).parent.parent.parent / "data" / "base_dictionary.txt"

        if not dict_path.exists():
            _logger.warning("Base dictionary not found: %s", dict_path)
            return False

        try:
            with open(dict_path, "r") as f:
                content = f.read()

            # Process each line
            for line in content.split("\n"):
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                # Learn the words/phrases on this line
                self.learn(line)

            _logger.info("Base dictionary loaded: %d total words", self.total_words)
            return True
        except Exception as e:
            _logger.error("Failed to load base dictionary: %s", e)
            return False

    def load_common_bigrams(self, bigrams_path: Optional[Path] = None) -> bool:
        """
        Load common word pairs for better next-word prediction.

        Args:
            bigrams_path: Path to bigrams file. If None, uses default location.

        Returns:
            True if loaded successfully
        """
        if bigrams_path is None:
            bigrams_path = Path(__file__).parent.parent.parent / "data" / "common_bigrams.txt"

        if not bigrams_path.exists():
            _logger.debug("Common bigrams file not found: %s", bigrams_path)
            return False

        try:
            count = 0
            with open(bigrams_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        word1, word2 = parts[0].lower(), parts[1].lower()
                        # High weight for curated bigrams
                        self.bigrams[word1][word2] += 50
                        count += 1

            _logger.info("Common bigrams loaded: %d pairs", count)
            return True
        except Exception as e:
            _logger.warning("Failed to load common bigrams: %s", e)
            return False

    def load_common_trigrams(self, trigrams_path: Optional[Path] = None) -> bool:
        """
        Load common three-word sequences for better prediction.

        Args:
            trigrams_path: Path to trigrams file. If None, uses default location.

        Returns:
            True if loaded successfully
        """
        if trigrams_path is None:
            trigrams_path = (
                Path(__file__).parent.parent.parent / "data" / "common_trigrams.txt"
            )

        if not trigrams_path.exists():
            _logger.debug("Common trigrams file not found: %s", trigrams_path)
            return False

        try:
            count = 0
            with open(trigrams_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) >= 3:
                        w1, w2, w3 = parts[0].lower(), parts[1].lower(), parts[2].lower()
                        key = f"{w1} {w2}"
                        # High weight for curated trigrams
                        self.trigrams[key][w3] += 50
                        # Also reinforce the bigrams within the trigram
                        self.bigrams[w1][w2] += 10
                        self.bigrams[w2][w3] += 10
                        count += 1

            _logger.info("Common trigrams loaded: %d sequences", count)
            return True
        except Exception as e:
            _logger.warning("Failed to load common trigrams: %s", e)
            return False

    def clear_user_data(self) -> None:
        """Clear all user-learned data and rebuild from base dictionaries."""
        # Wipe everything — unigrams, bigrams, trigrams all contain
        # user-learned entries that can't be separated in-place.
        self.user_vocab.clear()
        self.unigrams.clear()
        self.bigrams.clear()
        self.trigrams.clear()
        self.total_words = 0
        self.blacklist.clear()
        self.dispreference.clear()
        self._blacklist_type_count.clear()
        self._learn_count = 0
        # Clear learned capitalization so user-typed forms don't persist
        self.capitalization.clear()

        # Rebuild base vocabulary from wordlists
        self._load_frequency_wordlist()
        self._load_proper_nouns()
        _logger.info("User data cleared, base dictionary reloaded")

    def get_stats(self) -> dict:
        """Get prediction engine statistics."""
        return {
            "total_words": self.total_words,
            "unique_words": len(self.unigrams),
            "bigrams": sum(len(v) for v in self.bigrams.values()),
            "trigrams": sum(len(v) for v in self.trigrams.values()),
            "user_words": len(self.user_vocab),
        }
