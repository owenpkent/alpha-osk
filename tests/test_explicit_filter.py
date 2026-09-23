"""The explicit-content filter on prediction suggestions.

The filter decides what the prediction bar *volunteers*, and nothing else.
The words stay in the dictionary, stay typable character by character, and
stay learnable, which is what makes the setting reversible: turning it off
brings back everything, including whatever the user typed while it was on.

Every case below is paired with the near-miss it must not catch, because
the two ways this feature fails are opposite and a test for one direction
alone would pass against the other. Suppressing too little makes the
setting pointless; suppressing an ordinary word makes it the setting
everybody turns off and leaves off, which is the same outcome by a longer
route.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

PySide6 = pytest.importorskip("PySide6")

from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402
from src.prediction.language import ENGLISH  # noqa: E402

#: Ordinary words a substring match would wrongly catch. This is the
#: Scunthorpe set: every one of them contains a flagged stem somewhere
#: inside it, and every one must survive.
INNOCENT = (
    "class",
    "classes",
    "assess",
    "assessment",
    "assassin",
    "cocktail",
    "peacock",
    "cockpit",
    "dictionary",
    "analysis",
    "analyst",
    "shiitake",
    "passage",
    "bassoon",
    "harass",
    "embarrass",
    "massage",
    "spooky",
    "spooked",
    "japan",
    "japanese",
    "spicy",
    # Caught by the slur stems this filter used to carry ("spic", "jap",
    # "chink", "retard"), which are now exact words instead.
    "spiced",
    "spicier",
    "japes",
    "chinking",
    "retarder",
    "retarding",
    "mickey",
    "gypsy",
    "coonhound",
    "retardant",
    "negotiate",
    "title",
)


@pytest.fixture
def predictor(tmp_model_dir: Path) -> HybridPredictor:
    return HybridPredictor(model_dir=tmp_model_dir, enable_llm=False)


def _flagged(predictor: HybridPredictor) -> list[str]:
    return sorted(predictor._explicit_words)


class TestTheListItselfIsSane:
    def test_the_shipped_list_loaded(self, predictor: HybridPredictor) -> None:
        assert predictor.explicit_filter_available
        assert _flagged(predictor)

    def test_no_ordinary_word_is_flagged(self, predictor: HybridPredictor) -> None:
        """The Scunthorpe half, and the one that decides whether the
        setting survives contact with a user."""
        caught = [word for word in INNOCENT if word in predictor._explicit_words]
        assert not caught, f"ordinary words flagged: {caught}"

    def test_the_no_swears_wordlist_is_disjoint_from_the_flag_list(
        self, predictor: HybridPredictor
    ) -> None:
        """A stronger version of the above, against ground truth.

        The shipped frequency list is explicitly the no-swears build, so
        anything flagged that also appears there is a false positive by
        construction rather than by anyone's judgement.
        """
        path = ENGLISH.frequency
        assert path is not None
        clean = {
            line.strip().lower()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        assert not (predictor._explicit_words & clean)


class TestTheFilterSuppressesSuggestions:
    def test_a_flagged_word_is_not_offered_while_the_filter_is_on(
        self, predictor: HybridPredictor
    ) -> None:
        for word in _flagged(predictor):
            for length in (2, 3, 4):
                if length >= len(word):
                    continue
                offered = [w.lower() for w in predictor.predict(word[:length], n=8)]
                assert word not in offered, f"{word!r} offered for prefix {word[:length]!r}"

    def test_turning_it_off_brings_them_back(self, predictor: HybridPredictor) -> None:
        """The inverse, and it is not decoration.

        Without it, a filter that had stopped suggesting *anything*, or a
        flag list that had silently become the whole vocabulary, would
        satisfy the test above perfectly.
        """
        predictor.set_explicit_filter(False)

        offered_any = False
        for word in _flagged(predictor):
            for length in (2, 3, 4):
                if length >= len(word):
                    continue
                if word in [w.lower() for w in predictor.predict(word[:length], n=8)]:
                    offered_any = True
                    break
            if offered_any:
                break

        assert offered_any, "no flagged word came back with the filter off"

    def test_ordinary_suggestions_are_untouched(self, predictor: HybridPredictor) -> None:
        with_filter = predictor.predict("hel", n=5)
        predictor.set_explicit_filter(False)
        without_filter = predictor.predict("hel", n=5)

        assert with_filter == without_filter
        assert any(w.startswith("hel") for w in with_filter)


class TestPersonalVocabularyOutranksTheFilter:
    def test_a_word_the_user_has_typed_is_offered_again(self, predictor: HybridPredictor) -> None:
        """Once the word is in ``user_vocab`` the keyboard has direct
        evidence of the user's own register, and imposing one over it is
        exactly what this setting exists to let them switch off."""
        word = next(w for w in _flagged(predictor) if len(w) > 4)
        assert predictor._explicit_suppressed(word)

        for _ in range(4):  # past the three-sighting promotion gate
            predictor.learn(word)

        assert not predictor._explicit_suppressed(word)

    def test_one_typing_is_enough(self, predictor: HybridPredictor) -> None:
        """One is the threshold, and it is lower than it looks elsewhere.

        The three-sighting candidate gate applies to words the model does
        not already know. These are in the shipped dictionary, so ``learn``
        takes the known-word branch and the first typing reaches
        ``user_vocab``. That matches the setting as specified, "unless you
        have typed it before", and it is worth pinning because the
        neighbouring gate makes three the number a reader expects.
        """
        word = next(w for w in _flagged(predictor) if len(w) > 4)
        assert predictor._explicit_suppressed(word)

        predictor.learn(word)

        assert not predictor._explicit_suppressed(word)

    def test_never_typing_it_keeps_it_suppressed(self, predictor: HybridPredictor) -> None:
        """The near-miss for the pair above: being in the dictionary is not
        being in the user's history, so shipping a word must not exempt it."""
        for word in _flagged(predictor):
            assert word not in predictor._ngram.user_vocab
            assert predictor._explicit_suppressed(word)


