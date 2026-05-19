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
from typing import Dict, List, Optional, Tuple

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

        # Active merge strategy.  Default "rank" preserves byte-identical
        # behaviour with prior versions; "rrf", "linear", "loglinear" are
        # opt-in alternatives surfaced via Settings → Smart Typing → Suggestion Engine.
        # See ``docs/HYBRID_MERGING.md`` for the trade-offs.
        self._merge_strategy: str = "rank"

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

        # Get predictions from multiple sources, with their per-source
        # raw scores.  Each source's scores live in a different scale
        # (n-gram = interpolated probability, PPM = chained char prob,
        # fuzzy = log-spatial); the merge strategies normalise per
        # source before combining, so we propagate raw scores here.
        ngram_preds = self._ngram.predict_with_scores(context, n * 2)
        _logger.debug(
            "N-GRAM preds (next=%s): %s",
            is_next_word,
            [w for w, _ in ngram_preds[:5]],
        )

        # Add PPM predictions if enabled
        ppm_preds: List[Tuple[str, float]] = []
        if self._enable_ppm:
            ppm_preds = self._ppm_word.predict_with_scores(context, n * 2)
            _logger.debug("PPM preds: %s", [w for w, _ in ppm_preds[:5]])

        # Add fuzzy candidates for current word
        fuzzy_preds = self._fuzzy.get_fuzzy_predictions(context, n)
        if fuzzy_preds:
            _logger.debug("FUZZY preds: %s", [w for w, _ in fuzzy_preds[:5]])

        # Merge predictions with the active strategy
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
        ngram: List[Tuple[str, float]],
        ppm: List[Tuple[str, float]],
        fuzzy: List[Tuple[str, float]],
        n: int
    ) -> List[str]:
        """Merge candidate lists from each predictor into a final ranking.

        Dispatches to the active strategy (``_merge_strategy``) — see
        ``docs/HYBRID_MERGING.md`` for the menu.  Each strategy returns
        a ``Dict[str, float]`` of combined scores (and populates
        ``sources`` for debug logging); the shared ``_finalize_scores``
        applies dispreference penalty, short-word filter, sentence-start
        capitalisation, and caps to ``n`` results.

        For next-word prediction (context ends with space), every
        strategy weights n-gram heavily over PPM/fuzzy — that ratio is
        the dominant behaviour and shouldn't depend on the merge
        formula.
        """
        is_next_word = self._current_context.endswith(" ")
        sources: Dict[str, List[str]] = {}

        strategy = self._merge_strategy
        if strategy == "rrf":
            scores = self._score_rrf(ngram, ppm, fuzzy, is_next_word, sources)
        elif strategy == "linear":
            scores = self._score_linear(ngram, ppm, fuzzy, is_next_word, sources)
        elif strategy == "loglinear":
            scores = self._score_loglinear(
                ngram, ppm, fuzzy, is_next_word, sources
            )
        else:  # "rank" — default, byte-identical to pre-strategy behaviour
            scores = self._score_rank(ngram, ppm, fuzzy, is_next_word, sources)

        return self._finalize_scores(scores, sources, n, is_next_word)

    # --- Per-strategy scorers --------------------------------------------

    def _score_rank(
        self,
        ngram: List[Tuple[str, float]],
        ppm: List[Tuple[str, float]],
        fuzzy: List[Tuple[str, float]],
        is_next_word: bool,
        sources: Dict[str, List[str]],
    ) -> Dict[str, float]:
        """Original rank-based fusion: ``score = weight / (rank + 1)``.

        Default strategy.  Discards each predictor's confidence and
        ranks purely by positional contribution, weighted per source.
        """
        scores: Dict[str, float] = {}
        ngram_weight, ppm_weight, fuzzy_weight = self._source_weights(
            is_next_word
        )

        for i, (word, _) in enumerate(ngram):
            if not self._candidate_passes(word, is_next_word):
                continue
            scores[word] = scores.get(word, 0.0) + ngram_weight / (i + 1)
            sources.setdefault(word, []).append("ng")

        for i, (word, _) in enumerate(ppm):
            if not self._candidate_passes(word, is_next_word):
                continue
            scores[word] = scores.get(word, 0.0) + ppm_weight / (i + 1)
            sources.setdefault(word, []).append("ppm")

        bigram_table = self._fuzzy_bigram_table()
        for i, (word, _) in enumerate(fuzzy):
            if not self._is_valid_word(word):
                continue
            bonus = self._bigram_bonus(word, bigram_table)
            scores[word] = (
                scores.get(word, 0.0) + (fuzzy_weight / (i + 1)) * bonus
            )
            sources.setdefault(word, []).append("fz")

        return scores

    def _score_rrf(
        self,
        ngram: List[Tuple[str, float]],
        ppm: List[Tuple[str, float]],
        fuzzy: List[Tuple[str, float]],
        is_next_word: bool,
        sources: Dict[str, List[str]],
        *,
        k: int = 60,
    ) -> Dict[str, float]:
        """Reciprocal Rank Fusion (Cormack et al. 2009).

        ``score = weight / (k + rank + 1)`` with ``k = 60`` — the
        IR-standard smoothing constant that ships in elasticsearch,
        Vespa, and Azure AI Search.  Same shape as the rank strategy
        but the rank-1 vs rank-2 ratio is ~1.02 instead of 2× — so
        consensus across sources matters far more than positional
        dominance within any one source.
        """
        scores: Dict[str, float] = {}
        ngram_weight, ppm_weight, fuzzy_weight = self._source_weights(
            is_next_word
        )

        for i, (word, _) in enumerate(ngram):
            if not self._candidate_passes(word, is_next_word):
                continue
            scores[word] = (
                scores.get(word, 0.0) + ngram_weight / (k + i + 1)
            )
            sources.setdefault(word, []).append("ng")

        for i, (word, _) in enumerate(ppm):
            if not self._candidate_passes(word, is_next_word):
                continue
            scores[word] = (
                scores.get(word, 0.0) + ppm_weight / (k + i + 1)
            )
            sources.setdefault(word, []).append("ppm")

        bigram_table = self._fuzzy_bigram_table()
        for i, (word, _) in enumerate(fuzzy):
            if not self._is_valid_word(word):
                continue
            bonus = self._bigram_bonus(word, bigram_table)
            scores[word] = (
                scores.get(word, 0.0)
                + (fuzzy_weight / (k + i + 1)) * bonus
            )
            sources.setdefault(word, []).append("fz")

        return scores

    def _score_linear(
        self,
        ngram: List[Tuple[str, float]],
        ppm: List[Tuple[str, float]],
        fuzzy: List[Tuple[str, float]],
        is_next_word: bool,
        sources: Dict[str, List[str]],
    ) -> Dict[str, float]:
        """Probability-space linear interpolation (Presage-style).

        Each source's raw scores are normalised into a per-source
        sum-to-1 distribution; the merged score is
        ``Σ w_i · P_i(w)``.  Words missing from a source contribute
        zero from that source (additive, not multiplicative).  This
        is what Presage's ``MeritocracyCombiner`` ships, with full
        awareness of the calibration risk between predictors that
        produce probabilities at different scales.
        """
        ngram_weight, ppm_weight, fuzzy_weight = self._source_weights(
            is_next_word
        )
        bigram_table = self._fuzzy_bigram_table()

        p_ngram = self._normalise_source(ngram, is_next_word)
        p_ppm = self._normalise_source(ppm, is_next_word)
        p_fuzzy = self._normalise_source(
            fuzzy, is_next_word, fuzzy_bigram_table=bigram_table
        )

        scores: Dict[str, float] = {}
        for word, p in p_ngram.items():
            scores[word] = scores.get(word, 0.0) + ngram_weight * p
            sources.setdefault(word, []).append("ng")
        for word, p in p_ppm.items():
            scores[word] = scores.get(word, 0.0) + ppm_weight * p
            sources.setdefault(word, []).append("ppm")
        for word, p in p_fuzzy.items():
            scores[word] = scores.get(word, 0.0) + fuzzy_weight * p
            sources.setdefault(word, []).append("fz")

        return scores

    def _score_loglinear(
        self,
        ngram: List[Tuple[str, float]],
        ppm: List[Tuple[str, float]],
        fuzzy: List[Tuple[str, float]],
        is_next_word: bool,
        sources: Dict[str, List[str]],
        *,
        floor: float = 1e-6,
    ) -> Dict[str, float]:
        """Log-linear / multiplicative mixture (Google patent style).

        Equivalent to ``Π P_i(w)^w_i`` — the per-source weights become
        exponents on the probabilities.  Klakow (1998) showed log-linear
        outperforms linear interpolation by ~20% relative perplexity on
        n-gram smoothing; the failure mode is "single zero kills the
        candidate," mitigated here by floor-smoothing missing words at
        ``1e-6`` (every shipping log-linear system has the same fix).

        Output is converted back to linear space (with a max-shift to
        avoid underflow) so dispreference and sort behave identically
        to the other strategies.
        """
        ngram_weight, ppm_weight, fuzzy_weight = self._source_weights(
            is_next_word
        )
        bigram_table = self._fuzzy_bigram_table()

        p_ngram = self._normalise_source(ngram, is_next_word)
        p_ppm = self._normalise_source(ppm, is_next_word)
        p_fuzzy = self._normalise_source(
            fuzzy, is_next_word, fuzzy_bigram_table=bigram_table
        )

        candidates = set(p_ngram) | set(p_ppm) | set(p_fuzzy)
        if not candidates:
            return {}

        log_floor = math.log(floor)
        log_scores: Dict[str, float] = {}
        for word in candidates:
            log_score = 0.0
            if word in p_ngram:
                log_score += ngram_weight * math.log(p_ngram[word])
                sources.setdefault(word, []).append("ng")
            else:
                log_score += ngram_weight * log_floor
            if word in p_ppm:
                log_score += ppm_weight * math.log(p_ppm[word])
                sources.setdefault(word, []).append("ppm")
            else:
                log_score += ppm_weight * log_floor
            if word in p_fuzzy:
                log_score += fuzzy_weight * math.log(p_fuzzy[word])
                sources.setdefault(word, []).append("fz")
            else:
                log_score += fuzzy_weight * log_floor
            log_scores[word] = log_score

        # Convert log → linear space so dispreference (multiplicative)
        # and sort behave the same as other strategies.  Subtract max
        # before exp to keep the largest score at 1.0 and avoid
        # underflowing very-negative scores to 0 unnecessarily.
        max_log = max(log_scores.values())
        return {w: math.exp(s - max_log) for w, s in log_scores.items()}

    # --- Shared helpers --------------------------------------------------

    def _normalise_source(
        self,
        items: List[Tuple[str, float]],
        is_next_word: bool,
        *,
        fuzzy_bigram_table: Optional[Dict[str, int]] = None,
    ) -> Dict[str, float]:
        """Per-source filter + sum-to-1 normalisation.

        Used by the linear and log-linear strategies to bring each
        predictor's raw scores onto a comparable probability scale.
        Filters words first (short-word + vocabulary gates for
        ngram/ppm; vocabulary-only for fuzzy), then divides by the
        sum so the result is a probability distribution over the
        surviving candidates.

        For fuzzy specifically, applies the bigram bonus *before*
        normalisation so the context signal flows through the
        resulting distribution rather than being applied
        post-mixture.  Pass ``fuzzy_bigram_table`` to opt into this
        path; ``None`` means "this is ngram or ppm — short-word
        filter, no bigram bonus."

        Returns an empty dict when no candidates survive or all
        scores are zero.
        """
        is_fuzzy = fuzzy_bigram_table is not None
        filtered: List[Tuple[str, float]] = []
        for word, raw_score in items:
            if is_fuzzy:
                if not self._is_valid_word(word):
                    continue
                bonus = self._bigram_bonus(word, fuzzy_bigram_table)
                filtered.append((word, raw_score * bonus))
            else:
                if not self._candidate_passes(word, is_next_word):
                    continue
                filtered.append((word, raw_score))

        total = sum(score for _, score in filtered)
        if total <= 0:
            return {}
        return {word: score / total for word, score in filtered}

    def _source_weights(self, is_next_word: bool) -> Tuple[float, float, float]:
        """Per-source merge weights, dependent on next-word vs completion.

        These weights are shared across every strategy — the formula
        differs, but the relative trust between predictors does not.
        """
        ngram_weight = 3.0 if is_next_word else 1.0
        ppm_weight = 0.3 if is_next_word else 0.8
        fuzzy_weight = self._fuzzy.prediction_weight
        return ngram_weight, ppm_weight, fuzzy_weight

    def _candidate_passes(self, word: str, is_next_word: bool) -> bool:
        """Combined short-word filter + vocabulary validation gate.

        Mirrors the inline checks the rank strategy applied before
        adding a word to ``scores``.  Used by every strategy that
        iterates per-source predictions before merging.
        """
        if is_next_word and len(word) <= 2 and word != "i":
            return False
        return self._is_valid_word(word)

    def _fuzzy_bigram_table(self) -> Dict[str, int]:
        """Bigram-count table for the previous context word.

        Returned dict is empty when there's no preceding word.  Strategy
        scorers consult it to compute the fuzzy bigram bonus that lets
        confident context override positional ranking — fuzzy candidates
        are otherwise context-blind.
        """
        prev_word = self._last_context_word()
        if not prev_word:
            return {}
        return self._ngram.bigrams.get(prev_word, {})

    @staticmethod
    def _bigram_bonus(
        word: str, bigram_table: Optional[Dict[str, int]]
    ) -> float:
        """Multiplicative bonus for fuzzy candidates with bigram support.

        ``1 + log1p(count) / 2`` when the previous word frequently
        precedes ``word``; ``1.0`` (no bonus) otherwise.  The /2 slope
        is generous on purpose — fuzzy has no other context signal, so
        a confident bigram should be able to outrank a positional
        leader.  Accepts ``None`` so the caller can pass through the
        optional bigram-table parameter without a wrapping guard.
        """
        if not bigram_table:
            return 1.0
        count = bigram_table.get(word, 0)
        if count <= 0:
            return 1.0
        return 1.0 + math.log1p(count) / 2.0

    def _finalize_scores(
        self,
        scores: Dict[str, float],
        sources: Dict[str, List[str]],
        n: int,
        is_next_word: bool,
    ) -> List[str]:
        """Apply dispreference penalty, sort, capitalise, return top n.

        Shared post-processing for every strategy.  Sentence-start
        detection only fires on actual punctuation (`.!?`); empty
        context is **not** a sentence start — see the n-gram path's
        comment for why (terminal/REPL backspace, app switch).
        """
        for word in list(scores):
            dp = self._ngram.get_dispreference(word)
            if dp > 0:
                scores[word] /= 1 + dp * 0.5

        sorted_words = sorted(scores.items(), key=lambda x: -x[1])

        ctx = self._current_context.rstrip()
        sentence_start = bool(ctx) and ctx[-1] in ".!?"

        results: List[str] = []
        for word, score in sorted_words:
            # Final short-word guard — catches any short word that
            # slipped past the per-source filter (e.g. fuzzy, which
            # the rank strategy historically didn't filter).
            if is_next_word and len(word) <= 2 and word != "i":
                continue
            capped = self._ngram.get_capitalized(word, sentence_start)
            results.append(capped)
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

    def unlearn_word(self, word: str) -> bool:
        """Reverse one sighting of a word — see ``NgramPredictor.unlearn_word``."""
        return self._ngram.unlearn_word(word)

    def learn_from_selection(self, context: str, selected_word: str) -> None:
        """
        Learn when user selects a prediction.

        Routes the selected word through
        :meth:`NgramPredictor.learn_from_pill_click` — known words get
        the immediate +5 boost, unknown words pass through the
        3-sighting candidate gate. Without the gate, a single click on
        a fuzzy/PPM-generated pill for a never-typed word would inject
        it permanently into ``user_vocab``.

        Trailing bigram / trigram edges are still reinforced
        immediately via :meth:`NgramPredictor.reinforce_context`. The
        context edge was validated by *this* click; deferring it would
        delay legitimate context learning. A bigram pointing into a
        not-yet-promoted unigram surfaces the word only when that same
        context recurs, which is exactly the loop we want for "click
        more times to promote."

        Args:
            context: The context when prediction was made
            selected_word: The word the user selected
        """
        self._ngram.learn_from_pill_click(selected_word)
        self._ngram.reinforce_context(context, selected_word)

    def save(self) -> None:
        """Save all models to disk."""
        ngram_path = self._model_dir / "ngram_model.json"
        self._ngram.save(ngram_path)

        # Save PPM model
        if self._enable_ppm:
            ppm_path = self._model_dir / "ppm_model.json"
            self._ppm.save(ppm_path)

        _logger.info("Models saved")

    def reload_from_disk(self) -> None:
        """Re-read ngram + ppm models from disk and re-discover packs.

        Used after :func:`src.data_export.import_user_data` has
        replaced the on-disk model files and the packs directory in
        place. ``NgramPredictor.load`` already replaces (not merges)
        the in-memory tables, so this is a straight swap of the
        snapshot. The pack manager is reset and re-scans the now-
        replaced packs directory so the Settings panel reflects the
        imported state.

        Enabled-pack state is reset — packs come back disabled and
        the user re-enables what they want. Re-enabling persists
        application-side via the Qt settings layer.
        """
        ngram_path = self._model_dir / "ngram_model.json"
        if ngram_path.exists():
            self._ngram.load(ngram_path)
        ppm_path = self._model_dir / "ppm_model.json"
        if self._enable_ppm and ppm_path.exists():
            self._ppm.load(ppm_path)
        self._pack_manager._packs.clear()
        self._pack_manager._discover_packs()
        self.packsChanged.emit()
        _logger.info("Predictor reloaded from disk")

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
        stats["merge_strategy"] = self._merge_strategy
        stats["ppm"] = self._ppm.get_stats()
        stats["fuzzy"] = self._fuzzy.get_stats()
        return stats

    # --- Merge strategy selection ---

    _VALID_MERGE_STRATEGIES = ("rank", "rrf", "linear", "loglinear")

    @property
    def merge_strategy(self) -> str:
        """The active merge strategy name.

        One of ``"rank"`` (default), ``"rrf"``, ``"linear"``,
        ``"loglinear"``.  See ``docs/HYBRID_MERGING.md`` for the
        trade-offs between them.
        """
        return self._merge_strategy

    def set_merge_strategy(self, strategy: str) -> bool:
        """Switch the active merge strategy.

        Returns True if the strategy was recognised and applied,
        False otherwise (the previous strategy is preserved).  Unknown
        strategies are logged but never raise — keeps the QML/bridge
        slot defensive against typos.
        """
        if strategy not in self._VALID_MERGE_STRATEGIES:
            _logger.warning(
                "Unknown merge strategy %r; keeping %r",
                strategy, self._merge_strategy,
            )
            return False
        self._merge_strategy = strategy
        _logger.info("Merge strategy set to %r", strategy)
        return True

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
            pack_id: Pack directory name (the folder the user imported)

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

    def mark_good_suggestion(self, word: str) -> None:
        """Boost a word and record the boost for later undo."""
        self._ngram.remove_dispreference(word)
        self._ngram.mark_good(word)

    def remove_dispreference(self, word: str) -> None:
        """Remove dispreference penalty from a word."""
        self._ngram.remove_dispreference(word)

    def unprefer(self, word: str) -> None:
        """Roll back an explicit user boost."""
        self._ngram.unprefer(word)

    def record_typed_word(self, word: str) -> Optional[str]:
        """Track typed word for auto-rehabilitation of blacklisted words."""
        return self._ngram.record_typed_word(word)

    def learn_capitalization(self, word: str, *, allow_uppercase: bool = False) -> bool:
        """Learn preferred capitalization from user typing."""
        return self._ngram.learn_capitalization(word, allow_uppercase=allow_uppercase)

    def set_capitalization(self, word: str, preferred: str) -> None:
        """Explicitly set preferred capitalization (from user edit)."""
        self._ngram.capitalization[preferred.lower()] = preferred
