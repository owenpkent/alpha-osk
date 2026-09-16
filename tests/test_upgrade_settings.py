"""Black box coverage for the legacy uninstaller settings bridge."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
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
    """The settings come back whatever the old uninstaller returns.

    Its exit code deliberately does NOT stop setup. On the same-directory
    path this helper runs before the replacement files are extracted, so
    aborting there left a user whose only input device is this keyboard
    with the old install gone, no new one, and a retry that failed
    identically. Backup and restore failures are the ones that stay
    fail-closed, and they have their own tests below.
    """
    org = "Alpha-OSK-UpgradeHarness-" + uuid.uuid4().hex
    old, bridge, marker = tmp_path / "legacy.exe", tmp_path / "bridge.exe", tmp_path / "old-ran.txt"
    try:
        _compile(_makensis(), _old_source(org, marker).replace("@EXIT_CODE@", str(exit_code)), old)
        _compile(_makensis(), _new_source(org, old), bridge)
        _seed(org)
        result = subprocess.run([str(bridge), "/S"], capture_output=True, text=True, timeout=30)
        assert marker.read_text(encoding="utf-8") == "ran"
        assert result.returncode == 0, result.stdout + result.stderr
        _assert_seed(org)
        with pytest.raises(FileNotFoundError):
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\" + org + "-upgrade-backups")
    finally:
        _delete_tree(winreg.HKEY_CURRENT_USER, "Software\\" + org)
        _delete_tree(winreg.HKEY_CURRENT_USER, "Software\\" + org + "-upgrade-backups")


def test_a_failing_uninstaller_with_nothing_to_preserve_still_continues(tmp_path: Path) -> None:
    """The cell the parametrised test above cannot reach.

    It always seeds the settings first, so the old uninstaller's non-zero
    exit was only ever seen after a successful restore. With no settings
    to back up, the previous code took the same abort while the message
    box claimed the settings had been preserved, about settings that never
    existed.
    """
    org = "Alpha-OSK-UpgradeHarness-" + uuid.uuid4().hex
    old, bridge, marker = tmp_path / "legacy.exe", tmp_path / "bridge.exe", tmp_path / "old-ran.txt"
    try:
        _compile(_makensis(), _old_source(org, marker).replace("@EXIT_CODE@", "9"), old)
        _compile(_makensis(), _new_source(org, old), bridge)
        result = subprocess.run([str(bridge), "/S"], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert marker.read_text(encoding="utf-8") == "ran"
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


# --------------------------------------------------------------------------
# A real uninstaller, so `_?=` is falsifiable.
#
# The stand-in above is a `SilentInstall silent` *installer*: it does its
# work and exits, so it never performs the copy-to-%TEMP%-and-return-
# immediately dance that `_?=` exists to suppress, and deleting `_?=` from
# the production script left every test in this file passing. These two
# compile a genuine NSIS uninstaller and run it both ways, so the pair
# fails if `_?=` is dropped.
#
# The uninstaller sleeps before doing its damage. Without `_?=` the
# launcher returns at once, so the guard restores the settings while the
# temp copy is still asleep, and the copy then deletes what was just put
# back; with `_?=` the wait is for the real process and the restore lands
# after it. The sleep is what makes that ordering deterministic rather
# than a race.

_UNINSTALLER_SLEEP_MS = 3000


def _uninstaller_maker_source(org: str, marker: Path, install_dir: Path) -> str:
    """An installer whose only job is to write the uninstaller under test."""
    return f'''Name "legacy"
OutFile "@OUTPUT@"
RequestExecutionLevel user
SilentInstall silent
InstallDir "{install_dir}"
Section
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\\uninstall.exe"
SectionEnd
Section "Uninstall"
  Sleep {_UNINSTALLER_SLEEP_MS}
  DeleteRegKey HKCU "Software\\{org}"
  FileOpen $0 "{marker}" w
  FileWrite $0 "ran"
  FileClose $0
SectionEnd
'''


def _bridge_source(org: str, command: str) -> str:
    return f'''!define APP_ORG "{org}"
!include "{HELPER}"
Name "bridge"
OutFile "@OUTPUT@"
RequestExecutionLevel user
SilentInstall silent
Section
  Push '{command}'
  Call RunPreviousUninstaller
SectionEnd
'''


def _run_against_a_real_uninstaller(tmp_path: Path, *, in_place: bool) -> bool:
    """Drive the guard against a genuine uninstaller. True if settings survived."""
    org = "Alpha-OSK-UpgradeHarness-" + uuid.uuid4().hex
    install_dir = tmp_path / ("inplace" if in_place else "detached")
    install_dir.mkdir()
    maker = tmp_path / f"maker-{'a' if in_place else 'b'}.exe"
    bridge = tmp_path / f"bridge-{'a' if in_place else 'b'}.exe"
    marker = tmp_path / f"old-ran-{'a' if in_place else 'b'}.txt"
    uninstaller = install_dir / "uninstall.exe"
    makensis = _makensis()
    try:
        _compile(makensis, _uninstaller_maker_source(org, marker, install_dir), maker)
        subprocess.run([str(maker), "/S"], capture_output=True, text=True, timeout=30, check=True)
        assert uninstaller.exists(), "the harness did not produce an uninstaller"
        command = f'"{uninstaller}" /S _?={install_dir}' if in_place else f'"{uninstaller}" /S'
        _compile(makensis, _bridge_source(org, command), bridge)
        _seed(org)
        result = subprocess.run([str(bridge), "/S"], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        # Let a detached temp copy finish, so the comparison is about
        # ordering rather than about which process we happened to outrun.
        time.sleep(_UNINSTALLER_SLEEP_MS / 1000 + 2)
        try:
            _assert_seed(org)
            return True
        except (FileNotFoundError, OSError, AssertionError):
            return False
    finally:
        _delete_tree(winreg.HKEY_CURRENT_USER, "Software\\" + org)
        _delete_tree(winreg.HKEY_CURRENT_USER, "Software\\" + org + "-upgrade-backups")


def test_the_guard_waits_for_a_real_uninstaller(tmp_path: Path) -> None:
    assert _run_against_a_real_uninstaller(tmp_path, in_place=True) is True


def test_without_in_place_execution_the_restore_lands_too_early(tmp_path: Path) -> None:
    """The inverse, and the reason the test above can fail at all.

    Drop `_?=` from the production script and this is the behaviour every
    upgrade gets: the guard puts the settings back before the uninstaller
    has removed them.
    """
    assert _run_against_a_real_uninstaller(tmp_path, in_place=False) is False
