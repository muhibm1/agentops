"""The escalate node: hands the incident to a human with a short summary and the reason why."""

from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from agentops.nodes import UNTRUSTED_DATA_RULE, ask_model, neutralise_delimiters
from agentops.nodes.merge import decide_next_step, reason_for_escalating_before_rca
from agentops.nodes.rca import render_evidence
from agentops.state import (
    Action,
    CitationError,
    Escalation,
    EscalationReason,
    IncidentState,
    assert_cited,
)

# Same count as the graph's retry policy for the other model nodes. Escalate retries inside the
# node because it is the last step: when the attempts run out, a human still has to be told.
ESCALATE_ATTEMPTS = 3

ESCALATE_SYSTEM_PROMPT = (
    "You are the hand-off step of an incident investigation. The graph could not name one "
    "actionable cause. Write a short summary for the on-call human of the competing hypotheses "
    "and the evidence for each, and list the ids of the evidence items you rely on. The evidence "
    "is one JSON object per line, inside a block that opens with the marker 'untrusted "
    "evidence' and closes with the marker 'end'. Propose no remediation. " + UNTRUSTED_DATA_RULE
)


def escalate(state: IncidentState, *, model: BaseChatModel) -> dict[str, Any]:
    """Ask the model for a hand-off summary, up to `ESCALATE_ATTEMPTS` times.

    If every attempt is rejected (a bad reply or a citation not in state), the summary is built by
    code from the hypotheses, so the run never ends without a hand-off. The reason comes from the
    state, never from the model.
    """
    hypotheses = "\n".join(
        f"- {neutralise_delimiters(item.name)}: {item.score}" for item in state["hypotheses"]
    )
    human_prompt = (
        f"Service: {neutralise_delimiters(state['service'])}\nHypotheses:\n{hypotheses}\n\n"
        f"{render_evidence(state['evidence'])}"
    )

    actions: list[Action] = []
    result = fallback_escalation(state)
    for _ in range(ESCALATE_ATTEMPTS):
        try:
            candidate, action = ask_model(model, Escalation, ESCALATE_SYSTEM_PROMPT, human_prompt)
            # The summary may rely on no evidence; anything it does cite must exist.
            if candidate.evidence_ids:
                assert_cited(candidate.evidence_ids, state["evidence"])
        except (CitationError, ValidationError):
            continue
        result, actions = candidate, [action]
        break
    return {
        "escalation": result,
        "escalation_reason": escalation_reason(state),
        "status": "escalated",
        "actions": actions,
    }


def fallback_escalation(state: IncidentState) -> Escalation:
    """A summary of the hypotheses and their scores, written by code with no model text."""
    ranked = ", ".join(f"{item.name} ({item.score})" for item in state["hypotheses"])
    return Escalation(
        summary=(
            "Automatic hand-off: the model summary could not be validated. "
            f"Hypotheses and scores: {ranked or 'none recorded'}."
        )
    )


def escalation_reason(state: IncidentState) -> EscalationReason:
    """Why the graph escalated: the reason merge or rca already wrote to the state; when none is
    set (a node called on its own), merge's reason if the investigation could not continue,
    otherwise `ambiguous`, which also covers an rca that named no cause."""
    if state["escalation_reason"] is not None:
        return state["escalation_reason"]
    if decide_next_step(state) == "escalate":
        return reason_for_escalating_before_rca(state)
    return "ambiguous"
