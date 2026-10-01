---
id: merge-is-release-route-to-owner
trigger: "when the profile has no deploy command and merging to main is the release"
confidence: 0.5
domain: workflow
source: 2026-10-01-increment-1-scaffold
scope: project
---

## Action
Have the shipper stop at G4 with a note that the owner merges; do not dispatch a deploy-mode shipper, since the permission classifier denies it.

## Evidence
- conductor-log.md 2026-10-01T05:00:30Z: deploy step denied by permission classifier; owner merged PR 1 (45e026d) at 05:04.
