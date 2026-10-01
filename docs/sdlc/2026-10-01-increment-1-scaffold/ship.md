# Ship: Increment 1 scaffold

Change id: `2026-10-01-increment-1-scaffold` · Tier 2 · Branch `wh/2026-10-01-increment-1-scaffold` at `384f7df` (code at `db61178`) · PR https://github.com/muhibm1/agentops/pull/1
Prepared 2026-10-01 UTC · Design approved: see [approvals.md](./approvals.md)

This is the Ship document. Approving it records your decision for the merge. The PR is open and not merged.

## The short version

Increment 1 builds the AgentOps scaffold: typed incident state, a simulated evidence source with two scenarios, a triage, evidence and root-cause graph run by a scripted model, the `agentops run` command, and CI with a test-count floor. It changes only this repository; no environment is deployed. You are deciding whether to merge it, knowing the first GitHub Actions run has not been seen yet (R20).

## What changed

Plain words: the project went from design documents to a working package. `agentops run --scenario X --model scripted` loads a synthetic incident, gathers evidence, and prints a root cause that must cite real evidence. No live model, no network, no real systems.

1. `.github/workflows/ci.yml`, `.github/dependabot.yml`, `.githooks/pre-push`: CI, pinned actions, dependency updates; protected "ask first" paths, authorised by the brief (D1).
2. `.workhorse/profile.yml`, `CLAUDE.md`: profile commands now confirmed; sensitive, so first.
3. `requirements.txt`, `requirements-dev.txt`, `pyproject.toml`, `LICENSE`, `.gitignore`: exact pins, licence, `*.egg-info/` ignore (D-C1).
4. `src/agentops/state.py`: incident state and the citation rule; tier-2 floor path.
5. `src/agentops/graph.py`, `nodes/*.py`: triage, evidence, rca, retries, prompt fencing of untrusted text.
6. `src/agentops/sources/*`, `scenarios/*.json`: evidence source and two scenarios; hidden sections never served.
7. `src/agentops/fakes.py`, `cli.py`: scripted model; CLI with terminal escaping of model text.
8. `scripts/check_test_count.py`: fails CI if fewer than 30 tests ran.
9. `tests/*`: 76 tests across 11 files.
10. `README.md`, `CONTRIBUTING.md`, `docs/ci.md`, `docs/sdlc/codebase-map.md`, three ADRs, this folder: documentation.

## Proof

| Check | Command | Exit code | Output | Status |
|-------|---------|-----------|--------|--------|
| install | `python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-dev.txt` | not run | n/a | not run (verify skips install) |
| typecheck | `.venv/Scripts/python.exe -m pyright` | 0 | [typecheck.log](./verify-logs/typecheck.log) | confirmed |
| lint | `.venv/Scripts/python.exe -m ruff check .` | 0 | [lint.log](./verify-logs/lint.log) | confirmed |
| test | `.venv/Scripts/python.exe -m pytest -q` (76 passed) | 0 | [test.log](./verify-logs/test.log) | confirmed |
| security_audit | `.venv/Scripts/python.exe -m pip_audit -r requirements.txt` (0 vulnerabilities) | 0 | [security_audit.log](./verify-logs/security_audit.log) | confirmed |
| build, e2e, screenshot | none defined in the profile | n/a | n/a | not run (no check defined) |
| green Actions run (R20) | none; needs the pushed branch read by the owner | n/a | n/a | believed, not verified |

Evals: golden 18/18, edge 5/5, failure 8/8, adversarial 7/7 (target 100% each; confirmed, [verification.md](./verification.md)).
Known pre-existing failures cited: none (`wh.js known-failure list`: "No known failures recorded.").

## What the reviewers found

0 critical, 0 high, 5 medium (all fixed in `db61178` and re-checked closed by the reviewer who raised them), 15 low rows below (8 open per the conductor, 0 accepted instead of fixed). Reports: [reviews/](./reviews/).

