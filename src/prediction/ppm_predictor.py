"""
PPM (Prediction by Partial Matching) language model.

Character-level prediction with adaptive learning, inspired by Dasher.
Uses variable-length context (up to 10 characters) with escape mechanism.

Key advantages over word-level n-grams:
- Handles partial words naturally
- Adapts to user's typing patterns
- Works for any language without word boundaries
- Better probability estimates for rare sequences
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_logger = logging.getLogger("PPMPredictor")


@dataclass
class PPMNode:
    """Node in the PPM trie structure."""
    count: int = 0
    children: Dict[str, "PPMNode"] = field(default_factory=dict)

    def get_child(self, char: str) -> Optional["PPMNode"]:
        """Get child node for character."""
        return self.children.get(char)

    def add_child(self, char: str) -> "PPMNode":
        """Add or get child node for character."""
        if char not in self.children:
            self.children[char] = PPMNode()
        return self.children[char]

    def total_children_count(self) -> int:
        """Get total count of all children."""
        return sum(child.count for child in self.children.values())

    def num_children(self) -> int:
        """Get number of unique children (for escape probability)."""
        return len(self.children)


class PPMPredictor:
    """
    PPM language model for character-level prediction.

    Uses Prediction by Partial Matching (PPM) algorithm:
    1. Look for longest matching context
    2. If character not found, "escape" to shorter context
    3. Blend probabilities from multiple context lengths

    This is the core algorithm used by Dasher for 25+ years.
    """

    def __init__(
        self,
        max_order: int = 8,
        alphabet: Optional[str] = None,
        model_path: Optional[Path] = None
    ):
        """
        Initialize PPM predictor.

        Args:
            max_order: Maximum context length (default 8 characters)
            alphabet: Valid characters (default: lowercase + common punctuation)
            model_path: Path to saved model file
        """
        self.max_order = max_order

        # Default alphabet
        if alphabet is None:
            alphabet = "abcdefghijklmnopqrstuvwxyz '.,!?-"
        self.alphabet = set(alphabet)

        # Root of the PPM trie
        self.root = PPMNode()

        # Escape probability method (PPMD uses this)
        # escape_prob = num_unique_children / (total_count + num_unique_children)
        self.escape_method = "ppmd"

        # Statistics
        self.total_chars = 0

        # Load saved model if provided
        if model_path and model_path.exists():
            self.load(model_path)

    def train(self, text: str) -> None:
        """
        Train the model on a text corpus.

        Args:
            text: Training text
        """
        text = self._normalize(text)
        if len(text) < 2:
            return

        _logger.info("Training PPM on %d characters...", len(text))

        # Process each character with its context
        for i in range(len(text)):
            # Get context (up to max_order characters before current)
            start = max(0, i - self.max_order)
            context = text[start:i]
            char = text[i]

            # Update model for each context length
            self._update(context, char)

        self.total_chars += len(text)
        _logger.info("Training complete. Total chars: %d", self.total_chars)

    def _update(self, context: str, char: str) -> None:
        """Update model with context -> char observation."""
        # Update for each suffix of context (including empty)
        for order in range(len(context) + 1):
            suffix = context[-(order):] if order > 0 else ""

            # Navigate to context node
            node = self.root
            for c in suffix:
                node = node.add_child(c)

            # Add character as child
            child = node.add_child(char)
            child.count += 1

    def _normalize(self, text: str) -> str:
        """Normalize text to valid alphabet."""
        text = text.lower()
        return "".join(c if c in self.alphabet else " " for c in text)

    def get_probabilities(self, context: str) -> Dict[str, float]:
        """
        Get probability distribution over next characters.

        Uses PPM escape mechanism: blend probabilities from
        multiple context lengths.

        Args:
            context: Previous characters (up to max_order)

        Returns:
            Dict mapping character -> probability
        """
        context = self._normalize(context)
        context = context[-self.max_order:]  # Limit context length

        # Initialize with uniform over alphabet
        probs = {c: 1.0 / len(self.alphabet) for c in self.alphabet}

        # Get blended probabilities
        blended = self._blend_probabilities(context)

        # Merge with uniform (smoothing)
        for char, prob in blended.items():
            if char in probs:
                # Weight blended probabilities higher
                probs[char] = 0.1 * probs[char] + 0.9 * prob

        # Normalize
        total = sum(probs.values())
        if total > 0:
            probs = {c: p / total for c, p in probs.items()}

        return probs

    def _blend_probabilities(self, context: str) -> Dict[str, float]:
        """
        Compute blended probabilities using PPM escape.

        Implements PPMD escape estimation.
        """
        probs: Dict[str, float] = {}
        excluded: Set[str] = set()
        escape_weight = 1.0

        # Try each context length from longest to shortest
        for order in range(len(context), -1, -1):
            suffix = context[-(order):] if order > 0 else ""

            # Navigate to context node
            node = self.root
            found = True
            for c in suffix:
                child = node.get_child(c)
                if child is None:
                    found = False
                    break
                node = child

            if not found:
                continue

            # Get probabilities from this context
            total_count = node.total_children_count()
            num_unique = node.num_children()

            if total_count == 0:
                continue

            # PPMD escape probability
            escape_prob = num_unique / (total_count + num_unique)

            # Add probability for each child not yet seen
            for char, child in node.children.items():
                if char not in excluded:
                    # Probability at this level
                    char_prob = child.count / (total_count + num_unique)

                    # Weight by escape probability from higher orders
                    probs[char] = probs.get(char, 0) + escape_weight * char_prob
                    excluded.add(char)

            # Update escape weight for next (shorter) context
            escape_weight *= escape_prob

        return probs

    def predict_word(self, context: str, partial: str = "", n: int = 5) -> List[Tuple[str, float]]:
        """
        Predict word completions given context and partial word.

        Args:
            context: Previous text (for character probabilities)
            partial: Partial word being typed
            n: Number of predictions to return

        Returns:
            List of (word, probability) tuples
        """
        context = self._normalize(context)
        partial = partial.lower()

        # Use beam search to find likely completions
        completions = self._beam_search_words(context, partial, beam_width=n * 3)

        # Return top n
        return completions[:n]

    def _beam_search_words(
        self,
        context: str,
        partial: str,
        beam_width: int = 15,
        max_length: int = 15
    ) -> List[Tuple[str, float]]:
        """
        Beam search for word completions.

        Args:
            context: Character context
            partial: Partial word typed
            beam_width: Number of candidates to keep at each step
            max_length: Maximum word length

        Returns:
            List of (word, probability) tuples
        """
        # Start with partial word
        beam = [(partial, 1.0, context + partial)]
        completed = []

        for _ in range(max_length - len(partial)):
            new_beam = []

            for word, prob, ctx in beam:
                # Get next character probabilities
                char_probs = self.get_probabilities(ctx)

                for char, char_prob in char_probs.items():
                    new_prob = prob * char_prob

                    if char == " ":
                        # Word complete
                        if len(word) > 1:
                            completed.append((word, new_prob))
                    else:
                        # Continue building word
                        new_word = word + char
                        new_ctx = ctx + char
                        new_beam.append((new_word, new_prob, new_ctx))

            # Keep top candidates
            new_beam.sort(key=lambda x: -x[1])
            beam = new_beam[:beam_width]

            if not beam:
                break

        # Add remaining beam entries as completions
        for word, prob, _ in beam:
            if len(word) > 1:
                completed.append((word, prob))

        # Sort by probability
        completed.sort(key=lambda x: -x[1])

        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for word, prob in completed:
            if word not in seen:
                seen.add(word)
                unique.append((word, prob))

        return unique

    def predict_next_chars(self, context: str, n: int = 5) -> List[Tuple[str, float]]:
        """
        Predict most likely next characters.

        Args:
            context: Previous text
            n: Number of predictions

        Returns:
            List of (char, probability) tuples
        """
        probs = self.get_probabilities(context)
        sorted_probs = sorted(probs.items(), key=lambda x: -x[1])
        return sorted_probs[:n]

    def learn_text(self, text: str) -> None:
        """
        Learn from a single piece of text (e.g., user input).

        Args:
            text: Text to learn from
        """
        self.train(text)

    def learn_word(self, word: str, context: str = "") -> None:
        """
        Learn a single word with optional context.

        Args:
            word: Word to learn
            context: Previous context (optional)
        """
        # Learn the word with some context
        full = context + " " + word if context else word
        self.train(full)

    def save(self, path: Path) -> None:
        """Save model to JSON file."""
        def node_to_dict(node: PPMNode) -> dict:
            return {
                "count": node.count,
                "children": {
                    char: node_to_dict(child)
                    for char, child in node.children.items()
                }
            }

        data = {
            "max_order": self.max_order,
            "alphabet": "".join(sorted(self.alphabet)),
            "total_chars": self.total_chars,
            "root": node_to_dict(self.root),
        }

        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

        _logger.info("PPM model saved to %s", path)

    # Same defensive bound as the n-gram model: a full PPM trie stays
    # well under 50 MB even after extended training; beyond that we
    # refuse to load rather than risk OOM during recursive rebuild.
    _MAX_MODEL_FILE_BYTES = 50 * 1024 * 1024

    def load(self, path: Path) -> None:
        """Load model from JSON file."""
        def dict_to_node(d: dict) -> PPMNode:
            node = PPMNode(count=d.get("count", 0))
            for char, child_dict in d.get("children", {}).items():
                node.children[char] = dict_to_node(child_dict)
            return node

        try:
            file_size = path.stat().st_size
            if file_size > self._MAX_MODEL_FILE_BYTES:
                _logger.warning(
                    "PPM model %s too large (%d bytes); skipping load.",
                    path, file_size,
                )
                return

            with open(path) as f:
                data = json.load(f)

            self.max_order = data.get("max_order", self.max_order)
            self.alphabet = set(data.get("alphabet", self.alphabet))
            self.total_chars = data.get("total_chars", 0)
            self.root = dict_to_node(data.get("root", {}))

            _logger.info("PPM model loaded from %s (%d chars)", path, self.total_chars)
        except Exception as e:
            _logger.error("Failed to load PPM model: %s", e)

    def load_training_text(self, path: Path) -> bool:
        """
        Load and train on a text file.

        Args:
            path: Path to text file

        Returns:
            True if loaded successfully
        """
        if not path.exists():
            _logger.warning("Training text not found: %s", path)
            return False

        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.train(text)
            return True
        except Exception as e:
            _logger.error("Failed to load training text: %s", e)
            return False

    def get_stats(self) -> dict:
        """Get model statistics."""
        def count_nodes(node: PPMNode) -> int:
            return 1 + sum(count_nodes(c) for c in node.children.values())

        return {
            "max_order": self.max_order,
            "alphabet_size": len(self.alphabet),
            "total_chars": self.total_chars,
            "trie_nodes": count_nodes(self.root),
        }

    def get_context_entropy(self, context: str) -> float:
        """
        Get entropy of the probability distribution for context.

        Lower entropy = more confident predictions.

        Args:
            context: Character context

        Returns:
            Entropy in bits
        """
        probs = self.get_probabilities(context)
        entropy = 0.0
        for prob in probs.values():
            if prob > 0:
                entropy -= prob * math.log2(prob)
        return entropy


class PPMWordPredictor:
    """
    Word-level wrapper around PPM character model.

    Combines character-level PPM predictions with a word dictionary
    for practical word completion.
    """

    def __init__(
        self,
        ppm: Optional[PPMPredictor] = None,
        dictionary: Optional[Set[str]] = None
    ):
        """
        Initialize word predictor.

        Args:
            ppm: Character-level PPM model (creates one if not provided)
            dictionary: Set of valid words (loads default if not provided)
        """
        self.ppm = ppm or PPMPredictor()
        self.dictionary = dictionary or set()

        # Cache for word completions
        self._completion_cache: Dict[str, List[str]] = {}
        self._cache_max_size = 1000

    def load_dictionary(self, path: Path) -> bool:
        """Load word dictionary from file."""
        if not path.exists():
            _logger.warning("Dictionary not found: %s", path)
            return False

        try:
            with open(path) as f:
                for line in f:
                    word = line.strip().lower()
                    if word and not word.startswith("#"):
                        self.dictionary.add(word)

            _logger.info("Dictionary loaded: %d words", len(self.dictionary))
            return True
        except Exception as e:
            _logger.error("Failed to load dictionary: %s", e)
            return False

    def predict(self, context: str, n: int = 5) -> List[str]:
        """
        Predict words given context.

        Args:
            context: Text typed so far
            n: Number of predictions

        Returns:
            List of predicted words
        """
        # IMPORTANT: Check for trailing space BEFORE stripping
        # Trailing space = predict NEXT word, not complete current
        ends_with_space = context.endswith(" ")

        context_clean = context.lower().strip()

        # Extract partial word and previous context
        words = context_clean.split() if context_clean else []
        partial = ""
        prev_context = ""

        if not ends_with_space and words:
            # Mid-word - complete current word
            partial = words[-1]
            prev_context = " ".join(words[:-1]) if len(words) > 1 else ""
        else:
            # Start of new word (space at end) - predict next word
            prev_context = " ".join(words)

        # Check cache
        cache_key = f"{prev_context[-20:]}|{partial}"
        if cache_key in self._completion_cache:
            return self._completion_cache[cache_key][:n]

        # Get predictions
        predictions = self._get_predictions(prev_context, partial, n * 2)

        # Cache results
        if len(self._completion_cache) >= self._cache_max_size:
            # Clear oldest entries
            keys = list(self._completion_cache.keys())
            for key in keys[:len(keys) // 2]:
                del self._completion_cache[key]

        self._completion_cache[cache_key] = predictions

        return predictions[:n]

    def _get_predictions(self, context: str, partial: str, n: int) -> List[str]:
        """Get word predictions using PPM + dictionary."""
        predictions = []

        # 1. Dictionary words matching partial
        if partial:
            matches = [
                w for w in self.dictionary
                if w.startswith(partial) and len(w) > len(partial)
            ]

            # Score by PPM probability
            scored = []
            for word in matches[:50]:  # Limit for speed
                # Get PPM probability for this completion
                completion = word[len(partial):]
                ctx = context + " " + partial if context else partial

                prob = 1.0
                for char in completion:
                    char_probs = self.ppm.get_probabilities(ctx)
                    prob *= char_probs.get(char, 0.01)
                    ctx += char

                scored.append((word, prob))

            # Sort by probability
            scored.sort(key=lambda x: -x[1])
            predictions = [w for w, _ in scored]

        # 2. PPM beam search for novel completions
        if len(predictions) < n:
            ppm_preds = self.ppm.predict_word(context, partial, n - len(predictions))
            for word, _ in ppm_preds:
                if word not in predictions:
                    predictions.append(word)

        return predictions

    def learn(self, text: str) -> None:
        """Learn from text."""
        self.ppm.learn_text(text)

        # Add words to dictionary
        words = text.lower().split()
        for word in words:
            word = "".join(c for c in word if c.isalpha() or c == "'")
            if len(word) > 1:
                self.dictionary.add(word)

        # Clear cache
        self._completion_cache.clear()

    def save(self, model_path: Path, dict_path: Optional[Path] = None) -> None:
        """Save model and optionally dictionary."""
        self.ppm.save(model_path)

        if dict_path:
            with open(dict_path, "w") as f:
                for word in sorted(self.dictionary):
                    f.write(word + "\n")

    def get_stats(self) -> dict:
        """Get statistics."""
        stats = self.ppm.get_stats()
        stats["dictionary_size"] = len(self.dictionary)
        stats["cache_size"] = len(self._completion_cache)
        return stats
