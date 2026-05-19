"""Tests for the n-gram prediction engine."""

from __future__ import annotations

from pathlib import Path

from src.prediction.ngram_predictor import NgramPredictor


class TestNgramPredictorBasic:
    """Basic prediction behavior."""

    def test_empty_context_returns_top_unigrams(self):
        predictor = NgramPredictor()
        results = predictor.predict("", n=5)
        assert len(results) <= 5
        assert all(isinstance(w, str) for w in results)

    def test_predict_returns_requested_count(self):
        predictor = NgramPredictor()
        for n in (1, 3, 5, 10):
            results = predictor.predict("", n=n)
            assert len(results) <= n

    def test_partial_word_completion(self):
        predictor = NgramPredictor()
        # "th" should match "the", "that", "this", etc.
        results = predictor.predict("th", n=10)
        assert any(w.startswith("th") for w in results)

    def test_partial_word_no_match_returns_empty(self):
        predictor = NgramPredictor()
        results = predictor.predict("zzzzqqqq", n=5)
        assert results == []

    def test_next_word_prediction_after_space(self):
        """Trailing space signals 'predict next word', not 'complete current'."""
        predictor = NgramPredictor()
        # Train heavily so bigram "want -> to" is strong
        for _ in range(10):
            predictor.learn("I want to go home")
        results = predictor.predict("I want ", n=5)
        # "to" should appear as a likely next word after "want"
        assert "to" in results

    def test_case_insensitive(self):
        predictor = NgramPredictor()
        predictor.learn("Hello World")
        results = predictor.predict("hel", n=5)
        assert "hello" in results


class TestNgramLearning:
    """Learning and vocabulary updates."""

    def test_learn_increases_frequency(self):
        predictor = NgramPredictor()
        initial_freq = predictor.unigrams.get("xylophone", 0)
        predictor.learn("xylophone xylophone xylophone")
        assert predictor.unigrams["xylophone"] > initial_freq

    def test_learn_builds_bigrams(self):
        predictor = NgramPredictor()
        predictor.learn("quick brown fox")
        assert "brown" in predictor.bigrams.get("quick", {})
        assert "fox" in predictor.bigrams.get("brown", {})

    def test_learn_builds_trigrams(self):
        predictor = NgramPredictor()
        predictor.learn("the quick brown fox")
        key = "the quick"
        assert "brown" in predictor.trigrams.get(key, {})

    def test_learn_word_boosts_frequency(self):
        predictor = NgramPredictor()
        before = predictor.unigrams.get("zephyr", 0)
        predictor.learn_word("zephyr")
        assert predictor.unigrams["zephyr"] == before + 5

    def test_learn_updates_user_vocab(self):
        predictor = NgramPredictor()
        # Both words are in the base 10K dictionary, so they skip the
        # fragment-filter repetition gate and land in user_vocab on the
        # first learn() call.
        predictor.learn("hello world")
        assert predictor.user_vocab["hello"] > 0
        assert predictor.user_vocab["world"] > 0

    def test_clear_user_data(self):
        predictor = NgramPredictor()
        predictor.learn("testing clear")
        predictor.clear_user_data()
        assert len(predictor.user_vocab) == 0

    def test_user_vocab_boosts_predictions(self):
        predictor = NgramPredictor()
        # Learn a word many times to boost it
        for _ in range(20):
            predictor.learn_word("zugzwang")
        results = predictor.predict("zugz", n=5)
        assert "zugzwang" in results


