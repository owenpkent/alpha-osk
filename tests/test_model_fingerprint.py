"""Model-correctness fingerprints for the prediction engine.

Borrowed from DasherCore's ``test_ppm_serialization`` / ``test_deterministic`` /
``test_lm_correctness``: train a model, serialise it, and treat the bytes as the
fingerprint. Existing suites check that a reloaded model *predicts* the same
(``test_ngram_predictor.py::test_save_load_round_trip_predictions_match``, the
property suite's reload check, PPM's sum-to-one). These check the state itself:

* **Determinism**: the same training gives byte-identical files and identical
  predictions. The saved JSON carries no timestamps; ``atomic_write_json`` sorts
  keys, so raw bytes are compared with no canonicalisation.
* **Idempotent save**: save, load, save again is a fixed point.
* **No-op training**: input the model has nothing to learn from leaves the
  saved state untouched.
* **Reset**: ``clear_user_data()`` returns to the fresh-install fingerprint.
* **Behaviour snapshot**: top-N predictions of a fresh ``HybridPredictor`` for
  fixed probes are pinned in ``tests/data/prediction_snapshot.json`` (words
  only, no scores, so no float formatting). An engine change then shows up as
  a reviewable diff in the PR. To regenerate after an intended change::

      ALPHA_OSK_UPDATE_SNAPSHOT=1 python -m pytest tests/test_model_fingerprint.py -k snapshot

  and commit the updated JSON, calling the change out in the PR description.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List

import pytest

PySide6 = pytest.importorskip("PySide6")

from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402
from src.prediction.ngram_predictor import NgramPredictor  # noqa: E402
from src.prediction.ppm_predictor import PPMPredictor  # noqa: E402

TRAINING_TEXT = (
    "the quick brown fox jumps over the lazy dog and the dog barks at the fox "
    "i want to go to the store and i want to buy some bread"
)

NGRAM_PROBES = ["the ", "i want ", "and the ", "to ", "the qu", "i wa", "dog "]
PPM_PROBES = ["the ", "i want", "the q", "fox ", "", "zzzz"]

SNAPSHOT_PATH = Path(__file__).parent / "data" / "prediction_snapshot.json"
SNAPSHOT_PROBES = [
    # next word after a short context
    "I ",
    "I want ",
    "I want to ",
    "thank ",
    "how are ",
    "good ",
    "see you ",
    "what is the ",
    "in the ",
    "it is ",
    "do you ",
    "let me ",
    "one ",
    "I am going ",
    # mid-word prefixes
    "th",
    "wor",
    "I wan",
    "hel",
    "prob",
    "beca",
    "thank y",
    "going to th",
    "appoint",
    "ques",
    # slips and transpositions
    "teh",
    "wnat",
    "recieve",
    "becuase",
    "I wamt",
    "thnk",
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ngram_save(model: NgramPredictor, path: Path) -> str:
    model.save(path)
    return _digest(path)


def _ppm_save(model: PPMPredictor, path: Path) -> str:
    model.save(path)
    return _digest(path)


def _ppm_distributions(model: PPMPredictor) -> Dict[str, Dict[str, float]]:
    return {
        ctx: {c: round(p, 12) for c, p in model.get_probabilities(ctx).items()}
        for ctx in PPM_PROBES
    }


@pytest.fixture
def fresh_ngram_digest(tmp_path: Path) -> str:
    return _ngram_save(NgramPredictor(), tmp_path / "fresh_ngram.json")


@pytest.fixture
def fresh_ppm_digest(tmp_path: Path) -> str:
    return _ppm_save(PPMPredictor(), tmp_path / "fresh_ppm.json")


class TestTrainingIsDeterministic:
    """Two instances fed the same text must be indistinguishable. A set iteration
    leaking into a table, or a clock into the file, shows up as a byte diff here
    long before it shows up as a flaky ranking."""

    def test_ngram_files_are_byte_identical(self, tmp_path: Path) -> None:
        a, b = NgramPredictor(), NgramPredictor()
        a.learn(TRAINING_TEXT)
        b.learn(TRAINING_TEXT)
        assert _ngram_save(a, tmp_path / "a.json") == _ngram_save(b, tmp_path / "b.json")

    def test_ngram_predictions_are_identical(self) -> None:
        a, b = NgramPredictor(), NgramPredictor()
        a.learn(TRAINING_TEXT)
        b.learn(TRAINING_TEXT)
        for probe in NGRAM_PROBES:
            assert a.predict_with_scores(probe, 5) == b.predict_with_scores(probe, 5), probe

    def test_ppm_files_are_byte_identical(self, tmp_path: Path) -> None:
        a, b = PPMPredictor(), PPMPredictor()
        a.train(TRAINING_TEXT)
        b.train(TRAINING_TEXT)
        assert _ppm_save(a, tmp_path / "a.json") == _ppm_save(b, tmp_path / "b.json")
        assert _ppm_distributions(a) == _ppm_distributions(b)

    def test_different_text_gives_a_different_fingerprint(self, tmp_path: Path) -> None:
        """Inverse: a digest that ignored the training would pass every test above."""
        a, b = NgramPredictor(), NgramPredictor()
        a.learn(TRAINING_TEXT)
        b.learn(TRAINING_TEXT + " zebra")
        assert _ngram_save(a, tmp_path / "a.json") != _ngram_save(b, tmp_path / "b.json")
        p, q = PPMPredictor(), PPMPredictor()
        p.train(TRAINING_TEXT)
        q.train(TRAINING_TEXT + " zebra")
        assert _ppm_save(p, tmp_path / "p.json") != _ppm_save(q, tmp_path / "q.json")

    def test_hybrid_model_files_are_byte_identical(self, tmp_path: Path) -> None:
        dirs = []
        for name in ("one", "two"):
            model_dir = tmp_path / name
            model_dir.mkdir()
            hybrid = HybridPredictor(model_dir=model_dir, enable_llm=False)
            hybrid.learn(TRAINING_TEXT)
            hybrid.save()
            dirs.append(model_dir)
        for fname in ("ngram_model.json", "ppm_model.json"):
            assert _digest(dirs[0] / fname) == _digest(dirs[1] / fname), fname


class TestSaveIsIdempotent:
    """save -> load -> save is a fixed point: a load that dropped or renormalised
    anything would make every launch rewrite a slightly different model."""

    def test_ngram(self, tmp_path: Path) -> None:
        model = NgramPredictor()
        model.learn(TRAINING_TEXT)
        first = _ngram_save(model, tmp_path / "one.json")
        reloaded = NgramPredictor()
        reloaded.load(tmp_path / "one.json")
        assert _ngram_save(reloaded, tmp_path / "two.json") == first

    def test_ppm(self, tmp_path: Path) -> None:
        model = PPMPredictor()
        model.train(TRAINING_TEXT)
        first = _ppm_save(model, tmp_path / "one.json")
        reloaded = PPMPredictor()
        reloaded.load(tmp_path / "one.json")
        assert _ppm_save(reloaded, tmp_path / "two.json") == first

    def test_a_trained_model_is_not_the_fresh_fingerprint(
        self, tmp_path: Path, fresh_ngram_digest: str, fresh_ppm_digest: str
    ) -> None:
        """Inverse: the fixed point must not be the trivial empty one."""
        ngram = NgramPredictor()
        ngram.learn(TRAINING_TEXT)
        assert _ngram_save(ngram, tmp_path / "n.json") != fresh_ngram_digest
        ppm = PPMPredictor()
        ppm.train(TRAINING_TEXT)
        assert _ppm_save(ppm, tmp_path / "p.json") != fresh_ppm_digest


class TestPpmRoundTripPredictions:
    """The n-gram round trip is covered elsewhere; this is the PPM half. Compares
    whole character distributions, not just the top few, on several contexts."""

    def test_distributions_survive_a_reload(self, tmp_path: Path) -> None:
        model = PPMPredictor()
        model.train(TRAINING_TEXT)
        before = _ppm_distributions(model)
        model.save(tmp_path / "ppm.json")
        reloaded = PPMPredictor()
        reloaded.load(tmp_path / "ppm.json")
        assert _ppm_distributions(reloaded) == before
        assert reloaded.total_chars == model.total_chars

    def test_next_chars_and_words_survive_a_reload(self, tmp_path: Path) -> None:
        model = PPMPredictor()
        model.train(TRAINING_TEXT)
        model.save(tmp_path / "ppm.json")
        reloaded = PPMPredictor(model_path=tmp_path / "ppm.json")
        # Ranked over the whole alphabet and compared as score -> set of chars:
        # tied characters come out in set-iteration order (``alphabet`` is a
        # ``set``, rebuilt on load, and string hashing is salted per process), so
        # the order within a tie is not part of the contract. Comparing it made
        # this test fail about one run in three.
        everything = len(model.alphabet)

        def by_score(m: PPMPredictor, ctx: str) -> Dict[float, frozenset]:
            ranked: Dict[float, set] = {}
            for char, score in m.predict_next_chars(ctx, everything):
                ranked.setdefault(round(score, 12), set()).add(char)
            return {k: frozenset(v) for k, v in ranked.items()}

        for ctx in PPM_PROBES:
            assert by_score(reloaded, ctx) == by_score(model, ctx), ctx

        def words(m: PPMPredictor) -> Dict[str, float]:
            return {w: round(s, 12) for w, s in m.predict_word("the ", "q", 50)}

        assert words(reloaded) == words(model)

    def test_a_reload_of_an_untrained_model_differs(self, tmp_path: Path) -> None:
        """Inverse: distributions must actually depend on the file."""
        model = PPMPredictor()
        model.train(TRAINING_TEXT)
        model.save(tmp_path / "ppm.json")
        assert _ppm_distributions(PPMPredictor()) != _ppm_distributions(model)


# Input with nothing to learn from. "́" is a combining acute, the rest are
# an emoji, and two supplementary-plane letters (Deseret).
_NOTHING_TO_LEARN = {
    "empty": "",
    "whitespace": "   \n\t  \r\n ",
    "emoji": "\U0001f600\U0001f600 \U0001f680\U0001f680",
    "combining_marks": "́́ ̂̂",
    "non_bmp": "\U00010400\U00010401\U00010402",
}


class TestNothingToLearnLeavesNoTrace:
    """Learning from text with no words in it must not move the saved state, or a
    stray emoji or a held Enter would silently reshape a user's model."""

    @pytest.mark.parametrize("name", list(_NOTHING_TO_LEARN))
    def test_ngram_state_is_unchanged(
        self, name: str, tmp_path: Path, fresh_ngram_digest: str
    ) -> None:
        model = NgramPredictor()
        model.learn(_NOTHING_TO_LEARN[name])
        assert _ngram_save(model, tmp_path / "n.json") == fresh_ngram_digest

    def test_ppm_empty_string_is_unchanged(self, tmp_path: Path, fresh_ppm_digest: str) -> None:
        model = PPMPredictor()
        model.train("")
        assert _ppm_save(model, tmp_path / "p.json") == fresh_ppm_digest

    @pytest.mark.parametrize(
        "name",
        [
            pytest.param(
                n,
                marks=pytest.mark.xfail(
                    strict=True,
                    reason=(
                        "PPMPredictor._normalize maps every out-of-alphabet character to "
                        "a space and train() then counts the spaces, so text with no "
                        "letters still adds ' ' -> ' ' statistics and bumps total_chars "
                        "(PPMPredictor().train('   ') -> total_chars 3). Probably "
                        "unintended; harmless today because PPM is out of the merge."
                    ),
                ),
            )
            for n in ("whitespace", "emoji", "combining_marks", "non_bmp")
        ],
    )
    def test_ppm_state_is_unchanged(self, name: str, tmp_path: Path, fresh_ppm_digest: str) -> None:
        model = PPMPredictor()
        model.train(_NOTHING_TO_LEARN[name])
        assert _ppm_save(model, tmp_path / "p.json") == fresh_ppm_digest

    def test_real_text_does_move_the_state(self, tmp_path: Path, fresh_ngram_digest: str) -> None:
        """Inverse: the comparison above can fail."""
        model = NgramPredictor()
        model.learn("zebra crossing")
        assert _ngram_save(model, tmp_path / "n.json") != fresh_ngram_digest


