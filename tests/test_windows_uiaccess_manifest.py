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


# ---------------------------------------------------------------------------
#  The update helper is the opposite case: it must NOT request UIAccess
# ---------------------------------------------------------------------------
#
# 1.6.0 made the main exe request uiAccess="true".  The updater's helper used
# to be that exe renamed and run from %TEMP%, and Windows refuses to start a
# UIAccess image from outside a secure location (WinError 740), so from 1.6.0
# the helper never started: no update screen, and the keyboard returned only
# through the installer's explorer fallback after a blank gap (production log
# 2026-10-05: "Failed to spawn update relauncher: [WinError 740]").  The helper
# is now its own exe, built plain.  Everything below pins one half of that.


class _Stub:
    """Records what the spec hands each PyInstaller class."""

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        # What a real Analysis exposes and the spec reads or rewrites.
        self.binaries: list = []
        self.datas: list = []
        self.pure: list = []
        self.zipped_data: list = []
        self.zipfiles: list = []
        self.scripts: list = []


def _run_spec(tmp_path: Path) -> dict[str, list[_Stub]]:
    """Execute the spec with PyInstaller's four classes replaced by recorders."""
    made: dict[str, list[_Stub]] = {"Analysis": [], "PYZ": [], "EXE": [], "COLLECT": []}

    def recorder(kind: str):
        def make(*args, **kwargs):
            stub = _Stub(*args, **kwargs)
            made[kind].append(stub)
            return stub

        return make

    namespace = {
        "SPECPATH": str(BUILD_DIR),
        "workpath": str(tmp_path),
        "__file__": str(SPEC),
        **{kind: recorder(kind) for kind in made},
    }
    added = str(REPO_ROOT)
    inserted = added not in sys.path
    if inserted:
        sys.path.insert(0, added)
    try:
        exec(compile(SPEC.read_text(encoding="utf-8"), str(SPEC), "exec"), namespace)
    finally:
        if inserted:
            sys.path.remove(added)
    return made


@pytest.fixture(scope="module")
def made(tmp_path_factory):
    return _run_spec(tmp_path_factory.mktemp("spec"))


class TestTheSpecBuildsAPlainUpdateHelper:
    def _exe(self, made, name: str) -> _Stub:
        matches = [e for e in made["EXE"] if e.kwargs.get("name") == name]
        assert len(matches) == 1, f"expected one EXE named {name!r}, found {len(matches)}"
        return matches[0]

    def test_the_keyboard_still_requests_uiaccess(self, made) -> None:
        """The pair: the helper's change must not have moved the keyboard's."""
        assert self._exe(made, "alpha-osk").kwargs["uac_uiaccess"] is True

    def test_the_helper_does_not(self, made) -> None:
        helper = self._exe(made, "alpha-osk-relauncher")
        assert helper.kwargs["uac_uiaccess"] is False
        assert not helper.kwargs.get("uac_admin"), "an updater that prompts to launch is no help"

    def test_the_helper_is_a_windowed_exe_with_the_apps_icon_and_a_version(self, made) -> None:
        helper = self._exe(made, "alpha-osk-relauncher")
        keyboard = self._exe(made, "alpha-osk")
        assert helper.kwargs["console"] is False
        assert helper.kwargs["icon"] == keyboard.kwargs["icon"]
        version_text = Path(helper.kwargs["version"]).read_text(encoding="utf-8")
        assert "Alpha-OSK Updater" in version_text, "told apart from the keyboard in Task Manager"
        assert "alpha-osk-relauncher.exe" in version_text
        # Its own version resource, not the keyboard's.
        assert helper.kwargs["version"] != keyboard.kwargs["version"]

    def test_the_helper_is_built_from_its_own_entry_script(self, made) -> None:
        helpers = [a for a in made["Analysis"] if a.args[0][0].endswith("relauncher_entry.py")]
        assert len(helpers) == 1
        entry = Path(helpers[0].args[0][0])
        assert entry.is_file()
        text = entry.read_text(encoding="utf-8")
        assert "run_relauncher" in text
        # The launcher's startup (singleton lock, QML, the model) is not wanted.
        assert "keyboard_app" not in text

    def test_both_exes_land_in_the_one_collect(self, made) -> None:
        """One bundle, one _internal: the stage copy and the install see both."""
        (collect,) = made["COLLECT"]
        exes = [a for a in collect.args if isinstance(a, _Stub) and "name" in a.kwargs]
        assert {e.kwargs["name"] for e in exes} == {"alpha-osk", "alpha-osk-relauncher"}
        assert collect.kwargs["name"] == "alpha-osk"

    def test_the_helper_does_not_pull_in_what_the_keyboard_drops(self, made) -> None:
        keyboard_analysis, helper_analysis = made["Analysis"]
        assert helper_analysis.kwargs["excludes"] == keyboard_analysis.kwargs["excludes"]
        assert "torch" in helper_analysis.kwargs["excludes"]


