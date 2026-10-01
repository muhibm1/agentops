# 0002: Enforce citations in code with explicit retry classes

Date: 2026-10-01
Status: proposed
Change: 2026-10-01-increment-1-scaffold

## Context

Design §5 says a hypothesis, root cause or remediation that cites no evidence id, or an id not
in state, is rejected by the node and routed to a retry; design §4 says retries belong to the
graph's `RetryPolicy`, not to prompt engineering (R13). `RetryPolicy.retry_on` defaults to
`default_retry_on` (confirmed in the LangGraph reference), which is believed to exclude
`ValueError` and its subclasses; `pydantic.ValidationError` is a `ValueError` subclass. Relying
on the default would make a malformed structured response fail the run on its first attempt.

## Decision

`state.py` defines `CitationError(Exception)` (not a `ValueError`) and `assert_cited(ids,
evidence)`, which raises when `ids` is empty or names an id absent from `evidence`. `triage`
and `rca` call it on every output before returning. `build_graph` attaches
`RetryPolicy(max_attempts=3, retry_on=(CitationError, ValidationError))` to both model nodes.
The evidence node keeps `RetryPolicy(max_attempts=2)` with the default predicate.
Correction (task 6): the default predicate does not retry the `RuntimeError` E37 raises, so the
evidence node uses `RetryPolicy(max_attempts=2, retry_on=Exception)` (confirmed by E37). Eval case E23
proves the retry empirically with a bad-then-good script; the "believed" above becomes
confirmed or refuted by that test, and the builder records which in the commit message.

## Alternatives

| Option | Why not |
|--------|---------|
| Default `retry_on` | Believed not to retry `ValueError` subclasses; a silent first-attempt failure |
| Catch inside the node and re-invoke the model in a loop | Reimplements what the graph already provides and hides attempts from LangGraph's trace and from `actions` |
| Validate citations only in the prompt | The design principle: the prompt asks, the code checks |
| `error_handler=` routing to `escalate` | Escalate does not exist until increment 2; add it there without changing the policy |

## Consequences

Easier: every model node has one retry policy and one failure class a test can name.
Harder: increment 2 must keep `retry_on` in step if it adds exception classes (for example an
Anthropic API error class), and `set_node_defaults` may replace per-node policies then. A node
that raises three times surfaces `CitationError` to the caller; the CLI maps it to exit 2.
