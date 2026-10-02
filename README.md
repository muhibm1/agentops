# AgentOps

AgentOps is a LangGraph incident-response runtime. It investigates a simulated production
failure, proposes a remediation backed by cited evidence, stops for a human approval, and
executes only allowlisted actions, each one exactly once. The model recommends; the graph
enforces every edge, budget, retry and side effect in code.

Status: increment 3, act, pause, persist and resume. The graph runs triage, parallel evidence
collection (logs, metrics, deploys), a merge step with an investigation budget, root-cause
analysis, a human approval, execution of an allowlisted action, verification and a postmortem,
with an escalation path, over eight simulated scenarios with a scripted model, so no API key is
needed: `clean_bad_deploy`, `deploy_red_herring_db`, `dependency_timeout_noisy_neighbour`,
`late_partition_zero_rows`, `schema_drift_ingestion`, `two_concurrent_faults`,
`ambiguous_two_causes` and `log_injection_drop_table`. A run first stops at
`awaiting_approval` (a root cause and a remediation, citing evidence) or ends `escalated` (a
hand-off summary and the reason, such as `budget_exhausted`, `ambiguous`, `no_evidence` or
`action_not_allowed`). After an approval the action is applied once, the symptoms are checked
again, and the thread ends `resolved` with a postmortem or goes back to investigate. The live
model and the evaluation harness arrive in later increments.

To try it, follow the setup in [CONTRIBUTING.md](CONTRIBUTING.md). The five commands, run from
the repository root:

| Command | What it does |
|---|---|
| `agentops run --scenario clean_bad_deploy --model scripted` | Investigates until the approval pause or an escalation and prints the thread id |
| `agentops approve <thread> --decision approve` | Records your decision (`approve`, `reject --reason ...` or `edit --remediation-file ...`) and continues |
| `agentops resume <thread>` | Continues a thread that stopped before its end; a thread waiting for a decision is only shown |
| `agentops history <thread>` | One line per checkpoint, newest first |
| `agentops fork <thread> --at <checkpoint> --set <hypothesis>=<score>` | Starts a new thread from a past checkpoint with changed hypothesis scores |

`python -m agentops` runs the same commands. Every command except `history` prints the thread
id, status, loops, remaining budget, hypotheses and evidence count, then the pending proposal,
the escalation, or the outcome of the action, and where the audit log is.
Exit codes: 0 when the thread waits for approval, is escalated or is resolved; 1 for a usage
problem, including a decision the approval step refused and asked again; 2 when the run fails or
the state file cannot be used.

Options shared by the thread commands: `--state-db` (the checkpoint file, default
`agentops.sqlite`), `--audit-dir` (audit logs and ledgers, default `audit/`) and
`--scenarios-dir` (default `scenarios/`). `approve` also takes `--operator`, your role: one of
`operator` (the default), `on-call` or `reviewer`. The role is recorded in the audit log; it is
a label, not a login, and any role may approve.

Every thread is checkpointed to the SQLite file `agentops.sqlite` (`--state-db`) under a thread
id equal to the incident id. Every proposal, decision, precondition, dry run, apply and verify is
appended to `audit/<incident_id>.jsonl`, and the simulated system's record of applied actions is
`audit/<incident_id>.ledger.jsonl` (both under `--audit-dir`). These runtime files are
git-ignored. Scenario files are read from `--scenarios-dir`, which defaults to `scenarios`;
every path is resolved relative to the current directory.

## langgraph dev

`langgraph dev`, run from the repository root after installing the development requirements
(they pin `langgraph-cli[inmem]==0.4.32`), serves the graph to LangGraph Studio: `langgraph.json`
points it at `make_graph` in `src/agentops/dev_server.py`, which builds the graph for
`configurable.scenario_id` (default `clean_bad_deploy`) with the scripted model. What it sends
off the machine, confirmed by reading the installed source of `langgraph-cli` 0.4.32 and
`langgraph-api` 0.15.1: the CLI sends anonymous usage analytics (operating system and its
version, Python and CLI versions, the command name, and which options were changed but not
their values) to a Supabase endpoint each time `dev`, `up`, `build`, `dockerfile`, `validate` or
`new` runs (`langgraph_cli/analytics.py`; the endpoint is `SUPABASE_URL` in
`langgraph_cli/constants.py`). Set `LANGGRAPH_CLI_NO_ANALYTICS=1` to turn that off; only the
value `1` counts. `langgraph dev` then opens LangGraph Studio in your browser at
`https://smith.langchain.com/studio/?baseUrl=<the local server>` (`langgraph_api/cli.py`; your
configured LangSmith host instead when LangSmith tracing is switched on), a page
hosted by LangSmith that talks to the local server from your browser; `--no-browser` skips it
and `--studio-url` names another Studio host. CI installs the CLI with the development
requirements, but no test and no CI step runs it.

## When it fails

Start with `agentops history <thread>` and the audit file `audit/<incident_id>.jsonl`: the last
line says what was reached. Every thread is saved at each step, so stopping is safe.

- Exit 2, "The run failed": the step that failed is named on stderr. Fix the cause (for example
  a full disk) and run `agentops resume <thread>`. Resuming is safe: an action is applied only
  after its dry run is in the audit log, and a key already in the ledger is never applied again.
- Exit 2, "The state file ... cannot be used": the `--state-db` path is not a usable SQLite
  file. Do not delete `agentops.sqlite`; it holds every checkpoint and the only record of where
  a thread stopped. Point `--state-db` at the right file, or copy the file aside first.
- Audit log cannot be written (the audit directory is a file, read-only or full): the run
  stops before the action is applied, with exit 2. Fix the directory and `resume`. If it fails
  after the action was applied, `resume` finds the key in `audit/<incident_id>.ledger.jsonl`,
  applies nothing again and writes the missing `apply` record from the ledger.
- `escalation: apply_failed`: the source refused or failed the action and nothing was changed.
  Read the `apply` line in the audit file for the reason. The thread is handed to a human;
  `resume` does not retry it.
- `approve` exits 1 with "The decision was not recorded": the approval step refused the answer
  (for example an edit naming an action outside the runbook). Nothing was applied and the
  thread still waits; run `approve` again with a valid decision.

The crash demo shows that an action is applied exactly once even when the process dies right
after applying it. Three separate processes:

```
agentops run --scenario clean_bad_deploy --model scripted --thread crash-demo
agentops approve crash-demo --decision approve --crash-after-apply
agentops resume crash-demo
```

The second command ends the process right after the action is applied and recorded, so it exits
non-zero. The third resumes from the last checkpoint, finds the action in the ledger, applies
nothing again and ends `resolved`. The ledger and the audit log each hold one applied record.
`--crash-after-apply` is a hidden demo switch; it skips no check and cannot skip the approval.

The design, its decisions and the plan for each increment are in
[docs/superpowers/specs/2026-09-29-agentops-design.md](docs/superpowers/specs/2026-09-29-agentops-design.md).
How the code is laid out is in [docs/sdlc/codebase-map.md](docs/sdlc/codebase-map.md).
What CI checks, and the known gaps it accepts, are in [docs/ci.md](docs/ci.md). Licensed under
the [MIT License](LICENSE).
