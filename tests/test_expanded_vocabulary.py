"""New shipped words stay base data across upgrades and personal learning."""

import json
from dataclasses import replace
from pathlib import Path

from src.prediction.hybrid_predictor import HybridPredictor
from src.prediction.language import ENGLISH
from src.prediction.ngram_predictor import NgramPredictor


def small_profile(tmp_path):
    extra = tmp_path / "extra.txt"
    extra.write_text(
        "# unranked words\ncaregiver\ndystrophy\ncaregiver\nthe\n\ntwo words\ninvalid123\nxqz\nc\n",
        encoding="utf-8",
    )
    dictionary = tmp_path / "base.txt"
    dictionary.write_text("hello 20\n", encoding="utf-8")
    return replace(ENGLISH, frequency=None, dictionary=dictionary, extra_vocabulary=extra)


def test_extra_words_are_low_weight_base_counts_not_personal_history(tmp_path):
    profile = small_profile(tmp_path)
    original = NgramPredictor(profile=replace(profile, extra_vocabulary=None))
    predictor = NgramPredictor(profile=profile)

    assert predictor._base_total == original._base_total + 2
    assert predictor.total_words == original.total_words + 2
    assert predictor._base_unigrams["the"] == original._base_unigrams["the"]
    for word in ("caregiver", "dystrophy"):
        assert predictor._base_unigrams[word] == predictor.unigrams[word] == 1
    for word in ("two words", "invalid123", "xqz", "c"):
        assert word not in predictor.unigrams
    assert predictor._user_total == sum(predictor.user_vocab.values()) == 0
    assert not predictor._candidate_counts

    predictor._load_extra_vocabulary()
    assert predictor._base_total == original._base_total + 2
    assert predictor.total_words == original.total_words + 2


def test_new_words_survive_an_older_saved_model_and_clear(tmp_path):
    profile = small_profile(tmp_path)
    old = NgramPredictor(profile=replace(profile, extra_vocabulary=None))
    old.learn_word("hello")
    snapshot = tmp_path / "old.json"
    old.save(snapshot)
    predictor = NgramPredictor(snapshot, profile)

    assert predictor._base_unigrams["caregiver"] == 1
    assert predictor.user_vocab == old.user_vocab
    assert predictor._user_total == sum(predictor.user_vocab.values())
    assert predictor.predict("caregi", 5) == ["caregiver"]

    predictor.learn_word("caregiver")
    assert predictor.user_vocab["caregiver"] == 5
    assert predictor._base_unigrams["caregiver"] == 1
    predictor.clear_user_data()
    assert predictor.unigrams["caregiver"] == 1
    assert predictor._base_total == sum(predictor._base_unigrams.values())
    assert predictor._user_total == sum(predictor.user_vocab.values()) == 0


def test_hybrid_reload_restores_new_words_to_both_predictors(tmp_path):
    profile = small_profile(tmp_path)
    snapshot = tmp_path / "models" / "ngram_model.json"
    snapshot.parent.mkdir()
    # A pre-expansion backup must not erase the new release's vocabulary.
    snapshot.write_text(json.dumps({"unigrams": {"hello": 20}}), encoding="utf-8")
    predictor = HybridPredictor(snapshot.parent, enable_llm=False, profile=profile)
    for reload in (False, True):
        if reload:
            predictor.reload_from_disk()
        assert "caregiver" in predictor.predict("caregi", 5)
        assert "caregiver" in predictor._fuzzy.word_generator.dictionary
        assert predictor._ngram._user_total == sum(predictor._ngram.user_vocab.values())


def test_optional_or_missing_extra_list_does_not_prevent_loading(tmp_path):
    profile = small_profile(tmp_path)
    for extra in (None, tmp_path / "missing.txt"):
        predictor = NgramPredictor(profile=replace(profile, extra_vocabulary=extra))
        assert "caregiver" not in predictor._base_unigrams
        assert predictor.predict("the", 5)


def test_release_dictionary_covers_care_and_software_without_personal_learning():
    predictor = NgramPredictor()
    predictor.load_base_dictionary()
    for word in (
        "caregiver",
        "dystrophy",
        "screenreader",
        "telehealth",
        "typescript",
        "neurodiversity",
        "rhythmically",
    ):
        assert word in predictor._base_unigrams
        assert predictor._is_plausible_word(word)
    assert predictor._base_total == sum(predictor._base_unigrams.values())
    assert predictor._user_total == sum(predictor.user_vocab.values()) == 0


