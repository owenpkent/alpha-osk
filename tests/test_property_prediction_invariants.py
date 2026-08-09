"""Property-based tests for the prediction-engine invariants.

CLAUDE.md calls three of these "load-bearing", meaning code elsewhere is
allowed to assume them and will misbehave silently if they break:

- ``NgramPredictor._user_total == sum(user_vocab.values())``. It exists so
  ``predict()`` doesn't recompute an O(N) sum on every keystroke, which
  means every mutation path has to keep it in step by hand. There are ten
  such paths (learn, learn_word, learn_from_pill_click, mark_good, unprefer,
  unlearn_word, the decay sweep, clear_user_data, load, and the candidate
  promotion inside learn) and nothing but arithmetic discipline keeps them
  honest. A drift here skews the personal-vs-base probability blend for the
  rest of the session with no visible symptom.
- ``SpatialKeyModel`` returns a normalised distribution. Downstream the
  scores get multiplied into ``log1p(frequency)``, so a distribution that
  doesn't sum to 1 rescales every fuzzy candidate against the n-gram ones.
- ``_context_buffer`` / ``_current_word`` mirror the on-screen text. Pill
  insertion is suffix-only against ``_current_word``, so a buffer that has
  drifted from the screen types the wrong tail into the user's document.

The existing suites test these at specific points. These tests hunt for the
operation *ordering* that breaks them, which is where an incrementally
maintained counter actually fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from src.prediction.fuzzy_recognizer import (
    QWERTY_POSITIONS,
    FuzzyWordGenerator,
    SpatialKeyModel,
)
from src.prediction.ngram_predictor import NgramPredictor

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# A pool mixing words the base dictionary knows (these skip the candidate
# gate and mutate user_vocab immediately) with plausible unknown ones (these
# go through _candidate_counts and only promote on the third sighting). Both
# arms have to be exercised: the gate is where the counter bookkeeping is
# least obvious.
_KNOWN = ["the", "hello", "please", "work", "today", "help", "want", "need"]
_UNKNOWN = ["zorvax", "blenty", "gorpal", "trisken", "vaulty", "morden"]
words = st.sampled_from(_KNOWN + _UNKNOWN)

_MUTATIONS = [
    "learn",
    "learn_word",
    "learn_from_pill_click",
    "mark_good",
    "unprefer",
    "unlearn_word",
    "record_typed_word",
    "blacklist_word",
    "unblacklist_word",
    "mark_bad",
    "remove_dispreference",
    "decay",
]

operations = st.lists(
    st.tuples(st.sampled_from(_MUTATIONS), words),
    min_size=1,
    max_size=25,
)

letters = st.sampled_from(list("abcdefghijklmnopqrstuvwxyz"))
# Weighted so sequences actually reach the interesting states: mostly typing,
# with enough space/backspace to commit words and then backspace into them.
key_ops = st.one_of(
    letters.map(lambda c: ("char", c)),
    st.just(("special", "space")),
    st.just(("special", "backspace")),
)


def _apply(predictor: NgramPredictor, op: str, word: str) -> None:
    if op == "learn":
        predictor.learn(word)
    elif op == "decay":
        predictor._apply_decay()
    else:
        getattr(predictor, op)(word)


# ---------------------------------------------------------------------------
# NgramPredictor._user_total
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def predictor() -> NgramPredictor:
    """One real predictor, base dictionary and all.

    Module-scoped because construction loads ~19k words. State deliberately
    accumulates across examples: the invariant has to hold from *any*
    starting state, so a deeper one is a better test than a clean one.
    """
    return NgramPredictor()


class TestUserTotalStaysInStepWithUserVocab:
    @given(ops=operations)
    def test_invariant_holds_after_every_single_operation(
        self, predictor: NgramPredictor, ops: List[Tuple[str, str]]
    ) -> None:
        """Asserted after each op, not just at the end, so the report names
        the operation that broke it rather than the sequence that contained
        it."""
        for op, word in ops:
            _apply(predictor, op, word)
            assert predictor._user_total == sum(predictor.user_vocab.values()), (
                f"_user_total drifted after {op}({word!r}): "
                f"{predictor._user_total} != {sum(predictor.user_vocab.values())}"
            )

    @given(ops=operations)
    def test_counters_never_go_negative(
        self, predictor: NgramPredictor, ops: List[Tuple[str, str]]
    ) -> None:
        """unprefer / unlearn_word subtract. Every one of them caps at the
        current value, and a negative total would poison the probability
        blend rather than fail loudly."""
        for op, word in ops:
            _apply(predictor, op, word)
            assert predictor._user_total >= 0
            assert predictor.total_words >= 0
            assert all(c >= 0 for c in predictor.user_vocab.values())
            assert all(c >= 0 for c in predictor.unigrams.values())

    @given(word=words, boosts=st.integers(min_value=1, max_value=5))
    def test_boost_then_rollback_returns_to_the_organic_count(self, word: str, boosts: int) -> None:
        """`unprefer` must remove exactly what `mark_good` added, leaving any
        organically learned count behind. A fresh predictor per example: this
        is about an exact delta, so a shared accumulating one would hide an
        off-by-N."""
        p = NgramPredictor()
        organic = p.user_vocab.get(word, 0)

        for _ in range(boosts):
            p.mark_good(word)
        assert p.preferred[word] == 5 * boosts

        p.unprefer(word)

        assert word not in p.preferred
        assert p.user_vocab.get(word, 0) == organic
        assert p._user_total == sum(p.user_vocab.values())

    @given(ops=operations)
    def test_clear_user_data_resets_to_a_consistent_state(self, ops: List[Tuple[str, str]]) -> None:
        p = NgramPredictor()
        for op, word in ops:
            _apply(p, op, word)
        p.clear_user_data()

        assert p._user_total == 0
        assert sum(p.user_vocab.values()) == 0
        assert p.blacklist == set()
        assert p.preferred == {}


class TestModelRoundTrip:
    @given(ops=operations)
    @settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow])
    def test_save_load_preserves_the_invariant_and_the_counts(
        self, tmp_path_factory: pytest.TempPathFactory, ops: List[Tuple[str, str]]
    ) -> None:
        """`load` recomputes _user_total from the loaded dict rather than
        trusting a saved figure, so a model saved mid-drift self-heals. Pin
        that, and pin that the surviving entries keep their exact counts."""
        p = NgramPredictor()
        for op, word in ops:
            _apply(p, op, word)

        path = Path(tmp_path_factory.mktemp("model")) / "ngram_model.json"
        p.save(path)
        before = dict(p.user_vocab)

        p.load(path)

        assert p._user_total == sum(p.user_vocab.values())
        # The loader drops implausible entries; everything it keeps must be
        # unchanged, and it must not invent any.
        for word, count in p.user_vocab.items():
            assert before.get(word) == count
        assert set(p.user_vocab) <= set(before)

    @given(ops=operations)
    @settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow])
    def test_saved_model_is_json_serialisable_and_reloadable(
        self, tmp_path_factory: pytest.TempPathFactory, ops: List[Tuple[str, str]]
    ) -> None:
        """`save` writes defaultdicts and a set (blacklist). Any state that
        json can't encode would raise on quit, losing the whole session's
        learning at exactly the moment it should be persisted."""
        p = NgramPredictor()
        for op, word in ops:
            _apply(p, op, word)

        path = Path(tmp_path_factory.mktemp("model")) / "ngram_model.json"
        p.save(path)

        data = json.loads(path.read_text())
        assert isinstance(data["blacklist"], list)
        assert isinstance(data["user_vocab"], dict)
        assert all(isinstance(v, int) for v in data["user_vocab"].values())


# ---------------------------------------------------------------------------
# SpatialKeyModel
# ---------------------------------------------------------------------------

mapped_keys = st.sampled_from(sorted(QWERTY_POSITIONS))
radii = st.floats(min_value=0.1, max_value=6.0, allow_nan=False, allow_infinity=False)


class TestSpatialDistributionIsNormalised:
    @given(key=mapped_keys, radius=radii)
    def test_probabilities_sum_to_one(self, key: str, radius: float) -> None:
        """Fuzzy scores are multiplied into log1p(frequency) downstream, so
        an unnormalised distribution silently rescales every fuzzy candidate
        against the n-gram ones."""
        model = SpatialKeyModel(uncertainty_radius=radius)
        probs = model.get_key_probabilities(key)

        assert probs, f"no candidates at all for {key!r} at radius {radius}"
        assert sum(probs.values()) == pytest.approx(1.0)

    @given(key=mapped_keys, radius=radii)
    def test_every_probability_is_a_probability(self, key: str, radius: float) -> None:
        model = SpatialKeyModel(uncertainty_radius=radius)
        for value in model.get_key_probabilities(key).values():
            assert 0.0 < value <= 1.0

    @given(key=mapped_keys, radius=radii)
    def test_the_key_actually_pressed_is_always_the_most_likely(
        self, key: str, radius: float
    ) -> None:
        """Distance 0 gives the maximum of the Gaussian, so no amount of
        uncertainty may rank a neighbour above the key under the pointer."""
        model = SpatialKeyModel(uncertainty_radius=radius)
        probs = model.get_key_probabilities(key)

        assert key in probs
        assert probs[key] == max(probs.values())

    @given(key=mapped_keys, radius=radii)
    def test_candidates_are_exactly_the_keys_within_the_radius(
        self, key: str, radius: float
    ) -> None:
        """`get_nearby_keys` and `get_key_probabilities` must not disagree;
        the beam search walks one and scores with the other."""
        model = SpatialKeyModel(uncertainty_radius=radius)
        assert set(model.get_nearby_keys(key)) == set(model.get_key_probabilities(key))

    @given(
        key=mapped_keys,
        small=radii,
        grow=st.floats(min_value=0.1, max_value=3.0, allow_nan=False),
    )
    def test_widening_the_radius_never_drops_a_candidate(
        self, key: str, small: float, grow: float
    ) -> None:
        """Monotonicity: a more forgiving setting can only ever add
        candidates. A cache-rebuild bug in set_uncertainty_radius would show
        up here as a shrinking set."""
        model = SpatialKeyModel(uncertainty_radius=small)
        narrow = set(model.get_key_probabilities(key))

        model.set_uncertainty_radius(small + grow)
        wide = set(model.get_key_probabilities(key))

        assert narrow <= wide

    @given(key=st.text(max_size=3), radius=radii)
    def test_unmapped_input_returns_certainty_not_an_empty_dict(
        self, key: str, radius: float
    ) -> None:
        """Punctuation and numpad keys are deliberately unmapped. Callers
        index the result directly, so an empty dict would be a KeyError on
        every punctuation press."""
        assume(key.lower() not in QWERTY_POSITIONS)
        model = SpatialKeyModel(uncertainty_radius=radius)
        probs = model.get_key_probabilities(key)

        assert probs == {key.lower(): 1.0}


class TestFuzzyGeneratorRobustness:
    @given(
        typed=st.text(alphabet="abcdefghijklmnopqrstuvwxyz'", max_size=12),
        max_candidates=st.integers(min_value=1, max_value=10),
    )
    @settings(
        max_examples=60,
        # small_dictionary is function-scoped. Safe to reuse across
        # examples: each one builds its own generator and nothing here
        # mutates the dict.
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_candidates_are_capped_and_ranked(
        self, small_dictionary: dict, typed: str, max_candidates: int
    ) -> None:
        """Whatever the user smears across the keyboard, the bar gets a
        bounded, descending list. The cap is what stops a 2-char prefix
        expanding into thousands of beam-search candidates mid-keystroke."""
        gen = FuzzyWordGenerator(dictionary=small_dictionary, max_candidates=max_candidates)
        results = gen.generate_candidates(typed)

        assert len(results) <= max_candidates
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True)
        assert all(isinstance(w, str) and w for w, _ in results)

    @given(typed=st.text(max_size=10))
    @settings(
        max_examples=60,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_arbitrary_input_never_raises(self, small_dictionary: dict, typed: str) -> None:
        """Runs on every keystroke. Unicode from a pasted string, an
        apostrophe, an empty prefix: none of it may reach the user as a
        traceback instead of a prediction."""
        gen = FuzzyWordGenerator(dictionary=small_dictionary)
        gen.generate_candidates(typed)


# ---------------------------------------------------------------------------
# KeyboardBridge context buffers
# ---------------------------------------------------------------------------

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def buffer_bridge():
    """A bridge with the prediction engine stubbed out.

    These tests are about the buffer bookkeeping in `_press_char` /
    `pressSpecialKey`, and running the real engine costs ~47 ms per
    simulated keystroke, which puts a property test out of reach. The
    rehydrate path that the interesting cases exercise runs regardless.
    """
    from src.keyboard_bridge import KeyboardBridge

    with patch("src.keyboard_bridge.create_key_synthesizer") as factory:
        synth = MagicMock()
        synth.is_available.return_value = True
        synth.backend_name.return_value = "MockSynth"
        factory.return_value = synth
        bridge = KeyboardBridge()
        bridge._synth = synth

    predictor = MagicMock()
    predictor.learn.return_value = []
    predictor.record_typed_word.return_value = None
    predictor.learn_capitalization.return_value = False
    bridge._predictor = predictor
    bridge._update_predictions = lambda *a, **kw: None
    return bridge


def _reset(bridge) -> None:
    bridge._context_buffer = ""
    bridge._current_word = ""
    bridge._sentence_buffer = ""
    bridge._privacy_mode = False


def _typed(bridge) -> str:
    return bridge._context_buffer + bridge._current_word


class TestContextBufferAccounting:
    @given(ops=st.lists(key_ops, min_size=1, max_size=40))
    @settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow])
    def test_each_keystroke_moves_the_buffers_by_exactly_one_character(
        self, buffer_bridge, ops: List[Tuple[str, str]]
    ) -> None:
        """The buffers together must track the document one character per
        press. Stated as a delta rather than a mirror because a space with
        no word in progress is deliberately a no-op (there is nothing to
        commit), which a naive equality model would flag.
        """
        _reset(buffer_bridge)

        for kind, value in ops:
            before = _typed(buffer_bridge)
            if kind == "char":
                buffer_bridge.pressKey(value)
                assert _typed(buffer_bridge) == before + value
            elif value == "space":
                after = _typed(buffer_bridge)
                # Committing a word appends the separator; a bare space with
                # nothing in progress commits nothing.
                buffer_bridge.pressSpecialKey("space")
                after = _typed(buffer_bridge)
                assert after in (before, before + " ")
            else:
                buffer_bridge.pressSpecialKey("backspace")
                after = _typed(buffer_bridge)
                assert after == before[:-1] if before else after == ""

    @given(ops=st.lists(key_ops, min_size=1, max_size=40))
    @settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow])
    def test_current_word_is_never_a_multi_word_fragment(
        self, buffer_bridge, ops: List[Tuple[str, str]]
    ) -> None:
        """Pill insertion is suffix-only against `_current_word`. If a space
        ever survived in there, the suffix comparison would run against two
        words and type the wrong tail into the document."""
        _reset(buffer_bridge)

        for kind, value in ops:
            if kind == "char":
                buffer_bridge.pressKey(value)
            else:
                buffer_bridge.pressSpecialKey(value)
            assert " " not in buffer_bridge._current_word

    @given(ops=st.lists(key_ops, min_size=1, max_size=30))
    @settings(max_examples=30, suppress_health_check=[HealthCheck.too_slow])
    def test_backspacing_everything_empties_both_buffers(
        self, buffer_bridge, ops: List[Tuple[str, str]]
    ) -> None:
        """Wiping the document must wipe the context too. A stale trailing
        "." left behind here makes the next prediction fire with
        sentence_start=True on what looks like an empty document."""
        _reset(buffer_bridge)

        for kind, value in ops:
            if kind == "char":
                buffer_bridge.pressKey(value)
            else:
                buffer_bridge.pressSpecialKey(value)

        for _ in range(len(_typed(buffer_bridge))):
            buffer_bridge.pressSpecialKey("backspace")

        assert buffer_bridge._current_word == ""
        assert buffer_bridge._context_buffer == ""

    @given(
        word=st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=8),
        extra=st.lists(letters, max_size=5),
    )
    @settings(max_examples=40, suppress_health_check=[HealthCheck.too_slow])
    def test_backspacing_into_a_committed_word_rehydrates_it(
        self, buffer_bridge, word: str, extra: List[str]
    ) -> None:
        """Type a word, commit it with space, then backspace. The user is
        now editing that word, so it has to come back out of the committed
        context and into `_current_word` -- otherwise a pill click takes the
        "no current word" branch and types the full word alongside the
        partial one already on screen ("backspacbackspaces")."""
        _reset(buffer_bridge)

        for ch in word:
            buffer_bridge.pressKey(ch)
        buffer_bridge.pressSpecialKey("space")
        assert buffer_bridge._current_word == ""

        buffer_bridge.pressSpecialKey("backspace")

        assert buffer_bridge._current_word == word
        assert buffer_bridge._context_buffer == ""

        # And typing on continues to extend that same word.
        for ch in extra:
            buffer_bridge.pressKey(ch)
        assert buffer_bridge._current_word == word + "".join(extra)

    @given(ops=st.lists(key_ops, min_size=1, max_size=60))
    @settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
    def test_context_buffer_stays_bounded(self, buffer_bridge, ops: List[Tuple[str, str]]) -> None:
        """It is trimmed to the last 200 chars so a long session doesn't
        grow an unbounded string that every keystroke re-scans."""
        _reset(buffer_bridge)

        for kind, value in ops:
            if kind == "char":
                buffer_bridge.pressKey(value)
            else:
                buffer_bridge.pressSpecialKey(value)
            assert len(buffer_bridge._context_buffer) <= 200

    @given(ops=st.lists(key_ops, min_size=1, max_size=30))
    @settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
    def test_privacy_mode_leaves_the_buffers_untouched(
        self, buffer_bridge, ops: List[Tuple[str, str]]
    ) -> None:
        """Password characters must not reach the buffers at all: they feed
        predictions, learning and the live visualisation.

        Goes through `setPrivacyMode`, the Learning switch's own slot, not
        the `_privacy_mode` flag. Every keystroke calls
        `_check_password_field_sync` to close the 200 ms polling race, and
        that overwrites a hand-set flag on the first press; only the manual
        toggle (`_privacy_mode_manual`) makes it stand down.
        """
        _reset(buffer_bridge)
        buffer_bridge.setPrivacyMode(True)
        try:
            for kind, value in ops:
                if kind == "char":
                    buffer_bridge.pressKey(value)
                else:
                    buffer_bridge.pressSpecialKey(value)
                assert buffer_bridge._current_word == ""
                assert buffer_bridge._context_buffer == ""
        finally:
            buffer_bridge.setPrivacyMode(False)
