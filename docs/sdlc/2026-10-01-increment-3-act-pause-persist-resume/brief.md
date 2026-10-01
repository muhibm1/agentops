# Increment 3: act, pause, persist, resume

Change id: `2026-10-01-increment-3-act-pause-persist-resume` · Prepared 2026-10-01T07:30 UTC, revised after the constraint audit
Risk tier: 3 (the action allowlist and the executor are the security boundary and sit on the profile's tier-3 floor; a new runtime dependency and the CI workflow are also edited)
For the agents: [spec.md](./spec.md), [plan.md](./plan.md), [evals.md](./evals.md), [adr/](./adr/)

This is the Design document. A person reads it in under five minutes, technical or not. Plain
sentences; name things by what they do; no code.

## The short version

You asked for the half of the design where the system acts: a fixed list of allowed actions, a
pause for your approval, a change applied exactly once with a written record, a check that the
symptoms are gone, a postmortem, and the ability to stop, restart and replay a run. Before any of
that, the four problems increment 2 left open are closed: a dead evidence source is reported as
an outage, partial evidence is named and handled, the twelve-read budget becomes a hard cap, and
look-alike characters can no longer fake the fence around untrusted evidence. The one thing worth
knowing: even when the model proposes the destructive action planted in scenario 8, the list of
allowed actions blocks it before anyone is asked to approve anything.

The constraint audit found nothing that blocks approval: no high findings, four medium and
eleven low. The four medium findings are decisions D21 to D24 below, each with the fix already
chosen; the eleven low findings are folded into the spec and the plan as builder notes.

## Problem

In your words: "all eight scenarios end approval-ready or escalated under a scripted model, but
nothing can act, pause, persist or resume." Precisely: the graph stops when it has a proposal.
There is no list of allowed actions, no executor, no audit file, no checkpoint file a restart can
pick up, no approval step, no verification, no postmortem, and the command line has only `run`.
Four findings from increment 2's review were deferred to this change and the CI test floor (80)
sits far below the real suite (239), so a mass deletion of tests would still pass.

## Outcome

When this is done, `agentops run` on a scenario stops at a proposal and prints a thread id;
`agentops approve <thread>` applies the action through the allowed-action list, checks the
symptoms, loops once more if they remain (scenario 6 does exactly that and ends resolved), and
writes a postmortem; `agentops resume`, `history` and `fork` work on the saved thread. A process
killed in the middle of applying a change, then restarted, applies it exactly once, and the
ledger and the audit file prove it. Scenario 8's planted "drop table" is blocked even when the
model asks for it. A source that dies both times is reported as `source_outage`; no run ever
makes more than twelve evidence reads; the CI floor tracks the real suite.

## What changes for people

You, as the operator, see a pause: the run prints the proposed action, the evidence it cites,
a one-sentence dry run and the rollback plan, and waits. You answer with approve, reject (with a
reason the next investigation round sees) or edit (checked again before anything runs). Every
answer you give, accepted or refused, is written to the audit file, so an attempt to smuggle a
bad edit through the pause leaves a trace. New files appear beside the repository when you run
it: a SQLite checkpoint file with two small companion files SQLite keeps next to it, and an
`audit/` folder with one append-only record per incident. The checkpoint file and the folder are
already ignored by git; the two companion files need one new ignore line (D23). Reviewers and
recruiters reading the repository see the allowed-action list as one readable YAML file. Nothing
changes for CI except a higher floor and three more pinned packages.

## What could go wrong, and the guard for each

| Risk | Guard |
|------|-------|
| The model is steered by injected logs into a destructive action | The action list is code; a proposal outside it goes to escalation, never to approval or execution |
| An edited proposal smuggles a forbidden action or an absurd parameter | Every edit is re-checked against the list and the parameter ranges; a bad edit is handed back with the reason, and the attempt is written to the audit file |
| The approval is attached to one change and a different change gets applied | The approval record carries a fingerprint of the exact change the operator saw; the executor refuses anything whose fingerprint differs |
| A crash between approval and apply runs the change twice | An idempotency key, the source's own ledger on disk, and a check before every apply; a test kills a real process and proves one apply |
| Someone adds a flag that skips approval | No such parameter, variable or setting exists; a test reads the modules, including the action layer, and the graph's edges to prove the only path into execution is through approval |
| The audit file is rewritten | It is only ever opened for appending; a test watches its size never shrink |
| A dead source looks like "nothing found" | A new reason, `source_outage`, and the unreachable sources named in the proposal |
| The twelve-read budget is exceeded when reads fail and retry | The router now reserves both attempts before asking; a test runs with every source dead |
| Look-alike brackets fake the end of the evidence block | A table of look-alikes is normalised and every prompt uses a random marker the evidence cannot guess |
| The new SQLite package pulls in something unexpected | It and the two packages it needs are pinned to one version each with their licences; the dependency audit in CI covers all three |
| Checkpoint companion files end up committed | A new ignore pattern, and the verification step lists tracked files to prove none slipped in |

## Decisions

Every question the design raised, each with the recommended answer already chosen. Approving
this document accepts every recommendation; say otherwise in the approval notes to change one.
D1, D2, D3, D13, D23 and D24 touch paths the profile marks "ask first"; your request named the
first four, and your approval here is the consent for all six.

| # | Decision | Recommendation | Alternative | Why the recommendation |
|---|----------|----------------|-------------|------------------------|
| D1 | New runtime package for checkpoints (needs your ask: `requirements.txt`) | `langgraph-checkpoint-sqlite==3.1.1`, licence MIT (confirmed from the package metadata); nothing present can save checkpoints to a file | Keep checkpoints in memory | The design names this package and version; a restart must find the run |
| D2 | YAML parser moves from the dev list to the runtime list (needs your ask) | Move `pyyaml==6.0.3` as is | Write the runbook as JSON | The request names a YAML runbook that people read |
| D3 | The allowed-action list (needs your ask: `runbooks/`) | The five actions the scenarios already use, each with parameter ranges, preconditions, a risk and a rollback declaration, as written in the spec | Fewer or broader actions | The list is the security boundary; it should be exactly what the scenarios need and nothing more |
| D4 | Checkpoints required to build the graph | Required; tests use the in-memory saver, the CLI a SQLite file named `agentops.sqlite` in the working directory | Optional checkpoints | The pause needs one; a graph that fails only at approval is a trap |
| D5 | How a thread is named | The thread id is the incident id; `approve`, `resume`, `history` and `fork` take it; the scenario is read back from the checkpoint | Pass the scenario again on every command | One id to remember; no way to resume against the wrong scenario |
| D6 | When writes are forced to disk | On every command, not only around approval and execution | Only the resume command | Runs take seconds; one rule is easier to trust than two |
| D7 | What the idempotency key hashes | Action, target and parameters, in a fixed order | The whole proposal including its wording | The same change with different wording must be the same change |
| D8 | How a bad edit is handed back | Through the graph's edge, interrupting again with the error attached (ADR 0001) | A loop inside the approval step | LangGraph's resume rules forbid the loop |
| D9 | Where the simulator remembers what it applied | A ledger file beside the audit file, replayed on restart (ADR 0002) | In memory | A killed process must not forget a change it made |
| D10 | How "symptoms cleared" is decided | A written rule over healthy metric ranges and post-action log lines (ADR 0003) | Compare with the pre-incident values | Scenario 5 starts in the bad state; the model may not decide a control edge |
| D11 | How the crash demo kills the process | A hidden `--crash-after-apply` switch on `approve`, documented as the demo switch; it can only abort and reaches the executor only through the approve command | An environment variable | A command-line switch is visible in a review; a variable is not |
| D12 | New reasons and evidence kinds | Reasons `source_outage`, `action_not_allowed`, `precondition_failed`, `apply_failed`, `symptoms_persist`; evidence kinds `action`, `precondition`, `operator` | Reuse existing names | Each names a distinct thing an on-call human must know |
| D13 | The CI floor (needs your ask: the workflow) | Ninety percent of the passing count, rounded down to a multiple of ten, never below 240; raised in the last task when the count is final | A fixed 300 | Tracks the real suite as you asked, and stays below it |
| D14 | Look-alike fence characters | Both a table of look-alikes and a random marker per prompt (ADR 0005) | The table only | Any character the table misses would be a new finding |
| D15 | `history` and `fork` in this change | Include them; the design table had them in increment 4 | Defer to increment 4 | Your request names them and they are thin over two library calls |
| D16 | A postmortem the model gets wrong three times | A postmortem written by code from the root cause, status still resolved | Fail the run | A verified fix must never be reported as a failed run |
| D17 | A precondition that fails | Return the failure as evidence and investigate again once; a second failure for the same change escalates | Escalate at once | Gives the model one chance to use the new fact, bounded |
| D18 | How the twelve-read cap is enforced | The router reserves both attempts per source before asking (ADR 0004) | Skip the retry when the budget is spent | A node cannot see what its parallel siblings spend |
| D19 | The workflow test's exact "80" | Changed to "at least 240"; a deliberate test edit | Keep asserting the exact value | The floor is now a rule, not a constant |
| D20 | Tests that run the graph without a checkpoint | Updated to pass the in-memory saver and stop at the first pause; deliberate test edits named in the commit | Keep them as they are | They cannot pass once a pause exists |
| D21 | Audit finding: a refused operator answer left no record | Write every operator answer to the audit file, accepted or not, with a valid flag and the reason it was refused, before pausing again | Keep the refusal only in the checkpoint state | The pause is the privileged step; a smuggle attempt is the event an investigator most needs, and checkpoint state can be forked away or deleted |
| D22 | Audit finding: the approval was not tied to the change it approved | The approval record stores the fingerprint (idempotency key) of the change the operator saw; the executor refuses when the change in front of it has a different one | Rely on the graph's wiring alone | The executor is meant to be a second, independent check; without the fingerprint it only looks like one |
| D23 | Audit finding: SQLite's companion files are not git-ignored (needs your ask: `.gitignore`) | Add the pattern `*.sqlite-*` to `.gitignore` and include it in the tracked-files check at verification; WAL mode is confirmed from the library's source, not assumed | Default the checkpoint file into the already ignored `audit/` folder | One line fixes it for any path the operator chooses; moving the file would mix checkpoints with audit records |
| D24 | Audit finding: the packages the SQLite saver needs had no pin or licence (needs your ask: `requirements.txt`) | Pin `aiosqlite==0.22.1` (MIT) and `sqlite-vec==0.1.9` (MIT or Apache-2.0), both confirmed from the package resolver | Pin direct dependencies only, as today | The rule is one exact pin per dependency with its licence; the other transitive packages stay as they are, and changing that convention is a separate change |

## How it will be proved

The profile checks: typecheck, lint, format check, the full test suite, and the dependency audit
(now covering the three new packages). The eval cases in [evals.md](./evals.md): 64 numbered
cases, 21 golden (one per requirement), 17 edge (reject and edit loops, restarts, forks,
repeated keys, empty signals, key ordering, the audit file across a full run), 14 failure (bad
runbooks, dead sources, refusals, unwritable files, bad threads, exhausted budgets, failing
preconditions), 12 adversarial (the planted action, smuggled and tampered edits, forged fences,
bypass attempts, path tricks, terminal escapes), plus 3 non-functional (suite under 120 seconds,
the floor, the demo under 10 seconds). All are tests a machine runs; none is manual. Done means:
every scenario ends resolved or escalated under the full graph, the crash test shows one apply,
no checkpoint or audit file is tracked by git, and CI is green at the new floor.

## Estimate

Waves: 5. Tasks: 8. Agent-time budget at tier 3: 180 minutes, of which about 24 are spent on
design so far.

## Your decision

Approve to build it exactly this way, or reject with notes to have it redesigned. Approving
accepts D1 to D24, including the six that touch ask-first paths (D1, D2, D3, D13, D23, D24).
