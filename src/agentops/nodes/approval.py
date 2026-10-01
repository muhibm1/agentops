"""The approval node: the one place a human decides whether a remediation runs (ADR 0001).

It calls `interrupt()` exactly once per execution and validates the answer in code. A refused
answer is written to `approval_error`, and the edge brings the run back here to ask again with
the error in the payload. Every answer, accepted or not, is appended to the audit log after the
interrupt returns, so a re-run before an answer writes nothing (brief D21).
"""

import json
import re
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import Send, interrupt
from pydantic import ValidationError

from agentops.actions.audit import AuditLog
from agentops.actions.registry import (
    ParameterError,
    Registry,
    UnknownActionError,
    dry_run_description,
    idempotency_key,
    safe_name,
)
from agentops.nodes import neutralise_delimiters, run_thread_id
from agentops.nodes.router import route_to_sources
from agentops.state import (
    Approval,
    ApprovalDecision,
    Evidence,
    IncidentState,
    Remediation,
)

SAFE_EVIDENCE_ID = re.compile(r"[a-z0-9_-]{1,64}")
UNSAFE_ID_PLACEHOLDER = "<unsafe evidence id>"
# A refused answer is kept in the audit file as submitted, cut to this many characters.
SUBMITTED_MAX_CHARACTERS = 4000


def approval(
    state: IncidentState,
    config: RunnableConfig,
    *,
    registry: Registry,
    audit: AuditLog,
) -> dict[str, Any]:
    """Pause for the operator's decision on `state["remediation"]` and apply it to the state.

    Raises ValueError when there is no remediation to decide on or no thread id to audit under.
    """
    thread_id = run_thread_id(config, "approval")
    remediation = state["remediation"]
    if remediation is None:
        raise ValueError("The approval node needs a remediation to put to the operator.")
    answer = interrupt(approval_payload(state, remediation, registry))

    decision = read_decision(answer, state, registry)
    if isinstance(decision, str):
        audit.append(state["incident_id"], thread_id, refused_record(answer, decision))
        return {"approval_error": decision}
    chosen = decision.remediation or remediation
    key = idempotency_key(state["incident_id"], chosen)
    audit.append(state["incident_id"], thread_id, accepted_record(decision, chosen, key))
    if decision.decision == "reject":
        return rejected(state, decision)
    approved = Approval(
        decision=decision.decision,
        operator=decision.operator,
        edited_remediation=decision.remediation,
        decided_at=datetime.now(UTC),
        idempotency_key=key,
    )
    return {
        "approval": approved,
        "approval_error": None,
        "remediation": chosen,
        "status": "remediating",
    }


def after_approval(state: IncidentState) -> list[Send] | str:
    """The edge after approval: back to `approval` after a refused answer, `execute` after an
    approve or a valid edit, otherwise (a reject) back to the router."""
    if state["approval_error"] is not None:
        return "approval"
    if state["approval"] is not None:
        return "execute"
    return route_to_sources(state)


def approval_payload(
    state: IncidentState, remediation: Remediation, registry: Registry
) -> dict[str, Any]:
    """What the operator sees: the proposal, its cited evidence, the dry run, the rollback, the
    unreachable sources, and the error of the last refused answer when there is one."""
    by_id = {item.id: item for item in state["evidence"]}
    payload: dict[str, Any] = {
        "remediation": remediation.model_dump(mode="json"),
        "evidence": [
            by_id[cited].model_dump(mode="json", include={"id", "kind", "payload"})
            for cited in remediation.evidence_ids
            if cited in by_id
        ],
        "dry_run": "The action is not in the runbook; it cannot run.",
        "rollback": {"runbook": None, "proposal": remediation.rollback},
        "unreachable_sources": list(state["unreachable_sources"]),
        "incident_id": state["incident_id"],
        "idempotency_key": idempotency_key(state["incident_id"], remediation),
    }
    if registry.has(remediation.action):
        entry = registry.get(remediation.action)
        payload["dry_run"] = dry_run_description(entry, remediation)
        payload["rollback"]["runbook"] = entry.rollback.model_dump(mode="json")
    if state["approval_error"] is not None:
        payload["error"] = state["approval_error"]
    return payload


def read_decision(
    answer: object, state: IncidentState, registry: Registry
) -> ApprovalDecision | str:
    """The validated decision, or a sentence saying why it was refused. The sentence names
    fields and rules, never the submitted text."""
    try:
        decision = ApprovalDecision.model_validate(answer)
    except ValidationError as error:
        return describe_validation_error(error)
    if decision.remediation is not None:
        problem = edit_problem(decision.remediation, state, registry)
        if problem is not None:
            return problem
    return decision


def edit_problem(remediation: Remediation, state: IncidentState, registry: Registry) -> str | None:
    """Why an edited remediation may not run: an uncited id, an action outside the runbook, or
    parameters outside its schema. None when it may."""
    known = {item.id for item in state["evidence"]}
    unknown = [shown_id(cited) for cited in remediation.evidence_ids if cited not in known]
    if unknown:
        return f"The edit cites evidence ids not present in state: {', '.join(unknown)}."
    try:
        registry.validate_params(remediation.action, remediation.params, target=remediation.target)
    except (UnknownActionError, ParameterError) as error:
        return f"The edit was refused. {error}"
    return None


def shown_id(evidence_id: str) -> str:
    """The id itself when it has the shape of a signal id, else a placeholder: an id typed by
    the operator is echoed into the payload and the audit file only when it is that shape."""
    return evidence_id if SAFE_EVIDENCE_ID.fullmatch(evidence_id) else UNSAFE_ID_PLACEHOLDER


def describe_validation_error(error: ValidationError) -> str:
    problems = []
    for item in error.errors(include_url=False, include_input=False):
        where = ".".join(safe_name(str(part)) for part in item["loc"]) or "answer"
        problems.append(f"{where}: {item['msg']}")
    return f"The answer was refused. {'; '.join(problems)}."


def rejected(state: IncidentState, decision: ApprovalDecision) -> dict[str, Any]:
    """Drop the proposal, keep the operator's reason as evidence, and investigate again."""
    number = sum(1 for item in state["evidence"] if item.kind == "operator") + 1
    reason = Evidence(
        id=f"operator-{number}",
        source="operator",
        kind="operator",
        payload=neutralise_delimiters(
            {"decision": "reject", "operator": decision.operator, "reason": decision.reason}
        ),
        collected_at=datetime.now(UTC),
    )
    return {
        "evidence": [reason],
        "approval": None,
        "approval_error": None,
        "remediation": None,
        "root_cause": None,
        "loops": state["loops"] + 1,
        "status": "investigating",
    }


def accepted_record(decision: ApprovalDecision, chosen: Remediation, key: str) -> dict[str, Any]:
    return {
        "event": "approval",
        "idempotency_key": key,
        "decision": decision.decision,
        "operator": decision.operator,
        "reason": decision.reason,
        "edited": decision.remediation is not None,
        "action": chosen.action,
        "target": chosen.target,
        "params": dict(chosen.params),
        "valid": True,
    }


def refused_record(answer: object, error: str) -> dict[str, Any]:
    submitted = json.dumps(answer, default=str)[:SUBMITTED_MAX_CHARACTERS]
    return {"event": "approval", "valid": False, "error": error, "submitted": submitted}
