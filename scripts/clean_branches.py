"""Delete local branches whose work has already landed on main.

Usage:
    python scripts/clean_branches.py                # delete what it can prove
    python scripts/clean_branches.py --dry-run      # list them and stop
    python scripts/clean_branches.py --install-hook # run it on every `git pull`

Requires the `gh` CLI authenticated against this repo.

Making it automatic
-------------------
There is no local event for "a branch was merged": the merge happens on
GitHub, and nothing on this machine is told.  The nearest thing is the
`git pull` that brings the squashed commit down afterwards, so
``--install-hook`` writes a ``post-merge`` hook and turns on
``fetch.prune`` for this repo, which is what makes the "upstream is
gone" signal accurate at the moment the hook reads it.  Hooks are not
version controlled, so a fresh clone has to run that once.

The hook is deliberately best-effort: it never fails the pull.  A branch
left behind is clutter, and clutter is not worth a git operation
reporting failure over.

Why this needs a script rather than `git branch -d`
---------------------------------------------------
This repo squash-merges.  A squash rewrites the branch's commits into
one new commit, so the branch tip is never an ancestor of main and
``git branch -d`` refuses every merged branch as "not fully merged".
The only thing left is ``-D``, which refuses nothing at all and will
just as happily throw away work that never landed.

So the check cannot be "is it merged" in git's sense.  It is "does this
branch have a pull request, and was that pull request merged", which is
the question a squash-merging repo can actually answer.  A branch whose
PR is still open, closed unmerged, or absent is left alone and said so,
because those are the three shapes real unlanded work comes in.

Two guards on top of that, both for the case where the ledger and the
tree disagree:

* **The upstream must be gone.**  GitHub deletes the remote branch on
  merge (``delete_branch_on_merge`` is on), so a still-present remote
  means either the merge has not happened or someone pushed after it.
* **Never the current branch, and never main.**  Deleting the branch you
  are standing on fails anyway; refusing it by name gives a better
  message than git's.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

PROTECTED = {"main", "master"}

# `--no-fetch` because the pull that triggered this has just fetched, and
# `--install-hook` sets fetch.prune so that fetch pruned. `|| true` so a
# missing python, an unauthenticated gh or any other failure cannot make
# `git pull` report an error: see the module docstring.
_HOOK = """#!/bin/sh
# Installed by scripts/clean_branches.py --install-hook
# Deletes local branches whose pull request has been merged.
python scripts/clean_branches.py --no-fetch || true
"""


def _install_hook() -> int:
    """Write .git/hooks/post-merge and make `git pull` prune."""
    try:
        git_dir = Path(_git("rev-parse", "--git-dir"))
    except subprocess.CalledProcessError:
        print("not a git repository", file=sys.stderr)
        return 1
    hooks = git_dir / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "post-merge"
    if hook.exists() and "clean_branches.py" not in hook.read_text(encoding="utf-8"):
        # Refuse rather than clobber: a post-merge hook someone else
        # wrote is theirs, and silently replacing it is the kind of
        # thing that gets noticed weeks later.
        print(f"{hook} already exists and is not ours; leaving it alone", file=sys.stderr)
        return 1
    hook.write_text(_HOOK, encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    subprocess.run(["git", "config", "fetch.prune", "true"], check=True)
    print(f"installed {hook}")
    print("set fetch.prune=true for this repo")
    print("merged branches will now be cleaned up on every `git pull`")
    return 0


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def _branches_with_gone_upstreams() -> List[str]:
    """Local branches whose tracked remote branch no longer exists.

    ``%(upstream:track)`` renders exactly ``[gone]`` for these, which is
    the same signal ``git branch -vv`` prints.  Branches with no upstream
    at all render empty and are skipped: never-pushed work is the last
    thing this should be deleting.
    """
    out = _git(
        "for-each-ref",
        "--format=%(refname:short)\t%(upstream:track)",
        "refs/heads",
    )
    gone = []
    for line in out.splitlines():
        name, _, track = line.partition("\t")
        if track.strip() == "[gone]":
            gone.append(name)
    return gone


def _merged_pr(branch: str) -> Tuple[bool, str]:
    """Whether *branch* had a pull request that was merged, and why.

    The reason string is returned either way: a branch that is kept has
    to say what stopped it, or this reads as having silently skipped
    something.
    """
    try:
        out = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                "all",
                "--limit",
                "10",
                "--json",
                "number,state,mergedAt",
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except FileNotFoundError:
        return False, "gh CLI not found"
    except subprocess.CalledProcessError as exc:
        return False, f"gh failed: {exc.stderr.strip().splitlines()[-1] if exc.stderr else '?'}"

    try:
        prs = json.loads(out)
    except json.JSONDecodeError:
        return False, "gh returned unparseable JSON"

    if not prs:
        return False, "no pull request found"
    merged = [p for p in prs if p.get("state") == "MERGED"]
    if not merged:
        states = ", ".join(sorted({str(p.get("state")) for p in prs}))
        return False, f"pull request is {states.lower()}, not merged"
    pr = merged[0]
    when = (pr.get("mergedAt") or "")[:10]
    return True, f"PR #{pr['number']} merged {when}"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be deleted and stop.",
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip `git fetch --prune` (offline, or you just fetched).",
    )
    parser.add_argument(
        "--install-hook",
        action="store_true",
        help="Install a post-merge hook so this runs on every `git pull`.",
    )
    args = parser.parse_args(argv)

    if args.install_hook:
        return _install_hook()

    try:
        if not args.no_fetch:
            print("fetching...")
            subprocess.run(["git", "fetch", "--prune", "--quiet"], check=True)
        current = _git("rev-parse", "--abbrev-ref", "HEAD")
    except FileNotFoundError:
        print("git not found", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc}", file=sys.stderr)
        return 1

    candidates = _branches_with_gone_upstreams()
    if not candidates:
        print("nothing to clean: no local branch has a deleted upstream")
        return 0

    to_delete: List[Tuple[str, str]] = []
    kept: List[Tuple[str, str]] = []
    for branch in candidates:
        if branch in PROTECTED:
            kept.append((branch, "protected branch"))
        elif branch == current:
            kept.append((branch, "you are on it (switch away first)"))
        else:
            ok, reason = _merged_pr(branch)
            (to_delete if ok else kept).append((branch, reason))

    width = max(len(b) for b, _ in to_delete + kept)
    for branch, reason in kept:
        print(f"  keep    {branch:<{width}}  {reason}")
    for branch, reason in to_delete:
        sha = _git("rev-parse", "--short", branch)
        verb = "would delete" if args.dry_run else "deleted"
        if not args.dry_run:
            subprocess.run(["git", "branch", "-D", branch], capture_output=True, check=True)
        print(f"  {verb} {branch:<{width}}  {reason}  (was {sha})")

    if not to_delete:
        print("\nnothing deleted")
    elif args.dry_run:
        print(f"\n{len(to_delete)} branch(es) would be deleted; re-run without --dry-run")
    else:
        # The sha is the whole recovery story for a squash-merged branch:
        # `git branch <name> <sha>` brings it back, and the reflog keeps
        # it reachable for the usual 90 days.
        print(f"\n{len(to_delete)} branch(es) deleted. Recover one with: git branch <name> <sha>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
