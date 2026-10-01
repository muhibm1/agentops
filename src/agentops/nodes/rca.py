"""The rca node: names the root cause and one remediation, both citing collected evidence."""

import json
from typing import Any

from langchain_core.language_models import BaseChatModel

from agentops.nodes import UNTRUSTED_DATA_RULE, ask_model, neutralise_delimiters
from agentops.state import Evidence, IncidentState, RcaOutput, assert_cited

OPENING_DELIMITER = "<<<untrusted evidence>>>"
CLOSING_DELIMITER = "<<<end>>>"

# The delimiters are described, not quoted, so the prompt holds exactly one of each.
RCA_SYSTEM_PROMPT = (
    "You are the root-cause step of an incident investigation. The evidence is one JSON object "
    "per line, inside a block that opens with the marker 'untrusted evidence' and closes with "
    "the marker 'end'. Name the root cause as a snake_case label and propose one remediation. "
    "Both must cite the ids of the evidence items that support them, and only those ids. "
    + UNTRUSTED_DATA_RULE
)


def rca(state: IncidentState, *, model: BaseChatModel) -> dict[str, Any]:
    """Ask the model for a root cause and a remediation; raise `CitationError` if either cites
    nothing or an id not in state."""
    symptoms = "\n".join(f"- {neutralise_delimiters(symptom)}" for symptom in state["symptoms"])
    human_prompt = (
        f"Service: {neutralise_delimiters(state['service'])}\nSymptoms:\n{symptoms}\n\n"
        f"{render_evidence(state['evidence'])}"
    )

    result, action = ask_model(model, RcaOutput, RCA_SYSTEM_PROMPT, human_prompt)

    assert_cited(result.root_cause.evidence_ids, state["evidence"])
    assert_cited(result.remediation.evidence_ids, state["evidence"])
    return {
        "root_cause": result.root_cause,
        "remediation": result.remediation,
        "actions": [action],
    }


def render_evidence(evidence: list[Evidence]) -> str:
    """The evidence block: one JSON line per item between the untrusted-data delimiters."""
    lines = [
        json.dumps({"id": item.id, "kind": item.kind, "payload": item.payload}, ensure_ascii=False)
        for item in evidence
    ]
    return "\n".join([OPENING_DELIMITER, *lines, CLOSING_DELIMITER])
