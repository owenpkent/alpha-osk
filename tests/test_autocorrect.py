"""Tests for the common-misspellings fast-path table."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.prediction.autocorrect import CommonMisspellings


@pytest.fixture
def misspellings_file(tmp_path: Path) -> Path:
    p = tmp_path / "common_misspellings.txt"
    p.write_text(
        "# Comments and blank lines should be ignored\n"
        "\n"
        "recieve receive\n"
        "definately definitely\n"
        "TEH the\n"  # case folding
        "skip\n"  # malformed (single token) — skipped
        "noop noop\n"  # word == correction — skipped
        "\n",
        encoding="utf-8",
    )
    return p


class TestCommonMisspellings:
    def test_loads_valid_entries(self, misspellings_file: Path):
        m = CommonMisspellings()
        assert m.load(misspellings_file)
        assert len(m) == 3

    def test_lookup_returns_correction(self, misspellings_file: Path):
        m = CommonMisspellings()
        m.load(misspellings_file)
        assert m.lookup("recieve") == "receive"
        assert m.lookup("definately") == "definitely"

    def test_lookup_is_case_insensitive(self, misspellings_file: Path):
        m = CommonMisspellings()
        m.load(misspellings_file)
        assert m.lookup("RECIEVE") == "receive"
        assert m.lookup("Recieve") == "receive"

    def test_lookup_unknown_returns_none(self, misspellings_file: Path):
        m = CommonMisspellings()
        m.load(misspellings_file)
        assert m.lookup("the") is None
        assert m.lookup("") is None

    def test_no_op_entries_skipped(self, misspellings_file: Path):
        m = CommonMisspellings()
        m.load(misspellings_file)
        # "noop noop" line should not produce a self-mapping.
        assert m.lookup("noop") is None

    def test_missing_file_returns_false(self, tmp_path: Path):
        m = CommonMisspellings()
        assert not m.load(tmp_path / "nonexistent.txt")
        assert len(m) == 0

    def test_real_data_file_loads(self):
        # The shipped data file should load cleanly with sane content.
        m = CommonMisspellings()
        repo_root = Path(__file__).resolve().parent.parent
        data_path = repo_root / "data" / "common_misspellings.txt"
        assert m.load(data_path)
        # A few canonical entries that must be there.
        assert m.lookup("recieve") == "receive"
        assert m.lookup("definately") == "definitely"
        assert m.lookup("seperate") == "separate"
