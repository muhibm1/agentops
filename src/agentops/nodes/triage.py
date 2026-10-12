"""The triage node: ranks hypotheses from the service and its symptoms, before any evidence."""

from typing import Any

from langchain_core.language_models import BaseChatModel

from agentops.nodes import UNTRUSTED_DATA_RULE, ask_model, neutralise_delimiters
from agentops.state import (
    HYPOTHESIS_NAME_MAX_LENGTH,
    IncidentState,
    Triage,
    assert_cited,
    cause_catalogue,
)

# Stable, so it is the cached system block; triage fences no evidence and has no nonce.
TRIAGE_INSTRUCTIONS = (
    "You are the triage step of an incident investigation. From the affected service and its "
    "symptoms, list the plausible causes as hypotheses, each with a score between 0 and 1. "
    f"Name each hypothesis with one label from this list: {cause_catalogue()}. Only if "
    "none fits, use another short snake_case label of at most "
    f"{HYPOTHESIS_NAME_MAX_LENGTH} characters. Later steps refer to it by that exact name. "
    "No evidence has been collected yet, so leave supporting_evidence and "
    "contradicting_evidence empty: they hold evidence ids, never a description of a symptom. "
    + UNTRUSTED_DATA_RULE
)
TRIAGE_SYSTEM_PROMPT = TRIAGE_INSTRUCTIONS


def triage(state: IncidentState, *, model: BaseChatModel) -> dict[str, Any]:
    """Ask the model for hypotheses; raise `CitationError` if one cites evidence not in state."""
    symptoms = "\n".join(f"- {neutralise_delimiters(symptom)}" for symptom in state["symptoms"])
    human_prompt = f"Service: {neutralise_delimiters(state['service'])}\nSymptoms:\n{symptoms}"

    result, action = ask_model(model, Triage, TRIAGE_INSTRUCTIONS, human_prompt)

    for hypothesis in result.hypotheses:
        cited = [*hypothesis.supporting_evidence, *hypothesis.contradicting_evidence]
        # A hypothesis may cite nothing at triage; anything it does cite must exist.
        if cited:
            assert_cited(cited, state["evidence"])
    return {"hypotheses": result.hypotheses, "status": "investigating", "actions": [action]}
