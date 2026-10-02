"""E9 to E15 and E38: the simulator serves only signals, filters reads and applies actions once."""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from agentops.sources.base import ActionRequest, EvidenceSource, Window
from agentops.sources.scenario import Scenario, load_scenario
from agentops.sources.simulator import LedgerError, SimulatorSource, ledger_path_for

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


# E32, E33, E41 (R20): the ledger file survives the process, and combined worlds.

TWO_CONCURRENT_FAULTS = "two_concurrent_faults"
ROLLBACK = ActionRequest(action="rollback_deploy", target="deploy-001")
SCALE_POOL = ActionRequest(action="scale_db_pool", target="postgres", params={"pool_size": 96})


def latest_on(source: SimulatorSource, service: str, metric_name: str, window: Window) -> float:
    (series,) = source.get_metrics(service, [metric_name], window)
    return max(series.points, key=lambda point: point.at).value


def ledger_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_apply_appends_one_json_line_to_the_ledger_file_before_returning(tmp_path: Path) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    source = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)

    source.apply(ROLLBACK, "key-rollback-1")

    assert ledger_lines(ledger_path) == [
        {
            "idempotency_key": "key-rollback-1",
            "action": "rollback_deploy",
            "target": "deploy-001",
            "params": {},
        }
    ]


def test_refused_and_repeated_applies_add_nothing_to_the_ledger_file(tmp_path: Path) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    source = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)

    refused = source.apply(ActionRequest(action="drop_table", target="orders"), "key-drop-1")
    assert not ledger_path.exists()
    source.apply(ROLLBACK, "key-rollback-1")
    repeated = source.apply(ROLLBACK, "key-rollback-1")

    assert refused.ok is False
    assert repeated.applied is False
    assert len(ledger_lines(ledger_path)) == 1


def test_a_source_on_a_ledger_path_with_no_file_yet_starts_empty_and_writes_nothing(
    tmp_path: Path,
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")

    source = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)

    assert source.ledger() == []
    assert list(tmp_path.iterdir()) == []


def test_a_new_source_on_the_same_ledger_file_serves_the_post_action_world(
    tmp_path: Path,
) -> None:
    scenario = scenario_named(CLEAN_BAD_DEPLOY)
    window = whole_day_around(scenario.incident_time)
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    SimulatorSource(scenario, ledger_path=ledger_path).apply(ROLLBACK, "key-rollback-1")
    applied_in_memory = SimulatorSource(scenario)
    applied_in_memory.apply(ROLLBACK, "key-rollback-1")

    restarted = SimulatorSource(scenario, ledger_path=ledger_path)
    repeated = restarted.apply(ROLLBACK, "key-rollback-1")

    assert restarted.get_metrics("checkout", ["error_rate"], window) == (
        applied_in_memory.get_metrics("checkout", ["error_rate"], window)
    )
    assert [entry.idempotency_key for entry in restarted.ledger()] == ["key-rollback-1"]
    assert repeated.ok is True
    assert repeated.applied is False
    assert len(ledger_lines(ledger_path)) == 1


def test_scenario_six_serves_the_combined_world_once_both_actions_are_applied() -> None:
    scenario = scenario_named(TWO_CONCURRENT_FAULTS)
    source = SimulatorSource(scenario)
    window = whole_day_around(scenario.incident_time)

    source.apply(ROLLBACK, "key-rollback-1")
    pool_after_rollback = latest_on(source, "orders", "db_pool_utilisation", window)
    source.apply(SCALE_POOL, "key-scale-1")

    assert pool_after_rollback == pytest.approx(0.97)
    assert latest_on(source, "orders", "db_pool_utilisation", window) == pytest.approx(0.55)
    assert latest_on(source, "orders", "error_rate", window) == pytest.approx(0.005)
    assert latest_on(source, "orders", "p99_latency_ms", window) == pytest.approx(300)


