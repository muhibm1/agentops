# 0001: Retry source reads inside the evidence node and escalate when nothing was collected

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-2-investigation-half

## Context

Increment 1 put `RetryPolicy(max_attempts=2)` on the single evidence node and raised
`NoEvidenceError` (CLI exit 2) when all reads returned nothing (`src/agentops/graph.py`,
`src/agentops/nodes/evidence.py`, confirmed). The ship document's open low 3 says failed
attempts never appear in `actions`, because LangGraph discards a failed attempt's writes before
retrying (design §4). Increment 2 runs three evidence nodes in one super-step (R3): one node
raising after its retries would fail the whole round, including the two sources that answered.
Scenario 4's trap is a source that legitimately returns nothing (R10).

## Decision

Each evidence node calls the source through `read_with_one_retry`: attempt 1, and on any
exception a recorded `Action` with `ok=False`, the error type and `attempt=1`, then attempt 2.
A second failure is recorded the same way and the node returns no evidence; it never raises for
a source error. `SOURCE_NODE_RETRY` is removed. An empty round is not an error: `merge` asks
again while budget and rounds allow, and when nothing is left to ask and `evidence` is still
empty it routes to `escalate` with reason `no_evidence`. `NoEvidenceError` is deleted.

## Alternatives

| Option | Why not |
|--------|---------|
| Keep `RetryPolicy` on each evidence node | One dead source fails the parallel round and the failed attempt stays invisible; the design wants retries visible in `actions` (§18) |
| Keep `NoEvidenceError` as exit 2 | "Nothing to investigate" is exactly what escalation is for (design §19 D9); a typed crash is less useful to an operator than an escalated run with a reason |
| Record failures in a separate state list | A second reducer for the same fact; `Action` already has `ok` and `detail` |

## Consequences

Easier: the CLI's failed-action count is honest for source reads; a flaky adapter costs budget
instead of the run. Harder: model-node retries are still unrecorded (decision D15) until
LangSmith traces exist; a persistently failing source silently reduces the evidence set, which
the `budget remaining` and `(M failed)` lines expose. Revisit when a real adapter (my trading engine) arrives
and adapter timeouts need their own policy.
