# Release: Increment 3 act pause persist resume

Change id: `2026-10-01-increment-3-act-pause-persist-resume`
Version: not versioned (no release tag)
PR: #5 (merged to main)
Merged commit: `4cad9f4beb6e17ca6e8eab5b10e8a5e40818d826` (confirmed, supplied by the main session)

## Changelog

Added: act, pause, persist and resume behaviour for increment 3. See [ship.md](ship.md) for the full list.

## Deploy plan

| Environment | Command | Auto | Gate | Status |
|-------------|---------|------|------|--------|
| all | none defined in the profile | n/a | G5 | nothing to deploy |

The release was the merge to main. Nothing is deployed anywhere (confirmed: no environment has a deploy command).

Pre-deploy checks:
- [x] Verification green at the change branch head (confirmed, verification.status green in state)
- [x] Migrations reviewed for reversibility: not applicable, see ship.md
- [x] Feature flags or config changes: none known (believed, not verified beyond ship.md)

Config and secrets touched (names only, never values):
- None recorded.

## Rollback

Revert the merge commit through a PR: `git revert -m 1 4cad9f4beb6e17ca6e8eab5b10e8a5e40818d826` on a new branch, open a PR, merge it. Not rehearsed: nothing is deployed, so the revert only changes the repository.

## Runbook

Nothing runs in production, so there is nothing to watch.

| Signal | Where | Threshold | Action |
|--------|-------|-----------|--------|
| Main test or CI failure after merge | repository CI | any red | revert the merge via PR |

## Deploy record

| Environment | At (UTC) | By | Commit | Result |
|-------------|----------|----|--------|--------|
| none | n/a | n/a | 4cad9f4 | merged only; nothing deployed |

## Your decision

Approve G5 to close the change, confirming the merge was the whole release. Reject with notes if something should still be deployed.

| D1 | Treat the merge as the release | Approve and close | Hold open for a deploy step | No deploy command exists |
