# Ship: increment 2 investigation half

Change id: `2026-10-01-increment-2-investigation-half` · Tier 2 · Branch `wh/2026-10-01-increment-2-investigation-half` at code `ad3be8e` (docs-only head `22d1842`, plus this file) · PR https://github.com/muhibm1/agentops/pull/3
Prepared 2026-10-01T06:55Z UTC · Design approved: see [approvals.md](./approvals.md)

This is the Ship document. Approving it records G4. Nothing is deployed by an agent: the release is you merging the PR to main.

## The short version

AgentOps now investigates an incident in parallel: a router picks which evidence sources to read, a merge step owns a 12-read budget and loops with a wider time window, and the run ends either with a proposed fix awaiting approval or with an escalation that names its reason. It changes the behaviour of the CLI (no evidence now escalates with exit 0 instead of failing with exit 2), adds 6 scenarios (8 total), and raises the CI test floor from 30 to 80 on a pinned ubuntu-24.04 runner. You are deciding whether to accept the code and the two deferred medium findings (source outage reporting, partial-evidence routing) as increment 3 work.

## What changed

In plain words: before, one run read one fixed set of evidence and failed if a source died. Now each source read retries once inside the node, a dead source no longer sinks the others, and when the model cannot pick a single cause (top two within 0.15) or nothing was collected, the graph escalates to a human with a reason set by code, not by the model. Everything below is `confirmed` by the diff `git diff main...HEAD` (49 files, +4068 -253).

