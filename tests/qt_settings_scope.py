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
"""

from __future__ import annotations

import os

_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "")

TEST_ORG = f"alpha-osk-tests-{_XDIST_WORKER}" if _XDIST_WORKER else "alpha-osk-tests"
TEST_APP = "Alpha-OSK-Tests"
