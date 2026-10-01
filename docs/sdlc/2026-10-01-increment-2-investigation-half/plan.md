# Plan: Increment 2, the investigation half of the graph

Change id: `2026-10-01-increment-2-investigation-half`
Spec: [spec.md](./spec.md)
Branch: `wh/2026-10-01-increment-2-investigation-half`
Worktree: one per task under `.worktrees/`, assigned by the conductor

## Approach

Wave 1 changes the shared shapes every later task imports (state, scenario schema, scripted
model) and, in parallel, the two sensitive files the request authorises (CI runner, profile).
Wave 2 builds the three node groups and the six scenario files as four file-disjoint tasks;
each is unit-tested against crafted states, not the graph. Wave 3 wires the topology and runs
the eight scenarios end to end, the one task that can prove R10. Wave 4 finishes the CLI and the
documents, which read the final state shape. Every task writes its failing tests first, runs the
profile `lint`, `format`, `typecheck` and `test` commands before committing, and names the eval
cases it implements. Increment 1 tests whose behaviour the spec changes are replaced in the
task that owns the file (brief D16); no test is edited to make it pass. The injected line in
scenario 8 is file content; builders must not put that phrase on a shell command line.

## Files

| Action | Path | Purpose |
|--------|------|---------|
| modify | `src/agentops/state.py` (tier-2 floor) | `Action.attempt`, `RcaOutput` re-scored and nullable pair, `Escalation`, `EscalationReason`, two state fields, `CONTROL_FIELDS`; remove `NoEvidenceError` |
| modify | `src/agentops/sources/scenario.py`, `src/agentops/fakes.py` | `ScriptedOutputs.escalate`; `scripted_model_for(scenario, attempts)` per-schema queues, docstring (low 5) |
| modify | `scenarios/clean_bad_deploy.json`, `scenarios/deploy_red_herring_db.json` | Re-scored hypotheses in the scripted rca |
| create | `scenarios/dependency_timeout_noisy_neighbour.json`, `schema_drift_ingestion.json`, `late_partition_zero_rows.json`, `two_concurrent_faults.json`, `ambiguous_two_causes.json`, `log_injection_drop_table.json` | Scenarios 3 to 8 per the spec's Data table |
| create | `src/agentops/nodes/router.py`, `src/agentops/nodes/merge.py`, `src/agentops/nodes/escalate.py` | Router functions, merge and decision, escalate node |
| modify | `src/agentops/nodes/evidence.py`, `src/agentops/nodes/rca.py`, `src/agentops/nodes/__init__.py` | Three nodes with in-node retry; re-scoring and ambiguity; key-collision fix (low 2) |
| modify | `src/agentops/graph.py` (tier-2 floor) | The increment 2 topology |
| modify | `src/agentops/cli.py` | Report lines, stub message, error-path escaping (low 1), recursion limit |
| modify | `.github/workflows/ci.yml` (sensitive), `.workhorse/profile.yml` (sensitive) | Runner pin and floor; release note and confirmed labels |
| modify | `README.md`, `CLAUDE.md`, `docs/sdlc/codebase-map.md`, `docs/ci.md` | Eight scenarios, outcomes, stub model, the floor value (low 4) |
| create | `tests/test_router.py`, `tests/test_merge.py`, `tests/test_evidence_nodes.py`, `tests/test_rca_escalate.py`, `tests/test_prompt_helpers.py`, `tests/test_scenario_runs.py` | New cases per evals.md |
| modify | `tests/conftest.py`, `tests/test_state.py`, `tests/test_fakes.py`, `tests/test_scenarios.py`, `tests/test_graph.py`, `tests/test_cli.py`, `tests/test_ci_workflow.py`, `tests/test_repo_documents.py` | Extended or replaced cases |

Not touched: `runbooks/**` (does not exist; increment 3), `requirements*.txt`, `pyproject.toml`,
`langgraph.json`, `.env*`. No new dependency.

## Waves

