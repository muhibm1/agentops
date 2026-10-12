# Verification: Increment 3 act pause persist resume

Change id: `2026-10-01-increment-3-act-pause-persist-resume`
Status: partial (checks green; evals not yet run)
Run at: 2026-10-01T10:29:23.263Z UTC
Commit: `55dc8806d62f`

Status is green only when every defined check exited 0 and every eval category met its target.
"No check defined" rows do not count as passes; they are listed so the gap is visible.

## What was measured

<!-- wh:verify-start -->
<!-- written by `wh.js verify`; edit outside the markers -->
- Type check: 0 errors (`.venv/Scripts/python.exe -m pyright`).
- Lint: 0 errors (`.venv/Scripts/python.exe -m ruff check .`).
- Test suite: 548 passed, 0 failed, 1 skipped (`.venv/Scripts/python.exe -m pytest -q`).
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
| golden | 21 | 21 | 100% | met |
| edge | 17 | 17 | 100% | met |
| failure | 14 | 14 | 100% | met |
| adversarial | 12 | 12 | 100% | met |

## Failures and fixes

Chronological. Each failure names the check, the cause, the fix commit, and the re-run.

| # | Check | Cause | Fix commit | Re-run exit code |
|---|-------|-------|------------|------------------|
| none | | | | |

## Not verified

Anything believed but not run, with the reason.

- Manual cases: none; all 64 cases are pytest tests.
- Case-to-test mapping is by file and by the case id appearing in the tests; I did not map each id to a single test function.

## Eval run

Confirmed: each of the 16 eval test files run with `.venv/Scripts/python.exe -m pytest -q <file>`, all exit 0, 0 failures, log `verify-logs/evals.log`. All 64 case ids E1-E64 are referenced in `tests/`, and every implemented-as file exists.

| Non-functional | Target | Measured | Met |
|---|---|---|---|
| N1 suite time | under 120 s | 25.81 s (re-run, 501 passed, 1 skipped); verify run 27.7 s | met |
| N2 floor | at least 450 (CI `MIN_TESTS`) | 501 via `scripts/check_test_count.py` on a JUnit report, exit 0 | met |
| N3 run plus approve | under 10 s | 2.41 s per the T7 report (not re-measured); E18 subprocess test passes in 3.58 s including crash and resume | met (reported figure) |

## Known failures

None recorded (`wh.js known-failure list`).

## Comparison

Up from 239 tests at the previous change, `2026-10-01-increment-2-investigation-half`.

## Notes for the Ship document

None.

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

