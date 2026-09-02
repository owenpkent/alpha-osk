"""The QSettings scope the headless QML tests run under.

Four test modules load ``qml/Main.qml`` for real, and ``Main.qml``
persists through a QML ``Settings {}`` element.  That resolves to a
process-external store -- a key under ``HKCU\\Software`` on Windows, a
file under ``~/.config`` elsewhere -- so the scope is shared state in the
strongest sense: it outlives the process and is visible to every other
one on the machine.

Two things follow, and both used to be handled per module (badly):

* **It must not be the app's real scope.**  These tests write window
  geometry and panel toggles; pointing them at ``alpha-osk`` would mean
  running the suite silently reconfigured the user's keyboard.
* **It must be per process when the suite is sharded.**  ``pytest -n
  auto`` runs a dozen workers, and the fixtures in these modules call
  ``QSettings(...).clear()``.  Two workers sharing one scope do not
  merely interleave, they wipe each other's keys mid-test:
  ``TestRestartPersistence`` writes a window width, another worker
  clears it, and the restart reads back the default.  That surfaced as
  "the window width drifted across restarts: [1160, 940, 1160]", which
  is indistinguishable from the persistence bug those tests exist to
  catch.

The constants live here rather than in one of the test modules because
three of the four had their own copy of the literal, and the fourth
imported from the first.  That is only correct while all four copies
agree, and they must: ``QCoreApplication.setOrganizationName`` is
global and can only be set once per process, so the ``Settings {}``
element writes wherever the *first* module to build the app pointed it,
while each module's explicit ``QSettings(TEST_ORG, TEST_APP)`` calls
look wherever their own copy says.  Diverge them and the tests read a
different scope than the QML wrote to, which fails as a persistence
bug rather than as a configuration mistake.

* **It must be per checkout, too.**  Worker names are ``gw0``..``gwN``
  in every run, so two checkouts of this repo (git worktrees, say, each
  gating a branch at the same time) collide exactly as two workers did,
  and it shows up the same way: a window width that "drifted across
  restarts", in whichever run lost the race.  Seen on 2026-09-02 with
  four worktrees running ``check.py`` at once.  The scope therefore
  also carries a short stable hash of the checkout path.  ``crc32``
  rather than ``hash()``, for the reason ``conftest.py`` gives for the
  shard hash: string hashing is salted per process.
"""

from __future__ import annotations

import os
import zlib
from pathlib import Path


def scope_org(checkout: Path, worker: str) -> str:
    """The organisation name for one (checkout, xdist worker) pair.

    Every part is alphanumeric or a hyphen, so the value is safe as both
    a registry key and a directory name.
    """
    tag = f"{zlib.crc32(str(checkout.resolve()).encode('utf-8')) & 0xFFFFFFFF:08x}"
    return "-".join(part for part in ("alpha-osk-tests", tag, worker) if part)


_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")
_CHECKOUT = Path(__file__).resolve().parent.parent

TEST_ORG = scope_org(_CHECKOUT, _XDIST_WORKER)
TEST_APP = "Alpha-OSK-Tests"
