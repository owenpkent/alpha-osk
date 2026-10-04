from __future__ import annotations

import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "gen_vocabulary.py"
    spec = importlib.util.spec_from_file_location("gen_vocabulary", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_generator_no_longer_filters_content():
    """Profanity filtering moved out of generation and into suggestion time.

    Slurs are the one exception, matched as exact words from
    ``data/slurs.txt`` rather than by this stem machinery (see
    ``test_generator_drops_slurs_and_keeps_their_neighbours``). The stems
    now seed ``scripts/gen_explicit_words.py``, so the words stay
    in the vocabulary and a user setting decides whether the bar offers
    them. This asserts the old machinery is gone rather than merely unused:
    while it existed, a future change could quietly start calling it again,
    and the words it removes cannot be recovered by any setting.
    """
    generator = _module()
    assert not hasattr(generator, "is_explicit_word")
    assert not hasattr(generator, "excluded_forms")
    assert not hasattr(generator, "EXPLICIT_SUFFIXES")


def test_generator_rejects_unverified_archive(tmp_path, monkeypatch):
    generator = _module()
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("en_US.txt", "caregiver\n")
    output = tmp_path / "out.txt"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "gen_vocabulary.py",
            str(archive),
            "--out",
            str(output),
            "--base",
            str(tmp_path / "missing.txt"),
        ],
    )
    try:
        generator.main()
    except SystemExit as exc:
        assert "source SHA-256 mismatch" in str(exc)
    else:
        raise AssertionError("unverified archive was accepted")
    assert not output.exists()


def test_generator_preserves_long_words_and_filters_source_noise(tmp_path, monkeypatch):
    generator = _module()
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(
            "en_US.txt",
            "caregiver\nneurodiversity\ncaregiver\ncaregiver's\nAlice\nxqz\n"
            "a\nrhythm\ncockpit\ncocksucker\nfucking\ncloud\nblowjobs\nhello123\n",
        )
    # Synthetic trusted archive exercises the transformation without a network
    # dependency. The separate test above enforces the real input checksum.
    monkeypatch.setattr(
        generator, "SOURCE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    base = tmp_path / "base.txt"
    base.write_text("# base words\ncloud 500\n", encoding="utf-8")
    output = tmp_path / "out.txt"
    monkeypatch.setattr(
        sys, "argv", ["gen_vocabulary.py", str(archive), "--out", str(output), "--base", str(base)]
    )
    generator.main()
    words = [line for line in output.read_text().splitlines() if not line.startswith("#")]
    # The explicit words come through now: generation no longer removes
    # content, and data/explicit_words.txt governs whether the bar offers
    # them. What is still filtered here is source noise, which is a
    # different job: capitalised forms, possessives, digits, runs with no
    # vowel, words of two characters or fewer, and words already known.
    assert words == [
        "blowjobs",
        "caregiver",
        "cockpit",
        "cocksucker",
        "fucking",
        "neurodiversity",
        "rhythm",
    ]
    assert "output_words=7" in output.with_suffix(".manifest").read_text()
    # The near-miss: "cloud" was in the base list, "Alice" is capitalised,
    # "caregiver's" is a possessive, "xqz" has no vowel, "a" is too short
    # and "hello123" carries digits. None of them survive.
    for noise in ("cloud", "alice", "caregiver's", "xqz", "a", "hello123"):
        assert noise not in words


def test_generator_drops_slurs_and_keeps_their_neighbours(tmp_path, monkeypatch):
    """Slurs are the one content exclusion made at generation time.

    Matched as exact words, so the ordinary words a stem rule would take
    with them ("spiced" is spic+ed) come through. Profanity comes through
    too: it is the suggestion filter's job, under a user setting.
    """
    generator = _module()
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("en_US.txt", "spic\nspics\nspiced\nspicy\nwetback\nwetland\nfucking\n")
    monkeypatch.setattr(
        generator, "SOURCE_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest()
    )
    base = tmp_path / "base.txt"
    base.write_text("cloud\n", encoding="utf-8")
    output = tmp_path / "out.txt"
    monkeypatch.setattr(
        sys, "argv", ["gen_vocabulary.py", str(archive), "--out", str(output), "--base", str(base)]
    )
    generator.main()
    words = [line for line in output.read_text().splitlines() if not line.startswith("#")]
    assert words == ["fucking", "spiced", "spicy", "wetland"]


def test_shipped_supplement_matches_its_manifest():
    generator = _module()
    data = Path(__file__).parents[1] / "data"
    words = generator.words_from_file(data / "english-expanded.txt")
    assert {"cockpit", "neurodiversity", "rhythmically"} <= words
    assert all(generator.WORD_RE.fullmatch(word) for word in words)
    manifest = (data / "english-expanded.manifest").read_text(encoding="utf-8")
    assert f"output_words={len(words)}\n" in manifest
    assert f"source_sha256={generator.SOURCE_SHA256}\n" in manifest
    assert "content=slurs excluded" in manifest


def test_the_shipped_wordlist_is_unfiltered_and_the_flag_list_covers_it():
    """The pair that states the whole arrangement.

    The vocabulary carries explicit words, and every one of them is on the
    list the suggestion filter consults. Asserted together because either
    half alone is satisfiable by the wrong thing: an empty vocabulary would
    satisfy the coverage check, and an empty flag list would satisfy nothing
    but would look fine if only the vocabulary were checked.
    """
    generator = _module()
    data = Path(__file__).parents[1] / "data"
    words = generator.words_from_file(data / "english-expanded.txt")
    stems = generator.words_from_file(data / "explicit_stems.txt")
    flagged = generator.words_from_file(data / "explicit_words.txt")

    suffixes = ("", "s", "es", "ed", "ing", "er", "ers", "y", "ies", "ish", "ier", "iest", "ily")
    forms = {stem + suffix for stem in stems for suffix in suffixes}

    present = words & forms
    assert present, "the wordlist no longer carries the words the stems name"
    assert present <= flagged, sorted(present - flagged)
