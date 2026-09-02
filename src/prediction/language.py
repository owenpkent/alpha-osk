"""Per-language behaviour, gathered into one object.

Every field here was a module-level literal somewhere in the prediction
engine, written when English was the only language: the tokenizer's
``[a-zA-Z']+``, the vowel set the fragment filter reads, the short-word
allow-list, the "I" family, the paths to the two English wordlists.
None of them are wrong; they are just answers to questions that have a
different answer in French, and a keyboard that wants a second language
has to be able to ask the question.

**English is a profile like any other, and that is the acceptance
criterion.** ``ENGLISH`` below is constructed from exactly the constants
that used to be inline, so the whole existing test suite has to pass
unchanged. If it does not, the refactor is wrong rather than the tests.

The profile is resolved once and **passed down** rather than read from a
module-level "current language". A global would be one more thing to
keep in sync with the layout, the model file and the settings layer, and
this codebase has already learned what parallel copies of one fact cost
(see the sticky-modifier release blocks in ``keyboard_bridge.py``).

Deliberately **not** here:

- **Key positions.** Dvorak-English is a real combination, so the
  spatial model belongs to the *layout*, not the language, and
  ``fuzzy_recognizer.positions_from_layout`` derives it from the layout
  JSON instead.
- **The ``text_patterns`` locale data** (NANP phone groupings, the US
  street-suffix matcher, which punctuation auto-spaces, the ``3.14`` /
  ``1,000`` number shapes). All of it is English-and-American and all of
  it belongs in a profile eventually, but those are free functions with
  about twenty call sites across the bridge and the token predictor, and
  a field nothing reads is worse than a field that does not exist yet.
  That slice lands with French, which is what gives it a second value to
  hold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Pattern

_DATA_DIR = Path(__file__).parent.parent.parent / "data"


@dataclass(frozen=True)
class LanguageProfile:
    """Everything the prediction engine needs to know about a language.

    Frozen because a profile is a description, not state: the engine
    reads it on every keystroke and nothing should be able to edit the
    language out from under a half-typed word.
    """

    #: BCP 47-ish tag, for logging and for naming the model file.
    code: str

    #: What counts as a word. Applied to already-lowercased text.
    word_re: Pattern[str]

    #: Strict vowels, for the fragment filter's "has a vowel" half.
    vowels: frozenset

    #: Letters counted as *both* vowel and consonant by the fragment
    #: filter. English has one, ``y``, which is what lets "cry" and
    #: "rhythm" through the vowel half while "eye" and "aye" still pass
    #: the consonant half.
    semivowels: frozenset

    #: One- and two-letter strings that are real words. An allow-list
    #: rather than a length rule on purpose: the engine learns whatever
    #: the user types, so stray fragments ("th", "ap") accumulate, and
    #: relaxing the length bar would let every one of them compete for a
    #: pill. Words, not lengths.
    short_words: frozenset

    #: Lowercase form to display form, applied regardless of position.
    #: Keep it tight: this is the one auto-capital that fires
    #: mid-sentence whatever the user typed, so an entry has to be
    #: unambiguous in *every* context. English has the "I" family and
    #: nothing else; French has nothing at all.
    always_capitalize: Mapping[str, str]

    #: Bootstrap dictionary, one word (or ``word<TAB>count``) per line.
    dictionary: Path

    #: Frequency-ranked wordlist, most common first. Optional because a
    #: language may ship a dictionary that already carries counts.
    frequency: Optional[Path] = None

    def is_short_word(self, word: str) -> bool:
        """True if ``word`` is short enough to need the allow-list and on it.

        Lives here rather than at the two call sites so the length bar
        and the list cannot drift apart, which is the same reason the
        list has one copy rather than one per consumer.
        """
        return word.lower() in self.short_words


ENGLISH = LanguageProfile(
    code="en",
    # Every digit and every symbol is discarded before a word reaches
    # the vocabulary, which is right for a word model and is why
    # structured tokens (phone numbers, zips, emails) need a store of
    # their own. See token_predictor.py.
    word_re=re.compile(r"[a-zA-Z']+"),
    vowels=frozenset("aeiou"),
    semivowels=frozenset("y"),
    short_words=frozenset(
        {
            "a",
            "i",
            "am",
            "an",
            "as",
            "at",
            "be",
            "by",
            "do",
            "go",
            "he",
            "hi",
            "if",
            "in",
            "is",
            "it",
            "me",
            "my",
            "no",
            "of",
            "oh",
            "ok",
            "on",
            "or",
            "so",
            "to",
            "up",
            "us",
            "we",
            "ya",
            "ha",
            "ah",
            "eh",
            "mm",
            "hm",
            "mr",
            "ms",
            "dr",
            "st",
            "pm",
        }
    ),
    always_capitalize={
        "i": "I",
        "i'm": "I'm",
        "i'll": "I'll",
        "i'd": "I'd",
        "i've": "I've",
    },
    dictionary=_DATA_DIR / "base_dictionary.txt",
    frequency=_DATA_DIR / "google-10000-english-usa-no-swears.txt",
)


#: Every profile the build knows about, by ``code``.
PROFILES: Mapping[str, LanguageProfile] = {ENGLISH.code: ENGLISH}

DEFAULT_PROFILE = ENGLISH


def get_profile(code: str) -> LanguageProfile:
    """Look up a profile by code, falling back to the default.

    Falls back rather than raising because the code can arrive from a
    settings file or an imported archive, and an unknown language is a
    reason to keep predicting in English, not a reason to fail to start.
    """
    return PROFILES.get(code, DEFAULT_PROFILE)
