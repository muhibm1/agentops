"""E4 to E8: the incident state, the model-output schemas and the citation rule (R4 to R6)."""

from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

from agentops.state import (
    APPROVAL_REASON_MAX_LENGTH,
    CONTROL_FIELDS,
    ESCALATION_SUMMARY_MAX_LENGTH,
    HYPOTHESIS_NAME_MAX_LENGTH,
    MODEL_OUTPUT_SCHEMAS,
    MODEL_TEXT_MAX_LENGTH,
    RCA,
    Action,
    Approval,
    ApprovalDecision,
    CitationError,
    Escalation,
    EscalationReason,
    Evidence,
    ExecutionRecord,
    Hypothesis,
    Postmortem,
    RcaOutput,
    Remediation,
    Triage,
    assert_cited,
    new_incident,
)

OPENED_AT = datetime(2026, 3, 10, 10, 30, tzinfo=UTC)


def property_names_in(schema: Any) -> set[str]:
    """Every property name declared anywhere in a JSON schema, `$defs` included."""
    names: set[str] = set()
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            names.update(properties)
        for value in schema.values():
            names |= property_names_in(value)
    elif isinstance(schema, list):
        for item in schema:
            names |= property_names_in(item)
    return names


def evidence_item(evidence_id: str) -> Evidence:
    return Evidence(
        id=evidence_id,
        source="simulator",
        kind="log",
        payload={"message": "KeyError: 'shipping_option'"},
        collected_at=OPENED_AT,
    )


def remediation_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": "rollback_deploy",
        "target": "checkout",
        "params": {"version": "2.14.0"},
        "reason": "The deploy introduced the KeyError.",
        "evidence_ids": ["deploy-001"],
        "risk": "low",
        "rollback": "Redeploy 2.15.0.",
    }
    payload.update(overrides)
    return payload


def test_new_incident_starts_in_triage_with_full_budget() -> None:
    state = new_incident("checkout", ["error rate 12%"], opened_at=OPENED_AT)

    assert state["status"] == "triage"
    assert state["investigation_budget"] == 12
    assert state["loops"] == 0
    assert state["service"] == "checkout"
    assert state["symptoms"] == ["error rate 12%"]
    assert state["opened_at"] == OPENED_AT
    assert state["incident_id"]
    assert state["hypotheses"] == []
    assert state["evidence"] == []
    assert state["actions"] == []
    for empty_field in ("root_cause", "remediation", "postmortem", "approval", "execution"):
        assert state[empty_field] is None


def test_control_fields_absent_from_every_model_output_schema() -> None:
    assert CONTROL_FIELDS == {
        "status",
        "investigation_budget",
        "loops",
        "approval",
        "execution",
        "escalation_reason",
        "unreachable_sources",
        "approval_error",
    }
    assert set(MODEL_OUTPUT_SCHEMAS) == {Triage, RcaOutput, Postmortem, Escalation}

    for schema in MODEL_OUTPUT_SCHEMAS:
        names = property_names_in(schema.model_json_schema())

        assert names, f"{schema.__name__} declares no properties"
        assert names & CONTROL_FIELDS == set(), schema.__name__


def test_approval_accepts_a_role_operator() -> None:
    approval = Approval.model_validate(
        {
            "decision": "approve",
            "operator": "on-call",
            "decided_at": OPENED_AT.isoformat(),
            "idempotency_key": "a" * 64,
        }
    )

    assert approval.operator == "on-call"
    assert Approval.model_validate_json(approval.model_dump_json()).decision == "approve"


@pytest.mark.parametrize("score", [1.01, -0.1, float("nan")])
def test_scores_outside_unit_interval_and_empty_citations_are_rejected(score: float) -> None:
    with pytest.raises(ValidationError):
        Hypothesis(name="bad_deploy", score=score)
    with pytest.raises(ValidationError):
        RCA(label="bad_deploy", explanation="The deploy broke checkout.", evidence_ids=[])
    with pytest.raises(ValidationError):
        Remediation.model_validate(remediation_payload(evidence_ids=[]))


@pytest.mark.parametrize("operator", ["muhibm1", "a@b.example", "Operator"])
def test_operator_rejects_usernames_emails_and_case_variants(operator: str) -> None:
    with pytest.raises(ValidationError):
        Approval.model_validate(
            {
                "decision": "approve",
                "operator": operator,
                "decided_at": OPENED_AT.isoformat(),
                "idempotency_key": "a" * 64,
            }
        )


def test_model_output_rejects_a_control_field_smuggled_in_as_an_extra_key() -> None:
    with pytest.raises(ValidationError):
        RCA.model_validate(
            {
                "label": "bad_deploy",
                "explanation": "The deploy broke checkout.",
                "evidence_ids": ["deploy-001"],
                "status": "resolved",
            }
        )


@pytest.mark.parametrize("label", ["Bad Deploy", "bad-deploy", ""])
def test_rca_label_and_remediation_action_must_be_snake_case(label: str) -> None:
    with pytest.raises(ValidationError):
        RCA(label=label, explanation="x", evidence_ids=["deploy-001"])
    with pytest.raises(ValidationError):
        Remediation.model_validate(remediation_payload(action=label))


