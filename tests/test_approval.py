"""E11, E23, E24, E46, E51, E59: the approval interrupt and its re-validation loop (ADR 0001).

A small graph holds the approval node, its self edge and the real execute node, over
`InMemorySaver`, so a test can pause at the interrupt, resume it and read the state and the
audit file back.
"""

import json
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry, idempotency_key
from agentops.nodes import approval as approval_module
from agentops.nodes.approval import after_approval, approval
from agentops.nodes.evidence import deploys, logs, metrics
from agentops.nodes.execute import execute
from agentops.sources.scenario import Scenario, load_scenario
from agentops.sources.simulator import SimulatorSource
from agentops.state import (
    APPROVAL_REASON_MAX_LENGTH,
    RCA,
    Hypothesis,
    IncidentState,
    Remediation,
    new_incident,
)

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "scenarios"
PAYLOAD_FIELDS = {
    "remediation",
    "evidence",
    "dry_run",
    "rollback",
    "unreachable_sources",
    "incident_id",
    "idempotency_key",
}
INVESTIGATION_NODES = ("logs", "metrics", "deploys", "merge")


def scenario_named(scenario_id: str) -> Scenario:
    return load_scenario(SCENARIOS_DIR / f"{scenario_id}.json")


def proposal_state(scenario: Scenario) -> IncidentState:
    """The scenario after a real evidence round and its scripted rca: waiting for approval."""
    source = SimulatorSource(scenario)
    state = new_incident(
        scenario.service,
        scenario.symptoms,
        opened_at=scenario.incident_time,
        incident_id=scenario.id,
        scenario_id=scenario.id,
    )
    for node in (logs, metrics, deploys):
        update = node(state, source=source)
        state["evidence"] = [*state["evidence"], *update["evidence"]]
        state["actions"] = [*state["actions"], *update["actions"]]
    rca = scenario.scripted_outputs.rca
    state["hypotheses"] = [Hypothesis.model_validate(item) for item in rca["hypotheses"]]
    state["root_cause"] = RCA.model_validate(rca["root_cause"])
    state["remediation"] = Remediation.model_validate(rca["remediation"])
    state["status"] = "awaiting_approval"
    return state


class ApprovalRun:
    """One thread of the small approval graph, with its source and audit directory."""

    def __init__(self, scenario: Scenario, audit_dir: Path, *, executes: bool = True) -> None:
        self.scenario = scenario
        self.source = SimulatorSource(scenario)
        self.audit_dir = audit_dir
        self.config: RunnableConfig = {"configurable": {"thread_id": scenario.id}}
        self.graph = approval_graph(self.source, AuditLog(audit_dir), executes=executes)

    def start(self) -> dict[str, Any]:
        return interrupt_value(self.graph.invoke(proposal_state(self.scenario), self.config))

    def answer(self, value: Any) -> dict[str, Any]:
        return self.graph.invoke(Command(resume=value), self.config)

    def state(self) -> dict[str, Any]:
        return self.graph.get_state(self.config).values

    def audit(self) -> list[dict[str, Any]]:
        path = self.audit_dir / f"{self.scenario.id}.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def approval_records(self) -> list[dict[str, Any]]:
        return [record for record in self.audit() if record["event"] == "approval"]


def approval_graph(
    source: SimulatorSource, audit: AuditLog, *, executes: bool
) -> CompiledStateGraph:
    """Approval with its self edge; `execute` is the real node, or a no-op when `executes` is
    False so the state the approval node wrote can be read as it left it."""
    registry = default_registry()
    graph = StateGraph(IncidentState)
    graph.add_node("approval", partial(approval, registry=registry, audit=audit))
    if executes:
        graph.add_node("execute", partial(execute, source=source, registry=registry, audit=audit))
    else:
        graph.add_node("execute", lambda state: {})
    for name in INVESTIGATION_NODES:
        graph.add_node(name, lambda state: {})
        graph.add_edge(name, END)
    graph.add_edge(START, "approval")
    graph.add_conditional_edges(
        "approval", after_approval, ["approval", "execute", *INVESTIGATION_NODES]
    )
    graph.add_edge("execute", END)
    return graph.compile(checkpointer=InMemorySaver())


