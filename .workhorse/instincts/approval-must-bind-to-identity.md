---
id: approval-must-bind-to-identity
trigger: "when code accepts a recorded approval, idempotency key or marker as proof that an action may run"
confidence: 0.5
domain: security
source: 2026-10-02-increment-4-no-spend-measurement-harness
scope: project
---

## Action
Check that the record is bound to the exact subject (thread id, incident id, action key) and not just present. Add an adversarial test through the real graph that copies state to a new thread and expects refusal, plus a golden test that a legitimate fork still works.

## Evidence
- approvals.md G4 rejection: bug-reviewer R2b, executor.py:112-130, an approval from thread p1 admitted execute on copy p1-x. Fixed 4f10e42; E49 (adversarial) and E50 (fork golden).
- Rated below high by the reviewer; the owner side raised it to a blocker. Severity for core-claim breaks should be set by the claim, not the exploit path.
