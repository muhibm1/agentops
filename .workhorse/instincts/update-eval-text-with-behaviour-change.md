---
id: update-eval-text-with-behaviour-change
trigger: "when a fix changes behaviour that an eval case in evals.md describes"
confidence: 0.5
domain: workflow
source: 2026-10-01-increment-2-investigation-half
scope: project
---

## Action
Edit the eval case wording in the same commit as the fix, so the verifier does not go red on stale text.

## Evidence
- conductor-log 06:38:14: "verifier red on stale E29 text" after round 1; verification.md notes E29 now names the retry and hand-off tests.
