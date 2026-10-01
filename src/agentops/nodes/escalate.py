"""The escalate node: hands the incident to a human with a short summary and the reason why, and
writes an `escalation` record to the audit log naming the reason and any proposed action."""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from pydantic import ValidationError

from agentops.actions.audit import AuditLog
from agentops.actions.registry import idempotency_key
from agentops.nodes import (
    UNTRUSTED_DATA_RULE,
    ask_model,
    fence_rule,
    neutralise_delimiters,
    new_fence_nonce,
)
from agentops.nodes.merge import decide_next_step, reason_for_escalating_before_rca
from agentops.nodes.rca import render_evidence, run_thread_id
from agentops.state import (
    ESCALATION_SUMMARY_MAX_LENGTH,
    Action,
    CitationError,
    Escalation,
    EscalationReason,
    IncidentState,
    assert_cited,
)

# Same count as the graph's retry policy for the other model nodes (increment-1 ADR 0002).
# Escalate retries inside the node because it is the last step: when the attempts run out, a
# human still has to be told.
ESCALATE_ATTEMPTS = 3

ESCALATE_INSTRUCTIONS = (
    "You are the hand-off step of an incident investigation. The graph could not name one "
    "actionable cause. Write a short summary for the on-call human of the competing hypotheses "
    "and the evidence for each, and list the ids of the evidence items you rely on. Propose no "
    "remediation. "
)


def escalate_system_prompt(nonce: str) -> str:
    """The hand-off instructions plus the description of this prompt's evidence fence."""
    return f"{ESCALATE_INSTRUCTIONS}{fence_rule(nonce)} {UNTRUSTED_DATA_RULE}"


def escalate(
    state: IncidentState,
    config: RunnableConfig,
    *,
    model: BaseChatModel,
    audit: AuditLog,
) -> dict[str, Any]:
    """Ask the model for a hand-off summary, up to `ESCALATE_ATTEMPTS` times.

    If every attempt is rejected (a bad reply or a citation not in state), the summary is built by
    code from the hypotheses, so the run never ends without a hand-off. The reason comes from the
    state, never from the model. Raises ValueError when the run's `configurable` has no thread id.
    """
    thread_id = run_thread_id(config, "escalate")
    result, actions = hand_off_summary(state, model)
    reason = escalation_reason(state)
    audit.append(state["incident_id"], thread_id, escalation_record(state, reason))
    return {
        "escalation": result,
        "escalation_reason": reason,
        "status": "escalated",
        "actions": actions,
    }


def hand_off_summary(state: IncidentState, model: BaseChatModel) -> tuple[Escalation, list[Action]]:
    """The model's validated summary, or the code-built fallback, and one action per attempt."""
    hypotheses = "\n".join(
        f"- {neutralise_delimiters(item.name)}: {item.score}" for item in state["hypotheses"]
    )
    nonce = new_fence_nonce()
    system_prompt = escalate_system_prompt(nonce)
    human_prompt = (
        f"Service: {neutralise_delimiters(state['service'])}\nHypotheses:\n{hypotheses}\n\n"
        f"{render_evidence(state['evidence'], nonce)}"
    )

    actions: list[Action] = []
    result = fallback_escalation(state)
    for attempt in range(1, ESCALATE_ATTEMPTS + 1):
        try:
            candidate, action = ask_model(model, Escalation, system_prompt, human_prompt)
            # The summary may rely on no evidence; anything it does cite must exist.
            if candidate.evidence_ids:
                assert_cited(candidate.evidence_ids, state["evidence"])
        except (CitationError, ValidationError) as error:
            # Only the class name: the message can quote model output or evidence text.
            rejected = Action(
                tool="model",
                ok=False,
                latency_ms=0.0,
                detail=type(error).__name__,
                attempt=attempt,
            )
            actions.append(rejected)
            continue
        result = candidate
        actions.append(action.model_copy(update={"attempt": attempt}))
        break
    return result, actions


def escalation_record(state: IncidentState, reason: EscalationReason) -> dict[str, Any]:
    """The audit record of the hand-off: the reason and, when one exists, the proposed action."""
    remediation = state["remediation"]
    if remediation is None:
        return {"event": "escalation", "reason": reason, "proposed_action": None}
    return {
        "event": "escalation",
        "idempotency_key": idempotency_key(state["incident_id"], remediation),
        "reason": reason,
        "proposed_action": remediation.action,
        "target": remediation.target,
    }


def fallback_escalation(state: IncidentState) -> Escalation:
    """A summary of the hypotheses and their scores, written by code with no model text."""
    ranked = ", ".join(f"{item.name} ({item.score})" for item in state["hypotheses"])
    summary = (
        "Automatic hand-off: the model summary could not be validated. "
        f"Hypotheses and scores: {ranked or 'none recorded'}."
    )
    return Escalation(summary=summary[:ESCALATION_SUMMARY_MAX_LENGTH])


def escalation_reason(state: IncidentState) -> EscalationReason:
    """Why the graph escalated: the reason merge or rca already wrote to the state; when none is
    set (a node called on its own), merge's reason if the investigation could not continue,
    otherwise `ambiguous`, which also covers an rca that named no cause."""
    if state["escalation_reason"] is not None:
        return state["escalation_reason"]
    if decide_next_step(state) == "escalate":
        return reason_for_escalating_before_rca(state)
    return "ambiguous"
