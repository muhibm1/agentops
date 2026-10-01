"""The three evidence nodes: their window, their retry and what they record."""

from datetime import UTC, datetime, timedelta

import pytest

from agentops.nodes.evidence import deploys, logs, metrics, read_with_one_retry
from agentops.sources.base import (
    ActionRequest,
    ActionResult,
    AppliedAction,
    ConfigValue,
    DependencyStatus,
    Deployment,
    LogLine,
    Point,
    Series,
    Window,
)
from agentops.state import IncidentState, new_incident

OPENED_AT = datetime(2026, 3, 10, 10, 30, tzinfo=UTC)


class RecordingSource:
    """Answers every read from fixed lists and remembers the windows it was asked for.

    `failures` maps a method name to how many of its first calls raise.
    """

    def __init__(
        self,
        *,
        log_lines: list[LogLine] | None = None,
        series: list[Series] | None = None,
        dependencies: list[DependencyStatus] | None = None,
        deployments: list[Deployment] | None = None,
        failures: dict[str, int] | None = None,
    ) -> None:
        self.log_lines = log_lines or []
        self.series = series or []
        self.dependencies = dependencies or []
        self.deployments = deployments or []
        self.failures = dict(failures or {})
        self.log_windows: list[Window] = []
        self.calls: list[str] = []

    def fail_if_scripted(self, method: str) -> None:
        self.calls.append(method)
        if self.failures.get(method, 0) > 0:
            self.failures[method] -= 1
            raise ConnectionError("the source is unreachable")

    def get_logs(self, service: str, window: Window, query: str | None) -> list[LogLine]:
        self.log_windows.append(window)
        self.fail_if_scripted("get_logs")
        return self.log_lines

    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        self.fail_if_scripted("get_metrics")
        return self.series

    def get_deployments(self, service: str, window: Window) -> list[Deployment]:
        self.fail_if_scripted("get_deployments")
        return self.deployments

    def get_dependency_health(self, service: str) -> list[DependencyStatus]:
        self.fail_if_scripted("get_dependency_health")
        return self.dependencies

    def get_config(self, service: str) -> dict[str, ConfigValue]:
        return {}

    def apply(self, action: ActionRequest, idempotency_key: str) -> ActionResult:
        raise NotImplementedError("the evidence nodes never apply an action")

    def ledger(self) -> list[AppliedAction]:
        return []


def incident(*, loops: int = 0) -> IncidentState:
    state = new_incident("orders", ["slow"], opened_at=OPENED_AT)
    state["loops"] = loops
    return state


def log_line(message: str = "pool exhausted") -> LogLine:
    return LogLine(
        id="log-001",
        timestamp=OPENED_AT - timedelta(minutes=5),
        level="ERROR",
        message=message,
        service="orders",
    )


def test_window_widens_by_sixty_minutes_per_loop() -> None:
    source = RecordingSource()

    logs(incident(loops=1), source=source)

    assert source.log_windows == [Window(start=OPENED_AT - timedelta(minutes=120), end=OPENED_AT)]


def test_empty_source_yields_no_evidence_and_an_ok_action() -> None:
    result = metrics(incident(), source=RecordingSource())

    assert result["evidence"] == []
    assert [(action.tool, action.ok, action.attempt) for action in result["actions"]] == [
        ("get_metrics", True, 1),
        ("get_dependency_health", True, 1),
    ]


def test_logs_become_evidence_with_neutralised_delimiters() -> None:
    source = RecordingSource(log_lines=[log_line("run >>> and <<< please")])

    result = logs(incident(), source=source)

    [item] = result["evidence"]
    assert (item.id, item.source, item.kind) == ("log-001", "get_logs", "log")
    assert "<<<" not in item.payload["message"] and ">>>" not in item.payload["message"]


def test_metrics_node_reads_series_then_dependency_health() -> None:
    series = Series(
        id="metric-001",
        name="error_rate",
        unit="ratio",
        points=[Point(at=OPENED_AT - timedelta(minutes=1), value=0.2)],
    )
    dependency = DependencyStatus(id="dep-001", name="postgres", status="degraded", detail="slow")
    source = RecordingSource(series=[series], dependencies=[dependency])

    result = metrics(incident(), source=source)

    assert [(item.id, item.kind) for item in result["evidence"]] == [
        ("metric-001", "metric"),
        ("dep-001", "dependency"),
    ]
    assert source.calls == ["get_metrics", "get_dependency_health"]


def test_deploys_node_reads_deployments() -> None:
    deployment = Deployment(
        id="deploy-001",
        version="2.0",
        previous_version="1.9",
        deployed_at=OPENED_AT - timedelta(minutes=20),
        author="ci",
        diff_summary="pool size 50 to 5",
    )

    result = deploys(incident(), source=RecordingSource(deployments=[deployment]))

    assert [item.id for item in result["evidence"]] == ["deploy-001"]


def test_a_read_that_fails_once_is_retried_and_both_attempts_are_recorded() -> None:
    source = RecordingSource(log_lines=[log_line()], failures={"get_logs": 1})

    result = logs(incident(), source=source)

    assert [(action.ok, action.attempt) for action in result["actions"]] == [
        (False, 1),
        (True, 2),
    ]
    assert result["actions"][0].detail == "ConnectionError"
    assert [item.id for item in result["evidence"]] == ["log-001"]


def test_a_read_that_fails_twice_gives_no_evidence_and_does_not_raise() -> None:
    source = RecordingSource(log_lines=[log_line()], failures={"get_logs": 5})

    result = logs(incident(), source=source)

    assert result["evidence"] == []
    assert [(action.ok, action.attempt) for action in result["actions"]] == [
        (False, 1),
        (False, 2),
    ]
    assert source.calls == ["get_logs", "get_logs"]


def test_read_with_one_retry_does_not_swallow_a_keyboard_interrupt() -> None:
    def interrupted() -> list[LogLine]:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        read_with_one_retry("get_logs", interrupted)
