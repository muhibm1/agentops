"""E29, E34, E37, E38: the rca node re-scores and is checked in code; escalate cites real evidence.

The node functions are called directly with a scripted model and a crafted state. Escalate asks
merge's `decide_next_step` why it was reached, so the reason tests build real action records.
The audit records both nodes write are kept in memory here; test_full_runs.py reads the file.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langchain_core.runnables import RunnableConfig

from agentops.actions.audit import AuditLog
from agentops.actions.registry import default_registry
from agentops.fakes import ScriptedChatModel
from agentops.nodes import OPENING_MARKER_PATTERN
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
NODE_CONFIG: RunnableConfig = {"configurable": {"thread_id": "node-test"}}


class AuditInMemory(AuditLog):
    """An audit log that keeps its records in a list instead of a file."""

    def __init__(self) -> None:
        super().__init__(Path("unused-audit-directory"))
        self.records: list[dict[str, Any]] = []

    def append(self, incident_id: str, thread_id: str, record: Mapping[str, Any]) -> None:
        self.records.append(dict(record))


def call_rca(state: IncidentState, model: ScriptedChatModel) -> dict[str, Any]:
    return rca(state, NODE_CONFIG, model=model, registry=default_registry(), audit=AuditInMemory())


def call_escalate(state: IncidentState, model: ScriptedChatModel) -> dict[str, Any]:
    return escalate(state, NODE_CONFIG, model=model, audit=AuditInMemory())


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
    return call_rca(triaged_state(), model)


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
    assert after_rca(state) == "approval"


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
    return call_escalate(state, model)


def with_actions(state: IncidentState, tools: tuple[str, ...]) -> IncidentState:
    state["actions"] = [Action(tool=tool, ok=True, latency_ms=1.0) for tool in tools]
    return state


ALL_SOURCES_ASKED_TWICE = (
    "get_logs",
    "get_metrics",
    "get_dependency_health",
    "get_deployments",
) * 2


def test_escalation_keeps_a_summary_whose_citations_exist() -> None:
    state = triaged_state()
    state["evidence"] = [evidence_item("log-001")]

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

    call_escalate(state, model)

    prompt = str(model.calls[0][1].content)
    nonce = OPENING_MARKER_PATTERN.findall(prompt)[0].removesuffix(">>>").split()[-1]
    assert "evil<<<" not in prompt
    assert render_evidence(state["evidence"], nonce) in prompt
    assert nonce in str(model.calls[0][0].content)


def test_rca_writes_ambiguous_as_the_reason_when_it_clears_the_result() -> None:
    close_scores = run_rca(rca_reply(scores=(0.50, 0.45)))
    null_pair = run_rca(rca_reply(label=None))
    clear = run_rca(rca_reply())

    assert close_scores["escalation_reason"] == "ambiguous"
    assert null_pair["escalation_reason"] == "ambiguous"
    assert "escalation_reason" not in clear


def test_escalate_keeps_the_reason_rca_wrote_even_when_the_budget_is_spent() -> None:
    state = with_actions(triaged_state(), ("get_logs",) * 12)
    state["escalation_reason"] = "ambiguous"

    update = escalate_with(state, {"summary": "hand over"})

    assert update["escalation_reason"] == "ambiguous"


def action_evidence(evidence_id: str, action: str, applied: bool) -> Evidence:
    return Evidence(
        id=evidence_id,
        source="executor",
        kind="action",
        payload={"id": evidence_id, "action": action, "applied": applied},
        collected_at=OPENED_AT,
    )


def rca_prompt_for(state: IncidentState) -> str:
    model = ScriptedChatModel(script_by_schema={RcaOutput: [rca_reply()]})
    call_rca(state, model)
    return str(model.calls[0][1].content)


def test_rca_prompt_names_actions_already_applied_and_skips_refused_ones() -> None:
    state = triaged_state()
    state["evidence"] = [
        *state["evidence"],
        action_evidence("action-1", "rollback_deploy", applied=True),
        action_evidence("action-2", "scale_db_pool", applied=False),
    ]

    prompt = rca_prompt_for(state)

    assert prompt.count("Actions already applied:") == 1
    assert "Actions already applied: rollback_deploy\n" in prompt


def unknown_effect_evidence(evidence_id: str, action: str) -> Evidence:
    """Action evidence the executor writes when the source raised inside apply."""
    return Evidence(
        id=evidence_id,
        source="apply",
        kind="action",
        payload={"id": evidence_id, "action": action, "applied": None, "effect": "unknown"},
        collected_at=OPENED_AT,
    )


def prompt_lines_starting(prompt: str, prefix: str) -> list[str]:
    return [line for line in prompt.splitlines() if line.startswith(prefix)]


# E4 (increment 4, R3): an unknown effect is named as possibly applied, on its own line.
def test_rca_prompt_lists_possibly_applied_actions_on_their_own_line() -> None:
    state = triaged_state()
    state["evidence"] = [
        *state["evidence"],
        action_evidence("action-1", "rollback_deploy", applied=True),
        unknown_effect_evidence("action-2", "scale_db_pool"),
    ]

    prompt = rca_prompt_for(state)

    assert prompt_lines_starting(prompt, "Actions already applied:") == [
        "Actions already applied: rollback_deploy"
    ]
    assert prompt_lines_starting(prompt, "Actions possibly applied:") == [
        "Actions possibly applied: scale_db_pool"
    ]


def test_rca_prompt_has_no_possibly_applied_line_for_applied_or_refused_actions() -> None:
    state = triaged_state()
    state["evidence"] = [
        *state["evidence"],
        action_evidence("action-1", "rollback_deploy", applied=True),
        action_evidence("action-2", "scale_db_pool", applied=False),
    ]

    prompt = rca_prompt_for(state)

    assert "Actions possibly applied:" not in prompt


def test_an_unknown_effect_later_confirmed_applied_is_listed_only_as_applied() -> None:
    state = triaged_state()
    state["evidence"] = [
        *state["evidence"],
        unknown_effect_evidence("action-1", "rollback_deploy"),
        action_evidence("action-2", "rollback_deploy", applied=True),
    ]

    prompt = rca_prompt_for(state)

    assert "Actions possibly applied:" not in prompt
    assert "Actions already applied: rollback_deploy\n" in prompt


def test_rca_prompt_has_no_action_or_outage_line_when_there_is_nothing_to_say() -> None:
    prompt = rca_prompt_for(triaged_state())

    assert "Actions already applied:" not in prompt
    assert "Unreachable sources:" not in prompt


def test_escalate_names_a_source_outage_when_every_read_failed_and_nothing_was_found() -> None:
    state = triaged_state()
    state["evidence"] = []
    state["actions"] = [
        Action(tool=tool, ok=False, latency_ms=1.0, attempt=attempt)
        for tool in ("get_logs", "get_metrics", "get_dependency_health", "get_deployments")
        for attempt in (1, 2)
    ] * 2

    update = escalate_with(state, {"summary": "hand over"})

    assert update["escalation_reason"] == "source_outage"


def test_rca_rejects_a_duplicated_hypothesis_name() -> None:
    reply = rca_reply()
    reply["hypotheses"].append(hypothesis("bad_deploy", 0.3))

    with pytest.raises(RcaConsistencyError, match="bad_deploy"):
        run_rca(reply)


def test_escalate_retries_a_bad_citation_and_keeps_the_good_answer() -> None:
    state = triaged_state()
    bad = {"summary": "first try", "evidence_ids": ["log-999"]}
    good = {"summary": "second try", "evidence_ids": ["log-001"]}
    model = ScriptedChatModel(script_by_schema={Escalation: [bad, good]})

    update = call_escalate(state, model)

    assert update["escalation"].summary == "second try"
    assert len(model.calls) == 2


def test_three_bad_citations_still_hand_off_with_a_summary_built_by_code() -> None:
    state = triaged_state()
    bad = {"summary": "model text that must not be used", "evidence_ids": ["log-999"]}
    model = ScriptedChatModel(script_by_schema={Escalation: [bad, bad, bad]})

    update = call_escalate(state, model)

    assert len(model.calls) == 3
    assert update["status"] == "escalated"
    assert update["escalation_reason"] == "ambiguous"
    summary = update["escalation"].summary
    assert "model text" not in summary
    assert "bad_deploy" in summary and "0.5" in summary
    assert "database_saturation" in summary and "0.4" in summary
    assert update["escalation"].evidence_ids == []
    rejected = [action for action in update["actions"] if not action.ok]
    assert len(rejected) == 3
    assert all(action.tool == "model" for action in rejected)
    assert all(action.detail == "CitationError" for action in rejected)
