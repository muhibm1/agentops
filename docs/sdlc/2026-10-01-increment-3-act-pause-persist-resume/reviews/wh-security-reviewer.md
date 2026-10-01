Verdict: 3 findings (0 critical, 0 high, 1 medium, 2 low)

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| medium | src/agentops/actions/preconditions.py:114, :133 (also :89, :174) | Preconditions check the incident's service and never the action's target. `deployment_within_minutes` and `previous_version_available` read the service's latest deployment and never compare its `id` with `remediation.target`. The metric and config checks also read the service only. Rule: check the precondition against the object the action changes (spec Security: "preconditions against the source"). Confirmed with a probe on `clean_bad_deploy`: `rollback_deploy` with target `deploy-001`, with `payments-prod-db`, and with a 5000-character target all pass both checks. | Steered evidence (scenario 8 class) leads the model to propose `rollback_deploy` on a deployment other than the recent one. Both runbook checks pass because they validated a different object. With a real adapter, the operator's approval is the only thing between the proposal and a rollback of the wrong deployment. For `scale_db_pool` and `pause_batch_job` any target name is accepted. | preconditions.py: in `deployment_within_minutes` and `previous_version_available`, fail unless `latest.id == remediation.target`. runbook.py, `ActionEntry`: add a required `target_pattern` (for example `^postgres$` for `scale_db_pool`). Check it in `Registry.validate_params`, which serves executor step 2 and `approval.edit_problem`. tests/test_preconditions.py and tests/test_executor.py: add deny tests that expect `precondition_failed` or `refused` for a foreign target. |
| low | src/agentops/actions/executor.py:221-227, :185, :266 | The audit log can miss a real apply. `source.apply` makes the ledger line durable (simulator.py `append_ledger_line`, fsync) before `write_apply_record` runs. If the process dies between the two, or the audit append raises after a successful apply, no `apply` record with `applied: true` is ever written. On resume the ledger check returns `already_applied` and writes `applied: false`. This constraint-audit low is still open: R9 does not cover this window, and `--crash-after-apply` fires only after the audit write, so no test reaches it. Confirmed by reading the code; not run. | The process is killed (OOM, power loss) or the disk fills between the two fsyncs. The system was changed, but the audit file for the incident shows a `dry_run` and then only `applied: false / already_applied`. An investigator reading the append-only log concludes that nothing was ever applied. | executor.py `find_recorded_execution`: when the key is found in `source.ledger()` but not in `state["execution"]`, and the incident's audit file has no `apply` record with `applied: true` for that key, write one with `detail: "recovered from the source ledger after an interrupted run"` before returning `already_applied`. tests/test_executor.py: use a source whose `apply` succeeds while the audit append raises once, then assert the recovered record after re-execution. |
| low | src/agentops/state.py:17, :89, :191 | `Remediation.target` and `ExecutionRecord.target` have a pattern but no maximum length. String parameters are capped at 64 (registry.py `STRING_PARAMETER_MAX_LENGTH`); targets are not. Confirmed: the probe built a valid `Remediation` with a 5000-character target. Rule: bound free text that reaches logs and prompts. | Model output with a very long target is copied into every `proposal`, `precondition`, `dry_run`, `apply` and `approval` audit line, into the simulator ledger and into the next prompt. It also buries the one-sentence dry run the operator approves in the terminal. | state.py: `target: str = Field(pattern=TARGET_NAME, max_length=64)` on both models. tests/test_state.py: assert that a 65-character target is rejected. |

