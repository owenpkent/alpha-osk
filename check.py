"""
Pre-push sanity check.

Runs the same three checks GitHub Actions runs in .github/workflows/ci.yml:
    1. ruff check src/ tests/
    2. mypy src/ --ignore-missing-imports
    3. pytest

Run before `git push` to catch lint / type / test failures locally
instead of waiting for the CI red X.

Usage:
    python check.py           # fast: lint + types + tests, no coverage
    python check.py --full    # adds the --cov-fail-under=60 gate
                              # CI uses (~3 min vs ~30 s)

Exits 0 if everything passes, 1 if any step fails.

The default skips coverage tracking because it adds ~6x to pytest's
runtime and the coverage threshold has only ever moved when we added
tests, never broken on a fresh push.  Use --full when you actually
want CI parity (e.g. before bumping the version + cutting a release).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time


# ANSI colours, falling back to no-colour on terminals that don't support them.
class C:
    HEADER = "\033[95m"
    OK = "\033[92m"
    FAIL = "\033[91m"
    DIM = "\033[90m"
    BOLD = "\033[1m"
    END = "\033[0m"


def _safe(s: str) -> str:
    try:
        s.encode(sys.stdout.encoding or "utf-8")
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode("ascii", errors="replace").decode("ascii")


def banner(msg: str) -> None:
    print(_safe(f"\n{C.HEADER}{C.BOLD}== {msg} =={C.END}"))


def run(label: str, cmd: list[str]) -> tuple[bool, float]:
    """Run a CI step.  Returns (ok, elapsed_seconds)."""
    banner(label)
    print(_safe(f"{C.DIM}$ {' '.join(cmd)}{C.END}"))
    start = time.perf_counter()
    rc = subprocess.run(cmd).returncode
    elapsed = time.perf_counter() - start
    ok = rc == 0
    status = (
        f"{C.OK}OK{C.END}" if ok
        else f"{C.FAIL}FAIL (exit {rc}){C.END}"
    )
    print(_safe(f"{status}  {label} ({elapsed:.1f}s)"))
    return ok, elapsed


def have(tool: str) -> bool:
    return shutil.which(tool) is not None or _have_module(tool)


def _have_module(name: str) -> bool:
    """Some tools (ruff, mypy, pytest) ship as Python modules."""
    try:
        subprocess.run(
            [sys.executable, "-m", name, "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def main() -> int:
    full = "--full" in sys.argv[1:]
    py = sys.executable
    pytest_cmd = [py, "-m", "pytest", "-q"]
    if full:
        pytest_cmd += [
            "--cov=src", "--cov-report=term-missing",
            "--cov-fail-under=60",
        ]
    steps = [
        ("ruff",   [py, "-m", "ruff", "check", "src/", "tests/"]),
        ("mypy",   [py, "-m", "mypy", "src/", "--ignore-missing-imports"]),
        ("pytest", pytest_cmd),
    ]

    missing = [name for name, _ in steps if not _have_module(name)]
    if missing:
        print(_safe(
            f"{C.FAIL}Missing tools: {', '.join(missing)}.{C.END}\n"
            f"Install with: pip install ruff mypy pytest pytest-cov PySide6"
        ))
        return 1

    results: list[tuple[str, bool, float]] = []
    for label, cmd in steps:
        ok, elapsed = run(label, cmd)
        results.append((label, ok, elapsed))

    # Summary
    banner("Summary")
    total = sum(t for _, _, t in results)
    all_ok = all(ok for _, ok, _ in results)
    for label, ok, elapsed in results:
        mark = f"{C.OK}PASS{C.END}" if ok else f"{C.FAIL}FAIL{C.END}"
        print(_safe(f"  {mark}  {label:<8} {elapsed:>6.1f}s"))
    print(_safe(f"  {C.DIM}total {total:>6.1f}s{C.END}"))

    if all_ok:
        print(_safe(f"\n{C.OK}{C.BOLD}All checks passed.{C.END} Safe to push."))
        return 0
    failed = [label for label, ok, _ in results if not ok]
    print(_safe(
        f"\n{C.FAIL}{C.BOLD}{len(failed)} check(s) failed:{C.END} "
        f"{', '.join(failed)}"
    ))
    return 1


if __name__ == "__main__":
    sys.exit(main())
