"""The version resource ``build/windows/alpha-osk.spec`` stamps into the exe.

Found while walking the taskbar for the launch bug: the shipped exe carried
no version resource at all, so a pin made from the running button was named
from the bare filename (``alpha-osk``) rather than from ``FileDescription``,
and Explorer suffixed ``(2)`` because a pin of that name already existed.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO_ROOT / "build" / "windows"


@pytest.fixture(scope="module")
def vr():
    spec = importlib.util.spec_from_file_location(
        "_alpha_osk_version_resource", BUILD_DIR / "version_resource.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["_alpha_osk_version_resource"] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop("_alpha_osk_version_resource", None)


class TestTheExeSaysWhatItIs:
    def test_the_file_description_is_the_app_name(self, vr) -> None:
        """This is the field the shell names a pin from."""
        assert "StringStruct('FileDescription', 'Alpha-OSK')" in vr.version_info_text("1.5.0")

    def test_the_product_name_matches(self, vr) -> None:
        assert "StringStruct('ProductName', 'Alpha-OSK')" in vr.version_info_text("1.5.0")

    def test_the_publisher_is_the_installers_publisher(self, vr) -> None:
        """The two version blocks sit side by side in the file properties
        dialog, and the installer's is the one the EV certificate names."""
        build_py = (BUILD_DIR / "build.py").read_text(encoding="utf-8")
        match = re.search(r'!define APP_PUBLISHER "([^"]+)"', build_py)
        assert match, "APP_PUBLISHER not found in build.py"
        assert vr.PUBLISHER == match.group(1)

    def test_the_version_comes_from_the_argument(self, vr) -> None:
        text = vr.version_info_text("2.7.1")
        assert "filevers=(2, 7, 1, 0)" in text
        assert "prodvers=(2, 7, 1, 0)" in text
        assert "StringStruct('FileVersion', '2.7.1')" in text
        assert "StringStruct('ProductVersion', '2.7.1')" in text

    def test_a_prerelease_suffix_stays_in_the_strings_only(self, vr) -> None:
        text = vr.version_info_text("1.6.0rc1")
        assert "filevers=(1, 6, 0, 0)" in text
        assert "StringStruct('FileVersion', '1.6.0rc1')" in text

    def test_a_non_version_is_refused(self, vr) -> None:
        with pytest.raises(ValueError):
            vr.version_tuple("latest")

    def test_the_text_is_what_pyinstaller_reads(self, vr, tmp_path: Path) -> None:
        """Round-trip through PyInstaller's own loader, when it is installed:
        the format is theirs, and a typo in it would otherwise surface only
        on a release build."""
        versioninfo = pytest.importorskip("PyInstaller.utils.win32.versioninfo")
        path = tmp_path / "version-info.txt"
        path.write_text(vr.version_info_text("1.5.0"), encoding="utf-8")

        loader = getattr(versioninfo, "load_version_info_from_text_file", None) or getattr(
            versioninfo, "load_version_info"
        )
        info = loader(str(path))

        assert info.ffi.fileVersionMS == (1 << 16) | 5
        assert info.ffi.fileVersionLS == 0
        table = info.kids[0].kids[0]
        strings = {kid.name: kid.val for kid in table.kids}
        assert strings["FileDescription"] == "Alpha-OSK"
        assert strings["OriginalFilename"] == "alpha-osk.exe"


class TestTheSpecAppliesIt:
    def test_the_exe_block_takes_the_generated_file(self) -> None:
        spec = (BUILD_DIR / "alpha-osk.spec").read_text(encoding="utf-8")
        assert "version_resource" in spec, "the spec no longer generates the version resource"
        exe_block = spec[spec.index("exe = EXE(") : spec.index("coll = COLLECT(")]
        assert re.search(r"version\s*=\s*str\(VERSION_FILE\)", exe_block), (
            "EXE() is not being handed the version file, so the exe ships without "
            "a FileDescription and pins from the running button are named 'alpha-osk'"
        )
