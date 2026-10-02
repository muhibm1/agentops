# Increment 4, the no-spend half: close increment 3's open items, then build the measurement harness

Change id: `2026-10-02-increment-4-no-spend-measurement-harness` · Prepared 2026-10-02 17:10 UTC · Revised after the constraint audit (no high finding; D17 to D20 added)
Risk tier: 3 (edits the executor's apply path, the preconditions and the runbook allowlist, which the profile floors at tier 3; the eval files and the graph are floored at 2)
For the agents: [spec.md](./spec.md), [plan.md](./plan.md), [evals.md](./evals.md), [adr/](./adr/)

This is the Design document. A person reads it in under five minutes, technical or not.

## The short version

You asked for the half of increment 4 that spends nothing: first close the five low findings and
the three missing permanent tests from increment 3, then build everything needed to measure the
graph against a naive baseline, runnable with the scripted model and no key. Ten tasks in four
waves do that, and the result is a working `agentops eval` that prints a results table today. The
one thing worth knowing: with the scripted model that table measures the harness, not a model,
and it says so on every run; the first real comparison is the separate paid change you decide on.

## Problem

In your words: the graph investigates, pauses, applies once, verifies, writes a postmortem and
survives a crash, but there is no way to measure it, and five low findings are open. Precisely:
when the simulated system throws an error during an action, the audit log says "not applied"
although nobody knows; a half-written line in the simulator's ledger makes every later command
fail until someone edits the file by hand; the time window in which the graph looks for an
action's effect is a fixed 30 minutes in code; two job actions accept almost any job name; and
three failure paths found in review have no permanent test. On the measurement side there is no
baseline, no evaluators, no dataset split, no results command, no tracing switch and no file for
the LangGraph development server.

## Outcome

When this is done: an action whose result is unknown is recorded as unknown, the graph still
checks whether the symptoms cleared, and if they did not it hands off to a human with the reason
"effect unknown". A torn ledger line is skipped with a notice and the action is applied once. The
verify window is a per-action number in the runbook you review. The two job actions accept only
their naming convention and only a job the incident's own logs mention. `agentops eval --model
scripted` runs the graph and the baseline over the five development scenarios, prints pass rates
for five deterministic checks and the spread of tool calls and timing, lists every failure, and
can repeat each run. The three frozen scenarios are named in a committed file whose checksum a
test enforces, and scoring them needs an explicit flag. Tracing to LangSmith turns on only when
you set both of its variables, and never has to be on. `langgraph dev` has its configuration
file. Nothing in tests or CI can call a live model or upload a trace, `--model anthropic` still
exits with "not wired yet", and no `.env` file is created.

## What changes for people

You, as operator: two new commands (`agentops eval`, `agentops dataset`), one new hand-off
reason in reports ("effect unknown"), one new closing sentence ("an approved action may have been
applied; its effect is unknown"), and a stderr notice when a torn ledger line was skipped. The
README gains a "Measuring it" section. You, as reviewer: the runbook shows a `verify_window_minutes`
knob and tighter job-name patterns; each scenario file gains a scripted baseline, written as a
plausible naive agent (the trap scenarios take the bait on purpose, so the harness has something to
catch). Recruiters and readers: nothing yet; the public README arrives in increment 5.

## What could go wrong, and the guard for each

| Risk | Guard |
|------|-------|
| The LangChain agent builder refuses our scripted model | Task 6 proves it first, before anything depends on it; the fallback is a thicker fake, recorded in the ship document |
| The scripted results get mistaken for a measurement | Every scripted run prints "these numbers measure the harness, not a model"; the README repeats it |
| Someone scores the frozen split by habit | The flag is required, it first re-checks the checksums and refuses on any mismatch, a warning prints, no test scores the committed split (D18), and a profile ask-rule (D13) stops agents from running it in a shell |
| A frozen scenario is edited to flatter a result | A committed checksum and a test fail on any byte change; regenerating it needs your approval |
| A stale tracing variable in your shell | The command exits with the variable's name instead of silently uploading or failing to; a variable set to `false` or `0` simply means off |
| The unknown-effect change ripples through code that assumed yes or no | The type checker now points at every reader; task 1 lists them and each gets a test |
| The narrowed job patterns break a scenario | Both job names were checked against their scenarios before writing; a test pins accept and reject lists |

## Decisions

Approving this document accepts every recommendation; say otherwise in the approval notes to
change one. Rows marked "your hand" touch ask-first files or the profile.

| # | Decision | Recommendation | Alternative | Why the recommendation |
|---|----------|----------------|-------------|------------------------|
| D1 | New runtime dependency (your hand: `requirements.txt`) | Add `langchain` 1.4.3, MIT, exact pin | Hand-roll a tool loop | The design names `create_agent` as the baseline; resolves cleanly against the current pins (dry run confirmed) |
| D2 | New development dependency (your hand: `requirements-dev.txt`) | Add `langgraph-cli[inmem]` 0.4.32, MIT | Install it ad hoc when needed | `langgraph dev` is part of the deliverable and the test suite does not need it, so it stays out of the runtime audit |
| D3 | Where the harness code lives | A package under `src/agentops/evals/`; data under `evals/` (ADR 0001) | Top-level `evals/*.py` as the design sketched | The installed command must import it from anywhere; the design's layout only works from the repository root |
| D4 | Which three scenarios are frozen | `ambiguous_two_causes`, `deploy_red_herring_db`, `schema_drift_ingestion` | A seeded random pick | One hand-off case, one trap, one data-pipeline case; keeps the injection scenario in dev for the README story |
| D5 | How the frozen split is pinned (your hand: `evals/checksums.sha256`, `evals/held_out/`) | A manifest of ids plus sha256 of each scenario file (ADR 0002) | Copies of the three files | No duplicate data to drift; any byte change fails a test |
| D6 | Where the verify window lives (your hand: `runbooks/default.yaml`) | Per action in the runbook, default 30, 1 to 1440 (ADR 0003) | A scenario field carried in state | It describes the action's effect; the file you already review |
| D7 | Job-name patterns and precondition (your hand: runbook) | `rerun_partition` targets end in `_rollup`, `_aggregate`, `_partition` or `_export`; `pause_batch_job` targets contain `-job`; both need the target named in the incident's logs | Keep broad patterns, rely on the dry run | Narrows the finding to a convention plus a check tied to observed evidence; the operator remains the control for intent |
| D8 | How an unknown effect is represented | Three states, a new outcome and a new hand-off reason (ADR 0004) | "not applied" plus an error flag | "not applied" already caused two high findings; a third state cannot be misread |
| D9 | Torn ledger line | Skip it, count it, print a notice, never rewrite the file | Fail as today | The file is append-only by design; a fragment can only come from a crash and the simulator applied nothing for it |
| D10 | Baseline scripts | In each scenario file, written as naive behaviour (ADR 0005) | A generic script derived in code | Gives the evaluators something real to catch; keeps a scenario self-contained |
| D11 | How the harness answers the approval pause | Approve every proposal as role `operator` | A scripted operator that rejects wrong actions | Measures the graph's own judgement; a rejecting operator would hide its errors |
| D12 | CI test floor (your hand: `.github/workflows/ci.yml`) | Raise `MIN_TESTS` by the documented rule once the branch count is known | Leave 450 | The rule in `docs/ci.md`; task 10 computes it and records the count |
| D13 | Profile ask-rules (your hand: `.workhorse/profile.yml`) | Add `--held-out` and `--model anthropic` to `ask_commands` at approval | None | Constraints Q7 asked for this once the flags existed; they exist now |
| D14 | `langgraph.json` content (your hand) | Dependencies `.`, one graph `agentops` pointing at a factory that builds the chosen scenario with the scripted model; no `env` key | Point at a compiled graph object | The graph needs a scenario and must not carry its own checkpointer under the dev server |
| D15 | LangSmith client library | Keep it an unpinned transitive dependency; import nothing from it | Pin `langsmith` and import it | Tracing works from two environment variables alone; no import means no upload path to audit |
| D16 | `.env` and `.env.example` | Not created; variable names are documented in the README | You create `.env.example` by hand | The harness refuses those filenames and nothing here needs a value |
| D17 | Which malformed ledger lines are skipped (from the audit) | Only a malformed last line is skipped; a malformed earlier line stops the command with an error naming it | Skip any malformed line | Only the last line can be torn by a crash; skipping an earlier one would silently forget that an action was applied, and the action could run twice |
| D18 | How the frozen-split flag is tested (from the audit) | The test builds a small frozen split of its own in a temporary folder; no test or CI run ever scores the three committed frozen scenarios | Test the flag against the committed split | Scoring the frozen split on every change is exactly what the repository rules forbid |
| D19 | Which systems the naive baseline may touch (from the audit) | The simulator only; any other source is refused before a single tool exists, with a test | Accept any source | The baseline has no approval step by design, so it must never be able to reach a real system |
| D20 | What the development server sends off the machine (from the audit) | Task 9 reads the installed `langgraph-cli` source, confirms whether it sends usage analytics and which Studio host it opens, and names both (and the opt-out variable, if confirmed) in the spec and README | Leave it unstated | The profile asks that every new telemetry or SaaS dependency is flagged when it is added; today the two facts are believed, not verified |

## How it will be proved

Profile checks on the branch head: lint, format check, typecheck, the full test suite, and the
dependency audit. Evals: 72 cases in `evals.md`, all run as tests with the scripted model and no
key: 39 golden, 9 edge, 11 failure, 11 adversarial, 2 non-functional (the offline eval finishes in
under two minutes; the CI floor is raised by the rule). Three of the adversarial and edge cases
and one rewritten case came from the constraint audit (D17 to D20). Every requirement has at least one case;
the only judgement left to a person is whether the eight baseline scripts read as honest naive
behaviour. Done looks like: the suite is green, `agentops eval --model scripted` prints a header,
two system rows and a failures list without touching your working directory, and the four-line
checksum file matches the committed scenarios.

## Estimate

Waves: 4. Tasks: 10. Agent-time budget at tier 3: 180 minutes. Increment 3 ran 212 minutes
against the same budget with 8 tasks; this change has more files but less novelty, and the
riskiest task (the baseline on `create_agent`) is isolated in wave 2.

## Your decision

Approve to build it exactly this way, or reject with notes to have it redesigned. Approving also
accepts D1, D2, D5, D6, D7, D12 and D14 as edits the builders make to ask-first files, and asks
you to perform D13 (two lines in the profile) at the same time.
