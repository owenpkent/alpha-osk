"""Recognising structured tokens in typed text.

Two unrelated-looking features turn out to ask the same question, so
they share one module:

- **Intelligent spacing** needs to know, the instant a ``.`` or ``,`` or
  ``:`` is typed, whether an auto-space after it would break the thing
  being typed.  "3" + "." is a decimal, "gmail" + "." inside an email is
  a domain, and a space in either place is wrong.
- **Snippet auto-detection** needs to know, once a word is finished,
  whether it looks like an email address, a phone number, or the start
  of a postal address, so the user can be offered a one-tap save into
  Snippets instead of retyping it forever.

Both are "does this text have a recognisable shape", and answering them
in one place keeps the two from drifting into disagreeing about what an
email address looks like.

Design constraints worth knowing before editing:

**Every pattern here runs on the keystroke path**, so they are all
linear-time.  No nested quantifiers, no backtracking traps: a regex
that can blow up on a pathological token would freeze the keyboard, and
the input is whatever the user is typing.  Prefer a simple pattern plus
an explicit length cap over a clever one.

**Detection is evidence-based and deliberately conservative.**  A false
positive here is worse than a miss in both features: silently swallowing
a space corrupts prose, and offering to save the wrong thing trains the
user to dismiss the offer.  Where a shape is genuinely ambiguous the
answer is "no", and the known gaps are documented rather than papered
over with a guess.

**Nothing here logs.**  Every argument is the user's typed content; see
the diagnostic-log rules in CLAUDE.md.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Cap every input before it reaches a pattern.  Real emails, URLs and
# phone numbers are far shorter than this; anything longer is either not
# what we're looking for or is a paste-sized blob we shouldn't scan on
# the keystroke path.
_MAX_SCAN_LEN = 512
_MAX_TOKEN_LEN = 128

# ---------------------------------------------------------------------
#  Shapes
# ---------------------------------------------------------------------

# Deliberately looser than RFC 5322, which is unimplementable in a regex
# and would reject nothing a user actually types.  The job is "does this
# look like an email to a human", not "is this deliverable".
_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,4}$"
)

# A run of digits with optional separators: 3.14, 1,000, 1,000.50, 12:30,
# 192.168.1.1, 2026-08-13.  The alternation is over single characters, so
# there is no backtracking blow-up.
_NUMERIC_RUN_RE = re.compile(r"^[0-9]+(?:[.,:\-/][0-9]+)*$")

# A run of digits with no separator in it yet -- the one token shape for
# which a following "." or "," is genuinely ambiguous.  See
# ``suppression_is_provisional``.
_BARE_DIGITS_RE = re.compile(r"^[0-9]+$")

# URL schemes worth recognising before the "://" is typed, so the colon
# in "https:" doesn't get an auto-space.
_URL_SCHEMES = frozenset(
    {"http", "https", "ftp", "ftps", "sftp", "file", "mailto", "ssh", "git", "ws", "wss"}
)

# Hosts that announce themselves before the first dot.
_HOST_PREFIXES = frozenset({"www", "www2", "ftp", "mail", "smtp", "imap", "api", "localhost"})

# Phone numbers.  Matched on the *grouping* of the digits, not merely on
# how many there are.
#
# This started as "7 to 15 digits with at least one separator", which is
# the obvious rule and is badly wrong.  Every one of these satisfied it:
#
#     123-45-6789     a US Social Security number
#     4111 1111 1111  the first twelve digits of a card number
#     2026-08-13      an ISO date
#     192.168.1.1     an IP address
#     1.234-5.678     two decimals with a dash between them
#
# Offering to save any of those into Snippets is worse than missing a
# phone number: the value goes to disk, into the Data Backup archive, and
# is thereafter one tap from being typed into whatever app has focus.  So
# the rule is now an allow-list of groupings people actually write phone
# numbers in, and everything else is rejected by default.
_PHONE_ALLOWED_RE = re.compile(r"^\+?[0-9().\-\s]{7,24}$")
_PHONE_MIN_DIGITS = 7
_PHONE_MAX_DIGITS = 15  # E.164 caps the subscriber number at 15 digits.

# Digit-group shapes that mean "phone number" when there is no leading
# "+".  Written as tuples of group lengths, reading left to right:
#   (10,)        5551234567            bare NANP
#   (3, 3, 4)    555-123-4567, (555) 123-4567, 555.123.4567
#   (3, 4)       555-1234              seven-digit local
#   (1, 3, 3, 4) 1-555-123-4567        with the US country code
# Deliberately absent, and each for a reason: (3, 2, 4) is an SSN,
# (4, 2, 2) an ISO date, (4, 4, 4, 4) a card, (3, 3, 1, 1) an IP.
_PHONE_GROUPINGS = frozenset({(10,), (3, 3, 4), (3, 4), (1, 3, 3, 4)})

# Street-address detection.  A house number followed by up to four words
# ending in a street-type suffix.  This is the one genuinely fuzzy shape
# in the module, so it demands both anchors (a leading number *and* a
# recognised suffix) rather than either alone -- "500 people" and
# "Baker Street" both correctly fail.
_STREET_SUFFIXES = frozenset(
    {
        "st",
        "street",
        "ave",
        "avenue",
        "rd",
        "road",
        "blvd",
        "boulevard",
        "ln",
        "lane",
        "dr",
        "drive",
        "ct",
        "court",
        "cir",
        "circle",
        "pl",
        "place",
        "ter",
        "terrace",
        "way",
        "pkwy",
        "parkway",
        "hwy",
        "highway",
        "trl",
        "trail",
        "loop",
        "row",
        "walk",
        "close",
        "crescent",
        "grove",
        "gardens",
        "square",
    }
)
_ADDRESS_RE = re.compile(
    # The lookbehind excludes "." and "," so a decimal fraction cannot
    # supply the house number: without it, "total 12.50 in the close"
    # matched with "50" as the number.
    r"(?<![0-9A-Za-z.,])"
    r"([0-9]{1,6}[A-Za-z]?"  # house number, optionally "221b"
    r"(?:\s+[A-Za-z0-9'\-.]{1,20}){1,4})"  # 1-4 street-name words
    r"(?![0-9A-Za-z])",
)


# ---------------------------------------------------------------------
#  Single-token classification
# ---------------------------------------------------------------------


def strip_trailing_punctuation(token: str) -> str:
    """Drop sentence punctuation clinging to the end of a token.

    "owen@example.com." at the end of a sentence is still an email; the
    full stop belongs to the prose, not to the address.  Leading
    punctuation is left alone -- it is far rarer and stripping it would
    turn "(555" into something that looks like a number fragment.

    Public because ``TokenPredictor.learn`` stores what this returns
    while ``is_learnable_token`` validates it.  Those two have to be the
    same string: a second copy of the character class would let the
    store persist tokens that were never validated in the form they were
    saved in.
    """
    return token.rstrip(".,;:!?)]}\"'")


def is_email(token: str) -> bool:
    """True if *token* is shaped like an email address."""
    token = strip_trailing_punctuation(token.strip())
    if not token or len(token) > _MAX_TOKEN_LEN:
        return False
    return bool(_EMAIL_RE.match(token))


def is_numeric_run(token: str) -> bool:
    """True if *token* is digits plus separators (3.14, 1,000, 12:30)."""
    if not token or len(token) > _MAX_TOKEN_LEN:
        return False
    return bool(_NUMERIC_RUN_RE.match(token))


def _digit_groups(token: str) -> Tuple[int, ...]:
    """The lengths of each run of digits in *token*, left to right.

    "555-123-4567" -> (3, 3, 4); "123-45-6789" -> (3, 2, 4).  This is the
    signal that separates a phone number from an SSN, a date or a card
    number, all of which the digit *count* alone cannot tell apart.
    """
    groups: List[int] = []
    run = 0
    for ch in token:
        if ch.isdigit():
            run += 1
        elif run:
            groups.append(run)
            run = 0
    if run:
        groups.append(run)
    return tuple(groups)


def is_phone(token: str) -> bool:
    """True if *token* is shaped like a phone number.

    Matched on how the digits are *grouped*, not just how many there are.
    A count-only rule accepts Social Security numbers, ISO dates, card
    numbers and IP addresses (see the comment on ``_PHONE_GROUPINGS``),
    and offering to save one of those is much worse than missing a phone
    number, so anything that is not a recognised grouping is rejected.

    A leading "+" is treated as intent: nobody writes "+" in front of a
    date or an account number, and international grouping varies too much
    to enumerate ("+44 20 7946 0958", "+1 555 123 4567"), so a "+" plus a
    plausible digit count is accepted on its own.
    """
    token = strip_trailing_punctuation(token.strip())
    if not token or len(token) > _MAX_TOKEN_LEN:
        return False
    if not _PHONE_ALLOWED_RE.match(token):
        return False
    digits = sum(1 for ch in token if ch.isdigit())
    if not (_PHONE_MIN_DIGITS <= digits <= _PHONE_MAX_DIGITS):
        return False
    if token.startswith("+"):
        return True
    return _digit_groups(token) in _PHONE_GROUPINGS


# ---------------------------------------------------------------------
#  Learnable tokens
# ---------------------------------------------------------------------

# The n-gram model tokenises on ``[a-zA-Z']+``, so every digit and every
# symbol is discarded before it ever reaches the vocabulary.  That is the
# right call for a *word* model -- but it means a house number, a zip, a
# phone number and an email address are, to the prediction engine, things
# the user has never typed.  ``TokenPredictor`` keeps them in a separate
# prefix-matched store, and this is the gate on what gets in.
#
# It lives here rather than next to the store because it is the same
# question the rest of this module answers ("does this text have a
# recognisable shape"), and answering it in two places is how the two
# copies start disagreeing about what an email looks like.

_MIN_LEARNABLE_LEN = 3
_MAX_LEARNABLE_LEN = 64

# Above this many digits a token stops looking like something a person
# retypes often and starts looking like something they would not want a
# keyboard to remember: card numbers (16), IBANs, account and policy
# numbers.  Eight is chosen to sit *below* a bare nine-digit US Social
# Security number while still clearing every shape worth learning -- a
# zip (5), a house number (1-6), a year (4), an IP (8), an ISO date (8).
# Real phone numbers run longer than this and are admitted separately by
# ``is_phone``, which vets their digit *grouping* first.
_MAX_LEARNABLE_DIGITS = 8

# Digit groupings that are never learned even when they clear the count.
# (3, 2, 4) is a US Social Security number; ``is_phone`` already rejects
# it, but this store would otherwise accept it through the generic
# "has a digit" path below, and a keyboard that offers to complete an SSN
# from its first three digits is the worst thing this feature could do.
_NEVER_LEARNED_GROUPINGS = frozenset({(3, 2, 4)})


def is_learnable_token(token: str) -> bool:
    """True if *token* is a structured token worth remembering verbatim.

    The bar is deliberately asymmetric.  A missed token costs one
    suggestion the user never sees; a wrongly-learned one puts a slice of
    something private into a store that is prefix-matched into the
    suggestion bar and travels in the Data Backup archive.  So this
    admits the shapes people genuinely retype (emails, phone numbers,
    house numbers, zips, apartment numbers, versions, IPs) and rejects
    long digit runs wholesale rather than trying to enumerate which kinds
    of identifier are sensitive.

    Plain alphabetic words are rejected: they are the n-gram model's job,
    and duplicating them here would put a second, dumber vocabulary in
    front of the one that does context.

    **A mixed alphanumeric token is admitted, and that is a deliberate
    call rather than an oversight.**  The digit cap does not look at how
    letters and digits interleave, so "hunter2", "Tr0ub4dor" and
    "AB1234567" all clear it, and password auto-detection fails open by
    design (no AT-SPI on Linux, no TCC grant on macOS, any field UIA does
    not mark).  Rejecting every letter-plus-digit token would close that,
    at the cost of "Apt4B" and "v1.2" -- there is no rule that keeps one
    and drops the other, because they are the same shape.  The project's
    position is that the recall is worth more: a wrongly-learned token is
    visible to the user and removable (``TokenPredictor.forget``, and
    Clear Learned Data), where a missed one is silently never offered.
    If that trade is ever revisited, the change is a single
    ``any(ch.isalpha() ...)`` rejection below, and the tests that pin the
    current behaviour name it explicitly.
    """
    token = strip_trailing_punctuation(token.strip())
    if not (_MIN_LEARNABLE_LEN <= len(token) <= _MAX_LEARNABLE_LEN):
        return False
    if any(ch.isspace() for ch in token):
        return False

    digits = sum(1 for ch in token if ch.isdigit())
    if "@" in token:
        # An address or nothing.  "@owen" is a mention, not a token with a
        # domain to complete, and is left to the word model.
        return is_email(token)
    if not digits:
        return False

    groups = _digit_groups(token)
    if groups in _NEVER_LEARNED_GROUPINGS:
        return False
    if is_phone(token):
        return True
    return digits <= _MAX_LEARNABLE_DIGITS


# There is deliberately no ``looks_like_url``.  Neither caller needs one:
# intelligent spacing reasons about a *partial* token mid-typing (is there
# a "/" or an "@" in it yet), and snippet detection only offers emails,
# phones and addresses.  A whole-URL matcher existed here briefly, unused
# by ``src/`` and exercised only by its own tests, which is worse than no
# function at all -- it reads as covered behaviour while nothing depends
# on it.  Add one back when something actually calls it.


# ---------------------------------------------------------------------
#  Intelligent spacing
# ---------------------------------------------------------------------

# Punctuation that normally earns an auto-space after it.
_AUTO_SPACED_PUNCTUATION = frozenset({".", ",", ":", ";"})


def suppresses_auto_space(token: str, punct: str) -> bool:
    """Would auto-spacing after *punct* break the token being typed?

    *token* is the unbroken run of characters immediately before *punct*
    (so for "gmail" + "." while typing an email, it is everything back to
    the last whitespace, including the "@" -- not the prediction engine's
    ``_current_word``, which resets at "@").

    Returns True to mean "skip the space".

    **Every rule here requires positive evidence in the token itself**,
    and that is a deliberate retreat from something cleverer.  There used
    to be a "first-token rescue" that suppressed the dot in a lowercase
    single word when nothing containing whitespace had been typed yet, on
    the theory that this is what typing "example.com" into a URL bar
    looks like.  It was wrong twice over.  The proxy for "start of the
    field" was ``_context_buffer`` being space-free, but that buffer is
    emptied on every app switch *and* every focused-element change, so
    the rule fired on the first sentence of every newly focused text box:
    click into a fresh field, type "hello." and the space vanished.  It
    then repeated, because the buffer still held no space.  And the rule
    could not be repaired by a better proxy, because at the moment the
    dot lands "example" and "hello" are the same string; only text that
    has not been typed yet tells them apart.

    **Known gap, and now an unconditional one.** A bare "example.com"
    always gets a space after its *first* dot, whether typed mid-sentence
    or into an empty URL bar, and since that space ends the run,
    "example.co.uk" loses one per dot.  Guessing from the *following*
    character was also tried and rejected: it turns "i went home. then i
    left" into "home.then", and corrupting prose is far worse than a
    space the user can delete.  Closing this properly needs lookahead
    (buffering keystrokes until a known TLD resolves), which costs the
    immediate visual feedback a mouse-driven keyboard depends on.  The
    cases with real evidence -- "www.", a scheme, anything after an "@",
    a path, a number -- are all still covered.
    """
    if punct not in _AUTO_SPACED_PUNCTUATION:
        return False
    if not token or len(token) > _MAX_TOKEN_LEN:
        # No token means the punctuation opened the line; keep the
        # existing behaviour rather than guessing.
        return False

    lowered = token.lower()

    # Email and paths, but only for the punctuation that can be *part* of
    # one.  A domain and a path both contain dots and colons; neither
    # contains a comma or a semicolon, so one typed after an address is
    # ordinary prose and still earns its space.  Suppressing there ran
    # the next word straight into the address: "owen@gmail.com, thanks"
    # came out "owen@gmail.com,thanks".
    if punct in (".", ":"):
        # Everything after the "@" is a domain, so its dots are
        # structural.  Checked before the numeric rules because an
        # address can be entirely numeric on either side.
        if "@" in lowered:
            return True
        if "/" in lowered or "\\" in lowered:
            return True
    if punct == ":" and lowered in _URL_SCHEMES:
        return True
    if punct == "." and lowered in _HOST_PREFIXES:
        return True

    # Numbers: decimals, thousands separators, times, version strings,
    # IP addresses.  Covers both "3" + "." and "1,000" + ",".
    if is_numeric_run(lowered):
        return True

    # A dot already in the token means we are inside a dotted structure
    # (example.co + "." -> example.co.uk, 192.168.1 + "."), so every dot
    # after the first is suppressed even when the first one was not.
    if punct == "." and "." in lowered:
        return True

    return False


def suppression_is_provisional(token: str, punct: str) -> bool:
    """Is a suppression resting only on a bare run of digits?

    "3" + "." is the start of a decimal; "42" + "." is the end of a
    sentence.  At the instant the dot lands the two are the same string,
    exactly like "example" and "hello" in the known gap above -- but
    unlike that case, the *next* character settles it, and settles it
    additively: a digit confirms the decimal and nothing needs doing,
    while a letter proves prose and the skipped space can simply be
    typed then.  So the caller suppresses provisionally and delivers the
    space one keystroke late rather than guessing now.  Nothing is ever
    deleted, so the wrong guess costs a late space rather than mangled
    text.

    True only when a bare digit run was the *sole* evidence.  Anything
    with a separator already in it ("192.168.1", "1,000") or with an
    "@", a slash or a known scheme is structural on its own terms and
    stays suppressed outright.
    """
    if not suppresses_auto_space(token, punct):
        return False
    return bool(_BARE_DIGITS_RE.match(token))


# ---------------------------------------------------------------------
#  Snippet-worthy detection
# ---------------------------------------------------------------------

# The snippet slot each detected shape belongs in.  The label is matched
# case-insensitively against the user's existing snippet labels, so a
# renamed slot still gets found.
DETECTABLE_KINDS: Tuple[str, ...] = ("email", "phone", "address")

_KIND_LABELS = {"email": "Email", "phone": "Phone", "address": "Address"}


def label_for_kind(kind: str) -> str:
    """The default snippet label for a detected *kind*."""
    return _KIND_LABELS.get(kind, kind.title())


def detect_snippet_candidate(text: str) -> Optional[Tuple[str, str]]:
    """Find a saveable email / phone / address in *text*.

    Returns ``(kind, value)`` for the first shape found, or None.  *text*
    is a short span of recently typed content (the sentence buffer), not
    the whole document: an address needs several words of context, so a
    single token would miss it.

    Order matters.  Email is checked first because an address containing
    an "@" would otherwise be matched as a phone number by its digits.
    Address is checked last because it is the fuzziest shape and should
    only win when nothing more definite matched.
    """
    if not text:
        return None
    text = text.strip()[:_MAX_SCAN_LEN]
    if not text:
        return None

    tokens = text.split()

    for token in tokens:
        if is_email(token):
            return ("email", strip_trailing_punctuation(token))

    phone = _find_phone(text)
    if phone is not None:
        return ("phone", phone)

    address = _find_address(text)
    if address is not None:
        return ("address", address)

    return None


def _find_phone(text: str) -> Optional[str]:
    """Find a phone number in *text*, spaces and brackets included.

    A phone number is often written with internal spaces ("+1 555 123
    4567", "(555) 123 4567"), so token-at-a-time matching misses it.
    Scanning windows of consecutive tokens catches those without needing
    a regex that can straddle arbitrary whitespace.
    """
    tokens = text.split()
    # Four tokens is enough for "+1 (555) 123 4567"; more than that and
    # the run stops looking like a single number.
    for start in range(len(tokens)):
        for size in (4, 3, 2, 1):
            if start + size > len(tokens):
                continue
            candidate = " ".join(tokens[start : start + size])
            stripped = strip_trailing_punctuation(candidate)
            if is_phone(stripped):
                return stripped
    return None


def _find_address(text: str) -> Optional[str]:
    """Find a street address in *text*, or None.

    Three anchors, all required, and the third is the one that makes this
    usable.  A house number plus a street-type suffix sounds specific and
    is not: the suffix list is full of ordinary English words, and any
    digit run reads as a house number, so "it took 2 hours to drive",
    "we walked 3 miles down the road" and "there were 10 people in the
    square" all matched.  Those are fragments of whatever the user was
    writing, offered up for storage unprompted.

    The fix is **a capitalised street-name word** between the number and
    the suffix.  People write "123 Main Street" and "45 Oak Drive"; the
    prose cases are lowercase throughout, because the capital is a proper
    noun and prose has none there.

    The cost is a miss on an address typed entirely in lowercase, which
    is a real cost on a keyboard where capitals take an extra click. It
    is still the right trade: a missed offer is one the user never sees,
    while a false one puts a slice of a private message on screen and one
    tap from disk.
    """
    for match in _ADDRESS_RE.finditer(text):
        candidate = match.group(1).strip()
        words = candidate.split()
        if len(words) < 3:
            # Need the number, at least one name word, and the suffix.
            continue
        if words[-1].lower().strip(".,") not in _STREET_SUFFIXES:
            continue
        if any(w[:1].isupper() for w in words[1:-1]):
            return candidate
    return None
