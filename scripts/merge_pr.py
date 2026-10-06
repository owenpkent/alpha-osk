"""Merge a pull request with the admin bypass, but only on checks that really ran.

Usage:
    python scripts/merge_pr.py 123                  # wait for green, then merge
    python scripts/merge_pr.py 123 --dry-run        # read everything, mutate nothing
    python scripts/merge_pr.py 123 --poll 15 --timeout 30

Requires the `gh` CLI authenticated against this repo.

Why this exists
---------------
Branch protection here does not require a PR to be up to date with main
(``strict: false``), and one approving review is required, which GitHub
will not let an author give their own PR.  So the maintainer merges with
``gh pr merge --admin``.  But ``--admin`` skips the required checks as
well as the review.  That cost three times: #166 and #176 were each green
alone and broke main together; #144's OSV scan failed only because its
branch predated a lockfile fix already on main; and one PR was
admin-merged while its shards were still running.

The rule this script enforces: the bypass may skip the review, never the
checks, and the checks must have run on a head that contains current main.
So it brings the branch up to date, waits for every required check to
pass on that head, re-checks that main has not moved meanwhile, and only
then merges, pinned to that exact commit with ``--match-head-commit``.

A required check that was cancelled or timed out (a runner problem, not
code) gets one automatic rerun per invocation.  A real failure never does.

What it cannot close: ``gh pr merge`` pins the head, never main, so another
actor (Dependabot's auto-merge, a second invocation) can land between the
last comparison and the merge call, and the head then lands on a main its
checks never saw.  The comparison runs last, right before the merge, so
that window is one API round trip wide, and the merge commit's parent is
read back afterwards: a landing in the window is reported with exit code 3
rather than hidden.  Closing it needs a server-side merge queue.

Exit codes: 0 merged (or dry run finished), 1 refused or failed, 2 timed
out, 3 merged but main moved in the window, so watch main's own CI run.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional, Set, Tuple

BASE = "main"
# The aggregator job summarises the shards, so its own failure says nothing
# about why: the shard jobs under it are what to inspect.
AGGREGATOR = "Tests"
_INFRA = {"cancelled", "timed_out"}
_BAD_JOB = {"failure"} | _INFRA

# Module-level so tests can replace them and never really wait.
_sleep = time.sleep
_now = time.monotonic


def _gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def _gh_ok(*args: str) -> str:
    result = _gh(*args)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def _view(number: int) -> Dict:
    fields = "number,state,isDraft,baseRefName,headRefName,headRefOid,mergeable"
    return json.loads(_gh_ok("pr", "view", str(number), "--json", fields))


def _compare(repo: str, sha: str) -> Tuple[int, str]:
    """Return (commits of main the head lacks, main's tip as of this call)."""
    out = _gh_ok(
        "api", f"repos/{repo}/compare/{BASE}...{sha}", "--jq", "[.behind_by, .base_commit.sha]"
    )
    behind, tip = json.loads(out)
    return int(behind or 0), str(tip or "")


def _behind_by(repo: str, sha: str) -> int:
    return _compare(repo, sha)[0]


def _landed_on(repo: str, number: int) -> str:
    """The commit a merged PR's squash commit sits on: its only parent."""
    view = json.loads(_gh_ok("pr", "view", str(number), "--json", "mergeCommit"))
    oid = (view.get("mergeCommit") or {}).get("oid", "")
    if not oid:
        return ""
    return _gh_ok("api", f"repos/{repo}/commits/{oid}", "--jq", ".parents[0].sha").strip()


def _required_contexts(repo: str) -> List[str]:
    out = _gh_ok(
        "api",
        f"repos/{repo}/branches/{BASE}/protection/required_status_checks",
        "--jq",
        ".contexts",
    )
    return list(json.loads(out))


def _checks(number: int) -> List[Dict]:
    # Exits non-zero while anything is pending or failing, so the exit code
    # is meaningless here: empty stdout simply means nothing has reported.
    result = _gh("pr", "checks", str(number), "--json", "name,bucket,link,state,workflow")
    try:
        data = json.loads(result.stdout) if result.stdout.strip() else []
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _judge(required: List[str], checks: List[Dict]) -> Tuple[List[Dict], List[str]]:
    """Return (bad required checks, required contexts still pending).

    A required name matches a check by exact name; gh reports the same
    strings branch protection stores, including the reusable workflow's
    "OSV Scanner (deps CVE check) / osv-scan".  One that has not reported
    at all is pending, never passing.  ``skipping`` counts as a pass.
    """
    bad: List[Dict] = []
    pending: List[str] = []
    for name in required:
        mine = [c for c in checks if c.get("name") == name]
        if not mine:
            pending.append(name)
            continue
        bad.extend(c for c in mine if c.get("bucket") in ("fail", "cancel"))
        if any(c.get("bucket") not in ("pass", "skipping", "fail", "cancel") for c in mine):
            pending.append(name)
    return bad, pending


def _run_id(link: str) -> Optional[str]:
    match = re.search(r"/actions/runs/(\d+)", link or "")
    return match.group(1) if match else None


def _classify(bad: List[Dict]) -> Tuple[bool, Set[str]]:
    """Decide whether every failure is a runner problem; return the run ids."""
    runs = {r for r in (_run_id(c.get("link", "")) for c in bad) if r}
    verdicts: List[str] = []
    for run in sorted(runs):
        jobs = json.loads(_gh_ok("run", "view", run, "--json", "jobs")).get("jobs", [])
        for job in jobs:
            if job.get("name") == AGGREGATOR:
                continue
            if job.get("conclusion") in _BAD_JOB:
                verdicts.append(job["conclusion"])
    infra = bool(verdicts) and all(v in _INFRA for v in verdicts)
    return infra, runs


def _wait_for_new_head(number: int, old: str, deadline: float, poll: float) -> bool:
    while _now() < deadline:
        _sleep(poll)
        if _view(number)["headRefOid"] != old:
            return True
    return False


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("number", type=int)
    parser.add_argument("--dry-run", action="store_true", help="print mutations, run none")
    parser.add_argument("--poll", type=float, default=30, help="seconds between polls")
    parser.add_argument("--timeout", type=float, default=60, help="minutes before giving up")
    args = parser.parse_args(argv)
    n = args.number

    deadline = _now() + args.timeout * 60
    retried = False
    last = ""

    def say(msg: str) -> None:
        nonlocal last
        if msg != last:
            print(msg)
            last = msg

    try:
        repo = json.loads(_gh_ok("repo", "view", "--json", "nameWithOwner"))["nameWithOwner"]
        required = _required_contexts(repo)
        while True:
            if _now() >= deadline:
                print(f"timed out after {args.timeout:g} minutes; not merging")
                return 2
            pr = _view(n)
            if pr["state"] != "OPEN":
                print(f"PR #{n} is {pr['state']}, not OPEN; refusing")
                return 1
            if pr["isDraft"]:
                print(f"PR #{n} is a draft; refusing")
                return 1
            if pr["baseRefName"] != BASE:
                print(f"PR #{n} targets {pr['baseRefName']}, not {BASE}; refusing")
                return 1
            if pr["mergeable"] == "CONFLICTING":
                print(f"PR #{n} conflicts with {BASE}; resolve the conflict first")
                return 1
            sha = pr["headRefOid"]

            behind = _behind_by(repo, sha)
            if behind > 0:
                if args.dry_run:
                    print(f"behind {BASE} by {behind}; would run: gh pr update-branch {n}")
                    print("dry run stops here: the checks would have to run again")
                    return 0
                say(f"behind {BASE} by {behind}, updating")
                _gh_ok("pr", "update-branch", str(n))
                if not _wait_for_new_head(n, sha, deadline, args.poll):
                    print("timed out waiting for the updated head; not merging")
                    return 2
                continue

            bad, pending = _judge(required, _checks(n))
            if pending:
                say(f"waiting: {len(pending)} of {len(required)} required checks pending")
                _sleep(args.poll)
                continue
            if bad:
                infra, runs = _classify(bad)
                if infra and not retried:
                    ids = sorted(runs)
                    if args.dry_run:
                        print(f"runner failure; would run: gh run rerun {ids[0]} --failed")
                        return 0
                    retried = True
                    say("required checks cancelled or timed out, rerunning once")
                    for run in ids:
                        _gh_ok("run", "rerun", run, "--failed")
                    _sleep(args.poll)
                    continue
                print("required checks failed, not merging:")
                for check in bad:
                    print(f"  {check.get('name')}: {check.get('link')}")
                return 1

            # The checks passed on `sha`; make sure nothing moved under them.
            pr = _view(n)
            if pr["headRefOid"] != sha or _behind_by(repo, sha) > 0:
                say(f"{BASE} or the branch moved while waiting, starting over")
                continue

            say("all required checks passed")
            children = json.loads(
                _gh_ok(
                    "pr", "list", "--base", pr["headRefName"], "--state", "open", "--json", "number"
                )
            )
            # Merging the parent with --delete-branch closes any PR based on it.
            for child in children:
                cmd = ["pr", "edit", str(child["number"]), "--base", BASE]
                if args.dry_run:
                    print("would run: gh " + " ".join(cmd))
                else:
                    print(f"retargeting stacked PR #{child['number']} to {BASE}")
                    _gh_ok(*cmd)
            # The last look at main, after the retargets have widened the gap
            # since the previous one.  The merge pins the head only, so what
            # is left open is the round trip between here and the merge.
            behind, tip = _compare(repo, sha)
            if behind > 0:
                say(f"{BASE} moved while the children were retargeted, starting over")
                continue
            merge = [
                "pr", "merge", str(n), "--squash", "--admin", "--delete-branch",
                "--match-head-commit", sha,
            ]  # fmt: skip
            if args.dry_run:
                print("would run: gh " + " ".join(merge))
                return 0
            _gh_ok(*merge)
            landed = _landed_on(repo, n)
            if landed and tip and landed != tip:
                print(
                    f"merged #{n}, but {BASE} moved from {tip[:9]} to {landed[:9]} between"
                    " the last check and the merge: the checks never ran on this"
                    f" combination. Watch the CI run on {BASE} for this merge."
                )
                return 3
            print(f"merged #{n}; `git pull` on {BASE} will delete the local branch")
            return 0
    except RuntimeError as exc:
        print(str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
