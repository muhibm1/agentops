"""E28, E29, E30, F6, A11: the ReAct baseline on `create_agent` (R8).

The baseline runs on the scripted tool-calling model over a `SimulatorSource`. It has no
approval, registry gate or audit log by design, so these tests read what reached the source and
what the trace recorded, and that it refuses any source that is not the simulator (D19).
"""

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from agentops.baseline import (
    BaselineAnswer,
    BaselineTrace,
    baseline_tools,
    run_baseline,
)
from agentops.evals.evaluators import correct_outcome
from agentops.evals.outcome import (
    UNDETERMINED_LABEL,
    ApplyAttempt,
    inputs_for,
    reference_for,
    remediation_label,
)
from agentops.evals.run import outcome_from_baseline
from agentops.fakes import ScriptedChatModel, scripted_baseline_model_for
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
from agentops.sources.scenario import BASELINE_TOOL_NAMES, Scenario, load_scenario
from agentops.sources.simulator import SimulatorSource

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
INCIDENT_ID = "inc-baseline"


def scenario_named(scenario_id: str) -> Scenario:
    return load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")


class ApplyRecordingSimulator(SimulatorSource):
    """The simulator, plus a list of every action handed to `apply`."""

    def __init__(self, scenario: Scenario) -> None:
        super().__init__(scenario)
        self.apply_requests: list[ActionRequest] = []

    def apply(self, action: ActionRequest, idempotency_key: str) -> ActionResult:
        self.apply_requests.append(action)
        return super().apply(action, idempotency_key)


class MetricsDownSimulator(SimulatorSource):
    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        raise RuntimeError("the metrics backend is down")


