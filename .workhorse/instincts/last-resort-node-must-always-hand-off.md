---
id: last-resort-node-must-always-hand-off
trigger: "when writing a final fallback node (escalate, hand-off) that calls a model and validates its output"
confidence: 0.5
domain: workflow
source: 2026-10-01-increment-2-investigation-half
scope: project
---

## Action
Retry validation a bounded number of times, then build the hand-off in code from state. Never let the last node raise or end with no output.

## Evidence
- ship.md findings table: medium, ecc-silent-failure-hunter, `nodes/escalate.py:35-41`, "Three bad citations ended the run with no hand-off"; fixed in 058d641 (decision C9).
