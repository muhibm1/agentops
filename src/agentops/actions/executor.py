"""The executor: applies one approved remediation exactly once, through the seven steps of
design section 7, and writes the audit trail as it goes.

The steps are registry lookup, parameter validation, preconditions, idempotency key, ledger
check, dry run and apply. Nothing here reads an environment variable and nothing skips a step;
the one switch, `crash_after_apply`, can only end the process after the apply was recorded
(ADR 0002).
"""

import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from agentops.actions.audit import AuditLog
from agentops.actions.preconditions import PreconditionResult, evaluate
from agentops.actions.registry import (
    ParameterError,
    Registry,
    UnknownActionError,
    dry_run_description,
    idempotency_key,
)
from agentops.actions.runbook import ActionEntry
from agentops.nodes import neutralise_delimiters
from agentops.sources.base import ActionRequest, ActionResult, EvidenceSource
from agentops.state import (
    Action,
    Evidence,
    EvidenceKind,
    ExecutionRecord,
    IncidentState,
    Remediation,
)

OutcomeKind = Literal[
    "applied", "already_applied", "refused", "precondition_failed", "apply_failed"
]
# The exit code of the demo crash; any non-zero code would do, this one is not used elsewhere.
CRASH_EXIT_CODE = 3
ALREADY_APPLIED = "already_applied"


@dataclass(frozen=True)
class ExecutionOutcome:
    """What one executor run did. `execution` is None when the remediation was refused."""

    kind: OutcomeKind
    detail: str
    execution: ExecutionRecord | None = None
    evidence: list[Evidence] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)


class RefusedError(Exception):
    """A step refused the remediation. The message is code-written and quotes no model text."""


@dataclass(frozen=True)
class ExecutionContext:
    """The run the steps share: the state, the source, the audit log and the thread."""

    state: IncidentState
    source: EvidenceSource
    audit: AuditLog
    thread_id: str

    def write_audit(self, record: dict[str, Any]) -> None:
        self.audit.append(self.state["incident_id"], self.thread_id, record)


def execute(
    state: IncidentState,
    source: EvidenceSource,
    registry: Registry,
    audit: AuditLog,
    *,
    thread_id: str,
    crash_after_apply: bool = False,
) -> ExecutionOutcome:
    """Apply the approved remediation in `state` at most once.

    Returns `refused` when there is no usable approval, the action or its parameters break the
    runbook, or the remediation is not the one approved; `precondition_failed` when a runbook
    check fails; `already_applied` when the key is recorded in state or in the source's ledger;
    otherwise `applied` or `apply_failed`. Raises when the audit log cannot be written (before
    apply) or the source raises inside `apply` (after its `apply` record is written).
    """
    context = ExecutionContext(state, source, audit, thread_id)
    try:
        remediation = approved_remediation(state)
        entry = look_up_action(registry, remediation)
        validate_parameters(registry, remediation)
        checks = check_preconditions(context, entry, remediation)
        if not all(check.passed for check in checks):
            return precondition_failed(context, remediation, checks)
        key = compute_key(state, remediation)
        recorded = find_recorded_execution(context, remediation, key)
        if recorded is not None:
            return recorded
        description = record_dry_run(context, entry, remediation, key)
        return apply_and_audit(context, remediation, key, description, crash_after_apply)
    except RefusedError as refusal:
        return refused(state, str(refusal))


def exit_process(code: int) -> None:
    """End the process at once, with no cleanup, as a crash would (the demo switch only)."""
    os._exit(code)


def approved_remediation(state: IncidentState) -> Remediation:
    """The remediation the operator approved: the edited one for an edit, else the proposal."""
    approval = state["approval"]
    if approval is None:
        raise RefusedError("There is no approval record; nothing executes without one.")
    if approval.decision == "reject":
        raise RefusedError("The operator rejected the proposal; nothing executes.")
    if approval.decision == "edit":
        if approval.edited_remediation is None:
            raise RefusedError("The edit approval carries no edited remediation.")
        return approval.edited_remediation
    if state["remediation"] is None:
        raise RefusedError("The approval has no remediation in state to apply.")
    return state["remediation"]


