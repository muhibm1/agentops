"""The incident state, the Pydantic models it holds, and the citation rule the nodes enforce.

The state is split by who may write it: model nodes write belief fields through validated
model-output schemas, and only graph code writes the control fields named in CONTROL_FIELDS.
"""

import operator
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Literal, Self, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

SNAKE_CASE_NAME = r"^[a-z0-9_]+$"
# A remediation target names a service, deployment or config key; it is never free text.
TARGET_NAME = r"^[a-z0-9_.-]+$"
INITIAL_INVESTIGATION_BUDGET = 12
# Caps on model free text that is printed and re-sent in later prompts.
HYPOTHESIS_NAME_MAX_LENGTH = 80
ESCALATION_SUMMARY_MAX_LENGTH = 1000
# An operator's reject reason reaches the audit file, the checkpoint and the next rca prompt.
APPROVAL_REASON_MAX_LENGTH = 1000

IncidentStatus = Literal[
    "triage",
    "investigating",
    "awaiting_approval",
    "remediating",
    "verifying",
    "resolved",
    "escalated",
    "failed",
]
# `action` is an apply outcome, `precondition` a failed runbook check and `operator` the reason
# an operator gave for rejecting a proposal.
EvidenceKind = Literal[
    "log", "metric", "deployment", "dependency", "config", "action", "precondition", "operator"
]
OperatorRole = Literal["operator", "on-call", "reviewer"]
# Why the graph handed the incident to a human instead of remediating.
EscalationReason = Literal[
    "budget_exhausted",
    "ambiguous",
    "no_evidence",
    "source_outage",
    "action_not_allowed",
    "precondition_failed",
    "apply_failed",
    "symptoms_persist",
]


class StrictModel(BaseModel):
    """Rejects unknown keys, so a model output cannot smuggle in a field the schema lacks."""

    model_config = ConfigDict(extra="forbid")


class Hypothesis(StrictModel):
    name: str = Field(min_length=1, max_length=HYPOTHESIS_NAME_MAX_LENGTH)
    score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    supporting_evidence: list[str] = Field(default_factory=list)
    contradicting_evidence: list[str] = Field(default_factory=list)


class Triage(StrictModel):
    """Model output of the triage node."""

    hypotheses: list[Hypothesis] = Field(min_length=1)

    @model_validator(mode="after")
    def check_hypothesis_names_are_distinct(self) -> Self:
        names = [item.name for item in self.hypotheses]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(f"Hypothesis names must be distinct. Repeated: {repeated}.")
        return self


class RCA(StrictModel):
    label: str = Field(pattern=SNAKE_CASE_NAME)
    explanation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class Remediation(StrictModel):
    action: str = Field(pattern=SNAKE_CASE_NAME)
    target: str = Field(pattern=TARGET_NAME)
    params: dict[str, str | int | float | bool] = Field(default_factory=dict)
    reason: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    risk: Literal["low", "medium", "high"]
    rollback: str = Field(min_length=1)


class RcaOutput(StrictModel):
    """Model output of the rca node: re-scored hypotheses and, when one cause stands out, the
    root cause with its remediation. A model that cannot name a cause returns both as null."""

    hypotheses: list[Hypothesis] = Field(min_length=1)
    root_cause: RCA | None = None
    remediation: Remediation | None = None

    @model_validator(mode="after")
    def check_root_cause_and_remediation_come_together(self) -> Self:
        if (self.root_cause is None) != (self.remediation is None):
            raise ValueError("root_cause and remediation must both be present or both be null.")
        return self


class Escalation(StrictModel):
    """Model output of the escalate node: a hand-off summary for the human."""

    summary: str = Field(min_length=1, max_length=ESCALATION_SUMMARY_MAX_LENGTH)
    evidence_ids: list[str] = Field(default_factory=list)


class Postmortem(StrictModel):
    """Model output of the postmortem node (increment 3 onward)."""

    summary: str = Field(min_length=1)
    timeline: list[str] = Field(default_factory=list)
    root_cause: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    follow_ups: list[str] = Field(default_factory=list)


class Evidence(StrictModel):
    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    kind: EvidenceKind
    payload: dict[str, Any]
    collected_at: datetime