def test_assert_cited_accepts_ids_present_in_evidence() -> None:
    evidence = [evidence_item("deploy-001"), evidence_item("log-003")]

    assert_cited(["log-003", "deploy-001"], evidence)


@pytest.mark.parametrize(
    ("ids", "named_in_message"),
    [([], "no evidence"), (["log-999"], "log-999"), (["deploy-001", "log-999"], "log-999")],
)
def test_assert_cited_raises_on_empty_or_unknown_ids(ids: list[str], named_in_message: str) -> None:
    with pytest.raises(CitationError, match=named_in_message):
        assert_cited(ids, [evidence_item("deploy-001")])


def test_citation_error_is_not_a_value_error() -> None:
    assert not issubclass(CitationError, ValueError)


def hypothesis_payload() -> dict[str, Any]:
    return {"name": "bad_deploy", "score": 0.9}


def root_cause_payload() -> dict[str, Any]:
    return {
        "label": "bad_deploy",
        "explanation": "The deploy broke checkout.",
        "evidence_ids": ["deploy-001"],
    }


def test_root_cause_and_remediation_are_both_present_or_both_null() -> None:
    hypotheses = [hypothesis_payload()]

    both_null = RcaOutput.model_validate(
        {"hypotheses": hypotheses, "root_cause": None, "remediation": None}
    )
    both_present = RcaOutput.model_validate(
        {
            "hypotheses": hypotheses,
            "root_cause": root_cause_payload(),
            "remediation": remediation_payload(),
        }
    )

    assert both_null.root_cause is None and both_null.remediation is None
    assert both_present.root_cause is not None and both_present.remediation is not None
    with pytest.raises(ValidationError, match="both"):
        RcaOutput.model_validate(
            {"hypotheses": hypotheses, "root_cause": root_cause_payload(), "remediation": None}
        )
    with pytest.raises(ValidationError, match="both"):
        RcaOutput.model_validate(
            {"hypotheses": hypotheses, "root_cause": None, "remediation": remediation_payload()}
        )


def test_rca_output_requires_at_least_one_hypothesis() -> None:
    with pytest.raises(ValidationError):
        RcaOutput.model_validate({"hypotheses": [], "root_cause": None, "remediation": None})


def test_escalation_evidence_ids_default_to_empty() -> None:
    escalation = Escalation(summary="Two causes fit equally well.")

    assert escalation.evidence_ids == []


def test_action_attempt_defaults_to_one_and_must_be_at_least_one() -> None:
    assert Action(tool="triage", ok=True, latency_ms=1.0).attempt == 1
    with pytest.raises(ValidationError):
        Action(tool="triage", ok=True, latency_ms=1.0, attempt=0)


def test_new_incident_has_no_escalation() -> None:
    state = new_incident("checkout", ["error rate 12%"], opened_at=OPENED_AT)

    assert state["escalation"] is None
    assert state["escalation_reason"] is None


@pytest.mark.parametrize(
    ("schema", "payload"),
    [
        (Triage, {"hypotheses": [hypothesis_payload()]}),
        (
            RcaOutput,
            {"hypotheses": [hypothesis_payload()], "root_cause": None, "remediation": None},
        ),
        (Escalation, {"summary": "Two causes fit equally well."}),
    ],
    ids=["Triage", "RcaOutput", "Escalation"],
)
@pytest.mark.parametrize("control_field", sorted(CONTROL_FIELDS))
def test_every_model_output_schema_rejects_a_smuggled_control_field(
    schema: type[BaseModel], payload: dict[str, Any], control_field: str
) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate({**payload, control_field: "resolved"})


def test_hypothesis_name_and_escalation_summary_have_a_length_cap() -> None:
    assert Hypothesis(name="a" * HYPOTHESIS_NAME_MAX_LENGTH, score=0.5)
    with pytest.raises(ValidationError):
        Hypothesis(name="a" * (HYPOTHESIS_NAME_MAX_LENGTH + 1), score=0.5)
    assert Escalation(summary="s" * ESCALATION_SUMMARY_MAX_LENGTH)
    with pytest.raises(ValidationError):
        Escalation(summary="s" * (ESCALATION_SUMMARY_MAX_LENGTH + 1))


def test_rca_explanation_has_a_length_cap() -> None:
    def rca(explanation: str) -> RCA:
        return RCA(label="bad_deploy", explanation=explanation, evidence_ids=["log-1"])

    assert rca("e" * MODEL_TEXT_MAX_LENGTH)
    with pytest.raises(ValidationError):
        rca("e" * (MODEL_TEXT_MAX_LENGTH + 1))


def test_postmortem_summary_has_a_length_cap() -> None:
    def postmortem(summary: str) -> Postmortem:
        return Postmortem(summary=summary, root_cause="A bad deploy.", evidence_ids=["log-1"])

    assert postmortem("s" * MODEL_TEXT_MAX_LENGTH)
    with pytest.raises(ValidationError):
        postmortem("s" * (MODEL_TEXT_MAX_LENGTH + 1))


