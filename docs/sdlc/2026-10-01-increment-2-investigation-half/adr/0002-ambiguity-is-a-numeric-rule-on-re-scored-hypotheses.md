# 0002: The rca node re-scores hypotheses and the graph escalates on a numeric margin

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-2-investigation-half

## Context

Design §5 makes `escalated` a success state for ambiguous scenarios and §19 D9 says the
evaluators reward it. Design §9's fork story has `rca` "re-score" after new evidence, but the
increment 1 `RcaOutput` (`src/agentops/state.py`, confirmed) holds only `root_cause` and
`remediation`, so nothing in state says how sure the model was or what it ruled out. R6 needs
a rule the graph applies, because "the model recommends; the graph enforces" (profile
style_notes).

## Decision

`RcaOutput` becomes `hypotheses` (re-scored, each citing only existing evidence ids),
`root_cause` and `remediation`; the last two are both present or both null, checked by a model
validator. After the node runs, `is_ambiguous` returns true when `root_cause` is null or when
the two highest re-scored scores differ by less than `AMBIGUITY_MARGIN = 0.15`. The edge after
rca routes ambiguous output to `escalate` even if the model named a cause; a clear result sets
status `awaiting_approval`. Scenarios 1 and 2 gain re-scored hypotheses in their scripted rca.

## Alternatives

| Option | Why not |
|--------|---------|
| Null root cause is the only ambiguity signal | The model can commit to a cause its own scores do not support; the adversarial case E34 exists to catch exactly that |
| A separate contradiction-check node | That is the v2 graph of design §11, week two; building it now pre-empts the baseline comparison |
| Ambiguity decided at triage scores | Triage runs before evidence; the scores that matter are the ones after evidence |

## Consequences

Easier: the fork story and the evidence-sufficiency evaluator read re-scored hypotheses from
state; ambiguity is testable with crafted scores. Harder: a margin is a tunable; 0.15 is a
judgement, recorded here, to be checked against the first live experiment in increment 4.
Scenario files 1 and 2 change shape, and increment 1 tests on `RcaOutput` are replaced.
