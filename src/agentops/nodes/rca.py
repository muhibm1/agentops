"""The rca node: re-scores the triage hypotheses and, when one cause stands out, names it with one
remediation. The graph, not the model, decides whether the result is clear enough to act on."""

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END

from agentops.nodes import UNTRUSTED_DATA_RULE, ask_model, neutralise_delimiters
from agentops.state import (
    CitationError,
    Evidence,
    Hypothesis,
    IncidentState,
    RcaOutput,
    assert_cited,
)

OPENING_DELIMITER = "<<<untrusted evidence>>>"
CLOSING_DELIMITER = "<<<end>>>"
# The two best hypotheses must differ by at least this much, or the cause is ambiguous (ADR 0002).
AMBIGUITY_MARGIN = 0.15

# The delimiters are described, not quoted, so the prompt holds exactly one of each.
RCA_SYSTEM_PROMPT = (
    "You are the root-cause step of an incident investigation. The evidence is one JSON object "
    "per line, inside a block that opens with the marker 'untrusted evidence' and closes with "
    "the marker 'end'. Re-score every hypothesis you were given between 0 and 1 in light of the "
    "evidence, keeping each name exactly as it is and citing the evidence ids that support or "
    "contradict it. If one cause stands out, name it as a snake_case label that equals the "
    "highest-scored hypothesis and propose one remediation; both must cite the ids of the "
    "evidence items that support them, and only those ids. If no cause stands out, return both "
    "as null. " + UNTRUSTED_DATA_RULE
)


class RcaConsistencyError(CitationError):
    """The re-scored hypotheses or the root cause label contradict the triage hypotheses.

    A `CitationError` subclass, so the model-node retry policy already covers it.
    """


def rca(state: IncidentState, *, model: BaseChatModel) -> dict[str, Any]:
    """Ask the model to re-score the hypotheses and propose a cause and remediation.

    Raises `CitationError` if any cited id is not in state, and `RcaConsistencyError` if the
    names differ from triage or the label is not the top-scored name. When the result is
    ambiguous or has no cause, `root_cause` and `remediation` are written as None.
    """
    symptoms = "\n".join(f"- {neutralise_delimiters(symptom)}" for symptom in state["symptoms"])
    triage_names = ", ".join(neutralise_delimiters(item.name) for item in state["hypotheses"])
    human_prompt = (
        f"Service: {neutralise_delimiters(state['service'])}\nSymptoms:\n{symptoms}\n"
        f"Hypotheses to re-score: {triage_names}\n\n{render_evidence(state['evidence'])}"
    )

    result, action = ask_model(model, RcaOutput, RCA_SYSTEM_PROMPT, human_prompt)

    check_citations(result, state["evidence"])
    check_consistent_with_triage(result, state)
    update: dict[str, Any] = {"hypotheses": result.hypotheses, "actions": [action]}
    if result.root_cause is None or is_ambiguous(result):
        return {
            **update,
            "root_cause": None,
            "remediation": None,
            "status": "investigating",
            # Written here so escalate does not have to guess later from a budget that may also be
            # spent.
            "escalation_reason": "ambiguous",
        }
    return {
        **update,
        "root_cause": result.root_cause,
        "remediation": result.remediation,
        "status": "awaiting_approval",
    }


def check_citations(output: RcaOutput, evidence: list[Evidence]) -> None:
    """Raise `CitationError` unless every id the output cites exists in `evidence`."""
    for hypothesis in output.hypotheses:
        cited = [*hypothesis.supporting_evidence, *hypothesis.contradicting_evidence]
        # A hypothesis may cite nothing; anything it does cite must exist.
        if cited:
            assert_cited(cited, evidence)
    if output.root_cause is not None and output.remediation is not None:
        assert_cited(output.root_cause.evidence_ids, evidence)
        assert_cited(output.remediation.evidence_ids, evidence)


def check_consistent_with_triage(output: RcaOutput, state: IncidentState) -> None:
    """Raise `RcaConsistencyError` unless the re-scored names equal the triage names, each listed
    once, and the root cause label, when present, is a highest-scored name."""
    triage_names = {item.name for item in state["hypotheses"]}
    rescored_names = {item.name for item in output.hypotheses}
    if len(output.hypotheses) != len(state["hypotheses"]) and rescored_names == triage_names:
        names = [item.name for item in output.hypotheses]
        repeated = sorted({name for name in names if names.count(name) > 1})
        raise RcaConsistencyError(
            f"The re-scored hypotheses must list each triage name exactly once. Repeated: "
            f"{repeated}."
        )
    if rescored_names != triage_names:
        dropped = sorted(triage_names - rescored_names)
        added = sorted(rescored_names - triage_names)
        raise RcaConsistencyError(
            f"The re-scored hypotheses must keep the triage names. Dropped: {dropped}. "
            f"Added: {added}."
        )
    if output.root_cause is None:
        return
    top_score = max(item.score for item in output.hypotheses)
    top_names = {item.name for item in output.hypotheses if item.score == top_score}
    if output.root_cause.label not in top_names:
        raise RcaConsistencyError(
            f"The root cause label '{output.root_cause.label}' is not the highest-scored "
            f"hypothesis ({sorted(top_names)})."
        )


def is_ambiguous(output: RcaOutput) -> bool:
    """True when the two highest re-scored scores differ by less than `AMBIGUITY_MARGIN`."""
    return has_ambiguous_scores(output.hypotheses)


def has_ambiguous_scores(hypotheses: Sequence[Hypothesis]) -> bool:
    """The margin rule over any hypotheses; a single hypothesis is never ambiguous."""
    if len(hypotheses) < 2:
        return False
    best, runner_up = sorted((item.score for item in hypotheses), reverse=True)[:2]
    # Rounded so 0.65 against 0.50 sits exactly on the margin instead of a float error beyond it.
    return round(best - runner_up, 9) < AMBIGUITY_MARGIN


def after_rca(state: IncidentState) -> str:
    """The edge after rca: `escalate` when there is no root cause or the scores are ambiguous,
    else the end of the graph with an approval-ready remediation."""
    if state["root_cause"] is None or has_ambiguous_scores(state["hypotheses"]):
        return "escalate"
    return END


def render_evidence(evidence: list[Evidence]) -> str:
    """The evidence block: one JSON line per item between the untrusted-data delimiters."""
    lines = [
        json.dumps({"id": item.id, "kind": item.kind, "payload": item.payload}, ensure_ascii=False)
        for item in evidence
    ]
    return "\n".join([OPENING_DELIMITER, *lines, CLOSING_DELIMITER])
