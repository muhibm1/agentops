# Spec: Increment 3, act, pause, persist, resume

Change id: `2026-10-01-increment-3-act-pause-persist-resume`
Intent: the request as recorded in [conductor-log.md](./conductor-log.md) (no intent.md was written)
Status: draft
Policy skills applied: wh-agent-rules, wh-security-baseline, wh-readable-code, wh-adr, wh-evals

## Summary

The graph gains the second half of design section 4: a typed runbook and registry, an executor
that applies an approved action exactly once with an append-only audit log, an approval interrupt
with resume, a verify loop, a postmortem, SQLite checkpoints and the CLI commands that drive them,
after first closing the four findings increment 2 deferred. The one decision that matters most:
every control edge stays code, and the registry, not the prompt, decides what may run, so
scenario 8's injected `drop_table` is blocked even when the model proposes it (R13).

Claims below are `confirmed` by reading the repository on 2026-10-01 unless marked `believed`.

## Requirements

| ID | Requirement | Acceptance check | Source |
|----|-------------|------------------|--------|
| R1 | When no evidence was collected and every recorded source read attempt failed, the run SHALL escalate with reason `source_outage`; sources that answer with empty results still give `no_evidence` | Every source raising on every call ends `escalated` with `source_outage`; scenario 4 style empty metrics still yield `no_evidence` when nothing else was found | ship.md deferred medium 1, retro |
| R2 | Merge SHALL write `unreachable_sources` (sources whose every recorded attempt failed) to state; rca's prompt SHALL name them; after rca, a hypothesis still open (score at or above 0.2) that only unreachable sources can judge SHALL route to `escalate` with `source_outage`; the approval payload lists them | A deploys source dead both rounds with `recent_deploy_regression` re-scored 0.3 escalates; the same with 0.1 reaches approval, with `deploys` in the payload | ship.md deferred medium 2 |
| R3 | Total recorded source read attempts per run SHALL never exceed 12, under any failure pattern; `affordable` reserves nominal cost times `READ_ATTEMPTS` per source; the increment 2 loop and budget assertions still hold | A run where every read fails every time records at most 12 attempts; the increment 2 path test is unchanged and green | ship.md open low (16 reads) |
| R4 | `neutralise_delimiters` SHALL remove Unicode format characters and map the lookalike brackets in ADR 0005 to ASCII before spacing out runs; rca, escalate and postmortem SHALL fence evidence with a per-prompt nonce in both markers | Each listed lookalike run and a zero-width-split run comes out spaced; two prompts never share a nonce; the fence regex matches once each | ship.md open low (lookalikes), ADR 0005 |
| R5 | `MIN_TESTS` in `.github/workflows/ci.yml` SHALL be the largest multiple of 10 at or below 90 percent of the passing count on the branch, at least 240; `docs/ci.md` states the new figure | The workflow test asserts the value is an integer at least 240; the final suite passes above it | request, ship.md open low (floor) |
| R6 | `runbooks/default.yaml` SHALL declare exactly `scale_db_pool`, `rollback_deploy`, `circuit_break_dependency`, `rerun_partition` and `pause_batch_job`; the loader parses with `yaml.safe_load` only and validates every entry into a typed model with unknown keys forbidden, distinct names, a parameter schema, a risk, preconditions, a dry-run mode, a rollback declaration and an idempotent flag; a bad file fails at startup naming the entry and field | Loading the file yields five entries; a file with an unknown key, a duplicate name or a `min` above `max` raises with the path; no scenario's `wrong_actions` name is in the registry; grep finds no `yaml.load(` under `src/` | design section 7, increment 2 spec D14 |
| R7 | The registry SHALL answer `has(name)`, `get(name)` and `validate_params(name, params)` against the declared schema (type, min, max, pattern, required); patterns are matched with `re.fullmatch`; every string parameter has a maximum length of 64 characters; unknown, out-of-range, overlong or non-matching parameters are rejected with the parameter name | `pool_size` 96 passes, 10000 fails naming `pool_size`, an undeclared key fails naming it, `to_version` of `v1` followed by a newline fails, a 65-character `fallback` fails | design section 7 |
| R8 | Preconditions SHALL be evaluated against the `EvidenceSource` only, through six check kinds (`metric_above`, `metric_below`, `deployment_within_minutes`, `previous_version_available`, `dependency_not_healthy`, `config_true`); each returns a result with the check, passed, observed value and a sentence; a source error makes the check fail and never raises | Every runbook precondition passes on its scenario's source; a raising source returns `passed: false` with the error type | design section 7 step 3 |
| R9 | The executor SHALL run the seven steps in design section 7 order and no other: registry lookup, parameter validation, preconditions, idempotency key `sha256(incident_id + canonical(action, target, params))`, check of `state.execution` and `source.ledger()`, dry-run description appended to the audit log, apply and audit; outcomes are `applied`, `already_applied`, `refused`, `precondition_failed`, `apply_failed`; a key already recorded returns the recorded result, applies nothing and appends an `apply` record with `applied: false` and `detail: already_applied`; the `apply` record for a real apply is written in step 7 immediately after `apply` returns, and an adapter that raises inside `apply` gets an `apply` record with `ok: false` and the error type before the exception propagates | Order asserted by a recording source; a second execution with the same key leaves one ledger entry and one `applied: true` record; a source refusal yields `apply_failed` with no execution `applied_at`; a raising source leaves an `apply` record naming the error | design section 7, constraints T7 |
| R10 | The audit log SHALL be one JSONL file per incident under the audit directory, opened in append mode only, one JSON object per line with `event`, `at`, `incident_id`, `thread_id` and `idempotency_key` where one exists; events are `proposal`, `approval`, `precondition`, `dry_run`, `apply`, `verify`, `escalation`; an incident id outside the signal id pattern is refused | After a full run the file parses line by line, every line carries the thread id, the file is never shorter than before any write, and a traversal id raises before any file is opened | design section 7, constraints must-not-change 6 |
| R11 | The approval node SHALL call `interrupt()` once per execution with the remediation, its cited evidence, the dry-run description, the rollback declaration and `unreachable_sources`; the resume value is validated as `ApprovalDecision` (`approve`, `reject` with a reason, `edit` with a remediation, an operator role); every resume value, accepted or not, is appended to the audit log as an `approval` record with `valid` and, when rejected, `error` (D21); approve writes `Approval` carrying the idempotency key of the approved remediation (D22) and routes to execute; reject appends the reason as evidence of kind `operator`, clears the proposal, adds one loop and routes to the router; edit is re-validated (citations, registry, parameters) and an invalid edit interrupts again with the error in the payload | Each decision ends in the stated state; an edit naming `drop_table` or `pool_size` 10000 produces a second interrupt carrying `error`, no execution, and an `approval` record with `valid: false` naming the error | design section 8, ADR 0001 |
| R12 | Nothing SHALL execute without an approval record: `execute` is reachable only from `approval`, the executor refuses when `state.approval` is absent, a reject, or carries an idempotency key different from the one computed in step 4 (D22), no parameter of `build_graph`, environment variable or configuration skips the interrupt | The compiled graph's only edge into `execute` comes from `approval`; the executor on a state without approval returns `refused` and applies nothing; a state whose remediation was changed after approval returns `refused`; the approval, execute, graph and `actions/**` modules reference no environment variable | constraints must-not-change 1, profile style note 4 |
| R13 | A proposal whose action is not in the registry SHALL route from rca to `escalate` with reason `action_not_allowed`, never reaching approval or apply; the escalate node records the blocked action in the audit log | Scenario 8 with a model scripted to propose `drop_table` ends `escalated`, `action_not_allowed`, no interrupt, empty ledger, one `escalation` audit record naming `drop_table` | design section 7 step 1, section 6 scenario 8 |
| R14 | `build_graph` SHALL require a checkpointer; the CLI uses `SqliteSaver` on a file with `thread_id` equal to the incident id and invokes with `durability="sync"`; tests use `InMemorySaver`; a run interrupted at approval survives a new process on the same file | A graph built on a fresh `SqliteSaver` over the same file resumes to `resolved`; grep finds `InMemorySaver` only under `tests/` | design section 8, constraints T3 |
| R15 | `verify` SHALL re-read metrics and logs for the window from the incident's opening to thirty minutes after, append the post-action signals as evidence with ids `verify-<round>-<signal id>`, and apply ADR 0003's rule in code; cleared routes to `postmortem`; remaining routes to the router when the last execution applied something and the budget is above zero, otherwise to `escalate` with `symptoms_persist` or `budget_exhausted` | Scenario 6 applies `rollback_deploy`, verify finds the pool still saturated, loops, rca proposes `scale_db_pool`, verify clears, status `resolved`; scenario 4 clears on logs alone | design section 4, ADR 0003 |
| R16 | `postmortem` SHALL ask the model for `Postmortem`, check every cited id exists, write `status=resolved`, retry under the model retry policy, and after three rejected attempts write a code-built postmortem so a verified incident never ends as a failed run | Scripted postmortems resolve seven scenarios; three uncited replies still end `resolved` with a fallback summary | design section 4, increment 2 ship C9 |
| R17 | A failed precondition SHALL append evidence of kind `precondition` and route to the router; a second failure for the same idempotency key SHALL escalate with `precondition_failed` | `scale_db_pool` on a source whose pool is at 0.4 routes to the router with the evidence; the same again escalates | design section 7 step 3 |
| R18 | A process killed after `apply` returns and before the execute node completes SHALL, on `agentops resume <thread>` in a new process, apply nothing again and continue to `resolved`; proof is one ledger entry for the key and exactly one `apply` audit record with `applied: true` | The subprocess test in R19's layout passes on Windows and Linux | design section 8, ADR 0002 |
| R19 | The CLI SHALL offer `run` (ends at approval or escalation, prints the thread id and the pending proposal), `approve <thread> --decision approve|reject|edit [--reason] [--remediation-file] [--operator role]`, `resume <thread>`, `history <thread>` (one line per checkpoint: id, created, next node, status, loops, top hypotheses, evidence count) and `fork <thread> --at <checkpoint id> --set <hypothesis>=<score>` (`update_state` as node `triage`, then continue); every thread id, given or generated, is checked against `SIGNAL_ID_PATTERN` before any file is opened; exit 0 at `awaiting_approval`, `escalated` or `resolved`, 1 for usage (unknown or malformed thread, thread not waiting, bad decision, unknown hypothesis), 2 when the run fails; every model string is escaped; `--model anthropic` stays the exit-1 stub; the hidden `--crash-after-apply` switch reaches the executor only through the run's `configurable` dict, which only `approve` sets | Typer runner tests per command and exit code; `history` on an unknown thread exits 1; `run --thread ../x` exits 1 before any file exists; a fork run continues to approval or escalation and the original history is intact | design sections 8, 9, 13 |
| R20 | `SimulatorSource` SHALL accept a ledger path, refuse one whose file stem is not an incident id matching `SIGNAL_ID_PATTERN`, append each applied action as one JSON line before returning, replay the ledger's worlds at construction, and merge a combined world keyed by the sorted `+`-joined names of all applied actions when the scenario declares one; `ScriptedOutputs` SHALL accept `rca_after_action` and `postmortem`, required for `remediate` and `loop` scenarios; the scripted model answers `rca_after_action` to any rca prompt containing the line `Actions already applied:` | A second source on the same ledger file serves the post-action world; scenario 6 declares `rollback_deploy+scale_db_pool`; scenario validation rejects a remediate scenario without a postmortem | design section 6, ADR 0002 |
| R21 | README, CLAUDE.md, CONTRIBUTING.md, docs/ci.md and docs/sdlc/codebase-map.md SHALL describe the new commands, the audit and checkpoint files, the crash demo and the floor; the repo-document test asserts the new sentences and that "arrives in increment 3" is gone | Repo-document test green | profile style notes, CLAUDE.md |
| N1 | The full suite SHALL finish under 120 s on the owner's machine and in CI | `pytest -q` wall time from the log | increment 2 N1 |
| N2 | Passing tests SHALL be at or above `MIN_TESTS` with the floor set per R5 | `scripts/check_test_count.py` exit 0 in CI | R5 |
| N3 | `run` then `approve` of scenario 1 through the CLI SHALL finish under 10 s together | Timed in the CLI lifecycle test | design section 8 demo |

