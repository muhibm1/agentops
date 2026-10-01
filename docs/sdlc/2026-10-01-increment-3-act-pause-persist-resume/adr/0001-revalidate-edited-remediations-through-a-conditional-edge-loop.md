# 0001: Re-validate an edited remediation through a conditional edge loop, not a loop inside the approval node

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-3-act-pause-persist-resume

## Context

Design section 8 says an invalid edit "is returned to the operator, not executed" (R11). The
obvious shape is a validation loop around `interrupt()` inside the approval node. LangGraph's
interrupt rules forbid that: resume values are matched to `interrupt()` calls by index within one
node execution, and the documentation names `while True` validation loops as a pattern that
mismatches cached values (confirmed in the interrupts page, "Do not loop interrupt calls").
The node also re-runs from its start on every resume, so any side effect before the call runs
again.

## Decision

The approval node calls `interrupt()` exactly once per execution. It validates the resume value
in code (decision literal, citations present in state, action in the registry, parameters inside
the declared schema). On failure it writes the reason to the control field `approval_error` and
leaves `approval` empty; the conditional edge after the node routes back to `approval`, which
re-runs, builds the payload again with the error attached, and interrupts again. On success it
clears `approval_error`, writes the `Approval` record and the audit entries, and the edge routes
to `execute`. All audit writes happen after the interrupt returns, so a re-run before a decision
writes nothing.

## Alternatives

| Option | Why not |
|--------|---------|
| Loop `interrupt()` inside the node until the edit validates | Forbidden by LangGraph's index-based resume matching; a second resume could be served the first value |
| Treat an invalid edit as a reject and loop to the router | Discards the operator's intent and re-runs rca for a typo in a parameter |
| Validate only in the CLI before sending the resume | The node is the boundary; a console or a direct `Command(resume=...)` would bypass it |

## Consequences

Easier: the node stays deterministic across re-runs, which is what resume requires. One more
control field (`approval_error`) that the model can never write. Harder: the graph gains a self
edge on `approval`; the recursion limit of 60 still bounds a run, and a test asserts the edge
set. Revisit if a console submits edits in a form the CLI never produces.
