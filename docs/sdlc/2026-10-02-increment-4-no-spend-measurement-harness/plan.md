# Plan: Increment 4, the no-spend half

Change id: `2026-10-02-increment-4-no-spend-measurement-harness`
Spec: [spec.md](./spec.md)
Branch: `wh/2026-10-02-increment-4-no-spend-measurement-harness`
Worktree: the conductor's worktree for this change (one per builder in a wave)

## Approach

Wave 1 closes increment 3's items that need no new dependency and lays the pure pieces of the
harness (scripted baseline model and scripts, evaluators, tracing status), four file-disjoint
tasks. Wave 2 adds the pieces that depend on them: the runbook window and job-name fixes (which
also split `graph.py`), the baseline agent with the `langchain` pin, and the dataset files (whose
checksums must be computed after wave 1's scenario edits). Wave 3 wires the CLI commands and the
dev server. Wave 4 writes the documents and raises the CI floor once the count is known. Every
task writes its failing test first, runs the profile's `test_file`, implements, then runs the
full suite with `lint`, `format`, `typecheck`. Commits are conventional. Tasks that touch a
sensitive path say so in their heading; no task touches a protected path or a harness-guarded
filename. The builder never runs `agentops eval` from a shell with `--held-out` or
`--model anthropic`; tests exercise those through `CliRunner`, and no test scores the committed
frozen split: the `--held-out` tests use a temporary scenarios directory with its own manifest
(D18).

## Files

| Action | Path | Purpose |
|--------|------|---------|
| modify | `src/agentops/state.py` | `ExecutionRecord.applied: bool \| None`; `EscalationReason` gains `effect_unknown` |
| modify | `src/agentops/actions/executor.py` | `apply_unknown` outcome; `effect` on apply records |
| modify | `src/agentops/nodes/execute.py`, `nodes/verify.py`, `nodes/rca.py`, `nodes/postmortem.py` | Route and describe the unknown effect; verify reads the runbook window |
| modify | `src/agentops/sources/simulator.py` | Torn-line tolerant replay; fresh-line append; `torn_ledger_lines` |
| modify | `src/agentops/fakes.py` | `bind_tools`, `messages` queue, `scripted_baseline_model_for` |
| modify | `src/agentops/sources/scenario.py` | `ScriptedOutputs.baseline`, `BaselineScript`, `BaselineStep` |
| modify | `scenarios/*.json` (8 files) | `scripted_outputs.baseline` per scenario |
| create | `src/agentops/evals/__init__.py`, `outcome.py`, `evaluators.py`, `judge.py` | Outcome shape, seven evaluators, the judge |
| create | `src/agentops/tracing.py` | `tracing_status` |
| modify | `src/agentops/actions/runbook.py`, `actions/preconditions.py` | `verify_window_minutes`; `target_named_in_logs` |
| modify | `runbooks/default.yaml` | Two patterns, one precondition (ask-first) |
| modify | `src/agentops/graph.py` | `build_state_graph`; verify gets the registry |
| create | `src/agentops/baseline.py` | ReAct baseline on `create_agent` |
| modify | `requirements.txt` | `langchain==1.4.3  # MIT` (ask-first) |
| create | `src/agentops/evals/dataset.py`, `evals/held_out/manifest.json`, `evals/checksums.sha256` | Split and pins (the two data files are ask-first) |
| create | `src/agentops/evals/run.py`, `src/agentops/evals/commands.py` | Runner, table, `eval` and `dataset` commands |
| modify | `src/agentops/cli.py` | Register the commands; unknown-effect sentence; torn-ledger notice |
| create | `src/agentops/dev_server.py`, `langgraph.json` | Factory for `langgraph dev` (`langgraph.json` is ask-first) |
| modify | `requirements-dev.txt` | `langgraph-cli[inmem]==0.4.32  # MIT` (ask-first) |
| modify | `README.md`, `CONTRIBUTING.md`, `docs/ci.md`, `docs/sdlc/codebase-map.md` | Operator and contributor documentation |
| modify | `.github/workflows/ci.yml` | `MIN_TESTS` (ask-first) |
| create | `tests/test_evaluators.py`, `test_tracing.py`, `test_baseline.py`, `test_dataset.py`, `test_eval_cli.py`, `test_langgraph_json.py` | New suites |
| modify | `tests/test_executor.py`, `test_verify_postmortem.py`, `test_rca_escalate.py`, `test_state.py`, `test_simulator.py`, `test_fakes.py`, `test_scenarios.py`, `test_runbook.py`, `test_preconditions.py`, `test_graph.py`, `test_requirements.py`, `test_cli.py`, `test_cli_lifecycle.py`, `test_repo_documents.py`, `test_ci_workflow.py` | Added cases |

## Waves

| Wave | Tasks | Files (disjoint within the wave) |
|------|-------|----------------------------------|
| 1 | T1, T2, T3, T4 | T1: state, executor, execute, verify, rca, postmortem + their tests. T2: simulator + test. T3: fakes, scenario.py, 8 scenario files + tests. T4: `evals/{__init__,outcome,evaluators,judge}.py`, `tracing.py` + tests |
| 2 | T5, T6, T7 | T5: runbook.py, preconditions.py, verify.py, graph.py, `runbooks/default.yaml` + tests. T6: baseline.py, requirements.txt, test_baseline, test_requirements. T7: `evals/dataset.py`, the two data files, test_dataset |
| 3 | T8, T9 | T8: `evals/run.py`, `evals/commands.py`, cli.py + test_eval_cli, test_cli, test_cli_lifecycle. T9: dev_server.py, langgraph.json, requirements-dev.txt, test_langgraph_json, test_requirements, README (one paragraph), spec.md Compliance labels |
| 4 | T10 | README, CONTRIBUTING, docs/ci.md, codebase map, ci.yml, test_repo_documents, test_ci_workflow |

Largest wave: 4 tasks (`build.max_parallel` is 4).

## Tasks

### Task 1: Record an apply that raised as an unknown effect (sensitive: `src/agentops/actions/**`, `state.py`)

- Requirement(s): R1, R2, R3 (rca, postmortem), R7
- Files: `src/agentops/state.py`, `src/agentops/actions/executor.py`, `src/agentops/nodes/execute.py`, `src/agentops/nodes/verify.py`, `src/agentops/nodes/rca.py`, `src/agentops/nodes/postmortem.py`, `tests/test_executor.py`, `tests/test_verify_postmortem.py`, `tests/test_rca_escalate.py`, `tests/test_state.py`
- Parallel: yes
- Steps:
  1. Write failing tests: E1 and A1 in `tests/test_executor.py` (a `SimulatorSource` subclass whose `apply` calls `super().apply` then raises, and one that raises first); E6 in `tests/test_state.py`; E2, E3, E5 in `tests/test_verify_postmortem.py`; E4 in `tests/test_rca_escalate.py`.
  2. Run `test_file` on each; confirm they fail on the missing `apply_unknown` kind, the `bool` field and the missing prompt lines.
  3. `state.py`: `applied: bool | None`, add `effect_unknown`. `executor.py`: `OutcomeKind` gains `apply_unknown`; `apply_and_audit` catches, writes the record (R1 fields), returns `unknown_outcome(...)`; `write_apply_record` gains `effect`. `execute.py`: `VERIFIABLE_RESULTS` gains `apply_unknown`. `verify.py` `next_step_fields`: `applied is None` and not cleared returns `effect_unknown`. `rca.py`: `possibly_applied_action_names` and the prompt line. `postmortem.py`: `(effect unknown)` descriptions.
  4. F1: add `test_a_crash_between_apply_and_its_record_resumes_as_possibly_applied` to `tests/test_executor.py` (an `AuditLog` subclass that raises once on the `apply` event, then a fresh graph over the same ledger and checkpointer resumes); if the decc79b test already covers this, rename it to this name and strengthen the assertions rather than duplicating it. Confirm F2 and E7 (existing tests named in evals.md) still pass unchanged.
  4a. Added by the evals review: E45 and E47 in `tests/test_executor.py` (E45: execute the same remediation twice after an applies-then-raises source, assert `already_applied`, one ledger entry, one `apply` call; E47: assert `effect` is `applied`, `not_applied`, `unknown` on the three audit records).
  5. Green on the four files, then full suite, `lint`, `format`, `typecheck` (pyright will list every `applied` reader; fix each).
  6. Commit: `fix(executor): record an apply that raised as an unknown effect and let verify judge it`
- Done when: E1 to E6, E45, E47, F1, F2, E7, A1 pass; `grep -n "applied=False, ok=False" src/agentops/actions/executor.py` finds nothing; the suite is green.

### Task 2: Recover from a torn last ledger line

- Requirement(s): R4 (source half)
- Files: `src/agentops/sources/simulator.py`, `tests/test_simulator.py`
- Parallel: yes
- Steps:
  1. Write failing tests E8, E9, F3, F10 in `tests/test_simulator.py` (files written with `tmp_path`, the fragment without a trailing newline; F10 puts the fragment on line 1 before a valid line 2 and expects `LedgerError`, so `_replay` skips a non-JSON line only when it is the last one).
  2. Run; confirm E8 fails with `LedgerError` on line 2 and E9 on the missing attribute.
  3. `_replay`: split lines, `json.loads` first; a `JSONDecodeError` on the last line appends its number to `self.torn_ledger_lines` and replay completes; a `JSONDecodeError` on any earlier line raises `LedgerError` naming the line (D17: only a torn final line can come from a crash); otherwise `AppliedAction.model_validate` as today. `append_ledger_line`: when the file exists and is non-empty, read its last byte in binary and prefix `\n` if it is not a newline.
  4. Green, full suite, lint, format, typecheck.
  5. Commit: `fix(simulator): skip a torn final ledger line and start the next entry on a fresh line`
- Done when: E8, E9, F3, F10 pass and the existing simulator ledger tests are unchanged and green.

### Task 3: Scripted baseline model and per-scenario baseline scripts

- Requirement(s): R9
- Files: `src/agentops/fakes.py`, `src/agentops/sources/scenario.py`, `scenarios/ambiguous_two_causes.json`, `scenarios/clean_bad_deploy.json`, `scenarios/dependency_timeout_noisy_neighbour.json`, `scenarios/deploy_red_herring_db.json`, `scenarios/late_partition_zero_rows.json`, `scenarios/log_injection_drop_table.json`, `scenarios/schema_drift_ingestion.json`, `scenarios/two_concurrent_faults.json`, `tests/test_fakes.py`, `tests/test_scenarios.py`
- Parallel: yes
- Steps:
  1. Write failing tests E11, E10 in `tests/test_fakes.py`; E12, A2 in `tests/test_scenarios.py`. The six tool names are a constant `BASELINE_TOOL_NAMES` in `scenario.py` so this task needs nothing from task 6.
  2. Run; confirm failures on the missing `bind_tools`, `messages`, `baseline` field.
  3. `scenario.py`: `BaselineStep(tool: Literal[the six], args: dict)`, `BaselineScript(steps: list[BaselineStep] = min 1, answer: dict)`, `ScriptedOutputs.baseline: BaselineScript | None = None`, and a validator that every `apply` step has `action` and `target` in `args`. `fakes.py`: `messages: SkipValidation[list[AIMessage]]`, drained by `_generate` before `script`; `bind_tools` returns `self` and records names in `bound_tools`; `scripted_baseline_model_for(scenario)` builds one `AIMessage(content="", tool_calls=[{"name", "args", "id": f"call-{n}"}])` per step and the answer as JSON text.
  4. Author the eight scripts per ADR 0005 (three to six steps each; args use the scenario's service, window and signal names; the answer has `root_cause`, and `action`, `target`, `params` when it acts). Trap scenarios take the bait as the ADR states.
  5. Green, full suite, lint, format (JSON files are not formatted by ruff), typecheck.
  6. Commit: `feat(fakes): scripted tool-calling model and a baseline script in every scenario`
- Done when: E10, E11, E12, A2 pass; `agentops run` on every scenario is unchanged (existing full-run tests green).

### Task 4: Evaluators, outcome shape, judge and tracing status

- Requirement(s): R10, R11, R15, R17 (import scan)
- Files: `src/agentops/evals/__init__.py`, `src/agentops/evals/outcome.py`, `src/agentops/evals/evaluators.py`, `src/agentops/evals/judge.py`, `src/agentops/tracing.py`, `tests/test_evaluators.py`, `tests/test_tracing.py`
- Parallel: yes
- Steps:
  1. Write failing tests E13 to E19, F4, A3 in `tests/test_evaluators.py`; E20, F5, E48, A4 in `tests/test_tracing.py` (E48: every falsy value of the tracing variable with a key present is off with no problem; A4 walks `src/agentops/**/*.py` with `ast` and collects import roots).
  2. Run; confirm import errors.
  3. `outcome.py`: `RunOutcome` (spec Interfaces), `inputs_for`, `reference_for`, `outcome_from_state(state, evidence_ids_before_proposal, started, finished)`. `evaluators.py`: the seven functions and `DETERMINISTIC_EVALUATORS` tuple. `judge.py`: `JUDGE_RUBRIC`, `explanation_quality(..., *, model)` using `fence_markers` and `new_fence_nonce` from `agentops.nodes`. `tracing.py`: `TracingStatus`, `tracing_status(environ: Mapping[str, str] = os.environ)` with two module constants `TRUTHY = {"1", "true", "yes", "on"}` and `FALSY = {"", "0", "false", "no", "off"}` compared casefolded; a falsy value is treated as unset (R15).
  4. Green, full suite, lint, format, typecheck.
  5. Commit: `feat(evals): deterministic evaluators, outcome shape, judge rubric and tracing status`
- Done when: E13 to E20, E48, F4, F5, A3, A4 pass; no module under `src/agentops` imports `langsmith`, `langchain_anthropic` or `anthropic`.

### Task 5: Verify window from the runbook, narrowed job names, `build_state_graph` (sensitive: `runbooks/**`, `src/agentops/actions/**`, `graph.py`)

- Requirement(s): R5, R6, R16 (graph half)
- Files: `src/agentops/actions/runbook.py`, `src/agentops/actions/preconditions.py`, `src/agentops/nodes/verify.py`, `src/agentops/graph.py`, `runbooks/default.yaml`, `tests/test_runbook.py`, `tests/test_preconditions.py`, `tests/test_verify_postmortem.py`, `tests/test_graph.py`
- Parallel: yes
- Steps:
  1. Write failing tests E21, E24, A9 in `tests/test_runbook.py` (A9: newline, case, trailing space, Cyrillic and non-breaking-hyphen variants all rejected, so the patterns must be full matches); E23, E25 in `tests/test_preconditions.py`; E22 in `tests/test_verify_postmortem.py` (a source subclass recording the windows it is asked for); E27 in `tests/test_graph.py`.
  2. Run; confirm failures on the unknown field, the old patterns and the missing function.
  3. `runbook.py`: `DEFAULT_VERIFY_WINDOW_MINUTES` from `VERIFY_WINDOW`; `verify_window_minutes` field; `TargetNamedInLogs` in the union. `preconditions.py`: `evaluate` passes `timedelta(minutes=entry.verify_window_minutes)`; `target_named_in_logs`. `verify.py`: `verify(..., registry)` and `read_post_action_signals(state, source, window_length)`. `graph.py`: `build_state_graph` returns the `StateGraph`; `build_graph` calls it and compiles; verify node gets `registry=registry`. `runbooks/default.yaml`: the two patterns of R6 and `- { check: target_named_in_logs }` on both job actions; a comment on `verify_window_minutes` (default 30).
  4. Green, then full suite (E26 is the existing parametrised full-run test), lint, format, typecheck.
  5. Commit: `fix(runbook): per-action verify window, job-name conventions and a target-in-logs precondition`
- Done when: E21 to E27 and A9 pass and `tests/test_full_runs.py` is green over all eight scenarios.

### Task 6: The ReAct baseline on `create_agent` (sensitive: `requirements.txt`)

- Requirement(s): R8, R18 (runtime pin)
- Files: `src/agentops/baseline.py`, `requirements.txt`, `tests/test_baseline.py`, `tests/test_requirements.py`
- Parallel: yes
- Steps:
  1. Add `langchain==1.4.3  # MIT` to `requirements.txt` and run the profile `install` command (confirmed to resolve on this machine by `pip install --dry-run`, 2026-10-02). Write failing tests E31 in `tests/test_requirements.py`, then E28, E29, E30, F6, A11 in `tests/test_baseline.py` using `scripted_baseline_model_for` (A11: a minimal recording `EvidenceSource` that is not a `SimulatorSource` is refused with `TypeError` by both `baseline_tools` and `run_baseline` before any call).
  2. Run; confirm import error on `agentops.baseline`.
  3. `baseline.py`: `BaselineTrace`, `BaselineAnswer`, `BaselineRun`, `baseline_tools(...)`, `BASELINE_PROMPT`, `run_baseline(scenario, model, *, incident_id)`; both entry points start with an `isinstance(source, SimulatorSource)` check raising `TypeError` naming `SimulatorSource` (D19), and the module docstring says why. Prove first that `create_agent` accepts the scripted model (believed in the spec): if it rejects `bind_tools` or needs a profile, record what it needed in the module docstring and in the ship notes.
  4. Green, full suite, lint, format, typecheck.
  5. Commit: `feat(baseline): ReAct baseline over the same evidence and apply tools with no approval or allowlist`
- Done when: E28 to E31, F6, A11 pass; `pip_audit -r requirements.txt` (the profile's `security_audit`) exits 0.

### Task 7: Dataset split, manifest and checksums (sensitive: `evals/checksums*`, `**/held_out/**`)

- Requirement(s): R12
- Files: `src/agentops/evals/dataset.py`, `evals/held_out/manifest.json`, `evals/checksums.sha256`, `tests/test_dataset.py`
- Parallel: yes
- Steps:
  1. Write failing tests E32, E33, E34, F7, A5 in `tests/test_dataset.py` (temporary copies for the mutating cases; E33 reads the committed files).
  2. Run; confirm import error.
  3. `dataset.py`: `HELD_OUT_IDS` (the committed three, used by `write_dataset`), `HeldOutRefusedError`, `split_scenarios(split, scenarios_dir, evals_dir, *, allow_held_out=False)` reading the held-out ids from `evals_dir/held_out/manifest.json` so a temporary evals directory defines its own split (D18), `write_dataset(scenarios_dir, evals_dir, *, force, held_out_ids=HELD_OUT_IDS)`, `check_dataset(scenarios_dir, evals_dir) -> list[str]` (mismatch messages), `sha256_of(path)`. The `dataset` command itself is task 8; this task exposes the functions and the test calls them directly, plus the two files written by calling `write_dataset` once against the real directories.
  4. Green, full suite, lint, format, typecheck.
  5. Commit: `feat(evals): dev and held-out split pinned by a committed sha256 checksum`
- Done when: E32 to E34, F7, A5 pass; `evals/checksums.sha256` has four lines matching the committed bytes.

### Task 8: `agentops eval`, `agentops dataset`, and the CLI halves of R3 and R4

- Requirement(s): R13, R14, R3 (cli), R4 (cli), R15 (eval exit), R17 (anthropic stub), N1
- Files: `src/agentops/evals/run.py`, `src/agentops/evals/commands.py`, `src/agentops/cli.py`, `tests/test_eval_cli.py`, `tests/test_cli.py`, `tests/test_cli_lifecycle.py`
- Parallel: yes
- Steps:
  1. Write failing tests E35 to E38, E44, F8, F11, E46, A6, A7, A10, N1 in `tests/test_eval_cli.py` (F11: missing and unreadable scenarios directory exit 2; E46: the run-config helper, no run executed; E38 and A10: copy two dev scenarios into `tmp_path/scenarios`, call `write_dataset(..., held_out_ids=[one of them])` into `tmp_path/evals`, then run `--held-out --scenarios-dir --evals-dir` over them, A10 after flipping one byte in the held-out copy; neither test reads the committed `evals/` or runs a committed frozen scenario, D18) (`CliRunner`, `monkeypatch.chdir(tmp_path)`); E39 in `tests/test_cli.py`; E40 in `tests/test_cli_lifecycle.py`.
  2. Run; confirm `No such command 'eval'`.
  3. `run.py`: `run_graph_once`, `run_baseline_once`, `run_split`, `render_table`, `rows_as_json`; a run that raises becomes a `failed` row. `commands.py`: `eval_command` (options of the spec including `--evals-dir`; with `--held-out` it calls `check_dataset` first and exits 1 with the first mismatch before any run, then `split_scenarios(..., allow_held_out=True)`), `dataset_command`; `cli.py`: `app.command("eval")(eval_command)`, `app.command("dataset")(dataset_command)`, `closing_sentence` unknown branch, the torn-ledger stderr notice in `ThreadRun.start`.
  4. Green, full suite, lint, format, typecheck.
  5. Commit: `feat(cli): agentops eval and agentops dataset, offline with the scripted model`
- Done when: E35 to E40, E44, E46, F8, F11, A6, A7, A10, N1 pass; running the suite leaves no `audit/` or `agentops.sqlite` in the repository root; `grep -rn "held_out\|held-out" tests/test_eval_cli.py` shows every `--held-out` invocation pointed at a `tmp_path` directory.

### Task 9: `langgraph.json` and the dev-server factory (sensitive: `langgraph.json`, `requirements-dev.txt`)

- Requirement(s): R16, R18 (dev pin), R17 (no `env` key), R19 (the `langgraph dev` paragraph only, D20)
- Files: `src/agentops/dev_server.py`, `langgraph.json`, `requirements-dev.txt`, `tests/test_langgraph_json.py`, `tests/test_requirements.py`, `README.md` (one paragraph under a `langgraph dev` heading; task 10 extends the file in wave 4), `docs/sdlc/2026-10-02-increment-4-no-spend-measurement-harness/spec.md` (the Compliance paragraph's two "believed" labels only)
- Parallel: yes
- Steps:
  1. Write failing tests E41, F9 in `tests/test_langgraph_json.py` and E42 in `tests/test_requirements.py` (task 6 edited that file in wave 2, which has merged before this wave starts).
  2. Run; confirm the file is missing.
  3. `dev_server.py`: `make_graph(config)` reads `configurable.scenario_id` (default `clean_bad_deploy`), validates it with `SIGNAL_ID_PATTERN` and `list_scenario_ids`, builds `SimulatorSource`, `scripted_model_for`, `default_registry()`, `AuditLog(Path("audit"))`, returns `build_state_graph(...).compile()`. `langgraph.json` exactly as the spec states. Add `langgraph-cli[inmem]==0.4.32  # MIT` to `requirements-dev.txt`; run `install`; run `langgraph dev --help` once to confirm the CLI installs (do not leave a server running).
  3a. D20: read the installed package source (`python -m pip show -f langgraph-cli` lists the files; grep them for `analytics`, `LANGGRAPH_CLI_NO_ANALYTICS` and the Studio URL, and read the matching lines) and record what is there: whether usage analytics are sent, the exact opt-out variable name if one exists, and the host `langgraph dev` opens for Studio. Write the result as one README paragraph under a `langgraph dev` heading (the word `analytics`, the opt-out variable, the Studio host, and that no test or CI step runs the CLI), and replace the two "believed, not verified" labels in the spec's Compliance paragraph with "confirmed against `<path>`" or "not present in `<version>`". Do not run the server.
  4. Green, full suite, lint, format, typecheck.
  5. Commit: `feat(dev): langgraph.json and a scenario factory for langgraph dev; record what the CLI sends off the machine`
- Done when: E41, E42, F9 pass; the README paragraph and the spec Compliance paragraph name the confirmed analytics behaviour and Studio host with the file they were read from.

### Task 10: Documents and the CI floor (sensitive: `.github/workflows/**`)

- Requirement(s): R19, N2, R17 (tracked files)
- Files: `README.md`, `CONTRIBUTING.md`, `docs/ci.md`, `docs/sdlc/codebase-map.md`, `.github/workflows/ci.yml`, `tests/test_repo_documents.py`, `tests/test_ci_workflow.py`
- Parallel: no (only task in its wave)
- Steps:
  1. Write failing tests E43 in `tests/test_repo_documents.py`; N2, A8 in `tests/test_ci_workflow.py`.
  2. Run the full suite once with `--junitxml`, read the passing count, compute the floor by the rule in `docs/ci.md`; confirm the tests fail on the old text and floor.
  3. README: status line for increment 4, an "Measuring it" section (eval, dataset, the scripted sentence, `--held-out` discipline including the checksum check that precedes it, tracing variables by name with the falsy-means-off rule, `langgraph dev`, keeping task 9's analytics and Studio paragraph so E43 finds it), "When it fails" rows for `effect_unknown` and the torn-ledger notice. CONTRIBUTING: adding a baseline script. `docs/ci.md`: the new floor and count. Codebase map: `src/agentops/evals/`, `evals/`, `baseline.py`, `dev_server.py`, `tracing.py`, `langgraph.json`, and the ADR 0001 deviation. `ci.yml`: `MIN_TESTS` only.
  4. Green, full suite, lint, format, typecheck.
  5. Commit: `docs: describe agentops eval, the frozen split, tracing and langgraph dev; raise the test floor`
- Done when: E43, N2, A8 pass and every other case in evals.md is green on the branch head.

## Verification plan

Profile commands on the branch head: `lint`, `format`, `typecheck`, `test`, `security_audit`
(`build`, `e2e`, `screenshot` are empty in the profile and stay so). Evals: all 72 cases run as
pytest in the same session; the verifier reports pass counts per category against the targets
and lists any failing id with input, expected and actual. Evidence the human sees: the
verification report, the `agentops eval --model scripted` output pasted from the E35 test's
captured stdout (header, two rows, failures list), the `agentops dataset` exit 0 from E44, and
the four-line `evals/checksums.sha256`. Not verifiable here and said so: `langgraph dev` serving
the graph in a browser (needs a running server and a person); the baseline against a live model
(a later change).

## Rollback

No environment is deployed (`profile.environments` have no deploy command). The release is the
owner merging the pull request; undo is `git revert -m 1 <merge commit>` on `main`. Local state:
audit records written by the new code carry an extra `effect` key that old code ignores; a
checkpoint with `applied: null` would fail validation under old code, so a thread that recorded an
unknown effect is not resumable after a revert (it is escalated or resolved by then, so nothing
is lost). The two dataset files and `langgraph.json` are deleted by the revert. Runtime files are
git-ignored and untouched.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| `create_agent` rejects a `BaseChatModel` without a provider profile or needs more than `bind_tools` | medium | Task 6 slips; the baseline needs a thicker fake | Task 6 proves it first; the fake stays in `fakes.py`; the ship document records what was needed |
| `langchain` 1.4.3 pulls a transitive pin that conflicts with `langgraph` 1.2.12 at CI install | low | Red CI | Dry-run resolved cleanly on this machine; CI installs the same pins on Linux |
| Pyright fallout from `applied: bool \| None` across readers | high | Task 1 takes longer | Expected and listed; each reader gets an explicit `is True` or `is None` test |
| The narrowed patterns break a scenario target | low | Full-run tests red | Both targets confirmed to match before writing; E24 pins accept and reject lists |
| `langgraph dev` ignores or rejects a factory compiled without a checkpointer | medium | The dev server does not start | Only `langgraph dev --help` is in scope to run; serving is a manual check named in the brief |
| Held-out checksum computed before a later task edits a scenario | low | E33 red at verify | No task after wave 2 edits `scenarios/`; the test catches it anyway |
| A stale `LANGSMITH_TRACING` in the owner's shell | medium | A surprising exit 1 | The message names the variable and the fix; tests clear the variables |
