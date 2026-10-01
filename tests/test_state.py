"""E4 to E8: the incident state, the model-output schemas and the citation rule (R4 to R6)."""

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from agentops.state import (
    CONTROL_FIELDS,
    MODEL_OUTPUT_SCHEMAS,
    RCA,
    Approval,
    CitationError,
    Evidence,
    Hypothesis,
    Remediation,
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
    assert CONTROL_FIELDS == {"status", "investigation_budget", "loops", "approval", "execution"}
    assert len(MODEL_OUTPUT_SCHEMAS) == 3

    for schema in MODEL_OUTPUT_SCHEMAS:
        names = property_names_in(schema.model_json_schema())

        assert names, f"{schema.__name__} declares no properties"
        assert names & CONTROL_FIELDS == set(), schema.__name__


def test_approval_accepts_a_role_operator() -> None:
    approval = Approval.model_validate(
        {"decision": "approve", "operator": "on-call", "decided_at": OPENED_AT.isoformat()}
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
            {"decision": "approve", "operator": operator, "decided_at": OPENED_AT.isoformat()}
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
