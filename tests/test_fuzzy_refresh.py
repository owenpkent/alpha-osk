"""The fuzzy dictionary follows the vocabulary, and PPM is out of the merge.

The fuzzy recognizer's dictionary was loaded once at startup and never
touched again: a comment in the hybrid's constructor named a
``_refresh_fuzzy_frequencies`` that did not exist, ``enable_vocabulary_pack``
wrote pack words into the n-gram only, and so a word learned this session
was not fuzzy-matchable until a restart and a pack's words never were.
Each test here drives one vocabulary event through the hybrid and asks
the fuzzy source, on the same instance, whether it can now see the word.

The PPM half: its word candidates were a character beam with no
dictionary and, measured with the prefix beam in place, cost a point of
keystroke savings and 18 ms per keystroke.  The model keeps training and
saving; its candidates simply no longer enter the merge unless a bench
puts them back.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.prediction.hybrid_predictor import HybridPredictor
from src.prediction.prefix_beam import PrefixIndex
from src.prediction.symspell import SymSpell
from src.prediction.vocabulary_pack import PackManager

NOVEL = "zorblat"  # in no wordlist; promoted into user_vocab on its third sighting


@pytest.fixture
def hp(tmp_path: Path) -> HybridPredictor:
    return HybridPredictor(model_dir=tmp_path / "model", enable_llm=False)


def fuzzy_top(hp: HybridPredictor, typed: str) -> list[str]:
    return [w for w, _ in hp._fuzzy.get_fuzzy_predictions(typed, 5)]


class TestLearnedWordsReachTheFuzzySource:
    def test_a_word_learned_this_session_is_fuzzy_matchable_without_a_restart(self, hp):
        assert NOVEL not in fuzzy_top(hp, "zorb")
        for _ in range(3):
            hp.learn(f"the {NOVEL} arrived")
        assert NOVEL in fuzzy_top(hp, "zorb")
        # and through the merge, from a slipped prefix (p sits beside o)
        assert NOVEL in hp.predict("the zprbl", 5)

    def test_a_pill_clicked_three_times_reaches_it_too(self, hp):
        for _ in range(3):
            hp.learn_from_selection("the", NOVEL)
        assert NOVEL in fuzzy_top(hp, "zorb")

    def test_a_boost_raises_the_fuzzy_frequency_of_a_known_word(self, hp):
        before = hp._fuzzy.word_generator.dictionary["hello"]
        hp.mark_good_suggestion("hello")
        assert hp._fuzzy.word_generator.dictionary["hello"] > before

    def test_a_lower_count_never_lowers_the_fuzzy_frequency(self, hp):
        before = hp._fuzzy.word_generator.dictionary["hello"]
        hp._fuzzy.update_word("hello", 1)
        assert hp._fuzzy.word_generator.dictionary["hello"] == before

    def test_a_personal_weight_of_one_does_not_take_the_keystroke_path_down(self, hp):
        # `personal_weight` is unclamped and the base-scale mapping divides
        # by (1 - alpha) inside `learn`, so 1.0 used to raise mid-keystroke.
        hp._ngram.personal_weight = 1.0
        for _ in range(3):
            hp.learn(f"the {NOVEL} arrived")
        assert NOVEL in hp._fuzzy.word_generator.dictionary


class TestPacksReachTheFuzzySource:
    def test_enabling_a_pack_makes_its_words_fuzzy_matchable(self, hp, tmp_path):
        packs = tmp_path / "packs"
        (packs / "mypack").mkdir(parents=True)
        (packs / "mypack" / "dictionary.txt").write_text("quxwordly\nquxwordless\n")
        hp._pack_manager = PackManager(packs_dir=packs, user_packs_dir=tmp_path / "user")
        assert "quxwordly" not in fuzzy_top(hp, "quxw")
        assert hp.enable_vocabulary_pack("mypack")
        assert "quxwordly" in fuzzy_top(hp, "quxw")


class TestAShrunkVocabularyIsRebuilt:
    def test_clear_learned_data_forgets_the_word_in_the_fuzzy_dictionary_too(self, hp):
        for _ in range(3):
            hp.learn(f"the {NOVEL} arrived")
        assert NOVEL in hp._fuzzy.word_generator.dictionary
        hp.clear_user_data()
        assert NOVEL not in hp._fuzzy.word_generator.dictionary
        assert "hello" in hp._fuzzy.word_generator.dictionary  # the base survives

    def test_rolling_back_a_boost_lowers_the_fuzzy_frequency_too(self, hp):
        # The boost reaches the fuzzy dictionary through the refresh, which
        # only raises, so the rollback has to rebuild; without it the word
        # kept its boosted frequency until the next restart.
        before = hp._fuzzy.word_generator.dictionary["hello"]
        hp.mark_good_suggestion("hello")
        boosted = hp._fuzzy.word_generator.dictionary["hello"]
        assert boosted > before
        hp.unprefer("hello")
        assert hp._fuzzy.word_generator.dictionary["hello"] == before

    def test_reload_from_disk_rebuilds_from_what_is_on_disk(self, hp, tmp_path):
        for _ in range(3):
            hp.learn(f"the {NOVEL} arrived")
        hp._ngram.save(tmp_path / "model" / "ngram_model.json")
        for _ in range(3):
            hp.learn("the florpish arrived")  # in memory only, never saved
        assert "florpish" in hp._fuzzy.word_generator.dictionary
        hp.reload_from_disk()
        assert NOVEL in hp._fuzzy.word_generator.dictionary
        assert "florpish" not in hp._fuzzy.word_generator.dictionary


class TestTheIndexesUpdateInPlace:
    def test_symspell_indexes_a_new_word_without_a_rebuild(self):
        index = SymSpell(max_edit_distance=2)
        for w in ("hello", "help", "world"):
            index.add_word(w, 10)
        index.prepare()
        assert index._built
        index.add_word("zorblat", 3)
        assert index._built, "a new word must not invalidate the whole index"
        assert [w for w, _, _ in index.lookup("zorblt")][:1] == ["zorblat"]

    def test_an_incrementally_built_index_matches_a_fresh_one(self):
        words = {"hello": 10, "help": 8, "world": 6, "word": 5, "worn": 2}
        fresh = SymSpell(max_edit_distance=2)
        for w, f in words.items():
            fresh.add_word(w, f)
        fresh.prepare()
        grown = SymSpell(max_edit_distance=2)
        grown.add_word("hello", 10)
        grown.prepare()
        for w, f in words.items():
            grown.add_word(w, f)
        for probe in ("helo", "wrold", "wor", "wrn"):
            assert grown.lookup(probe) == fresh.lookup(probe)

    def test_prefix_index_update_adds_a_word_and_reorders_completions(self):
        index = PrefixIndex({"cat": 3.0, "cats": 2.0}, precompute_len=2)
        assert not index.is_live("car")
        index.update_word("car", 1.0)
        assert index.is_live("car") and index.children("ca") == "rt"
        assert [w for _, w in index.completions("ca")] == ["cat", "cats", "car"]
        index.update_word("car", 9.0)
        assert [w for _, w in index.completions("ca")] == ["car", "cat", "cats"]
        assert [w for _, w in index.completions("car")] == ["car"]  # the bisect path


class TestPPMIsOutOfTheMerge:
    def test_the_merge_does_not_consult_ppm_by_default(self, hp, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("PPM word candidates were requested")

        monkeypatch.setattr(hp._ppm_word, "predict_with_scores", boom)
        assert hp.predict("hel", 5)
        hp._ppm_in_merge = True
        with pytest.raises(AssertionError):
            hp.predict("hel", 5)

    def test_ppm_still_learns(self, hp, monkeypatch):
        seen: list[str] = []
        monkeypatch.setattr(hp._ppm, "learn_text", lambda text: seen.append(text))
        hp.learn("hello there")
        assert "hello there" in seen
