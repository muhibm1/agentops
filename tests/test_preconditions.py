"""E8, E38, E64, E23, E25: runbook preconditions evaluated against the evidence source only."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from agentops.actions.preconditions import PreconditionResult, evaluate
from agentops.actions.registry import Registry, default_registry
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
from agentops.sources.scenario import Scenario, load_scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import IncidentState, Remediation, new_incident

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
REMEDIATE_SCENARIOS = [
    "clean_bad_deploy",
    "dependency_timeout_noisy_neighbour",
    "deploy_red_herring_db",
    "late_partition_zero_rows",
    "log_injection_drop_table",
    "schema_drift_ingestion",
    "two_concurrent_faults",
]
CHECK_KINDS = {
    "metric_above",
    "metric_below",
    "deployment_within_minutes",
    "previous_version_available",
    "dependency_not_healthy",
    "config_true",
    "target_named_in_logs",
}


def scenario_named(scenario_id: str) -> Scenario:
    return load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")


def incident_for(scenario: Scenario) -> IncidentState:
    return new_incident(
        scenario.service,
        scenario.symptoms,
        opened_at=scenario.incident_time,
        incident_id=scenario.id,
        scenario_id=scenario.id,
    )


def scripted_remediation(scenario: Scenario) -> Remediation:
    return Remediation.model_validate(scenario.scripted_outputs.rca["remediation"])


def remediation(action: str, target: str, params: dict[str, Any] | None = None) -> Remediation:
    return Remediation(
        action=action,
        target=target,
        params=params or {},
        reason="Shaped for a precondition test.",
        evidence_ids=["log-001"],
        risk="low",
        rollback="Undo it.",
    )


class ShapedSource:
    """An `EvidenceSource` whose every answer is set by the test; `apply` is never expected."""

    def __init__(
        self,
        *,
        series: list[Series] | None = None,
        deployments: list[Deployment] | None = None,
        config: dict[str, ConfigValue] | None = None,
        dependencies: list[DependencyStatus] | None = None,
    ) -> None:
        self._series = series or []
        self._deployments = deployments or []
        self._config = config or {}
        self._dependencies = dependencies or []

    def get_logs(self, service: str, window: Window, query: str | None) -> list[LogLine]:
        return []

    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        return [
            series.model_copy(
                update={"points": [point for point in series.points if window.contains(point.at)]}
            )
            for series in self._series
            if series.name in names
        ]

    def get_deployments(self, service: str, window: Window) -> list[Deployment]:
        return [item for item in self._deployments if window.contains(item.deployed_at)]

    def get_config(self, service: str) -> dict[str, ConfigValue]:
        return dict(self._config)

    def get_dependency_health(self, service: str) -> list[DependencyStatus]:
        return list(self._dependencies)

    def apply(self, action: ActionRequest, idempotency_key: str) -> ActionResult:
        raise AssertionError("Preconditions must never apply anything.")

    def ledger(self) -> list[AppliedAction]:
        return []


class RaisingSource(ShapedSource):
    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        raise TimeoutError("metrics backend timed out")


@pytest.fixture(scope="module")
def registry() -> Registry:
    return default_registry()


@pytest.fixture
def incident() -> IncidentState:
    return incident_for(scenario_named("clean_bad_deploy"))


def one_series(incident: IncidentState, name: str, value: float) -> Series:
    return Series(
        id=f"metric-{name}",
        name=name,
        unit="ratio",
        points=[Point(at=incident["opened_at"] - timedelta(minutes=5), value=value)],
    )


def deployment_at(incident: IncidentState, minutes_ago: int, previous: str) -> Deployment:
    return Deployment(
        id="deploy-001",
        version="v2.0.0",
        previous_version=previous,
        deployed_at=incident["opened_at"] - timedelta(minutes=minutes_ago),
        author="ci-bot",
        diff_summary="A change.",
    )


# E8


@pytest.mark.parametrize("scenario_id", REMEDIATE_SCENARIOS)
def test_every_runbook_check_passes_on_its_scenarios_source(
    registry: Registry, scenario_id: str
) -> None:
    scenario = scenario_named(scenario_id)
    proposed = scripted_remediation(scenario)

    results = evaluate(
        registry.get(proposed.action), proposed, incident_for(scenario), SimulatorSource(scenario)
    )

    assert results
    for result in results:
        assert result.passed, f"{scenario_id}: {result.check} failed: {result.detail}"
        assert result.observed is not None
        assert result.detail


def test_the_scenarios_exercise_every_check_kind(registry: Registry) -> None:
    exercised: set[str] = set()
    for scenario_id in REMEDIATE_SCENARIOS:
        scenario = scenario_named(scenario_id)
        proposed = scripted_remediation(scenario)
        results = evaluate(
            registry.get(proposed.action),
            proposed,
            incident_for(scenario),
            SimulatorSource(scenario),
        )
        exercised.update(result.check for result in results)

    assert exercised == CHECK_KINDS


# E38


def test_a_raising_source_fails_the_check_without_raising(
    registry: Registry, incident: IncidentState
) -> None:
    proposed = remediation("scale_db_pool", "postgres", {"pool_size": 96})

    (result,) = evaluate(registry.get("scale_db_pool"), proposed, incident, RaisingSource())

    assert result == PreconditionResult(
        check="metric_above", passed=False, observed=None, detail=result.detail
    )
    assert "TimeoutError" in result.detail


def test_an_absent_metric_fails_the_check_with_no_observed_value(
    registry: Registry, incident: IncidentState
) -> None:
    proposed = remediation("scale_db_pool", "postgres", {"pool_size": 96})

    (result,) = evaluate(registry.get("scale_db_pool"), proposed, incident, ShapedSource())

    assert result.passed is False
    assert result.observed is None
    assert "no points" in result.detail


# E64


def failing_case(name: str, incident: IncidentState) -> tuple[str, Remediation, ShapedSource]:
    """The action to evaluate and a source shaped so that its named check fails."""
    if name == "deployment_within_minutes":
        source = ShapedSource(deployments=[deployment_at(incident, 200, "v1.9.0")])
        return "rollback_deploy", remediation("rollback_deploy", "deploy-001"), source
    if name == "previous_version_available":
        source = ShapedSource(deployments=[deployment_at(incident, 20, "v1.9.0")])
        proposed = remediation("rollback_deploy", "deploy-001", {"to_version": "v0.1.0"})
        return "rollback_deploy", proposed, source
    if name == "dependency_not_healthy":
        healthy = DependencyStatus(
            id="dep-fraud-check", name="fraud-check", status="healthy", detail="Fine."
        )
        proposed = remediation(
            "circuit_break_dependency", "fraud-check", {"fallback": "manual_review_queue"}
        )
        return "circuit_break_dependency", proposed, ShapedSource(dependencies=[healthy])
    if name == "config_true":
        source = ShapedSource(config={"batch_jobs_enabled": False})
        return "pause_batch_job", remediation("pause_batch_job", "reindex-job"), source
    source = ShapedSource(series=[one_series(incident, "rows_written", 5)])
    proposed = remediation("rerun_partition", "rollup", {"partition": "2026-03-18"})
    return "rerun_partition", proposed, source


@pytest.mark.parametrize(
    "check",
    [
        "deployment_within_minutes",
        "previous_version_available",
        "dependency_not_healthy",
        "config_true",
        "metric_below",
    ],
)
def test_a_check_shaped_to_fail_returns_a_failed_result_with_what_it_saw(
    registry: Registry, incident: IncidentState, check: str
) -> None:
    action, proposed, source = failing_case(check, incident)

    results = evaluate(registry.get(action), proposed, incident, source)

    (result,) = [result for result in results if result.check == check]
    assert result.passed is False
    assert result.observed is not None
    assert result.detail.endswith(".")


# The deployment checks must look at the deployment the action targets, not just the latest one.
@pytest.mark.parametrize("check", ["deployment_within_minutes", "previous_version_available"])
def test_a_deployment_check_fails_when_the_target_is_not_the_latest_deployment(
    registry: Registry, incident: IncidentState, check: str
) -> None:
    source = ShapedSource(deployments=[deployment_at(incident, 20, "v1.9.0")])
    foreign = remediation("rollback_deploy", "deploy-999")

    results = evaluate(registry.get("rollback_deploy"), foreign, incident, source)

    (result,) = [result for result in results if result.check == check]
    assert result.passed is False
    assert result.observed == "deploy-001"
    assert "deploy-999" in result.detail


@pytest.mark.parametrize("check", ["deployment_within_minutes", "previous_version_available"])
def test_a_deployment_check_passes_when_the_target_is_the_latest_deployment(
    registry: Registry, incident: IncidentState, check: str
) -> None:
    source = ShapedSource(deployments=[deployment_at(incident, 20, "v1.9.0")])
    own = remediation("rollback_deploy", "deploy-001")

    results = evaluate(registry.get("rollback_deploy"), own, incident, source)

    (result,) = [result for result in results if result.check == check]
    assert result.passed is True


class RecordingSource(ShapedSource):
    """A shaped source that serves the given log lines and remembers every window it is asked."""

    def __init__(self, *, logs: list[LogLine] | None = None, **shaped: Any) -> None:
        super().__init__(**shaped)
        self._logs = logs or []
        self.metric_windows: list[Window] = []
        self.log_windows: list[Window] = []

    def get_logs(self, service: str, window: Window, query: str | None) -> list[LogLine]:
        self.log_windows.append(window)
        return [line for line in self._logs if window.contains(line.timestamp)]

    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        self.metric_windows.append(window)
        return super().get_metrics(service, names, window)


class RaisingLogSource(ShapedSource):
    def get_logs(self, service: str, window: Window, query: str | None) -> list[LogLine]:
        raise ConnectionError("log backend unreachable")


def log_line(incident: IncidentState, line_id: str, minutes_before: int, message: str) -> LogLine:
    return LogLine(
        id=line_id,
        timestamp=incident["opened_at"] - timedelta(minutes=minutes_before),
        level="INFO",
        message=message,
        service=incident["service"],
    )


def target_named_in_logs_result(
    registry: Registry, incident: IncidentState, source: ShapedSource
) -> PreconditionResult:
    proposed = remediation("pause_batch_job", "reindex-job-nightly")
    results = evaluate(registry.get("pause_batch_job"), proposed, incident, source)
    (result,) = [result for result in results if result.check == "target_named_in_logs"]
    return result


# E23 (increment 4, R5): a metric check looks as far ahead as the action's verify window.
def test_metric_preconditions_use_the_actions_verify_window(
    registry: Registry, incident: IncidentState
) -> None:
    entry = registry.get("scale_db_pool").model_copy(update={"verify_window_minutes": 90})
    source = RecordingSource(series=[one_series(incident, "db_pool_utilisation", 0.97)])
    proposed = remediation("scale_db_pool", "postgres", {"pool_size": 96})

    (result,) = evaluate(entry, proposed, incident, source)

    assert result.passed is True
    assert [window.end for window in source.metric_windows] == [
        incident["opened_at"] + timedelta(minutes=90)
    ]


# E25 (increment 4, R6): a job action needs the incident's own logs to name its target. The
# window is the hour before the incident opened (spec, Design "Job names").
def test_target_named_in_logs_passes_only_when_the_incident_logs_name_it(
    registry: Registry, incident: IncidentState
) -> None:
    naming = RecordingSource(
        logs=[
            log_line(incident, "log-001", 50, "search-api served 640 queries"),
            log_line(incident, "log-002", 40, "Reindex-Job-Nightly started"),
        ]
    )
    silent = RecordingSource(logs=[log_line(incident, "log-001", 50, "search-api is slow")])

    named = target_named_in_logs_result(registry, incident, naming)
    unnamed = target_named_in_logs_result(registry, incident, silent)
    raised = target_named_in_logs_result(registry, incident, RaisingLogSource())

    assert named.passed is True
    assert named.observed == "log-002"
    assert naming.log_windows == [
        Window(start=incident["opened_at"] - timedelta(minutes=60), end=incident["opened_at"])
    ]
    assert unnamed.passed is False
    assert unnamed.observed is None
    assert raised.passed is False
    assert raised.observed is None
    assert "ConnectionError" in raised.detail


def test_target_named_in_logs_ignores_lines_outside_the_hour_before_the_opening(
    registry: Registry, incident: IncidentState
) -> None:
    too_early = log_line(incident, "log-001", 61, "reindex-job-nightly started")
    after_opening = log_line(incident, "log-002", -5, "reindex-job-nightly still running")
    source = RecordingSource(logs=[too_early, after_opening])

    result = target_named_in_logs_result(registry, incident, source)

    assert result.passed is False
    assert result.observed is None


def test_target_named_in_logs_fails_when_a_line_names_only_part_of_the_target(
    registry: Registry, incident: IncidentState
) -> None:
    source = RecordingSource(logs=[log_line(incident, "log-001", 30, "reindex-job started")])

    result = target_named_in_logs_result(registry, incident, source)

    assert result.passed is False
    assert result.observed is None
