---
id: headless-run-dies-silently
trigger: "when a long headless agent (designer, fixer) is dispatched on a tier 3 change"
confidence: 0.3
domain: workflow
source: 2026-10-02-increment-4-no-spend-measurement-harness
scope: project
---

## Action
Split the work into smaller dispatches, and have the conductor log a heartbeat so dead time is visible and attributable.

## Evidence
- conductor-log 18:44:18: "resumed at design after dead headless run" (design gap 17:17 to 18:44).
- conductor-log 21:15:10: fixer "hit turn limit mid-fix". Clock: dead_minutes 90.8.
