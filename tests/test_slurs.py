"""Slurs are removed from the shipped vocabulary, and stay removed.

Two different mechanisms keep content off the prediction bar, and this file
is about the stricter one. Profanity stays in the dictionary and a user
setting decides whether the bar volunteers it (``test_explicit_filter.py``).
A slur is taken out of every shipped wordlist and seed file, so no setting
offers it, and stripped from an old saved model on load, so an upgrade does
not keep it alive.

Every removal case is paired with the near-miss it must leave alone. The
list is exact words, never stems, because the stem rule it replaced caught
"spiced", "japes", "chinking" and "retarder", and a word wrongly removed is
one nobody can ever have predicted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.prediction.language import ENGLISH

DATA = Path(__file__).resolve().parent.parent / "data"


def _words(path: Path) -> set[str]:
    return {
        line.strip().lower()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


SLURS = _words(DATA / "slurs.txt")

#: Ordinary words beside a slur, each one of them caught by some stem rule
#: or substring match. Removing any of these would be the Scunthorpe failure
#: in its worst form: not merely withheld, but gone.
NEAR_MISSES = (
    "spiced",
    "spicier",
    "spicy",
    "spices",
    "japes",
    "japed",
    "chinked",
    "chinking",
    "retarding",
    "retarder",
    "retardant",
    "retardation",
    "raccoon",
    "sauerkraut",
    "spooky",
    "cocoon",
    "tycoon",
    "gypsum",
)

#: Words with a slur sense and a common ordinary one. They stay in the
#: vocabulary and are left to the suggestion filter instead.
FILTER_ONLY = ("chink", "chinks", "dyke", "dykes", "fag", "fags", "negro")


def _shipped_vocabulary() -> set[str]:
    words: set[str] = set()
    for path in (
        ENGLISH.dictionary,
        ENGLISH.frequency,
        ENGLISH.extra_vocabulary,
        DATA / "google-20000-supplement.txt",
    ):
        assert path is not None
        words |= {line.split()[0] for line in _words(path)}
    return words


class TestTheListItself:
    def test_it_is_exact_lowercase_words(self) -> None:
        assert SLURS
        assert all(re.fullmatch(r"[a-z]+", word) for word in SLURS), sorted(SLURS)

    def test_the_profile_points_at_it(self) -> None:
        assert ENGLISH.slurs == DATA / "slurs.txt"

    def test_it_names_no_ordinary_word(self) -> None:
        assert not SLURS & set(NEAR_MISSES)
        assert not SLURS & set(FILTER_ONLY)


class TestNoShippedFileCarriesOne:
    @pytest.mark.parametrize(
        "path",
        sorted(p for p in DATA.glob("*.txt") if p.name != "slurs.txt"),
        ids=lambda p: p.name,
    )
    def test_the_file_is_clean(self, path: Path) -> None:
        """Every data file, not only the wordlists: the seed tables fed
        "redskins" to the context model on their own, as a prefix with
        five continuations, after it was gone from every wordlist but one."""
        tokens = set(re.findall(r"[a-z']+", path.read_text(encoding="utf-8").lower()))
        assert not tokens & SLURS, sorted(tokens & SLURS)

    def test_the_near_misses_are_still_shipped(self) -> None:
        """The inverse. An empty vocabulary would pass the test above."""
        vocabulary = _shipped_vocabulary()
        missing = [word for word in NEAR_MISSES if word not in vocabulary]
        assert not missing, missing

    def test_the_ambiguous_words_are_shipped_and_flagged(self) -> None:
        vocabulary = _shipped_vocabulary()
        flagged = _words(DATA / "explicit_words.txt")
        for word in FILTER_ONLY:
            assert word in vocabulary, word
            assert word in flagged, word

    def test_the_filter_no_longer_flags_the_near_misses(self) -> None:
        flagged = _words(DATA / "explicit_words.txt")
        assert not flagged & set(NEAR_MISSES), sorted(flagged & set(NEAR_MISSES))


PySide6 = pytest.importorskip("PySide6")

from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402


class TestAnOldSavedModelLosesThem:
    def test_a_base_count_carried_in_a_saved_model_is_stripped(self, tmp_path: Path) -> None:
        """The merged unigram table is persisted, so a model saved before
        this change holds each slur at its old base count. Paired with an
        ordinary word written the same way, which must survive: a strip
        that dropped every persisted base entry would pass the first half."""
        model_dir = tmp_path / "model"
        first = HybridPredictor(model_dir=model_dir, enable_llm=False)
        first._ngram.unigrams["wetback"] = 1
        first._ngram.unigrams["zzqordinary"] = 1
        first.save()

        second = HybridPredictor(model_dir=model_dir, enable_llm=False)

        assert not second._ngram.in_vocabulary("wetback")
        assert "wetback" not in second._fuzzy.word_generator.dictionary
        assert "wetback" not in [w.lower() for w in second.predict("wetb", n=8)]
        assert second._ngram.unigrams["zzqordinary"] == 1

    def test_a_word_the_user_typed_is_theirs_to_keep(self, tmp_path: Path) -> None:
        """Removed from what the keyboard volunteers, not from what the user
        may say. Once it is in their own vocabulary it survives a reload."""
        model_dir = tmp_path / "model"
        first = HybridPredictor(model_dir=model_dir, enable_llm=False)
        first.learn_word("wetback")
        first.save()

        second = HybridPredictor(model_dir=model_dir, enable_llm=False)

        assert second._ngram.user_vocab["wetback"] == 5
        assert second._ngram.in_vocabulary("wetback")

    def test_a_fresh_model_never_offers_one(self, tmp_path: Path) -> None:
        predictor = HybridPredictor(model_dir=tmp_path / "model", enable_llm=False)
        predictor.set_explicit_filter(False)
        for word in sorted(SLURS):
            assert not predictor._ngram.in_vocabulary(word), word
