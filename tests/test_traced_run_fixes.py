"""Fixes for the two graph failures the traced live run hit on 2026-10-12
(/mnt/project-files/agentops/live-eval-2026-10-12-traced): triage filled the evidence lists with
sentences about the symptoms, and rca proposed an action name the runbook does not declare
because no prompt listed the runbook's names. Also the escalation reason, which that run's
records did not keep."""

from datetime import UTC, datetime
from typing import Any

import pytest
from anthropic import transform_schema
from pydantic import ValidationError

from agentops.actions.registry import default_registry, describe_actions
from agentops.baseline import baseline_prompt
from agentops.evals.run import run_graph_once
from agentops.nodes.rca import is_allowed, outcome_fields, rca_human_prompt
from agentops.nodes.triage import TRIAGE_INSTRUCTIONS
from agentops.sources.scenario import load_scenario
from agentops.state import Hypothesis, RcaOutput, Remediation, Triage, new_incident
from tests.conftest import SCENARIOS_DIR


def test_triage_says_to_leave_the_evidence_lists_empty() -> None:
    assert "leave supporting_evidence and contradicting_evidence empty" in TRIAGE_INSTRUCTIONS
    assert "never a description of a symptom" in TRIAGE_INSTRUCTIONS


def test_the_schema_the_model_answers_to_says_evidence_lists_hold_ids() -> None:
    schema = Triage.model_json_schema()
    fields = schema["$defs"]["Hypothesis"]["properties"]

    for name in ("supporting_evidence", "contradicting_evidence"):
        assert "Never free text" in fields[name]["description"]
    assert Hypothesis.model_fields["supporting_evidence"].description


def test_every_runbook_action_is_described_with_its_target_and_params() -> None:
    registry = default_registry()

    line = describe_actions(registry)

    for name in registry.names():
        entry = registry.get(name)
        assert name in line
        assert entry.target_pattern in line
        for key in entry.params:
            assert key in line


def test_the_rca_prompt_lists_the_runbook_actions_only_when_given_a_registry() -> None:
    state = new_incident(
        "checkout", ["error rate above 10%"], opened_at=datetime(2026, 3, 10, 10, tzinfo=UTC)
    )
    line = describe_actions(default_registry())

    assert line in rca_human_prompt(state, "abc123", default_registry())
    assert line not in rca_human_prompt(state, "abc123")


def test_the_baseline_is_told_the_same_action_names() -> None:
    registry = default_registry()

    assert describe_actions(registry) in baseline_prompt(registry)


def test_an_escalated_graph_run_records_why() -> None:
    scenario = load_scenario(SCENARIOS_DIR / "ambiguous_two_causes.json")

    outcome = run_graph_once(scenario, config={})

    assert outcome.outcome == "escalated"
    assert outcome.escalation_reason == "ambiguous"


def test_a_resolved_graph_run_records_no_escalation_reason() -> None:
    scenario = load_scenario(SCENARIOS_DIR / "clean_bad_deploy.json")

    outcome = run_graph_once(scenario, config={})

    assert outcome.outcome == "remediated"
    assert outcome.escalation_reason is None


# The three-repetition run (/mnt/project-files/agentops/live-eval-2026-10-12-r3): rca proposed
# circuit_break_dependency with no parameters, the operator was asked to approve it, and execute
# refused it because the runbook requires `fallback`.
def remediation(**changes: object) -> Remediation:
    fields: dict[str, object] = {
        "action": "circuit_break_dependency",
        "target": "fraud-check",
        "params": {"fallback": "manual_review_queue"},
        "reason": "the dependency is timing out",
        "evidence_ids": ["dep-fraud-check"],
        "risk": "medium",
        "rollback": "close the circuit breaker",
    }
    fields.update(changes)
    return Remediation.model_validate(fields)


def test_the_action_line_states_every_parameter_constraint() -> None:
    line = describe_actions(default_registry())

    assert "fallback (string, required, matching ^[a-z0-9_]+$)" in line
    assert "pool_size (integer, required, at least 8, at most 128)" in line