| Wave | Tasks | Files (disjoint within the wave) |
|------|-------|----------------------------------|
| 1 | T1, T2 | T1: state, scenario schema, fakes, scenarios 1 and 2, their tests. T2: ci.yml, profile, workflow test |
| 2 | T3, T4, T5 | T3: evidence, router, merge and their tests. T4: rca, escalate, nodes `__init__` and their tests. T5: scenarios 3 to 8, scenario test |
| 3 | T6 | graph, conftest, test_graph, test_scenario_runs (scenario files only if a path mismatch shows) |
| 4 | T7 | cli, README, CLAUDE.md, codebase map, docs/ci.md, test_cli, test_repo_documents |

`build.max_parallel` is 4; the largest wave has 3 tasks.

## Tasks

### Task 1: State shapes, scenario schema and the scripted model builder (tier-2 floor: src/agentops/state.py)

- Requirement(s): R5 (schema), R7 (schema), R8, R9 (schema and scenarios 1, 2), R13 (docstring, builder)
- Files: `src/agentops/state.py`, `src/agentops/sources/scenario.py`, `src/agentops/fakes.py`, `scenarios/clean_bad_deploy.json`, `scenarios/deploy_red_herring_db.json`, `tests/test_state.py`, `tests/test_fakes.py`
- Parallel: yes
- Steps:
  1. Write failing tests in `tests/test_state.py`: E23 `test_root_cause_and_remediation_are_both_present_or_both_null`; E24 extend `test_control_fields_absent_from_every_model_output_schema` to cover `Escalation` and `escalation_reason`, and the smuggling test to all three schemas. In `tests/test_fakes.py` add a test that `scripted_model_for(scenario, attempts=3)` queues three answers per schema and an `Escalation` queue only when the scenario has one. Run; confirm failures are `AttributeError` or `ImportError` on the missing names.
  2. In `state.py`: `Action.attempt` (int, default 1, at least 1); `RcaOutput` with `hypotheses: list[Hypothesis]` (at least one), `root_cause: RCA | None`, `remediation: Remediation | None`, a model validator rejecting a half-null pair; `Escalation(summary, evidence_ids default empty)`; `EscalationReason` literal; `IncidentState.escalation`, `IncidentState.escalation_reason`; add `escalation_reason` to `CONTROL_FIELDS`; `MODEL_OUTPUT_SCHEMAS` gains `Escalation`; `new_incident` sets both new fields to None; delete `NoEvidenceError`.
  3. In `scenario.py`: `ScriptedOutputs.escalate: dict | None = None`. In `fakes.py`: `scripted_model_for(scenario, attempts=3)` with per-schema queues (`Triage`, `RcaOutput`, and `Escalation` when present); the class docstring explains `script` (one shared queue) against `script_by_schema` (one queue per requested schema, so a retried node meets its own answer again). Import `MODEL_NODE_RETRY` is not possible before T6 compiles; use a module constant `DEFAULT_ATTEMPTS = 3` and let T6 assert it equals the retry policy's `max_attempts`.
  4. Update the two scenario files: `scripted_outputs.rca.hypotheses` with re-scored entries citing the ids the rca already cites (S1: `bad_deploy` family 0.9 and `database_saturation` 0.1; S2: `database_saturation` 0.9, `recent_deploy_regression` 0.1).
  5. Run the two test files green, then lint, format, typecheck and the full suite. Expect `tests/test_graph.py` and `tests/test_cli.py` failures that reference `NoEvidenceError` or the old rca shape; list them in the commit body as replaced by T6 and T7. Everything else green.
  6. Commit: `feat(state): re-scored rca output, escalation schema and per-schema scripted model`
- Done when: E23 and E24 pass; `scripted_model_for` is the only scripted-model builder; both scenario files validate with hypotheses in their rca.

### Task 2: CI runner pin, test floor and the profile's release note (sensitive: .github/workflows/ci.yml, .workhorse/profile.yml)

