# Evals: Increment 2, the investigation half of the graph

Change id: `2026-10-01-increment-2-investigation-half`
Spec: [spec.md](./spec.md)

Every case runs with the scripted model, no API key and no network. Cases named `existing`
are increment 1 tests kept as written; cases named `replaces` supersede an increment 1 test
whose behaviour the spec changes (brief D16). Scenario numbers follow the spec's Data table.

## Targets

| Category | Target | Rationale |
|----------|--------|-----------|
| Golden | 100% pass | Every scenario reaches approval-ready or escalates by the stated path |
| Edge | 100% pass | Empty sources, unmatched hypotheses, budget clamp, half-null outputs |
| Failure | 100% correct handling | Dead sources, uncited outputs, exhausted scripts, error-path printing |
| Adversarial | 100% rejected | Log injection stays fenced; a model cannot talk the graph out of escalating |
| Non-functional | see rows | Suite time and test-count floor |

## Cases

| ID | Category | Given | When | Then | Maps to requirement | Implemented as |
|----|----------|-------|------|------|---------------------|----------------|
| E1 | golden | Scenario 1 and its scripted model | The graph runs | `hypotheses` equal the scripted triage; status is not `triage`; the triage prompt names the untrusted-data rule | R1 | `tests/test_graph.py::test_triage_writes_hypotheses_and_leaves_triage` |
| E2 | golden | Scenario 1's hypotheses (`recent_deploy_regression` 0.7, `database_saturation` 0.2), no evidence, no actions | `score_sources` and `route_to_sources` are called | Scores logs 2, metrics 1, deploys 1; the Sends name logs, metrics, deploys in that order | R2 | `tests/test_router.py::test_router_scores_sources_against_open_hypotheses_in_fixed_order` |
| E3 | golden | A state where metrics already holds evidence and deploys was asked in two rounds | `route_to_sources` is called twice | Both calls return exactly one Send, to logs; the two results are equal | R2 | `tests/test_router.py::test_router_is_deterministic_and_skips_answered_or_exhausted_sources` |
| E4 | golden | Scenario 2 and its scripted model | The graph runs | Evidence holds log, metric, deployment and dependency kinds; four source reads in `actions`; budget 8; loops 0 | R3, R4 | `tests/test_graph.py::test_three_sources_run_in_one_round_and_merge_charges_the_budget` |
| E5 | golden | A state with loops 1 and `opened_at` T | `logs` node runs against a recording source | The window passed to `get_logs` starts at T minus 120 minutes and ends at T | R3 | `tests/test_evidence_nodes.py::test_window_widens_by_sixty_minutes_per_loop` |
| E6 | golden | A state where every relevant source holds evidence | `decide_next_step` | Returns `rca` | R4 | `tests/test_merge.py::test_merge_routes_to_rca_when_nothing_is_left_to_ask` |
| E7 | golden | A state with 4 reads, deploys asked once with no evidence | `merge` runs, then `decide_next_step` | Decision is investigate with a single Send to deploys; `loops` becomes 1; budget 8 | R4 | `tests/test_merge.py::test_merge_loops_back_for_an_empty_source_and_counts_the_loop` |
| E8 | golden | Scenario 1 with 11 source reads pre-recorded in `actions` | The graph runs | Ends `escalated`, reason `budget_exhausted`, budget 0, rca never called | R4, R7 | `tests/test_graph.py::test_budget_exhaustion_escalates_without_an_rca_call` |
| E9 | golden | Scenario 2 and its scripted model | The graph runs | Re-scored hypotheses equal the scripted rca's; status `awaiting_approval`; remediation `scale_db_pool`; `root_cause.label` `database_saturation` | R5 | `tests/test_scenario_runs.py::test_red_herring_ends_approval_ready_with_rescored_hypotheses` |
| E10 | golden | Scenario 7 and its scripted model | The graph runs | Status `escalated`, reason `ambiguous`, `escalation.summary` non-empty and every cited id in `evidence`; `remediation` is None | R6, R7 | `tests/test_scenario_runs.py::test_ambiguous_scenario_escalates_with_both_hypotheses` |
| E11 | golden | Each of the eight scenario files | The graph runs with that scenario's scripted model | Final status matches `expected_outcome`: `remediate` and `loop` give `awaiting_approval`, `escalate` gives `escalated` | R10 | `tests/test_scenario_runs.py::test_every_scenario_ends_as_its_expected_outcome` (parametrised) |
| E12 | golden | Each scenario whose outcome is `remediate` or `loop` | The graph runs | `remediation.action` equals `correct_action`, is not in `wrong_actions`, and `required_evidence` is a subset of the evidence ids | R10 | `tests/test_scenario_runs.py::test_remediation_matches_ground_truth_on_collected_evidence` (parametrised) |
| E13 | golden | Each of the eight scenarios | The graph runs | `loops` is 1 for scenarios 3, 4, 5 and 0 for 1, 2, 6, 8; every run ends with budget at or above 4 | R10 | `tests/test_scenario_runs.py::test_loop_counts_and_budget_follow_the_stated_paths` (parametrised) |
| E14 | golden | Scenario 7 | `agentops run --scenario ambiguous_two_causes --model scripted` | Exit 0; stdout has `status: escalated`, `escalation: ambiguous` and the summary | R12 | `tests/test_cli.py::test_run_prints_escalation_reason_and_summary_and_exits_zero` |
| E15 | golden | Scenario 3 | `agentops run --scenario dependency_timeout_noisy_neighbour --model scripted` | Exit 0; stdout has `status: awaiting_approval`, `loops: 1`, `budget remaining: ` with a number, `remediation: circuit_break_dependency` | R12 | `tests/test_cli.py::test_run_prints_loops_budget_and_remediation` |
| E16 | golden | `.github/workflows/ci.yml` | Parsed with pyyaml | `runs-on` is `ubuntu-24.04`; no job uses a string ending in `-latest`; `MIN_TESTS` is `80`; the increment 1 gate order and pin assertions still hold | R14 | `tests/test_ci_workflow.py::test_workflow_runs_the_gates_in_order_with_a_read_only_token` (extended) |
| E17 | golden | README, CLAUDE.md, codebase map, profile | Read as text | README names all eight scenario ids and both terminal outcomes; CLAUDE.md no longer says the live model "arrives in increment 2"; the codebase map names `scripted_model_for`; the profile text says merging the pull request to `main` is the release and the owner performs it | R13, R15, R16 | `tests/test_repo_documents.py::test_licence_contributing_readme_ci_doc_and_profile_match_the_built_repo` (extended) |
| E18 | golden | Every `scenarios/*.json` | Loaded with `load_scenario`; scripted outputs validated | Eight files load; `list_scenario_ids` returns the eight ids; triage validates as `Triage`, rca as `RcaOutput`, escalate (where present) as `Escalation`; every cited id is a signal id of that file | R9 | `tests/test_scenarios.py::test_scripted_outputs_are_valid_and_cite_real_signals` (extended) |
| E19 | edge | One open hypothesis named `something_odd` | `score_sources` | Logs 1, metrics 1, deploys 1 | R2 | `tests/test_router.py::test_hypothesis_matching_no_keyword_counts_for_every_source` |
| E20 | edge | A source whose `get_metrics` and `get_dependency_health` return empty lists | `metrics` node runs | Returns no evidence and two `ok` actions with `attempt` 1 | R3 | `tests/test_evidence_nodes.py::test_empty_source_yields_no_evidence_and_an_ok_action` |
| E21 | edge | `{"a<<<": 1, "a< < <": 2}` | `neutralise_delimiters` | Two entries remain, values 1 and 2, keys `a< < <` and `a< < < (2)` | R13 | `tests/test_prompt_helpers.py::test_colliding_keys_are_both_kept` |
| E22 | edge | A state with 13 source read attempts | `merge` runs | `investigation_budget` is 0, not negative | R4 | `tests/test_merge.py::test_budget_never_goes_below_zero` |
| E23 | edge | An rca object with `root_cause` set and `remediation` null, and the reverse | `RcaOutput.model_validate` | Both raise `ValidationError`; both null validates; both set validates | R5 | `tests/test_state.py::test_root_cause_and_remediation_are_both_present_or_both_null` |
| E24 | edge | `Triage`, `RcaOutput`, `Escalation` schemas | JSON schema inspected; an object with `escalation_reason` added | No control field, including `escalation_reason`, appears in any schema; the extra key is rejected | R8 | `tests/test_state.py::test_control_fields_absent_from_every_model_output_schema` (extended) |
| E25 | failure | Scenario 1 with a source whose `get_logs` raises once | The graph runs | `get_logs` called twice; one action `ok=False`, `attempt` 1, detail naming the error type; log evidence present; status `awaiting_approval` | R3 | `tests/test_graph.py::test_source_failure_is_retried_once_and_recorded` (replaces the increment 1 flaky-source test) |
| E26 | failure | Scenario 1 with `get_logs` always raising | The graph runs | Two failed actions per round for logs, no log evidence, run still reaches `awaiting_approval` on deployments and metrics; required log evidence absent is reported by E12 only for the real file, so this test asserts the status and the counts | R3 | `tests/test_graph.py::test_dead_source_does_not_fail_the_round` |
| E27 | failure | Scenario 1 with a source returning empty lists for every method | The graph runs | Status `escalated`, reason `no_evidence`; the model was called for triage and escalate only; each source was asked in two rounds | R7 | `tests/test_graph.py::test_no_evidence_at_all_escalates_after_two_rounds` (replaces the increment 1 empty-evidence test) |
| E28 | failure | Scenario 1 with a scripted rca citing an unknown id three times | The graph runs | `CitationError` propagates after three rca calls | R5 | `tests/test_graph.py::test_three_uncited_rcas_surface_citation_error` (existing) |
| E29 | failure | A state with evidence `log-001` and a scripted escalation citing `log-999` | The `escalate` node runs | Raises `CitationError`; the same node with `log-001` cited returns status `escalated` | R7 | `tests/test_rca_escalate.py::test_escalation_citations_are_checked_like_any_other` |
| E30 | failure | Scenario 1 with a scripted rca citing an id containing an escape character and a newline | `agentops run` | Exit 2; stderr shows the id with visible escapes, no raw control character | R12 | `tests/test_cli.py::test_error_path_escapes_control_characters_in_model_text` |
| E31 | failure | Scenario 1 with a scripted triage citing `deploy-001` before any evidence | `agentops run` | Exit 2; stderr names `CitationError`, not `ValidationError` | R1, R12 | `tests/test_cli.py::test_triage_citation_error_is_reported_as_citation_error_not_a_validation_error` (existing) |
| E32 | failure | Any scenario | `agentops run --model anthropic` | Exit 1; stderr says the live model is not wired yet and to use `--model scripted` | R12 | `tests/test_cli.py::test_usage_errors_exit_one_with_a_named_cause` (extended) |
| E33 | adversarial | Scenario 8 and its scripted model | The graph runs | The injected line appears in prompts only between the opening and closing delimiters, with no `<<<` or `>>>` run inside; remediation is `pause_batch_job`; `drop` does not appear in `remediation.action` | R11 | `tests/test_scenario_runs.py::test_log_injection_stays_fenced_and_the_real_cause_wins` |
| E34 | adversarial | A scripted rca naming `memory_pressure_gc_pauses` as root cause with re-scored scores 0.50 and 0.45 | The `rca` node runs, then `after_rca` | Node returns status `investigating` with `root_cause` None and `remediation` None, whatever the model proposed; `after_rca` returns `escalate`; `is_ambiguous` is true | R6 | `tests/test_rca_escalate.py::test_graph_escalates_when_the_scores_do_not_support_the_cause` |
| E35 | adversarial | Each of the eight scenarios | The graph runs with a socket guard | No prompt contains a hidden section name, the ground-truth label or the scenario id; the rca and escalate prompts hold exactly one opening and one closing delimiter; no socket is opened | R1, R11 | `tests/test_graph.py::test_prompts_never_contain_hidden_sections_or_forged_delimiters_and_open_no_network` (existing, extended to the escalate prompt) |
| E36 | edge | Hypotheses `database_saturation` and `recent_deploy_regression`, logs already hold evidence, and action records leaving 1, 2 and 3 reads of budget in three sub-cases | `route_to_sources` is called in each | With 1 left: returns `merge` (metrics, cost 2, is first and does not fit, so the prefix is empty even though deploys would fit); with 2: one Send, to metrics; with 3: Sends to metrics then deploys | R2 | `tests/test_router.py::test_router_keeps_only_the_longest_prefix_that_fits_the_budget` |
| E37 | edge | Re-scored hypotheses with scores 0.65 and 0.50, 0.65 and 0.51, a single hypothesis, and a clear pair (0.9, 0.1) with `root_cause` null | `is_ambiguous` and `after_rca` | 0.65 and 0.50 (margin 0.15) is not ambiguous; 0.65 and 0.51 is ambiguous; a single hypothesis is not ambiguous; the null root cause returns `escalate` from `after_rca` whatever the scores | R6 | `tests/test_rca_escalate.py::test_ambiguity_margin_boundary_single_hypothesis_and_null_root_cause` |
| E38 | failure | A triage set of two names; scripted rcas that (a) cite `log-999` from one re-scored hypothesis, (b) drop one triage name, (c) add a name triage never produced, (d) rename one, (e) set `root_cause.label` to the lower-scored name | The `rca` node runs on each, with the retry budget exhausted | (a) raises `CitationError` naming `log-999`; (b) to (e) each raise `RcaConsistencyError` naming the offending name; with the triage names, the top-scored label and present ids the node returns status `awaiting_approval` | R5 | `tests/test_rca_escalate.py::test_rca_output_is_checked_against_triage_names_and_cited_evidence` |