def test_a_restarted_source_replays_both_actions_into_the_combined_world(tmp_path: Path) -> None:
    scenario = scenario_named(TWO_CONCURRENT_FAULTS)
    window = whole_day_around(scenario.incident_time)
    ledger_path = ledger_path_for(tmp_path, "inc-006")
    first = SimulatorSource(scenario, ledger_path=ledger_path)
    first.apply(ROLLBACK, "key-rollback-1")
    first.apply(SCALE_POOL, "key-scale-1")

    restarted = SimulatorSource(scenario, ledger_path=ledger_path)

    metric_names = [series.name for series in scenario.signals.metrics]
    assert restarted.get_metrics("orders", metric_names, window) == (
        first.get_metrics("orders", metric_names, window)
    )
    assert restarted.ledger() == first.ledger()


@pytest.mark.parametrize(
    "second_line",
    [
        '{"idempotency_key": "k"}',
        json.dumps({"idempotency_key": "k", **ROLLBACK.model_dump(), "x": 1}),
    ],
    ids=["missing fields", "unknown key"],
)
def test_a_malformed_ledger_line_stops_the_source_naming_the_line(
    tmp_path: Path, second_line: str
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    first_line = json.dumps({"idempotency_key": "key-rollback-1", **ROLLBACK.model_dump()})
    ledger_path.write_text(f"{first_line}\n{second_line}\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="line 2"):
        SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)


def test_a_ledger_line_for_an_action_the_world_cannot_model_stops_the_source(
    tmp_path: Path,
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    entry = {"idempotency_key": "key-drop-1", "action": "drop_table", "target": "x", "params": {}}
    ledger_path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    with pytest.raises(LedgerError, match="line 1"):
        SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)


@pytest.mark.parametrize("incident_id", ["../x", "a/b", "C:\\x", "", "x\n", "Upper"])
def test_ledger_path_for_refuses_an_incident_id_outside_the_signal_id_pattern(
    tmp_path: Path, incident_id: str
) -> None:
    with pytest.raises(LedgerError, match="incident id"):
        ledger_path_for(tmp_path, incident_id)

    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("file_name", ["inc-001.jsonl", ".ledger.jsonl", "Inc 1.ledger.jsonl"])
def test_a_ledger_path_whose_name_is_not_an_incident_ledger_is_refused(
    tmp_path: Path, file_name: str
) -> None:
    with pytest.raises(LedgerError, match="incident id"):
        SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=tmp_path / file_name)

    assert list(tmp_path.iterdir()) == []


def test_the_same_action_under_a_new_key_does_not_merge_its_world_a_second_time(
    tmp_path: Path,
) -> None:
    scenario = scenario_named(CLEAN_BAD_DEPLOY)
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    source = SimulatorSource(scenario, ledger_path=ledger_path)
    window = Window(start=scenario.incident_time, end=scenario.incident_time + timedelta(hours=1))
    other_version = ActionRequest(
        action="rollback_deploy", target="deploy-001", params={"to_version": "v2.14.0"}
    )

    source.apply(ROLLBACK, "key-rollback-1")
    second = source.apply(other_version, "key-rollback-2")
    replayed = SimulatorSource(scenario, ledger_path=ledger_path)

    assert second.applied is True
    for served in (source, replayed):
        log_ids = [line.id for line in served.get_logs(scenario.service, window, None)]
        assert log_ids.count("log-009") == 1
        point_times = [
            point.at
            for series in served.get_metrics(scenario.service, ["error_rate"], window)
            for point in series.points
        ]
        assert len(point_times) == len(set(point_times))
    assert len(source.ledger()) == 2


TORN_FRAGMENT = '{"idempotency_key": "ab'
SEAL_MARKER = '{"torn_seal": true}'
ROLLBACK_LINE = json.dumps({"idempotency_key": "key-rollback-1", **ROLLBACK.model_dump()})


def test_a_torn_final_ledger_line_is_skipped_and_counted(tmp_path: Path) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n{TORN_FRAGMENT}", encoding="utf-8")

    source = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)

    assert [entry.idempotency_key for entry in source.ledger()] == ["key-rollback-1"]
    assert source.torn_ledger_lines == [2]