- Requirement(s): R14, R15, N2
- Files: `.github/workflows/ci.yml`, `.workhorse/profile.yml`, `tests/test_ci_workflow.py`
- Parallel: yes
- Steps:
  1. Extend `tests/test_ci_workflow.py::test_workflow_runs_the_gates_in_order_with_a_read_only_token` (E16): assert `job["runs-on"] == "ubuntu-24.04"`, that no `runs-on` string ends with `-latest`, and `MIN_TESTS == "80"`. Run; confirm it fails on the runner value.
  2. Edit `ci.yml`: `runs-on: ubuntu-24.04`, `MIN_TESTS: "80"`. Nothing else changes; the pin and gate-order assertions stay.
  3. Edit `profile.yml`: in the `environments` comment and in `notes`, state that merging the pull request to `main` is the release and the owner performs it; no agent deploys (brief D12 also flips the stack labels from "(believed)" to confirmed on 2026-10-01). N2 is the floor itself.
  4. Run the test file green, then lint, format, typecheck and the full suite (all green; this task changes no Python).
  5. Commit: `ci: pin the runner to ubuntu-24.04 and raise the test floor to 80`
- Done when: E16 passes and `node ~/WorkHorse/scripts/wh.js status` still parses the profile.

### Task 3: Evidence nodes, router and merge

- Requirement(s): R2, R3, R4
- Files: `src/agentops/nodes/evidence.py`, `src/agentops/nodes/router.py`, `src/agentops/nodes/merge.py`, `tests/test_router.py`, `tests/test_merge.py`, `tests/test_evidence_nodes.py`
- Parallel: yes
- Steps:
  1. Write failing tests: `tests/test_router.py` E2, E3, E19, E36 (budget prefix at 1, 2 and 3 reads left); `tests/test_merge.py` E6, E7, E22; `tests/test_evidence_nodes.py` E5, E20. Build states with `new_incident` plus crafted `hypotheses`, `evidence` and `actions`; use a small recording source class in the test file. Confirm `ImportError` on the new modules.
  2. `router.py`: the keyword table, `OPEN_HYPOTHESIS_MIN_SCORE = 0.2`, `NOMINAL_COST`, `score_sources`, `rounds_asked(state, source)`, `sources_to_ask(state)`, `affordable(state, sources)`, `route_to_sources(state)` returning a list of `Send` or the string `merge`.
  3. `evidence.py`: `read_with_one_retry`, `logs`, `metrics`, `deploys`, `window_for(state)`, `METRIC_CATALOGUE`, `SOURCE_READ_TOOLS`; keep `as_evidence` and the delimiter neutralising.
  4. `merge.py`: `source_read_attempts(state)`, `decide_next_step(state)` returning `rca`, `escalate` or the Send list, `merge(state)` writing the clamped budget and `loops`, and `after_merge(state)` for the edge. Functions under 40 lines; the table in the spec is the docstring.
  5. Run the three test files green, then lint, format, typecheck, full suite (the T1 note on pending graph tests applies).
  6. Commit: `feat(nodes): parallel evidence nodes, deterministic router and budget-owning merge`
- Done when: E2, E3, E5, E6, E7, E19, E20, E22, E36 pass and `decide_next_step` has no dependence on `loops`.

### Task 4: RCA re-scoring, ambiguity, escalate and the key-collision fix