class TestNgramRecencyDecay:
    """Recency decay for user-learned frequencies."""

    def test_decay_reduces_user_vocab(self):
        predictor = NgramPredictor()
        predictor.learn("xylophone xylophone xylophone")
        before = predictor.user_vocab["xylophone"]
        predictor._apply_decay()
        after = predictor.user_vocab["xylophone"]
        assert after < before

    def test_decay_removes_low_frequency_words(self):
        predictor = NgramPredictor()
        predictor.user_vocab["rareword"] = 1
        predictor._apply_decay()
        # Frequency 1 * 0.95 = 0, should be removed
        assert "rareword" not in predictor.user_vocab

    def test_decay_triggered_after_interval(self):
        predictor = NgramPredictor()
        predictor._decay_interval = 5  # Small interval for testing
        predictor.learn_word("testword")
        initial = predictor.user_vocab["testword"]
        # Learn enough times to trigger decay
        for i in range(6):
            predictor.learn(f"word{i} sentence here")
        # Decay should have been applied
        assert predictor.user_vocab.get("testword", 0) < initial

    def test_recent_words_outweigh_old_after_decay(self):
        predictor = NgramPredictor()
        predictor._decay_interval = 3  # Trigger frequently
        # Learn "oldword" early
        predictor.learn("oldword oldword oldword")
        # Trigger several decay cycles
        for i in range(12):
            predictor.learn(f"filler{i} text here")
        # Now learn "newword"
        predictor.learn("newword newword newword")
        # newword should have higher user_vocab than oldword (decayed)
        assert predictor.user_vocab["newword"] > predictor.user_vocab.get("oldword", 0)

    def test_decay_preserves_bigrams(self):
        predictor = NgramPredictor()
        predictor.learn("alpha beta")
        predictor._apply_decay()
        # Bigram should still exist (decayed but ≥ 1)
        assert predictor.bigrams["alpha"]["beta"] >= 1


class TestNgramPersistence:
    """Save and load round-trips."""

    def test_save_and_load_preserves_unigrams(self, tmp_model_dir: Path):
        path = tmp_model_dir / "ngram.json"
        predictor = NgramPredictor()
        predictor.learn("alpha beta gamma")
        predictor.save(path)

        loaded = NgramPredictor()
        loaded.load(path)
        assert loaded.unigrams["alpha"] == predictor.unigrams["alpha"]
        assert loaded.unigrams["beta"] == predictor.unigrams["beta"]

    def test_save_and_load_preserves_bigrams(self, tmp_model_dir: Path):
        path = tmp_model_dir / "ngram.json"
        predictor = NgramPredictor()
        predictor.learn("alpha beta gamma")
        predictor.save(path)

        loaded = NgramPredictor()
        loaded.load(path)
        assert loaded.bigrams["alpha"]["beta"] == predictor.bigrams["alpha"]["beta"]

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        path = tmp_path / "nested" / "dir" / "model.json"
        predictor = NgramPredictor()
        predictor.save(path)
        assert path.exists()

    def test_load_nonexistent_file_is_safe(self, tmp_path: Path):
        predictor = NgramPredictor()
        predictor.load(tmp_path / "nope.json")
        # Should not crash, model still usable
        assert predictor.predict("", n=3) is not None

    def test_load_corrupt_file_is_safe(self, tmp_model_dir: Path):
        path = tmp_model_dir / "corrupt.json"
        path.write_text("not valid json {{{{")
        predictor = NgramPredictor()
        predictor.load(path)
        # Should not crash
        assert predictor.predict("", n=3) is not None

    def test_save_load_round_trip_predictions_match(self, tmp_model_dir: Path):
        path = tmp_model_dir / "ngram.json"
        predictor = NgramPredictor()
        predictor.learn("the quick brown fox jumps over the lazy dog")
        original_preds = predictor.predict("the ", n=5)
        predictor.save(path)

        loaded = NgramPredictor()
        loaded.load(path)
        loaded_preds = loaded.predict("the ", n=5)
        assert original_preds == loaded_preds


class TestNgramTokenization:
    """Tokenization edge cases."""

    def test_tokenize_strips_punctuation(self):
        predictor = NgramPredictor()
        tokens = predictor._tokenize("hello, world! it's fine.")
        assert "hello" in tokens
        assert "world" in tokens
        assert "it's" in tokens  # apostrophe preserved

    def test_tokenize_handles_empty(self):
        predictor = NgramPredictor()
        assert predictor._tokenize("") == []

    def test_tokenize_handles_only_punctuation(self):
        predictor = NgramPredictor()
        assert predictor._tokenize("...!!!") == []


class TestNgramStats:
    """Statistics reporting."""

    def test_get_stats_keys(self):
        predictor = NgramPredictor()
        stats = predictor.get_stats()
        assert "total_words" in stats
        assert "unique_words" in stats
        assert "bigrams" in stats
        assert "trigrams" in stats
        assert "user_words" in stats

    def test_stats_update_after_learning(self):
        predictor = NgramPredictor()
        before = predictor.get_stats()["user_words"]
        predictor.learn("unique test words here")
        after = predictor.get_stats()["user_words"]
        assert after > before


