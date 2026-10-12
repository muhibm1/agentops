---
id: unknown-effect-is-not-false
trigger: "when code records the outcome of a side-effecting call that can fail or crash part way (apply, write, send)"
confidence: 0.5
domain: data
source: 2026-10-01-increment-3-act-pause-persist-resume
scope: project
---

## Action
Model "effect unknown" as its own state, never as applied=False. Downstream verify, rca and report steps must treat unknown as possibly changed. Add a crash-between-apply-and-record test at design time.

## Evidence
- wh-bug-reviewer high, executor.py:185 and verify.py:123: crash after apply recorded as applied=False (fixed c2b4139, 285d25e).
- ecc-silent-failure-hunter low, executor.py:222: audit still says applied=False when source.apply raises (open; ship.md D26, first item of increment 4).
