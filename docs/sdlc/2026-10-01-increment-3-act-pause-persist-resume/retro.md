# Retro: 2026-10-01-increment-3-act-pause-persist-resume

## What happened

Increment 3 added the act half of AgentOps: approved fix from an allowed-action runbook, pause for an operator, one-time apply with a SQLite checkpoint, verify, postmortem, audit trail. Design (24 decisions), 8 build tasks in 5 waves, and a green verify (64/64 evals) came first. Seven reviewers then found 2 high and 10 medium issues; two fixer rounds and three re-checks closed all of them, and PR 5 was merged at G4/G5 on the owner's standing instruction without a human reading the packets.

## Clock

From `wh.js digest --all` for this id (confirmed): agent 211.8 min, waiting 1.6, dead 2.7, wall 216.2, budget 180 (tier 3), within_budget false. The conductor reported 3h32m agent time; the digest figure is 3h32m (211.8). Over by about 32 min.

Longest phase: review (reviewers 85 min, fixer 24 min). From conductor-log: reviewers 08:46 to 09:06 first pass, then fix, then a re-check that ran 09:07 to 10:24 (77 min, one dispatch round of seven reviewers), then a second fixer round (3 commits) and a third re-check at 10:30. The reason is in the log: the first fixer round (crash-after-apply) introduced a new medium (repeat of an applied key looped verify), which forced a second fix round and re-check. Build took 58 min across 5 waves with no fix loops; scope (24 reqs, 64 evals, 8981 lines) was large for a 180 min tier-3 budget. Cause of overrun is explained by the log, so no finding about the log.

## What the pipeline caught

- Design: constraint auditor (4 medium) and eval designer; mediums resolved as D21-D24 before G2.
- Verify: green; no failures.
- Review: 2 high (crash after apply recorded as applied=False; failed metrics read declared cleared), 10 medium (target never checked in preconditions, fork reused ledger, approve exit 0 on refusal, postmortem named one action, missing tests, no operator docs), 14 low (9 fixed). A human skimming the diff would likely have missed the high ones, which need failure-path reasoning.

## What it missed

- Verify and evals were green at f2dfd3a while both highs existed: eval cases covered the happy path and declared failures, not crash-between-apply-and-record or failed-read. Should have been caught at eval design.
- Fix round 1 introduced a regression (repeat applied key, medium) caught only at re-check. Should have been caught by negative cases in the fix.
- G2, G4, G5 were approved unread. G4 left D25 (AgentShield scan, refused by harness) undone and 5 lows open, including applied=False when `source.apply` raises.
- Evals ran against f2dfd3a, not the final head 8465aa2 (ship.md says so); verification.md still says "partial (evals not yet run)" in its header though the table shows 64/64 met. Stale status text.

## Instincts written (`.workhorse/instincts/`)

- `fix-round-needs-negative-cases`: raised 0.5 to 0.7, evidence appended.
- `unknown-effect-is-not-false` (0.5, new).
- `failed-read-must-not-mean-healthy` (0.5, new).
- `gate-approval-without-reading` (0.5, new).

## Proposed memory updates

CLAUDE.md "Mistakes to avoid" (only the instinct at 0.7), add as newest line:

```diff
+- After a review fix to a matching or validation rule, add negative cases in the same commit and expect a second round; the increment 3 crash-after-apply fix introduced a repeat-key regression found only at re-check.
```

Profile: no change. Evals (`evals.md` of this change, or increment 4): add permanent cases:

```diff
+ | F-new1 | Failure | Given a crash after apply and before the ledger write, When resumed, Then verify and rca treat the action as possibly applied | executor.py:185 |
+ | F-new2 | Failure | Given the metrics read fails during verify, When verify runs, Then outcome is escalate, never "cleared" | verify.py:97 |
+ | E-new3 | Edge | Given a plain repeat of an applied key, When executed, Then escalate, not loop to budget_exhausted | executor.py:184 |
```

Plugin skill suggestion (client-independent, not edited by me): in `wh-evals`, add to "Designing evals" a required Failure case for every side-effecting call: "crash between effect and record" and "read fails during verification". Also suggest the budget estimator warn at design when a tier-3 change has more than 20 requirements.

Open for increment 4 (from ship.md): audit wording for applied=False on raise (D26), AgentShield scan (D25), partial ledger line recovery, fixed 30 min verify window, broad job-name patterns.
