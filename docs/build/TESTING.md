# Testing

This doc holds the full write-up behind the CLAUDE.md *Testing* section: the property suites, model snapshot, autouse guards, pre-push gate, CI sharding and merge rules.

## Testing

```bash
python -m pytest                    # All tests
python -m pytest tests/test_keyboard_bridge.py  # Bridge tests
python -m pytest -k "fuzzy"         # Fuzzy recognizer tests
python -m pytest -k "property"      # Property-based suites only
```

### Property-based tests

`tests/test_property_import_hardening.py` and
`tests/test_property_prediction_invariants.py` use Hypothesis. They exist
because the things they cover are the ones where hand-picked examples are
weakest: adversarial *inputs* (an archive member or pack folder can be
named anything) and adversarial *orderings* (an incrementally maintained
counter breaks on the sequence nobody thought to write down).

- **Import hardening**: the property is "nothing outside the destination
  directory is ever created, modified or removed", asserted end-to-end
  against a sandbox holding a canary tree, plus the allow-list invariants.
  Note the deliberate inverse test (`test_the_legitimate_layout_is_still_accepted`):
  an allow-list that rejected everything would satisfy every containment
  property while silently turning import into a no-op.
- **Engine invariants**: `_user_total == sum(user_vocab.values())` checked
  after *every individual* mutation across generated operation sequences,
  so a failure names the operation rather than the sequence. Plus the
  spatial model's normalisation and the `_context_buffer` / `_current_word`
  accounting.