class TestTheFilterNeverBlocksTyping:
    def test_a_flagged_word_stays_in_the_vocabulary(self, predictor: HybridPredictor) -> None:
        """It is a suggestion filter, not a censor. The distinction is the
        whole design: the user can always type the word, and the engine can
        always learn it."""
        word = next(iter(predictor._explicit_words))

        assert predictor._ngram.in_vocabulary(word)

    def test_it_can_still_be_learned_while_the_filter_is_on(
        self, predictor: HybridPredictor
    ) -> None:
        word = next(w for w in _flagged(predictor) if len(w) > 4)

        for _ in range(4):
            predictor.learn(word)

        assert word in predictor._ngram.user_vocab


class TestAMissingListFailsOpen:
    def test_the_filter_reports_itself_unavailable(self, tmp_model_dir: Path) -> None:
        """Construction must survive a missing data file, and the UI has to
        be able to tell, or it shows a toggle that governs nothing."""
        profile = replace(ENGLISH, explicit_words=tmp_model_dir / "absent.txt")

        predictor = HybridPredictor(model_dir=tmp_model_dir, enable_llm=False, profile=profile)

        assert not predictor.explicit_filter_available
        assert predictor.predict("th", n=3)


class TestTheCheckedInListMatchesItsGenerator:
    def test_regenerating_produces_the_committed_file(self, tmp_path: Path) -> None:
        """A generated file that nothing checks drifts from its generator.

        Run in a copy of ``data/`` so the real file is never rewritten by a
        test run, which is the failure ``tests/conftest.py`` already guards
        the config directory against.
        """
        import shutil
        import subprocess
        import sys

        root = Path(__file__).resolve().parent.parent
        sandbox = tmp_path / "repo"
        (sandbox / "scripts").mkdir(parents=True)
        shutil.copytree(root / "data", sandbox / "data")
        shutil.copy(root / "scripts" / "gen_explicit_words.py", sandbox / "scripts")

        result = subprocess.run(
            [sys.executable, str(sandbox / "scripts" / "gen_explicit_words.py")],
            capture_output=True,
            text=True,
            cwd=sandbox,
        )

        assert result.returncode == 0, result.stderr
        regenerated = (sandbox / "data" / "explicit_words.txt").read_text(encoding="utf-8")
        committed = (root / "data" / "explicit_words.txt").read_text(encoding="utf-8")
        assert regenerated == committed, (
            "data/explicit_words.txt is out of date. "
            "Re-run scripts/gen_explicit_words.py and commit the result."
        )


class TestTheRefinementPathIsFilteredToo:
    def test_a_reranked_suggestion_is_filtered(self, predictor: HybridPredictor) -> None:
        """The LLM re-ranking path emits without going through
        ``_finalize_scores``, so it is the one place in the engine that can
        put a pill on the bar without passing the filter's choke point.

        The path is disabled by default, which is precisely why it needs a
        test: nothing else exercises it, so the gap would sit there until
        somebody enabled the feature and found it the hard way.
        """
        word = next(w for w in _flagged(predictor) if len(w) > 4)
        emitted: list[list[str]] = []
        predictor.predictionsRefined.connect(emitted.append)

        class _Stub:
            def rerank_async(self, context, candidates, callback, n):
                callback([word, "hello"])

        predictor._transformer = _Stub()
        predictor._current_context = "the "
        predictor._pending_refinement = False

        predictor._refine_async("the ", ["hello"], 5)

        assert emitted, "the refinement path emitted nothing at all"
        assert word not in [w.lower() for w in emitted[-1]]
        assert "hello" in emitted[-1]

    def test_it_still_emits_when_the_filter_is_off(self, predictor: HybridPredictor) -> None:
        """The inverse: a path that had stopped emitting entirely, or a stub
        that never fired, would satisfy the test above perfectly."""
        word = next(w for w in _flagged(predictor) if len(w) > 4)
        emitted: list[list[str]] = []
        predictor.predictionsRefined.connect(emitted.append)
        predictor.set_explicit_filter(False)

        class _Stub:
            def rerank_async(self, context, candidates, callback, n):
                callback([word, "hello"])

        predictor._transformer = _Stub()
        predictor._current_context = "the "
        predictor._pending_refinement = False

        predictor._refine_async("the ", ["hello"], 5)

        assert emitted
        assert word in [w.lower() for w in emitted[-1]]