class TestResetReturnsToFreshInstall:
    def test_ngram_clear_user_data(self, tmp_path: Path, fresh_ngram_digest: str) -> None:
        model = NgramPredictor()
        model.learn(TRAINING_TEXT)
        assert _ngram_save(model, tmp_path / "before.json") != fresh_ngram_digest
        model.clear_user_data()
        assert _ngram_save(model, tmp_path / "after.json") == fresh_ngram_digest

    def test_ngram_predictions_return_to_fresh(self) -> None:
        fresh = NgramPredictor()
        model = NgramPredictor()
        for _ in range(5):
            model.learn("the zebra zebra zebra")
        model.clear_user_data()
        for probe in NGRAM_PROBES:
            assert model.predict_with_scores(probe, 5) == fresh.predict_with_scores(probe, 5)

    def test_hybrid_clear_user_data_restores_the_ngram_file(self, tmp_path: Path) -> None:
        fresh_dir = tmp_path / "fresh"
        used_dir = tmp_path / "used"
        fresh_dir.mkdir()
        used_dir.mkdir()
        fresh = HybridPredictor(model_dir=fresh_dir, enable_llm=False)
        fresh.save()
        used = HybridPredictor(model_dir=used_dir, enable_llm=False)
        used.learn(TRAINING_TEXT)
        used.save()
        assert _digest(used_dir / "ngram_model.json") != _digest(fresh_dir / "ngram_model.json")
        used.clear_user_data()
        used.save()
        assert _digest(used_dir / "ngram_model.json") == _digest(fresh_dir / "ngram_model.json")


