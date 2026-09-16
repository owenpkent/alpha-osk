"""Behavioral parity tests for accelerated n-gram candidate selection."""

from __future__ import annotations

import random
import re
from dataclasses import replace
from pathlib import Path

import pytest

from src.prediction.language import ENGLISH
from src.prediction.ngram_predictor import NgramPredictor

MAX_UNICODE = "\U0010ffff"


def _blank_predictor(*, broad_unicode: bool = False) -> NgramPredictor:
    profile = replace(
        ENGLISH,
        word_re=re.compile(r"[^\s]+") if broad_unicode else ENGLISH.word_re,
        frequency=None,
        extra_vocabulary=None,
    )
    predictor = NgramPredictor(profile=profile)
    predictor.unigrams.clear()
    predictor._base_unigrams.clear()
    predictor.user_vocab.clear()
    predictor._corpus_unigrams.clear()
    predictor.bigrams.clear()
    predictor.trigrams.clear()
    predictor._user_bigrams.clear()
    predictor._user_trigrams.clear()
    predictor._base_total = 0
    predictor._user_total = 0
    predictor.total_words = 0
    predictor.use_sentence_start_context = False
    return predictor


def _seed_unigrams(
    predictor: NgramPredictor,
    base: dict[str, int],
    user: dict[str, int] | None = None,
) -> None:
    user = user or {}
    predictor._base_unigrams.update(base)
    predictor.user_vocab.update(user)
    predictor._base_total = sum(base.values())
    predictor._user_total = sum(user.values())
    for word in set(base) | set(user):
        predictor.unigrams[word] = base.get(word, 0) + user.get(word, 0)
    predictor.total_words = sum(predictor.unigrams.values())


def _seed_corpus_prior(predictor: NgramPredictor, corpus: dict[str, int]) -> None:
    predictor._corpus_unigrams.update(corpus)
    predictor._corpus_total = sum(corpus.values())


def _seed_context(
    predictor: NgramPredictor,
    prefix: str,
    *,
    base: dict[str, int],
    user: dict[str, float] | None = None,
    trigram: bool = False,
) -> None:
    user = user or {}
    merged_table = predictor.trigrams if trigram else predictor.bigrams
    user_table = predictor._user_trigrams if trigram else predictor._user_bigrams
    for word in set(base) | set(user):
        merged_table[prefix][word] = base.get(word, 0) + int(user.get(word, 0.0) + 0.5)
    user_table[prefix].update(user)


def _oracle_context_probs(
    predictor: NgramPredictor,
    merged: dict[str, int] | None,
    user: dict[str, float] | None,
) -> dict[str, float]:
    user_evidence = sum(user.values()) if user else 0.0
    if user_evidence <= 0.0 or user is None:
        if not merged:
            return {}
        total = sum(merged.values())
        if total <= 0:
            return {}
        return {word: count / total for word, count in merged.items()}

    merged = merged or {}
    base_counts: dict[str, int] = {}
    base_total = 0
    for word, count in merged.items():
        base_count = count - int(user.get(word, 0.0) + 0.5)
        if base_count > 0:
            base_counts[word] = base_count
            base_total += base_count
    prior = predictor._CONTEXT_PRIOR_FLOOR + predictor._CONTEXT_BASE_TRUST * base_total
    user_weight = user_evidence / (user_evidence + prior)
    probabilities = {}
    for word in set(merged) | set(user):
        probability = user_weight * (user.get(word, 0.0) / user_evidence)
        if base_total > 0:
            probability += (1.0 - user_weight) * (base_counts.get(word, 0) / base_total)
        if probability > 0.0:
            probabilities[word] = probability
    return probabilities