def test_triage_rejects_a_repeated_hypothesis_name() -> None:
    with pytest.raises(ValidationError, match="bad_deploy"):
        Triage(
            hypotheses=[
                Hypothesis(name="bad_deploy", score=0.6),
                Hypothesis(name="bad_deploy", score=0.5),
            ]
        )


def test_triage_accepts_distinct_hypothesis_names() -> None:
    triage = Triage(
        hypotheses=[Hypothesis(name="bad_deploy", score=0.6), Hypothesis(name="pool", score=0.5)]
    )

    assert len(triage.hypotheses) == 2


# Increment 3 additions to the state (spec Data, brief D12, D21, D22).


def test_new_incident_starts_with_no_unreachable_source_and_no_approval_error() -> None:
    state = new_incident("checkout", ["error rate 12%"], opened_at=OPENED_AT)

    assert state["unreachable_sources"] == []
    assert state["approval_error"] is None


def test_approval_requires_the_key_of_the_remediation_the_operator_saw() -> None:
    with pytest.raises(ValidationError, match="idempotency_key"):
        Approval.model_validate(
            {"decision": "approve", "operator": "on-call", "decided_at": OPENED_AT.isoformat()}
        )


def test_an_approve_decision_defaults_to_the_operator_role() -> None:
    decision = ApprovalDecision.model_validate({"decision": "approve"})

    assert decision.operator == "operator"
    assert decision.reason is None
    assert decision.remediation is None


def test_an_edit_decision_carries_its_remediation() -> None:
    decision = ApprovalDecision.model_validate(
        {"decision": "edit", "remediation": remediation_payload(), "operator": "reviewer"}
    )

    assert decision.remediation is not None
    assert decision.remediation.action == "rollback_deploy"


@pytest.mark.parametrize(
    ("payload", "named_in_message"),
    [
        ({"decision": "reject"}, "reason"),
        ({"decision": "reject", "reason": "   "}, "reason"),
        ({"decision": "reject", "reason": "x" * (APPROVAL_REASON_MAX_LENGTH + 1)}, "reason"),
        ({"decision": "edit"}, "remediation"),
        ({"decision": "approve", "remediation": remediation_payload()}, "remediation"),
        ({"decision": "approve "}, "decision"),
        ({"decision": "approve", "status": "resolved"}, "status"),
        ({"decision": "approve", "operator": "muhibm1"}, "operator"),
    ],
    ids=[
        "reject without reason",
        "reject with blank reason",
        "reject with oversized reason",
        "edit without remediation",
        "approve with remediation",
        "padded decision",
        "smuggled control field",
        "username as operator",
    ],
)
def test_an_approval_decision_that_does_not_fit_its_rules_is_rejected(
    payload: dict[str, Any], named_in_message: str
) -> None:
    with pytest.raises(ValidationError, match=named_in_message):
        ApprovalDecision.model_validate(payload)


def test_an_execution_record_names_its_action_target_and_whether_it_applied() -> None:
    record = ExecutionRecord(
        idempotency_key="a" * 64,
        action="rollback_deploy",
        target="checkout",
        applied=False,
        result="refused",
        dry_run_output="Would roll checkout back.",
    )

    assert record.applied is False
    assert record.applied_at is None
    with pytest.raises(ValidationError):
        ExecutionRecord.model_validate({**record.model_dump(), "action": "Drop Table"})


@pytest.mark.parametrize(
    "reason",
    [
        "source_outage",
        "action_not_allowed",
        "precondition_failed",
        "apply_failed",
        "symptoms_persist",
    ],
)
def test_the_new_escalation_reasons_are_accepted(reason: str) -> None:
    assert reason in get_args(EscalationReason)


@pytest.mark.parametrize("kind", ["action", "precondition", "operator"])
def test_the_new_evidence_kinds_are_accepted(kind: str) -> None:
    item = Evidence.model_validate(
        {
            "id": f"{kind}-1",
            "source": "graph",
            "kind": kind,
            "payload": {},
            "collected_at": OPENED_AT,
        }
    )

    assert item.kind == kind


def execution_record_payload(target: str) -> dict[str, Any]:
    return {
        "idempotency_key": "a" * 64,
        "action": "rollback_deploy",
        "target": target,
        "applied": False,
        "result": "refused",
        "dry_run_output": "",
    }


def test_a_target_of_sixty_four_characters_is_accepted_on_both_records() -> None:
    target = "a" * 64

    assert Remediation.model_validate(remediation_payload(target=target)).target == target
    assert ExecutionRecord.model_validate(execution_record_payload(target)).target == target


def test_a_target_of_sixty_five_characters_is_rejected_on_both_records() -> None:
    target = "a" * 65

    with pytest.raises(ValidationError, match="target"):
        Remediation.model_validate(remediation_payload(target=target))
    with pytest.raises(ValidationError, match="target"):
        ExecutionRecord.model_validate(execution_record_payload(target))
