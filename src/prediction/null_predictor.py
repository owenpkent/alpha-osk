"""The stand-in the bridge holds until the real engine has loaded.

``KeyboardBridge`` builds ``HybridPredictor`` on a background thread (see
``loader.py``), so for the first seconds of every launch there is no
engine.  The first version expressed that as ``_predictor = None`` and a
hand-copied ``if self._predictor is None`` at each of about fifty call
sites.  That is the parallel-blocks failure CLAUDE.md documents for the
sticky-modifier release: a call site added without its copy raises
AttributeError on a keystroke during startup, and the synchronous test
bridges never see it because they are always ready.

So the bridge holds a ``NullPredictor`` instead, which answers every call
the bridge and ``StudyBridge`` make with the answer an engine with nothing
to say would give: no suggestions, no correction, learning dropped,
nothing saved.  A new call site then works during loading by construction,
and ``tests/test_null_predictor.py`` inventories every
``self._predictor.<name>`` the two bridges reach (through local aliases
too) and fails on any this class lacks.

Two things it deliberately does NOT do, and both are the point:

* **It never writes anything.**  ``save`` is a no-op, so nothing reachable
  during loading can overwrite the user's saved model with an empty one.
* **It refuses to freeze.**  ``frozen_learning`` raises: a study session
  started against the stand-in would freeze an object that is about to be
  replaced and then run every block unfrozen on the real engine, which is
  the error ``tests/test_learning_freeze.py`` exists to prevent.

The slots whose honest answer during loading is "not yet" (Data Backup
export and import, Save Now, Clear Learned Data, a pack import, a text
import, the study's ``startSession``) do not rely on these no-ops.  They
check ``is_loaded`` and refuse, because a silent no-op there would report
success for work that was thrown away.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, ContextManager, Dict, List, Mapping, Optional, Sequence, Tuple

_EMPTY_COUNTS: Mapping[str, int] = MappingProxyType({})
_EMPTY_CONTEXT: Mapping[str, Mapping[str, int]] = MappingProxyType({})


class _NullTokenStore:
    """The slice of ``TokenPredictor`` the bridge reads directly."""

    tokens: Mapping[str, int] = _EMPTY_COUNTS

    def forget(self, token: str) -> bool:
        return False


class _NullNgramView:
    """The slice of ``NgramPredictor`` the bridge reaches through ``_ngram``.

    The dashboard slots read the n-gram tables directly, so the stand-in
    offers the same names as empty, read-only tables: the views render as
    an empty model rather than needing a branch of their own.  Read-only
    on purpose, so nothing can accumulate here and be lost at hand-over.
    """

    def __init__(self) -> None:
        self.tokens = _NullTokenStore()
        self.unigrams: Mapping[str, int] = _EMPTY_COUNTS
        self.user_vocab: Mapping[str, int] = _EMPTY_COUNTS
        self.bigrams: Mapping[str, Mapping[str, int]] = _EMPTY_CONTEXT
        self.trigrams: Mapping[str, Mapping[str, int]] = _EMPTY_CONTEXT
        self.blacklist: frozenset[str] = frozenset()
        self.dispreference: Mapping[str, int] = _EMPTY_COUNTS
        self.preferred: Mapping[str, int] = _EMPTY_COUNTS

    def learn(self, text: str) -> List[str]:
        return []

    def get_stats(self) -> Dict[str, Any]:
        # The same keys NgramPredictor.get_stats returns, so the dashboard
        # shows zeros rather than missing fields while the engine loads.
        return {
            "total_words": 0,
            "unique_words": 0,
            "bigrams": 0,
            "trigrams": 0,
            "user_bigrams": 0,
            "user_trigrams": 0,
            "user_words": 0,
        }


class EngineNotLoaded(RuntimeError):
    """Raised by the one call the stand-in must not quietly absorb."""


class NullPredictor:
    """An engine with nothing to say, standing in until the real one loads.

    Parameter lists mirror ``HybridPredictor`` exactly, which
    ``tests/test_null_predictor.py`` checks, so a call that works against
    the real engine works against this one.
    """

    def __init__(self) -> None:
        self._ngram = _NullNgramView()
        # Settable, like the real property: the bridge writes the user's
        # choice here too and reapplies it to the engine at hand-over.
        self.enable_llm = False

    @property
    def llm_available(self) -> bool:
        return False

    # --- prediction ---------------------------------------------------

    def predict(
        self,
        context: str,
        n: int = 5,
        *,
        offsets: Optional[Sequence[Optional[Tuple[float, float]]]] = None,
    ) -> List[str]:
        return []

    def predict_with_refinement(
        self,
        context: str,
        n: int = 5,
        *,
        offsets: Optional[Sequence[Optional[Tuple[float, float]]]] = None,
    ) -> List[str]:
        # The real engine emits predictionsReady here; with no signal to
        # emit, the bar keeps whatever it shows, which during loading is
        # nothing.
        return []

    def predict_tokens(self, prefix: str, n: int = 5) -> List[str]:
        return []

    def predict_email_domains(self, prefix: str, n: int = 5) -> List[str]:
        return []

    def check_autocorrect(self, typed_word: str, context: str = "") -> Optional[str]:
        return None

    def get_key_alternatives(self, key: str) -> Dict[str, float]:
        return {}

    def get_stats(self) -> dict:
        return {}

    # --- learning (dropped: there is no model to learn into) -----------

    def learn(self, text: str) -> List[str]:
        return []

    def learn_from_selection(
        self, context: str, selected_word: str, *, explicit: bool = False
    ) -> None:
        return None

    def learn_capitalization(self, word: str, *, allow_uppercase: bool = False) -> bool:
        return False

    def set_capitalization(self, word: str, preferred: str) -> None:
        return None

    def learn_token(self, token: str) -> bool:
        return False

    def unlearn_word(self, word: str) -> bool:
        return False

    def record_typed_word(self, word: str) -> Optional[str]:
        return None

    def observe_press(self, char: str, dx: float, dy: float) -> None:
        return None

    def frozen_learning(self) -> ContextManager[None]:
        raise EngineNotLoaded("the prediction engine has not loaded, so there is nothing to freeze")

    # --- settings (the bridge remembers them and reapplies at hand-over)

    def set_explicit_filter(self, enabled: bool) -> None:
        return None

    def set_merge_strategy(self, strategy: str) -> bool:
        return False

    def set_key_positions(self, positions: Dict[str, Tuple[float, float]]) -> None:
        return None

    # --- word suppression ---------------------------------------------

    def blacklist_word(self, word: str) -> None:
        return None

    def unblacklist_word(self, word: str) -> None:
        return None

    def mark_bad_suggestion(self, word: str) -> None:
        return None

    def mark_good_suggestion(self, word: str) -> None:
        return None

    def remove_dispreference(self, word: str) -> None:
        return None

    def unprefer(self, word: str) -> None:
        return None

    # --- vocabulary packs ---------------------------------------------

    def get_available_packs(self) -> List[dict]:
        return []

    def get_enabled_packs(self) -> List[str]:
        return []

    def enable_vocabulary_pack(self, pack_id: str) -> bool:
        return False

    def disable_vocabulary_pack(self, pack_id: str) -> bool:
        return False

    def import_vocabulary_pack(self, source_dir: str) -> str:
        return ""

    def get_user_packs_dir(self) -> str:
        return ""

    # --- persistence (never touches disk) -----------------------------

    def save(self) -> None:
        return None

    def reload_from_disk(self) -> None:
        return None

    def reload_dictionary(self) -> bool:
        return False

    def clear_user_data(self) -> None:
        return None


def is_loaded(predictor: object) -> bool:
    """Whether *predictor* is a real engine rather than the stand-in.

    The one readiness test, shared by the bridge and the study, for the
    slots that must refuse while the engine loads.  ``None`` counts as not
    loaded too, since ``StudyBridge`` can be built without a predictor.
    """
    return predictor is not None and not isinstance(predictor, NullPredictor)
