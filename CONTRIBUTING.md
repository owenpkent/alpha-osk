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
  [`okstudio1/alpha-osk-releases`](https://github.com/okstudio1/alpha-osk-releases).
  Installer downloads and signed binaries go there. Source code,
  development, and issues live here.

## Ways to contribute

- **Report bugs** using the bug report template.
- **Request features** using the feature request template.
- **Improve docs** — typos, clearer wording, missing context.
- **Add tests** — the suite is large (270+) but coverage gaps exist.
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
python -m pytest                    # full suite
python -m pytest tests/test_keyboard_bridge.py
python -m pytest -k "fuzzy"
```

### Pre-push check

Before pushing, run the same gates CI runs:

```bash
python check.py        # lint + type + tests, ~85s
python check.py --full # adds coverage gate, ~3min
```

This catches ruff / mypy / pytest failures locally instead of red Xs in
CI.

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
- `docs/architecture/SWIPE_TYPING.md` — glide-typing decoder
- `docs/build/WINDOWS.md`, `docs/build/LINUX.md`, `docs/build/MACOS.md` — per-platform build
  and packaging notes

## Coding conventions

- **Python**: linted with `ruff`, typed with `mypy`. Run `ruff check src/`
  and `mypy src/` before pushing.
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
5. CI runs ruff + mypy + pytest + OSV vulnerability scan. Merges are
   gated on a clean run.
6. A maintainer will review. Iterate as needed.

If your PR touches the prediction engine, build pipeline, or telemetry,
please call that out in the PR description so it gets extra eyes.

## License

By contributing, you agree that your contributions will be licensed under
the same [MIT License](LICENSE) that covers the project.
