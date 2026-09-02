"""Tests for the shared tempfile-then-rename write helper."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

from src.atomic_write import atomic_write_json, atomic_write_text


class TestAtomicWriteText:
    def test_write_creates_file_with_exact_content(self, tmp_path: Path) -> None:
        path = tmp_path / "out.txt"
        atomic_write_text(path, "hello world")
        assert path.read_text(encoding="utf-8") == "hello world"

    def test_write_creates_parent_directories(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "dir" / "out.txt"
        atomic_write_text(path, "content")
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "content"

    def test_write_over_existing_file_replaces_it(self, tmp_path: Path) -> None:
        path = tmp_path / "out.txt"
        atomic_write_text(path, "first")
        atomic_write_text(path, "second")
        assert path.read_text(encoding="utf-8") == "second"

    def test_no_leftover_tmp_files_after_a_successful_write(self, tmp_path: Path) -> None:
        path = tmp_path / "out.txt"
        atomic_write_text(path, "content")
        leftovers = [p for p in tmp_path.iterdir() if p.name != "out.txt"]
        assert leftovers == []

    def test_failed_rename_leaves_the_original_content_intact(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "out.txt"
        atomic_write_text(path, "original")

        def _boom(_src: object, _dst: object) -> None:
            raise OSError("simulated rename failure")

        monkeypatch.setattr(os, "replace", _boom)

        with pytest.raises(OSError):
            atomic_write_text(path, "new content")

        # The original file is untouched, and no *.tmp sibling survives
        # the failed attempt.
        assert path.read_text(encoding="utf-8") == "original"
        leftovers = [p for p in tmp_path.iterdir() if p.name != "out.txt"]
        assert leftovers == []

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode only")
    def test_mode_is_applied_on_posix(self, tmp_path: Path) -> None:
        path = tmp_path / "secret.txt"
        atomic_write_text(path, "shh", mode=0o600)
        actual = stat.S_IMODE(path.stat().st_mode)
        assert actual == 0o600


class TestAtomicWriteJson:
    def test_write_creates_file_with_exact_content(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"
        atomic_write_json(path, {"a": 1, "b": [1, 2, 3]})
        assert path.read_text(encoding="utf-8") == '{"a": 1, "b": [1, 2, 3]}'

    def test_serialisation_failure_creates_no_file_and_no_temp(self, tmp_path: Path) -> None:
        path = tmp_path / "out.json"

        class Unserialisable:
            pass

        with pytest.raises(TypeError):
            atomic_write_json(path, {"bad": Unserialisable()})

        assert not path.exists()
        assert list(tmp_path.iterdir()) == []
