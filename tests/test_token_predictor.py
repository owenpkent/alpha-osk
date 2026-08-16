"""Tests for the structured-token store (numbers, phones, emails).

The store exists because the n-gram model tokenises on ``[a-zA-Z']+``
and so cannot learn a phone number, a zip or an email address.  Two
things are being guarded here and they pull in opposite directions:

* it has to actually complete the strings the user retypes, and
* it must not remember the ones they would be alarmed to see offered.

So every positive case below is paired with the near-miss it has to
reject, following the same discipline as ``test_text_patterns.py``.  The
hostile examples are the ones that bite: an SSN and a phone number are
both "digits with dashes", and a rule that sounds specific ("7-15 digits
with a separator") accepts both.
"""

from __future__ import annotations

from src.prediction.token_predictor import DEFAULT_DOMAINS, TokenPredictor


def _store(*tokens: str) -> TokenPredictor:
    store = TokenPredictor()
    for token in tokens:
        store.learn(token)
    return store


class TestWhatIsLearned:
    """The admission rule, stated through the store's own front door."""

    def test_a_phone_number_is_learned(self):
        assert _store("555-123-4567").tokens == {"555-123-4567": 1}

    def test_a_social_security_number_is_not(self):
        """The paired near-miss: same shape family, catastrophic to keep."""
        assert _store("123-45-6789").tokens == {}

    def test_a_bare_social_security_number_is_not(self):
        """Nine bare digits clears no phone grouping and must not fall through."""
        assert _store("123456789").tokens == {}

    def test_a_card_number_is_not(self):
        assert _store("4111111111111111").tokens == {}

    def test_a_house_number_is_learned(self):
        assert "1247" in _store("1247").tokens

    def test_a_zip_is_learned(self):
        assert "02134" in _store("02134").tokens

    def test_an_email_is_learned(self):
        assert "owen@gmail.com" in _store("owen@gmail.com").tokens

    def test_a_mention_is_not(self):
        """ "@owen" is a handle; the word model can complete a name."""
        assert _store("@owen").tokens == {}

    def test_a_plain_word_is_not(self):
        """Words belong to the n-gram model, which has context."""
        assert _store("hello").tokens == {}

    def test_an_alphanumeric_mix_is_learned(self):
        assert "apt4b" in _store("apt4b").tokens

    def test_repeated_sightings_accumulate(self):
        assert _store("02134", "02134", "02134").tokens["02134"] == 3

    def test_trailing_sentence_punctuation_is_not_a_separate_entry(self):
        store = _store("555-123-4567", "555-123-4567.")
        assert store.tokens == {"555-123-4567": 2}


class TestPrediction:
    def test_a_learned_token_completes_its_prefix(self):
        assert _store("555-123-4567").predict("555") == ["555-123-4567"]

    def test_a_one_character_prefix_offers_nothing(self):
        """Below two characters the prefix matches most of the store."""
        assert _store("555-123-4567").predict("5") == []

    def test_an_exact_match_is_not_offered(self):
        """There would be nothing left to insert."""
        assert _store("02134").predict("02134") == []

    def test_the_most_typed_token_ranks_first(self):
        store = _store("1247", "1250", "1250", "1250")
        assert store.predict("12")[0] == "1250"

    def test_matching_ignores_case_but_the_stored_form_comes_back(self):
        """Case-insensitive lookup, stored casing preserved in the result.

        This is exactly why ``_insert_token_pill`` has to check the case
        before taking its suffix-only path: the pill here reads "Apt4B"
        while "apt" is on screen, so appending the tail alone would
        leave "apt4B", which is neither.
        """
        assert _store("Apt4B").predict("apt") == ["Apt4B"]

    def test_an_unrelated_prefix_offers_nothing(self):
        assert _store("555-123-4567").predict("77") == []


class TestEmailDomains:
    def test_the_common_domains_are_offered_on_a_fresh_install(self):
        assert TokenPredictor().domains("")[0] == "gmail.com"

    def test_typing_narrows_them(self):
        assert TokenPredictor().domains("out") == ["outlook.com"]

    def test_a_learned_domain_outranks_the_built_ins(self):
        """The point of learning: a work address beats gmail after a few uses."""
        store = _store("owen@alphaosk.dev", "owen@alphaosk.dev")
        assert store.domains("")[0] == "alphaosk.dev"

    def test_a_learned_domain_is_not_offered_twice(self):
        store = _store("owen@gmail.com")
        assert store.domains("").count("gmail.com") == 1

    def test_the_count_is_respected(self):
        assert len(TokenPredictor().domains("", n=3)) == 3

    def test_only_emails_contribute_domains(self):
        """A phone number has no "@" and must not reach the domain index."""
        assert _store("555-123-4567").domains("") == list(DEFAULT_DOMAINS[:5])


