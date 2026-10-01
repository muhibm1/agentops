"""The verify node: re-reads the source after an action and decides, by a rule written in code,
whether the symptoms are cleared (ADR 0003).

Cleared means at least one signal arrived after the incident opened, every catalogue metric
observed since then has its latest value inside the healthy range below, no log line since then
is at level ERROR, and both the metric and the log read succeeded. No model takes part in the
decision.
"""

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send
from pydantic import BaseModel, ConfigDict

from agentops.actions.audit import AuditLog
from agentops.nodes import run_thread_id
from agentops.nodes.evidence import METRIC_CATALOGUE, as_evidence, read_with_one_retry
from agentops.nodes.router import route_to_sources
from agentops.sources.base import VERIFY_WINDOW, EvidenceSource, LogLine, Series, Window
from agentops.state import Action, Evidence, IncidentState

# Healthy ranges for every metric in METRIC_CATALOGUE (src/agentops/nodes/evidence.py). A new
# catalogue metric needs a range here in the same change.
HEALTHY_MAXIMUM: dict[str, float] = {
    "error_rate": 0.01,
    "p99_latency_ms": 500,
    "db_pool_utilisation": 0.85,
    "disk_io_utilisation": 0.85,
    "memory_utilisation": 0.85,
    "dependency_p99_ms": 1000,
    "ingestion_lag_seconds": 3600,
}
HEALTHY_MINIMUM: dict[str, float] = {"rows_written": 1}
ERROR_LEVEL = "ERROR"
# Verify's reads are recorded under their own tool names, so they never spend the
# investigation budget that merge charges for evidence reads.
METRICS_TOOL = "verify_get_metrics"
LOGS_TOOL = "verify_get_logs"


class VerifyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cleared: bool
    unhealthy_metrics: list[str]
    error_lines: list[str]
    # Sources ("metrics", "logs") whose post-action read failed on every attempt.
    unreadable_sources: list[str]


def verify(
    state: IncidentState,
    config: RunnableConfig,
    *,
    source: EvidenceSource,
    audit: AuditLog,
) -> dict[str, Any]:
    """Read the post-action signals, judge them, audit the verdict and write the next step.

    Raises ValueError when the run's `configurable` has no thread id.
    """
    thread_id = run_thread_id(config, "verify")
    round_number = verify_rounds(state) + 1
    series, lines, actions = read_post_action_signals(state, source)
    result = judge(series, lines, unreadable_sources(actions))
    execution = state["execution"]
    audit.append(
        state["incident_id"],
        thread_id,
        {
            "event": "verify",
            "idempotency_key": execution.idempotency_key if execution else None,
            "round": round_number,
            **result.model_dump(),
        },
    )
    evidence = verify_evidence(round_number, series, lines)
    return {"evidence": evidence, "actions": actions, **next_step_fields(state, result.cleared)}


def after_verify(state: IncidentState) -> list[Send] | str:
    """The edge after verify: `escalate` when verify wrote a reason, `postmortem` when the
    symptoms cleared (verify leaves the status at `verifying`), otherwise back to the router."""
    if state["escalation_reason"] is not None:
        return "escalate"
    if state["status"] == "verifying":
        return "postmortem"
    return route_to_sources(state)


def judge(
    series: list[Series], lines: list[LogLine], unreadable: list[str] | None = None
) -> VerifyResult:
    """ADR 0003's rule over signals already limited to after the incident opened. A source that
    could not be read is never "cleared": silence from a dead backend is not health."""
    unreadable = unreadable or []
    unhealthy = sorted(
        item.name for item in series if not is_healthy(item.name, latest_value(item))
    )
    error_lines = [line.id for line in lines if line.level.upper() == ERROR_LEVEL]
    has_signal = any(item.points for item in series) or bool(lines)
    return VerifyResult(
        cleared=has_signal and not unhealthy and not error_lines and not unreadable,
        unhealthy_metrics=unhealthy,
        error_lines=error_lines,
        unreadable_sources=unreadable,
    )


def unreadable_sources(actions: list[Action]) -> list[str]:
    """Which of metrics and logs have read attempts in `actions`, none of which succeeded."""
    unreadable = []
    for name, tool in (("logs", LOGS_TOOL), ("metrics", METRICS_TOOL)):
        attempts = [action for action in actions if action.tool == tool]
        if not any(action.ok for action in attempts):
            unreadable.append(name)
    return unreadable


def is_healthy(metric: str, value: float) -> bool:
    if metric in HEALTHY_MAXIMUM:
        return value <= HEALTHY_MAXIMUM[metric]
    if metric in HEALTHY_MINIMUM:
        return value >= HEALTHY_MINIMUM[metric]
    # A metric with no declared range cannot be judged, and "cannot judge" is not "healthy".
    return False


def latest_value(series: Series) -> float:
    return max(series.points, key=lambda point: point.at).value


def next_step_fields(state: IncidentState, cleared: bool) -> dict[str, Any]:
    """Cleared: keep `verifying` for the postmortem. Not cleared: investigate again while the
    last execution changed something and budget is left, otherwise hand off with the reason."""
    if cleared:
        return {"status": "verifying"}
    execution = state["execution"]
    if execution is None or not execution.applied:
        return {"escalation_reason": "symptoms_persist"}
    if state["investigation_budget"] <= 0:
        return {"escalation_reason": "budget_exhausted"}
    return {
        "status": "investigating",
        "approval": None,
        "remediation": None,
        "root_cause": None,
        "loops": state["loops"] + 1,
    }


def read_post_action_signals(
    state: IncidentState, source: EvidenceSource
) -> tuple[list[Series], list[LogLine], list[Action]]:
    """Metrics and logs from the incident's opening to thirty minutes after, keeping only points
    and lines later than the opening. A series left with no point is dropped."""
    opened_at = state["opened_at"]
    window = Window(start=opened_at, end=opened_at + VERIFY_WINDOW)
    series, metric_actions = read_with_one_retry(
        METRICS_TOOL, lambda: source.get_metrics(state["service"], METRIC_CATALOGUE, window)
    )
    lines, log_actions = read_with_one_retry(
        LOGS_TOOL, lambda: source.get_logs(state["service"], window, None)
    )
    later_series = [
        item.model_copy(update={"points": [p for p in item.points if p.at > opened_at]})
        for item in series
    ]
    return (
        [item for item in later_series if item.points],
        [line for line in lines if line.timestamp > opened_at],
        [*metric_actions, *log_actions],
    )


def verify_evidence(
    round_number: int, series: list[Series], lines: list[LogLine]
) -> list[Evidence]:
    """The post-action signals as evidence, each id prefixed `verify-<round>-`."""
    items = [
        *(as_evidence(item, METRICS_TOOL, "metric") for item in series),
        *(as_evidence(line, LOGS_TOOL, "log") for line in lines),
    ]
    return [item.model_copy(update={"id": f"verify-{round_number}-{item.id}"}) for item in items]


def verify_rounds(state: IncidentState) -> int:
    """How many times verify has run: each run makes one first-attempt metrics read."""
    return sum(
        1 for action in state["actions"] if action.tool == METRICS_TOOL and action.attempt == 1
    )
