"""The generated base context seeds, and the sentence-start context they enable.

Every positive case is paired with the near-miss it must leave alone, because
both halves of this feature are easy to get nearly right: a loader that
accepts the backoff section would corrupt the counts silently, and a
sentence-start rule that fires one word too late is invisible until someone
reads the pills.

See ``docs/architecture/NGRAM_SEEDS.md`` for the format and its provenance.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.prediction.ngram_predictor import SENTENCE_START, NgramPredictor

SEED_FILE = """\
# a generated header that must be ignored
\\data\\
order=2
contexts=3
edges=4

\\backoff:
how -0.779169
<s> -2.33381

\\seeds:
how much 14
how do 10
<s> i 30
wheelchair bound 35
"""

TRIGRAM_FILE = """\
\\data\\
order=3
contexts=1
edges=2

\\backoff:
how are -0.5

\\seeds:
how are you 40
how are things 6
"""


@pytest.fixture
def predictor() -> NgramPredictor:
    """A predictor with no data files loaded, so only the seeds under test show."""
    return NgramPredictor()


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


class TestTheSeedLoader:
    def test_counts_land_in_the_bigram_table(self, predictor: NgramPredictor, tmp_path: Path):
        assert predictor.load_seed_ngrams(_write(tmp_path, "s.txt", SEED_FILE))
        assert predictor.bigrams["how"]["much"] == 14
        assert predictor.bigrams["how"]["do"] == 10
        assert predictor.bigrams["wheelchair"]["bound"] == 35

    def test_the_backoff_section_is_not_read_as_counts(
        self, predictor: NgramPredictor, tmp_path: Path
    ):
        """The backoff rows are ``<context> <weight>``, two fields where a seed
        row has three, so a loader that ignored sections would still skip them
        by arity. This asserts the *effect*: no phantom continuation appears."""
        predictor.load_seed_ngrams(_write(tmp_path, "s.txt", SEED_FILE))
        assert set(predictor.bigrams["how"]) == {"much", "do"}
        assert "-0.779169" not in predictor.bigrams
        assert all(not k.startswith("-") for k in predictor.bigrams)

    def test_an_order_three_file_lands_in_the_trigram_table(
        self, predictor: NgramPredictor, tmp_path: Path
    ):
        assert predictor.load_seed_ngrams(_write(tmp_path, "t.txt", TRIGRAM_FILE))
        assert predictor.trigrams["how are"]["you"] == 40
        assert predictor.trigrams["how are"]["things"] == 6
        # A 2-word context must not be flattened into the bigram table.
        assert "how are" not in predictor.bigrams

    def test_seeds_add_to_curated_pairs_rather_than_replacing_them(
        self, predictor: NgramPredictor, tmp_path: Path
    ):
        predictor.bigrams["how"]["do"] += 50  # as load_common_bigrams would
        predictor.load_seed_ngrams(_write(tmp_path, "s.txt", SEED_FILE))
        assert predictor.bigrams["how"]["do"] == 60

    def test_a_missing_file_is_not_an_error(self, predictor: NgramPredictor, tmp_path: Path):
        assert predictor.load_seed_ngrams(tmp_path / "nope.txt") is False
        assert not predictor.bigrams

    def test_malformed_rows_are_skipped_one_at_a_time(
        self, predictor: NgramPredictor, tmp_path: Path
    ):
        body = "\\data\\\norder=2\n\n\\seeds:\nhow much notanumber\nhow do 10\nragged\nhow is 0\n"
        assert predictor.load_seed_ngrams(_write(tmp_path, "s.txt", body))
        assert predictor.bigrams["how"] == {"do": 10}


class TestSentenceStartContext:
    """The ``<s>`` row conditions the first word of a sentence."""

    @pytest.fixture
    def seeded(self, tmp_path: Path) -> NgramPredictor:
        p = NgramPredictor()
        p.load_seed_ngrams(_write(tmp_path, "s.txt", SEED_FILE))
        # Give the unigram table a different favourite, so a prediction that
        # came from the <s> row is distinguishable from one that did not.
        for _ in range(400):
            p.learn("zebra")
        return p

    def test_the_key_cannot_be_produced_by_typing(self, seeded: NgramPredictor):
        """``<s>`` must be unreachable by any text a user could enter, or a
        typed word could collide with the sentence-start row."""
        for text in ("<s>", "< s >", "s", "sentence <s> start"):
            assert SENTENCE_START not in seeded._tokenize(text)

    def test_an_empty_context_predicts_sentence_openers(self, seeded: NgramPredictor):
        seeded.use_sentence_start_context = True
        assert "i" in seeded.predict("", 5)

    def test_a_full_stop_starts_a_new_sentence(self, seeded: NgramPredictor):
        """After a terminator the previous sentence's last word must not be
        what the next word is conditioned on."""
        seeded.use_sentence_start_context = True
        for ended in ("wheelchair. ", "wheelchair! ", "wheelchair? "):
            assert "i" in seeded.predict(ended, 5), ended
            assert "bound" not in seeded.predict(ended, 5), ended

    def test_mid_sentence_is_left_alone(self, seeded: NgramPredictor):
        """The near-miss: the same word without a terminator still conditions
        on that word, which is the whole of ordinary next-word prediction."""
        seeded.use_sentence_start_context = True
        assert "bound" in seeded.predict("wheelchair ", 5)

    def test_a_partial_word_is_left_alone(self, seeded: NgramPredictor):
        """No trailing space means the user is mid-word, so this is a
        completion, not the start of anything."""
        seeded.use_sentence_start_context = True
        assert all(w.startswith("wh") for w in seeded.predict("wh", 5))

    def test_the_flag_restores_the_previous_behaviour(self, seeded: NgramPredictor):
        seeded.use_sentence_start_context = False
        assert seeded.predict("", 5) == seeded._top_unigrams(5)

    def test_it_is_inert_without_a_sentence_start_row(self):
        """The flag defaults on, so a model with no seeds must be unaffected."""
        p = NgramPredictor()
        for _ in range(400):
            p.learn("zebra")
        p.use_sentence_start_context = True
        assert p.predict("", 5) == p._top_unigrams(5)