class RecordingSource:
    """A complete `EvidenceSource` that is not the simulator and records every call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_logs(self, service: str, window: Window, query: str | None) -> list[LogLine]:
        self.calls.append("get_logs")
        return []

    def get_metrics(self, service: str, names: list[str], window: Window) -> list[Series]:
        self.calls.append("get_metrics")
        return []

    def get_deployments(self, service: str, window: Window) -> list[Deployment]:
        self.calls.append("get_deployments")
        return []

    def get_config(self, service: str) -> dict[str, ConfigValue]:
        self.calls.append("get_config")
        return {}

    def get_dependency_health(self, service: str) -> list[DependencyStatus]:
        self.calls.append("get_dependency_health")
        return []

    def apply(self, action: ActionRequest, idempotency_key: str) -> ActionResult:
        self.calls.append("apply")
        return ActionResult(ok=True, applied=True, idempotency_key=idempotency_key, detail="")

    def ledger(self) -> list[AppliedAction]:
        self.calls.append("ledger")
        return []


def one_tool_call(name: str, args: dict[str, Any], call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def test_the_baseline_runs_the_scripted_tools_and_applies_through_the_simulator() -> None:
    scenario = scenario_named("clean_bad_deploy")
    script = scenario.scripted_outputs.baseline
    assert script is not None
    model = scripted_baseline_model_for(scenario)
    source = SimulatorSource(scenario)

    run = run_baseline(scenario, model, incident_id=INCIDENT_ID, source=source)

    assert [call.tool for call in run.tool_calls] == [step.tool for step in script.steps]
    assert all(call.ok for call in run.tool_calls)
    assert sorted(model.bound_tools) == sorted(BASELINE_TOOL_NAMES)
    assert [(entry.action, entry.target) for entry in source.ledger()] == [
        ("rollback_deploy", "deploy-001")
    ]
    assert run.answer == BaselineAnswer.model_validate(script.answer)
    assert run.root_cause_label == "recent_deploy_regression"
    assert run.outcome == "remediated"
    assert set(scenario.required_evidence) <= set(run.evidence_ids_before_apply)


def test_the_baseline_applies_without_approval_or_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    scenario = scenario_named("log_injection_drop_table")
    source = ApplyRecordingSimulator(scenario)

    run = run_baseline(
        scenario, scripted_baseline_model_for(scenario), incident_id=INCIDENT_ID, source=source
    )

    assert [request.action for request in source.apply_requests] == ["drop_table"]
    assert run.applies == [
        ApplyAttempt(
            action="drop_table",
            target="search_index_old",
            approved=False,
            in_registry=False,
            ok=False,
        )
    ]
    assert source.ledger() == []
    assert list(tmp_path.iterdir()) == []


def test_a_baseline_whose_only_apply_was_refused_did_not_remediate() -> None:
    scenario = scenario_named("log_injection_drop_table")

    run = run_baseline(scenario, scripted_baseline_model_for(scenario), incident_id=INCIDENT_ID)

    assert run.answer is not None
    assert run.outcome == "escalated"


def test_an_unparseable_baseline_answer_is_a_failed_run() -> None:
    scenario = scenario_named("clean_bad_deploy")
    model = ScriptedChatModel(
        messages=[one_tool_call("get_config", {}, "call-1")],
        script=["The checkout service looks broken, probably the deploy."],
    )

    run = run_baseline(scenario, model, incident_id=INCIDENT_ID)

    assert run.outcome == "failed"
    assert run.root_cause_label == UNDETERMINED_LABEL
    assert run.answer is None
    assert run.failure is not None


def test_a_raising_tool_does_not_end_the_baseline_run() -> None:
    scenario = scenario_named("clean_bad_deploy")
    model = scripted_baseline_model_for(scenario)

    run = run_baseline(
        scenario, model, incident_id=INCIDENT_ID, source=MetricsDownSimulator(scenario)
    )

    metrics_calls = [call for call in run.tool_calls if call.tool == "get_metrics"]
    tool_messages = [
        message
        for message in model.calls[-1]
        if isinstance(message, ToolMessage) and message.tool_call_id == "call-3"
    ]
    assert [call.ok for call in metrics_calls] == [False]
    assert len(tool_messages) == 1
    assert "RuntimeError" in str(tool_messages[0].content)
    assert run.answer is not None
    assert run.outcome == "remediated"


def test_the_baseline_refuses_any_source_that_is_not_the_simulator() -> None:
    scenario = scenario_named("clean_bad_deploy")
    model = scripted_baseline_model_for(scenario)
    source = RecordingSource()

    with pytest.raises(TypeError, match="SimulatorSource"):
        baseline_tools(source, scenario.service, INCIDENT_ID, BaselineTrace())
    with pytest.raises(TypeError, match="SimulatorSource"):
        run_baseline(scenario, model, incident_id=INCIDENT_ID, source=source)

    assert source.calls == []
    assert model.calls == []


# E31b: the baseline is labelled by the same rule as the graph, so a `loop` scenario is not
# unwinnable for it: two accepted applies are `looped_then_remediated`.
ROLLBACK_CALL = {
    "action": "rollback_deploy",
    "target": "deploy-001",
    "params": {"to_version": "v7.7.3"},
}
SCALE_CALL = {"action": "scale_db_pool", "target": "postgres", "params": {"pool_size": 96}}
FAULT_ANSWER = (
    '{"root_cause": "recent_deploy_regression", "action": "rollback_deploy", '
    '"target": "deploy-001", "params": {}}'
)


def baseline_applying(scenario: Scenario, *calls: dict[str, Any]) -> Any:
    model = ScriptedChatModel(
        messages=[one_tool_call("apply", call, f"call-{n}") for n, call in enumerate(calls, 1)],
        script=[FAULT_ANSWER],
    )
    return run_baseline(scenario, model, incident_id=INCIDENT_ID)


def test_a_baseline_with_two_accepted_applies_is_looped_then_remediated() -> None:
    scenario = scenario_named("two_concurrent_faults")

    run = baseline_applying(scenario, ROLLBACK_CALL, SCALE_CALL)

    assert [attempt.ok for attempt in run.applies] == [True, True]
    assert run.outcome == "looped_then_remediated"
    outcome = outcome_from_baseline(run, started=0.0, finished=0.0)
    assert correct_outcome(inputs_for(scenario), outcome.model_dump(), reference_for(scenario))


def test_a_baseline_with_one_accepted_apply_is_remediated_and_misses_a_loop_scenario() -> None:
    scenario = scenario_named("two_concurrent_faults")

    run = baseline_applying(scenario, ROLLBACK_CALL)

    assert run.outcome == "remediated"
    outcome = outcome_from_baseline(run, started=0.0, finished=0.0)
    assert not correct_outcome(inputs_for(scenario), outcome.model_dump(), reference_for(scenario))


def test_a_baseline_whose_second_apply_was_refused_is_remediated_not_looped() -> None:
    scenario = scenario_named("two_concurrent_faults")
    unmodellable = {"action": "drop_table", "target": "orders", "params": {}}

    run = baseline_applying(scenario, ROLLBACK_CALL, unmodellable)

    assert [attempt.ok for attempt in run.applies] == [True, False]
    assert run.outcome == "remediated"


@pytest.mark.parametrize(
    ("accepted_flags", "label"),
    [
        ([], "remediated"),
        ([True], "remediated"),
        ([False, True], "remediated"),
        ([True, True], "looped_then_remediated"),
        ([True, False, True], "looped_then_remediated"),
    ],
)
def test_the_remediation_label_counts_accepted_applies(
    accepted_flags: list[bool], label: str
) -> None:
    attempts = [
        ApplyAttempt(action="a", target="t", approved=True, in_registry=True, ok=ok)
        for ok in accepted_flags
    ]

    assert remediation_label(attempts) == label
