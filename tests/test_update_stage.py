"""Staging the update helper, and sweeping stages that are not in use.

Two behaviours, each with the near-miss it must not become:

* Only the files the build listed are staged, and anything doubtful (no list,
  an unsafe entry, a listed file that is missing) falls back to the whole
  bundle, never to a half-copied stage.
* The sweep removes old stages and never one a helper may still be using: a
  held lock, or a stage too young to have taken its lock yet.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from src import update_signals, update_stage

HELPER = "alpha-osk-relauncher.exe"
MAIN = "alpha-osk.exe"


def _install(root: Path, *, listed: list[str] | None = None, extra: list[str] | None = None):
    """A fake install: the two exes, some runtime files, and an optional list."""
    files = [HELPER, MAIN, "_internal/python3.dll", "_internal/PySide6/Qt6Core.dll"]
    files += extra or []
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rel.encode())
    if listed is not None:
        manifest = root / update_stage.MANIFEST_RELPATH
        manifest.write_text("# generated\n" + "\n".join(listed) + "\n", encoding="utf-8")
    return root


class TestWhatIsStaged:
    def test_only_the_listed_files_are_copied(self, tmp_path: Path) -> None:
        src = _install(
            tmp_path / "src",
            listed=[HELPER, "_internal/python3.dll"],
            extra=["_internal/qml/Big.qml", "_internal/PySide6/Qt6Quick.dll"],
        )
        mode = update_stage.stage_bundle(src, tmp_path / "dest", helper_exe=HELPER, main_exe=MAIN)
        assert mode == update_stage.SUBSET
        staged = {
            p.relative_to(tmp_path / "dest").as_posix()
            for p in (tmp_path / "dest").rglob("*")
            if p.is_file()
        }
        assert staged == {HELPER, "_internal/python3.dll"}

    def test_the_main_exe_is_never_staged(self, tmp_path: Path) -> None:
        src = _install(tmp_path / "src", listed=[HELPER, MAIN])
        update_stage.stage_bundle(src, tmp_path / "dest", helper_exe=HELPER, main_exe=MAIN)
        # Listed or not, an image of that name in a stage is what the
        # installer's image-name matching must never meet.
        assert not (tmp_path / "dest" / MAIN).exists()

    def test_no_list_means_the_whole_bundle_minus_the_main_exe(self, tmp_path: Path) -> None:
        src = _install(tmp_path / "src")
        mode = update_stage.stage_bundle(src, tmp_path / "dest", helper_exe=HELPER, main_exe=MAIN)
        assert mode == update_stage.FULL
        assert (tmp_path / "dest" / HELPER).is_file()
        assert (tmp_path / "dest" / "_internal" / "python3.dll").is_file()
        assert not (tmp_path / "dest" / MAIN).exists()

    def test_a_listed_file_that_is_missing_falls_back_to_everything(self, tmp_path: Path) -> None:
        src = _install(tmp_path / "src", listed=[HELPER, "_internal/not-there.dll"])
        mode = update_stage.stage_bundle(src, tmp_path / "dest", helper_exe=HELPER, main_exe=MAIN)
        assert mode == update_stage.FULL
        assert (tmp_path / "dest" / "_internal" / "PySide6" / "Qt6Core.dll").is_file()

    def test_a_list_without_the_helper_falls_back_to_everything(self, tmp_path: Path) -> None:
        src = _install(tmp_path / "src", listed=["_internal/python3.dll"])
        mode = update_stage.stage_bundle(src, tmp_path / "dest", helper_exe=HELPER, main_exe=MAIN)
        assert mode == update_stage.FULL

    @pytest.mark.parametrize(
        "bad",
        ["../outside.dll", "/abs/path.dll", "C:/Windows/x.dll", "_internal/../../x", "a\\..\\b"],
    )
    def test_an_entry_that_could_leave_the_bundle_voids_the_whole_list(
        self, tmp_path: Path, bad: str
    ) -> None:
        src = _install(tmp_path / "src", listed=[HELPER, bad])
        assert update_stage.read_manifest(src) is None
        mode = update_stage.stage_bundle(src, tmp_path / "dest", helper_exe=HELPER, main_exe=MAIN)
        assert mode == update_stage.FULL

    def test_a_clean_list_reads_back_without_comments_or_blanks(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        (src / "_internal").mkdir(parents=True)
        (src / update_stage.MANIFEST_RELPATH).write_text(
            "# comment\n\n_internal\\a.dll\n  _internal/b.dll  \n", encoding="utf-8"
        )
        assert update_stage.read_manifest(src) == ["_internal/a.dll", "_internal/b.dll"]

    def test_an_empty_list_is_no_list(self, tmp_path: Path) -> None:
        src = _install(tmp_path / "src", listed=[])
        assert update_stage.read_manifest(src) is None

    def test_a_stage_without_the_helper_is_an_error(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        src.mkdir()
        (src / "something.dll").write_bytes(b"x")
        with pytest.raises(OSError):
            update_stage.stage_bundle(src, tmp_path / "dest", helper_exe=HELPER, main_exe=MAIN)


def _age(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


class TestTheSweepLeavesLiveStagesAlone:
    @pytest.fixture
    def temp_root(self, tmp_path: Path) -> Path:
        root = tmp_path / "temp"
        root.mkdir()
        return root

    def _stage(self, root: Path, name: str, *, age_s: float) -> Path:
        stage = root / (update_stage.STAGE_PREFIX + name)
        (stage / "bundle").mkdir(parents=True)
        (stage / "bundle" / "x.bin").write_bytes(b"x")
        _age(stage, age_s)
        return stage

    def test_an_old_unlocked_stage_is_swept(self, temp_root: Path) -> None:
        stage = self._stage(temp_root, "old", age_s=3600)
        update_stage.purge_stale_stages(temp_root)
        assert not stage.exists()

    def test_a_young_stage_is_never_swept(self, temp_root: Path) -> None:
        # Its helper may not have started (so no lock yet), or the user
        # retried an update within moments of cancelling one.
        stage = self._stage(temp_root, "young", age_s=5)
        update_stage.purge_stale_stages(temp_root)
        assert stage.exists()

    def test_a_stage_whose_helper_holds_the_lock_survives_however_old(
        self, temp_root: Path
    ) -> None:
        stage = self._stage(temp_root, "live", age_s=86400)
        lock = stage / update_signals.HELPER_LOCK_FILE
        lock.write_bytes(b"")
        _age(stage, 86400)  # creating the lock touched the directory
        # A second open file description stands in for the helper process:
        # on both platforms a lock is per open handle, not per process.
        holder = _hold_in_another_handle(lock)
        try:
            assert update_signals.lock_is_held(lock) is True
            update_stage.purge_stale_stages(temp_root)
            assert (stage / "bundle" / "x.bin").exists(), (
                "the sweep deleted files out from under a running helper"
            )
            assert lock.exists()
        finally:
            holder()
        # The near-miss: once the helper is gone the same stage goes.
        _age(stage, 86400)
        update_stage.purge_stale_stages(temp_root)
        assert not stage.exists()

    def test_only_our_own_prefix_is_touched(self, temp_root: Path) -> None:
        other = temp_root / "something-else"
        other.mkdir()
        _age(other, 86400)
        update_stage.purge_stale_stages(temp_root)
        assert other.exists()

    def test_a_missing_temp_root_is_not_an_error(self, tmp_path: Path) -> None:
        assert update_stage.purge_stale_stages(tmp_path / "nope") == []


@pytest.mark.skipif(sys.platform != "win32", reason="a Windows handle")
def _hold_windows(path: Path):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(path), 0xC0000000, 0, None, 4, 0, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    return lambda: kernel32.CloseHandle(handle)


def _hold_in_another_handle(path: Path):
    """Take the lock the way the helper does, from a different handle; return a release."""
    if sys.platform == "win32":
        return _hold_windows(path)
    import fcntl

    fd = os.open(str(path), os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return lambda: os.close(fd)
