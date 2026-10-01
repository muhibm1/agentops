"""The postmortem node: writes the record of a verified fix and resolves the incident.

The model is asked for a `Postmortem` whose every cited id exists in state, up to three times.
When every attempt is rejected, a postmortem built by code from the root cause is written
instead, so a verified incident never ends as a failed run (brief D16).
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from pydantic import ValidationError

from agentops.nodes import (
    UNTRUSTED_DATA_RULE,
    ask_model,
    fence_rule,
    neutralise_delimiters,
    new_fence_nonce,
)
from agentops.nodes.rca import render_evidence
from agentops.state import Action, CitationError, IncidentState, Postmortem, assert_cited

# Same count as the graph's retry policy for the other model nodes (increment-1 ADR 0002).
POSTMORTEM_ATTEMPTS = 3

POSTMORTEM_INSTRUCTIONS = (
    "You are the postmortem step of an incident investigation. A remediation was applied and "
    "verification found the symptoms cleared. Write a short blameless postmortem: a summary, a "
    "timeline, the root cause, follow-ups, and the ids of the evidence items you rely on. Cite "
    "only ids that appear in the evidence. "
)


def postmortem_system_prompt(nonce: str) -> str:
    """The postmortem instructions plus the description of this prompt's evidence fence."""
    return f"{POSTMORTEM_INSTRUCTIONS}{fence_rule(nonce)} {UNTRUSTED_DATA_RULE}"


def postmortem(state: IncidentState, *, model: BaseChatModel) -> dict[str, Any]:
    """Ask for a cited postmortem, falling back to one written by code; resolve the incident."""
    nonce = new_fence_nonce()
    system_prompt = postmortem_system_prompt(nonce)
    human_prompt = postmortem_human_prompt(state, nonce)

    actions: list[Action] = []
    result = fallback_postmortem(state)
    for attempt in range(1, POSTMORTEM_ATTEMPTS + 1):
        try:
            candidate, action = ask_model(model, Postmortem, system_prompt, human_prompt)
            assert_cited(candidate.evidence_ids, state["evidence"])
        except (CitationError, ValidationError) as error:
            # Only the class name: the message can quote model output or evidence text.
            actions.append(
                Action(
                    tool="model",
                    ok=False,
                    latency_ms=0.0,
                    detail=type(error).__name__,
                    attempt=attempt,
                )
            )
            continue
        result = candidate
        actions.append(action.model_copy(update={"attempt": attempt}))
        break
    return {"postmortem": result, "status": "resolved", "actions": actions}


def postmortem_human_prompt(state: IncidentState, nonce: str) -> str:
    """The service, symptoms, root cause and applied action, then the fenced evidence."""
    symptoms = "\n".join(f"- {neutralise_delimiters(symptom)}" for symptom in state["symptoms"])
    lines = [
        f"Service: {neutralise_delimiters(state['service'])}",
        f"Symptoms:\n{symptoms}",
    ]
    root_cause = state["root_cause"]
    if root_cause is not None:
        lines.append(
            f"Root cause: {root_cause.label}: {neutralise_delimiters(root_cause.explanation)}"
        )
    execution = state["execution"]
    if execution is not None:
        lines.append(f"Action applied: {execution.action} on {execution.target}")
    return "\n".join(lines) + "\n\n" + render_evidence(state["evidence"], nonce)


def fallback_postmortem(state: IncidentState) -> Postmortem:
    """A postmortem with no model text: the root cause label, the action, and citations taken
    from the root cause and the action evidence already in state."""
    root_cause = state["root_cause"]
    label = root_cause.label if root_cause is not None else "unknown"
    execution = state["execution"]
    applied = f"{execution.action} on {execution.target}" if execution else "the approved action"
    summary = (
        "Automatic postmortem: the model's postmortem could not be validated. "
        f"Root cause {label}; {applied} was applied and verification found the symptoms cleared."
    )
    return Postmortem(
        summary=summary,
        timeline=[f"Applied {applied}.", "Verification found the symptoms cleared."],
        root_cause=label,
        evidence_ids=fallback_citations(state),
    )


def fallback_citations(state: IncidentState) -> list[str]:
    """Ids already in state: the root cause's citations, then the action evidence. Raises
    ValueError when the state holds no evidence at all, which a verified incident cannot."""
    known = [item.id for item in state["evidence"]]
    root_cause = state["root_cause"]
    cited = [item for item in (root_cause.evidence_ids if root_cause else []) if item in known]
    cited += [item.id for item in state["evidence"] if item.kind == "action"]
    if not cited and known:
        cited = known[:1]
    if not cited:
        raise ValueError("A postmortem needs evidence to cite, and the state holds none.")
    return cited