- Requirement(s): R5, R6, R7, R13 (collision)
- Files: `src/agentops/nodes/rca.py`, `src/agentops/nodes/escalate.py`, `src/agentops/nodes/__init__.py`, `tests/test_rca_escalate.py`, `tests/test_prompt_helpers.py`
- Parallel: yes
- Steps:
  1. Write failing tests: `tests/test_rca_escalate.py` E29, E34 (the ambiguous node returns `root_cause` None and `remediation` None, brief D18), E37 (margin boundary, single hypothesis, null root cause), E38 (hypothesis evidence citation plus the four consistency mismatches, brief D17: dropped, added and renamed names, and a label that is not the top-scored name) (call the node functions directly with a `ScriptedChatModel` and crafted state); `tests/test_prompt_helpers.py` E21. Confirm they fail on missing names or the dropped key.
  2. `rca.py`: validate citations for every hypothesis and for the pair; `RcaConsistencyError(CitationError)` with a message naming the offending hypothesis or label; `check_consistent_with_triage(output, state)` raising it when the re-scored names differ as a set from `state["hypotheses"]` names or when `root_cause.label` is present and is not the highest-scored name (the existing `MODEL_NODE_RETRY` retries `CitationError`, confirmed in `graph.py`, so no graph change); `AMBIGUITY_MARGIN = 0.15`; `is_ambiguous(output)`; return `hypotheses` always, and `root_cause` and `remediation` from the output only when the result is clear, else both None with status `investigating`; status `awaiting_approval` when clear; `after_rca(state)` returning `escalate` or `END`.
  3. `escalate.py`: a system prompt that asks for a short summary of the competing hypotheses and the evidence for each, the fenced evidence block reused from `rca.render_evidence`, `Escalation` output, citations checked, `escalation_reason` from `decide_next_step` and `is_ambiguous` (import from `merge` and `rca`), status `escalated`.
  4. `nodes/__init__.py`: on a colliding neutralised key, keep the later entry under the key plus ` (2)`, ` (3)` and so on.
  5. Run the two test files green, then lint, format, typecheck, full suite.
  6. Commit: `feat(nodes): rca re-scores with an ambiguity rule, escalate node, key-collision fix`
- Done when: E21, E29, E34, E37, E38 pass; `rca.py` and `escalate.py` share one evidence renderer; no state written by `rca` holds a remediation when `after_rca` returns `escalate`.

### Task 5: Scenarios 3 to 8

- Requirement(s): R9
- Files: the six new files under `scenarios/`, `tests/test_scenarios.py`
- Parallel: yes
- Steps:
  1. Extend `tests/test_scenarios.py` (E18): the id list assertion names the eight ids; the scripted-outputs test validates `rca` as the new `RcaOutput`, `escalate` as `Escalation` when present, and checks every cited id (hypotheses included) is a signal id. Confirm it fails on the missing files.
  2. Author the six files to the spec's Data table: service, two or three symptoms, `incident_time`, signals with ids in the `log-NNN`, `metric-<name>`, `deploy-NNN`, `dep-<name>` style, `ground_truth`, `required_evidence` reachable on the stated path, `wrong_actions`, `expected_outcome`, `post_action_world` for the correct action (scenario 6: one world per action, rollback clears latency only), and `scripted_outputs` whose rca cites only ids the path collects. Scenario 7 has `escalate`. Scenario 8's injected line sits in a log message inside the first hour.
  3. Run the test file green, then lint, format, typecheck, full suite.
  4. Commit: `feat(scenarios): scenarios 3 to 8 with scripted outputs`
- Done when: E18 passes over eight files and no file contains a real hostname, email or key.

### Task 6: Graph topology and the end-to-end scenario runs (tier-2 floor: src/agentops/graph.py)

- Requirement(s): R1, R3, R4, R6, R7, R10, R11, N1
- Files: `src/agentops/graph.py`, `tests/conftest.py`, `tests/test_graph.py`, `tests/test_scenario_runs.py`, and any `scenarios/*.json` only to correct a path mismatch found here
- Parallel: no
- Steps:
  1. `tests/conftest.py`: `SCENARIO_IDS` from `list_scenario_ids`; `scripted_model` fixture uses `scripted_model_for(scenario, attempts=3)`. Write failing tests in `tests/test_graph.py`: E1, E4, E8, E25, E26 (replaces the flaky-source test), E27 (replaces the empty-evidence test), keep E28 and extend E35 to the escalate prompt. Write `tests/test_scenario_runs.py`: E9, E10, E11, E12, E13, E33. Confirm failures on the old topology.
  2. `graph.py`: nodes `triage`, `logs`, `metrics`, `deploys`, `merge`, `rca`, `escalate`; conditional edges `route_to_sources` from triage (path map logs, metrics, deploys, merge), `after_merge` from merge (rca, escalate, logs, metrics, deploys), `after_rca` from rca (escalate, END); plain edges from each evidence node to merge and from escalate to END; `MODEL_NODE_RETRY` on triage, rca, escalate; assert `fakes.DEFAULT_ATTEMPTS == MODEL_NODE_RETRY.max_attempts` in a test.
  3. Run the two test files; where a scenario's path differs from the spec table, fix the scenario file (names, window, missing signal), never the rule, and note it in the commit body.
  4. Run lint, format, typecheck and the full suite green; record the suite wall time for N1 in the commit body.
  5. Commit: `feat(graph): fan-out router, merge loop and escalation terminal`
