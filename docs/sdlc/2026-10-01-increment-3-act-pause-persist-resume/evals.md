# Evals: Increment 3, act, pause, persist, resume

Change id: `2026-10-01-increment-3-act-pause-persist-resume`
Spec: [spec.md](./spec.md)

Evals turn "does it work" into evidence. Every case below is a pytest test a machine in this
repository runs with the scripted model and no API key; none is manual. Targets are numbers.

## Targets

| Category | Target | Rationale |
|----------|--------|-----------|
| Golden | 100% pass | One case per requirement; the outcome depends on each |
| Edge | 100% pass | Loops, repeats, empty signals, restarts, forks |
| Failure | 100% correct handling | Bad runbooks, dead sources, refusals, unwritable files, bad threads |
| Adversarial | 100% rejected | Injected actions, smuggled edits, forged fences, bypass attempts |
| Non-functional | see rows | Suite time, floor, demo latency |

## Cases

| ID | Category | Given | When | Then | Maps to requirement | Implemented as |
|----|----------|-------|------|------|---------------------|----------------|
| E1 | golden | A source that raises on every call for logs, metrics and deploys, scenario 1's model | The graph runs | Status `escalated`, reason `source_outage`, evidence empty, every read action `ok=False` | R1 | tests/test_deferred_findings.py |
| E2 | golden | Deploys raise on every call, logs and metrics answer; one rca output re-scores `recent_deploy_regression` 0.3, another 0.1 | The graph runs with each | 0.3 escalates with `source_outage`; 0.1 reaches the interrupt with `deploys` in `unreachable_sources` and in the payload; both rca prompts contain `Unreachable sources: deploys` | R2 | tests/test_deferred_findings.py |
| E3 | golden | Every source raises every time; also the eight scripted scenarios | The graph runs | Read attempts recorded are at most 12 in the dead run; the increment 2 loop and budget path assertions still pass unchanged | R3 | tests/test_deferred_findings.py |
| E4 | golden | Two rca prompts for the same state | Both are rendered | Each prompt holds exactly one opening and one closing marker matching the nonce pattern; the nonces differ; the system prompt names the same nonce | R4 | tests/test_prompt_helpers.py |
| E5 | golden | The workflow file | The workflow test parses it | `MIN_TESTS` is an integer at least 240 and `docs/ci.md` states the same number | R5 | tests/test_ci_workflow.py |
| E6 | golden | `runbooks/default.yaml` | It is loaded | Exactly the five names, each with a risk, preconditions, rollback declaration and `idempotent: true`; every scenario's scripted remediation names a registry action except scenario 7's | R6 | tests/test_runbook.py |
| E7 | golden | The registry | `validate_params` is called with scenario 2's `pool_size` 96, scenario 5's partition, scenario 3's fallback and scenario 8's empty params | All pass; `has` answers true for the five names and false for `drop_table` and `restart_service` | R7 | tests/test_runbook.py |
| E8 | golden | Each remediate scenario's source after a normal investigation | Its action's preconditions are evaluated | Every check passes with an observed value and a sentence; the six check kinds are each exercised at least once across the scenarios | R8 | tests/test_preconditions.py |
| E9 | golden | A recording source wrapper around scenario 2 and an approved state | The executor runs | Calls happen in the order registry, params, preconditions, key, ledger, dry-run audit, apply, apply audit; outcome `applied`; `execution.applied_at` set; key equals the sha256 of the canonical form computed independently in the test | R9 | tests/test_executor.py |
| E10 | golden | A temporary audit directory | Three records are appended for one incident, with the file size read between writes | One file, three parseable lines, size strictly increases, the file is opened with append mode (asserted through a patched `open`) | R10 | tests/test_audit.py |
| E11 | golden | A one-node graph around `approval` with `InMemorySaver`, scenario 1's proposal | Invoked, then resumed with `approve` as `on-call` | The interrupt payload holds the seven fields; after resume `approval.decision` is `approve`, `operator` `on-call`, status `remediating`, the edge returns `execute`, audit holds `proposal` and `approval` records | R11 | tests/test_approval.py |
| E12 | golden | The compiled graph and a state with no approval | The edge set is inspected; the executor runs on that state | The only edge into `execute` leaves `approval`; the executor returns `refused` with nothing applied | R12 | tests/test_executor.py |
| E13 | golden | Scenario 8 with a model scripted to propose `drop_table` on `search_index_old` with valid citations | The graph runs | Ends `escalated`, reason `action_not_allowed`, no interrupt raised, source ledger empty, one `escalation` audit record naming `drop_table` | R13 | tests/test_full_runs.py |
| E14 | golden | Scenario 1 on a `SqliteSaver` over a temporary file | Run to the interrupt, the graph object discarded, a new graph and saver built on the same file, resumed with `approve` | Ends `resolved` with one ledger entry; `InMemorySaver` appears in no file under `src/` | R14 | tests/test_full_runs.py |
| E15 | golden | Scenario 6 | Run, approve, and approve again when the second proposal arrives | First action `rollback_deploy`, verify round 1 not cleared (pool 0.97), second proposal `scale_db_pool`, verify round 2 cleared, status `resolved`, two ledger entries, loops counted | R15 | tests/test_full_runs.py |
| E16 | golden | A verified state for scenario 1 and its scripted postmortem | The postmortem node runs | `postmortem` set, every cited id in state, status `resolved` | R16 | tests/test_verify_postmortem.py |
| E17 | golden | Scenario 2's source with the pool series ending at 0.4, an approved `scale_db_pool` | The executor runs, then runs again with the same state | First: `precondition_failed`, evidence of kind `precondition`, nothing applied, edge to the router; second: `escalate` with `precondition_failed` | R17 | tests/test_executor.py |
| E18 | golden | A fresh temporary state file and audit directory | Subprocess 1 runs scenario 1; subprocess 2 approves with `--crash-after-apply`; subprocess 3 resumes | Subprocess 2 exits non-zero with the ledger holding one entry; subprocess 3 exits 0 with status `resolved`; the ledger still holds one entry; exactly one `apply` audit record has `applied: true`; the total elapsed time is recorded for N3 | R18 | tests/test_cli_lifecycle.py |
| E19 | golden | The Typer runner | `run` scenario 1, then `approve` with `--decision approve` | `run` exits 0 and prints the thread id and the proposal; `approve` exits 0 and prints `status: resolved`; `--model anthropic` exits 1 with the stub sentence | R19 | tests/test_cli_lifecycle.py |
| E20 | golden | The eight scenario files | Validated | Seven carry a `postmortem` citing signal ids; scenario 6 carries `rca_after_action` naming `scale_db_pool` and a combined world `rollback_deploy+scale_db_pool`; a remediate scenario with the postmortem removed is rejected naming the field | R20 | tests/test_scenarios.py |
| E21 | golden | The five documents | The repo-document test reads them | README names the five commands and the audit file; CLAUDE.md no longer says approval "arrives in increment 3"; docs/ci.md states the floor; the codebase map lists `actions/` | R21 | tests/test_repo_documents.py |
| E22 | edge | Scenario 1 at the interrupt | Resumed with `reject`, reason `rollback window too risky right now` | Evidence gains one item of kind `operator` holding the reason, `loops` is one higher, rca runs again with the reason inside its fence, a second interrupt arrives with a proposal | R11 | tests/test_full_runs.py |
| E23 | edge | Scenario 2 at the interrupt | Resumed with `edit` setting `pool_size` to 112 | `approval.decision` `edit`, `edited_remediation.params.pool_size` 112, `remediation` replaced, execution applies with 112 in the ledger | R11 | tests/test_approval.py |
| E24 | edge | Scenario 2 at the interrupt | Resumed with `edit` citing an evidence id not in state | A second interrupt whose payload holds `error` naming the id; `approval` still empty; nothing applied; resuming that with `approve` executes the original | R11 | tests/test_approval.py |
| E25 | edge | A state whose `execution` already holds the key, and separately a source whose ledger holds it | The executor runs | Outcome `already_applied` in both; `apply` is never called; the audit `apply` record says `applied: false` | R9 | tests/test_executor.py |
| E26 | edge | A source with no points or lines after the incident opened | `verify` runs | Not cleared, evidence holds a `verify` record with no signals, the edge routes to the router while the last execution applied and the budget is above zero | R15 | tests/test_verify_postmortem.py |
| E27 | edge | Scenario 4's source after `rollback_deploy` was applied | `verify` runs | Cleared on the single INFO line with no metrics; scenario 6 after `rollback_deploy` alone is not cleared naming `db_pool_utilisation` and `error_rate` | R15 | tests/test_verify_postmortem.py |
| E28 | edge | Scenarios 1, 2, 3, 4, 5, 6 and 8 | Each run and approved until terminal | Each ends `resolved` with a postmortem citing ids in state; scenario 7 still ends `escalated` with `ambiguous` and no ledger entry | R15 | tests/test_full_runs.py |
| E29 | edge | A thread run to the interrupt | `history <thread>` | One line per checkpoint in reverse order, each with id, next node, status, loops, top hypothesis and evidence count; the latest shows next `approval` | R19 | tests/test_cli_lifecycle.py |
| E30 | edge | Scenario 2's thread and the checkpoint whose next node is the router | `fork --at <id> --set database_saturation=0.9` | Exit 0, the fork reaches an interrupt or escalation, its state holds the new score, and `history` of the original thread still lists the original checkpoints | R19 | tests/test_cli_lifecycle.py |
| E31 | edge | A thread waiting at the interrupt | `resume <thread>` | Exit 0, prints the pending proposal and a sentence to use `approve`; nothing applied | R19 | tests/test_cli_lifecycle.py |
| E32 | edge | A ledger file with one applied `rollback_deploy` for scenario 1 | A new `SimulatorSource` is built on it | Its metrics include the post-action points, its ledger lists the entry, and applying the same key reports `applied: false` | R20 | tests/test_simulator.py |
| E33 | edge | Scenario 6's source | `rollback_deploy` then `scale_db_pool` are applied | After the first, the latest pool point is 0.97; after the second, the combined world's points are the latest (pool 0.55, error rate 0.005, p99 300) | R20 | tests/test_simulator.py |
| E34 | edge | Scenario 6's scripted model | Asked for `RcaOutput` with a prompt lacking, then containing, `Actions already applied:` | The first answer is `rollback_deploy`, the second `scale_db_pool`; a scenario without `rca_after_action` keeps answering its queue | R20 | tests/test_fakes.py |
| E35 | edge | Logs and deploys answer, metrics raise every time | The graph runs | Reaches the interrupt; `unreachable_sources` is `["metrics"]`; attempts at most 12; reason is not `no_evidence` | R1 | tests/test_deferred_findings.py |
| E36 | failure | Runbook files with an unknown key, a duplicate name, `min` above `max`, a non-list root, and a missing `risk` | Each is loaded | Each raises naming the entry index and field; the registry is never built | R6 | tests/test_runbook.py |
| E37 | failure | The registry | `pool_size` 10000, `pool_size` "big", an undeclared `force` key, and `pause_batch_job` with a parameter | Each rejected naming the parameter; the sentence contains no model free text | R7 | tests/test_runbook.py |
| E38 | failure | A source whose `get_metrics` raises and one whose metric is absent | `metric_above` is evaluated | Both results `passed: false`, observed null, detail names the error type or "no points"; no exception escapes | R8 | tests/test_preconditions.py |
| E39 | failure | Scenario 1's source and an approved `pause_batch_job` (no world declared) | The executor runs | Outcome `apply_failed`, `execution.applied` false, evidence of kind `action` with `applied: false`, edge routes to `escalate` with `apply_failed`, ledger empty | R9 | tests/test_executor.py |
| E40 | failure | An audit directory that is a file, not a directory | The executor runs an approved action | Raises before `apply` is called (asserted by the recording source); the ledger stays empty | R10 | tests/test_executor.py |
| E41 | failure | A ledger file with a malformed second line | A `SimulatorSource` is built on it | Raises naming line 2; nothing is served | R20 | tests/test_simulator.py |
| E42 | failure | A verified state and a model whose three postmortems cite an unknown id | The postmortem node runs | Three rejected attempts recorded, then a code-built postmortem with the root cause label; status `resolved` | R16 | tests/test_verify_postmortem.py |
| E43 | failure | An unknown thread id | `resume`, `approve` and `history` | Each exits 1 with a sentence naming the thread; no file is created under the audit directory | R19 | tests/test_cli_lifecycle.py |
| E44 | failure | A thread already `resolved` | `approve --decision approve` | Exit 1, "not waiting for approval"; the ledger is unchanged | R19 | tests/test_cli_lifecycle.py |
| E45 | adversarial | Scenario 8 at its interrupt | Resumed with `edit` whose remediation names `drop_table` | A second interrupt with `error` naming the action; `approval` empty; ledger empty; audit holds no `apply` record | R11 | tests/test_full_runs.py |
| E46 | adversarial | Scenario 2 at the interrupt | Resumed with `edit` setting `pool_size` to 10000 and adding `force: true` | A second interrupt with `error` naming `pool_size` and `force`; nothing applied | R11 | tests/test_approval.py |
| E47 | adversarial | Scenario 1 at the interrupt | Rejected with a reason containing a forged closing marker, a fullwidth run and a zero-width-split run | The operator evidence payload holds no run of three brackets of any listed kind; the next rca prompt still holds exactly one opening and one closing marker | R4 | tests/test_full_runs.py |
| E48 | adversarial | Each lookalike in ADR 0005's table, a run split by U+200B, and a run split by U+200D, parametrised | `neutralise_delimiters` | Every output is the spaced ASCII form; plain text and keys without runs are unchanged | R4 | tests/test_prompt_helpers.py |
| E49 | adversarial | The source tree | `approval.py`, `execute.py`, `executor.py`, `graph.py` and `cli.py` are read; `build_graph` is introspected | None references `os.environ`, `getenv` or `dotenv`; `build_graph` has no parameter whose name contains `approval`, `skip` or `auto`; the graph has no edge from `rca`, `merge` or `verify` into `execute` | R12 | tests/test_graph.py |
| E50 | adversarial | Incident ids `../x`, `a/b`, `C:\\x` and an empty string | `AuditLog.append` and the simulator ledger path builder | Each raises before any file is opened (asserted by a patched `open`) | R10 | tests/test_audit.py |
| E51 | adversarial | Scenario 1 at the interrupt | Resumed with `decision: "approve "`, then with `decision: "approve", "status": "resolved"` (an extra key), then with `operator: "muhibm1"` | Each produces a second interrupt with `error`; none writes `approval`; the state's `status` is unchanged | R11 | tests/test_approval.py |
| E52 | adversarial | Every scenario's `wrong_actions` | Checked against the registry | None is a registry name; `drop_table`, `restart_service` and `flush_cache` are all absent | R6 | tests/test_runbook.py |
| E53 | edge | Two remediations for one incident whose params differ only in key order, and the same remediation under two incident ids | The key is computed for each | The first pair gives one identical key and the second pair gives two different keys, each equal to the independently computed sha256 | R9 | tests/test_executor.py |
| E54 | adversarial | A state whose `approval` holds `decision: reject`, and one whose `approval` holds `edit` with no edited remediation | The executor runs on each | Each returns `refused`; `apply` is never called; the ledger is empty; the audit holds no `apply` record | R12 | tests/test_executor.py |
| E55 | adversarial | An approved state tampered after the checkpoint so the remediation names `drop_table`, and another with `pool_size` 10000 | The executor runs on each | Each returns `refused` (registry) or a parameter rejection naming `pool_size`; `apply` is never called; the ledger is empty | R9 | tests/test_executor.py |
| E56 | failure | `build_graph` called with no checkpointer, and with `None` | It is called | It raises naming `checkpointer`; no graph object is returned | R14 | tests/test_graph.py |
| E57 | edge | Scenario 6 run, approved twice, to `resolved` with an audit directory | The audit file is read after each node | Every line parses and holds `event`, `at`, `incident_id`; the file size never decreases; events appear in the order `proposal`, `approval`, `precondition`, `dry_run`, `apply`, `verify` per round, ending with a `verify` record that cleared | R10 | tests/test_full_runs.py |
| E58 | failure | A verified-not-cleared state with budget zero, and one whose last execution did not apply anything | `after_verify` and the node run | The first escalates with `budget_exhausted`, the second with `symptoms_persist`; each writes an `escalation` audit record and no further apply happens | R15 | tests/test_verify_postmortem.py |
| E59 | adversarial | Scenario 1 at the interrupt | Resumed with `reject` and no reason, `reject` with a 1001-character reason, and `edit` with no remediation | Each produces a second interrupt with `error`; none writes `approval`, adds evidence or changes `loops`; the payload holds no echo of the oversized text | R11 | tests/test_approval.py |
| E60 | failure | A thread at the interrupt | `approve --decision maybe`, and `fork` with `--set unknown_hypothesis=0.9` | Each exits 1 with one sentence naming the bad value; nothing applied; the original history is unchanged | R19 | tests/test_cli_lifecycle.py |
| E61 | failure | A `--state-db` path that is a directory | `run` and `approve` | Each exits 2 with a sentence naming the path; no ledger entry and no audit file is created | R19 | tests/test_cli_lifecycle.py |
| E62 | adversarial | Scenario 8 whose log lines and a reject reason carry ANSI escape bytes and markup brackets | `run`, `approve` with reject, and `history` are run through the Typer runner | No raw escape byte appears on stdout or stderr in any command's output | R19 | tests/test_cli_lifecycle.py |
| E63 | edge | The escalate prompt and the postmortem prompt for a state with a forged marker in an evidence line | Both are rendered twice | Each holds exactly one opening and one closing nonce marker, the nonces differ across renders and across the two prompts | R4 | tests/test_verify_postmortem.py |
| E64 | failure | Sources shaped to fail `deployment_within_minutes` (deploy 200 minutes old), `previous_version_available`, `dependency_not_healthy` (dependency healthy), `config_true` (flag false) and `metric_below` (rows written 5), parametrised | Each check is evaluated | Each result is `passed: false` with the observed value and a sentence; no exception escapes | R8 | tests/test_preconditions.py |