def _brute_force_predictions(
    predictor: NgramPredictor,
    context: str,
    n: int,
) -> list[tuple[str, float]]:
    ends_with_space = context.endswith(" ")
    context_clean = context.lower().strip()
    partial = ""
    if not context_clean:
        words: list[str] = []
    else:
        words = predictor._tokenize(context_clean)
        if not ends_with_space and words:
            partial = words[-1]
            words = words[:-1]

    if not words and not partial:
        scores = {word: float(frequency) for word, frequency in predictor.unigrams.items()}
        for word, frequency in predictor._corpus_unigrams.items():
            scores[word] = scores.get(word, 0.0) + predictor._CORPUS_PRIOR_WEIGHT * frequency
        return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:n]

    trigram_probabilities: dict[str, float] = {}
    if len(words) >= 2:
        prefix = f"{words[-2]} {words[-1]}"
        trigram_probabilities = _oracle_context_probs(
            predictor,
            predictor.trigrams.get(prefix),
            predictor._user_trigrams.get(prefix),
        )
    bigram_probabilities: dict[str, float] = {}
    if words:
        prefix = words[-1]
        bigram_probabilities = _oracle_context_probs(
            predictor,
            predictor.bigrams.get(prefix),
            predictor._user_bigrams.get(prefix),
        )

    if trigram_probabilities or bigram_probabilities:
        trigram_weight = predictor._LAMBDA_TRI
        bigram_weight = predictor._LAMBDA_BI
        unigram_weight = predictor._LAMBDA_UNI
    else:
        trigram_weight = 0.0
        bigram_weight = 0.0
        unigram_weight = 1.0

    words_to_score = (
        set(predictor._base_unigrams)
        | set(predictor.user_vocab)
        | set(predictor._corpus_unigrams)
        | set(bigram_probabilities)
        | set(trigram_probabilities)
    )
    candidates = []
    for word in words_to_score:
        if not predictor._matches_partial(word, partial):
            continue
        base_probability = (
            predictor._base_unigrams.get(word, 0) / predictor._base_total
            if predictor._base_total
            else 0.0
        )
        user_probability = (
            predictor._effective_typing_count(word) / predictor._effective_typing_total()
            if predictor._effective_typing_total()
            else 0.0
        )
        unigram_probability = (
            predictor.personal_weight * user_probability
            + (1.0 - predictor.personal_weight) * base_probability
        )
        score = (
            trigram_weight * trigram_probabilities.get(word, 0.0)
            + bigram_weight * bigram_probabilities.get(word, 0.0)
            + unigram_weight * unigram_probability
        )
        if score > 0.0:
            candidates.append((word, score))
    return sorted(candidates, key=lambda item: (-item[1], item[0]))[:n]


def _assert_matches_oracle(predictor: NgramPredictor, context: str, n: int) -> None:
    assert predictor.predict_with_scores(context, n) == _brute_force_predictions(
        predictor, context, n
    )


def test_dense_equal_frequency_tail_has_deterministic_lexical_ties():
    predictor = _blank_predictor()
    tail = {f"tail{index:05d}": 1 for index in reversed(range(12_000))}
    base = {"common": 100, "frequent": 90, **tail}
    _seed_unigrams(predictor, base)

    _assert_matches_oracle(predictor, "unknown ", 12)
    assert predictor.predict("unknown ", 12) == [
        "common",
        "frequent",
        *[f"tail{index:05d}" for index in range(10)],
    ]


def test_empty_context_top_unigrams_use_the_same_lexical_tie_rule():
    predictor = _blank_predictor()
    _seed_unigrams(
        predictor,
        {"zulu": 10, "alpha": 10, "middle": 10, "lower": 2},
        {"personal": 20},
    )

    _assert_matches_oracle(predictor, "", 5)
    assert predictor.predict("", 5) == ["personal", "alpha", "middle", "zulu", "lower"]


def test_corpus_prior_uses_raw_scores_and_lexical_ties_without_context():
    predictor = _blank_predictor()
    _seed_unigrams(predictor, {"zulu": 5, "alpha": 5})
    _seed_corpus_prior(predictor, {"middle": 50, "zulu": 10})

    _assert_matches_oracle(predictor, "", 4)
    assert predictor.predict("", 4) == ["zulu", "alpha", "middle"]


def test_base_pruning_keeps_apostrophe_optional_base_matches():
    predictor = _blank_predictor()
    _seed_unigrams(
        predictor,
        {"i'll": 100, "illness": 80, "illusion": 70, "alpha": 10},
    )

    _assert_matches_oracle(predictor, "ill", 1)
    assert predictor.predict("ill", 1) == ["i'll"]


