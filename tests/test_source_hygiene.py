"""Repo-wide checks on the source files themselves rather than the code.

There is exactly one so far, and it earned its place by happening twice
in a day.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Extensions whose contents are legitimately binary, so a NUL in them says
# nothing. Everything else in the repo is text and is searched as text.
_BINARY_SUFFIXES = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".icns",
        ".pdf",
        ".zip",
        ".gz",
        ".exe",
        ".dll",
        ".pyd",
        ".so",
        ".dylib",
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".wav",
        ".mp3",
        ".bmp",
        ".pyc",
    }
)


def _tracked_files() -> list[Path]:
    """Every file git tracks, so nothing untracked or ignored is scanned."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:  # pragma: no cover - only outside a git checkout
        pytest.skip("not a git checkout, so there is no file list to scan")
    return [REPO_ROOT / name for name in result.stdout.split("\0") if name]


def test_no_text_file_contains_a_nul_byte() -> None:
    """A NUL turns a text file binary as far as every search tool is concerned.

    ``qml/Main.qml`` carried a literal U+0000 as a string separator. The
    runtime value was correct and git still diffed the file as text, because
    git only sniffs the first 8000 bytes, so nothing ever complained. But
    ripgrep classifies a file as binary from the first NUL onward and stops:
    searching for anything defined in the back two thirds of that file, which
    was the entire snippets window, returned **nothing at all**.

    That is worse than an error. An empty result reads as "this does not
    exist", and it got acted on: a search for the edit-mode signal handlers
    came back empty and the obvious conclusion, that the snippets editor had
    none, was wrong.

    Then it happened again in the same day, in the very paragraph of
    ``GOTCHAS.md`` documenting it, which is the argument for a test rather
    than a note. The character is invisible in every editor, every diff and
    every review.
    """
    offenders: list[str] = []
    for path in _tracked_files():
        if path.suffix.lower() in _BINARY_SUFFIXES or not path.is_file():
            continue
        data = path.read_bytes()
        index = data.find(b"\x00")
        if index != -1:
            line = data[:index].count(b"\n") + 1
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line}")

    assert offenders == [], (
        "NUL byte in a text file, which makes ripgrep treat everything after "
        "it as binary and silently return no matches: " + ", ".join(offenders)
    )
