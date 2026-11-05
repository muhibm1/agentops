"""Long-term memory: every finished incident, kept across threads in a LangGraph store.

The checkpointer is each thread's short-term memory: one incident's steps, resumable and
forkable. A store is LangGraph's memory across threads. When a graph is compiled with one, the
two nodes that end an incident (`postmortem` and `escalate`) each write one record under the
namespace `("incidents", <service>)`, keyed by the thread id, so a fork of an incident is
remembered beside the original rather than over it. `agentops incidents` lists them. Under
`langgraph dev` the server supplies a store of its own, and the same nodes write to it (believed
from LangGraph's documentation, not yet run here).

The records are written, never read back into a prompt. Giving triage the history of a service
would change what the model sees, and so the measured results, which describe the graph
without it. Reading them is the next step, and it would be measured like any other change.

A graph compiled without a store (every test that does not ask for one, and the eval) writes
nothing.
"""

from collections.abc import Mapping
from typing import Any

from langgraph.config import get_config, get_store
from langgraph.store.base import BaseStore

NAMESPACE = "incidents"
# How many characters of the postmortem or hand-off summary a record keeps.
SUMMARY_MAX_LENGTH = 500
# How many records a listing reads before sorting them newest first.
SEARCH_LIMIT = 10_000


def incident_record(state: Mapping[str, Any], thread_id: str | None = None) -> dict[str, Any]:
    """What a store keeps about a finished incident: how it ended, its cause and the action
    taken, and the first part of its summary. All JSON."""
    root_cause = state.get("root_cause")
    execution = state.get("execution")
    postmortem = state.get("postmortem")
    escalation = state.get("escalation")
    summary = postmortem.summary if postmortem is not None else None
    if summary is None and escalation is not None:
        summary = escalation.summary
    opened_at = state.get("opened_at")
    return {
        "incident_id": state["incident_id"],
        "thread_id": thread_id or state["incident_id"],
        "scenario_id": state.get("scenario_id"),
        "service": state["service"],
        "opened_at": opened_at.isoformat() if opened_at is not None else None,
        "status": state.get("status"),
        "root_cause": root_cause.label if root_cause is not None else None,
        "action": execution.action if execution is not None else None,
        "applied": execution.applied if execution is not None else None,
        "escalation_reason": state.get("escalation_reason"),
        "loops": state.get("loops"),
        "summary": (summary or "")[:SUMMARY_MAX_LENGTH],
    }


def remember_incident(state: Mapping[str, Any]) -> None:
    """Write the finished incident to the run's store; do nothing when it has none."""
    store = current_store()
    if store is None:
        return
    thread_id = get_config().get("configurable", {}).get("thread_id") or state["incident_id"]
    record = incident_record(state, str(thread_id))
    store.put((NAMESPACE, state["service"]), record["thread_id"], record)


def current_store() -> BaseStore | None:
    """The store the running graph was compiled with, or None (also outside a graph run, as
    when a test calls a node directly)."""
    try:
        return get_store()
    except RuntimeError:
        return None


def past_incidents(
    store: BaseStore, service: str | None = None, *, limit: int = 20
) -> list[dict[str, Any]]:
    """The remembered incidents, for one service or for all, newest first."""
    prefix = (NAMESPACE, service) if service else (NAMESPACE,)
    items = store.search(prefix, limit=SEARCH_LIMIT)
    items.sort(key=lambda item: item.updated_at, reverse=True)
    return [dict(item.value) for item in items[:limit]]