Sweep evidence (confirmed unless marked)
- Injection: the runbook is parsed with `yaml.safe_load` (runbook.py). Parameter patterns use `re.fullmatch`, and string values are capped at 64 characters (registry.py). Unsafe parameter names and evidence ids are replaced by placeholders, never echoed. Model and scenario text is fenced with a nonce and neutralised (`neutralise_delimiters`).
- Path traversal: the incident id and thread id are matched with `re.fullmatch(SIGNAL_ID_PATTERN)` before any path is built, in `AuditLog.path_for`, `ledger_path_for`, `check_ledger_file_name` and `check_thread_id`. The pattern has no `.` or `/`.
- Deserialization: `checkpoint_serializer()` passes an explicit `allowed_msgpack_modules`. Confirmed in the installed langgraph-checkpoint 4.2.0 `jsonplus.py`: a type outside the allowlist is blocked (returns None), and `pickle_fallback` defaults to False. Write access to the checkpoint file remains an accepted trust assumption (spec Security).
- Approval binding: the executor recomputes the key and refuses on a mismatch with `Approval.idempotency_key` (executor.py:172-185). An edit is re-validated against the registry, the parameter schema and cited ids (approval.py `edit_problem`). Only `edit` may carry a remediation (state.py `ApprovalDecision`).
- Terminal output: `fail()` escapes every message. Model strings in `report`, `pending_proposal_lines` and `history_line` are escaped. Targets and actions printed unescaped are pattern-bound.
- Crash switch: reached only through `configurable["crash_after_apply"] is True` (execute.py), set only by `approve --crash-after-apply`. No environment variable is read under `actions/`.
- Dependency audit: `.venv/Scripts/python.exe -m pip_audit -r requirements.txt` printed "No known vulnerabilities found" (2026-10-01; exit code not captured separately). verify-logs/summary.json: tests 501 passed, which clears the CI floor MIN_TESTS=450 (ci.yml:12, asserted at ci.yml:45).
- Secrets: `git diff main...HEAD` added lines grepped for key, token, password, private-key and connection-string shapes. Hits were only `secrets.token_hex` (the nonce), a `tokens` variable and prose. No secret values.

Constraint audit mediums (spec)
- D21, refused answers audited: resolved in approval.py `refused_record`, which writes `valid: false` and the error.
- D22, approval bound to its key: resolved in executor.py:177-181.
- D23, SQLite sidecar files: resolved with `.gitignore` `*.sqlite-*`. `git ls-files` lists no `*.sqlite*`, `*.db`, `*.jsonl` or ledger file; the only match is an ADR file name.
- D24, transitive pins: resolved; requirements.txt pins `aiosqlite==0.22.1` and `sqlite-vec==0.1.9` with licences. `langgraph-checkpoint` (4.2.0 installed) stays unpinned by a documented choice.

Pre-ship checklist
- `for update` policy, `for insert` policy, new DB function grants, `CREATE OR REPLACE`, policy tests, deny-side assertions, storage bucket: n/a. The change has no Postgres, no policies and no buckets; the only database is a local SQLite checkpoint file.
- Free text bounded: pass. `ApprovalDecision.reason` max_length is 1000 (state.py). The refused-answer copy is cut at 4000 characters (approval.py `SUBMITTED_MAX_CHARACTERS`). The `target` gap is the low finding above.
- Admin capability writes to an append-only log: pass. Proposal, approval (valid and refused), precondition, dry_run, apply, verify and escalation are written in append mode with fsync (audit.py). The crash window is the low finding above.
- New secret or env var: n/a, none added. Runtime files are covered by patterns (`*.sqlite`, `*.sqlite-*`, `audit/`), confirmed with `git ls-files`.
- Third-party runtime imports pinned: pass. requirements.txt has exact pins for langgraph-checkpoint-sqlite 3.1.1, aiosqlite, sqlite-vec and pyyaml 6.0.3; pyyaml moved out of requirements-dev.txt.
- Regime walk: the profile lists no regimes (`regimes: []`). GDPR is only possibly applicable: the operator is a role literal (state.py `OperatorRole`), and the reason text carries the accepted note from the constraint audit.

Findings outside scope: none

Not verified:
- AgentShield scan (the diff changes CLAUDE.md): `npx -y ecc-agentshield@1.6.0 scan --path ~/AgentOps --min-severity high --format json` was denied by the the harness permission classifier ("Untrusted Code Integration"). The owner must run it by hand or allow it.
- Medium finding: whether a real (non-simulator) adapter would act on the foreign target is believed, not verified; only the simulator exists, and it ignores `target`.

## Re-check (fixer commits f2dfd3a..decc79b)

Verdict after re-check: 0 critical, 0 high, 0 medium open; 3 low (2 new, 1 residual).
Run: `.venv/Scripts/python.exe -m pytest -q` on the touched suites, 214 passed (confirmed, run at decc79b); verify-logs/summary.json at decc79b: checks_green true.

