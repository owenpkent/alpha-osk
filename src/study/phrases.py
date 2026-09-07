"""Phrase pool for the copy-typing task, and the contamination check.

Two sources, per section 7 of the protocol.  The bundled set below is written
for this study.  The standard instrument, MacKenzie and Soukoreff's 500-phrase
set, is not vendored here (it is not ours to redistribute and the study should
be able to state exactly which revision it used): :func:`load_phrase_file`
reads it from a path the researcher supplies, and :func:`build_pool` merges the
two.

The part of this module that matters is not the phrases, it is
:func:`contamination_report`.  A phrase the shipped model was seeded on
produces inflated savings that look exactly like a result, and the seed data is
large enough that nobody can eyeball it.  The check runs before a session, not
after the analysis.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

_logger = logging.getLogger("Study.Phrases")

# A phrase has to be long enough that prediction has somewhere to work and
# short enough to finish in one breath with a pointer.  MacKenzie and
# Soukoreff's set averages 28.6 characters, and these track that.
MIN_PHRASE_LEN = 16
MAX_PHRASE_LEN = 48

# Reading a supplied phrase file is reading a file the researcher picked, not
# untrusted input, but every other loader in this project caps its input and
# there is no reason for this one to be the exception.
_MAX_PHRASE_FILE_BYTES = 1_024 * 1_024
_MAX_PHRASES = 2_000

_WORD_RE = re.compile(r"[a-z']+")

# Written for this study rather than drawn from the standard set, in the
# register the system is actually used for: correspondence, scheduling, and
# medical and personal logistics.  Lowercase and unpunctuated by convention,
# so that capitalisation and punctuation are not silently part of the task.
BUNDLED_PHRASES: tuple[str, ...] = (
    "the meeting has been moved to thursday",
    "please call me when you get a chance",
    "i will send the documents this afternoon",
    "can we reschedule for next week",
    "thank you for getting back to me so quickly",
    "the appointment is at half past two",
    "i need to order a new set of batteries",
    "let me know if that time works for you",
    "the delivery should arrive before friday",
    "i have attached the form you asked for",
    "could you pass on my thanks to the team",
    "the physical therapy session went well",
    "i am running about ten minutes late",
    "please bring the paperwork with you",
    "the weather is supposed to turn cold",
    "i will be out of the office on monday",
    "we should talk about this in person",
    "the invoice has already been paid",
    "can you remind me what time it starts",
    "i finished reading the report last night",
    "the new chair is much more comfortable",
    "please let the others know as well",
    "i would rather do it in the morning",
    "the results came back completely normal",
    "we are looking forward to seeing you",
    "i left a message on the answering machine",
    "the parking is easier round the back",
    "please check that the door is locked",
    "i think we have covered everything",
    "the software update finished overnight",
    "can you help me lift this box",
    "i have not heard anything back yet",
    "the train was delayed by an hour",
    "please save a copy for your records",
    "i will look into it and let you know",
    "the pharmacy closes at six on sunday",
    "we agreed to meet outside the entrance",
    "i appreciate you taking the time",
    "the batteries need charging again",
    "please send it whenever you are ready",
)


@dataclass(frozen=True)
class ContaminationReport:
    """What a candidate pool shares with the data the model was built from."""

    total: int
    clean: tuple[str, ...]
    contaminated: tuple[tuple[str, str], ...]
    """Each entry is (phrase, the reason it was rejected)."""

    @property
    def is_clean(self) -> bool:
        return not self.contaminated


def normalise(phrase: str) -> str:
    """Lowercased, NFKC-folded, whitespace-collapsed form used for comparison."""
    folded = unicodedata.normalize("NFKC", phrase).lower()
    return " ".join(folded.split())


def words_in(phrase: str) -> tuple[str, ...]:
    return tuple(_WORD_RE.findall(normalise(phrase)))


def is_well_formed(phrase: str) -> bool:
    """Whether a phrase is usable as a trial at all.

    Length bounds plus "every character is one we can present and score".  A
    phrase carrying a character the layout cannot produce is not a hard
    phrase, it is an impossible one, and it would show up in the data as a
    participant who could not finish a trial.
    """
    text = normalise(phrase)
    if not MIN_PHRASE_LEN <= len(text) <= MAX_PHRASE_LEN:
        return False
    return all(c == " " or c == "'" or c.isalpha() for c in text)


def load_phrase_file(path: Path) -> tuple[str, ...]:
    """Read one phrase per line, skipping blanks and ``#`` comments.

    Malformed lines are dropped individually rather than rejecting the file,
    the same tolerance every other loader here applies: one bad line in a
    supplied phrase set is not a reason to be unable to run the study.
    """
    try:
        if path.stat().st_size > _MAX_PHRASE_FILE_BYTES:
            _logger.warning("phrase file over cap, ignoring: %s", path)
            return ()
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        _logger.warning("phrase file unreadable (%s): %s", path, e)
        return ()

    out: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        phrase = normalise(stripped)
        if is_well_formed(phrase):
            out.append(phrase)
        if len(out) >= _MAX_PHRASES:
            break
    return tuple(out)


def build_pool(extra_file: Path | None = None) -> tuple[str, ...]:
    """The bundled phrases, plus a supplied set, de-duplicated in order."""
    seen: dict[str, None] = {}
    for phrase in BUNDLED_PHRASES:
        if is_well_formed(phrase):
            seen.setdefault(normalise(phrase), None)
    if extra_file is not None:
        for phrase in load_phrase_file(extra_file):
            seen.setdefault(phrase, None)
    return tuple(seen)


def contamination_report(
    pool: Sequence[str],
    *,
    seed_phrases: Iterable[str] = (),
    seed_bigrams: Iterable[tuple[str, str]] = (),
) -> ContaminationReport:
    """Reject phrases the shipped model was built from.

    Two kinds of contamination, and the second is the one that is easy to
    miss.  A phrase appearing verbatim in the training corpus is obvious.  A
    phrase whose every adjacent word pair is a curated seed bigram is not
    verbatim in anything, and is still a phrase the engine has been handed the
    answer to: the context tables will complete it end to end.

    Anything rejected is returned with its reason rather than silently
    dropped, because the count of rejections is itself worth reporting.  A
    pool that loses most of itself here is evidence that the pool was drawn
    from the same well as the training data.
    """
    seeds = {normalise(p) for p in seed_phrases}
    seed_pairs = {(a.lower(), b.lower()) for a, b in seed_bigrams}

    clean: list[str] = []
    bad: list[tuple[str, str]] = []
    for phrase in pool:
        text = normalise(phrase)
        if text in seeds:
            bad.append((phrase, "appears verbatim in the seed corpus"))
            continue
        if any(text in seed and text != seed for seed in seeds):
            bad.append((phrase, "is a substring of a seed corpus line"))
            continue
        pairs = tuple(zip(words_in(text), words_in(text)[1:]))
        if pairs and all(pair in seed_pairs for pair in pairs):
            bad.append((phrase, "every word pair is a curated seed bigram"))
            continue
        clean.append(phrase)

    return ContaminationReport(total=len(pool), clean=tuple(clean), contaminated=tuple(bad))
