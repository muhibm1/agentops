# Ship: Increment 3 act pause persist resume

Change id: `2026-10-01-increment-3-act-pause-persist-resume` · Tier 3 · Branch `wh/2026-10-01-increment-3-act-pause-persist-resume` at `c86b099` (code at `8081b87`) · PR https://github.com/muhibm1/agentops/pull/5
Prepared 2026-10-01 UTC · Design approved: see [approvals.md](./approvals.md)

This is the Ship document. This repository has no deploy target: the owner merging PR #5 to `main` is the release.

## The short version

AgentOps can now act on its own diagnosis: it proposes a fix from an allowed-action list, stops and waits for a named operator to approve, applies the fix exactly once, checks that the symptoms cleared, and writes a postmortem. It changes the command line (new `approve`, `resume`, `history`, `fork`), adds a SQLite checkpoint file so a run survives a restart, and adds an append-only audit trail for every step. You are deciding whether to accept this and merge it yourself; the PR is ready for review and has not been merged.

## What changed

In plain words: before this change the agent only investigated and reported. Now it can pause for human approval, survive a crash after applying a fix (rerunning does not apply twice), verify the result, and produce a postmortem. The only system it can change is the built-in simulator. 75 files, 8981 lines added, 248 removed (confirmed, `git diff --stat main...HEAD`).

Diff tour, ordered by risk:

