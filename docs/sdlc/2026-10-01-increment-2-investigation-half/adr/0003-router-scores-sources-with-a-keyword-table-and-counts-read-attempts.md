# 0003: Score sources with a keyword table, count read attempts as budget, widen the window per loop

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-2-investigation-half

## Context

Design §4 wants a router that "scores each evidence source by how many open hypotheses it
could confirm or rule out", deterministic given the state, with `investigation_budget` at 12
"tool calls" decremented by `merge`. Hypothesis names are free text the model writes
(`Hypothesis.name`, `src/agentops/state.py`, confirmed), so scoring needs a rule in code that
does not call the model. Parallel nodes cannot decrement a plain integer (no reducer), so the
budget must be derived.

## Decision

`nodes/router.py` holds one keyword table: `metrics` and `deploys` each match a short list of
substrings in the lower-cased hypothesis name; `logs` matches every open hypothesis; a name
matching neither list counts for metrics and deploys as well. Open means score at or above 0.2.
Budget is derived: `12 - (number of source read attempts recorded in actions)`, clamped at 0
in state by `merge`. Nominal costs are logs 1, metrics 2 (metrics plus dependency health),
deploys 1; the router keeps the longest prefix of its ordered choice that the budget affords.
A source is asked at most twice per run, and the window on loop `n` is 60 minutes times
`n + 1`, so the second ask is a wider look, not a repeat.

## Alternatives

| Option | Why not |
|--------|---------|
| Ask the model which sources to query | The router would no longer be deterministic, and the evidence-sufficiency evaluator could not be deterministic either |
| Always query all three sources | No routing to evaluate; the budget would never matter on eight scenarios |
| Budget as a per-round counter written by merge | Needs merge to know how many nodes ran; counting attempts in `actions` is the same number and is auditable from state |
| Same window on each loop | A repeat read of an empty source returns the same nothing; widening is the cheapest real second step |

## Consequences

Easier: router behaviour is a table a reader can change; budget is reconstructible from any
checkpoint. Harder: a hypothesis name outside the table costs two extra reads; the table will
grow with the week-two scenarios and should move to a scenario-independent config then. A
retry can carry attempts one past the budget inside a round; the clamp and the attempt record
make this visible rather than hidden.
