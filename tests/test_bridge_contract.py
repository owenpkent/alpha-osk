"""Every ``keyboard.<name>`` the QML calls exists on both backends.

``qml/`` is **shared**: the Python backend (``src/``) and the C++ rewrite
(``cpp/``, on the ``cpp-rewrite`` branch) render the same QML against
their own ``KeyboardBridge``.  That sharing is what keeps the two cheap
to hold in sync, and it is also the one surface where a change to one
backend silently breaks the other: adding a slot in Python and calling it
from QML is a complete, working, green change here, and it lands on the
C++ branch at the next merge as a runtime TypeError in whatever panel
uses it.

Nothing caught that before.  ``tests/conformance/`` diffs the two
*prediction engines*, which is the deepest surface but not the one that
drifts -- the QML-to-bridge contract is wider, changes far more often,
and has no compiler on either side, because QML resolves these names at
call time.

The check is deliberately crude in one direction and strict in the
other.  A name QML never calls is allowed to exist on either bridge
(both carry internal API and Python carries slots QML dropped), so
this only walks *outward* from the QML.  What it will not tolerate is a
name the QML does call and a bridge does not have.

The C++ half **skips** rather than fails when ``cpp/`` is absent, which
is the normal state on ``main``: the sources live on ``cpp-rewrite``.
That makes this a test that grows teeth exactly where it is needed --
green on ``main``, load-bearing on the branch where the two backends
actually sit side by side.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
QML_DIR = REPO_ROOT / "qml"
CPP_DIR = REPO_ROOT / "cpp"

# `keyboard.foo(` / `keyboard.foo` in any .qml file.  The trailing
# boundary keeps `keyboard.setSnippetColor` from also matching as
# `keyboard.setSnippet`, which is exactly the false negative that made an
# earlier hand-grep of this miss a genuinely absent slot.
_CALL_RE = re.compile(r"\bkeyboard\.([A-Za-z_][A-Za-z0-9_]*)\b")

# Properties are bound, not called, and are declared with Property() /
# Q_PROPERTY rather than @Slot / Q_INVOKABLE.  Collected separately so a
# missing *property* reports as a missing property.
_PY_SLOT_RE = re.compile(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE)
_PY_PROP_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*Property\(", re.MULTILINE)
_CPP_INVOKABLE_RE = re.compile(
    r"Q_INVOKABLE\s+[\w:<>,\s&*]+?\b([A-Za-z_][A-Za-z0-9_]*)\s*\(",
)
_CPP_PROP_RE = re.compile(r"Q_PROPERTY\s*\([^)]*?\bREAD\s+([A-Za-z_][A-Za-z0-9_]*)")
_CPP_PROP_NAME_RE = re.compile(r"Q_PROPERTY\s*\(\s*[\w:<>,\s*&]+?\b([A-Za-z_][A-Za-z0-9_]*)\s+READ")

# Names QML reaches on the bridge that are neither slots nor properties:
# Qt signals, which QML connects to via `onFooChanged` / Connections and
# which are declared as Signal(...) / Q_SIGNALS on each side.
_PY_SIGNAL_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*Signal\(", re.MULTILINE)
_CPP_SIGNAL_RE = re.compile(
    r"^\s*(?:void\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\([^;{]*\)\s*;", re.MULTILINE
)


def _qml_calls() -> dict[str, set[Path]]:
    """Every ``keyboard.<name>`` in ``qml/``, mapped to the files using it."""
    found: dict[str, set[Path]] = {}
    for path in sorted(QML_DIR.rglob("*.qml")):
        for name in _CALL_RE.findall(path.read_text(encoding="utf-8")):
            found.setdefault(name, set()).add(path.relative_to(REPO_ROOT))
    return found


def _python_bridge_names() -> set[str]:
    src = (REPO_ROOT / "src" / "keyboard_bridge.py").read_text(encoding="utf-8")
    return (
        set(_PY_SLOT_RE.findall(src))
        | set(_PY_PROP_RE.findall(src))
        | set(_PY_SIGNAL_RE.findall(src))
    )


def _cpp_bridge_names() -> set[str]:
    header = CPP_DIR / "KeyboardBridge.h"
    src = header.read_text(encoding="utf-8")
    return (
        set(_CPP_INVOKABLE_RE.findall(src))
        | set(_CPP_PROP_RE.findall(src))
        | set(_CPP_PROP_NAME_RE.findall(src))
        | set(_CPP_SIGNAL_RE.findall(src))
    )


def test_the_scan_finds_something() -> None:
    """The inverse guard.

    Every assertion below is "the QML's calls are a subset of the
    bridge's names", which a regex that matched nothing would satisfy
    perfectly.  This is what stops the file passing while checking
    nothing at all.
    """
    calls = _qml_calls()
    assert len(calls) > 30, f"only found {len(calls)} keyboard.* calls; the scan is broken"
    assert "pressKey" in calls
    assert len(_python_bridge_names()) > 50


def test_every_qml_call_exists_on_the_python_bridge() -> None:
    calls = _qml_calls()
    have = _python_bridge_names()
    missing = {
        name: sorted(str(p) for p in files) for name, files in calls.items() if name not in have
    }
    assert not missing, (
        "QML calls names the Python KeyboardBridge does not define. QML "
        "resolves these at call time, so each one is a runtime TypeError "
        f"in the panel that uses it:\n{missing}"
    )


@pytest.mark.skipif(
    not (CPP_DIR / "KeyboardBridge.h").is_file(),
    reason="cpp/ lives on the cpp-rewrite branch; nothing to compare against here",
)
def test_every_qml_call_exists_on_the_cpp_bridge() -> None:
    """The one this file exists for.

    ``qml/`` is shared, so a slot added on the Python side and called
    from QML reaches the C++ backend at the next merge whether or not
    anyone ported it.  It does not fail to build -- QML resolves the name
    at call time -- so the first sign is the feature quietly not working.

    When this fails the fix is to port the slot, not to relax the test.
    Update the Snippets / relevant row in
    ``docs/architecture/BACKEND_PARITY.md`` in the same change, per that
    file's own "Keeping this current" rule.
    """
    calls = _qml_calls()
    have = _cpp_bridge_names()
    missing = {
        name: sorted(str(p) for p in files) for name, files in calls.items() if name not in have
    }
    assert not missing, (
        "QML calls names the C++ KeyboardBridge does not define, so these "
        "panels are broken on the C++ backend:\n"
        f"{missing}\n"
        "Port them to cpp/KeyboardBridge.{h,cpp} and update "
        "docs/architecture/BACKEND_PARITY.md in the same change."
    )