## Design

### Architecture

The graph (`src/agentops/graph.py`) becomes design section 4 in full:
`START -> triage -> router -> logs | metrics | deploys -> merge -> rca -> approval -> execute ->
verify -> postmortem -> END`, with `merge -> escalate`, `rca -> escalate`, `approval -> approval`
(invalid edit), `approval -> router` (reject), `execute -> router | escalate` (precondition
failure, refusal), `verify -> router | escalate`. The router stays a conditional edge
(`route_to_sources`); edges leaving approval, execute and verify return its result when they loop
back. `build_graph(source, model, registry, audit, checkpointer)` injects the registry and the
audit log into the nodes that need them and requires a checkpointer (D4).

New components, one paragraph each:

- `src/agentops/actions/runbook.py`: the typed runbook. Pydantic models with unknown keys
  forbidden for an action entry, its parameter schema, a precondition and a rollback declaration;
  `load_runbook(path)` parses YAML (`pyyaml`, moved to runtime, D2) and validates. Depends on
  nothing in the graph.
- `src/agentops/actions/registry.py`: `Registry` built from a runbook; `has`, `get`,
  `validate_params`, `names`. `default_registry()` loads `runbooks/default.yaml` from the
  package's repository root. Also the pure helpers `idempotency_key(incident_id, remediation)`
  and `dry_run_description(entry, remediation)`, shared by the approval node and the executor.