1. `.github/workflows/ci.yml`, `.workhorse/profile.yml` (sensitive paths, approved as brief D11, D12, D19): runner pinned to ubuntu-24.04, floor 80, release note and stale "(believed)" labels corrected.
2. `src/agentops/state.py`: new escalation fields, re-scored hypotheses, length caps, distinct-name check on triage. First of the code because every node reads it.
3. `src/agentops/nodes/rca.py`, `escalate.py`: the model's proposal is checked in code (same causes as triage, top cause chosen, ambiguity rule); escalate clears the proposal and falls back to a code-built summary.
4. `src/agentops/nodes/router.py`, `merge.py`, `evidence.py`: scoring router, budget and loop owner, parallel source reads with one in-node retry.
5. `src/agentops/graph.py`, `nodes/__init__.py`, `cli.py`, `fakes.py`, `sources/scenario.py`: wiring, delimiter neutralising (NFKC first), terminal output escaping, scripted model per schema.
6. `scenarios/*.json` (6 new, 2 edited): include the prompt-injection scenario (log line asking to drop a table, treated as data).
7. `tests/` (11 files): 239 passing, up from 76. No test was edited to pass, except the deliberate change in Decisions below.
8. `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `docs/ci.md`, `docs/sdlc/codebase-map.md`, 3 ADRs under [adr/](./adr/): documents match the new behaviour.

## Proof

| Check | Command | Exit code | Output | Status |
|-------|---------|-----------|--------|--------|
| typecheck | `.venv/Scripts/python.exe -m pyright` | 0 | [typecheck.log](./verify-logs/typecheck.log) | confirmed |
| lint | `.venv/Scripts/python.exe -m ruff check .` | 0 | [lint.log](./verify-logs/lint.log) | confirmed |
| test | `.venv/Scripts/python.exe -m pytest -q` (239 passed, 1 skipped) | 0 | [test.log](./verify-logs/test.log) | confirmed |
| security_audit | `.venv/Scripts/python.exe -m pip_audit -r requirements.txt` (0 vulnerabilities) | 0 | [security_audit.log](./verify-logs/security_audit.log) | confirmed |
| evals | `pytest -v` over 12 eval files (216 passed, 1 skipped) | 0 | [evals.log](./verify-logs/evals.log) | confirmed |
| CI on GitHub | Actions run 36826541286 (push) and 36826544455 (pull_request), ubuntu-24.04, MIN_TESTS 80, on `22d1842` | success | https://github.com/muhibm1/agentops/actions/runs/36826541286 | confirmed |
| build, e2e, screenshot | no command in the profile | n/a | n/a | not run: no check defined |
| install | skipped by `wh.js verify` (venv already built) | n/a | n/a | not run |

Evals: golden 18/18, edge 8/8, failure 9/9, adversarial 3/3 (targets 100%; all met, confirmed). The one skip is the E12 case the eval text excludes. N1 17 s against 60 s, N2 239 tests against 80.
This is the first real Actions run with the floor at 80 on the pinned runner; it passed (confirmed). The CI run covers `22d1842`, which differs from the code at `ad3be8e` only by docs; the commit holding this file triggers one more run (believed, not verified at writing time).
Known pre-existing failures: none (`wh.js known-failure list` reported none).

## What the reviewers found

Six reviewers: 0 critical, 0 high, 13 distinct medium (11 original, 2 raised by the bug re-check; two reviewers share one row), 11 low. Medium: 11 fixed, 2 accepted. Low: 6 fixed, 5 open. Reports and re-checks: [reviews/](./reviews/).

| Severity | Reviewer | File:line | Finding | Resolution |
|----------|----------|-----------|---------|------------|
| medium | ecc-silent-failure-hunter | `nodes/evidence.py`, `nodes/merge.py` | A source that fails both attempts looks like "no evidence"; no `source_outage` reason | accepted: deferred, conductor D6, increment 3; see [ecc-silent-failure-hunter.md](./reviews/ecc-silent-failure-hunter.md) |
| medium | ecc-silent-failure-hunter | `nodes/rca.py` (`after_rca`) | A source down in both rounds is dropped; rca runs on partial evidence with the gap unmarked | accepted: deferred, conductor D6, increment 3; see [ecc-silent-failure-hunter.md](./reviews/ecc-silent-failure-hunter.md) |
| medium | wh-bug (re-check) | `nodes/router.py:130` | Keywords after a prefix (`misconfigured_*`, `*_redeploy`) lost their source | fixed in `ad3be8e` |
| medium | wh-bug (re-check) | `nodes/rca.py:100` | Duplicate triage names made a correct re-score fail | fixed in `ad3be8e` |
| medium | wh-bug, ecc-python | `nodes/escalate.py:47` | Escalation reason re-derived after rca, could say `budget_exhausted` for an ambiguous result | fixed in `5aca3b0` |
| medium | wh-bug | `nodes/router.py:118` | Raw substring keyword match misrouted `conversion_rate_drop` and others | fixed in `2eb69eb` (spec R2 wording updated in `ad3be8e`) |
| medium | ecc-silent-failure-hunter | `nodes/escalate.py:35-41` | Three bad citations ended the run with no hand-off | fixed in `058d641`, summary cap in `c791a21` |
| medium | wh-security | `tests/test_scenario_runs.py` | Untrusted-data rule not asserted per prompt; fence test could not fail | fixed in `4e1063f` (mutation-checked by the reviewer) |
| medium | wh-adoption | `graph.py:31`, `evidence.py:5` | "ADR 0001" ambiguous between increments | fixed in `e1d996a` |
| medium | wh-adoption | `CONTRIBUTING.md:66` | Scenario guide omitted the `escalate` answer | fixed in `e1d996a` |
| medium | wh-conformance | `cli.py:137` | Failed-read count had no non-zero test | fixed in `18546cb` |
| medium | wh-conformance | `.workhorse/profile.yml` | R15 release note not asserted by a test | fixed in `18546cb` |
| medium | wh-conformance | `README.md`, `CLAUDE.md` | R16 stub-model sentence not asserted | fixed in `18546cb` |
| low | wh-security | `nodes/__init__.py:58` | Lookalike delimiters outside NFKC (U+2039, U+3008, zero-width split) pass; scripted model only today | open: deferred to increment 3 (conductor D7), nonce-per-prompt fix named in report |
| low | wh-security | `nodes/router.py:91` | Up to 16 reads against a 12 budget (nominal cost checked, attempts charged) | open: deferred to increment 3 (conductor D7, D8) |
| low | wh-bug | `nodes/escalate.py:63` | Rejected escalate attempt recorded with `latency_ms` 0.0 | open: left as a note (conductor D8); record only, nothing sums latency |
| low | ecc-silent-failure-hunter | `evidence.py:124` | Failed read keeps the exception type name only, no message | open: not touched by any fix |
| low | wh-adoption | `docs/ci.md` | Floor 80 against 239 real tests; a mass deletion would still pass | open: raising the floor touches `ci.yml` (sensitive), your call |
| low | wh-security (re-check) | `nodes/escalate.py:52-66` | Rejected escalate attempts left no Action record | fixed in `ad3be8e` |
| low | wh-security | `state.py` | No length cap on hypothesis name and summary | fixed in `c791a21` |
| low | wh-security, ecc-silent-failure-hunter | `nodes/escalate.py` | Reason recomputed, not recorded | fixed in `5aca3b0` |
| low | wh-security | `nodes/__init__.py:24` | Fullwidth `<<<end>>>` not neutralised | fixed in `4e1063f` (residual is the open row above) |
| low | ecc-python, wh-bug | `nodes/rca.py:91` | Repeated name in the re-score passed the set check | fixed in `058d641` |

Spec conformance: 18 of 18 requirements traced to code and test (R1 to R16, N1, N2); the three test gaps the reviewer found are closed in `18546cb` (confirmed by grep, the conformance reviewer did not re-run). Adoption score: 4 (after re-check, no blockers).
Not run by the security reviewer: AgentShield (`npx` denied by the permission classifier; the CLAUDE.md diff is descriptive text, read by hand). `pip_audit` was not run by that reviewer; the `security_audit` check above ran it and passed (confirmed).

## Decisions

The 19 brief decisions (D1 to D19) were accepted at G2 and are in [brief.md](./brief.md). Decisions taken during build and review are numbered C1 to C8 here (conductor log D1 to D8).

| # | Decision | Recommendation | Alternative | Why the recommendation |
|---|----------|----------------|-------------|------------------------|
| C1 | Escalation reason names | Spec short names `budget_exhausted`, `ambiguous`, `no_evidence` | Keep the longer names in state | Spec is the contract; one literal fixed in `state.py` |
| C2 | Metric names | Scenario files win (`dependency_p99_ms`) | Rename scenarios | Files are the data the tests read |
| C3 | Scenario 1 label | Must equal the top hypothesis name | Loosen the check | Ground truth must match what the graph names |
| C4 | Old CLI escape test | Replaced by the new escape and report tests | Keep both | Old test asserted behaviour brief D16 redefines |
| C5 | Router keyword match | Match at the start of a `_` token (later also after `mis`, `re`, `un`); spec R2 wording updated | Raw substring | Substring misrouted three real-looking names |
| C6 | Source outage reason and partial-evidence routing | Defer to increment 3, documented risk, not fixed here | Fix now | Needs a design decision on a new reason and rca prompt; increment 3 owns the action layer |
| C7 | Second fix round | Fix the two new mediums; defer lookalike characters and the 16-read worst case | Fix all | Neither is reachable while only the scripted model runs |
| C8 | Latency on a rejected escalate attempt | Leave as a note | Time the attempt | Record only, nothing reads it |
| C9 | Escalate test behaviour change (fixer round 1) | An escalate test that expected a raise now expects three retries, then the code-built fallback summary | Keep raising | Reviewer medium: the last-resort node must always hand off. This changes a test deliberately; it is not a test edited to pass |

## Deploy and undo

| Environment | Command | Automatic on approval | Rollback |
|-------------|---------|-----------------------|----------|
| dev | none in the profile | not applicable: no deploy command | not applicable |
| staging | none in the profile | not applicable: no deploy command | not applicable |
| prod | none in the profile; the release is the owner merging the PR to main | no (`auto: false`); the owner performs it | `git revert -m 1 <merge commit>` on a new branch, merged through a PR |

Rollback rehearsed (tier 2+): not rehearsed: no environment has a deploy command and nothing in v1 is deployed, so there is nothing to deploy, roll back and deploy again. The revert is a plain git operation on the merge commit; it was not run (believed, not verified).
Config and secrets touched (names only, never values): none. CI reads no secret and the tests call no live model (confirmed by the security reviewer's diff grep).

## Clock

```
Clock: agents 1 h 12 m of 1 h 30 m budget · waiting on you 25 m · dead 1 m · wall 1 h 38 m
By agent (minutes charged to the agent that reported):
     21.8  build   wh-builder
     16.8  design  wh-designer
     12.0  review  wh-fixer
      7.7  review  reviewers
      5.1  design  wh-constraint-auditor+eval-designer+designer revision
      3.0  review  wh-verifier
      1.5  review  wh.js verify
      1.2  verify  wh-verifier
      0.9  build   wh-polish
      0.8  verify  wh.js verify
      0.4  build   conductor
      0.1  design  conductor
      0.0  review  conductor
     71.3  total
```

## Your decision

Approve to record G4 (you then merge the PR to main yourself; no agent merges or deploys), or reject with notes to send it back. Approving accepts C1 to C9 and the 5 open lows and 2 deferred mediums above.

```
/workhorse:approve G4
/workhorse:approve G4 --reject "notes"
```
