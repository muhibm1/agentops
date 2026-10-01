Verdict: 3 findings (0 critical, 0 high, 2 medium, 1 low)

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| medium | tests/test_executor.py:494 | Only the audit-failure-before-apply path is tested; the failure of the `apply` record write after a successful source apply is untested (confirmed by reading the test names; no test fails the audit on the `apply` event) | Source apply succeeds, `write_apply_record` raises (disk full), process dies; on resume the ledger fallback in `find_recorded_execution` is the only guard against a second rollback, and no test proves it | tests/test_executor.py: add a test with an AuditLog subclass that raises when `record["event"] == "apply"`; run `execute`, expect the raise and `len(source.ledger()) == 1`; rerun with a normal AuditLog and assert `kind == "already_applied"` and ledger length still 1 |
| medium | tests/test_verify_postmortem.py:119-215 | `judge` clears on `latest_value` only and on `p.at > opened_at`; neither the latest-point rule nor the boundary is tested (confirmed: no test with an unhealthy earlier post-action point followed by a healthy one, or a point exactly at `opened_at`) | A series whose last point is healthy but whose earlier post-action point breached a threshold, or a point stamped exactly at `opened_at` that a `>=` regression would admit, flips verdict to cleared and ships a postmortem for a live incident, with no test red | tests/test_verify_postmortem.py: add two cases built on a copied scenario: (a) points [unhealthy, healthy] expect the documented ADR 0003 verdict; (b) a lone point at `opened_at` expect `cleared is False` and no `verify-` evidence |
| low | tests/test_executor.py:278 | `test_executor_body_is_short_and_names_the_seven_steps_in_order` asserts source structure, not behaviour (confirmed by name) | A harmless refactor of `execute` (extracting a step) turns it red, while a behavioural reorder that keeps the names passes; the ordering is already covered by `test_executor_runs_the_seven_steps_in_order_and_applies` | tests/test_executor.py: delete this test, or keep it only if the ordering assertion in line 212 does not already use a call-order recorder |

Findings outside scope: none
Not verified: I did not run `pytest` or the floor script (`scripts/check_test_count.py`); I read test names and the executor, approval, verify and audit sources only. test_preconditions.py, test_runbook.py and test_simulator.py were not read, so admit and deny pairing there is believed, not verified.

## Re-check (f2dfd3a..decc79b)

- Medium 1 (apply-record write failure untested): closed, confirmed by reading the diff. tests/test_executor.py adds `FailingApplyAudit` (raises when `record["event"] == "apply"`) and `test_a_failed_apply_record_write_is_recovered_on_rerun_without_a_second_apply` asserting `kind == "already_applied"`; tests/test_cli_lifecycle.py adds the same case at CLI level. A companion test covers no duplicate recovered record when the audit already shows the apply.
- Medium 2 (latest-point rule and opened_at boundary untested): closed, confirmed by reading the diff. tests/test_verify_postmortem.py adds `test_only_the_latest_point_of_a_series_decides_the_verdict` and `test_a_point_or_line_exactly_at_the_opening_is_not_a_post_action_signal`. Test bodies not read line by line, so assertion strength is believed adequate, not verified.
- Low (structure test): unchanged, not required.
- New findings in changed tests: none.
- Not verified: pytest was not run.