def test_a_corrupt_wordlist_does_not_stop_the_keyboard_starting(tmp_path: Path) -> None:
    """The loader runs inside ``__init__``, so it must swallow everything.

    Bytes that are not UTF-8 raise ``UnicodeDecodeError``, which is a
    ``ValueError`` and not an ``OSError``, so the original ``except
    OSError`` let it out of the constructor. A user whose only input
    device is this keyboard cannot repair the file without the keyboard,
    which is why the cost of being wrong is asymmetric here and the catch
    is deliberately broad.
    """
    broken = tmp_path / "broken.txt"
    broken.write_bytes(b"caregiver\n\xff\xfe not utf-8 \xff\n")
    profile = replace(ENGLISH, extra_vocabulary=broken)

    predictor = NgramPredictor(profile=profile)

    # It started, and it is usable rather than merely constructed.
    assert predictor.predict("th", 3)


def test_a_missing_wordlist_is_simply_absent(tmp_path: Path) -> None:
    """The near-miss: absence is ordinary, not an error to report.

    Paired with the case above so a loader that swallowed everything by
    doing nothing at all could not satisfy both.
    """
    profile = replace(ENGLISH, extra_vocabulary=tmp_path / "nope.txt")

    predictor = NgramPredictor(profile=profile)

    assert predictor.predict("th", 3)
    assert "caregiver" not in predictor._base_unigrams


def test_the_validated_wordlist_is_shared_between_instances(tmp_path: Path) -> None:
    """Two predictors over the same file validate it once.

    The cache is why construction is not 125 ms of re-parsing on every one
    of the suite's ~1,300 predictors.
    """
    from src.prediction import ngram_predictor as module

    extra = tmp_path / "extra.txt"
    extra.write_text("caregiver\ndystrophy\n", encoding="utf-8")
    profile = replace(ENGLISH, extra_vocabulary=extra)

    module._EXTRA_VOCABULARY_CACHE.clear()
    first = NgramPredictor(profile=profile)
    entries_after_first = len(module._EXTRA_VOCABULARY_CACHE)
    second = NgramPredictor(profile=profile)

    assert entries_after_first == 1
    assert len(module._EXTRA_VOCABULARY_CACHE) == 1
    assert first._base_unigrams["caregiver"] == second._base_unigrams["caregiver"]


def test_an_edited_wordlist_is_revalidated(tmp_path: Path) -> None:
    """The near-miss: a cache ignoring mtime and size would serve the old
    contents after an edit, which is how a stale generated file silently
    outlives its regeneration."""
    from src.prediction import ngram_predictor as module

    extra = tmp_path / "extra.txt"
    extra.write_text("caregiver\n", encoding="utf-8")
    profile = replace(ENGLISH, extra_vocabulary=extra)

    module._EXTRA_VOCABULARY_CACHE.clear()
    before = NgramPredictor(profile=profile)
    assert "dystrophy" not in before._base_unigrams

    extra.write_text("caregiver\ndystrophy\n", encoding="utf-8")
    after = NgramPredictor(profile=profile)

    assert "dystrophy" in after._base_unigrams


def test_a_populated_capitalisation_table_bypasses_the_cache(tmp_path: Path) -> None:
    """The guard that removes an ordering assumption.

    ``_is_plausible_word`` can admit a word through ``is_taught_acronym``,
    which reads ``taught_capitalization``. Which table matters: proper
    nouns fill ``capitalization`` and leave this one empty, so guarding on
    the wrong one bypasses the cache on every construction and the caching
    silently does nothing. That is what this test caught.
    """
    from src.prediction import ngram_predictor as module

    extra = tmp_path / "extra.txt"
    extra.write_text("caregiver\n", encoding="utf-8")
    profile = replace(ENGLISH, extra_vocabulary=extra)

    module._EXTRA_VOCABULARY_CACHE.clear()
    predictor = NgramPredictor(profile=profile)
    predictor.taught_capitalization.add("pr")

    module._EXTRA_VOCABULARY_CACHE.clear()
    words = predictor._validated_extra_words()

    assert words == ("caregiver",)
    assert not module._EXTRA_VOCABULARY_CACHE, "a populated table must not fill the cache"
