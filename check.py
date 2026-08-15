"""
Pre-push sanity check.

Runs the same checks GitHub Actions runs in .github/workflows/ci.yml:
    1. ruff check src/ tests/
    2. ruff format --check src/ tests/
    3. mypy src/ --ignore-missing-imports --platform linux
       (the platform CI type-checks on)
    4. mypy src/ --ignore-missing-imports --platform win32
       (the `if sys.platform == "win32"` bodies, which mypy prunes as
       unreachable under the pass above)
    5. pytest

Usage:
    python check.py                # the full gate (~1 min)
    python check.py --full         # adds the --cov-fail-under=60 gate
    python check.py --serial       # one process, for debugging
    python check.py --install-hook # run it automatically on git push

Exits 0 if everything passes, 1 if any step fails.

Why this is not tiered
----------------------
There is deliberately no "fast subset" mode.  There was going to be one,
because this script had grown to well over twenty minutes and the
obvious fix for a slow gate is to run less of it.  Measuring first turned
up something better: the three static steps cost about five seconds
between them, so the gate *was* pytest, and pytest was slow for a reason
that had nothing to do with how many tests there are.

Building a ``KeyboardBridge`` cost ~1 s (a 20k-word dictionary, a
SymSpell deletion index, a PPM model), the ``bridge`` fixture is
function-scoped, and there are ~1300 tests.  That is per-process setup
repeated 1300 times: embarrassingly parallel, and untouched by any
amount of clever test selection.  Sharding it with pytest-xdist took the
suite from 25 minutes to under a minute, which is fast enough that
skipping tests would buy seconds while costing exactly the coverage this
script exists to provide.  (Half of the per-bridge cost also turned out
to be a duplicated SymSpell build in ``HybridPredictor.__init__``, which
was a real bug: it slowed every app launch too.)

If it creeps back up, measure before tiering.  A single slow test is
worth fixing; per-test setup multiplied by the suite is worth
parallelising; neither is worth trading away test coverage on the one
gate that runs before code leaves the machine.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


# ANSI colours, falling back to no-colour on terminals that don't support them.
class C:
    HEADER = "\033[95m"
    OK = "\033[92m"
    FAIL = "\033[91m"
    WARN = "\033[93m"
    DIM = "\033[90m"
    BOLD = "\033[1m"
    END = "\033[0m"


REPO_ROOT = Path(__file__).resolve().parent

# Installed by --install-hook.  Prefers the repo's venv over whatever
# `python` happens to be on PATH, because that is where the pinned ruff /
# mypy / pytest live -- a system interpreter would either fail to import
# them or, worse, run a different version and disagree with CI.
# `git push --no-verify` remains the escape hatch.
_PRE_PUSH_HOOK = """#!/bin/sh
# Alpha-OSK pre-push gate.  Installed by `python check.py --install-hook`.
# Skip once with: git push --no-verify
if [ -x "venv/Scripts/python.exe" ]; then
    PY="venv/Scripts/python.exe"
elif [ -x "venv/bin/python" ]; then
    PY="venv/bin/python"
else
    PY=python
