# Ship: increment 4, no-spend measurement harness

Change id: `2026-10-02-increment-4-no-spend-measurement-harness` · Tier 3 · Branch `wh/2026-10-02-increment-4-no-spend-measurement-harness` · code head `17d6b56` (ship.md and review records are docs-only commits on top) · PR https://github.com/muhibm1/agentops/pull/10
Prepared 2026-10-02 UTC · Design approved: see [approvals.md](./approvals.md) (G2, recorded by the main session on the owner's standing instruction; the owner did not read the brief)

Approving this document records Ship (G4). Per CLAUDE.md the release is the owner merging PR #10 to `main`; no agent merges and no deploy command exists.

## The short version

Increment 4 adds a measurement harness that runs the graph and a naive baseline over the eight scenarios with no live model and no spend. It adds `agentops eval`, `agentops dataset`, a frozen held-out split pinned by checksums, a `langgraph dev` entry, new runbook preconditions, and a stricter execute step that only applies an approval the audit log records. You are deciding whether to merge it, accepting the Decisions table (D25, D26 and the ask-first path edits need your eye).

## What changed

In plain words: you can now score the graph against a baseline from the command line, the held-out scenarios are locked by hashes, and a crash during a write no longer leaves a ledger or audit file that blocks resume. The held-out split was never scored (confirmed: verification.md, "Not verified").

Diff tour, by risk (79 files, +7790 -165 against `main`):

1. `runbooks/default.yaml` (ask-first, security boundary): per-action verify window, job-name patterns, target-named-in-logs precondition. Diff is +8 -3; allowlist membership unchanged (believed, not verified).
2. `requirements.txt`, `requirements-dev.txt` (ask-first): exact pins `langchain==1.4.3`, `langsmith==0.14.2`, `langgraph-cli[inmem]==0.4.32`, `langgraph-api` 0.15.1 and `langgraph-runtime-inmem` 0.35.1 (the last two Elastic-2.0, dev only).
3. `.github/workflows/ci.yml` (ask-first): `MIN_TESTS` 450 to 700 by the docs/ci.md rule (784 x 0.9).
4. `evals/checksums.sha256`, `evals/held_out/manifest.json` (ask-first, eval integrity): new files pinning the three frozen scenarios.
5. `langgraph.json` (ask-first): new, one graph, CORS origins pinned, no `env` key. `.workhorse/profile.yml`: D13 ask rules (+2 lines).
6. `src/agentops/actions/executor.py`, `audit.py`, `preconditions.py`, `nodes/execute.py`: approval must be in the audit log; torn audit line repaired; whole-token target match.
7. `src/agentops/sources/simulator.py`: crash-safe ledger repair with a seal marker, LF-only appends.
8. `src/agentops/evals/**`, `baseline.py`, `tracing.py`, `dev_server.py`, `cli.py`: harness, evaluators, runner, dataset, tracing switch rule.
9. `scenarios/*.json` (8, with baseline scripts), `tests/**`, `README.md`, `CONTRIBUTING.md`, `docs/`: tests and docs.

Ask-first paths touched, plainly: `runbooks/default.yaml`, `requirements.txt`, `requirements-dev.txt`, `langgraph.json`, `.github/workflows/ci.yml`, `evals/checksums.sha256`, `evals/held_out/manifest.json`, and `.workhorse/profile.yml`. Not touched: `pyproject.toml`, `.gitignore`, `LICENSE`, `.githooks/**`, `.github/dependabot.yml`, `.env*` (confirmed by `git diff --name-only main...HEAD`).

## Proof

All rows at commit `17d6b56` (confirmed, from [verification.md](./verification.md) and `verify-logs/summary.json`; the code did not move after that commit).

| Check | Command | Exit code | Output | Status |
|-------|---------|-----------|--------|--------|
| typecheck | `.venv/Scripts/python.exe -m pyright` | 0 | `verify-logs/typecheck.log` | confirmed |
| lint | `.venv/Scripts/python.exe -m ruff check .` | 0 | `verify-logs/lint.log` | confirmed |
| test | `.venv/Scripts/python.exe -m pytest -q` (876 passed, 1 skipped) | 0 | `verify-logs/test.log` | confirmed |
| security_audit | `.venv/Scripts/python.exe -m pip_audit -r requirements.txt` | 0 | `verify-logs/security_audit.log` | confirmed |
| install, build, e2e, screenshot | none defined, or install not run | | | not run |
| CI `ci` (push) | GitHub Actions run 37068165078 at `17d6b56` | success | `gh pr checks 10 --watch` | confirmed |
| CI `ci` (pull_request) | GitHub Actions run 37068166798 at `17d6b56` | success | `gh pr checks 10 --watch` | confirmed |

Evals (confirmed; 74 eval test ids plus `tests/test_full_runs.py` run at `17d6b56`, `verify-logs/evals.log`): golden 39/39, edge 9/9, failure 11/11, adversarial 11/11, all at the 100% target. Known pre-existing failures: none (`wh.js known-failure list`). Test count at the head: 877 collected, 876 passed, 1 skipped (which one: believed, not verified; previous change 784), above the CI floor of 700.

## What the reviewers found

Seven reviewers; reports in [reviews/](./reviews/); two re-check rounds. Critical 0; high 1 (fixed); medium 15 (all fixed, none accepted); lows below.

| Severity | Reviewer | File:line | Finding | Resolution |
|----------|----------|-----------|---------|------------|
| high | ecc-pr-test-analyzer | tests/test_simulator.py:494 | Second crash inside the ledger repair write bricks replay | fixed b8cfd7d |
| medium | wh-security | langgraph.json:1, executor.py:117 | Dev server had no CORS pin; execute trusted `state["approval"]` | fixed 3ec1000 (audit-recorded approval, origins pinned); residue is R1, R2b below |
| medium | wh-bug | preconditions.py:224 | Target matched as a substring | fixed b0835b2 |
| medium | wh-bug, ecc-silent, ecc-python | simulator.py:285, ~270 | Ledger repair not crash safe; complete JSON without newline mis-sealed | fixed b8cfd7d |
| medium | wh-bug | dataset.py:73, commands.py:83 | Dev split skipped the checksum check | fixed 7f3b10a |
| medium | wh-bug | baseline.py:280 | Baseline could never be labelled looped | fixed c1260f6 (2+ accepted applies) |
| medium | wh-bug (re-check) | simulator.py:304 | CRLF tear on Windows breaks replay | fixed b4a5a8b |
| medium | wh-bug (re-check) | executor.py:124, audit.py:63 | Torn audit line swallowed a real approval | fixed 6e98ed7 |
| medium | ecc-silent, ecc-pr-test | evals/run.py:288, evaluators.py | Failed row scored True on safety and false_remediation | fixed c3b93db |
| medium | ecc-silent, ecc-pr-test | evals/run.py:244 | Approval capture could end silently; untested | fixed c3b93db |
| medium | ecc-pr-test | test_verify_postmortem.py:391 | Verify window fallbacks untested | fixed 0cce395 |
| medium | wh-adoption | README.md "When it fails" | No entries for eval and dataset failures (score 4, now 5) | fixed 2514723 |
| low | wh-security, ecc-silent | preconditions.py, cli.py, conftest.py, requirements*.txt, run.py:164 | Substring match; tracing rule scope; two switches uncleared; unpinned langsmith and Elastic-2.0 packages; failed rows hid the cause | fixed b0835b2, 77f392d, 866c2f2, c3b93db |
| low | wh-conformance | spec.md:46 | B1 (SqliteSaver) missing from the spec | fixed 2514723 |
| low | wh-conformance | brief.md D1-D13 | Ask-first decisions taken on recommendation | surfaced under Decisions |
| low | ecc-python, wh-adoption | commands.py:197, dataset.py, README.md | UnicodeDecodeError; manifest written before hashing; stale command count; ADRs unlinked | fixed 2514723 and re-check commits |

Unresolved lows (nothing hidden); none is critical, high or medium:

| State | Reviewer | Where | Finding | Resolution |
|-------|----------|-------|---------|------------|
| open | wh-security R1 | langgraph.json:6 | CORS pin does not stop a `text/plain` simple POST; a real fix needs an auth handler in `langgraph.json` | accepted: conductor under the owner's standing instruction (D26), dev-only, simulator-only; not run against a live server |
| open | wh-security R2b, wh-bug | src/agentops/actions/executor.py:112-130, :224 | One recorded approval can apply again from a second thread by writing state on the dev server. Proposed fix: match `thread_id` in the approval record, add eval E41 | open, no follow-up file; owner to decide |
| open | wh-bug | src/agentops/evals/outcome.py:195-212 | `already_applied` is not counted as an apply; unreachable from `agentops eval` (believed) | open, no follow-up file |
| open | ecc-python | src/agentops/evals/run.py:154 | `RUNNERS` erases the runner signature (Protocol proposed) | deferred as agreed |
| open | ecc-python | src/agentops/evals/commands.py:87 | `os.environ.update` leaks the tracing project in-process | deferred as agreed |
| open | ecc-silent | src/agentops/actions/executor.py:253 | Apply exception reduced to its type name; no traceback logged | open |
| open | wh-adoption | README.md checksum bullet | Does not say held-out files are ask-first | open (new in re-check) |
| by rule | conductor | scenario `two_concurrent_faults` | Graph `root_cause_accuracy` is False because grading reads the final diagnosis (D24) | accepted: conductor, no scenario or checksum edit |

Spec conformance: 19 of 19 requirements (R1-R19 with N1, N2) traced to code and test; no scope creep (wh-conformance-reviewer, original pass at 78bc4a4). Adoption score: 5 (was 4).

## Decisions

Approving accepts every recommendation. Nothing in this run was irreversible or needed a permission the agents lacked. D1-D20 were approved at G2 (approvals.md); the rows below are the ones taken since, plus those that touch ask-first paths.

| # | Decision | Recommendation | Alternative | Why |
|---|----------|----------------|-------------|-----|
| D26 | Dev server simple-POST CORS gap (R1) | Accept; dev-only, simulator-only | Add an auth handler to `langgraph.json` | Wider scope, ask-first file; no real system is reachable |
| D25 | Pin `langsmith`, `langgraph-api`, `langgraph-runtime-inmem` | Exact pins with licences (last two Elastic-2.0, dev only) | Leave transitive | CLAUDE.md requires exact pins; audit saw 0.14.2 vs 0.14.4 drift |
| D24 | `two_concurrent_faults` graph root_cause_accuracy False | Accept and document | Edit scenario (breaks checksums) | Grading reads the final diagnosis by rule |
| D21-D23 | Ledger rules: raise on a mid-file fragment (D21); execute refuses any remediation but the approved one (D22); D23 | Keep; D21 later refined by the seal marker | Silent skip | Only D21 and D22 are described in code and logs; D23 text not found (believed, not verified) |
| Seal | Torn-ledger seal marker | Marker line; replay accepts a run of torn lines | Rewrite the file | Append-only file; spec R4 updated |
| Looped | Label for `loop` scenarios | 2+ accepted applies, same for graph and baseline | Count loop passes | Matches the graph; spec updated |
| T3 | Red-herring bait in `deploy_red_herring_db` | `rollback_deploy`, per scenario data | `scale_db_pool` as ADR 0005 says | Data is the source; ADR 0005 wording is stale (note, not a defect) |
| T4 | Tracing switches | Clear and check all four variables | Two | LangSmith reads four |
| T6 | Spec row on `create_agent` tool errors | Row was wrong (builder proved it) | Keep | Proven by test |
| B1 | Eval checkpointer | Temporary `SqliteSaver` | `InMemorySaver` per spec | Spec updated in 2514723 |
| Fixtures | Executor test fixtures edited for `target_named_in_logs` (7f2c0c6); one test replaced for the unknown-effect requirement change | Edit fixtures, keep intent | Loosen the precondition | The precondition is the new requirement; CLAUDE.md says never edit a test to pass, so review these two (believed, not verified that no assertion was weakened) |
| D13 | `ask_commands` gains `--held-out` and `--model anthropic` | Keep | None | Done at G2 |
| D1, D2, D6, D7, D11, D12 | Ask-first pins, runbook fields and patterns, approve-all operator, CI floor | As approved at G2 | See brief.md | Listed because they touch ask-first paths |

## Deploy and undo

| Environment | Command | Automatic on approval | Rollback |
|-------------|---------|-----------------------|----------|
| dev | none defined | n/a | none needed |
| staging | none defined | n/a | none needed |
| prod | none defined | no | none needed |

The release is the owner merging PR #10 to `main`. Undo: `git revert -m 1 <merge commit>` on `main` (plan.md "Rollback"). Rollback rehearsed: not rehearsed: no environment has a deploy command and nothing is deployed. Config and secrets touched (names only): none; no `.env*` file is created or read.

## Clock

```
Clock: agents 3 h 22 m of 3 h 00 m budget (OVER) · waiting on you 0 m · dead 1 h 28 m · wall 4 h 50 m
By agent (minutes charged to the agent that reported):
     70.4  build   wh-builder
     50.7  review  wh-fixer
     22.2  design  wh-designer
     11.2  build   wh-builder x4
      9.5  build   wh-fixer
      8.5  build   wh-builder x3
      6.8  review  re-check round 1
      6.4  review  reviewers x7
      3.9  review  wh-verifier + wh-bug-reviewer
      2.9  review  wh.js verify
      2.3  design  wh-constraint-auditor
      1.5  build   wh-polish
      1.3  verify  wh.js verify
      0.9  verify  wh-verifier
      0.2  design  conductor
      0.2  build   conductor
      0.0  design  wh-eval-designer
      0.0  review  conductor
    198.9  total
```

## Your decision

Approve to record Ship (G4), or reject with notes. Merging PR #10 stays with you.

```
/workhorse:approve G4
/workhorse:approve G4 --reject "notes"
```
