---
id: spec-promised-output-needs-test
trigger: "when a spec states what the CLI or logs will print"
confidence: 0.5
domain: tooling
source: 2026-10-01-increment-1-scaffold
scope: project
---

## Action
Add a test asserting each promised output line at build time; reviewers found the spec and code disagreeing.

## Evidence
- reviews/wh-adoption-reviewer.md: medium spec Observability vs cli.py report(); closed, tests/test_cli.py:262.
