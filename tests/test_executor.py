"""E9, E12, E17, E25, E39, E40, E53, E54, E55: the executor and the execute node.

The executor applies an approved remediation exactly once, through seven steps in a fixed order,
and writes the audit trail as it goes. These tests drive it through a recording source, so the
order of every read, check and write is visible, and read the audit file back line by line.
"""

import hashlib
import inspect
import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agentops.actions import executor
from agentops.actions.audit import AuditLog
from agentops.actions.registry import Registry, default_registry, idempotency_key
from agentops.nodes.execute import after_execute
from agentops.nodes.execute import execute as execute_node
from agentops.nodes.router import route_to_sources
from agentops.sources.base import (
    ActionRequest,
    ActionResult,
    AppliedAction,
    ConfigValue,
    DependencyStatus,
    Deployment,
    LogLine,
    Series,
    Window,
)
from agentops.sources.scenario import Scenario, load_scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import (
    Approval,
    ApprovalChoice,
    ExecutionRecord,
    Hypothesis,
    IncidentState,
    Remediation,
    new_incident,
)

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"


class RecordingSource:
    """Wraps a real source and writes every call's name into a shared `calls` list."""

    def __init__(
        self, inner: SimulatorSource, calls: list[str], apply_error: Exception | None = None
    ) -> None:
        self.inner = inner
        self.calls = calls
        self.apply_error = apply_error

    def get_logs(self, service: str, window: Window, query: str | None) -> list[LogLine]:
        self.calls.append("get_logs")
        return self.inner.get_logs(service, window, query)

    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        self.calls.append("get_metrics")
        return self.inner.get_metrics(service, names, window)

    def get_deployments(self, service: str, window: Window) -> list[Deployment]:
        self.calls.append("get_deployments")
        return self.inner.get_deployments(service, window)

    def get_config(self, service: str) -> dict[str, ConfigValue]:
        self.calls.append("get_config")
        return self.inner.get_config(service)

    def get_dependency_health(self, service: str) -> list[DependencyStatus]:
        self.calls.append("get_dependency_health")
        return self.inner.get_dependency_health(service)

    def apply(self, action: ActionRequest, idempotency_key: str) -> ActionResult:
        self.calls.append("apply")
        if self.apply_error is not None:
            raise self.apply_error
        return self.inner.apply(action, idempotency_key)

    def ledger(self) -> list[AppliedAction]:
        self.calls.append("ledger")
        return self.inner.ledger()


class RecordingRegistry(Registry):
    """The default registry, writing `registry` on lookup and `params` on validation."""

    def __init__(self, calls: list[str]) -> None:
        self._entries = {name: default_registry().get(name) for name in default_registry().names()}
        self.calls = calls
        self.validating = False

    def get(self, name: str) -> Any:
        # validate_params looks the entry up itself; only the executor's own lookup counts.
        if not self.validating:
            self.calls.append("registry")
        return super().get(name)

    def validate_params(self, name: str, params: Any) -> None:
        self.calls.append("params")
        self.validating = True
        try:
            super().validate_params(name, params)
        finally:
            self.validating = False


class RecordingAuditLog(AuditLog):
    def __init__(self, directory: Path, calls: list[str]) -> None:
        super().__init__(directory)
        self.calls = calls

    def append(self, incident_id: str, thread_id: str, record: Any) -> None:
        super().append(incident_id, thread_id, record)
        self.calls.append(f"audit:{record['event']}")


class ProcessExit(Exception):
    """Stands in for the process exit the crash switch performs."""


def scenario_named(scenario_id: str) -> Scenario:
    return load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")


def scripted_remediation(scenario: Scenario) -> Remediation:
    return Remediation.model_validate(scenario.scripted_outputs.rca["remediation"])


def approved_state(
    scenario: Scenario,
    remediation: Remediation | None = None,
    decision: ApprovalChoice = "approve",
) -> IncidentState:
    """The scenario's incident with a proposal and an operator's approval of exactly it."""
    proposal = remediation or scripted_remediation(scenario)
    state = new_incident(
        scenario.service,
        scenario.symptoms,
        opened_at=scenario.incident_time,
        incident_id=scenario.id,
        scenario_id=scenario.id,
    )
    state["hypotheses"] = [Hypothesis(name="cause", score=0.9)]
    state["remediation"] = proposal
    state["status"] = "remediating"
    state["approval"] = Approval(
        decision=decision,
        operator="on-call",
        decided_at=datetime.now(UTC),
        idempotency_key=idempotency_key(scenario.id, proposal),
    )
    return state


