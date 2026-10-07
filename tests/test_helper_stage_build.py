"""The build's half of staging the update helper.

* ``verify_helper_stage_runs`` fails the build when the helper's file list is
  missing, so a build that lost it cannot quietly put every update back on the
  slow whole-bundle copy.
* The installer extracts ``alpha-osk.exe`` last, which is what lets the helper
  treat "the installed exe changed and is closed" as "the install finished"
  and drop its fixed pause.  Pinned here because nothing else would notice the
  file order changing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO_ROOT / "build" / "windows"


@pytest.fixture
def build(monkeypatch, tmp_path: Path):
    spec = importlib.util.spec_from_file_location("_alpha_osk_build_stage", BUILD_DIR / "build.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_alpha_osk_build_stage"] = module
    try:
        spec.loader.exec_module(module)
        monkeypatch.setattr(module, "DIST_DIR", tmp_path)
        yield module
    finally:
        sys.modules.pop("_alpha_osk_build_stage", None)


def _fake_dist(root: Path) -> None:
    for rel in (
        "alpha-osk.exe",
        "alpha-osk-relauncher.exe",
        "_internal/python312.dll",
        "_internal/PySide6/Qt6Core.dll",
        "_internal/zzz/last-alphabetically.dll",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")


class TestTheBuildRequiresAFileList:
    def test_a_dist_without_the_list_fails(self, build, tmp_path: Path) -> None:
        _fake_dist(tmp_path)
        assert build.verify_helper_stage_runs(tmp_path) is False

    def test_a_dist_without_the_helper_fails(self, build, tmp_path: Path) -> None:
        assert build.verify_helper_stage_runs(tmp_path) is False

    def test_a_list_naming_a_missing_file_fails(self, build, tmp_path: Path) -> None:
        _fake_dist(tmp_path)
        (tmp_path / "_internal" / "relauncher-files.txt").write_text(
            "alpha-osk-relauncher.exe\n_internal/not-there.dll\n", encoding="utf-8"
        )
        # Falls back to the whole bundle at run time, and the build refuses it.
        assert build.verify_helper_stage_runs(tmp_path) is False


class TestTheSpecWritesTheList:
    def test_it_is_generated_from_the_helpers_own_analysis(self) -> None:
        text = (BUILD_DIR / "alpha-osk.spec").read_text(encoding="utf-8")
        assert "relauncher-files.txt" in text
        for toc in ("helper_a.binaries", "helper_a.datas"):
            assert toc in text[text.index("_helper_files = ") :]
        assert "_HELPER_SKIP_PREFIXES" in text


@pytest.mark.skipif(sys.platform != "win32", reason="the installer is built on Windows")
class TestTheMainExeIsExtractedLast:
    def test_alpha_osk_exe_is_the_last_file_the_installer_writes(
        self, build, tmp_path: Path
    ) -> None:
        _fake_dist(tmp_path)
        script = build._generate_nsi_script("9.9.9", "Alpha-OSK-Setup-9.9.9")
        files = [ln.strip() for ln in script.splitlines() if ln.strip().startswith("File ")]
        assert files, "the generated script lists no files"
        assert files[-1].endswith('alpha-osk.exe"'), (
            "the helper treats a changed, closed alpha-osk.exe as a finished install; "
            "if it is not written last, it would launch a half-extracted bundle"
        )
        assert any("relauncher" in f for f in files[:-1])
