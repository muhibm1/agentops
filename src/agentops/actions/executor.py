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
    "applied", "already_applied", "refused", "precondition_failed", "apply_failed", "apply_unknown"
]
# What an `apply` record says about the change: `applied` True, False, or None (the source
# raised, so nobody knows) map to these three words (increment 4, R1).
Effect = Literal["applied", "not_applied", "unknown"]
# The exit code of the demo crash; any non-zero code would do, this one is not used elsewhere.
CRASH_EXIT_CODE = 3
ALREADY_APPLIED = "already_applied"
RECOVERED_DETAIL = "Recovered from the source ledger after an interrupted run."


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
    `apply_unknown` when the source raises inside `apply`, so the change may or may not be in
    effect; otherwise `applied` or `apply_failed`. Raises when the audit log cannot be written.
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


def unrecorded_approval_problem(
    state: IncidentState, audit: AuditLog, thread_id: str
) -> str | None:
    """Why the state's approval is not backed by the audit log, or None when it is (or when the
    state has no approval, which `execute` refuses by itself).

    `state["approval"]` is only a field: anything that can write graph state (the `langgraph dev`
    state endpoint, a tampered checkpoint) could set it. The approval node appends an accepted
    `approval` record to the audit file before it sets the field, so the file is the proof that an
    operator was asked and said yes. The record must match the approval in decision and
    idempotency key (the key already binds the action, target and parameters) and must have been
    written by `thread_id`, the thread now executing: a state copied into another thread, or a
    fork, needs its own approval at its own interrupt."""
    approval = state["approval"]
    if approval is None:
        return None
    for record in audit.records(state["incident_id"]):
        if (
            record.get("event") == "approval"
            and record.get("valid") is True
            and record.get("decision") == approval.decision
            and record.get("idempotency_key") == approval.idempotency_key
            and record.get("thread_id") == thread_id
        ):
            return None
    return (
        "The audit log holds no accepted approval for this remediation; "
        "nothing executes on an approval the approval node did not record."
    )


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
        registry.validate_params(remediation.action, remediation.params, target=remediation.target)
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
        write_repeat_record(context, remediation, key)
        return already_applied(context, remediation, key, previous.result)
    if any(entry.idempotency_key == key for entry in context.source.ledger()):
        if audit_shows_apply(context, key):
            write_repeat_record(context, remediation, key)
        else:
            write_recovered_apply_record(context, remediation, key)
        return already_applied(context, remediation, key, "found in the source's ledger")
    # Scoped to this thread: a fork shares the incident id and audit file but has its own
    # source, and applies for itself.
    if audit_shows_apply(context, key, this_thread_only=True):
        write_repeat_record(context, remediation, key)
        return already_applied(context, remediation, key, "found in the audit log")
    return None


def audit_shows_apply(
    context: ExecutionContext, key: str, *, this_thread_only: bool = False
) -> bool:
    """True when the incident's audit file has an `apply` record for `key` with `applied: true`,
    written by this thread only when `this_thread_only` is set."""
    return any(
        (not this_thread_only or record.get("thread_id") == context.thread_id)
        and record.get("event") == "apply"
        and record.get("idempotency_key") == key
        and record.get("applied") is True
        for record in context.audit.records(context.state["incident_id"])
    )


def write_repeat_record(context: ExecutionContext, remediation: Remediation, key: str) -> None:
    write_apply_record(context, remediation, key, applied=False, ok=True, detail=ALREADY_APPLIED)


def write_recovered_apply_record(
    context: ExecutionContext, remediation: Remediation, key: str
) -> None:
    """The process died, or the audit write failed, between the source's apply and its record;
    the audit file gets the record now, from the source's ledger."""
    write_apply_record(context, remediation, key, applied=True, ok=True, detail=RECOVERED_DETAIL)


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
        # The source may have changed the world before raising, so the effect is unknown, not
        # false; verify judges it. Only the type name: the message can quote anything.
        detail = f"The source raised {type(error).__name__} inside apply."
        write_apply_record(context, remediation, key, applied=None, ok=False, detail=detail)
        latency_ms = (time.perf_counter() - started) * 1000
        return unknown_outcome(context.state, remediation, key, description, detail, latency_ms)
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


def unknown_outcome(
    state: IncidentState,
    remediation: Remediation,
    key: str,
    description: str,
    detail: str,
    latency_ms: float,
) -> ExecutionOutcome:
    """The source raised inside apply: the change may be in effect or not, so `applied` is None
    and the graph goes on to verify, which decides from the signals."""
    kind: OutcomeKind = "apply_unknown"
    execution = ExecutionRecord(
        idempotency_key=key,
        action=remediation.action,
        target=remediation.target,
        applied=None,
        applied_at=None,
        result=kind,
        dry_run_output=description,
    )
    evidence = action_evidence(state, remediation, key, kind, applied=None, detail=detail)
    action = Action(tool="apply", ok=False, latency_ms=latency_ms, detail=kind)
    return ExecutionOutcome(kind, detail, execution, [evidence], [action])


def already_applied(
    context: ExecutionContext, remediation: Remediation, key: str, recorded: str
) -> ExecutionOutcome:
    """Apply nothing; record that this key was applied before (`recorded` says where).

    The execution is marked `applied` only when no action evidence yet records this key as
    applied: that is the run resumed after a crash, whose earlier apply verify must still judge.
    A plain repeat of a key applied earlier in the run changed nothing this time, so it stays
    `applied=False` and verify escalates with symptoms_persist instead of looping.
    """
    detail = f"Key {key} was already applied ({recorded}); nothing changed."
    changed_now = not applied_in_evidence(context.state, key)
    previous = context.state["execution"]
    execution = ExecutionRecord(
        idempotency_key=key,
        action=remediation.action,
        target=remediation.target,
        applied=changed_now,
        applied_at=previous.applied_at if previous and previous.idempotency_key == key else None,
        result=ALREADY_APPLIED,
        dry_run_output="",
    )
    evidence = action_evidence(
        context.state, remediation, key, ALREADY_APPLIED, applied=changed_now, detail=detail
    )
    return ExecutionOutcome(ALREADY_APPLIED, detail, execution, [evidence])


def applied_in_evidence(state: IncidentState, key: str) -> bool:
    """True when action evidence already records `key` as applied."""
    return any(
        item.kind == "action"
        and item.payload.get("idempotency_key") == key
        and item.payload.get("applied") is True
        for item in state["evidence"]
    )


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
    applied: bool | None,
    ok: bool,
    detail: str,
) -> None:
    context.write_audit(
        {
            "event": "apply",
            "idempotency_key": key,
            **describe(remediation),
            "applied": applied,
            "effect": effect_of(applied),
            "ok": ok,
            "detail": detail,
        }
    )


def effect_of(applied: bool | None) -> Effect:
    if applied is None:
        return "unknown"
    return "applied" if applied else "not_applied"


def action_evidence(
    state: IncidentState,
    remediation: Remediation,
    key: str,
    outcome: OutcomeKind,
    *,
    applied: bool | None,
    detail: str,
) -> Evidence:
    """Evidence of kind `action`: rca and postmortem read `action`, `applied` and `effect` to
    list what is in effect and what may be."""
    number = count_of_kind(state, "action") + 1
    payload = {
        "idempotency_key": key,
        **describe(remediation),
        "outcome": outcome,
        "applied": applied,
        "effect": effect_of(applied),
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