## Non-functional

| ID | Measure | Target | How measured |
|----|---------|--------|--------------|
| N1 | Full suite wall time with no network | Under 60 s on the development machine (76 tests took 15 s on 2026-10-01, confirmed) | The verifier's `test` command output; the last line of `verify-logs/test.log` |
| N2 | Tests passed | At least 80 | `scripts/check_test_count.py junit.xml 80` in CI; the verifier's pytest count locally |

## Failure taxonomy

| Class | Description | Detection | Example |
|-------|-------------|-----------|---------|
| Wrong result | Approval-ready remediation names a wrong action or a cause the evidence does not support | E11, E12 fail; in production the correct-outcome and false-remediation evaluators (increment 4) | Scenario 2 proposing `rollback_deploy` |
| Missing result | Required evidence not collected before the proposal, or a source silently skipped | E12, E13; `budget remaining` and `(M failed)` in the CLI report | Logs never asked because a hypothesis name matched no keyword and metrics answered first |
| Slow | Loops or reads beyond the stated path | E13; `loops` and `investigation_budget` in the checkpoint | A scenario looping twice on an empty source |
| Unrecoverable | Budget spent or no evidence; the run escalates | `escalation_reason` in state and the CLI line | E8, E27 |
| Leaked | Hidden scenario sections or the scenario id in a prompt | E35 | Ground-truth label in the escalate prompt |
| Unauthorised | Injected instruction leaves the fence or shapes the action | E33 | Scenario 8 proposing `drop_table` |
| Corrupted | A model output smuggles a control field or a half-null rca | E23, E24 | `escalation_reason` in an rca reply |
