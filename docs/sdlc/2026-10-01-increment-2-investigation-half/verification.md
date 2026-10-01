# Verification: increment 2 investigation half

Change id: `2026-10-01-increment-2-investigation-half`
Status: green
Run at: 2026-10-01T06:42:13.544Z UTC
Commit: `ad3be8ecf295`

Status is green only when every defined check exited 0 and every eval category met its target.
"No check defined" rows do not count as passes; they are listed so the gap is visible.

## What was measured

<!-- wh:verify-start -->
<!-- written by `wh.js verify`; edit outside the markers -->
- Type check: 0 errors (`.venv/Scripts/python.exe -m pyright`).
- Lint: 0 errors (`.venv/Scripts/python.exe -m ruff check .`).
- Test suite: 239 passed, 0 failed, 1 skipped (`.venv/Scripts/python.exe -m pytest -q`).
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
| golden | 18 | 18 | 100% | met |
| edge | 8 | 8 | 100% | met |
| failure | 9 | 9 | 100% | met |
| adversarial | 3 | 3 | 100% | met |

Eval run (confirmed): `.venv/Scripts/python.exe -m pytest -v` over the 12 eval test files, exit 0, 216 passed, 1 skipped in 14.29 s, log `verify-logs/evals.log`. All 38 cases (E1-E38) have their named test present with a PASSED line; parametrised cases passed for every parameter. The one skip is E12 for `ambiguous_two_causes`, an escalate scenario the case text excludes. E29 now names the retry and hand-off tests and both passed. The second fix round (router prefixes, Triage distinct-name validator, escalate failed-attempt actions) is covered by the passing router, state and rca_escalate tests.

Non-functional (confirmed): N1 eval run 14.29 s, full suite 16.4 s (summary.json), target under 60 s, met. N2 full suite 239 passed, target at least 80, met.

Comparison: up from 76 tests at the previous change, `2026-10-01-increment-1-scaffold`, to 239 passed.

## Known failures

None. `known-failure list` reported no failures recorded.

## Failures and fixes

Two review fix rounds (058d641 to ad3be8e). Checks re-ran green at ad3be8e and all evals were re-run in full: no failures remain.

## Not verified

Anything believed but not run, with the reason.

- Manual cases: none in evals.md.
- Not applicable checks: build, e2e, screenshot have no command in the profile.

## Notes for the Ship document

- Table counts in the E-rows (e.g. E35 'existing, extended') were not re-derived beyond the named-test PASSED check.

