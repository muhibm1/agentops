---
id: scripted-fake-queue-per-schema
trigger: "when writing a scripted or fake model that returns canned outputs for several output schemas"
confidence: 0.5
domain: testing
source: 2026-10-01-increment-1-scaffold
scope: project
---

## Action
Keep one pending queue per output schema, and add a CLI-level test where an early node fails, so retries cannot consume another node's entries.

## Evidence
- reviews/wh-bug-reviewer.md: medium cli.py:109-118, triage CitationError reported as ValidationError; closed in db61178.
