"""The rca node: re-scores the triage hypotheses and, when one cause stands out, names it with one
remediation. The graph, not the model, decides whether the result is clear enough to act on: an
ambiguous result, an open hypothesis only an unreachable source could settle, or an action the
runbook does not declare, escalates. Every remediation the model proposes is written to the audit
log as a `proposal` record."""

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from agentops.actions.audit import AuditLog
from agentops.actions.registry import Registry, idempotency_key
from agentops.nodes import (
    UNTRUSTED_DATA_RULE,
    ask_model,
    fence_markers,
    fence_rule,
    neutralise_delimiters,
    new_fence_nonce,
)
from agentops.nodes.router import OPEN_HYPOTHESIS_MIN_SCORE, deciding_sources
from agentops.state import (
    CitationError,
    EscalationReason,
    Evidence,
    Hypothesis,
    IncidentState,
    RcaOutput,
    Remediation,
    assert_cited,
)

# The two best hypotheses must differ by at least this much, or the cause is ambiguous
# (increment-2 ADR 0002).
AMBIGUITY_MARGIN = 0.15

RCA_INSTRUCTIONS = (
    "You are the root-cause step of an incident investigation. Re-score every hypothesis you "
    "were given between 0 and 1 in light of the evidence, keeping each name exactly as it is and "
    "citing the evidence ids that support or contradict it. If one cause stands out, name it as "
    "a snake_case label that equals the highest-scored hypothesis and propose one remediation; "
    "both must cite the ids of the evidence items that support them, and only those ids. If no "
    "cause stands out, return both as null. "
)


def rca_system_prompt(nonce: str) -> str:
    """The rca instructions plus the description of this prompt's evidence fence."""
    return f"{RCA_INSTRUCTIONS}{fence_rule(nonce)} {UNTRUSTED_DATA_RULE}"


class RcaConsistencyError(CitationError):
    """The re-scored hypotheses or the root cause label contradict the triage hypotheses.

    A `CitationError` subclass, so the model-node retry policy already covers it.
    """


def rca(
    state: IncidentState,
    config: RunnableConfig,
    *,
    model: BaseChatModel,
    registry: Registry,
    audit: AuditLog,
) -> dict[str, Any]:
    """Ask the model to re-score the hypotheses and propose a cause and remediation.

    Raises `CitationError` if any cited id is not in state, `RcaConsistencyError` if the names
    differ from triage or the label is not the top-scored name, and ValueError when the run's
    `configurable` has no thread id. When the result is ambiguous, has no cause, or leaves open a
    hypothesis only an unreachable source could settle, `root_cause` and `remediation` are
    written as None and the reason is written for escalate. A proposal naming an action outside
    the runbook is kept in state, so escalate can name it, with the reason `action_not_allowed`.
    """
    thread_id = run_thread_id(config, "rca")
    nonce = new_fence_nonce()
    human_prompt = rca_human_prompt(state, nonce)

    result, action = ask_model(model, RcaOutput, rca_system_prompt(nonce), human_prompt)

    check_citations(result, state["evidence"])
    check_consistent_with_triage(result, state)
    if result.remediation is not None:
        audit.append(
            state["incident_id"],
            thread_id,
            proposal_record(state["incident_id"], result.remediation),
        )
    return {
        "hypotheses": result.hypotheses,
        "actions": [action],
        **outcome_fields(result, state["unreachable_sources"], registry),
    }


def outcome_fields(
    result: RcaOutput, unreachable_sources: Sequence[str], registry: Registry
) -> dict[str, Any]:
    """The cause, remediation, status and escalation reason rca writes for `result`."""
    stop_reason = reason_to_stop_after_rca(
        result.hypotheses, result.root_cause is not None, unreachable_sources
    )
    if stop_reason is not None:
        return {
            "root_cause": None,
            "remediation": None,
            "status": "investigating",
            # Written here so escalate does not have to guess later from a budget that may also be
            # spent.
            "escalation_reason": stop_reason,
        }
    if not is_allowed(result.remediation, registry):
        return {
            "root_cause": result.root_cause,
            "remediation": result.remediation,
            "status": "investigating",
            "escalation_reason": "action_not_allowed",
        }
    return {
        "root_cause": result.root_cause,
        "remediation": result.remediation,
        "status": "awaiting_approval",
    }


