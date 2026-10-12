# Evaluating a non-deterministic agent

This is the evaluation method I use for systems where a model makes a judgment call: the same
input can produce a different answer on the next run, and a plausible answer can still be wrong.
I have applied it twice, to Studbook (cited retrieval over an engineering record) and to AgentOps
(an incident-response graph), and it is written here as a method another team could adopt, with
those two systems as the examples.

Each principle below names a case where I applied it and what happened.

## 1. Decide what a wrong answer costs before choosing a metric

A metric encodes a judgment about which mistakes matter. Make that judgment first, in plain
words, then pick metrics that follow from it.

For Studbook, an invented citation cost more than a refusal: an engineer who acts on a confident,
wrong answer about their own record is worse off than one told "Not in the record". So the
evaluation tracks faithfulness and trap questions as first-class results, next to correctness,
and rewards a refusal where the record is silent.

For AgentOps, a wrong remediation costs more than an escalation: rolling back the wrong deploy
during an incident makes it longer. So `escalated` is a success state when the evidence is
ambiguous, `false_remediation` is its own evaluator, and `safety` (no action applied without an
approval record, nothing outside the runbook) is checked on every run.

## 2. Build cases with known answers, and make most of them traps

If the obvious answer is always right, accuracy measures nothing: a naive system scores as well
as a careful one.

Studbook's gold set includes questions the record cannot answer. When I later audited all nine
traps against the ingested corpus (`docs/gold-set-audit.md` in [Studbook][studbook]), only three tested a
record that is genuinely silent; for the other six the record says outright that the thing does
not exist, which is a weaker test, and one trap had to be rewritten because part of it was
answerable. Auditing the traps is part of the method: a trap that is secretly answerable rewards
the wrong behaviour.

AgentOps's eight scenarios each carry their ground truth, the evidence a correct diagnosis needs
(`required_evidence`) and the actions that would be wrong (`wrong_actions`). Most are built so
the first plausible answer is wrong: a deploy that coincides with a database problem it did not
cause (`deploy_red_herring_db`), a noisy neighbour behind a dependency timeout, two concurrent
faults where fixing the first reveals the second, a case where the right answer is "it could be
either, escalate", and a log line that tries to instruct the operator to drop a table
(`log_injection_drop_table`).

## 3. Split dev from held-out, pin the held-out bytes, and score held-out only when frozen

Every change I make is steered by some number. If that number comes from the cases I will later
report, the report describes how well I fitted those cases, not how well the system works.

Both systems split cases into a dev set for steering and a held-out set scored only when a
version is frozen. In AgentOps the held-out ids live in `evals/held_out/manifest.json`, and
`evals/checksums.sha256` pins the sha256 of that manifest and of each held-out scenario file.
`agentops eval --held-out` refuses to run if one byte has changed, and no test or CI step scores
the committed held-out split. With eight scenarios the split is five dev, three held out.

In Studbook the held-out split (n=20) was scored three times, each after a change that made the
previous column describe a system that no longer existed, and every earlier column was kept in
the README rather than replaced. Showing the history is what lets a reader see that the held-out
number was not tuned.

## 4. Prefer deterministic evaluators; anchor any model judge to ground truth

When the case file holds the answer, check it in code. A deterministic evaluator is a stronger
claim than a model's opinion, it costs nothing to run, and it gives the same verdict every time,
so a change in the score is a change in the system.

AgentOps grades every run with seven evaluators that need no model: root-cause accuracy, correct
outcome, evidence sufficiency, false remediation and safety pass or fail against the scenario
file, and tool calls and latency are measured. Only explanation quality uses a model judge,
because whether an explanation cites the evidence that actually separates the top two hypotheses
is not something code can read. The judge's rubric names that discriminating evidence from the
scenario file, so it grades against ground truth rather than style, and its verdict is a
structured `{score, comment}` that is validated, not parsed out of prose.

## 5. Compare against a baseline, not against nothing

A score alone invites the question "compared to what?". A difference against the approach most
teams ship first answers it.

AgentOps compares its graph against a ReAct baseline built with LangChain's `create_agent` over
the same tools and the same simulator, with no approval step and no allowlist. Both run over the
same scenarios and are graded by the same evaluators, and the table prints them side by side. The
point of the comparison is not that the graph wins everywhere; it is where the difference
concentrates. The design predicts the trap scenarios.

## 6. Repeat, and report the spread

A model's answer varies between runs, so one run per case is an anecdote. Run each case several
times (three is my minimum on a small set), report the mean with the minimum and maximum, and say
plainly when a difference sits inside the spread.

`agentops eval --repetitions N` runs each system on each scenario N times, and every metric column
prints as `mean/min/max`. Cost is reported the same way, from the token counts each reply
carried, priced per model from a dated price table; a model the table does not name prints
`n/a`, never a guess.

## 7. Track per-case churn between versions

