"""What a measured run produced (`RunOutcome`), what it was given, and what it is graded against.

The graph and the baseline both reduce a run to a `RunOutcome`; the evaluators read only that,
so the two systems are scored by the same rules.
"""

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field

from agentops.actions.registry import Registry, default_registry
from agentops.sources.scenario import Scenario
from agentops.state import IncidentState, StrictModel

UNDETERMINED_LABEL = "undetermined"
MODEL_TOOL = "model"
# Action-evidence outcomes for which the executor handed the change to `source.apply`.
APPLY_ATTEMPT_OUTCOMES = frozenset({"applied", "apply_failed", "apply_unknown"})
APPROVING_DECISIONS = frozenset({"approve", "edit"})

SystemName = Literal["graph", "baseline"]
RunResult = Literal["remediated", "escalated", "looped_then_remediated", "failed"]
ExpectedOutcome = Literal["remediate", "escalate", "loop"]


class ApplyAttempt(StrictModel):
    """One change a run sent to the source, with what allowed it."""

    action: str
    target: str
    approved: bool
    in_registry: bool
    ok: bool


class RunOutcome(StrictModel):
    """The `outputs` every evaluator validates. Counts and latency are never negative."""

    system: SystemName
    scenario_id: str = Field(min_length=1)
    # `undetermined` when the run named no root cause.
    root_cause_label: str = Field(min_length=1)
    explanation: str | None
    cited_evidence_ids: list[str]
    outcome: RunResult
    # The exception type name, or what stopped the run, when `outcome` is `failed`.
    failure: str | None
    evidence_ids_before_proposal: list[str]
    proposed_actions: list[str]
    applies: list[ApplyAttempt]
    tool_calls: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: float = Field(ge=0.0, allow_inf_nan=False)
    loops: int = Field(ge=0)


class GradingReference(StrictModel):
    """The `reference_outputs` of every evaluator: the scenario's grading key."""

    root_cause: str
    correct_action: str
    required_evidence: list[str]
    wrong_actions: list[str]
    expected_outcome: ExpectedOutcome


def inputs_for(scenario: Scenario) -> dict[str, Any]:
    """What both systems are given: the service and its symptoms, never the grading key."""
    return {
        "scenario_id": scenario.id,
        "service": scenario.service,
        "symptoms": list(scenario.symptoms),
    }


def reference_for(scenario: Scenario) -> dict[str, Any]:
    reference = GradingReference(
        root_cause=scenario.ground_truth.root_cause,
        correct_action=scenario.ground_truth.correct_action,
        required_evidence=list(scenario.required_evidence),
        wrong_actions=list(scenario.wrong_actions),
        expected_outcome=scenario.expected_outcome,
    )
    return reference.model_dump()


def evidence_ids_of(state: IncidentState) -> list[str]:
    """The ids of the evidence in `state`, in collection order. The runner takes this snapshot
    the first time a run proposes a remediation or names an escalation reason."""
    return [item.id for item in state["evidence"]]


def outcome_from_state(
    state: IncidentState,
    evidence_ids_before_proposal: Sequence[str],
    started: float,
    finished: float,
    *,
    registry: Registry | None = None,
) -> RunOutcome:
    """Reduce a graph run's final state to a `RunOutcome`.

    `started` and `finished` are `time.perf_counter()` readings in seconds. `registry` decides
    `in_registry` for each apply and defaults to the shipped runbook. A run that stopped in any
    status other than `resolved` or `escalated` is `failed`, naming the status. Raises
    `ValueError` for a state with no `scenario_id`, because its outcome could not be graded.
    """
    if state["scenario_id"] is None:
        raise ValueError("Only a run started from a scenario can be graded; scenario_id is None.")
    registry = registry or default_registry()
    applies = apply_attempts_of(state, registry)
    outcome, failure = run_result(state, applies)
    actions = state["actions"]
    return RunOutcome(
        system="graph",
        scenario_id=state["scenario_id"],
        root_cause_label=state["root_cause"].label if state["root_cause"] else UNDETERMINED_LABEL,
        explanation=explanation_of(state),
        cited_evidence_ids=cited_ids_of(state),
        outcome=outcome,
        failure=failure,
        evidence_ids_before_proposal=list(evidence_ids_before_proposal),
        proposed_actions=proposed_actions_of(state),
        applies=applies,
        tool_calls=sum(1 for action in actions if action.tool != MODEL_TOOL),
        model_calls=sum(1 for action in actions if action.tool == MODEL_TOOL),
        input_tokens=sum(action.input_tokens for action in actions),
        output_tokens=sum(action.output_tokens for action in actions),
        latency_ms=max(finished - started, 0.0) * 1000,
        loops=state["loops"],
    )


def run_result(
    state: IncidentState, applies: Sequence[ApplyAttempt]
) -> tuple[RunResult, str | None]:
    """The run's result. A resolved run is `looped_then_remediated` only when a remediation was
    applied and a second one had to follow (`remediation_label`); `loops` alone cannot say so,
    because the merge node and a failed precondition also count a loop when they only go back
    for more evidence."""
    status = state["status"]
    if status == "resolved":
        return remediation_label(applies), None
    if status == "escalated":
        return "escalated", None
    return "failed", f"the run stopped in status {status}"


def remediation_label(
    applies: Sequence[ApplyAttempt],
) -> Literal["remediated", "looped_then_remediated"]:
    """The label of a run that changed the world: `looped_then_remediated` when the source
    accepted two or more applies, `remediated` otherwise. The graph and the baseline both use
    this one rule, so a `loop` scenario is scored the same for both."""
    accepted = sum(1 for attempt in applies if attempt.ok)
    return "looped_then_remediated" if accepted >= 2 else "remediated"


def explanation_of(state: IncidentState) -> str | None:
    if state["root_cause"] is not None:
        return state["root_cause"].explanation
    if state["escalation"] is not None:
        return state["escalation"].summary
    return None


def cited_ids_of(state: IncidentState) -> list[str]:
    if state["root_cause"] is not None:
        return list(state["root_cause"].evidence_ids)
    if state["escalation"] is not None:
        return list(state["escalation"].evidence_ids)
    return []


def proposed_actions_of(state: IncidentState) -> list[str]:
    """Every action name the run proposed, first proposal first, each once.

    A spent proposal is cleared from `remediation` when the run loops, so the names are also read
    back from the action and precondition evidence the executor wrote for it."""
    names: list[str] = []
    for item in state["evidence"]:
        if item.kind in ("action", "precondition") and isinstance(item.payload.get("action"), str):
            names.append(item.payload["action"])
    approval = state["approval"]
    if approval is not None and approval.edited_remediation is not None:
        names.append(approval.edited_remediation.action)
    if state["remediation"] is not None:
        names.append(state["remediation"].action)
    return list(dict.fromkeys(names))


def apply_attempts_of(state: IncidentState, registry: Registry) -> list[ApplyAttempt]:
    """One `ApplyAttempt` per action evidence whose change reached `source.apply`.

    `approved` is read from the approval in state rather than assumed from the graph's shape, so
    an apply that somehow ran without an approval scores as unsafe."""
    approval = state["approval"]
    approved = approval is not None and approval.decision in APPROVING_DECISIONS
    return [
        ApplyAttempt(
            action=item.payload["action"],
            target=str(item.payload.get("target", "")),
            approved=approved,
            in_registry=registry.has(item.payload["action"]),
            ok=item.payload.get("outcome") == "applied",
        )
        for item in state["evidence"]
        if item.kind == "action" and item.payload.get("outcome") in APPLY_ATTEMPT_OUTCOMES
    ]
