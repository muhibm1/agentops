"""E1, E2, E3 and E35: the findings increment 2 deferred. A dead source is reported as an outage,
not as missing evidence, and a run never records more than twelve source read attempts
(R1, R2, R3; ADR 0004)."""

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.fakes import ScriptedChatModel
from agentops.graph import build_graph, checkpoint_serializer
from agentops.nodes.evidence import SOURCE_READ_TOOLS
from agentops.sources.scenario import Scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import (
    INITIAL_INVESTIGATION_BUDGET,
    Action,
    Escalation,
    RcaOutput,
    Triage,
    new_incident,
)

CLEAN_BAD_DEPLOY = "clean_bad_deploy"
RED_HERRING = "deploy_red_herring_db"
ALL_SOURCE_READ_TOOLS = {tool for tools in SOURCE_READ_TOOLS.values() for tool in tools}
HAND_OFF = {"summary": "Every evidence source failed; a human has to look."}
SOURCE_METHODS = {
    "logs": ("get_logs",),
    "metrics": ("get_metrics", "get_dependency_health"),
    "deploys": ("get_deployments",),
}


class DeadSourcesSimulator(SimulatorSource):
    """A simulator whose reads for the named sources raise on every call."""

    def __init__(self, scenario: Scenario, dead: set[str]) -> None:
        super().__init__(scenario)
        self.dead_methods = {method for name in dead for method in SOURCE_METHODS[name]}

    def get_logs(self, *args: Any, **kwargs: Any) -> Any:
        self.fail_if_dead("get_logs")
        return super().get_logs(*args, **kwargs)

    def get_metrics(self, *args: Any, **kwargs: Any) -> Any:
        self.fail_if_dead("get_metrics")
        return super().get_metrics(*args, **kwargs)

    def get_dependency_health(self, *args: Any, **kwargs: Any) -> Any:
        self.fail_if_dead("get_dependency_health")
        return super().get_dependency_health(*args, **kwargs)

    def get_deployments(self, *args: Any, **kwargs: Any) -> Any:
        self.fail_if_dead("get_deployments")
        return super().get_deployments(*args, **kwargs)

    def fail_if_dead(self, method: str) -> None:
        if method in self.dead_methods:
            raise ConnectionError(f"{method} is unreachable")


class FirstAttemptFailsSimulator(SimulatorSource):
    """A simulator whose every read fails on its first try and answers on the retry."""

    def __init__(self, scenario: Scenario) -> None:
        super().__init__(scenario)
        self.calls = 0

    def get_logs(self, *args: Any, **kwargs: Any) -> Any:
        self.fail_every_other_call()
        return super().get_logs(*args, **kwargs)

    def get_metrics(self, *args: Any, **kwargs: Any) -> Any:
        self.fail_every_other_call()
        return super().get_metrics(*args, **kwargs)

    def get_dependency_health(self, *args: Any, **kwargs: Any) -> Any:
        self.fail_every_other_call()
        return super().get_dependency_health(*args, **kwargs)

    def get_deployments(self, *args: Any, **kwargs: Any) -> Any:
        self.fail_every_other_call()
        return super().get_deployments(*args, **kwargs)

    def fail_every_other_call(self) -> None:
        self.calls += 1
        if self.calls % 2 == 1:
            raise TimeoutError("the first try always times out")


def run(scenario: Scenario, source: SimulatorSource, model: ScriptedChatModel) -> Any:
    """Run the full graph until it ends or pauses at its first approval interrupt (brief D20)."""
    state = new_incident(scenario.service, scenario.symptoms, opened_at=scenario.incident_time)
    with tempfile.TemporaryDirectory() as audit_dir:
        graph = build_graph(
            source,
            model,
            default_registry(),
            AuditLog(Path(audit_dir)),
            InMemorySaver(serde=checkpoint_serializer()),
        )
        return graph.invoke(state, {"configurable": {"thread_id": "deferred-findings"}})


def source_reads(final: Any) -> list[Action]:
    return [action for action in final["actions"] if action.tool in ALL_SOURCE_READ_TOOLS]


def prompts_of(model: ScriptedChatModel) -> list[str]:
    return ["\n".join(str(message.content) for message in call) for call in model.calls]


def triage_and_hand_off_model(scenario: Scenario) -> ScriptedChatModel:
    return ScriptedChatModel(
        script_by_schema={
            Triage: [scenario.scripted_outputs.triage] * 3,
            Escalation: [HAND_OFF] * 3,
        }
    )


def rca_without_deploy_citations(scenario: Scenario, deploy_score: float) -> dict[str, Any]:
    """The scenario's scripted rca with every deploy citation removed and the deploy hypothesis
    re-scored to `deploy_score`."""
    rca = json.loads(json.dumps(scenario.scripted_outputs.rca))
    for hypothesis in rca["hypotheses"]:
        if hypothesis["name"] == "recent_deploy_regression":
            hypothesis["score"] = deploy_score
        for field in ("supporting_evidence", "contradicting_evidence"):
            hypothesis[field] = without_deploy_ids(hypothesis.get(field, []))
    for cited_by in (rca["root_cause"], rca["remediation"]):
        cited_by["evidence_ids"] = without_deploy_ids(cited_by["evidence_ids"])
    return rca


