"""The three evidence nodes: `logs`, `metrics` and `deploys`, each reading one kind of source.

They run in one super-step and append through the `evidence` and `actions` reducers. A read that
fails is retried once inside the node and both attempts are recorded; a source that fails twice
yields no evidence and never fails the run (ADR 0001).

Every payload string has the prompt delimiters `<<<` and `>>>` broken up before it enters
state, so a log line cannot open or close the untrusted-evidence block the rca prompt builds.
"""

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from agentops.nodes import neutralise_delimiters
from agentops.sources.base import EvidenceSource, Window
from agentops.state import Action, Evidence, EvidenceKind, IncidentState

SourceName = Literal["logs", "metrics", "deploys"]
EVIDENCE_WINDOW = timedelta(minutes=60)
READ_ATTEMPTS = 2
METRIC_CATALOGUE = [
    "error_rate",
    "p99_latency_ms",
    "db_pool_utilisation",
    "dependency_p99_latency_ms",
    "rows_written",
    "ingestion_lag_seconds",
    "disk_io_utilisation",
    "memory_utilisation",
]
# The source reads each node makes, first read first. Merge charges one budget unit per attempt
# at any of these tools, and the router treats the first as "this source was asked".
SOURCE_READ_TOOLS: dict[SourceName, tuple[str, ...]] = {
    "logs": ("get_logs",),
    "metrics": ("get_metrics", "get_dependency_health"),
    "deploys": ("get_deployments",),
}

SourceItem = TypeVar("SourceItem", bound=BaseModel)


def logs(state: IncidentState, *, source: EvidenceSource) -> dict[str, Any]:
    """Read the service's log lines for the current window."""
    window = window_for(state)
    items, actions = read_with_one_retry(
        "get_logs", lambda: source.get_logs(state["service"], window, None)
    )
    return {"evidence": as_evidence_list(items, "get_logs", "log"), "actions": actions}


def metrics(state: IncidentState, *, source: EvidenceSource) -> dict[str, Any]:
    """Read the metric catalogue for the current window, then the dependency health."""
    window = window_for(state)
    series, series_actions = read_with_one_retry(
        "get_metrics", lambda: source.get_metrics(state["service"], METRIC_CATALOGUE, window)
    )
    dependencies, dependency_actions = read_with_one_retry(
        "get_dependency_health", lambda: source.get_dependency_health(state["service"])
    )
    return {
        "evidence": [
            *as_evidence_list(series, "get_metrics", "metric"),
            *as_evidence_list(dependencies, "get_dependency_health", "dependency"),
        ],
        "actions": [*series_actions, *dependency_actions],
    }


def deploys(state: IncidentState, *, source: EvidenceSource) -> dict[str, Any]:
    """Read the service's deployments for the current window."""
    window = window_for(state)
    items, actions = read_with_one_retry(
        "get_deployments", lambda: source.get_deployments(state["service"], window)
    )
    return {
        "evidence": as_evidence_list(items, "get_deployments", "deployment"),
        "actions": actions,
    }


def window_for(state: IncidentState) -> Window:
    """Sixty minutes times (loops + 1) before the incident opened; each loop looks further back."""
    return Window(
        start=state["opened_at"] - EVIDENCE_WINDOW * (state["loops"] + 1), end=state["opened_at"]
    )


def source_read_attempts(state: IncidentState) -> int:
    """How many source read attempts the state records, failed attempts included."""
    read_tools = {tool for tools in SOURCE_READ_TOOLS.values() for tool in tools}
    return sum(1 for action in state["actions"] if action.tool in read_tools)


def read_with_one_retry(
    tool: str, call: Callable[[], list[SourceItem]], attempts: int = READ_ATTEMPTS
) -> tuple[list[SourceItem], list[Action]]:
    """Run `call` up to `attempts` times; return its items and one `Action` per attempt.

    Every ordinary exception is caught and recorded with its type name, because a dead source must
    not fail the run. After the last failed attempt the items are empty.
    """
    actions: list[Action] = []
    for attempt in range(1, attempts + 1):
        started = time.perf_counter()
        try:
            items = call()
        except Exception as error:
            actions.append(timed_read_action(tool, started, attempt, error))
            continue
        actions.append(timed_read_action(tool, started, attempt, None))
        return items, actions
    return [], actions


def timed_read_action(tool: str, started: float, attempt: int, error: Exception | None) -> Action:
    return Action(
        tool=tool,
        ok=error is None,
        latency_ms=(time.perf_counter() - started) * 1000,
        detail=None if error is None else type(error).__name__,
        attempt=attempt,
    )


def as_evidence_list(
    items: list[SourceItem], source_method: str, kind: EvidenceKind
) -> list[Evidence]:
    return [as_evidence(item, source_method, kind) for item in items]


def as_evidence(item: BaseModel, source_method: str, kind: EvidenceKind) -> Evidence:
    """Copy a source item into `Evidence`, keeping its id and neutralising its delimiters."""
    payload = neutralise_delimiters(item.model_dump(mode="json"))
    return Evidence(
        id=payload["id"],
        source=source_method,
        kind=kind,
        payload=payload,
        collected_at=datetime.now(UTC),
    )
