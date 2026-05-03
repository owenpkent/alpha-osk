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
from typing import Dict, List, Optional, Tuple

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
        # Unigram: word -> frequency (MERGED VIEW — base + user).  Kept
        # for backwards compatibility; predict() uses the split tables
        # below.  External callers (hybrid_predictor._is_valid_word) rely
        # on this as a simple "is this word in the vocabulary" set.
        self.unigrams: Dict[str, int] = defaultdict(int)
        # Bigram: (prev_word, word) -> frequency
        self.bigrams: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # Trigram: (prev2, prev1, word) -> frequency (optional, more context)
        self.trigrams: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Total word count for probability calculation
        self.total_words = 0

        # Split-table scoring: predictions blend base-dictionary statistics
        # with the user's personal typing counts in probability space, so
        # frequently-typed words (e.g. "Claude") surface above common
        # English words even when the user's raw counts are small.
        #   P(w) = alpha · P_user(w) + (1 - alpha) · P_base(w)
        # alpha ("personal_weight") defaults to 0.7 — personal typing wins
        # on rank but the base dictionary still shapes the long tail.
        self._base_unigrams: Dict[str, int] = defaultdict(int)
        self._base_total: int = 0
        self.personal_weight: float = 0.7

        # User-typed word counts.  Incremented by learn() / learn_word();
        # feeds P_user in the split-table score.  Recency-decayed.
        self.user_vocab: Dict[str, int] = defaultdict(int)
        # Running sum of user_vocab.values().  Maintained incrementally
        # so predict() doesn't recompute sum() (O(N)) per keystroke.
        self._user_total: int = 0

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
        # sentence start, not mid-sentence (avoids "the Jack was loose"
        # or "there are Many reasons").  132 entries.
        self._ambiguous_names: set = {
            # Common words that are also first names
            "art", "bar", "bell", "bill", "bird", "bob", "bud", "buddy",
            "cam", "candy", "carol", "carry", "chase", "cliff", "con",
            "dale", "dawn", "dean", "desire", "don", "dot", "drew",
            "earl", "faith", "fan", "fern", "flora", "frank", "gay",
            "gene", "glad", "glen", "grace", "grant", "guy", "happy",
            "heath", "honor", "hope", "hunter", "iris", "ivy", "jack",
            "jade", "jan", "jean", "jerry", "jimmy", "joe", "john",
            "joy", "june", "junior", "kit", "lady", "lance", "lane",
            "lee", "lib", "lily", "lucky", "man", "many", "mark",
            "marine", "matt", "max", "may", "mercy", "mike", "min",
            "miss", "nick", "norm", "olive", "pat", "pearl", "pen",
            "penny", "pet", "peter", "princess", "queen", "ray", "reed",
            "rob", "robin", "rocky", "rose", "row", "ruby", "sandy",
            "see", "shell", "son", "song", "soon", "sue", "sun",
            "terry", "thu", "tiny", "troy", "valentine", "van", "violet",
            "wade", "ward", "will", "winter", "young",
            # Common words that overlap with other proper nouns
            "alpha", "angel", "angeles", "angle", "brain", "delta",
            "echo", "edge", "else", "era", "forest", "glory", "golden",
            "loan", "long", "love", "manual", "moon", "nova", "numbers",
            "season",
        }
        self._load_proper_nouns()

        # Recency decay: every N learn() calls, scale user frequencies down
        # so recent words gradually outweigh older ones
        self._learn_count = 0
        self._decay_interval = 50  # decay every 50 learn() calls
        self._decay_factor = 0.95  # multiply by this on each decay

        # Fragment filter: unknown words must pass a shape check AND be
        # sighted _candidate_threshold times before entering user_vocab.
        # Keeps random consonant clusters and one-off keyboard slips out
        # of predictions.  Gboard / AOSP LatinIME use a similar gate.
        self._candidate_counts: Dict[str, int] = defaultdict(int)
        self._candidate_threshold: int = 3

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
                self._base_unigrams[word] = 100
                self._base_total += 100
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

            # The Google 10K list is scraped from web search corpora and
            # contains every letter of the alphabet plus ~370 two-letter
            # abbreviations / state codes / fragments (pm, cd, uk, tx,
            # th, re, de, etc.). Each lands at frequency ~9700, so a
            # one-letter prefix surfaces all 26 letters in the pills.
            # Apply the same plausibility filter we use for learned
            # words so the OSK doesn't suggest "c", "x", "tv", "uk".
            kept = 0
            max_freq = len(words)
            for rank, word in enumerate(words):
                if not self._is_plausible_word(word):
                    continue
                frequency = max_freq - rank
                self.unigrams[word] = frequency
                self._base_unigrams[word] = frequency
                self._base_total += frequency
                self.total_words += frequency
                kept += 1

            _logger.info(
                "Google 10K wordlist loaded: %d words (%d filtered as fragments)",
                kept, len(words) - kept,
            )
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
                kept = 0
                for rank, word in enumerate(supplement):
                    if not self._is_plausible_word(word):
                        continue
                    if word not in self.unigrams:
                        # Lower frequency tier: these words rank below the 10K list
                        frequency = max(1, 500 - rank // 20)
                        self.unigrams[word] = frequency
                        self._base_unigrams[word] = frequency
                        self._base_total += frequency
                        self.total_words += frequency
                    kept += 1
                _logger.info(
                    "Supplement wordlist loaded: %d words (%d filtered as fragments)",
                    kept, len(supplement) - kept,
                )
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

    def learn_capitalization(self, word: str, *, allow_uppercase: bool = False) -> bool:
        """Learn preferred capitalization from user typing.

        Only stores non-trivial capitalization (not all-lower or first-letter-upper
        for words that aren't already known proper nouns).  Single-character
        words are skipped (handled by _always_capitalize).

        All-uppercase typings ("HELLO", "WORLD") are skipped by default —
        those almost always come from Caps Lock being on, not a deliberate
        signal that the word is canonically uppercase. Without this
        guard, every word the user types with caps lock on would
        pollute the capitalisation table, and predictions would come
        back shouty. Genuine acronyms (HBO, IBM, NASA) are loaded via
        `_load_proper_nouns` directly into ``self.capitalization``,
        bypassing this learn path, so they still work.

        Pass ``allow_uppercase=True`` when the caller has positive
        evidence that the user typed all-caps deliberately (e.g.
        Caps Lock was off for every char of the word — the user
        right-clicked / shifted each letter individually). The bridge
        gates this on its `_word_typed_under_caps_lock` flag.

        Returns True if a new or updated capitalization was saved.
        """
        if not word or len(word) < 2:
            return False
        if word.isupper() and not allow_uppercase:
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

    # Linear-interpolation weights for next-word scoring.  Mirrors the
    # classic Presage / LatinIME recipe: trigram evidence dominates,
    # bigram is the main fallback, unigram is the long-tail anchor.
    # All three probabilities live in [0, 1] so the weighted sum is
    # itself a probability — no SCALE-vs-raw-count mismatch.
    _LAMBDA_TRI = 0.5
    _LAMBDA_BI = 0.3
    _LAMBDA_UNI = 0.2

    def predict(self, context: str, n: int = 5) -> List[str]:
        """
        Predict next words based on context.

        Thin wrapper around :meth:`predict_with_scores` that strips the
        scores.  Kept for callers (and external integrations) that only
        need the ranked word list.

        Args:
            context: The text typed so far (full or partial word at end)
            n: Number of predictions to return

        Returns:
            List of predicted words, most likely first
        """
        return [word for word, _ in self.predict_with_scores(context, n)]

    def predict_with_scores(
        self, context: str, n: int = 5
    ) -> List[Tuple[str, float]]:
        """
        Predict next words with their interpolated probability scores.

        Scoring is a linear interpolation of conditional probabilities:

            score(w) = λ₃·P(w | w₋₂, w₋₁) + λ₂·P(w | w₋₁) + λ₁·P_uni(w)

        where P_uni is the split-table personal/base mix
        (``alpha·P_user + (1−alpha)·P_base``).  When there is no
        preceding context (pure partial-word completion), the weighted
        formula collapses to P_uni so the long-tail unigram ranking
        isn't artificially depressed.

        Returned scores are unnormalised — they're the raw interpolated
        values used internally for ranking.  Callers that need
        comparable probabilities across predictors (e.g. the merge
        strategies in :class:`HybridPredictor`) must normalise per
        source before combining.

        Args:
            context: The text typed so far (full or partial word at end)
            n: Number of predictions to return

        Returns:
            List of ``(word, score)`` tuples, most likely first
        """
        # IMPORTANT: Check for trailing space BEFORE stripping
        # Trailing space = user finished word, predict NEXT word
        # No trailing space = user typing, complete CURRENT word
        ends_with_space = context.endswith(" ")

        context_clean = context.lower().strip()
        if not context_clean:
            return self._top_unigrams_with_scores(n)

        words = self._tokenize(context_clean)

        # Check if user is mid-word (no trailing space in original)
        partial_word = ""
        if not ends_with_space and words:
            partial_word = words[-1]
            words = words[:-1]
        # else: user finished word (space at end) — predict next word

        # Conditional trigram probabilities for this 2-word prefix.
        # Normalising by the prefix-total turns raw counts into
        # P(w | w₋₂, w₋₁), which is what the interpolation expects.
        trigram_probs: Dict[str, float] = {}
        if len(words) >= 2:
            key = f"{words[-2]} {words[-1]}"
            tri_context = self.trigrams.get(key)
            if tri_context:
                total = sum(tri_context.values())
                if total > 0:
                    for word, freq in tri_context.items():
                        trigram_probs[word] = freq / total

        # Conditional bigram probabilities for the 1-word prefix.
        bigram_probs: Dict[str, float] = {}
        if len(words) >= 1:
            prev_word = words[-1]
            bi_context = self.bigrams.get(prev_word)
            if bi_context:
                total = sum(bi_context.values())
                if total > 0:
                    for word, freq in bi_context.items():
                        bigram_probs[word] = freq / total

        alpha = self.personal_weight
        user_total = self._user_total
        base_total = self._base_total
        # Decide the per-component weights.  When there's no context to
        # condition on, use unigram at full strength instead of λ₁·P_uni
        # — the trigram/bigram terms are identically zero, and down-
        # weighting unigram in that case would needlessly flatten
        # ranking.  Same logic when the user has typed a preceding word
        # the model has never seen (no bigram context): fall back to
        # unigram-at-full-weight rather than λ₁·P_uni.
        has_context = bool(trigram_probs) or bool(bigram_probs)
        if has_context:
            w_tri = self._LAMBDA_TRI
            w_bi = self._LAMBDA_BI
            w_uni = self._LAMBDA_UNI
        else:
            w_tri = 0.0
            w_bi = 0.0
            w_uni = 1.0

        # Candidate set: every word that could get non-zero score.
        seen_words: set[str] = set()
        seen_words.update(trigram_probs.keys())
        seen_words.update(bigram_probs.keys())
        seen_words.update(self._base_unigrams.keys())
        seen_words.update(self.user_vocab.keys())

        candidates: Dict[str, float] = {}
        for word in seen_words:
            if not self._matches_partial(word, partial_word):
                continue
            p_tri = trigram_probs.get(word, 0.0)
            p_bi = bigram_probs.get(word, 0.0)
            base_freq = self._base_unigrams.get(word, 0)
            user_freq = self.user_vocab.get(word, 0)
            p_base = (base_freq / base_total) if base_total else 0.0
            p_user = (user_freq / user_total) if user_total else 0.0
            p_uni = alpha * p_user + (1.0 - alpha) * p_base

            score = w_tri * p_tri + w_bi * p_bi + w_uni * p_uni
            if score > 0:
                candidates[word] = score

        sorted_candidates = sorted(candidates.items(), key=lambda x: -x[1])
        return sorted_candidates[:n]

    def _matches_partial(self, word: str, partial: str) -> bool:
        """Check if word matches partial input."""
        if not partial:
            return True
        return word.startswith(partial)

    def _top_unigrams(self, n: int) -> List[str]:
        """Get top n words by frequency."""
        return [word for word, _ in self._top_unigrams_with_scores(n)]

    def _top_unigrams_with_scores(self, n: int) -> List[Tuple[str, float]]:
        """Top n words by frequency, with the raw frequency as score.

        Used by :meth:`predict_with_scores` when there is no context to
        condition on.  The raw integer frequency stands in for the
        unigram probability (callers normalise per source before
        combining).
        """
        sorted_words = sorted(
            self.unigrams.items(), key=lambda x: -x[1]
        )
        return [(word, float(freq)) for word, freq in sorted_words[:n]]

    def _tokenize(self, text: str) -> List[str]:
        """Split text into words."""
        # Simple tokenization: split on non-alphanumeric
        words = re.findall(r"[a-zA-Z']+", text.lower())
        return words

    # Vowels include 'y' so "cry", "try", "rhythm" pass the shape filter.
    _VOWELS = frozenset("aeiouy")
    # 1- and 2-letter words that ARE legitimate — everything else at
    # length ≤ 2 is treated as a fragment.  Kept small on purpose; edge
    # cases just need to be typed a few times to enter user_vocab via
    # the repetition gate path for unknown words that happen to have a
    # vowel, but the pure-fragment case (length ≤ 2, no vowel) is
    # shape-rejected outright.
    _SHORT_WORD_WHITELIST = frozenset({
        "a", "i",
        "am", "an", "as", "at", "be", "by", "do", "go",
        "he", "hi", "if", "in", "is", "it", "me", "my",
        "no", "of", "oh", "ok", "on", "or", "so", "to",
        "up", "us", "we", "ya", "ha", "ah", "eh", "mm",
        "hm", "mr", "ms", "dr", "st", "pm",
    })

    def _is_plausible_word(self, word: str) -> bool:
        """Reject obvious keyboard-slip fragments.

        Rules:
          - length ≤ 2: must be in the short-word whitelist
          - length ≥ 3: must contain at least one vowel (a/e/i/o/u/y)
            AND at least one non-aeiou letter.  The two-sided check
            rejects both all-consonant clusters ("xqz") and vowel mashing
            ("aaaa", "iii").  'y' is counted as a vowel for the first
            check (so "cry", "rhythm" pass) but as a consonant for the
            second (so "eye", "aye" still pass).
        """
        n = len(word)
        if n == 0:
            return False
        if n <= 2:
            return word in self._SHORT_WORD_WHITELIST
        has_vowel = False
        has_consonant = False
        for c in word:
            if c in "aeiou":
                has_vowel = True
            elif c == "y":
                has_vowel = True
                has_consonant = True
            elif c.isalpha():
                has_consonant = True
        return has_vowel and has_consonant

    def learn(self, text: str) -> List[str]:
        """
        Learn from new text, updating n-gram frequencies.

        Unknown words pass through a two-stage filter:
          1. Shape check — rejects all-consonant clusters and untrusted
             1-/2-letter fragments outright.
          2. Repetition gate — surviving unknown words must be sighted
             ``_candidate_threshold`` times (default 3) before entering
             user_vocab.  Known base-dict words and words already in
             user_vocab skip the gate.

        Bigrams and trigrams are only formed between words that actually
        land in the vocabulary on this call, so a gated fragment never
        produces a "the xqz" context edge.

        Returns:
            List of words that were new to user_vocab (first time learned).
        """
        words = self._tokenize(text)
        if not words:
            return []

        new_words: List[str] = []
        # Parallel to `words`; entry is the word iff it was accepted into
        # the vocabulary on this call, else None.  Drives bigram/trigram
        # updates so filtered/gated words don't seed context edges.
        learned: List[Optional[str]] = []

        for word in words:
            if not self._is_plausible_word(word):
                learned.append(None)
                continue

            if word in self._base_unigrams or word in self.user_vocab:
                # Known word — learn immediately, bypass the gate.
                was_new = word not in self.user_vocab
                self.unigrams[word] += 1
                self.user_vocab[word] += 1
                self._user_total += 1
                self.total_words += 1
                if was_new:
                    new_words.append(word)
                learned.append(word)
                continue

            # Unknown but plausible — accumulate sightings in the
            # candidate pool and only promote once the threshold is hit.
            self._candidate_counts[word] += 1
            if self._candidate_counts[word] >= self._candidate_threshold:
                count = self._candidate_counts.pop(word)
                self.unigrams[word] += count
                self.user_vocab[word] += count
                self._user_total += count
                self.total_words += count
                new_words.append(word)
                learned.append(word)
            else:
                learned.append(None)

        # Update bigrams — only between neighbours that both made it in.
        for i in range(1, len(learned)):
            prev_word, curr_word = learned[i - 1], learned[i]
            if prev_word and curr_word:
                self.bigrams[prev_word][curr_word] += 1

        # Update trigrams — all three positions must have been accepted.
        for i in range(2, len(learned)):
            w2, w1, curr = learned[i - 2], learned[i - 1], learned[i]
            if w2 and w1 and curr:
                key = f"{w2} {w1}"
                self.trigrams[key][curr] += 1

        # Periodic recency decay so old words don't dominate
        self._learn_count += 1
        if self._learn_count >= self._decay_interval:
            self._apply_decay()
            self._learn_count = 0

        return new_words

    def _learn_base(self, text: str) -> None:
        """Learn from a base corpus / built-in dictionary.

        Unlike :meth:`learn`, counts go into ``_base_unigrams`` (not
        ``user_vocab``), so loading the shipped dictionary does not mask
        the user's genuine typing signal.  Bigrams and trigrams are still
        populated — those tables are not split in the current design, and
        the base sentences are useful context regardless.
        """
        words = self._tokenize(text)
        if not words:
            return

        for word in words:
            self._base_unigrams[word] += 1
            self.unigrams[word] += 1
            self._base_total += 1
            self.total_words += 1

        for i in range(1, len(words)):
            self.bigrams[words[i - 1]][words[i]] += 1
        for i in range(2, len(words)):
            key = f"{words[i - 2]} {words[i - 1]}"
            self.trigrams[key][words[i]] += 1

    def _apply_decay(self) -> None:
        """Scale down user-learned frequencies so recent words outweigh old ones."""
        factor = self._decay_factor
        min_freq = 1

        # Decay user vocab boost
        to_remove = []
        new_total = 0
        for word in self.user_vocab:
            self.user_vocab[word] = int(self.user_vocab[word] * factor)
            if self.user_vocab[word] < min_freq:
                to_remove.append(word)
            else:
                new_total += self.user_vocab[word]
        for word in to_remove:
            del self.user_vocab[word]
        self._user_total = new_total

        # Decay user-learned bigrams (only those above base dictionary levels)
        for prev_word in list(self.bigrams):
            for word in list(self.bigrams[prev_word]):
                self.bigrams[prev_word][word] = max(
                    min_freq, int(self.bigrams[prev_word][word] * factor)
                )

        # Decay candidate counts too — a word seen once long ago
        # shouldn't slowly accumulate toward promotion across sessions.
        for word in list(self._candidate_counts):
            decayed = int(self._candidate_counts[word] * factor)
            if decayed < 1:
                del self._candidate_counts[word]
            else:
                self._candidate_counts[word] = decayed

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
            self._user_total += 5
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
            "candidate_counts": dict(self._candidate_counts),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)
        _logger.info("Model saved to %s", path)

    # Defensive bounds on saved-model shape.  A legitimate model grown
    # through normal typing stays well under these limits; values beyond
    # them suggest a corrupt or crafted file and are refused rather than
    # risking OOM at startup.
    _MAX_MODEL_FILE_BYTES = 50 * 1024 * 1024     # 50 MB on disk
    _MAX_UNIGRAMS = 500_000
    _MAX_BIGRAMS_PREFIXES = 500_000
    _MAX_CAPITALIZATIONS = 100_000

    def load(self, path: Path) -> None:
        """Load model from JSON file."""
        try:
            file_size = path.stat().st_size
            if file_size > self._MAX_MODEL_FILE_BYTES:
                _logger.warning(
                    "Model file %s too large (%d bytes > %d cap); skipping load.",
                    path, file_size, self._MAX_MODEL_FILE_BYTES,
                )
                return

            with open(path) as f:
                data = json.load(f)

            unigrams = data.get("unigrams", {})
            if len(unigrams) > self._MAX_UNIGRAMS:
                _logger.warning(
                    "Model file %s has %d unigrams (> %d); skipping load.",
                    path, len(unigrams), self._MAX_UNIGRAMS,
                )
                return
            bigrams = data.get("bigrams", {})
            if len(bigrams) > self._MAX_BIGRAMS_PREFIXES:
                _logger.warning(
                    "Model file %s has %d bigram prefixes (> %d); skipping load.",
                    path, len(bigrams), self._MAX_BIGRAMS_PREFIXES,
                )
                return
            caps = data.get("capitalization", {})
            if len(caps) > self._MAX_CAPITALIZATIONS:
                _logger.warning(
                    "Model file %s has %d capitalizations (> %d); skipping load.",
                    path, len(caps), self._MAX_CAPITALIZATIONS,
                )
                return

            # Strip fragments from older saved models. The dictionary
            # loaders apply this filter at startup so fresh installs
            # are clean, but a long-running user's model.json was
            # saved before the filter existed and still contains every
            # letter of the alphabet plus ~370 two-letter abbreviations
            # at high frequencies. Drop them on load and the next save
            # writes the cleaned model back.
            unigrams = {
                w: c for w, c in unigrams.items()
                if self._is_plausible_word(w)
            }
            user_vocab_raw = data.get("user_vocab", {})
            user_vocab_clean = {
                w: c for w, c in user_vocab_raw.items()
                if self._is_plausible_word(w)
            }

            self.unigrams = defaultdict(int, unigrams)
            self.bigrams = defaultdict(
                lambda: defaultdict(int),
                {k: defaultdict(int, v) for k, v in bigrams.items()}
            )
            self.trigrams = defaultdict(
                lambda: defaultdict(int),
                {k: defaultdict(int, v) for k, v in data.get("trigrams", {}).items()}
            )
            self.user_vocab = defaultdict(int, user_vocab_clean)
            # Rebuild incremental running total from loaded counts.
            self._user_total = sum(self.user_vocab.values())
            self.total_words = data.get("total_words", 0)
            self.blacklist = set(data.get("blacklist", []))
            self.dispreference = defaultdict(int, data.get("dispreference", {}))
            self._blacklist_type_count = defaultdict(int, data.get("blacklist_type_count", {}))
            self._candidate_counts = defaultdict(int, data.get("candidate_counts", {}))
            # Merge saved capitalization with built-in proper nouns (user overrides win)
            self.capitalization.update(caps)
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

            # Process each line — route through _learn_base so counts go
            # into _base_unigrams and do NOT inflate the user's personal
            # vocab (which would mask actual personal typing signal).
            #
            # Two line formats accepted:
            #   word                  → +1 to unigrams via _learn_base
            #   word count            → +count, set directly so high-freq
            #                            entries (contractions, etc.) can
            #                            compete with the Google 10K wordlist
            for line in content.split("\n"):
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1].isdigit():
                    word = parts[0].lower()
                    count = int(parts[1])
                    self.unigrams[word] += count
                    self._base_unigrams[word] += count
                    self._base_total += count
                    self.total_words += count
                else:
                    self._learn_base(line)

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
        self._base_unigrams.clear()
        self._base_total = 0
        self._user_total = 0
        self.total_words = 0
        self.blacklist.clear()
        self.dispreference.clear()
        self._blacklist_type_count.clear()
        self._candidate_counts.clear()
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
