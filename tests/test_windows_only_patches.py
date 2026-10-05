"""No test may reach a Windows-only ``ctypes`` attribute on a Linux shard.

``ctypes.windll`` (and ``WinDLL``, ``oledll``, ``WINFUNCTYPE``,
``GetLastError`` and the rest of the Windows half of ctypes) exists only
on Windows.  A test that patches one without saying the attribute may be
missing, or reads one outside a test skipped off Windows, passes on every
developer machine, because they are all Windows, and fails only on the
ubuntu shards in CI.  It has broken all four of them at once
(``test_window_corners.py``, 2026-09-07), and ``check.py`` could not
catch it, because the gate runs on the developer's own platform.

This is that check, done statically so it runs on Windows too.  It reads
every test file and flags:

* a patch of a Windows-only attribute that does not create it:
  ``monkeypatch.setattr(ctypes, "windll", ...)`` without
  ``raising=False``, and ``patch("ctypes.windll", ...)`` or
  ``patch.object(ctypes, "windll", ...)`` without ``create=True``;
* a patch target that walks *through* one
  (``patch("ctypes.windll.user32.X")``), which no flag can rescue,
  because the lookup of ``windll`` fails before anything is created;
* ``ctypes.windll`` read in a function that is not skipped off Windows
  (a ``skipif`` naming ``win32`` on the function, its class or the
  module's ``pytestmark``).

The flag is required at every patch site, skipped or not.  Whether a
patch runs off Windows depends on every caller of the function it sits
in, and a helper called only from a skipped test carries no marker of
its own, so deciding it statically would mean guessing.  The flag is
harmless on Windows, so asking for it everywhere costs nothing.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

# The attributes `ctypes` defines only on Windows.
WINDOWS_ONLY = frozenset(
    {
        "windll",
        "WinDLL",
        "oledll",
        "OleDLL",
        "WINFUNCTYPE",
        "HRESULT",
        "FormatError",
        "GetLastError",
        "WinError",
        "get_last_error",
        "set_last_error",
    }
)


def _is_ctypes(node: ast.AST) -> bool:
    """`ctypes`, or `<anything>.ctypes` (a module's own import of it)."""
    if isinstance(node, ast.Name):
        return node.id == "ctypes"
    return isinstance(node, ast.Attribute) and node.attr == "ctypes"


def _keyword_is(call: ast.Call, name: str, value: bool) -> bool:
    return any(
        kw.arg == name and isinstance(kw.value, ast.Constant) and kw.value.value is value
        for kw in call.keywords
    )


def _dotted_target(target: str) -> tuple[str, bool] | None:
    """For a dotted patch target, the Windows-only name it reaches, and
    whether the target *ends* there (fixable with a flag) or walks past it
    (not fixable).  None if it never touches one."""
    parts = target.split(".")
    for i in range(len(parts) - 1):
        if parts[i] == "ctypes" and parts[i + 1] in WINDOWS_ONLY:
            return parts[i + 1], i + 2 == len(parts)
    return None


def _callee(call: ast.Call) -> tuple[str, str]:
    """(attribute, receiver) for `x.attr(...)`, ("name", "") for `name(...)`."""
    func = call.func
    if isinstance(func, ast.Attribute):
        receiver = func.value
        receiver_name = (
            receiver.id
            if isinstance(receiver, ast.Name)
            else receiver.attr
            if isinstance(receiver, ast.Attribute)
            else ""
        )
        return func.attr, receiver_name
    if isinstance(func, ast.Name):
        return func.id, ""
    return "", ""


def _patch_problems(call: ast.Call) -> Iterator[str]:
    """Problems with one call, if it is a patch of a Windows-only name."""
    attr, receiver = _callee(call)
    args = call.args

    if attr == "setattr" and receiver != "":  # monkeypatch.setattr / mp.setattr
        if len(args) >= 2 and _is_ctypes(args[0]):
            name = args[1]
            if isinstance(name, ast.Constant) and name.value in WINDOWS_ONLY:
                if not _keyword_is(call, "raising", False):
                    yield f'setattr(ctypes, "{name.value}", ...) needs raising=False'
                return
        if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            hit = _dotted_target(args[0].value)
            if hit is not None:
                name, ends = hit
                if not ends:
                    yield f'setattr("{args[0].value}") walks through {name}; fake {name} itself'
                elif not _keyword_is(call, "raising", False):
                    yield f'setattr("{args[0].value}", ...) needs raising=False'
        # `monkeypatch.setattr(ctypes.windll.shell32, ...)` reads windll;
        # the attribute-read check covers it.
        return

    if attr == "object" and receiver == "patch":  # patch.object(ctypes, "windll")
        if len(args) >= 2 and _is_ctypes(args[0]):
            name = args[1]
            if isinstance(name, ast.Constant) and name.value in WINDOWS_ONLY:
                if not _keyword_is(call, "create", True):
                    yield f'patch.object(ctypes, "{name.value}") needs create=True'
        return

    if attr == "patch":  # patch("..."), mock.patch("..."), unittest.mock.patch("...")
        if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            hit = _dotted_target(args[0].value)
            if hit is not None:
                name, ends = hit
                if not ends:
                    yield f'patch("{args[0].value}") walks through {name}; fake {name} itself'
                elif not _keyword_is(call, "create", True):
                    yield f'patch("{args[0].value}") needs create=True'


def _skips_off_windows(decorators: list[ast.expr]) -> bool:
    """A `skipif` whose condition names win32."""
    for dec in decorators:
        if isinstance(dec, ast.Call) and "skipif" in ast.unparse(dec.func):
            if "win32" in ast.unparse(dec):
                return True
    return False


def _module_skips_off_windows(tree: ast.Module) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets
        ):
            if "skipif" in ast.unparse(node.value) and "win32" in ast.unparse(node.value):
                return True
    return False


