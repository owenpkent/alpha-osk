# CI and the pre-push gate

The same gates run in two places: `python check.py` before a push, and
GitHub Actions on every PR. This file is the reasoning behind how they are
arranged. The load-bearing rules a change has to respect are summarised in
CLAUDE.md under *Testing*; everything here is why they are what they are.

## The pre-push gate (`check.py`)

Run `python check.py` before `git push` to catch lint / format / type /
test failures locally instead of waiting for CI's red X (the same gates
GitHub Actions runs). Default mode skips coverage tracking (~60 s); add
`--full` to include the `--cov-fail-under=60` gate (~110 s, matches CI
exactly). `python check.py --install-hook` writes `.git/hooks/pre-push` so
it runs on `git push` instead of from memory; `git push --no-verify` is the
escape hatch. Hooks are not version controlled, so a fresh clone has to run
that once.

## The suite is sharded with `pytest-xdist`, and there is no fast subset

`-n auto` is what makes the gate a minute instead of twenty-five. There is
deliberately **no fast-subset tier**, and the reason is worth keeping: the
three static steps cost ~5 s between them, so the gate *was* pytest, and
pytest was slow for a reason unrelated to how many tests there are.
Building a `KeyboardBridge` cost ~1 s (20k-word dictionary + SymSpell
deletion index + PPM), the `bridge` fixture is function-scoped, and there
were ~1300 tests at the time: per-process setup repeated 1300 times, which
no amount of clever test selection touches and which shards almost
linearly.

Half of that per-bridge second was also a genuine bug:
`HybridPredictor.__init__` called `set_frequencies` twice, and the first
call's SymSpell index was discarded four lines later by the second, on
every app launch as well as every test.

If the gate creeps back up, **measure before tiering**; trading away
coverage on the one gate that runs before code leaves the machine is the
last resort, not the first.

## The QSettings scope trap

Sharding needed one test-side fix, and it is the kind that recurs: the four
headless QML modules each persist through a QML `Settings {}` element,
which resolves to a *process-external* store (a key under HKCU on Windows).
Three of them defined the same `TEST_ORG` literal independently and the
fourth imported it, so under `-n auto` several workers shared one scope and
called `.clear()` on each other mid-test. That surfaced as "the window
width drifted across restarts: [1160, 940, 1160]", indistinguishable from
the persistence bug those tests exist to catch.

The scope now lives once in `tests/qt_settings_scope.py`, suffixed with
`PYTEST_XDIST_WORKER`. Any new test touching QSettings, the registry, a
fixed temp path, or any other machine-global resource has to key it per
worker the same way.

## CI shards on two axes, and they are different axes

`-n auto` spreads the suite over one machine's cores; `--shard-id N
--shard-count M` spreads it over several machines. CI does both.

Across cores came first, and the argument for leaving it serial was that CI
is the release gate and its runners have far fewer cores. The cores are
real, 4 against 16, but the argument was wrong about where the time goes:
the run is dominated by per-test setup repeated ~1600 times, which shards
almost linearly whatever the core count. Serial it took **26 minutes**.

Across machines came second, because 4 cores is still 4 cores: after
`-n auto`, ubuntu took **14m30s** and windows **19m05s**, with every push
waiting on the slower. Dependency install was 15 s and 52 s respectively
and lint / typecheck / OSV are all under 30 s, so there was nothing else to
cut. The matrix is now `os x shard[0..3]`, eight jobs, each still `-n auto`
over its own cores.

## The shard hash cannot be `hash()`

The split lives in `tests/conftest.py::pytest_collection_modifyitems`: a
test belongs to shard `crc32(nodeid) % count`. Deliberately not
`pytest-split`, which wants a recorded-durations file that goes stale
silently and is one more exactly-pinned, CVE-scanned dependency; at 1600
tests over 4 shards a hash balances to within about 6% (measured: 377 / 389
/ 421 / 421) with no moving parts.

**The hash cannot be `hash()`**: CPython salts string hashing per process,
every xdist worker inside a shard is its own process, and workers
disagreeing about which tests are theirs does not fail loudly, it runs some
tests twice and others never.

## Coverage is combined in its own job

`--cov-fail-under` moved out of the test jobs, because a shard measures
roughly a quarter of the lines and every shard would fail the gate. Each
shard uploads its `.coverage.<os>.<shard>` and a separate `coverage` job
combines and applies the threshold.

Two things about it are load-bearing: `include-hidden-files: true` on the
upload (the file starts with a dot, and upload-artifact has excluded hidden
files since v4.4, so without it every upload is empty and the gate silently
stops gating), and the combine is **per OS**, since a coverage data file
records absolute source paths and the two runners disagree about those. Per
OS also preserves what the unsharded jobs promised: each platform had to
clear 60% on its own, and the `if sys.platform == "win32"` bodies are
measured on exactly one of the two.

## `fail-fast: false` on the matrix

The default cancelled every sibling on the first red, so one flaky ubuntu
test took the windows job down with it and the run reported two failures
where there was one, with nothing to say whether windows would have passed.

## Branch protection requires the `Tests` job, not the shards

Required status checks are configured by *name*, in the repo settings
rather than in the workflow file, so naming the shards there would mean
re-configuring protection on every change to the shard count. A stale entry
does not fail loudly: the named check never reports and every PR blocks for
ever on something that cannot go green, which is what sharding did to the
old `Test (ubuntu-latest)` / `Test (windows-latest)` entries.

`tests-passed` is a one-step job that asserts `needs.test.result` and
`needs.coverage.result`, and it carries `if: always()` because a job whose
dependency failed is *skipped*, and a skipped required check reads as
pending rather than red: without it the gate goes quiet exactly when it
should be loud. The required set is Lint, Type Check, Tests, OSV Scanner.

## Concurrency

The workflow sets `concurrency: group: ci-${{ github.ref }}` with
`cancel-in-progress: true`, so a new push supersedes the run it replaces
rather than both finishing. A cancelled intermediate commit is the intended
outcome: what has to be green is the tip.

## `mypy` runs twice

Under `--platform linux` and `--platform win32`, and CI mirrors both.
Neither covers the other: linux is the runner's platform, and win32 is the
only pass that type-checks the `if sys.platform == "win32"` bodies, which
mypy otherwise prunes as unreachable. On the linux pass alone, a deliberate
`int = "str"` planted inside `_window_class_name`'s Windows branch was
invisible.

## Formatting is a separate gate from linting

The `format` step is `ruff format --check src/ tests/`, and it is separate
from `ruff` because `ruff check` does not look at layout. Fix a failure
with `ruff format src/ tests/` rather than by hand.

The `ruff-format` hook in `.pre-commit-config.yaml` only helps contributors
who ran `pre-commit install`, which is why the tree had drifted in 46 of 57
files before the gate existed. The hook is pinned to a commit SHA matching
CI's `ruff==0.16.2`, so a contributor's local `--fix` pass and CI never
disagree on a rule version.