def test_a_ledger_holding_only_a_torn_fragment_replays_nothing_and_counts_line_one(
    tmp_path: Path,
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(TORN_FRAGMENT, encoding="utf-8")

    source = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)

    assert source.ledger() == []
    assert source.torn_ledger_lines == [1]


def test_a_final_fragment_cut_inside_a_multibyte_character_is_torn(tmp_path: Path) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    unicode_entry = json.dumps(
        {"idempotency_key": "key-café", **ROLLBACK.model_dump()}, ensure_ascii=False
    ).encode("utf-8")
    cut_inside_e_acute = unicode_entry.index("é".encode()) + 1
    ledger_path.write_bytes(ROLLBACK_LINE.encode() + b"\n" + unicode_entry[:cut_inside_e_acute])

    source = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)

    assert [entry.idempotency_key for entry in source.ledger()] == ["key-rollback-1"]
    assert source.torn_ledger_lines == [2]


def test_a_ledger_with_no_torn_line_reports_none(tmp_path: Path) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n", encoding="utf-8")

    with_file = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)
    without_file = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY))

    assert with_file.torn_ledger_lines == []
    assert without_file.torn_ledger_lines == []


def test_an_append_after_a_torn_line_starts_a_fresh_line(tmp_path: Path) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n{TORN_FRAGMENT}", encoding="utf-8")
    source = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)

    result = source.apply(ROLLBACK, "key-rollback-2")

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert result.applied is True
    assert len(lines) == 4
    assert lines[1] == TORN_FRAGMENT
    assert lines[2] == SEAL_MARKER
    assert json.loads(lines[3])["idempotency_key"] == "key-rollback-2"
    assert [entry.idempotency_key for entry in source.ledger()] == [
        "key-rollback-1",
        "key-rollback-2",
    ]
    assert source.torn_ledger_lines == [2]


# D21, changed by the seal marker: a fragment sealed by a later append used to raise on the next
# replay, which made the thread unreadable after a recovered crash. The marker line now tells a
# sealed fragment from corruption, so the second process replays it and counts the fragment.
def test_a_later_source_on_a_ledger_with_a_sealed_fragment_replays_and_counts_it(
    tmp_path: Path,
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n{TORN_FRAGMENT}", encoding="utf-8")
    SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path).apply(
        ROLLBACK, "key-rollback-2"
    )

    later = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)

    assert [entry.idempotency_key for entry in later.ledger()] == [
        "key-rollback-1",
        "key-rollback-2",
    ]
    assert later.torn_ledger_lines == [2]


def test_two_torn_crashes_in_a_row_replay_with_both_fragments_counted(tmp_path: Path) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n{TORN_FRAGMENT}", encoding="utf-8")
    SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path).apply(
        ROLLBACK, "key-rollback-2"
    )
    with ledger_path.open("a", encoding="utf-8") as ledger_file:
        ledger_file.write(TORN_FRAGMENT)
    SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path).apply(
        ROLLBACK, "key-rollback-3"
    )

    later = SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)

    assert len(ledger_path.read_text(encoding="utf-8").splitlines()) == 7
    assert [entry.idempotency_key for entry in later.ledger()] == [
        "key-rollback-1",
        "key-rollback-2",
        "key-rollback-3",
    ]
    assert later.torn_ledger_lines == [2, 5]


