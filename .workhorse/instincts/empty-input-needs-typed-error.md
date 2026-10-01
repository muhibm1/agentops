---
id: empty-input-needs-typed-error
trigger: "when a pipeline step reads from sources that can legitimately return nothing"
confidence: 0.5
domain: workflow
source: 2026-10-01-increment-1-scaffold
scope: project
---

## Action
Raise a dedicated error naming the cause (service, window) and exclude it from retry policy, so the failure does not point at the model.

## Evidence
- reviews/ecc-silent-failure-hunter.md: medium evidence.py:109-127, closed with NoEvidenceError.