| Prior finding | Status | Evidence |
|---|---|---|
| medium, preconditions ignore target | closed (confirmed) | f643e6c: preconditions.py `not_the_target` fails both deployment checks unless `latest.id == remediation.target`; runbook.py `target_pattern` is required and must compile; registry.py `validate_params(target=)` is called from executor.py:141 and approval.py:146 (grep: no other caller); refusal text does not echo the target. Deny tests: test_preconditions, test_executor `a_rollback_aimed_at_another_object_applies_nothing`, test_approval, test_runbook. |
| low, apply audit gap | closed (confirmed) | 32c6139 + decc79b: executor.py `find_recorded_execution` writes `applied: true` with RECOVERED_DETAIL when the ledger has the key and the audit file has no apply record; tests cover the recovered and the no-duplicate cases. |
| low, unbounded target | closed (confirmed) | state.py TARGET_MAX_LENGTH 64 on Remediation and ExecutionRecord; 64/65 tests in test_state.py. |

| Severity | File:line | Finding | Failure scenario | Fix |
|---|---|---|---|---|
| low | runbooks/default.yaml:51, :64; preconditions.py:180 | Residual of the medium: `rerun_partition` and `pause_batch_job` accept any job name matching a broad pattern, and their preconditions (`metric_below`, `config_true`) read the service only, never the job (confirmed by reading). Both are risk low. | Steered evidence proposes pausing a job unrelated to the incident; preconditions pass; only the operator's reading of the dry run stops it. | Pin each `target_pattern` to the scenario's job names, or add a precondition that the target appears in the service's evidence; record the acceptance in CONTRIBUTING.md if deferred. |
| low | src/agentops/cli.py:287 | Catch-all prints raw exception text, `{type(error).__name__}: {error}`, escaped but unbounded and unfiltered (confirmed by reading). Every other failure path prints the type name only. | A future node or a real adapter raises an exception whose message carries a payload, a connection string or a long model string; it lands on stderr and in CI logs. Today no secret exists in the process (believed: no env var or key is read; the live model is not wired). | cli.py:287: print the type name and the first 200 characters of the message, or the type name only, as open_checkpoints does. |
| low | src/agentops/state.py:84, :123; cli.py:533, :563 | `RCA.explanation` and `Postmortem.summary` are model text with no max_length, printed to stdout (escaped) and re-sent in prompts (confirmed). | The model copies fenced evidence (log lines) into the summary; the terminal and any captured stdout carry it at any length. | state.py: max_length 2000 on both, as ESCALATION_SUMMARY_MAX_LENGTH does for Escalation.summary; one test each. |

Observability review of cli.py (the automated "sensitive-to-observability" flag)
- No logging module, no print() under src/agentops (grep, confirmed). Output is typer.echo only: stdout for report/history, stderr for `fail()`.
- Evidence payloads: never printed; only counts (cli.py:528, :494) (confirmed).
- Operator reason and remediation file content: never printed. Refusals are built from field names and rules, `include_input=False` (approval.py:124, :160); the submitted answer goes only to the audit file, cut at 4000 characters (approval.py:205) (confirmed).
- Edited parameters: reach stdout only inside the dry run, after pattern and 64-character validation (confirmed).
- Thread ids, incident ids, targets, action names: pattern-bound; targets now capped at 64 (confirmed). Operator is a role literal.
- File paths (state db, audit file, ledger, scenario file): printed; these are the operator's own local paths and intended (confirmed).
- Exception text: type name only everywhere except cli.py:287 (low above) (confirmed).
- Escaping: every model string and every `fail()` sentence passes `escape_for_terminal` (confirmed). `execution.target`, `execution.action`, `escalation_reason` print unescaped but are pattern- or Literal-bound (confirmed).
- The new refusal path (cli.py:179-181) prints `approval_error` twice (stdout pending line and stderr); it holds no submitted text (confirmed).
- Conclusion: no unintended sensitive content reaches output; bounding gaps are the two lows above. The flag gave no detail, so whether it meant something else is not verified.