| Severity | Reviewer | File:line | Finding | Resolution |
|----------|----------|-----------|---------|------------|
| medium | wh-security | cli.py:123-131 | Model text printed to terminal unescaped | fixed in db61178 |
| medium | wh-bug | cli.py:109-118 | Triage CitationError shown as an unrelated ValidationError | fixed in db61178 |
| medium | ecc-silent-failure-hunter | nodes/evidence.py:109-127 | Zero evidence accepted silently | fixed in db61178 (NoEvidenceError, exit 2) |
| medium | wh-adoption | cli.py:121-134 | Spec says CLI prints action counts; it did not | fixed in db61178 |
| medium | wh-adoption | README.md:12-15 | Run section silent on `--scenarios-dir` default and code map | fixed in db61178 |
| low | wh-bug | cli.py:127-136 | Error path (`fail()`, CitationError ids) prints model text unescaped | open, low |
| low | wh-bug | nodes/__init__.py:58-60 | Neutralised dict-key collision can drop an entry | open, low |
| low | ecc-silent-failure-hunter | nodes/__init__.py:83-89 | Failed retry attempts not recorded in `state.actions`; `(M failed)` in the report is always 0 for them | open, low |
| low | wh-adoption | docs/sdlc/codebase-map.md:23 | `scripted_model_with_retries` (cli.py) not mentioned | open, low |
| low | wh-adoption | fakes.py:35-38 | `ScriptedChatModel` docstring does not cover `script` vs `script_by_schema` | open, low |
| low | wh-conformance | .workhorse/profile.yml:5,17-19 | Stack labels still say "believed" (sensitive file, owner edits) | open, low |
| low | wh-security | whole run | `npx ecc-agentshield` scan could not run (harness denied external code); read by hand instead | open, low, not verified |
| low | verifier artifact (conductor-noted) | verification.md:22,74 | Checks table appears twice | open, low |
| low | wh-conformance | brief.md D1-D17 | Decisions taken by recommendation, no per-row human answer | open, low (G2 notes accept all 17) |
| low | ecc-python | check_test_count.py:18; graph.py:25 | Unguarded int/XML parse; source retry on bare Exception | open, low (believed unchanged; ecc-python was not re-run; both fail closed) |
| low | ecc-python, ecc-silent-failure-hunter | cli.py:116-118 | Triage script held one entry, hiding the real error | fixed in db61178 |
| low | wh-bug, wh-security | scenario.py:93, evidence.py:56, triage.py/rca.py, evidence.py:58 | BOM/UTF-16 scenario files; 4+ bracket runs; unfenced service and symptoms; dict keys not neutralised | fixed in db61178 |

Spec conformance: 20 of 23 requirements traced to code and test (R1-R18, R22, R23). R19 is run by the verifier, R20 has no machine check, R21 timing has no test (confirmed by [wh-conformance-reviewer.md](./reviews/wh-conformance-reviewer.md)). Adoption score: 4.

## Decisions

Approving accepts every recommendation. G2 already accepted D1-D17 ([approvals.md](./approvals.md)); they are in [brief.md](./brief.md).

| # | Decision | Recommendation | Alternative | Why the recommendation |
|---|----------|----------------|-------------|------------------------|
| D-S1 | Rollback rehearsal (tier 2) | Not rehearsed: no environment has a deploy command | Invent a local deploy target | Nothing deploys; undo is a git revert of the merge commit |
| D-S2 | R20 green Actions run | Merge only after you read the first run on the PR | Merge now | Not verified by any agent; a Linux install may fail (see `CLAUDE.md` mistakes) |
| D-C1 | `*.egg-info/` added to `.gitignore` | Keep (spec said no edit; harmless) | Revert | The editable install creates that folder |
| D-C2 | ADR 0002 corrected | Keep: evidence node retries on any Exception, confirmed needed for E37 | Narrow the retry | Narrowing is an ecc-python low, deferred until real adapters exist |
| D-C3 | Open lows | Leave open for a later increment | Fix before merge | None blocks; profile labels are yours to edit (sensitive) |
| D1-D17 | See brief | Accepted at G2 | | |

## Deploy and undo

| Environment | Command | Automatic on approval | Rollback |
|-------------|---------|-----------------------|----------|
| dev | none (empty) | yes, nothing to run | `git revert -m 1 <merge commit>` (not run) |
| staging | none (empty) | yes, nothing to run | same |
| prod | none (empty) | no | same |

Rollback rehearsed (tier 2+): not rehearsed: no environment has a deploy command (D-S1).
Config and secrets touched (names only, never values): none. `ANTHROPIC_API_KEY` is not read in this increment.

## Clock

Clock: agents 1 h 03 m of 1 h 30 m budget · waiting on you 12 m · dead 1 m · wall 1 h 15 m

## Your decision

Approve to merge and deploy, or reject with notes to send it back. Recommended: read the first Actions run on the PR, then approve.
