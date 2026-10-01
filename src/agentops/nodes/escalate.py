"""The escalate node: hands the incident to a human with a short summary and the reason why."""

from typing import Any

from langchain_core.language_models import BaseChatModel

from agentops.nodes import UNTRUSTED_DATA_RULE, ask_model, neutralise_delimiters
from agentops.nodes.merge import decide_next_step, reason_for_escalating_before_rca
from agentops.nodes.rca import render_evidence
from agentops.state import Escalation, EscalationReason, IncidentState, assert_cited

ESCALATE_SYSTEM_PROMPT = (
    "You are the hand-off step of an incident investigation. The graph could not name one "
    "actionable cause. Write a short summary for the on-call human of the competing hypotheses "
    "and the evidence for each, and list the ids of the evidence items you rely on. The evidence "
    "is one JSON object per line, inside a block that opens with the marker 'untrusted "
    "evidence' and closes with the marker 'end'. Propose no remediation. " + UNTRUSTED_DATA_RULE
)


def escalate(state: IncidentState, *, model: BaseChatModel) -> dict[str, Any]:
    """Ask the model for a hand-off summary; raise `CitationError` if it cites an id not in state.

    The reason comes from the state, never from the model.
    """
    hypotheses = "\n".join(
        f"- {neutralise_delimiters(item.name)}: {item.score}" for item in state["hypotheses"]
    )
    human_prompt = (
        f"Service: {neutralise_delimiters(state['service'])}\nHypotheses:\n{hypotheses}\n\n"
        f"{render_evidence(state['evidence'])}"
    )

    result, action = ask_model(model, Escalation, ESCALATE_SYSTEM_PROMPT, human_prompt)

    # The summary may rely on no evidence; anything it does cite must exist.
    if result.evidence_ids:
        assert_cited(result.evidence_ids, state["evidence"])
    return {
        "escalation": result,
        "escalation_reason": escalation_reason(state),
        "status": "escalated",
        "actions": [action],
    }


def escalation_reason(state: IncidentState) -> EscalationReason:
    """Why the graph escalated: merge's reason when the investigation could not continue,
    otherwise `ambiguous`, which also covers an rca that named no cause."""
    if decide_next_step(state) == "escalate":
        return reason_for_escalating_before_rca(state)
    return "ambiguous"
