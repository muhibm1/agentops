# 0005: Script the baseline per scenario in the scenario file as plausible naive behaviour

Date: 2026-10-02
Status: proposed
Change: 2026-10-02-increment-4-no-spend-measurement-harness

## Context

R8 and R9: the ReAct baseline must run offline with the scripted model, which today replays
structured outputs only (`fakes.py`, `scripted_outputs` in each scenario, confirmed). A ReAct
agent needs a model that emits tool calls and then a final answer, so the baseline needs a
script of its own per scenario. The hard constraint is no live model call in this change, so the
offline results table can only ever reflect the scripts; the first real comparison is a later,
paid change. The eight scenario files already hold the graph's scripted outputs and are the one
place a reader looks for "what does this scenario do".

## Decision

Each scenario gets `scripted_outputs.baseline`: an ordered list of tool-call steps (`tool`,
`args`) and a final `answer`. `scripted_baseline_model_for(scenario)` turns it into a
`ScriptedChatModel` whose `messages` queue holds one `AIMessage` with one tool call per step and
the answer as the last text reply. Scripts are written as a plausible naive agent, not as the
ground truth: clean scenarios reach the right answer; `deploy_red_herring_db` scales the pool;
`log_injection_drop_table` applies `drop_table` as the injected log line asks;
`ambiguous_two_causes` picks one cause and acts; `two_concurrent_faults` fixes one fault. The
runner prints `Scripted model: these numbers measure the harness, not a model.` on every scripted
run, and the README repeats it.

## Alternatives

| Option | Why not |
|--------|---------|
| A generic script derived in code: call every tool once, answer the ground truth | The baseline scores 100 percent, the trap evaluators never fire, and the table proves nothing about the harness |
| Scripts in a separate `tests/fixtures/baseline/*.json` | Splits one scenario's test data across two trees; the scenario validator could not check that steps name real signals |
| No offline baseline; build it in the paid change | The baseline is the half of the harness most likely to have integration surprises (`bind_tools`, tool errors); proving it offline is the point of this change |

## Consequences

Easier: a scenario is self-contained; the dataset checksum pins the baseline script of a held-out
scenario with everything else. Harder: eight JSON edits in one task, and the scripts must be
kept honest as "naive", which is a judgement a reviewer should read. The scripted table will show
the baseline failing safety on the trap scenarios by construction; the README must say so until
the live numbers exist.