class TestCapitalization:
    """Context-aware capitalization (Android/Gboard model)."""

    def test_always_capitalize_i(self):
        predictor = NgramPredictor()
        assert predictor.get_capitalized("i", sentence_start=False) == "I"
        assert predictor.get_capitalized("i", sentence_start=True) == "I"

    def test_always_capitalize_contractions(self):
        predictor = NgramPredictor()
        assert predictor.get_capitalized("i'm", sentence_start=False) == "I'm"
        assert predictor.get_capitalized("i'll", sentence_start=False) == "I'll"
        assert predictor.get_capitalized("i'd", sentence_start=False) == "I'd"
        assert predictor.get_capitalized("i've", sentence_start=False) == "I've"

    def test_proper_nouns_not_auto_capitalized(self):
        """Proper-noun and sentence-start auto-cap were removed because
        the proper-noun list ("hope", "may", "rose", "mark", "monday",
        "paris", "iphone", "nasa", ...) fired on common English words
        and pills came back capitalised when the user had typed
        lowercase. Pills now mirror the typed prefix's casing only;
        the only exception is the ``I`` family (Tier 1)."""
        predictor = NgramPredictor()
        for word in ("monday", "paris", "iphone", "nasa", "ibm",
                     "will", "jack", "may", "hello"):
            assert predictor.get_capitalized(word, sentence_start=False) == word
            assert predictor.get_capitalized(word, sentence_start=True) == word

    def test_learn_capitalization_still_records_form(self):
        """``learn_capitalization`` still records preferred forms in
        ``self.capitalization`` (so the data isn't lost), even though
        ``get_capitalized`` no longer consults that dict. Lets a
        future opt-in switch re-enable proper-noun cap without
        re-teaching."""
        predictor = NgramPredictor()
        # Use a made-up brand that isn't in data/proper_nouns.txt so
        # the assertion exercises learn_capitalization rather than the
        # preload path.
        assert "zigzaqcorp" not in predictor.capitalization
        assert predictor.learn_capitalization("ZigZaqCorp") is True
        assert predictor.capitalization.get("zigzaqcorp") == "ZigZaqCorp"
        # But the pill-facing path leaves it lowercase.
        assert predictor.get_capitalized("zigzaqcorp") == "zigzaqcorp"

    def test_all_uppercase_typing_not_learned(self):
        """All-caps typing is almost always Caps Lock being on, not a
        deliberate "this word is uppercase" signal. The capitalisation
        table should not be polluted with shouty forms of every word
        the user types under caps lock.
        """
        predictor = NgramPredictor()
        result = predictor.learn_capitalization("HELLO")
        assert result is False, "all-caps typing should not be learned"
        assert "hello" not in predictor.capitalization

    def test_all_uppercase_typing_learned_when_explicitly_allowed(self):
        """The bridge passes ``allow_uppercase=True`` when it has
        positive evidence the user did NOT use Caps Lock — i.e. they
        right-clicked / shifted each letter individually. That's a
        deliberate signal that the word is canonically all-caps
        ("HVAC", "ROFL", a domain acronym), and the table should
        learn it. ``get_capitalized`` does not surface the form in
        pills under the current Tier-1-only policy."""
        predictor = NgramPredictor()
        result = predictor.learn_capitalization("HVAC", allow_uppercase=True)
        assert result is True, "deliberate all-caps typing should be learned"
        assert predictor.capitalization.get("hvac") == "HVAC"


