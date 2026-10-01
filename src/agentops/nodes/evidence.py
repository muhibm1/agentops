"""The evidence node: reads logs, metrics and deployments for the hour before the incident.

Every payload string has the prompt delimiters `<<<` and `>>>` broken up before it enters
state, so a log line cannot open or close the untrusted-evidence block the rca prompt builds.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel

from agentops.nodes import timed_action
from agentops.sources.base import EvidenceSource, Window
from agentops.state import Evidence, EvidenceKind, IncidentState

EVIDENCE_WINDOW = timedelta(minutes=60)
METRIC_NAMES = ["error_rate", "p99_latency_ms", "db_pool_utilisation"]


def collect_evidence(state: IncidentState, *, source: EvidenceSource) -> dict[str, Any]:
    """Call three source methods once each and return their items as `Evidence`."""
    service = state["service"]
    window = Window(start=state["opened_at"] - EVIDENCE_WINDOW, end=state["opened_at"])

    logs, logs_action = timed_action("get_logs", lambda: source.get_logs(service, window, None))
    metrics, metrics_action = timed_action(
        "get_metrics", lambda: source.get_metrics(service, METRIC_NAMES, window)
    )
    deployments, deployments_action = timed_action(
        "get_deployments", lambda: source.get_deployments(service, window)
    )

    evidence = [
        *(as_evidence(line, "get_logs", "log") for line in logs),
        *(as_evidence(series, "get_metrics", "metric") for series in metrics),
        *(as_evidence(item, "get_deployments", "deployment") for item in deployments),
    ]
    return {"evidence": evidence, "actions": [logs_action, metrics_action, deployments_action]}


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


def neutralise_delimiters(value: Any) -> Any:
    """Return `value` with every `<<<` and `>>>` in its strings spaced out, at any depth."""
    if isinstance(value, str):
        return value.replace("<<<", "< < <").replace(">>>", "> > >")
    if isinstance(value, dict):
        return {key: neutralise_delimiters(item) for key, item in value.items()}
    if isinstance(value, list):
        return [neutralise_delimiters(item) for item in value]
    return value