def interrupt_value(result: dict[str, Any]) -> dict[str, Any]:
    [pending] = result["__interrupt__"]
    return pending.value


def edited(scenario: Scenario, **changes: Any) -> dict[str, Any]:
    remediation = dict(scenario.scripted_outputs.rca["remediation"])
    remediation.update(changes)
    return {"decision": "edit", "remediation": remediation, "operator": "on-call"}


# E11: the payload, an approve as on-call, the audit record and exactly one interrupt per pass.
def test_approve_as_on_call_writes_the_approval_and_routes_to_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_interrupt = approval_module.interrupt
    interrupt_calls: list[Any] = []

    def counting_interrupt(value: Any) -> Any:
        interrupt_calls.append(value)
        return real_interrupt(value)

    monkeypatch.setattr(approval_module, "interrupt", counting_interrupt)
    scenario = scenario_named("clean_bad_deploy")
    run = ApprovalRun(scenario, tmp_path, executes=False)

    payload = run.start()
    state_before = run.state()
    run.answer({"decision": "approve", "operator": "on-call"})
    resumed = run.state()

    proposal = Remediation.model_validate(scenario.scripted_outputs.rca["remediation"])
    assert set(payload) == PAYLOAD_FIELDS
    assert payload["remediation"]["action"] == "rollback_deploy"
    assert [item["id"] for item in payload["evidence"]] == proposal.evidence_ids
    assert "rollback_deploy" in payload["dry_run"]
    assert payload["rollback"]["proposal"] == proposal.rollback
    assert payload["unreachable_sources"] == []
    assert payload["incident_id"] == scenario.id
    assert payload["idempotency_key"] == idempotency_key(scenario.id, proposal)
    assert state_before["approval"] is None
    assert resumed["approval"].decision == "approve"
    assert resumed["approval"].operator == "on-call"
    assert resumed["approval"].idempotency_key == payload["idempotency_key"]
    assert resumed["status"] == "remediating"
    assert after_approval(resumed) == "execute"  # type: ignore[arg-type]
    [record] = run.approval_records()
    assert record["valid"] is True
    assert record["decision"] == "approve"
    assert record["operator"] == "on-call"
    assert record["thread_id"] == scenario.id
    assert len(interrupt_calls) == 2  # once when paused, once when resumed: one per execution


# E23: a valid edit replaces the remediation and the edited change is what runs.
def test_a_valid_edit_is_approved_and_executes_with_the_edited_parameters(tmp_path: Path) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    run = ApprovalRun(scenario, tmp_path)
    run.start()

    run.answer(edited(scenario, params={"pool_size": 112}))
    final = run.state()

    assert final["approval"].decision == "edit"
    assert final["approval"].edited_remediation.params == {"pool_size": 112}
    assert final["remediation"].params == {"pool_size": 112}
    assert [entry.params for entry in run.source.ledger()] == [{"pool_size": 112}]
    [record] = run.approval_records()
    assert record["edited"] is True
    assert record["valid"] is True


# E24: an edit citing an unknown id interrupts again; approving then runs the original.
def test_an_edit_citing_an_unknown_id_asks_again_then_approve_runs_the_original(
    tmp_path: Path,
) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    run = ApprovalRun(scenario, tmp_path)
    run.start()

    second = interrupt_value(run.answer(edited(scenario, evidence_ids=["log-999"])))
    waiting = run.state()
    run.answer({"decision": "approve", "operator": "on-call"})

    assert "log-999" in second["error"]
    assert waiting["approval"] is None
    assert waiting["approval_error"] is not None
    assert [entry.params for entry in run.source.ledger()] == [{"pool_size": 96}]
    refused, accepted = run.approval_records()
    assert refused["valid"] is False
    assert "log-999" in refused["error"]
    assert accepted["valid"] is True
    assert run.state()["approval_error"] is None