- Done when: E1, E4, E8, E9 to E13, E25 to E28, E33, E35 pass; suite under 60 s.

### Task 7: CLI report and the documents

- Requirement(s): R12, R13 (codebase map), R16
- Files: `src/agentops/cli.py`, `README.md`, `CLAUDE.md`, `docs/sdlc/codebase-map.md`, `docs/ci.md`, `tests/test_cli.py`, `tests/test_repo_documents.py`
- Parallel: no
- Steps:
  1. Write failing tests in `tests/test_cli.py`: E14, E15, E30; extend E32's usage test for the stub sentence; keep E31. Extend `tests/test_repo_documents.py` for E17. Confirm failures.
  2. `cli.py`: delete the local scripted-model helper and use `scripted_model_for`; invoke with `config={"recursion_limit": 60}`; report adds `loops`, `budget remaining: n of 12`, the escalation reason and escaped summary; `fail()` escapes its sentence; the stub message says the live model is not wired yet; exit 0 on both terminal statuses.
  3. Documents: README status paragraph for increment 2 with the eight scenario ids and the two outcomes; CLAUDE.md commands and architecture lines; codebase map "what exists today" rows for the new modules and `scripted_model_for`; `docs/ci.md` floor value 80.
  4. Run the two test files green, then lint, format, typecheck and the full suite.
  5. Commit: `feat(cli): report loops, budget and escalation; documents for increment 2`
- Done when: E14, E15, E17, E30, E31, E32 pass and `agentops run --scenario ambiguous_two_causes --model scripted` exits 0 from the repository root.

## Verification plan

The verifier runs the profile commands `lint`, `format`, `typecheck`, `test` and
`security_audit` on the change branch and reads their exit codes; `build`, `e2e` and
`screenshot` are empty and reported as not defined. Evals: 38 cases, all implemented as pytest
tests named above, counted by category (golden 18, edge 8, failure 9, adversarial 3) plus N1
from the pytest wall time and N2 from the passed count. The human sees the Ship document's
proof table, the eval counts and the first green Actions run on the pull request, which no
agent can observe (ship D-S2 from increment 1 still applies).

## Rollback

No environment has a deploy command (profile `environments`); the release is the owner's merge.
Undo is `git revert -m 1 <merge commit>` on a new branch through a new pull request. No schema,
no data, no migration. Rehearsal at G5 is a dry statement of that command.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| A scenario's path differs from the spec table once the real router runs it | medium | T6 reworks a scenario file | T6 owns scenario corrections; the rule stays fixed; the commit body records the change |
| Send fan-in triggers `merge` more than once per round | low | double budget charge | E4 asserts four reads and budget 8; `Send` and fan-in confirmed in the installed langgraph 1.2.12 source on 2026-10-01 |
| Wave 2 tasks fail the full suite on tests T6 will replace | high | noise, not a defect | T1 lists the pending tests; builders report them as expected failures, not fix them |
| A builder puts the scenario 8 phrase on a shell command line | low | a hook prompt stalls the run | The approach paragraph forbids it; the phrase is file content only |
| Test floor 80 fails CI if tests are fewer than expected | low | red CI | The plan adds about 35 tests to 76; T7 checks the local count before commit |