def test_corpus_prior_candidates_remain_after_base_pruning():
    predictor = _blank_predictor()
    _seed_unigrams(predictor, {f"base{index:03d}": 100 - index for index in range(50)})
    _seed_corpus_prior(predictor, {"corpusonly": 20, "corpusword": 10})

    for context in ("missing ", "corpus"):
        _assert_matches_oracle(predictor, context, 5)


@pytest.mark.parametrize("alpha", [0.0, 0.25, 0.7, 1.0])
@pytest.mark.parametrize("n", [1, 3, 9, 300])
def test_sparse_user_and_context_candidates_match_brute_force(alpha: float, n: int):
    predictor = _blank_predictor()
    base = {f"base{index:03d}": 300 - index for index in range(200)}
    user = {"personalrare": 7, "base199": 4}
    _seed_unigrams(predictor, base, user)
    _seed_context(
        predictor,
        "prior",
        base={"contextonly": 20, "base150": 5},
        user={"personalrare": 3.5, "usercontextonly": 1.5},
    )
    _seed_context(
        predictor,
        "first prior",
        base={"trigramonly": 25, "base175": 4},
        user={"personalrare": 2.0},
        trigram=True,
    )
    predictor.personal_weight = alpha

    for context in ("prior ", "first prior ", "prior base1", "missing "):
        _assert_matches_oracle(predictor, context, n)


@pytest.mark.parametrize("alpha", [-0.25, 1.25])
def test_unusual_personal_weights_fall_back_without_changing_results(alpha: float):
    predictor = _blank_predictor()
    _seed_unigrams(
        predictor,
        {"highest": 100, "middle": 50, "lowest": 1},
        {"personal": 7, "middle": 2},
    )
    _seed_context(predictor, "prior", base={"contextonly": 3}, user={"personal": 1.0})
    predictor.personal_weight = alpha

    for context in ("prior ", "missing ", "prior m"):
        _assert_matches_oracle(predictor, context, 10)


def test_prefix_ranges_cover_empty_and_maximum_unicode_character():
    predictor = _blank_predictor(broad_unicode=True)
    base = {
        MAX_UNICODE: 20,
        MAX_UNICODE + "a": 19,
        MAX_UNICODE + MAX_UNICODE: 18,
        "a" + MAX_UNICODE: 17,
        "a" + MAX_UNICODE + "tail": 16,
        "alpha": 15,
        "a\uffff": 14,
    }
    _seed_unigrams(predictor, base)

    for context in ("", "prior ", "a", "a" + MAX_UNICODE, MAX_UNICODE):
        for n in (0, 1, 3, 20):
            _assert_matches_oracle(predictor, context, n)


def test_direct_base_key_and_count_mutations_are_visible_after_a_warm_lookup():
    predictor = _blank_predictor()
    _seed_unigrams(predictor, {"alpha": 30, "beta": 20, "gamma": 10})
    _assert_matches_oracle(predictor, "prior ", 3)

    predictor._base_unigrams["aardvark"] = 100
    predictor._base_total += 100
    predictor.unigrams["aardvark"] = 100
    predictor.total_words += 100
    _assert_matches_oracle(predictor, "prior ", 4)

    old_alpha = predictor._base_unigrams["alpha"]
    predictor._base_unigrams["alpha"] = 200
    predictor._base_total += 200 - old_alpha
    predictor.unigrams["alpha"] += 200 - old_alpha
    predictor.total_words += 200 - old_alpha
    _assert_matches_oracle(predictor, "prior ", 4)

    removed = predictor._base_unigrams.pop("aardvark")
    predictor._base_total -= removed
    predictor.unigrams.pop("aardvark")
    predictor.total_words -= removed
    _assert_matches_oracle(predictor, "prior ", 4)

    predictor._base_unigrams = {"plainmap": 40, "replacement": 35}
    predictor._base_total = 75
    predictor.unigrams.clear()
    predictor.unigrams.update(predictor._base_unigrams)
    predictor.total_words = 75
    _assert_matches_oracle(predictor, "prior ", 4)