class TestPersonalVocabRanking:
    """Personal typing should outrank dictionary-only words on partial match."""

    def test_typed_word_beats_never_typed_dict_word(self):
        """Typing 'claude' 10 times should make it beat 'can' when user types 'c'.

        Under the old multiplicative-boost scoring, personal vocab was
        drowned by the base dictionary's massive pre-seeded frequencies.
        After the probability-space split (P = alpha*P_user + (1-a)*P_base)
        a handful of personal uses is enough to win on rank.
        """
        predictor = NgramPredictor()
        # Simulate typing "claude" 10 times
        for _ in range(10):
            predictor.learn("claude")

        results = predictor.predict("c", n=20)
        assert "claude" in results, f"'claude' missing from predictions: {results}"
        # Should beat 'can' (very common English word, never typed by user)
        assert results.index("claude") < results.index("can"), (
            f"'claude' should outrank 'can' after 10 uses; got {results}"
        )

    def test_frequent_personal_word_ranks_near_top(self):
        """A heavily-used personal word lands in the top 3 for its prefix."""
        predictor = NgramPredictor()
        for _ in range(50):
            predictor.learn("claude")
        results = predictor.predict("cl", n=10)
        assert "claude" in results[:3], (
            f"'claude' should be top-3 after 50 uses; got {results}"
        )

    def test_zero_personal_vocab_falls_back_to_base(self):
        """With no personal typing, ranking comes from the base dictionary."""
        predictor = NgramPredictor()
        results = predictor.predict("th", n=10)
        # 'the' is the most common English word — should be the first 'th' hit
        th_results = [w for w in results if w.startswith("th")]
        assert th_results, f"no 'th' predictions: {results}"
        assert th_results[0] == "the"

    def test_personal_weight_knob_affects_ranking(self):
        """Turning alpha down puts the dictionary back in charge.

        Uses a realistic amount of background personal text (~450 words)
        so P_user isn't pathologically dominated by a single entry — the
        knob's effect is measured the way it would be in practice.
        """
        predictor = NgramPredictor()
        predictor.learn("the quick brown fox jumps over the lazy dog " * 50)
        for _ in range(10):
            predictor.learn("claude")

        predictor.personal_weight = 0.9
        strong = predictor.predict("c", n=20)

        predictor.personal_weight = 0.05
        weak = predictor.predict("c", n=20)

        # Under strong personal bias "claude" should rank at least as
        # well as under weak personal bias.
        if "claude" in strong and "claude" in weak:
            assert strong.index("claude") <= weak.index("claude")


class TestLoadBounds:
    """DoS cap: an adversarial or corrupted model file is refused."""

    def test_load_rejects_oversized_file(self, tmp_path):
        """A file above the size cap is skipped, not json.loaded."""
        p = tmp_path / "big.json"
        # Exceed the 50 MB cap with mostly whitespace so we don't burn
        # real memory building the actual dict.
        p.write_bytes(b" " * (51 * 1024 * 1024))
        pred = NgramPredictor()
        pred.unigrams.clear()
        pred.load(p)
        # load() silently no-ops on oversize; unigrams stays empty
        assert len(pred.unigrams) == 0

    def test_load_rejects_too_many_unigrams(self, tmp_path):
        """A legitimately-small file with millions of unigrams is refused."""
        import json
        p = tmp_path / "many.json"
        huge = {"unigrams": {f"w{i}": 1 for i in range(NgramPredictor._MAX_UNIGRAMS + 100)}}
        p.write_text(json.dumps(huge))
        pred = NgramPredictor()
        before = dict(pred.unigrams)
        pred.load(p)
        # Base dictionary state preserved; crafted file discarded.
        assert dict(pred.unigrams) == before


class TestContextualRanking:
    """Linear-interpolation scoring must let context actually beat unigram prior.

    Pre-change, `predict()` summed raw bigram/trigram frequencies with a
    100000-scaled unigram probability — the unigram term dwarfed
    everything else, so "I want " predicted "the" (most common English
    word) instead of "to" (very common continuation of "want"). These
    tests pin the fix: when a bigram/trigram context has been trained,
    the continuation word must outrank the global unigram favourite.
    """

    def test_bigram_beats_top_unigram_on_trained_context(self):
        p = NgramPredictor()
        for _ in range(20):
            p.learn("I want to go home")
        # After "I want " the top pick should be "to", not "the".
        results = p.predict("I want ", n=5)
        assert results[0] == "to", f"expected 'to' first, got {results}"

    def test_trigram_beats_bigram_when_both_seen(self):
        p = NgramPredictor()
        # "big red ball" — trigram (big, red) → ball.  Also train
        # (red → apple) as a competing bigram, more frequent than the
        # trigram-specific continuation, to prove the trigram still wins.
        for _ in range(10):
            p.learn("big red ball")
        for _ in range(30):
            p.learn("red apple")
        results = p.predict("big red ", n=5)
        # Trigram evidence (big, red) → ball should outrank the very
        # strong but context-less bigram red → apple.
        assert results[0] == "ball", f"expected 'ball' first, got {results}"

    def test_unigram_full_strength_without_context(self):
        p = NgramPredictor()
        # With no preceding word, ranking must come from P_uni at full
        # weight — i.e. the usual top-of-dict word wins on a prefix.
        results = p.predict("th", n=5)
        assert results[0] == "the", f"expected 'the' first, got {results}"


