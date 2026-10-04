"""The exe's embedded manifest has to request UIAccess, and the build checks it.

Found on the installed 1.5.0: ``alpha-osk.exe`` carried
``<requestedExecutionLevel level="asInvoker" uiAccess="false"/>`` and the
running keyboard's token had no UIAccess, although
``build/windows/alpha-osk.exe.manifest`` says ``uiAccess="true"``.  The
spec handed PyInstaller the file through ``manifest=`` and nothing else,
and PyInstaller 6 rewrites ``requestedExecutionLevel`` from
``EXE(uac_admin=..., uac_uiaccess=...)`` whatever the file says.
PyInstaller 5 kept the file's ``uiAccess`` (the 1.1.0 lockfile shows 5.12),
so it was the upgrade to 6 that turned it off: 1.2.0 through 1.5.0 asked
Windows for nothing and could not type into an elevated window.

Every positive case here is paired with the near-miss it must reject.  The
pair that bites is the PyInstaller round trip with the flag left off: the
file still says ``"true"`` and the result still says ``"false"``, which is
the whole bug in two lines.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO_ROOT / "build" / "windows"
SPEC = BUILD_DIR / "alpha-osk.spec"
MANIFEST = BUILD_DIR / "alpha-osk.exe.manifest"
# Microsoft's own on-screen keyboard, which carries uiAccess="true": the one
# exe on every Windows desktop that is known to ask for exactly this.
OSK = Path(r"C:\Windows\System32\osk.exe")

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="reads PE resources")


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
def mc():
    return _load("_alpha_osk_manifest_check", BUILD_DIR / "manifest_check.py")


@pytest.fixture(scope="module")
def winmanifest():
    return pytest.importorskip("PyInstaller.utils.win32.winmanifest")


def _exe_block() -> str:
    spec = SPEC.read_text(encoding="utf-8")
    return spec[spec.index("exe = EXE(") : spec.index("coll = COLLECT(")]


class TestTheSpecRequestsUIAccess:
    def test_the_exe_call_passes_uac_uiaccess(self) -> None:
        """The flag, not the file, is what PyInstaller writes into the exe."""
        assert re.search(r"^\s*uac_uiaccess\s*=\s*True\s*,", _exe_block(), re.MULTILINE), (
            "EXE() does not pass uac_uiaccess=True, so PyInstaller overwrites the "
            "manifest's uiAccess with 'false' and the keyboard cannot type into "
            "elevated windows"
        )

    def test_the_exe_call_still_embeds_the_manifest_file(self) -> None:
        """The rest of the manifest (DPI awareness, the identity) still comes
        from the file, so the flag must be an addition, not a replacement."""
        assert re.search(
            r"^\s*manifest\s*=\s*str\(SPEC_DIR / 'alpha-osk\.exe\.manifest'\)",
            _exe_block(),
            re.MULTILINE,
        )

    def test_it_does_not_ask_for_elevation_instead(self) -> None:
        """``uac_admin=True`` would make every launch a UAC prompt, which is
        the always-admin design UIAccess exists to avoid."""
        assert not re.search(r"^\s*uac_admin\s*=\s*True", _exe_block(), re.MULTILINE)


class TestWhatPyInstallerWritesIntoTheExe:
    """Round trip through PyInstaller's own manifest writer, when it is
    installed: the overwrite is their behaviour, so it is checked against
    their code rather than restated."""

    def test_the_flag_yields_uiaccess_true(self, mc, winmanifest) -> None:
        out = winmanifest.create_application_manifest(MANIFEST.read_bytes(), uac_uiaccess=True)
        level = mc.requested_execution_level(out)
        assert level == {"level": "asInvoker", "uiAccess": "true"}

    def test_without_the_flag_the_files_true_is_overwritten(self, mc, winmanifest) -> None:
        """The trap itself.  The file says "true"; what reaches the exe says
        "false".  The comment in the file also mentions ``uiAccess="true"``
        and survives the rewrite, which is why the build guard parses the
        XML instead of searching the bytes: a substring check passes here."""
        source = MANIFEST.read_bytes()
        assert mc.requested_execution_level(source)["uiAccess"] == "true"

        out = winmanifest.create_application_manifest(source, uac_uiaccess=False)
        assert mc.requested_execution_level(out) == {"level": "asInvoker", "uiAccess": "false"}
        assert b'uiAccess="true"' in out, "the comment no longer carries the decoy"

    def test_the_rest_of_the_manifest_survives(self, winmanifest) -> None:
        """The reason to keep ``manifest=`` at all."""
        out = winmanifest.create_application_manifest(MANIFEST.read_bytes(), uac_uiaccess=True)
        assert b"PerMonitorV2" in out
        assert b"AlphaOSK.OnScreenKeyboard" in out


@windows_only
class TestTheManifestReader:
    def test_microsofts_osk_requests_uiaccess(self, mc) -> None:
        if not OSK.exists():
            pytest.skip("this Windows install has no osk.exe")
        assert mc.requests_uiaccess(OSK) is True

    def test_python_does_not(self, mc) -> None:
        """Python carries a manifest with ``uiAccess="false"``, so this goes
        through the parser rather than the no-manifest branch."""
        assert mc.read_embedded_manifest(sys.executable) is not None
        assert mc.requests_uiaccess(sys.executable) is False

    def test_a_binary_with_no_manifest_reads_as_no(self, mc) -> None:
        kernel32 = Path(r"C:\Windows\System32\kernel32.dll")
        assert mc.read_embedded_manifest(kernel32) is None
        assert mc.requests_uiaccess(kernel32) is False

    def test_a_missing_file_is_an_error_not_a_no(self, mc, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            mc.read_embedded_manifest(tmp_path / "absent.exe")


@windows_only
class TestTheBuildRefusesAnExeWithoutUIAccess:
    """``build.py::verify_exe_requests_uiaccess`` against a stand-in
    ``dist/alpha-osk/alpha-osk.exe``."""

    @pytest.fixture
    def guard(self, mc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        build = _load("_alpha_osk_build_for_uiaccess", BUILD_DIR / "build.py")
        monkeypatch.setattr(build, "DIST_DIR", tmp_path)
        # The guard imports manifest_check by name after putting build/windows
        # on sys.path; hand it the loaded module and keep both changes local.
        monkeypatch.setattr(sys, "path", list(sys.path))
        monkeypatch.setitem(sys.modules, "manifest_check", mc)
        return build

    def test_an_exe_that_requests_it_passes(self, guard, tmp_path: Path) -> None:
        if not OSK.exists():
            pytest.skip("this Windows install has no osk.exe")
        shutil.copyfile(OSK, tmp_path / "alpha-osk.exe")
        assert guard.verify_exe_requests_uiaccess() is True

    def test_an_exe_that_does_not_fails_the_build(self, guard, tmp_path: Path) -> None:
        shutil.copyfile(sys.executable, tmp_path / "alpha-osk.exe")
        assert guard.verify_exe_requests_uiaccess() is False

    def test_an_exe_with_no_manifest_fails_the_build(self, guard, tmp_path: Path) -> None:
        shutil.copyfile(r"C:\Windows\System32\kernel32.dll", tmp_path / "alpha-osk.exe")
        assert guard.verify_exe_requests_uiaccess() is False

    def test_a_missing_exe_fails_the_build(self, guard) -> None:
        """An empty or incomplete dist directory must not pass the guard."""
        assert guard.verify_exe_requests_uiaccess() is False