def test_a_proposal_that_fits_its_runbook_entry_is_allowed() -> None:
    assert is_allowed(remediation(), default_registry())


@pytest.mark.parametrize(
    "changes",
    [
        {"params": {}},
        {"params": {"fallback": "fail-open"}},
        {"params": {"fallback": "manual_review_queue", "extra": "x"}},
        {"target": "fraud_check"},
        {"action": "rollback_deployment"},
    ],
)
def test_a_proposal_execute_would_refuse_is_not_allowed(changes: dict[str, object]) -> None:
    assert not is_allowed(remediation(**changes), default_registry())


def test_a_proposal_missing_a_required_parameter_escalates_before_approval() -> None:
    result = RcaOutput.model_validate(
        {
            "hypotheses": [{"name": "dependency_timeout", "score": 0.9}],
            "root_cause": {
                "label": "dependency_timeout",
                "explanation": "fraud-check times out",
                "evidence_ids": ["dep-fraud-check"],
            },
            "remediation": remediation(params={}).model_dump(),
        }
    )

    fields = outcome_fields(result, [], default_registry())

    assert fields["status"] == "investigating"
    assert fields["escalation_reason"] == "action_not_allowed"


# The r3b run: every proposal of an action with a required parameter came back with no
# parameters, because the SDK closes an open mapping into an object that may hold no keys.
def closed_params_schema() -> dict[str, Any]:
    schema = transform_schema(RcaOutput.model_json_schema())
    return schema["$defs"]["Remediation"]["properties"]["params"]


def test_the_schema_the_model_answers_to_can_carry_parameters() -> None:
    params = closed_params_schema()

    assert params["type"] == "array"
    assert set(params["items"]["properties"]) == {"name", "value"}


def test_parameters_arrive_as_pairs_and_are_kept_as_a_mapping() -> None:
    proposed = remediation(params=[{"name": "fallback", "value": "manual_review_queue"}])

    assert proposed.params == {"fallback": "manual_review_queue"}
    assert proposed.model_dump()["params"] == {"fallback": "manual_review_queue"}
    assert is_allowed(proposed, default_registry())


@pytest.mark.parametrize(
    "pairs",
    [
        [{"name": "fallback"}],
        [{"name": "fallback", "value": "a", "extra": 1}],
        [{"name": "fallback", "value": "a"}, {"name": "fallback", "value": "b"}],
        ["fallback"],
    ],
)
def test_malformed_parameter_pairs_are_refused(pairs: list[object]) -> None:
    with pytest.raises(ValidationError):
        remediation(params=pairs)


def test_the_rca_prompt_lists_the_catalogue_and_the_rules_for_adding_and_targets() -> None:
    from agentops.nodes.rca import RCA_INSTRUCTIONS
    from agentops.state import cause_catalogue

    assert cause_catalogue() in RCA_INSTRUCTIONS
    assert "began first" in RCA_INSTRUCTIONS
    assert "copied exactly from the evidence" in RCA_INSTRUCTIONS


def test_a_failed_precondition_is_named_in_the_run_record() -> None:
    from agentops.state import Evidence

    state = new_incident("search", ["timeouts"], opened_at=datetime(2026, 3, 22, 15, tzinfo=UTC))
    state["evidence"] = [
        Evidence(
            id="precondition-1",
            source="preconditions",
            kind="precondition",
            payload={
                "check": "target_named_in_logs",
                "passed": False,
                "target": "reindex-job",
                "action": "pause_batch_job",
            },
            collected_at=state["opened_at"],
        )
    ]

    from agentops.evals.outcome import failed_preconditions_of

    assert failed_preconditions_of(state) == ["target_named_in_logs on reindex-job"]


def test_the_rca_prompt_gives_numeric_scores_and_says_both_or_neither() -> None:
    from agentops.nodes.rca import RCA_INSTRUCTIONS

    assert "0.9 or above" in RCA_INSTRUCTIONS
    assert "0.6 or below" in RCA_INSTRUCTIONS
    assert "a root cause with no remediation is rejected" in RCA_INSTRUCTIONS
