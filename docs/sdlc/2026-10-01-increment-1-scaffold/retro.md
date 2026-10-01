# Retro: 2026-10-01-increment-1-scaffold

## What happened

The change took AgentOps from design documents to a runnable scaffold (CLI, graph, simulator, scripted model, CI) at tier 2. G2 and G4 were each approved first time with no rejections. Review found 5 mediums (4 fixed in db61178 along with 4 lows, plus one more medium closed on re-check) and the deploy step stalled because the permission classifier denied the shipper; the owner merged PR #1 himself (merge 45e026d).

## Clock

Agents 1 h 03 m, waiting on you 12 m, dead 1 m, wall 1 h 15 m (from ship.md, confirmed). Budget 1 h 30 m (tier 2). Within budget. Note: the conductor-log shows a gap of about 40 minutes between G4 presented (04:21) and resume (05:00) and a further gap after the deploy block; ship.md's clock was pasted before those, so wall time after G4 is not in it (believed, not verified).

## What the pipeline caught

- Design: auditor pass added D16 and D17 before G2.
- Verify: green at 43da834, 38/38 evals; the 4 profile checks confirmed.
- Review (6 reviewers, 0 critical, 0 high, 5 medium): terminal injection through model text (security), triage CitationError masked as ValidationError (bug), silent empty evidence (silent-failure), spec/code mismatch on action counts and README gap (adoption). A human reading the diff would plausibly have missed the first two.
- Re-check closed all mediums; fix added 10 tests (66 to 76).

## What it missed

- Deploy dispatch was blocked by the permission classifier (log 05:00:30Z). Where to catch: the profile has no deploy command, so the conductor should have skipped the shipper's deploy mode and routed straight to the owner.
- R20 (green Actions run) was verified by no agent; the owner observed CI run 36814695863 (76 passed) before G4. Acceptable, recorded as D-S2.
- Open lows deferred: error-path escaping, dict key collision, retry not logged, a docs sentence, a docstring.
- No gate rejections and no fix loop over two iterations.

## Lessons (instincts in .workhorse/instincts/)

All at confidence 0.5 (seen once): scripted-fake-queue-per-schema, escape-model-text-in-terminal, empty-input-needs-typed-error, spec-promised-output-needs-test, merge-is-release-route-to-owner. None reaches 0.7, so no CLAUDE.md promotion.

## Proposed memory updates

CLAUDE.md: none yet (all below 0.7).

Profile (`.workhorse/profile.yml`), proposal: add a note that merge of the PR is the release and the owner performs it; leave the deploy command empty.

evals.md, proposal: add E39 (CLI, triage cites an unknown id, stderr contains CitationError) if not already present, and an error-path escaping case for the open low.

Plugin skill, proposal for the conductor skill: when the profile defines no deploy command, skip the shipper deploy dispatch and ask the owner to merge, then finish done and retro.