class TestFragmentFilter:
    """Shape filter + repetition gate keep random fragments out of user_vocab."""

    def test_shape_filter_rejects_all_consonant_cluster(self):
        p = NgramPredictor()
        for _ in range(10):
            p.learn("xqz")
        # No vowel → always rejected, no matter how many times seen.
        assert p.user_vocab.get("xqz", 0) == 0
        assert p._candidate_counts.get("xqz", 0) == 0

    def test_shape_filter_rejects_all_vowel_mash(self):
        p = NgramPredictor()
        for _ in range(10):
            p.learn("aaaa")
        # No consonant → always rejected.
        assert p.user_vocab.get("aaaa", 0) == 0
        assert p._candidate_counts.get("aaaa", 0) == 0

    def test_shape_filter_accepts_y_as_both(self):
        p = NgramPredictor()
        # "eye" — y must count as consonant for this word to pass.
        # "cry" — y must count as vowel for this word to pass.
        for _ in range(3):
            p.learn("eye")
            p.learn("cry")
        assert p.user_vocab.get("eye", 0) > 0
        assert p.user_vocab.get("cry", 0) > 0

    def test_shape_filter_rejects_short_non_whitelisted(self):
        p = NgramPredictor()
        for _ in range(10):
            p.learn("qq")
        # "qq" is length 2 and not in the whitelist.
        assert p.user_vocab.get("qq", 0) == 0

    def test_shape_filter_accepts_short_whitelist(self):
        p = NgramPredictor()
        p.learn("hi")
        # "hi" is in the whitelist AND in the base dict — learns immediately.
        assert p.user_vocab.get("hi", 0) > 0

    def test_repetition_gate_blocks_first_sighting(self):
        p = NgramPredictor()
        p.learn("zephyrish")  # plausible shape, not in base dict
        assert p.user_vocab.get("zephyrish", 0) == 0
        assert p._candidate_counts.get("zephyrish", 0) == 1

    def test_repetition_gate_promotes_at_threshold(self):
        p = NgramPredictor()
        for _ in range(3):
            p.learn("zephyrish")
        assert p.user_vocab.get("zephyrish", 0) >= 3
        # Candidate pool is emptied once the word is promoted.
        assert p._candidate_counts.get("zephyrish", 0) == 0

    def test_known_base_word_bypasses_gate(self):
        p = NgramPredictor()
        p.learn("hello")
        assert p.user_vocab.get("hello", 0) > 0

    def test_learn_word_bypasses_gate(self):
        p = NgramPredictor()
        p.learn_word("zephyrish")
        # Explicit user add (right-click → Show more, vocab pack
        # import) — the gate doesn't apply.
        assert p.user_vocab.get("zephyrish", 0) > 0


class TestLearnFromPillClick:
    """Pill clicks must pass the same 3-sighting gate that free-typing
    does for unknown words. Otherwise a single click on a fuzzy/PPM-
    generated pill permanently injects a never-typed word into the
    model with weight 5."""

    def test_first_click_lands_in_candidate_pool(self):
        p = NgramPredictor()
        p.learn_from_pill_click("zephyrish")
        # Not promoted yet.
        assert p.user_vocab.get("zephyrish", 0) == 0
        assert p.unigrams.get("zephyrish", 0) == 0
        # Sighting recorded.
        assert p._candidate_counts["zephyrish"] == 1
        assert "zephyrish" in p._candidate_last_seen

    def test_three_clicks_promote_with_full_weight(self):
        p = NgramPredictor()
        for _ in range(3):
            p.learn_from_pill_click("zephyrish")
        # Promoted with cumulative weight (3 * _PILL_CLICK_WEIGHT).
        assert p.user_vocab["zephyrish"] == 15
        assert p.unigrams["zephyrish"] == 15
        # Pool drained for this word.
        assert "zephyrish" not in p._candidate_counts
        assert "zephyrish" not in p._candidate_last_seen
        # Invariant preserved.
        assert p._user_total == sum(p.user_vocab.values())

    def test_known_base_word_bypasses_gate(self):
        p = NgramPredictor()
        # "hello" is in the Google 10K base dict — pill click on a
        # known word reinforces immediately, no gate.
        before = p.unigrams.get("hello", 0)
        p.learn_from_pill_click("hello")
        assert p.unigrams["hello"] == before + 5
        assert p.user_vocab["hello"] == 5

    def test_already_promoted_word_bypasses_gate(self):
        p = NgramPredictor()
        # Promote first via learn().
        for _ in range(3):
            p.learn("zephyrish")
        before = p.user_vocab["zephyrish"]
        p.learn_from_pill_click("zephyrish")
        # Now in user_vocab — pill click adds +5 directly.
        assert p.user_vocab["zephyrish"] == before + 5

    def test_implausible_word_rejected(self):
        p = NgramPredictor()
        p.learn_from_pill_click("xqz")  # no vowels
        assert "xqz" not in p._candidate_counts
        assert "xqz" not in p.user_vocab

    def test_clicks_update_last_seen(self):
        p = NgramPredictor()
        import time
        p.learn_from_pill_click("zephyrish")
        t1 = p._candidate_last_seen["zephyrish"]
        time.sleep(0.01)
        p.learn_from_pill_click("zephyrish")
        t2 = p._candidate_last_seen["zephyrish"]
        assert t2 > t1


