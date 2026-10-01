"""Merge owns the budget and the loop count; the next step depends only on evidence and reads."""

from datetime import UTC, datetime

from langgraph.types import Send

from agentops.nodes.merge import (
    decide_next_step,
    merge,
    reason_for_escalating_before_rca,
    source_read_attempts,
)
from agentops.state import Action, Evidence, Hypothesis, IncidentState, new_incident

OPENED_AT = datetime(2026, 3, 10, 10, 30, tzinfo=UTC)
HYPOTHESIS_NAMES = ["recent_deploy_regression", "database_saturation"]


def incident_with(
    *, evidence_tools: tuple[str, ...], action_tools: tuple[str, ...]
) -> IncidentState:
    """Two open hypotheses, one evidence item per evidence tool, one ok action per action tool."""
    state = new_incident("orders", ["slow"], opened_at=OPENED_AT)
    state["hypotheses"] = [Hypothesis(name=name, score=0.5) for name in HYPOTHESIS_NAMES]
    state["evidence"] = [
        Evidence(
            id=f"{tool}-{index}",
            source=tool,
            kind="log",
            payload={"id": f"{tool}-{index}"},
            collected_at=OPENED_AT,
        )
        for index, tool in enumerate(evidence_tools)
    ]
    state["actions"] = [Action(tool=tool, ok=True, latency_ms=1.0) for tool in action_tools]
    return state


def test_merge_routes_to_rca_when_nothing_is_left_to_ask() -> None:
    state = incident_with(
        evidence_tools=("get_logs", "get_metrics", "get_deployments"),
        action_tools=("get_logs", "get_metrics", "get_dependency_health", "get_deployments"),
    )

    assert decide_next_step(state) == "rca"


def test_merge_loops_back_for_an_empty_source_and_counts_the_loop() -> None:
    state = incident_with(
        evidence_tools=("get_logs", "get_metrics"),
        action_tools=("get_logs", "get_metrics", "get_dependency_health", "get_deployments"),
    )

    update = merge(state)
    state["loops"] = update["loops"]
    state["investigation_budget"] = update["investigation_budget"]
    decision = decide_next_step(state)

    assert isinstance(decision, list)
    assert [send.node for send in decision if isinstance(send, Send)] == ["deploys"]
    assert state["loops"] == 1
    assert state["investigation_budget"] == 8


def test_merge_does_not_count_a_loop_when_it_routes_to_rca() -> None:
    state = incident_with(
        evidence_tools=("get_logs", "get_metrics", "get_deployments"),
        action_tools=("get_logs", "get_metrics", "get_dependency_health", "get_deployments"),
    )

    assert merge(state)["loops"] == 0


def test_budget_never_goes_below_zero() -> None:
    state = incident_with(evidence_tools=("get_logs",), action_tools=("get_logs",) * 13)

    assert merge(state)["investigation_budget"] == 0


def test_only_source_reads_are_charged_to_the_budget() -> None:
    state = incident_with(evidence_tools=(), action_tools=("get_logs", "model", "model"))

    assert source_read_attempts(state) == 1


def test_no_evidence_after_every_source_was_asked_escalates_as_no_evidence() -> None:
    state = incident_with(
        evidence_tools=(),
        action_tools=("get_logs", "get_metrics", "get_dependency_health", "get_deployments") * 2,
    )

    assert decide_next_step(state) == "escalate"
    assert reason_for_escalating_before_rca(state) == "no_evidence"


def test_an_unaffordable_source_escalates_as_budget_exhausted() -> None:
    state = incident_with(evidence_tools=("get_logs",), action_tools=("get_logs",) * 11)

    assert decide_next_step(state) == "escalate"
    assert reason_for_escalating_before_rca(state) == "budget_exhausted"


def test_the_next_step_does_not_depend_on_the_loop_count() -> None:
    state = incident_with(
        evidence_tools=("get_logs", "get_metrics"),
        action_tools=("get_logs", "get_metrics", "get_dependency_health", "get_deployments"),
    )
    steps = []
    for loops in (0, 1, 5):
        state["loops"] = loops
        decision = decide_next_step(state)
        assert isinstance(decision, list)
        steps.append([send.node for send in decision])

    assert steps == [["deploys"]] * 3


def test_merge_writes_its_stop_reason_when_it_routes_to_escalate() -> None:
    no_evidence = incident_with(
        evidence_tools=(),
        action_tools=("get_logs", "get_metrics", "get_dependency_health", "get_deployments") * 2,
    )
    budget_spent = incident_with(evidence_tools=("get_logs",), action_tools=("get_logs",) * 12)
    carry_on = incident_with(evidence_tools=("get_logs", "get_metrics"), action_tools=())

    assert merge(no_evidence)["escalation_reason"] == "no_evidence"
    assert merge(budget_spent)["escalation_reason"] == "budget_exhausted"
    assert "escalation_reason" not in merge(carry_on)


def failed_reads(tools: tuple[str, ...], rounds: int) -> list[Action]:
    """Both attempts of every tool failing, `rounds` times over."""
    return [
        Action(tool=tool, ok=False, latency_ms=1.0, attempt=attempt)
        for _ in range(rounds)
        for tool in tools
        for attempt in (1, 2)
    ]


def test_every_read_failing_with_no_evidence_escalates_as_a_source_outage() -> None:
    state = incident_with(evidence_tools=(), action_tools=())
    state["actions"] = failed_reads(
        ("get_logs", "get_metrics", "get_dependency_health", "get_deployments"), rounds=2
    )

    update = merge(state)

    assert decide_next_step(state) == "escalate"
    assert update["escalation_reason"] == "source_outage"
    assert update["unreachable_sources"] == ["logs", "metrics", "deploys"]


def test_merge_names_only_sources_whose_every_attempt_failed() -> None:
    state = incident_with(
        evidence_tools=("get_logs", "get_metrics"),
        action_tools=("get_logs", "get_metrics", "get_dependency_health"),
    )
    state["actions"] = [*state["actions"], *failed_reads(("get_deployments",), rounds=1)]

    assert merge(state)["unreachable_sources"] == ["deploys"]
