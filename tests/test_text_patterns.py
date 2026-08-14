"""Tests for structured-token recognition (src/text_patterns.py).

Two features read this module and they fail in opposite directions, so
the tests are organised around the failure rather than the function:

- **Intelligent spacing** must never swallow a space in prose.  A false
  positive there corrupts what the user is writing, which is far worse
  than leaving a domain unjoined, so every suppression rule is paired
  with the prose case it must not catch.
- **Snippet detection** must never offer to save something that isn't
  personal data, because an offer the user has to dismiss twice is an
  offer they will learn to ignore.  Every positive is paired with the
  near-miss it must reject ("500 people" against "500 Main Street").

The inverse assertions are the point.  A rule that suppressed every
space, or a detector that matched every number, would satisfy the
positive half of this file completely.
"""

from __future__ import annotations

import pytest

from src.text_patterns import (
    detect_snippet_candidate,
    is_email,
    is_numeric_run,
    is_phone,
    label_for_kind,
    suppresses_auto_space,
    suppression_is_provisional,
)


class TestEmailShape:
    @pytest.mark.parametrize(
        "token",
        [
            "owen@example.com",
            "owen.p.kent@example.co.uk",
            "owen+tag@example.com",
            "o@e.io",
            "123@456.com",
        ],
    )
    def test_recognised(self, token) -> None:
        assert is_email(token)

    @pytest.mark.parametrize(
        "token",
        [
            "",
            "owen",
            "owen@",
            "@example.com",
            "owen@example",  # no TLD
            "owen example.com",
            "owen@@example.com",
        ],
    )
    def test_rejected(self, token) -> None:
        assert not is_email(token)

    def test_a_trailing_full_stop_is_not_part_of_the_address(self) -> None:
        """ "...email me at owen@example.com." is still an email."""
        assert is_email("owen@example.com.")


class TestPhoneShape:
    @pytest.mark.parametrize(
        "token",
        [
            "555-123-4567",
            "555.123.4567",
            "(555) 123-4567",
            "5551234567",  # bare NANP 10-digit
            "+15551234567",
            "+44 20 7946 0958",
            "555-1234",
        ],
    )
    def test_recognised(self, token) -> None:
        assert is_phone(token)

    @pytest.mark.parametrize(
        "token",
        [
            "",
            "2026",  # a year
            "123456",  # too few digits
            "1234567",  # 7 bare digits: an order id as easily as a number
            "12345678901234567890",  # past E.164
            "abc-def-ghij",
            "555-123-4567x",
        ],
    )
    def test_rejected(self, token) -> None:
        assert not is_phone(token)

    @pytest.mark.parametrize(
        "token,what",
        [
            ("123-45-6789", "US Social Security number"),
            ("4111 1111 1111", "leading digits of a card number"),
            ("4111-1111-1111-1111", "full card number"),
            ("2026-08-13", "ISO date"),
            ("192.168.1.1", "IP address"),
            ("1.234-5.678", "two decimals with a dash between them"),
            ("10.20.30.40", "another dotted quad"),
        ],
    )
    def test_other_things_made_of_digits_are_not_phone_numbers(self, token, what) -> None:
        """The rule is the digit *grouping*, not the digit count.

        Every one of these satisfies "7-15 digits with a separator in it",
        which is the obvious rule and the one this module shipped with
        first.  They matter more than an ordinary miss would: a value that
        reaches Snippets is written to disk, travels in the Data Backup
        archive, and is one tap from being typed into whatever app has
        focus.  A user entering their SSN on a tax form must not be
        offered a button that stores it.
        """
        assert not is_phone(token), f"{what} matched as a phone number"

    def test_a_bare_digit_run_needs_to_be_exactly_ten(self) -> None:
        """Separators or a "+" are what make a digit run *mean* phone number.

        Without that signal an 8- or 11-digit run is just as likely to be
        an account number, so only the one unambiguous bare form (NANP
        10-digit) is accepted.
        """
        assert is_phone("5551234567")
        assert not is_phone("55512345")
        assert not is_phone("555123456789")


class TestNumericRun:
    @pytest.mark.parametrize("token", ["3", "3.14", "1,000", "12:30", "192.168.1.1", "2026-08-13"])
    def test_recognised(self, token) -> None:
        assert is_numeric_run(token)

    @pytest.mark.parametrize("token", ["", "3a", "hello", "1,", ".5"])
    def test_rejected(self, token) -> None:
        assert not is_numeric_run(token)


class TestAutoSpaceIsSuppressedInsideStructure:
    """The cases the feature exists for."""

    @pytest.mark.parametrize(
        "token,punct",
        [
            ("3", "."),  # decimal
            ("3.14", "."),  # version string
            ("1", ","),  # thousands separator
            ("1,000", ","),
            ("12", ":"),  # time
            ("owen@gmail", "."),  # email domain
            ("owen@example.co", "."),
            ("www", "."),  # host prefix
            ("mail", "."),
            ("https", ":"),  # url scheme
            ("http", ":"),
            ("https://example", "."),
            ("C:/Users/owen", "."),  # path
            ("example.co", "."),  # every dot after the first
            ("192.168", "."),
        ],
    )
    def test_suppressed(self, token, punct) -> None:
        assert suppresses_auto_space(token, punct)