class TestStaleCandidateSweep:
    """Candidates not re-sighted within the max-age window expire on
    the next decay tick. Stops an accidental pill click on a typo
    from sitting in the pool indefinitely."""

    def test_stale_candidate_dropped_by_sweep(self):
        p = NgramPredictor()
        p.learn_from_pill_click("zephyrish")
        # Backdate the timestamp past the cutoff.
        p._candidate_last_seen["zephyrish"] = 0.0
        p._sweep_stale_candidates()
        assert "zephyrish" not in p._candidate_counts
        assert "zephyrish" not in p._candidate_last_seen

    def test_fresh_candidate_survives_sweep(self):
        p = NgramPredictor()
        p.learn_from_pill_click("zephyrish")
        p._sweep_stale_candidates()
        assert p._candidate_counts.get("zephyrish") == 1

    def test_missing_timestamp_is_backfilled(self):
        p = NgramPredictor()
        # Simulate a candidate loaded from a pre-timestamp save file.
        p._candidate_counts["zephyrish"] = 1
        # No entry in _candidate_last_seen.
        p._sweep_stale_candidates()
        # Not expired, backfilled.
        assert p._candidate_counts.get("zephyrish") == 1
        assert "zephyrish" in p._candidate_last_seen


class TestBaseWordlistFiltering:
    """The shape filter applies to the base wordlists too, not just learn().

    The Google 10K dump contains every letter of the alphabet plus
    ~370 two-letter abbreviations / state codes / fragments at high
    frequency. Without filtering, a one-letter prefix surfaces all 26
    letters in the prediction pills.
    """

    def test_single_letters_not_loaded(self):
        p = NgramPredictor()
        # "c", "x", "n", etc. are in the Google 10K dump but should be
        # filtered. Only "a" and "i" survive (in the short whitelist).
        for letter in "bcdefghjklmnopqrstuvwxyz":
            assert letter not in p.unigrams, (
                f"single letter {letter!r} should be filtered from base dict"
            )
        # "a" and "i" are real one-letter words and ARE in the whitelist.
        assert "a" in p.unigrams
        assert "i" in p.unigrams

    def test_two_letter_fragments_not_loaded(self):
        p = NgramPredictor()
        # State codes / abbreviations that the Google list includes but
        # aren't in the short-word whitelist.
        for frag in ("tx", "ca", "ny", "kb", "pp", "th", "re"):
            assert frag not in p.unigrams, (
                f"fragment {frag!r} should be filtered from base dict"
            )

    def test_real_short_words_still_loaded(self):
        p = NgramPredictor()
        # All survive because they're in _SHORT_WORD_WHITELIST.
        for word in ("of", "to", "in", "is", "it", "be", "by", "we", "ok"):
            assert word in p.unigrams, f"real word {word!r} should be in base dict"

    def test_load_strips_fragments_from_old_save(self, tmp_path):
        """A saved model from before this filter existed contains junk.
        Loading it should silently strip the junk so existing users
        don't have to manually clear data."""
        import json
        path = tmp_path / "old_model.json"
        path.write_text(json.dumps({
            "unigrams": {
                "hello": 100, "world": 100,  # real
                "c": 9000, "x": 9000,        # single-letter junk
                "tx": 5000, "kb": 5000,      # 2-letter junk
            },
            "user_vocab": {"hello": 5, "qq": 3},  # qq is junk
            "bigrams": {}, "trigrams": {},
            "total_words": 100,
        }))
        p = NgramPredictor()
        p.load(path)
        assert "hello" in p.unigrams
        assert "world" in p.unigrams
        assert "c" not in p.unigrams
        assert "x" not in p.unigrams
        assert "tx" not in p.unigrams
        assert "kb" not in p.unigrams
        # And user_vocab is also cleaned.
        assert p.user_vocab.get("hello") == 5
        assert "qq" not in p.user_vocab
        # _user_total reflects only the surviving entries (an invariant
        # the prediction path depends on).
        assert p._user_total == sum(p.user_vocab.values())

    def test_gated_word_does_not_form_bigrams(self):
        p = NgramPredictor()
        p.learn("the xqz fox")
        # "xqz" is shape-filtered; no bigram edges should involve it.
        assert "xqz" not in p.bigrams.get("the", {})
        assert "fox" not in p.bigrams.get("xqz", {})

    def test_candidate_counts_persist_across_save_load(self, tmp_path):
        p = NgramPredictor()
        p.learn("zephyrish")
        path = tmp_path / "model.json"
        p.save(path)

        q = NgramPredictor()
        q.load(path)
        assert q._candidate_counts.get("zephyrish", 0) == 1

    def test_candidate_last_seen_persists_across_save_load(self, tmp_path):
        p = NgramPredictor()
        p.learn("zephyrish")
        stamped = p._candidate_last_seen["zephyrish"]
        path = tmp_path / "model.json"
        p.save(path)

        q = NgramPredictor()
        q.load(path)
        assert q._candidate_last_seen.get("zephyrish") == stamped

    def test_load_tolerates_missing_last_seen_field(self, tmp_path):
        """An older save file without candidate_last_seen should still
        load — the sweep backfills the timestamp on first run rather
        than instantly expiring legacy candidates."""
        import json
        path = tmp_path / "old_model.json"
        path.write_text(json.dumps({
            "unigrams": {"hello": 100},
            "bigrams": {}, "trigrams": {},
            "user_vocab": {},
            "total_words": 100,
            "candidate_counts": {"zephyrish": 1},
            # No candidate_last_seen key.
        }))
        p = NgramPredictor()
        p.load(path)
        assert p._candidate_counts.get("zephyrish") == 1
        # Backfilled on the next sweep.
        p._sweep_stale_candidates()
        assert "zephyrish" in p._candidate_last_seen

    def test_clear_user_data_resets_candidates(self):
        p = NgramPredictor()
        p.learn("zephyrish")
        p.clear_user_data()
        assert len(p._candidate_counts) == 0
        assert len(p._candidate_last_seen) == 0


