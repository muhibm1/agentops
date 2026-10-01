# Increment 2: the investigation half of the graph

Change id: `2026-10-01-increment-2-investigation-half` · Prepared 2026-10-01 05:30 UTC
Risk tier: 2 (edits the CI workflow and the tier-2 floor paths `state.py` and `graph.py`; no data store, money or irreversible step)
For the agents: [spec.md](./spec.md), [plan.md](./plan.md), [evals.md](./evals.md), [adr/](./adr/)

This is the Design document. A person reads it in under five minutes, technical or not.

## The short version

You asked for increment 2 of the design: the full investigation half of the graph, six new
trap scenarios, and the leftovers from increment 1's retro. The build replaces the straight
line "triage, evidence, root cause" with a router that decides which sources to ask, three
sources read in parallel, a merge step that owns the budget and the loops, a root-cause step
that re-scores its hypotheses, and an escalation step that is a proper, successful ending.
The one thing worth knowing: the graph, not the model, decides when the evidence is enough,
when the budget is spent, and when two causes cannot be told apart.

## Problem

In your words: increment 1 reaches a root cause on two scenarios through a linear graph with a
scripted model. Restated: today every scenario takes the same three steps, asks every source
once, cannot loop, cannot run out of budget, and cannot say "I do not know". Six of the eight
designed scenarios do not exist, so there is no trap that would show the difference between an
agent that investigates and one that guesses. Increment 1 also left five small findings open,
two eval cases unwritten, the CI runner on a floating image, and the profile silent on what a
release is.

## Outcome

When this is done: `agentops run --scenario <any of eight> --model scripted` ends in one of two
states, approval-ready (a cited root cause and a cited remediation, waiting for the approval
step that increment 3 adds) or escalated (a reason and a short summary of the competing
causes). Scenarios 3, 4 and 5 loop once to widen their search; scenario 7 escalates because
its two causes score too close; scenario 8's injected "drop the table" line is shown to the
model only inside the untrusted-data fence and the real cause wins. The report prints how many
loops ran, how much budget is left and how many source reads failed. CI runs on a pinned
`ubuntu-24.04` image with a test floor of 80, and the profile says that merging the pull
request to `main` is the release and you perform it. No live model is called anywhere.

## What changes for people

You, as the operator of the demo, see two more lines in the report and a new ending called
escalated, which is a success, not a crash. Anyone reading the repository sees eight scenario
files with a readable trap each, and a graph diagram that matches the design document. Nothing
is deployed and no secret, environment variable or dependency is added. Increment 3 will
reuse the five action names the scenarios propose when it writes the runbook.

## What could go wrong, and the guard for each

| Risk | Guard |
|------|-------|
| A scenario does not follow the path the spec promises once the real router runs it | The end-to-end task owns scenario corrections and may change only the scenario file, never the routing rule; a test per scenario checks the loop count and the budget |
| The model names a cause its own scores do not support | A numeric rule in the graph escalates when the top two scores are within 0.15, whatever the model said; an adversarial test proves it |
| One dead evidence source takes down the whole parallel round | Reads retry once inside the node, record the failure, and return nothing rather than raising; a test kills one source and the run still finishes |
| The budget is charged twice when three sources finish together | Budget is derived from the recorded reads, not counted per step; a test asserts four reads and budget 8 after the first round |
| Hidden scenario answers or the injected line leak into a prompt | The existing fence and leak tests now run over all eight scenarios and the new escalation prompt |
| Fewer tests than the new floor | The plan adds about 35 tests to 76; the last task checks the count before committing |

## Decisions

Approving this document accepts every recommendation; say otherwise in the approval notes to
change one. All are reversible in code except D12, D14 and D19, which need you.

