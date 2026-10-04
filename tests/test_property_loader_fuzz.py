"""Fuzz every on-disk loader with hostile bytes and structurally-mutated JSON.

Each persisted store here (the n-gram and PPM models, snippets, key actions,
analytics, dictation config, telemetry state, the token store) is read back
from a file the user, an older build, or an imported Data Backup archive may
have damaged. Each loader documents the same contract: whatever the file
holds, loading does not raise, and the object that results is usable.

Two input families, because they find different bugs:

* **Raw bytes** (``st.binary``): not JSON, not UTF-8, truncated, empty.
* **Mutated valid JSON**: the right top-level keys carrying the wrong types,
  nested junk, huge numbers, NaN / Infinity, negative counts, very long
  strings. This is the family that gets past the first ``isinstance`` check
  and into the code behind it, which is where the bugs live.

Properties, per loader: it does not raise; a normal follow-up call (predict,
list, save, reload) works; and where the code promises all-or-nothing
(analytics parses into locals first) a poisoned field leaves no trace.

Files are written only into a scratch directory under the system temp dir.
Run the deep profile with ``--hypothesis-profile=alpha-osk-deep`` (see
docs/build/CI.md, "Nightly fuzzing").
"""

from __future__ import annotations

import functools
import json
import math
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src import text_patterns as tp
from src.analytics import TypingAnalytics
from src.dictation.config import DictationConfig
from src.key_actions import FUNCTION_KEYS, KeyActionStore
from src.prediction.ngram_predictor import NgramPredictor
from src.prediction.ppm_predictor import PPMPredictor
from src.prediction.token_predictor import TokenPredictor
from src.snippets import MAX_SNIPPETS, SnippetStore
from src.telemetry import TelemetryClient

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_HUGE = [10**30, -(10**30), 2**63, -(2**63), 2**31, -1, 0]
_FLOATS = [float("nan"), float("inf"), float("-inf"), 1e308, -1e308, 5e-324, -0.0, 0.5]

_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.sampled_from(_HUGE),
    st.sampled_from(_FLOATS),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=40),
    st.text(min_size=2000, max_size=6000),
    st.sampled_from(["", "\x00", "\ud800", "a" * 100_000, "I'll", "555-123-4567", "a@b.co"]),
)

# Keys that look like the real thing, so mutated values land in live code.
_key = st.one_of(
    st.sampled_from(["the", "a", "i", "I'm", "555-123-4567", "a@b.co", "f13", "f1", "x", ""]),
    st.text(max_size=12),
)

junk = st.recursive(
    _scalar,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(_key, children, max_size=5),
    ),
    max_leaves=12,
)


def _mutated(base: dict[str, Any]) -> st.SearchStrategy[Any]:
    """The base document with each top-level key kept, replaced, or dropped."""

    def per_key(value: Any) -> st.SearchStrategy[Any]:
        return st.one_of(st.just(value), st.just(value), junk)

    keys = {
        k: st.one_of(st.just(None).map(lambda _: _MISSING), per_key(v)) for k, v in base.items()
    }
    return (
        st.fixed_dictionaries(keys).map(lambda d: {k: v for k, v in d.items() if v is not _MISSING})
        | junk
    )


_MISSING = object()


def _encode(doc: Any) -> bytes:
    """JSON text, NaN and Infinity included (Python's encoder emits them)."""
    try:
        return json.dumps(doc).encode("utf-8", "surrogatepass")
    except (TypeError, ValueError, RecursionError):  # pragma: no cover - generator bug guard
        return b"{}"


def _documents(base: dict[str, Any]) -> st.SearchStrategy[bytes]:
    # Deep nesting is described by a small spec and expanded here, so the
    # (huge) text never appears in a strategy repr or a failure report.
    nested = st.tuples(
        st.sampled_from(["list", "dict", "actions"]),
        st.sampled_from([50, 1_000, 20_000, 150_000]),
    ).map(lambda spec: _nest(*spec))
    return st.one_of(
        st.binary(max_size=2000),
        _mutated(base).map(_encode),
        nested,
        st.sampled_from(
            [b"", b"{", b"null", b"[]", b'"x"', b"NaN", b"-Infinity", b"\xef\xbb\xbf{}"]
        ),
    )


def _nest(kind: str, n: int) -> bytes:
    if kind == "list":
        return b"[" * n + b"]" * n
    opener = b'{"a":' if kind == "dict" else b'{"actions":'
    return opener * n + b"1" + b"}" * n