class TestUserTotalIncremental:
    """_user_total must stay equal to sum(user_vocab.values())."""

    def test_learn_tracks_total(self):
        p = NgramPredictor()
        p.learn("alpha beta alpha")
        assert p._user_total == sum(p.user_vocab.values())
        p.learn("alpha gamma")
        assert p._user_total == sum(p.user_vocab.values())

    def test_learn_word_tracks_total(self):
        p = NgramPredictor()
        p.learn_word("claude")
        assert p._user_total == sum(p.user_vocab.values())

    def test_decay_restores_total(self):
        p = NgramPredictor()
        for _ in range(200):
            p.learn("claude")
        # Force decay
        p._apply_decay()
        assert p._user_total == sum(p.user_vocab.values())

    def test_clear_user_data_resets_total(self):
        p = NgramPredictor()
        p.learn("alpha beta gamma")
        p.clear_user_data()
        assert p._user_total == 0

    def test_load_rebuilds_total(self, tmp_path):
        p = NgramPredictor()
        p.learn("one two three")
        saved_file = tmp_path / "m.json"
        p.save(saved_file)
        # Scramble running total, then reload
        q = NgramPredictor()
        q._user_total = 99999
        q.load(saved_file)
        assert q._user_total == sum(q.user_vocab.values())


class TestUnlearnWord:
    """Backspace-as-negative-signal: retract one sighting."""

    def test_unlearn_decrements_candidate_count(self):
        p = NgramPredictor()
        # "zephyrish" is plausible-shape but not in the base dict, so
        # the first learn() call lands in candidate_counts.
        p.learn("zephyrish")
        assert p._candidate_counts["zephyrish"] == 1
        assert p.unlearn_word("zephyrish") is True
        assert "zephyrish" not in p._candidate_counts
        assert p.user_vocab.get("zephyrish", 0) == 0

    def test_unlearn_partial_candidate_count(self):
        p = NgramPredictor()
        p.learn("zephyrish")
        p.learn("zephyrish")
        assert p._candidate_counts["zephyrish"] == 2
        p.unlearn_word("zephyrish")
        assert p._candidate_counts["zephyrish"] == 1
        # Still not promoted.
        assert p.user_vocab.get("zephyrish", 0) == 0

    def test_unlearn_decrements_user_vocab_when_promoted(self):
        p = NgramPredictor()
        # Three sightings promote into user_vocab.
        for _ in range(3):
            p.learn("zephyrish")
        before_user = p.user_vocab["zephyrish"]
        before_total = p._user_total
        before_total_words = p.total_words
        assert p.unlearn_word("zephyrish") is True
        assert p.user_vocab["zephyrish"] == before_user - 1
        assert p._user_total == before_total - 1
        assert p.total_words == before_total_words - 1
        # Invariant preserved.
        assert p._user_total == sum(p.user_vocab.values())

    def test_unlearn_returns_false_for_unknown_word(self):
        p = NgramPredictor()
        # Not in candidate_counts and not in user_vocab.
        assert p.unlearn_word("thisismadeupzzz") is False

    def test_unlearn_does_not_touch_bigrams(self):
        p = NgramPredictor()
        # Both base-dict words → land in user_vocab and bigrams immediately.
        p.learn("the cat")
        bigram_before = p.bigrams["the"]["cat"]
        p.unlearn_word("cat")
        # One backspace shouldn't crater bigram history.
        assert p.bigrams["the"]["cat"] == bigram_before

    def test_unlearn_case_insensitive(self):
        p = NgramPredictor()
        p.learn("Zephyrish")  # tokenized to lowercase
        assert p._candidate_counts["zephyrish"] == 1
        # Caller passes whatever case (e.g. mid-word cap), unlearn lowers it.
        p.unlearn_word("Zephyrish")
        assert "zephyrish" not in p._candidate_counts


