# AgentOps

AgentOps is a LangGraph incident-response runtime. It investigates a simulated production
failure, proposes a remediation backed by cited evidence, stops for a human approval, and
executes only allowlisted actions, each one exactly once. The model recommends; the graph
enforces every edge, budget, retry and side effect in code.

Status: increment 4, the measurement harness, plus the live model path. `agentops eval` scores
the graph against a ReAct baseline, offline with the scripted model or on a live Anthropic model
behind a spend guard (see [Measuring it](#measuring-it)); the first paid comparison has not been
run yet. The graph runs triage, parallel evidence
collection (logs, metrics, deploys), a merge step with an investigation budget, root-cause
analysis, a human approval, execution of an allowlisted action, verification and a postmortem,
with an escalation path, over eight simulated scenarios with a scripted model, so no API key is
needed: `clean_bad_deploy`, `deploy_red_herring_db`, `dependency_timeout_noisy_neighbour`,
`late_partition_zero_rows`, `schema_drift_ingestion`, `two_concurrent_faults`,
`ambiguous_two_causes` and `log_injection_drop_table`. A run first stops at
`awaiting_approval` (a root cause and a remediation, citing evidence) or ends `escalated` (a
hand-off summary and the reason, such as `budget_exhausted`, `ambiguous`, `no_evidence` or
`action_not_allowed`). After an approval the action is applied once, the symptoms are checked
again, and the thread ends `resolved` with a postmortem or goes back to investigate. Every
command that calls a model also takes `--model anthropic`; see [The live model](#the-live-model).

To try it, follow the setup in [CONTRIBUTING.md](CONTRIBUTING.md). The five thread commands, run from
the repository root (`agentops eval` and `agentops dataset` are described in
[Measuring it](#measuring-it)):

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

## Measuring it

`agentops eval --model scripted` runs the graph and a ReAct baseline (an agent built with
LangChain's `create_agent` over six tools and the same simulator, with no approval step and no
allowlist) over the dev split, grades every run, and prints the results. It needs no API key and
calls no model service. The first lines are the header, which with the
scripted model always includes this sentence:

```
Scripted model: these numbers measure the harness, not a model.
```

Read it literally. The scripted model replays answers written into each scenario file, for the
graph and for the baseline alike (the baseline's answers are `scripted_outputs.baseline`, written
as a plausible naive agent, so it takes the bait in the trap scenarios). The table shows that
the measurement runs end to end and what each evaluator rewards; it says nothing about how well
a model diagnoses incidents. That comparison needs the live model; see
[The live model](#the-live-model).

The table has one row per system: runs, then pass count and percentage for
`root_cause_accuracy`, `correct_outcome`, `evidence_sufficiency`, `false_remediation` and
`safety`, then the mean, minimum and maximum of tool calls, latency, input, output, cache-read
and cache-write tokens, and `cost_usd` (the measured dollar cost per run). Every failing system,
scenario, repetition and evaluator is listed after it, so the failures are part of the result.
One is known and expected: the graph fails `root_cause_accuracy` on `two_concurrent_faults`,
because that scenario needs two actions, the graph's final diagnosis is the second fault
(`database_saturation`, after the rollback leaves the pool saturated) and grading reads the
final diagnosis, while the scenario's ground truth names the first (`recent_deploy_regression`).
A run that raises is a `failed` row naming the exception type, not a crash; an error inside one
of the baseline's tools is caught by that tool and handed back to the agent as text, as a real
agent would see it.

Options: `--system graph|baseline|both` (default `both`), `--repetitions` (1 to 10, default 1),
`--json <path>` (also writes one JSON object per run with every evaluator result),
`--scenarios-dir` (default `scenarios/`) and `--evals-dir` (default `evals/`, where the frozen
split's manifest and checksums are read). Each run gets a fresh simulator, an audit log and a
SQLite checkpoint file in a temporary directory that is removed afterwards, and an automatic
approval by the role `operator`; apart from the `--json` file, nothing is written under the
working directory. With `--model anthropic` the spend guard runs first; see
[The live model](#the-live-model).
Exit codes: 0 when the measurement completed, even with failing rows; 1 for a usage problem
(including a tracing problem or a checksum mismatch); 2 when a scenario or dataset file cannot
be read.

### The live model

`--model anthropic` runs the graph's four model nodes, the baseline and the judge on Claude
through `langchain-anthropic`. It works on `run`, `approve`, `resume`, `fork` and `eval`:

```
export ANTHROPIC_API_KEY=...        # read from this shell only, never from a file
agentops run --scenario clean_bad_deploy --model anthropic --dev
agentops eval --model anthropic --dev            # prints the plan and the estimate, exits 1
agentops eval --model anthropic --dev --yes      # spends it
```

The variables:

- `ANTHROPIC_API_KEY`: the key, read from the process environment and passed to the client
  explicitly. Without it every live command exits 1 naming the variable, before any file is
  written. The key's value is replaced by `[redacted]` in anything the command line prints.
- `AGENTOPS_MODEL_<ROLE>`: overrides one role's model id, for `TRIAGE`, `RCA`, `POSTMORTEM`,
  `ESCALATE`, `BASELINE` or `JUDGE`. A blank value counts as unset.
- `AGENTOPS_TEST_ANTHROPIC_API_KEY`: read only by the live smoke test (below), never by the
  command line.

Every role uses `claude-sonnet-5-5` by default; `--dev` puts every role on the cheaper
`claude-haiku-4-5-20251001`. The API address is fixed in code, so a stray `ANTHROPIC_BASE_URL`
cannot send the key elsewhere.

The spend guard: `agentops eval --model anthropic` first runs every usual check (repetitions,
systems, tracing, scenarios, checksums), then prints `planned: <n> runs ... plus <m> judge calls
on <ids>` and `estimated cost: $<x.xx>`, an upper bound priced role by role from the price table
in `src/agentops/pricing.py`, and exits 1 unless `--yes` is given. When any role's id is not in
the price table it prints `estimated cost: unknown (unpriced: <id>)` instead, never a partial
figure, and `--yes` is still required. Nothing has called a model when the guard stops. The
prices were transcribed from Anthropic's pricing page as of 2026-10-09 (`PRICES_AS_OF`); check
them there before trusting a figure. With the live model the judge also scores every graph run
for `explanation_quality`: its pass count is a column of the table, and its own cost is a
`judge cost:` line under it, apart from the systems' cost. A model id the price table does not
name prints `n/a (unpriced: <id>)` in the `cost_usd` column, never a guess; on the scripted
model that is always `n/a (unpriced: scripted)`.

Nothing in the tests or in CI's default job calls a model: the test suite blocks every
non-loopback network connection, and CI sets no key. One test, marked `live_smoke`, makes a
single triage call on the dev model when `AGENTOPS_TEST_ANTHROPIC_API_KEY` is set, and is
skipped otherwise. Run it with
`pytest -q -m live_smoke tests/test_live_smoke.py`, or by hand in GitHub Actions through
`.github/workflows/live-smoke.yml`. `AGENTOPS_TEST_ANTHROPIC_API_KEY` must come from a
separate Anthropic workspace with its own monthly spend limit, recorded in
`docs/hosted-config.md`; the app key must not be reused for tests.

What leaves the machine on a live run: the prompts, which hold the incident's symptoms and the
evidence the simulator returns, go to Anthropic. So do a reject reason or an edited remediation
typed at the approval step: on a live run, anything typed at approval is
sent to the model provider with the next prompt, so never type a secret there.

### The frozen split

Eight scenarios are split five for development and three held out. The held-out ids are in
`evals/held_out/manifest.json`, and `evals/checksums.sha256` pins the sha256 of that manifest and
of the three held-out scenario files. `agentops dataset` checks them (the default, `--check`)
and exits 1 naming the first mismatch; `agentops dataset --write` writes them and refuses to
overwrite a pinned split without `--force`.

`agentops eval --held-out` scores the held-out split. It first runs the same checksum check and
exits 1 naming the first mismatch before any run, then prints a warning that the split is frozen.
Run it only when a version is frozen, and never change the system to improve its numbers: a
change steered by the held-out scores turns them into development scores. No test and no CI
step scores the committed held-out split.

### Tracing

Tracing to LangSmith is opt-in and off by default. It is on only when `LANGSMITH_TRACING` (or
one of `LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING_V2`, `LANGCHAIN_TRACING`) is truthy (`1`,
`true`, `yes` or `on`, any case) and `LANGSMITH_API_KEY` is set. A falsy value (`0`, `false`,
`no`, `off` or empty) counts as unset, so tracing stays off whether or not a key is present.
A truthy switch without a key, or a switch set to anything else, is a problem: `agentops eval`
exits 1 naming the variable, never its value. Only `agentops eval` checks this rule; the other
commands (`run`, `approve`, `resume`, `history`, `fork`, `dataset`) do not read the switches, so
a shell that has a switch and a key set will still let the LangChain libraries trace them. Unset
the switches in a shell where that is not wanted. When tracing is on, traces go to the project in
`LANGSMITH_PROJECT`, default `agentops`, and the header says so. A trace holds the prompts, the
model outputs and the scenario's synthetic evidence. The tests clear all four switches and the LangSmith
and LangChain key and endpoint variables before every test, and CI
sets none of them.

### langgraph dev

`langgraph dev`, run from the repository root after installing the development requirements
(they pin `langgraph-cli[inmem]==0.4.32`, and `langgraph-api` and `langgraph-runtime-inmem`, which are
licensed Elastic-2.0, a source-available licence that is not MIT: fine for local development,
but it bars offering them as a hosted service), serves the graph to LangGraph Studio: `langgraph.json`
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
- `escalation: apply_failed`: the source answered that the action failed and nothing was
  changed. Read the `apply` line in the audit file for the reason. The thread is handed to a
  human; `resume` does not retry it.
- `escalation: effect_unknown`, with "An approved action may have been applied; its effect is
  unknown.": the source raised while applying the action, so the change may or may not have
  taken. The `apply` line in the audit file has `applied: null`, `effect: "unknown"` and the
  exception type. The graph checked the symptoms anyway: had they cleared, the thread would have
  ended `resolved`. They did not, and an action whose effect is unknown is never followed by
  another proposal, so the thread is handed to a human. Find out from the system itself whether
  the action took before doing anything else.
- `ledger: skipped torn line <n> in <path>` on stderr: a process ended while writing a line of
  the ledger `audit/<incident_id>.ledger.jsonl`, leaving part of a line. That fragment is not a
  complete record, so the simulator skips it and the command carries on. The next action written
  to the ledger starts on a fresh line after a `{"torn_seal": true}` marker line, so later
  `resume` and `history` runs keep reading the file; the notice repeats each time the ledger is
  read and is not a new problem. Any other line that is not a valid record
  stops the command with exit 2, "The ledger could not be used"; do not edit or delete the
  ledger, which is the only record of what was applied.
- `agentops eval` or `agentops dataset` exits 1 naming a mismatch: "The frozen split does not
  match its checksums". A scenario file or the manifest changed after the checksums were
  written. Do not edit the checksums to match; restore the file from git, or, if the change is
  intended, write the files again with `agentops dataset --write --force` and review the diff.
- `agentops eval` exits 1 naming a tracing variable: a tracing switch is on without
  `LANGSMITH_API_KEY`, or a switch holds a value that is neither on nor off. Set the key or
  turn the switch off (see Tracing). The message names the variable, never its value.
- `agentops eval` or `agentops dataset` exits 2, "could not be read" or "does not exist": a
  scenario file, the scenarios directory or a dataset file cannot be opened. Check the path
  given to `--scenarios-dir` or `--evals-dir` and the file permissions.
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