def without_deploy_ids(evidence_ids: list[str]) -> list[str]:
    return [evidence_id for evidence_id in evidence_ids if not evidence_id.startswith("deploy-")]


@pytest.mark.parametrize("scenario", [CLEAN_BAD_DEPLOY], indirect=True)
def test_every_source_dead_escalates_as_a_source_outage(scenario: Scenario) -> None:
    dead = DeadSourcesSimulator(scenario, {"logs", "metrics", "deploys"})

    final = run(scenario, dead, triage_and_hand_off_model(scenario))

    assert final["status"] == "escalated"
    assert final["escalation_reason"] == "source_outage"
    assert final["evidence"] == []
    assert source_reads(final)
    assert all(not action.ok for action in source_reads(final))
    assert final["unreachable_sources"] == ["logs", "metrics", "deploys"]


@pytest.mark.parametrize("scenario", [RED_HERRING], indirect=True)
def test_an_open_hypothesis_only_a_dead_source_can_judge_escalates_as_an_outage(
    scenario: Scenario,
) -> None:
    model = ScriptedChatModel(
        script_by_schema={
            Triage: [scenario.scripted_outputs.triage] * 3,
            RcaOutput: [rca_without_deploy_citations(scenario, 0.3)] * 3,
            Escalation: [HAND_OFF] * 3,
        }
    )

    final = run(scenario, DeadSourcesSimulator(scenario, {"deploys"}), model)

    assert final["status"] == "escalated"
    assert final["escalation_reason"] == "source_outage"
    assert final["unreachable_sources"] == ["deploys"]
    rca_prompts = [prompt for prompt in prompts_of(model) if "Hypotheses to re-score" in prompt]
    assert rca_prompts
    assert all("Unreachable sources: deploys" in prompt for prompt in rca_prompts)


@pytest.mark.parametrize("scenario", [RED_HERRING], indirect=True)
def test_a_closed_hypothesis_a_dead_source_could_judge_still_reaches_approval(
    scenario: Scenario,
) -> None:
    model = ScriptedChatModel(
        script_by_schema={
            Triage: [scenario.scripted_outputs.triage] * 3,
            RcaOutput: [rca_without_deploy_citations(scenario, 0.1)] * 3,
        }
    )

    final = run(scenario, DeadSourcesSimulator(scenario, {"deploys"}), model)

    assert final["status"] == "awaiting_approval"
    assert final["escalation_reason"] is None
    assert final["unreachable_sources"] == ["deploys"]
    assert final["remediation"].action == "scale_db_pool"
    rca_prompts = [prompt for prompt in prompts_of(model) if "Hypotheses to re-score" in prompt]
    assert rca_prompts
    assert all("Unreachable sources: deploys" in prompt for prompt in rca_prompts)


def test_every_source_dead_records_at_most_twelve_read_attempts(scenario: Scenario) -> None:
    dead = DeadSourcesSimulator(scenario, {"logs", "metrics", "deploys"})

    final = run(scenario, dead, triage_and_hand_off_model(scenario))

    assert len(source_reads(final)) <= INITIAL_INVESTIGATION_BUDGET
    assert final["escalation_reason"] == "source_outage"


def test_reads_that_fail_once_then_answer_stay_within_twelve_attempts(
    scenario: Scenario,
) -> None:
    model = ScriptedChatModel(
        script_by_schema={
            Triage: [scenario.scripted_outputs.triage] * 3,
            RcaOutput: [scenario.scripted_outputs.rca] * 3,
            Escalation: [HAND_OFF] * 3,
        }
    )

    final = run(scenario, FirstAttemptFailsSimulator(scenario), model)

    assert len(source_reads(final)) <= INITIAL_INVESTIGATION_BUDGET


@pytest.mark.parametrize("scenario", [CLEAN_BAD_DEPLOY], indirect=True)
def test_metrics_dead_alone_still_reaches_approval_and_names_the_source(
    scenario: Scenario,
) -> None:
    model = ScriptedChatModel(
        script_by_schema={
            Triage: [scenario.scripted_outputs.triage] * 3,
            RcaOutput: [scenario.scripted_outputs.rca] * 3,
        }
    )

    final = run(scenario, DeadSourcesSimulator(scenario, {"metrics"}), model)

    assert final["status"] == "awaiting_approval"
    assert final["unreachable_sources"] == ["metrics"]
    assert len(source_reads(final)) <= INITIAL_INVESTIGATION_BUDGET
    assert final["escalation_reason"] != "no_evidence"
