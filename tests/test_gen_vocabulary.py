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


def test_explicit_filter_does_not_use_substrings():
    generator = _module()
    roots = {"cock", "fuck"}
    assert generator.is_explicit_word("cock", roots)
    assert generator.is_explicit_word("fucking", roots)
    assert not generator.is_explicit_word("cockpit", roots)


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
    assert words == ["caregiver", "cockpit", "neurodiversity", "rhythm"]
    assert "output_words=4" in output.with_suffix(".manifest").read_text()


def test_shipped_supplement_matches_its_manifest_and_exclusion_policy():
    generator = _module()
    data = Path(__file__).parents[1] / "data"
    words = generator.words_from_file(data / "english-expanded.txt")
    exclusions = generator.words_from_file(data / "explicit_exclusions.txt")
    assert not words.intersection(generator.excluded_forms(exclusions))
    assert {"cockpit", "neurodiversity", "rhythmically"} <= words
    assert all(generator.WORD_RE.fullmatch(word) for word in words)
    manifest = (data / "english-expanded.manifest").read_text(encoding="utf-8")
    assert f"output_words={len(words)}\n" in manifest
    assert f"source_sha256={generator.SOURCE_SHA256}\n" in manifest