@pytest.mark.parametrize(
    ("content", "failing_line"),
    [
        (f"{SEAL_MARKER}\n{ROLLBACK_LINE}\n", "line 1"),
        (f"{ROLLBACK_LINE}\n{SEAL_MARKER}\n", "line 2"),
        (f"{ROLLBACK_LINE}\n{SEAL_MARKER}\n{TORN_FRAGMENT}", "line 2"),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER}\n{SEAL_MARKER}\n{ROLLBACK_LINE}\n", "line 3"),
        (f'{TORN_FRAGMENT}\n{{"torn_seal": true, "x": 1}}\n{ROLLBACK_LINE}\n', "line 1"),
        (f'{TORN_FRAGMENT}\n{{"torn_seal": false}}\n{ROLLBACK_LINE}\n', "line 1"),
        (f'{TORN_FRAGMENT}\n{{"torn_seal": 1}}\n{ROLLBACK_LINE}\n', "line 1"),
        (f"{TORN_FRAGMENT}\n{ROLLBACK_LINE}\n", "line 1"),
    ],
    ids=[
        "marker first",
        "marker after a valid line",
        "marker after a valid line then a fragment",
        "two markers in a row",
        "marker with an extra key",
        "marker with the value false",
        "marker with the value 1",
        "fragment followed by an entry, not a marker",
    ],
)
def test_a_seal_marker_is_legal_only_directly_after_a_fragment(
    tmp_path: Path, content: str, failing_line: str
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(content, encoding="utf-8")

    with pytest.raises(LedgerError, match=failing_line):
        SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)


@pytest.mark.parametrize("ending", ["\n", ""], ids=["newline after", "final line"])
@pytest.mark.parametrize(
    "second_line",
    [
        '{"idempotency_key": "k"}',
        json.dumps({"idempotency_key": "k", "action": "drop_table", "target": "x", "params": {}}),
    ],
    ids=["not an applied action", "unmodellable action"],
)
def test_a_complete_but_invalid_ledger_line_still_raises(
    tmp_path: Path, second_line: str, ending: str
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n{second_line}{ending}", encoding="utf-8")

    with pytest.raises(LedgerError, match="line 2"):
        SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)


@pytest.mark.parametrize("ending", ["\n", ""], ids=["newline after", "no newline after"])
def test_a_non_json_line_that_is_not_the_last_still_raises(tmp_path: Path, ending: str) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{TORN_FRAGMENT}\n{ROLLBACK_LINE}{ending}", encoding="utf-8")

    with pytest.raises(LedgerError, match="line 1"):
        SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)


def replayed(ledger_path: Path) -> SimulatorSource:
    return SimulatorSource(scenario_named(CLEAN_BAD_DEPLOY), ledger_path=ledger_path)


def keys_of(source: SimulatorSource) -> list[str]:
    return [entry.idempotency_key for entry in source.ledger()]