class Action(StrictModel):
    """One model or tool call, recorded for cost and latency accounting."""

    tool: str = Field(min_length=1)
    ok: bool
    latency_ms: float = Field(ge=0.0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    detail: str | None = None
    # Which try of a retried node this record belongs to, so cost accounting sees every attempt.
    attempt: int = Field(default=1, ge=1)


ApprovalChoice = Literal["approve", "reject", "edit"]


class Approval(StrictModel):
    decision: ApprovalChoice
    # A role, never a person's name, username or email (compliance control 1, brief D10).
    operator: OperatorRole
    edited_remediation: Remediation | None = None
    decided_at: datetime
    # The key of the remediation the operator saw; the executor refuses any other (brief D22).
    idempotency_key: str = Field(min_length=1)


class ApprovalDecision(StrictModel):
    """The value an operator resumes the approval interrupt with, validated before it is used.

    A reject needs a reason; an edit needs a remediation and is the only decision that may carry
    one, so an approve can never quietly change what runs.
    """

    decision: ApprovalChoice
    # Free text: describe the evidence, not people (a name here is the profile's GDPR trigger).
    reason: str | None = Field(default=None, max_length=APPROVAL_REASON_MAX_LENGTH)
    remediation: Remediation | None = None
    # A role, never a person's name, username or email (compliance control 1).
    operator: OperatorRole = "operator"

    @model_validator(mode="after")
    def check_fields_fit_the_decision(self) -> Self:
        if self.decision == "reject" and not (self.reason and self.reason.strip()):
            raise ValueError("A reject needs a reason describing the evidence.")
        if self.decision == "edit" and self.remediation is None:
            raise ValueError("An edit needs the edited remediation.")
        if self.decision != "edit" and self.remediation is not None:
            raise ValueError("Only an edit may carry a remediation.")
        return self


class ExecutionRecord(StrictModel):
    idempotency_key: str = Field(min_length=1)
    action: str = Field(pattern=SNAKE_CASE_NAME)
    target: str = Field(pattern=TARGET_NAME)
    # True when the change is in effect: this execution applied it, or the key was already applied
    # before (a repeat, or a run resumed after a crash). A refusal or a failure leaves it False.
    applied: bool
    applied_at: datetime | None = None
    result: str
    dry_run_output: str


class IncidentState(TypedDict):
    # identity and input
    incident_id: str
    scenario_id: str | None
    service: str
    symptoms: list[str]
    opened_at: datetime

    # belief: written by model nodes, always as validated model output
    hypotheses: list[Hypothesis]
    root_cause: RCA | None
    remediation: Remediation | None
    postmortem: Postmortem | None
    escalation: Escalation | None

    # evidence: append-only through reducers, so parallel nodes never clobber each other
    evidence: Annotated[list[Evidence], operator.add]
    actions: Annotated[list[Action], operator.add]

    # control: written only by graph code, never by a model call
    status: IncidentStatus
    investigation_budget: int
    loops: int
    approval: Approval | None
    execution: ExecutionRecord | None
    escalation_reason: EscalationReason | None
    # Evidence sources whose every recorded read attempt failed, written by merge (spec R2).
    unreachable_sources: list[str]
    # Why the last operator answer at the approval interrupt was refused, or None.
    approval_error: str | None


CONTROL_FIELDS = frozenset(
    {
        "status",
        "investigation_budget",
        "loops",
        "approval",
        "execution",
        "escalation_reason",
        "unreachable_sources",
        "approval_error",
    }
)
MODEL_OUTPUT_SCHEMAS: tuple[type[BaseModel], ...] = (Triage, RcaOutput, Postmortem, Escalation)


class CitationError(Exception):
    """A model output cited no evidence, or an id absent from the state's evidence.

    Deliberately not a ValueError, so the graph's retry policy names it explicitly
    (increment-1 ADR 0002).
    """


def new_incident(
    service: str,
    symptoms: list[str],
    *,
    opened_at: datetime,
    incident_id: str | None = None,
    scenario_id: str | None = None,
) -> IncidentState:
    """A fresh state in triage with the full investigation budget.

    `opened_at` must be timezone-aware: evidence windows are computed from it.
    """
    if opened_at.tzinfo is None:
        raise ValueError("opened_at must be timezone-aware, for example 2026-03-10T10:30:00Z.")
    return IncidentState(
        incident_id=incident_id or uuid.uuid4().hex,
        scenario_id=scenario_id,
        service=service,
        symptoms=list(symptoms),
        opened_at=opened_at,
        hypotheses=[],
        root_cause=None,
        remediation=None,
        postmortem=None,
        escalation=None,
        evidence=[],
        actions=[],
        status="triage",
        investigation_budget=INITIAL_INVESTIGATION_BUDGET,
        loops=0,
        approval=None,
        execution=None,
        escalation_reason=None,
        unreachable_sources=[],
        approval_error=None,
    )


def assert_cited(ids: Sequence[str], evidence: Sequence[Evidence]) -> None:
    """Raise CitationError unless `ids` is non-empty and every id names an item in `evidence`."""
    if not ids:
        raise CitationError("The output cites no evidence; at least one evidence id is required.")
    known_ids = {item.id for item in evidence}
    unknown_ids = [cited for cited in ids if cited not in known_ids]
    if unknown_ids:
        raise CitationError(
            f"The output cites evidence ids not present in state: {', '.join(unknown_ids)}."
        )
