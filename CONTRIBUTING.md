# Contributing to Alpha-OSK

Thanks for your interest. Alpha-OSK is an accessibility tool built by and
for people with motor disabilities. Contributions of every size are
welcome, especially from users of adaptive technology.

## Before you start

- Read the [Code of Conduct](CODE_OF_CONDUCT.md). It applies to every
  interaction in issues, PRs, and discussions.
- For security issues, **do not** open a public issue. Follow the process
  in [SECURITY.md](SECURITY.md) instead.
- Releases live in a separate public repo:
  [`owenpkent/alpha-osk-releases`](https://github.com/owenpkent/alpha-osk-releases).
  Installer downloads and signed binaries go there. Source code,
  development, and issues live here.

## Ways to contribute

- **Report bugs** using the bug report template. Attach the diagnostic log if you have one: *Settings > Data & Privacy > Diagnostics > Open Log Folder*, or `alpha-osk.log` in the config directory. It carries crash tracebacks and never carries typed content.
- **Request features** using the feature request template.
- **Improve docs** — typos, clearer wording, missing context.
- **Add tests**: the suite is large (1679) but coverage gaps exist.
- **Code changes** — see "Development setup" below.

If you are unsure whether a change is wanted, open an issue first to
discuss. For larger features, please discuss before writing code so we can
align on scope and approach.

## Development setup

```bash
git clone https://github.com/owenpkent/alpha-osk.git
cd alpha-osk
python run.py
```

`run.py` creates a virtual environment, installs dependencies, and
launches the keyboard. Subsequent runs reuse the venv.

### Running tests

```bash
python -m pytest -n auto            # full suite, sharded (~50s; ~25min without -n)
python -m pytest tests/test_keyboard_bridge.py
python -m pytest -k "fuzzy"
```

`-n auto` matters more here than it usually would: the suite's cost is
per-process setup repeated per test rather than any single slow test, so
running it in one process is roughly 25x slower. `python check.py` passes
the flag for you.

Two suites are property-based (`tests/test_property_*.py`, using
[Hypothesis](https://hypothesis.readthedocs.io/)): they cover the archive
and vocabulary-pack import paths, where the property is "nothing outside
the destination directory is ever touched", and the prediction-engine
invariants that the rest of the code is allowed to assume. They run under a
fixed profile declared in `tests/conftest.py` with the example database
disabled, so they are deterministic — a run cannot pass locally and fail on
CI because of a cached corpus. To iterate faster while developing:

```bash
python -m pytest tests/test_property_import_hardening.py \
  -p no:randomly --hypothesis-profile=alpha-osk-fast
```

If one fails, the report prints the exact generated input that broke it.
Reproduce it by pasting the `@reproduce_failure(...)` decorator Hypothesis
suggests onto the test, or just add the shrunk case as a plain example
test — a minimal counterexample usually deserves to be pinned permanently.

Some QML tests (`tests/test_qml_*.py`) need Qt's GL/xkb system libraries.
They skip themselves with a message naming the missing library if it is not
present, rather than failing the run; on Debian/Ubuntu, `sudo apt-get
install libegl1 libgl1 libxkbcommon0` is enough to make them run.

If you write a headless QML test, know that both of these have already
shipped a test that could not fail:

- **`root.findChildren(QObject, name)` does not find a `Repeater`'s
  delegates.** They are re-parented as *visual* children, so the call
  returns an empty list and your assertions pass over nothing. Walk
  `childItems()` from `root.contentItem` instead, and assert the result is
  non-empty so a broken lookup fails loudly. See `_pill_texts` in
  `tests/test_qml_prediction_bar.py`.
- **Do not assert `contentWidth <= width` on an eliding `Text`.** Once it
  elides, `contentWidth` measures the shortened string, so the comparison
  is true by construction. Assert on `Text.truncated`.

More generally: when a test guards against a rendering defect, check that
it fails against the broken code before you trust it. Both bugs above were
found only because someone re-ran the new test on the pre-fix tree.

### Pre-push check

Before pushing, run the same gates CI runs:

```bash
python check.py              # lint + format + type + tests, ~60s
python check.py --full       # adds coverage gate, ~110s
python check.py --install-hook  # run it on `git push` instead of by hand
```

This catches ruff / ruff-format / mypy / pytest failures locally instead
of red Xs in CI. Formatting is checked separately from linting because
`ruff check` does not look at layout; fix a format failure by running
`ruff format src/ tests/`, not by hand.

`--install-hook` writes `.git/hooks/pre-push`. Hooks are not version
controlled, so run it once per clone; `git push --no-verify` skips it.

The suite runs sharded via `pytest-xdist` (`-n auto`), which is what
keeps it under a minute. The cost is per-process setup repeated per
test (building a `KeyboardBridge` loads a 20k-word dictionary and builds
a SymSpell index), not any single slow test. Two consequences for new
tests:

- **Anything machine-global has to be keyed per worker.** QSettings
  scopes, registry keys, fixed temp paths, ports. The headless QML tests
  do this via `tests/qt_settings_scope.py`; use `tmp_path` for files.
  A test that shares such state with another worker fails as though the
  code were broken, which is the most expensive kind of flake to read.
- **`python check.py --serial` reproduces a failure in one process**, which
  is the first thing to try when a test passes alone and fails in a run.

CI shards the same way (across machines as well as cores), so a green
local run and a green CI run now cost about the same and mean the same
thing. `docs/build/CI.md` has the full arrangement and the reasoning
behind it. It also cancels a run that a
newer push has superseded, so pushing a few commits in a row gives you
one result for the tip rather than several stale ones arriving out of
order.

One asymmetry to know about, because it has bitten: a test that **skips**
on your machine and runs on CI is a hole in that promise. The pill-width
tests skip when Qt resolves a fixed-width placeholder font, which it does
under the offscreen platform unless pointed at real fonts, and for a while
that meant seven of them ran on Linux CI and nowhere else. `conftest.py`
sets `QT_QPA_FONTDIR` on Windows to close that one. If you add a test that
skips itself on a capability, check whether the capability can be supplied
instead.

## Architecture orientation

The single most useful file to read first is
[`CLAUDE.md`](CLAUDE.md). It is the AI-onboarding doc, but it is also the
clearest map of the codebase: directory layout, the prediction engine, the
QML/Python bridge, platform abstractions, settings, telemetry, build
pipeline, and the gotchas that have bitten us. Skim it before opening a
PR.

Other useful docs in `docs/`:

- `docs/architecture/HYBRID_MERGING.md` — prediction merging strategies
- `docs/architecture/FUZZY_RECOGNITION.md` — spatial error correction
- `docs/architecture/PPM.md` — character-level prediction
- `docs/architecture/DICTATION.md` (voice input, Deepgram-backed)
- `docs/build/WINDOWS.md`, `docs/build/LINUX.md`, `docs/build/MACOS.md` — per-platform build
  and packaging notes

The dictation tests need neither a Deepgram API key nor a microphone. They
drive a fake provider and stub the audio capture, so `tests/test_dictation.py`
runs on any machine with nothing to set up first.

## Coding conventions

- **Python**: linted with `ruff`, formatted with `ruff format`, typed
  with `mypy`. Run `python check.py` before pushing, which covers all
  three (plus the tests).
- **Comments**: write them only when the *why* is non-obvious. Don't
  describe what well-named code already does.
- **No em dashes** in code, docs, commit messages, or PR descriptions.
  Use periods, commas, parens, or rephrase.
- **Conventional commits**: `feat:`, `fix:`, `docs:`, `refactor:`,
  `chore:`, `test:`. Subject under ~72 chars.
- **Tests required** for behavior changes. Use `pytest` and keep tests in
  `tests/`.
- **Accessibility first**: any UI change must work for users who cannot
  press keys quickly or precisely. If you change keystroke timing, repeat
  intervals, or visual feedback, test with `repeat_delay` defaults and
  the longest reasonable warm-up grace.

## Pull request flow

1. Fork and branch from `main`.
2. Make your change, with tests.
3. Run `python check.py` locally.
4. Push and open a PR using the template.
5. CI runs ruff + ruff-format + mypy + pytest + OSV vulnerability scan. `main` is a
   protected branch: the merge button stays disabled until five required
   checks pass green: `Lint`, `Type Check`, `Test (ubuntu-latest)`,
   `Test (windows-latest)`, and `OSV Scanner (deps CVE check)`. A new CVE
   advisory in a dependency lockfile fails the OSV gate and blocks the
   merge just like a failing test would.
6. A maintainer will review. Iterate as needed.

If your PR touches the prediction engine, build pipeline, or telemetry,
please call that out in the PR description so it gets extra eyes.

## License

By contributing, you agree that your contributions will be licensed under
the same [MIT License](LICENSE) that covers the project.
