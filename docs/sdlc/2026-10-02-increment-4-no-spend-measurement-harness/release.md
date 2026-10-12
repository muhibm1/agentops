# Release: increment 4 no-spend measurement harness

Change id: `2026-10-02-increment-4-no-spend-measurement-harness`
Version: none (no version tag; the release is the merge to main)
PR: #10 (merged by the main session on the owner's standing instruction)
Merged commit: `82d201c1724a634e3dccd50da8fd8971436bf9e6` (includes the G4 approval commit 6376c6a)

## Changelog

Added: the no-spend measurement harness for increment 4. See [ship.md](ship.md) for the full list of changes and reviewer findings.

## Deploy plan

| Environment | Command | Auto | Gate | Status |
|-------------|---------|------|------|--------|
| all | none defined in the profile | n/a | G5 | nothing deployed (confirmed: no environment has a deploy command) |

Pre-deploy checks:
- [x] Verification green before merge (confirmed: state shows verification green at 2026-10-02T21:56:03Z)
- [x] Migrations: none (believed, not verified at this step)
- [x] Feature flags or config changes: none known

Config and secrets touched (names only): none.

## Rollback

The release was the merge. Rollback is a revert of merge commit 82d201c1724a634e3dccd50da8fd8971436bf9e6 through a new pull request (`git revert -m 1 82d201c`, then PR to main; only the owner pushes to main). Not rehearsed: no environment exists to rehearse in, and the change is a local measurement harness.

## Runbook

Nothing runs in production, so nothing is watched after release. Check that CI on main is green after the merge.

| Signal | Where | Threshold | Action |
|--------|-------|-----------|--------|
| CI on main | GitHub Actions | any red | revert the merge through a PR |

## Deploy record

| Environment | At (UTC) | By | Commit | Result |
|-------------|----------|----|--------|--------|
| none | n/a | n/a | 82d201c | merged only; nothing deployed |

## Your decision

Approve G5 to record the release as complete, or reject with notes.

/workhorse:approve G5
/workhorse:approve G5 --reject "notes"