| # | Decision | Recommendation | Alternative | Why the recommendation |
|---|----------|----------------|-------------|------------------------|
| D1 | How a failing evidence read is retried | Once, inside the node, with the failure recorded; a second failure yields no evidence and the run continues | Keep the graph-level retry policy from increment 1 | A dead source must not fail the two sources that answered; the failed attempt becomes visible (closes low 3 for source reads) |
| D2 | What happens when nothing at all is collected | The run escalates with reason "no evidence" (exit 0) instead of failing with exit 2 | Keep the increment 1 typed failure | Escalation is the designed ending for "I cannot investigate"; the reason is printed, so it is not silent |
| D3 | How ambiguity is detected | A numeric rule: no root cause, or the top two re-scored hypotheses within 0.15 of each other | Trust the model's null root cause only | The graph enforces; the model recommends |
| D4 | Root-cause output shape | Gains re-scored hypotheses; root cause and remediation are both present or both empty; scenarios 1 and 2 updated | Leave the shape alone | Needed for D3 and for the fork story in design section 9 |
| D5 | How the router scores sources | A keyword table on hypothesis names; logs always count; an unmatched name counts for everything | Always ask all three sources | The design asks for a deterministic scoring router; a table is readable and testable |
| D6 | Where dependency health is read | Inside the metrics node, as a second read | A fourth evidence node | The design fixes three nodes; dependency status is "how things are behaving now" |
| D7 | What the budget counts | Source read attempts, 12 per incident, derived from the recorded actions; each source asked at most twice | A per-round counter | Reconstructible from any checkpoint; no shared counter between parallel nodes |
| D8 | What a loop does differently | Widens the time window by 60 minutes per loop | Repeat the same read | A repeat returns the same nothing |
| D9 | Recording the escalation reason | Two new state fields, escalation (model text) and escalation reason (set by code) | Put the reason in the summary text | The reason is a control fact; it must not be the model's to write |
| D10 | `--model anthropic` | Stays a stub with a clearer message; the live model is wired with the evaluation work in increment 4 | Wire it now | The request allows the stub; no spend before you decide on API budget |
| D11 | Test floor | Raise from 30 to 80 | Keep 30 | 76 tests exist; the floor should catch a suite that lost half its tests |
| D12 | Profile edit scope (sensitive file, needs your permission) | Add the release note and also flip the stale "(believed)" stack labels to confirmed | Release note only | Same file, same visit, and the labels were a reviewer's open low |
| D13 | Where the scripted-model builder lives | One function in `fakes.py`, replacing the copy in the CLI | Two helpers | Closes low 4 and 5 together |
| D14 | Runbook (protected path, needs your ask) | Not touched now; the five action names the scenarios use are recorded in the spec for increment 3 | Create the runbook now | The allowlist is increment 3's security boundary and deserves its own review |
| D15 | Model-call retries in the action record | Still not recorded; accepted until LangSmith tracing in increment 4 | Retry model calls inside the node too | Would undo increment 1's ADR 0002 for a count that traces will give for free |
| D16 | Increment 1 tests whose behaviour this change redefines | Replaced by the named new cases in the task that owns the file | Keep them and add exceptions | A test asserting the old crash would contradict D1 and D2 |
| D17 | Can the model dodge the ambiguity rule by dropping or renaming a cause (audit finding) | The root-cause step checks in code that the re-scored causes are exactly the ones triage named and that the chosen cause is the top-scored one; a mismatch is treated like a bad citation, retried up to three times, then the run fails | Trust the model to keep the list intact | A rule that only reads what the model wrote is a rule the model can route around; the check costs a few lines and one test |
| D18 | What an escalated run keeps of the model's proposal (audit finding) | Nothing: when the graph escalates after root cause, the root cause and remediation are left empty whatever the model said | Keep the proposal in the state for the record | Increment 3's approval and execute steps will read those fields; an escalated run must not hand them anything to act on |
| D19 | Editing the CI workflow (sensitive file, needs your permission) | Pin the runner to `ubuntu-24.04` and raise the test floor to 80; nothing else in the file changes | Leave the floating image | Your request text asked for it: "pin CI runner to ubuntu-24.04, record that merging the PR to main is the release and the owner performs it"; the second half is D12 |

## How it will be proved

The profile checks `lint`, `format`, `typecheck`, `test` and `security_audit` must exit 0 on
the branch. The eval file holds 35 cases as written: 18 golden (every scenario's ending, path,
loop count and budget; router scoring; merge decisions; CLI output; CI and documents), 6 edge
(empty sources, unmatched names, budget clamp, half-empty outputs, colliding keys), 8 failure
(dead sources, uncited outputs, error-path printing, the stub) and 3 adversarial (log
injection stays fenced, the model cannot talk the graph out of escalating, no hidden answer in
any prompt), plus two measures: the suite under 60 seconds and at least 80 tests. Done looks
like: all eight scenarios end approval-ready or escalated under test, and CI is green on the
pinned runner.

## Estimate

Waves: 4. Tasks: 7 (largest wave 3). Agent-time budget at tier 2: 90 minutes.

## Your decision

Approve to build it exactly this way, or reject with notes to have it redesigned. If you
approve, D12 (profile labels), D14 (no runbook yet) and D19 (the CI workflow edit) are taken
as your answers.