An improved mean can hide a regression: three cases get better, one gets worse, and the average
goes up. Compare versions case by case, and report which got better, which got worse and which
moved both ways.

Studbook's `eval/compare.py` does this, and `agentops compare` does the same for two
`agentops eval --json` files: per system and scenario, `better`, `worse`, `mixed` or `same`,
with the evaluators that moved. Halving the rerank candidate pool moved held-out answer
quality with three questions better, none worse and two mixed, and on dev the same tool read a
correctness move of 1.500 to 1.513 as noise, which is why that change was described as "same
quality, 2.2x faster" rather than as an improvement.

## 8. Publish the failures

A results table with no wrong answers is either a toy or a lie, and an experienced reader knows
it. List every failing case next to the summary.

Studbook's README says where it still fails: of the three answerable held-out questions it still
refuses, two were refused because retrieval never handed it a relevant passage, and one had the
answer in front of it. Chasing that produced its most useful negative result: giving the
generator 10 passages instead of 5 recovered every point of retrieval recall and moved
correctness by exactly zero, so production stayed at 5.

`agentops eval` prints every failing system, scenario, repetition and evaluator under the table,
and a run that raises is a `failed` row naming the exception type, not a crash. Even the scripted
harness has a known, expected failure that stays in the output: the graph's final diagnosis on
`two_concurrent_faults` is the second fault, while the scenario's ground truth names the first.

## Worked example: Studbook

From the Studbook README at commit `9e7b469` (2026-09-27), copied, not re-derived. Held-out test
split, n=20, scored after tuning was frozen:

| Metric | 2026-09-15 | 2026-09-16 | Current |
|---|---|---|---|
| Answer correctness (mean, 0-2, LLM-judged) | 1.30 | 1.40 | 1.60 |
| Fully correct (score 2) | 0.65 | 0.65 | 0.75 |
| Faithfulness (no unsupported claims) | 0.95 | 1.00 | 1.00 |
| Citation validity | 1.00 | 1.00 | 0.95 |
| Unanswerable trap questions handled | 3/3 | 3/3 | 3/3, nothing invented |
| Over-refused answerable questions | 7 of 17 (41%) | 6 of 17 (35%) | 3 of 17 (18%) |
| Retrieval recall@5 | 0.6471 | 0.6471 | 0.7059 |

Read against the method: the cost judgment (principle 1) shows in the rows chosen, with trap
handling and faithfulness given the same weight as correctness. Citation validity went down
(1.00 to 0.95) in the same change that moved everything else up, and the table keeps it rather
than dropping the row (principle 8). Studbook is a reference implementation and measurement
harness, not a system in daily use.

## Worked example: AgentOps

Dev split, five scenarios, three repetitions, Claude Sonnet 5.5, at commit `64289e6`
(2026-10-12), then the frozen held-out split (three scenarios, three repetitions) scored once on
the same commit. The spread is passes per repetition.

| Metric | Graph, dev | Spread | Baseline, dev | Spread | Graph, held out | Baseline, held out |
|---|---|---|---|---|---|---|
| Correct outcome | 14/15 | 5/5/4 | 12/15 | 4/4/4 | 9/9 | 9/9 |
| Root cause label exactly right | 14/15 | 5/5/4 | 15/15 | 5/5/5 | 6/9 | 3/9 |
| Gathered the required evidence first | 15/15 | 5/5/5 | 6/15 | 2/2/2 | 9/9 | 6/9 |
| No wrong action applied | 15/15 | 5/5/5 | 15/15 | 5/5/5 | 9/9 | 9/9 |
| Safety | 15/15 (14/15 as first scored) | 5/5/5 | 0/15 | 0/0/0 | 9/9 | 3/9 |

Read against the method: the cost judgment (principle 1) is why safety and evidence rows sit next
to outcome. A baseline that reaches an end state by acting without approval is not the better
system, and the table has to make that visible rather than reduce it to one number. The baseline
comparison (principle 5) showed where the difference concentrates: the graph loses nothing on
outcome once rca may add a cause the evidence supports, and the baseline's evidence and safety
rows do not move with any prompt change, because they are what the gates enforce. The spread
(principle 6) and the per-scenario comparison (principle 7) are what let one miss be read
correctly: the dev outcome went from 15/15 to 14/15 between the last two commits, and
`agentops compare` showed it was one repetition of one scenario, tipped by a prompt change made
for the cheaper model; the regression is named in the README rather than tuned away. The safety
column carries its own correction (principle 8): the first scoring read every apply's approval
from the final state, which a loop clears, so one approved rollback scored as unapproved; the
scorer was fixed, the saved run re-scored beside the original, and both numbers are shown. The
held-out split (principle 3) was scored once on the frozen commit and gave the graph its only
exact-cause misses, `schema_drift` labelled as the deploy that carried it; that stays a note.
The eleven paid runs that got here, four of them failing on the harness's own code, are in
[trace-mining.md](trace-mining.md).

[studbook]: https://github.com/muhibm1/studbook
