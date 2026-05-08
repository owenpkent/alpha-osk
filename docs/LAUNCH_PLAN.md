# Launch Plan — target 2026-05-28

Living checklist for the next Alpha-OSK release. **Three-week window**, today is 2026-05-07. Update status fields as you go; nothing here is fixed until shipped.

## What's launching

A feature release that bundles two workstreams completed in early May 2026:

1. **Redesigned Analytics dashboard** (single-section, four impact tiles, no composite quality score).
2. **Opt-in telemetry pipeline** (community impact counter, off by default, no content ever sent).

This is a meaningful enough scope to warrant a **minor version bump (1.1.0)** rather than another 1.0.x point release. Final call is yours; the version source of truth is `src/__version__.py`.

## State right now

| Workstream | Status | Notes |
|------------|--------|-------|
| Analytics dashboard | ✓ **shipped to main** | commit `2530be4` (initial), `8225178` (docs) |
| Telemetry client + bridge wiring | ✓ **shipped to main** | commit `0f737d1`. `DEFAULT_ENDPOINT=""` so it's inert |
| Settings → Privacy UI | ✓ **shipped to main** | toggle + two-step delete button, untested in dev |
| Telemetry docs | ✓ **shipped to main** | `docs/TELEMETRY.md`, `docs/PRIVACY.md` |
| CF Worker code | ✓ **scaffolded** | `backend/cf-worker/`, TypeScript + D1 schema + cron |
| CF Worker **deployed** | ✗ **not started** | blocker on telemetry going live |
| `DEFAULT_ENDPOINT` set | ✗ **blocked on deploy** | trivial change once URL is known |
| Dev-validate Privacy UI | ✗ **pending** | per `docs/TELEMETRY.md` § "Test in dev" |
| Version bump to 1.1.0 | ✗ **pending** | one-line edit to `src/__version__.py` |
| Release build (Windows) | ✗ **pending** | `python build/windows/build.py` from non-elevated shell, eToken plugged in |
| Release build (Linux) | ✗ **pending** | `python build/linux/build.py --appimage --fetch-appimagetool` |
| Tag + GitHub release | ✗ **pending** | `gh release create vX.Y.Z --repo okstudio1/alpha-osk-releases ...` |
| Public stats page | ✗ **deferred** | post-launch, after 4-8 weeks of real data |
| Federated learning | ✗ **deferred** | separate feature; not in this release |

## Three-week timeline

### Week 1 (2026-05-07 → 2026-05-14): activate telemetry backend

- [ ] Make/confirm Cloudflare account access.
- [ ] Run `npx wrangler d1 create alpha-osk-telemetry`. Paste `database_id` into `backend/cf-worker/wrangler.toml` (copy from `.example`).
- [ ] `npm install` then `npm run schema:remote`.
- [ ] `npm run deploy`. Note the printed worker URL.
- [ ] Smoke-test with the curl examples in `backend/cf-worker/README.md`. Confirm `POST /v1/submit` returns 204 and `GET /v1/aggregate` returns the row.
- [ ] Set `DEFAULT_ENDPOINT` in `src/telemetry.py` to the worker URL. Commit.
- [ ] Run the dev-validation checklist in `docs/TELEMETRY.md` § "3. Test in dev, then ship a release". Specifically: toggle persists, force-submit lands a row in D1, delete-button removes it, opt-out clears `anon_id`, opt-back-in generates a new one.

### Week 2 (2026-05-15 → 2026-05-21): release prep

- [ ] Bump `src/__version__.py` to `1.1.0`.
- [ ] In `CHANGELOG.md`, replace `## [Unreleased]` heading with `## [1.1.0] — 2026-05-28` (move existing entries under it). Add a fresh empty `## [Unreleased]` above.
- [ ] Run `python check.py --full` (the `--full` flag enables the coverage gate; matches CI exactly, ~3 min).
- [ ] `python build/windows/build.py` from a non-elevated shell with the eToken plugged in. (See `docs/WINDOWS.md` for the signing requirement.)
- [ ] Test the installer in `release/` on a clean account or VM. Specifically: install, launch OSK, open Settings → Privacy, toggle on, restart, confirm toggle persisted.
- [ ] `python build/linux/build.py --appimage --fetch-appimagetool` if the Linux build is also shipping this cycle.

