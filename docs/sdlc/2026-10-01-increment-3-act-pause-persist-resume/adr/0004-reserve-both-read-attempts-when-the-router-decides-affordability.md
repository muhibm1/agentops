# 0004: Reserve both read attempts when the router decides what is affordable, and name a source outage

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-3-act-pause-persist-resume

## Context

Increment 2 left two findings open (ship.md, deferred mediums and lows): `affordable` in
`src/agentops/nodes/router.py` charges each source its nominal cost (1, 2, 1) while merge
charges every attempt and each read retries once, so a run can make 16 reads against the
12-read budget; and a source that fails both attempts in both rounds is reported as
`no_evidence`, or silently dropped while rca runs on partial evidence (R1 to R3).

## Decision

`affordable` reserves `NOMINAL_COST[source] * READ_ATTEMPTS` per source, so a round can never
start reads it cannot pay for even if every read fails and retries; the total of recorded read
attempts is then at most 12 under any failure pattern. With every read succeeding the reserved
cost never exceeds the budget left on the scripted paths, so loop counts and the "budget at or
above 4" assertions from increment 2 still hold. Merge writes `unreachable_sources` (sources
whose every recorded attempt failed) into state and escalates with the new reason
`source_outage` when no evidence exists and every attempt failed. When evidence exists, rca runs
with the unreachable sources named in its prompt, and the edge after rca escalates with
`source_outage` if a hypothesis still open after re-scoring can be judged only by an
unreachable source.

## Alternatives

| Option | Why not |
|--------|---------|
| Skip the retry inside the evidence node when the budget is spent | A node cannot see what its parallel siblings are spending in the same super-step |
| Pass a per-node read allowance through `Send` | More state on every fan-out for the same guarantee |
| Always escalate on any unreachable source | Throws away a clear answer from the sources that did respond |

## Consequences

Easier: the budget is a hard cap a test can prove. Harder: a run whose first round fails twice
on every source has four reads left and asks one source in round two instead of three; that is
the intended trade. One more control field and one more escalation reason to document.
