"""
N-gram based word prediction engine.

Fast, lightweight prediction using word frequency and context.
This is the "instant" layer of the hybrid approach.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from ..atomic_write import atomic_write_json
from .language import ENGLISH, LanguageProfile
from .token_predictor import TokenPredictor

_logger = logging.getLogger("NgramPredictor")


class NgramPredictor:
    """
    N-gram based predictor for instant word suggestions.

    Uses unigram (word frequency) and bigram (word pairs) models
    to predict the next word based on context.
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        profile: LanguageProfile = ENGLISH,
    ):
        """
        Initialize the predictor.

        Args:
            model_path: Path to saved model file. If None, starts with empty model.
            profile: Which language's word shapes, wordlists and
                always-capitalise map to use.  Defaults to English, which
                is expressed as a profile like any other rather than as
                the constants this class used to carry inline.
        """
        self.profile = profile
        # Unigram: word -> frequency (MERGED VIEW — base + user).  Kept
        # for backwards compatibility; predict() uses the split tables
        # below.  External callers (hybrid_predictor._is_valid_word) rely
        # on this as a simple "is this word in the vocabulary" set.
        self.unigrams: Dict[str, int] = defaultdict(int)
        # Bigram: (prev_word, word) -> frequency
        self.bigrams: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # Trigram: (prev2, prev1, word) -> frequency (optional, more context)
        self.trigrams: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # The user's half of the two context tables above.  ``bigrams`` /
        # ``trigrams`` stay the merged view every reader expects; the base
        # share is whatever is left once the user's share is taken out
        # (invariant: ``bigrams[p][w] >= round(_user_bigrams[p][w])``).
        # Keeping the halves apart is what lets the shipped seeds be
        # re-applied on every launch without accumulating, lets recency
        # decay act on the user's typing without erasing the curated
        # corpus, and lets scoring trust the user's phrases in proportion
        # to how often they were typed (see ``_context_probs``).  Floats,
        # so a count decays a little at a time instead of snapping to the
        # int floor of 1, which is how 78% of a matured model's edges
        # ended up parked there permanently.  Only these two tables are
        # persisted; the base half is rebuilt from the data files.
        self._user_bigrams: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._user_trigrams: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

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
        # Explicit user boosts via the prediction-pill "Show more" menu.
        # Value is the cumulative +5 boosts applied to ``unigrams`` /
        # ``user_vocab``; tracking it lets the dashboard surface the
        # word as boosted AND lets ``unprefer`` roll the boost back.
        self.preferred: Dict[str, int] = defaultdict(int)

        # Auto-rehabilitation: track how many times a blacklisted word is typed
        self._blacklist_type_count: Dict[str, int] = defaultdict(int)
        self._rehabilitate_threshold = 3

        # Capitalization: lowercase → preferred form (e.g. "owen" → "Owen")
        self.capitalization: Dict[str, str] = {}
        # Structured tokens (phone numbers, zips, house numbers, emails).
        # `_tokenize` strips every digit and symbol, so these cannot live
        # in the vocabulary above; the store is owned here purely so it
        # rides along in this class's save/load and gets backed up with
        # the rest of what the user taught us.  See token_predictor.py.
        self.tokens = TokenPredictor()
        # Words that are ALWAYS capitalized regardless of position.
        # Language data, so it comes from the profile; see language.py.
        self._always_capitalize: Mapping[str, str] = profile.always_capitalize
        # Words that are common English AND names — only capitalize at
        # sentence start, not mid-sentence (avoids "the Jack was loose"
        # or "there are Many reasons").  132 entries.
        self._ambiguous_names: set = {
            # Common words that are also first names
            "art",
            "bar",
            "bell",
            "bill",
            "bird",
            "bob",
            "bud",
            "buddy",
            "cam",
            "candy",
            "carol",
            "carry",
            "chase",
            "cliff",
            "con",
            "dale",
            "dawn",
            "dean",
            "desire",
            "don",
            "dot",
            "drew",
            "earl",
            "faith",
            "fan",
            "fern",
            "flora",
            "frank",
            "gay",
            "gene",
            "glad",
            "glen",
            "grace",
            "grant",
            "guy",
            "happy",
            "heath",
            "honor",
            "hope",
            "hunter",
            "iris",
            "ivy",
            "jack",
            "jade",
            "jan",
            "jean",
            "jerry",
            "jimmy",
            "joe",
            "john",
            "joy",
            "june",
            "junior",
            "kit",
            "lady",
            "lance",
            "lane",
            "lee",
            "lib",
            "lily",
            "lucky",
            "man",
            "many",
            "mark",
            "marine",
            "matt",
            "max",
            "may",
            "mercy",
            "mike",
            "min",
            "miss",
            "nick",
            "norm",
            "olive",
            "pat",
            "pearl",
            "pen",
            "penny",
            "pet",
            "peter",
            "princess",
            "queen",
            "ray",
            "reed",
            "rob",
            "robin",
            "rocky",
            "rose",
            "row",
            "ruby",
            "sandy",
            "see",
            "shell",
            "son",
            "song",
            "soon",
            "sue",
            "sun",
            "terry",
            "thu",
            "tiny",
            "troy",
            "valentine",
            "van",
            "violet",
            "wade",
            "ward",
            "will",
            "winter",
            "young",
            # Common words that overlap with other proper nouns
            "alpha",
            "angel",
            "angeles",
            "angle",
            "brain",
            "delta",
            "echo",
            "edge",
            "else",
            "era",
            "forest",
            "glory",
            "golden",
            "loan",
            "long",
            "love",
            "manual",
            "moon",
            "nova",
            "numbers",
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
        # Parallel _candidate_last_seen tracks the most-recent sighting
        # time so accidental sightings expire if never reinforced — see
        # _sweep_stale_candidates.
        self._candidate_counts: Dict[str, int] = defaultdict(int)
        self._candidate_last_seen: Dict[str, float] = {}
        self._candidate_threshold: int = 3
        # 30 days. A candidate not re-sighted within this window is
        # dropped at the next decay tick. Picks a balance between
        # forgiving slow learners (a word the user uses once a fortnight
        # still gets to 3 sightings before timing out) and not letting
        # stale typos linger forever in the pool.
        self._candidate_max_age_seconds: float = 30 * 86400

        # Load Google 10K wordlist (frequency-ranked) if available
        self._load_frequency_wordlist()

        # Fallback common words if wordlist not available
        if self.total_words == 0:
            self._common_words = [
                "the",
                "be",
                "to",
                "of",
                "and",
                "a",
                "in",
                "that",
                "have",
                "I",
                "it",
                "for",
                "not",
                "on",
                "with",
                "he",
                "as",
                "you",
                "do",
                "at",
                "this",
                "but",
                "his",
                "by",
                "from",
                "they",
                "we",
                "say",
                "her",
                "she",
                "or",
                "an",
                "will",
                "my",
                "one",
                "all",
                "would",
                "there",
                "their",
                "what",
                "so",
                "up",
                "out",
                "if",
                "about",
                "who",
                "get",
                "which",
                "go",
                "me",
                "is",
                "are",
                "was",
                "were",
                "been",
                "being",
                "am",
                "can",
                "could",
                "may",
                "might",
                "must",
                "shall",
                "should",
                "will",
                "would",
                "need",
                "want",
                "like",
                "hello",
                "hi",
                "thanks",
                "thank",
                "please",
                "yes",
                "no",
                "okay",
                "ok",
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
        wordlist_path = self.profile.frequency
        if wordlist_path is None:
            return

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
                kept,
                len(words) - kept,
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
                    kept,
                    len(supplement) - kept,
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
        """Return capitalization for a word.

        Only the "I" family ("I", "I'm", "I'll", "I'd", "I've") is
        auto-capitalized. Sentence-start auto-cap and the proper-noun /
        learned-capitalization tiers were removed because they fired on
        too many common English words ("hope", "rose", "may", "mark",
        and the post-period word in any sentence). The user's intent —
        capitalize when shift / caps lock is engaged — is captured
        downstream by the typed-prefix mirror in
        ``KeyboardBridge._display_cased``.

        ``self.capitalization`` is still populated by ``_load_proper_nouns``
        and ``learn_capitalization`` and persisted with the model, but
        is intentionally not consulted here. Keeping it lets a future
        opt-in switch re-enable proper-noun cap without losing the
        accumulated user preferences.

        Args:
            word: The word to capitalize (usually lowercase).
            sentence_start: Kept for API compatibility; ignored.
        """
        lower = word.lower()
        if lower in self._always_capitalize:
            return self._always_capitalize[lower]
        return word

    # Linear-interpolation weights for next-word scoring.  Mirrors the
    # classic Presage / LatinIME recipe: trigram evidence dominates,
    # bigram is the main fallback, unigram is the long-tail anchor.
    # All three probabilities live in [0, 1] so the weighted sum is
    # itself a probability — no SCALE-vs-raw-count mismatch.
    _LAMBDA_TRI = 0.5
    _LAMBDA_BI = 0.3
    _LAMBDA_UNI = 0.2

    # How far to trust the user's own context counts against the base
    # corpus, per prefix.  The user's distribution gets weight
    # ``U / (U + prior)`` where ``U`` is how many times the user has typed
    # anything after this prefix and ``prior`` is
    # ``_CONTEXT_PRIOR_FLOOR + _CONTEXT_BASE_TRUST * (base count for the
    # prefix)``.  Two things this shape buys, and both were measured
    # failures of the single merged table: a phrase typed once after a
    # word with a rich seed table (2,600 seed mass behind "the") needed
    # 55 typings to reach the pills, and a phrase typed once after a word
    # with no seeds at all was scored as a certainty (P = 1.0).  With the
    # seeds loaded at 50 per curated pair, a trust of 0.02 makes one
    # curated pair worth one user typing; the floor keeps a prefix with
    # no base evidence from handing a single sighting the whole
    # distribution (one typing gets 1 / 6, five get 1 / 2).  With no user
    # evidence the formula collapses to the base distribution exactly, so
    # a fresh model scores byte-for-byte as it did before the split.
    _CONTEXT_PRIOR_FLOOR = 5.0
    _CONTEXT_BASE_TRUST = 0.02
    # A user context count below this after decay is dropped.  At the
    # 0.95 decay factor a single typing survives about 45 decay ticks,
    # roughly a week of ordinary typing, before it is forgotten.
    _USER_CONTEXT_MIN = 0.1

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

    def predict_with_scores(self, context: str, n: int = 5) -> List[Tuple[str, float]]:
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
            trigram_probs = self._context_probs(
                self.trigrams.get(key), self._user_trigrams.get(key)
            )

        # Conditional bigram probabilities for the 1-word prefix.
        bigram_probs: Dict[str, float] = {}
        if len(words) >= 1:
            prev_word = words[-1]
            bigram_probs = self._context_probs(
                self.bigrams.get(prev_word), self._user_bigrams.get(prev_word)
            )

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
        sorted_words = sorted(self.unigrams.items(), key=lambda x: -x[1])
        return [(word, float(freq)) for word, freq in sorted_words[:n]]

    def _tokenize(self, text: str) -> List[str]:
        """Split text into words, per the active language's word shape."""
        return self.profile.word_re.findall(text.lower())

    def _is_plausible_word(self, word: str) -> bool:
        """Reject obvious keyboard-slip fragments.

        Rules:
          - length ≤ 2: must be on the profile's short-word allow-list
          - length ≥ 3: must contain at least one vowel AND at least one
            letter that is not a strict vowel.  The two-sided check
            rejects both all-consonant clusters ("xqz") and vowel mashing
            ("aaaa", "iii").  A *semivowel* counts on both sides, which
            in English is 'y': it passes the vowel half (so "cry",
            "rhythm" survive) and the consonant half (so "eye", "aye"
            still do too).

        All three sets come from ``self.profile``; see language.py for
        why they are not literals here any more.
        """
        n = len(word)
        if n == 0:
            return False
        if n <= 2:
            return self.profile.is_short_word(word)
        vowels = self.profile.vowels
        semivowels = self.profile.semivowels
        has_vowel = False
        has_consonant = False
        for c in word:
            if c in vowels:
                has_vowel = True
            elif c in semivowels:
                has_vowel = True
                has_consonant = True
            elif c.isalpha():
                has_consonant = True
        return has_vowel and has_consonant

    def learn(self, text: str, *, corpus: bool = False) -> List[str]:
        """
        Learn from new text, updating n-gram frequencies.

        ``corpus=True`` marks the text as shipped training data rather
        than the user's typing: the bigram / trigram pairs then go to the
        base share of the context tables, which is re-read from the data
        files on every launch and never persisted, instead of to the
        user's share, which is.  The unigram side is unchanged either way.

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
            self._candidate_last_seen[word] = time.time()
            if self._candidate_counts[word] >= self._candidate_threshold:
                count = self._candidate_counts.pop(word)
                self._candidate_last_seen.pop(word, None)
                self.unigrams[word] += count
                self.user_vocab[word] += count
                self._user_total += count
                self.total_words += count
                new_words.append(word)
                learned.append(word)
            else:
                learned.append(None)

        self._link_context(learned, base=corpus)

        # Periodic recency decay so old words don't dominate
        self._learn_count += 1
        if self._learn_count >= self._decay_interval:
            self._apply_decay()
            self._learn_count = 0

        return new_words

    def _link_context(self, learned: List[Optional[str]], *, base: bool) -> None:
        """Record the bigram / trigram pairs of one accepted word sequence.

        ``learned`` is parallel to the tokenised text; a ``None`` marks a
        word that did not make it into the vocabulary, and no pair is
        formed across one, so a gated fragment never seeds a context edge.
        Base pairs go straight into the merged tables; user pairs go
        through ``_bump_user_context`` so the user's share is tracked.
        """
        for i in range(1, len(learned)):
            prev_word, curr_word = learned[i - 1], learned[i]
            if prev_word and curr_word:
                if base:
                    self.bigrams[prev_word][curr_word] += 1
                else:
                    self._bump_user_context(self._user_bigrams, self.bigrams, prev_word, curr_word)
        for i in range(2, len(learned)):
            w2, w1, curr = learned[i - 2], learned[i - 1], learned[i]
            if w2 and w1 and curr:
                key = f"{w2} {w1}"
                if base:
                    self.trigrams[key][curr] += 1
                else:
                    self._bump_user_context(self._user_trigrams, self.trigrams, key, curr)

    def learn_corpus_context(self, text: str) -> None:
        """Add a corpus's bigram / trigram pairs to the base share only.

        The unigram side is deliberately left alone.  ``reload_from_disk``
        uses this after a Data Backup import: the file it re-reads holds
        only the user's half of the context tables, so the shipped seeds
        and corpus have to be re-applied, and re-learning the corpus in
        full would add one more copy of its words to the unigram counts.
        """
        learned: List[Optional[str]] = [
            word if self._is_plausible_word(word) else None for word in self._tokenize(text)
        ]
        self._link_context(learned, base=True)

    @staticmethod
    def _ri(value: float) -> int:
        """Round half up; ``round()`` rounds half to even."""
        return int(value + 0.5)

    def _bump_user_context(
        self,
        user_table: Dict[str, Dict[str, float]],
        merged_table: Dict[str, Dict[str, int]],
        prefix: str,
        word: str,
        delta: float = 1.0,
    ) -> None:
        """Add ``delta`` to a user context count, keeping the merged view in step."""
        old = user_table[prefix].get(word, 0.0)
        new = old + delta
        user_table[prefix][word] = new
        merged_table[prefix][word] += self._ri(new) - self._ri(old)

    def _decay_user_context(
        self,
        user_table: Dict[str, Dict[str, float]],
        merged_table: Dict[str, Dict[str, int]],
        factor: float,
    ) -> None:
        """Scale the user's share of a context table down, dropping the dust.

        The merged view loses exactly what the user's share loses, so the
        base share underneath is untouched: a curated pair keeps its seed
        count however long the session runs.
        """
        for prefix in list(user_table):
            row = user_table[prefix]
            merged_row = merged_table.get(prefix)
            for word in list(row):
                old = row[word]
                new = old * factor
                if new < self._USER_CONTEXT_MIN:
                    del row[word]
                    new = 0.0
                else:
                    row[word] = new
                if merged_row is not None:
                    merged_row[word] -= self._ri(old) - self._ri(new)
                    if merged_row[word] <= 0:
                        del merged_row[word]
            if not row:
                del user_table[prefix]
            if merged_row is not None and not merged_row:
                del merged_table[prefix]

    def _context_probs(
        self,
        merged_ctx: Optional[Dict[str, int]],
        user_ctx: Optional[Dict[str, float]],
    ) -> Dict[str, float]:
        """P(w | prefix), blending the base and user shares of one prefix row.

        With no user evidence this is the merged row normalised, exactly
        the pre-split computation.  Otherwise the user's distribution is
        trusted with weight ``U / (U + prior)``; see the constants above
        for why the prior scales with the base evidence.  When the prefix
        has no base evidence at all the remaining ``1 - w`` mass goes
        nowhere, which is the point: it falls through to the lower-order
        terms of the interpolation rather than crowning a single sighting.
        """
        if not merged_ctx:
            return {}
        user_evidence = sum(user_ctx.values()) if user_ctx else 0.0
        if user_evidence <= 0.0 or user_ctx is None:
            total = sum(merged_ctx.values())
            if total <= 0:
                return {}
            return {word: count / total for word, count in merged_ctx.items()}
        base_counts: Dict[str, int] = {}
        base_total = 0
        for word, count in merged_ctx.items():
            base = count - self._ri(user_ctx.get(word, 0.0))
            if base > 0:
                base_counts[word] = base
                base_total += base
        prior = self._CONTEXT_PRIOR_FLOOR + self._CONTEXT_BASE_TRUST * base_total
        w_user = user_evidence / (user_evidence + prior)
        probs: Dict[str, float] = {}
        for word in set(merged_ctx) | set(user_ctx):
            p = w_user * (user_ctx.get(word, 0.0) / user_evidence)
            if base_total > 0:
                p += (1.0 - w_user) * (base_counts.get(word, 0) / base_total)
            if p > 0.0:
                probs[word] = p
        return probs

    def _learn_base(self, text: str) -> None:
        """Learn from a base corpus / built-in dictionary.

        Unlike :meth:`learn`, counts go into ``_base_unigrams`` (not
        ``user_vocab``), so loading the shipped dictionary does not mask
        the user's genuine typing signal.  Bigrams and trigrams go to the
        base share of the context tables for the same reason.
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

        # Decay the user's share of both context tables.  This used to
        # scale every bigram in the merged table, seeds included, floored
        # at 1: on a fresh model the curated corpus was flat within about
        # 2,500 learns, and on a matured one the seeds instead inflated,
        # because every launch re-added them on top of the decayed file.
        # Trigrams were never decayed at all, so a user trigram typed once
        # sat at 1 against seeds that grew by 50 per launch.
        self._decay_user_context(self._user_bigrams, self.bigrams, factor)
        self._decay_user_context(self._user_trigrams, self.trigrams, factor)

        # Time-based sweep: drop candidates not seen within the max-age
        # window before applying the multiplicative decay. An accidental
        # pill click on a typo shouldn't sit in the pool indefinitely
        # just because the user types fast enough to keep the
        # learn-call decay sparse.
        self._sweep_stale_candidates()

        # Multiplicative decay: a word seen once long ago shouldn't
        # slowly accumulate toward promotion across sessions even if
        # within the time window.
        for word in list(self._candidate_counts):
            decayed = int(self._candidate_counts[word] * factor)
            if decayed < 1:
                del self._candidate_counts[word]
                self._candidate_last_seen.pop(word, None)
            else:
                self._candidate_counts[word] = decayed

        _logger.debug("Applied recency decay (factor=%.2f)", factor)

    def _sweep_stale_candidates(self) -> None:
        """Drop candidate entries older than ``_candidate_max_age_seconds``.

        Called from :meth:`_apply_decay`. Entries lacking a timestamp
        (e.g. loaded from a pre-timestamp save file) are stamped with
        the current time on first sweep so they get the full age
        window from that point rather than being instantly expired.
        """
        now = time.time()
        cutoff = now - self._candidate_max_age_seconds
        for word in list(self._candidate_counts):
            last = self._candidate_last_seen.get(word)
            if last is None:
                # Backfill: stamp now and re-check next sweep.
                self._candidate_last_seen[word] = now
                continue
            if last < cutoff:
                del self._candidate_counts[word]
                self._candidate_last_seen.pop(word, None)

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

    def mark_good(self, word: str) -> None:
        """Boost a word and record the boost so it can be undone.

        Bumps ``unigrams`` / ``user_vocab`` by +5 (same magnitude as
        :meth:`learn_word` and the prediction-click reinforcement) and
        increments ``preferred[word]`` so the dashboard can surface
        the word and :meth:`unprefer` can roll the boost back later.
        """
        word_lower = word.lower().strip()
        if not word_lower:
            return
        self.learn_word(word_lower)
        self.preferred[word_lower] += 5
        _logger.info("Marked good: %s (boost now %d)", word, self.preferred[word_lower])

    def unprefer(self, word: str) -> None:
        """Roll back an explicit user boost.

        Decrements ``unigrams`` / ``user_vocab`` / ``_user_total`` /
        ``total_words`` by the cumulative boost amount, then drops the
        ``preferred`` entry. Capped at the current counter values so a
        word that was also organically learned still keeps its
        organic count after the boost is removed.
        """
        word_lower = word.lower().strip()
        if not word_lower or word_lower not in self.preferred:
            return
        amount = self.preferred[word_lower]
        rollback = min(amount, self.user_vocab.get(word_lower, 0))
        if rollback > 0:
            self.user_vocab[word_lower] -= rollback
            self._user_total = max(0, self._user_total - rollback)
            self.unigrams[word_lower] = max(0, self.unigrams.get(word_lower, 0) - rollback)
            self.total_words = max(0, self.total_words - rollback)
            if self.user_vocab[word_lower] <= 0:
                del self.user_vocab[word_lower]
            if self.unigrams.get(word_lower, 0) <= 0:
                self.unigrams.pop(word_lower, None)
        del self.preferred[word_lower]
        _logger.info("Unpreferred: %s (rolled back %d)", word, rollback)

    def get_preference(self, word: str) -> int:
        """Get the explicit boost count for a word (0 if not boosted)."""
        return self.preferred.get(word.lower(), 0)

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
                _logger.info(
                    "Auto-rehabilitated word: %s (typed %d times)",
                    word_lower,
                    self._rehabilitate_threshold,
                )
                return word_lower
        return None

    def learn_word(self, word: str) -> None:
        """Learn a single word (boost its frequency).

        Bypasses the candidate gate — used for explicit user actions
        (right-click → Show more, vocab pack import) where the user has
        deliberately signalled "boost this word." For implicit signals
        like a prediction pill click, route through
        :meth:`learn_from_pill_click` instead so an accidental click on a
        brand-new word doesn't permanently inflate the model.
        """
        word = word.lower().strip()
        if word:
            self.unigrams[word] += 5
            self.user_vocab[word] += 5
            self._user_total += 5
            self.total_words += 5

    # Per-click weight when promoting a candidate through the pill-click
    # path. Matches the +5 that :meth:`learn_word` applies for known
    # words, so a word that promotes at the threshold lands with
    # threshold * 5 weight rather than a single sighting.
    _PILL_CLICK_WEIGHT: int = 5

    def learn_from_pill_click(self, word: str) -> None:
        """Reinforce a word the user selected from the prediction bar.

        Known words (base dict or already in user_vocab) get the same
        +5 boost :meth:`learn_word` applies. Unknown words route through
        the candidate gate: each click is one sighting, recorded in
        ``_candidate_counts`` / ``_candidate_last_seen``; promotion
        only happens after ``_candidate_threshold`` clicks. Without
        this gate a single click on a fuzzy- or PPM-generated pill
        would inject a never-typed word into ``user_vocab`` permanently
        with weight 5.
        """
        word = word.lower().strip()
        if not word:
            return
        if word in self._base_unigrams or word in self.user_vocab:
            # Known word — reinforce immediately, no gate.
            self.unigrams[word] += self._PILL_CLICK_WEIGHT
            self.user_vocab[word] += self._PILL_CLICK_WEIGHT
            self._user_total += self._PILL_CLICK_WEIGHT
            self.total_words += self._PILL_CLICK_WEIGHT
            return
        # Plausibility filter: a brand-new word coming via a pill click
        # already cleared a higher bar than free-typing (an engine
        # generated it from the user's prefix), but the same shape
        # filter learn() uses still applies for defence in depth.
        if not self._is_plausible_word(word):
            return
        self._candidate_counts[word] += 1
        self._candidate_last_seen[word] = time.time()
        if self._candidate_counts[word] >= self._candidate_threshold:
            count = self._candidate_counts.pop(word)
            self._candidate_last_seen.pop(word, None)
            weight = count * self._PILL_CLICK_WEIGHT
            self.unigrams[word] += weight
            self.user_vocab[word] += weight
            self._user_total += weight
            self.total_words += weight

    def reinforce_context(self, context: str, selected_word: str) -> None:
        """Add +1 to the trailing edges into ``selected_word``.

        Strengthens the (prev_word, selected_word) bigram and the
        (prev2, prev1, selected_word) trigram, where prev1/prev2 are the
        last 1/2 tokens of ``context``. Earlier bigrams in the context
        are deliberately NOT touched — they were already counted when
        the user typed those words, and re-incrementing them on every
        prediction click would inflate them in proportion to how many
        predictions the user picks per sentence.

        Used by :meth:`HybridPredictor.learn_from_selection` so picking a
        prediction strengthens the context→word edge that was just
        validated, without polluting the rest of the running buffer.
        """
        if not selected_word:
            return
        sel = selected_word.lower().strip()
        if not sel:
            return
        prev_tokens = self._tokenize(context)
        if not prev_tokens:
            return
        prev_word = prev_tokens[-1]
        self._bump_user_context(self._user_bigrams, self.bigrams, prev_word, sel)
        if len(prev_tokens) >= 2:
            prev2 = prev_tokens[-2]
            self._bump_user_context(self._user_trigrams, self.trigrams, f"{prev2} {prev_word}", sel)

    def unlearn_word(self, word: str) -> bool:
        """Reverse one sighting of a word — backspace-as-negative-signal.

        When the user backspaces past a space and starts editing a word
        whose ``learn()`` call already fired, retract that sighting so a
        typo typed once and immediately corrected doesn't accumulate
        toward the candidate-gate threshold.

        Decrement priority:
          - ``_candidate_counts[word]`` if present (most common — typo
            never made it into ``user_vocab`` yet);
          - else ``user_vocab[word]`` together with ``unigrams[word]``,
            ``_user_total`` and ``total_words``, if the word was already
            promoted.

        Bigrams/trigrams are intentionally left alone. One backspace
        shouldn't crater multi-word context history, and the magnitude
        of context pollution from a single retracted sighting is small
        compared to the user_vocab pollution this guard prevents.

        Returns True if anything was decremented, False if the word was
        unknown to the user-side tables (e.g. base-dict word with no
        user-typed signal yet).
        """
        word = word.lower().strip()
        if not word:
            return False
        if word in self._candidate_counts:
            self._candidate_counts[word] -= 1
            if self._candidate_counts[word] <= 0:
                del self._candidate_counts[word]
            return True
        if word in self.user_vocab:
            self.user_vocab[word] -= 1
            self._user_total = max(0, self._user_total - 1)
            self.unigrams[word] = max(0, self.unigrams.get(word, 0) - 1)
            self.total_words = max(0, self.total_words - 1)
            if self.user_vocab[word] <= 0:
                del self.user_vocab[word]
            if self.unigrams.get(word, 0) <= 0:
                self.unigrams.pop(word, None)
            return True
        return False

    def save(self, path: Path) -> None:
        """Save model to JSON file."""
        data = {
            "unigrams": dict(self.unigrams),
            # Only the user's half of the context tables is written.  The
            # base half is re-read from the data files on every launch,
            # and persisting the merged view is what made the seeds grow
            # by 50 per launch (a live model was found at 1,037 for a
            # pair shipped at 50, and 3,848 for a trigram shipped at 53).
            "user_bigrams": self._user_context_to_json(self._user_bigrams),
            "user_trigrams": self._user_context_to_json(self._user_trigrams),
            "user_vocab": dict(self.user_vocab),
            "total_words": self.total_words,
            "blacklist": sorted(self.blacklist),
            "dispreference": dict(self.dispreference),
            "preferred": dict(self.preferred),
            "blacklist_type_count": dict(self._blacklist_type_count),
            "capitalization": dict(self.capitalization),
            "tokens": self.tokens.to_dict(),
            "candidate_counts": dict(self._candidate_counts),
            "candidate_last_seen": dict(self._candidate_last_seen),
        }
        atomic_write_json(path, data)
        _logger.info("Model saved to %s", path)

    # Defensive bounds on saved-model shape.  A legitimate model grown
    # through normal typing stays well under these limits; values beyond
    # them suggest a corrupt or crafted file and are refused rather than
    # risking OOM at startup.
    _MAX_MODEL_FILE_BYTES = 50 * 1024 * 1024  # 50 MB on disk
    _MAX_UNIGRAMS = 500_000
    _MAX_BIGRAMS_PREFIXES = 500_000
    _MAX_CAPITALIZATIONS = 100_000

    @staticmethod
    def _user_context_to_json(table: Dict[str, Dict[str, float]]) -> Dict[str, Dict[str, float]]:
        return {
            prefix: {word: round(count, 3) for word, count in row.items() if count > 0}
            for prefix, row in table.items()
            if row
        }

    def _adopt_user_context(
        self, raw: object
    ) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, int]]]:
        """Build (user, merged) context tables from a persisted mapping.

        Accepts both the current ``user_*`` form and a legacy merged
        table, which is adopted wholesale as user history: its counts
        cannot be separated after the fact, treating them as the user's
        keeps every ranking exactly as it was, and recency decay retires
        the inherited seed mass over the following weeks while the base
        share underneath is re-seeded cleanly from the data files.
        Malformed entries are skipped one at a time rather than failing
        the file, the same rule the token store applies.
        """
        user: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        merged: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        if not isinstance(raw, dict):
            return user, merged
        for prefix, row in raw.items():
            if not isinstance(prefix, str) or not isinstance(row, dict):
                continue
            for word, value in row.items():
                if not isinstance(word, str) or isinstance(value, bool):
                    continue
                try:
                    count = float(value)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(count) or count <= 0.0:
                    continue
                user[prefix][word] = count
                rounded = self._ri(count)
                if rounded > 0:
                    merged[prefix][word] = rounded
        return user, merged

    def load(self, path: Path) -> None:
        """Load model from JSON file."""
        try:
            file_size = path.stat().st_size
            if file_size > self._MAX_MODEL_FILE_BYTES:
                _logger.warning(
                    "Model file %s too large (%d bytes > %d cap); skipping load.",
                    path,
                    file_size,
                    self._MAX_MODEL_FILE_BYTES,
                )
                return

            with open(path) as f:
                data = json.load(f)

            unigrams = data.get("unigrams", {})
            if len(unigrams) > self._MAX_UNIGRAMS:
                _logger.warning(
                    "Model file %s has %d unigrams (> %d); skipping load.",
                    path,
                    len(unigrams),
                    self._MAX_UNIGRAMS,
                )
                return
            legacy_context = "user_bigrams" not in data and "bigrams" in data
            bigrams = data.get("user_bigrams", data.get("bigrams", {}))
            trigrams = data.get("user_trigrams", data.get("trigrams", {}))
            for label, table in (("bigram", bigrams), ("trigram", trigrams)):
                if len(table) > self._MAX_BIGRAMS_PREFIXES:
                    _logger.warning(
                        "Model file %s has %d %s prefixes (> %d); skipping load.",
                        path,
                        len(table),
                        label,
                        self._MAX_BIGRAMS_PREFIXES,
                    )
                    return
            caps = data.get("capitalization", {})
            if len(caps) > self._MAX_CAPITALIZATIONS:
                _logger.warning(
                    "Model file %s has %d capitalizations (> %d); skipping load.",
                    path,
                    len(caps),
                    self._MAX_CAPITALIZATIONS,
                )
                return

            # Strip fragments from older saved models. The dictionary
            # loaders apply this filter at startup so fresh installs
            # are clean, but a long-running user's model.json was
            # saved before the filter existed and still contains every
            # letter of the alphabet plus ~370 two-letter abbreviations
            # at high frequencies. Drop them on load and the next save
            # writes the cleaned model back.
            unigrams = {w: c for w, c in unigrams.items() if self._is_plausible_word(w)}
            user_vocab_raw = data.get("user_vocab", {})
            user_vocab_clean = {
                w: c for w, c in user_vocab_raw.items() if self._is_plausible_word(w)
            }

            self.unigrams = defaultdict(int, unigrams)
            self._user_bigrams, self.bigrams = self._adopt_user_context(bigrams)
            self._user_trigrams, self.trigrams = self._adopt_user_context(trigrams)
            if legacy_context:
                _logger.info(
                    "Adopted a pre-split context table (%d bigram, %d trigram prefixes) "
                    "as user history; base seeds are re-applied from the data files.",
                    len(self.bigrams),
                    len(self.trigrams),
                )
            self.user_vocab = defaultdict(int, user_vocab_clean)
            # Rebuild incremental running total from loaded counts.
            self._user_total = sum(self.user_vocab.values())
            self.total_words = data.get("total_words", 0)
            self.blacklist = set(data.get("blacklist", []))
            self.dispreference = defaultdict(int, data.get("dispreference", {}))
            self.preferred = defaultdict(int, data.get("preferred", {}))
            self._blacklist_type_count = defaultdict(int, data.get("blacklist_type_count", {}))
            self._candidate_counts = defaultdict(int, data.get("candidate_counts", {}))
            # Coerce loaded timestamps to float; older saves don't have
            # this key, in which case the sweep backfills the field on
            # first run rather than instantly expiring legacy entries.
            raw_last_seen = data.get("candidate_last_seen", {}) or {}
            self._candidate_last_seen = {
                str(w): float(t) for w, t in raw_last_seen.items() if w in self._candidate_counts
            }
            # Merge saved capitalization with built-in proper nouns (user overrides win)
            self.capitalization.update(caps)
            # Absent from every model saved before the token store existed,
            # which from_dict reads as an empty store rather than an error.
            self.tokens.from_dict(data.get("tokens", {}))
            _logger.info(
                "Model loaded from %s (%d blacklisted, %d capitalizations)",
                path,
                len(self.blacklist),
                len(self.capitalization),
            )
        except Exception as e:
            _logger.warning("Failed to load model from %s: %s", path, e)

    def load_corpus(self, text: str) -> None:
        """Load a large corpus for initial training."""
        _logger.info("Loading corpus (%d chars)...", len(text))
        self.learn(text, corpus=True)
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
            dict_path = self.profile.dictionary

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
            trigrams_path = Path(__file__).parent.parent.parent / "data" / "common_trigrams.txt"

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
        self._user_bigrams.clear()
        self._user_trigrams.clear()
        self._base_unigrams.clear()
        self._base_total = 0
        self._user_total = 0
        self.total_words = 0
        self.blacklist.clear()
        self.dispreference.clear()
        self.preferred.clear()
        self._blacklist_type_count.clear()
        self._candidate_counts.clear()
        self._candidate_last_seen.clear()
        self._learn_count = 0
        # Clear learned capitalization so user-typed forms don't persist
        self.capitalization.clear()
        # Learned phone numbers / addresses / emails are user data too,
        # and "clear my learned data" has to mean all of it.
        self.tokens.clear()

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
            "user_bigrams": sum(len(v) for v in self._user_bigrams.values()),
            "user_trigrams": sum(len(v) for v in self._user_trigrams.values()),
            "user_words": len(self.user_vocab),
        }
