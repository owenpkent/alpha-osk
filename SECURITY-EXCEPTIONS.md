# Security exceptions

Findings from automated security audits that have been reviewed and
knowingly skipped. Each entry records the finding, the reason it
isn't load-bearing for this project's threat model, and the trigger
that would warrant revisiting.

Triage agents should treat entries here as "known skipped, do not
re-flag" rather than as a free pass to ignore the underlying class of
finding.

## scorecard/Branch-Protection = 0/10 (resolved 2026-09-02)

- **Finding:** `main` had no GitHub branch-protection rule.
- **Decided:** 2026-05-16. Skip. **Revisited:** 2026-09-02, and the skip no
  longer applies: the repository is public and the rule now exists.
- **What is configured now:** `main` requires the Lint, Type Check, Tests
  and OSV Scanner checks to pass and refuses force-pushes and branch
  deletion. It does not require a review (there is still one maintainer),
  does not apply to administrators, and does not require a branch to be
  up to date before merging. Those three gaps are deliberate, for the
  motor-cost reason the original skip gave, and they are the residual
  this entry now records: Scorecard will keep scoring the rule below
  10/10 on the review requirement.
- **Revisit when:** A second maintainer is added (then require a review),
  or a bad merge lands that an up-to-date requirement would have caught.
- **Original reason (2026-05-16):** Solo-dev private repo. The protection adds a forced PR
  workflow on every change. Concrete cost (extra steps per fix) is
  high for an accessibility tool maintained by a user with motor
  constraints; concrete benefit is small. `git log` is already the
  audit trail. Force-push protection guards a hypothetical
  account-compromise scenario the attacker would route around anyway
  (a compromised account can land a malicious commit through the new
  PR flow just as easily as via direct push).
- **Original revisit trigger:** A second contributor is added to the
  repo, or the repo goes public. The second fired.

## scorecard/Signed-Releases = 0/10

- **Finding:** Releases v1.0.0 through v1.0.4 are not signed with
  cosign and carry no SLSA build provenance.
- **Decided:** 2026-05-16. Defer.
- **Reason:** Windows installers are already EV-signed via the
  SafeNet eToken, which covers the end-user trust path (Windows
  SmartScreen, antivirus heuristics, "verified publisher" on the
  installer dialog). Cosign + SLSA provenance is a separate
  supply-chain audit trail aimed at third-party auditors verifying
  "the bytes came from this CI workflow at this commit." That
  audience doesn't exist for an accessibility tool with a small user
  base. Adding a cosign workflow introduces another moving piece in
  the release pipeline (keys, OIDC, CI maintenance) for a benefit
  that isn't load-bearing today.
- **Revisit when:** Publishing to a distribution channel that
  requires provenance (Chocolatey, winget, an enterprise
  marketplace), or a downstream consumer asks how to verify the
  binary independently of the EV cert.

## socket/License score 70 -- pypi/hypothesis (MPL-2.0)

- **Finding:** Socket Security flags `hypothesis` with a License
  score of 70/100 (amber) on PR #14. Its other four scores are
  98-100. The score reflects the license class, not a defect:
  hypothesis is MPL-2.0, weak copyleft, and Socket ranks that below
  permissive MIT/BSD/Apache.
- **Decided:** 2026-08-07. Skip.
- **Reason:** MPL-2.0 obligations are file-level and attach to
  *distributing* covered files. Alpha-OSK does neither thing that
  would trigger them: hypothesis is unmodified, declared only in
  `requirements-dev.txt`, imported by nothing under `src/`, and now
  named in both PyInstaller `excludes` lists so it cannot reach a
  shipped bundle even by accident. MPL-2.0 also explicitly permits
  combination with a differently-licensed Larger Work, so the MIT
  license on Alpha-OSK itself is unaffected. Both Socket checks pass;
  this is an informational score, not a gate.
- **Revisit when:** hypothesis (or another MPL/copyleft package)
  moves into `requirements.txt`, or any shipped module imports it --
  at which point the installer would be distributing MPL code and the
  notice/source-availability obligations become real.

## How to add an exception

Document the finding (rule ID + score), the date the decision was
made, the threat-model reasoning, and the concrete trigger that
would warrant revisiting. "Revisit when" should be observable, not
aspirational: "when X happens" not "eventually."
