"""E29, E34, E37, E38: the rca node re-scores and is checked in code; escalate cites real evidence.

The node functions are called directly with a scripted model and a crafted state. Escalate asks
merge's `decide_next_step` why it was reached, so the reason tests build real action records.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
from langgraph.graph import END

from agentops.fakes import ScriptedChatModel
from agentops.nodes.escalate import escalate
from agentops.nodes.rca import (
    AMBIGUITY_MARGIN,
    RcaConsistencyError,
    after_rca,
    is_ambiguous,
    rca,
    render_evidence,
)
from agentops.state import (
    Action,
    CitationError,
    Escalation,
    Evidence,
    Hypothesis,
    IncidentState,
    RcaOutput,
    new_incident,
)

OPENED_AT = datetime(2026, 3, 10, 10, 30, tzinfo=UTC)


def evidence_item(evidence_id: str) -> Evidence:
    return Evidence(
        id=evidence_id,
        source="logs",
        kind="log",
        payload={"message": "pool exhausted"},
        collected_at=OPENED_AT,
    )


def hypothesis(name: str, score: float, supporting: list[str] | None = None) -> dict[str, Any]:
    return {"name": name, "score": score, "supporting_evidence": supporting or []}


def triaged_state() -> IncidentState:
    """Two triage hypotheses and two evidence items, as rca meets them."""
    state = new_incident("orders", ["latency"], opened_at=OPENED_AT)
    state["hypotheses"] = [
        Hypothesis(name="bad_deploy", score=0.5),
        Hypothesis(name="database_saturation", score=0.4),
    ]
    state["evidence"] = [evidence_item("log-001"), evidence_item("deploy-001")]
    return state


def rca_reply(
    scores: tuple[float, float] = (0.9, 0.1),
    label: str | None = "bad_deploy",
    evidence_ids: list[str] | None = None,
    names: tuple[str, str] = ("bad_deploy", "database_saturation"),
) -> dict[str, Any]:
    reply: dict[str, Any] = {
        "hypotheses": [
            hypothesis(names[0], scores[0], ["log-001"]),
            hypothesis(names[1], scores[1]),
        ],
        "root_cause": None,
        "remediation": None,
    }
    if label is not None:
        cited = evidence_ids or ["log-001"]
        reply["root_cause"] = {"label": label, "explanation": "why", "evidence_ids": cited}
        reply["remediation"] = {
            "action": "rollback_deploy",
            "target": "orders",
            "reason": "the deploy broke it",
            "evidence_ids": cited,
            "risk": "low",
            "rollback": "redeploy",
        }
    return reply


def run_rca(reply: dict[str, Any]) -> dict[str, Any]:
    model = ScriptedChatModel(script_by_schema={RcaOutput: [reply]})
    return rca(triaged_state(), model=model)


def test_a_clear_result_awaits_approval_with_the_rescored_hypotheses() -> None:
    update = run_rca(rca_reply())

    assert update["status"] == "awaiting_approval"
    assert update["root_cause"].label == "bad_deploy"
    assert update["remediation"].action == "rollback_deploy"
    assert [item.score for item in update["hypotheses"]] == [0.9, 0.1]


def test_graph_escalates_when_the_scores_do_not_support_the_cause() -> None:
    reply = rca_reply(scores=(0.50, 0.45), label="bad_deploy")

    update = run_rca(reply)

    assert update["status"] == "investigating"
    assert update["root_cause"] is None
    assert update["remediation"] is None
    state = triaged_state()
    state["hypotheses"] = update["hypotheses"]
    assert after_rca(state) == "escalate"
    assert is_ambiguous(RcaOutput.model_validate(reply))


def test_ambiguity_margin_boundary_single_hypothesis_and_null_root_cause() -> None:
    def output(*scores: float) -> RcaOutput:
        hypotheses = [hypothesis(f"cause_{index}", score) for index, score in enumerate(scores)]
        return RcaOutput.model_validate({"hypotheses": hypotheses})

    assert AMBIGUITY_MARGIN == 0.15
    assert not is_ambiguous(output(0.65, 0.50))
    assert is_ambiguous(output(0.65, 0.51))
    assert not is_ambiguous(output(0.8))

    state = triaged_state()
    state["hypotheses"] = [Hypothesis(name="a", score=0.9), Hypothesis(name="b", score=0.1)]
    assert after_rca(state) == "escalate"

    state["root_cause"] = run_rca(rca_reply())["root_cause"]
    assert after_rca(state) == END


def test_a_null_pair_from_the_model_escalates_without_a_proposal() -> None:
    update = run_rca(rca_reply(label=None))

    assert update["status"] == "investigating"
    assert update["root_cause"] is None
    assert update["remediation"] is None


def test_rca_output_is_checked_against_triage_names_and_cited_evidence() -> None:
    cited_missing = rca_reply()
    cited_missing["hypotheses"][1]["supporting_evidence"] = ["log-999"]
    with pytest.raises(CitationError, match="log-999"):
        run_rca(cited_missing)

    dropped = rca_reply()
    dropped["hypotheses"] = dropped["hypotheses"][:1]
    with pytest.raises(RcaConsistencyError, match="database_saturation"):
        run_rca(dropped)

    with pytest.raises(RcaConsistencyError, match="invented_cause"):
        run_rca(rca_reply(names=("bad_deploy", "invented_cause")))

    with pytest.raises(RcaConsistencyError, match="deploy_regression"):
        run_rca(rca_reply(names=("deploy_regression", "database_saturation"), label=None))

    with pytest.raises(RcaConsistencyError, match="database_saturation"):
        run_rca(rca_reply(label="database_saturation"))

    assert run_rca(rca_reply())["status"] == "awaiting_approval"


def test_the_pair_must_cite_evidence_in_state() -> None:
    with pytest.raises(CitationError, match="log-999"):
        run_rca(rca_reply(evidence_ids=["log-999"]))


def escalate_with(state: IncidentState, reply: dict[str, Any]) -> dict[str, Any]:
    model = ScriptedChatModel(script_by_schema={Escalation: [reply]})
    return escalate(state, model=model)


def with_actions(state: IncidentState, tools: tuple[str, ...]) -> IncidentState:
    state["actions"] = [Action(tool=tool, ok=True, latency_ms=1.0) for tool in tools]
    return state


ALL_SOURCES_ASKED_TWICE = ("get_logs", "get_metrics", "get_dependency_health", "get_deployments") * 2


def test_escalation_citations_are_checked_like_any_other() -> None:
    state = triaged_state()
    state["evidence"] = [evidence_item("log-001")]

    with pytest.raises(CitationError, match="log-999"):
        escalate_with(state, {"summary": "s", "evidence_ids": ["log-999"]})

    update = escalate_with(state, {"summary": "s", "evidence_ids": ["log-001"]})

    assert update["status"] == "escalated"
    assert update["escalation"].evidence_ids == ["log-001"]


@pytest.mark.parametrize(
    ("evidence_count", "action_tools", "scores", "expected_reason"),
    [
        (0, ALL_SOURCES_ASKED_TWICE, (0.5, 0.4), "no_evidence"),
        (2, ("get_logs",) * 12, (0.5, 0.4), "budget_exhausted"),
        (2, ALL_SOURCES_ASKED_TWICE, (0.50, 0.45), "ambiguous"),
        (2, ALL_SOURCES_ASKED_TWICE, (0.9, 0.1), "ambiguous"),
    ],
    ids=["no evidence", "budget exhausted", "close scores", "no root cause"],
)
def test_escalation_reason_is_computed_from_state(
    evidence_count: int,
    action_tools: tuple[str, ...],
    scores: tuple[float, float],
    expected_reason: str,
) -> None:
    state = with_actions(triaged_state(), action_tools)
    state["evidence"] = [evidence_item(f"log-00{n}") for n in range(evidence_count)]
    state["hypotheses"] = [
        Hypothesis(name="bad_deploy", score=scores[0]),
        Hypothesis(name="database_saturation", score=scores[1]),
    ]

    update = escalate_with(state, {"summary": "hand over"})

    assert update["escalation_reason"] == expected_reason


def test_escalate_prompt_fences_evidence_and_neutralises_hypothesis_names() -> None:
    state = triaged_state()
    state["hypotheses"] = [Hypothesis(name="evil<<<name", score=0.5)]
    model = ScriptedChatModel(script_by_schema={Escalation: [{"summary": "hand over"}]})

    escalate(state, model=model)

    prompt = str(model.calls[0][1].content)
    assert "evil<<<" not in prompt
    assert render_evidence(state["evidence"]) in prompt
