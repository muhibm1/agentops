# Verification: Increment 1 scaffold

Change id: `2026-10-01-increment-1-scaffold`
Status: green
Run at: 2026-10-01T04:16:43.198Z UTC
Commit: `db611789bdb0`

Status is green only when every defined check exited 0 and every eval category met its target.
"No check defined" rows do not count as passes; they are listed so the gap is visible.

## What was measured

<!-- wh:verify-start -->
<!-- written by `wh.js verify`; edit outside the markers -->
- Type check: 0 errors (`.venv/Scripts/python.exe -m pyright`).
- Lint: 0 errors (`.venv/Scripts/python.exe -m ruff check .`).
- Test suite: 76 passed, 0 failed, 0 skipped (`.venv/Scripts/python.exe -m pytest -q`).
- Dependency audit: 0 known vulnerabilities (`.venv/Scripts/python.exe -m pip_audit -r requirements.txt`).
<!-- wh:verify-end -->


## Checks

| Check | Command | Exit code | Output | Status |
|-------|---------|-----------|--------|--------|
| install | python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-dev.txt | | | skipped (runs with --install) |
| typecheck | `.venv/Scripts/python.exe -m pyright` | 0 | `verify-logs/typecheck.log` | confirmed |
| lint | `.venv/Scripts/python.exe -m ruff check .` | 0 | `verify-logs/lint.log` | confirmed |
| test | `.venv/Scripts/python.exe -m pytest -q` | 0 | `verify-logs/test.log` | confirmed |
| build |  | | | no check defined |
| e2e |  | | | no check defined |
| security_audit | `.venv/Scripts/python.exe -m pip_audit -r requirements.txt` | 0 | `verify-logs/security_audit.log` | confirmed |
| screenshot |  | | | no check defined |

## Evals

| Category | Cases | Passed | Target | Met |
|----------|-------|--------|--------|-----|
| golden | 18 | 18 | 100% | yes |
| edge | 5 | 5 | 100% | yes |
| failure | 8 | 8 | 100% | yes |
| adversarial | 7 | 7 | 100% | yes |

Non-functional (confirmed): N1 full suite 12.39 s on pytest's summary line (target < 60 s), met. N2 `agentops run --scenario clean_bad_deploy --model scripted` 1.185 s real under Git Bash `time`, exit 0 (target < 5 s), met. E34: lint, typecheck, test, security_audit exit 0 per summary.json; `ruff format --check .` exit 0 ("28 files already formatted").

Eval run (confirmed): `.venv/Scripts/python.exe -m pytest -v -rA`, exit 0, 76 passed, re-run after review fix db61178 (all cases re-checked; E18, E19, E20, E26, E29, E32, E35, E37 named PASSED), log `verify-logs/evals.log`. Each of the 37 test-backed cases E1 to E38 (excluding E34) has its named test with a PASSED line there (parametrised cases: every parameter passed). 38 cases total: 18 golden, 5 edge, 8 failure, 7 adversarial.

## Failures and fixes

Chronological. Each failure names the check, the cause, the fix commit, and the re-run.

| # | Check | Cause | Fix commit | Re-run exit code |
|---|-------|-------|------------|------------------|
| none | | | | |

## Not verified

Anything believed but not run, with the reason.

- R20: a green GitHub Actions run. No machine-runnable case; the owner reads the run after pushing (plan.md).

## Known failures

None recorded (`wh.js known-failure list`).

## Comparison

No previous change to compare with.

## Notes for the Ship document

Review fix db61178 added 10 tests (66 to 76 total). Up from 66 tests at the earlier run of this same change. N1 updated to 12.39 s from the new pytest summary.

## Checks

| Check | Command | Exit code | Output | Status |
|-------|---------|-----------|--------|--------|
| install | python -m venv .venv && .venv/Scripts/python.exe -m pip install -r requirements.txt -r requirements-dev.txt | | | skipped (runs with --install) |
| typecheck | `.venv/Scripts/python.exe -m pyright` | 0 | `verify-logs/typecheck.log` | confirmed |
| lint | `.venv/Scripts/python.exe -m ruff check .` | 0 | `verify-logs/lint.log` | confirmed |
| test | `.venv/Scripts/python.exe -m pytest -q` | 0 | `verify-logs/test.log` | confirmed |
| build |  | | | no check defined |
| e2e |  | | | no check defined |
| security_audit | `.venv/Scripts/python.exe -m pip_audit -r requirements.txt` | 0 | `verify-logs/security_audit.log` | confirmed |
| screenshot |  | | | no check defined |