fi
exec "$PY" check.py
"""


def _safe(s: str) -> str:
    try:
        s.encode(sys.stdout.encoding or "utf-8")
        return s
    except (UnicodeEncodeError, LookupError):
        return s.encode("ascii", errors="replace").decode("ascii")


def banner(msg: str) -> None:
    print(_safe(f"\n{C.HEADER}{C.BOLD}== {msg} =={C.END}"))


def _child_creationflags() -> int:
    """``CREATE_NO_WINDOW`` when there is no console for children to inherit.

    Every step here launches a console-subsystem binary (``python -m
    ruff`` and friends) and deliberately does *not* capture its output,
    so it streams to the terminal.  That is right whenever a terminal
    exists: the child inherits this process's console and no window
    appears.

    Run from the pre-push hook, there may be no console at all.  Git for
    Windows executes hooks through ``sh.exe``, and when the push itself
    came from a GUI (an IDE's source-control panel, a git client),
    nothing in that chain owns a console.  Windows then allocates a fresh
    one per child, which is where the two blank windows titled with the
    repo path come from -- one per ruff step, the two fastest steps, so
    they appear and vanish while the slower ones are still to come.

    Probing ``GetConsoleWindow`` rather than ``isatty`` is deliberate.
    stdout being a pipe does not mean there is no console: a hook run
    from a terminal has both, and there the flag is unwanted, because a
    process created with ``CREATE_NO_WINDOW`` has no console of its own
    and ruff and pytest would drop their colour. Asking about the
    console directly keeps the ordinary terminal path byte-identical to
    what it has always done, and changes behaviour only in the case that
    actually pops a window.  Inherited stdout / stderr handles are
    unaffected either way, so git still shows the output.
    """
    if sys.platform != "win32":
        return 0
    # Two conditions, and the tty one is first because it is the one that
    # is certain.  `GetConsoleWindow` is the direct question but its
    # answer is not always what the name suggests: it reports 0 for a
    # process attached to a pseudo-console, and for one launched with its
    # output on a pipe, so on its own it would set the flag for an
    # ordinary terminal run too.  Requiring stdout to be a non-tty as
    # well means the hand-run path can never take this branch at all,
    # whatever the console probe says.
    try:
        if sys.stdout is not None and sys.stdout.isatty():
            return 0
    except (AttributeError, ValueError):
        pass
    try:
        if _get_console_window() != 0:
            return 0
    except (AttributeError, OSError):
        # Fail open.  Getting this wrong costs a blank window; raising
        # here would cost the gate itself.
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _get_console_window() -> int:
    """``GetConsoleWindow()``: the console's HWND, or 0 if there is none.

    Split out so the branch above is testable from a terminal, which is
    the only place anyone runs this script by hand and therefore the one
    place the interesting case never occurs.
    """
    import ctypes

    return int(ctypes.windll.kernel32.GetConsoleWindow())


def run(label: str, cmd: list[str], creationflags: int = 0) -> tuple[bool, float]:
    """Run a CI step.  Returns (ok, elapsed_seconds)."""
    banner(label)
    print(_safe(f"{C.DIM}$ {' '.join(cmd)}{C.END}"))
    start = time.perf_counter()
    rc = subprocess.run(cmd, creationflags=creationflags).returncode
    elapsed = time.perf_counter() - start
    ok = rc == 0
    status = f"{C.OK}OK{C.END}" if ok else f"{C.FAIL}FAIL (exit {rc}){C.END}"
    print(_safe(f"{status}  {label} ({elapsed:.1f}s)"))
    return ok, elapsed


def have(tool: str) -> bool:
    return shutil.which(tool) is not None or _have_module(tool)


def _have_module(name: str) -> bool:
    """Some tools (ruff, mypy, pytest) ship as Python modules."""
    try:
        subprocess.run(
            [sys.executable, "-m", name, "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            # Output is discarded; CREATE_NO_WINDOW suppresses the flash
            # of a console window on Windows when the parent has no
            # inherited console.  No effect on POSIX.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _have_xdist() -> bool:
    """Is pytest-xdist importable?

    Probed rather than assumed: it is pinned in requirements-dev.txt, but
    a contributor on a venv built before that pin landed would otherwise
    get an opaque "unrecognized arguments: -n" out of pytest.
    """
    try:
        import xdist  # noqa: F401
    except ImportError:
        return False
    return True


def install_hook() -> int:
    """Write .git/hooks/pre-push so the gate runs on push, not by hand."""
    hooks_dir = REPO_ROOT / ".git" / "hooks"
    if not hooks_dir.is_dir():
        print(_safe(f"{C.FAIL}No .git/hooks directory: not a git checkout?{C.END}"))
        return 1
    hook = hooks_dir / "pre-push"
    if hook.exists() and "Alpha-OSK pre-push gate" not in hook.read_text(encoding="utf-8"):
        print(
            _safe(
                f"{C.WARN}A pre-push hook already exists and isn't ours; "
                f"leaving it alone.{C.END}\n  {hook}"
            )
        )
        return 1
    hook.write_text(_PRE_PUSH_HOOK, encoding="utf-8", newline="\n")
    # Git for Windows runs hooks through sh, which honours the exec bit on
    # filesystems that have one; setting it is a no-op elsewhere.
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(_safe(f"{C.OK}Installed {hook}{C.END}"))
    print(_safe(f"{C.DIM}`git push` now runs check.py first. Skip with --no-verify.{C.END}"))
    return 0


def main() -> int:
    argv = sys.argv[1:]
    if "--install-hook" in argv:
        return install_hook()
    full = "--full" in argv
    serial = "--serial" in argv
    py = sys.executable

    pytest_cmd = [py, "-m", "pytest", "-q"]
    parallel = not serial and _have_xdist()
    if parallel:
        # The suite's cost is per-process setup repeated per test, so it
        # shards almost linearly.  See the module docstring.
        pytest_cmd += ["-n", "auto"]
    if full:
        pytest_cmd += [
            "--cov=src",
            "--cov-report=term-missing",
            "--cov-fail-under=60",
        ]
    steps = [
        ("ruff", [py, "-m", "ruff", "check", "src/", "tests/"]),
        ("format", [py, "-m", "ruff", "format", "--check", "src/", "tests/"]),
        # Both platforms, and both are load-bearing. --platform linux is
        # what CI type-checks under, and typeshed gates whole symbols on
        # the platform (ctypes.WinDLL does not exist off Windows, so a
        # call to it degrades to Any and trips warn_return_any); without
        # it the gate passes on a Windows dev box and fails on the
        # runner, which is the one thing check.py exists to prevent.
        # But mypy also prunes `if sys.platform == "win32"` bodies as
        # unreachable, so pinning to linux alone type-checked the Windows
        # half of every platform branch *nowhere*: a deliberate
        # `int = "str"` inside _window_class_name's Windows branch was
        # invisible. The second pass is what covers them.
        ("mypy", [py, "-m", "mypy", "src/", "--ignore-missing-imports", "--platform", "linux"]),
        (
            "mypy-win32",
            [py, "-m", "mypy", "src/", "--ignore-missing-imports", "--platform", "win32"],
        ),
        ("pytest", pytest_cmd),
    ]

    # Probe the module named in the command (cmd[2]), not the step label.
    # They used to be assumed identical, which silently made "format" a
    # requirement to have a `format` module installed.
    missing = sorted({cmd[2] for _, cmd in steps if not _have_module(cmd[2])})
    if missing:
        print(
            _safe(
                f"{C.FAIL}Missing tools: {', '.join(missing)}.{C.END}\n"
                f"Install with: pip install -r requirements-dev.txt"
            )
        )
        return 1
    if not parallel and not serial:
        print(
            _safe(
                f"{C.WARN}pytest-xdist not installed: running the suite in one "
                f"process, which takes ~25x longer.{C.END}\n"
                f"{C.DIM}Fix with: pip install -r requirements-dev.txt{C.END}"
            )
        )

    results: list[tuple[str, bool, float]] = []
    creationflags = _child_creationflags()
    for label, cmd in steps:
        ok, elapsed = run(label, cmd, creationflags)
        results.append((label, ok, elapsed))

    # Summary
    banner("Summary")
    total = sum(t for _, _, t in results)
    all_ok = all(ok for _, ok, _ in results)
    for label, ok, elapsed in results:
        mark = f"{C.OK}PASS{C.END}" if ok else f"{C.FAIL}FAIL{C.END}"
        print(_safe(f"  {mark}  {label:<10} {elapsed:>6.1f}s"))
    print(_safe(f"  {C.DIM}total {total:>6.1f}s{C.END}"))

    if all_ok:
        print(_safe(f"\n{C.OK}{C.BOLD}All checks passed.{C.END} Safe to push."))
        if (
            not (REPO_ROOT / ".git" / "hooks" / "pre-push").exists()
            and os.environ.get("GITHUB_ACTIONS") is None
        ):
            print(
                _safe(
                    f"{C.DIM}Tip: `python check.py --install-hook` runs this "
                    f"automatically on git push.{C.END}"
                )
            )
        return 0
    failed = [label for label, ok, _ in results if not ok]
    print(_safe(f"\n{C.FAIL}{C.BOLD}{len(failed)} check(s) failed:{C.END} {', '.join(failed)}"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
