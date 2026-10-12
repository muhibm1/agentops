Verdict: 4 findings (0 critical, 0 high, 1 medium, 3 low)

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| medium | src/agentops/cli.py (`fork`, `opened_thread`, `ThreadRun.next_fork`) | A fork reuses the original incident id, so it opens the original's simulator ledger and audit file (believed, not verified: no test forks a thread whose action was already applied) | `run`, `approve` applies an action, then `fork --at <checkpoint before approval>`: the fork's simulator replays the original ledger, serves the post-action world, and `execute` returns `already_applied` with nothing applied. The fork then explores a counterfactual on top of the real change. | src/agentops/cli.py: build the fork's `SimulatorSource` with a ledger path for the fork thread id (empty), or state in the docs and a test that forks share the ledger. Add a test forking after an apply. |
| low | src/agentops/cli.py `opened_thread` | `values["scenario_id"]` and `values["incident_id"]` are read with `[]` from the latest checkpoint (believed, not verified) | A thread whose newest checkpoint holds no channel values (for example a run killed before the first node checkpoint) -> `KeyError` traceback instead of one sentence and exit 1 or 2, against the module's stated contract. | src/agentops/cli.py: use `.get`, and `fail(USAGE_ERROR, "Thread X has no usable checkpoint")` when either is missing. |
| low | src/agentops/sources/simulator.py `_replay`/`parse_ledger_line` | A partial last ledger line makes replay raise `LedgerError` | Process killed mid-write of a ledger line -> every later `resume`, `approve`, `history` on that incident exits 2 with "ledger could not be used" until someone edits the file by hand. | src/agentops/sources/simulator.py: tolerate one unterminated final line (no trailing newline) by ignoring it with a warning, and test it. |
| low | src/agentops/nodes/verify.py `read_post_action_signals` | Window end is fixed at `opened_at + VERIFY_WINDOW` (30 minutes) (believed, not verified against a live source) | A real source where the apply happens more than 30 minutes after the incident opened -> no post-action points fall in the window, `has_signal` stays false, verify never clears and ends in `symptoms_persist`. | src/agentops/nodes/verify.py: end the window at now or `execution.applied_at + VERIFY_WINDOW`. |

Findings outside scope: none
Not verified: ruff, mypy and pytest were not run (review of the diff only). rca.py, escalate.py, postmortem.py, state.py and the tests were not read.

## Re-check (228bbbc..285d25e)

- Medium, fork shares the original ledger: closed (confirmed by reading the diff; tests not run). `ThreadRun.start` now takes the thread id and `ledger_path_for(audit_dir, thread_id)`; `next_fork` calls `start` with `<thread>-fork-<n>`, so a fork gets its own empty ledger. `test_a_fork_after_an_apply_has_its_own_empty_ledger_and_applies_for_itself` covers the scenario I described.
- Low, `KeyError` on missing channel values: closed for `scenario_id` (confirmed): `.get` plus a one-sentence `USAGE_ERROR`. `incident_id` is no longer read.
- Low, partial ledger line and fixed verify window: not changed by the fix, still open (low, out of re-check scope).
- New findings in changed code: none. Residual note (believed, not verified): a thread created before this fix whose id differs from its incident id would now look for a ledger under the thread id; the CLI only creates threads with id equal to incident id, so no impact expected.
- Not verified: ruff, mypy, pytest not run.
