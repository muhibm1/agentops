# Verification: increment 4 no-spend measurement harness

Change id: `2026-10-02-increment-4-no-spend-measurement-harness`
Status: green
Run at: 2026-10-02T21:35:38.179Z UTC
Commit: `17d6b569e0b1`

Status is green only when every defined check exited 0 and every eval category met its target.
"No check defined" rows do not count as passes; they are listed so the gap is visible.

## What was measured

<!-- wh:verify-start -->
<!-- written by `wh.js verify`; edit outside the markers -->
- Type check: 0 errors (`.venv/Scripts/python.exe -m pyright`).
- Lint: 0 errors (`.venv/Scripts/python.exe -m ruff check .`).
- Test suite: 876 passed, 0 failed, 1 skipped (`.venv/Scripts/python.exe -m pytest -q`).
- Dependency audit: 0 known vulnerabilities (`.venv/Scripts/python.exe -m pip_audit -r requirements.txt`).
<!-- wh:verify-end -->

Evals (confirmed): at commit 17d6b56 every test id named in evals.md "Implemented as" (74 cases, including N1 and N2) plus all of `tests/test_full_runs.py` (E26) was passed to pytest; pytest collected every id and the run (which also swept the whole `tests/` directory) gave 876 passed, 1 skipped, exit 0 (`verify-logs/evals.log`). Up from 784 tests at the previous change, `2026-10-01-increment-3-act-pause-persist-resume` (877 now, 876 passed and 1 skipped). No known failures.

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
| golden | 39 | 39 | 100% | met |
| edge | 9 | 9 | 100% | met |
| failure | 11 | 11 | 100% | met |
| adversarial | 11 | 11 | 100% | met |

## Failures and fixes

Chronological. Each failure names the check, the cause, the fix commit, and the re-run.

| # | Check | Cause | Fix commit | Re-run exit code |
|---|-------|-------|------------|------------------|
| | | | | |

## Not verified

Anything believed but not run, with the reason.

- No held-out scoring, live model, LangSmith or .env was used, by instruction. The 1 skipped test in the full suite is not an eval case (believed, not verified which test it is).

## Known failures

None.

## Notes for the Ship document

- Tests added by the review fixes (78bc4a4..HEAD) beyond the evals.md cases, all passing: baseline label counts accepted applies (4, test_baseline); torn-twice ledger and newline-less entry through the CLI (2, test_cli_lifecycle); unreadable scenario fails write first, non-UTF-8 manifest raises, malformed manifest is a mismatch (3, test_dataset); dev eval refuses a stale manifest or changed frozen scenario, non-UTF-8 manifest exits 2, failed rows fail safety and false_remediation, failed-row stderr, exit 1 only when every row failed (7, test_eval_cli); approval-limit and captured-ids behaviour (6, test_eval_run); failed run scores false (3, test_evaluators); approval needs a matching audit record (2, test_executor); dev server origins pinned (1, test_langgraph_json); target inside a longer name vs whole token (2, test_preconditions); langsmith and dev server pins (2 functions, test_requirements); ledger repair tear cases and marker rules (7, test_simulator); verify window fallbacks (3, test_verify_postmortem).
- evals.md cases E4, E9, E14, E35 and A1 wording may predate the fixes (repair write, failed-row scoring); the tests pass, so this is prose only.

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