class TestAutoSpaceSurvivesProse:
    """The inverse, and the half that actually matters.

    Swallowing a space in ordinary writing is the failure this feature
    can cause, so these assertions are what keep a broader rule from
    passing as an improvement.
    """

    @pytest.mark.parametrize(
        "token,punct",
        [
            ("hello", "."),
            ("home", "."),
            ("world", ","),
            ("however", ";"),
            ("following", ":"),
            ("Mr", "."),
            ("etc", ","),
        ],
    )
    def test_not_suppressed(self, token, punct) -> None:
        assert not suppresses_auto_space(token, punct)

    def test_punctuation_that_never_auto_spaces_is_left_alone(self) -> None:
        assert not suppresses_auto_space("3", "!")
        assert not suppresses_auto_space("3", "?")

    def test_an_empty_token_is_never_suppressed(self) -> None:
        """Punctuation opening a line has no token to reason about."""
        assert not suppresses_auto_space("", ".")

    def test_an_absurdly_long_token_is_left_alone(self) -> None:
        assert not suppresses_auto_space("a" * 500, ".")

    @pytest.mark.parametrize(
        "token,punct",
        [
            ("owen@gmail.com", ","),  # "owen@gmail.com, thanks"
            ("owen@gmail.com", ";"),
            ("@owen", ","),  # "@owen, hi"
            ("C:/Users/owen", ","),  # "see C:/Users/owen, then"
            ("https://example.com", ","),
            ("https://example.com", ";"),
        ],
    )
    def test_a_comma_after_an_address_is_prose(self, token, punct) -> None:
        """The "@" and path rules used to fire for every auto-spaced mark.

        A domain contains dots and colons; it never contains a comma or a
        semicolon, so one typed after an address belongs to the sentence
        around it.  Suppressing there ran the next word straight into the
        address: "owen@gmail.com, thanks" came out "owen@gmail.com,thanks".
        """
        assert not suppresses_auto_space(token, punct)

    def test_the_structural_marks_are_still_suppressed(self) -> None:
        """The inverse: gating on punctuation must not disarm the rule."""
        assert suppresses_auto_space("owen@gmail", ".")
        assert suppresses_auto_space("owen@host", ":")
        assert suppresses_auto_space("C:/Users/owen", ".")


class TestProvisionalSuppression:
    """Which suppressions rest on evidence, and which on a coin flip.

    A bare run of digits is the one token shape where the following
    punctuation is genuinely ambiguous: "3" + "." starts a decimal and
    "42" + "." ends a sentence, and at that instant they are the same
    string.  The caller withholds the space and delivers it on the next
    character if that character proves prose, so this predicate is what
    separates "wait and see" from "suppress outright".
    """

    @pytest.mark.parametrize("token,punct", [("3", "."), ("42", "."), ("1", ","), ("12", ":")])
    def test_a_bare_number_is_provisional(self, token, punct) -> None:
        assert suppression_is_provisional(token, punct)

    @pytest.mark.parametrize(
        "token,punct",
        [
            ("192.168.1", "."),  # a separator is already in it
            ("1,000", ","),
            ("owen@gmail", "."),  # the "@" is the evidence
            ("C:/Users/owen", "."),
            ("www", "."),
            ("https", ":"),
            ("example.co", "."),
        ],
    )
    def test_an_evidenced_suppression_is_not(self, token, punct) -> None:
        assert not suppression_is_provisional(token, punct)

    @pytest.mark.parametrize("token,punct", [("hello", "."), ("world", ","), ("", ".")])
    def test_nothing_suppressed_is_nothing_to_defer(self, token, punct) -> None:
        """Provisional is a *kind* of suppression, never a source of one."""
        assert not suppression_is_provisional(token, punct)


class TestTheRemovedFirstTokenRescue:
    """The rule that used to join a bare domain, and why it is gone.

    ``suppresses_auto_space`` briefly took a "nothing containing a space
    has been typed yet" hint and used it to suppress the dot in a
    lowercase single word, on the theory that this is what typing
    "example.com" into a URL bar looks like.

    It was wrong twice.  The caller derived the hint from
    ``_context_buffer``, which is emptied on every app switch *and* every
    focused-element change, so it was true at the start of every newly
    focused text box: click into a field, type "hello." and the space
    vanished -- then kept vanishing, because the buffer still held no
    space.  And no better proxy would have saved it, because at the
    instant the dot lands "example" and "hello" are the same string.

    These tests state the resulting behaviour so nobody reintroduces the
    rule by accident.  The end-to-end version, through the bridge, is
    tests/test_keyboard_bridge.py::TestIntelligentSpacingInAFreshField.
    """

    def test_a_bare_domain_is_not_joined_without_other_evidence(self) -> None:
        """The documented cost, stated plainly rather than hidden."""
        assert not suppresses_auto_space("example", ".")

    def test_a_lowercase_first_word_keeps_its_space(self) -> None:
        """The bug the rescue caused: the first sentence of a fresh field."""
        assert not suppresses_auto_space("hello", ".")

    @pytest.mark.parametrize(
        "token,punct",
        [("www", "."), ("mail", "."), ("https", ":"), ("owen@gmail", "."), ("3", ".")],
    )
    def test_evidence_in_the_token_still_suppresses(self, token, punct) -> None:
        """The inverse: removing the rescue must not have removed the rest."""
        assert suppresses_auto_space(token, punct)


