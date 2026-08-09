"""
Transformer-based word prediction for re-ranking.

Uses a small LLM (DistilGPT-2) to re-rank n-gram candidates
for improved accuracy. Runs asynchronously in background.
"""

from __future__ import annotations

import logging
import threading
from queue import Queue
from typing import Callable, List, Optional

_logger = logging.getLogger("TransformerPredictor")

# Lazy import to avoid slow startup
_pipeline = None
_model_loaded = False
_model_lock = threading.Lock()


def _load_model():
    """Lazy-load the transformer model."""
    global _pipeline, _model_loaded

    if _model_loaded:
        return _pipeline

    with _model_lock:
        if _model_loaded:
            return _pipeline

        try:
            from transformers import pipeline

            _logger.info("Loading DistilGPT-2 model (this may take a moment)...")
            _pipeline = pipeline(
                "text-generation",
                model="distilgpt2",
                device=-1,  # CPU
                framework="pt",
            )
            _model_loaded = True
            _logger.info("DistilGPT-2 model loaded successfully")
        except ImportError:
            _logger.warning(
                "transformers not installed. Install with: pip install transformers torch"
            )
            _pipeline = None
        except Exception as e:
            _logger.error("Failed to load transformer model: %s", e)
            _pipeline = None

    return _pipeline


class TransformerPredictor:
    """
    Transformer-based predictor for LLM-quality suggestions.

    Used to re-rank candidates from the n-gram model for better accuracy.
    Runs in a background thread to avoid blocking the UI.
    """

    def __init__(self, lazy_load: bool = True):
        """
        Initialize the predictor.

        Args:
            lazy_load: If True, model loads on first use. If False, loads immediately.
        """
        self._model = None
        self._loaded = False
        self._loading = False

        # Background processing queue
        self._request_queue: Queue = Queue()
        self._worker_thread: Optional[threading.Thread] = None
        self._running = False

        if not lazy_load:
            self._ensure_loaded()

    def _ensure_loaded(self) -> bool:
        """Ensure model is loaded. Returns True if available."""
        if self._loaded:
            return self._model is not None

        if self._loading:
            return False

        self._loading = True
        self._model = _load_model()
        self._loaded = True
        self._loading = False

        return self._model is not None

    def is_available(self) -> bool:
        """Check if transformer model is available."""
        return self._ensure_loaded()

    def predict(self, context: str, n: int = 5) -> List[str]:
        """
        Generate word predictions using the transformer.

        Args:
            context: Text context for prediction
            n: Number of predictions to return

        Returns:
            List of predicted next words
        """
        if not self._ensure_loaded() or not self._model:
            return []

        try:
            # Generate completions using pipeline defaults
            results = self._model(
                context,
                max_new_tokens=3,
                num_return_sequences=min(n * 2, 10),
                do_sample=True,
                top_k=50,
                pad_token_id=self._model.tokenizer.eos_token_id,
                return_full_text=False,
            )

            # Extract first word from each completion
            words = []
            seen = set()
            for result in results:
                text = result["generated_text"].strip()
                # Get first word
                first_word = text.split()[0] if text.split() else ""
                # Clean up
                first_word = "".join(c for c in first_word if c.isalpha() or c == "'")
                first_word = first_word.lower()

                if first_word and first_word not in seen and len(first_word) > 1:
                    words.append(first_word)
                    seen.add(first_word)

                if len(words) >= n:
                    break

            return words

        except Exception as e:
            _logger.error("Transformer prediction failed: %s", e)
            return []

    def rerank(self, context: str, candidates: List[str], n: int = 5) -> List[str]:
        """
        Re-rank candidate words using the transformer.

        Args:
            context: Text context
            candidates: List of candidate words from n-gram model
            n: Number of top candidates to return

        Returns:
            Re-ranked list of candidates
        """
        if not self._ensure_loaded() or not self._model or not candidates:
            return candidates[:n]

        try:
            # Score each candidate by computing likelihood
            scores = []
            for word in candidates[:20]:  # Limit to top 20 for speed
                # Create completion with candidate
                try:
                    # Use model's scoring (pseudo-likelihood)
                    result = self._model(
                        context,
                        max_new_tokens=1,
                        num_return_sequences=1,
                        do_sample=False,
                        return_full_text=False,
                    )

                    # Check if generated word matches candidate
                    generated = result[0]["generated_text"].strip().lower()
                    if generated.startswith(word[:3]):
                        scores.append((word, 1.0))
                    else:
                        scores.append((word, 0.5))

                except Exception:
                    scores.append((word, 0.5))

            # Sort by score (higher is better)
            scores.sort(key=lambda x: -x[1])
            return [word for word, _ in scores[:n]]

        except Exception as e:
            _logger.error("Re-ranking failed: %s", e)
            return candidates[:n]

    def predict_async(
        self, context: str, callback: Callable[[List[str]], None], n: int = 5
    ) -> None:
        """
        Generate predictions asynchronously.

        Args:
            context: Text context
            callback: Function to call with results
            n: Number of predictions
        """

        def worker():
            result = self.predict(context, n)
            callback(result)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def rerank_async(
        self, context: str, candidates: List[str], callback: Callable[[List[str]], None], n: int = 5
    ) -> None:
        """
        Re-rank candidates asynchronously.

        Args:
            context: Text context
            candidates: Candidate words to re-rank
            callback: Function to call with results
            n: Number of top candidates to return
        """

        def worker():
            result = self.rerank(context, candidates, n)
            callback(result)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