@contextmanager
def _scratch() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="aosk-fuzz-") as d:
        yield Path(d)


def _write(directory: Path, name: str, blob: bytes) -> Path:
    path = directory / name
    path.write_bytes(blob)
    return path


# ---------------------------------------------------------------------------
# n-gram model (and the tokens / pointer sections it carries)
# ---------------------------------------------------------------------------

_NGRAM_BASE: dict[str, Any] = {
    "unigrams": {"the": 100, "hello": 5},
    "user_vocab": {"hello": 3},
    "user_bigrams": {"the": {"cat": 2}},
    "user_trigrams": {"the cat": {"sat": 1}},
    "capitalization": {"nasa": "NASA"},
    "taught_capitalization": ["nasa"],
    "total_words": 105,
    "blacklist": ["badword"],
    "dispreference": {"foo": 1},
    "preferred": {"hello": 5},
    "blacklist_type_count": {"badword": 1},
    "candidate_counts": {"zzz": 2},
    "candidate_last_seen": {"zzz": 1700000000.0},
    "tokens": {"555-123-4567": 3},
    "pointer": {"a": [3.0, 0.3, -0.1]},
}


def _fresh_ngram() -> NgramPredictor:
    return NgramPredictor()


# A quarter of the active profile's budget: each example builds a predictor
# over the 83k-word base, so the full count would dominate the suite's runtime.
@settings(max_examples=max(10, settings.default.max_examples // 4))
@given(blob=_documents(_NGRAM_BASE))
def test_ngram_load_never_raises_and_stays_usable(blob: bytes) -> None:
    with _scratch() as d:
        model = _write(d, "ngram_model.json", blob)
        p = _fresh_ngram()
        p.load(model)  # documented contract: never raises
        assert p._user_total == sum(p.user_vocab.values())
        p.predict("the ", 5)
        p.predict("", 5)
        p.learn("hello there my friend")
        assert p._user_total == sum(p.user_vocab.values())


# Saving writes the whole 83k-word merged table, so the round trip runs on a
# handful of examples rather than on every one.
@settings(max_examples=max(10, settings.default.max_examples // 15))
@given(blob=_documents(_NGRAM_BASE))
def test_ngram_survivor_of_a_hostile_load_saves_and_reloads(blob: bytes) -> None:
    with _scratch() as d:
        p = _fresh_ngram()
        p.load(_write(d, "ngram_model.json", blob))
        out = d / "out.json"
        p.save(out)
        again = _fresh_ngram()
        again.load(out)
        assert again._user_total == sum(again.user_vocab.values())


@functools.lru_cache(maxsize=1)
def _shared_pointer() -> Any:
    return _fresh_ngram().pointer


@given(blob=_documents({"a": [1, 2, 3]}))
def test_pointer_and_token_sections_survive_any_value(blob: bytes) -> None:
    """The two sub-stores take an arbitrary object, not just a file's dict."""
    try:
        data = json.loads(blob)
    except (ValueError, RecursionError):
        return
    t = TokenPredictor()
    t.from_dict(data)
    assert len(t.tokens) <= TokenPredictor.MAX_TOKENS
    assert all(isinstance(k, str) and isinstance(v, int) and v > 0 for k, v in t.tokens.items())
    t.predict("55", 5)
    _shared_pointer().from_dict(data)


# ---------------------------------------------------------------------------
# PPM model
# ---------------------------------------------------------------------------

_PPM_BASE: dict[str, Any] = {
    "max_order": 8,
    "alphabet": ["a", "b"],
    "total_chars": 10,
    "root": {"count": 3, "children": {"a": {"count": 2, "children": {}}}},
}


@given(blob=_documents(_PPM_BASE))
def test_ppm_load_never_raises_and_stays_usable(blob: bytes) -> None:
    with _scratch() as d:
        model = _write(d, "ppm_model.json", blob)
        p = PPMPredictor()
        p.load(model)
        p.predict_word("the ca", "ca", 5)
        p.predict_next_chars("the ", 3)
        p.learn_text("hello world")
        p.save(d / "out.json")


# ---------------------------------------------------------------------------
# Snippets and key actions
# ---------------------------------------------------------------------------

_SNIPPET_BASE = {
    "version": 2,
    "snippets": [{"label": "Home", "value": "1 Main St", "color": "red"}],
}


@given(blob=_documents(_SNIPPET_BASE))
def test_snippet_store_load_never_raises_and_stays_usable(blob: bytes) -> None:
    with _scratch() as d:
        path = _write(d, "snippets.json", blob)
        store = SnippetStore(path)
        store.load()
        items = store.get_all()
        assert 1 <= len(items) <= MAX_SNIPPETS
        for item in items:
            assert set(item) == {"label", "value", "color"}
            assert all(isinstance(v, str) for v in item.values())
        store.save()
        again = SnippetStore(path)
        again.load()
        assert again.get_all() == items


_KEY_ACTION_BASE = {
    "version": 1,
    "actions": {"f13": {"type": "text", "text": "hi", "label": "Hi"}},
}


@given(blob=_documents(_KEY_ACTION_BASE))
def test_key_action_store_load_never_raises_and_stays_usable(blob: bytes) -> None:
    with _scratch() as d:
        path = _write(d, "key_actions.json", blob)
        store = KeyActionStore(path)
        store.load()
        actions = store.get_all()
        assert set(actions) <= set(FUNCTION_KEYS)
        store.save()
        again = KeyActionStore(path)
        again.load()
        assert again.get_all() == actions


# ---------------------------------------------------------------------------
# Analytics: parse into locals first, so a bad field leaves no trace
# ---------------------------------------------------------------------------

_ANALYTICS_COUNTERS = [
    "keystrokes",
    "words",
    "predictions",
    "keystrokes_saved",
    "sessions",
    "backspaces",
    "prediction_offers",
    "prediction_rank_sum",
    "prediction_rank_count",
    "top_pick_count",
]
_ANALYTICS_BASE: dict[str, Any] = {
    **{k: 7 for k in _ANALYTICS_COUNTERS},
    "minutes": 3.5,
    "word_freq": {"the": 4},
    "key_freq": {"a": 2},
}


def _analytics_from(path: Path) -> TypingAnalytics:
    with mock.patch.object(TypingAnalytics, "_get_stats_path", staticmethod(lambda: path)):
        return TypingAnalytics()


def _alltime_state(a: TypingAnalytics) -> tuple[Any, ...]:
    return (
        a._alltime_keystrokes,
        a._alltime_words,
        a._alltime_predictions,
        a._alltime_keystrokes_saved,
        a._alltime_minutes,
        a._alltime_backspaces,
        a._alltime_prediction_offers,
        a._alltime_prediction_rank_sum,
        a._alltime_prediction_rank_count,
        a._alltime_top_pick_count,
        dict(a._alltime_word_freq),
        dict(a._alltime_key_freq),
    )


_ZERO_STATE = (0, 0, 0, 0, 0.0, 0, 0, 0, 0, 0, {}, {})


@given(blob=_documents(_ANALYTICS_BASE))
def test_analytics_load_never_raises_and_stays_usable(blob: bytes) -> None:
    with _scratch() as d:
        path = _write(d, "analytics.json", blob)
        a = _analytics_from(path)
        stats = a.get_session_stats()
        assert isinstance(stats, dict)
        a.save()  # the arithmetic that a wrongly-typed counter would break
        assert json.loads(path.read_text())["keystrokes"] >= 0


@given(
    field=st.sampled_from(_ANALYTICS_COUNTERS + ["minutes"]),
    bad=st.one_of(
        st.none(),
        st.booleans(),
        st.text(max_size=8),
        st.lists(st.integers(), max_size=2),
        st.just(float("nan")),
        st.just(float("inf")),
        st.just(float("-inf")),
    ),
)
def test_analytics_poisoned_field_leaves_no_partial_state(field: str, bad: Any) -> None:
    """One rejected scalar discards the file: nothing from it is applied."""
    with _scratch() as d:
        doc = dict(_ANALYTICS_BASE)
        doc[field] = bad
        path = _write(d, "analytics.json", _encode(doc))
        assert _alltime_state(_analytics_from(path)) == _ZERO_STATE


def test_analytics_valid_baseline_does_apply() -> None:
    """Inverse of the above: a loader that rejected everything would pass it."""
    with _scratch() as d:
        path = _write(d, "analytics.json", _encode(_ANALYTICS_BASE))
        state = _alltime_state(_analytics_from(path))
        assert state != _ZERO_STATE
        assert state[0] == 7 and state[4] == 3.5


@given(
    count=st.one_of(
        st.integers(min_value=-(10**12), max_value=10**12),
        st.floats(allow_nan=False, allow_infinity=False),
    )
)
def test_analytics_counters_are_never_negative(count: float) -> None:
    with _scratch() as d:
        doc = {k: count for k in _ANALYTICS_COUNTERS}
        doc["minutes"] = count
        a = _analytics_from(_write(d, "analytics.json", _encode(doc)))
        state = _alltime_state(a)
        assert all(v >= 0 for v in state[:10])
        assert not math.isnan(state[4])


# ---------------------------------------------------------------------------
# Dictation config and telemetry state
# ---------------------------------------------------------------------------

_DICTATION_BASE = {
    "enabled": True,
    "api_key": "abcd",
    "key_protected": False,
    "model": "nova-3",
    "language": "en",
    "device": "mic",
    "max_seconds": 60,
    "silence_seconds": 5,
    "keyterms": ["alpha"],
    "stream_inserts": True,
}


@given(blob=_documents(_DICTATION_BASE))
def test_dictation_config_load_never_raises_and_stays_usable(blob: bytes) -> None:
    with _scratch() as d:
        path = _write(d, "dictation.json", blob)
        cfg = DictationConfig.load(path)
        assert isinstance(cfg.api_key, str)
        assert isinstance(cfg.keyterms, list) and all(isinstance(k, str) for k in cfg.keyterms)
        assert isinstance(cfg.max_seconds, int) and isinstance(cfg.silence_seconds, int)
        repr(cfg)
        cfg.masked_key()
        cfg.save(path)
        again = DictationConfig.load(path)
        assert again.model == cfg.model and again.language == cfg.language


_TELEMETRY_BASE = {
    "enabled": True,
    "anon_id": "0" * 36,
    "last_submit_ts": 1700000000.0,
    "invite_applied": False,
}


@given(blob=_documents(_TELEMETRY_BASE))
def test_telemetry_state_load_never_raises_and_stays_usable(blob: bytes) -> None:
    with _scratch() as d:
        path = _write(d, "telemetry.json", blob)
        client = TelemetryClient(
            state_path=path, endpoint="", submit_fn=lambda *_: 204, now=lambda: 1.0
        )
        assert isinstance(client.enabled, bool)
        assert client.anon_id is None or isinstance(client.anon_id, str)
        assert math.isfinite(client._last_submit_ts)
        client.disable()
        assert client.anon_id is None


# ---------------------------------------------------------------------------
# text_patterns: never raises on any text, and stays linear on hostile runs
# ---------------------------------------------------------------------------

# A generous bound: these calls take well under a millisecond on a 10k-char
# input when linear, and seconds when quadratic. Ten seconds across every
# input shape flags a blowup without flaking on a loaded CI runner.
_SLOW_SECONDS = 10.0

_any_text = st.one_of(
    st.text(max_size=300),
    st.text(alphabet=st.characters(codec=None), max_size=300),
    st.text(alphabet="0123456789-.,@ ()+'\"/:\\́‍\ud800", max_size=300),
)


def _exercise_text_patterns(text: str) -> None:
    tp.strip_trailing_punctuation(text)
    tp.is_email(text)
    tp.is_numeric_run(text)
    tp.is_phone(text)
    tp.is_learnable_token(text)
    for punct in (".", ":", "@", ",", "!", " ", text[:1]):
        tp.suppresses_auto_space(text, punct)
        tp.suppression_is_provisional(text, punct)
    tp.label_for_kind(text)
    tp.detect_snippet_candidate(text)


@given(text=_any_text)
def test_text_patterns_never_raise(text: str) -> None:
    _exercise_text_patterns(text)


@given(text=_any_text, tail=_any_text)
def test_text_patterns_never_raise_on_a_sentence(text: str, tail: str) -> None:
    tp.detect_snippet_candidate(text + " " + tail)
    tp.detect_snippet_candidate(f"call {text} or write to {tail}")


_ADVERSARIAL = [
    "." * 10_000,
    "@" * 10_000,
    "9" * 10_000,
    "-" * 10_000,
    " " * 10_000,
    "1 " * 5_000,
    "1." * 5_000,
    "a@" * 5_000,
    "a.b." * 2_500,
    "1 a " * 2_500,
    "12 Main " * 1_250,
    "555-" * 2_500,
    "(" * 10_000,
    "+1 (555) 123-" * 800,
    "9" * 5_000 + "." + "9" * 5_000,
    "a" * 5_000 + "@" + "b." * 2_500,
    "1" + " a" * 5_000,
    "́" * 10_000,
]


@pytest.mark.parametrize("text", _ADVERSARIAL, ids=lambda t: f"{t[:8]!r}x{len(t)}")
def test_text_patterns_stay_fast_on_long_adversarial_input(text: str) -> None:
    start = time.perf_counter()
    _exercise_text_patterns(text)
    elapsed = time.perf_counter() - start
    assert elapsed < _SLOW_SECONDS, f"text_patterns took {elapsed:.1f}s on {len(text)} chars"


@given(
    unit=st.sampled_from([".", "@", "9", "-", " ", "1 ", "a@", "12 Main ", "555-", "(", "a.b."]),
    reps=st.integers(min_value=2_000, max_value=5_000),
)
def test_text_patterns_stay_fast_on_generated_runs(unit: str, reps: int) -> None:
    start = time.perf_counter()
    _exercise_text_patterns(unit * reps)
    assert time.perf_counter() - start < _SLOW_SECONDS


# ---------------------------------------------------------------------------
# Regressions: each is the minimal input Hypothesis found against a loader
# ---------------------------------------------------------------------------


def test_regression_ppm_null_max_order_does_not_poison_the_model(tmp_path: Path) -> None:
    path = _write(tmp_path, "ppm_model.json", b'{"max_order":null}')
    p = PPMPredictor()
    p.load(path)
    assert p.max_order == 8  # unchanged: the file was rejected whole
    p.predict_word("the ca", "ca", 5)


def test_regression_ppm_bad_root_leaves_no_half_applied_state(tmp_path: Path) -> None:
    doc = {"max_order": 3, "total_chars": 99, "root": {"count": "x"}}
    path = _write(tmp_path, "ppm_model.json", _encode(doc))
    p = PPMPredictor()
    p.load(path)
    assert (p.max_order, p.total_chars) == (8, 0)


def test_an_order_zero_model_survives_a_reload(tmp_path: Path) -> None:
    """Order 0 is a real PPM (bare character frequencies), and the bits-per-char
    benchmark saves and reloads one, so the range check must admit it. The
    inverse, an order past the cap, is still rejected whole."""
    trained = PPMPredictor(max_order=0)
    trained.train("abracadabra")
    trained.save(tmp_path / "ppm_model.json")
    reloaded = PPMPredictor(max_order=0, model_path=tmp_path / "ppm_model.json")
    assert reloaded.total_chars == trained.total_chars > 0

    doc = {"max_order": 65, "total_chars": 99, "root": {"count": 1, "children": {}}}
    path = _write(tmp_path, "too_deep.json", _encode(doc))
    p = PPMPredictor()
    p.load(path)
    assert (p.max_order, p.total_chars) == (8, 0)


@pytest.mark.parametrize("name", ["snippets", "key_actions", "analytics", "telemetry", "dictation"])
def test_regression_deeply_nested_json_is_not_fatal(name: str, tmp_path: Path) -> None:
    """json raises RecursionError (not ValueError) on deep nesting."""
    blob = _nest("list", 150_000)
    if name == "snippets":
        store = SnippetStore(_write(tmp_path, "s.json", blob))
        store.load()
        assert store.get_all()
    elif name == "key_actions":
        ka = KeyActionStore(_write(tmp_path, "k.json", blob))
        ka.load()
        assert ka.get_all() == {}
    elif name == "analytics":
        _analytics_from(_write(tmp_path, "a.json", blob))
    elif name == "telemetry":
        TelemetryClient(state_path=_write(tmp_path, "t.json", blob), endpoint="")
    else:
        assert DictationConfig.load(_write(tmp_path, "d.json", blob)).model == "nova-3"


def test_regression_dictation_unhashable_model_and_language(tmp_path: Path) -> None:
    path = _write(tmp_path, "d.json", b'{"language":[],"model":{}}')
    cfg = DictationConfig.load(path)
    assert (cfg.model, cfg.language) == (DictationConfig.model, DictationConfig.language)


@pytest.mark.parametrize("blob", [b"null", b"[]", b'"x"', b"7"])
def test_regression_telemetry_state_that_is_not_an_object(blob: bytes, tmp_path: Path) -> None:
    client = TelemetryClient(state_path=_write(tmp_path, "t.json", blob), endpoint="")
    assert client.enabled is False


@pytest.mark.parametrize("value", ["Infinity", "-Infinity", "NaN"])
def test_regression_telemetry_non_finite_timestamp_does_not_silence_submits(
    value: str, tmp_path: Path
) -> None:
    blob = ('{"enabled": true, "last_submit_ts": %s}' % value).encode()
    client = TelemetryClient(state_path=_write(tmp_path, "t.json", blob), endpoint="")
    assert client._last_submit_ts == 0.0