def run_thread_id(config: RunnableConfig, node: str) -> str:
    """The thread id from the run's `configurable`; every audit record must name its thread."""
    thread_id = (config.get("configurable") or {}).get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise ValueError(f"The {node} node needs a thread_id in the run's configurable.")
    return thread_id


def is_allowed(remediation: Remediation | None, registry: Registry) -> bool:
    """True when the remediation names an action the runbook declares (spec R13)."""
    return remediation is not None and registry.has(remediation.action)


def proposal_record(incident_id: str, remediation: Remediation) -> dict[str, Any]:
    return {
        "event": "proposal",
        "idempotency_key": idempotency_key(incident_id, remediation),
        "action": remediation.action,
        "target": remediation.target,
        "params": dict(remediation.params),
        "evidence_ids": list(remediation.evidence_ids),
        "risk": remediation.risk,
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


def awaits_an_unreachable_source(
    hypotheses: Sequence[Hypothesis], unreachable_sources: Sequence[str]
) -> bool:
    """True when a hypothesis is still open and every source that could settle it is
    unreachable, so the evidence cannot rule it out (spec R2, ADR 0004)."""
    return any(
        item.score >= OPEN_HYPOTHESIS_MIN_SCORE
        and all(source in unreachable_sources for source in deciding_sources(item.name))
        for item in hypotheses
    )


def reason_to_stop_after_rca(
    hypotheses: Sequence[Hypothesis], has_root_cause: bool, unreachable_sources: Sequence[str]
) -> EscalationReason | None:
    """Why rca's result must go to a human instead of approval, or None when it may proceed.
    A source outage is named first, because it tells the human what to fix before re-running."""
    if awaits_an_unreachable_source(hypotheses, unreachable_sources):
        return "source_outage"
    if not has_root_cause or has_ambiguous_scores(hypotheses):
        return "ambiguous"
    return None


def after_rca(state: IncidentState) -> str:
    """The edge after rca: `escalate` when rca wrote a reason (an action outside the runbook
    among them), there is no root cause, the scores are ambiguous or an open hypothesis waits on
    an unreachable source; otherwise `approval`."""
    if state["escalation_reason"] is not None:
        return "escalate"
    stop_reason = reason_to_stop_after_rca(
        state["hypotheses"], state["root_cause"] is not None, state["unreachable_sources"]
    )
    return "approval" if stop_reason is None else "escalate"


def rca_human_prompt(state: IncidentState, nonce: str) -> str:
    """The service, symptoms, hypotheses, what is known about sources and actions, then the
    fenced evidence. The two context lines appear only when they have something to say."""
    symptoms = "\n".join(f"- {neutralise_delimiters(symptom)}" for symptom in state["symptoms"])
    triage_names = ", ".join(neutralise_delimiters(item.name) for item in state["hypotheses"])
    lines = [
        f"Service: {neutralise_delimiters(state['service'])}",
        f"Symptoms:\n{symptoms}",
        f"Hypotheses to re-score: {triage_names}",
    ]
    if state["unreachable_sources"]:
        lines.append(f"Unreachable sources: {', '.join(state['unreachable_sources'])}")
    applied = applied_action_names(state["evidence"])
    if applied:
        lines.append(f"Actions already applied: {', '.join(applied)}")
    return "\n".join(lines) + "\n\n" + render_evidence(state["evidence"], nonce)


def applied_action_names(evidence: Sequence[Evidence]) -> list[str]:
    """The action names of every evidence item of kind `action` that records a real apply."""
    return [
        str(neutralise_delimiters(str(item.payload.get("action", item.id))))
        for item in evidence
        if item.kind == "action" and item.payload.get("applied") is True
    ]


def render_evidence(evidence: Sequence[Evidence], nonce: str) -> str:
    """The evidence block: one JSON line per item between the markers fenced with `nonce`."""
    opening, closing = fence_markers(nonce)
    lines = [
        json.dumps({"id": item.id, "kind": item.kind, "payload": item.payload}, ensure_ascii=False)
        for item in evidence
    ]
    return "\n".join([opening, *lines, closing])