# A second crash inside the repair write itself must not brick the ledger: every shape the cut
# can take is a run of non-JSON lines that ends at a marker or at the end of the file.
@pytest.mark.parametrize(
    ("tail", "torn_lines"),
    [
        (f"{TORN_FRAGMENT}\n", [2]),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER[:-3]}", [2, 3]),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER[:1]}", [2, 3]),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER}\n{ROLLBACK_LINE[:20]}", [2, 4]),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER}\n{ROLLBACK_LINE[:20]}\n", [2, 4]),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER}", [2]),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER}\n", [2]),
    ],
    ids=[
        "fragment then bare newline",
        "fragment then torn marker",
        "fragment then one byte of marker",
        "torn entry after the marker",
        "torn entry after the marker with a newline",
        "marker without its newline",
        "marker with nothing after it",
    ],
)
def test_a_tear_inside_the_repair_write_still_replays_and_counts_every_cut_line(
    tmp_path: Path, tail: str, torn_lines: list[int]
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n{tail}", encoding="utf-8")

    source = replayed(ledger_path)

    assert keys_of(source) == ["key-rollback-1"]
    assert source.torn_ledger_lines == torn_lines


@pytest.mark.parametrize(
    "tail",
    [
        f"{TORN_FRAGMENT}\n{SEAL_MARKER[:-3]}",
        f"{TORN_FRAGMENT}\n{SEAL_MARKER}\n{ROLLBACK_LINE[:20]}",
        f"{TORN_FRAGMENT}\n",
        f"{TORN_FRAGMENT}\n{SEAL_MARKER}",
    ],
    ids=["torn marker", "torn entry after marker", "bare newline", "marker without newline"],
)
def test_an_append_after_a_tear_inside_the_repair_write_replays_in_a_later_process(
    tmp_path: Path, tail: str
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n{tail}", encoding="utf-8")
    replayed(ledger_path).apply(ROLLBACK, "key-rollback-2")

    later = replayed(ledger_path)

    assert keys_of(later) == ["key-rollback-1", "key-rollback-2"]


def test_repeated_crashes_with_a_different_cut_each_time_never_brick_the_ledger(
    tmp_path: Path,
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n{TORN_FRAGMENT}", encoding="utf-8")
    cuts = [SEAL_MARKER[:-3], ROLLBACK_LINE[:20], "", SEAL_MARKER[:2]]
    for number, cut in enumerate(cuts, start=2):
        replayed(ledger_path).apply(ROLLBACK, f"key-rollback-{number}")
        with ledger_path.open("a", encoding="utf-8") as ledger_file:
            ledger_file.write(cut)

    later = replayed(ledger_path)

    assert keys_of(later) == [f"key-rollback-{number}" for number in range(1, 6)]


def test_a_final_entry_that_is_complete_json_without_its_newline_gets_only_a_newline(
    tmp_path: Path,
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_bytes(ROLLBACK_LINE.encode())
    first = replayed(ledger_path)
    assert keys_of(first) == ["key-rollback-1"]

    first.apply(ROLLBACK, "key-rollback-2")

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert SEAL_MARKER not in lines
    later = replayed(ledger_path)
    assert keys_of(later) == ["key-rollback-1", "key-rollback-2"]
    assert later.torn_ledger_lines == []


def test_an_append_after_a_marker_that_is_the_last_line_adds_no_second_marker(
    tmp_path: Path,
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n{TORN_FRAGMENT}\n{SEAL_MARKER}\n", encoding="utf-8")

    replayed(ledger_path).apply(ROLLBACK, "key-rollback-2")

    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    assert lines.count(SEAL_MARKER) == 1
    assert keys_of(replayed(ledger_path)) == ["key-rollback-1", "key-rollback-2"]


def test_an_append_to_a_ledger_with_a_complete_last_line_adds_no_marker(tmp_path: Path) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(f"{ROLLBACK_LINE}\n", encoding="utf-8")

    replayed(ledger_path).apply(ROLLBACK, "key-rollback-2")

    assert SEAL_MARKER not in ledger_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("content", "failing_line"),
    [
        (f"{TORN_FRAGMENT}\n{ROLLBACK_LINE}\n", "line 1"),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER[:-3]}\n{ROLLBACK_LINE}\n", "line 1"),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER}\n{ROLLBACK_LINE[:20]}\n{ROLLBACK_LINE}\n", "line 3"),
        (f"{ROLLBACK_LINE}\n{SEAL_MARKER}\n", "line 2"),
        (f"{TORN_FRAGMENT}\n{SEAL_MARKER}\n{SEAL_MARKER}\n", "line 3"),
        (
            f"{TORN_FRAGMENT}\n{SEAL_MARKER}\n{ROLLBACK_LINE[:20]}\n{SEAL_MARKER}\n{SEAL_MARKER}",
            "line 5",
        ),
    ],
    ids=[
        "fragment then an entry",
        "torn marker then an entry",
        "torn entry after a marker then an entry",
        "marker after an entry",
        "marker after a marker",
        "second marker after a sealed torn entry",
    ],
)
def test_a_run_of_torn_lines_followed_by_an_entry_or_a_stray_marker_still_raises(
    tmp_path: Path, content: str, failing_line: str
) -> None:
    ledger_path = ledger_path_for(tmp_path, "inc-001")
    ledger_path.write_text(content, encoding="utf-8")

    with pytest.raises(LedgerError, match=failing_line):
        replayed(ledger_path)