# Step 1.
def look_up_action(registry: Registry, remediation: Remediation) -> ActionEntry:
    try:
        return registry.get(remediation.action)
    except UnknownActionError as error:
        raise RefusedError(str(error)) from error


# Step 2.
def validate_parameters(registry: Registry, remediation: Remediation) -> None:
    try:
        registry.validate_params(remediation.action, remediation.params)
    except ParameterError as error:
        raise RefusedError(str(error)) from error


# Step 3.
def check_preconditions(
    context: ExecutionContext, entry: ActionEntry, remediation: Remediation
) -> list[PreconditionResult]:
    """Evaluate every declared check against the source and audit each result."""
    results = evaluate(entry, remediation, context.state, context.source)
    for result in results:
        context.write_audit(
            {
                "event": "precondition",
                "action": remediation.action,
                "target": remediation.target,
                "check": result.check,
                "passed": result.passed,
                "observed": result.observed,
                "detail": result.detail,
            }
        )
    return results


# Step 4.
def compute_key(state: IncidentState, remediation: Remediation) -> str:
    return idempotency_key(state["incident_id"], remediation)


# Step 5.
def find_recorded_execution(
    context: ExecutionContext, remediation: Remediation, key: str
) -> ExecutionOutcome | None:
    """Refuse a remediation other than the approved one (brief D22); return `already_applied`
    when the key is recorded in state or in the source's ledger; else None."""
    approval = context.state["approval"]
    if approval is None or approval.idempotency_key != key:
        raise RefusedError(
            "The remediation is not the one the operator approved: its idempotency key differs."
        )
    previous = context.state["execution"]
    if previous is not None and previous.idempotency_key == key and previous.applied:
        return already_applied(context, remediation, key, previous.result)
    if any(entry.idempotency_key == key for entry in context.source.ledger()):
        return already_applied(context, remediation, key, "found in the source's ledger")
    return None


# Step 6.
def record_dry_run(
    context: ExecutionContext, entry: ActionEntry, remediation: Remediation, key: str
) -> str:
    """Write the dry-run description to the audit log; raises, before any apply, if it cannot."""
    description = dry_run_description(entry, remediation)
    context.write_audit(
        {
            "event": "dry_run",
            "idempotency_key": key,
            **describe(remediation),
            "description": description,
        }
    )
    return description


# Step 7.
def apply_and_audit(
    context: ExecutionContext,
    remediation: Remediation,
    key: str,
    description: str,
    crash_after_apply: bool,
) -> ExecutionOutcome:
    """Apply through the source and write the `apply` record the moment it returns or raises."""
    request = ActionRequest(
        action=remediation.action, target=remediation.target, params=dict(remediation.params)
    )
    started = time.perf_counter()
    try:
        result = context.source.apply(request, key)
    except Exception as error:
        detail = f"The source raised {type(error).__name__} inside apply."
        write_apply_record(context, remediation, key, applied=False, ok=False, detail=detail)
        raise
    latency_ms = (time.perf_counter() - started) * 1000
    write_apply_record(
        context, remediation, key, applied=result.applied, ok=result.ok, detail=result.detail
    )
    if crash_after_apply:
        exit_process(CRASH_EXIT_CODE)
    return applied_outcome(context.state, remediation, key, description, result, latency_ms)


def applied_outcome(
    state: IncidentState,
    remediation: Remediation,
    key: str,
    description: str,
    result: ActionResult,
    latency_ms: float,
) -> ExecutionOutcome:
    kind: OutcomeKind = "apply_failed"
    if result.ok:
        kind = "applied" if result.applied else "already_applied"
    execution = ExecutionRecord(
        idempotency_key=key,
        action=remediation.action,
        target=remediation.target,
        applied=result.applied,
        applied_at=datetime.now(UTC) if result.applied else None,
        result=kind,
        dry_run_output=description,
    )
    evidence = action_evidence(
        state, remediation, key, kind, applied=result.applied, detail=result.detail
    )
    action = Action(tool="apply", ok=result.ok, latency_ms=latency_ms, detail=kind)
    return ExecutionOutcome(kind, result.detail, execution, [evidence], [action])