- **Keystroke state** (`tests/test_property_keystroke_state.py`, after
  PowerToys Keyboard Manager's `MockedInput`): two state machines drive a
  real bridge over `tests/fake_os_keyboard.py`, a synthesizer that models the
  OS truthfully (a held Shift uppercases `send_text`, a held Ctrl turns it
  into chords, `fail_after(n)` makes a call raise). After every step the
  modifiers the OS holds must equal the ones the bridge reports active, and
  every verbatim insert must arrive intact. **A new keystroke path or
  modifier slot gets a rule here**; this is what found the release-before-flag
  bug `_release_held` fixes. Do not weaken the fake to make a failure go away.
- **Loader fuzzing** (`tests/test_property_loader_fuzz.py`): bytes, mutated
  JSON and deep nesting into every on-disk loader, plus `text_patterns` under
  a time bound. A new store's loader belongs in it.

Every `tests/test_property_*.py` module also runs nightly under the
`alpha-osk-deep` profile
(`.github/workflows/fuzz-nightly.yml`, not derandomized, so each night tries
new inputs); see `docs/build/CI.md` for reproducing a failure.

### Model fingerprints and the prediction snapshot

`tests/test_model_fingerprint.py` (after DasherCore's model tests) asserts
that training is deterministic, save is byte-idempotent, text with nothing
to learn changes nothing, and `clear_user_data()` returns to the fresh
fingerprint. It also holds `tests/data/prediction_snapshot.json`, the top-5
predictions for 30 probes. **An intended engine change fails it by design**:
regenerate with `ALPHA_OSK_UPDATE_SNAPSHOT=1` and let the diff show the
behaviour change in the PR. The n-gram and PPM break ranking ties by string,
which is what keeps the snapshot stable across processes (string hashing is
salted per process); a new sort over scores needs the same tie-break. `scripts/bench/bpc.py` is the matching intrinsic benchmark
(PPM bits per character on the KSR corpora; numbers in `docs/architecture/PPM.md`).

### Autouse guards in `tests/conftest.py`

Two fixtures apply to every test, both stating a property that has to hold
for tests nobody has written yet rather than being patched in case by case:

- **`_unplug_the_live_desktop`** stubs `is_password_field()` and
  `external_click_detected()`. `KeyboardBridge` is constructed for real, and
  both read the developer's actual desktop, so a password field on screen
  flipped the bridge into privacy mode mid-test.
- **`_no_real_update_relauncher`** stubs `updater._spawn_relauncher`. Several
  `download_and_install` tests stub only `_launch_installer` and reached the
  real spawn, which launches a *detached* process by design; it outlives the
  pytest worker, so every run of `tests/test_updater.py` stranded four of
  them, each holding a console window. (Today the real spawn is also a
  bundle-sized copy into the developer's temp dir, one more reason no test
  may reach it.)

Both fail the same way when absent: something outside the test survives it.
A test wanting the real behaviour patches the same name and wins, since its
own monkeypatch applies after the fixture's.

Determinism is load-bearing: the `alpha-osk` profile in `tests/conftest.py`
sets `database=None` and `derandomize=True`, so these cannot pass locally
and fail on CI from a stale `.hypothesis` corpus. Use
`--hypothesis-profile=alpha-osk-fast` (25 examples) while iterating. **Don't
add `assume()` to filter a generated value down to a narrow case**: it
throws away most examples and trips the `filter_too_much` health check;
build a strategy that generates the interesting shape directly, and branch
on the predicate instead of discarding.

**Two traps these tests already hit**, worth knowing before writing more:
- The bridge's buffer accounting is a per-keystroke *delta*, not a mirror
  of the screen. Space with no word in progress deliberately commits
  nothing, so an equality model flags a non-defect.
- Privacy mode must be set via `setPrivacyMode()`, not by poking
  `_privacy_mode`. Every keystroke calls `_check_password_field_sync()` to
  close the 200 ms polling race, and that overwrites a hand-set flag on the
  first press; only `_privacy_mode_manual` makes it stand down.

### Pre-push check

`python check.py` runs the same gates CI does (lint, format, mypy under both
platforms, pytest) in ~60 s; `--full` adds the `--cov-fail-under=60` coverage
gate (~110 s, full CI parity). `python check.py --install-hook` wires it to
`git push` so it happens instead of being remembered; `git push --no-verify`
is the escape hatch. Hooks are not version controlled, so a fresh clone runs
that once. The hook is shared by every worktree and borrows the main
checkout's `venv/` from one (it used to look only in the worktree, fail to
import ruff, and get bypassed); `check.py` tips when the installed hook is an
older copy, so rerun `--install-hook` then. A gate on Windows cannot see a
Linux-only failure, so `tests/test_windows_only_patches.py` catches the one
that has bitten: patching `ctypes.windll` (and the rest of ctypes' Windows
half) without `create=True` / `raising=False`.

The suite is sharded on two different axes: `pytest-xdist` (`-n auto`) across
one machine's cores, and `--shard-id` / `--shard-count` across CI machines
(the matrix is `os x shard[0..3]`, eight jobs, each still `-n auto`). That is
what makes the gate a minute rather than twenty-five, and CI fourteen minutes
rather than twenty-six. Measurements, the per-choice reasoning, and the bugs
each one fixed: `docs/build/CI.md`. The rules that outlive the reasoning:

- **Any test touching QSettings, the registry, a fixed temp path, or any other
  machine-global resource has to key it per xdist worker**, through
  `tests/qt_settings_scope.py` (suffixed with `PYTEST_XDIST_WORKER`). Workers
  sharing one scope call `.clear()` on each other mid-test, and it surfaces as
  exactly the persistence bug those tests exist to catch, not as an obvious
  crash.
- **The shard hash cannot be `hash()`.** CPython salts string hashing per
  process, so workers would disagree about which tests are theirs, which does
  not fail loudly: it runs some tests twice and others never. It is
  `crc32(nodeid) % count`, in `tests/conftest.py::pytest_collection_modifyitems`.
- **Dependabot patch and minor updates merge themselves once the required
  checks pass** (`.github/workflows/dependabot-auto-merge.yml`); a library
  major waits for a person, while a GitHub Actions bump merges at any level
  because the PR's own checks run the changed workflow (the `ci.yml` uses;
  the scheduled telemetry and nightly OSV uses are outside that gate and
  surface on their next run). Needs the
  repository's "Allow auto-merge" setting on. The job
  runs only when Dependabot opened the PR *and* caused the event, and never
  checks out PR code. To take a Dependabot PR over, `gh pr merge <n>
  --disable-auto` before pushing to it. A merge made with `GITHUB_TOKEN` does
  not trigger CI's push run on main, and protection does not require branches
  to be up to date, so a break from an auto-merged update surfaces on the next
  PR's checks rather than on main. **Dependabot is switched on in three
  places and needs all three**: the version schedule in
  `.github/dependabot.yml`, the repository's *Dependabot security updates*
  setting (a different trigger, "an advisory now names your pinned version",
  enabled 2026-09-20 after running without it), and *Allow auto-merge*. A
  security update is an ordinary Dependabot PR, so the auto-merge job covers
  it with no new workflow. See `docs/build/CI.md`.
