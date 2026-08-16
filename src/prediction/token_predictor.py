"""
Verbatim completion for structured tokens: numbers, emails, phones.

The n-gram model tokenises on ``[a-zA-Z']+``.  Every digit and every
symbol is dropped before a word reaches the vocabulary, which is correct
for a word model and leaves a real gap for the person using this
keyboard: their own phone number, zip code, house number and email
address are the strings they retype most often and the ones the engine
structurally cannot learn.  Each is also expensive to type on a
mouse-driven OSK -- a ten-digit phone number is ten clicks, and the digit
row is a layer hop away on the compact layouts.

``TokenPredictor`` is that gap and nothing more.  It is a flat
count-weighted store of whole tokens, matched by prefix.  There is no
context model, no fuzzy matching and no capitalisation logic:

* Context would need bigrams over tokens, and the useful signal ("this
  is the number I always type") is already carried by the count.
* Fuzzy matching is actively wrong here.  Correcting a letter the user
  mistyped is a favour; "correcting" a digit is silently changing a
  number, and a phone number that is one digit off is worse than no
  suggestion at all.
* Casing is either irrelevant (domains, digits) or already carried in the
  stored form.

What may enter the store is decided by :func:`text_patterns.is_learnable_token`,
which lives beside the other shape rules so that "what does an email look
like" has exactly one answer in this codebase.

Persistence rides along in ``ngram_model.json`` under a ``tokens`` key
rather than in a file of its own: that file is already the "everything
the user taught us" store (capitalisation, blacklist, boosts), it is
already in the Data Backup archive, and it already has load-time size
caps.  A separate file would have needed all three re-established.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from ..text_patterns import is_email, is_learnable_token, strip_trailing_punctuation

_logger = logging.getLogger("TokenPredictor")

# Domains offered after an "@" when the user has not taught us their own.
# Ordered by how often they turn up in general use, because that order is
# the ranking whenever nothing has been learned yet.  Kept short on
# purpose: the prediction bar drops the pills that do not fit (see the
# fitter in Main.qml), so a long tail here would only ever push the
# common answers out of view.
DEFAULT_DOMAINS: Tuple[str, ...] = (
    "gmail.com",
    "outlook.com",
    "yahoo.com",
    "hotmail.com",
    "icloud.com",
    "aol.com",
    "proton.me",
    "live.com",
)


class TokenPredictor:
    """A prefix-matched store of structured tokens the user has typed."""

    # A user who types is not going to produce thousands of distinct
    # numbers and addresses; a store this size is already generous and
    # bounds what a long-running install writes into ngram_model.json.
    MAX_TOKENS = 2_000

    # Below two characters a prefix matches most of the store, so the bar
    # would fill with unrelated numbers on the first digit of anything.
    MIN_PREFIX_LEN = 2

    def __init__(self) -> None:
        self.tokens: Dict[str, int] = {}
        self._domain_cache: Dict[str, int] = {}
        self._domains_dirty = True

    # ------------------------------------------------------------------
    #  Learning
    # ------------------------------------------------------------------

    def learn(self, token: str) -> bool:
        """Record one sighting of *token*.  Returns True if it was kept.

        Rejects are the common case and are silent: this is called at
        every word boundary, and almost every word boundary ends a plain
        word.
        """
        token = token.strip()
        if not is_learnable_token(token):
            return False
        # Strip the sentence punctuation the shape rule already ignored,
        # so "555-123-4567." and "555-123-4567" are one entry rather
        # than two.  Called rather than re-spelled: `is_learnable_token`
        # validates what this returns, so a second copy of the character
        # class would let the store persist a form that was never vetted.
        token = strip_trailing_punctuation(token)
        self.tokens[token] = self.tokens.get(token, 0) + 1
        self._domains_dirty = True
        if len(self.tokens) > self.MAX_TOKENS:
            self._evict()
        return True

    def _evict(self) -> None:
        """Drop the least-seen entries back to the cap.

        Ties break toward the *longer* token, which is the one that cost
        more clicks to type and so is worth more as a completion.
        """
        ranked = sorted(self.tokens.items(), key=lambda kv: (-kv[1], -len(kv[0])))
        self.tokens = dict(ranked[: self.MAX_TOKENS])
        self._domains_dirty = True

    def forget(self, token: str) -> bool:
        """Remove *token* entirely.  Returns True if it was there."""
        if self.tokens.pop(token, None) is None:
            return False
        self._domains_dirty = True
        return True

    def clear(self) -> None:
        self.tokens.clear()
        self._domain_cache.clear()
        self._domains_dirty = False

    # ------------------------------------------------------------------
    #  Prediction
    # ------------------------------------------------------------------

    def predict(self, prefix: str, n: int = 5) -> List[str]:
        """Learned tokens starting with *prefix*, best first.

        The prefix match is case-insensitive but the stored casing is
        what comes back, so "Apt4B" survives being re-typed as "apt".
        An exact match is excluded: there is nothing left to insert.
        """
        if len(prefix) < self.MIN_PREFIX_LEN or not self.tokens:
            return []
        lowered = prefix.lower()
        matches = [
            (tok, count)
            for tok, count in self.tokens.items()
            if len(tok) > len(prefix) and tok.lower().startswith(lowered)
        ]
        matches.sort(key=lambda kv: (-kv[1], len(kv[0]), kv[0]))
        return [tok for tok, _ in matches[:n]]

    def domains(self, prefix: str, n: int = 5) -> List[str]:
        """Email domains starting with *prefix* (which may be empty).

        Domains the user has actually typed rank above the built-in list
        and are ordered by how often they were typed, so a work address
        on a company domain overtakes ``gmail.com`` after a few uses.
        The built-ins fill whatever room is left, which is what makes the
        feature useful on the very first email typed on a fresh install.
        """
        lowered = prefix.lower()
        learned = self._domain_counts()
        ranked = sorted(
            (d for d in learned if d.startswith(lowered)),
            key=lambda d: (-learned[d], len(d), d),
        )
        out: List[str] = list(ranked)
        for dom in DEFAULT_DOMAINS:
            if len(out) >= n:
                break
            if dom.startswith(lowered) and dom not in out:
                out.append(dom)
        return out[:n]

    def _domain_counts(self) -> Dict[str, int]:
        """Domain -> total sightings, derived from the learned emails.

        Rebuilt lazily rather than maintained on every learn, because
        learns are frequent and reads happen only while the user is
        actually typing past an "@".
        """
        if self._domains_dirty:
            cache: Dict[str, int] = {}
            for tok, count in self.tokens.items():
                if "@" not in tok or not is_email(tok):
                    continue
                domain = tok.rsplit("@", 1)[1].lower()
                cache[domain] = cache.get(domain, 0) + count
            self._domain_cache = cache
            self._domains_dirty = False
        return self._domain_cache

    # ------------------------------------------------------------------
    #  Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, int]:
        return dict(self.tokens)

    def from_dict(self, data: object) -> None:
        """Load from the ``tokens`` key of a saved model.

        Anything malformed is dropped entry by entry rather than
        rejecting the whole file: this rides in ``ngram_model.json``
        alongside the vocabulary, and a bad token is not a reason to lose
        someone's learned words.  Every entry is re-validated against the
        current shape rule, so tightening that rule retroactively cleans
        the store on the next load.
        """
        self.clear()
        if not isinstance(data, dict):
            return
        kept: Dict[str, int] = {}
        for tok, count in data.items():
            if len(kept) >= self.MAX_TOKENS:
                break
            if not isinstance(tok, str) or not isinstance(count, int) or isinstance(count, bool):
                continue
            # Strip before validating, exactly as ``learn`` does.  Doing
            # it the other way round -- validating the stripped form and
            # storing the raw one -- let a file written by an older
            # build, hand-edited, or arriving in an imported archive keep
            # "555-1234.", admitted on the strength of "555-1234" and
            # then offered as a pill that types a stray full stop into
            # the user's number.  Counts are merged rather than
            # overwritten so a file carrying both forms keeps both
            # sightings instead of whichever iterated last.
            tok = strip_trailing_punctuation(tok)
            if count <= 0 or not is_learnable_token(tok):
                continue
            kept[tok] = kept.get(tok, 0) + count
        self.tokens = kept
        self._domains_dirty = True
        _logger.info("Token store loaded (%d entries)", len(kept))
