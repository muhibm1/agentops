"""The merge node: charges the read budget and decides what happens after a round of evidence.

Decision table, evaluated in order (increment-2 ADR 0003). It reads only hypotheses, evidence and
actions, never `loops`, so the node that counts the loop and the edge that routes agree.

1. Nothing left to ask and evidence exists: `rca`.
2. Nothing left to ask and no evidence: `escalate`, reason `source_outage` when every recorded
   read attempt failed, otherwise `no_evidence` (ADR 0004).
3. An affordable source is left: send to it again, and `loops` goes up by one.
4. Otherwise: `escalate`, reason `source_outage` when there is no evidence and every recorded
   read attempt failed, otherwise `budget_exhausted`.

Merge also writes `unreachable_sources` on every pass, so rca and the approval payload can name
the sources that never answered.
"""

from typing import Any, Literal

from langgraph.types import Send

from agentops.nodes.evidence import (
    every_source_read_failed,
    source_read_attempts,
    unreachable_sources,
)
from agentops.nodes.router import affordable, sources_to_ask
from agentops.state import INITIAL_INVESTIGATION_BUDGET, IncidentState

# The reasons merge can give for stopping before rca. The escalate node writes the state's
# `escalation_reason` from these.
StopReason = Literal["no_evidence", "budget_exhausted", "source_outage"]

__all__ = [
    "after_merge",
    "decide_next_step",
    "merge",
    "reason_for_escalating_before_rca",
    "source_read_attempts",
]


def merge(state: IncidentState) -> dict[str, Any]:
    """Write the remaining budget (never below zero) and the unreachable sources, count a loop
    when sources are re-asked, and write the stop reason when the next step is escalate."""
    next_step = decide_next_step(state)
    update: dict[str, Any] = {
        "investigation_budget": max(0, INITIAL_INVESTIGATION_BUDGET - source_read_attempts(state)),
        "loops": state["loops"] + 1 if isinstance(next_step, list) else state["loops"],
        "unreachable_sources": list(unreachable_sources(state)),
    }
    if next_step == "escalate":
        update["escalation_reason"] = reason_for_escalating_before_rca(state)
    return update


def after_merge(state: IncidentState) -> list[Send] | str:
    """The conditional edge after merge."""
    return decide_next_step(state)


def decide_next_step(state: IncidentState) -> list[Send] | str:
    """`rca`, `escalate`, or one `Send` per source to ask again."""
    sources = sources_to_ask(state)
    if not sources:
        return "rca" if state["evidence"] else "escalate"
    affordable_sources = affordable(state, sources)
    if not affordable_sources:
        return "escalate"
    return [Send(source, state) for source in affordable_sources]


def reason_for_escalating_before_rca(state: IncidentState) -> StopReason:
    """Why `decide_next_step` chose `escalate`: every source failed, nothing was found, or nothing
    more is affordable."""
    if not state["evidence"] and every_source_read_failed(state):
        return "source_outage"
    nothing_left_to_ask = not sources_to_ask(state)
    return "no_evidence" if nothing_left_to_ask and not state["evidence"] else "budget_exhausted"