# E46: an edit with an out-of-range parameter and an undeclared one is refused, naming both.
def test_an_edit_with_a_huge_pool_and_a_force_flag_is_refused(tmp_path: Path) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    run = ApprovalRun(scenario, tmp_path)
    run.start()

    second = interrupt_value(
        run.answer(edited(scenario, params={"pool_size": 10000, "force": True}))
    )

    assert "pool_size" in second["error"]
    assert "force" in second["error"]
    assert run.state()["approval"] is None
    assert run.source.ledger() == []
    [record] = run.approval_records()
    assert record["valid"] is False
    assert "pool_size" in record["error"]
    assert not [record for record in run.audit() if record["event"] == "apply"]


def test_an_edit_naming_an_action_outside_the_runbook_is_refused(tmp_path: Path) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    run = ApprovalRun(scenario, tmp_path)
    run.start()

    second = interrupt_value(
        run.answer(edited(scenario, action="drop_table", target="search_index_old", params={}))
    )

    assert "drop_table" in second["error"]
    assert run.state()["approval"] is None
    assert run.source.ledger() == []


def test_an_edit_aimed_at_a_target_outside_the_actions_pattern_is_refused(tmp_path: Path) -> None:
    scenario = scenario_named("deploy_red_herring_db")
    run = ApprovalRun(scenario, tmp_path)
    run.start()

    second = interrupt_value(run.answer(edited(scenario, target="payments-warehouse")))

    assert "target" in second["error"]
    assert "payments-warehouse" not in second["error"]
    assert run.state()["approval"] is None
    assert run.source.ledger() == []


# E51: a padded decision, an extra key and a username as operator are all refused.
@pytest.mark.parametrize(
    "answer",
    [
        {"decision": "approve "},
        {"decision": "approve", "status": "resolved"},
        {"decision": "approve", "operator": "muhibm1"},
    ],
    ids=["padded decision", "extra key", "username as operator"],
)
def test_a_malformed_answer_interrupts_again_and_changes_nothing(
    tmp_path: Path, answer: dict[str, Any]
) -> None:
    scenario = scenario_named("clean_bad_deploy")
    run = ApprovalRun(scenario, tmp_path)
    run.start()

    second = interrupt_value(run.answer(answer))
    waiting = run.state()

    assert second["error"]
    assert waiting["approval"] is None
    assert waiting["status"] == "awaiting_approval"
    [record] = run.approval_records()
    assert record["valid"] is False


# E59: a reject with no reason or an oversized one, and an edit with no remediation.
@pytest.mark.parametrize(
    "answer",
    [
        {"decision": "reject"},
        {"decision": "reject", "reason": "x" * (APPROVAL_REASON_MAX_LENGTH + 1)},
        {"decision": "edit"},
    ],
    ids=["reject without reason", "oversized reason", "edit without remediation"],
)
def test_an_incomplete_answer_interrupts_again_and_echoes_no_free_text(
    tmp_path: Path, answer: dict[str, Any]
) -> None:
    scenario = scenario_named("clean_bad_deploy")
    run = ApprovalRun(scenario, tmp_path)
    run.start()
    before = run.state()

    second = interrupt_value(run.answer(answer))
    waiting = run.state()

    assert second["error"]
    assert "x" * 50 not in json.dumps(second)
    assert waiting["approval"] is None
    assert len(waiting["evidence"]) == len(before["evidence"])
    assert waiting["loops"] == before["loops"]


def test_a_reject_with_a_reason_records_operator_evidence_and_goes_back_to_the_router(
    tmp_path: Path,
) -> None:
    scenario = scenario_named("clean_bad_deploy")
    run = ApprovalRun(scenario, tmp_path)
    run.start()
    before = run.state()

    run.answer({"decision": "reject", "reason": "rollback window too risky right now"})
    after = run.state()

    [operator_evidence] = [item for item in after["evidence"] if item.kind == "operator"]
    assert operator_evidence.payload["reason"] == "rollback window too risky right now"
    assert after["loops"] == before["loops"] + 1
    assert after["remediation"] is None
    assert after["approval"] is None
    assert after_approval(after) not in ("approval", "execute")  # type: ignore[arg-type]
    assert run.source.ledger() == []
    [record] = run.approval_records()
    assert record["decision"] == "reject"
    assert record["valid"] is True
