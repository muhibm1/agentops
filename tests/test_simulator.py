"""E9 to E15 and E38: the simulator serves only signals, filters reads and applies actions once."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agentops.sources.base import ActionRequest, EvidenceSource, Window
from agentops.sources.scenario import Scenario, load_scenario
from agentops.sources.simulator import SimulatorSource

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
CLEAN_BAD_DEPLOY = "clean_bad_deploy"
DEPLOY_RED_HERRING_DB = "deploy_red_herring_db"
HIDDEN_SECTION_NAMES = [
    "ground_truth",
    "correct_action",
    "wrong_actions",
    "required_evidence",
    "scripted_outputs",
    "post_action_world",
]


def scenario_named(scenario_id: str) -> Scenario:
    return load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")


def window_before(moment: datetime, minutes: int) -> Window:
    return Window(start=moment - timedelta(minutes=minutes), end=moment)


def whole_day_around(moment: datetime) -> Window:
    return Window(start=moment - timedelta(hours=12), end=moment + timedelta(hours=12))


def latest_value(source: SimulatorSource, metric_name: str, window: Window) -> float:
    (series,) = source.get_metrics("payments", [metric_name], window)
    return max(series.points, key=lambda point: point.at).value


def test_simulator_source_satisfies_the_evidence_source_protocol() -> None:
    source: EvidenceSource = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY))

    assert isinstance(source, EvidenceSource)


def test_reads_respect_window_query_and_names_and_keep_scenario_ids() -> None:
    scenario = scenario_named(CLEAN_BAD_DEPLOY)
    source = SimulatorSource(scenario)
    window = window_before(scenario.incident_time, 60)
    declared_ids = {line.id for line in scenario.signals.logs}

    all_logs = source.get_logs("checkout", window, None)
    matching_logs = source.get_logs("checkout", window, "keyerror")
    metrics = source.get_metrics("checkout", ["error_rate"], window)
    deployments = source.get_deployments("checkout", window)

    assert all_logs
    assert all(window.start <= line.timestamp <= window.end for line in all_logs)
    assert {line.id for line in all_logs} <= declared_ids
    assert len(matching_logs) == 5
    assert all("KeyError" in line.message for line in matching_logs)
    assert [series.name for series in metrics] == ["error_rate"]
    assert metrics[0].id == "metric-error_rate"
    assert [deployment.id for deployment in deployments] == ["deploy-001"]


def test_empty_window_unknown_metric_and_unicode_query_return_empty() -> None:
    scenario = scenario_named(CLEAN_BAD_DEPLOY)
    source = SimulatorSource(scenario)
    first_log_at = min(line.timestamp for line in scenario.signals.logs)
    early_window = window_before(first_log_at - timedelta(minutes=1), 30)
    incident_window = window_before(scenario.incident_time, 60)

    early_logs = source.get_logs("checkout", early_window, None)
    unknown_metrics = source.get_metrics("checkout", ["no_such_metric"], incident_window)
    unicode_query_logs = source.get_logs("checkout", incident_window, "ünïcødé 障害")

    assert early_logs == []
    assert unknown_metrics == []
    assert unicode_query_logs == []


@pytest.mark.parametrize("scenario_id", [CLEAN_BAD_DEPLOY, DEPLOY_RED_HERRING_DB])
def test_hidden_scenario_sections_never_leave_the_source(scenario_id: str) -> None:
    scenario = scenario_named(scenario_id)
    source = SimulatorSource(scenario)
    window = whole_day_around(scenario.incident_time)
    service = scenario.service
    metric_names = [series.name for series in scenario.signals.metrics]

    served = {
        "logs": [line.model_dump(mode="json") for line in source.get_logs(service, window, None)],
        "metrics": [
            series.model_dump(mode="json")
            for series in source.get_metrics(service, metric_names, window)
        ],
        "deployments": [
            deployment.model_dump(mode="json")
            for deployment in source.get_deployments(service, window)
        ],
        "config": source.get_config(service),
        "dependencies": [
            status.model_dump(mode="json") for status in source.get_dependency_health(service)
        ],
        "ledger": [entry.model_dump(mode="json") for entry in source.ledger()],
    }
    dump = json.dumps(served)

    assert served["logs"] and served["metrics"] and served["deployments"]
    for hidden in [*HIDDEN_SECTION_NAMES, scenario.ground_truth.root_cause, scenario.id]:
        assert hidden not in dump


def test_apply_mutates_the_world_and_records_the_ledger() -> None:
    scenario = scenario_named(DEPLOY_RED_HERRING_DB)
    source = SimulatorSource(scenario)
    window = whole_day_around(scenario.incident_time)
    request = ActionRequest(action="scale_db_pool", target="payments-db", params={"pool_size": 96})
    assert latest_value(source, "db_pool_utilisation", window) == pytest.approx(0.97)

    result = source.apply(request, "key-scale-1")

    assert result.ok is True
    assert result.applied is True
    assert result.idempotency_key == "key-scale-1"
    assert latest_value(source, "db_pool_utilisation", window) < 0.6
    assert [entry.idempotency_key for entry in source.ledger()] == ["key-scale-1"]


def test_apply_is_idempotent_on_the_key() -> None:
    scenario = scenario_named(DEPLOY_RED_HERRING_DB)
    source = SimulatorSource(scenario)
    window = whole_day_around(scenario.incident_time)
    request = ActionRequest(action="scale_db_pool", target="payments-db", params={"pool_size": 96})
    source.apply(request, "key-scale-1")
    metrics_after_first_apply = source.get_metrics("payments", ["db_pool_utilisation"], window)

    repeated = source.apply(request, "key-scale-1")

    assert repeated.ok is True
    assert repeated.applied is False
    assert len(source.ledger()) == 1
    assert source.get_metrics("payments", ["db_pool_utilisation"], window) == (
        metrics_after_first_apply
    )


def test_apply_refuses_an_action_the_world_cannot_model() -> None:
    scenario = scenario_named(CLEAN_BAD_DEPLOY)
    source = SimulatorSource(scenario)
    window = whole_day_around(scenario.incident_time)
    metric_names = [series.name for series in scenario.signals.metrics]
    logs_before = source.get_logs("checkout", window, None)
    metrics_before = source.get_metrics("checkout", metric_names, window)

    result = source.apply(ActionRequest(action="drop_table", target="orders"), "key-drop-1")

    assert result.ok is False
    assert result.applied is False
    assert "drop_table" in result.detail
    assert source.ledger() == []
    assert source.get_logs("checkout", window, None) == logs_before
    assert source.get_metrics("checkout", metric_names, window) == metrics_before


def test_apply_on_one_source_never_mutates_the_scenario_or_another_source() -> None:
    scenario = scenario_named(CLEAN_BAD_DEPLOY)
    signals_before = scenario.signals.model_dump(mode="json")
    first = SimulatorSource(scenario)
    second = SimulatorSource(scenario)
    window = whole_day_around(scenario.incident_time)
    second_metrics_before = second.get_metrics("checkout", ["error_rate"], window)

    first.apply(ActionRequest(action="rollback_deploy", target="deploy-001"), "key-rollback-1")

    assert first.get_metrics("checkout", ["error_rate"], window) != second_metrics_before
    assert second.get_metrics("checkout", ["error_rate"], window) == second_metrics_before
    assert scenario.signals.model_dump(mode="json") == signals_before
    assert len(first.ledger()) == 1
    assert second.ledger() == []


def test_returned_items_are_copies_a_caller_cannot_use_to_edit_the_world() -> None:
    scenario = scenario_named(CLEAN_BAD_DEPLOY)
    source = SimulatorSource(scenario)
    window = whole_day_around(scenario.incident_time)
    (series,) = source.get_metrics("checkout", ["error_rate"], window)

    series.points.clear()
    source.get_config("checkout").clear()

    assert source.get_metrics("checkout", ["error_rate"], window)[0].points
    assert source.get_config("checkout")


def test_reads_for_another_service_return_nothing() -> None:
    scenario = scenario_named(CLEAN_BAD_DEPLOY)
    source = SimulatorSource(scenario)
    window = whole_day_around(scenario.incident_time)

    assert source.get_logs("billing", window, None) == []
    assert source.get_metrics("billing", ["error_rate"], window) == []
    assert source.get_deployments("billing", window) == []
    assert source.get_config("billing") == {}
    assert source.get_dependency_health("billing") == []