def test_mapping_union_mutations_refresh_warm_predictions():
    predictor = _blank_predictor()
    _seed_unigrams(predictor, {"alpha": 30, "beta": 20, "gamma": 10})
    assert predictor.predict("prior ", 2) == ["alpha", "beta"]

    predictor._base_unigrams |= {"newword": 100}
    predictor._base_total += 100
    predictor.unigrams["newword"] = 100
    predictor.total_words += 100
    _assert_matches_oracle(predictor, "prior ", 4)
    assert predictor.predict("prior ", 2) == ["newword", "alpha"]

    old_beta = predictor._base_unigrams["beta"]
    predictor._base_unigrams |= {"beta": 200}
    predictor._base_total += 200 - old_beta
    predictor.unigrams["beta"] += 200 - old_beta
    predictor.total_words += 200 - old_beta
    _assert_matches_oracle(predictor, "prior ", 4)
    assert predictor.predict("prior ", 2) == ["beta", "newword"]


def test_missing_base_pop_preserves_warm_predictions():
    predictor = _blank_predictor()
    _seed_unigrams(predictor, {"alpha": 30, "beta": 20, "gamma": 10})
    expected = predictor.predict_with_scores("prior ", 3)
    before = dict(predictor._base_unigrams)

    with pytest.raises(KeyError):
        predictor._base_unigrams.pop("missing")
    assert dict(predictor._base_unigrams) == before
    assert predictor.predict_with_scores("prior ", 3) == expected

    marker = object()
    assert predictor._base_unigrams.pop("missing", marker) is marker
    assert dict(predictor._base_unigrams) == before
    assert predictor.predict_with_scores("prior ", 3) == expected


def test_randomized_small_models_match_brute_force_exactly():
    for seed in range(30):
        rng = random.Random(seed)
        predictor = _blank_predictor()
        base = {f"word{index:03d}": rng.randint(1, 30) for index in range(rng.randint(10, 80))}
        user_words = rng.sample(sorted(base), rng.randint(0, min(8, len(base))))
        user = {word: rng.randint(1, 10) for word in user_words}
        user[f"personal{seed:02d}"] = rng.randint(1, 10)
        _seed_unigrams(predictor, base, user)

        context_words = rng.sample(sorted(base), min(6, len(base)))
        _seed_context(
            predictor,
            "prior",
            base={word: rng.randint(1, 15) for word in context_words[:4]},
            user={word: float(rng.randint(1, 5)) for word in context_words[2:]},
        )
        _seed_context(
            predictor,
            "first prior",
            base={context_words[0]: 8, f"trigram{seed:02d}": 5},
            user={context_words[-1]: 2.5},
            trigram=True,
        )
        predictor.personal_weight = rng.choice([0.0, 0.2, 0.7, 1.0])

        for context in ("", "prior ", "first prior ", "prior word0", "missing "):
            for n in (1, 4, 15):
                _assert_matches_oracle(predictor, context, n)


def test_learning_after_a_warm_lookup_keeps_sparse_candidates_complete():
    predictor = _blank_predictor()
    _seed_unigrams(predictor, {"alpha": 30, "beta": 20, "gamma": 10})
    _assert_matches_oracle(predictor, "alpha ", 5)

    predictor.learn("alpha alpha beta")
    for _ in range(3):
        predictor.learn("novelword")

    for context in ("alpha ", "alpha alpha ", "nov", "missing "):
        _assert_matches_oracle(predictor, context, 10)


def test_clear_user_data_discards_warm_candidate_state():
    predictor = _blank_predictor()
    _seed_unigrams(predictor, {"alpha": 30, "beta": 20})
    predictor.learn("alpha beta")
    assert predictor.predict("prior ", 5)

    predictor.clear_user_data()

    _assert_matches_oracle(predictor, "prior ", 5)
    assert predictor.predict("prior ", 5) == []


def test_save_load_rebuilds_candidates_from_loaded_user_state(tmp_path: Path):
    path = tmp_path / "ngram.json"
    source = _blank_predictor()
    base = {"alpha": 30, "beta": 20, "gamma": 10}
    _seed_unigrams(source, base)
    source.learn("alpha beta alpha")
    for _ in range(3):
        source.learn("novelword")
    source.save(path)

    loaded = _blank_predictor()
    _seed_unigrams(loaded, base)
    _assert_matches_oracle(loaded, "alpha ", 10)
    loaded.load(path)

    for context in ("", "alpha ", "alpha beta ", "nov", "missing "):
        _assert_matches_oracle(loaded, context, 10)
