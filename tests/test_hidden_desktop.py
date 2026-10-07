"""The build's helper self-test runs on a desktop nobody is looking at.

A staged helper that cannot start raises PyInstaller's bootloader MessageBox
("Failed to load Python DLL"), and offscreen Qt does nothing about that: the
dialog is the bootloader's, before Python or Qt exist.  A self-test run on
the interactive desktop would put it in front of whoever is at the machine
and wait for a click, so ``build/windows/hidden_desktop.py`` runs it on a
desktop created for the purpose.  These tests use only a harmless child
(the base interpreter) and never a frozen exe.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO_ROOT / "build" / "windows"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


@pytest.fixture(scope="module")
def hd():
    return _load("_alpha_osk_hidden_desktop", BUILD_DIR / "hidden_desktop.py")


def _python() -> str:
    # The base interpreter, not a venv's launcher shim: the shim starts the
    # real interpreter as a child, and killing the shim would orphan it.
    base = Path(sys.base_prefix) / "python.exe"
    return str(base if base.exists() else sys.executable)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktops")
class TestRunningOnAHiddenDesktop:
    def test_the_exit_code_comes_back(self, hd) -> None:
        assert hd.run_on_hidden_desktop([_python(), "-c", "raise SystemExit(7)"]) == 7
        assert hd.run_on_hidden_desktop([_python(), "-c", "pass"]) == 0

    def test_the_child_really_is_on_a_desktop_of_its_own(self, hd, tmp_path: Path) -> None:
        out = tmp_path / "desktop.txt"
        code = (
            "import ctypes, sys\n"
            "u = ctypes.windll.user32\n"
            "u.GetThreadDesktop.restype = ctypes.c_void_p\n"
            "h = u.GetThreadDesktop(ctypes.windll.kernel32.GetCurrentThreadId())\n"
            "buf = ctypes.create_unicode_buffer(256)\n"
            "n = ctypes.c_ulong()\n"
            "u.GetUserObjectInformationW(ctypes.c_void_p(h), 2, buf, 512, ctypes.byref(n))\n"
            "open(sys.argv[1], 'w').write(buf.value)\n"
        )
        assert hd.run_on_hidden_desktop([_python(), "-c", code, str(out)]) == 0
        name = out.read_text()
        assert name.startswith("aosk-test-"), (
            f"the child ran on {name!r}: that is not the private desktop, so a dialog "
            "it raised would land in front of the user"
        )
        assert name != "Default"

    def test_a_child_that_overruns_is_stopped_and_reports_none(self, hd, tmp_path: Path) -> None:
        pid_file = tmp_path / "pid.txt"
        code = (
            "import os, sys, time\nopen(sys.argv[1], 'w').write(str(os.getpid()))\ntime.sleep(30)\n"
        )
        started = time.monotonic()
        assert (
            hd.run_on_hidden_desktop([_python(), "-c", code, str(pid_file)], timeout_s=1.5) is None
        )
        assert time.monotonic() - started < 15, "the timeout has to bite, not wait out the child"
        pid = int(pid_file.read_text())
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            code_out = ctypes.c_ulong()
            kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code_out))
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            assert code_out.value != 259, "the overrunning child is still running"

    def test_a_program_that_cannot_be_created_raises_not_falls_back(
        self, hd, tmp_path: Path
    ) -> None:
        with pytest.raises(OSError):
            hd.run_on_hidden_desktop([str(tmp_path / "no-such-program.exe")])

    def test_the_environment_given_is_the_environment_seen(self, hd, tmp_path: Path) -> None:
        out = tmp_path / "env.txt"
        env = dict(os.environ, AOSK_PROBE="hello there")
        code = "import os, sys; open(sys.argv[1], 'w').write(os.environ.get('AOSK_PROBE', ''))"
        assert hd.run_on_hidden_desktop([_python(), "-c", code, str(out)], env=env) == 0
        assert out.read_text() == "hello there"


class TestTheBuildUsesIt:
    def _body(self) -> str:
        text = (BUILD_DIR / "build.py").read_text(encoding="utf-8")
        return text[text.index("def verify_helper_stage_runs") : text.index("def verify_build(")]

    def test_the_staged_helper_is_never_run_on_the_users_desktop(self) -> None:
        body = self._body()
        assert "run_on_hidden_desktop(" in body
        for call in ("subprocess.run(", "subprocess.Popen(", "subprocess.call(", "os.startfile("):
            assert call not in body, (
                f"{call} on the staged helper would run it on the interactive desktop, "
                "where a bootloader error dialog is seen and waits for a click"
            )

    def test_the_check_runs_before_anything_is_signed_or_packaged(self) -> None:
        text = (BUILD_DIR / "build.py").read_text(encoding="utf-8")
        main_body = text[text.index("def main()") :]
        assert main_body.index("verify_helper_stage_runs()") < main_body.index(
            "sign_build(signtool)"
        )