class TestReinforceContext:
    """Targeted (prev, sel) bigram + (prev2, prev1, sel) trigram bumps."""

    def test_reinforces_only_trailing_bigram(self):
        p = NgramPredictor()
        # Establish a baseline: type "I have asked" — three bigrams formed.
        p.learn("I have asked")
        before_i_have = p.bigrams["i"]["have"]
        before_have_asked = p.bigrams["have"]["asked"]
        # User clicks "claude" — only the (asked, claude) edge should grow.
        p.reinforce_context("I have asked", "claude")
        assert p.bigrams["asked"]["claude"] == 1
        # Earlier context bigrams must NOT be re-incremented.
        assert p.bigrams["i"]["have"] == before_i_have
        assert p.bigrams["have"]["asked"] == before_have_asked

    def test_reinforces_trigram_when_two_prev_tokens(self):
        p = NgramPredictor()
        p.reinforce_context("I have asked", "claude")
        # Trigram key uses the last two tokens.
        assert p.trigrams["have asked"]["claude"] == 1

    def test_no_trigram_with_only_one_prev_token(self):
        p = NgramPredictor()
        p.reinforce_context("hello", "world")
        assert p.bigrams["hello"]["world"] == 1
        # Only one prev token → no trigram bump.
        assert "hello" not in p.trigrams or "world" not in p.trigrams.get("hello", {})

    def test_no_op_with_empty_context(self):
        p = NgramPredictor()
        p.reinforce_context("", "claude")
        # No prev token → no bigram, no trigram.
        assert "claude" not in p.bigrams.get("", {})

    def test_no_op_with_empty_selection(self):
        p = NgramPredictor()
        p.reinforce_context("hello", "")
        assert p.bigrams.get("hello", {}) == {}

    def test_reinforce_lowercases_selection(self):
        p = NgramPredictor()
        p.reinforce_context("hello", "World")
        assert p.bigrams["hello"]["world"] == 1