def with_remediation(state: IncidentState, **changes: Any) -> IncidentState:
    tampered = dict(state)
    tampered["remediation"] = state["remediation"].model_copy(update=changes)  # type: ignore[union-attr]
    return tampered  # type: ignore[return-value]


def audit_records(directory: Path, incident_id: str) -> list[dict[str, Any]]:
    path = directory / f"{incident_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def independent_key(incident_id: str, action: str, target: str, params: dict[str, Any]) -> str:
    canonical = json.dumps(
        {"action": action, "target": target, "params": params},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256((incident_id + canonical).encode("utf-8")).hexdigest()


def merged(state: IncidentState, update: dict[str, Any]) -> IncidentState:
    """The state after a node update, appending the reducer fields as the graph would."""
    result = dict(state)
    for key, value in update.items():
        result[key] = [*state[key], *value] if key in ("evidence", "actions") else value
    return result  # type: ignore[return-value]


def thread_config(state: IncidentState, **extra: Any) -> RunnableConfig:
    return {"configurable": {"thread_id": state["incident_id"], **extra}}


def run_executor(
    state: IncidentState, source: Any, audit_dir: Path, registry: Registry | None = None
) -> executor.ExecutionOutcome:
    return executor.execute(
        state,
        source,
        registry or default_registry(),
        AuditLog(audit_dir),
        thread_id=state["incident_id"],
    )


# E9: the seven steps run in order and the key is the canonical sha256.
def test_executor_runs_the_seven_steps_in_order_and_applies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    calls: list[str] = []
    source = RecordingSource(SimulatorSource(scenario), calls)
    real_key = executor.idempotency_key

    def recording_key(incident_id: str, remediation: Remediation) -> str:
        calls.append("key")
        return real_key(incident_id, remediation)

    monkeypatch.setattr(executor, "idempotency_key", recording_key)
    state = approved_state(scenario)

    outcome = executor.execute(
        state,
        source,
        RecordingRegistry(calls),
        RecordingAuditLog(tmp_path, calls),
        thread_id=scenario.id,
    )

    assert calls == [
        "registry",
        "params",
        "get_metrics",
        "audit:precondition",
        "key",
        "ledger",
        "audit:dry_run",
        "apply",
        "audit:apply",
    ]
    assert outcome.kind == "applied"
    assert outcome.execution is not None
    assert outcome.execution.applied is True
    assert outcome.execution.applied_at is not None
    assert outcome.execution.idempotency_key == independent_key(
        scenario.id, "scale_db_pool", "postgres", {"pool_size": 96}
    )
    assert [entry.idempotency_key for entry in source.inner.ledger()] == [
        outcome.execution.idempotency_key
    ]


def test_applied_outcome_writes_action_evidence_and_an_apply_audit_record(
    tmp_path: Path,
) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    state = approved_state(scenario)

    outcome = run_executor(state, SimulatorSource(scenario), tmp_path)

    [evidence] = outcome.evidence
    assert evidence.kind == "action"
    assert evidence.payload["action"] == "scale_db_pool"
    assert evidence.payload["applied"] is True
    apply_records = [r for r in audit_records(tmp_path, scenario.id) if r["event"] == "apply"]
    assert len(apply_records) == 1
    assert apply_records[0]["applied"] is True
    assert apply_records[0]["ok"] is True
    assert apply_records[0]["thread_id"] == scenario.id
    assert apply_records[0]["idempotency_key"] == outcome.execution.idempotency_key  # type: ignore[union-attr]


def test_executor_body_is_short_and_names_the_seven_steps_in_order() -> None:
    body = inspect.getsource(executor.execute)
    steps = [
        "look_up_action(",
        "validate_parameters(",
        "check_preconditions(",
        "compute_key(",
        "find_recorded_execution(",
        "record_dry_run(",
        "apply_and_audit(",
    ]

    positions = [body.index(step) for step in steps]

    assert len(body.splitlines()) < 40
    assert positions == sorted(positions)


# E12: nothing executes without an approval record.
def test_executor_refuses_a_state_without_an_approval(tmp_path: Path) -> None:
    scenario = scenario_named("clean_bad_deploy")
    calls: list[str] = []
    source = RecordingSource(SimulatorSource(scenario), calls)
    state = approved_state(scenario)
    state["approval"] = None

    outcome = run_executor(state, source, tmp_path)

    assert outcome.kind == "refused"
    assert outcome.execution is None
    assert "apply" not in calls
    assert source.inner.ledger() == []
    assert audit_records(tmp_path, scenario.id) == []


# E54: a reject, or an edit carrying no edited remediation, is refused.
@pytest.mark.parametrize("decision", ["reject", "edit"])
def test_executor_refuses_a_reject_or_an_edit_without_a_remediation(
    tmp_path: Path, decision: ApprovalChoice
) -> None:
    scenario = scenario_named("clean_bad_deploy")
    calls: list[str] = []
    source = RecordingSource(SimulatorSource(scenario), calls)
    state = approved_state(scenario, decision=decision)

    outcome = run_executor(state, source, tmp_path)

    assert outcome.kind == "refused"
    assert "apply" not in calls
    assert source.inner.ledger() == []
    assert not [r for r in audit_records(tmp_path, scenario.id) if r["event"] == "apply"]


def test_executor_applies_the_edited_remediation_of_an_edit(tmp_path: Path) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    edited = scripted_remediation(scenario).model_copy(update={"params": {"pool_size": 112}})
    state = approved_state(scenario, remediation=edited, decision="edit")
    state["approval"] = state["approval"].model_copy(update={"edited_remediation": edited})  # type: ignore[union-attr]
    source = SimulatorSource(scenario)

    outcome = run_executor(state, source, tmp_path)

    assert outcome.kind == "applied"
    assert [entry.params for entry in source.ledger()] == [{"pool_size": 112}]


# E55: a remediation changed after the approval is refused, never applied.
@pytest.mark.parametrize(
    ("changes", "named"),
    [
        ({"action": "drop_table", "target": "search_index_old", "params": {}}, "drop_table"),
        ({"params": {"pool_size": 10000}}, "pool_size"),
        ({"params": {"pool_size": 112}}, "approved"),
    ],
    ids=["unregistered action", "out of range parameter", "different key"],
)
def test_executor_refuses_a_remediation_tampered_after_approval(
    tmp_path: Path, changes: dict[str, Any], named: str
) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    calls: list[str] = []
    source = RecordingSource(SimulatorSource(scenario), calls)
    state = with_remediation(approved_state(scenario), **changes)

    outcome = run_executor(state, source, tmp_path)

    assert outcome.kind == "refused"
    assert named in outcome.detail
    assert "apply" not in calls
    assert source.inner.ledger() == []
    assert not [r for r in audit_records(tmp_path, scenario.id) if r["event"] == "apply"]


def test_a_raising_adapter_leaves_an_apply_record_naming_the_error(tmp_path: Path) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    calls: list[str] = []
    source = RecordingSource(SimulatorSource(scenario), calls, apply_error=RuntimeError("down"))
    state = approved_state(scenario)

    with pytest.raises(RuntimeError):
        run_executor(state, source, tmp_path)

    [record] = [r for r in audit_records(tmp_path, scenario.id) if r["event"] == "apply"]
    assert record["ok"] is False
    assert record["applied"] is False
    assert "RuntimeError" in record["detail"]


# E53: the key ignores parameter order and changes with the incident.
def test_idempotency_key_is_canonical_and_per_incident() -> None:
    first = Remediation(
        action="rollback_deploy",
        target="orders",
        params={"to_version": "v7.7.3", "pool_size": 8},
        reason="One order.",
        evidence_ids=["log-001"],
        risk="high",
        rollback="Redeploy.",
    )
    reordered = first.model_copy(update={"params": {"pool_size": 8, "to_version": "v7.7.3"}})
    expected = independent_key("incident-a", "rollback_deploy", "orders", first.params)

    assert idempotency_key("incident-a", first) == expected
    assert idempotency_key("incident-a", reordered) == expected
    assert idempotency_key("incident-b", first) == independent_key(
        "incident-b", "rollback_deploy", "orders", first.params
    )
    assert idempotency_key("incident-b", first) != expected


# E25: a key already recorded in state or in the source's ledger applies nothing.
def test_a_key_already_in_state_returns_already_applied(tmp_path: Path) -> None:
    scenario = scenario_named("clean_bad_deploy")
    calls: list[str] = []
    source = RecordingSource(SimulatorSource(scenario), calls)
    state = approved_state(scenario)
    state["execution"] = ExecutionRecord(
        idempotency_key=state["approval"].idempotency_key,  # type: ignore[union-attr]
        action="rollback_deploy",
        target="deploy-001",
        applied=True,
        applied_at=datetime.now(UTC),
        result="applied",
        dry_run_output="Would apply rollback_deploy.",
    )

    outcome = run_executor(state, source, tmp_path)

    assert outcome.kind == "already_applied"
    assert "apply" not in calls
    [record] = [r for r in audit_records(tmp_path, scenario.id) if r["event"] == "apply"]
    assert record["applied"] is False
    assert record["detail"] == "already_applied"


def test_a_key_already_in_the_source_ledger_returns_already_applied(tmp_path: Path) -> None:
    scenario = scenario_named("clean_bad_deploy")
    state = approved_state(scenario)
    inner = SimulatorSource(scenario)
    remediation = scripted_remediation(scenario)
    inner.apply(
        ActionRequest(action=remediation.action, target=remediation.target),
        state["approval"].idempotency_key,  # type: ignore[union-attr]
    )
    calls: list[str] = []

    outcome = run_executor(state, RecordingSource(inner, calls), tmp_path)

    assert outcome.kind == "already_applied"
    assert outcome.execution is not None
    assert outcome.execution.applied is False
    assert "apply" not in calls
    assert len(inner.ledger()) == 1
    [record] = [r for r in audit_records(tmp_path, scenario.id) if r["event"] == "apply"]
    assert record["applied"] is False
    assert record["detail"] == "already_applied"


# E39: a source that refuses the action gives apply_failed and escalates.
def test_a_source_refusal_is_apply_failed_and_escalates(tmp_path: Path) -> None:
    scenario = scenario_named("clean_bad_deploy").model_copy(deep=True)
    # Scenario 1 has no batch job flag; set it so the precondition passes and apply is reached.
    scenario.signals.config["batch_jobs_enabled"] = True
    source = SimulatorSource(scenario)
    pause = Remediation(
        action="pause_batch_job",
        target="reindex-job-nightly",
        reason="Shaped for a refusal test.",
        evidence_ids=["log-001"],
        risk="low",
        rollback="Resume it.",
    )
    state = approved_state(scenario, remediation=pause)

    update = execute_node(
        state,
        thread_config(state),
        source=source,
        registry=default_registry(),
        audit=AuditLog(tmp_path),
    )
    after = merged(state, update)

    assert after["execution"] is not None
    assert after["execution"].result == "apply_failed"
    assert after["execution"].applied is False
    assert after["execution"].applied_at is None
    [evidence] = update["evidence"]
    assert evidence.kind == "action"
    assert evidence.payload["applied"] is False
    assert after["escalation_reason"] == "apply_failed"
    assert after_execute(after) == "escalate"
    assert source.ledger() == []


# E40: an audit directory that cannot be written stops the executor before apply.
def test_an_unwritable_audit_directory_raises_before_apply(tmp_path: Path) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    calls: list[str] = []
    source = RecordingSource(SimulatorSource(scenario), calls)
    not_a_directory = tmp_path / "audit"
    not_a_directory.write_text("a file, not a directory", encoding="utf-8")

    with pytest.raises(OSError):
        run_executor(approved_state(scenario), source, not_a_directory)

    assert "apply" not in calls
    assert source.inner.ledger() == []


# E17: a failed precondition goes back to the router once, then escalates.
def test_a_failed_precondition_routes_to_the_router_then_escalates(tmp_path: Path) -> None:
    scenario = scenario_named("deploy_red_herring_db").model_copy(deep=True)
    for series in scenario.signals.metrics:
        if series.name == "db_pool_utilisation":
            for point in series.points:
                point.value = 0.4
    source = SimulatorSource(scenario)
    state = approved_state(scenario)
    approval = state["approval"]
    audit = AuditLog(tmp_path)

    first = merged(
        state,
        execute_node(
            state, thread_config(state), source=source, registry=default_registry(), audit=audit
        ),
    )
    [failure] = [item for item in first["evidence"] if item.kind == "precondition"]
    retry = dict(first)
    retry.update(remediation=scripted_remediation(scenario), approval=approval)
    second = merged(
        retry,  # type: ignore[arg-type]
        execute_node(
            retry,  # type: ignore[arg-type]
            thread_config(state),
            source=source,
            registry=default_registry(),
            audit=audit,
        ),
    )

    assert failure.payload["check"] == "metric_above"
    assert failure.payload["passed"] is False
    assert first["escalation_reason"] is None
    assert first["approval"] is None
    assert first["remediation"] is None
    assert first["loops"] == state["loops"] + 1
    assert after_execute(first) == route_to_sources(first)
    assert after_execute(first) not in ("escalate", "verify")
    assert second["escalation_reason"] == "precondition_failed"
    assert after_execute(second) == "escalate"
    assert source.ledger() == []
    precondition_records = [
        r for r in audit_records(tmp_path, scenario.id) if r["event"] == "precondition"
    ]
    assert [r["passed"] for r in precondition_records] == [False, False]


def test_an_applied_execution_routes_to_verify(tmp_path: Path) -> None:
    scenario = scenario_named("clean_bad_deploy")
    state = approved_state(scenario)

    update = execute_node(
        state,
        thread_config(state),
        source=SimulatorSource(scenario),
        registry=default_registry(),
        audit=AuditLog(tmp_path),
    )
    after = merged(state, update)

    assert after["status"] == "verifying"
    assert after["execution"] is not None
    assert after["execution"].result == "applied"
    assert after_execute(after) == "verify"
    assert [action.tool for action in update["actions"]] == ["apply"]


def test_the_execute_node_refuses_without_a_thread_id(tmp_path: Path) -> None:
    scenario = scenario_named("clean_bad_deploy")
    state = approved_state(scenario)

    with pytest.raises(ValueError, match="thread_id"):
        execute_node(
            state,
            {"configurable": {}},
            source=SimulatorSource(scenario),
            registry=default_registry(),
            audit=AuditLog(tmp_path),
        )


# ADR 0002: the crash switch exits only after the apply record, and only from `configurable`.
def test_crash_after_apply_exits_after_the_apply_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_exit(code: int) -> None:
        raise ProcessExit(code)

    monkeypatch.setattr(executor, "exit_process", fake_exit)
    scenario = scenario_named("clean_bad_deploy")
    state = approved_state(scenario)
    source = SimulatorSource(scenario)

    with pytest.raises(ProcessExit):
        execute_node(
            state,
            thread_config(state, crash_after_apply=True),
            source=source,
            registry=default_registry(),
            audit=AuditLog(tmp_path),
        )

    assert len(source.ledger()) == 1
    assert audit_records(tmp_path, scenario.id)[-1]["event"] == "apply"


def test_the_crash_switch_needs_exactly_true_in_configurable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_exit(code: int) -> None:
        raise ProcessExit(code)

    monkeypatch.setattr(executor, "exit_process", fake_exit)
    scenario = scenario_named("clean_bad_deploy")
    state = approved_state(scenario)

    update = execute_node(
        state,
        thread_config(state, crash_after_apply="yes"),
        source=SimulatorSource(scenario),
        registry=default_registry(),
        audit=AuditLog(tmp_path),
    )

    assert update["execution"].result == "applied"


def test_the_execute_node_reads_the_thread_id_inside_a_compiled_graph(tmp_path: Path) -> None:
    scenario = scenario_named("clean_bad_deploy")
    state = approved_state(scenario)
    source = SimulatorSource(scenario)
    graph = StateGraph(IncidentState)
    graph.add_node(
        "execute",
        partial(execute_node, source=source, registry=default_registry(), audit=AuditLog(tmp_path)),
    )
    graph.add_edge(START, "execute")
    graph.add_edge("execute", END)
    compiled = graph.compile(checkpointer=InMemorySaver())

    final = compiled.invoke(state, {"configurable": {"thread_id": "thread-one"}})

    assert final["execution"].result == "applied"
    assert {r["thread_id"] for r in audit_records(tmp_path, scenario.id)} == {"thread-one"}
