"""The base / user split of the n-gram context tables.

Before the split, bigrams and trigrams were one merged table each.  Every
launch re-added the shipped seeds on top of the persisted table, recency
decay scaled every bigram (seeds included) down to a floor of 1 and never
touched trigrams, and a phrase the user typed competed against seed mass
that grew by 50 per launch.  Each test here pins one of the behaviours
that replaced that, and each positive case is paired with the near-miss
it must leave alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.prediction.hybrid_predictor import HybridPredictor
from src.prediction.ngram_predictor import NgramPredictor

FILLER = "please let me know when you have a chance to look at this"


def _seed_file(path: Path, prefix: str, successors: int) -> Path:
    path.write_text("\n".join(f"{prefix} seedword{i}" for i in range(successors)) + "\n")
    return path


class TestSeedsSurviveTheSession:
    def test_seed_bigrams_are_not_decayed_by_ordinary_typing(self):
        p = NgramPredictor()
        p.load_common_bigrams()
        before = dict(p.bigrams["i"])
        for _ in range(400):
            p.learn(FILLER)
        assert dict(p.bigrams["i"]) == before

    def test_seed_trigrams_are_not_decayed_either(self):
        p = NgramPredictor()
        p.load_common_trigrams()
        before = dict(p.trigrams["i want"])
        for _ in range(400):
            p.learn(FILLER)
        assert dict(p.trigrams["i want"]) == before

    def test_the_users_own_pairs_do_decay_and_are_eventually_forgotten(self):
        p = NgramPredictor()
        p.learn("alpha beta gamma")
        assert p.bigrams["alpha"]["beta"] == 1
        assert p.trigrams["alpha beta"]["gamma"] == 1
        for _ in range(80):
            p._apply_decay()
        assert "beta" not in p.bigrams.get("alpha", {})
        assert "gamma" not in p.trigrams.get("alpha beta", {})
        assert "alpha" not in p._user_bigrams
        assert "alpha beta" not in p._user_trigrams

    def test_a_single_decay_tick_does_not_snap_a_pair_to_zero(self):
        # The int table floored at 1, so `>= 1` after one tick was the old
        # guarantee; the float table keeps it without the floor.
        p = NgramPredictor()
        p.learn("alpha beta")
        p._apply_decay()
        assert p.bigrams["alpha"]["beta"] == 1
        assert p._user_bigrams["alpha"]["beta"] == pytest.approx(0.95)


class TestTheMergedViewStaysHonest:
    def test_merged_never_drops_below_the_rounded_user_share(self):
        p = NgramPredictor()
        p.load_common_bigrams()
        for i in range(120):
            p.learn(f"{FILLER} i want {'coffee' if i % 2 else 'tea'}")
            p.reinforce_context("i want", "coffee")
            if i % 7 == 0:
                p._apply_decay()
        for prefix, row in p._user_bigrams.items():
            for word, count in row.items():
                assert p.bigrams[prefix][word] >= int(count + 0.5)
        for prefix, row in p._user_trigrams.items():
            for word, count in row.items():
                assert p.trigrams[prefix][word] >= int(count + 0.5)

    def test_corpus_context_goes_to_the_base_share(self):
        p = NgramPredictor()
        p.learn_corpus_context("alpha beta gamma")
        assert p.bigrams["alpha"]["beta"] == 1
        assert p.trigrams["alpha beta"]["gamma"] == 1
        assert not p._user_bigrams and not p._user_trigrams
        # and it left the unigram side alone
        assert p.user_vocab.get("alpha", 0) == 0

    def test_load_corpus_routes_context_to_base_but_unigrams_as_before(self):
        p = NgramPredictor()
        p.load_corpus("alpha beta gamma")
        assert p.bigrams["alpha"]["beta"] == 1
        assert not p._user_bigrams
        # unigram behaviour is deliberately unchanged by the split
        assert p.total_words > 0

    def test_reseeding_the_corpus_gates_rare_words_exactly_as_a_launch_does(self):
        # A launch learns the corpus through `learn(corpus=True)`, which
        # withholds an unknown word until its third sighting.  The reseed
        # after a Data Backup import linked every plausible word instead,
        # so a reload grew context edges a fresh start never has.
        rare = "zibbertrunk flombasso"
        launched, reloaded = NgramPredictor(), NgramPredictor()
        launched.load_corpus(rare)
        reloaded.learn_corpus_context(rare)
        assert not launched.bigrams.get("zibbertrunk")
        assert not reloaded.bigrams.get("zibbertrunk")
        # and the inverse: the third sighting promotes on both paths alike
        launched, reloaded = NgramPredictor(), NgramPredictor()
        launched.load_corpus(" ".join([rare] * 3))
        reloaded.learn_corpus_context(" ".join([rare] * 3))
        assert launched.bigrams["zibbertrunk"]["flombasso"] == 1
        assert reloaded.bigrams["zibbertrunk"]["flombasso"] == 1


class TestScoringTrustsTheUserInProportion:
    def test_with_no_user_evidence_the_score_is_the_old_normalised_row(self):
        p = NgramPredictor()
        merged = {"a": 30, "b": 10}
        assert p._context_probs(merged, None) == {"a": 0.75, "b": 0.25}
        assert p._context_probs(merged, {}) == {"a": 0.75, "b": 0.25}

    def test_one_sighting_after_a_new_word_is_not_a_certainty(self):
        p = NgramPredictor()
        p.learn("zebra crossing")
        probs = p._context_probs(p.bigrams["zebra"], p._user_bigrams["zebra"])
        assert probs["crossing"] == pytest.approx(1 / (1 + p._CONTEXT_PRIOR_FLOOR))

    def test_a_lone_user_pair_keeps_scoring_until_it_is_forgotten(self):
        # Decay drops the merged count the moment it rounds to zero (14
        # ticks for a single typing) while the user share lives on down
        # to _USER_CONTEXT_MIN (45 ticks).  For a prefix with no base
        # evidence that used to leave an empty merged row, and an early
        # return on it scored the pair at nothing for two thirds of its
        # remaining life.
        p = NgramPredictor()
        p.learn("zebra crossing")
        for _ in range(20):
            p._apply_decay()
        share = p._user_bigrams["zebra"]["crossing"]
        assert share > p._USER_CONTEXT_MIN
        assert not p.bigrams.get("zebra")
        probs = p._context_probs(p.bigrams.get("zebra"), p._user_bigrams.get("zebra"))
        assert probs["crossing"] == pytest.approx(share / (share + p._CONTEXT_PRIOR_FLOOR))

    def test_and_scores_nothing_once_the_user_share_is_gone_too(self):
        p = NgramPredictor()
        p.learn("zebra crossing")
        for _ in range(80):
            p._apply_decay()
        assert p._context_probs(p.bigrams.get("zebra"), p._user_bigrams.get("zebra")) == {}

    def test_a_personal_phrase_surfaces_after_a_few_typings_after_a_common_word(self, tmp_path):
        p = NgramPredictor()
        p.load_common_bigrams(_seed_file(tmp_path / "bi.txt", "the", 40))
        assert "bus" not in [w for w, _ in p.predict_with_scores("the ", 5)]
        for _ in range(3):
            p.learn("the bus")
        assert "bus" in [w for w, _ in p.predict_with_scores("the ", 5)]

    def test_and_the_seed_ordering_underneath_is_intact_afterwards(self, tmp_path):
        p = NgramPredictor()
        p.load_common_bigrams(_seed_file(tmp_path / "bi.txt", "the", 40))
        before = dict(p.bigrams["the"])
        for _ in range(3):
            p.learn("the bus")
        after = {w: c for w, c in p.bigrams["the"].items() if w != "bus"}
        assert after == before

    def test_a_reinforced_pill_click_counts_as_user_evidence(self):
        p = NgramPredictor()
        p.reinforce_context("i want", "coffee")
        assert p._user_bigrams["want"]["coffee"] == 1.0
        assert p._user_trigrams["i want"]["coffee"] == 1.0
        assert p.bigrams["want"]["coffee"] == 1


class TestOnlyTheUserHalfIsPersisted:
    def test_seeds_are_not_written_and_do_not_accumulate_across_launches(self, tmp_path):
        path = tmp_path / "ngram.json"
        p = NgramPredictor()
        p.load_common_bigrams()
        p.load_common_trigrams()
        seed_bi, seed_tri = p.bigrams["i"]["want"], p.trigrams["i want"]["to"]
        p.learn("alpha beta")
        p.save(path)

        data = json.loads(path.read_text())
        assert "user_bigrams" in data and "user_trigrams" in data
        assert "bigrams" not in data and "trigrams" not in data
        assert "i" not in data["user_bigrams"]

        # a second launch: load the file, re-apply the seeds once
        q = NgramPredictor()
        q.load(path)
        assert q.bigrams.get("i", {}).get("want", 0) == 0
        q.load_common_bigrams()
        q.load_common_trigrams()
        assert q.bigrams["i"]["want"] == seed_bi
        assert q.trigrams["i want"]["to"] == seed_tri
        assert q.bigrams["alpha"]["beta"] == 1
        assert q._user_bigrams["alpha"]["beta"] == 1.0

    def test_a_pre_split_model_is_adopted_as_user_history(self, tmp_path):
        path = tmp_path / "legacy.json"
        path.write_text(
            json.dumps(
                {
                    "unigrams": {"want": 5, "to": 5},
                    "bigrams": {"i": {"want": 1037}},
                    "trigrams": {"i want": {"to": 3848}},
                }
            )
        )
        p = NgramPredictor()
        p.load(path)
        assert p.bigrams["i"]["want"] == 1037
        assert p._user_bigrams["i"]["want"] == 1037.0
        assert p.trigrams["i want"]["to"] == 3848
        # the next save writes the new shape
        out = tmp_path / "next.json"
        p.save(out)
        data = json.loads(out.read_text())
        assert data["user_bigrams"] == {"i": {"want": 1037.0}}
        assert "bigrams" not in data

    def test_malformed_entries_are_skipped_one_at_a_time(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text(
            json.dumps(
                {
                    "unigrams": {},
                    "user_bigrams": {
                        "good": {"pair": 2.5, "neg": -1, "nan": "x", "flag": True},
                        "list": ["not", "a", "row"],
                    },
                }
            )
        )
        p = NgramPredictor()
        p.load(path)
        assert dict(p._user_bigrams["good"]) == {"pair": 2.5}
        assert p.bigrams["good"]["pair"] == 3
        assert "list" not in p.bigrams

    def test_clear_user_data_empties_the_user_half(self):
        p = NgramPredictor()
        p.learn("alpha beta gamma")
        p.clear_user_data()
        assert not p._user_bigrams and not p._user_trigrams


class TestReloadReseedsTheBase:
    def test_reload_from_disk_brings_the_seeds_back(self, tmp_path):
        hp = HybridPredictor(model_dir=tmp_path, enable_llm=False)
        ng = hp._ngram
        seed_bi = ng.bigrams["i"]["want"]
        seed_tri = ng.trigrams["i want"]["to"]
        ng.learn("alpha beta")
        ng.save(tmp_path / "ngram_model.json")
        hp.reload_from_disk()
        assert hp._ngram.bigrams["i"]["want"] == seed_bi
        assert hp._ngram.trigrams["i want"]["to"] == seed_tri
        assert hp._ngram.bigrams["alpha"]["beta"] == 1
