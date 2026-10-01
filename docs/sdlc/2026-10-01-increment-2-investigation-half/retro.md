# Retro: increment 2 investigation half

Change id: `2026-10-01-increment-2-investigation-half`

## What happened

AgentOps gained parallel investigation: a router chooses evidence sources, a merge step owns a 12-read budget and widens the time window, and a run ends in a proposed fix or a named escalation. Six reviewers found 0 high and 13 medium findings; two fix rounds closed 11 of them and 2 were deferred to increment 3. The owner approved G2 and G4, merged PR #3 (44f68f1), and nothing was deployed because the profile has no deploy command (confirmed).

## Clock

Numbers from the Clock block pasted in ship.md (confirmed); I did not re-query wh.js.

- agent 71.3 min (1 h 12 m), waiting on human 25 m, dead 1 m, wall 1 h 38 m
- tier 2 budget 90 min: within budget
- Largest charges: build 21.8 (wh-builder), design 16.8, review fixer 12.0 over two rounds, reviewers 7.7.

No over-budget lesson. The review fixer's 12 minutes are the two fix rounds below.

## What the pipeline caught

- Design: 19 decisions in the brief, all accepted at G2.
- Verify: 38/38 evals, 219 tests green at cc6ff2f. A human would likely not have found the reviewer items below.
- Review: silent-failure hunter found the escalate node ending with no hand-off after three bad citations; bug reviewer found router keyword substring misrouting; security reviewer found missing length caps and lookalike delimiter gaps.
- Re-check after round 1 caught regressions (router prefix, duplicate triage names) before the human saw the PR.

## What it missed

- Router prefix rule and triage name validation were wrong again after round 1 (two fix rounds, not over two). Should have been caught by negative test cases in the round-1 fix.
- Verifier went red on stale E29 text after the fix. The eval wording should have been updated with the fix.
- Two silent-failure mediums (source outage looks like no evidence; partial evidence unmarked) and two lows (lookalike delimiters, 16-read worst case) were accepted as increment 3 work (C6, C7). Documented risk, not a miss. They should be the first increment 3 tasks, as the G4 note says.
- No gate rejections.

## Lessons (instincts written in .workhorse/instincts/)

- last-resort-node-must-always-hand-off (new, 0.5)
- fix-round-needs-negative-cases (new, 0.5)
- update-eval-text-with-behaviour-change (new, 0.5)
- merge-is-release-route-to-owner (raised 0.5 to 0.7, seen in two changes)

## Proposed memory updates

CLAUDE.md "Mistakes to avoid" (promote only 0.7 or higher), a human applies:

```diff
 ## Mistakes to avoid
+- The profile has no deploy command; the owner's merge to main is the release. Stop at G4 and
+  do not dispatch a deploy-mode shipper (the permission classifier denies it).
```

Profile: no change proposed.

evals.md: add a permanent case for "source fails both attempts is reported as outage, not no_evidence" when increment 3 fixes the deferred medium.

Plugin skill: wh-conductor could note that a fix round that edits behaviour also updates the matching eval case text (client-independent). No plugin edit made.
