# Retro: increment 4, no-spend measurement harness

Change id: `2026-10-02-increment-4-no-spend-measurement-harness` · Tier 3 · PR #10 merged as 82d201c

## What happened

The change added `agentops eval`, `agentops dataset`, a checksum-pinned held-out split, a `langgraph dev` entry, new runbook preconditions and a stricter execute step, and closed five low findings from increment 3. Seven reviewers and three re-check rounds ran; G4 was rejected once for a cross-thread approval replay (R2b), fixed in 4f10e42, then approved. The owner merged PR #10; nothing is deployed.

## Clock

Confirmed from `wh.js status --metrics`: agent 218.9 min, waiting 1.8 min, dead 90.8 min, budget 180 min (tier 3), within_budget false.

Over budget. The longest phase was build (waves 1 to 4, about 18:45 to 20:26 by conductor-log, 6 builder dispatches plus 2 fixers). The reasons in the log: wave 3 was one 39-minute builder dispatch (T8+T9) that surfaced four open issues; and review ran 7 reviewers plus 2 fixer rounds (the first fixer hit its turn limit at 21:15:10). Dead time of 90.8 min is mostly the design run that died headless (log 18:44:18, "resumed at design after dead headless run") and the fixer turn-limit stall. Without the dead time the run is 218.9 min against 180, still over, so the reason is scope: 96 files, 8695 added lines, 20 decisions at G2. The log explains it; it is not a log gap.

## What the pipeline caught

- Design: constraint auditor, 4 medium (D17-D20), resolved before G2.
- Build and verify: fixers closed E39/E47 fixtures, torn-ledger resume, looped label.
- Review: 1 high (second crash inside ledger repair bricks replay, tests reviewer), 15 medium, all fixed. A human reading 96 files would very likely not have found the ledger and audit torn-write cases.

## What it missed

1. R2b: an approval recorded on thread p1 admitted execute on a copy p1-x. All seven reviewers' first pass and the fix rounds left it as a low; the G4 reader rejected it. It should have been caught at design (spec said "approval must be in the audit log" without binding it to the thread) and rated by the claim it breaks.
2. Fix-round regressions again: re-check round 1 found CRLF ledger tear and torn audit write refusing a real approval, both in fixer code (c79d507, e8ba3b2).
3. Fixer turn limit and a dead design run: 90.8 dead minutes.
4. The owner did not read G2, G4 or G5 packets (standing instruction). Open lows unseen by the owner: outcome.py already_applied, python lows 4-5, silent executor traceback, security R1 (CORS simple POST), D25 Elastic-2.0 dev pins, D26.

## Instincts written or updated (`.workhorse/instincts/`)

- fix-round-needs-negative-cases: 0.7 to 0.9 (third occurrence).
- gate-approval-without-reading: 0.5 to 0.7.
- approval-must-bind-to-identity: new, 0.5.
- headless-run-dies-silently: new, 0.3.

## Proposed memory updates (not applied)

CLAUDE.md "Mistakes to avoid", add at top (instincts at 0.7 or more). The list is at 5 lines, under the cap of ten.

```diff
+- Rate a finding by the claim it breaks, not the exploit path: an approval or idempotency record must be bound to its thread and action, and a replay through a copied thread is a blocker (increment 4 R2b).
+- The owner approves gates without reading; list open lows and accepted risks in the approval note so they are not lost.
```

The existing "After a review fix to a matching or validation rule..." line may be updated to say "seen in three changes".

profile.yml: none.

evals.md: E49 and E50 already added by the fix. Proposed permanent case: a torn write inside the repair path itself (ledger and audit) stays in the suite as a named regression.

Plugin skill (wh-bug-reviewer): when a finding touches an approval, idempotency or authorization record, rate it by whether the record is bound to its subject, not by reachability. Describe only; not edited here.
