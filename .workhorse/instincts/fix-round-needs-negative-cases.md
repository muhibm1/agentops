---
id: fix-round-needs-negative-cases
trigger: "when a fixer changes a matching or validation rule (keyword match, name check) after review"
confidence: 0.9
domain: testing
source: 2026-10-01-increment-2-investigation-half
scope: project
---

## Action
Add negative cases (names that must not match) alongside the positive ones in the same fix, and keep the re-check review step. Plan for a second round.

## Evidence
- conductor-log 06:38:14: re-check found "router prefix regression, triage duplicate names" introduced or exposed by round 1; round 2 fixed them (ship C5, C7).
- 2026-10-01-increment-3-act-pause-persist-resume: fix 32c6139/decc79b for crash-after-apply introduced a repeat-key regression (medium, found at re-check, fixed e5bd16f); the second re-check closed it.

## Evidence (added by 2026-10-02-increment-4-no-spend-measurement-harness)
- conductor-log 21:30:03: re-check round 1 found 2 new mediums in fixer code (CRLF ledger tear, torn audit write refusing a real approval); fixed b4a5a8b, 6e98ed7. Third occurrence.
