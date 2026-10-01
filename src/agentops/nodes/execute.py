"""The execute node: runs the executor on the approved remediation and decides where to go next.

The thread id and the demo crash switch come only from the run's `configurable` dict, never
from an environment variable (spec R12, R19).
"""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send

from agentops.actions import executor
from agentops.actions.audit import AuditLog
from agentops.actions.registry import Registry
from agentops.nodes.router import route_to_sources
from agentops.sources.base import EvidenceSource
from agentops.state import IncidentState

VERIFIABLE_RESULTS = ("applied", "already_applied")


def execute(
    state: IncidentState,
    config: RunnableConfig,
    *,
    source: EvidenceSource,
    registry: Registry,
    audit: AuditLog,
) -> dict[str, Any]:
    """Write the execution record, its evidence and actions, and the fields the next edge reads.

    Raises ValueError when the run's `configurable` has no thread id, because every audit record
    must name its thread.
    """
    configurable = config.get("configurable") or {}
    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError("The execute node needs a thread_id in the run's configurable.")
    outcome = executor.execute(
        state,
        source,
        registry,
        audit,
        thread_id=thread_id,
        crash_after_apply=configurable.get("crash_after_apply") is True,
    )
    update: dict[str, Any] = {
        "execution": outcome.execution,
        "evidence": outcome.evidence,
        "actions": outcome.actions,
    }
    return {**update, **next_step_fields(state, outcome)}


def after_execute(state: IncidentState) -> list[Send] | str:
    """The edge after execute: `escalate` when the node wrote a reason, `verify` after an apply
    or a repeat of one, otherwise back to the router to investigate again."""
    if state["escalation_reason"] is not None:
        return "escalate"
    execution = state["execution"]
    if execution is not None and execution.result in VERIFIABLE_RESULTS:
        return "verify"
    return route_to_sources(state)


def next_step_fields(state: IncidentState, outcome: executor.ExecutionOutcome) -> dict[str, Any]:
    if outcome.kind in VERIFIABLE_RESULTS:
        return {"status": "verifying"}
    if outcome.kind == "refused":
        return {"status": "remediating", "escalation_reason": "action_not_allowed"}
    if outcome.kind == "apply_failed":
        return {"status": "remediating", "escalation_reason": "apply_failed"}
    if failed_before(state, outcome):
        return {"status": "remediating", "escalation_reason": "precondition_failed"}
    # A first failed precondition: the proposal is spent and the investigation goes round again
    # with the failure as new evidence (brief D17).
    return {
        "status": "investigating",
        "approval": None,
        "remediation": None,
        "root_cause": None,
        "loops": state["loops"] + 1,
    }


def failed_before(state: IncidentState, outcome: executor.ExecutionOutcome) -> bool:
    """True when the same change (same idempotency key) already failed a precondition."""
    if outcome.execution is None:
        return False
    key = outcome.execution.idempotency_key
    return any(
        item.kind == "precondition" and item.payload.get("idempotency_key") == key
        for item in state["evidence"]
    )