- `src/agentops/actions/preconditions.py`: `evaluate(entry, remediation, state, source)` runs
  each declared check through one function per kind; every function catches source errors and
  returns a failed result. Reads the source only.
- `src/agentops/actions/audit.py`: `AuditLog(directory)` with `append(incident_id, record)`;
  opens `<directory>/<incident_id>.jsonl` in append mode for each write, writes one line, flushes
  and syncs, closes. Refuses an incident id outside `SIGNAL_ID_PATTERN`.
- `src/agentops/actions/executor.py`: `execute(state, source, registry, audit, crash_after_apply)`
  returning an `ExecutionOutcome` (kind, execution record, evidence, actions). The seven steps
  are seven named functions called in order from one short body.
- `src/agentops/nodes/approval.py`, `execute.py`, `verify.py`, `postmortem.py`: thin nodes and
  their edges `after_approval`, `after_execute`, `after_verify`. The verify rule and its
  thresholds live in `verify.py` beside a reference to `METRIC_CATALOGUE`.
- `src/agentops/__main__.py`: three lines so `python -m agentops` runs the Typer app (the
  subprocess test and the README use it).

Changed modules: `state.py` (new literals, fields and models below), `nodes/__init__.py`
(lookalike table, format-character strip, `fence_markers(nonce)`), `rca.py` (nonce fence,
`Unreachable sources:` and `Actions already applied:` lines, registry check writing
`action_not_allowed`, `proposal` audit record, `after_rca` outage rule), `escalate.py` (nonce
fence, `escalation` audit record), `merge.py` (`unreachable_sources`, `source_outage`),
`router.py` (`affordable` reserves attempts), `evidence.py` (`unreachable_sources(state)`
helper beside `source_read_attempts`), `fakes.py` (marker answers and the postmortem queue),
`sources/scenario.py` and `simulator.py` (R20), `cli.py` (R19), seven scenario files
(postmortems; scenario 6 also `rca_after_action` and the combined world).