def already_applied(
    context: ExecutionContext, remediation: Remediation, key: str, recorded: str
) -> ExecutionOutcome:
    """Apply nothing; record that this key was applied before (`recorded` says where)."""
    write_apply_record(context, remediation, key, applied=False, ok=True, detail=ALREADY_APPLIED)
    detail = f"Key {key} was already applied ({recorded}); nothing changed."
    execution = ExecutionRecord(
        idempotency_key=key,
        action=remediation.action,
        target=remediation.target,
        applied=False,
        result=ALREADY_APPLIED,
        dry_run_output="",
    )
    evidence = action_evidence(
        context.state, remediation, key, ALREADY_APPLIED, applied=False, detail=detail
    )
    return ExecutionOutcome(ALREADY_APPLIED, detail, execution, [evidence])


def precondition_failed(
    context: ExecutionContext, remediation: Remediation, checks: list[PreconditionResult]
) -> ExecutionOutcome:
    """Turn each failed check into evidence of kind `precondition`, labelled with the change's
    key so a second failure of the same change can be recognised (spec R17)."""
    key = idempotency_key(context.state["incident_id"], remediation)
    failures = [check for check in checks if not check.passed]
    first_number = count_of_kind(context.state, "precondition") + 1
    evidence = [
        new_evidence(
            f"precondition-{first_number + offset}",
            "preconditions",
            "precondition",
            {
                "idempotency_key": key,
                **describe(remediation),
                **check.model_dump(mode="json"),
            },
        )
        for offset, check in enumerate(failures)
    ]
    execution = ExecutionRecord(
        idempotency_key=key,
        action=remediation.action,
        target=remediation.target,
        applied=False,
        result="precondition_failed",
        dry_run_output="",
    )
    detail = " ".join(check.detail for check in failures)
    return ExecutionOutcome("precondition_failed", detail, execution, evidence)


def refused(state: IncidentState, detail: str) -> ExecutionOutcome:
    """Nothing was applied; the reason becomes evidence so the hand-off can show it."""
    number = count_of_kind(state, "action") + 1
    evidence = new_evidence(
        f"action-{number}",
        "executor",
        "action",
        {"outcome": "refused", "applied": False, "detail": detail},
    )
    return ExecutionOutcome("refused", detail, evidence=[evidence])


def write_apply_record(
    context: ExecutionContext,
    remediation: Remediation,
    key: str,
    *,
    applied: bool,
    ok: bool,
    detail: str,
) -> None:
    context.write_audit(
        {
            "event": "apply",
            "idempotency_key": key,
            **describe(remediation),
            "applied": applied,
            "ok": ok,
            "detail": detail,
        }
    )


def action_evidence(
    state: IncidentState,
    remediation: Remediation,
    key: str,
    outcome: OutcomeKind,
    *,
    applied: bool,
    detail: str,
) -> Evidence:
    """Evidence of kind `action`: rca reads `action` and `applied` to list what is in effect."""
    number = count_of_kind(state, "action") + 1
    payload = {
        "idempotency_key": key,
        **describe(remediation),
        "outcome": outcome,
        "applied": applied,
        "detail": detail,
    }
    return new_evidence(f"action-{number}", "apply", "action", payload)


def describe(remediation: Remediation) -> dict[str, Any]:
    return {
        "action": remediation.action,
        "target": remediation.target,
        "params": dict(remediation.params),
    }


def count_of_kind(state: IncidentState, kind: EvidenceKind) -> int:
    return sum(1 for item in state["evidence"] if item.kind == kind)


def new_evidence(
    evidence_id: str, source: str, kind: EvidenceKind, payload: dict[str, Any]
) -> Evidence:
    """Evidence whose payload strings are neutralised: details can quote the source."""
    return Evidence(
        id=evidence_id,
        source=source,
        kind=kind,
        payload=neutralise_delimiters(payload),
        collected_at=datetime.now(UTC),
    )
