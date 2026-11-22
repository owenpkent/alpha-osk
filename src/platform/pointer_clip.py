"""
Pointer confinement with an out-of-process safety guard
=======================================================

The virtual analog stick keeps the mouse pointer inside a small rectangle
(Win32 ``ClipCursor``) while the user holds it.

**Measured fact (2026-11-21, Windows 11): a clip outlives the process that
set it.**  A child process set a clip and was killed; the clip was still in
force three seconds later.  The pointer is this app's user's only input
device, so an app that died or hung while confining would leave the pointer
boxed in with nothing to escape with.

Hence one rule: **no clip without a live guard.**  A second process (the
guard, ``python -m src.platform.pointer_clip_guard``) watches two things and
calls ``ClipCursor(NULL)`` itself when either goes wrong:

* the parent process dying (a process handle waited on with a zero timeout);
* the parent's heartbeat going stale while a clip is active (a hung UI
  thread: alive, but no longer ticking).

The parent and guard share a tiny named memory block (:class:`Heartbeat`).
The parent refuses to confine unless the guard has proven it is running and
mapping that same block, and it keeps checking on every tick.

Everything touching the real pointer sits behind :class:`ClipApi`, whose
factory :func:`win32_clip_api` is the seam tests patch.
"""

from __future__ import annotations

import atexit
import ctypes
import mmap
import os
import secrets
import struct
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Protocol, Tuple

_IS_WINDOWS = sys.platform == "win32"

# left, top, right, bottom in physical pixels; right/bottom exclusive (a Win32 RECT).
Rect = Tuple[int, int, int, int]

STALE_S = 0.4
POLL_S = 0.05
GUARD_SILENCE_S = 0.5
DRY_RUN_ENV = "ALPHA_OSK_CLIP_GUARD_DRY_RUN"

_SYNCHRONIZE = 0x00100000
_WAIT_TIMEOUT = 0x00000102
_RELEASE_ATTEMPTS = 3


class ClipApi(Protocol):
    """The pointer calls, none of which ever raises (a failure is None/False)."""

    def get_clip(self) -> Optional[Rect]: ...

    def set_clip(self, rect: Rect) -> bool: ...

    def release(self) -> bool: ...

    def cursor_pos(self) -> Optional[Tuple[int, int]]: ...

    def set_cursor_pos(self, x: int, y: int) -> bool: ...


