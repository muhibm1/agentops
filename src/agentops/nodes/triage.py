"""The triage node: ranks hypotheses from the service and its symptoms, before any evidence."""

from typing import Any

from langchain_core.language_models import BaseChatModel

from agentops.nodes import UNTRUSTED_DATA_RULE, ask_model
from agentops.state import IncidentState, Triage, assert_cited

TRIAGE_SYSTEM_PROMPT = (
    "You are the triage step of an incident investigation. From the affected service and its "
    "symptoms, list the plausible causes as hypotheses, each with a score between 0 and 1. "
    "Cite an evidence id only if it was shown to you; none has been collected yet. "
    + UNTRUSTED_DATA_RULE
)


def triage(state: IncidentState, *, model: BaseChatModel) -> dict[str, Any]:
    """Ask the model for hypotheses; raise `CitationError` if one cites evidence not in state."""
    symptoms = "\n".join(f"- {symptom}" for symptom in state["symptoms"])
    human_prompt = f"Service: {state['service']}\nSymptoms:\n{symptoms}"

    result, action = ask_model(model, Triage, TRIAGE_SYSTEM_PROMPT, human_prompt)

    for hypothesis in result.hypotheses:
        cited = [*hypothesis.supporting_evidence, *hypothesis.contradicting_evidence]
        # A hypothesis may cite nothing at triage; anything it does cite must exist.
        if cited:
            assert_cited(cited, state["evidence"])
    return {"hypotheses": result.hypotheses, "status": "investigating", "actions": [action]}