## Non-functional

| ID | Measure | Target | How measured |
|----|---------|--------|--------------|
| N1 | Full suite wall time | Under 120 s | `pytest -q` duration line in `verify-logs/test.log` |
| N2 | Passing tests against the floor | At or above `MIN_TESTS`, floor at least 240 | `scripts/check_test_count.py` on the JUnit report in CI; locally the count from the test log |
| N3 | `run` plus `approve` of scenario 1 through the CLI | Under 10 s together | Elapsed time asserted in E18's subprocess test |

## Failure taxonomy

| Class | Description | Detection | Example |
|-------|-------------|-----------|---------|
| wrong result | The graph applies the wrong action, or clears symptoms that remain | `verify` audit record disagrees with the post-action world; scenario run tests | Scenario 6 reported resolved after `rollback_deploy` alone |
| missing result | A run ends without a hand-off, a postmortem or a proposal | Status `failed` or exit 2 in the CLI; E42 fallback | Three bad postmortems end the run |
| slow | The suite or the demo exceeds its budget | N1, N3 | A verify window read that scans every point of every series twice |
| leaked | Grading data, a secret or an operator identity reaches a prompt, a checkpoint or the audit log | The hidden-section test in `test_simulator.py`, the role literal on `operator` | A username written into `Approval.operator` |
| unauthorised | Something executes without an approval record or outside the registry | E12, E13, E45, E49; the only edge into `execute` | An edit that smuggles `drop_table` past validation |
| corrupted | The audit log or ledger is rewritten, truncated or holds an unparseable line | E10 size monotonic, E41 | A write in `w` mode |
| unrecoverable | A killed process cannot resume, or resumes and applies twice | E14, E18 | Ledger lost with the process |