### Week 3 (2026-05-22 → 2026-05-28): ship

- [ ] `git tag v1.1.0 && git push origin main && git push origin v1.1.0`.
- [ ] `gh release create v1.1.0 release/Alpha-OSK-Setup-1.1.0.exe --repo okstudio1/alpha-osk-releases --title "v1.1.0 — Community impact + cleaner analytics" --notes-file release-notes-1.1.0.md`. Asset filename **must** match `Alpha-OSK-Setup-1.1.0.exe` exactly (the auto-updater rejects anything else).
- [ ] Verify auto-update path: install 1.0.16 on a test machine, wait for or force the update check, confirm 1.1.0 lands, confirm Settings → Privacy section is present.
- [ ] Watch `npx wrangler tail` for the first 24 hours to catch malformed payloads or unexpected spikes.
- [ ] (Optional) Announcement / blog post / social.

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Worker not deployed → entire telemetry feature dark on launch | Front-load deploy in Week 1; everything else gates on it |
| First production submissions are also the first integration test | Dev-validation checklist in Week 1; smoke-test live worker before flipping `DEFAULT_ENDPOINT` |
| Privacy UI was unit-tested but never seen in dev | Step in Week 1 explicitly opens Settings → Privacy |
| Auto-update flow has churn (1.0.16 fix is recent) | End-to-end install-old → update → confirm-new in Week 3, before announcement |
| eToken not present at build time → unsigned binary | `docs/WINDOWS.md` "Code Signing" section covers the most common trap (must be non-elevated shell) |
| Misconfigured `DEFAULT_ENDPOINT` (staging URL, typo) routes opt-in users to wrong DB | `docs/WINDOWS.md` step 2a is the gate; verify before build |
| User opts in, sees "0% picked the first suggestion" because counter is fresh | Already fixed: that subtext was removed from the Predictions Used tile after Owen flagged it |

## Decisions deferred

- **Public stats page surface.** Wait until the aggregate has meaningful numbers (4-8 weeks of real data). Then design the static page that reads `/v1/aggregate`.
- **Endpoint domain.** Plan is the workers.dev subdomain for v1; could move to `alpha-osk.com/telemetry` later if the project gets a domain. Migration is a Cloudflare custom-route addition, not a code change (the client just hits whatever `DEFAULT_ENDPOINT` says).
- **Backfilling telemetry to old releases.** Recommendation: **don't**. Telemetry ships with 1.1.0 onward; 1.0.x stays unchanged. Reason: shipping a "1.0.16-with-telemetry" point release through the auto-updater could feel like a privacy bait-and-switch even with the toggle off, since the user installed a version that didn't have the feature. Clean line at 1.1.0.
- **Whether to prompt users about telemetry on first launch of 1.1.0.** Currently no prompt — toggle just sits in Settings → Privacy. Pro: not pushy on an accessibility tool. Con: lower opt-in rate. Hold this decision for after launch unless adoption is much lower than expected.

## Out of scope for this launch

- Federated learning (own design doc, much bigger surface).
- Public stats page.
- Anything in the Whitepaper §8 known gaps that isn't already in flight.

## Task tracker

`docs/launch_tasks.csv` is the row-level tracker (40 tasks, ID-keyed, with dependency arrows). Open it in Excel / Numbers / Sheets / a CSV viewer to filter by status or phase. The markdown checklist above is the human-readable narrative; the CSV is the structured shadow.

## Next action (today)

If launching 2026-05-28, the deploy step needs to land in Week 1 to leave room for build/test/release. Concretely: open `backend/cf-worker/README.md` and walk through the "One-time setup" section. ~30 min of work.