class TestSnippetDetection:
    def test_finds_an_email(self) -> None:
        assert detect_snippet_candidate("my email is owen@example.com thanks") == (
            "email",
            "owen@example.com",
        )

    def test_finds_a_phone(self) -> None:
        assert detect_snippet_candidate("call me at 555-123-4567") == ("phone", "555-123-4567")

    def test_finds_a_phone_written_with_spaces(self) -> None:
        """A number split across tokens needs a multi-token scan to see."""
        assert detect_snippet_candidate("ring (555) 123 4567 today") == (
            "phone",
            "(555) 123 4567",
        )

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("call 5551234567", "5551234567"),
            ("dial 555-1234", "555-1234"),
            ("ring 1-555-123-4567", "1-555-123-4567"),
            ("tel 555.123.4567", "555.123.4567"),
            ("call +44 20 7946 0958 now", "+44 20 7946 0958"),
        ],
    )
    def test_real_phone_formats_survive_the_tightening(self, text, expected) -> None:
        """The inverse of the rejections above.

        A grouping allow-list that rejected everything would satisfy every
        negative test in this file, so each accepted shape is pinned.
        """
        assert detect_snippet_candidate(text) == ("phone", expected)

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("home is 45 Oak Drive", "45 Oak Drive"),
            ("mail 1600 Pennsylvania Avenue", "1600 Pennsylvania Avenue"),
            ("ship to 221b Baker Street", "221b Baker Street"),
        ],
    )
    def test_real_addresses_survive_the_tightening(self, text, expected) -> None:
        assert detect_snippet_candidate(text) == ("address", expected)

    def test_finds_a_street_address(self) -> None:
        assert detect_snippet_candidate("i live at 123 Main Street") == (
            "address",
            "123 Main Street",
        )

    def test_email_wins_over_the_digits_inside_it(self) -> None:
        """Order matters: an address full of digits must not read as a phone."""
        kind, _ = detect_snippet_candidate("write to 5551234567@example.com now")
        assert kind == "email"

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "nothing interesting here",
            "500 people showed up",  # number without a street suffix
            "walking down Baker Street",  # street suffix without a number
            "the year 2026 was fine",
            "order 1234567 shipped",
        ],
    )
    def test_finds_nothing_worth_offering(self, text) -> None:
        assert detect_snippet_candidate(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "it took 2 hours to drive",
            "we walked 3 miles down the road",
            "there were 10 people in the square",
            "reply by 9 to confirm the court",
            "total 12.50 in the close",
            "meet at 7 in the grove",
            "i need 5 more days off work",
            "there are 4 ways to do this",
        ],
    )
    def test_ordinary_prose_containing_a_number_is_not_an_address(self, text) -> None:
        """The near-misses that actually bite, and the reason for the third anchor.

        "A house number plus a street-type suffix" sounds specific and is
        not: the suffix list is full of ordinary English words (way, road,
        drive, court, square, close, grove) and any digit run reads as a
        house number, so every one of these matched at first.  What was
        offered up was a slice of whatever the user happened to be
        writing.  Requiring a *capitalised* street-name word between the
        two is what separates "123 Main Street" from "3 miles down the
        road".

        The original version of this file paired each positive with a
        near-miss, which was the right instinct and the wrong examples:
        "500 people showed up" and "walking down Baker Street" each fail
        one anchor outright, so they passed against a matcher that was
        wide open.  Pick hostile examples, not tidy ones.
        """
        assert detect_snippet_candidate(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "my ssn is 123-45-6789 ok",
            "card 4111 1111 1111 1111 exp",
            "meeting 2026-08-13 at noon",
            "the server is 192.168.1.1 today",
        ],
    )
    def test_sensitive_lookalikes_are_never_offered(self, text) -> None:
        """End-to-end cover for the digit-grouping rule.

        Worth asserting at this level as well as on `is_phone`, because
        `_find_phone` scans multi-token windows and could reconstruct one
        of these out of pieces even if the single-token check rejects it.
        """
        assert detect_snippet_candidate(text) is None

    def test_labels(self) -> None:
        assert label_for_kind("email") == "Email"
        assert label_for_kind("phone") == "Phone"
        assert label_for_kind("address") == "Address"
