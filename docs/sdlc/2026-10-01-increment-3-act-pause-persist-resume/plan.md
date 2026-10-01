# Plan: Increment 3, act, pause, persist, resume

Change id: `2026-10-01-increment-3-act-pause-persist-resume`
Spec: [spec.md](./spec.md)
Branch: `wh/2026-10-01-increment-3-act-pause-persist-resume`
Worktree: one per task under `.worktrees/`, folded back by rebase (profile `build.wave_merge`)

## Approach

Close the four deferred findings and lay the scaffolding first (wave 1: state, runbook and
registry, simulator ledger and scenario schema), then the executor and the four new nodes
(wave 2), the full graph and the scenario stories (wave 3), the CLI and the cross-process crash
proof (wave 4), then the documents and the CI floor, which needs the final count (wave 5).
Every task writes its failing tests first and runs the full suite before its commit; increment 2
assertions a requirement changes are updated deliberately and named in the commit body (D19,
D20). No task edits a protected path. T2, T6 and T8 touch sensitive or ask-first paths and say
so in their headings; the G2 approval of the brief (D1, D2, D3, D13, D23, D24) is the consent.

## Files

| Action | Path | Purpose |
|--------|------|---------|
| modify | `src/agentops/state.py` | New reasons, kinds, control fields, `ApprovalDecision`, `ExecutionRecord` fields |
| modify | `src/agentops/nodes/__init__.py`, `rca.py`, `escalate.py`, `merge.py`, `router.py`, `evidence.py` | R1 to R4, nonce fences, registry check, audit records |
| create | `src/agentops/actions/__init__.py`, `runbook.py`, `registry.py`, `preconditions.py`, `audit.py`, `executor.py` | The action layer (sensitive: tier-3 floor) |
| create | `runbooks/default.yaml` | The allowlist (sensitive) |
| create | `src/agentops/nodes/approval.py`, `execute.py`, `verify.py`, `postmortem.py` | The four new nodes and their edges |
| modify, create | `src/agentops/graph.py`, `cli.py`, `fakes.py`, `sources/scenario.py`, `sources/simulator.py`; new `__main__.py` | Wiring, commands, scripted answers, ledger file, combined worlds, `python -m agentops` |
| modify | `scenarios/*.json` (7 files) | Postmortems; scenario 6 also `rca_after_action` and the combined world |
| modify | `requirements.txt`, `requirements-dev.txt` | `langgraph-checkpoint-sqlite==3.1.1`, `aiosqlite==0.22.1`, `sqlite-vec==0.1.9` (D24), `pyyaml` moved to runtime (sensitive) |
| modify | `.github/workflows/ci.yml`, `.gitignore` | `MIN_TESTS`; `*.sqlite-*` for the WAL sidecars (D23) (both sensitive) |
| modify | `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `docs/ci.md`, `docs/sdlc/codebase-map.md` | R21 |
| create | `tests/test_deferred_findings.py`, `test_runbook.py`, `test_preconditions.py`, `test_audit.py`, `test_executor.py`, `test_approval.py`, `test_verify_postmortem.py`, `test_full_runs.py`, `test_cli_lifecycle.py` | New cases |
| modify | `tests/test_prompt_helpers.py`, `test_state.py`, `test_merge.py`, `test_router.py`, `test_rca_escalate.py`, `test_graph.py`, `test_scenario_runs.py`, `test_cli.py`, `test_scenarios.py`, `test_simulator.py`, `test_fakes.py`, `test_requirements.py`, `test_ci_workflow.py`, `test_repo_documents.py` | Updated assertions |

## Waves

| Wave | Tasks | Files (disjoint within the wave) |
|------|-------|----------------------------------|
| 1 | T1, T2, T3 | T1: state and nodes listed in T1; T2: `actions/` except executor, runbook, requirements; T3: sources, fakes, scenarios |
| 2 | T4, T5 | T4: `actions/executor.py`, `nodes/execute.py`; T5: `nodes/approval.py`, `verify.py`, `postmortem.py` |
| 3 | T6 | `graph.py`, `rca.py`, `escalate.py`, `cli.py` (minimal), run tests |
| 4 | T7 | `cli.py`, `__main__.py`, CLI tests |
| 5 | T8 | Documents, `ci.yml`, `.gitignore`, document tests |

Largest wave: 3 tasks (`build.max_parallel` is 4).

## Tasks

### Task T1: close the four deferred findings and extend the state

- Requirement(s): R1, R2, R3, R4, and the state additions every later task imports
- Files: `src/agentops/state.py`, `src/agentops/nodes/__init__.py`, `src/agentops/nodes/rca.py`,
  `src/agentops/nodes/escalate.py`, `src/agentops/nodes/merge.py`, `src/agentops/nodes/router.py`,
  `src/agentops/nodes/evidence.py`, `tests/test_deferred_findings.py` (new),
  `tests/test_prompt_helpers.py`, `tests/test_state.py`, `tests/test_merge.py`,
  `tests/test_router.py`, `tests/test_rca_escalate.py`, `tests/test_graph.py`,
  `tests/test_scenario_runs.py`
- Parallel: yes
- Steps:
  1. Write failing tests: `tests/test_deferred_findings.py` with E1 (dead sources end
     `source_outage`), E2 (deploys dead, 0.3 escalates and 0.1 reaches the end with
     `unreachable_sources`, prompts carry `Unreachable sources: deploys`), E3 (attempts at most
     12 in the dead run; the increment 2 path test in `test_scenario_runs.py` is re-asserted
     unchanged), E35 (metrics dead only); `tests/test_prompt_helpers.py` with E4 (nonce markers,
     distinct per prompt) and E48 (parametrised lookalikes and zero-width splits).
  2. Run them; confirm each fails on the missing reason, field, cap or neutralisation.
  3. `state.py`: add the reasons, kinds, `unreachable_sources`, `approval_error`,
     `ApprovalDecision`, the `ExecutionRecord` fields, `CONTROL_FIELDS` entries, `new_incident`
     defaults. `evidence.py`: `unreachable_sources(state)`. `router.py`: `affordable` reserves
     `NOMINAL_COST * READ_ATTEMPTS`. `merge.py`: write `unreachable_sources`; reason
     `source_outage` when no evidence and every attempt failed. `nodes/__init__.py`: format
     character strip, lookalike table, `fence_markers(nonce)`. `rca.py`: nonce fence, the
     `Unreachable sources:` and `Actions already applied:` lines (from evidence of kind
     `action`), `after_rca` outage rule writing `source_outage`. `escalate.py`: nonce fence.
  4. Update the existing prompt assertions in `test_graph.py`, `test_scenario_runs.py` and
     `test_rca_escalate.py` to match markers by pattern (D20); run the new tests green, then the
     full suite.
  5. Commit: `fix(nodes): report source outages, cap reads at 12 and fence evidence with a nonce`
- Done when: E1, E2, E3, E4, E35, E48 pass; the eight scenario path assertions from increment 2
  pass unchanged; `MODEL_OUTPUT_SCHEMAS` test shows no control field in any output schema.

### Task T2: runbook, registry, preconditions and audit log (sensitive: `runbooks/**`, `requirements*.txt`, tier-3 floor `actions/**`)

- Requirement(s): R6, R7, R8, R10, and the `idempotency_key` and `dry_run_description` helpers
- Files: `runbooks/default.yaml` (new), `src/agentops/actions/__init__.py`, `runbook.py`,
  `registry.py`, `preconditions.py`, `audit.py` (all new), `requirements.txt`,
  `requirements-dev.txt`, `tests/test_runbook.py`, `tests/test_preconditions.py`,
  `tests/test_audit.py` (new), `tests/test_requirements.py`
- Parallel: yes
- Steps:
  1. Edit the requirements: add `langgraph-checkpoint-sqlite==3.1.1  # MIT`,
     `aiosqlite==0.22.1  # MIT` and `sqlite-vec==0.1.9  # MIT or Apache-2.0` (D24) to
     `requirements.txt`, move `pyyaml==6.0.3  # MIT` there from `requirements-dev.txt`; run the
     profile `install`; update `tests/test_requirements.py` to assert all four.
  2. Write failing tests: `tests/test_runbook.py` with E6 (five names, every field, the
     scripted remediations resolve), E7 (`validate_params` and `has`), E36 (five bad files; also
     no `yaml.load(` under `src/`), E37 (four bad parameter sets, plus `to_version` `v1` with a
     trailing newline and a 65-character `fallback`), E52 (no `wrong_actions` name is
     registered); `tests/test_preconditions.py` with E8 (every runbook check passes on its
     scenario, six kinds exercised) and E38 (raising source, absent metric);
     `tests/test_audit.py` with E10 (append mode, size monotonic, parseable, `thread_id` on
     every line) and E50 (traversal ids refused before `open`); add E64 to
     `tests/test_preconditions.py` (five checks parametrised to fail, no exception).
  3. Run them; confirm each fails on the missing module or file.
  4. Write `runbooks/default.yaml` with the five entries in spec Interfaces; implement the
     models and loader (`yaml.safe_load` only), the registry with `re.fullmatch` on patterns and
     a 64-character cap on string parameters, `idempotency_key(incident_id, remediation)`
     (sha256 of the sorted-key JSON of action, target, params) and `dry_run_description(entry,
     remediation)`, the six precondition functions, and `AuditLog` (`append(incident_id,
     thread_id, record)`).
  5. Green, then the full suite.
  6. Commit: `feat(actions): typed runbook, registry, preconditions and append-only audit log`
- Done when: E6, E7, E8, E10, E36, E37, E38, E50, E52, E64 pass; `pyright` clean on `actions/`.

### Task T3: simulator ledger file, combined worlds, scripted postmortems

- Requirement(s): R20
- Files: `src/agentops/sources/scenario.py`, `src/agentops/sources/simulator.py`,
  `src/agentops/fakes.py`, `scenarios/clean_bad_deploy.json`, `deploy_red_herring_db.json`,
  `dependency_timeout_noisy_neighbour.json`, `late_partition_zero_rows.json`,
  `schema_drift_ingestion.json`, `two_concurrent_faults.json`, `log_injection_drop_table.json`,
  `tests/test_scenarios.py`, `tests/test_simulator.py`, `tests/test_fakes.py`
- Parallel: yes
- Steps:
  1. Write failing tests: `tests/test_scenarios.py` with E20 (postmortems present and citing
     signal ids, scenario 6's `rca_after_action` and combined world, a remediate scenario
     without a postmortem rejected); `tests/test_simulator.py` with E32 (ledger replay),
     E33 (combined world wins after the second action) and E41 (malformed ledger line);
     `tests/test_fakes.py` with E34 (marker answer).
  2. Run them; confirm each fails on the schema, the missing path argument or the queue.
  3. Extend `ScriptedOutputs` and the world-key validation; add `ledger_path` to
     `SimulatorSource` (append before return, replay at construction, refuse a bad line);
     add `answers_by_prompt_marker` to `ScriptedChatModel` and the postmortem queue to
     `scripted_model_for`; write the seven postmortems and scenario 6's second rca answer
     (`database_saturation` 0.8 top, `scale_db_pool` on `postgres` with `pool_size` 96, citing
     `metric-db_pool_utilisation` and `log-004`) and its `rollback_deploy+scale_db_pool` world
     (pool 0.55, error rate 0.005, p99 300 at 11:50 and 11:55).
  4. Green, then the full suite.
  5. Commit: `feat(sources): persisted simulator ledger, combined worlds and scripted postmortems`
- Done when: E20, E32, E33, E34, E41 pass; `test_hidden_scenario_sections_never_leave_the_source`
  still passes with the new sections.

### Task T4: the executor and the execute node (sensitive: tier-3 floor)

- Requirement(s): R9, R12 (executor half), R17
- Files: `src/agentops/actions/executor.py`, `src/agentops/nodes/execute.py`,
  `tests/test_executor.py` (new)
- Parallel: yes
- Steps:
  1. Write failing tests in `tests/test_executor.py`: E9 (order through a recording source,
     key equality), E12 (no approval record returns `refused`, nothing applied), E17 (failed
     precondition routes to the router, second failure escalates), E25 (`already_applied` from
     state and from the ledger), E39 (source refusal), E40 (unwritable audit directory raises
     before apply), E53 (key canonical order and per-incident), E54 (reject or edit-without-
     remediation approval is refused), E55 (tampered remediation refused because
     `approval.idempotency_key` differs from the computed key, D22; a raising adapter leaves an
     `apply` record naming the error).
  2. Run them; confirm each fails on the missing module.
  3. Implement the seven steps as named functions, `ExecutionOutcome`, the D22 key comparison
     in step 5, the `already_applied` path appending `apply` with `applied: false`, the step 7
     `apply` record written immediately after `apply` returns (`ok: false` when the adapter
     raises), the `crash_after_apply` switch read from `configurable` only (process exit after
     that record, before return), the execute node writing `execution`, evidence, actions and
     status, and `after_execute`.
  4. Green, then the full suite.
  5. Commit: `feat(actions): executor with idempotency key, ledger check and audit trail`
- Done when: E9, E12, E17, E25, E39, E40, E53, E54, E55 pass; the executor body is under 40 lines and calls
  the seven steps in order.

### Task T5: approval, verify and postmortem nodes (sensitive: tier-3 floor `nodes/approval.py`)

- Requirement(s): R11, R15 (rule and node), R16
- Files: `src/agentops/nodes/approval.py`, `src/agentops/nodes/verify.py`,
  `src/agentops/nodes/postmortem.py`, `tests/test_approval.py`,
  `tests/test_verify_postmortem.py` (new)
- Parallel: yes
- Steps:
  1. Write failing tests: `tests/test_approval.py` using a one-node graph with `InMemorySaver`:
     E11 (payload fields, approve as `on-call`, audit records), E23 (valid edit), E24 (edit
     citing an unknown id interrupts again, then approve executes the original), E46
     (`pool_size` 10000 and `force`), E51 (padded decision, extra key, a username as operator);
     `tests/test_verify_postmortem.py`: E16 (scripted postmortem resolves), E26 (no post-action
     signal), E27 (scenario 4 clears on logs, scenario 6 not cleared after rollback alone), E42
     (three bad postmortems fall back), E58 (`budget_exhausted` and `symptoms_persist`), E63
     (escalate and postmortem nonce fences); add E59 to `tests/test_approval.py` (reject with no
     reason, oversized reason, edit with no remediation).
  2. Run them; confirm each fails on the missing module.
  3. Implement the approval node per ADR 0001 (one `interrupt()`, validation, `approval_error`,
     an `approval` audit record for every resume value with `valid` and `error` (D21), `Approval`
     carrying the idempotency key (D22)), `after_approval`; the verify node with the threshold
     table and `after_verify`; the postmortem node with the nonce fence and the fallback. E24
     and E46 assert the `valid: false` record.
  4. Green, then the full suite.
  5. Commit: `feat(nodes): approval interrupt with re-validated edits, verify rule and postmortem`
- Done when: E11, E16, E23, E24, E26, E27, E42, E46, E51, E58, E59, E63 pass; the approval node calls
  `interrupt` exactly once (asserted by a counting patch in E11).

### Task T6: wire the full graph and prove the scenario stories (sensitive: `graph.py` tier-2 floor)

- Requirement(s): R12 (graph half), R13, R14, R15 (end to end), and the rca and escalate audit
  records of R10
- Files: `src/agentops/graph.py`, `src/agentops/nodes/rca.py`, `src/agentops/nodes/escalate.py`,
  `src/agentops/cli.py` (only: pass a `SqliteSaver` and thread id to `run`, print the pending
  proposal), `tests/test_full_runs.py` (new), `tests/test_graph.py`, `tests/test_scenario_runs.py`,
  `tests/test_cli.py`
- Parallel: no
- Steps:
  1. Write failing tests: `tests/test_full_runs.py` with E13 (scenario 8 `drop_table` blocked),
     E14 (`SqliteSaver` restart in process), E15 (scenario 6 loops and resolves), E22 (reject
     loop), E28 (seven scenarios resolve, scenario 7 escalates), E45 (edit to `drop_table`), E47
     (forged markers in a reject reason); `tests/test_graph.py` with E49 (edge set and no
     environment variable in `nodes/approval.py`, `nodes/execute.py`, `graph.py` or
     `actions/**`) and E56 (`build_graph` without a checkpointer
     raises); add E57 to `tests/test_full_runs.py` (audit file lines, order, never shrinks).
  2. Run them; confirm each fails on the missing nodes or edges.
  3. Make `build_graph(source, model, registry, audit, checkpointer)` wire every node and edge
     in spec Architecture; add the registry check and `proposal` record to rca and the
     `escalation` record to escalate; update the graph-invoking helpers in `test_graph.py`,
     `test_scenario_runs.py` and `test_cli.py` to pass `InMemorySaver` and a thread id and to
     stop at the first interrupt (D20); change the CLI report's last sentence and its test.
  4. Green, then the full suite.
  5. Commit: `feat(graph): approval, execute, verify and postmortem wired with checkpoints`
- Done when: E13, E14, E15, E22, E28, E45, E47, E49, E56, E57 pass; `grep -r InMemorySaver src/` is empty.

### Task T7: CLI commands and the cross-process crash proof

- Requirement(s): R18, R19, N3
- Files: `src/agentops/cli.py`, `src/agentops/__main__.py` (new), `tests/test_cli.py`,
  `tests/test_cli_lifecycle.py` (new)
- Parallel: no
- Steps:
  1. Write failing tests in `tests/test_cli_lifecycle.py`: E18 (three subprocesses through
     `python -m agentops` with a temporary state file and audit directory; one ledger entry; one
     `apply` record with `applied: true`; elapsed time under 10 s recorded as N3), E19 (`run`
     then `approve` through the Typer runner, the anthropic stub), E29 (`history` lines), E30
     (`fork` with a new score, original history intact), E31 (`resume` on a waiting thread),
     E43 (unknown thread on three commands), E44 (approve on a resolved thread), E60 (bad
     decision and unknown hypothesis exit 1), E61 (unusable state db exits 2), E62 (ANSI escapes
     never reach output).
  2. Run them; confirm each fails on the missing command.
  3. Implement `approve`, `resume`, `history`, `fork` and the shared options; check every thread
     id against `SIGNAL_ID_PATTERN` before opening any file (E43 adds `run --thread ../x`); read
     the scenario id from the checkpoint tuple; invoke with `durability="sync"`; the hidden
     `--crash-after-apply` passed only through `configurable`; `__main__.py`.
  4. Green, then the full suite.
  5. Commit: `feat(cli): approve, resume, history and fork over SQLite checkpoints`
- Done when: E18, E19, E29, E30, E31, E43, E44, E60, E61, E62 and N3 pass on Windows; every printed model
  string goes through `escape_for_terminal` (existing escape test extended to the new lines).

### Task T8: documents, the CI floor and the sidecar ignore (sensitive: `.github/workflows/ci.yml`, `.gitignore`)

- Requirement(s): R5, R21, N1, N2, and D23
- Files: `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `docs/ci.md`,
  `docs/sdlc/codebase-map.md`, `.github/workflows/ci.yml`, `.gitignore`,
  `tests/test_ci_workflow.py`, `tests/test_repo_documents.py`
- Parallel: no
- Steps:
  1. Write failing tests: `tests/test_ci_workflow.py` E5 (`MIN_TESTS` integer at least 240,
     same number in `docs/ci.md`); `tests/test_repo_documents.py` E21 (new sentences present,
     "arrives in increment 3" absent).
  2. Run them; confirm they fail on the old floor and wording.
  3. Run the full suite, note the passing count, set `MIN_TESTS` to the largest multiple of 10
     at or below 90 percent of it (D13); write the documents: README status and the five
     commands with the crash demo sequence, CLAUDE.md architecture lines 1, 4 and the demo
     command, CONTRIBUTING's scenario guide (postmortem, `rca_after_action`, combined worlds),
     docs/ci.md floor paragraph, codebase-map "What exists today" with `actions/`, the four
     nodes, `runbooks/`, the runtime files; add `*.sqlite-*` to `.gitignore` under `*.sqlite`
     (D23) and extend E21 to assert the line. Record N1 from the suite's duration line and N2
     from the floor script run locally on a JUnit report.
  4. Green, then the full suite.
  5. Commit: `docs: increment 3 commands, audit files and the CI floor`
- Done when: E5, E21 pass; N1 under 120 s and N2 at or above the floor are written into the
  task report with the command and the figure.

## Verification plan

Profile commands, in order: `typecheck`, `lint`, `format`, `test`, `security_audit` (covers the
three new runtime pins). Evals: every case above through `test_file` per new test file and the
full `test`; golden 21, edge 17, failure 14, adversarial 12, non-functional 3, all at 100
percent. Evidence the human sees: the five logs under `verify-logs/`, the count against the
floor, `git ls-files` showing no `audit/`, `*.sqlite`, `*.sqlite-*` or `.env*` file tracked,
and the Ship document's proof table. Not machine-run: nothing.

## Rollback

No environment has a deploy command (profile); the release is the owner's merge. Every
environment: revert the merge commit on a new branch through a pull request. No schema exists;
runtime files (`agentops.sqlite` and its sidecars, `audit/*.jsonl`) are local, gitignored and
may be deleted. A stale venv keeps the three reverted pins installed, which is harmless.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| LangGraph resumes differently from what the docs say (resume after a crash with `None`, interrupt index rules) | low | T6 and T7 slip | E14 and E18 are the first tests written in their tasks; the docs quotes are in ADR 0001 and 0002 |
| `sqlite-vec` 0.1.9 is a platform wheel and may be missing for some platform | low | install fails in CI | The resolver found the win_amd64 wheel (confirmed); a manylinux wheel for ubuntu-24.04 is believed, not verified, and the first CI run proves it |
| The nonce changes break more prompt assertions than listed | medium | T1 longer | Pattern-based helper in `tests/` shared by the updated tests |
| The verify thresholds disagree with a scenario's post-action numbers | low | E27 or E28 red | Numbers checked against every world in ADR 0003 before writing |
| Eight scenario files edited by hand introduce an id typo | medium | E20 red | The scenario validator catches unknown ids and the postmortem citation test runs per file |
