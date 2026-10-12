---
id: merge-is-release-route-to-owner
trigger: "when the profile has no deploy command and merging to main is the release"
confidence: 0.7
domain: workflow
source: 2026-10-01-increment-1-scaffold
scope: project
---

## Action
Have the shipper stop at G4 with a note that the owner merges; do not dispatch a deploy-mode shipper, since the permission classifier denies it.

## Evidence
- conductor-log.md 2026-10-01T05:00:30Z: deploy step denied by permission classifier; owner merged PR 1 (219757e) at 05:04.

## Evidence (added by 2026-10-01-increment-2-investigation-half)
- conductor-log 06:56:50: "D1: deploy recorded as owner's merge, nothing deployed -> shipper deploy mode skipped". Second change in a row; the pattern held.
