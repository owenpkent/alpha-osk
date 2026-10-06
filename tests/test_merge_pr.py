"""`scripts/merge_pr.py`, which merges with the admin bypass.

`--admin` skips the review (which an author cannot give their own PR) but
also the required checks, so the script has to put the checks back: they
must have passed, on a head that contains current main.  Every test that
ends in a merge is paired with the near-miss that must not.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from scripts import merge_pr

REQUIRED = ["Lint", "Type Check", "OSV Scanner (deps CVE check) / osv-scan", "Tests"]
RUN_LINK = "https://github.com/o/r/actions/runs/777/job/1"


def _check(name: str, bucket: str = "pass") -> dict:
    return {"name": name, "bucket": bucket, "link": RUN_LINK, "state": "X", "workflow": "CI"}


def _green() -> list:
    return [_check(n) for n in REQUIRED]


def _job(name: str, conclusion: str) -> dict:
    return {"name": name, "conclusion": conclusion, "status": "completed"}


class _FakeGh:
    """Answers `gh` by argv and records every call."""

    def __init__(
        self,
        *,
        checks: list | None = None,
        behind: list | None = None,
        jobs: list | None = None,
        children: list | None = None,
        state: str = "OPEN",
        draft: bool = False,
        base: str = "main",
        mergeable: str = "MERGEABLE",
    ) -> None:
        self.checks = checks if checks is not None else [_green()]
        self.behind = behind if behind is not None else [0]
        self.jobs = jobs or []
        self.children = children or []
        self.state, self.draft, self.base, self.mergeable = state, draft, base, mergeable
        self.heads = [f"sha{i}" for i in range(10)]
        self.head_index = 0
        self.calls: list[list[str]] = []
        self.clock = 0.0

    @staticmethod
    def _next(seq: list):
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def run(self, cmd, **kwargs):  # noqa: ANN001 - subprocess.run shim
        assert cmd[0] == "gh", cmd
        args = cmd[1:]
        self.calls.append(args)
        out, code = "", 0
        if args[:2] == ["repo", "view"]:
            out = json.dumps({"nameWithOwner": "o/r"})
        elif args[:2] == ["pr", "view"]:
            out = json.dumps(
                {
                    "number": 5,
                    "state": self.state,
                    "isDraft": self.draft,
                    "baseRefName": self.base,
                    "headRefName": "feat",
                    "headRefOid": self.heads[self.head_index],
                    "mergeable": self.mergeable,
                }
            )
        elif args[0] == "api" and "compare" in args[1]:
            assert args[1].endswith(f"...{self.heads[self.head_index]}"), args
            out = str(self._next(self.behind))
        elif args[0] == "api":
            out = json.dumps(REQUIRED)
        elif args[:2] == ["pr", "checks"]:
            rows = self._next(self.checks)
            out = json.dumps(rows) if rows else ""
            code = 0 if rows and all(r["bucket"] in ("pass", "skipping") for r in rows) else 8
        elif args[:2] == ["run", "view"]:
            out = json.dumps({"jobs": self._next(self.jobs)})
        elif args[:2] == ["pr", "list"]:
            out = json.dumps([{"number": c} for c in self.children])
        elif args[:2] == ["pr", "update-branch"]:
            self.head_index += 1
        elif args[:2] in (["pr", "edit"], ["pr", "merge"], ["run", "rerun"]):
            pass
        else:
            raise AssertionError(f"unexpected gh call: {args}")
        return subprocess.CompletedProcess(cmd, code, stdout=out, stderr="")

    def sleep(self, seconds: float) -> None:
        self.clock += seconds

    def now(self) -> float:
        return self.clock

    @property
    def mutations(self) -> list[list[str]]:
        kinds = (["pr", "merge"], ["pr", "edit"], ["pr", "update-branch"], ["run", "rerun"])
        return [c for c in self.calls if c[:2] in kinds]

    def merges(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["pr", "merge"]]


def _run(monkeypatch, fake: _FakeGh, *extra: str) -> int:
    monkeypatch.setattr(merge_pr.subprocess, "run", fake.run)
    monkeypatch.setattr(merge_pr, "_sleep", fake.sleep)
    monkeypatch.setattr(merge_pr, "_now", fake.now)
    return merge_pr.main(["5", *extra])


def test_up_to_date_and_green_merges_pinned_to_the_head(monkeypatch):
    fake = _FakeGh()
    assert _run(monkeypatch, fake) == 0
    assert fake.merges() == [
        [
            "pr",
            "merge",
            "5",
            "--squash",
            "--admin",
            "--delete-branch",
            "--match-head-commit",
            "sha0",
        ]  # fmt: skip
    ]
    assert fake.mutations == fake.merges()


def test_behind_updates_waits_for_the_new_head_and_merges_that_sha(monkeypatch):
    fake = _FakeGh(behind=[2, 0])
    assert _run(monkeypatch, fake) == 0
    assert ["pr", "update-branch", "5"] in fake.calls
    assert fake.merges()[0][-1] == "sha1"
    assert fake.calls.index(["pr", "update-branch", "5"]) < fake.calls.index(fake.merges()[0])


def test_main_moving_during_the_wait_updates_again_before_merging(monkeypatch):
    # up to date, checks go green, main moves (3), still behind on the next lap, then current.
    fake = _FakeGh(behind=[0, 3, 3, 0, 0])
    assert _run(monkeypatch, fake) == 0
    assert fake.calls.count(["pr", "update-branch", "5"]) == 1
    assert fake.merges()[0][-1] == "sha1"


def test_pending_checks_are_waited_for_not_skipped(monkeypatch):
    pending = [_check(n, "pending" if n == "Tests" else "pass") for n in REQUIRED]
    fake = _FakeGh(checks=[pending, _green()])
    assert _run(monkeypatch, fake) == 0
    assert fake.clock > 0
    assert len(fake.merges()) == 1


def test_a_missing_required_check_is_pending_not_passing(monkeypatch):
    partial = [_check(n) for n in REQUIRED if n != "Lint"]
    fake = _FakeGh(checks=[partial])
    assert _run(monkeypatch, fake, "--timeout", "1") == 2
    assert fake.merges() == []


def test_skipping_counts_as_a_pass(monkeypatch):
    fake = _FakeGh(checks=[[_check(n, "skipping") for n in REQUIRED]])
    assert _run(monkeypatch, fake) == 0
    assert len(fake.merges()) == 1


def test_a_real_failure_stops_without_rerun_or_merge(monkeypatch, capsys):
    checks = [_check(n, "fail" if n == "Tests" else "pass") for n in REQUIRED]
    jobs = [[_job("Tests", "failure"), _job("Test (ubuntu-latest 1/4)", "failure")]]
    fake = _FakeGh(checks=[checks], jobs=jobs)
    assert _run(monkeypatch, fake) == 1
    assert [c for c in fake.calls if c[:2] == ["run", "rerun"]] == []
    assert fake.merges() == []
    assert RUN_LINK in capsys.readouterr().out


def test_a_real_failure_mixed_with_a_cancelled_job_is_not_retried(monkeypatch):
    checks = [_check(n, "fail" if n == "Tests" else "pass") for n in REQUIRED]
    jobs = [[_job("Test (a)", "cancelled"), _job("Test (b)", "failure")]]
    fake = _FakeGh(checks=[checks], jobs=jobs)
    assert _run(monkeypatch, fake) == 1
    assert fake.mutations == []


def test_cancelled_jobs_get_one_rerun_then_merge_when_green(monkeypatch):
    bad = [_check(n, "fail" if n == "Tests" else "pass") for n in REQUIRED]
    jobs = [[_job("Tests", "failure"), _job("Test (windows-latest 3/4)", "cancelled")]]
    fake = _FakeGh(checks=[bad, _green()], jobs=jobs)
    assert _run(monkeypatch, fake) == 0
    reruns = [c for c in fake.calls if c[:2] == ["run", "rerun"]]
    assert reruns == [["run", "rerun", "777", "--failed"]]
    assert len(fake.merges()) == 1


def test_a_second_infra_failure_is_not_rerun_again(monkeypatch):
    bad = [_check(n, "cancel" if n == "Tests" else "pass") for n in REQUIRED]
    jobs = [[_job("Test (x)", "timed_out")]]
    fake = _FakeGh(checks=[bad], jobs=jobs)
    assert _run(monkeypatch, fake) == 1
    assert len([c for c in fake.calls if c[:2] == ["run", "rerun"]]) == 1
    assert fake.merges() == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"state": "CLOSED"},
        {"state": "MERGED"},
        {"draft": True},
        {"base": "feature/parent"},
        {"mergeable": "CONFLICTING"},
    ],
)
def test_unmergeable_prs_are_refused_without_any_mutation(monkeypatch, kwargs):
    fake = _FakeGh(**kwargs)
    assert _run(monkeypatch, fake) == 1
    assert fake.mutations == []


def test_the_near_miss_to_the_refusals_merges(monkeypatch):
    # Same shapes as above but ordinary: open, not draft, main, mergeable.
    fake = _FakeGh(state="OPEN", draft=False, base="main", mergeable="UNKNOWN")
    assert _run(monkeypatch, fake) == 0
    assert len(fake.merges()) == 1


def test_stacked_children_are_retargeted_before_the_merge(monkeypatch):
    fake = _FakeGh(children=[8, 9])
    assert _run(monkeypatch, fake) == 0
    edits = [c for c in fake.calls if c[:2] == ["pr", "edit"]]
    assert edits == [["pr", "edit", "8", "--base", "main"], ["pr", "edit", "9", "--base", "main"]]
    merge_at = fake.calls.index(fake.merges()[0])
    assert all(fake.calls.index(e) < merge_at for e in edits)
    listing = next(c for c in fake.calls if c[:2] == ["pr", "list"])
    assert listing[listing.index("--base") + 1] == "feat"


def test_no_children_means_no_edit(monkeypatch):
    fake = _FakeGh()
    _run(monkeypatch, fake)
    assert [c for c in fake.calls if c[:2] == ["pr", "edit"]] == []


def test_dry_run_makes_no_mutating_call(monkeypatch, capsys):
    fake = _FakeGh(children=[8])
    assert _run(monkeypatch, fake, "--dry-run") == 0
    assert fake.mutations == []
    out = capsys.readouterr().out
    assert "would run: gh pr merge 5 --squash --admin --delete-branch" in out
    assert "--match-head-commit sha0" in out
    assert "would run: gh pr edit 8 --base main" in out


def test_dry_run_when_behind_says_so_and_stops(monkeypatch, capsys):
    fake = _FakeGh(behind=[4])
    assert _run(monkeypatch, fake, "--dry-run") == 0
    assert fake.mutations == []
    assert "would run: gh pr update-branch 5" in capsys.readouterr().out


def test_timeout_exits_2_without_merging(monkeypatch):
    pending = [_check(n, "pending") for n in REQUIRED]
    fake = _FakeGh(checks=[pending])
    assert _run(monkeypatch, fake, "--timeout", "2", "--poll", "30") == 2
    assert fake.mutations == []
