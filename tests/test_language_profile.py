"""Tests for the language profile.

The refactor these cover moved a pile of English literals out of the
prediction engine and into ``LanguageProfile``.  Its acceptance
criterion is that the *rest* of the suite passes unchanged, so what is
worth asserting here is the part that criterion cannot see: that the
engine genuinely reads the profile rather than having kept a private
copy of the constants it used to own.

Every case therefore drives ``NgramPredictor`` with a deliberately
un-English profile and checks the behaviour follows.  A test that only
exercised ``ENGLISH`` would pass just as happily against the literals.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from src.prediction.hybrid_predictor import _short_word_allowed
from src.prediction.language import ENGLISH, PROFILES, LanguageProfile, get_profile
from src.prediction.ngram_predictor import NgramPredictor


def _profile(**overrides) -> LanguageProfile:
    """ENGLISH with a field or two swapped out."""
    fields = {
        "code": ENGLISH.code,
        "word_re": ENGLISH.word_re,
        "vowels": ENGLISH.vowels,
        "semivowels": ENGLISH.semivowels,
        "short_words": ENGLISH.short_words,
        "always_capitalize": ENGLISH.always_capitalize,
        "dictionary": ENGLISH.dictionary,
        "frequency": ENGLISH.frequency,
    }
    fields.update(overrides)
    return LanguageProfile(**fields)


class TestEnglishIsStillEnglish:
    """The profile has to reproduce what the literals said."""

    def test_the_i_family_is_the_whole_always_capitalize_map(self):
        # Deliberately tight: this is the one auto-capital that fires
        # mid-sentence whatever the user typed, and the removed
        # proper-noun tiers are not coming back as a default.
        assert dict(ENGLISH.always_capitalize) == {
            "i": "I",
            "i'm": "I'm",
            "i'll": "I'll",
            "i'd": "I'd",
            "i've": "I've",
        }

    def test_y_is_a_semivowel_not_a_vowel(self):
        # The split is what lets "cry" pass the vowel half and "eye"
        # pass the consonant half.  Collapsing them into one set breaks
        # one of those two, and which one depends on which set wins.
        assert "y" in ENGLISH.semivowels
        assert "y" not in ENGLISH.vowels

    def test_the_tokenizer_still_drops_digits_and_symbols(self):
        # Structured tokens have a store of their own precisely because
        # this pattern discards them; widening it here would quietly
        # give the word model a second, dumber vocabulary.
        assert ENGLISH.word_re.findall("call 555-1234 now") == ["call", "now"]

    def test_the_shipped_wordlists_exist(self):
        assert ENGLISH.dictionary.exists()
        assert ENGLISH.frequency is not None and ENGLISH.frequency.exists()

    def test_lookup_falls_back_rather_than_raising(self):
        # The code can arrive from a settings file or an imported
        # archive, and an unknown language is a reason to keep
        # predicting in English, not a reason to fail to start.
        assert get_profile("en") is ENGLISH
        assert get_profile("kl") is ENGLISH
        assert ENGLISH.code in PROFILES


class TestTheEngineReadsTheProfile:
    """The half the rest of the suite cannot see."""

    def test_the_tokenizer_comes_from_the_profile(self):
        # A pattern that keeps digits: if _tokenize still held its own
        # literal, "abc123" would come back split.
        predictor = NgramPredictor(profile=_profile(word_re=re.compile(r"[a-z0-9]+")))
        assert predictor._tokenize("abc123 def") == ["abc123", "def"]

    def test_the_fragment_filter_reads_the_profile_vowels(self):
        # "xqz" is the canonical all-consonant reject.  Declaring x a
        # vowel has to rescue it, or the filter is still consulting
        # "aeiou" directly.
        assert not NgramPredictor()._is_plausible_word("xqz")
        rescued = NgramPredictor(profile=_profile(vowels=frozenset("aeioux")))
        assert rescued._is_plausible_word("xqz")

    def test_the_short_word_allow_list_comes_from_the_profile(self):
        assert not NgramPredictor()._is_plausible_word("th")
        allowed = NgramPredictor(profile=_profile(short_words=frozenset({"th"})))
        assert allowed._is_plausible_word("th")

    def test_always_capitalize_comes_from_the_profile(self):
        # A profile with an empty map is the French case, and it must
        # leave "i" alone rather than falling back to the old literal.
        assert NgramPredictor().get_capitalized("i") == "I"
        flat = NgramPredictor(profile=_profile(always_capitalize={}))
        assert flat.get_capitalized("i") == "i"

    def test_the_next_word_filter_reads_the_profile(self):
        # _short_word_allowed lives in hybrid_predictor and used to
        # reach into NgramPredictor for the class attribute.  It is the
        # second consumer of the one list, and the reason the list has
        # one copy.
        assert not _short_word_allowed("zz")
        assert _short_word_allowed("zz", _profile(short_words=frozenset({"zz"})))

    def test_the_dictionary_path_comes_from_the_profile(self, tmp_path):
        # A profile pointing at a wordlist of one made-up word: the
        # word has to land in the vocabulary, which it cannot do if
        # load_base_dictionary still resolves data/ itself.
        wordlist = tmp_path / "tiny.txt"
        wordlist.write_text("zorble\n", encoding="utf-8")
        predictor = NgramPredictor(profile=_profile(dictionary=wordlist, frequency=None))
        assert predictor.load_base_dictionary()
        assert "zorble" in predictor.unigrams

    def test_a_profile_with_no_frequency_list_still_loads(self):
        # `frequency` is optional because a language may ship a
        # dictionary that already carries counts.  None must be a
        # skip, not a crash on None.exists().
        NgramPredictor(profile=_profile(frequency=None))._load_frequency_wordlist()


class TestTheProfileIsADescriptionNotState:
    def test_it_cannot_be_edited_out_from_under_a_half_typed_word(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            ENGLISH.code = "fr"  # type: ignore[misc]