- **Branch protection requires the `Tests` job, not the shards.** Required
  checks are configured by name in the repo settings, so naming shards there
  means reconfiguring protection on every change to the shard count, and a
  stale entry blocks every PR for ever on a check that can never report.
- **One approving review is required to merge, and administrators are
  exempt.** GitHub will not let an author approve their own PR, so the
  maintainer merges with an admin bypass; the rule gates any future
  collaborator or token instead. **Merge with `python scripts/merge_pr.py
  <n>`, never a bare `gh pr merge --admin`**: `--admin` skips the required
  checks too, and protection does not require an up-to-date branch, which
  is how two PRs each green alone broke main together on 2026-10-05. The
  script updates the branch onto main, waits for the required checks on that
  head, re-runs a runner-fault failure once, and merges with
  `--match-head-commit`. Exit code 3 means another merge landed in the
  round trip between its last comparison and the merge (a gap only a merge
  queue could close): watch main's run for that commit. Main's CI groups by
  commit and never cancels, so every merge gets its own verdict. The Dependabot workflow approves the patch
  and minor updates it queues, which needs the repository's "Allow GitHub
  Actions to create and approve pull requests" setting on. Decided
  2026-09-15 from the security audit; see `docs/build/CI.md`.
- **`--cov-fail-under` belongs to the separate `coverage` job**, not the test
  jobs: a shard measures roughly a quarter of the lines. Its artifact upload
  needs `include-hidden-files: true`, or every upload is empty and the gate
  silently stops gating.
- **There is deliberately no fast-subset tier.** The static steps cost ~5 s
  between them, so the gate is pytest, and pytest is dominated by per-test
  setup rather than test count. If the gate creeps back up, measure before
  tiering: coverage on the one gate that runs before code leaves the machine
  is the last thing to trade away.
- **The `test` and `coverage` jobs carry `timeout-minutes: 20`.** A shard
  finishes in about six minutes, so a job that reaches twenty has hung, not
  slowed; without it the default is six hours, and a flaky hang on a windows
  shard held a PR for half an hour until it was cancelled by hand. If the
  shards ever grow toward it, measure what grew before raising the number.

**A test module that constructs a Qt application must build a
`QGuiApplication` under that same scope, never a bare
`QCoreApplication`.** There is one application per *process*, it cannot
be upgraded after construction, and `-n auto` puts unrelated modules in
the same worker. `tests/test_dictation.py` shipped briefly with a fixture
doing `QCoreApplication([])` with no organisation name, and the cost was
not subtle: every headless QML test that happened to run after it in that
worker failed at *setup*, because `QQmlApplicationEngine` needs a GUI
application it could no longer get. The unnamed organisation is the worse
half, since a QML `Settings` element resolves to a process-external store
and an unnamed app points it at the **real user's** settings. That is why
the QML modules assert their organisation in the `qapp` fixture rather
than merely setting it; the assertion is what caught this, and any new
`qapp` fixture should carry it too.

