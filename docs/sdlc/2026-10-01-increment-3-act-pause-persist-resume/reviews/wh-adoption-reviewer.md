Verdict: 3 findings (0 critical, 0 high, 1 medium, 2 low)
Adoption score: 4

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| medium | README.md (exit codes paragraph) | No operator guidance for the failure outcomes: exit 2 (run fails or state file unusable), `apply_failed`, and an audit log that cannot be written (executor raises before apply). README only says what the codes mean (confirmed: read diff). No runbook entry exists; `docs/handover/` does not exist (confirmed) | On-call sees exit 2 or a thread stuck after a crash, does not know whether to run `resume`, delete `agentops.sqlite`, or inspect `audit/<id>.ledger.jsonl` first, and may delete the checkpoint file that holds the only record | README.md: add a short "When it fails" list per outcome: where to look (audit file, ledger, `history <thread>`), whether `resume` is safe, and that `agentops.sqlite` must not be deleted |
| low | README.md, CONTRIBUTING.md:72 | Adding or changing an allowlisted action in `runbooks/default.yaml` is not described anywhere a reader finds from the README (only a one-line mention that names come from the file). The file is security-sensitive per CLAUDE.md:60 | Engineer adds an action to the YAML without the registry parameter validation or precondition, and does not know which tests (`tests/test_runbook.py`) pin the schema | CONTRIBUTING.md: add "Adding a runbook action" pointing to `src/agentops/actions/runbook.py`, `preconditions.py` and `tests/test_runbook.py` |
| low | README.md | `--operator` option on `approve` (roles operator, on-call, reviewer) and `--state-db`, `--audit-dir` defaults are not in the command table | Engineer cannot tell which role is allowed to approve without reading `nodes/approval.py` | README.md: one line listing `--operator` values and the three path options with defaults |

Findings outside scope: none
Not verified: Ran `PYTHONPATH=src python -m agentops --help`: failed with `No module named 'typer'` (dependencies not installed in this environment), so documented commands were not executed; `agentops` entry point confirmed only by reading `src/agentops/cli.py` (run, approve, resume, history, fork all defined) and `__main__.py`. Confirmed by reading: every new module under `actions/` and the four new nodes carries a module docstring; five ADRs added and linked from the spec; spec observability section matches the CLI report and audit log description (not checked line by line against code). Tests not run.

## Re-check (after 228bbbc..285d25e)

- medium, README "When it fails": closed. confirmed: `git show b0b6ebe` adds a "When it fails" section covering exit 2 (run failure and unusable state file), audit write failure before and after apply, `apply_failed`, and a refused `approve`; it says not to delete `agentops.sqlite` and when `resume` is safe. The audit-after-apply recovery claim is backed by commit 285d25e's new test in tests/test_executor.py (believed, not run).
- low, runbook action how-to: closed. confirmed: CONTRIBUTING.md "Adding a runbook action" names runbook.py, preconditions.py, tests/test_runbook.py and tests/test_preconditions.py.
- low, `--operator` and path options: closed. confirmed: README now lists `--state-db`, `--audit-dir`, `--scenarios-dir` defaults and the `--operator` roles.
- No new blockers from the code fixes (read stat only; docs are the only user-facing change). Tests and commands not run (typer missing earlier; not retried).

Final adoption score: 4 (no open high or medium findings).