1. `runbooks/default.yaml` (new) - the allowed-action list, the security boundary: five actions with parameter ranges, preconditions, risk, rollback and `target_pattern`.
2. `requirements.txt`, `requirements-dev.txt` - new pinned runtime dependencies (`langgraph-checkpoint-sqlite==3.1.1`, `aiosqlite==0.22.1`, `sqlite-vec==0.1.9`); `pyyaml==6.0.3` moved to runtime. Approved at G2 (D1, D2, D24).
3. `.github/workflows/ci.yml`, `.gitignore` - CI floor `MIN_TESTS` 450 (D13); `*.sqlite-*` ignored (D23).
4. `src/agentops/actions/executor.py` - the only code that applies a change: registry check, preconditions, idempotency key, ledger check, dry run, apply, audit.
5. `src/agentops/actions/registry.py`, `runbook.py`, `preconditions.py`, `audit.py` - validation of action, parameters and target; preconditions; append-only audit file.
6. `src/agentops/nodes/approval.py`, `execute.py` - the pause for the operator, re-validated edits, approval bound to the change's key (D21, D22).
7. `src/agentops/nodes/verify.py`, `postmortem.py`, `rca.py`, `escalate.py`, `router.py`, `merge.py`, `evidence.py`, `nodes/__init__.py` - verify rule, postmortem, read cap of 12, nonce fence for untrusted text.
8. `src/agentops/sources/simulator.py`, `scenario.py`, `base.py` and 7 `scenarios/*.json` - simulator ledger persisted to a file; post-action worlds.
9. `src/agentops/cli.py`, `__main__.py`, `graph.py`, `state.py`, `fakes.py` - commands, checkpointing, new state fields, length caps.
10. `tests/*` - 548 tests now, up from 239 before increment 3.
11. `README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, `docs/`, this change's `spec.md`, `plan.md`, `adr/` - documentation and the record.

## Proof

| Check | Command | Exit code | Output | Status |
|-------|---------|-----------|--------|--------|
| install | `python -m venv .venv && ... pip install ...` | | | not run (verify runs it only with `--install`) |
| typecheck | `.venv/Scripts/python.exe -m pyright` | 0 | `verify-logs/typecheck.log` | confirmed (0 errors) |
| lint | `.venv/Scripts/python.exe -m ruff check .` | 0 | `verify-logs/lint.log` | confirmed (0 errors) |
| test | `.venv/Scripts/python.exe -m pytest -q` | 0 | `verify-logs/test.log` | confirmed (548 passed, 0 failed, 1 skipped) |
| build | none defined in the profile | | | no check defined |
| e2e | none defined in the profile | | | no check defined |
| security_audit | `.venv/Scripts/python.exe -m pip_audit -r requirements.txt` | 0 | `verify-logs/security_audit.log` | confirmed (0 known vulnerabilities) |
| screenshot | none defined (no UI) | | | no check defined |
| CI, push run 36849676591 | GitHub Actions `ci` at `c86b099` | success (1m30s) | https://github.com/muhibm1/agentops/actions/runs/36849676591 | confirmed |
| CI, PR run 36849681648 | GitHub Actions `ci` (pull_request) at `c86b099` | success (1m22s) | https://github.com/muhibm1/agentops/actions/runs/36849681648 | confirmed |

Local checks ran at `8081b87` ([verify-logs/summary.json](./verify-logs/summary.json), `checks_green: true`). The commits after it change only files under `docs/sdlc/`.

Evals: golden 21/21, edge 17/17, failure 14/14, adversarial 12/12 (targets 100%, all met; 16 eval files, [verify-logs/evals.log](./verify-logs/evals.log)). They ran against `228bbbc`, before the review fixes; the full suite at `8081b87` is green (confirmed). The case-to-test mapping is by file and case id, not one function per case (believed).
Non-functional: suite time 25.81 s against 120 s (confirmed); test floor 501 against 450 (confirmed, before the fixes added tests); run plus approve 2.41 s against 10 s (reported figure, not re-measured).
Known pre-existing failures: none (`wh.js known-failure list`: "No known failures recorded").

## What the reviewers found

Seven reports in [reviews/](./reviews/). Every high and medium row is closed by a fix commit and was re-checked by the same reviewer. Totals: 0 critical, 2 high (both fixed), 10 medium (all fixed), 14 low (9 fixed, 5 open). 0 accepted rather than fixed.

| Severity | Reviewer | File:line | Finding | Resolution |
|----------|----------|-----------|---------|------------|
| high | wh-bug-reviewer | executor.py:185, verify.py:123 | A crash after apply was recorded as `applied=False`, so verify and rca treated it as unchanged | fixed: c2b4139, 285d25e |
| high | ecc-silent-failure-hunter | verify.py:97-148 | A failed metrics read still declared "cleared" | fixed: c0c689b |
| medium | wh-security-reviewer | preconditions.py:114,133 | Preconditions checked the service, never the action's target | fixed: efe6f37 |
| medium | wh-bug-reviewer | executor.py:184, verify.py:139 | A plain repeat of an applied key looped verify instead of escalating (found at re-check) | fixed: c8e88e0 |
| medium | wh-bug-reviewer | postmortem.py:81 | Postmortem named only the last applied action | fixed: f5f5b2f |
| medium | wh-bug-reviewer | verify.py:141 | Verify window ignored post-action signals past 30 minutes (scenarios) | fixed: c4aabb5 (non-simulator half: see low row) |
| medium | ecc-python-reviewer | cli.py (fork) | A fork reused the original's ledger and audit file | fixed: e0f6f73 |
| medium | ecc-silent-failure-hunter | cli.py:560, executor.py:268 | Report said "Nothing was changed." after crash and resume | fixed: c2b4139 |
| medium | ecc-silent-failure-hunter | cli.py:174 | `approve` exited 0 when the answer was refused | fixed: e0f6f73 |
| medium | ecc-pr-test-analyzer | test_executor.py:494 | Failed apply-record write untested | fixed: 285d25e |
| medium | ecc-pr-test-analyzer | test_verify_postmortem.py:119 | Latest-point rule and opening boundary untested | fixed: two tests added within 228bbbc..285d25e (re-check read the diff; assertion strength believed adequate) |
| medium | wh-adoption-reviewer | README.md | No operator guidance for failure outcomes | fixed: b0b6ebe |
| low | wh-security-reviewer | runbooks/default.yaml:51,64; preconditions.py:180 | `rerun_partition` and `pause_batch_job` accept any job name matching a broad pattern; preconditions read the service only | open, not fixed; both are risk low; the operator reading the dry run is the control |
| low | ecc-python-reviewer | simulator.py `_replay` | A partial last ledger line makes later commands exit 2 until hand-edited | open, not fixed |
| low | ecc-python-reviewer, wh-bug-reviewer | verify.py (window) | Fixed 30-minute verify window; a non-simulator apply later than that sees no signal | open, not fixed (believed, not verified against a live source) |
| low | ecc-silent-failure-hunter | executor.py:222 | When `source.apply` raises, audit says `applied=False` though the effect is unknown | open, not fixed |
| low | ecc-pr-test-analyzer | test_executor.py:278 | Structure test asserts source layout, not behaviour | open, not required |
| low | wh-security-reviewer | executor.py:221 | Audit gap if the process dies between apply and its audit write | fixed: c2b4139, 285d25e |
| low | wh-security-reviewer | state.py:17,89 | `target` had no maximum length | fixed: efe6f37 (64 characters) |
| low | wh-security-reviewer | cli.py:287 | Catch-all printed unbounded exception text | fixed: acbb06f (200 characters) |
| low | wh-security-reviewer | state.py:84,123 | `RCA.explanation`, `Postmortem.summary` unbounded | fixed: 8081b87 (2000 characters) |
| low | ecc-python-reviewer | cli.py `opened_thread` | `KeyError` on a checkpoint with no values | fixed: e0f6f73 |
| low | ecc-silent-failure-hunter | verify.py:105 | `is_healthy` true for a metric with no range | fixed in 228bbbc..285d25e; the unescaped `str(error)` part is unchanged |
| low | wh-bug-reviewer | simulator.py:150 | Same post-action world merged twice | fixed: efb5f04 |
| low | wh-adoption-reviewer | CONTRIBUTING.md | Adding a runbook action undocumented | fixed: b0b6ebe |
| low | wh-adoption-reviewer | README.md | `--operator`, `--state-db`, `--audit-dir` undocumented | fixed: b0b6ebe |

Security reviewer, observability of `cli.py` (the automated sensitive-to-observability flag): confirmed that no unintended sensitive content reaches output. There is no `print` or logging under `src/agentops`; evidence payloads, operator reasons and remediation file content are never printed; model strings and failure sentences are escaped. The two length-cap lows were fixed in acbb06f and 8081b87. The flag gave no detail, so whether it meant something else is not verified.

Spec conformance: 24 of 24 requirements traced to code and test (R1-R21, N1-N3), no scope creep. That reviewer did not run N2; the test count was confirmed separately above.
Adoption score: 4.

## Decisions

D1-D24 were taken in [brief.md](./brief.md) and approved at G2 on 2026-10-01 ([approvals.md](./approvals.md)). The ones touching ask-first paths are D1, D2, D24 (dependencies), D3 (runbook), D13 (CI floor), D23 (`.gitignore`). Decisions since G2:

| # | Decision | Recommendation | Alternative | Why the recommendation |
|---|----------|----------------|-------------|------------------------|
| D25 | Needs you: run the AgentShield scan (this diff changes `CLAUDE.md`) | You run `npx -y ecc-agentshield@1.6.0 scan --path . --min-severity high --format json` by hand | Allow the permission classifier to run it | The harness refused it as "Untrusted Code Integration"; not run, so no result exists |
| D26 | Five open lows listed above | Merge with them open; they are listed here | Fix first | Only the simulator exists. Each low needs a malformed file, a very late apply, or steered evidence plus an approving operator |
| D27 | Who merges | You merge PR #5 yourself | An agent merges | Repository rule: the owner performs publishing pushes; no agent merged |
| D28 | Length caps on model text (64, 200, 2000 characters) | Keep the caps | No caps | Bounds what reaches terminals, logs and prompts; raised by the security reviewer |

## Deploy and undo

| Environment | Command | Automatic on approval | Rollback |
|-------------|---------|-----------------------|----------|
| none in `profile.environments` | none; the merge of PR #5 to `main` is the release | no: the owner merges | `git revert -m 1 <merge commit>` on `main`, then push |

Rollback rehearsed (tier 2+): not rehearsed: no local or dev deploy target exists; this is a library and CLI with no hosting. That a revert is clean is believed, not verified. Local state (`agentops.sqlite`, `audit/`) is git-ignored and untouched by a revert.
Config and secrets touched (names only, never values): none. No environment variable or secret is read (confirmed by the security reviewer).
Tier 3 note: no `release.md`, because there is no production environment to run.

## Clock

Clock: agents 3 h 26 m of 3 h 00 m budget (OVER) · waiting on you 1 m · dead 0 m · wall 3 h 28 m

The agent-time budget was overrun by 26 minutes. Most of it came from two review and fix rounds after the first build (believed, from the conductor log).

## Your decision

Approve to merge and deploy, or reject with notes to send it back. The PR is marked ready for review; merging is yours to do. Items you can accept or reject: D25 to D28 and the five open lows.
