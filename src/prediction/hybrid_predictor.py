"""
Hybrid word prediction engine.

Combines fast n-gram predictions with LLM re-ranking for
the best balance of speed and accuracy.

Architecture:
1. N-gram provides instant predictions (<10ms)
2. Transformer re-ranks in background (~100ms)
3. UI updates with refined predictions when ready
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from .autocorrect import CommonMisspellings
from .fuzzy_recognizer import FuzzyRecognizer
from .ngram_predictor import NgramPredictor
from .ppm_predictor import PPMPredictor, PPMWordPredictor
from .transformer_predictor import TransformerPredictor
from .vocabulary_pack import PackManager

_logger = logging.getLogger("HybridPredictor")


class HybridPredictor(QObject):
    """
    Hybrid prediction engine combining multiple approaches:

    1. N-gram: Instant word-level predictions (<10ms)
    2. PPM: Character-level context modeling (Dasher algorithm)
    3. Fuzzy: Spatial error correction for motor challenges
    4. Transformer: LLM re-ranking for accuracy (~100ms)

    Emits Qt signals for integration with QML UI.
    """

    # Signals for QML integration
    predictionsReady = Signal(list)      # Instant predictions
    predictionsRefined = Signal(list)    # LLM-refined predictions
    modelLoading = Signal(bool)          # True when LLM is loading
    llmAvailableChanged = Signal(bool)   # LLM availability changed
    autocorrectSuggested = Signal(str, str)  # (typed, correction)
    packsChanged = Signal()              # Vocabulary packs enabled/disabled

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        enable_llm: bool = True,
        parent: Optional[QObject] = None
    ):
        """
        Initialize the hybrid predictor.

        Args:
            model_dir: Directory for storing model files
            enable_llm: Whether to enable LLM re-ranking (can disable for speed)
            parent: Qt parent object
        """
        super().__init__(parent)

        # Set up model directory (cross-platform: AppData on Windows, .config on Linux)
        if model_dir is None:
            from ..platform import get_model_dir
            model_dir = get_model_dir()
        self._model_dir = model_dir
        self._model_dir.mkdir(parents=True, exist_ok=True)

        # Initialize n-gram predictor (always available)
        ngram_path = self._model_dir / "ngram_model.json"
        self._ngram = NgramPredictor(ngram_path if ngram_path.exists() else None)

        # Load base dictionary for better initial predictions
        self._ngram.load_base_dictionary()
        # Load common bigrams and trigrams for next-word prediction
        self._ngram.load_common_bigrams()
        self._ngram.load_common_trigrams()
        _logger.info("N-gram predictor initialized")

        # Initialize PPM predictor (character-level, Dasher algorithm)
        ppm_path = self._model_dir / "ppm_model.json"
        self._ppm = PPMPredictor(model_path=ppm_path if ppm_path.exists() else None)
        self._ppm_word = PPMWordPredictor(ppm=self._ppm)
        self._enable_ppm = True
        _logger.info("PPM predictor initialized")

        # Initialize fuzzy recognizer (spatial error correction)
        self._fuzzy = FuzzyRecognizer()
        data_dir = Path(__file__).parent.parent.parent / "data"
        self._fuzzy.load_dictionary(data_dir / "base_dictionary.txt")
        # Load common-misspellings fast-path table for autocorrect.
        self._misspellings = CommonMisspellings()
        self._misspellings.load(data_dir / "common_misspellings.txt")
        # Inject n-gram unigram frequencies so candidate ranking
        # prefers common words ("the") over rare ones ("tha").  Done
        # before the training corpus runs since we want this snapshot
        # at startup; learning during a session updates the n-gram
        # model directly and re-injects via _refresh_fuzzy_frequencies.
        self._fuzzy.set_frequencies(self._ngram.unigrams)
        _logger.info("Fuzzy recognizer initialized")

        # Load training corpus for better predictions
        self._load_training_corpus()
        # Re-inject after corpus training expanded the unigram counts.
        self._fuzzy.set_frequencies(self._ngram.unigrams)

        # Initialize vocabulary pack manager
        self._pack_manager = PackManager()
        _logger.info(
            "Pack manager initialized: %d packs available",
            len(self._pack_manager.get_available_packs()),
        )

        # Initialize transformer predictor (lazy loaded)
        self._enable_llm = enable_llm
        self._transformer: Optional[TransformerPredictor] = None
        self._llm_available = False

        if enable_llm:
            # Load LLM in background
            self._load_llm_async()

        # Current context for tracking
        self._current_context = ""
        self._pending_refinement = False

    def _load_llm_async(self) -> None:
        """Load the LLM in a background thread."""
        def loader():
            self.modelLoading.emit(True)
            try:
                self._transformer = TransformerPredictor(lazy_load=False)
                self._llm_available = self._transformer.is_available()
                if self._llm_available:
                    _logger.info("LLM predictor loaded and available")
                else:
                    _logger.warning("LLM predictor not available (missing dependencies?)")
            except Exception as e:
                _logger.error("Failed to load LLM: %s", e)
                self._llm_available = False
            finally:
                self.modelLoading.emit(False)
                # Notify QML of availability change
                self.llmAvailableChanged.emit(self._llm_available)

        thread = threading.Thread(target=loader, daemon=True)
        thread.start()

    def predict(self, context: str, n: int = 5) -> List[str]:
        """
        Get instant predictions combining n-gram, PPM, fuzzy, and vocabulary packs.

        Args:
            context: Text typed so far
            n: Number of predictions

        Returns:
            List of predicted words
        """
        self._current_context = context
        is_next_word = context.endswith(" ")

        # Get predictions from multiple sources
        ngram_preds = self._ngram.predict(context, n * 2)  # Get more for filtering
        _logger.debug("N-GRAM preds (next=%s): %s", is_next_word, ngram_preds[:5])

        # Add PPM predictions if enabled
        ppm_preds = []
        if self._enable_ppm:
            ppm_preds = self._ppm_word.predict(context, n * 2)
            _logger.debug("PPM preds: %s", ppm_preds[:5])

        # Add fuzzy candidates for current word
        fuzzy_preds = []
        fuzzy_candidates = self._fuzzy.get_fuzzy_predictions(context, n)
        fuzzy_preds = [word for word, _ in fuzzy_candidates]
        if fuzzy_preds:
            _logger.debug("FUZZY preds: %s", fuzzy_preds[:5])

        # Merge predictions with weighted scoring
        predictions = self._merge_predictions(
            ngram_preds, ppm_preds, fuzzy_preds, n
        )

        _logger.debug("MERGED result: %s", predictions)
        return predictions

    def _is_valid_word(self, word: str) -> bool:
        """Check if word is in our vocabulary and not blacklisted."""
        word_lower = word.lower()
        # Blacklisted words never appear
        if self._ngram.is_suppressed(word_lower):
            return False
        # Check n-gram vocabulary (includes Google 10K)
        if word_lower in self._ngram.unigrams:
            return True
        # Check if it's a common short word (pronouns, articles, etc.)
        if word_lower in {"i", "a", "an", "am", "as", "at", "be", "by", "do",
                          "go", "he", "if", "in", "is", "it", "me", "my", "no",
                          "of", "on", "or", "so", "to", "up", "us", "we"}:
            return True
        return False

    def _last_context_word(self) -> str:
        """Return the lowercase last word of the current context, or "".

        Used by the fuzzy-prediction bigram bonus.  Splits on
        whitespace and takes the trailing token; an empty context or a
        context that ends with whitespace and nothing else returns "".
        """
        parts = self._current_context.strip().split()
        return parts[-1].lower() if parts else ""

    def _merge_predictions(
        self,
        ngram: List[str],
        ppm: List[str],
        fuzzy: List[str],
        n: int
    ) -> List[str]:
        """
        Merge predictions from multiple sources with weighted scoring.

        For next-word prediction (context ends with space), heavily favor
        n-gram word predictions over PPM character fragments.
        Only include words that exist in our vocabulary.
        """
        scores: Dict[str, float] = {}
        sources: Dict[str, List[str]] = {}  # Track where each word came from

        # Check if we're predicting next word (context ends with space)
        is_next_word = self._current_context.endswith(" ")

        # N-gram predictions (weight: 3.0 for next-word, 1.0 for completion)
        ngram_weight = 3.0 if is_next_word else 1.0
        for i, word in enumerate(ngram):
            if is_next_word and len(word) <= 2 and word != "i":
                continue
            # Validate word exists in vocabulary
            if not self._is_valid_word(word):
                continue
            score = ngram_weight / (i + 1)
            scores[word] = scores.get(word, 0) + score
            sources.setdefault(word, []).append("ng")

        # PPM predictions (weight: 0.3 for next-word, 0.8 for completion)
        ppm_weight = 0.3 if is_next_word else 0.8
        for i, word in enumerate(ppm):
            if is_next_word and len(word) <= 2 and word != "i":
                continue
            # Validate word exists in vocabulary
            if not self._is_valid_word(word):
                continue
            score = ppm_weight / (i + 1)
            scores[word] = scores.get(word, 0) + score
            sources.setdefault(word, []).append("ppm")

        # Fuzzy predictions weight (from FuzzyRecognizer constants).
        # Re-rank with a bigram bonus from the n-gram model — fuzzy
        # candidates are otherwise context-blind, so "the" after "of "
        # would tie with "thy" and "tha" purely on spatial scores.  The
        # bonus is capped (log1p(count)/5) so it nudges ranking without
        # letting a single noisy bigram dominate.
        fuzzy_weight = self._fuzzy.prediction_weight
        prev_word = self._last_context_word()
        bigram_table = self._ngram.bigrams.get(prev_word, {}) if prev_word else {}
        for i, word in enumerate(fuzzy):
            if not self._is_valid_word(word):
                continue
            bigram_bonus = 1.0
            if bigram_table:
                bg_count = bigram_table.get(word, 0)
                if bg_count > 0:
                    # /2 (not /5) so a confident bigram can override
                    # positional ranking — fuzzy candidates are
                    # context-blind by default, the bigram is the
                    # primary context signal we have for them.
                    bigram_bonus = 1.0 + math.log1p(bg_count) / 2.0
            score = (fuzzy_weight / (i + 1)) * bigram_bonus
            scores[word] = scores.get(word, 0) + score
            sources.setdefault(word, []).append("fz")

        # Apply dispreference penalties before sorting
        for word in list(scores):
            dp = self._ngram.get_dispreference(word)
            if dp > 0:
                scores[word] /= (1 + dp * 0.5)

        # Sort by combined score
        sorted_words = sorted(scores.items(), key=lambda x: -x[1])

        # Determine if we're at sentence start (for capitalization)
        ctx = self._current_context.rstrip()
        sentence_start = (
            not ctx
            or ctx[-1] in ".!?"
            or ctx.endswith("\n")
        )

        # Return top n valid words, applying context-aware capitalization
        results = []
        for word, score in sorted_words:
            # Allow "i" through (always-capitalize handles it), but skip
            # other short words for next-word predictions
            if is_next_word and len(word) <= 2 and word != "i":
                continue
            # Apply context-aware capitalization
            capped = self._ngram.get_capitalized(word, sentence_start)
            results.append(capped)
            # Log source for debugging
            src = "+".join(sources.get(word, ["?"]))
            _logger.debug("  %s (%.2f) [%s]", capped, score, src)
            if len(results) >= n:
                break

        return results

    def predict_with_refinement(self, context: str, n: int = 5) -> List[str]:
        """
        Get instant predictions and trigger async LLM refinement.

        Emits predictionsReady immediately, then predictionsRefined
        when LLM finishes.

        Args:
            context: Text typed so far
            n: Number of predictions

        Returns:
            Instant hybrid predictions (n-gram + PPM + fuzzy)
        """
        self._current_context = context

        # Get instant hybrid predictions (n-gram + PPM + fuzzy)
        predictions = self.predict(context, n)
        self.predictionsReady.emit(predictions)

        # Trigger async LLM refinement if available
        if self._llm_available and self._transformer and len(context) > 3:
            self._refine_async(context, predictions, n)

        return predictions

    def _refine_async(self, context: str, candidates: List[str], n: int) -> None:
        """Trigger async LLM re-ranking."""
        if self._pending_refinement:
            return  # Don't queue multiple refinements

        self._pending_refinement = True

        def on_refined(refined: List[str]):
            self._pending_refinement = False
            # Only emit if context hasn't changed
            if context == self._current_context and refined:
                self.predictionsRefined.emit(refined)

        # Get more candidates for re-ranking
        extended_candidates = self._ngram.predict(context, n * 3)
        assert self._transformer is not None  # Guarded by caller
        self._transformer.rerank_async(context, extended_candidates, on_refined, n)

    def learn(self, text: str) -> List[str]:
        """
        Learn from user's text to improve predictions.

        Args:
            text: Text to learn from

        Returns:
            List of words that were new to user vocabulary.
        """
        new_words = self._ngram.learn(text)

        # Also train PPM model
        if self._enable_ppm:
            self._ppm.learn_text(text)
            self._ppm_word.learn(text)

        return new_words

    def learn_word(self, word: str) -> None:
        """Learn a single word (e.g., when user types it)."""
        self._ngram.learn_word(word)

    def learn_from_selection(self, context: str, selected_word: str) -> None:
        """
        Learn when user selects a prediction.

        This helps the model understand which predictions are useful.

        Args:
            context: The context when prediction was made
            selected_word: The word the user selected
        """
        # Boost the selected word
        self._ngram.learn_word(selected_word)

        # Learn the context -> word association
        full_text = f"{context} {selected_word}"
        self._ngram.learn(full_text)

    def save(self) -> None:
        """Save all models to disk."""
        ngram_path = self._model_dir / "ngram_model.json"
        self._ngram.save(ngram_path)

        # Save PPM model
        if self._enable_ppm:
            ppm_path = self._model_dir / "ppm_model.json"
            self._ppm.save(ppm_path)

        _logger.info("Models saved")

    def _load_training_corpus(self) -> None:
        """Load default training corpus for better predictions."""
        corpus_path = Path(__file__).parent.parent.parent / "data" / "training_corpus.txt"

        if not corpus_path.exists():
            _logger.info("No training corpus found at %s", corpus_path)
            return

        try:
            text = corpus_path.read_text(encoding="utf-8", errors="ignore")
            # Filter out comments
            lines = [line for line in text.split('\n')
                     if line.strip() and not line.startswith('#')]
            clean_text = '\n'.join(lines)

            # Train both n-gram and PPM
            self._ngram.load_corpus(clean_text)
            if self._enable_ppm:
                self._ppm.train(clean_text)

            _logger.info("Training corpus loaded: %d characters", len(clean_text))
        except Exception as e:
            _logger.error("Failed to load training corpus: %s", e)

    def load_corpus(self, corpus_path: Path) -> None:
        """
        Load a text corpus for initial training.

        Args:
            corpus_path: Path to text file
        """
        if not corpus_path.exists():
            _logger.warning("Corpus not found: %s", corpus_path)
            return

        text = corpus_path.read_text(encoding="utf-8", errors="ignore")
        self._ngram.load_corpus(text)

        # Also train PPM
        if self._enable_ppm:
            self._ppm.train(text)

        self.save()

    @property
    def llm_available(self) -> bool:
        """Check if LLM is available for refinement."""
        return self._llm_available

    @property
    def enable_llm(self) -> bool:
        """Check if LLM is enabled."""
        return self._enable_llm

    @enable_llm.setter
    def enable_llm(self, value: bool) -> None:
        """Enable/disable LLM refinement."""
        self._enable_llm = value
        if value and self._transformer is None:
            self._load_llm_async()

    def get_stats(self) -> dict:
        """Get prediction engine statistics."""
        stats = self._ngram.get_stats()
        stats["llm_enabled"] = self._enable_llm
        stats["llm_available"] = self._llm_available
        stats["ppm_enabled"] = self._enable_ppm
        stats["ppm"] = self._ppm.get_stats()
        stats["fuzzy"] = self._fuzzy.get_stats()
        return stats

    # --- Public API for callers that previously reached through to _ngram ---

    def get_unigram_freqs(self) -> Dict[str, int]:
        """Merged unigram counts (base + user).

        Public forwarder so callers (e.g. the swipe recogniser) don't
        reach through the private ``_ngram`` attribute.
        """
        return self._ngram.unigrams

    def get_capitalized(self, word: str, sentence_start: bool = False) -> str:
        """Return the preferred capitalisation for ``word``.

        See :meth:`NgramPredictor.get_capitalized` for the three-tier
        model.  Exposed publicly so external callers don't have to
        reach into ``_ngram``.
        """
        return self._ngram.get_capitalized(word, sentence_start)

    # --- PPM Control ---

    @property
    def enable_ppm(self) -> bool:
        """Check if PPM is enabled."""
        return self._enable_ppm

    @enable_ppm.setter
    def enable_ppm(self, value: bool) -> None:
        """Enable/disable PPM predictions."""
        self._enable_ppm = value

    def load_ppm_training_text(self, path: Path) -> bool:
        """
        Load training text for PPM model.

        Args:
            path: Path to text file

        Returns:
            True if loaded successfully
        """
        return self._ppm.load_training_text(path)

    # --- Fuzzy Recognition ---

    def check_autocorrect(self, typed_word: str, context: str = "") -> Optional[str]:
        """Return the corrected spelling for ``typed_word`` or None.

        Two-tier:
        1. **Fast path** — common-misspellings table (``data/common_misspellings.txt``).
           Direct lookup for well-known mistakes that the fuzzy machinery
           would either miss ("definately") or only weakly correct.
        2. **Slow path** — fuzzy ``should_autocorrect``, which uses spatial
           probability + frequency + edit distance.  Only fires if the
           typed word isn't already in the fuzzy dictionary, so we never
           "correct" something the user actually meant.
        """
        if not typed_word:
            return None

        # Fast path: direct table lookup.
        misspell = self._misspellings.lookup(typed_word)
        if misspell and misspell != typed_word.lower():
            self.autocorrectSuggested.emit(typed_word, misspell)
            return misspell

        # Slow path: spatial / edit-distance fuzzy correction.
        correction = self._fuzzy.should_autocorrect(typed_word, context)
        if correction:
            self.autocorrectSuggested.emit(typed_word, correction)
        return correction

    def get_key_alternatives(self, key: str) -> Dict[str, float]:
        """
        Get probability distribution over intended keys.

        Useful for debugging or advanced UI feedback.

        Args:
            key: Pressed key

        Returns:
            Dict mapping key -> probability
        """
        return self._fuzzy.get_key_alternatives(key)

    def clear_user_data(self) -> None:
        """Clear all user-learned data and rebuild base dictionaries."""
        self._ngram.clear_user_data()
        # Reload base dictionary and common n-grams
        self._ngram.load_base_dictionary()
        self._ngram.load_common_bigrams()
        self._ngram.load_common_trigrams()
        # Reset PPM models — reinitialise from scratch
        if self._enable_ppm:
            self._ppm = PPMPredictor(max_order=self._ppm.max_order)
            self._ppm_word = PPMWordPredictor(ppm=self._ppm)
            self._load_training_corpus()

    def reload_dictionary(self) -> bool:
        """Reload the base dictionary."""
        return self._ngram.load_base_dictionary()

    # --- Vocabulary Packs ---

    def get_available_packs(self) -> List[dict]:
        """Get metadata for all available vocabulary packs."""
        return self._pack_manager.get_all_pack_info()

    def get_enabled_packs(self) -> List[str]:
        """Get list of enabled pack IDs."""
        return self._pack_manager.get_enabled_packs()

    def enable_vocabulary_pack(self, pack_id: str) -> bool:
        """
        Enable a vocabulary pack and inject its vocabulary.

        Args:
            pack_id: Pack directory name (e.g., "medical", "programming")

        Returns:
            True if pack was found and enabled
        """
        if self._pack_manager.enable_pack(pack_id):
            self._pack_manager.apply_to_predictor(self._ngram)
            self.packsChanged.emit()
            _logger.info("Vocabulary pack enabled: %s", pack_id)
            return True
        return False

    def disable_vocabulary_pack(self, pack_id: str) -> bool:
        """
        Disable a vocabulary pack.

        Note: Vocabulary already injected remains in the n-gram model
        for this session. It will not be re-injected on next predict.

        Args:
            pack_id: Pack directory name

        Returns:
            True if pack was found and disabled
        """
        if self._pack_manager.disable_pack(pack_id):
            self.packsChanged.emit()
            _logger.info("Vocabulary pack disabled: %s", pack_id)
            return True
        return False

    def import_vocabulary_pack(self, source_dir: str) -> str:
        """
        Import a custom vocabulary pack from a folder.

        Args:
            source_dir: Path to the pack folder (must contain dictionary.txt)

        Returns:
            Pack ID on success, empty string on failure
        """
        from pathlib import Path
        pack_id = self._pack_manager.import_pack(Path(source_dir))
        if pack_id:
            self.packsChanged.emit()
            return pack_id
        return ""

    def get_user_packs_dir(self) -> str:
        """Return the user packs directory path as a string."""
        return str(self._pack_manager.get_user_packs_dir())

    # --- Word Suppression ---

    def blacklist_word(self, word: str) -> None:
        """Remove a word from all future predictions."""
        self._ngram.blacklist_word(word)

    def unblacklist_word(self, word: str) -> None:
        """Restore a previously blacklisted word."""
        self._ngram.unblacklist_word(word)

    def mark_bad_suggestion(self, word: str) -> None:
        """Downweight a word in future predictions."""
        self._ngram.mark_bad(word)

    def remove_dispreference(self, word: str) -> None:
        """Remove dispreference penalty from a word."""
        self._ngram.remove_dispreference(word)

    def record_typed_word(self, word: str) -> Optional[str]:
        """Track typed word for auto-rehabilitation of blacklisted words."""
        return self._ngram.record_typed_word(word)

    def learn_capitalization(self, word: str) -> bool:
        """Learn preferred capitalization from user typing."""
        return self._ngram.learn_capitalization(word)

    def set_capitalization(self, word: str, preferred: str) -> None:
        """Explicitly set preferred capitalization (from user edit)."""
        self._ngram.capitalization[preferred.lower()] = preferred