class TestTheHelpersManifestOnTheWire:
    """Through PyInstaller's own manifest writer, as the keyboard's is."""

    def test_the_flag_off_yields_uiaccess_false_and_keeps_the_rest(self, mc, winmanifest) -> None:
        out = winmanifest.create_application_manifest(MANIFEST.read_bytes(), uac_uiaccess=False)
        assert mc.requested_execution_level(out) == {"level": "asInvoker", "uiAccess": "false"}
        assert b"PerMonitorV2" in out, "DPI awareness comes from the file, for both exes"


@windows_only
class TestTheBuildRefusesAHelperWithUIAccess:
    """``build.py::verify_helper_does_not_request_uiaccess`` against a stand-in
    ``dist/alpha-osk/alpha-osk-relauncher.exe``."""

    @pytest.fixture
    def guard(self, mc, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        build = _load("_alpha_osk_build_for_helper", BUILD_DIR / "build.py")
        monkeypatch.setattr(build, "DIST_DIR", tmp_path)
        monkeypatch.setattr(sys, "path", list(sys.path))
        monkeypatch.setitem(sys.modules, "manifest_check", mc)
        return build

    def test_a_helper_that_asks_for_nothing_passes(self, guard, mc, tmp_path: Path) -> None:
        shutil.copyfile(sys.executable, tmp_path / "alpha-osk-relauncher.exe")
        manifest = mc.read_embedded_manifest(sys.executable)
        level = mc.requested_execution_level(manifest) if manifest else None
        if not level or level.get("uiAccess") != "false":
            pytest.skip("this interpreter's manifest does not say uiAccess='false'")
        assert guard.verify_helper_does_not_request_uiaccess() is True

    def test_a_helper_that_requests_uiaccess_fails_the_build(self, guard, tmp_path: Path) -> None:
        if not OSK.exists():
            pytest.skip("this Windows install has no osk.exe")
        shutil.copyfile(OSK, tmp_path / "alpha-osk-relauncher.exe")
        assert guard.verify_helper_does_not_request_uiaccess() is False

    def test_a_helper_with_no_manifest_fails_the_build(self, guard, tmp_path: Path) -> None:
        """Absent is not "false": fail closed, as the keyboard's guard does."""
        shutil.copyfile(r"C:\Windows\System32\kernel32.dll", tmp_path / "alpha-osk-relauncher.exe")
        assert guard.verify_helper_does_not_request_uiaccess() is False

    def test_a_missing_helper_fails_the_build(self, guard) -> None:
        assert guard.verify_helper_does_not_request_uiaccess() is False


class TestThePipelineChecksAndSignsTheHelper:
    def test_both_checks_run_before_signing(self) -> None:
        text = (BUILD_DIR / "build.py").read_text(encoding="utf-8")
        main_body = text[text.index("def main()") :]
        signing = main_body.index("sign_build(signtool)")
        assert main_body.index("verify_exe_requests_uiaccess()") < signing
        assert main_body.index("verify_helper_does_not_request_uiaccess()") < signing

    def test_the_helper_is_verified_after_signing_too(self) -> None:
        text = (BUILD_DIR / "build.py").read_text(encoding="utf-8")
        verify_body = text[text.index("def verify_build(") : text.index("def main()")]
        assert "verify_helper_does_not_request_uiaccess()" in verify_body
        assert "HELPER_EXE_NAME" in verify_body, "the helper's signature is verified too"

    def test_signing_covers_every_exe_in_the_bundle(self) -> None:
        """The helper is signed because ``sign_directory`` signs every ``.exe``
        under the bundle: not by name, so a new exe cannot be forgotten."""
        sign = (BUILD_DIR / "sign.py").read_text(encoding="utf-8")
        body = sign[sign.index("def sign_directory(") : sign.index("def main()")]
        assert "rglob" in body
        assert '"*.exe"' in body