class _Scanner(ast.NodeVisitor):
    def __init__(self, module_skipped: bool) -> None:
        self.skipped = [module_skipped]
        self.problems: list[tuple[int, str]] = []

    def _scoped(self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> None:
        self.skipped.append(self.skipped[-1] or _skips_off_windows(node.decorator_list))
        self.generic_visit(node)
        self.skipped.pop()

    visit_FunctionDef = _scoped
    visit_AsyncFunctionDef = _scoped
    visit_ClassDef = _scoped

    def visit_Call(self, node: ast.Call) -> None:
        for message in _patch_problems(node):
            self.problems.append((node.lineno, message))
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in WINDOWS_ONLY and _is_ctypes(node.value) and not self.skipped[-1]:
            self.problems.append(
                (
                    node.lineno,
                    f"ctypes.{node.attr} is read in a test not skipped off Windows",
                )
            )
        self.generic_visit(node)


def problems_in(source: str) -> list[tuple[int, str]]:
    tree = ast.parse(source)
    scanner = _Scanner(_module_skips_off_windows(tree))
    scanner.visit(tree)
    return sorted(scanner.problems)


def _test_files() -> list[Path]:
    return sorted(p for p in TESTS_DIR.rglob("*.py") if p.name != Path(__file__).name)


@pytest.mark.parametrize("path", _test_files(), ids=lambda p: p.relative_to(TESTS_DIR).as_posix())
def test_no_windows_only_ctypes_reach_a_linux_shard(path: Path) -> None:
    found = problems_in(path.read_text(encoding="utf-8"))
    assert not found, "\n".join(f"{path.name}:{line}: {msg}" for line, msg in found)


class TestTheScannerItself:
    """Each rule paired with the near-miss it must accept.  A scanner that
    flagged nothing would pass the sweep above on any tree."""

    @pytest.mark.parametrize(
        "source",
        [
            'def test_x(mp):\n    mp.setattr(ctypes, "windll", fake)\n',
            'def test_x(mp):\n    mp.setattr("ctypes.windll", fake)\n',
            'def test_x():\n    p = patch("ctypes.windll", fake)\n',
            'def test_x():\n    p = mock.patch("src.mod.ctypes.WinDLL", fake)\n',
            'def test_x():\n    p = patch.object(ctypes, "oledll", fake)\n',
            # No flag rescues a walk through windll: the lookup fails first.
            'def test_x():\n    p = patch("ctypes.windll.user32.X", fake, create=True)\n',
            "def test_x():\n    ctypes.windll.user32.GetForegroundWindow()\n",
            "def test_x(mp):\n    mp.setattr(ctypes.windll.shell32, 'F', f)\n",
            # Skipped on Linux only, which is the wrong way round.
            "@pytest.mark.skipif(sys.platform == 'linux', reason='')\n"
            "def test_x():\n    ctypes.GetLastError()\n",
        ],
    )
    def test_flags(self, source: str) -> None:
        assert problems_in(source)

    @pytest.mark.parametrize(
        "source",
        [
            'def test_x(mp):\n    mp.setattr(ctypes, "windll", fake, raising=False)\n',
            'def test_x(mp):\n    mp.setattr("ctypes.windll", fake, raising=False)\n',
            'def test_x():\n    p = patch("ctypes.windll", fake, create=True)\n',
            'def test_x():\n    p = patch.object(ctypes, "oledll", fake, create=True)\n',
            # Cross-platform ctypes is fine.
            'def test_x(mp):\n    mp.setattr(ctypes, "sizeof", f)\n',
            "def test_x():\n    ctypes.c_int(1)\n",
            "@pytest.mark.skipif(sys.platform != 'win32', reason='')\n"
            "def test_x():\n    ctypes.windll.user32.GetForegroundWindow()\n",
            "@pytest.mark.skipif(sys.platform != 'win32', reason='')\n"
            "class TestX:\n    def test_x(self):\n        ctypes.windll.kernel32\n",
            "pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='')\n"
            "def test_x():\n    ctypes.WinDLL('user32')\n",
        ],
    )
    def test_accepts(self, source: str) -> None:
        assert problems_in(source) == []
