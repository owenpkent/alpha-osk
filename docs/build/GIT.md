# Git conventions

This doc holds the full write-up behind the CLAUDE.md sections *Git Conventions* and *Community Files*.

## Git Conventions

Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`

### Cleaning up merged branches

`python scripts/clean_branches.py` deletes local branches whose pull
request has been merged; `--dry-run` lists them instead, and
`--install-hook` wires it to `git pull` so it happens on its own.

**It cannot use `git branch -d`, and that is the whole reason it
exists.** This repo squash-merges, so a merge rewrites the branch's
commits into one new commit and the branch tip is never an ancestor of
`main`: `-d` refuses every merged branch alike as "not fully merged".
What is left is `-D`, which refuses nothing and will throw away
unlanded work just as happily. So the question the script asks is not
git's "is it merged" but "does this branch have a pull request, and was
it merged", which is the one a squash-merging repo can actually answer.
A branch whose PR is open, closed unmerged, or missing is kept, and the
run says which. `git diff main <branch>` is **not** a usable check
either: main moves on after the merge, so that diff reports main's own
later commits as deletions on the branch side and every merged branch
looks like it still has work.

Three things fail closed, each because the cost of being wrong is
asymmetric (a kept branch is clutter, a deleted one is lost work): a
branch with no upstream at all is never a candidate, since never-pushed
work exists nowhere else and renders the same empty tracking field an
up-to-date branch does; `gh` being missing or unauthenticated keeps
every branch rather than deleting them all; and `main` and the
checked-out branch are refused by name. **A merged branch checked out
in any other worktree is kept too, with the worktree named**: git
refuses to delete it anyway, and that refusal used to escape as an
uncaught error that ended the run, leaving every later branch behind.
It is usually another session still working there, so it is reported,
not retried. Any other refused delete is reported as `failed`, the run
carries on, and the exit code is 1.

**"Automatic" here means `git pull`, because there is no local event for
a merge.** The merge happens on GitHub and nothing on this machine is
told, so `--install-hook` hangs a `post-merge` hook off the pull that
brings the squashed commit down, and sets `fetch.prune` for the repo,
which is what makes the "upstream is gone" reading accurate at the
moment the hook runs. The hook ends in `|| true`: a branch left behind
is clutter, and clutter is not worth making `git pull` report a
failure. Hooks are not version controlled, so a fresh clone runs
`--install-hook` once, the same as `check.py --install-hook`.

Guarded by `tests/test_clean_branches.py`, where every case that could
delete something is paired with the near-miss it must keep.


## Community Files

The repo ships the standard GitHub community health files at the top level and under `.github/`:

- `CODE_OF_CONDUCT.md` - Contributor Covenant 2.1. Reports go to owenpkent@gmail.com with subject `CONDUCT: alpha-osk`.
- `CONTRIBUTING.md` - dev setup, `check.py` pre-push gate, conventions, PR flow. Points new contributors at this file as the architecture map.
- `SECURITY.md` - private vulnerability reporting via the releases repo's GHSA form, email fallback.
- `.github/ISSUE_TEMPLATE/bug_report.yml` and `feature_request.yml` - form templates. `config.yml` disables blank issues and links to the security advisory + Discussions.
- `.github/pull_request_template.md` - summary, type, test plan, accessibility check.

If you change the security reporting flow, the CoC contact email, or the contribution gates, update both the relevant file and the cross-references in `CONTRIBUTING.md` / `bug_report.yml`.