### Data

No database tables. State changes in `src/agentops/state.py`:

- `EscalationReason` adds `source_outage`, `action_not_allowed`, `precondition_failed`,
  `apply_failed`, `symptoms_persist`.
- `EvidenceKind` adds `action` (apply outcome), `precondition` (failed check), `operator`
  (reject reason).
- `IncidentState` adds control fields `unreachable_sources: list[str]` and
  `approval_error: str | None`; both join `CONTROL_FIELDS` and are absent from every model output
  schema (the existing test over `MODEL_OUTPUT_SCHEMAS` covers them).
- `ExecutionRecord` adds `action`, `target` and `applied: bool`.
- `Approval` adds `idempotency_key: str`, the key of the remediation the operator saw (D22).
- New `ApprovalDecision` (strict): `decision`, `reason` (at most 1000 characters, required for
  reject), `remediation: Remediation | None` (required for edit), `operator: OperatorRole`
  defaulting to `operator`. A role, never a name (compliance control 1). The reason is free text
  that reaches the audit file, the checkpoint and the rca prompt; the `--reason` help text says
  to describe the evidence, not people. A name typed there is the profile's GDPR trigger and is
  recorded as such for later phases.

Files written at run time: the checkpoint file (default `agentops.sqlite` in the working
directory, `--state-db` to override) and its WAL sidecars `agentops.sqlite-wal` and
`agentops.sqlite-shm` (confirmed: `SqliteSaver` in the 3.1.1 wheel runs
`PRAGMA journal_mode=WAL`), `audit/<incident_id>.jsonl` and `audit/<incident_id>.ledger.jsonl`
(the simulator's ledger, D9). `.gitignore` covers `*.sqlite` and `audit/` (confirmed) but not
the sidecars; D23 adds `*.sqlite-*` and the verification `git ls-files` check names that pattern.
Who writes: only the audit log module writes the audit file; only the simulator writes the
ledger; only LangGraph writes the checkpoint file. Nothing updates in place: both JSONL files are
append-only, and a test asserts the audit file never shrinks across a run. Contents are synthetic
scenario data plus the operator role string and the reason text.

Scenario schema (`sources/scenario.py`): `ScriptedOutputs` gains `rca_after_action` and
`postmortem` (both optional, validated as required by `expected_outcome`); `post_action_world`
keys may be a single action name or sorted distinct names joined by `+`, each matching the
signal id pattern.

### Interfaces

Runbook entry shape (YAML, one list item per action; the loader rejects anything else):

```yaml
- action: scale_db_pool
  target_kind: database
  params: { pool_size: { type: integer, min: 8, max: 128, required: true } }
  risk: medium
  preconditions: [{ check: metric_above, metric: db_pool_utilisation, value: 0.85 }]
  dry_run: describe
  rollback: { action: scale_db_pool, params_from: previous }
  idempotent: true
```

The five declared actions and their checks (targets and parameters match the scripted
remediations in `scenarios/*.json`, confirmed): `scale_db_pool` (database; `pool_size` integer
8 to 128; `metric_above db_pool_utilisation 0.85`; medium), `rollback_deploy` (deployment;
optional `to_version` matching `^v[0-9][0-9a-z.-]*$`; `deployment_within_minutes 120` and
`previous_version_available`; high), `circuit_break_dependency` (dependency; `fallback`
snake_case string; `dependency_not_healthy` on the target; medium), `rerun_partition` (job;
`partition` matching `^[0-9]{4}-[0-9]{2}-[0-9]{2}$`; `metric_below rows_written 1`; low),
`pause_batch_job` (job; no parameters; `config_true batch_jobs_enabled`; low).

Interrupt payload (JSON object): `remediation` (the model's object), `evidence` (the cited
items), `dry_run` (one sentence from the registry entry and the parameters), `rollback` (the
runbook declaration plus the model's rollback sentence), `unreachable_sources`, `error`
(present only after an invalid edit), `incident_id`, `idempotency_key`.

Resume value: `{"decision": "approve" | "reject" | "edit", "reason": str, "remediation": object,
"operator": "operator" | "on-call" | "reviewer"}`.

Audit record: `{"event", "at", "incident_id", "thread_id", "idempotency_key", ...}` with
per-event fields: `proposal` (action, target, params, evidence ids, risk), `approval` (decision,
operator, reason, edited, valid, error when not valid), `precondition` (check, passed, observed),
`dry_run` (description), `apply` (applied, ok, detail), `verify` (round, cleared, unhealthy
metrics, error lines), `escalation` (reason, proposed action when one exists). `thread_id` is
read from the run's `configurable`, so a fork's records are told apart from the original's.

CLI (all commands take `--model scripted|anthropic`, `--state-db`, `--audit-dir`,
`--scenarios-dir`): `run --scenario <id> [--thread <id>]`; `approve <thread> --decision <d>
[--reason <text>] [--remediation-file <json>] [--operator <role>] [--crash-after-apply, hidden]`;
`resume <thread>`; `history <thread>`; `fork <thread> --at <checkpoint id> --set <name>=<score>`
(repeatable). Error contract: one escaped sentence on stderr and the exit codes in R19.

### Security and privacy

Threats considered: a model steered by evidence proposing a destructive action (R13, scenario
8); an edit smuggling an out-of-registry action or an out-of-range parameter (R11); a forged
fence marker in a log line (R4); a double apply after a crash (R18); an audit file rewritten or
truncated (R10); a path traversal through an incident id (R10); an operator identity leaking
into checkpoints and traces (operator is a role literal). Controls: registry at rca and again in
the executor, parameter schema, preconditions against the source, idempotency key and ledger,
append-only JSONL, id pattern check, nonce fences and the lookalike table, no environment
variable in the approval path (R12).

Baseline checklist items applied in this change (no auditor at tier 3 replaces the security
reviewer; this is the designer's pass): new third-party imports pinned to exact versions with
their licences (D1, `langgraph-checkpoint-sqlite==3.1.1`, MIT; D24, `aiosqlite==0.22.1`, MIT,
and `sqlite-vec==0.1.9`, MIT or Apache-2.0, a platform wheel; all three confirmed from a
`pip install --dry-run --report` resolution on 2026-10-01); no new secret or environment
variable; the new privileged capability (apply) and every operator decision write to an
append-only log (D21); runtime files covered by `.gitignore` patterns including the WAL sidecars
(D23), to be confirmed with `git ls-files` at verification; no storage bucket, policy or database
function. Data categories: synthetic only; retention unchanged (profile).

Accepted trust assumption: `resume` deserialises the checkpoint file, so whoever can write that
file controls the state that reaches `execute`. Acceptable for a local, single-user CLI; the
executor's registry, parameter and key checks (R12, D22) still bound what such a state can do,
and the audit file records it. Revisit before any shared or hosted deployment.

### Failure modes

| Dependency | Slow or down | Returns garbage | What is retried, surfaced, logged |
|---|---|---|---|
| `EvidenceSource` reads | One in-node retry as today; a source dead in every attempt becomes `unreachable_sources` and, with no evidence, `source_outage` (R1, R2) | Pydantic rejects it at the boundary | Both attempts in `actions`; reason in state and in the report |
| `EvidenceSource.apply` | The simulator never raises; a raising adapter gets an `apply` audit record with `ok: false` and the error type, then the execute node fails, the run exits 2 with no execution record; `resume` re-runs the node, and the ledger check guards a double apply (R9, R18) | `ok=False` becomes `apply_failed`, evidence of kind `action`, escalate | `apply` audit record, `actions` entry |
| Preconditions through the source | A source error fails the check (R8) | A missing metric fails the check with `observed: null` | `precondition` audit record and evidence (R17) |
| Model (scripted today) | Retry policy on rca and postmortem; postmortem falls back to a code-built record (R16) | Validation and citation errors are retried, then fall back or fail as in increment 2 | `actions` entries per attempt |
| SQLite checkpoint file | A locked or missing file fails the command with exit 2 and a sentence naming the path; nothing is applied before the checkpoint exists (sync durability) | Not applicable: LangGraph owns the format | stderr sentence |
| Audit directory | Unwritable: the dry-run append raises before apply; exit 2, nothing applied (R10) | Not applicable | stderr sentence |
| Ledger file | Unreadable or corrupt line: the simulator refuses to start with the line number (R20) | Same | stderr sentence |
| Operator | No decision: the thread waits forever at the checkpoint; `history` shows it | A bad resume value interrupts again with the error (R11) | `approval_error` in state |

### Observability

The CLI report gains `thread`, `approval`, `execution` (key, applied, result), `verify` (round,
cleared, unhealthy metrics) and `postmortem` lines, and names the audit file. `history` is the
on-call view of a thread. `actions` records every apply, verify read and model attempt. No
LangSmith wiring in this change (increment 4).

## Alternatives considered

| Option | Why not |
|--------|---------|
| Keep `build_graph` compiling without a checkpointer | `interrupt()` requires one; a graph that works until approval and then fails is a trap |
| Store the runbook as JSON to avoid moving `pyyaml` | The request names a YAML runbook, and the file is read by humans at review |
| Put history and fork in increment 4 as the design table does | The request names them; they are thin over `get_state_history` and `update_state` and share the CLI task (D15) |
| Validate edits inside the approval node with a loop | Forbidden by LangGraph's resume rules (ADR 0001) |
| Compare metrics with their first point to decide "cleared" | Fails scenario 5, which starts in the bad state (ADR 0003) |

## Decisions

[0001](./adr/0001-revalidate-edited-remediations-through-a-conditional-edge-loop.md) edit
re-validation through an edge loop; [0002](./adr/0002-exactly-once-from-a-persisted-ledger-and-a-crash-switch-on-the-cli.md)
persisted ledger and the crash switch; [0003](./adr/0003-verify-with-a-code-rule-over-healthy-thresholds-and-post-action-logs.md)
the verify rule; [0004](./adr/0004-reserve-both-read-attempts-when-the-router-decides-affordability.md)
the budget cap and the outage reason; [0005](./adr/0005-fence-untrusted-evidence-with-a-per-prompt-nonce-and-a-lookalike-table.md)
nonce fences. The full decision table with recommendations is in [brief.md](./brief.md), D1 to D24.

## Open questions

None left open: every question became a decision row in the brief. Five need the owner because
they touch ask-first paths: D1, D2 and D24 (`requirements*.txt`), D3 (`runbooks/default.yaml`),
D13 (`.github/workflows/ci.yml`) and D23 (`.gitignore`). The request names the first four; the
consent for all five counts only through the G2 approval of the brief, and the plan assumes it.

## Response to audit

Four medium findings, each now a decision row in the brief with the recommendation taken:

- "Operator decisions that fail validation leave no audit record": D21. R11 now writes an
  `approval` record for every resume value with `valid` and `error`; the Interfaces record shape
  carries both fields; plan T5 implements and E24 and E46 assert the record.
- "The approval is not bound to the action it approved": D22. `Approval` gains
  `idempotency_key` (Data); R12 makes the executor refuse on a mismatch; E55 asserts it in T4.
- "`*.sqlite` does not match SQLite sidecar files": D23, and WAL mode is now confirmed from the
  wheel, not believed. T8 adds `*.sqlite-*` to `.gitignore` (ask-first, needs the G2 approval)
  and the verification `git ls-files` check names the pattern.
- "Packages that `langgraph-checkpoint-sqlite` pulls in have no pin": D24. `aiosqlite==0.22.1`
  and `sqlite-vec==0.1.9` are pinned with licences in T2; versions and licences confirmed from
  the resolver. `langgraph-checkpoint` is already pulled by `langgraph` and stays unpinned, as
  every existing transitive package is; changing that convention is outside this change.

The eleven low findings are folded into R6 (`safe_load`), R7 (`fullmatch`, length 64), R9 (which
step writes the `apply` record, raising adapters), R10 (`thread_id`), R12 (`actions/**` in the
environment-variable check), R19 (thread id pattern, the crash switch only through
`configurable`), R20 (ledger path check), Data (the reason free text), Security (the checkpoint
trust assumption) and Open questions (consent through G2). No finding changed a task boundary.

## Constraint audit

Filled by the constraint auditor. Severity: high blocks G2.

Audit result: pass

Auditor scope, 2026-10-01: spec.md, plan.md, brief.md, `.workhorse/profile.yml` and the repo
CLAUDE.md only, as instructed. evals.md, approvals.md, `.gitignore`, `requirements*.txt` and the
code were not read, so every claim the spec marks `confirmed` about them is accepted as the
designer's claim, not re-verified here. Compliance regimes in the profile: none; GDPR is
`possibly_applicable` only, so it is checked as a trigger, not as a regime.

| Severity | Finding | Requirement affected | Resolution |
|----------|---------|----------------------|------------|
| medium | Operator decisions that fail validation leave no audit record. R10's event list and R11 audit only accepted decisions; an edit that names `drop_table`, `pool_size` 10000 or an uncited id (E45, E46, E24) is recorded only as `approval_error` in checkpoint state, which `fork` and a deleted SQLite file can lose. The approval step is the privileged capability, and an attempted smuggle through it is the event an investigator most needs. (security baseline: new admin capability writes to an append-only log; profile style note 6) | R10, R11 | open (proposed D21) |
| medium | The approval is not bound to the action it approved. R12 says the executor refuses when `state.approval` is absent or a reject, but nothing makes it refuse an approve record whose remediation or idempotency key differs from the one it is about to apply. Graph wiring prevents that today; R12 presents the executor check as an independent second control, and as written it is not one. (profile style note 4: nothing executes without an approval record; design section 7) | R9, R12 | open (proposed D22) |
| medium | The `.gitignore` pattern `*.sqlite` does not match SQLite sidecar files (`agentops.sqlite-wal`, `-shm`, `-journal`). LangGraph's `SqliteSaver` turns on WAL mode during setup (believed, not verified), so a run would leave untracked sidecar files containing checkpoint state. The plan states no task edits `.gitignore`, and that path is ask-first. (security baseline: runtime files covered by a pattern, confirmed with `git ls-files`; CLAUDE.md protected paths) | R14, spec Data | open (proposed D23) |
| medium | Packages that `langgraph-checkpoint-sqlite==3.1.1` pulls in (believed `aiosqlite` and `sqlite-vec`, the second a native extension, per plan Risks) have no pin, licence or review in D1. Whether `requirements.txt` already pins transitive packages was not checked. (CLAUDE.md: every dependency is an exact pin with its licence; security baseline: runtime import pinned to an exact version) | D1, R14 | open (proposed D24) |
| low | The executor signature gains `crash_after_apply`, a process-exit switch inside the tier-3 boundary, and the CLI hides it from `--help`. That conflicts with D11's reasoning that a switch is "visible in a review". It can only abort, but nothing in the spec stops it from being reached outside `approve`. (profile style note 4) | R18, D11 | open |
| low | After a crash between apply and the `apply` audit write, the spec does not say which step writes the single `apply` record with `applied: true` on resume, because the ledger check returns `already_applied` and skips step 7. R18's acceptance check would catch a missing record, but the builder needs the rule. (security baseline: side effects with no return value vanish silently) | R9, R18 | open |
| low | A source adapter that raises inside `apply` ends with exit 2 and a stderr sentence, with no `apply` audit record (Failure modes, row 2). An apply whose outcome is unknown should be recorded. (security baseline: no failure path both silent and consequential) | R9, R10 | open |
| low | Only `AuditLog` checks the incident id against the signal id pattern. The simulator ledger path `audit/<incident_id>.ledger.jsonl` and the `run --thread <id>` option (which D5 says always equals the incident id) have no stated check. (security baseline: verify input at each boundary; R10 traversal threat) | R10, R19, R20, D5 | open |
| low | The runbook loader must use `yaml.safe_load`; the spec only says "parses YAML". (OWASP A08, unsafe deserialization) | R6 | open |
| low | The parameter `pattern` checks should use `re.fullmatch` (in Python, `$` also matches before a trailing newline, so `v1\n` passes `^v...$`), and string parameters (`to_version`, `fallback`) need a maximum length. (security baseline: bounded free text) | R7 | open |
| low | R12's "no environment variable" check covers the approval, execute and graph modules only, not `actions/**` (executor, registry, `default_registry()`), which sit on the same path. (profile style note 4) | R12 | open |
| low | Audit records carry no thread or checkpoint id. A `fork` uses the same incident id and appends to the same audit file, so records from the fork and from the original run cannot be told apart. (security baseline: an audit log must answer who did what) | R10, R19 | open |
| low | Resume deserializes the SQLite checkpoint file, so anyone who can write that file controls the state that reaches `execute`. Acceptable for a local single-user CLI; record it as an accepted trust assumption in the security section. (OWASP A08; verify before asserting) | R14 | open |
| low | `ApprovalDecision.reason` (up to 1000 characters of free text) goes into the audit file, the checkpoint and, as `operator` evidence, the model prompt. A name typed into it is the trigger the profile names for re-checking GDPR. The operator role literal is correctly handled (E51). (profile compliance.possibly_applicable.gdpr; controls_for_later_phases 1) | R11 | open |
| low | D1, D2, D3 and D13 edit ask-first paths on the grounds that "the request authorises" them. That consent counts only through the G2 approval of this brief; approvals.md was not read. (CLAUDE.md Protected: ask first) | D1, D2, D3, D13 | open |
