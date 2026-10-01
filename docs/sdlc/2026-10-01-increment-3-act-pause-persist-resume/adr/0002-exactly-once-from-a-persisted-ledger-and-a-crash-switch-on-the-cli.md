# 0002: Prove exactly-once execution with a persisted simulator ledger and a crash switch on the CLI

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-3-act-pause-persist-resume

## Context

Design section 8 wants a demo and a test that kill the process between approval and apply and
show the change applied exactly once, with the ledger and the audit log as proof (R18). The
executor assumes at-least-once execution of its body (constraints T7): LangGraph re-runs a node
from its start on resume, and a checkpoint is never written mid-node. Today
`SimulatorSource` keeps its ledger in memory (`src/agentops/sources/simulator.py`), so a killed
process forgets what it applied, and no honest cross-process proof is possible.

## Decision

`SimulatorSource` accepts an optional ledger path. Every successful `apply` appends one JSON line
to that file before returning, and a source built on an existing file replays the ledger's
post-action worlds in order at construction, so a restarted process serves the world as it was
after the applied actions. The CLI's `approve` command carries a hidden `--crash-after-apply`
switch that makes the executor exit the process immediately after `apply` returns and the ledger
line is written, before the node returns and before its checkpoint. On restart, `agentops resume
<thread>` re-runs the execute node; step 5 of the executor finds the idempotency key in the
source's ledger and returns the recorded result without applying again. The proof is one ledger
entry for the key and exactly one audit `apply` record with `applied: true`.

## Alternatives

| Option | Why not |
|--------|---------|
| Kill the subprocess from the test on a timer | Non-deterministic; may kill before the ledger write and prove nothing |
| An environment variable read by the executor | Configuration that changes runtime behaviour outside the command line; harder to see in a review |
| Keep the ledger in memory and simulate the crash in-process | Does not exercise `SqliteSaver` across processes, which is the claim the README makes |

## Consequences

Easier: the demo clip and the test are the same command sequence. The switch can only abort;
it cannot skip approval, preconditions or the ledger check, and a test asserts that. Harder:
the simulator now has a file side effect; the ledger path lives beside the audit log under
`audit/` (gitignored). Revisit when a real `EvidenceSource` with its own ledger arrives.