class _Win32ClipApi:
    def __init__(self) -> None:
        # getattr, not ctypes.WinDLL: typeshed has no WinDLL off Windows.
        user32: ctypes.CDLL = getattr(ctypes, "WinDLL")("user32", use_last_error=True)
        user32.GetClipCursor.argtypes = [ctypes.POINTER(wintypes.RECT)]
        user32.GetClipCursor.restype = wintypes.BOOL
        user32.ClipCursor.argtypes = [ctypes.c_void_p]
        user32.ClipCursor.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        self._user32 = user32

    def get_clip(self) -> Optional[Rect]:
        try:
            r = wintypes.RECT()
            if not self._user32.GetClipCursor(ctypes.byref(r)):
                return None
            return (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            return None

    def set_clip(self, rect: Rect) -> bool:
        try:
            r = wintypes.RECT(*rect)
            return bool(self._user32.ClipCursor(ctypes.byref(r)))
        except Exception:
            return False

    def release(self) -> bool:
        try:
            return bool(self._user32.ClipCursor(None))
        except Exception:
            return False

    def cursor_pos(self) -> Optional[Tuple[int, int]]:
        try:
            p = wintypes.POINT()
            if not self._user32.GetCursorPos(ctypes.byref(p)):
                return None
            return (int(p.x), int(p.y))
        except Exception:
            return None

    def set_cursor_pos(self, x: int, y: int) -> bool:
        try:
            return bool(self._user32.SetCursorPos(int(x), int(y)))
        except Exception:
            return False


class _NoopClipApi:
    """Dry-run api: lets the real guard process run without touching the pointer."""

    def get_clip(self) -> Optional[Rect]:
        return None

    def set_clip(self, rect: Rect) -> bool:
        return True

    def release(self) -> bool:
        return True

    def cursor_pos(self) -> Optional[Tuple[int, int]]:
        return None

    def set_cursor_pos(self, x: int, y: int) -> bool:
        return True


def win32_clip_api() -> Optional[ClipApi]:
    """The real pointer api, or None off Windows.  THE seam tests patch."""
    if not _IS_WINDOWS:
        return None
    try:
        return _Win32ClipApi()
    except Exception:
        return None


# --------------------------------------------------------------------------
# Heartbeat: a named shared-memory block, four little-endian uint32 fields.
# --------------------------------------------------------------------------

_FIELDS = struct.Struct("<4I")
_SIZE = _FIELDS.size
_OFF_ACTIVE, _OFF_BEAT, _OFF_RELEASES, _OFF_GUARD_BEAT = 0, 4, 8, 12
_MASK = 0xFFFFFFFF

# Off Windows there is no tagged mapping to share between processes, so a
# block is an anonymous mmap held here by name.  That is enough for tests that
# play both sides inside one process; it cannot span processes.
_fallback_blocks: Dict[str, "mmap.mmap"] = {}


@dataclass(frozen=True)
class HeartbeatState:
    active: int
    beat: int
    guard_releases: int
    guard_beat: int


class Heartbeat:
    """Shared counters.  Each field has one writer; writes are single 4-byte stores."""

    def __init__(self, name: str, block: "mmap.mmap") -> None:
        self.name = name
        self._block: Optional["mmap.mmap"] = block

    @staticmethod
    def _map(name: str, create: bool) -> "mmap.mmap":
        if sys.platform == "win32":
            return mmap.mmap(-1, _SIZE, tagname=name)
        if create or name not in _fallback_blocks:
            _fallback_blocks[name] = mmap.mmap(-1, _SIZE)
        return _fallback_blocks[name]

    @classmethod
    def create(cls, name: str) -> "Heartbeat":
        return cls(name, cls._map(name, True))

    @classmethod
    def open(cls, name: str) -> "Heartbeat":
        return cls(name, cls._map(name, False))

    def _get(self, off: int) -> int:
        assert self._block is not None
        return int(struct.unpack_from("<I", self._block, off)[0])

    def _put(self, off: int, value: int) -> None:
        assert self._block is not None
        struct.pack_into("<I", self._block, off, value & _MASK)

    def set_active(self, active: bool) -> None:
        self._put(_OFF_ACTIVE, 1 if active else 0)

    def beat(self) -> None:
        self._put(_OFF_BEAT, self._get(_OFF_BEAT) + 1)

    def guard_tick(self) -> None:
        self._put(_OFF_GUARD_BEAT, self._get(_OFF_GUARD_BEAT) + 1)

    def note_guard_release(self) -> None:
        self._put(_OFF_RELEASES, self._get(_OFF_RELEASES) + 1)

    def read(self) -> HeartbeatState:
        assert self._block is not None
        a, b, r, g = _FIELDS.unpack_from(self._block, 0)
        return HeartbeatState(int(a), int(b), int(r), int(g))

    def close(self) -> None:
        block, self._block = self._block, None
        if block is not None and sys.platform == "win32":
            try:
                block.close()
            except Exception:
                pass


# --------------------------------------------------------------------------
# The guard loop (child process).
# --------------------------------------------------------------------------


def _try_release(api: ClipApi) -> bool:
    try:
        return bool(api.release())
    except Exception:
        return False


def run_guard(
    parent_alive: Callable[[], bool],
    heartbeat: Heartbeat,
    api: ClipApi,
    *,
    stale_s: float = STALE_S,
    poll_s: float = POLL_S,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    max_iterations: Optional[int] = None,
) -> str:
    """Release the clip when the parent dies or stops heartbeating.  Never raises."""
    last_beat: Optional[int] = None
    last_change = clock()
    released_this_stall = False
    iterations = 0
    while max_iterations is None or iterations < max_iterations:
        iterations += 1
        try:
            heartbeat.guard_tick()
        except Exception:
            pass
        try:
            alive = bool(parent_alive())
        except Exception:
            alive = False  # fail towards releasing
        if not alive:
            for _ in range(_RELEASE_ATTEMPTS):
                if _try_release(api):
                    break
                sleep(poll_s)
            return "parent-gone"
        try:
            state = heartbeat.read()
            now = clock()
            if state.beat != last_beat:
                last_beat = state.beat
                last_change = now
                released_this_stall = False
            if not state.active:
                last_change = now
                released_this_stall = False
            elif not released_this_stall and now - last_change > stale_s:
                # A failed release is retried on the next poll, not counted.
                if _try_release(api):
                    released_this_stall = True
                    heartbeat.note_guard_release()
        except Exception:
            pass
        sleep(poll_s)
    return "max-iterations"


def _parent_alive_probe(pid: int) -> Optional[Callable[[], bool]]:
    """A probe over a SYNCHRONIZE handle to ``pid``, or None if it cannot be opened."""
    if not _IS_WINDOWS:
        return None
    try:
        kernel32: ctypes.CDLL = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        handle = kernel32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return None
    except Exception:
        return None

    def alive() -> bool:
        return int(kernel32.WaitForSingleObject(handle, 0)) == _WAIT_TIMEOUT

    return alive


def guard_main(argv: list[str]) -> int:
    """Entry point of the guard process: ``<parent_pid> <heartbeat_name>``."""
    dry = os.environ.get(DRY_RUN_ENV) == "1"
    api: Optional[ClipApi] = _NoopClipApi() if dry else win32_clip_api()
    try:
        pid = int(argv[0])
        name = argv[1]
        alive = _parent_alive_probe(pid)
        if alive is None:
            raise RuntimeError("parent not openable")
        heartbeat = Heartbeat.open(name)
        if api is None:
            raise RuntimeError("no pointer api")
    except Exception:
        if api is not None and not dry:
            _try_release(api)
        return 2
    run_guard(alive, heartbeat, api)
    return 0


# --------------------------------------------------------------------------
# The parent side.
# --------------------------------------------------------------------------


def _default_spawn_guard(name: str) -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    return subprocess.Popen(
        [sys.executable, "-m", "src.platform.pointer_clip_guard", str(os.getpid()), name],
        cwd=str(repo_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class PointerConfiner:
    """Confines the pointer only while a live guard process can undo it."""

    def __init__(
        self,
        *,
        api_factory: Optional[Callable[[], Optional[ClipApi]]] = None,
        spawn_guard: Callable[[str], Any] = _default_spawn_guard,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Resolved at call time, not bound as a default, so the test guard that
        # patches the module's win32_clip_api reaches it.
        self._api_factory = api_factory
        self._spawn_guard = spawn_guard
        self._clock = clock
        self._sleep = sleep
        self._api: Optional[ClipApi] = None
        self._heartbeat: Optional[Heartbeat] = None
        self._proc: Any = None
        self._started = False
        self._holds_clip = False
        #: A release the OS refused. The clip may still be set; the heartbeat
        #: is left active and unbeaten so the guard retries it, and every later
        #: call here retries it first.
        self.release_pending = False
        # Guards left running because a release was still outstanding at close().
        self._orphans: list[Any] = []
        self._releases_seen = 0
        self._seen_guard_beat = 0
        self._seen_guard_at = 0.0
        self._atexit_registered = False
        self.confined = False
        self.reapplied = 0
        self.rect: Optional[Rect] = None

    # -- lifecycle ---------------------------------------------------------

    def start(self, timeout_s: float = 1.5) -> bool:
        if self._started:
            return True
        try:
            if self._ensure_api() is None:
                return False
            name = f"alpha-osk-clip-{os.getpid()}-{secrets.token_hex(6)}"
            self._heartbeat = Heartbeat.create(name)
            self._proc = self._spawn_guard(name)
            if not self._await_guard(timeout_s):
                self._teardown()
                return False
            self._started = True
            if not self._atexit_registered:
                atexit.register(self.close)
                self._atexit_registered = True
            return True
        except Exception:
            self._teardown()
            return False

    def _await_guard(self, timeout_s: float) -> bool:
        assert self._heartbeat is not None
        deadline = self._clock() + timeout_s
        baseline = self._heartbeat.read().guard_beat
        last = baseline
        advances = 0
        while True:
            current = self._heartbeat.read().guard_beat
            if current != last:
                advances += 1
                last = current
                if advances >= 2:
                    self._seen_guard_beat = current
                    self._seen_guard_at = self._clock()
                    return True
            if self._proc is not None and self._proc.poll() is not None:
                return False
            if self._clock() >= deadline:
                return False
            self._sleep(0.01)

    def _teardown(self) -> None:
        self._started = False
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=1.0)
                except Exception:
                    proc.kill()
            except Exception:
                pass
        hb, self._heartbeat = self._heartbeat, None
        if hb is not None:
            try:
                hb.close()
            except Exception:
                pass
        # The api is kept: reading the pointer needs no guard, and a stick that
        # cannot be confined must still be usable unconfined.

    def _ensure_api(self) -> Optional[ClipApi]:
        if self._api is None:
            try:
                self._api = (self._api_factory or win32_clip_api)()
            except Exception:
                self._api = None
        return self._api

    @property
    def guard_alive(self) -> bool:
        try:
            if not self._started or self._heartbeat is None or self._proc is None:
                return False
            if self._proc.poll() is not None:
                return False
            current = self._heartbeat.read().guard_beat
            now = self._clock()
            if current != self._seen_guard_beat:
                self._seen_guard_beat = current
                self._seen_guard_at = now
            return now - self._seen_guard_at <= GUARD_SILENCE_S
        except Exception:
            return False

    # -- confinement -------------------------------------------------------

    def _forget_clip(self) -> None:
        self._holds_clip = False
        self.confined = False
        self.rect = None

    def _set_inactive(self) -> None:
        try:
            if self._heartbeat is not None:
                self._heartbeat.set_active(False)
        except Exception:
            pass

    def _os_release(self) -> bool:
        """``ClipCursor(NULL)``, as a plain bool. A raise is a failure."""
        try:
            return bool(self._api is not None and self._api.release())
        except Exception:
            return False

    def _settle_pending(self) -> bool:
        """Retry a release the OS refused. True once nothing of ours is clipped."""
        if not self.release_pending:
            return True
        if self._os_release():
            self._set_inactive()
            self._forget_clip()
            self.release_pending = False
            return True
        return False

    def confine(self, rect: Rect) -> bool:
        try:
            if not self._started or self._api is None or self._heartbeat is None:
                return False
            if not self._settle_pending():
                return False
            if not self.guard_alive:
                return False
            hb = self._heartbeat
            hb.beat()
            hb.set_active(True)
            if not self._api.set_clip(rect):
                if not self._holds_clip:
                    hb.set_active(False)
                return False
            # Counted from here: a guard release before this clip is old news,
            # one after it is about this clip.
            self._releases_seen = hb.read().guard_releases
            self._holds_clip = True
            self.confined = True
            self.rect = rect
            return True
        except Exception:
            return False

    def tick(self) -> str:
        try:
            if self.release_pending:
                return "idle" if self._settle_pending() else "release-pending"
            if not self.confined or self._heartbeat is None or self._api is None:
                return "idle"
            hb = self._heartbeat
            hb.beat()
            releases = hb.read().guard_releases
            if releases != self._releases_seen:
                # The guard clears the OS clip and counts it as two steps, in
                # another process. A tick that ran between them saw the cleared
                # rectangle and put ours back, so the count going up does not
                # prove nothing is clipped. Release for ourselves before giving
                # up ownership; forgetting here without it is how the pointer
                # stays boxed in with both sides believing it is free.
                self._releases_seen = releases
                self.release()
                return "guard-released"
            if not self.guard_alive:
                self.release()
                return "guard-lost"
            if self._api.get_clip() != self.rect:
                assert self.rect is not None
                if self._api.set_clip(self.rect):
                    self.reapplied += 1
                    return "reapplied"
                # Something cleared the clip and it will not go back: the
                # pointer is loose, so this is not confinement any more.
                self.release()
                return "clip-lost"
            return "ok"
        except Exception:
            # Unknown state: fail towards freeing the pointer.
            self.release()
            return "clip-lost"

    def release(self) -> bool:
        """Let the pointer go. True once nothing of ours is clipped.

        The heartbeat goes inactive only AFTER the OS confirms the release.
        Until then the guard is still watching, and because nothing beats
        while no stick is held, its stale-heartbeat path takes the retry over
        within ``STALE_S``. (Marking it inactive first would leave a refused
        release, or a stall between the two steps, with a clip set and nobody
        responsible for it.) Only a clip this object set is cleared: a game
        may hold its own.
        """
        try:
            if not self._holds_clip:
                self._set_inactive()
                self._forget_clip()
                self.release_pending = False
                return True
            if self._os_release():
                self._set_inactive()
                self._forget_clip()
                self.release_pending = False
                return True
            # Still ours, still clipped. Not "confined" as far as a caller is
            # concerned (no stick may follow it), but not forgotten either.
            self.confined = False
            self.rect = None
            self.release_pending = True
            return False
        except Exception:
            self.release_pending = self._holds_clip
            return not self._holds_clip

    def close(self) -> None:
        released = False
        try:
            released = self.release()
            for _ in range(_RELEASE_ATTEMPTS):
                if released:
                    break
                self._sleep(POLL_S)
                released = self._settle_pending()
        except Exception:
            released = False
        if self._atexit_registered:
            try:
                atexit.unregister(self.close)
            except Exception:
                pass
            self._atexit_registered = False
        if released:
            self._teardown()
        else:
            self._abandon_to_guard()

    def _abandon_to_guard(self) -> None:
        """Stop, but leave the guard alive: it is the only thing still retrying.

        The heartbeat stays active and unbeaten, so the guard keeps trying the
        release on every poll until it succeeds, and releases once more when
        this process exits. Terminating it here would strand the clip.
        """
        self._started = False
        proc, self._proc = self._proc, None
        if proc is not None:
            self._orphans.append(proc)
        self._heartbeat = None

    # -- pointer pass-throughs ----------------------------------------------

    def cursor_pos(self) -> Optional[Tuple[int, int]]:
        """Where the pointer is. Needs no guard: it changes nothing."""
        api = self._ensure_api()
        try:
            return api.cursor_pos() if api is not None else None
        except Exception:
            return None

    def set_cursor_pos(self, x: int, y: int) -> bool:
        """Move the pointer, only while it is confined: never one that is not walled in."""
        if not self.confined or self._api is None:
            return False
        try:
            return bool(self._api.set_cursor_pos(x, y))
        except Exception:
            return False
