"""`scripts/clean_branches.py`, which deletes branches for a living.

Every test here is really about one asymmetry: keeping a branch that
could have been deleted costs a line of clutter, and deleting one whose
work never landed costs the work.  So the interesting cases are all the
ways a branch can *look* finished without being finished, and each is
paired with the case it must not also reject.

The script cannot use ``git branch -d``'s own answer, because this repo
squash-merges: a squash rewrites the branch's commits into one new
commit, so the tip is never an ancestor of main and ``-d`` refuses every
merged branch alike.  What is left is ``-D``, which refuses nothing.
The PR state is the only ledger that can tell the two apart, which is
why these tests spend their time on it.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from scripts import clean_branches


class _FakeGit:
    """Stands in for `git` and `gh`, recording what was asked of it."""

    def __init__(
        self,
        refs: str,
        prs: dict,
        current: str = "main",
        worktrees: str | None = None,
        refuse: frozenset[str] = frozenset(),
    ) -> None:
        self._refs = refs
        self._prs = prs
        self._current = current
        # `git worktree list --porcelain`: by default only the main
        # checkout, standing on `current`, which is what a repo with no
        # extra worktrees reports.
        self._worktrees = (
            worktrees
            if worktrees is not None
            else f"worktree C:/repo\nHEAD abc1234\nbranch refs/heads/{current}\n"
        )
        self._refuse = refuse
        self.deleted: list[str] = []
        self.fetched = False

    def run(self, cmd, **kwargs):  # noqa: ANN001 - subprocess.run shim
        if cmd[0] == "git":
            return self._git(cmd)
        if cmd[0] == "gh":
            head = cmd[cmd.index("--head") + 1]
            return subprocess.CompletedProcess(
                cmd, 0, stdout=json.dumps(self._prs.get(head, [])), stderr=""
            )
        raise AssertionError(f"unexpected command: {cmd}")

    def _git(self, cmd):
        if cmd[1] == "fetch":
            self.fetched = True
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[1] == "for-each-ref":
            return subprocess.CompletedProcess(cmd, 0, stdout=self._refs, stderr="")
        if cmd[1] == "rev-parse" and "--abbrev-ref" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout=self._current, stderr="")
        if cmd[1] == "rev-parse":
            return subprocess.CompletedProcess(cmd, 0, stdout="abc1234", stderr="")
        if cmd[1] == "worktree":
            return subprocess.CompletedProcess(cmd, 0, stdout=self._worktrees, stderr="")
        if cmd[1] == "branch" and cmd[2] == "-D":
            if cmd[3] in self._refuse:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr=f"error: cannot delete branch '{cmd[3]}'\n"
                )
            self.deleted.append(cmd[3])
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        raise AssertionError(f"unexpected git command: {cmd}")


def _merged(number: int) -> list:
    return [{"number": number, "state": "MERGED", "mergedAt": "2026-08-17T00:28:22Z"}]


def _install(monkeypatch, fake: _FakeGit) -> None:
    monkeypatch.setattr(clean_branches.subprocess, "run", fake.run)


class TestWhichBranchesAreEvenConsidered:
    """Only a branch whose remote is gone is a candidate at all."""

    REFS = (
        "main\t\n"
        "feat/landed\t[gone]\n"
        "feat/still-open\t\n"
        "feat/never-pushed\t\n"
        "feat/behind\t[behind 3]\n"
    )

    def test_only_gone_upstreams_are_candidates(self, monkeypatch) -> None:
        fake = _FakeGit(self.REFS, {})
        _install(monkeypatch, fake)
        assert clean_branches._branches_with_gone_upstreams() == ["feat/landed"]

    def test_a_branch_with_no_upstream_is_never_touched(self, monkeypatch) -> None:
        """Never-pushed work is the last thing this should delete.

        It renders an empty track field, exactly like an up-to-date
        branch does, so a looser check ("no tracking info") would sweep
        up local-only work that exists nowhere else.
        """
        fake = _FakeGit(self.REFS, {})
        _install(monkeypatch, fake)
        assert "feat/never-pushed" not in clean_branches._branches_with_gone_upstreams()

    def test_a_branch_merely_behind_is_not_gone(self, monkeypatch) -> None:
        """The inverse, so matching on "there is tracking text" fails."""
        fake = _FakeGit(self.REFS, {})
        _install(monkeypatch, fake)
        assert "feat/behind" not in clean_branches._branches_with_gone_upstreams()


class TestOnlyAMergedPullRequestAuthorisesDeletion:
    """A gone remote is necessary and nowhere near sufficient."""

    @pytest.mark.parametrize(
        "prs,expected",
        [
            ([], False),
            ([{"number": 1, "state": "OPEN", "mergedAt": None}], False),
            ([{"number": 2, "state": "CLOSED", "mergedAt": None}], False),
            (_merged(3), True),
        ],
        ids=["no-pr", "open", "closed-unmerged", "merged"],
    )
    def test_the_pr_state_decides(self, monkeypatch, prs, expected) -> None:
        fake = _FakeGit("feat/x\t[gone]\n", {"feat/x": prs})
        _install(monkeypatch, fake)
        ok, reason = clean_branches._merged_pr("feat/x")
        assert ok is expected
        assert reason, "a decision without a reason reads as a silent skip"

    def test_a_closed_unmerged_branch_survives_the_whole_run(self, monkeypatch) -> None:
        """The failure that costs work, driven end to end.

        A PR closed without merging is the exact shape of abandoned-then-
        resurrected work, and its remote branch is deleted just like a
        merged one's.
        """
        fake = _FakeGit(
            "feat/landed\t[gone]\nfeat/rejected\t[gone]\n",
            {
                "feat/landed": _merged(10),
                "feat/rejected": [{"number": 11, "state": "CLOSED", "mergedAt": None}],
            },
        )
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 0
        assert fake.deleted == ["feat/landed"]

    def test_a_gh_failure_keeps_the_branch(self, monkeypatch) -> None:
        """Unable to ask is not permission to delete.

        `gh` missing or unauthenticated must fail closed, or running this
        on a machine without it would delete every candidate at once.
        """
        fake = _FakeGit("feat/x\t[gone]\n", {})

        def explode(cmd, **kwargs):  # noqa: ANN001
            if cmd[0] == "gh":
                raise FileNotFoundError("gh")
            return fake.run(cmd, **kwargs)

        monkeypatch.setattr(clean_branches.subprocess, "run", explode)
        assert clean_branches.main([]) == 0
        assert fake.deleted == []


class TestTheGuardsThatDoNotDependOnGitHub:
    def test_main_is_never_deleted(self, monkeypatch) -> None:
        """Even handed a merged PR for it."""
        fake = _FakeGit("main\t[gone]\n", {"main": _merged(1)}, current="feat/other")
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 0
        assert fake.deleted == []

    def test_the_checked_out_branch_is_never_deleted(self, monkeypatch) -> None:
        fake = _FakeGit("feat/here\t[gone]\n", {"feat/here": _merged(2)}, current="feat/here")
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 0
        assert fake.deleted == []


class TestBranchesHeldByAnotherWorktree:
    """A merged branch that another worktree has checked out is kept.

    Git refuses to delete it anyway, and that refusal used to escape as
    an uncaught ``CalledProcessError`` that ended the run part way
    through, so every branch after it was left behind.  Found on a real
    run: another session's worktree still had its merged branch checked
    out.
    """

    ELSEWHERE = (
        "worktree C:/repo\nHEAD abc1234\nbranch refs/heads/main\n\n"
        "worktree C:/repo/.claude/worktrees/other\nHEAD def5678\n"
        "branch refs/heads/feat/elsewhere\n"
    )

    def test_it_is_kept_and_the_worktree_named(self, monkeypatch, capsys) -> None:
        fake = _FakeGit(
            "feat/elsewhere\t[gone]\n",
            {"feat/elsewhere": _merged(9)},
            worktrees=self.ELSEWHERE,
        )
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 0
        assert fake.deleted == []
        assert "C:/repo/.claude/worktrees/other" in capsys.readouterr().out

    def test_the_same_branch_in_no_worktree_is_deleted(self, monkeypatch) -> None:
        """The inverse, so a guard that kept every branch cannot pass."""
        fake = _FakeGit("feat/elsewhere\t[gone]\n", {"feat/elsewhere": _merged(9)})
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 0
        assert fake.deleted == ["feat/elsewhere"]

    def test_a_detached_worktree_shields_nothing(self, monkeypatch) -> None:
        """A detached HEAD holds no branch, so it must not keep one."""
        detached = (
            "worktree C:/repo\nHEAD abc1234\nbranch refs/heads/main\n\n"
            "worktree C:/repo/.worktrees/review\nHEAD def5678\ndetached\n"
        )
        fake = _FakeGit("feat/landed\t[gone]\n", {"feat/landed": _merged(4)}, worktrees=detached)
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 0
        assert fake.deleted == ["feat/landed"]

    def test_the_listing_maps_each_branch_to_its_own_worktree(self, monkeypatch) -> None:
        fake = _FakeGit("", {}, worktrees=self.ELSEWHERE)
        _install(monkeypatch, fake)
        assert clean_branches._branches_in_worktrees() == {
            "main": "C:/repo",
            "feat/elsewhere": "C:/repo/.claude/worktrees/other",
        }


class TestOneRefusalDoesNotEndTheRun:
    """If git refuses a delete for any other reason, the run carries on."""

    REFS = "feat/first\t[gone]\nfeat/second\t[gone]\n"
    PRS = {"feat/first": _merged(1), "feat/second": _merged(2)}

    def test_the_branches_after_a_refusal_are_still_deleted(self, monkeypatch, capsys) -> None:
        fake = _FakeGit(self.REFS, self.PRS, refuse=frozenset({"feat/first"}))
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 1
        assert fake.deleted == ["feat/second"]
        assert "failed  feat/first" in capsys.readouterr().out

    def test_a_clean_run_still_reports_success(self, monkeypatch) -> None:
        """The inverse: the non-zero exit is the refusal's, not the run's."""
        fake = _FakeGit(self.REFS, self.PRS)
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 0
        assert fake.deleted == ["feat/first", "feat/second"]


class TestDryRun:
    def test_dry_run_deletes_nothing(self, monkeypatch) -> None:
        fake = _FakeGit("feat/landed\t[gone]\n", {"feat/landed": _merged(4)})
        _install(monkeypatch, fake)
        assert clean_branches.main(["--dry-run"]) == 0
        assert fake.deleted == []

    def test_the_same_run_without_dry_run_does_delete(self, monkeypatch) -> None:
        """The inverse, so a script that never deletes cannot pass."""
        fake = _FakeGit("feat/landed\t[gone]\n", {"feat/landed": _merged(4)})
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 0
        assert fake.deleted == ["feat/landed"]

    def test_no_fetch_skips_the_network(self, monkeypatch) -> None:
        fake = _FakeGit("feat/landed\t[gone]\n", {"feat/landed": _merged(4)})
        _install(monkeypatch, fake)
        clean_branches.main(["--no-fetch", "--dry-run"])
        assert fake.fetched is False

    def test_fetching_is_the_default(self, monkeypatch) -> None:
        """A stale `[gone]` list is the whole input, so it must refresh."""
        fake = _FakeGit("feat/landed\t[gone]\n", {"feat/landed": _merged(4)})
        _install(monkeypatch, fake)
        clean_branches.main(["--dry-run"])
        assert fake.fetched is True


FOREIGN_HOOK = """#!/bin/sh
echo mine
"""


class TestInstallingTheHook:
    """`--install-hook` writes into .git, which is not version controlled."""

    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        git_dir = tmp_path / ".git"
        (git_dir / "hooks").mkdir(parents=True)
        calls: list[list[str]] = []

        def run(cmd, **kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:2] == ["git", "rev-parse"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=str(git_dir), stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(clean_branches.subprocess, "run", run)
        return git_dir, calls

    def test_it_writes_the_hook_and_turns_on_pruning(self, repo) -> None:
        """Both halves, because the hook is useless without the other.

        The hook runs with ``--no-fetch``, so what makes its "upstream is
        gone" reading accurate is the pull having pruned, which is what
        ``fetch.prune`` buys.
        """
        git_dir, calls = repo
        assert clean_branches.main(["--install-hook"]) == 0
        hook = git_dir / "hooks" / "post-merge"
        assert hook.exists()
        assert "clean_branches.py --no-fetch" in hook.read_text(encoding="utf-8")
        assert ["git", "config", "fetch.prune", "true"] in calls

    def test_reinstalling_is_harmless(self, repo) -> None:
        git_dir, _ = repo
        assert clean_branches.main(["--install-hook"]) == 0
        assert clean_branches.main(["--install-hook"]) == 0
        assert (git_dir / "hooks" / "post-merge").exists()

    def test_someone_elses_hook_is_never_clobbered(self, repo) -> None:
        """The one destructive thing --install-hook could do.

        A post-merge hook the user wrote is theirs, and replacing it
        silently is the kind of thing noticed weeks later, by which point
        the original is gone.
        """
        git_dir, _ = repo
        hook = git_dir / "hooks" / "post-merge"
        hook.write_text(FOREIGN_HOOK, encoding="utf-8")
        assert clean_branches.main(["--install-hook"]) == 1
        assert hook.read_text(encoding="utf-8") == FOREIGN_HOOK

    def test_installing_deletes_nothing(self, repo) -> None:
        """It is a setup flag, so it must not also do the run."""
        _, calls = repo
        clean_branches.main(["--install-hook"])
        assert not any(c[:3] == ["git", "branch", "-D"] for c in calls)


class TestNothingToDo:
    def test_an_empty_candidate_list_is_not_an_error(self, monkeypatch) -> None:
        fake = _FakeGit("main\t\nfeat/active\t\n", {})
        _install(monkeypatch, fake)
        assert clean_branches.main([]) == 0
        assert fake.deleted == []