class TestBounds:
    def test_the_store_is_capped(self):
        store = TokenPredictor()
        for i in range(TokenPredictor.MAX_TOKENS + 50):
            store.learn(f"{i:05d}1")
        assert len(store.tokens) <= TokenPredictor.MAX_TOKENS

    def test_eviction_keeps_the_most_typed(self):
        store = TokenPredictor()
        store.learn("90210")
        for _ in range(5):
            store.learn("90210")
        for i in range(TokenPredictor.MAX_TOKENS + 50):
            store.learn(f"{i:05d}1")
        assert "90210" in store.tokens

    def test_an_over_long_token_is_rejected(self):
        assert _store("1" + "a" * 200).tokens == {}


class TestPersistence:
    def test_a_round_trip_preserves_counts(self):
        store = _store("02134", "02134", "owen@gmail.com")
        restored = TokenPredictor()
        restored.from_dict(store.to_dict())
        assert restored.tokens == {"02134": 2, "owen@gmail.com": 1}

    def test_a_missing_key_loads_as_empty(self):
        """Every model saved before this feature existed has no tokens key."""
        store = TokenPredictor()
        store.from_dict({})
        assert store.tokens == {}

    def test_a_malformed_payload_is_ignored_rather_than_raising(self):
        store = TokenPredictor()
        store.from_dict("not a dict")
        assert store.tokens == {}

    def test_entries_are_revalidated_on_load(self):
        """Tightening the shape rule has to clean out an existing store.

        A file written by an older build (or hand-edited, or arriving in
        an imported Data Backup archive) is not a reason to start
        offering an SSN.
        """
        store = TokenPredictor()
        store.from_dict({"123-45-6789": 9, "555-123-4567": 2})
        assert store.tokens == {"555-123-4567": 2}

    def test_junk_entries_are_dropped_individually(self):
        """One bad token must not cost the user the rest of the store."""
        store = TokenPredictor()
        store.from_dict({"02134": "lots", "90210": 3, "76": True})
        assert store.tokens == {"90210": 3}

    def test_domains_survive_a_round_trip(self):
        store = _store("owen@alphaosk.dev")
        restored = TokenPredictor()
        restored.from_dict(store.to_dict())
        assert restored.domains("")[0] == "alphaosk.dev"


class TestForgettingIsPossible:
    def test_clear_empties_the_store(self):
        store = _store("02134", "owen@gmail.com")
        store.clear()
        assert store.tokens == {}
        assert store.domains("") == list(DEFAULT_DOMAINS[:5])

    def test_forget_removes_one_entry(self):
        store = _store("02134", "90210")
        assert store.forget("02134") is True
        assert store.tokens == {"90210": 1}

    def test_forgetting_an_absent_token_reports_it(self):
        assert _store("02134").forget("90210") is False


class TestALoadedEntryIsStoredInTheFormThatWasValidated:
    """`from_dict` validated the stripped form and stored the raw one.

    `is_learnable_token` strips trailing sentence punctuation before it
    judges, exactly as `learn` does before it stores, so validating one
    string and keeping another let a store hold a token `learn` would
    never have written -- and then offer it as a pill that types the
    stray punctuation into the user's number.  The file this rides in
    can be hand-edited or arrive in an imported archive, so an older or
    tampered-with model is not a reason to relax the rule.
    """

    def test_trailing_punctuation_is_stripped_on_load(self):
        store = TokenPredictor()
        store.from_dict({"555-123-4567.": 3})
        assert store.tokens == {"555-123-4567": 3}

    def test_the_stripped_form_is_what_gets_offered(self):
        store = TokenPredictor()
        store.from_dict({"555-123-4567.": 3})
        assert store.predict("555") == ["555-123-4567"]

    def test_both_written_forms_merge_into_one_entry(self):
        """Otherwise whichever iterated last silently won."""
        store = TokenPredictor()
        store.from_dict({"02134": 2, "02134.": 3})
        assert store.tokens == {"02134": 5}

    def test_a_token_that_is_only_punctuation_is_still_dropped(self):
        store = TokenPredictor()
        store.from_dict({"...": 4})
        assert store.tokens == {}