class TestPredictionSnapshot:
    """Pins the shipped engine's top-5 words for fixed probes. This is a tripwire,
    not a freeze: an intended engine change regenerates the file (see the module
    docstring) and the diff is what a reviewer reads. Words only, in rank order:
    no scores, so nothing depends on float formatting or platform."""

    @staticmethod
    def _current(tmp_path: Path) -> Dict[str, List[str]]:
        model_dir = tmp_path / "snapshot_models"
        model_dir.mkdir(parents=True)
        hybrid = HybridPredictor(model_dir=model_dir, enable_llm=False)
        return {probe: list(hybrid.predict(probe, n=5)) for probe in SNAPSHOT_PROBES}

    def test_matches_the_committed_snapshot(self, tmp_path: Path) -> None:
        current = self._current(tmp_path)
        if os.environ.get("ALPHA_OSK_UPDATE_SNAPSHOT") == "1":
            SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
            SNAPSHOT_PATH.write_text(
                json.dumps(current, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            pytest.skip(
                "snapshot regenerated; review and commit tests/data/prediction_snapshot.json"
            )
        assert SNAPSHOT_PATH.exists(), (
            "no snapshot; run with ALPHA_OSK_UPDATE_SNAPSHOT=1 to create it"
        )
        expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        diffs = [
            f"  {probe!r}: {expected.get(probe)} -> {current.get(probe)}"
            for probe in sorted(set(expected) | set(current))
            if expected.get(probe) != current.get(probe)
        ]
        assert not diffs, (
            "prediction behaviour drifted (regenerate with ALPHA_OSK_UPDATE_SNAPSHOT=1 if "
            "intended, and call it out in the PR):\n" + "\n".join(diffs)
        )

    def test_the_snapshot_is_not_vacuous(self) -> None:
        """Inverse: a snapshot of empty lists would pass any engine."""
        if not SNAPSHOT_PATH.exists():
            pytest.skip("snapshot not generated yet")
        expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        assert set(expected) == set(SNAPSHOT_PROBES)
        assert sum(1 for words in expected.values() if len(words) >= 3) >= len(SNAPSHOT_PROBES) - 3

    def test_the_prediction_run_is_repeatable(self, tmp_path: Path) -> None:
        assert self._current(tmp_path / "a") == self._current(tmp_path / "b")
