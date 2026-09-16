"""Tests for the learning freeze on src/prediction/hybrid_predictor.py.

See docs/research/STUDY_PROTOCOL.md section 5.2: without a freeze, the
prediction-on block trains the model that same block is scored on, which
inflates that condition specifically and does so more the later the block
runs. The freeze has to cover the n-gram tables, the pointer-bias model,
the capitalisation table and the token store, and it has to restore
cleanly even when the study harness raises partway through, because a
participant's own keyboard must never come out of a session permanently
unable to learn.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

# HybridPredictor extends QObject; skip this whole module where PySide6 is
# unavailable, the same guard tests/test_hybrid_predictor.py uses.
PySide6 = pytest.importorskip("PySide6")

from src.prediction.hybrid_predictor import HybridPredictor  # noqa: E402


@pytest.fixture
def predictor(tmp_model_dir: Path) -> HybridPredictor:
    """A real HybridPredictor over a temp model dir, LLM disabled.

    Mirrors the fixture in tests/test_hybrid_predictor.py rather than
    stubbing anything: the freeze has to be checked against the real
    n-gram tables, not a mock that would pass no matter what the gate did.
    """
    return HybridPredictor(model_dir=tmp_model_dir, enable_llm=False)


def _promote_to_known(predictor: HybridPredictor, word: str) -> None:
    """Push *word* past the 3-sighting candidate gate.

    Once a word is in ``user_vocab`` every later ``learn()`` call for it
    takes the deterministic "known word" branch (an unconditional +1),
    rather than the candidate-gate branch, which only promotes every
    third sighting. Priming the word this way keeps the state-change
    assertions below independent of whether the shipped base dictionary
    happens to already contain the word.
    """
    for _ in range(3):
        predictor.learn(word)


class TestLearningFrozenDefaultsToFalse:
    def test_a_fresh_predictor_is_not_frozen(self, predictor: HybridPredictor) -> None:
        assert predictor.learning_frozen is False


class TestLearnIsGatedByTheFreeze:
    def test_frozen_learn_returns_no_new_words_and_moves_nothing(
        self, predictor: HybridPredictor
    ) -> None:
        _promote_to_known(predictor, "zorblatt")
        before = predictor._ngram.user_vocab.get("zorblatt", 0)

        with predictor.frozen_learning():
            result = predictor.learn("zorblatt")

        assert result == []
        assert predictor._ngram.user_vocab.get("zorblatt", 0) == before

    def test_thawed_learn_still_moves_the_vocabulary(self, predictor: HybridPredictor) -> None:
        """The paired inverse: without this half, a freeze implemented as
        "break everything" (e.g. always returning early) would pass the
        frozen-case test above too.
        """
        _promote_to_known(predictor, "zorblatt")
        before = predictor._ngram.user_vocab.get("zorblatt", 0)

        predictor.learn("zorblatt")

        assert predictor._ngram.user_vocab.get("zorblatt", 0) == before + 1


class TestLearnWordIsGatedByTheFreeze:
    def test_frozen_learn_word_moves_nothing(self, predictor: HybridPredictor) -> None:
        before = predictor._ngram.user_vocab.get("gizmoword", 0)

        with predictor.frozen_learning():
            result = predictor.learn_word("gizmoword")

        assert result is None
        assert predictor._ngram.user_vocab.get("gizmoword", 0) == before

    def test_thawed_learn_word_still_boosts_the_word(self, predictor: HybridPredictor) -> None:
        predictor.learn_word("gizmoword")
        assert predictor._ngram.user_vocab.get("gizmoword", 0) == 5


class TestUnlearnWordIsGatedByTheFreeze:
    def test_frozen_unlearn_word_returns_false_and_moves_nothing(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.learn_word("gadgetword")  # seed it into user_vocab, unfrozen
        before = predictor._ngram.user_vocab.get("gadgetword", 0)

        with predictor.frozen_learning():
            result = predictor.unlearn_word("gadgetword")

        assert result is False
        assert predictor._ngram.user_vocab.get("gadgetword", 0) == before

    def test_thawed_unlearn_word_still_retracts_the_sighting(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.learn_word("gadgetword")
        before = predictor._ngram.user_vocab.get("gadgetword", 0)

        result = predictor.unlearn_word("gadgetword")

        assert result is True
        assert predictor._ngram.user_vocab.get("gadgetword", 0) == before - 1


class TestLearnTokenIsGatedByTheFreeze:
    def test_frozen_learn_token_returns_false_and_moves_nothing(
        self, predictor: HybridPredictor
    ) -> None:
        before = predictor._ngram.tokens.tokens.get("90210", 0)

        with predictor.frozen_learning():
            result = predictor.learn_token("90210")

        assert result is False
        assert predictor._ngram.tokens.tokens.get("90210", 0) == before

    def test_thawed_learn_token_still_records_the_token(self, predictor: HybridPredictor) -> None:
        result = predictor.learn_token("90210")
        assert result is True
        assert predictor._ngram.tokens.tokens.get("90210", 0) == 1


class TestLearnFromSelectionIsGatedByTheFreeze:
    def test_frozen_call_touches_neither_the_word_nor_the_context_edge(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.learn_word("selectedword")  # known word: deterministic reinforce branch
        before_vocab = predictor._ngram.user_vocab.get("selectedword", 0)
        before_bigram = predictor._ngram.bigrams.get("typing", {}).get("selectedword", 0)

        with predictor.frozen_learning():
            result = predictor.learn_from_selection("typing", "selectedword")

        assert result is None
        assert predictor._ngram.user_vocab.get("selectedword", 0) == before_vocab
        assert predictor._ngram.bigrams.get("typing", {}).get("selectedword", 0) == before_bigram

    def test_thawed_call_still_reinforces_the_word_and_the_context_edge(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.learn_word("selectedword")
        before_vocab = predictor._ngram.user_vocab.get("selectedword", 0)
        before_bigram = predictor._ngram.bigrams.get("typing", {}).get("selectedword", 0)

        predictor.learn_from_selection("typing", "selectedword")

        assert predictor._ngram.user_vocab.get("selectedword", 0) > before_vocab
        assert predictor._ngram.bigrams.get("typing", {}).get("selectedword", 0) > before_bigram


class TestMarkGoodSuggestionIsGatedByTheFreeze:
    def test_frozen_call_boosts_nothing(self, predictor: HybridPredictor) -> None:
        before_vocab = predictor._ngram.user_vocab.get("boostedword", 0)
        before_pref = predictor._ngram.preferred.get("boostedword", 0)

        with predictor.frozen_learning():
            result = predictor.mark_good_suggestion("boostedword")

        assert result is None
        assert predictor._ngram.user_vocab.get("boostedword", 0) == before_vocab
        assert predictor._ngram.preferred.get("boostedword", 0) == before_pref

    def test_thawed_call_still_boosts_the_word(self, predictor: HybridPredictor) -> None:
        predictor.mark_good_suggestion("boostedword")
        assert predictor._ngram.user_vocab.get("boostedword", 0) == 5
        assert predictor._ngram.preferred.get("boostedword", 0) == 5


class TestLearnCapitalizationIsGatedByTheFreeze:
    def test_frozen_call_returns_false_and_learns_nothing(self, predictor: HybridPredictor) -> None:
        with predictor.frozen_learning():
            result = predictor.learn_capitalization("myWord")

        assert result is False
        assert "myword" not in predictor._ngram.capitalization

    def test_thawed_call_still_learns_the_casing(self, predictor: HybridPredictor) -> None:
        result = predictor.learn_capitalization("myWord")
        assert result is True
        assert predictor._ngram.capitalization["myword"] == "myWord"


class TestSetCapitalizationIsGatedByTheFreeze:
    def test_frozen_call_writes_nothing(self, predictor: HybridPredictor) -> None:
        with predictor.frozen_learning():
            result = predictor.set_capitalization("ignored", "Zibble")

        assert result is None
        assert "zibble" not in predictor._ngram.capitalization

    def test_thawed_call_still_writes_the_preferred_casing(
        self, predictor: HybridPredictor
    ) -> None:
        predictor.set_capitalization("ignored", "Zibble")
        assert predictor._ngram.capitalization["zibble"] == "Zibble"


class TestFrozenLearningRestoresOnExit:
    def test_the_previous_value_is_restored_after_the_block(
        self, predictor: HybridPredictor
    ) -> None:
        assert predictor.learning_frozen is False
        with predictor.frozen_learning():
            assert predictor.learning_frozen is True
        assert predictor.learning_frozen is False

    def test_the_previous_value_is_restored_even_when_the_body_raises(
        self, predictor: HybridPredictor
    ) -> None:
        """A study session that throws part way through must not leave the
        participant's own keyboard permanently unable to learn, with
        nothing on screen to say so. contextmanager's try/finally is what
        this guards.
        """
        with pytest.raises(RuntimeError):
            with predictor.frozen_learning():
                assert predictor.learning_frozen is True
                raise RuntimeError("study harness blew up")

        assert predictor.learning_frozen is False
        # and the gate is really thawed, not just the flag report:
        before = predictor._ngram.user_vocab.get("afterthecrash", 0)
        predictor.learn_word("afterthecrash")
        assert predictor._ngram.user_vocab.get("afterthecrash", 0) == before + 5


class TestFrozenLearningNests:
    def test_an_inner_exit_does_not_thaw_the_outer_block(self, predictor: HybridPredictor) -> None:
        assert predictor.learning_frozen is False
        with predictor.frozen_learning():
            assert predictor.learning_frozen is True
            with predictor.frozen_learning():
                assert predictor.learning_frozen is True
            # the inner block exited; the outer freeze must still hold
            assert predictor.learning_frozen is True
        assert predictor.learning_frozen is False


class TestPredictionStillWorksWhileFrozen:
    def test_predict_still_returns_suggestions_while_frozen(
        self, predictor: HybridPredictor
    ) -> None:
        """The freeze is about mutation only. This is the test that would
        catch someone implementing it by disabling the engine instead of
        gating the writes: none of the state-comparison tests above ever
        call predict(), so a freeze that silently blanked predictions
        would pass every one of them.
        """
        predictor.learn("the cat sat on the mat")  # unfrozen: seed a known word

        with predictor.frozen_learning():
            results = predictor.predict("th", n=5)

        assert isinstance(results, list)
        assert any(w.startswith("th") for w in results)


# ----------------------------------------------------------------------
#  The inventory guard
#
#  Every test above names one method.  That is what let the freeze ship
#  with `observe_press` and `record_typed_word` unguarded for as long as
#  it did: both are mutations on the keystroke path, both are absent from
#  the list this file was written against, and a suite that only checks
#  the methods somebody remembered cannot report the one they forgot.
#
#  So the classification below is exhaustive by construction.  Every
#  public method of HybridPredictor must appear in exactly one bucket,
#  and `test_every_public_method_is_classified` fails on any that does
#  not, which makes a newly added method a failing test until somebody
#  decides which bucket it belongs in.
# ----------------------------------------------------------------------


def _nested(table: dict) -> dict:
    return {k: dict(v) for k, v in table.items()}


def _snapshot(predictor: HybridPredictor) -> dict:
    """Everything a study session must leave exactly as it found it.

    The *sub-threshold* stores below are the reason this is not simply
    the tables the dashboard displays.  An unknown word does not reach
    ``user_vocab`` until its third sighting and a blacklisted one is not
    released until its third, so a snapshot of the visible tables alone
    reports "nothing moved" for the first two sightings of either, and a
    guard that had stopped covering them would look perfectly frozen.
    The same goes for PPM, which ``learn`` trains and ``save`` persists
    but which appears in none of the n-gram tables, and for the user
    half of the context tables, whose changes can round away in the
    merged view.
    """
    ngram = predictor._ngram
    return {
        "unigrams": dict(ngram.unigrams),
        "user_vocab": dict(ngram.user_vocab),
        "user_total": ngram._user_total,
        "bigrams": _nested(ngram.bigrams),
        "trigrams": _nested(ngram.trigrams),
        "user_bigrams": _nested(ngram._user_bigrams),
        "user_trigrams": _nested(ngram._user_trigrams),
        "capitalization": dict(ngram.capitalization),
        "taught_capitalization": set(ngram.taught_capitalization),
        "blacklist": set(ngram.blacklist),
        "dispreference": dict(ngram.dispreference),
        "preferred": dict(ngram.preferred),
        "pointer": {k: tuple(v) for k, v in predictor._fuzzy.pointer._slots.items()},
        "tokens": dict(ngram.tokens.tokens),
        # Sub-threshold and out-of-table learned state.
        "candidate_counts": dict(ngram._candidate_counts),
        "candidate_last_seen": dict(ngram._candidate_last_seen),
        "blacklist_type_count": dict(ngram._blacklist_type_count),
        "ppm_total_chars": getattr(predictor._ppm, "total_chars", None),
        "ppm_word_total": getattr(predictor._ppm_word, "total_chars", None),
    }


def _prime_blacklisted(predictor: HybridPredictor) -> None:
    predictor.blacklist_word("help")


#: ``name -> (setup, call)``.  ``setup`` runs thawed, ``call`` is what the
#: freeze has to stop.  Every mutating method needs one, so a method
#: cannot be declared mutating and then left unprobed.
_RECIPES = {
    "learn": (
        lambda p: _promote_to_known(p, "zorblat"),
        lambda p: p.learn("zorblat"),
    ),
    "learn_word": (None, lambda p: p.learn_word("zorblat")),
    "unlearn_word": (
        lambda p: _promote_to_known(p, "zorblat"),
        lambda p: p.unlearn_word("zorblat"),
    ),
    # Below the promotion threshold: moves only the candidate pool and
    # PPM, which is exactly the hole the extended snapshot closes.
    "learn:below promotion": (None, lambda p: p.learn("quixotic zephyr")),
    # Below the rehabilitation threshold: moves only the sighting count.
    "record_typed_word:below rehabilitation": (
        _prime_blacklisted,
        lambda p: p.record_typed_word("help"),
    ),
    "learn_token": (None, lambda p: p.learn_token("555-1234")),
    "learn_from_selection": (None, lambda p: p.learn_from_selection("help", "hel")),
    "mark_good_suggestion": (None, lambda p: p.mark_good_suggestion("help")),
    "learn_capitalization": (
        lambda p: _promote_to_known(p, "zorblat"),
        lambda p: p.learn_capitalization("ZorBlat"),
    ),
    "set_capitalization": (None, lambda p: p.set_capitalization("zorblat", "ZorBlat")),
    # The two that were missing, and the reason this section exists.
    "observe_press": (None, lambda p: [p.observe_press("h", 0.4, 0.3) for _ in range(50)]),
    "record_typed_word": (
        _prime_blacklisted,
        lambda p: [p.record_typed_word("help") for _ in range(4)],
    ),
    # Explicit user actions, pinned below rather than gated.
    "blacklist_word": (None, lambda p: p.blacklist_word("hello")),
    "unblacklist_word": (
        lambda p: p.blacklist_word("water"),
        lambda p: p.unblacklist_word("water"),
    ),
    "mark_bad_suggestion": (None, lambda p: p.mark_bad_suggestion("help")),
    "remove_dispreference": (
        lambda p: p.mark_bad_suggestion("time"),
        lambda p: p.remove_dispreference("time"),
    ),
    "unprefer": (
        lambda p: p.mark_good_suggestion("work"),
        lambda p: p.unprefer("work"),
    ),
    "clear_user_data": (
        lambda p: _promote_to_known(p, "zorblat"),
        lambda p: p.clear_user_data(),
    ),
    "load_corpus": (None, lambda p: p.load_corpus(_corpus_file())),
    "load_ppm_training_text": (None, lambda p: p.load_ppm_training_text(_corpus_file())),
}


def _corpus_file() -> Path:
    """A small corpus on disk, for the two loaders that take a path."""
    directory = Path(tempfile.mkdtemp())
    path = directory / "corpus.txt"
    path.write_text("hello there quixotic zephyr\n" * 20, encoding="utf-8")
    return path


#: Mutates learned state and the freeze must stop it.
_MUST_FREEZE = frozenset(
    {
        "learn",
        "learn_word",
        "unlearn_word",
        "learn_token",
        "learn_from_selection",
        "mark_good_suggestion",
        "learn_capitalization",
        "set_capitalization",
        "observe_press",
        "record_typed_word",
    }
)

#: Mutates learned state and currently writes straight through the freeze.
#:
#: These are all *explicit* user actions (a pill right-click, a dashboard
#: rollback) rather than the implicit learning the freeze was written for,
#: so gating them silently is not obviously right: a control that quietly
#: does nothing reads as a click that failed to register, which this
#: project treats as the worse failure.  Blocking them in the UI for the
#: duration of a session is the likelier answer.  Pinned here so the
#: behaviour is stated rather than merely absent, and so that fixing one
#: fails this test and prompts moving it up to _MUST_FREEZE.
_LEAKS_PENDING_DECISION = frozenset(
    {
        "blacklist_word",
        "unblacklist_word",
        "mark_bad_suggestion",
        "remove_dispreference",
        "unprefer",
        "clear_user_data",
    }
)

#: Mutates learned state but is outside the freeze by design.
#:
#: These are administrative rather than learning: corpus and pack loading
#: build the *base* the user's model sits on, and reload/save move it to
#: and from disk.  None is on the keystroke path and the corpus loaders
#: have no production caller at all, so none is an ordinary-typing leak.
#: They are listed here rather than with the readers because the claim
#: this inventory makes is that every learned-state mutation has been
#: identified, and calling them non-mutating would make that claim false:
#: `load_corpus` moves `unigrams`, `user_vocab`, `_user_total` and both
#: context tables, and `load_ppm_training_text` trains the PPM trie.
#:
#: Whether a study session should be able to reach any of them is the
#: same open question as _LEAKS_PENDING_DECISION, and is not settled here.
_MUTATES_OUTSIDE_THE_FREEZE = frozenset(
    {
        "load_corpus",
        "load_ppm_training_text",
        "enable_vocabulary_pack",
        "disable_vocabulary_pack",
        "import_vocabulary_pack",
        "reload_from_disk",
        "save",
    }
)

#: Members of the bucket above that are probed, and what the rest need.
#:
#: The pack and disk APIs want a fixture this module does not build (an
#: importable pack directory, a written model file), and they are covered
#: by tests/test_vocabulary_pack.py and tests/test_data_export.py.  Naming
#: them here keeps "classified but unprobed" explicit rather than letting
#: a bucket membership imply a probe that does not exist.
_NO_RECIPE_NEEDS_FIXTURE = frozenset(
    {
        "enable_vocabulary_pack",
        "disable_vocabulary_pack",
        "import_vocabulary_pack",
        "reload_from_disk",
        "save",
    }
)


#: Reviewed and does not mutate learned state: readers, predictors, Qt
#: signals and configuration.
_NOT_LEARNING_STATE = frozenset(
    {
        "autocorrectSuggested",
        "check_autocorrect",
        "enable_llm",
        "enable_ppm",
        "learning_frozen",
        "llm_available",
        "merge_strategy",
        "frozen_learning",
        "get_available_packs",
        "get_capitalized",
        "get_enabled_packs",
        "get_key_alternatives",
        "get_stats",
        "get_unigram_freqs",
        "get_user_packs_dir",
        "llmAvailableChanged",
        "modelLoading",
        "packsChanged",
        "predict",
        "predict_email_domains",
        "predict_tokens",
        "predict_with_refinement",
        "predictionsReady",
        "predictionsRefined",
        "reload_dictionary",
        "set_key_positions",
        "set_merge_strategy",
    }
)


def _public_methods() -> set:
    """Every public callable HybridPredictor declares itself.

    ``vars()`` rather than ``dir()`` so the QObject base class's own large
    API is excluded without having to subtract it by name.
    """
    return {
        name
        for name, value in vars(HybridPredictor).items()
        if not name.startswith("_") and (callable(value) or hasattr(value, "__get__"))
    }


class TestTheInventoryIsExhaustive:
    def test_every_public_method_is_classified(self) -> None:
        """A new public method fails this until someone buckets it.

        This is the assertion the per-method tests above cannot make. It
        is why adding a mutation to this class can no longer quietly skip
        the freeze.
        """
        classified = (
            _MUST_FREEZE
            | _LEAKS_PENDING_DECISION
            | _MUTATES_OUTSIDE_THE_FREEZE
            | _NOT_LEARNING_STATE
        )
        actual = _public_methods()

        unclassified = actual - classified
        assert not unclassified, (
            f"new public method(s) {sorted(unclassified)} on HybridPredictor. "
            "Decide whether each mutates learned state and add it to "
            "_MUST_FREEZE, _LEAKS_PENDING_DECISION, "
            "_MUTATES_OUTSIDE_THE_FREEZE or _NOT_LEARNING_STATE in this "
            "file. If it mutates, give it a recipe in _RECIPES too."
        )

        stale = classified - actual
        assert not stale, f"classified method(s) {sorted(stale)} no longer exist"

    def test_the_buckets_do_not_overlap(self) -> None:
        buckets = (
            _MUST_FREEZE,
            _LEAKS_PENDING_DECISION,
            _MUTATES_OUTSIDE_THE_FREEZE,
            _NOT_LEARNING_STATE,
        )
        for i, left in enumerate(buckets):
            for right in buckets[i + 1 :]:
                assert not (left & right), sorted(left & right)

    def test_every_mutating_method_has_a_recipe(self) -> None:
        """Otherwise a method could be called mutating and never probed.

        ``_NO_RECIPE_NEEDS_FIXTURE`` is the one exemption, and it is an
        explicit list with a reason rather than a silent gap.
        """
        mutating = _MUST_FREEZE | _LEAKS_PENDING_DECISION | _MUTATES_OUTSIDE_THE_FREEZE
        # Recipe keys may carry a ":variant" suffix for a second case.
        probed = {name.split(":", 1)[0] for name in _RECIPES}

        assert mutating - probed - _NO_RECIPE_NEEDS_FIXTURE == set()
        assert probed - mutating == set()
        assert _NO_RECIPE_NEEDS_FIXTURE <= _MUTATES_OUTSIDE_THE_FREEZE

    def test_the_unfrozen_maintenance_apis_really_do_mutate(
        self, predictor: HybridPredictor
    ) -> None:
        """The bucket above claims these write; this is that claim tested.

        Without it the bucket would be an assertion in a comment, and a
        method could be parked there to get it out of the inventory's way.
        """
        for name in sorted(_MUTATES_OUTSIDE_THE_FREEZE - _NO_RECIPE_NEEDS_FIXTURE):
            fresh = HybridPredictor(model_dir=Path(tempfile.mkdtemp()), enable_llm=False)
            setup, call = _RECIPES[name]
            if setup is not None:
                setup(fresh)
            before = _snapshot(fresh)
            call(fresh)
            after = _snapshot(fresh)
            assert [k for k in before if before[k] != after[k]], (
                f"{name} is in _MUTATES_OUTSIDE_THE_FREEZE but moved nothing"
            )


class TestEveryMutatingMethodIsProbed:
    @pytest.mark.parametrize(
        "name", sorted(n for n in _RECIPES if n.split(":", 1)[0] in _MUST_FREEZE)
    )
    def test_it_moves_nothing_while_frozen(self, predictor: HybridPredictor, name: str) -> None:
        setup, call = _RECIPES[name]
        if setup is not None:
            setup(predictor)

        before = _snapshot(predictor)
        with predictor.frozen_learning():
            call(predictor)

        after = _snapshot(predictor)
        moved = sorted(k for k in before if before[k] != after[k])
        assert not moved, f"{name} wrote {moved} while learning was frozen"

    @pytest.mark.parametrize(
        "name", sorted(n for n in _RECIPES if n.split(":", 1)[0] in _MUST_FREEZE)
    )
    def test_it_still_moves_something_when_thawed(
        self, predictor: HybridPredictor, name: str
    ) -> None:
        """The inverse half, and it is not decoration.

        Without it a recipe that had quietly stopped doing anything (a
        renamed argument, a word the shipped dictionary now rejects)
        would satisfy the frozen assertion above perfectly, and the
        method would look guarded when nothing was testing it at all.
        """
        setup, call = _RECIPES[name]
        if setup is not None:
            setup(predictor)

        before = _snapshot(predictor)
        call(predictor)

        after = _snapshot(predictor)
        moved = sorted(k for k in before if before[k] != after[k])
        assert moved, f"{name} moved nothing even unfrozen: the recipe proves nothing"


class TestTheOutstandingLeaksAreStated:
    @pytest.mark.parametrize("name", sorted(_LEAKS_PENDING_DECISION))
    def test_it_still_writes_through_the_freeze(
        self, predictor: HybridPredictor, name: str
    ) -> None:
        """Pins the open decision described on _LEAKS_PENDING_DECISION.

        This test passing is not an endorsement. It fails when one of
        these is gated, which is the prompt to move that name into
        _MUST_FREEZE rather than to delete the assertion.
        """
        setup, call = _RECIPES[name]
        if setup is not None:
            setup(predictor)

        before = _snapshot(predictor)
        with predictor.frozen_learning():
            call(predictor)

        after = _snapshot(predictor)
        moved = sorted(k for k in before if before[k] != after[k])
        assert moved, (
            f"{name} no longer writes while frozen. If that was deliberate, "
            "move it from _LEAKS_PENDING_DECISION to _MUST_FREEZE."
        )
