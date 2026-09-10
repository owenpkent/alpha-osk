"""Black box coverage for the legacy uninstaller settings bridge."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

if os.name == "nt":
    import winreg
else:  # pragma: no cover
    winreg = None

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-only NSIS test")
ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "build" / "windows" / "upgrade_settings.nsh"
NSIS_CANDIDATES = (
    Path(r"C:\Program Files (x86)\NSIS\makensis.exe"),
    Path(r"C:\Program Files\NSIS\makensis.exe"),
)


def _makensis() -> Path:
    found = shutil.which("makensis")
    for candidate in ([Path(found)] if found else []) + list(NSIS_CANDIDATES):
        if candidate.exists():
            return candidate
    pytest.skip("makensis.exe is not installed")


def _delete_tree(root: int, subkey: str) -> None:
    try:
        with winreg.OpenKey(root, subkey, 0, 0x20019) as key:
            children = [winreg.EnumKey(key, i) for i in range(winreg.QueryInfoKey(key)[0])]
        for child in children:
            _delete_tree(root, subkey + "\\" + child)
        winreg.DeleteKey(root, subkey)
    except FileNotFoundError:
        pass


def _compile(makensis: Path, source: str, output: Path) -> None:
    script = output.with_suffix(".nsi")
    script.write_text(source.replace("@OUTPUT@", str(output)), encoding="utf-8")
    result = subprocess.run(
        [str(makensis), "/V1", str(script)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _old_source(org: str, marker: Path) -> str:
    return f'''Name "legacy"
OutFile "@OUTPUT@"
RequestExecutionLevel user
SilentInstall silent
Section
  FileOpen $0 "{marker}" w
  FileWrite $0 "ran"
  FileClose $0
  DeleteRegKey HKCU "Software\\{org}"
  SetErrorLevel @EXIT_CODE@
SectionEnd
'''


def _new_source(org: str, old: Path, helper: Path = HELPER) -> str:
    return f'''!define APP_ORG "{org}"
!include "{helper}"
Name "bridge"
OutFile "@OUTPUT@"
RequestExecutionLevel user
SilentInstall silent
Section
  Push '"{old}" /S'
  Call RunPreviousUninstaller
SectionEnd
'''


def _seed(org: str) -> None:
    key_path = "Software\\" + org
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path) as key:
        winreg.SetValueEx(key, "Theme", 0, winreg.REG_SZ, "深色")
        winreg.SetValueEx(key, "Scale", 0, winreg.REG_DWORD, 125)
        winreg.SetValueEx(key, "Bytes", 0, winreg.REG_BINARY, b"\x00\xff\x10")
        with winreg.CreateKey(key, "Nested") as nested:
            winreg.SetValueEx(nested, "Label", 0, winreg.REG_SZ, "café")


def _assert_seed(org: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\" + org) as key:
        assert winreg.QueryValueEx(key, "Theme") == ("深色", winreg.REG_SZ)
        assert winreg.QueryValueEx(key, "Scale") == (125, winreg.REG_DWORD)
        assert winreg.QueryValueEx(key, "Bytes") == (b"\x00\xff\x10", winreg.REG_BINARY)
        with winreg.OpenKey(key, "Nested") as nested:
            assert winreg.QueryValueEx(nested, "Label") == ("café", winreg.REG_SZ)


@pytest.mark.parametrize("exit_code", [0, 9])
def test_legacy_uninstaller_preserves_typed_nested_settings(tmp_path: Path, exit_code: int) -> None:
    org = "Alpha-OSK-UpgradeHarness-" + uuid.uuid4().hex
    old, bridge, marker = tmp_path / "legacy.exe", tmp_path / "bridge.exe", tmp_path / "old-ran.txt"
    try:
        _compile(_makensis(), _old_source(org, marker).replace("@EXIT_CODE@", str(exit_code)), old)
        _compile(_makensis(), _new_source(org, old), bridge)
        _seed(org)
        result = subprocess.run([str(bridge), "/S"], capture_output=True, text=True, timeout=30)
        assert marker.read_text(encoding="utf-8") == "ran"
        assert result.returncode == (0 if exit_code == 0 else 1)
        _assert_seed(org)
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\" + org + "-upgrade-backups")
    finally:
        _delete_tree(winreg.HKEY_CURRENT_USER, "Software\\" + org)
        _delete_tree(winreg.HKEY_CURRENT_USER, "Software\\" + org + "-upgrade-backups")


def test_fresh_install_runs_old_uninstaller_without_backup(tmp_path: Path) -> None:
    org = "Alpha-OSK-UpgradeHarness-" + uuid.uuid4().hex
    old, bridge, marker = tmp_path / "legacy.exe", tmp_path / "bridge.exe", tmp_path / "old-ran.txt"
    try:
        _compile(_makensis(), _old_source(org, marker).replace("@EXIT_CODE@", "0"), old)
        _compile(_makensis(), _new_source(org, old), bridge)
        result = subprocess.run([str(bridge), "/S"], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert marker.read_text(encoding="utf-8") == "ran"
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\" + org + "-upgrade-backups")
    finally:
        _delete_tree(winreg.HKEY_CURRENT_USER, "Software\\" + org)
        _delete_tree(winreg.HKEY_CURRENT_USER, "Software\\" + org + "-upgrade-backups")


def _failure_case(tmp_path: Path, restore: bool) -> None:
    org = "Alpha-OSK-UpgradeHarness-" + uuid.uuid4().hex
    key_path = "Software\\" + org
    old, bridge, marker = tmp_path / "legacy.exe", tmp_path / "bridge.exe", tmp_path / "old-ran.txt"
    broken = tmp_path / "upgrade_settings_broken.nsh"
    needle = (
        "System::Call 'advapi32::RegCopyTreeW(p r3, p 0, p r2) i .r1'"
        if restore
        else "System::Call 'advapi32::RegCopyTreeW(p r2, p 0, p r3) i .r1'"
    )
    helper_text = HELPER.read_text(encoding="utf-8")
    assert needle in helper_text
    helper = helper_text.replace(needle, "StrCpy $1 5", 1)
    try:
        broken.write_text(helper, encoding="utf-8")
        _compile(_makensis(), _old_source(org, marker).replace("@EXIT_CODE@", "0"), old)
        _compile(_makensis(), _new_source(org, old, broken), bridge)
        _seed(org)
        result = subprocess.run([str(bridge), "/S"], capture_output=True, text=True, timeout=30)
        assert result.returncode != 0
        assert marker.exists() is restore
        if restore:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as current:
                with pytest.raises(FileNotFoundError):
                    winreg.QueryValueEx(current, "Theme")
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, "Software\\" + org + "-upgrade-backups"
            ) as root:
                child = winreg.EnumKey(root, 0)
                _assert_seed(org + "-upgrade-backups\\" + child)
        else:
            _assert_seed(org)
    finally:
        _delete_tree(winreg.HKEY_CURRENT_USER, key_path)
        _delete_tree(winreg.HKEY_CURRENT_USER, "Software\\" + org + "-upgrade-backups")


def test_backup_failure_stops_legacy_uninstaller(tmp_path: Path) -> None:
    _failure_case(tmp_path, restore=False)


def test_restore_failure_retains_recovery_copy(tmp_path: Path) -> None:
    _failure_case(tmp_path, restore=True)
